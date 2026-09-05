"""intel.* commands: the agent's eyes. Screenshots, budgeted scene summaries,
snapshots and diffs, problem finding, selection inspection and plain language
descriptions. Everything here is read only except the screenshot, which writes
a temp file and may briefly retarget a model panel (restored afterwards).
"""
from __future__ import annotations

import math
import os
import re
import struct
import tempfile
import time
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

_SNAPSHOTS: Dict[str, Dict[str, Any]] = {}
_SNAPSHOT_LIMIT = 20
_SNAPSHOT_COUNTER = [0]

DEFAULT_CAMERAS = {"persp", "top", "front", "side", "back", "bottom", "left"}
_HISTORY_IGNORE = {
    "mesh", "transform", "shadingEngine", "groupId", "groupParts", "file", "place2dTexture",
    "lambert", "blinn", "phong", "standardSurface", "aiStandardSurface", "materialInfo", "nurbsCurve",
    "nurbsSurface", "camera", "joint", "displayLayer", "renderLayer", "objectSet", "skinCluster", "tweak",
}
ALL_CHECKS = [
    "non_manifold", "lamina", "zero_area", "ngons", "unfrozen_transforms", "non_uniform_scale",
    "missing_textures", "duplicate_names", "empty_groups", "unused_materials", "construction_history",
    "far_from_origin", "bbox_scale",
]


# helpers -------------------------------------------------------------------
def _short(name: str) -> str:
    return name.split("|")[-1]


def _mesh_shapes(node: str) -> List[str]:
    return _util.shapes_of(node, "mesh")


def _face_count(shape: str) -> int | None:
    try:
        v = cmds.polyEvaluate(shape, face=True)
        return int(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None


def _poly_eval(shape: str, **flag: Any) -> int | None:
    try:
        v = cmds.polyEvaluate(shape, **flag)
        return int(v) if isinstance(v, (int, float)) else None
    except Exception:
        return None


def _materials_of(shape: str) -> List[str]:
    try:
        sgs = cmds.listConnections(shape, type="shadingEngine") or []
    except Exception:
        return []
    out: List[str] = []
    for sg in dict.fromkeys(sgs):
        try:
            mats = cmds.listConnections(sg + ".surfaceShader") or []
        except Exception:
            mats = []
        out.extend(m for m in mats if m not in out)
    return out


def _is_animated(node: str) -> bool:
    try:
        n = cmds.keyframe(node, query=True, keyframeCount=True)
        if isinstance(n, (int, float)) and n > 0:
            return True
    except Exception:
        pass
    try:
        conns = cmds.listConnections(node, source=True, destination=False, type="animCurve") or []
        return bool(conns)
    except Exception:
        return False


def _visible(node: str) -> bool:
    try:
        return bool(cmds.getAttr(node + ".visibility"))
    except Exception:
        return True


def _children(node: str) -> List[str]:
    try:
        return cmds.listRelatives(node, children=True, type="transform", fullPath=True) or []
    except Exception:
        return []


def _classify(node: str) -> str:
    """One word kind for a transform: mesh, camera, light, joint, curve, locator, group."""
    try:
        nt = cmds.nodeType(node)
    except Exception:
        nt = "unknown"
    if nt == "joint":
        return "joint"
    if nt != "transform":
        return nt
    shapes = _util.shapes_of(node)
    if not shapes:
        return "group"
    kinds = set()
    for s in shapes:
        try:
            kinds.add(cmds.nodeType(s))
        except Exception:
            continue
    if "mesh" in kinds:
        return "mesh"
    if "camera" in kinds:
        return "camera"
    if any(k.endswith("Light") for k in kinds):
        return "light"
    if "nurbsCurve" in kinds:
        return "curve"
    if "nurbsSurface" in kinds:
        return "nurbs"
    if "locator" in kinds:
        return "locator"
    return sorted(kinds)[0]


def _png_size(data: bytes) -> tuple | None:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack("!II", data[16:24])
        return int(w), int(h)
    return None


# screenshot ----------------------------------------------------------------
@command("intel.viewport_screenshot")
def viewport_screenshot(camera: str | None = None, width: int = 1280, height: int = 720, panel: str | None = None, display_mode: str | None = None) -> Dict[str, Any]:
    """Grab the active (or given) model panel as a PNG via a one frame offscreen playblast."""
    _util.require_maya()
    width = max(64, min(int(width), 4096))
    height = max(64, min(int(height), 4096))
    try:
        if cmds.about(batch=True):
            raise BridgeError("viewport screenshots need an interactive Maya session (not mayapy/batch); use arnold.render_frame instead")
    except BridgeError:
        raise
    except Exception:
        pass
    if camera and not cmds.objExists(camera):
        raise BridgeError("camera %r not found; see previs.list_cameras" % camera)
    panel = panel or _active_model_panel()
    restore: Dict[str, Any] = {}
    if panel:
        try:
            if camera:
                restore["camera"] = cmds.modelPanel(panel, query=True, camera=True)
                cmds.modelEditor(panel, edit=True, camera=camera)
            if display_mode:
                restore["displayAppearance"] = cmds.modelEditor(panel, query=True, displayAppearance=True)
                restore["displayTextures"] = cmds.modelEditor(panel, query=True, displayTextures=True)
                _apply_display_mode(panel, display_mode)
        except Exception as exc:
            raise BridgeError("could not configure panel %r: %s" % (panel, exc))
    folder = os.path.join(tempfile.gettempdir(), "automaya")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "viewport_%d.png" % int(time.time() * 1000))
    try:
        frame = cmds.currentTime(query=True)
        kwargs: Dict[str, Any] = {
            "frame": [frame], "format": "image", "compression": "png", "completeFilename": path,
            "viewer": False, "offScreen": True, "widthHeight": [width, height], "percent": 100,
            "forceOverwrite": True, "showOrnaments": False, "quality": 100, "framePadding": 0,
        }
        if panel:
            kwargs["editorPanelName"] = panel
        cmds.playblast(**kwargs)
    finally:
        if panel and restore:
            try:
                if "camera" in restore and restore["camera"]:
                    cmds.modelEditor(panel, edit=True, camera=restore["camera"])
                if "displayAppearance" in restore:
                    cmds.modelEditor(panel, edit=True, displayAppearance=restore["displayAppearance"], displayTextures=bool(restore.get("displayTextures")))
            except Exception:
                pass
    if not os.path.exists(path):
        raise BridgeError("playblast produced no file at %s (is a model panel visible?)" % path)
    with open(path, "rb") as fh:
        data = fh.read()
    size = _png_size(data) or (width, height)
    cam = camera
    if not cam and panel:
        try:
            cam = cmds.modelPanel(panel, query=True, camera=True)
        except Exception:
            cam = None
    return {
        "image_base64": _util.read_file_base64(path), "format": "png", "width": size[0], "height": size[1],
        "camera": cam, "panel": panel, "path": path, "frame": frame,
    }


def _active_model_panel() -> str | None:
    try:
        p = cmds.getPanel(withFocus=True)
        if p and cmds.getPanel(typeOf=p) == "modelPanel":
            return p
    except Exception:
        pass
    try:
        visible = cmds.getPanel(visiblePanels=True) or []
        for p in visible:
            if cmds.getPanel(typeOf=p) == "modelPanel":
                return p
    except Exception:
        pass
    try:
        panels = cmds.getPanel(type="modelPanel") or []
        return panels[-1] if panels else None
    except Exception:
        return None


def _apply_display_mode(panel: str, mode: str) -> None:
    mode = mode.lower()
    if mode in ("wire", "wireframe"):
        cmds.modelEditor(panel, edit=True, displayAppearance="wireframe", displayTextures=False)
    elif mode in ("shaded", "smoothshaded", "smooth"):
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", displayTextures=False)
    elif mode in ("textured", "texture"):
        cmds.modelEditor(panel, edit=True, displayAppearance="smoothShaded", displayTextures=True)
    elif mode in ("flat", "flatshaded"):
        cmds.modelEditor(panel, edit=True, displayAppearance="flatShaded")
    elif mode in ("bounding", "boundingbox", "bbox"):
        cmds.modelEditor(panel, edit=True, displayAppearance="boundingBox")
    else:
        raise BridgeError("unknown display_mode %r (wireframe, shaded, textured, flat, boundingbox)" % mode)


# summary -------------------------------------------------------------------
@command("intel.scene_summary")
def scene_summary(max_nodes: int = 200, depth: int = 3, include_attrs: bool = False) -> Dict[str, Any]:
    """Hierarchical, token budgeted picture of the scene: assemblies and their
    children with type, face counts, bounding boxes, materials, visibility."""
    _util.require_maya()
    max_nodes = max(1, int(max_nodes))
    depth = max(0, int(depth))
    budget = [max_nodes]
    roots = cmds.ls(assemblies=True, long=True) or []
    tree: List[Dict[str, Any]] = []
    truncated = False
    for root in roots:
        if budget[0] <= 0:
            truncated = True
            break
        tree.append(_describe_tree(root, depth, budget, include_attrs))
    return {
        "scene": _scene_name(),
        "units": _units(),
        "totals": _totals(),
        "assemblies": tree,
        "assembly_count": len(roots),
        "truncated": truncated or budget[0] <= 0,
        "hint": "Use intel.describe_for_llm or scene.get_node_info for detail on one node; raise max_nodes/depth to see more." if truncated or budget[0] <= 0 else "",
    }


def _describe_tree(node: str, depth: int, budget: List[int], include_attrs: bool) -> Dict[str, Any]:
    budget[0] -= 1
    info = _node_brief(node, include_attrs)
    kids = _children(node)
    info["child_count"] = len(kids)
    if depth > 0 and kids:
        shown: List[Dict[str, Any]] = []
        for k in kids:
            if budget[0] <= 0:
                info["children_truncated"] = len(kids) - len(shown)
                break
            shown.append(_describe_tree(k, depth - 1, budget, include_attrs))
        info["children"] = shown
    return info


def _node_brief(node: str, include_attrs: bool = False) -> Dict[str, Any]:
    kind = _classify(node)
    info: Dict[str, Any] = {"name": _short(node), "path": node, "kind": kind}
    if not _visible(node):
        info["visible"] = False
    if _is_animated(node):
        info["animated"] = True
    if kind == "mesh":
        faces = 0
        mats: List[str] = []
        for s in _mesh_shapes(node):
            faces += _face_count(s) or 0
            mats.extend(m for m in _materials_of(s) if m not in mats)
        info["faces"] = faces
        if mats:
            info["materials"] = mats
        bb = _util.world_bbox([node])
        if bb:
            info["bbox_size"] = bb["size"]
            info["bbox_center"] = bb["center"]
    elif kind in ("camera", "light", "curve", "nurbs", "locator"):
        bb = _util.world_bbox([node])
        if bb:
            info["bbox_center"] = bb["center"]
    if include_attrs:
        info["translate"] = _util.triple(node, "translate")
        info["rotate"] = _util.triple(node, "rotate")
        info["scale"] = _util.triple(node, "scale", 1.0)
    return info


def _scene_name() -> str:
    try:
        return cmds.file(query=True, sceneName=True) or "untitled"
    except Exception:
        return "untitled"


def _units() -> Dict[str, Any]:
    try:
        return {"linear": cmds.currentUnit(query=True, linear=True), "angle": cmds.currentUnit(query=True, angle=True), "time": cmds.currentUnit(query=True, time=True), "up_axis": cmds.upAxis(query=True, axis=True)}
    except Exception:
        return {}


def _ls(**kw: Any) -> List[str]:
    try:
        return cmds.ls(**kw) or []
    except Exception:
        return []


def _totals() -> Dict[str, Any]:
    meshes = _ls(type="mesh", long=True, noIntermediate=True)
    faces = 0
    tris = 0
    for m in meshes:
        faces += _face_count(m) or 0
        tris += _poly_eval(m, triangle=True) or 0
    cams = [c for c in _ls(type="camera", long=True) if _short(_util.transform_of(c)) not in DEFAULT_CAMERAS]
    mats = [m for m in _ls(materials=True) if m not in ("lambert1", "standardSurface1", "particleCloud1", "shaderGlow1")]
    return {
        "transforms": len(_ls(type="transform", long=True)),
        "meshes": len(meshes),
        "faces": faces,
        "triangles": tris,
        "cameras": len(cams),
        "lights": len(_ls(lights=True, long=True)),
        "joints": len(_ls(type="joint", long=True)),
        "materials": len(mats),
        "references": len(_ls(references=True)),
        "frame_range": _frame_range(),
    }


def _frame_range() -> List[float]:
    try:
        return [cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True)]
    except Exception:
        return []


# snapshot + diff ------------------------------------------------------------
def _capture() -> Dict[str, Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for n in _ls(type="transform", long=True) + _ls(type="joint", long=True):
        if n in nodes:
            continue
        parent = n.rsplit("|", 1)[0] or None
        entry: Dict[str, Any] = {
            "type": _classify(n),
            "parent": parent,
            "t": _util.triple(n, "translate"),
            "r": _util.triple(n, "rotate"),
            "s": _util.triple(n, "scale", 1.0),
        }
        if entry["type"] == "mesh":
            entry["faces"] = sum(_face_count(s) or 0 for s in _mesh_shapes(n))
        nodes[n] = entry
    return nodes


@command("intel.snapshot")
def snapshot(label: str | None = None) -> Dict[str, Any]:
    """Record every transform's type, parent, t/r/s and face count so a later
    intel.diff can say what changed. Kept in memory (last 20 snapshots)."""
    _util.require_maya()
    _SNAPSHOT_COUNTER[0] += 1
    sid = "snap_%d" % _SNAPSHOT_COUNTER[0]
    _SNAPSHOTS[sid] = {"id": sid, "label": label, "ts": time.time(), "scene": _scene_name(), "nodes": _capture()}
    while len(_SNAPSHOTS) > _SNAPSHOT_LIMIT:
        del _SNAPSHOTS[next(iter(_SNAPSHOTS))]
    return {"snapshot_id": sid, "node_count": len(_SNAPSHOTS[sid]["nodes"]), "label": label, "ts": _SNAPSHOTS[sid]["ts"]}


@command("intel.list_snapshots")
def list_snapshots() -> List[Dict[str, Any]]:
    return [{"id": s["id"], "label": s["label"], "ts": s["ts"], "scene": s["scene"], "node_count": len(s["nodes"])} for s in _SNAPSHOTS.values()]


@command("intel.diff")
def diff(snapshot_id: str, snapshot_b: str | None = None, tolerance: float = 1e-4, max_items: int = 200) -> Dict[str, Any]:
    """Compare snapshot A against the live scene (or against snapshot B)."""
    _util.require_maya()
    a = _SNAPSHOTS.get(snapshot_id)
    if a is None:
        raise BridgeError("unknown snapshot %r; call intel.snapshot first (known: %s)" % (snapshot_id, ", ".join(_SNAPSHOTS) or "none"))
    if snapshot_b:
        b_snap = _SNAPSHOTS.get(snapshot_b)
        if b_snap is None:
            raise BridgeError("unknown snapshot %r" % snapshot_b)
        b = b_snap["nodes"]
    else:
        b = _capture()
    return _diff_nodes(a["nodes"], b, float(tolerance), int(max_items), snapshot_id, snapshot_b or "live")


def _diff_nodes(a: Dict[str, Dict[str, Any]], b: Dict[str, Dict[str, Any]], tol: float, max_items: int, name_a: str, name_b: str) -> Dict[str, Any]:
    added = [n for n in b if n not in a]
    removed = [n for n in a if n not in b]
    moved: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    for n, old in a.items():
        new = b.get(n)
        if new is None:
            continue
        deltas = {}
        for key in ("t", "r", "s"):
            if any(abs(x - y) > tol for x, y in zip(old[key], new[key])):
                deltas[key] = {"from": old[key], "to": new[key]}
        if deltas:
            moved.append({"node": n, **deltas})
        other = {}
        for key in ("type", "parent", "faces"):
            if old.get(key) != new.get(key):
                other[key] = {"from": old.get(key), "to": new.get(key)}
        if other:
            changed.append({"node": n, **other})
    return {
        "a": name_a, "b": name_b,
        "added": added[:max_items], "removed": removed[:max_items], "moved": moved[:max_items], "changed": changed[:max_items],
        "counts": {"added": len(added), "removed": len(removed), "moved": len(moved), "changed": len(changed), "unchanged": len(a) - len(removed) - len({m["node"] for m in moved} | {c["node"] for c in changed})},
    }


# problems ------------------------------------------------------------------
@command("intel.find_problems")
def find_problems(checks: List[str] | None = None, nodes: List[str] | None = None, far_threshold: float = 10000.0, max_per_check: int = 50) -> Dict[str, Any]:
    """Scene lint. Each finding has node, detail and a fix hint naming the tool to run."""
    _util.require_maya()
    wanted = list(checks) if checks else list(ALL_CHECKS)
    unknown = [c for c in wanted if c not in ALL_CHECKS]
    if unknown:
        raise BridgeError("unknown checks %s; valid: %s" % (unknown, ", ".join(ALL_CHECKS)))
    if nodes:
        _util.require_nodes(nodes)
        transforms = [t for t in cmds.ls(nodes, dag=True, type="transform", long=True) or []]
        meshes = [m for m in cmds.ls(nodes, dag=True, type="mesh", long=True, noIntermediate=True) or []]
    else:
        transforms = _ls(type="transform", long=True)
        meshes = _ls(type="mesh", long=True, noIntermediate=True)
    problems: Dict[str, List[Dict[str, Any]]] = {}
    skipped: Dict[str, str] = {}
    cap = max(1, int(max_per_check))
    runners = {
        "non_manifold": lambda: _check_polyinfo(meshes, "nonManifoldEdges", "non manifold edges", "modeling.cleanup or Mesh > Cleanup with nonmanifold on"),
        "lamina": lambda: _check_polyinfo(meshes, "laminaFaces", "lamina faces", "Mesh > Cleanup with lamina faces on, or delete the duplicate face"),
        "zero_area": lambda: _check_zero_area(meshes, skipped),
        "ngons": lambda: _check_ngons(meshes),
        "unfrozen_transforms": lambda: _check_unfrozen(transforms),
        "non_uniform_scale": lambda: _check_non_uniform(transforms),
        "missing_textures": _check_missing_textures,
        "duplicate_names": lambda: _check_duplicate_names(transforms),
        "empty_groups": lambda: _check_empty_groups(transforms),
        "unused_materials": _check_unused_materials,
        "construction_history": lambda: _check_history(meshes),
        "far_from_origin": lambda: _check_far(transforms, float(far_threshold)),
        "bbox_scale": lambda: _check_bbox_scale(meshes),
    }
    for c in wanted:
        try:
            found = runners[c]()
        except Exception as exc:  # one broken check must not hide the others
            skipped[c] = "check raised %s: %s" % (type(exc).__name__, exc)
            continue
        if found:
            problems[c] = found[:cap]
    counts = {c: len(v) for c, v in problems.items()}
    return {
        "problems": problems,
        "counts": counts,
        "total": sum(counts.values()),
        "checks_run": wanted,
        "skipped": skipped,
        "scope": "nodes" if nodes else "scene",
        "units": _units().get("linear"),
    }


def _finding(node: str, detail: str, fix: str, severity: str = "warning", **extra: Any) -> Dict[str, Any]:
    d = {"node": node, "detail": detail, "fix": fix, "severity": severity}
    d.update(extra)
    return d


def _check_polyinfo(meshes: Sequence[str], flag: str, label: str, fix: str) -> List[Dict[str, Any]]:
    out = []
    for m in meshes:
        try:
            comps = cmds.polyInfo(m, **{flag: True}) or []
        except Exception:
            continue
        if comps:
            out.append(_finding(m, "%d %s" % (len(comps), label), fix, "error", components=list(comps)[:20]))
    return out


def _check_zero_area(meshes: Sequence[str], skipped: Dict[str, str]) -> List[Dict[str, Any]]:
    fn = _om_zero_area_faces
    out = []
    try:
        for m in meshes:
            faces = fn(m)
            if faces is None:
                skipped["zero_area"] = "needs OpenMaya (not available in this session)"
                return out
            if faces:
                out.append(_finding(m, "%d zero area faces" % len(faces), "delete the faces or Mesh > Cleanup with zero area faces on", "error", faces=faces[:20]))
    except Exception as exc:
        skipped["zero_area"] = str(exc)
    return out


def _om_zero_area_faces(mesh: str, epsilon: float = 1e-8) -> List[int] | None:
    """Isolated OpenMaya path; returns None when OpenMaya is unavailable."""
    try:
        import maya.api.OpenMaya as om  # type: ignore
    except ImportError:
        return None
    if not hasattr(om, "MSelectionList"):
        return None
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    it = om.MItMeshPolygon(dag)
    bad: List[int] = []
    while not it.isDone():
        if it.getArea() < epsilon:
            bad.append(it.index())
        it.next()
    return bad


def _face_vertex_lines(mesh: str) -> List[List[int]]:
    """Parse polyInfo faceToVertex output into a list of vertex id lists."""
    try:
        lines = cmds.polyInfo(mesh, faceToVertex=True) or []
    except Exception:
        return []
    faces = []
    for line in lines:
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        ids = [int(x) for x in re.findall(r"-?\d+", parts[1])]
        faces.append(ids)
    return faces


def _check_ngons(meshes: Sequence[str], face_limit: int = 200_000) -> List[Dict[str, Any]]:
    out = []
    for m in meshes:
        fc = _face_count(m) or 0
        if fc > face_limit:
            continue
        ngons = [i for i, ids in enumerate(_face_vertex_lines(m)) if len(ids) > 4]
        if ngons:
            out.append(_finding(m, "%d faces with more than 4 sides" % len(ngons), "select them and run Mesh > Triangulate or add edge loops (modeling tools)", "warning", faces=ngons[:20]))
    return out


def _has_mesh(node: str) -> bool:
    return bool(_mesh_shapes(node))


def _check_unfrozen(transforms: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for t in transforms:
        if not _has_mesh(t):
            continue
        tr = _util.triple(t, "translate")
        ro = _util.triple(t, "rotate")
        sc = _util.triple(t, "scale", 1.0)
        if any(abs(v) > 1e-6 for v in tr + ro) or any(abs(v - 1.0) > 1e-6 for v in sc):
            out.append(_finding(t, "translate %s rotate %s scale %s" % (tr, ro, sc), "modeling.freeze_transforms(nodes=[...]) before export or rigging", "info"))
    return out


def _check_non_uniform(transforms: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for t in transforms:
        sc = _util.triple(t, "scale", 1.0)
        if max(sc) - min(sc) > 1e-4 or any(v < 0 for v in sc):
            kind = "negative" if any(v < 0 for v in sc) else "non uniform"
            out.append(_finding(t, "%s scale %s" % (kind, sc), "freeze scale (modeling.freeze_transforms) or fix the values; negative scale flips normals in game engines", "warning"))
    return out


def _check_missing_textures() -> List[Dict[str, Any]]:
    out = []
    for f in _ls(type="file"):
        try:
            path = cmds.getAttr(f + ".fileTextureName") or ""
        except Exception:
            continue
        if not path:
            out.append(_finding(f, "file node has no texture path", "materials.set_texture to assign a file, or delete the node", "warning"))
            continue
        if _texture_exists(path):
            continue
        out.append(_finding(f, "missing file %s" % path, "fix the path with materials.set_texture or File Path Editor", "error", path=path))
    return out


def _texture_exists(path: str) -> bool:
    if os.path.exists(path):
        return True
    low = path.lower()
    if "<udim>" in low or "<f>" in low or "<frame" in low or "u<u>" in low:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            return False
        prefix = re.split(r"<[^>]+>", os.path.basename(path))[0]
        try:
            return any(name.startswith(prefix) for name in os.listdir(folder))
        except OSError:
            return False
    return False


def _check_duplicate_names(transforms: Sequence[str]) -> List[Dict[str, Any]]:
    seen: Dict[str, List[str]] = {}
    for t in transforms:
        seen.setdefault(_short(t), []).append(t)
    out = []
    for short, paths in seen.items():
        if len(paths) > 1:
            out.append(_finding(paths[0], "name %r used by %d nodes" % (short, len(paths)), "scene.rename each to a unique name; duplicate short names break FBX/USD round trips", "warning", nodes=paths[:10]))
    return out


def _check_empty_groups(transforms: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for t in transforms:
        try:
            if cmds.nodeType(t) != "transform":
                continue
            kids = cmds.listRelatives(t, children=True, fullPath=True) or []
        except Exception:
            continue
        if not kids and _short(t) not in DEFAULT_CAMERAS:
            out.append(_finding(t, "transform has no children or shapes", "scene.delete it, or parent something under it", "info"))
    return out


def _check_unused_materials() -> List[Dict[str, Any]]:
    out = []
    for sg in _ls(type="shadingEngine"):
        if sg in ("initialShadingGroup", "initialParticleSE"):
            continue
        try:
            members = cmds.sets(sg, query=True) or []
        except Exception:
            members = []
        if members:
            continue
        try:
            mats = cmds.listConnections(sg + ".surfaceShader") or []
        except Exception:
            mats = []
        out.append(_finding(mats[0] if mats else sg, "material is not assigned to any geometry", "materials.assign_material to use it, or delete it (Edit > Delete Unused Nodes)", "info", shading_engine=sg))
    return out


def _check_history(meshes: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for m in meshes:
        hist = _history_nodes(m)
        if hist:
            out.append(_finding(m, "%d construction history nodes (%s)" % (len(hist), ", ".join(h["type"] for h in hist[:5])), "modeling.delete_history(nodes=[...]) once the model is final", "info", history=[h["node"] for h in hist[:10]]))
    return out


def _history_nodes(node: str) -> List[Dict[str, str]]:
    try:
        hist = cmds.listHistory(node, pruneDagObjects=True) or []
    except Exception:
        return []
    out = []
    for h in hist:
        if h == node or h == _short(node):
            continue
        try:
            t = cmds.nodeType(h)
        except Exception:
            continue
        if t in _HISTORY_IGNORE:
            continue
        out.append({"node": h, "type": t})
    return out


def _check_far(transforms: Sequence[str], threshold: float) -> List[Dict[str, Any]]:
    out = []
    for t in transforms:
        try:
            pos = cmds.xform(t, query=True, worldSpace=True, translation=True)
        except Exception:
            continue
        if not pos or len(pos) < 3:
            continue
        d = math.sqrt(sum(float(v) * float(v) for v in pos[:3]))
        if d > threshold:
            out.append(_finding(t, "world position %.1f units from origin" % d, "move it closer or raise far_threshold; far geometry loses float precision in viewports and engines", "warning", distance=round(d, 2)))
    return out


def _check_bbox_scale(meshes: Sequence[str], tiny: float = 0.01, huge: float = 100000.0) -> List[Dict[str, Any]]:
    out = []
    unit = _units().get("linear", "cm")
    for m in meshes:
        bb = _util.world_bbox([m])
        if not bb:
            continue
        longest = max(bb["size"])
        if longest < tiny:
            out.append(_finding(m, "bounding box is only %.5f %s across" % (longest, unit), "check scene units (introspect.env_info) or scale it up; it may have collapsed", "warning", size=bb["size"]))
        elif longest > huge:
            out.append(_finding(m, "bounding box is %.0f %s across" % (longest, unit), "check scene units; imported assets in meters read as 100x too big in cm scenes", "warning", size=bb["size"]))
    return out


# selection -----------------------------------------------------------------
@command("intel.inspect_selection")
def inspect_selection(max_components: int = 50) -> Dict[str, Any]:
    """What is selected right now: objects with summaries, or components with counts."""
    _util.require_maya()
    objects = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    flat = cmds.ls(selection=True, flatten=True) or []
    comps = [c for c in flat if "." in c]
    result: Dict[str, Any] = {"count": len(flat), "objects": [], "components": None}
    for o in dict.fromkeys(objects):
        if not cmds.objExists(o):
            continue
        entry = _util.node_summary(o)
        entry["kind"] = _classify(o) if cmds.nodeType(o) in ("transform", "joint") else cmds.nodeType(o)
        if entry["kind"] == "mesh":
            entry["faces"] = sum(_face_count(s) or 0 for s in _mesh_shapes(o))
            entry["materials"] = sorted({m for s in _mesh_shapes(o) for m in _materials_of(s)})
        bb = _util.world_bbox([o])
        if bb:
            entry["bbox"] = bb
        result["objects"].append(entry)
    if comps:
        result["components"] = _component_info(comps, int(max_components))
    try:
        result["mode"] = "component" if cmds.selectMode(query=True, component=True) else "object"
    except Exception:
        pass
    try:
        result["hilite"] = cmds.ls(hilite=True, long=True) or []
    except Exception:
        pass
    if not flat:
        result["hint"] = "nothing selected; scene.select or ask the user to select something"
    return result


def _component_info(comps: List[str], max_components: int) -> Dict[str, Any]:
    kinds = {"vtx": "vertices", "e": "edges", "f": "faces", "map": "uvs", "cv": "cvs", "vtxFace": "vertex_faces"}
    counts: Dict[str, int] = {}
    by_object: Dict[str, int] = {}
    for c in comps:
        m = re.search(r"\.(\w+)\[", c)
        key = kinds.get(m.group(1), m.group(1)) if m else "other"
        counts[key] = counts.get(key, 0) + 1
        by_object[c.split(".")[0]] = by_object.get(c.split(".")[0], 0) + 1
    info: Dict[str, Any] = {"counts": counts, "by_object": by_object, "sample": comps[:max_components]}
    try:
        faces = cmds.ls(cmds.polyListComponentConversion(comps, toFace=True) or [], flatten=True) or []
        verts = cmds.ls(cmds.polyListComponentConversion(comps, toVertex=True) or [], flatten=True) or []
        edges = cmds.ls(cmds.polyListComponentConversion(comps, toEdge=True) or [], flatten=True) or []
        info["converted"] = {"faces": len(faces), "vertices": len(verts), "edges": len(edges)}
    except Exception:
        pass
    bb = _util.world_bbox(comps)
    if bb:
        info["bbox"] = bb
    return info


# history, bbox, description ------------------------------------------------
@command("intel.get_history_stack")
def get_history_stack(node: str, max_attrs: int = 8) -> Dict[str, Any]:
    """Construction history of a node, oldest first, with key attribute values."""
    _util.require_maya()
    _util.require_nodes([node])
    stack = []
    for h in _history_nodes(node):
        entry: Dict[str, Any] = dict(h)
        attrs: Dict[str, Any] = {}
        try:
            names = cmds.listAttr(h["node"], keyable=True) or []
        except Exception:
            names = []
        for a in names[: int(max_attrs)]:
            try:
                v = cmds.getAttr("%s.%s" % (h["node"], a))
                attrs[a] = v[0] if isinstance(v, list) and len(v) == 1 and isinstance(v[0], tuple) else v
            except Exception:
                continue
        if attrs:
            entry["attrs"] = attrs
        stack.append(entry)
    stack.reverse()
    return {"node": node, "history": stack, "count": len(stack), "fix": "modeling.delete_history to bake it down" if stack else ""}


@command("intel.get_bounding_box")
def get_bounding_box(nodes: List[str] | None = None) -> Dict[str, Any]:
    """World space bounding box of the nodes (or selection), combined and per node."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    combined = _util.world_bbox(targets)
    if combined is None:
        raise BridgeError("no bounding box for %s (no shapes?)" % targets)
    per = {}
    for n in targets:
        bb = _util.world_bbox([n])
        if bb:
            per[n] = bb
    combined["nodes"] = targets
    combined["per_node"] = per
    combined["unit"] = _units().get("linear")
    return combined


@command("intel.describe_for_llm")
def describe_for_llm(node: str) -> Dict[str, Any]:
    """Natural language paragraph about one node, for reasoning or for the user."""
    _util.require_maya()
    _util.require_nodes([node])
    node = (cmds.ls(node, long=True) or [node])[0]
    kind = _classify(node)
    short = _short(node)
    parent = node.rsplit("|", 1)[0] or None
    kids = _children(node)
    bits = ["%s is a %s" % (short, kind if kind != "group" else "group transform")]
    bits.append("at the scene root" if not parent else "parented under %s" % _short(parent))
    if kids:
        bits.append("with %d child transform%s (%s%s)" % (len(kids), "" if len(kids) == 1 else "s", ", ".join(_short(k) for k in kids[:4]), ", ..." if len(kids) > 4 else ""))
    sentences = [", ".join(bits) + "."]
    facts: Dict[str, Any] = {"kind": kind, "parent": parent, "children": len(kids)}
    if kind == "mesh":
        shapes = _mesh_shapes(node)
        faces = sum(_face_count(s) or 0 for s in shapes)
        tris = sum(_poly_eval(s, triangle=True) or 0 for s in shapes)
        verts = sum(_poly_eval(s, vertex=True) or 0 for s in shapes)
        mats = sorted({m for s in shapes for m in _materials_of(s)})
        facts.update({"faces": faces, "triangles": tris, "vertices": verts, "materials": mats})
        mesh_sentence = "The mesh has %d faces (%d triangles, %d vertices), %s" % (
            faces, tris, verts, "shaded with %s" % ", ".join(mats) if mats else "with no material assigned")
        hist = _history_nodes(node)
        if hist:
            facts["history"] = [h["type"] for h in hist]
            mesh_sentence += ", and carries %d history nodes (%s)" % (len(hist), ", ".join(h["type"] for h in hist[:4]))
        sentences.append(mesh_sentence + ".")
    bb = _util.world_bbox([node])
    if bb:
        unit = _units().get("linear", "cm")
        facts["bbox"] = bb
        sentences.append("It spans %.2f x %.2f x %.2f %s centred at (%.1f, %.1f, %.1f)." % (
            bb["size"][0], bb["size"][1], bb["size"][2], unit, bb["center"][0], bb["center"][1], bb["center"][2]))
    t = _util.triple(node, "translate")
    r = _util.triple(node, "rotate")
    s = _util.triple(node, "scale", 1.0)
    facts.update({"translate": t, "rotate": r, "scale": s})
    if any(abs(v) > 1e-6 for v in t + r) or any(abs(v - 1) > 1e-6 for v in s):
        sentences.append("Local transform: translate %s, rotate %s, scale %s." % (t, r, s))
    else:
        sentences.append("Its local transform is at identity.")
    vis = _visible(node)
    anim = _is_animated(node)
    facts.update({"visible": vis, "animated": anim})
    state = []
    if not vis:
        state.append("hidden")
    if anim:
        state.append("animated")
    if state:
        sentences.append("It is " + " and ".join(state) + ".")
    return {"node": node, "description": " ".join(sentences), "facts": facts}


@command("intel.count_polys")
def count_polys(nodes: List[str] | None = None, top: int = 20) -> Dict[str, Any]:
    """Face/triangle/vertex/edge totals for the scene or the given nodes, plus the heaviest meshes."""
    _util.require_maya()
    if nodes:
        _util.require_nodes(nodes)
        meshes = cmds.ls(nodes, dag=True, type="mesh", long=True, noIntermediate=True) or []
    else:
        meshes = _ls(type="mesh", long=True, noIntermediate=True)
    per: List[Dict[str, Any]] = []
    totals = {"faces": 0, "triangles": 0, "vertices": 0, "edges": 0}
    for m in meshes:
        row = {"mesh": m, "transform": _util.transform_of(m)}
        for key, flag in (("faces", "face"), ("triangles", "triangle"), ("vertices", "vertex"), ("edges", "edge")):
            v = _poly_eval(m, **{flag: True}) or 0
            row[key] = v
            totals[key] += v
        per.append(row)
    per.sort(key=lambda r: r["triangles"], reverse=True)
    return {"scope": "nodes" if nodes else "scene", "mesh_count": len(meshes), "totals": totals, "heaviest": per[: int(top)]}


@command("intel.visibility_report")
def visibility_report(max_items: int = 100) -> Dict[str, Any]:
    """Why things are not showing: hidden nodes, hidden layers, intermediate shapes, template/reference overrides, lod visibility."""
    _util.require_maya()
    cap = int(max_items)
    hidden = []
    inherited: List[str] = []
    lod_hidden = []
    overrides = []
    for t in _ls(type="transform", long=True) + _ls(type="joint", long=True):
        if not _visible(t):
            hidden.append(t)
        elif any(t.startswith(h + "|") for h in hidden):
            inherited.append(t)
        try:
            if cmds.attributeQuery("lodVisibility", node=t, exists=True) and not cmds.getAttr(t + ".lodVisibility"):
                lod_hidden.append(t)
        except Exception:
            pass
        try:
            if cmds.getAttr(t + ".overrideEnabled"):
                mode = cmds.getAttr(t + ".overrideDisplayType")
                if mode in (1, 2) or not cmds.getAttr(t + ".overrideVisibility"):
                    overrides.append({"node": t, "display_type": {0: "normal", 1: "template", 2: "reference"}.get(mode, mode), "override_visibility": bool(cmds.getAttr(t + ".overrideVisibility"))})
        except Exception:
            pass
    layers = []
    for layer in _ls(type="displayLayer"):
        if layer == "defaultLayer":
            continue
        try:
            vis = bool(cmds.getAttr(layer + ".visibility"))
            dt = cmds.getAttr(layer + ".displayType")
            members = cmds.editDisplayLayerMembers(layer, query=True, fullNames=True) or []
        except Exception:
            continue
        if not vis or dt in (1, 2):
            layers.append({"layer": layer, "visible": vis, "display_type": {0: "normal", 1: "template", 2: "reference"}.get(dt, dt), "members": members[:20], "member_count": len(members)})
    intermediates = [s for s in _ls(type="mesh", long=True, intermediateObjects=True)]
    try:
        panels = cmds.getPanel(type="modelPanel") or []
        isolated = [p for p in panels if cmds.isolateSelect(p, query=True, state=True)]
    except Exception:
        isolated = []
    return {
        "hidden": hidden[:cap],
        "hidden_by_parent": inherited[:cap],
        "lod_hidden": lod_hidden[:cap],
        "display_overrides": overrides[:cap],
        "layers": layers,
        "intermediate_shapes": intermediates[:cap],
        "isolate_select_panels": isolated,
        "counts": {"hidden": len(hidden), "hidden_by_parent": len(inherited), "lod_hidden": len(lod_hidden), "overrides": len(overrides), "layers": len(layers), "intermediate_shapes": len(intermediates)},
        "fix": "scene.set_attr node.visibility 1, or turn the display layer back on; template/reference overrides live on overrideDisplayType",
    }
