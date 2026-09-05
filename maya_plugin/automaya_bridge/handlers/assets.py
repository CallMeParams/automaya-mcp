"""assets.* commands: import downloaded models, build skydomes and PBR networks.

The MCP server does every download; these handlers only see local paths.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from ..registry import command
from ._util import BridgeError, ensure_plugin, import_file, node_summary, require_maya

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

# Every import done through this module during the Maya session.
IMPORT_LOG: List[Dict[str, Any]] = []

# map alias -> standardSurface attribute it drives
_PBR_SLOTS = {
    "base_color": ("baseColor", True),
    "diffuse": ("baseColor", True),
    "roughness": ("specularRoughness", False),
    "metalness": ("metalness", False),
    "specular": ("specular", False),
    "opacity": ("opacity", True),
    "emission": ("emissionColor", True),
    "translucent": ("subsurfaceColor", True),
}


def _log(kind: str, path: str, nodes: List[str], extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    entry = {"kind": kind, "path": path, "nodes": nodes, "time": time.time()}
    if extra:
        entry.update(extra)
    IMPORT_LOG.append(entry)
    if len(IMPORT_LOG) > 500:
        del IMPORT_LOG[:-500]
    return entry


def _apply_transform(nodes: List[str], scale: float | None, freeze: bool, center: bool) -> List[str]:
    if scale and abs(scale - 1.0) > 1e-9:
        for n in nodes:
            cmds.scale(scale, scale, scale, n, relative=True)
    if center:
        for n in nodes:
            cmds.xform(n, centerPivots=True)
            bb = cmds.exactWorldBoundingBox(n)
            if bb and len(bb) == 6:
                cx = (bb[0] + bb[3]) / 2.0
                cz = (bb[2] + bb[5]) / 2.0
                cmds.move(-cx, -bb[1], -cz, n, relative=True, worldSpace=True)
    if freeze:
        cmds.makeIdentity(nodes, apply=True, translate=True, rotate=True, scale=True, normal=0)
    return nodes


@command("assets.import_model", mutates=True)
def import_model(path: str, name: str | None = None, group: bool = False, scale: float | None = None, freeze: bool = False, center: bool = False, namespace: str | None = None) -> Dict[str, Any]:
    """Import a model file (obj, fbx, abc, usd, ma, mb, glb/gltf when an importer exists).

    ``name`` renames the single top node, or names the group when ``group`` is true.
    """
    require_maya()
    if not path or not os.path.isfile(path):
        raise BridgeError("file not found: %r (the server downloads to a temp folder; pass that path)" % path)
    group_name = name if (group and name) else ("%s_grp" % name if group else None)
    result = import_file(path, namespace=namespace, group_name=group_name)
    nodes = list(result.get("top_nodes") or [])
    if not nodes:
        raise BridgeError("import of %s produced no top level nodes (empty file or unsupported content)" % os.path.basename(path))
    if name and not group and len(nodes) == 1:
        nodes = [cmds.ls(cmds.rename(nodes[0], name), long=True)[0]]
    nodes = _apply_transform(nodes, scale, freeze, center)
    out = {"path": path, "top_nodes": nodes, "nodes": [node_summary(n) for n in nodes]}
    if result.get("via"):
        out["via"] = result["via"]
    _log("model", path, nodes)
    return out


@command("assets.create_skydome", mutates=True)
def create_skydome(path: str, name: str = "skydome", intensity: float = 1.0, rotation: float = 0.0, exposure: float = 0.0, camera_visible: bool = True) -> Dict[str, Any]:
    """Wire an HDRI into an aiSkyDomeLight (mtoa) or, without Arnold, a large
    inside-out sphere with a file texture as a viewport environment."""
    require_maya()
    if not path or not os.path.isfile(path):
        raise BridgeError("HDRI file not found: %r" % path)
    file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name="%s_file" % name)
    place = cmds.shadingNode("place2dTexture", asUtility=True, name="%s_place2d" % name)
    for attr in ("coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger", "wrapU", "wrapV", "repeatUV", "offset", "rotateUV", "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne"):
        try:
            cmds.connectAttr("%s.%s" % (place, attr), "%s.%s" % (file_node, attr), force=True)
        except Exception:
            pass
    try:
        cmds.connectAttr(place + ".outUV", file_node + ".uv", force=True)
        cmds.connectAttr(place + ".outUvFilterSize", file_node + ".uvFilterSize", force=True)
    except Exception:
        pass
    cmds.setAttr(file_node + ".fileTextureName", path, type="string")
    try:
        cmds.setAttr(file_node + ".colorSpace", "scene-linear Rec.709-sRGB" if path.lower().endswith((".hdr", ".exr")) else "sRGB", type="string")
    except Exception:
        pass
    arnold = False
    try:
        ensure_plugin("mtoa")
        arnold = True
    except BridgeError:
        arnold = False
    if arnold:
        shape = cmds.shadingNode("aiSkyDomeLight", asLight=True, name="%sShape" % name)
        xform = cmds.listRelatives(shape, parent=True, fullPath=True)
        top = xform[0] if xform else shape
        if xform:
            top = cmds.ls(cmds.rename(xform[0], name), long=True)[0]
            shape = cmds.listRelatives(top, shapes=True, fullPath=True)[0]
        cmds.connectAttr(file_node + ".outColor", shape + ".color", force=True)
        cmds.setAttr(shape + ".intensity", float(intensity))
        cmds.setAttr(shape + ".exposure", float(exposure))
        cmds.setAttr(shape + ".camera", 1.0 if camera_visible else 0.0)
        cmds.setAttr(top + ".rotateY", float(rotation))
        nodes = [top]
        _log("skydome", path, nodes, {"arnold": True})
        return {"light": top, "shape": shape, "file_node": file_node, "arnold": True, "path": path}
    # Fallback: inverted sphere with a surface shader so it renders flat in viewport 2.0.
    sphere = cmds.polySphere(name=name, radius=5000.0, subdivisionsX=64, subdivisionsY=32)[0]
    cmds.polyNormal(sphere, normalMode=0, userNormalMode=0, constructionHistory=False)
    cmds.setAttr(sphere + ".rotateY", float(rotation))
    shader = cmds.shadingNode("surfaceShader", asShader=True, name="%s_surface" % name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="%s_SG" % name)
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    cmds.connectAttr(file_node + ".outColor", shader + ".outColor", force=True)
    cmds.sets(sphere, edit=True, forceElement=sg)
    try:
        cmds.setAttr(file_node + ".colorGain", intensity, intensity, intensity, type="double3")
        cmds.setAttr(sphere + ".castsShadows", 0)
        cmds.setAttr(sphere + ".receiveShadows", 0)
    except Exception:
        pass
    nodes = cmds.ls(sphere, long=True)
    _log("skydome", path, nodes, {"arnold": False})
    return {"light": nodes[0], "shader": shader, "file_node": file_node, "arnold": False, "path": path, "note": "mtoa not available; built an environment sphere instead of aiSkyDomeLight"}


def _file_texture(path: str, name: str, color: bool) -> str:
    node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=name)
    place = cmds.shadingNode("place2dTexture", asUtility=True, name=name + "_p2d")
    try:
        cmds.connectAttr(place + ".outUV", node + ".uv", force=True)
        cmds.connectAttr(place + ".outUvFilterSize", node + ".uvFilterSize", force=True)
        for attr in ("coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger", "wrapU", "wrapV", "repeatUV", "offset", "rotateUV", "noiseUV"):
            cmds.connectAttr("%s.%s" % (place, attr), "%s.%s" % (node, attr), force=True)
    except Exception:
        pass
    cmds.setAttr(node + ".fileTextureName", path, type="string")
    try:
        cmds.setAttr(node + ".colorSpace", "sRGB" if color else "Raw", type="string")
        if not color:
            cmds.setAttr(node + ".alphaIsLuminance", 1)
    except Exception:
        pass
    return node


def _minimal_pbr_network(maps: Dict[str, str], name: str, assign_to: List[str] | None, shader_type: str) -> Dict[str, Any]:
    shader = cmds.shadingNode(shader_type, asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    files: Dict[str, str] = {}
    for alias, path in maps.items():
        if not path or not os.path.isfile(path):
            continue
        key = alias.lower()
        if key in _PBR_SLOTS:
            attr, is_color = _PBR_SLOTS[key]
            node = _file_texture(path, "%s_%s" % (name, key), is_color)
            src = ".outColor" if is_color else ".outAlpha"
            cmds.connectAttr(node + src, "%s.%s" % (shader, attr), force=True)
            files[key] = node
        elif key in ("normal", "normal_dx"):
            node = _file_texture(path, "%s_normal" % name, False)
            bump = cmds.shadingNode("bump2d", asUtility=True, name="%s_bump2d" % name)
            cmds.setAttr(bump + ".bumpInterp", 1)
            cmds.connectAttr(node + ".outAlpha", bump + ".bumpValue", force=True)
            cmds.connectAttr(bump + ".outNormal", shader + ".normalCamera", force=True)
            files["normal"] = node
        elif key == "bump":
            node = _file_texture(path, "%s_bump" % name, False)
            bump = cmds.shadingNode("bump2d", asUtility=True, name="%s_bump2d" % name)
            cmds.connectAttr(node + ".outAlpha", bump + ".bumpValue", force=True)
            cmds.connectAttr(bump + ".outNormal", shader + ".normalCamera", force=True)
            files["bump"] = node
        elif key == "arm":
            # Packed ambient occlusion (R), roughness (G), metalness (B).
            node = _file_texture(path, "%s_arm" % name, False)
            cmds.connectAttr(node + ".outColorG", shader + ".specularRoughness", force=True)
            cmds.connectAttr(node + ".outColorB", shader + ".metalness", force=True)
            files["arm"] = node
        elif key == "displacement":
            node = _file_texture(path, "%s_disp" % name, False)
            disp = cmds.shadingNode("displacementShader", asShader=True, name="%s_dispShader" % name)
            cmds.connectAttr(node + ".outAlpha", disp + ".displacement", force=True)
            cmds.connectAttr(disp + ".displacement", sg + ".displacementShader", force=True)
            files["displacement"] = node
        elif key == "ao":
            files["ao"] = _file_texture(path, "%s_ao" % name, False)
    if assign_to:
        cmds.sets(assign_to, edit=True, forceElement=sg)
    return {"shader": shader, "shading_group": sg, "file_nodes": files, "assigned_to": assign_to or []}


@command("assets.import_texture_set", mutates=True)
def import_texture_set(maps: Dict[str, str], name: str = "pbrMat", assign_to: List[str] | None = None, shader_type: str = "standardSurface") -> Dict[str, Any]:
    """Build a PBR shading network from a dict of map alias -> file path
    (base_color, normal, roughness, metalness, arm, ao, displacement, bump,
    opacity). Delegates to materials.create_pbr_network when that handler
    exists, else wires a standardSurface itself."""
    require_maya()
    if not maps:
        raise BridgeError("maps is empty; expected {'base_color': path, 'normal': path, ...}")
    missing = [p for p in maps.values() if p and not os.path.isfile(p)]
    if missing:
        raise BridgeError("texture files not found: %s" % ", ".join(missing[:5]))
    try:
        from . import materials as materials_mod  # type: ignore

        fn = getattr(materials_mod, "create_pbr_network", None)
    except Exception:
        fn = None
    if fn is not None:
        try:
            result = fn(maps=maps, name=name, assign_to=assign_to, shader_type=shader_type)
            result = dict(result) if isinstance(result, dict) else {"result": result}
            result["via"] = "materials.create_pbr_network"
            _log("texture_set", ";".join(maps.values()), [result.get("shader", name)])
            return result
        except TypeError:
            pass  # signature mismatch, fall through to the minimal network
    result = _minimal_pbr_network(maps, name, assign_to, shader_type)
    result["via"] = "assets.minimal"
    _log("texture_set", ";".join(maps.values()), [result["shader"]])
    return result


@command("assets.list_imported")
def list_imported(limit: int = 50) -> List[Dict[str, Any]]:
    """Everything imported through the assets/gen handlers in this Maya session, newest last."""
    return IMPORT_LOG[-int(limit):]
