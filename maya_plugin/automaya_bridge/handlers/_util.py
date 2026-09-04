"""Helpers shared by handler modules. Stdlib + maya only."""
from __future__ import annotations

import base64
import os
import tempfile
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

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


def resolve_targets(nodes: Optional[Sequence[str]]) -> List[str]:
    """Use explicit nodes, else the current selection, else raise."""
    if nodes:
        return require_nodes(nodes)
    sel = cmds.ls(selection=True, long=True) or []
    if not sel:
        raise BridgeError("no nodes given and nothing is selected")
    return sel


def shapes_of(node: str, shape_type: Optional[str] = None) -> List[str]:
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


def new_nodes_since(before: Sequence[str]) -> List[str]:
    before_set = set(before)
    return [n for n in (cmds.ls(long=True) or []) if n not in before_set]


def download(url: str, suffix: str = "", headers: Optional[Dict[str, str]] = None, folder: Optional[str] = None) -> str:
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


def import_file(path: str, namespace: Optional[str] = None, group_name: Optional[str] = None) -> Dict[str, Any]:
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


def _import_gltf(path: str, group_name: Optional[str]) -> Dict[str, Any]:
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


def export_selection(path: str, nodes: Sequence[str], fmt: str, options: Optional[Dict[str, Any]] = None) -> str:
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
        cmds.file(path, force=True, exportSelected=True, type="USD Export", options=opts)
    elif fmt in ("ma", "mb"):
        cmds.file(path, force=True, exportSelected=True, type="mayaAscii" if fmt == "ma" else "mayaBinary")
    else:
        raise BridgeError("unsupported export format %r (use fbx, obj, abc, usd, ma, mb)" % fmt)
    return path
