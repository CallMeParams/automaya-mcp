"""photo.* commands: build a matched camera, a first block and a depth relief from a photo.

The server side (Pillow) reads the image; this module only receives numbers:
image size, focal length, and for the relief a small grid of heights. Nothing
here needs Pillow, so it runs inside Maya as is.
"""
from __future__ import annotations

import inspect
import math
import os
from typing import Any, Dict, List

from .. import registry
from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

MM_PER_INCH = 25.4
IMAGE_PLANE_FIT_HORIZONTAL = 2
MAX_RELIEF_SIDE = 128


def _positive(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise BridgeError("%s must be a number, got %r" % (label, value)) from None
    if out <= 0:
        raise BridgeError("%s must be positive, got %r" % (label, value))
    return out


def _safe_name(name: str, fallback: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in (name or fallback))
    if not cleaned or cleaned[0].isdigit():
        cleaned = fallback + "_" + cleaned
    return cleaned


def _camera_forward(camera: str) -> tuple:
    """World position and forward vector (Maya cameras look down -Z)."""
    try:
        m = cmds.xform(camera, query=True, worldSpace=True, matrix=True) or []
    except Exception:
        m = []
    if len(m) != 16:
        return [0.0, 0.0, 0.0], [0.0, 0.0, -1.0]
    pos = [float(m[12]), float(m[13]), float(m[14])]
    fwd = [-float(m[8]), -float(m[9]), -float(m[10])]
    length = math.sqrt(sum(v * v for v in fwd)) or 1.0
    return pos, [v / length for v in fwd]


@command("photo.camera_from_photo", mutates=True)
def camera_from_photo(path: str, image_width: int, image_height: int, name: str = "photoCam", focal_length: float | None = None,
                      sensor_width: float = 36.0, depth: float = 100.0, alpha: float = 1.0, group: bool = True, lock: bool = True) -> Dict[str, Any]:
    """Camera whose aspect and film back match the photo, with the photo on an image plane fitted horizontally.

    ``focal_length`` is the 35mm equivalent focal in mm (EXIF or a guess); when
    missing a 35mm lens is used and reported so the agent can refine it later.
    """
    _util.require_maya()
    if not path:
        raise BridgeError("path is required")
    if not os.path.isfile(path):
        raise BridgeError("image file not found: %r" % path)
    w = _positive(image_width, "image_width")
    h = _positive(image_height, "image_height")
    sensor_w = _positive(sensor_width, "sensor_width")
    focal = _positive(focal_length, "focal_length") if focal_length is not None else 35.0
    focal_source = "given" if focal_length is not None else "default_35mm"
    aspect = w / h
    sensor_h = sensor_w / aspect
    cam_name = _safe_name(name, "photoCam")
    result = cmds.camera(
        name=cam_name,
        focalLength=focal,
        horizontalFilmAperture=sensor_w / MM_PER_INCH,
        verticalFilmAperture=sensor_h / MM_PER_INCH,
        filmFit="horizontal",
        displayResolution=True,
        displayFilmGate=True,
    ) or []
    transform = result[0] if len(result) > 0 else cam_name
    shape = result[1] if len(result) > 1 else transform + "Shape"
    if transform != cam_name:
        transform = cmds.rename(transform, cam_name) or cam_name
        shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        shape = shapes[0] if shapes else shape
    cmds.setAttr(shape + ".displayGateMask", 1)
    plane = cmds.imagePlane(camera=shape, fileName=path.replace("\\", "/"), showInAllViews=False) or []
    ip_t = plane[0] if len(plane) > 0 else "imagePlane1"
    ip_s = plane[1] if len(plane) > 1 else ip_t + "Shape"
    cmds.setAttr(ip_s + ".fit", IMAGE_PLANE_FIT_HORIZONTAL)
    cmds.setAttr(ip_s + ".depth", float(depth))
    cmds.setAttr(ip_s + ".alphaGain", float(alpha))
    cmds.setAttr(ip_s + ".displayOnlyIfCurrent", 1)
    grp = None
    if group:
        grp = cmds.group(transform, name=cam_name + "_match_grp") or (cam_name + "_match_grp")
        if lock:
            for ch in ("translate", "rotate", "scale"):
                cmds.setAttr("%s.%s" % (grp, ch), lock=True)
    try:
        cmds.setAttr("defaultResolution.width", int(w))
        cmds.setAttr("defaultResolution.height", int(h))
        cmds.setAttr("defaultResolution.deviceAspectRatio", float(aspect))
    except Exception:
        pass
    fov = math.degrees(2.0 * math.atan(sensor_w / (2.0 * focal)))
    return {
        "camera": _util.long_name(transform),
        "shape": shape,
        "image_plane": _util.long_name(ip_t),
        "image_plane_shape": ip_s,
        "group": _util.long_name(grp) if grp else None,
        "path": path,
        "aspect": round(aspect, 4),
        "focal_length": focal,
        "focal_source": focal_source,
        "sensor_mm": [round(sensor_w, 3), round(sensor_h, 3)],
        "horizontal_fov_deg": round(fov, 2),
        "resolution": [int(w), int(h)],
        "note": "focal is a 35mm equivalent; if the photo has no EXIF, adjust with previs.set_lens until the plane and geometry line up",
    }


@command("photo.block_from_photo", mutates=True)
def block_from_photo(width: float, depth: float, height: float | None = None, floors: int | None = None, floor_height: float = 320.0,
                     name: str = "photoBlock", style: str = "flat", camera: str | None = None, distance: float | None = None) -> Dict[str, Any]:
    """First blocking volume for the photographed subject, in cm, pivot at the base.

    Uses ``procgen.building`` when that handler is registered (real facade
    detail), else a plain polyCube. With ``camera`` and ``distance`` the block
    is placed on the camera's forward axis at that distance so it sits behind
    the image plane.
    """
    _util.require_maya()
    w = _positive(width, "width")
    d = _positive(depth, "depth")
    if height is None:
        if floors is None:
            raise BridgeError("give height (cm) or floors")
        height = int(floors) * _positive(floor_height, "floor_height")
    h = _positive(height, "height")
    if floors is None:
        floors = max(1, int(round(h / float(floor_height))))
    node_name = _safe_name(name, "photoBlock")
    spec = registry.get("procgen.building")
    via = "polyCube"
    top = None
    fallback_note = None
    if spec is not None:
        wanted = {"width": w, "depth": d, "floors": int(floors), "floor_height": h / float(floors), "style": style, "name": node_name}
        try:
            params = inspect.signature(spec.func).parameters
            kwargs = {k: v for k, v in wanted.items() if k in params}
        except (TypeError, ValueError):
            kwargs = wanted
        try:
            result = spec.func(**kwargs)
        except BridgeError as exc:  # e.g. storey height outside the generator's range
            result = None
            fallback_note = "procgen.building refused (%s); used a polyCube" % exc
        if isinstance(result, dict):
            top = result.get("node") or result.get("group") or result.get("top") or (result.get("nodes") or [None])[0]
        elif isinstance(result, (list, tuple)) and result:
            top = result[0]
        if top is not None:
            via = "procgen.building"
    if top is None:
        made = cmds.polyCube(name=node_name, width=w, height=h, depth=d) or []
        top = made[0] if made else node_name
        via = "polyCube"
        cmds.xform(top, worldSpace=True, translation=[0.0, h * 0.5, 0.0])
        cmds.xform(top, worldSpace=True, pivots=[0.0, 0.0, 0.0])
        try:
            cmds.makeIdentity(top, apply=True, translate=True)
        except Exception:
            pass
    placed_at = None
    if camera:
        _util.require_nodes([camera])
        dist = _positive(distance, "distance") if distance is not None else 1000.0
        pos, fwd = _camera_forward(camera)
        placed_at = [round(pos[i] + fwd[i] * dist, 3) for i in range(3)]
        placed_at[1] = 0.0
        cmds.xform(top, worldSpace=True, translation=placed_at)
    long = _util.long_name(top)
    return {
        "node": long,
        "via": via,
        "note": fallback_note,
        "dims_cm": [w, h, d],
        "floors": int(floors),
        "placed_at": placed_at,
        "bbox": _util.world_bbox([long]),
        "next": "look through the camera, compare with the plate (maya_critique_compare) and adjust distance or dims",
    }


@command("photo.depth_relief", mutates=True)
def depth_relief(rows: List[List[float]], width: float = 1000.0, depth: float | None = None, height: float = 100.0, name: str = "photoRelief",
                 base_y: float = 0.0) -> Dict[str, Any]:
    """Displace a subdivided plane by a grid of heights (0..1) sent from the server.

    ``rows`` is top to bottom as seen in the picture; the top row lands at the
    far edge (-Z) so the relief reads like the photo when viewed from +Z.
    Amplitude is ``height`` cm; the plane is ``width`` by ``depth`` cm.
    """
    _util.require_maya()
    if not rows or not isinstance(rows, (list, tuple)) or not rows[0]:
        raise BridgeError("rows must be a non empty list of lists of heights 0..1 (the server builds it from a depth map)")
    ny = len(rows)
    nx = len(rows[0])
    if ny < 2 or nx < 2:
        raise BridgeError("rows needs at least 2 x 2 samples, got %d x %d" % (nx, ny))
    if nx > MAX_RELIEF_SIDE or ny > MAX_RELIEF_SIDE:
        raise BridgeError("rows is %d x %d; keep it at or below %d x %d (downsample server side)" % (nx, ny, MAX_RELIEF_SIDE, MAX_RELIEF_SIDE))
    if any(len(r) != nx for r in rows):
        raise BridgeError("every row must have the same number of samples")
    w = _positive(width, "width")
    d = _positive(depth, "depth") if depth is not None else w * ny / float(nx)
    amp = float(height)
    node_name = _safe_name(name, "photoRelief")
    made = cmds.polyPlane(name=node_name, width=w, height=d, subdivisionsX=nx - 1, subdivisionsY=ny - 1, axis=[0, 1, 0], createUVs=2) or []
    top = made[0] if made else node_name
    cmds.xform(top, worldSpace=True, translation=[0.0, float(base_y), 0.0])
    # polyPlane vertices are row major with x fastest; row 0 sits at +Z (near), so the
    # picture's top row (far) maps onto the last vertex row.
    moved = 0
    lo, hi = 1.0, 0.0
    for r in range(ny):
        vertex_row = ny - 1 - r
        for c in range(nx):
            try:
                value = float(rows[r][c])
            except (TypeError, ValueError):
                raise BridgeError("height at row %d col %d is not a number" % (r, c)) from None
            value = min(1.0, max(0.0, value))
            lo, hi = min(lo, value), max(hi, value)
            if value == 0.0:
                continue
            cmds.xform("%s.vtx[%d]" % (top, vertex_row * nx + c), relative=True, translation=[0.0, value * amp, 0.0])
            moved += 1
    try:
        cmds.polySoftEdge(top, angle=180, constructionHistory=False)
    except Exception:
        pass
    long = _util.long_name(top)
    return {
        "node": long,
        "samples": [nx, ny],
        "vertices": nx * ny,
        "displaced": moved,
        "dims_cm": [w, amp, round(d, 3)],
        "height_range": [round(lo, 4), round(hi, 4)],
        "bbox": _util.world_bbox([long]),
    }
