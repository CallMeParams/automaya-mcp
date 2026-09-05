"""arnold.* commands: lights, render settings, AOVs, per object Arnold attributes, rendering.

Every command except ``arnold.status`` calls ``_arnold()`` first, which loads mtoa or
raises a BridgeError explaining how to get it.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError, require_maya

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

MTOA_HINT = "Install Arnold for Maya (MtoA) and enable 'mtoa' in Windows > Settings > Plug-in Manager, then retry."
OPTIONS = "defaultArnoldRenderOptions"
DRIVER = "defaultArnoldDriver"
FILTER = "defaultArnoldFilter"
GLOBALS = "defaultRenderGlobals"
RESOLUTION = "defaultResolution"

LIGHT_TYPES = {
    "area": "aiAreaLight",
    "skydome": "aiSkyDomeLight",
    "mesh": "aiMeshLight",
    "photometric": "aiPhotometricLight",
    "distant": "directionalLight",
    "directional": "directionalLight",
    "point": "pointLight",
    "spot": "spotLight",
}
ARNOLD_LIGHT_NODES = ("aiAreaLight", "aiSkyDomeLight", "aiMeshLight", "aiPhotometricLight", "aiLightPortal")
MAYA_LIGHT_NODES = ("directionalLight", "pointLight", "spotLight", "areaLight", "ambientLight", "volumeLight")

# aiAOV.type enum follows the Arnold AI_TYPE constants.
AOV_TYPES = {"int": 1, "uint": 2, "bool": 3, "float": 4, "rgb": 5, "rgba": 6, "vector": 7, "vector2": 9, "string": 10, "pointer": 11}
AOV_DEFAULT_TYPE = {
    "N": "vector", "P": "vector", "Pref": "vector", "Z": "float", "motionvector": "vector", "AA_inverse_density": "float",
    "ID": "uint", "shadow_matte": "rgba", "volume_opacity": "rgb", "opacity": "rgb", "cputime": "float",
    "raycount": "float", "highlight": "rgb", "rim_light": "rgb", "shadow": "rgb", "shadow_diff": "rgb", "shadow_mask": "rgb",
    "albedo": "rgb", "background": "rgb", "beauty": "rgba", "RGBA": "rgba", "RGB": "rgb",
}
CRYPTO_AOVS = ("crypto_asset", "crypto_object", "crypto_material")
SUBDIV_TYPES = {"none": 0, "catclark": 1, "linear": 2}

# set_ai_attributes parameter -> mesh shape attribute
AI_SHAPE_ATTRS = {
    "subdivision_type": "aiSubdivType",
    "subdivision_iterations": "aiSubdivIterations",
    "subdivision_adaptive_error": "aiSubdivAdaptiveError",
    "opaque": "aiOpaque",
    "matte": "aiMatte",
    "self_shadows": "aiSelfShadows",
    "cast_shadows": "castsShadows",
    "receive_shadows": "receiveShadows",
    "visible_in_camera": "primaryVisibility",
    "visible_in_diffuse_reflection": "aiVisibleInDiffuseReflection",
    "visible_in_specular_reflection": "aiVisibleInSpecularReflection",
    "visible_in_diffuse_transmission": "aiVisibleInDiffuseTransmission",
    "visible_in_specular_transmission": "aiVisibleInSpecularTransmission",
    "visible_in_volume": "aiVisibleInVolume",
    "displacement_height": "aiDispHeight",
    "displacement_padding": "aiDispPadding",
    "displacement_zero_value": "aiDispZeroValue",
    "displacement_autobump": "aiDispAutobump",
    "motion_blur": "motionBlur",
}


# helpers ------------------------------------------------------------------
def _arnold() -> None:
    """Load mtoa (or raise) and make sure the Arnold globals nodes exist."""
    require_maya()
    _util.ensure_plugin("mtoa", MTOA_HINT)
    if not cmds.objExists(OPTIONS):
        try:
            import mtoa.core as core  # type: ignore

            core.createOptions()
        except Exception:
            pass


def _use_arnold_renderer() -> None:
    try:
        cmds.setAttr(GLOBALS + ".currentRenderer", "arnold", type="string")
    except Exception as exc:
        raise BridgeError("could not set the current renderer to arnold: %s" % exc)


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


def _images_dir() -> str:
    root = cmds.workspace(query=True, rootDirectory=True) or ""
    rule = cmds.workspace(fileRuleEntry="images") or "images"
    return os.path.normpath(os.path.join(root, rule)) if root else os.path.abspath(rule)


def _files_newer_than(folder: str, since: float) -> List[str]:
    found: List[str] = []
    if not os.path.isdir(folder):
        return found
    for dirpath, _dirs, files in os.walk(folder):
        for fn in files:
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(path) >= since - 1.0 and not fn.startswith("."):
                    found.append(path)
            except OSError:
                continue
    found.sort(key=lambda p: os.path.getmtime(p))
    return found


def _render_seq(frames: str, camera: str | None, width: int | None, height: int | None) -> None:
    """Run MtoA's arnoldRender for a frame string like '1' or '1-24'."""
    kwargs: Dict[str, Any] = {"seq": frames}
    if camera:
        kwargs["camera"] = camera
    if width:
        kwargs["width"] = int(width)
    if height:
        kwargs["height"] = int(height)
    try:
        cmds.arnoldRender(**kwargs)
        return
    except TypeError:
        pass
    except AttributeError:
        pass
    if mel is None:
        raise BridgeError("arnoldRender is unavailable; is mtoa loaded?")
    parts = ['arnoldRender -seq "%s"' % frames]
    if camera:
        parts.append('-cam "%s"' % camera)
    if width:
        parts.append("-w %d" % int(width))
    if height:
        parts.append("-h %d" % int(height))
    try:
        mel.eval(" ".join(parts))
    except RuntimeError as exc:
        raise BridgeError("arnoldRender failed: %s" % exc)


def _camera_shape(camera: str | None) -> str | None:
    if not camera:
        return None
    _util.require_nodes([camera])
    shapes = _util.shapes_of(camera, "camera")
    if shapes:
        return shapes[0]
    if cmds.nodeType(camera) == "camera":
        return camera
    raise BridgeError("%s is not a camera" % camera)


def _image_result(path: str | None, extra: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(extra)
    out["path"] = path
    if path and os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg"):
        try:
            out["image_base64"] = _util.read_file_base64(path)
            out["format"] = "jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "png"
        except OSError:
            pass
    return out


# commands -----------------------------------------------------------------
@command("arnold.status")
def status() -> Dict[str, Any]:
    """Whether mtoa is loaded, its version and whether Arnold is the current renderer. Never raises."""
    require_maya()
    loaded = bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
    if not loaded:
        try:
            cmds.loadPlugin("mtoa", quiet=True)
            loaded = bool(cmds.pluginInfo("mtoa", query=True, loaded=True))
        except Exception:
            loaded = False
    info: Dict[str, Any] = {"loaded": loaded, "current_renderer": _safe_get(GLOBALS + ".currentRenderer")}
    info["renderer_is_arnold"] = info["current_renderer"] == "arnold"
    if loaded:
        try:
            info["mtoa_version"] = cmds.pluginInfo("mtoa", query=True, version=True)
        except Exception:
            info["mtoa_version"] = None
        try:
            info["arnold_version"] = cmds.arnoldPlugins(getVersion=True) if hasattr(cmds, "arnoldPlugins") else None
        except Exception:
            info["arnold_version"] = None
        info["options_node"] = cmds.objExists(OPTIONS)
    else:
        info["hint"] = MTOA_HINT
    return info


@command("arnold.create_light", mutates=True)
def create_light(
    type: str = "area",
    name: str | None = None,
    intensity: float | None = None,
    exposure: float | None = None,
    color: Sequence[float] | None = None,
    translate: Sequence[float] | None = None,
    rotate: Sequence[float] | None = None,
    scale: Sequence[float] | None = None,
    hdri_path: str | None = None,
    samples: int | None = None,
    cast_shadows: bool | None = None,
    mesh: str | None = None,
) -> Dict[str, Any]:
    """Create an Arnold or Maya light. ``hdri_path`` wires a Raw file texture into a skydome; ``mesh`` is required for mesh lights."""
    _arnold()
    _use_arnold_renderer()
    if type not in LIGHT_TYPES:
        raise BridgeError("unknown light type %r. Use one of: %s" % (type, ", ".join(sorted(LIGHT_TYPES))))
    node_type = LIGHT_TYPES[type]
    warnings: List[str] = []
    base = name or (type + "Light1")
    extra: Dict[str, Any] = {}
    if node_type == "aiMeshLight":
        if not mesh:
            raise BridgeError("mesh lights need 'mesh': the transform or mesh shape to emit from")
        _util.require_nodes([mesh])
        mesh_shapes = _util.shapes_of(mesh, "mesh")
        if not mesh_shapes:
            raise BridgeError("%s has no mesh shape" % mesh)
        mesh_shape = mesh_shapes[0]
        transform = _util.transform_of(mesh_shape)
        shape = cmds.createNode("aiMeshLight", name=base + "Shape", parent=transform)
        cmds.connectAttr(mesh_shape + ".outMesh", shape + ".inMesh", force=True)
        _safe_set(shape, "lightVisible", True, warnings)
        extra["mesh_shape"] = mesh_shape
    elif node_type.startswith("ai"):
        shape = cmds.shadingNode(node_type, asLight=True, name=base + "Shape")
        transform = _util.transform_of(shape)
        transform = cmds.rename(transform, base) if transform != shape else transform
        shapes = _util.shapes_of(transform)
        shape = shapes[0] if shapes else shape
    else:
        maker = getattr(cmds, node_type)
        made = maker(name=base)
        made = made[0] if isinstance(made, (list, tuple)) and made else made
        shape = made if cmds.nodeType(made) == node_type else (_util.shapes_of(made, node_type) or [made])[0]
        transform = _util.transform_of(shape)
    is_ai = node_type.startswith("ai")
    if intensity is not None:
        _safe_set(shape, "intensity", float(intensity), warnings)
    if exposure is not None:
        _safe_set(shape, "exposure" if is_ai else "aiExposure", float(exposure), warnings)
    if color is not None:
        _safe_set(shape, "color", list(color), warnings)
    if samples is not None:
        _safe_set(shape, "aiSamples", int(samples), warnings)
    if cast_shadows is not None:
        _safe_set(shape, "aiCastShadows", bool(cast_shadows), warnings)
        if not is_ai:
            _safe_set(shape, "useRayTraceShadows", bool(cast_shadows), warnings)
    if translate is not None:
        _safe_set(transform, "translate", list(translate), warnings)
    if rotate is not None:
        _safe_set(transform, "rotate", list(rotate), warnings)
    if scale is not None:
        _safe_set(transform, "scale", list(scale), warnings)
    if hdri_path:
        if node_type != "aiSkyDomeLight":
            warnings.append("hdri_path is only used for skydome lights")
        else:
            nodes = _util.create_file_texture(hdri_path, color_space="Raw", name=base + "_hdri")
            cmds.connectAttr(nodes["file"] + ".outColor", shape + ".color", force=True)
            extra["hdri_file_node"] = nodes["file"]
            extra["hdri_exists_on_disk"] = os.path.isfile(os.path.expandvars(hdri_path))
    out = {"transform": _util.long_name(transform), "shape": _util.long_name(shape), "type": type, "node_type": node_type, "warnings": warnings}
    out.update(extra)
    return out


@command("arnold.list_lights")
def list_lights() -> Dict[str, Any]:
    """All Arnold and Maya lights with intensity, exposure, colour and transform."""
    require_maya()
    shapes: List[str] = []
    for node_type in ARNOLD_LIGHT_NODES + MAYA_LIGHT_NODES:
        try:
            shapes.extend(cmds.ls(type=node_type, long=True) or [])
        except Exception:
            continue
    lights: List[Dict[str, Any]] = []
    for shape in sorted(set(shapes)):
        node_type = cmds.nodeType(shape)
        transform = _util.transform_of(shape)
        is_ai = node_type.startswith("ai")
        entry: Dict[str, Any] = {
            "transform": transform, "shape": shape, "node_type": node_type,
            "intensity": _safe_get(shape + ".intensity"),
            "exposure": _safe_get(shape + (".exposure" if is_ai else ".aiExposure")),
            "color": _safe_get(shape + ".color"),
            "translate": _safe_get(transform + ".translate"),
            "rotate": _safe_get(transform + ".rotate"),
        }
        if node_type == "aiSkyDomeLight":
            src = cmds.listConnections(shape + ".color", source=True, destination=False, type="file") or []
            entry["hdri"] = _safe_get(src[0] + ".fileTextureName") if src else None
        lights.append(entry)
    return {"count": len(lights), "lights": lights}


@command("arnold.set_render_settings", mutates=True)
def set_render_settings(
    camera_aa: int | None = None,
    diffuse: int | None = None,
    specular: int | None = None,
    transmission: int | None = None,
    sss: int | None = None,
    volume: int | None = None,
    adaptive: bool | None = None,
    max_aa: int | None = None,
    threshold: float | None = None,
    denoiser: str | None = None,
    width: int | None = None,
    height: int | None = None,
    start_frame: float | None = None,
    end_frame: float | None = None,
    animation: bool | None = None,
    image_format: str | None = None,
    output_prefix: str | None = None,
    motion_blur: bool | None = None,
    camera: str | None = None,
    ray_depth_total: int | None = None,
) -> Dict[str, Any]:
    """Set Arnold sampling, resolution, frame range, output format/prefix, motion blur, denoiser and renderable camera."""
    _arnold()
    _use_arnold_renderer()
    warnings: List[str] = []
    applied: Dict[str, Any] = {}

    def _opt(attr: str, value: Any, node: str = OPTIONS) -> None:
        if _safe_set(node, attr, value, warnings):
            applied["%s.%s" % (node, attr)] = value

    if camera_aa is not None:
        _opt("AASamples", int(camera_aa))
    if diffuse is not None:
        _opt("GIDiffuseSamples", int(diffuse))
    if specular is not None:
        _opt("GISpecularSamples", int(specular))
    if transmission is not None:
        _opt("GITransmissionSamples", int(transmission))
    if sss is not None:
        _opt("GISssSamples", int(sss))
    if volume is not None:
        _opt("GIVolumeSamples", int(volume))
    if adaptive is not None:
        _opt("enableAdaptiveSampling", bool(adaptive))
    if max_aa is not None:
        _opt("AASamplesMax", int(max_aa))
    if threshold is not None:
        _opt("AAAdaptiveThreshold", float(threshold))
    if ray_depth_total is not None:
        _opt("GITotalDepth", int(ray_depth_total))
    if motion_blur is not None:
        _opt("motion_blur_enable", bool(motion_blur))
    if denoiser is not None:
        applied["denoiser"] = _set_denoiser(denoiser.lower(), warnings)
    if width is not None:
        _opt("width", int(width), RESOLUTION)
    if height is not None:
        _opt("height", int(height), RESOLUTION)
    if width is not None or height is not None:
        w = int(width) if width is not None else _safe_get(RESOLUTION + ".width")
        h = int(height) if height is not None else _safe_get(RESOLUTION + ".height")
        if w and h:
            _opt("deviceAspectRatio", float(w) / float(h), RESOLUTION)
            _opt("pixelAspect", 1.0, RESOLUTION)
    if start_frame is not None:
        _opt("startFrame", float(start_frame), GLOBALS)
    if end_frame is not None:
        _opt("endFrame", float(end_frame), GLOBALS)
    if animation is not None or start_frame is not None or end_frame is not None:
        anim = bool(animation) if animation is not None else True
        _opt("animation", anim, GLOBALS)
        if anim:
            _opt("outFormatControl", 0, GLOBALS)
            _opt("putFrameBeforeExt", True, GLOBALS)
            _opt("periodInExt", 1, GLOBALS)
            _opt("extensionPadding", 4, GLOBALS)
    if image_format is not None:
        fmt = image_format.lower().lstrip(".")
        fmt = {"jpg": "jpeg", "tiff": "tif"}.get(fmt, fmt)
        if fmt not in ("exr", "png", "jpeg", "tif", "deepexr", "maya"):
            raise BridgeError("image_format must be exr, png, jpeg or tif")
        _opt("aiTranslator", fmt, DRIVER)
        if fmt == "exr":
            _opt("mergeAOVs", True, DRIVER)
    if output_prefix is not None:
        _opt("imageFilePrefix", output_prefix, GLOBALS)
    if camera is not None:
        cam_shape = _camera_shape(camera)
        for other in cmds.ls(type="camera", long=True) or []:
            _safe_set(other, "renderable", other == cam_shape or other.endswith("|" + cam_shape) or cam_shape.endswith(other), warnings)
        _safe_set(cam_shape, "renderable", True, warnings)
        applied["renderable_camera"] = cam_shape
    return {"applied": applied, "warnings": warnings, "settings": get_render_settings()}


def _set_denoiser(mode: str, warnings: List[str]) -> str:
    if mode not in ("none", "oidn", "optix"):
        raise BridgeError("denoiser must be none, oidn or optix")
    _safe_set(OPTIONS, "denoiseBeauty", mode == "optix", warnings)
    existing = [n for n in (cmds.ls(type="aiImagerDenoiserOidn") or [])]
    if mode == "oidn":
        node = existing[0] if existing else cmds.createNode("aiImagerDenoiserOidn", name="aiImagerDenoiserOidn1")
        connected = cmds.listConnections(OPTIONS + ".imagers", source=True, destination=False) or []
        if node not in connected:
            try:
                cmds.connectAttr(node + ".message", "%s.imagers[%d]" % (OPTIONS, len(connected)), force=True)
            except Exception as exc:
                warnings.append("could not connect the OIDN imager: %s" % exc)
        _safe_set(node, "enable", True, warnings)
    else:
        for node in existing:
            _safe_set(node, "enable", False, warnings)
    return mode


@command("arnold.get_render_settings")
def get_render_settings() -> Dict[str, Any]:
    """Current Arnold sampling, adaptive, resolution, frame range, output and motion blur settings."""
    _arnold()
    cams = []
    for shape in cmds.ls(type="camera", long=True) or []:
        if _safe_get(shape + ".renderable"):
            cams.append(shape)
    return {
        "renderer": _safe_get(GLOBALS + ".currentRenderer"),
        "sampling": {
            "camera_aa": _safe_get(OPTIONS + ".AASamples"),
            "diffuse": _safe_get(OPTIONS + ".GIDiffuseSamples"),
            "specular": _safe_get(OPTIONS + ".GISpecularSamples"),
            "transmission": _safe_get(OPTIONS + ".GITransmissionSamples"),
            "sss": _safe_get(OPTIONS + ".GISssSamples"),
            "volume": _safe_get(OPTIONS + ".GIVolumeSamples"),
            "adaptive": _safe_get(OPTIONS + ".enableAdaptiveSampling"),
            "max_aa": _safe_get(OPTIONS + ".AASamplesMax"),
            "threshold": _safe_get(OPTIONS + ".AAAdaptiveThreshold"),
        },
        "ray_depth": {
            "total": _safe_get(OPTIONS + ".GITotalDepth"),
            "diffuse": _safe_get(OPTIONS + ".GIDiffuseDepth"),
            "specular": _safe_get(OPTIONS + ".GISpecularDepth"),
            "transmission": _safe_get(OPTIONS + ".GITransmissionDepth"),
        },
        "motion_blur": _safe_get(OPTIONS + ".motion_blur_enable"),
        "denoise_beauty": _safe_get(OPTIONS + ".denoiseBeauty"),
        "resolution": {"width": _safe_get(RESOLUTION + ".width"), "height": _safe_get(RESOLUTION + ".height")},
        "frame_range": {"start": _safe_get(GLOBALS + ".startFrame"), "end": _safe_get(GLOBALS + ".endFrame"), "animation": _safe_get(GLOBALS + ".animation")},
        "image_format": _safe_get(DRIVER + ".aiTranslator"),
        "output_prefix": _safe_get(GLOBALS + ".imageFilePrefix"),
        "images_dir": _images_dir(),
        "renderable_cameras": cams,
        "aov_count": len(cmds.ls(type="aiAOV") or []),
    }


@command("arnold.render_frame", mutates=False)
def render_frame(camera: str | None = None, width: int | None = None, height: int | None = None, frame: float | None = None, output_path: str | None = None) -> Dict[str, Any]:
    """Render one frame with Arnold to the project images folder. Returns the image for png/jpeg, else the path."""
    _arnold()
    _use_arnold_renderer()
    cam_shape = _camera_shape(camera)
    if cam_shape is None:
        renderable = [c for c in (cmds.ls(type="camera", long=True) or []) if _safe_get(c + ".renderable")]
        cam_shape = renderable[0] if renderable else "perspShape"
    if frame is not None:
        cmds.currentTime(float(frame), edit=True)
    else:
        frame = cmds.currentTime(query=True) or 1.0
    if output_path:
        ext = os.path.splitext(output_path)[1].lower().lstrip(".")
        if ext in ("exr", "png", "jpg", "jpeg", "tif"):
            _util.set_attr_value(DRIVER, "aiTranslator", {"jpg": "jpeg"}.get(ext, ext))
    started = time.time()
    frame_str = str(int(frame)) if float(frame).is_integer() else str(frame)
    _render_seq(frame_str, cam_shape, width, height)
    elapsed = time.time() - started
    images_dir = _images_dir()
    new_files = _files_newer_than(images_dir, started)
    path = new_files[-1] if new_files else None
    if path and output_path:
        target = os.path.expandvars(os.path.expanduser(output_path))
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        shutil.copyfile(path, target)
        path = target
    out = _image_result(path, {"camera": cam_shape, "frame": frame, "elapsed_s": round(elapsed, 2), "images_dir": images_dir, "width": width or _safe_get(RESOLUTION + ".width"), "height": height or _safe_get(RESOLUTION + ".height")})
    if path is None:
        out["note"] = "arnoldRender finished but no new file appeared in %s; check the Arnold render log (maya_get_console_log) or set output_prefix" % images_dir
    return out


@command("arnold.render_sequence", mutates=False)
def render_sequence(start: float | None = None, end: float | None = None, camera: str | None = None, width: int | None = None, height: int | None = None, step: int = 1) -> Dict[str, Any]:
    """Render a frame range with Arnold (Render Sequence). Returns the written file paths."""
    _arnold()
    _use_arnold_renderer()
    cam_shape = _camera_shape(camera)
    start = float(start) if start is not None else float(_safe_get(GLOBALS + ".startFrame") or cmds.playbackOptions(query=True, minTime=True))
    end = float(end) if end is not None else float(_safe_get(GLOBALS + ".endFrame") or cmds.playbackOptions(query=True, maxTime=True))
    if end < start:
        raise BridgeError("end frame must be >= start frame")
    warnings: List[str] = []
    _safe_set(GLOBALS, "animation", True, warnings)
    _safe_set(GLOBALS, "startFrame", start, warnings)
    _safe_set(GLOBALS, "endFrame", end, warnings)
    _safe_set(GLOBALS, "byFrameStep", float(step), warnings)
    started = time.time()
    if start == end:
        seq = "%d" % int(start)
    elif int(step) > 1:
        seq = "%d-%dx%d" % (int(start), int(end), int(step))
    else:
        seq = "%d-%d" % (int(start), int(end))
    _render_seq(seq, cam_shape, width, height)
    files = _files_newer_than(_images_dir(), started)
    return {"start": start, "end": end, "step": step, "camera": cam_shape, "elapsed_s": round(time.time() - started, 2), "frame_count": len(files), "paths": files, "images_dir": _images_dir(), "warnings": warnings}


@command("arnold.create_aov", mutates=True)
def create_aov(name: str, data_type: str | None = None, enabled: bool = True) -> Dict[str, Any]:
    """Add an AOV (diffuse, specular, N, Z, crypto_object ...) using the mtoa AOV interface, with a manual fallback."""
    _arnold()
    _use_arnold_renderer()
    if not name:
        raise BridgeError("name is required, e.g. diffuse, specular, N, Z, crypto_object")
    data_type = (data_type or AOV_DEFAULT_TYPE.get(name, "rgb")).lower()
    if data_type not in AOV_TYPES:
        raise BridgeError("data_type must be one of: %s" % ", ".join(sorted(AOV_TYPES)))
    for node in cmds.ls(type="aiAOV") or []:
        if _safe_get(node + ".name") == name:
            _util.set_attr_value(node, "enabled", bool(enabled))
            return {"aov": name, "node": node, "data_type": data_type, "existing": True}
    node: str | None = None
    via = "manual"
    try:
        from mtoa.aovs import AOVInterface  # type: ignore

        aov = AOVInterface().addAOV(name, aovType=data_type)
        node = getattr(aov, "node", None) or str(aov)
        via = "mtoa.aovs"
    except Exception:
        node = None
    if not node:
        node = cmds.createNode("aiAOV", name="aiAOV_" + name, skipSelect=True)
        cmds.setAttr(node + ".name", name, type="string")
        cmds.setAttr(node + ".type", AOV_TYPES[data_type])
        existing = cmds.listConnections(OPTIONS + ".aovList", source=True, destination=False) or []
        cmds.connectAttr(node + ".message", "%s.aovList[%d]" % (OPTIONS, len(existing)), force=True)
        cmds.connectAttr(DRIVER + ".message", node + ".outputs[0].driver", force=True)
        cmds.connectAttr(FILTER + ".message", node + ".outputs[0].filter", force=True)
    _util.set_attr_value(node, "enabled", bool(enabled))
    note = None
    if name in CRYPTO_AOVS:
        note = "cryptomatte needs an exr driver with merged AOVs; set image_format='exr'"
        try:
            cmds.setAttr(DRIVER + ".aiTranslator", "exr", type="string")
            cmds.setAttr(DRIVER + ".mergeAOVs", 1)
        except Exception:
            pass
    return {"aov": name, "node": node, "data_type": data_type, "via": via, "existing": False, "note": note}


@command("arnold.list_aovs")
def list_aovs() -> Dict[str, Any]:
    """Every aiAOV node with its name, type and enabled flag."""
    _arnold()
    type_names = {v: k for k, v in AOV_TYPES.items()}
    aovs = []
    for node in cmds.ls(type="aiAOV") or []:
        t = _safe_get(node + ".type")
        aovs.append({"node": node, "name": _safe_get(node + ".name"), "data_type": type_names.get(t, t), "enabled": _safe_get(node + ".enabled")})
    return {"count": len(aovs), "aovs": aovs}


@command("arnold.set_ai_attributes", mutates=True)
def set_ai_attributes(nodes: Sequence[str] | None = None, **options: Any) -> Dict[str, Any]:
    """Set per shape Arnold attributes: subdivision (type none/catclark/linear, iterations), opaque, matte,
    visibility flags, self shadows, displacement height/padding/zero value. Works on transforms or shapes."""
    _arnold()
    unknown = [k for k in options if k not in AI_SHAPE_ATTRS]
    if unknown:
        raise BridgeError("unknown option(s) %s. Supported: %s" % (", ".join(unknown), ", ".join(sorted(AI_SHAPE_ATTRS))))
    if not options:
        raise BridgeError("nothing to set; pass at least one option such as subdivision_type='catclark'")
    targets = _util.resolve_targets(nodes)
    values: Dict[str, Any] = {}
    for key, value in options.items():
        if key == "subdivision_type":
            if isinstance(value, str):
                if value.lower() not in SUBDIV_TYPES:
                    raise BridgeError("subdivision_type must be none, catclark or linear")
                value = SUBDIV_TYPES[value.lower()]
        values[AI_SHAPE_ATTRS[key]] = value
    results: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for target in targets:
        shapes = _util.shapes_of(target) or [target]
        for shape in shapes:
            done = [attr for attr, value in values.items() if _safe_set(shape, attr, value, warnings)]
            results.append({"shape": shape, "set": done})
    return {"shapes": results, "values": values, "warnings": warnings}
