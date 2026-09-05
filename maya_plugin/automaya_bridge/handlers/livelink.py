"""livelink.* commands: the real time viewport feed for Unreal (or anything else).

The event bus (events.BUS) records scene changes through OpenMaya callbacks.
The Broadcaster streams them as NDJSON on ``event_port``. These commands start
and stop that stream, restrict it to nodes, and provide the pull side: full
scene graph snapshots, world matrices, mesh buffers and live USD exports that
a receiver requests over the command port when it needs more than an event.

OpenMaya 2 is used for mesh buffers when present; a cmds only fallback keeps
the command usable in tests and in odd sessions where the API is missing.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Tuple

from .. import events, prefs
from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
MAX_MESH_FACES = 2_000_000
ROTATE_ORDERS = ["xyz", "yzx", "zxy", "xzy", "yxz", "zyx"]


def _om():
    """Return maya.api.OpenMaya when it is real, else None (the test stub has no MFnMesh)."""
    try:
        import maya.api.OpenMaya as om  # type: ignore
    except ImportError:
        return None
    return om if hasattr(om, "MFnMesh") else None


# stream control --------------------------------------------------------------
def _status_dict() -> Dict[str, Any]:
    bus = events.BUS
    b = bus.broadcaster
    running = bool(b is not None and b.running)
    return {
        "active": running,
        "port": b.port if b is not None else None,
        "host": b.host if b is not None else None,
        "subscribers": b.subscriber_count() if b is not None else 0,
        "events_sent": b.sent if b is not None else 0,
        "clients_total": b.clients_total if b is not None else 0,
        "uptime_s": round(time.time() - b.started_at, 1) if (b is not None and b.started_at) else 0.0,
        "bus_active": bus.active,
        "callbacks": bus.active,
        "last_seq": bus.last_seq,
        "events_per_sec": bus.rate(5.0),
        "watched": bus.watched(),
        "transform_only": bus.transform_only,
        "openmaya": _om() is not None,
    }


@command("livelink.start_stream")
def start_stream(port: int | None = None, transform_only: bool = False, nodes: List[str] | None = None) -> Dict[str, Any]:
    """Start the NDJSON broadcast on ``port`` (default prefs event_port) and the change callbacks."""
    bus = events.BUS
    port = int(port) if port else int(prefs.load().get("event_port", 9878))
    if port < 1024 or port > 65535:
        raise BridgeError("port must be between 1024 and 65535")
    b = bus.broadcaster
    if b is not None and b.running and b.port != port:
        b.stop()
        b = None
    if b is None or not b.running:
        b = events.Broadcaster(port=port)
        try:
            b.start()
        except OSError as exc:
            raise BridgeError("cannot bind event port %d: %s. Pick another port or stop the process using it." % (port, exc))
        bus.broadcaster = b
    bus.transform_only = bool(transform_only)
    if nodes:
        _util.require_nodes(nodes)
        bus.watch(list(cmds.ls(nodes, long=True) or nodes))
    else:
        bus.watch([])
    if not bus.active:
        bus.start()
    info = _status_dict()
    if not bus.active:
        info["note"] = "OpenMaya callbacks unavailable in this session; only explicit events (markers, usd_exported, time set through livelink) are streamed"
    return info


@command("livelink.stop_stream")
def stop_stream() -> Dict[str, Any]:
    """Close the broadcast port and drop subscribers. Change tracking for the console stays on."""
    b = events.BUS.broadcaster
    if b is not None:
        b.stop()
    return _status_dict()


@command("livelink.status")
def status() -> Dict[str, Any]:
    return _status_dict()


@command("livelink.subscribe_nodes")
def subscribe_nodes(nodes: List[str] | None = None, transform_only: bool | None = None) -> Dict[str, Any]:
    """Restrict attribute events to these nodes (empty list = everything)."""
    bus = events.BUS
    if nodes:
        _util.require_nodes(nodes)
        nodes = list(cmds.ls(nodes, long=True) or nodes)
    bus.watch(nodes or [])
    if transform_only is not None:
        bus.transform_only = bool(transform_only)
    return {"watched": bus.watched(), "transform_only": bus.transform_only, "active": bus.active}


# scene graph -----------------------------------------------------------------
def _world_matrix(node: str) -> List[float]:
    try:
        m = cmds.xform(node, query=True, matrix=True, worldSpace=True)
        if m and len(m) == 16:
            return [round(float(v), 6) for v in m]
    except Exception:
        pass
    return list(IDENTITY)


def _get(node: str, attr: str, default: Any = None) -> Any:
    try:
        v = cmds.getAttr("%s.%s" % (node, attr))
        return v if v is not None else default
    except Exception:
        return default


def _shape_kind(node: str) -> Tuple[str, str | None]:
    shapes = _util.shapes_of(node)
    if not shapes:
        return ("joint" if cmds.nodeType(node) == "joint" else "group"), None
    for s in shapes:
        t = cmds.nodeType(s)
        if t == "mesh":
            return "mesh", s
        if t == "camera":
            return "camera", s
        if t.endswith("Light") or t in ("aiAreaLight", "aiSkyDomeLight"):
            return "light", s
        if t == "locator":
            return "locator", s
        if t == "nurbsCurve":
            return "curve", s
    return cmds.nodeType(shapes[0]), shapes[0]


def _transform_entry(node: str, include_meshes: bool) -> Dict[str, Any] | None:
    kind, shape = _shape_kind(node)
    entry: Dict[str, Any] = {
        "path": node,
        "name": node.split("|")[-1],
        "type": kind,
        "parent": node.rsplit("|", 1)[0] or None,
        "world_matrix": _world_matrix(node),
        "translate": _util.triple(node, "translate"),
        "rotate": _util.triple(node, "rotate"),
        "scale": _util.triple(node, "scale", 1.0),
        "rotate_order": ROTATE_ORDERS[int(_get(node, "rotateOrder", 0) or 0) % 6],
        "visible": bool(_get(node, "visibility", True)),
    }
    if shape:
        entry["shape"] = shape
    if kind == "camera" and shape:
        entry["camera"] = {
            "focal_length": _get(shape, "focalLength", 35.0),
            "horizontal_aperture_in": _get(shape, "horizontalFilmAperture", 1.417),
            "vertical_aperture_in": _get(shape, "verticalFilmAperture", 0.945),
            "near_clip": _get(shape, "nearClipPlane", 0.1),
            "far_clip": _get(shape, "farClipPlane", 10000.0),
            "orthographic": bool(_get(shape, "orthographic", False)),
            "ortho_width": _get(shape, "orthographicWidth", 30.0),
            "focus_distance": _get(shape, "focusDistance", 5.0),
            "f_stop": _get(shape, "fStop", 5.6),
        }
    elif kind == "light" and shape:
        color = _get(shape, "color", [(1.0, 1.0, 1.0)])
        entry["light"] = {
            "light_type": cmds.nodeType(shape),
            "intensity": _get(shape, "intensity", 1.0),
            "color": list(color[0]) if isinstance(color, (list, tuple)) and color and isinstance(color[0], (list, tuple)) else [1.0, 1.0, 1.0],
            "exposure": _get(shape, "exposure", 0.0) or _get(shape, "aiExposure", 0.0),
        }
    elif kind == "mesh" and shape and include_meshes:
        entry["mesh"] = {
            "faces": _poly_count(shape, "face"),
            "vertices": _poly_count(shape, "vertex"),
            "triangles": _poly_count(shape, "triangle"),
            "bbox": _util.world_bbox([shape]),
        }
    return entry


def _poly_count(shape: str, flag: str) -> int:
    try:
        v = cmds.polyEvaluate(shape, **{flag: True})
        return int(v) if isinstance(v, (int, float)) else 0
    except Exception:
        return 0


@command("livelink.snapshot_scene_graph")
def snapshot_scene_graph(root: str | None = None, include_meshes: bool = False, include_cameras: bool = True, include_lights: bool = True, max_nodes: int = 5000) -> Dict[str, Any]:
    """Flat list of transforms with world matrices, for building a mirror scene in a receiver.

    Mesh transforms are always listed; ``include_meshes`` adds face/vertex counts and bounds.
    """
    _util.require_maya()
    if root:
        _util.require_nodes([root])
        nodes = list(cmds.ls(root, dag=True, long=True, type="transform") or [])
        nodes += [j for j in (cmds.ls(root, dag=True, long=True, type="joint") or []) if j not in nodes]
    else:
        nodes = list(cmds.ls(type="transform", long=True) or [])
        nodes += [j for j in (cmds.ls(type="joint", long=True) or []) if j not in nodes]
    nodes = sorted(set(nodes))
    truncated = len(nodes) > int(max_nodes)
    out: List[Dict[str, Any]] = []
    for n in nodes[: int(max_nodes)]:
        entry = _transform_entry(n, include_meshes)
        if entry is None:
            continue
        if entry["type"] == "camera" and not include_cameras:
            continue
        if entry["type"] == "light" and not include_lights:
            continue
        out.append(entry)
    return {
        "root": root,
        "nodes": out,
        "count": len(out),
        "truncated": truncated,
        "unit": _unit(),
        "up_axis": _up_axis(),
        "fps": _fps(),
        "frame": _current_frame(),
        "frame_range": _frame_range(),
        "seq": events.BUS.last_seq,
        "coordinate_note": "Maya %s up, %s, right handed. Matrices are row major 4x4 with translation in the last row (Maya convention)." % (_up_axis().upper(), _unit()),
    }


def _unit() -> str:
    try:
        return cmds.currentUnit(query=True, linear=True) or "cm"
    except Exception:
        return "cm"


def _up_axis() -> str:
    try:
        return cmds.upAxis(query=True, axis=True) or "y"
    except Exception:
        return "y"


_FPS = {"game": 15.0, "film": 24.0, "pal": 25.0, "ntsc": 30.0, "show": 48.0, "palf": 50.0, "ntscf": 60.0}


def _fps() -> float:
    try:
        t = cmds.currentUnit(query=True, time=True) or "film"
    except Exception:
        return 24.0
    if t in _FPS:
        return _FPS[t]
    m = re.match(r"^(\d+(?:\.\d+)?)fps$", str(t))
    return float(m.group(1)) if m else 24.0


def _current_frame() -> float:
    try:
        v = cmds.currentTime(query=True)
        return float(v) if isinstance(v, (int, float)) else 0.0
    except Exception:
        return 0.0


def _frame_range() -> List[float]:
    try:
        return [float(cmds.playbackOptions(query=True, minTime=True)), float(cmds.playbackOptions(query=True, maxTime=True))]
    except Exception:
        return [1.0, 24.0]


@command("livelink.get_transforms")
def get_transforms(nodes: List[str] | None = None) -> Dict[str, Any]:
    """World matrices and local t/r/s for a node list (or the selection), one round trip."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    rows = []
    for n in targets:
        long_name = (cmds.ls(n, long=True) or [n])[0]
        t = _util.transform_of(long_name)
        rows.append({
            "path": t,
            "world_matrix": _world_matrix(t),
            "translate": _util.triple(t, "translate"),
            "rotate": _util.triple(t, "rotate"),
            "scale": _util.triple(t, "scale", 1.0),
            "rotate_order": ROTATE_ORDERS[int(_get(t, "rotateOrder", 0) or 0) % 6],
            "visible": bool(_get(t, "visibility", True)),
        })
    return {"transforms": rows, "frame": _current_frame(), "seq": events.BUS.last_seq, "unit": _unit()}


# mesh buffers ----------------------------------------------------------------
@command("livelink.get_mesh_buffers")
def get_mesh_buffers(node: str, world_space: bool = True, include_normals: bool = True, include_uvs: bool = True, triangulate: bool = True) -> Dict[str, Any]:
    """Positions, normals, uvs and indices of one mesh as flat lists (OpenMaya 2, cmds fallback)."""
    _util.require_maya()
    _util.require_nodes([node])
    node = (cmds.ls(node, long=True) or [node])[0]
    shapes = _util.shapes_of(node, "mesh")
    if not shapes:
        raise BridgeError("%s has no mesh shape; livelink.get_mesh_buffers needs a polygon mesh" % node)
    shape = shapes[0]
    faces = _poly_count(shape, "face")
    if faces > MAX_MESH_FACES:
        raise BridgeError("%s has %d faces, over the %d limit; use livelink.export_usd_live instead" % (shape, faces, MAX_MESH_FACES))
    om = _om()
    if om is not None:
        data = _mesh_buffers_om(om, shape, world_space, include_normals, include_uvs)
        backend = "openmaya"
    else:
        data = _mesh_buffers_cmds(shape, world_space, include_normals, include_uvs)
        backend = "cmds"
    counts, ids = data["face_vertex_counts"], data["face_vertex_indices"]
    uv_ids = data.get("uv_ids") or []
    if triangulate:
        tri, tri_uv = _triangulate(counts, ids, uv_ids)
    else:
        tri, tri_uv = [], []
    positions = data["positions"]
    bbox = _bbox_of(positions)
    return {
        "node": node,
        "shape": shape,
        "backend": backend,
        "world_space": bool(world_space),
        "unit": _unit(),
        "up_axis": _up_axis(),
        "positions": positions,
        "normals": data.get("normals", []),
        "uvs": data.get("uvs", []),
        "uv_ids": uv_ids,
        "face_vertex_counts": counts,
        "face_vertex_indices": ids,
        "indices": tri,
        "uv_indices": tri_uv,
        "counts": {
            "vertices": len(positions) // 3,
            "faces": len(counts),
            "triangles": len(tri) // 3 if triangulate else sum(max(c - 2, 0) for c in counts),
            "uvs": len(data.get("uvs", [])) // 2,
            "normals": len(data.get("normals", [])) // 3,
        },
        "bbox": bbox,
        "notes": data.get("notes", []),
        "winding": "counter clockwise (Maya); flip for Unreal",
    }


def _mesh_buffers_om(om: Any, shape: str, world_space: bool, include_normals: bool, include_uvs: bool) -> Dict[str, Any]:
    """Isolated OpenMaya 2 path."""
    sel = om.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    fn = om.MFnMesh(dag)
    space = om.MSpace.kWorld if world_space else om.MSpace.kObject
    pts = fn.getPoints(space)
    positions: List[float] = []
    for p in pts:
        positions.extend((round(p.x, 5), round(p.y, 5), round(p.z, 5)))
    counts, ids = fn.getVertices()
    out: Dict[str, Any] = {"positions": positions, "face_vertex_counts": list(counts), "face_vertex_indices": list(ids), "notes": []}
    if include_normals:
        normals: List[float] = []
        for n in fn.getVertexNormals(False, space):
            normals.extend((round(n.x, 5), round(n.y, 5), round(n.z, 5)))
        out["normals"] = normals
    if include_uvs:
        try:
            us, vs = fn.getUVs()
            uvs: List[float] = []
            for u, v in zip(us, vs):
                uvs.extend((round(u, 6), round(v, 6)))
            _uv_counts, uv_ids = fn.getAssignedUVs()
            out["uvs"] = uvs
            out["uv_ids"] = list(uv_ids)
        except Exception as exc:
            out["uvs"] = []
            out["uv_ids"] = []
            out["notes"].append("uvs unavailable: %s" % exc)
    return out


def _mesh_buffers_cmds(shape: str, world_space: bool, include_normals: bool, include_uvs: bool) -> Dict[str, Any]:
    """cmds only fallback: vertex positions via xform, topology via polyInfo, normals computed."""
    nverts = _poly_count(shape, "vertex")
    positions: List[float] = []
    if nverts > 0:
        raw = cmds.xform("%s.vtx[0:%d]" % (shape, nverts - 1), query=True, translation=True, worldSpace=bool(world_space), objectSpace=not world_space)
        positions = [round(float(v), 5) for v in (raw or [])]
    counts: List[int] = []
    ids: List[int] = []
    for line in cmds.polyInfo(shape, faceToVertex=True) or []:
        parts = str(line).split(":", 1)
        if len(parts) != 2:
            continue
        vids = [int(x) for x in re.findall(r"-?\d+", parts[1])]
        counts.append(len(vids))
        ids.extend(vids)
    out: Dict[str, Any] = {"positions": positions, "face_vertex_counts": counts, "face_vertex_indices": ids, "notes": ["cmds fallback: normals are computed from faces, uvs are not available"]}
    if include_normals:
        out["normals"] = _smooth_normals(positions, counts, ids)
    if include_uvs:
        out["uvs"] = []
        out["uv_ids"] = []
    return out


def _smooth_normals(positions: List[float], counts: List[int], ids: List[int]) -> List[float]:
    n = len(positions) // 3
    acc = [[0.0, 0.0, 0.0] for _ in range(n)]
    cursor = 0
    for c in counts:
        face = ids[cursor: cursor + c]
        cursor += c
        if c < 3 or any(i >= n for i in face):
            continue
        # Newell's method gives a robust polygon normal for any planar-ish face.
        nx = ny = nz = 0.0
        for k in range(c):
            ax, ay, az = positions[face[k] * 3: face[k] * 3 + 3]
            bx, by, bz = positions[face[(k + 1) % c] * 3: face[(k + 1) % c] * 3 + 3]
            nx += (ay - by) * (az + bz)
            ny += (az - bz) * (ax + bx)
            nz += (ax - bx) * (ay + by)
        for i in face:
            acc[i][0] += nx
            acc[i][1] += ny
            acc[i][2] += nz
    out: List[float] = []
    for v in acc:
        length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if length > 1e-12:
            out.extend((round(v[0] / length, 5), round(v[1] / length, 5), round(v[2] / length, 5)))
        else:
            out.extend((0.0, 1.0, 0.0))
    return out


def _triangulate(counts: List[int], ids: List[int], uv_ids: List[int]) -> Tuple[List[int], List[int]]:
    """Fan triangulation of each polygon; uv ids follow the same corner order."""
    tri: List[int] = []
    tri_uv: List[int] = []
    cursor = 0
    have_uv = len(uv_ids) == len(ids)
    for c in counts:
        face = ids[cursor: cursor + c]
        face_uv = uv_ids[cursor: cursor + c] if have_uv else []
        cursor += c
        for k in range(1, c - 1):
            tri.extend((face[0], face[k], face[k + 1]))
            if have_uv:
                tri_uv.extend((face_uv[0], face_uv[k], face_uv[k + 1]))
    return tri, tri_uv


def _bbox_of(positions: List[float]) -> Dict[str, List[float]] | None:
    if len(positions) < 3:
        return None
    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    return {"min": mn, "max": mx, "size": [mx[i] - mn[i] for i in range(3)], "center": [(mx[i] + mn[i]) * 0.5 for i in range(3)]}


# usd + time + markers -----------------------------------------------------------
@command("livelink.export_usd_live")
def export_usd_live(path: str | None = None, nodes: List[str] | None = None, animation: bool = False, start: float | None = None, end: float | None = None) -> Dict[str, Any]:
    """Export nodes (or selection) to USD and announce the path on the stream so a receiver reloads its stage."""
    _util.require_maya()
    targets = [(cmds.ls(n, long=True) or [n])[0] for n in _util.resolve_targets(nodes)]
    if not path:
        folder = os.path.join(tempfile.gettempdir(), "automaya")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "livelink_%d.usda" % int(time.time()))
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".usd", ".usda", ".usdc"):
        raise BridgeError("path must end in .usd, .usda or .usdc")
    if animation and (start is None or end is None):
        rng = _frame_range()
        start = rng[0] if start is None else start
        end = rng[1] if end is None else end
    out = _util.export_selection(path, targets, "usd", {"animation": bool(animation), "start": start, "end": end})
    event = events.BUS.emit("usd_exported", path=out, nodes=targets, animation=bool(animation), start=start, end=end)
    return {"path": out, "nodes": targets, "animation": bool(animation), "start": start, "end": end, "seq": event["seq"]}


@command("livelink.set_frame")
def set_frame(frame: float) -> Dict[str, Any]:
    """Move the timeline (the receiver drives time). Emits time_changed even without callbacks."""
    _util.require_maya()
    cmds.currentTime(float(frame), edit=True)
    if not events.BUS.active:
        events.BUS.emit("time_changed", frame=float(frame))
    return {"frame": float(frame), "fps": _fps()}


@command("livelink.play_range")
def play_range(start: float, end: float, play: bool = False, loop: bool = True) -> Dict[str, Any]:
    """Set the playback range and optionally start playing."""
    _util.require_maya()
    start, end = float(start), float(end)
    if end < start:
        raise BridgeError("end must be >= start")
    cmds.playbackOptions(edit=True, minTime=start, maxTime=end, animationStartTime=min(start, _frame_range()[0]), animationEndTime=max(end, _frame_range()[1]), loop="continuous" if loop else "once")
    if play:
        cmds.play(forward=True, state=True)
    events.BUS.emit("play_range", start=start, end=end, playing=bool(play), loop=bool(loop))
    return {"start": start, "end": end, "playing": bool(play), "loop": bool(loop), "fps": _fps()}


@command("livelink.emit_marker")
def emit_marker(name: str, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Push a custom sync point onto the stream (e.g. 'shot_start', 'bake_done')."""
    if not name or not isinstance(name, str):
        raise BridgeError("name must be a non empty string")
    event = events.BUS.emit("marker", name=name, data=data or {})
    b = events.BUS.broadcaster
    return {"event": event, "delivered_to": b.subscriber_count() if (b is not None and b.running) else 0}


@command("livelink.protocol_spec")
def protocol_spec() -> Dict[str, Any]:
    """The NDJSON event schema and the command port framing, for receiver authors."""
    return PROTOCOL_SPEC


PROTOCOL_SPEC: Dict[str, Any] = {
    "version": 1,
    "transport": {
        "events": "TCP 127.0.0.1:<event_port> (default 9878). One JSON object per line, UTF-8, newline terminated. Read only; the server never reads from the socket.",
        "commands": "TCP 127.0.0.1:<port> (default 9877). Frames: 4 byte big endian unsigned length + UTF-8 JSON. Request {id, type, params}; response {id, status: success|error, result|message, elapsed_ms}.",
    },
    "common_fields": {"seq": "monotonic int per Maya session", "ts": "unix seconds float", "kind": "event kind", "human": "true when the user (not the agent) caused it"},
    "events": {
        "hello": {"protocol": 1, "event_port": "int", "unit": "cm|m|...", "up_axis": "y|z", "scene": "path", "note": "first line on connect; no seq"},
        "attr_changed": {"node": "full dag path or node name", "attr": "long attribute name e.g. translateX", "value": "scalar or null for compound/array (pull with livelink.get_transforms)"},
        "node_added": {"node": "name", "type": "node type"},
        "node_removed": {"node": "name", "type": "node type"},
        "time_changed": {"frame": "float"},
        "selection_changed": {"selection": ["dag paths"]},
        "connection_made": {"node": "", "attr": "", "other": "plug"},
        "connection_broken": {"node": "", "attr": "", "other": "plug"},
        "name_changed": {},
        "scene_opened": {"file": "path"},
        "scene_new": {},
        "scene_saved": {"file": "path"},
        "undo": {},
        "redo": {},
        "usd_exported": {"path": "usd file", "nodes": ["paths"], "animation": "bool", "start": "float|null", "end": "float|null"},
        "play_range": {"start": "float", "end": "float", "playing": "bool", "loop": "bool"},
        "marker": {"name": "string", "data": "object"},
        "events_started": {}, "events_stopped": {},
    },
    "pull_commands": {
        "livelink.snapshot_scene_graph": "flat transform list with world_matrix (16 floats, row major, Maya convention), camera and light params",
        "livelink.get_transforms": "world matrices for a node list",
        "livelink.get_mesh_buffers": "positions/normals/uvs/indices flat lists for one mesh",
        "livelink.export_usd_live": "write a USD file and emit usd_exported",
        "livelink.set_frame / livelink.play_range": "receiver drives Maya time",
    },
    "coordinate_conversion": {
        "maya": "right handed, Y up by default, centimeters by default; rotation in degrees, rotate order per node",
        "unreal": "left handed, Z up, centimeters",
        "position": "UE(x, y, z) = (maya_x, maya_z, maya_y) for Y up scenes; this swap is a reflection so it also flips handedness, which is what Unreal needs",
        "rotation": "do not remap Euler angles by hand. Build the Maya 4x4 (respecting rotate_order), then M_ue = S * M_maya * S where S swaps rows/cols 1 and 2 (y<->z), then extract the rotator from the 3x3 (FMatrix.Rotator / conv_matrix_to_transform). Row major, translation in row 3, in both apps. See docs/UNREAL_BRIDGE.md",
        "scale": "UE(sx, sy, sz) = (maya_sx, maya_sz, maya_sy)",
        "triangles": "flip winding (reverse each triangle) after the axis swap to keep faces front facing",
    },
}
