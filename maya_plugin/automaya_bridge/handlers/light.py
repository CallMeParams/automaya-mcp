"""light.* commands: lighting science driving Arnold lights, with Maya light fallbacks.

Arnold (mtoa) is the first choice. When it is missing the commands that can
still do something useful build Maya lights instead (directionalLight,
areaLight, pointLight) and report ``"path": "maya"`` so the agent knows the
render will not match. Portals and the physical sky have no Maya equivalent and
raise instead. The maths lives in ``_science`` (no Maya) so the server can
answer the same questions offline.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _science as sci
from . import _util
from ._util import BridgeError, require_maya

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

MTOA_HINT = "Install Arnold for Maya (MtoA) and enable 'mtoa' in Windows > Settings > Plug-in Manager, then retry."
OPTIONS = "defaultArnoldRenderOptions"
ARNOLD_LIGHT_NODES = ("aiAreaLight", "aiSkyDomeLight", "aiMeshLight", "aiPhotometricLight", "aiLightPortal")
MAYA_LIGHT_NODES = ("directionalLight", "pointLight", "spotLight", "areaLight")

# Practical light presets: typical lumens, colour temperature, luminous efficacy (lm per electrical W),
# Arnold area light shape (0 quad, 1 disk, 2 cylinder) and emitter size in cm.
PRACTICALS: Dict[str, Dict[str, Any]] = {
    "bulb": {"lumens": 800.0, "kelvin": 2700.0, "efficacy": 15.0, "shape": 1, "size": [6.0, 6.0, 6.0], "solid_angle": 4 * math.pi, "notes": "60 W incandescent equivalent; LED bulbs use efficacy 90"},
    "tube": {"lumens": 2800.0, "kelvin": 4000.0, "efficacy": 70.0, "shape": 2, "size": [120.0, 3.0, 3.0], "solid_angle": 4 * math.pi, "notes": "1.2 m fluorescent tube, 36 W"},
    "neon": {"lumens": 300.0, "kelvin": 3000.0, "efficacy": 20.0, "shape": 2, "size": [60.0, 1.5, 1.5], "solid_angle": 4 * math.pi, "notes": "per metre of neon tube; colour usually overrides the Kelvin"},
    "candle": {"lumens": 13.0, "kelvin": 1850.0, "efficacy": 0.3, "shape": 1, "size": [1.5, 1.5, 1.5], "solid_angle": 4 * math.pi, "notes": "one candela by definition"},
    "screen": {"lumens": 150.0, "kelvin": 6500.0, "efficacy": 4.0, "shape": 0, "size": [60.0, 34.0, 1.0], "solid_angle": math.pi, "notes": "27 inch monitor at 250 nits; emits into a hemisphere"},
}

STUDIO_STYLES: Dict[str, Dict[str, Any]] = {
    "softbox": {"key_angle": 35.0, "key_elevation": 25.0, "fill_stops": -1.5, "rim_stops": 0.0, "softness": 1.5, "kelvin": 5600.0, "notes": "big soft key, gentle fill, subtle rim; product and portrait default"},
    "butterfly": {"key_angle": 0.0, "key_elevation": 45.0, "fill_stops": -2.0, "rim_stops": -1.0, "softness": 1.0, "kelvin": 5600.0, "notes": "key straight on and high (Paramount), fill from below the lens axis"},
    "rembrandt": {"key_angle": 45.0, "key_elevation": 40.0, "fill_stops": -3.0, "rim_stops": -0.5, "softness": 0.6, "kelvin": 4300.0, "notes": "45 degree key, deep fill ratio, small triangle of light on the shadow cheek"},
    "rim_heavy": {"key_angle": 60.0, "key_elevation": 20.0, "fill_stops": -3.0, "rim_stops": 1.5, "softness": 0.7, "kelvin": 5000.0, "notes": "edge light dominates, moody silhouette; bring the key down another stop for noir"},
}


# helpers ------------------------------------------------------------------
def _has_mtoa() -> bool:
    """True when mtoa is (or can be) loaded. Never raises."""
    try:
        _util.ensure_plugin("mtoa", MTOA_HINT)
        return True
    except BridgeError:
        return False


def _require_mtoa(what: str) -> None:
    try:
        _util.ensure_plugin("mtoa", MTOA_HINT)
    except BridgeError as exc:
        raise BridgeError("%s needs Arnold: %s" % (what, exc))


def _safe_set(node: str, attr: str, value: Any, warnings: List[str]) -> bool:
    try:
        _util.set_attr_value(node, attr, value)
        return True
    except BridgeError as exc:
        warnings.append(str(exc))
        return False


def _safe_get(plug: str) -> Any:
    try:
        value = cmds.getAttr(plug)
    except Exception:
        return None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        return list(value[0])
    if isinstance(value, tuple):
        return list(value)
    return value


def _use_arnold_renderer(warnings: List[str]) -> None:
    _safe_set("defaultRenderGlobals", "currentRenderer", "arnold", warnings)


def _arnold_light(node_type: str, base: str) -> Dict[str, str]:
    shape = cmds.shadingNode(node_type, asLight=True, name=base + "Shape")
    transform = _util.transform_of(shape)
    if transform != shape:
        transform = cmds.rename(transform, base)
    shapes = _util.shapes_of(transform)
    shape = shapes[0] if shapes else shape
    return {"transform": _util.long_name(transform), "shape": _util.long_name(shape)}


def _maya_light(kind: str, base: str) -> Dict[str, str]:
    maker = getattr(cmds, kind)
    made = maker(name=base)
    made = made[0] if isinstance(made, (list, tuple)) and made else made
    made = made or base
    shape = made if cmds.nodeType(made) == kind else (_util.shapes_of(made, kind) or [made])[0]
    transform = _util.transform_of(shape)
    return {"transform": _util.long_name(transform), "shape": _util.long_name(shape)}


def _apply_kelvin(shape: str, kelvin: float, is_ai: bool, warnings: List[str]) -> Dict[str, Any]:
    """Prefer Arnold's colour temperature attributes, else bake the Kelvin into the colour."""
    rgb = sci.kelvin_to_rgb(kelvin)
    if is_ai:
        probe: List[str] = []
        if _safe_set(shape, "aiUseColorTemperature", True, probe) and _safe_set(shape, "aiColorTemperature", float(kelvin), probe):
            return {"kelvin": kelvin, "via": "aiColorTemperature", "rgb": rgb}
    _safe_set(shape, "color", rgb, warnings)
    return {"kelvin": kelvin, "via": "color", "rgb": rgb}


def _set_exposure(shape: str, exposure: float, is_ai: bool, warnings: List[str]) -> None:
    _safe_set(shape, "exposure" if is_ai else "aiExposure", float(exposure), warnings)


def _subject_bounds(subject: Sequence[str] | None) -> Dict[str, Any]:
    targets = _util.resolve_targets(subject)
    bbox = _util.world_bbox(targets)
    if not bbox:
        raise BridgeError("could not measure the bounding box of %s; is it a mesh?" % ", ".join(targets))
    size = bbox["size"]
    radius = max(size) * 0.5 or 50.0
    return {"targets": targets, "bbox": bbox, "center": bbox["center"], "radius": radius, "height": size[1]}


def _camera_shape(camera: str | None) -> str:
    camera = camera or "persp"
    _util.require_nodes([camera])
    shapes = _util.shapes_of(camera, "camera")
    if shapes:
        return shapes[0]
    if cmds.nodeType(camera) == "camera":
        return camera
    raise BridgeError("%s is not a camera" % camera)


def _place_area_light(name: str, position: Sequence[float], target: Sequence[float], size: float, exposure: float, kelvin: float, use_arnold: bool, warnings: List[str], intensity: float = 1.0) -> Dict[str, Any]:
    if use_arnold:
        made = _arnold_light("aiAreaLight", name)
        _safe_set(made["shape"], "aiTranslator", "quad", warnings)
    else:
        made = _maya_light("areaLight", name)
    is_ai = use_arnold
    shape, transform = made["shape"], made["transform"]
    _safe_set(transform, "translate", list(position), warnings)
    _safe_set(transform, "rotate", sci.aim_rotation(position, target), warnings)
    _safe_set(transform, "scale", [size, size, size], warnings)
    _safe_set(shape, "intensity", float(intensity), warnings)
    _set_exposure(shape, exposure, is_ai, warnings)
    colour = _apply_kelvin(shape, kelvin, is_ai, warnings)
    if use_arnold:
        _safe_set(shape, "aiSoftEdge", 0.5, warnings)
        _safe_set(shape, "aiSamples", 2, warnings)
    return {"name": name, "transform": transform, "shape": shape, "position": [round(float(v), 3) for v in position], "exposure": round(exposure, 3), "size": round(size, 3), "color": colour}


def _orbit(center: Sequence[float], distance: float, azimuth_deg: float, elevation_deg: float) -> List[float]:
    """Point on a sphere around center; azimuth 0 is in front (+Z, camera side), positive goes to camera left (+X)."""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    return [center[0] + distance * math.cos(e) * math.sin(a), center[1] + distance * math.sin(e), center[2] + distance * math.cos(e) * math.cos(a)]


def _all_light_shapes() -> List[str]:
    shapes: List[str] = []
    for node_type in ARNOLD_LIGHT_NODES + MAYA_LIGHT_NODES:
        try:
            shapes.extend(cmds.ls(type=node_type, long=True) or [])
        except Exception:
            continue
    return sorted(set(shapes))


# commands -----------------------------------------------------------------
@command("light.sun_sky", mutates=True)
def sun_sky(
    lat: float = 51.5,
    lon: float = -0.12,
    date: str = "2026-06-21",
    time: str = "12:00",
    utc_offset: float = 0.0,
    intensity: float = 1.0,
    turbidity: float = 3.0,
    name: str = "sun",
    sun_size: float = 0.51,
    ground_albedo: float = 0.2,
) -> Dict[str, Any]:
    """Sun and sky for a place, date and local time. Arnold: aiPhysicalSky into an aiSkyDomeLight plus a
    directionalLight sun with aiExposure; without Arnold only the directional sun. Returns the solar data."""
    require_maya()
    try:
        when = sci.local_to_utc(date, time, utc_offset)
    except (ValueError, TypeError) as exc:
        raise BridgeError("date must be YYYY-MM-DD and time HH:MM (%s)" % exc)
    try:
        sun = sci.solar_position(lat, lon, when)
    except ValueError as exc:
        raise BridgeError(str(exc))
    sky = sci.sky_illuminance_estimate(sun["elevation"])
    kelvin = sci.sun_kelvin_estimate(sun["elevation"])
    warnings: List[str] = []
    use_arnold = _has_mtoa()
    if use_arnold:
        _use_arnold_renderer(warnings)
    direct_ai = sci.lux_to_arnold_irradiance(sky["direct_normal_lux"]) * float(intensity)
    sun_intensity, sun_exposure = sci.split_intensity_exposure(direct_ai) if direct_ai > 0 else (0.0, 0.0)
    made = _maya_light("directionalLight", name)
    sun_shape, sun_transform = made["shape"], made["transform"]
    _safe_set(sun_transform, "rotate", sci.sun_light_rotation(sun["elevation"], sun["azimuth"]), warnings)
    _safe_set(sun_transform, "translate", [0.0, 1000.0, 0.0], warnings)
    _safe_set(sun_shape, "intensity", float(sun_intensity), warnings)
    _safe_set(sun_shape, "aiExposure", float(sun_exposure), warnings)
    _safe_set(sun_shape, "useRayTraceShadows", True, warnings)
    _safe_set(sun_shape, "aiAngle", float(sun_size), warnings)
    _safe_set(sun_shape, "color", sci.kelvin_to_rgb(kelvin), warnings)
    out: Dict[str, Any] = {
        "path": "arnold" if use_arnold else "maya",
        "sun_transform": sun_transform, "sun_shape": sun_shape,
        "sun_rotation": sci.sun_light_rotation(sun["elevation"], sun["azimuth"]),
        "sun_direction": sci.sun_direction(sun["elevation"], sun["azimuth"]),
        "sun_kelvin": kelvin, "sun_intensity": sun_intensity, "sun_exposure": sun_exposure,
        "solar": sun, "illuminance": sky, "ev100": sky["ev100"], "camera_ai_exposure": sci.exposure_value_to_arnold(sky["ev100"]),
        "utc": when.strftime("%Y-%m-%d %H:%M"), "warnings": warnings,
    }
    if use_arnold:
        dome = _arnold_light("aiSkyDomeLight", name + "Sky")
        physical = cmds.shadingNode("aiPhysicalSky", asTexture=True, name=name + "PhysicalSky")
        cmds.connectAttr(physical + ".outColor", dome["shape"] + ".color", force=True)
        _safe_set(physical, "elevation", float(sun["elevation"]), warnings)
        # aiPhysicalSky measures azimuth from +X toward +Z; ours is clockwise from north (-Z), so shift by 90.
        _safe_set(physical, "azimuth", float((sun["azimuth"] - 90.0) % 360.0), warnings)
        _safe_set(physical, "turbidity", float(turbidity), warnings)
        _safe_set(physical, "intensity", float(intensity), warnings)
        _safe_set(physical, "sunSize", float(sun_size), warnings)
        _safe_set(physical, "groundAlbedo", [ground_albedo] * 3, warnings)
        # The directional light is the sun; the sky node must not add a second one.
        _safe_set(physical, "enableSun", False, warnings)
        _safe_set(dome["shape"], "aiSamples", 2, warnings)
        _safe_set(dome["shape"], "camera", 1.0, warnings)
        out.update({"sky_transform": dome["transform"], "sky_shape": dome["shape"], "physical_sky": physical, "physical_sky_azimuth": (sun["azimuth"] - 90.0) % 360.0})
    else:
        out["note"] = "mtoa is unavailable so no physical sky was built; the directional sun still matches the real sun angle. " + MTOA_HINT
    if sun["elevation"] <= 0:
        out["note"] = (out.get("note", "") + " The sun is below the horizon at this time; expect twilight levels.").strip()
    return out


@command("light.hdri_dome", mutates=True)
def hdri_dome(path: str, rotation: float = 0.0, intensity: float = 1.0, exposure: float = 0.0, camera_visible: bool = True, ground_projection: bool = False, name: str = "hdriDome") -> Dict[str, Any]:
    """aiSkyDomeLight with an HDRI wired through a Raw file texture. Rotation is degrees about Y. Arnold only."""
    require_maya()
    if not path:
        raise BridgeError("path to an .hdr or .exr is required")
    _require_mtoa("hdri_dome")
    warnings: List[str] = []
    _use_arnold_renderer(warnings)
    dome = _arnold_light("aiSkyDomeLight", name)
    nodes = _util.create_file_texture(path, color_space="Raw", name=name + "_hdri")
    cmds.connectAttr(nodes["file"] + ".outColor", dome["shape"] + ".color", force=True)
    _safe_set(dome["shape"], "intensity", float(intensity), warnings)
    _safe_set(dome["shape"], "exposure", float(exposure), warnings)
    _safe_set(dome["shape"], "camera", 1.0 if camera_visible else 0.0, warnings)
    _safe_set(dome["shape"], "aiSamples", 2, warnings)
    _safe_set(dome["transform"], "rotate", [0.0, float(rotation), 0.0], warnings)
    out = {"path": "arnold", "transform": dome["transform"], "shape": dome["shape"], "file_node": nodes["file"], "hdri": path, "hdri_exists_on_disk": os.path.isfile(os.path.expandvars(os.path.expanduser(path))), "rotation": rotation, "warnings": warnings}
    if ground_projection:
        out["ground_projection_hint"] = "Arnold has no dome ground projection; add a large ground plane with aiShadowMatte (assign materials.create type aiShadowMatte) so objects sit on the HDRI ground."
    return out


@command("light.three_point", mutates=True)
def three_point(
    subject: Sequence[str] | None = None,
    key_stops: float = 0.0,
    fill_stops: float = -2.0,
    rim_stops: float = 1.0,
    key_angle: float = 45.0,
    key_elevation: float = 30.0,
    softness: float = 1.0,
    kelvin: float = 5600.0,
    fill_kelvin: float | None = None,
    rim_kelvin: float | None = None,
    distance_factor: float = 2.5,
    key_exposure: float | None = None,
    prefix: str = "",
) -> Dict[str, Any]:
    """Key, fill and rim area lights around a subject, sized and exposed from its bounding box. Stops are relative to the key,
    whose exposure defaults to what renders a white surface near 0.8 at aiExposure 0. Uses aiAreaLight, else Maya areaLight."""
    require_maya()
    bounds = _subject_bounds(subject)
    center, radius = bounds["center"], bounds["radius"]
    distance = radius * max(float(distance_factor), 1.1)
    size = radius * max(float(softness), 0.05)
    warnings: List[str] = []
    use_arnold = _has_mtoa()
    if use_arnold:
        _use_arnold_renderer(warnings)
    else:
        warnings.append("mtoa unavailable: built Maya areaLights instead of aiAreaLights (viewport only, no soft edge). " + MTOA_HINT)
    # A normalised area light of intensity 1 gives irradiance 2^exposure / d^2 (scene units); aim for 0.8 on a white surface.
    base = float(key_exposure) if key_exposure is not None else math.log2(0.8 * distance * distance)
    key_exp = base + float(key_stops)
    rig: Dict[str, Any] = {}
    rig["key"] = _place_area_light(prefix + "keyLight", _orbit(center, distance, key_angle, key_elevation), center, size, key_exp, kelvin, use_arnold, warnings)
    rig["fill"] = _place_area_light(prefix + "fillLight", _orbit(center, distance * 1.2, -key_angle * 1.3 if key_angle else -60.0, max(key_elevation * 0.4, 5.0)), center, size * 1.6, base + float(fill_stops), fill_kelvin or kelvin, use_arnold, warnings)
    rig["rim"] = _place_area_light(prefix + "rimLight", _orbit(center, distance, 180.0 - key_angle * 0.5, max(key_elevation + 20.0, 40.0)), center, size * 0.6, base + float(rim_stops), rim_kelvin or kelvin, use_arnold, warnings)
    grp = cmds.group([rig[k]["transform"] for k in ("key", "fill", "rim")], name=prefix + "threePointRig")
    return {
        "path": "arnold" if use_arnold else "maya", "group": _util.long_name(grp), "lights": rig, "subject": bounds["targets"], "bbox": bounds["bbox"],
        "distance": round(distance, 2), "key_exposure": round(key_exp, 3), "ratios_stops": {"fill": fill_stops, "rim": rim_stops}, "warnings": warnings,
    }


@command("light.studio", mutates=True)
def studio(subject: Sequence[str] | None = None, style: str = "softbox", kelvin: float | None = None, prefix: str | None = None) -> Dict[str, Any]:
    """Preset studio setups (softbox, butterfly, rembrandt, rim_heavy) built on three_point, plus a large white bounce card for softbox."""
    require_maya()
    style = (style or "softbox").lower().replace(" ", "_").replace("-", "_")
    if style not in STUDIO_STYLES:
        raise BridgeError("unknown style %r; use one of %s" % (style, ", ".join(sorted(STUDIO_STYLES))))
    preset = STUDIO_STYLES[style]
    out = three_point(
        subject=subject, key_angle=preset["key_angle"], key_elevation=preset["key_elevation"], fill_stops=preset["fill_stops"], rim_stops=preset["rim_stops"],
        softness=preset["softness"], kelvin=float(kelvin) if kelvin is not None else preset["kelvin"], prefix=(prefix if prefix is not None else style + "_"),
    )
    out["style"] = style
    out["notes"] = preset["notes"]
    return out


@command("light.interior_portals", mutates=True)
def interior_portals(windows: Sequence[str], name: str = "portal") -> Dict[str, Any]:
    """One aiLightPortal per window opening, sized and placed from each window's bounding box. Arnold only; needs a skydome."""
    require_maya()
    if not windows:
        raise BridgeError("windows must list the window opening transforms (planes or frames)")
    _require_mtoa("interior_portals")
    _util.require_nodes(list(windows))
    warnings: List[str] = []
    portals: List[Dict[str, Any]] = []
    for i, window in enumerate(windows):
        bbox = _util.world_bbox([window])
        if not bbox:
            warnings.append("%s has no bounding box, skipped" % window)
            continue
        size = bbox["size"]
        thin = min(range(3), key=lambda k: size[k])  # the thin axis is the window normal
        made = _arnold_light("aiLightPortal", "%s%d" % (name, i + 1))
        _safe_set(made["transform"], "translate", bbox["center"], warnings)
        rot = {0: [0.0, 90.0, 0.0], 1: [-90.0, 0.0, 0.0], 2: [0.0, 0.0, 0.0]}[thin]
        _safe_set(made["transform"], "rotate", rot, warnings)
        dims = [size[k] for k in range(3) if k != thin]
        _safe_set(made["transform"], "scale", [dims[0] * 0.5, dims[1] * 0.5, 1.0], warnings)
        portals.append({"window": window, "transform": made["transform"], "shape": made["shape"], "normal_axis": "xyz"[thin], "size": dims})
    domes = cmds.ls(type="aiSkyDomeLight") or []
    if not domes:
        warnings.append("no aiSkyDomeLight in the scene; portals only guide skydome light, add one with light.hdri_dome or light.sun_sky")
    return {"path": "arnold", "portals": portals, "count": len(portals), "skydome_present": bool(domes), "warnings": warnings}


@command("light.practical", mutates=True)
def practical(
    kind: str = "bulb",
    lumens: float | None = None,
    watts: float | None = None,
    kelvin: float | None = None,
    position: Sequence[float] | None = None,
    rotate: Sequence[float] | None = None,
    name: str | None = None,
    color: Sequence[float] | None = None,
) -> Dict[str, Any]:
    """A practical light (bulb, tube, neon, candle, screen) with a physically derived intensity from lumens or watts and colour from Kelvin.
    Arnold: aiAreaLight with the right shape; fallback: pointLight."""
    require_maya()
    kind = (kind or "bulb").lower()
    if kind not in PRACTICALS:
        raise BridgeError("unknown practical kind %r; use one of %s" % (kind, ", ".join(sorted(PRACTICALS))))
    preset = PRACTICALS[kind]
    if lumens is None:
        lumens = sci.watts_to_lumens(float(watts), preset["efficacy"]) if watts is not None else preset["lumens"]
    if float(lumens) < 0:
        raise BridgeError("lumens must be >= 0")
    kelvin = float(kelvin) if kelvin is not None else preset["kelvin"]
    photometry = sci.lumens_to_arnold_intensity(float(lumens), solid_angle_sr=preset["solid_angle"])
    warnings: List[str] = []
    use_arnold = _has_mtoa()
    base = name or (kind + "Light")
    if use_arnold:
        _use_arnold_renderer(warnings)
        made = _arnold_light("aiAreaLight", base)
        shape = made["shape"]
        _safe_set(shape, "aiTranslator", ["quad", "disk", "cylinder"][preset["shape"]], warnings)
        _safe_set(shape, "aiNormalize", True, warnings)
        _safe_set(shape, "aiSoftEdge", 0.2, warnings)
        _safe_set(shape, "aiSamples", 2, warnings)
        _safe_set(made["transform"], "scale", [v * 0.5 for v in preset["size"]], warnings)
        if kind == "screen":
            _safe_set(shape, "aiSpread", 1.0, warnings)
    else:
        made = _maya_light("pointLight", base)
        shape = made["shape"]
        _safe_set(shape, "useRayTraceShadows", True, warnings)
        warnings.append("mtoa unavailable: built a pointLight; intensity uses the same photometric scale. " + MTOA_HINT)
    _safe_set(shape, "intensity", photometry["intensity"], warnings)
    _set_exposure(shape, photometry["exposure"], use_arnold, warnings)
    if color is not None:
        _safe_set(shape, "color", list(color), warnings)
        colour: Dict[str, Any] = {"rgb": list(color), "via": "color"}
    else:
        colour = _apply_kelvin(shape, kelvin, use_arnold, warnings)
    if position is not None:
        _safe_set(made["transform"], "translate", list(position), warnings)
    if rotate is not None:
        _safe_set(made["transform"], "rotate", list(rotate), warnings)
    return {"path": "arnold" if use_arnold else "maya", "kind": kind, "transform": made["transform"], "shape": made["shape"], "lumens": float(lumens), "kelvin": kelvin, "color": colour, "photometry": photometry, "preset_notes": preset["notes"], "warnings": warnings}


@command("light.exposure", mutates=True)
def exposure(camera: str | None = None, ev: float | None = None, iso: float | None = None, fstop: float | None = None, shutter: float | None = None, reference_ev: float = sci.ARNOLD_REFERENCE_EV) -> Dict[str, Any]:
    """Set the camera's aiExposure from an EV or ISO/f-stop/shutter (seconds). EV 15 is aiExposure 0 under the AutoMaya light scale.
    Without Arnold the viewport exposure of every model panel is set instead."""
    require_maya()
    if ev is None:
        if iso is None or fstop is None or shutter is None:
            raise BridgeError("pass ev, or all of iso, fstop and shutter (seconds, 1/125 is 0.008)")
        try:
            ev = sci.ev_from(float(iso), float(fstop), float(shutter))
        except ValueError as exc:
            raise BridgeError(str(exc))
    ai_exposure = sci.exposure_value_to_arnold(float(ev), float(reference_ev))
    cam_shape = _camera_shape(camera)
    warnings: List[str] = []
    use_arnold = _has_mtoa()
    out: Dict[str, Any] = {"camera": cam_shape, "ev": float(ev), "ai_exposure": ai_exposure, "reference_ev": reference_ev, "illuminance_lux": sci.illuminance_from_ev(float(ev)), "suggested_settings": sci.ev_to_settings(float(ev)), "warnings": warnings}
    if use_arnold and _safe_set(cam_shape, "aiExposure", ai_exposure, warnings):
        out["path"] = "arnold"
    else:
        out["path"] = "maya"
        panels = cmds.getPanel(type="modelPanel") or []
        for panel in panels:
            try:
                cmds.modelEditor(panel, edit=True, exposure=ai_exposure)
            except Exception as exc:
                warnings.append("viewport exposure on %s failed: %s" % (panel, exc))
        out["viewport_panels"] = panels
        if not use_arnold:
            warnings.append("mtoa unavailable: set viewport exposure only. " + MTOA_HINT)
    return out


@command("light.light_report")
def light_report(camera: str | None = None, target: Sequence[float] | None = None) -> Dict[str, Any]:
    """Every light with its effective intensity, an illuminance guess at the target point (default: scene centre) in Arnold units and lux,
    the summed EV estimate and the camera's aiExposure so the agent can spot over/under exposure before rendering."""
    require_maya()
    cam_shape = _camera_shape(camera)
    if target is None:
        meshes = cmds.ls(type="mesh", long=True) or []
        bbox = _util.world_bbox([_util.transform_of(m) for m in meshes]) if meshes else None
        target = bbox["center"] if bbox else [0.0, 0.0, 0.0]
    target = [float(v) for v in target]
    lights: List[Dict[str, Any]] = []
    total_ai = 0.0
    for shape in _all_light_shapes():
        node_type = cmds.nodeType(shape)
        transform = _util.transform_of(shape)
        is_ai = node_type.startswith("ai")
        intensity = float(_safe_get(shape + ".intensity") or 0.0)
        exp = float(_safe_get(shape + (".exposure" if is_ai else ".aiExposure")) or 0.0)
        effective = intensity * (2.0 ** exp)
        pos = _util.triple(transform, "translate")
        dist = math.sqrt(sum((pos[i] - target[i]) ** 2 for i in range(3)))
        if node_type in ("directionalLight", "aiSkyDomeLight", "aiLightPortal"):
            irradiance = effective if node_type != "aiLightPortal" else 0.0
            law = "distant" if node_type == "directionalLight" else "dome"
        else:
            irradiance = effective / max(dist * dist, 1.0)
            law = "inverse_square"
        total_ai += irradiance
        lights.append({
            "transform": transform, "shape": shape, "node_type": node_type, "intensity": intensity, "exposure": exp, "effective": round(effective, 4),
            "color": _safe_get(shape + ".color"), "distance_to_target": round(dist, 2), "irradiance_arnold": round(irradiance, 6),
            "illuminance_lux": round(irradiance * sci.SUN_LUX, 2), "law": law,
        })
    lights.sort(key=lambda entry: -entry["irradiance_arnold"])
    total_lux = total_ai * sci.SUN_LUX
    ev = sci.ev_from_illuminance(total_lux) if total_lux > 0 else None
    cam_exp = _safe_get(cam_shape + ".aiExposure")
    cam_exp = float(cam_exp) if isinstance(cam_exp, (int, float)) else 0.0
    out: Dict[str, Any] = {
        "camera": cam_shape, "camera_ai_exposure": cam_exp, "target": target, "count": len(lights), "lights": lights,
        "total_irradiance_arnold": round(total_ai, 6), "total_illuminance_lux": round(total_lux, 2), "scene_ev100": ev,
        "recommended_camera_ai_exposure": sci.exposure_value_to_arnold(ev) if ev is not None else None,
    }
    if ev is not None:
        delta = cam_exp - sci.exposure_value_to_arnold(ev)
        out["exposure_delta_stops"] = round(delta, 2)
        out["verdict"] = "about right" if abs(delta) < 1.0 else ("over exposed by %.1f stops" % delta if delta > 0 else "under exposed by %.1f stops" % -delta)
    else:
        out["verdict"] = "no light reaches the target; add a light or check intensities"
    return out


@command("light.kelvin_to_rgb")
def kelvin_rgb(kelvin: float = 6500.0) -> Dict[str, Any]:
    """Linear RGB for a colour temperature (Tanner Helland approximation). No scene change."""
    return {"kelvin": float(kelvin), "rgb_linear": sci.kelvin_to_rgb(float(kelvin)), "rgb_srgb": sci.kelvin_to_rgb(float(kelvin), linear=False)}


@command("light.lux_to_arnold")
def lux_to_arnold(lux: float | None = None, lumens: float | None = None, solid_angle_sr: float = 4 * math.pi) -> Dict[str, Any]:
    """Convert illuminance (lux) to an Arnold distant light intensity, or lumens to a point/area light intensity and exposure."""
    out: Dict[str, Any] = {"convention": "100000 lux = 1 Arnold irradiance unit; EV 15 = aiExposure 0; 683 lm/W; scene units cm"}
    if lux is not None:
        out["lux"] = float(lux)
        out["distant_intensity"] = round(sci.lux_to_arnold_irradiance(float(lux)), 6)
        out["ev100"] = sci.ev_from_illuminance(float(lux))
        out["camera_ai_exposure"] = sci.exposure_value_to_arnold(out["ev100"])
    if lumens is not None:
        out["point_light"] = sci.lumens_to_arnold_intensity(float(lumens), solid_angle_sr=float(solid_angle_sr))
    if lux is None and lumens is None:
        raise BridgeError("pass lux and/or lumens")
    return out
