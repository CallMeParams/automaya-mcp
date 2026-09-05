"""scene.* commands: files, node queries, selection, hierarchy, attributes, undo, settings."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError, node_summary, require_maya, require_nodes, resolve_targets

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore


# Maya's named time units and their frame rates.
_TIME_UNITS: Dict[str, float] = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
    "23.976fps": 23.976,
    "29.97fps": 29.97,
    "29.97df": 29.97,
    "47.952fps": 47.952,
    "59.94fps": 59.94,
}
_FPS_TO_UNIT = {v: k for k, v in reversed(list(_TIME_UNITS.items()))}
_LINEAR_UNITS = ("mm", "cm", "m", "km", "in", "ft", "yd", "mi")
_ANGLE_UNITS = ("deg", "rad")
_COUNT_TYPES = {
    "transforms": "transform",
    "meshes": "mesh",
    "cameras": "camera",
    "lights": "light",
    "joints": "joint",
    "materials": "shadingDependNode",
    "nurbs_curves": "nurbsCurve",
    "nurbs_surfaces": "nurbsSurface",
}


def _long(nodes: Sequence[str]) -> List[str]:
    """Long names for a list of nodes, keeping order and dropping missing ones."""
    out: List[str] = []
    for n in nodes or []:
        found = cmds.ls(n, long=True) or []
        out.append(found[0] if found else n)
    return out


def _scene_name() -> str:
    return cmds.file(query=True, sceneName=True) or ""


def _time_unit_from(fps: Any) -> str:
    """Accept 'film', 'ntsc', '24fps', 24, 29.97 ... and return a Maya time unit string."""
    if isinstance(fps, str):
        key = fps.strip().lower()
        if key in _TIME_UNITS:
            return key
        if key.endswith("fps"):
            try:
                fps = float(key[:-3])
            except ValueError:
                raise BridgeError("unrecognised fps %r; use film/ntsc/pal/... or a number like 24 or '30fps'" % fps) from None
        else:
            raise BridgeError("unrecognised time unit %r; use film/ntsc/pal/game/show/palf/ntscf or a number like 24" % fps)
    try:
        value = float(fps)
    except (TypeError, ValueError):
        raise BridgeError("fps must be a number or a Maya time unit name") from None
    if value <= 0:
        raise BridgeError("fps must be positive")
    if value in _FPS_TO_UNIT:
        return _FPS_TO_UNIT[value]
    # Maya 2024 accepts arbitrary integer rates written as '<n>fps'.
    return "%gfps" % value


def _current_fps() -> float | None:
    unit = cmds.currentUnit(query=True, time=True) or ""
    if unit in _TIME_UNITS:
        return _TIME_UNITS[unit]
    if unit.endswith("fps"):
        try:
            return float(unit[:-3])
        except ValueError:
            return None
    return None


# file commands ----------------------------------------------------------------
@command("scene.new", mutates=False)
def new(force: bool = False) -> Dict[str, Any]:
    """Start a new empty scene. Refuses if there are unsaved changes unless force."""
    require_maya()
    if not force and cmds.file(query=True, modified=True):
        raise BridgeError("the scene has unsaved changes; call scene.save first or pass force=true")
    cmds.file(new=True, force=True)
    return {"scene": _scene_name() or "untitled", "modified": False}


@command("scene.open", mutates=False)
def open_scene(path: str, force: bool = False) -> Dict[str, Any]:
    """Open a .ma or .mb file, replacing the current scene."""
    require_maya()
    if not path or not os.path.isfile(path):
        raise BridgeError("file not found: %r" % path)
    if not force and cmds.file(query=True, modified=True):
        raise BridgeError("the scene has unsaved changes; save first or pass force=true")
    cmds.file(path, open=True, force=True, ignoreVersion=True)
    return {"scene": _scene_name(), "top_nodes": cmds.ls(assemblies=True, long=True) or []}


@command("scene.save", mutates=False)
def save(path: str | None = None, as_ascii: bool = False) -> Dict[str, Any]:
    """Save the scene. With ``path`` this behaves like Save As (extension picks ma/mb)."""
    require_maya()
    if path:
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".ma", ".mb"):
            raise BridgeError("save path must end in .ma or .mb (use scene.export for fbx/obj/abc/usd)")
        ftype = "mayaAscii" if (ext == ".ma" or as_ascii) else "mayaBinary"
        if as_ascii and ext == ".mb":
            path = path[:-3] + ".ma"
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        cmds.file(rename=path)
    else:
        current = _scene_name()
        if not current:
            raise BridgeError("the scene is untitled; pass a path to save it")
        ftype = "mayaAscii" if (as_ascii or current.lower().endswith(".ma")) else "mayaBinary"
    saved = cmds.file(save=True, force=True, type=ftype)
    return {"path": saved or _scene_name(), "type": ftype}


@command("scene.import_file", mutates=True)
def import_file(path: str, namespace: str | None = None, group_name: str | None = None) -> Dict[str, Any]:
    """Import obj/fbx/abc/usd/ma/mb/glb into the scene and return the new top level nodes."""
    require_maya()
    if not path or not os.path.isfile(path):
        raise BridgeError("file not found: %r" % path)
    return _util.import_file(path, namespace=namespace, group_name=group_name)


@command("scene.export", mutates=False)
def export(
    path: str,
    nodes: List[str] | None = None,
    format: str | None = None,
    animation: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> Dict[str, Any]:
    """Export nodes (or the selection) to fbx/obj/abc/usd/ma/mb."""
    require_maya()
    if not path:
        raise BridgeError("path is required")
    fmt = (format or os.path.splitext(path)[1].lstrip(".")).lower()
    if not fmt:
        raise BridgeError("could not infer the format; pass format or use a file extension")
    targets = resolve_targets(nodes)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    options: Dict[str, Any] = {"animation": animation}
    if start is not None:
        options["start"] = start
    if end is not None:
        options["end"] = end
    previous = cmds.ls(selection=True, long=True) or []
    out = _util.export_selection(path, targets, fmt, options)
    if previous:
        cmds.select(previous, replace=True)
    else:
        cmds.select(clear=True)
    return {"path": out, "format": fmt, "nodes": targets}


# queries ----------------------------------------------------------------------
@command("scene.get_info")
def get_info() -> Dict[str, Any]:
    """Overview of the open scene: file, units, fps, frame range, node counts, selection, references."""
    require_maya()
    counts: Dict[str, int] = {}
    for label, ntype in _COUNT_TYPES.items():
        try:
            counts[label] = len(cmds.ls(type=ntype) or [])
        except Exception:
            counts[label] = 0
    refs: List[str] = []
    try:
        refs = cmds.file(query=True, reference=True) or []
    except Exception:
        refs = []
    return {
        "scene": _scene_name() or "untitled",
        "modified": bool(cmds.file(query=True, modified=True)),
        "units": {
            "linear": cmds.currentUnit(query=True, linear=True),
            "angle": cmds.currentUnit(query=True, angle=True),
            "time": cmds.currentUnit(query=True, time=True),
        },
        "fps": _current_fps(),
        "up_axis": cmds.upAxis(query=True, axis=True),
        "frame_range": {
            "start": cmds.playbackOptions(query=True, minTime=True),
            "end": cmds.playbackOptions(query=True, maxTime=True),
            "animation_start": cmds.playbackOptions(query=True, animationStartTime=True),
            "animation_end": cmds.playbackOptions(query=True, animationEndTime=True),
            "current": cmds.currentTime(query=True),
        },
        "counts": counts,
        "selection": cmds.ls(selection=True, long=True) or [],
        "references": refs,
    }


@command("scene.list_nodes")
def list_nodes(
    type: str | None = None,
    pattern: str | None = None,
    selection_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    long: bool = True,
    include_defaults: bool = False,
) -> Dict[str, Any]:
    """List nodes with pagination. ``pattern`` is a Maya wildcard like 'pCube*'."""
    require_maya()
    limit = max(1, int(limit))
    offset = max(0, int(offset))
    kwargs: Dict[str, Any] = {"long": bool(long)}
    if type:
        kwargs["type"] = type
    if selection_only:
        kwargs["selection"] = True
    if pattern:
        nodes = cmds.ls(pattern, **kwargs) or []
    else:
        nodes = cmds.ls(**kwargs) or []
    if not include_defaults and not selection_only:
        defaults = set(cmds.ls(defaultNodes=True, long=bool(long)) or [])
        if defaults:
            nodes = [n for n in nodes if n not in defaults]
    total = len(nodes)
    page = nodes[offset : offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "has_more": offset + limit < total, "nodes": page}


def _materials_of(node: str) -> List[str]:
    """Surface shaders assigned to the node's shapes."""
    out: List[str] = []
    for shape in _util.shapes_of(node):
        engines = cmds.listConnections(shape, type="shadingEngine") or []
        for sg in engines:
            for mat in cmds.listConnections(sg + ".surfaceShader") or []:
                if mat not in out:
                    out.append(mat)
    return out


def _plain_value(value: Any) -> Any:
    """Unwrap Maya's [(x, y, z)] compound style into a flat list."""
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        return list(value[0])
    if isinstance(value, tuple):
        return list(value)
    return value


@command("scene.get_node_info")
def get_node_info(node: str, attributes: List[str] | None = None) -> Dict[str, Any]:
    """Details for one node: type, transform, shapes, parent, children, materials, connections, custom attrs."""
    require_maya()
    require_nodes([node])
    long_name = _long([node])[0]
    info: Dict[str, Any] = node_summary(long_name)
    info["name"] = long_name
    info["short_name"] = long_name.split("|")[-1]
    parents = cmds.listRelatives(long_name, parent=True, fullPath=True) or []
    info["parent"] = parents[0] if parents else None
    info["children"] = cmds.listRelatives(long_name, children=True, type="transform", fullPath=True) or []
    if "shapes" not in info:
        info["shapes"] = [{"name": s, "type": cmds.nodeType(s)} for s in _util.shapes_of(long_name)]
    info["materials"] = _materials_of(long_name)
    incoming = cmds.listConnections(long_name, source=True, destination=False, plugs=True) or []
    info["incoming_connections"] = len(incoming)
    custom: Dict[str, Any] = {}
    for attr in cmds.listAttr(long_name, userDefined=True) or []:
        try:
            custom[attr] = _plain_value(cmds.getAttr("%s.%s" % (long_name, attr)))
        except Exception:
            custom[attr] = None
    info["custom_attrs"] = custom
    if attributes:
        values: Dict[str, Any] = {}
        for attr in attributes:
            plug = "%s.%s" % (long_name, attr)
            try:
                values[attr] = _plain_value(cmds.getAttr(plug))
            except Exception as exc:
                values[attr] = "error: %s" % exc
        info["attributes"] = values
    return info


# selection and hierarchy -----------------------------------------------------
@command("scene.select", mutates=False)
def select(nodes: List[str] | None = None, add: bool = False, clear: bool = False) -> Dict[str, Any]:
    """Select nodes (replace by default), add to the selection, or clear it."""
    require_maya()
    if clear:
        cmds.select(clear=True)
        return {"selection": []}
    if not nodes:
        raise BridgeError("pass nodes to select, or clear=true")
    require_nodes(nodes)
    if add:
        cmds.select(nodes, add=True)
    else:
        cmds.select(nodes, replace=True)
    return {"selection": cmds.ls(selection=True, long=True) or []}


@command("scene.get_selection")
def get_selection(long: bool = True) -> Dict[str, Any]:
    require_maya()
    sel = cmds.ls(selection=True, long=bool(long)) or []
    return {"selection": sel, "count": len(sel)}


@command("scene.delete", mutates=True)
def delete(nodes: List[str] | None = None) -> Dict[str, Any]:
    """Delete nodes (or the selection)."""
    require_maya()
    targets = resolve_targets(nodes)
    cmds.delete(targets)
    return {"deleted": targets}


@command("scene.rename", mutates=True)
def rename(node: str, new_name: str) -> Dict[str, Any]:
    require_maya()
    require_nodes([node])
    if not new_name or not new_name.strip():
        raise BridgeError("new_name must not be empty")
    result = cmds.rename(node, new_name)
    return {"old": node, "name": _long([result])[0] if result else new_name}


@command("scene.parent", mutates=True)
def parent(nodes: List[str], parent: str | None = None) -> Dict[str, Any]:
    """Parent nodes under ``parent``; omit parent to move them to world."""
    require_maya()
    if not nodes:
        raise BridgeError("nodes is required")
    require_nodes(nodes)
    if parent:
        require_nodes([parent])
        result = cmds.parent(nodes, parent)
    else:
        result = cmds.parent(nodes, world=True)
    return {"nodes": _long(result or nodes), "parent": _long([parent])[0] if parent else None}


@command("scene.group", mutates=True)
def group(nodes: List[str] | None = None, name: str | None = None) -> Dict[str, Any]:
    """Group nodes under a new transform. With no nodes an empty group is made."""
    require_maya()
    kwargs: Dict[str, Any] = {}
    if name:
        kwargs["name"] = name
    if nodes:
        require_nodes(nodes)
        grp = cmds.group(nodes, **kwargs)
    else:
        grp = cmds.group(empty=True, **kwargs)
    long_name = _long([grp])[0]
    return {"group": long_name, "members": _long(nodes or []), "node_summary": node_summary(long_name)}


# attributes ------------------------------------------------------------------
def _plug(node: str, attr: str) -> str:
    plug = "%s.%s" % (node, attr)
    if not cmds.objExists(plug):
        raise BridgeError("attribute not found: %s. Use scene.get_node_info to list attributes." % plug)
    return plug


def _set_one(node: str, attr: str, value: Any) -> Any:
    plug = _plug(node, attr)
    try:
        if cmds.getAttr(plug, lock=True):
            raise BridgeError("%s is locked; unlock it first" % plug)
    except BridgeError:
        raise
    except Exception:
        pass
    try:
        if cmds.connectionInfo(plug, isDestination=True):
            raise BridgeError("%s is driven by a connection (%s); disconnect it or set the source instead" % (plug, cmds.connectionInfo(plug, sourceFromDestination=True)))
    except BridgeError:
        raise
    except Exception:
        pass
    try:
        attr_type = cmds.getAttr(plug, type=True) or ""
    except Exception:
        attr_type = ""
    if isinstance(value, dict):
        raise BridgeError("value for %s must be a number, bool, string or list" % plug)
    if attr_type == "string" or (isinstance(value, str) and attr_type not in ("enum", "bool")):
        if not isinstance(value, str):
            value = str(value)
        cmds.setAttr(plug, value, type="string")
    elif isinstance(value, (list, tuple)):
        values = list(value)
        if attr_type == "matrix":
            cmds.setAttr(plug, *values, type="matrix")
        elif attr_type in ("doubleArray", "floatArray", "Int32Array", "stringArray", "pointArray", "vectorArray"):
            cmds.setAttr(plug, len(values), *values, type=attr_type)
        elif attr_type:
            cmds.setAttr(plug, *values, type=attr_type)
        else:
            cmds.setAttr(plug, *values)
    elif attr_type == "enum" and isinstance(value, str):
        names = (cmds.attributeQuery(attr.split(".")[-1], node=node, listEnum=True) or [""])[0].split(":")
        lookup = {n.split("=")[0].strip().lower(): i for i, n in enumerate(names)}
        if value.strip().lower() not in lookup:
            raise BridgeError("enum %s accepts %s" % (plug, ", ".join(sorted(lookup))))
        cmds.setAttr(plug, lookup[value.strip().lower()])
    elif attr_type == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        cmds.setAttr(plug, bool(value))
    elif attr_type in ("long", "short", "byte", "enum"):
        cmds.setAttr(plug, int(value))
    elif attr_type in ("double", "float", "doubleLinear", "doubleAngle", "time", "distance"):
        cmds.setAttr(plug, float(value))
    else:
        cmds.setAttr(plug, value)
    return _plain_value(cmds.getAttr(plug))


@command("scene.set_attr", mutates=True)
def set_attr(node: str, attr: str, value: Any = None) -> Dict[str, Any]:
    """Set one attribute. Handles float/int/bool/string, enums by name, and 2/3 element lists."""
    require_maya()
    require_nodes([node])
    if value is None:
        raise BridgeError("value is required")
    new_value = _set_one(node, attr, value)
    return {"node": node, "attr": attr, "value": new_value}


@command("scene.get_attr")
def get_attr(node: str, attr: str) -> Dict[str, Any]:
    require_maya()
    require_nodes([node])
    plug = _plug(node, attr)
    try:
        attr_type = cmds.getAttr(plug, type=True)
    except Exception:
        attr_type = None
    return {"node": node, "attr": attr, "value": _plain_value(cmds.getAttr(plug)), "type": attr_type}


@command("scene.set_attrs", mutates=True)
def set_attrs(node: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Set several attributes on one node in a single undo step."""
    require_maya()
    require_nodes([node])
    if not isinstance(attrs, dict) or not attrs:
        raise BridgeError("attrs must be a non empty {attr: value} mapping")
    results: Dict[str, Any] = {}
    for attr, value in attrs.items():
        results[attr] = _set_one(node, attr, value)
    return {"node": node, "values": results}


@command("scene.connect_attr", mutates=True)
def connect_attr(src: str, dst: str, force: bool = False) -> Dict[str, Any]:
    """Connect src plug to dst plug, e.g. 'locator1.translateX' -> 'pCube1.translateX'."""
    require_maya()
    for plug in (src, dst):
        if "." not in plug or not cmds.objExists(plug):
            raise BridgeError("plug not found: %r (expected node.attribute)" % plug)
    cmds.connectAttr(src, dst, force=bool(force))
    return {"source": src, "destination": dst}


@command("scene.disconnect_attr", mutates=True)
def disconnect_attr(src: str, dst: str) -> Dict[str, Any]:
    require_maya()
    for plug in (src, dst):
        if "." not in plug or not cmds.objExists(plug):
            raise BridgeError("plug not found: %r (expected node.attribute)" % plug)
    try:
        cmds.disconnectAttr(src, dst)
    except RuntimeError as exc:
        raise BridgeError("could not disconnect %s -> %s: %s" % (src, dst, exc)) from None
    return {"source": src, "destination": dst}


# undo / redo (not wrapped in an undo chunk on purpose) ------------------------
@command("scene.undo", mutates=False)
def undo(count: int = 1) -> Dict[str, Any]:
    """Undo the last ``count`` operations (each tool call is one undo step)."""
    require_maya()
    done = 0
    for _ in range(max(1, int(count))):
        try:
            cmds.undo()
            done += 1
        except RuntimeError:
            break
    return {"undone": done}


@command("scene.redo", mutates=False)
def redo(count: int = 1) -> Dict[str, Any]:
    require_maya()
    done = 0
    for _ in range(max(1, int(count))):
        try:
            cmds.redo()
            done += 1
        except RuntimeError:
            break
    return {"redone": done}


# settings --------------------------------------------------------------------
@command("scene.settings", mutates=True)
def settings(
    linear_unit: str | None = None,
    angle_unit: str | None = None,
    time_unit: str | None = None,
    fps: Any | None = None,
    up_axis: str | None = None,
    start: float | None = None,
    end: float | None = None,
) -> Dict[str, Any]:
    """Change scene units, frame rate, up axis and playback range. Returns the resulting settings."""
    require_maya()
    if linear_unit:
        if linear_unit not in _LINEAR_UNITS:
            raise BridgeError("linear_unit must be one of %s" % ", ".join(_LINEAR_UNITS))
        cmds.currentUnit(linear=linear_unit)
    if angle_unit:
        if angle_unit not in _ANGLE_UNITS:
            raise BridgeError("angle_unit must be deg or rad")
        cmds.currentUnit(angle=angle_unit)
    if time_unit is not None or fps is not None:
        cmds.currentUnit(time=_time_unit_from(time_unit if time_unit is not None else fps))
    if up_axis:
        axis = up_axis.lower()
        if axis not in ("y", "z"):
            raise BridgeError("up_axis must be y or z")
        cmds.upAxis(axis=axis, rotateView=True)
    if start is not None:
        cmds.playbackOptions(minTime=start, animationStartTime=start)
    if end is not None:
        cmds.playbackOptions(maxTime=end, animationEndTime=end)
    if start is not None and end is not None and end < start:
        raise BridgeError("end must be >= start")
    return {
        "units": {
            "linear": cmds.currentUnit(query=True, linear=True),
            "angle": cmds.currentUnit(query=True, angle=True),
            "time": cmds.currentUnit(query=True, time=True),
        },
        "fps": _current_fps(),
        "up_axis": cmds.upAxis(query=True, axis=True),
        "frame_range": {
            "start": cmds.playbackOptions(query=True, minTime=True),
            "end": cmds.playbackOptions(query=True, maxTime=True),
        },
    }
