"""lookdev.* commands: measured materials, variation, wear, colour management, render presets, material audit.

Arnold (mtoa) first: aiStandardSurface with aiNoise/aiCellNoise/aiTriplanar
break-up and aiCurvature/aiAmbientOcclusion wear through aiLayerShader or
aiMixShader. Without mtoa the material commands fall back to standardSurface
(same attribute names) and skip the Arnold utility nodes, reporting
``"path": "maya"``. Values come from ``_science.MEASURED_MATERIALS``.
"""
from __future__ import annotations

import os
import random
import sys
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
PBR_TYPES = ("aiStandardSurface", "standardSurface", "openPBRSurface")
LEGACY_TYPES = ("lambert", "blinn", "phong", "phongE")

# OCIO configs shipped with Maya 2024 (relative to MAYA_LOCATION).
OCIO_PROFILES: Dict[str, Dict[str, Any]] = {
    "aces13": {"config": "resources/OCIO-configs/Maya2022-default/config.ocio", "view": "ACES 1.0 SDR-video (sRGB)", "rendering_space": "ACEScg", "notes": "ACES 1.3 config shipped with Maya 2022 to 2024"},
    "aces2": {"config": "resources/OCIO-configs/Maya2022-default/config.ocio", "view": "ACES 2.0 SDR 100 nits (Rec.709)", "rendering_space": "ACEScg", "fallback_view": "ACES 1.0 SDR-video (sRGB)", "notes": "ACES 2.0 views exist from Maya 2025.3; falls back to the ACES 1.0 view on Maya 2024"},
    "srgb": {"config": "resources/OCIO-configs/Maya-legacy/config.ocio", "view": "sRGB gamma", "rendering_space": "scene-linear Rec.709-sRGB", "notes": "legacy scene-linear sRGB pipeline, no tone mapping"},
}

RENDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "preview": {"camera_aa": 3, "diffuse": 1, "specular": 1, "transmission": 1, "sss": 1, "volume": 0, "adaptive": False, "denoiser": "none", "image_format": "png", "motion_blur": False, "ray_depth_total": 6, "aovs": []},
    "production": {"camera_aa": 5, "diffuse": 2, "specular": 2, "transmission": 2, "sss": 2, "volume": 2, "adaptive": True, "max_aa": 8, "threshold": 0.03, "denoiser": "oidn", "image_format": "exr", "motion_blur": True, "ray_depth_total": 10, "aovs": ["diffuse", "specular", "N", "Z"]},
    "final": {"camera_aa": 8, "diffuse": 3, "specular": 3, "transmission": 3, "sss": 3, "volume": 2, "adaptive": True, "max_aa": 16, "threshold": 0.015, "denoiser": "oidn", "image_format": "exr", "motion_blur": True, "ray_depth_total": 12, "aovs": ["diffuse", "specular", "coat", "sss", "emission", "N", "Z", "crypto_object", "crypto_material"]},
}

# science table key -> shader attribute
MATERIAL_ATTRS = {"roughness": "specularRoughness", "metalness": "metalness", "ior": "specularIOR", "coat": "coat", "sss": "subsurface", "transmission": "transmission"}


# helpers ------------------------------------------------------------------
def _has_mtoa() -> bool:
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


def _get(plug: str, default: Any = None) -> Any:
    try:
        value = cmds.getAttr(plug)
    except Exception:
        return default
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        return [float(v) for v in value[0]]
    if isinstance(value, tuple):
        return [float(v) for v in value]
    return value


def _scalar(plug: str, default: float) -> float:
    value = _get(plug, default)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _colour(plug: str, default: Sequence[float] = (0.5, 0.5, 0.5)) -> List[float]:
    value = _get(plug, None)
    if isinstance(value, list) and len(value) >= 3:
        return [float(v) for v in value[:3]]
    return list(default)


def _shading_groups(material: str) -> List[str]:
    try:
        return cmds.listConnections(material + ".outColor", type="shadingEngine", source=False, destination=True) or []
    except Exception:
        return []


def _new_shader(shader_type: str, name: str) -> Dict[str, str]:
    shader = cmds.shadingNode(shader_type, asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    return {"material": shader, "shading_group": sg}


def _assign(sg: str, targets: Sequence[str]) -> List[str]:
    _util.require_nodes(list(targets))
    cmds.sets(list(targets), edit=True, forceElement=sg)
    return list(targets)


def _mesh_has_uvs(node: str) -> bool:
    shapes = _util.shapes_of(node, "mesh") or [node]
    for shape in shapes:
        try:
            count = cmds.polyEvaluate(shape, uvcoord=True)
        except Exception:
            continue
        if isinstance(count, (int, float)) and count > 0:
            return True
    return False


def _maya_location() -> str:
    loc = os.environ.get("MAYA_LOCATION")
    if loc:
        return loc
    exe = sys.executable or ""
    if exe:
        return os.path.dirname(os.path.dirname(exe))
    return ""


def _apply_measured(shader: str, spec: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    applied: Dict[str, Any] = {}
    glassy = spec["transmission"] >= 0.9
    if _safe_set(shader, "base", 0.0 if glassy else 1.0, warnings):
        applied["base"] = 0.0 if glassy else 1.0
    if _safe_set(shader, "baseColor", spec["baseColor"], warnings):
        applied["baseColor"] = spec["baseColor"]
    for key, attr in MATERIAL_ATTRS.items():
        if _safe_set(shader, attr, float(spec[key]), warnings):
            applied[attr] = float(spec[key])
    if spec["sss"] > 0:
        _safe_set(shader, "subsurfaceColor", spec["baseColor"], warnings)
        _safe_set(shader, "subsurfaceRadius", [1.0, 0.35, 0.2] if spec["name"] == "skin" else [1.0, 1.0, 1.0], warnings)
    if spec["coat"] > 0:
        _safe_set(shader, "coatRoughness", 0.1, warnings)
    if spec["name"] == "fabric":
        _safe_set(shader, "sheen", 0.5, warnings)
        _safe_set(shader, "sheenRoughness", 0.4, warnings)
    return applied


# commands -----------------------------------------------------------------
@command("lookdev.measured_material", mutates=True)
def measured_material(
    name: str = "concrete",
    material_name: str | None = None,
    assign_to: Sequence[str] | None = None,
    breakup: float = 0.0,
    breakup_scale: float = 1.0,
    cell_noise: bool = False,
    triplanar: str = "auto",
    color_override: Sequence[float] | None = None,
) -> Dict[str, Any]:
    """aiStandardSurface (standardSurface without Arnold) from the measured table, with optional aiNoise/aiCellNoise
    break-up into colour and roughness and aiTriplanar projection (auto: when the assigned mesh has no UVs)."""
    require_maya()
    try:
        spec = sci.measured_material(name)
    except KeyError as exc:
        raise BridgeError(str(exc.args[0]))
    if color_override is not None:
        spec["baseColor"] = [float(v) for v in color_override]
    warnings: List[str] = []
    use_arnold = _has_mtoa()
    shader_type = "aiStandardSurface" if use_arnold else "standardSurface"
    made = _new_shader(shader_type, material_name or (spec["name"] + "_mat"))
    shader, sg = made["material"], made["shading_group"]
    applied = _apply_measured(shader, spec, warnings)
    assigned = _assign(sg, assign_to) if assign_to else []
    triplanar = (triplanar or "auto").lower()
    if triplanar not in ("auto", "on", "off"):
        raise BridgeError("triplanar must be auto, on or off")
    want_triplanar = triplanar == "on" or (triplanar == "auto" and assigned and not any(_mesh_has_uvs(t) for t in assigned))
    utility: Dict[str, Any] = {}
    if use_arnold and (breakup > 0 or want_triplanar):
        base = spec["baseColor"]
        amount = max(0.0, min(1.0, float(breakup)))
        noise_type = "aiCellNoise" if cell_noise else "aiNoise"
        noise = cmds.shadingNode(noise_type, asTexture=True, name=shader + "_" + ("cellNoise" if cell_noise else "noise"))
        utility["noise"] = noise
        _safe_set(noise, "scale", [float(breakup_scale)] * 3, warnings)
        if cell_noise:
            _safe_set(noise, "octaves", 3, warnings)
        else:
            _safe_set(noise, "octaves", 4, warnings)
            _safe_set(noise, "coordSpace", 2, warnings)  # world, so it does not need UVs
        _safe_set(noise, "color1", [max(0.0, v * (1.0 - amount * 0.5)) for v in base], warnings)
        _safe_set(noise, "color2", [min(1.0, v * (1.0 + amount * 0.5)) for v in base], warnings)
        colour_source = noise + ".outColor"
        if want_triplanar:
            tri = cmds.shadingNode("aiTriplanar", asTexture=True, name=shader + "_triplanar")
            utility["triplanar"] = tri
            _safe_set(tri, "blend", 0.3, warnings)
            _safe_set(tri, "scale", [float(breakup_scale)] * 3, warnings)
            cmds.connectAttr(noise + ".outColor", tri + ".input", force=True)
            colour_source = tri + ".outColor"
        if amount > 0 or want_triplanar:
            cmds.connectAttr(colour_source, shader + ".baseColor", force=True)
            utility["baseColor_from"] = colour_source
        if amount > 0:
            rough = float(spec["roughness"])
            rough_noise = cmds.shadingNode("aiNoise", asTexture=True, name=shader + "_roughNoise")
            utility["roughness_noise"] = rough_noise
            _safe_set(rough_noise, "scale", [float(breakup_scale) * 2.0] * 3, warnings)
            _safe_set(rough_noise, "coordSpace", 2, warnings)
            lo, hi = max(0.0, rough - amount * 0.25), min(1.0, rough + amount * 0.25)
            _safe_set(rough_noise, "color1", [lo] * 3, warnings)
            _safe_set(rough_noise, "color2", [hi] * 3, warnings)
            cmds.connectAttr(rough_noise + ".outColorR", shader + ".specularRoughness", force=True)
            utility["roughness_from"] = rough_noise + ".outColorR"
    elif breakup > 0 or want_triplanar:
        warnings.append("mtoa unavailable: break-up and triplanar need aiNoise/aiTriplanar, applied flat values only. " + MTOA_HINT)
    return {
        "path": "arnold" if use_arnold else "maya", "material": shader, "shading_group": sg, "type": shader_type, "preset": spec["name"], "values": applied,
        "notes": spec["notes"], "assigned": assigned, "triplanar": bool(want_triplanar and use_arnold), "utility_nodes": utility, "warnings": warnings,
    }


@command("lookdev.material_variation", mutates=True)
def material_variation(material: str, count: int = 5, hue_jitter: float = 8.0, value_jitter: float = 0.1, roughness_jitter: float = 0.1, seed: int = 0, assign_to: Sequence[str] | None = None) -> Dict[str, Any]:
    """``count`` copies of a PBR shader with jittered hue (degrees), value and roughness; optional round robin assignment to nodes."""
    require_maya()
    _util.require_nodes([material])
    shader_type = cmds.nodeType(material)
    if shader_type not in PBR_TYPES:
        raise BridgeError("%s is a %s; variation needs aiStandardSurface or standardSurface (materials.convert first)" % (material, shader_type))
    count = int(count)
    if count < 1 or count > 200:
        raise BridgeError("count must be 1 to 200")
    rng = random.Random(int(seed))
    base_color = _colour(material + ".baseColor")
    base_rough = _scalar(material + ".specularRoughness", 0.5)
    copy_attrs = {attr: _get("%s.%s" % (material, attr)) for attr in ("base", "metalness", "specularIOR", "coat", "coatRoughness", "subsurface", "transmission", "specular", "sheen")}
    warnings: List[str] = []
    variants: List[Dict[str, Any]] = []
    targets = list(assign_to) if assign_to else []
    if targets:
        _util.require_nodes(targets)
    for i in range(count):
        h, s, v = sci.rgb_to_hsv(base_color)
        h = (h + rng.uniform(-float(hue_jitter), float(hue_jitter))) % 360.0
        v = max(0.0, min(1.0, v * (1.0 + rng.uniform(-float(value_jitter), float(value_jitter)))))
        colour = sci.hsv_to_rgb([h, s, v])
        rough = max(0.0, min(1.0, base_rough + rng.uniform(-float(roughness_jitter), float(roughness_jitter))))
        made = _new_shader(shader_type, "%s_var%02d" % (material, i + 1))
        shader = made["material"]
        _safe_set(shader, "baseColor", colour, warnings)
        _safe_set(shader, "specularRoughness", rough, warnings)
        for attr, value in copy_attrs.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                _safe_set(shader, attr, float(value), warnings)
        assigned = []
        if targets:
            mine = [t for j, t in enumerate(targets) if j % count == i]
            if mine:
                cmds.sets(mine, edit=True, forceElement=made["shading_group"])
                assigned = mine
        variants.append({"material": shader, "shading_group": made["shading_group"], "baseColor": colour, "roughness": round(rough, 4), "assigned": assigned})
    return {"source": material, "type": shader_type, "count": count, "seed": seed, "variants": variants, "warnings": warnings}


@command("lookdev.wear", mutates=True)
def wear(
    material: str,
    edge_amount: float = 0.5,
    dirt_amount: float = 0.5,
    edge_color: Sequence[float] | None = None,
    dirt_color: Sequence[float] | None = None,
    edge_radius: float = 1.0,
    dirt_distance: float = 20.0,
    edge_metal: bool | None = None,
) -> Dict[str, Any]:
    """Layer edge wear (aiCurvature mask) and dirt (aiAmbientOcclusion mask) over a material. Two masks use aiLayerShader,
    one mask uses aiMixShader. The shading group is rewired to the new top shader. Arnold only."""
    require_maya()
    _util.require_nodes([material])
    _require_mtoa("wear")
    edge_amount, dirt_amount = max(0.0, min(1.0, float(edge_amount))), max(0.0, min(1.0, float(dirt_amount)))
    if edge_amount <= 0 and dirt_amount <= 0:
        raise BridgeError("edge_amount or dirt_amount must be above 0")
    warnings: List[str] = []
    base_color = _colour(material + ".baseColor")
    metal = _scalar(material + ".metalness", 0.0) >= 0.5
    edge_metal = metal if edge_metal is None else bool(edge_metal)
    layers: List[Dict[str, Any]] = []
    if edge_amount > 0:
        curvature = cmds.shadingNode("aiCurvature", asTexture=True, name=material + "_edgeMask")
        _safe_set(curvature, "output", 0, warnings)  # convex edges
        _safe_set(curvature, "radius", float(edge_radius), warnings)
        _safe_set(curvature, "samples", 4, warnings)
        _safe_set(curvature, "multiply", edge_amount * 2.0, warnings)
        edge = cmds.shadingNode("aiStandardSurface", asShader=True, name=material + "_edgeShader")
        if edge_color is None:
            edge_rgb = [0.56, 0.57, 0.58] if edge_metal else [min(1.0, v * 1.5 + 0.1) for v in base_color]
        else:
            edge_rgb = [float(v) for v in edge_color]
        _safe_set(edge, "baseColor", edge_rgb, warnings)
        _safe_set(edge, "metalness", 1.0 if edge_metal else 0.0, warnings)
        _safe_set(edge, "specularRoughness", 0.35 if edge_metal else 0.6, warnings)
        layers.append({"kind": "edge", "mask": curvature, "mask_plug": curvature + ".outColorR", "shader": edge, "amount": edge_amount, "color": edge_rgb})
    if dirt_amount > 0:
        ao = cmds.shadingNode("aiAmbientOcclusion", asShader=True, name=material + "_dirtMask")
        _safe_set(ao, "samples", 3, warnings)
        _safe_set(ao, "farClip", float(dirt_distance), warnings)
        _safe_set(ao, "spread", 0.9, warnings)
        # Occluded areas should read as mask 1: swap the AO colours and scale by amount.
        _safe_set(ao, "white", [0.0, 0.0, 0.0], warnings)
        _safe_set(ao, "black", [dirt_amount] * 3, warnings)
        dirt = cmds.shadingNode("aiStandardSurface", asShader=True, name=material + "_dirtShader")
        dirt_rgb = [float(v) for v in dirt_color] if dirt_color is not None else [0.06, 0.05, 0.04]
        _safe_set(dirt, "baseColor", dirt_rgb, warnings)
        _safe_set(dirt, "specularRoughness", 0.9, warnings)
        _safe_set(dirt, "specular", 0.2, warnings)
        layers.append({"kind": "dirt", "mask": ao, "mask_plug": ao + ".outColorR", "shader": dirt, "amount": dirt_amount, "color": dirt_rgb})
    if len(layers) == 1:
        top = cmds.shadingNode("aiMixShader", asShader=True, name=material + "_wearMix")
        cmds.connectAttr(material + ".outColor", top + ".shader1", force=True)
        cmds.connectAttr(layers[0]["shader"] + ".outColor", top + ".shader2", force=True)
        cmds.connectAttr(layers[0]["mask_plug"], top + ".mix", force=True)
        top_type = "aiMixShader"
    else:
        top = cmds.shadingNode("aiLayerShader", asShader=True, name=material + "_wearLayers")
        cmds.connectAttr(material + ".outColor", top + ".input1", force=True)
        _safe_set(top, "enable1", True, warnings)
        _safe_set(top, "name1", "base", warnings)
        for i, layer in enumerate(layers, start=2):
            cmds.connectAttr(layer["shader"] + ".outColor", "%s.input%d" % (top, i), force=True)
            cmds.connectAttr(layer["mask_plug"], "%s.mix%d" % (top, i), force=True)
            _safe_set(top, "enable%d" % i, True, warnings)
            _safe_set(top, "name%d" % i, layer["kind"], warnings)
        top_type = "aiLayerShader"
    groups = _shading_groups(material)
    for sg in groups:
        cmds.connectAttr(top + ".outColor", sg + ".surfaceShader", force=True)
    if not groups:
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=top + "SG")
        cmds.connectAttr(top + ".outColor", sg + ".surfaceShader", force=True)
        groups = [sg]
        warnings.append("%s had no shading group; created %s, assign it with materials.assign" % (material, sg))
    return {"path": "arnold", "material": material, "top_shader": top, "top_type": top_type, "layers": layers, "shading_groups": groups, "warnings": warnings}


@command("lookdev.color_management", mutates=True)
def color_management(profile: str = "aces13", view_transform: str | None = None, rendering_space: str | None = None, config_path: str | None = None) -> Dict[str, Any]:
    """Enable colour management with the ACES 1.3 config Maya 2024 ships (aces13), ACES 2 views when present (aces2) or legacy sRGB (srgb),
    and set the view transform and rendering space."""
    require_maya()
    profile = (profile or "aces13").lower().replace(".", "").replace("_", "")
    if profile not in OCIO_PROFILES:
        raise BridgeError("profile must be aces13, aces2 or srgb")
    preset = OCIO_PROFILES[profile]
    location = _maya_location()
    path = config_path or os.path.join(location, preset["config"]).replace("\\", "/")
    exists = os.path.isfile(path)
    warnings: List[str] = []
    if not exists:
        warnings.append("OCIO config not found on disk at %s (MAYA_LOCATION=%r); Maya may still resolve it, check the Color Management preferences" % (path, location))
    try:
        cmds.colorManagementPrefs(edit=True, cmEnabled=True)
        cmds.colorManagementPrefs(edit=True, cmConfigFileEnabled=True)
        cmds.colorManagementPrefs(edit=True, configFilePath=path)
    except Exception as exc:
        raise BridgeError("could not load the OCIO config %s: %s" % (path, exc))
    space = rendering_space or preset["rendering_space"]
    try:
        cmds.colorManagementPrefs(edit=True, renderingSpaceName=space)
    except Exception as exc:
        warnings.append("rendering space %r rejected: %s" % (space, exc))
    view = view_transform or preset["view"]
    used_view = view
    try:
        cmds.colorManagementPrefs(edit=True, viewTransform=view)
    except Exception as exc:
        fallback = preset.get("fallback_view")
        if fallback and not view_transform:
            warnings.append("view %r unavailable (%s); using %r" % (view, exc, fallback))
            try:
                cmds.colorManagementPrefs(edit=True, viewTransform=fallback)
                used_view = fallback
            except Exception as exc2:
                raise BridgeError("neither view %r nor %r is available: %s" % (view, fallback, exc2))
        else:
            raise BridgeError("view transform %r is not in this config: %s. Query maya_execute_python cmds.colorManagementPrefs(q=True, viewTransformNames=True)" % (view, exc))
    try:
        cmds.colorManagementPrefs(edit=True, outputTransformEnabled=True)
        cmds.colorManagementPrefs(edit=True, outputUseViewTransform=True)
    except Exception:
        pass
    return {"profile": profile, "config_path": path, "config_exists": exists, "view_transform": used_view, "rendering_space": space, "notes": preset["notes"], "warnings": warnings}


@command("lookdev.render_preset", mutates=True)
def render_preset(quality: str = "preview", width: int | None = None, height: int | None = None, camera: str | None = None) -> Dict[str, Any]:
    """Consistent Arnold sampling, adaptive, denoiser, format and AOVs for preview, production or final. Arnold only."""
    require_maya()
    quality = (quality or "preview").lower()
    if quality not in RENDER_PRESETS:
        raise BridgeError("quality must be preview, production or final")
    _require_mtoa("render_preset")
    from . import arnold

    preset = dict(RENDER_PRESETS[quality])
    aovs = preset.pop("aovs")
    if width is not None:
        preset["width"] = int(width)
    if height is not None:
        preset["height"] = int(height)
    if camera is not None:
        preset["camera"] = camera
    settings = arnold.set_render_settings(**preset)
    created = []
    for aov in aovs:
        try:
            created.append(arnold.create_aov(aov))
        except BridgeError as exc:
            settings["warnings"].append("AOV %s: %s" % (aov, exc))
    warnings = settings.get("warnings", [])
    lock = _safe_set(OPTIONS, "lock_sampling_pattern", quality != "preview", warnings)
    return {"path": "arnold", "quality": quality, "applied": settings["applied"], "aovs": [a["aov"] for a in created], "lock_sampling_pattern": lock and quality != "preview", "warnings": warnings}


@command("lookdev.material_report")
def material_report(include_defaults: bool = False) -> Dict[str, Any]:
    """Audit every shader for implausible values: albedo above 0.9 or nearly black, dark metals, roughness 0 dielectrics, odd IOR, legacy shader types."""
    require_maya()
    shaders = sorted(set(cmds.ls(materials=True) or []))
    defaults = {"lambert1", "standardSurface1", "particleCloud1", "shaderGlow1", "openPBRSurface1"}
    items: List[Dict[str, Any]] = []
    for shader in shaders:
        if not include_defaults and shader in defaults:
            continue
        kind = cmds.nodeType(shader)
        entry: Dict[str, Any] = {"material": shader, "type": kind, "issues": []}
        if kind in PBR_TYPES:
            colour = _colour(shader + ".baseColor")
            rough = _scalar(shader + ".specularRoughness", 0.5)
            metal = _scalar(shader + ".metalness", 0.0)
            ior = _scalar(shader + ".specularIOR", 1.5)
            trans = _scalar(shader + ".transmission", 0.0)
            entry.update({"baseColor": colour, "albedo": round(sci.luminance(colour), 4), "roughness": rough, "metalness": metal, "ior": ior, "transmission": trans})
            entry["issues"] = sci.material_issues(kind, colour, rough, metal, ior, trans)
        elif kind in LEGACY_TYPES:
            colour = _colour(shader + ".color")
            entry.update({"baseColor": colour, "albedo": round(sci.luminance(colour), 4)})
            entry["issues"] = sci.material_issues(kind, colour, None, 0.0, None) + ["legacy %s shader; convert to aiStandardSurface with materials.convert for physically based response" % kind]
        else:
            entry["skipped"] = True
        items.append(entry)
    flagged = [i for i in items if i.get("issues")]
    return {"count": len(items), "flagged_count": len(flagged), "flagged": flagged, "materials": items}


@command("lookdev.material_library")
def material_library() -> Dict[str, Any]:
    """The measured material table with sources. No scene change."""
    return {"count": len(sci.MEASURED_MATERIALS), "names": sci.material_names(), "aliases": dict(sci.MATERIAL_ALIASES), "materials": {k: dict(v) for k, v in sci.MEASURED_MATERIALS.items()}}
