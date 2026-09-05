"""Helpers shared by handler modules. Stdlib + maya only."""
from __future__ import annotations

import base64
import os
import tempfile
import urllib.request
from typing import Any, Dict, List, Sequence

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore


class BridgeError(Exception):
    """Handler level error with a message meant to guide the agent."""


def require_maya() -> None:
    if cmds is None:
        raise BridgeError("maya.cmds is unavailable; this command only works inside Maya")


def ensure_plugin(name: str, hint: str = "") -> None:
    """Load a Maya plugin (mtoa, bullet, AbcExport ...) or raise a helpful error."""
    require_maya()
    if cmds.pluginInfo(name, query=True, loaded=True):
        return
    try:
        cmds.loadPlugin(name, quiet=True)
    except Exception as exc:
        raise BridgeError("plugin %r could not be loaded (%s). %s" % (name, exc, hint))


def exists(node: str) -> bool:
    return bool(cmds.objExists(node))


def require_nodes(nodes: Sequence[str]) -> List[str]:
    missing = [n for n in nodes if not cmds.objExists(n)]
    if missing:
        raise BridgeError("node(s) not found: %s. Use scene.list_nodes to find valid names." % ", ".join(missing))
    return list(nodes)


def resolve_targets(nodes: Sequence[str] | None) -> List[str]:
    """Use explicit nodes, else the current selection, else raise."""
    if nodes:
        return require_nodes(nodes)
    sel = cmds.ls(selection=True, long=True) or []
    if not sel:
        raise BridgeError("no nodes given and nothing is selected")
    return sel


def shapes_of(node: str, shape_type: str | None = None) -> List[str]:
    kwargs: Dict[str, Any] = {"shapes": True, "noIntermediate": True, "fullPath": True}
    if shape_type:
        kwargs["type"] = shape_type
    if cmds.objectType(node, isType="transform") or cmds.nodeType(node) == "joint":
        return cmds.listRelatives(node, **kwargs) or []
    return [node] if (shape_type is None or cmds.nodeType(node) == shape_type) else []


def transform_of(node: str) -> str:
    if cmds.objectType(node, isType="transform"):
        return node
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    return parents[0] if parents else node


def node_summary(node: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": node, "type": cmds.nodeType(node)}
    try:
        if cmds.objectType(node, isType="transform"):
            info["translate"] = cmds.getAttr(node + ".translate")[0]
            info["rotate"] = cmds.getAttr(node + ".rotate")[0]
            info["scale"] = cmds.getAttr(node + ".scale")[0]
            shapes = shapes_of(node)
            info["shapes"] = [{"name": s, "type": cmds.nodeType(s)} for s in shapes]
            children = cmds.listRelatives(node, children=True, type="transform", fullPath=True) or []
            info["child_count"] = len(children)
    except Exception:
        pass
    return info


def set_attr_value(node: str, attr: str, value: Any) -> None:
    """setAttr with the type flags Maya wants for strings, bools, vectors and colors.

    Raises BridgeError with the attribute name when Maya rejects the value, so the
    agent learns which attribute was wrong instead of getting a bare RuntimeError.
    """
    plug = "%s.%s" % (node, attr)
    try:
        if isinstance(value, str):
            cmds.setAttr(plug, value, type="string")
        elif isinstance(value, bool):
            cmds.setAttr(plug, 1 if value else 0)
        elif isinstance(value, (list, tuple)):
            vals = [float(v) for v in value]
            if len(vals) == 3:
                cmds.setAttr(plug, vals[0], vals[1], vals[2], type="double3")
            elif len(vals) == 2:
                cmds.setAttr(plug, vals[0], vals[1], type="double2")
            elif len(vals) == 4:
                cmds.setAttr(plug, vals[0], vals[1], vals[2], vals[3], type="double4")
            else:
                raise BridgeError("attribute %s: lists must have 2, 3 or 4 numbers, got %d" % (plug, len(vals)))
        elif isinstance(value, dict):
            raise BridgeError("attribute %s: dict values are not supported, pass a number, string, bool or list" % plug)
        else:
            cmds.setAttr(plug, value)
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("could not set %s to %r: %s" % (plug, value, exc))


PLACE2D_LINKS = (
    "coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger", "wrapU", "wrapV",
    "repeatUV", "offset", "rotateUV", "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne",
)


def create_file_texture(path: str, color_space: str | None = None, name: str | None = None, uv_tiling: Any = None) -> Dict[str, str]:
    """Create a file node plus a place2dTexture with the 18 standard connections.

    ``color_space`` is a Maya color space name ("sRGB", "Raw", "ACEScg" ...).
    ``uv_tiling`` is a number or [u, v] pair written to repeatUV.
    Returns {"file": node, "place2d": node}.
    """
    require_maya()
    base = name or os.path.splitext(os.path.basename(path))[0] or "file"
    base = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in base)
    if base[0].isdigit():
        base = "tex_" + base
    file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=base + "_file")
    p2d = cmds.shadingNode("place2dTexture", asUtility=True, name=base + "_place2d")
    for attr in PLACE2D_LINKS:
        cmds.connectAttr("%s.%s" % (p2d, attr), "%s.%s" % (file_node, attr), force=True)
    cmds.connectAttr(p2d + ".outUV", file_node + ".uvCoord", force=True)
    cmds.connectAttr(p2d + ".outUvFilterSize", file_node + ".uvFilterSize", force=True)
    cmds.setAttr(file_node + ".fileTextureName", path.replace("\\", "/"), type="string")
    if "<udim>" in path.lower() or "<UDIM>" in path:
        cmds.setAttr(file_node + ".uvTilingMode", 3)
    if color_space:
        try:
            cmds.setAttr(file_node + ".ignoreColorSpaceFileRules", 1)
            cmds.setAttr(file_node + ".colorSpace", color_space, type="string")
        except Exception:
            pass
    if uv_tiling is not None:
        if isinstance(uv_tiling, (int, float)):
            u = v = float(uv_tiling)
        else:
            u, v = float(uv_tiling[0]), float(uv_tiling[1])
        cmds.setAttr(p2d + ".repeatU", u)
        cmds.setAttr(p2d + ".repeatV", v)
    return {"file": file_node, "place2d": p2d}


def long_name(node: str) -> str:
    """Full DAG path of ``node`` (falls back to the given name when ls returns nothing)."""
    found = cmds.ls(node, long=True) or []
    return found[0] if found else node


def long_names(nodes: Sequence[str]) -> List[str]:
    return [long_name(n) for n in nodes]


def world_bbox(nodes: Sequence[str]) -> Dict[str, Any] | None:
    """World space bounding box of nodes as {min, max, size, center} or None."""
    try:
        bb = cmds.exactWorldBoundingBox(list(nodes))
    except Exception:
        bb = None
    if not bb or len(bb) < 6:
        return None
    mn = [round(float(v), 4) for v in bb[:3]]
    mx = [round(float(v), 4) for v in bb[3:6]]
    return {
        "min": mn,
        "max": mx,
        "size": [round(mx[i] - mn[i], 4) for i in range(3)],
        "center": [round((mx[i] + mn[i]) * 0.5, 4) for i in range(3)],
    }


def triple(node: str, attr: str, default: float = 0.0) -> List[float]:
    """Read a 3-component attribute (translate/rotate/scale) as a plain list."""
    try:
        value = cmds.getAttr("%s.%s" % (node, attr))
        if value and isinstance(value[0], (list, tuple)):
            return [round(float(v), 5) for v in value[0]]
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return [round(float(v), 5) for v in value]
    except Exception:
        pass
    return [default, default, default]


def new_nodes_since(before: Sequence[str]) -> List[str]:
    before_set = set(before)
    return [n for n in (cmds.ls(long=True) or []) if n not in before_set]


def download(url: str, suffix: str = "", headers: Dict[str, str] | None = None, folder: str | None = None) -> str:
    """Download a URL to a temp file and return the path (stdlib only)."""
    folder = folder or os.path.join(tempfile.gettempdir(), "automaya")
    os.makedirs(folder, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "automaya-mcp/1.0", **(headers or {})})
    fd, path = tempfile.mkstemp(suffix=suffix, dir=folder)
    os.close(fd)
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return path


def read_file_base64(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def import_file(path: str, namespace: str | None = None, group_name: str | None = None) -> Dict[str, Any]:
    """Import obj/fbx/abc/usd/ma/mb and return the new top level transforms."""
    require_maya()
    ext = os.path.splitext(path)[1].lower()
    type_map = {
        ".obj": ("OBJ", "objExport"),
        ".fbx": ("FBX", "fbxmaya"),
        ".abc": ("Alembic", "AbcImport"),
        ".usd": ("USD Import", "mayaUsdPlugin"),
        ".usda": ("USD Import", "mayaUsdPlugin"),
        ".usdc": ("USD Import", "mayaUsdPlugin"),
        ".usdz": ("USD Import", "mayaUsdPlugin"),
        ".ma": ("mayaAscii", None),
        ".mb": ("mayaBinary", None),
        ".glb": ("GLB", None),
        ".gltf": ("GLTF", None),
    }
    if ext not in type_map:
        raise BridgeError("unsupported file type %r" % ext)
    ftype, plugin = type_map[ext]
    if plugin:
        ensure_plugin(plugin)
    if ext in (".glb", ".gltf"):
        return _import_gltf(path, group_name)
    before = cmds.ls(assemblies=True, long=True) or []
    kwargs: Dict[str, Any] = {"i": True, "type": ftype, "ignoreVersion": True, "mergeNamespacesOnClash": False, "options": "v=0;"}
    if namespace:
        kwargs["namespace"] = namespace
    else:
        kwargs["preserveReferences"] = True
    cmds.file(path, **kwargs)
    after = cmds.ls(assemblies=True, long=True) or []
    new = [n for n in after if n not in before]
    if group_name and new:
        grp = cmds.group(new, name=group_name)
        new = [cmds.ls(grp, long=True)[0]]
    return {"path": path, "top_nodes": new}


def _import_gltf(path: str, group_name: str | None) -> Dict[str, Any]:
    """Maya 2024 has no native glTF import. Try the Autodesk glTF plugin, then
    fall back to converting via the bundled mayaUsd (USD can read glTF when the
    usd plugin was built with it), otherwise tell the caller to request FBX/OBJ."""
    for plugin, ftype in (("gltfTranslator", "glTF"), ("maya2glTF", "glTF")):
        try:
            ensure_plugin(plugin)
            before = cmds.ls(assemblies=True, long=True) or []
            cmds.file(path, i=True, type=ftype, ignoreVersion=True, options="v=0;")
            new = [n for n in (cmds.ls(assemblies=True, long=True) or []) if n not in before]
            if group_name and new:
                new = [cmds.ls(cmds.group(new, name=group_name), long=True)[0]]
            return {"path": path, "top_nodes": new, "via": plugin}
        except BridgeError:
            continue
    raise BridgeError(
        "no glTF importer available in this Maya. Ask the provider for FBX or OBJ "
        "(gen3d tools accept output_format='fbx'), or install the Autodesk glTF plugin."
    )


def export_selection(path: str, nodes: Sequence[str], fmt: str, options: Dict[str, Any] | None = None) -> str:
    require_maya()
    fmt = fmt.lower()
    cmds.select(list(nodes), replace=True)
    if fmt == "fbx":
        ensure_plugin("fbxmaya")
        mel.eval('FBXResetExport')
        mel.eval('FBXExportSmoothingGroups -v true')
        mel.eval('FBXExportInputConnections -v false')
        if options and options.get("animation"):
            mel.eval('FBXExportBakeComplexAnimation -v true')
        mel.eval('FBXExport -f "%s" -s' % path.replace("\\", "/"))
    elif fmt == "obj":
        ensure_plugin("objExport")
        cmds.file(path, force=True, exportSelected=True, type="OBJexport", options="groups=1;ptgroups=1;materials=1;smoothing=1;normals=1")
    elif fmt in ("abc", "alembic"):
        ensure_plugin("AbcExport")
        start = options.get("start", cmds.playbackOptions(query=True, minTime=True)) if options else cmds.playbackOptions(query=True, minTime=True)
        end = options.get("end", cmds.playbackOptions(query=True, maxTime=True)) if options else cmds.playbackOptions(query=True, maxTime=True)
        roots = " ".join("-root %s" % n for n in nodes)
        cmds.AbcExport(j="-frameRange %s %s -uvWrite -worldSpace -writeVisibility -dataFormat ogawa %s -file %s" % (start, end, roots, path.replace("\\", "/")))
    elif fmt in ("usd", "usda", "usdc"):
        ensure_plugin("mayaUsdPlugin")
        opts = "exportUVs=1;exportSkels=none;exportSkin=none;exportBlendShapes=0;exportDisplayColor=0;exportColorSets=1;defaultMeshScheme=catmullClark;animation=%d;" % (
            1 if options and options.get("animation") else 0)
        if options and options.get("animation") and options.get("start") is not None and options.get("end") is not None:
            opts += "startTime=%s;endTime=%s;frameStride=1;" % (options["start"], options["end"])
        cmds.file(path, force=True, exportSelected=True, type="USD Export", options=opts)
    elif fmt in ("ma", "mb"):
        cmds.file(path, force=True, exportSelected=True, type="mayaAscii" if fmt == "ma" else "mayaBinary")
    else:
        raise BridgeError("unsupported export format %r (use fbx, obj, abc, usd, ma, mb)" % fmt)
    return path
