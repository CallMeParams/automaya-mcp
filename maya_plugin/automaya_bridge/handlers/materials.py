"""materials.* commands: shaders, shading groups, texture wiring, PBR networks."""
from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError, require_maya

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

SHADER_TYPES = ("lambert", "blinn", "phong", "standardSurface", "aiStandardSurface", "aiFlat", "useBackground")
ARNOLD_TYPES = ("aiStandardSurface", "aiFlat")
DEFAULT_MATERIALS = {"lambert1", "standardSurface1", "particleCloud1", "shaderGlow1", "openPBRSurface1"}

# Where the "main colour" lives for each shader type.
COLOR_ATTR = {
    "lambert": "color", "blinn": "color", "phong": "color", "phongE": "color",
    "standardSurface": "baseColor", "aiStandardSurface": "baseColor", "aiFlat": "color",
    "openPBRSurface": "baseColor",
}

# Attribute aliases so the agent can say "baseColor" on a lambert or "roughness" on a standardSurface.
ATTR_ALIASES = {
    "baseColor": ("color", "baseColor"),
    "color": ("baseColor", "color"),
    "diffuse": ("baseColor", "color"),
    "roughness": ("specularRoughness",),
    "specularRoughness": ("roughness",),
    "metalness": ("metallic",),
    "metallic": ("metalness",),
    "normal": ("normalCamera",),
    "bump": ("normalCamera",),
    "normalCamera": ("normalCamera",),
    "opacity": ("transparency",),
    "emission": ("emissionColor", "incandescence"),
    "emissionColor": ("incandescence",),
    "specular": ("specularColor",),
    "displacement": ("displacement",),
}

SCALAR_ATTRS = {
    "specularRoughness", "metalness", "specular", "specularIOR", "coat", "coatRoughness", "sheen", "sheenRoughness",
    "transmission", "subsurface", "emission", "diffuse", "eccentricity", "specularRollOff", "reflectivity", "cosinePower",
    "diffuseRoughness", "coatIOR", "thinFilmThickness", "transmissionExtraRoughness", "translucence", "glowIntensity",
    "displacement", "bumpValue",
}
COLOR_ATTRS = {
    "color", "baseColor", "specularColor", "transparency", "opacity", "incandescence", "ambientColor", "emissionColor",
    "coatColor", "sheenColor", "subsurfaceColor", "transmissionColor", "subsurfaceRadius", "normalCamera",
}

# Maps that are data, not colour, so they get Raw / linear handling and alpha-as-luminance.
DATA_ATTRS = SCALAR_ATTRS | {"normalCamera"}


# helpers ------------------------------------------------------------------
def _material_type(material: str) -> str:
    return cmds.nodeType(material)


def _shading_groups(material: str) -> List[str]:
    """Shading engines the material feeds (surface, displacement or volume)."""
    out: List[str] = []
    for plug in ("outColor", "outValue", "displacement", "outVolume"):
        try:
            found = cmds.listConnections("%s.%s" % (material, plug), type="shadingEngine", source=False, destination=True) or []
        except Exception:
            found = []
        for sg in found:
            if sg not in out:
                out.append(sg)
    return out


def _shading_group_for(material: str, create: bool = True) -> str | None:
    groups = _shading_groups(material)
    if groups:
        return groups[0]
    if not create:
        return None
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=material + "SG")
    cmds.connectAttr(material + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def _has_attr(node: str, attr: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


def _resolve_attr(node: str, attr: str) -> str:
    """Return the attribute that exists on ``node`` for ``attr`` or one of its aliases."""
    if _has_attr(node, attr):
        return attr
    for alias in ATTR_ALIASES.get(attr, ()):
        if _has_attr(node, alias):
            return alias
    raise BridgeError(
        "%s (%s) has no attribute %r. Common names: baseColor/color, specularRoughness, metalness, normalCamera, opacity, emissionColor." % (node, _material_type(node), attr)
    )


def _attr_kind(node: str, attr: str) -> str:
    """'color' (3 channel) or 'scalar' (single float)."""
    if attr in SCALAR_ATTRS:
        return "scalar"
    if attr in COLOR_ATTRS:
        return "color"
    try:
        kind = cmds.getAttr("%s.%s" % (node, attr), type=True)
    except Exception:
        kind = None
    return "color" if kind in ("float3", "double3") else "scalar"


def _apply_attrs(material: str, attrs: Dict[str, Any]) -> List[str]:
    applied: List[str] = []
    for key, value in (attrs or {}).items():
        attr = _resolve_attr(material, key)
        _util.set_attr_value(material, attr, value)
        applied.append(attr)
    return applied


def _assign(sg: str, targets: Sequence[str]) -> List[str]:
    missing = [t for t in targets if not cmds.objExists(t)]
    if missing:
        raise BridgeError("cannot assign, node(s) or component(s) not found: %s" % ", ".join(missing))
    cmds.sets(list(targets), edit=True, forceElement=sg)
    return list(targets)


def _upstream_plug(plug: str) -> str | None:
    found = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
    return found[0] if found else None


def _file_nodes_upstream(node: str) -> List[str]:
    history = cmds.listHistory(node, pruneDagObjects=True) or []
    return [n for n in history if n != node and cmds.nodeType(n) == "file"]


def _jsonable_attr(plug: str) -> Any:
    value = cmds.getAttr(plug)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        return list(value[0])
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return repr(value)


# commands -----------------------------------------------------------------
@command("materials.create", mutates=True)
def create(type: str = "standardSurface", name: str | None = None, color: Sequence[float] | None = None, attrs: Dict[str, Any] | None = None, assign_to: Sequence[str] | None = None) -> Dict[str, Any]:
    """Create a shader plus its shading group, optionally colour it and assign it."""
    require_maya()
    if type not in SHADER_TYPES:
        raise BridgeError("unknown material type %r. Use one of: %s" % (type, ", ".join(SHADER_TYPES)))
    if type in ARNOLD_TYPES:
        _util.ensure_plugin("mtoa", "aiStandardSurface/aiFlat need Arnold (mtoa); use standardSurface for a renderer agnostic PBR shader.")
    shader = cmds.shadingNode(type, asShader=True, name=name or (type + "1"))
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    applied: List[str] = []
    if color is not None and type in COLOR_ATTR:
        _util.set_attr_value(shader, COLOR_ATTR[type], color)
        applied.append(COLOR_ATTR[type])
    if attrs:
        applied.extend(_apply_attrs(shader, attrs))
    assigned = _assign(sg, list(assign_to)) if assign_to else []
    return {"material": shader, "shading_group": sg, "type": type, "attrs_set": applied, "assigned": assigned}


@command("materials.assign", mutates=True)
def assign(material: str, nodes: Sequence[str] | None = None) -> Dict[str, Any]:
    """Assign a material to objects, shapes or face components (pCube1.f[0:5]). Uses the selection when nodes is empty."""
    require_maya()
    _util.require_nodes([material])
    targets = list(nodes) if nodes else (cmds.ls(selection=True, long=True) or [])
    if not targets:
        raise BridgeError("no nodes given and nothing is selected")
    sg = _shading_group_for(material, create=True)
    assigned = _assign(sg, targets)
    return {"material": material, "shading_group": sg, "assigned": assigned}


@command("materials.list")
def list_materials(with_assignments: bool = True, include_defaults: bool = False) -> Dict[str, Any]:
    """List scene materials with their type, shading groups and assigned objects."""
    require_maya()
    shaders = cmds.ls(materials=True) or []
    items: List[Dict[str, Any]] = []
    for shader in sorted(set(shaders)):
        if not include_defaults and shader in DEFAULT_MATERIALS:
            continue
        entry: Dict[str, Any] = {"name": shader, "type": cmds.nodeType(shader), "shading_groups": _shading_groups(shader)}
        if with_assignments:
            members: List[str] = []
            for sg in entry["shading_groups"]:
                members.extend(cmds.sets(sg, query=True) or [])
            entry["assigned"] = members
        items.append(entry)
    return {"count": len(items), "materials": items}


@command("materials.get")
def get(material: str, max_attrs: int = 150) -> Dict[str, Any]:
    """Attributes, incoming texture connections and assignments for one material."""
    require_maya()
    _util.require_nodes([material])
    attrs: Dict[str, Any] = {}
    names = cmds.listAttr(material, settable=True, hasData=True, visible=True) or []
    for attr in names:
        if "." in attr or attr.startswith("ai") and attr.endswith("Message"):
            continue
        if len(attrs) >= int(max_attrs):
            break
        try:
            attrs[attr] = _jsonable_attr("%s.%s" % (material, attr))
        except Exception:
            continue
    inputs: List[Dict[str, Any]] = []
    pairs = cmds.listConnections(material, source=True, destination=False, connections=True, plugs=True) or []
    for i in range(0, len(pairs) - 1, 2):
        dest, src = pairs[i], pairs[i + 1]
        src_node = src.split(".")[0]
        info: Dict[str, Any] = {"attribute": dest.split(".", 1)[1], "source": src, "source_type": cmds.nodeType(src_node)}
        if info["source_type"] == "file":
            info["path"] = cmds.getAttr(src_node + ".fileTextureName")
        inputs.append(info)
    textures = []
    for f in _file_nodes_upstream(material):
        path = cmds.getAttr(f + ".fileTextureName") or ""
        textures.append({"node": f, "path": path, "exists_on_disk": _texture_exists(path), "color_space": _safe_get(f + ".colorSpace")})
    groups = _shading_groups(material)
    assigned: List[str] = []
    for sg in groups:
        assigned.extend(cmds.sets(sg, query=True) or [])
    return {"material": material, "type": cmds.nodeType(material), "attrs": attrs, "inputs": inputs, "textures": textures, "shading_groups": groups, "assigned": assigned}


@command("materials.set_attrs", mutates=True)
def set_attrs(material: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Set several attributes at once. Colors as [r, g, b], bools, floats and strings are all handled."""
    require_maya()
    _util.require_nodes([material])
    if not isinstance(attrs, dict) or not attrs:
        raise BridgeError("attrs must be a non empty dict like {'baseColor': [1, 0, 0], 'specularRoughness': 0.4}")
    applied = _apply_attrs(material, attrs)
    return {"material": material, "attrs_set": applied}


@command("materials.set_texture", mutates=True)
def set_texture(material: str, attribute: str, path: str, color_space: str | None = None, is_normal: bool = False, uv_tiling: Any = None, name: str | None = None) -> Dict[str, Any]:
    """Wire a file texture into a material attribute.

    Colour maps get sRGB, data maps (roughness, metalness, normal ...) get Raw unless
    ``color_space`` says otherwise. ``is_normal`` inserts a tangent space bump2d.
    """
    require_maya()
    _util.require_nodes([material])
    if not path:
        raise BridgeError("path is required")
    attr = _resolve_attr(material, "normalCamera" if is_normal and attribute in ("normal", "bump") else attribute)
    if attr == "normalCamera":
        is_normal = True
    kind = "normal" if is_normal else _attr_kind(material, attr)
    if color_space is None:
        color_space = "sRGB" if kind == "color" and attr not in DATA_ATTRS else "Raw"
    nodes = _util.create_file_texture(path, color_space=color_space, name=name, uv_tiling=uv_tiling)
    file_node, p2d = nodes["file"], nodes["place2d"]
    bump_node = None
    if kind == "normal":
        cmds.setAttr(file_node + ".alphaIsLuminance", 0)
        bump_node = cmds.shadingNode("bump2d", asUtility=True, name=file_node.replace("_file", "") + "_bump2d")
        cmds.setAttr(bump_node + ".bumpInterp", 1)  # tangent space normals
        cmds.setAttr(bump_node + ".aiFlipR", 0)
        cmds.setAttr(bump_node + ".aiFlipG", 0)
        cmds.connectAttr(file_node + ".outAlpha", bump_node + ".bumpValue", force=True)
        cmds.connectAttr(bump_node + ".outNormal", "%s.%s" % (material, attr), force=True)
    elif kind == "scalar":
        cmds.setAttr(file_node + ".alphaIsLuminance", 1)
        cmds.connectAttr(file_node + ".outAlpha", "%s.%s" % (material, attr), force=True)
    else:
        cmds.connectAttr(file_node + ".outColor", "%s.%s" % (material, attr), force=True)
    return {
        "material": material, "attribute": attr, "kind": kind, "file_node": file_node, "place2d": p2d,
        "bump_node": bump_node, "path": path, "color_space": color_space, "exists_on_disk": _texture_exists(path),
    }


@command("materials.create_pbr_network", mutates=True)
def create_pbr_network(
    name: str = "pbrMaterial",
    base_color: str | None = None,
    roughness: str | None = None,
    metalness: str | None = None,
    normal: str | None = None,
    displacement: str | None = None,
    ao: str | None = None,
    opacity: str | None = None,
    emission: str | None = None,
    shader_type: str = "standardSurface",
    assign_to: Sequence[str] | None = None,
    uv_tiling: Any = None,
    displacement_scale: float = 1.0,
) -> Dict[str, Any]:
    """Build a full PBR network from texture paths (base colour, roughness, metalness, normal, displacement, AO, opacity, emission)."""
    require_maya()
    if shader_type not in ("standardSurface", "aiStandardSurface"):
        raise BridgeError("shader_type must be standardSurface or aiStandardSurface")
    created = create(type=shader_type, name=name, assign_to=assign_to)
    shader, sg = created["material"], created["shading_group"]
    maps: Dict[str, Dict[str, Any]] = {}
    extra_nodes: List[str] = []

    def _wire(key: str, attr: str, path: str | None, **kwargs: Any) -> Dict[str, Any] | None:
        if not path:
            return None
        info = set_texture(shader, attr, path, uv_tiling=uv_tiling, name="%s_%s" % (name, key), **kwargs)
        maps[key] = info
        return info

    base = _wire("baseColor", "baseColor", base_color)
    if ao:
        ao_nodes = _util.create_file_texture(ao, color_space="Raw", name="%s_ao" % name, uv_tiling=uv_tiling)
        extra_nodes.append(ao_nodes["file"])
        if base:
            mult = cmds.shadingNode("multiplyDivide", asUtility=True, name=name + "_aoMultiply")
            extra_nodes.append(mult)
            cmds.connectAttr(base["file_node"] + ".outColor", mult + ".input1", force=True)
            cmds.connectAttr(ao_nodes["file"] + ".outColor", mult + ".input2", force=True)
            cmds.connectAttr(mult + ".output", shader + ".baseColor", force=True)
        else:
            cmds.connectAttr(ao_nodes["file"] + ".outColor", shader + ".baseColor", force=True)
        maps["ao"] = {"file_node": ao_nodes["file"], "place2d": ao_nodes["place2d"], "path": ao, "exists_on_disk": _texture_exists(ao)}
    _wire("roughness", "specularRoughness", roughness)
    _wire("metalness", "metalness", metalness)
    _wire("normal", "normalCamera", normal, is_normal=True)
    if opacity:
        _wire("opacity", "opacity", opacity, color_space="Raw")
    if emission:
        _wire("emission", "emissionColor", emission)
        cmds.setAttr(shader + ".emission", 1.0)
    if displacement:
        disp_nodes = _util.create_file_texture(displacement, color_space="Raw", name="%s_displacement" % name, uv_tiling=uv_tiling)
        cmds.setAttr(disp_nodes["file"] + ".alphaIsLuminance", 1)
        disp_shader = cmds.shadingNode("displacementShader", asShader=True, name=name + "_displacementShader")
        cmds.connectAttr(disp_nodes["file"] + ".outAlpha", disp_shader + ".displacement", force=True)
        cmds.connectAttr(disp_shader + ".displacement", sg + ".displacementShader", force=True)
        try:
            cmds.setAttr(disp_shader + ".scale", float(displacement_scale))
        except Exception:
            pass
        extra_nodes.extend([disp_nodes["file"], disp_shader])
        maps["displacement"] = {"file_node": disp_nodes["file"], "displacement_shader": disp_shader, "path": displacement, "exists_on_disk": _texture_exists(displacement)}
    missing = [k for k, v in maps.items() if v.get("exists_on_disk") is False]
    return {"material": shader, "shading_group": sg, "type": shader_type, "maps": maps, "extra_nodes": extra_nodes, "assigned": created["assigned"], "missing_on_disk": missing}


@command("materials.remove_unused", mutates=True)
def remove_unused() -> Dict[str, Any]:
    """Delete shading nodes that are not assigned to anything (Hypershade > Delete Unused Nodes)."""
    require_maya()
    before = set(cmds.ls(materials=True) or [])
    before_all = set(cmds.ls(type=["shadingEngine", "file", "place2dTexture", "bump2d"]) or [])
    if mel is None:
        raise BridgeError("MEL is unavailable outside Maya")
    mel.eval("MLdeleteUnused")
    after = set(cmds.ls(materials=True) or [])
    after_all = set(cmds.ls(type=["shadingEngine", "file", "place2dTexture", "bump2d"]) or [])
    removed = sorted((before - after) | (before_all - after_all))
    return {"removed_count": len(removed), "removed": removed}


@command("materials.list_textures")
def list_textures(missing_only: bool = False) -> Dict[str, Any]:
    """Every file texture node with its path, colour space, whether the file exists and what it feeds."""
    require_maya()
    items: List[Dict[str, Any]] = []
    for node in cmds.ls(type="file") or []:
        path = cmds.getAttr(node + ".fileTextureName") or ""
        exists_on_disk = _texture_exists(path)
        if missing_only and (exists_on_disk or not path):
            continue
        outputs = cmds.listConnections(node, source=False, destination=True, plugs=True) or []
        items.append({
            "node": node, "path": path, "exists_on_disk": exists_on_disk, "color_space": _safe_get(node + ".colorSpace"),
            "feeds": [p for p in outputs if not p.split(".")[0].startswith("defaultTextureList") and not p.endswith(".defaultTextureList") and "place2d" not in p.lower()],
        })
    missing = [i["node"] for i in items if i["path"] and not i["exists_on_disk"]]
    return {"count": len(items), "missing_count": len(missing), "missing": missing, "textures": items}


@command("materials.repath_textures", mutates=True)
def repath_textures(search: str, replace: str, dry_run: bool = False, regex: bool = False) -> Dict[str, Any]:
    """Replace ``search`` with ``replace`` in every file texture path (plain substring, or regex when regex=True)."""
    require_maya()
    if not search:
        raise BridgeError("search must be a non empty string")
    changed: List[Dict[str, Any]] = []
    for node in cmds.ls(type="file") or []:
        old = cmds.getAttr(node + ".fileTextureName") or ""
        new = re.sub(search, replace, old) if regex else old.replace(search, replace)
        if new == old:
            continue
        if not dry_run:
            cmds.setAttr(node + ".fileTextureName", new, type="string")
        changed.append({"node": node, "old": old, "new": new, "exists_on_disk": _texture_exists(new)})
    return {"changed_count": len(changed), "dry_run": dry_run, "changed": changed}


@command("materials.convert", mutates=True)
def convert(material: str, to_type: str = "standardSurface", delete_old: bool = True) -> Dict[str, Any]:
    """Convert a lambert/blinn/phong into standardSurface or aiStandardSurface, mapping the common attributes and textures."""
    require_maya()
    _util.require_nodes([material])
    if to_type not in ("standardSurface", "aiStandardSurface"):
        raise BridgeError("to_type must be standardSurface or aiStandardSurface")
    old_type = cmds.nodeType(material)
    if old_type not in ("lambert", "blinn", "phong", "phongE", "standardSurface", "aiStandardSurface"):
        raise BridgeError("cannot convert %s of type %s; only lambert, blinn, phong and the two PBR surfaces are supported" % (material, old_type))
    if old_type in ("standardSurface", "aiStandardSurface"):
        mapping = {"baseColor": "baseColor", "specularRoughness": "specularRoughness", "metalness": "metalness", "specularColor": "specularColor", "emissionColor": "emissionColor", "emission": "emission", "opacity": "opacity", "normalCamera": "normalCamera", "specular": "specular", "coat": "coat", "transmission": "transmission", "subsurface": "subsurface"}
    else:
        mapping = {"color": "baseColor", "normalCamera": "normalCamera", "incandescence": "emissionColor", "specularColor": "specularColor"}
    if to_type in ARNOLD_TYPES:
        _util.ensure_plugin("mtoa", "aiStandardSurface needs Arnold (mtoa); convert to standardSurface instead.")
    new = cmds.shadingNode(to_type, asShader=True, name=material + "_" + to_type)
    mapped: Dict[str, Any] = {}
    for src, dst in mapping.items():
        plug = "%s.%s" % (material, src)
        upstream = _upstream_plug(plug)
        if upstream:
            cmds.connectAttr(upstream, "%s.%s" % (new, dst), force=True)
            mapped[dst] = upstream
            continue
        try:
            value = _jsonable_attr(plug)
        except Exception:
            continue
        if value is None or dst == "normalCamera":
            continue
        _util.set_attr_value(new, dst, value)
        mapped[dst] = value
    if old_type in ("lambert", "blinn", "phong", "phongE"):
        # Legacy transparency is the inverse of opacity.
        try:
            t = _jsonable_attr(material + ".transparency")
            if isinstance(t, list) and len(t) == 3 and any(v > 0 for v in t):
                _util.set_attr_value(new, "opacity", [1.0 - float(v) for v in t])
                mapped["opacity"] = [1.0 - float(v) for v in t]
        except Exception:
            pass
        inc = mapped.get("emissionColor")
        if isinstance(inc, list) and any(v > 0 for v in inc):
            _util.set_attr_value(new, "emission", 1.0)
        rough = None
        if old_type == "blinn":
            ecc = _scalar(material + ".eccentricity", 0.3)
            rough = max(0.0, min(1.0, ecc ** 0.5))
            _util.set_attr_value(new, "specular", max(0.0, min(1.0, _scalar(material + ".specularRollOff", 0.7))))
        elif old_type in ("phong", "phongE"):
            power = _scalar(material + ".cosinePower", 20.0) if old_type == "phong" else 1.0 / max(_scalar(material + ".roughness", 0.5), 1e-3)
            rough = max(0.0, min(1.0, (2.0 / (power + 2.0)) ** 0.5))
        else:
            rough = 0.6
            _util.set_attr_value(new, "specular", 0.2)
        _util.set_attr_value(new, "specularRoughness", rough)
        mapped["specularRoughness"] = rough
    groups = _shading_groups(material)
    for sg in groups:
        cmds.connectAttr(new + ".outColor", sg + ".surfaceShader", force=True)
    if not groups:
        groups = [_shading_group_for(new, create=True)]
    if delete_old:
        cmds.delete(material)
    return {"material": new, "type": to_type, "from": material, "from_type": old_type, "shading_groups": groups, "mapped": mapped, "deleted_old": delete_old}


# misc ---------------------------------------------------------------------
def _scalar(plug: str, default: float) -> float:
    try:
        value = cmds.getAttr(plug)
        return float(value) if isinstance(value, (int, float)) else default
    except Exception:
        return default


def _safe_get(plug: str) -> Any:
    try:
        return _jsonable_attr(plug)
    except Exception:
        return None


def _texture_exists(path: str) -> bool:
    if not path or not isinstance(path, str):
        return False
    expanded = os.path.expandvars(os.path.expanduser(path))
    if "<udim>" in expanded.lower():
        pattern = re.sub("(?i)<udim>", "[0-9][0-9][0-9][0-9]", expanded)
        return bool(glob.glob(pattern))
    if "<f>" in expanded.lower():
        pattern = re.sub("(?i)<f>", "*", expanded)
        return bool(glob.glob(pattern))
    return os.path.isfile(expanded)
