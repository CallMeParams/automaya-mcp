"""previs.* commands: cameras, lenses, shot rigs, playblasts, camera sequencer.

Lens maths uses real sensor sizes: Maya stores film apertures in inches, so
sensor millimetres are divided by 25.4 on the way in and multiplied back on
the way out. Field of view is computed from focal length and aperture with
fov = 2 * atan(aperture / (2 * focal)).
"""
from __future__ import annotations

import math
import os
import tempfile
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

MM_PER_INCH = 25.4
FILM_FITS = {"fill": 0, "horizontal": 1, "vertical": 2, "overscan": 3}
FILM_FIT_NAMES = {v: k for k, v in FILM_FITS.items()}
IMAGE_PLANE_FITS = {"fill": 0, "best": 1, "horizontal": 2, "vertical": 3, "to_size": 4}
DISPLAY_MODES = ("wireframe", "smoothShaded", "flatShaded", "boundingBox", "points")
PLAYBLAST_FORMATS = ("image", "qt", "avfoundation", "avi", "movie")
STARTUP_CAMERAS = ("persp", "top", "front", "side")


# helpers ----------------------------------------------------------------------
def _vec3(value: Any, label: str) -> List[float]:
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise BridgeError("%s must be a list of 3 numbers, got %r" % (label, value)) from None
    if len(out) != 3:
        raise BridgeError("%s must have exactly 3 numbers, got %d" % (label, len(out)))
    return out


def _choice(value: str, allowed: Sequence[str], label: str) -> str:
    if value not in allowed:
        raise BridgeError("%s must be one of %s, got %r" % (label, ", ".join(allowed), value))
    return value


def _camera_pair(camera: str) -> tuple:
    """Return (transform, shape) for a camera transform or shape name."""
    _util.require_nodes([camera])
    if cmds.nodeType(camera) == "camera":
        return _util.transform_of(camera), camera
    shapes = _util.shapes_of(camera, "camera")
    if not shapes:
        raise BridgeError("%s is not a camera (no camera shape). previs.list_cameras shows the cameras in the scene." % camera)
    return camera, shapes[0]


def _f(plug: str) -> float:
    value = cmds.getAttr(plug)
    if isinstance(value, (list, tuple)):
        value = value[0]
        if isinstance(value, (list, tuple)):
            value = value[0]
    return float(value or 0.0)


def _point_of(target: Any, label: str) -> List[float]:
    """A world position from a node name or an [x, y, z] list."""
    if isinstance(target, str):
        _util.require_nodes([target])
        pos = cmds.xform(target, query=True, worldSpace=True, rotatePivot=True) or cmds.xform(target, query=True, worldSpace=True, translation=True)
        return [float(v) for v in (pos or [0.0, 0.0, 0.0])[:3]]
    return _vec3(target, label)


def _aim_camera(transform: str, eye: Sequence[float], look_at: Sequence[float], up: Sequence[float] = (0.0, 1.0, 0.0)) -> None:
    """Place and orient a camera without needing a viewport (viewPlace works in batch too)."""
    cmds.viewPlace(transform, eye=tuple(eye), lookAt=tuple(look_at), upDirection=tuple(up))


def _model_panels() -> List[str]:
    if cmds.about(batch=True):
        return []
    visible = cmds.getPanel(visiblePanels=True) or []
    return [p for p in visible if cmds.getPanel(typeOf=p) == "modelPanel"]


def _pick_panel(panel: str | None) -> str:
    if panel:
        if cmds.getPanel(typeOf=panel) != "modelPanel":
            raise BridgeError("%s is not a model panel" % panel)
        return panel
    focused = cmds.getPanel(withFocus=True)
    if focused and cmds.getPanel(typeOf=focused) == "modelPanel":
        return focused
    panels = _model_panels()
    if not panels:
        raise BridgeError("no visible model panel; this needs the Maya GUI with a viewport open (not mayapy batch)")
    return panels[0]


def _fov_deg(aperture_in: float, focal_mm: float) -> float:
    if focal_mm <= 0:
        return 0.0
    return math.degrees(2.0 * math.atan((aperture_in * MM_PER_INCH) / (2.0 * focal_mm)))


def _shots_for_camera(transform: str, shape: str) -> List[str]:
    out = []
    for shot in cmds.ls(type="shot") or []:
        cam = cmds.shot(shot, query=True, currentCamera=True)
        if cam and (cam == transform or cam == shape or cam.split("|")[-1] in (transform.split("|")[-1], shape.split("|")[-1])):
            out.append(shot)
    return out


def _notes_of(transform: str) -> str | None:
    if cmds.attributeQuery("notes", node=transform, exists=True):
        return cmds.getAttr(transform + ".notes")
    return None


# camera creation and lenses ----------------------------------------------------
@command("previs.create_camera", mutates=True)
def create_camera(name: str = "shotCam", focal_length: float = 35.0, sensor_width: float = 36.0, sensor_height: float | None = None, aspect: float | None = None,
                  near_clip: float = 1.0, far_clip: float = 100000.0, translate: List[float] | None = None, rotate: List[float] | None = None, aim: Any = None,
                  film_fit: str = "horizontal", display_resolution: bool = True, display_film_gate: bool = False, display_safe_action: bool = True,
                  display_safe_title: bool = False, overscan: float = 1.3, locked: bool = False, notes: str | None = None) -> Dict[str, Any]:
    """Create a previs camera with a real sensor size (mm), lens, clip planes and gate display."""
    _util.require_maya()
    if not name:
        raise BridgeError("name is required")
    if focal_length <= 0 or sensor_width <= 0:
        raise BridgeError("focal_length and sensor_width must be positive millimetres")
    if sensor_height is None:
        ratio = float(aspect) if aspect else (16.0 / 9.0)
        sensor_height = float(sensor_width) / ratio
    if sensor_height <= 0:
        raise BridgeError("sensor_height must be positive")
    if near_clip <= 0 or far_clip <= near_clip:
        raise BridgeError("clip planes need 0 < near_clip < far_clip")
    _choice(film_fit, tuple(FILM_FITS), "film_fit")
    result = cmds.camera(
        name=name,
        focalLength=float(focal_length),
        horizontalFilmAperture=float(sensor_width) / MM_PER_INCH,
        verticalFilmAperture=float(sensor_height) / MM_PER_INCH,
        nearClipDistance=float(near_clip),
        farClipDistance=float(far_clip),
        filmFit=film_fit,
        overscan=float(overscan),
        displayResolution=bool(display_resolution),
        displayFilmGate=bool(display_film_gate),
        lockTransform=bool(locked),
    ) or []
    transform = result[0] if len(result) > 0 else name
    shape = result[1] if len(result) > 1 else transform + "Shape"
    if transform != name:
        transform = cmds.rename(transform, name) or name
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        shape = shapes[0] if shapes else shape
    cmds.setAttr(shape + ".displaySafeAction", 1 if display_safe_action else 0)
    cmds.setAttr(shape + ".displaySafeTitle", 1 if display_safe_title else 0)
    cmds.setAttr(shape + ".displayGateMask", 1)
    if translate is not None:
        t = _vec3(translate, "translate")
        cmds.xform(transform, worldSpace=True, translation=t)
    if rotate is not None:
        r = _vec3(rotate, "rotate")
        cmds.xform(transform, worldSpace=True, rotation=r)
    if aim is not None:
        eye = _vec3(translate, "translate") if translate is not None else [0.0, 0.0, 0.0]
        _aim_camera(transform, eye, _point_of(aim, "aim"))
    if notes:
        shot_notes(transform, notes)
    if locked:
        for ch in ("translate", "rotate", "scale"):
            cmds.setAttr("%s.%s" % (transform, ch), lock=True)
    return camera_info(transform)


@command("previs.create_shot_camera_rig", mutates=True)
def create_shot_camera_rig(name: str = "shotCam", rig_type: str = "aim", focal_length: float = 35.0, sensor_width: float = 36.0, sensor_height: float | None = None,
                           translate: List[float] | None = None, aim: Any = None, arm_length: float = 300.0, lock_channels: bool = True) -> Dict[str, Any]:
    """Build a camera rig: 'aim' (shot_ctrl > cam group + aim/up locators) or 'crane' (dolly > base > arm > head > camera)."""
    _util.require_maya()
    _choice(rig_type, ("aim", "crane"), "rig_type")
    if not name:
        raise BridgeError("name is required")
    cam = create_camera(name=name + "_cam", focal_length=focal_length, sensor_width=sensor_width, sensor_height=sensor_height, film_fit="horizontal")
    cam_t = cam["camera"]
    ctrl = cmds.group(empty=True, name=name + "_shot_ctrl") or (name + "_shot_ctrl")
    nodes: Dict[str, str] = {"shot_ctrl": ctrl, "camera": cam_t, "camera_shape": cam["shape"]}
    eye = _vec3(translate, "translate") if translate is not None else [0.0, 150.0, 500.0]
    look = _point_of(aim, "aim") if aim is not None else [0.0, 100.0, 0.0]

    if rig_type == "aim":
        cam_grp = cmds.group(empty=True, name=name + "_cam_grp", parent=ctrl) or (name + "_cam_grp")
        cmds.parent(cam_t, cam_grp)
        cmds.xform(cam_t, worldSpace=True, translation=eye)
        aim_loc = (cmds.spaceLocator(name=name + "_aim") or [name + "_aim"])[0]
        up_loc = (cmds.spaceLocator(name=name + "_up") or [name + "_up"])[0]
        cmds.parent(aim_loc, ctrl)
        cmds.parent(up_loc, ctrl)
        cmds.xform(aim_loc, worldSpace=True, translation=look)
        cmds.xform(up_loc, worldSpace=True, translation=[eye[0], eye[1] + 100.0, eye[2]])
        con = cmds.aimConstraint(aim_loc, cam_t, aimVector=(0, 0, -1), upVector=(0, 1, 0), worldUpType="object", worldUpObject=up_loc, maintainOffset=False) or []
        nodes.update({"cam_grp": cam_grp, "aim": aim_loc, "up": up_loc, "aim_constraint": con[0] if con else None})
        if lock_channels:
            for ch in ("rotateX", "rotateY", "rotateZ"):
                cmds.setAttr("%s.%s" % (cam_t, ch), keyable=False)
        animatable = {"shot_ctrl": "translate/rotate (whole rig)", "cam_grp": "translate (dolly/boom)", "camera": "translate (local slide)", "aim": "translate (where the camera looks)", "camera_shape": "focalLength, focusDistance"}
    else:
        dolly = cmds.group(empty=True, name=name + "_dolly", parent=ctrl) or (name + "_dolly")
        base = cmds.group(empty=True, name=name + "_crane_base", parent=dolly) or (name + "_crane_base")
        arm = cmds.group(empty=True, name=name + "_arm", parent=base) or (name + "_arm")
        head = cmds.group(empty=True, name=name + "_head", parent=arm) or (name + "_head")
        cmds.xform(head, objectSpace=True, translation=[float(arm_length), 0.0, 0.0])
        cmds.parent(cam_t, head)
        cmds.xform(cam_t, objectSpace=True, translation=[0.0, 0.0, 0.0], rotation=[0.0, -90.0, 0.0])
        cmds.xform(dolly, worldSpace=True, translation=[eye[0], 0.0, eye[2]])
        cmds.xform(base, objectSpace=True, translation=[0.0, eye[1], 0.0])
        nodes.update({"dolly": dolly, "crane_base": base, "arm": arm, "head": head})
        if lock_channels:
            _lock_all_but(dolly, ("translateX", "translateY", "translateZ"))
            _lock_all_but(base, ("rotateY",))
            _lock_all_but(arm, ("rotateZ",))
            _lock_all_but(head, ("rotateX", "rotateY"))
            _lock_all_but(cam_t, ())
        animatable = {"dolly": "translate (track)", "crane_base": "rotateY (swing)", "arm": "rotateZ (boom up/down)", "head": "rotateY pan, rotateX tilt", "camera_shape": "focalLength, focusDistance"}
    nodes = {k: (_util.long_name(v) if isinstance(v, str) else v) for k, v in nodes.items()}
    return {"rig_type": rig_type, "nodes": nodes, "animatable": animatable, "camera_info": camera_info(nodes["camera"])}


def _lock_all_but(node: str, keep: Sequence[str]) -> None:
    for ch in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ", "visibility"):
        if ch not in keep:
            cmds.setAttr("%s.%s" % (node, ch), lock=True, keyable=False, channelBox=False)


@command("previs.set_lens", mutates=True)
def set_lens(camera: str, focal_length: float | None = None, field_of_view: float | None = None, f_stop: float | None = None,
             focus_distance: float | None = None, depth_of_field: bool | None = None) -> Dict[str, Any]:
    """Set focal length (or horizontal field of view in degrees), f-stop, focus distance and DOF on a camera."""
    _util.require_maya()
    transform, shape = _camera_pair(camera)
    if focal_length is not None and field_of_view is not None:
        raise BridgeError("pass focal_length or field_of_view, not both")
    if field_of_view is not None:
        if not 0 < field_of_view < 180:
            raise BridgeError("field_of_view must be between 0 and 180 degrees")
        hfa_mm = _f(shape + ".horizontalFilmAperture") * MM_PER_INCH
        focal_length = (hfa_mm / 2.0) / math.tan(math.radians(field_of_view) / 2.0)
    if focal_length is not None:
        if focal_length <= 0:
            raise BridgeError("focal_length must be positive")
        cmds.setAttr(shape + ".focalLength", float(focal_length))
    if f_stop is not None:
        if f_stop <= 0:
            raise BridgeError("f_stop must be positive")
        cmds.setAttr(shape + ".fStop", float(f_stop))
    if focus_distance is not None:
        if focus_distance <= 0:
            raise BridgeError("focus_distance must be positive")
        cmds.setAttr(shape + ".focusDistance", float(focus_distance))
    if depth_of_field is not None:
        cmds.setAttr(shape + ".depthOfField", 1 if depth_of_field else 0)
    return camera_info(transform)


@command("previs.list_cameras")
def list_cameras(include_default: bool = False) -> Dict[str, Any]:
    """List scene cameras (skips persp/top/front/side unless include_default)."""
    _util.require_maya()
    out = []
    for shape in cmds.ls(type="camera", long=True) or []:
        transform = _util.transform_of(shape)
        short = transform.split("|")[-1]
        is_default = bool(cmds.camera(shape, query=True, startupCamera=True)) or short in STARTUP_CAMERAS
        if is_default and not include_default:
            continue
        entry: Dict[str, Any] = {"camera": transform, "shape": shape, "default": is_default}
        try:
            entry["focal_length"] = _f(shape + ".focalLength")
            entry["renderable"] = bool(cmds.getAttr(shape + ".renderable"))
            entry["position"] = cmds.xform(transform, query=True, worldSpace=True, translation=True)
            entry["shots"] = _shots_for_camera(transform, shape)
        except Exception:
            pass
        out.append(entry)
    return {"cameras": out, "count": len(out)}


@command("previs.look_through", mutates=True)
def look_through(camera: str, panel: str | None = None) -> Dict[str, Any]:
    """Make a viewport look through a camera (the focused model panel by default)."""
    _util.require_maya()
    transform, shape = _camera_pair(camera)
    target_panel = _pick_panel(panel)
    cmds.modelPanel(target_panel, edit=True, camera=transform)
    return {"panel": target_panel, "camera": transform, "shape": shape}


@command("previs.frame", mutates=True)
def frame(camera: str | None = None, nodes: List[str] | None = None, all: bool = False, fit_factor: float = 0.9) -> Dict[str, Any]:
    """Frame nodes (or everything, or the selection) in a camera via viewFit."""
    _util.require_maya()
    kwargs: Dict[str, Any] = {"fitFactor": float(fit_factor), "animate": False}
    args: List[str] = []
    if camera:
        transform, _ = _camera_pair(camera)
        args.append(transform)
    if all:
        kwargs["allObjects"] = True
    elif nodes:
        args.extend(_util.require_nodes(nodes))
    else:
        if not (cmds.ls(selection=True) or []):
            raise BridgeError("nothing to frame: pass nodes, all=true, or select something")
    cmds.viewFit(*args, **kwargs)
    return {"camera": camera, "framed": "all" if all else (nodes or "selection"), "fit_factor": float(fit_factor)}


# playblast and viewport --------------------------------------------------------
@command("previs.playblast", mutates=True)
def playblast(camera: str | None = None, start: float | None = None, end: float | None = None, width: int = 1920, height: int = 1080, format: str = "image",
              filename: str | None = None, quality: int = 100, percent: int = 100, offscreen: bool = True, show_ornaments: bool = False,
              frame: float | None = None, compression: str | None = None, panel: str | None = None) -> Dict[str, Any]:
    """Playblast a single frame (returned as a PNG image) or a range (returns the movie/sequence path)."""
    _util.require_maya()
    _choice(format, PLAYBLAST_FORMATS, "format")
    if width <= 0 or height <= 0:
        raise BridgeError("width and height must be positive")
    if not 1 <= percent <= 100:
        raise BridgeError("percent must be 1..100")
    target_panel = None
    if camera:
        transform, _ = _camera_pair(camera)
        target_panel = _pick_panel(panel)
        cmds.modelPanel(target_panel, edit=True, camera=transform)
    elif panel:
        target_panel = _pick_panel(panel)
    common: Dict[str, Any] = {
        "viewer": False,
        "offScreen": bool(offscreen),
        "widthHeight": (int(width), int(height)),
        "percent": int(percent),
        "showOrnaments": bool(show_ornaments),
        "forceOverwrite": True,
        "quality": int(quality),
    }
    if target_panel:
        common["editorPanelName"] = target_panel
    folder = os.path.join(tempfile.gettempdir(), "automaya", "playblast")
    os.makedirs(folder, exist_ok=True)
    if frame is not None:
        path = filename or os.path.join(folder, "frame_%04d.png" % int(frame))
        if not path.lower().endswith(".png"):
            path += ".png"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cmds.playblast(format="image", compression="png", completeFilename=path, frame=[float(frame)], **common)
        if not os.path.isfile(path):
            raise BridgeError("playblast did not write %s; check that a viewport is available (GUI session, not batch)" % path)
        return {"image_base64": _util.read_file_base64(path), "format": "png", "width": int(width * percent / 100), "height": int(height * percent / 100), "path": path, "frame": float(frame), "camera": camera}
    if start is None:
        start = cmds.playbackOptions(query=True, minTime=True)
    if end is None:
        end = cmds.playbackOptions(query=True, maxTime=True)
    if end < start:
        raise BridgeError("end must be >= start")
    if format == "image":
        comp = compression or "png"
        base = filename or os.path.join(folder, "playblast")
    else:
        comp = compression or "H.264"
        base = filename or os.path.join(folder, "playblast")
    result = cmds.playblast(format=format, compression=comp, filename=base, startTime=float(start), endTime=float(end), sequenceTime=False, clearCache=True, **common)
    return {"path": result if isinstance(result, str) else base, "format": format, "compression": comp, "start": float(start), "end": float(end), "width": int(width), "height": int(height), "camera": camera, "panel": target_panel}


@command("previs.viewport_settings", mutates=True)
def viewport_settings(panel: str | None = None, display_mode: str | None = None, textures: bool | None = None, lights: str | None = None, shadows: bool | None = None,
                      wireframe_on_shaded: bool | None = None, grid: bool | None = None, hud: bool | None = None, aa: bool | None = None, ao: bool | None = None,
                      motion_blur: bool | None = None, hide: List[str] | None = None, show: List[str] | None = None) -> Dict[str, Any]:
    """Tune a viewport: shading, textures, lights, shadows, grid, HUD, AA/AO/motion blur (Viewport 2.0)."""
    _util.require_maya()
    target_panel = _pick_panel(panel)
    kwargs: Dict[str, Any] = {}
    if display_mode is not None:
        _choice(display_mode, DISPLAY_MODES, "display_mode")
        kwargs["displayAppearance"] = display_mode
    if textures is not None:
        kwargs["displayTextures"] = bool(textures)
    if lights is not None:
        _choice(lights, ("default", "all", "selected", "flat", "none", "active"), "lights")
        kwargs["displayLights"] = lights
    if shadows is not None:
        kwargs["shadows"] = bool(shadows)
    if wireframe_on_shaded is not None:
        kwargs["wireframeOnShaded"] = bool(wireframe_on_shaded)
    if grid is not None:
        kwargs["grid"] = bool(grid)
    if hud is not None:
        kwargs["headsUpDisplay"] = bool(hud)
    for flag_list, value in ((hide, False), (show, True)):
        for kind in flag_list or []:
            kwargs[kind] = value
    if kwargs:
        try:
            cmds.modelEditor(target_panel, edit=True, **kwargs)
        except TypeError as exc:
            raise BridgeError("modelEditor rejected a flag (%s). hide/show take modelEditor object flags such as cameras, locators, joints, nurbsCurves, polymeshes, imagePlane" % exc) from None
    hw = "hardwareRenderingGlobals"
    applied: Dict[str, Any] = dict(kwargs)
    if aa is not None:
        cmds.setAttr(hw + ".multiSampleEnable", 1 if aa else 0)
        if aa:
            cmds.setAttr(hw + ".multiSampleCount", 8)
        applied["multiSampleEnable"] = bool(aa)
    if ao is not None:
        cmds.setAttr(hw + ".ssaoEnable", 1 if ao else 0)
        applied["ssaoEnable"] = bool(ao)
    if motion_blur is not None:
        cmds.setAttr(hw + ".motionBlurEnable", 1 if motion_blur else 0)
        applied["motionBlurEnable"] = bool(motion_blur)
    return {"panel": target_panel, "applied": applied}


@command("previs.set_resolution", mutates=True)
def set_resolution(width: int, height: int, pixel_aspect: float = 1.0, device_aspect: float | None = None) -> Dict[str, Any]:
    """Set render resolution (defaultResolution), pixel aspect and device aspect."""
    _util.require_maya()
    if width <= 0 or height <= 0 or pixel_aspect <= 0:
        raise BridgeError("width, height and pixel_aspect must be positive")
    if device_aspect is None:
        device_aspect = (float(width) / float(height)) * float(pixel_aspect)
    cmds.setAttr("defaultResolution.aspectLock", 0)
    cmds.setAttr("defaultResolution.width", int(width))
    cmds.setAttr("defaultResolution.height", int(height))
    cmds.setAttr("defaultResolution.pixelAspect", float(pixel_aspect))
    cmds.setAttr("defaultResolution.deviceAspectRatio", float(device_aspect))
    return {"width": int(width), "height": int(height), "pixel_aspect": float(pixel_aspect), "device_aspect": round(float(device_aspect), 6)}


# camera sequencer ----------------------------------------------------------------
def _shot_info(shot: str) -> Dict[str, Any]:
    q = {}
    for key, flag in (("start", "startTime"), ("end", "endTime"), ("sequence_start", "sequenceStartTime"), ("sequence_end", "sequenceEndTime"), ("camera", "currentCamera"), ("track", "track"), ("shot_name", "shotName"), ("scale", "scale")):
        try:
            q[key] = cmds.shot(shot, query=True, **{flag: True})
        except Exception:
            q[key] = None
    q["node"] = shot
    if q.get("start") is not None and q.get("end") is not None:
        q["duration"] = float(q["end"]) - float(q["start"]) + 1.0
    return q


@command("previs.create_sequence_shot", mutates=True)
def create_sequence_shot(name: str, start: float, end: float, sequence_start: float | None = None, camera: str | None = None, track: int | None = None) -> Dict[str, Any]:
    """Create a Camera Sequencer shot covering a scene frame range, placed at a sequence time."""
    _util.require_maya()
    if not name:
        raise BridgeError("name is required")
    if end < start:
        raise BridgeError("end must be >= start")
    kwargs: Dict[str, Any] = {"startTime": float(start), "endTime": float(end), "shotName": name}
    seq_start = float(sequence_start) if sequence_start is not None else float(start)
    kwargs["sequenceStartTime"] = seq_start
    kwargs["sequenceEndTime"] = seq_start + (float(end) - float(start))
    if camera:
        transform, _ = _camera_pair(camera)
        kwargs["currentCamera"] = transform
    if track is not None:
        kwargs["track"] = int(track)
    shot = cmds.shot(name, **kwargs) or name
    return _shot_info(shot)


@command("previs.list_shots")
def list_shots() -> Dict[str, Any]:
    """All Camera Sequencer shots with scene/sequence ranges and cameras, sorted by sequence start."""
    _util.require_maya()
    shots = [_shot_info(s) for s in (cmds.ls(type="shot") or [])]
    shots.sort(key=lambda s: (s.get("sequence_start") is None, s.get("sequence_start") or 0.0))
    return {"shots": shots, "count": len(shots)}


@command("previs.camera_sequencer_info")
def camera_sequencer_info() -> Dict[str, Any]:
    """Camera Sequencer state: current shot, sequence time, sequencer nodes and every shot."""
    _util.require_maya()
    info: Dict[str, Any] = {}
    for key, flag in (("current_time", "currentTime"), ("current_shot", "currentShot"), ("sequencers", "listSequencers"), ("shots", "listShots")):
        try:
            info[key] = cmds.sequenceManager(query=True, **{flag: True})
        except Exception:
            info[key] = None
    info["shot_details"] = list_shots()["shots"]
    if info["shot_details"]:
        starts = [s["sequence_start"] for s in info["shot_details"] if s.get("sequence_start") is not None]
        ends = [s["sequence_end"] for s in info["shot_details"] if s.get("sequence_end") is not None]
        if starts and ends:
            info["sequence_range"] = [min(starts), max(ends)]
    return info


# image planes, locators, measuring ----------------------------------------------
@command("previs.add_image_plane", mutates=True)
def add_image_plane(camera: str, path: str, depth: float = 100.0, alpha: float = 1.0, fit: str = "best", offset: List[float] | None = None,
                    only_in_camera: bool = True, sequence: bool = False) -> Dict[str, Any]:
    """Attach an image (or image sequence) plane to a camera for reference or plate matching."""
    _util.require_maya()
    transform, shape = _camera_pair(camera)
    if not path:
        raise BridgeError("path is required")
    if not os.path.isfile(path) and not sequence:
        raise BridgeError("image file not found: %r" % path)
    _choice(fit, tuple(IMAGE_PLANE_FITS), "fit")
    if not 0.0 <= alpha <= 1.0:
        raise BridgeError("alpha must be 0..1")
    result = cmds.imagePlane(camera=shape, fileName=path.replace("\\", "/"), showInAllViews=not only_in_camera) or []
    ip_t = result[0] if len(result) > 0 else "imagePlane1"
    ip_s = result[1] if len(result) > 1 else ip_t + "Shape"
    cmds.setAttr(ip_s + ".depth", float(depth))
    cmds.setAttr(ip_s + ".alphaGain", float(alpha))
    cmds.setAttr(ip_s + ".fit", IMAGE_PLANE_FITS[fit])
    cmds.setAttr(ip_s + ".displayOnlyIfCurrent", 1 if only_in_camera else 0)
    if offset:
        if len(offset) != 2:
            raise BridgeError("offset is [x, y] in inches of film back")
        cmds.setAttr(ip_s + ".offsetX", float(offset[0]))
        cmds.setAttr(ip_s + ".offsetY", float(offset[1]))
    if sequence:
        cmds.setAttr(ip_s + ".useFrameExtension", 1)
    return {"image_plane": _util.long_name(ip_t), "shape": ip_s, "camera": transform, "path": path, "depth": float(depth), "fit": fit}


@command("previs.create_locator", mutates=True)
def create_locator(name: str = "locator1", pos: List[float] | None = None, parent: str | None = None, size: float = 1.0) -> Dict[str, Any]:
    """Create a locator at a world position (blocking marks, eyelines, hit points)."""
    _util.require_maya()
    loc = (cmds.spaceLocator(name=name) or [name])[0]
    if parent:
        _util.require_nodes([parent])
        cmds.parent(loc, parent)
    if pos is not None:
        cmds.xform(loc, worldSpace=True, translation=_vec3(pos, "pos"))
    if size != 1.0:
        for shp in cmds.listRelatives(loc, shapes=True, fullPath=True) or []:
            cmds.setAttr(shp + ".localScale", float(size), float(size), float(size), type="double3")
    return {"locator": _util.long_name(loc), "position": pos or [0.0, 0.0, 0.0], "parent": parent}


@command("previs.measure_distance")
def measure_distance(a: Any, b: Any) -> Dict[str, Any]:
    """Distance between two nodes or points ([x, y, z]) in scene units."""
    _util.require_maya()
    pa = _point_of(a, "a")
    pb = _point_of(b, "b")
    delta = [pb[i] - pa[i] for i in range(3)]
    dist = math.sqrt(sum(d * d for d in delta))
    unit = cmds.currentUnit(query=True, linear=True)
    return {"distance": round(dist, 4), "delta": [round(d, 4) for d in delta], "a": pa, "b": pb, "unit": unit}


@command("previs.set_camera_key", mutates=True)
def set_camera_key(camera: str, frame: float, translate: List[float] | None = None, rotate: List[float] | None = None, focal_length: float | None = None,
                   focus_distance: float | None = None, tangent: str = "auto") -> Dict[str, Any]:
    """Key a camera's position, rotation, focal length and focus distance at a frame."""
    _util.require_maya()
    transform, shape = _camera_pair(camera)
    if translate is None and rotate is None and focal_length is None and focus_distance is None:
        raise BridgeError("nothing to key: pass translate, rotate, focal_length or focus_distance")
    keyed: List[str] = []

    def key(node: str, attr: str, value: float) -> None:
        cmds.setKeyframe(node, attribute=attr, time=float(frame), value=float(value), inTangentType=tangent, outTangentType=tangent)
        keyed.append("%s.%s" % (node, attr))

    if translate is not None:
        for axis, v in zip("XYZ", _vec3(translate, "translate"), strict=True):
            key(transform, "translate" + axis, v)
    if rotate is not None:
        for axis, v in zip("XYZ", _vec3(rotate, "rotate"), strict=True):
            key(transform, "rotate" + axis, v)
    if focal_length is not None:
        key(shape, "focalLength", focal_length)
    if focus_distance is not None:
        key(shape, "focusDistance", focus_distance)
    return {"camera": transform, "frame": float(frame), "keyed": keyed}


@command("previs.camera_info")
def camera_info(camera: str) -> Dict[str, Any]:
    """Everything a previs artist asks about a camera: lens, fov, sensor, position, aim, clip, DOF, shots, notes."""
    _util.require_maya()
    transform, shape = _camera_pair(camera)
    focal = _f(shape + ".focalLength")
    hfa = _f(shape + ".horizontalFilmAperture")
    vfa = _f(shape + ".verticalFilmAperture")
    pos = [float(v) for v in (cmds.xform(transform, query=True, worldSpace=True, translation=True) or [0, 0, 0])]
    rot = [float(v) for v in (cmds.xform(transform, query=True, worldSpace=True, rotation=True) or [0, 0, 0])]
    matrix = cmds.xform(transform, query=True, worldSpace=True, matrix=True) or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    forward = [-float(matrix[8]), -float(matrix[9]), -float(matrix[10])]
    coi = _f(shape + ".centerOfInterest")
    aim = [round(pos[i] + forward[i] * coi, 4) for i in range(3)]
    film_fit = cmds.getAttr(shape + ".filmFit")
    info: Dict[str, Any] = {
        "camera": transform,
        "shape": shape,
        "focal_length": focal,
        "sensor_mm": {"width": round(hfa * MM_PER_INCH, 3), "height": round(vfa * MM_PER_INCH, 3), "aspect": round(hfa / vfa, 4) if vfa else None},
        "fov": {"horizontal": round(_fov_deg(hfa, focal), 3), "vertical": round(_fov_deg(vfa, focal), 3)},
        "film_fit": FILM_FIT_NAMES.get(film_fit, film_fit),
        "position": [round(v, 4) for v in pos],
        "rotation": [round(v, 4) for v in rot],
        "forward": [round(v, 4) for v in forward],
        "aim": aim,
        "center_of_interest": coi,
        "clip": {"near": _f(shape + ".nearClipDistance"), "far": _f(shape + ".farClipDistance")},
        "dof": {"enabled": bool(cmds.getAttr(shape + ".depthOfField")), "f_stop": _f(shape + ".fStop"), "focus_distance": _f(shape + ".focusDistance")},
        "overscan": _f(shape + ".overscan"),
        "display": {"resolution_gate": bool(cmds.getAttr(shape + ".displayResolution")), "film_gate": bool(cmds.getAttr(shape + ".displayFilmGate")),
                    "safe_action": bool(cmds.getAttr(shape + ".displaySafeAction")), "safe_title": bool(cmds.getAttr(shape + ".displaySafeTitle"))},
        "shots": _shots_for_camera(transform, shape),
        "notes": _notes_of(transform),
    }
    try:
        info["key_count"] = int(cmds.keyframe(transform, query=True, keyframeCount=True) or 0) + int(cmds.keyframe(shape, query=True, keyframeCount=True) or 0)
    except Exception:
        info["key_count"] = None
    return info


@command("previs.shot_notes", mutates=True)
def shot_notes(camera: str, notes: str | None = None, append: bool = False) -> Dict[str, Any]:
    """Read or write free text notes on a camera (stored in a string attribute called notes)."""
    _util.require_maya()
    transform, _ = _camera_pair(camera)
    if notes is None:
        return {"camera": transform, "notes": _notes_of(transform)}
    if not cmds.attributeQuery("notes", node=transform, exists=True):
        cmds.addAttr(transform, longName="notes", dataType="string")
    if append:
        existing = _notes_of(transform) or ""
        notes = (existing + "\n" + notes).strip() if existing else notes
    cmds.setAttr(transform + ".notes", notes, type="string")
    return {"camera": transform, "notes": notes}


# turntable and scene setup ---------------------------------------------------------
@command("previs.create_turntable", mutates=True)
def create_turntable(node: str, frames: int = 120, radius: float | None = None, camera: str | None = None, focal_length: float = 50.0, start: float | None = None,
                     height: float | None = None) -> Dict[str, Any]:
    """Turntable: a camera orbits the node over N frames (linear, cycling), or the node spins when camera is given."""
    _util.require_maya()
    _util.require_nodes([node])
    if frames < 2:
        raise BridgeError("frames must be at least 2")
    start_f = float(start) if start is not None else float(cmds.playbackOptions(query=True, minTime=True))
    end_f = start_f + float(frames)
    bbox = _util.world_bbox([node]) if hasattr(_util, "world_bbox") else None
    center = bbox["center"] if bbox else [float(v) for v in (cmds.xform(node, query=True, worldSpace=True, rotatePivot=True) or [0, 0, 0])[:3]]
    size = max(bbox["size"]) if bbox else 100.0
    if radius is None:
        radius = max(size * 2.5, 10.0)
    if height is None:
        height = center[1] + size * 0.15
    grp = cmds.group(empty=True, name=node.split("|")[-1] + "_turntable") or (node.split("|")[-1] + "_turntable")
    cmds.xform(grp, worldSpace=True, translation=center)
    if camera:
        cam_t, cam_s = _camera_pair(camera)
        cmds.parent(node, grp)
        spinner = grp
    else:
        cam = create_camera(name=node.split("|")[-1] + "_turntableCam", focal_length=focal_length, translate=[center[0], height, center[2] + float(radius)], aim=center)
        cam_t, cam_s = cam["camera"], cam["shape"]
        cmds.parent(cam_t, grp)
        spinner = grp
    cmds.setKeyframe(spinner, attribute="rotateY", time=start_f, value=0.0, inTangentType="linear", outTangentType="linear")
    cmds.setKeyframe(spinner, attribute="rotateY", time=end_f, value=360.0, inTangentType="linear", outTangentType="linear")
    cmds.setInfinity(spinner, attribute="rotateY", preInfinite="cycle", postInfinite="cycle")
    cmds.playbackOptions(minTime=start_f, maxTime=end_f - 1.0)
    return {"turntable_group": _util.long_name(grp), "camera": _util.long_name(cam_t), "shape": cam_s, "node": _util.long_name(node), "start": start_f, "end": end_f - 1.0, "frames": int(frames), "radius": float(radius), "center": center, "mode": "object spins" if camera else "camera orbits"}


@command("previs.setup_scene_for_previs", mutates=True)
def setup_scene_for_previs(fps: float = 24.0, units: str = "cm", start: float = 1001.0, end: float = 1100.0, aspect: float | None = None, width: int = 1920,
                           height: int = 1080, playback_realtime: bool = True) -> Dict[str, Any]:
    """One call scene prep: fps, linear units, frame range, render resolution and real time playback."""
    _util.require_maya()
    from . import rigging_animation as ra

    if fps <= 0:
        raise BridgeError("fps must be positive")
    _choice(units, ("mm", "cm", "m", "in", "ft", "yd"), "units")
    if end < start:
        raise BridgeError("end must be >= start")
    cmds.currentUnit(linear=units)
    cmds.currentUnit(time=ra._unit_from_fps(float(fps)), updateAnimation=False)
    cmds.playbackOptions(animationStartTime=float(start), animationEndTime=float(end), minTime=float(start), maxTime=float(end))
    cmds.currentTime(float(start), edit=True)
    if aspect:
        height = int(round(width / float(aspect)))
    res = set_resolution(width, height)
    cmds.playbackOptions(playbackSpeed=1.0 if playback_realtime else 0.0)
    cmds.playbackOptions(loop="continuous")
    return {"fps": float(fps), "time_unit": ra._unit_from_fps(float(fps)), "units": units, "start": float(start), "end": float(end), "resolution": res, "playback": "realtime" if playback_realtime else "every frame"}
