"""modeling.* commands: primitives, curves, poly ops, transforms, deformers, layout helpers."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError, node_summary, require_maya, require_nodes, resolve_targets

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

PRIMITIVES = ("cube", "sphere", "cylinder", "cone", "plane", "torus", "pipe", "disc", "prism", "pyramid", "helix", "platonic")
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


# helpers ---------------------------------------------------------------------
def _long(node: str) -> str:
    found = cmds.ls(node, long=True) or []
    return found[0] if found else node


def _longs(nodes: Sequence[str]) -> List[str]:
    return [_long(n) for n in (nodes or [])]


def _vec3(value: Any, label: str) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise BridgeError("%s must be a list of 3 numbers" % label)
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        raise BridgeError("%s must be a list of 3 numbers" % label) from None


def _mesh_shape(node: str) -> str:
    """The (non intermediate) mesh shape under a transform, or the node if it is a mesh."""
    shapes = _util.shapes_of(node, "mesh")
    if not shapes:
        raise BridgeError("%s has no polygon mesh shape; pick a poly object (see modeling.create_primitive)" % node)
    return shapes[0]


def _components(node: str, components: Sequence[str] | None) -> List[str]:
    """Turn ['f[0:3]', 'f[7]'] into full component plugs on ``node``. No components means the whole mesh."""
    if not components:
        return [node]
    out: List[str] = []
    for comp in components:
        comp = str(comp).strip()
        if "." in comp:
            out.append(comp)
        else:
            out.append("%s.%s" % (node, comp))
    return out


def _component_kind(components: Sequence[str] | None) -> str:
    if not components:
        return "face"
    first = str(components[0]).split(".")[-1]
    if first.startswith("e["):
        return "edge"
    if first.startswith("vtx["):
        return "vertex"
    return "face"


def _apply_placement(transform: str, translate: Any, rotate: Any, scale: Any) -> None:
    if translate is not None:
        cmds.xform(transform, worldSpace=True, translation=_vec3(translate, "translate"))
    if rotate is not None:
        cmds.xform(transform, worldSpace=True, rotation=_vec3(rotate, "rotate"))
    if scale is not None:
        cmds.xform(transform, scale=_vec3(scale, "scale"))


def _transform_values(transform: str) -> Dict[str, Any]:
    return {
        "translate": list(cmds.xform(transform, query=True, worldSpace=True, translation=True) or [0.0, 0.0, 0.0]),
        "rotate": list(cmds.xform(transform, query=True, worldSpace=True, rotation=True) or [0.0, 0.0, 0.0]),
        "scale": list(cmds.xform(transform, query=True, relative=True, scale=True) or [1.0, 1.0, 1.0]),
    }


def _created(result: Any, translate: Any = None, rotate: Any = None, scale: Any = None) -> Dict[str, Any]:
    """Standard reply for creators: long names of transform, shape and history nodes plus a summary."""
    nodes = list(result) if isinstance(result, (list, tuple)) else [result]
    if not nodes or not nodes[0]:
        raise BridgeError("Maya did not return a node")
    transform = _long(nodes[0])
    _apply_placement(transform, translate, rotate, scale)
    shapes = _util.shapes_of(transform)
    return {
        "transform": transform,
        "shape": shapes[0] if shapes else None,
        "history": [n for n in nodes[1:] if n],
        "node_summary": node_summary(transform),
    }


def _mesh_result(node: str, history: Any, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    transform = _long(_util.transform_of(node))
    out: Dict[str, Any] = {"node": transform, "history": [h for h in (history or []) if h], "node_summary": node_summary(transform)}
    if extra:
        out.update(extra)
    return out


# creators --------------------------------------------------------------------
@command("modeling.create_primitive", mutates=True)
def create_primitive(
    kind: str = "cube",
    name: str | None = None,
    size: float | None = None,
    radius: float | None = None,
    height: float | None = None,
    width: float | None = None,
    depth: float | None = None,
    subdivisions: int | None = None,
    translate: List[float] | None = None,
    rotate: List[float] | None = None,
    scale: List[float] | None = None,
) -> Dict[str, Any]:
    """Create a polygon primitive. ``size`` is a fallback for width/height/depth/diameter."""
    require_maya()
    kind = (kind or "cube").lower()
    if kind not in PRIMITIVES:
        raise BridgeError("kind must be one of %s" % ", ".join(PRIMITIVES))
    s = float(size) if size is not None else 1.0
    r = float(radius) if radius is not None else (s / 2.0 if size is not None else 1.0)
    h = float(height) if height is not None else (s if size is not None else 2.0)
    w = float(width) if width is not None else s
    d = float(depth) if depth is not None else s
    sub = int(subdivisions) if subdivisions is not None else None
    kw: Dict[str, Any] = {"constructionHistory": True}
    if name:
        kw["name"] = name

    if kind == "cube":
        hh = float(height) if height is not None else s
        result = cmds.polyCube(width=w, height=hh, depth=d, subdivisionsX=sub or 1, subdivisionsY=sub or 1, subdivisionsZ=sub or 1, **kw)
    elif kind == "sphere":
        result = cmds.polySphere(radius=r, subdivisionsX=sub or 20, subdivisionsY=sub or 20, **kw)
    elif kind == "cylinder":
        result = cmds.polyCylinder(radius=r, height=h, subdivisionsX=sub or 20, subdivisionsY=1, subdivisionsZ=1, **kw)
    elif kind == "cone":
        result = cmds.polyCone(radius=r, height=h, subdivisionsX=sub or 20, subdivisionsY=1, subdivisionsZ=0, **kw)
    elif kind == "plane":
        # polyPlane 'height' is the extent along Z; 'depth' is the friendlier name.
        result = cmds.polyPlane(width=w, height=float(depth) if depth is not None else (float(height) if height is not None else s), subdivisionsX=sub or 10, subdivisionsY=sub or 10, **kw)
    elif kind == "torus":
        section = float(width) if width is not None else r * 0.5
        result = cmds.polyTorus(radius=r, sectionRadius=section, subdivisionsX=sub or 20, subdivisionsY=sub or 20, **kw)
    elif kind == "pipe":
        thickness = float(width) if width is not None else r * 0.5
        result = cmds.polyPipe(radius=r, height=h, thickness=thickness, subdivisionsAxis=sub or 20, subdivisionsHeight=1, subdivisionsCaps=1, **kw)
    elif kind == "disc":
        # polyDisc: -sides is the polygon side count, -subdivisions the ring count, -radius the size.
        result = cmds.polyDisc(sides=sub or 8, subdivisions=1, radius=r, **kw)
    elif kind == "prism":
        # polyPrism: -length is the height along Y, -sideLength the edge length, -numberOfSides the side count.
        result = cmds.polyPrism(length=h, sideLength=w, numberOfSides=sub or 3, subdivisionsHeight=1, subdivisionsCaps=0, **kw)
    elif kind == "pyramid":
        result = cmds.polyPyramid(sideLength=w, numberOfSides=sub or 4, subdivisionsHeight=1, subdivisionsCaps=0, **kw)
    elif kind == "helix":
        # polyHelix: -width is the helix radius, -radius the tube radius, -coils the turn count.
        tube = float(width) if width is not None else r * 0.2
        result = cmds.polyHelix(coils=3, height=h, width=r, radius=tube, subdivisionsAxis=8, subdivisionsCoil=sub or 50, subdivisionsCaps=0, **kw)
    else:  # platonic
        # polyPlatonic -solidType: 0 dodecahedron, 1 icosahedron, 2 octahedron, 3 tetrahedron (Maya 2024 docs).
        result = cmds.polyPlatonic(solidType=sub if sub is not None else 1, radius=r, **kw)
    out = _created(result, translate, rotate, scale)
    out["kind"] = kind
    return out


@command("modeling.create_curve", mutates=True)
def create_curve(points: List[List[float]], degree: int = 3, closed: bool = False, name: str | None = None) -> Dict[str, Any]:
    """Create a NURBS curve through the given points (a list of [x, y, z])."""
    require_maya()
    if not points or not isinstance(points, (list, tuple)):
        raise BridgeError("points must be a list of [x, y, z] triples")
    pts = [_vec3(p, "point %d" % i) for i, p in enumerate(points)]
    degree = int(degree)
    if degree not in (1, 2, 3, 5, 7):
        raise BridgeError("degree must be 1, 2, 3, 5 or 7")
    if len(pts) < degree + 1:
        raise BridgeError("a degree %d curve needs at least %d points (got %d)" % (degree, degree + 1, len(pts)))
    kw: Dict[str, Any] = {"point": pts, "degree": degree}
    if name:
        kw["name"] = name
    crv = cmds.curve(**kw)
    if closed:
        # closeCurve with replaceOriginal keeps the same transform.
        cmds.closeCurve(crv, constructionHistory=False, preserveShape=1, replaceOriginal=True)
    out = _created(crv)
    out["points"] = len(pts)
    out["degree"] = degree
    out["closed"] = bool(closed)
    return out


@command("modeling.create_text", mutates=True)
def create_text(text: str, font: str = "Arial", name: str | None = None) -> Dict[str, Any]:
    """Create NURBS curves spelling ``text`` (Maya's textCurves)."""
    require_maya()
    if not text or not str(text).strip():
        raise BridgeError("text must not be empty")
    kw: Dict[str, Any] = {"text": str(text), "font": font or "Arial", "constructionHistory": False}
    if name:
        kw["name"] = name
    result = cmds.textCurves(**kw)
    out = _created(result)
    out["text"] = str(text)
    out["font"] = font
    return out


# transforms ------------------------------------------------------------------
@command("modeling.transform", mutates=True)
def transform(
    nodes: List[str] | None = None,
    translate: List[float] | None = None,
    rotate: List[float] | None = None,
    scale: List[float] | None = None,
    relative: bool = False,
    world: bool = True,
) -> Dict[str, Any]:
    """Move, rotate and/or scale nodes. Absolute by default; relative adds to the current values."""
    require_maya()
    targets = resolve_targets(nodes)
    if translate is None and rotate is None and scale is None:
        raise BridgeError("give at least one of translate, rotate, scale")
    space: Dict[str, Any] = {"worldSpace": True} if world else {"objectSpace": True}
    results: List[Dict[str, Any]] = []
    for node in targets:
        if translate is not None:
            cmds.xform(node, relative=bool(relative), translation=_vec3(translate, "translate"), **space)
        if rotate is not None:
            cmds.xform(node, relative=bool(relative), rotation=_vec3(rotate, "rotate"), **space)
        if scale is not None:
            cmds.xform(node, relative=bool(relative), scale=_vec3(scale, "scale"))
        entry = {"node": _long(node)}
        entry.update(_transform_values(node))
        results.append(entry)
    return {"nodes": results}


@command("modeling.duplicate", mutates=True)
def duplicate(
    nodes: List[str] | None = None,
    count: int = 1,
    offset_translate: List[float] | None = None,
    offset_rotate: List[float] | None = None,
    instance: bool = False,
    name: str | None = None,
) -> Dict[str, Any]:
    """Duplicate (or instance) nodes ``count`` times, offsetting each copy cumulatively."""
    require_maya()
    targets = resolve_targets(nodes)
    count = int(count)
    if count < 1 or count > 1000:
        raise BridgeError("count must be between 1 and 1000")
    t_off = _vec3(offset_translate, "offset_translate") if offset_translate is not None else None
    r_off = _vec3(offset_rotate, "offset_rotate") if offset_rotate is not None else None
    copies: List[Dict[str, Any]] = []
    for src in targets:
        previous = src
        for i in range(count):
            kw: Dict[str, Any] = {}
            if name:
                kw["name"] = name if (count == 1 and len(targets) == 1) else "%s%d" % (name, i + 1)
            if instance:
                made = cmds.instance(previous, **kw)
            else:
                made = cmds.duplicate(previous, returnRootsOnly=True, **kw)
            new = _long(made[0] if isinstance(made, (list, tuple)) else made)
            if t_off:
                cmds.xform(new, relative=True, worldSpace=True, translation=t_off)
            if r_off:
                cmds.xform(new, relative=True, worldSpace=True, rotation=r_off)
            entry = {"node": new, "source": _long(src), "instance": bool(instance)}
            entry.update(_transform_values(new))
            copies.append(entry)
            previous = new
    return {"copies": copies, "count": len(copies)}


# poly editing ----------------------------------------------------------------
@command("modeling.extrude", mutates=True)
def extrude(
    node: str,
    components: List[str] | None = None,
    distance: float = 1.0,
    thickness: float | None = None,
    divisions: int = 1,
    keep_faces_together: bool = True,
) -> Dict[str, Any]:
    """Extrude faces (default), edges ('e[..]') or vertices ('vtx[..]') of a mesh along their normals."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    targets = _components(node, components)
    kind = _component_kind(components)
    kw: Dict[str, Any] = {"constructionHistory": True, "keepFacesTogether": bool(keep_faces_together), "divisions": max(1, int(divisions)), "localTranslateZ": float(distance)}
    if thickness is not None:
        kw["thickness"] = float(thickness)
    if kind == "edge":
        kw.pop("thickness", None)  # polyExtrudeEdge has no thickness flag
        history = cmds.polyExtrudeEdge(*targets, **kw)
    elif kind == "vertex":
        history = cmds.polyExtrudeVertex(*targets, length=float(distance), divisions=max(1, int(divisions)), constructionHistory=True)
    else:
        history = cmds.polyExtrudeFacet(*targets, **kw)
    return _mesh_result(node, history, {"components": targets, "kind": kind})


@command("modeling.bevel", mutates=True)
def bevel(
    node: str,
    components: List[str] | None = None,
    edges: List[str] | None = None,
    fraction: float = 0.5,
    segments: int = 1,
    chamfer: bool = True,
) -> Dict[str, Any]:
    """Bevel edges (or whole mesh). fraction is 0..1 of the edge length (offsetAsFraction)."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    comps = components or edges
    targets = _components(node, comps)
    fraction = float(fraction)
    if not 0.0 < fraction <= 1.0:
        raise BridgeError("fraction must be in (0, 1]")
    history = cmds.polyBevel3(
        *targets,
        fraction=fraction,
        offsetAsFraction=1,
        segments=max(1, int(segments)),
        chamfer=bool(chamfer),
        autoFit=1,
        depth=1,
        mitering=0,
        miterAlong=0,
        smoothingAngle=30,
        constructionHistory=True,
    )
    return _mesh_result(node, history, {"components": targets})


@command("modeling.boolean", mutates=True)
def boolean(a: str, b: str, operation: str = "union", name: str | None = None) -> Dict[str, Any]:
    """Boolean two meshes: union, difference (a minus b) or intersection."""
    require_maya()
    require_nodes([a, b])
    ops = {"union": 1, "difference": 2, "intersection": 3}
    op = ops.get((operation or "union").lower())
    if op is None:
        raise BridgeError("operation must be union, difference or intersection")
    kw: Dict[str, Any] = {"operation": op, "constructionHistory": True}
    if name:
        kw["name"] = name
    result = cmds.polyBoolOp(a, b, **kw)
    out = _created(result)
    out["operation"] = operation.lower()
    return out


@command("modeling.combine", mutates=True)
def combine(nodes: List[str] | None = None, name: str | None = None) -> Dict[str, Any]:
    """Combine several meshes into one (polyUnite)."""
    require_maya()
    targets = resolve_targets(nodes)
    if len(targets) < 2:
        raise BridgeError("combine needs at least two meshes")
    kw: Dict[str, Any] = {"constructionHistory": True, "mergeUVSets": 1}
    if name:
        kw["name"] = name
    result = cmds.polyUnite(*targets, **kw)
    out = _created(result)
    out["sources"] = _longs(targets)
    return out


@command("modeling.separate", mutates=True)
def separate(node: str) -> Dict[str, Any]:
    """Split a mesh into its disconnected shells."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    result = cmds.polySeparate(node, constructionHistory=True) or []
    pieces = [n for n in result if cmds.objExists(n) and cmds.objectType(n, isType="transform")]
    history = [n for n in result if n not in pieces]
    return {"pieces": _longs(pieces), "history": history, "count": len(pieces), "summaries": [node_summary(_long(p)) for p in pieces]}


@command("modeling.mirror", mutates=True)
def mirror(node: str, axis: str = "x", direction: str = "+", merge: bool = True) -> Dict[str, Any]:
    """Mirror a mesh across an axis of its own pivot (polyMirrorFace)."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    axis = (axis or "x").lower()
    if axis not in _AXIS_INDEX:
        raise BridgeError("axis must be x, y or z")
    positive = str(direction).strip() in ("+", "positive", "pos", "1")
    # polyMirrorFace: axis 0/1/2 = x/y/z, axisDirection 1 = positive, 0 = negative,
    # mergeMode 1 = merge border vertices, 0 = no merge, mirrorAxis 1 = object pivot.
    history = cmds.polyMirrorFace(
        node,
        axis=_AXIS_INDEX[axis],
        axisDirection=1 if positive else 0,
        mergeMode=1 if merge else 0,
        mirrorAxis=1,
        mergeThreshold=0.001,
        constructionHistory=True,
    )
    return _mesh_result(node, history, {"axis": axis, "direction": "+" if positive else "-"})


@command("modeling.smooth", mutates=True)
def smooth(node: str, divisions: int = 1) -> Dict[str, Any]:
    """Subdivide a mesh (polySmooth, Catmull-Clark style)."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    divisions = int(divisions)
    if divisions < 1 or divisions > 4:
        raise BridgeError("divisions must be between 1 and 4 (each level quadruples the face count)")
    history = cmds.polySmooth(node, divisions=divisions, method=0, keepBorder=1, constructionHistory=True)
    return _mesh_result(node, history, {"divisions": divisions})


@command("modeling.reduce", mutates=True)
def reduce(node: str, percentage: float = 50.0, keep_quads: bool = False) -> Dict[str, Any]:
    """Reduce polygon count by a percentage (polyReduce version 1 algorithm)."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    percentage = float(percentage)
    if not 0.0 < percentage < 100.0:
        raise BridgeError("percentage must be between 0 and 100 (exclusive)")
    history = cmds.polyReduce(
        node,
        version=1,
        termination=0,
        percentage=percentage,
        keepQuadsWeight=1.0 if keep_quads else 0.0,
        keepBorder=1,
        keepMapBorder=1,
        keepHardEdge=1,
        preserveTopology=1,
        replaceOriginal=1,
        cachingReduce=1,
        constructionHistory=True,
    )
    return _mesh_result(node, history, {"percentage": percentage})


@command("modeling.freeze_transforms", mutates=True)
def freeze_transforms(nodes: List[str] | None = None, translate: bool = True, rotate: bool = True, scale: bool = True, normal: bool = False) -> Dict[str, Any]:
    """Bake transforms into the geometry (makeIdentity apply)."""
    require_maya()
    targets = resolve_targets(nodes)
    cmds.makeIdentity(targets, apply=True, translate=bool(translate), rotate=bool(rotate), scale=bool(scale), normal=1 if normal else 0, preserveNormals=True)
    return {"nodes": [{"node": _long(n), **_transform_values(n)} for n in targets]}


@command("modeling.center_pivot", mutates=True)
def center_pivot(nodes: List[str] | None = None) -> Dict[str, Any]:
    require_maya()
    targets = resolve_targets(nodes)
    for n in targets:
        cmds.xform(n, centerPivots=True)
    return {"nodes": [{"node": _long(n), "pivot": list(cmds.xform(n, query=True, worldSpace=True, rotatePivot=True) or [0.0, 0.0, 0.0])} for n in targets]}


@command("modeling.delete_history", mutates=True)
def delete_history(nodes: List[str] | None = None) -> Dict[str, Any]:
    require_maya()
    targets = resolve_targets(nodes)
    cmds.delete(targets, constructionHistory=True)
    return {"nodes": _longs(targets)}


@command("modeling.mesh_stats")
def mesh_stats(node: str) -> Dict[str, Any]:
    """Counts and bounding box for a mesh (polyEvaluate)."""
    require_maya()
    require_nodes([node])
    shape = _mesh_shape(node)

    def count(**flag: Any) -> Any:
        value = cmds.polyEvaluate(shape, **flag)
        return value if isinstance(value, (int, float)) else 0

    bbox = cmds.polyEvaluate(shape, boundingBox=True) or [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    try:
        mins = [float(bbox[0][0]), float(bbox[1][0]), float(bbox[2][0])]
        maxs = [float(bbox[0][1]), float(bbox[1][1]), float(bbox[2][1])]
    except (IndexError, TypeError, ValueError):
        mins, maxs = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return {
        "node": _long(_util.transform_of(node)),
        "shape": shape,
        "vertices": count(vertex=True),
        "edges": count(edge=True),
        "faces": count(face=True),
        "triangles": count(triangle=True),
        "uv_coords": count(uvcoord=True),
        "shells": count(shell=True),
        "bounding_box": {"min": mins, "max": maxs, "size": [maxs[i] - mins[i] for i in range(3)]},
    }


@command("modeling.uv_auto", mutates=True)
def uv_auto(node: str, method: str = "automatic", axis: str = "y") -> Dict[str, Any]:
    """Generate UVs: automatic (6 plane), planar (along axis), cylindrical or spherical."""
    require_maya()
    require_nodes([node])
    _mesh_shape(node)
    method = (method or "automatic").lower()
    faces = "%s.f[*]" % node
    if method == "automatic":
        history = cmds.polyAutoProjection(faces, layoutMethod=0, planes=6, insertBeforeDeformers=1, scaleMode=1, optimize=1, percentageSpace=0.2, layout=2, worldSpace=0, constructionHistory=True)
    elif method == "planar":
        axis = (axis or "y").lower()
        if axis not in _AXIS_INDEX:
            raise BridgeError("axis must be x, y or z")
        history = cmds.polyProjection(faces, type="Planar", mapDirection=axis, insertBeforeDeformers=True, constructionHistory=True)
    elif method == "cylindrical":
        history = cmds.polyProjection(faces, type="Cylindrical", insertBeforeDeformers=True, constructionHistory=True)
    elif method == "spherical":
        history = cmds.polyProjection(faces, type="Spherical", insertBeforeDeformers=True, constructionHistory=True)
    else:
        raise BridgeError("method must be automatic, planar, cylindrical or spherical")
    return _mesh_result(node, history, {"method": method})


@command("modeling.lattice", mutates=True)
def lattice(nodes: List[str] | None = None, divisions: List[int] | None = None, name: str | None = None) -> Dict[str, Any]:
    """Add an FFD lattice deformer around nodes. divisions is [s, t, u] (default 2x2x2 fitted to the objects)."""
    require_maya()
    targets = resolve_targets(nodes)
    div = [int(v) for v in (divisions or [2, 2, 2])]
    if len(div) != 3 or min(div) < 2:
        raise BridgeError("divisions must be three integers >= 2")
    kw: Dict[str, Any] = {"divisions": tuple(div), "objectCentered": True, "outsideLattice": 0, "ldivisions": (2, 2, 2)}
    if name:
        kw["name"] = name
    result = cmds.lattice(targets, **kw) or []
    names = list(result) + [None, None, None]
    return {"ffd": names[0], "lattice": _long(names[1]) if names[1] else None, "base": _long(names[2]) if names[2] else None, "nodes": _longs(targets), "divisions": div}


@command("modeling.nurbs_to_poly", mutates=True)
def nurbs_to_poly(node: str, quads: bool = True, fit: str = "general", spans_u: int = 4, spans_v: int = 4, name: str | None = None) -> Dict[str, Any]:
    """Convert a NURBS surface to polygons. fit: general (per span count), count, standard, control."""
    require_maya()
    require_nodes([node])
    if not _util.shapes_of(node, "nurbsSurface"):
        raise BridgeError("%s is not a NURBS surface (revolve/loft produce one)" % node)
    formats = {"count": 0, "standard": 1, "general": 2, "control": 3}
    fmt = formats.get((fit or "general").lower())
    if fmt is None:
        raise BridgeError("fit must be general, count, standard or control")
    # nurbsToPoly: -format 2 (general) with uType/vType 3 = per span number, -polygonType 1 = quads.
    kw: Dict[str, Any] = {
        "format": fmt,
        "polygonType": 1 if quads else 0,
        "uType": 3,
        "uNumber": max(1, int(spans_u)),
        "vType": 3,
        "vNumber": max(1, int(spans_v)),
        "matchNormalDir": 1,
        "constructionHistory": True,
    }
    if name:
        kw["name"] = name
    result = cmds.nurbsToPoly(node, **kw)
    out = _created(result)
    out["source"] = _long(node)
    return out


@command("modeling.revolve", mutates=True)
def revolve(curve: str, axis: str = "y", degrees: float = 360.0, sections: int = 8, output_poly: bool = False, name: str | None = None) -> Dict[str, Any]:
    """Revolve a profile curve around an axis (lathe)."""
    require_maya()
    require_nodes([curve])
    axis = (axis or "y").lower()
    if axis not in _AXIS_INDEX:
        raise BridgeError("axis must be x, y or z")
    vec = [0.0, 0.0, 0.0]
    vec[_AXIS_INDEX[axis]] = 1.0
    kw: Dict[str, Any] = {
        "axis": vec,
        "startSweep": 0,
        "endSweep": float(degrees),
        "sections": max(1, int(sections)),
        "degree": 3,
        "useTolerance": 0,
        "polygon": 1 if output_poly else 0,
        "constructionHistory": True,
    }
    if name:
        kw["name"] = name
    result = cmds.revolve(curve, **kw)
    out = _created(result)
    out["source"] = _long(curve)
    return out


@command("modeling.loft", mutates=True)
def loft(curves: List[str], close: bool = False, uniform: bool = True, output_poly: bool = False, name: str | None = None) -> Dict[str, Any]:
    """Loft a surface through two or more curves."""
    require_maya()
    if not curves or len(curves) < 2:
        raise BridgeError("loft needs at least two curves")
    require_nodes(curves)
    kw: Dict[str, Any] = {
        "uniform": bool(uniform),
        "close": bool(close),
        "autoReverse": True,
        "degree": 3,
        "sectionSpans": 1,
        "range": False,
        "polygon": 1 if output_poly else 0,
        "reverseSurfaceNormals": True,
        "constructionHistory": True,
    }
    if name:
        kw["name"] = name
    result = cmds.loft(*curves, **kw)
    out = _created(result)
    out["sources"] = _longs(curves)
    return out


@command("modeling.cleanup", mutates=True)
def cleanup(
    nodes: List[str] | None = None,
    nonmanifold: bool = True,
    lamina: bool = True,
    zero_area: bool = True,
    zero_length: bool = False,
    select_only: bool = False,
) -> Dict[str, Any]:
    """Run Mesh > Cleanup for non manifold geometry, lamina faces and zero area faces/edges.

    select_only=True only selects the offending components instead of fixing them.
    """
    require_maya()
    if mel is None:
        raise BridgeError("MEL is unavailable outside Maya")
    targets = resolve_targets(nodes)
    for n in targets:
        _mesh_shape(n)
    cmds.select(targets, replace=True)
    # polyCleanupArgList version 4 (Maya 2019+): allMeshes, selectOnly, historyOn, quads, nsided,
    # concave, holed, nonplanar, zeroGeom, zeroGeomTol, zeroEdge, zeroEdgeTol, zeroMap, zeroMapTol,
    # sharedUVs, nonmanifold (0 off, 1 normals+geometry, 2 geometry), lamina, invalidComponents.
    args = [
        "0",
        "1" if select_only else "0",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "1" if zero_area else "0",
        "1e-05",
        "1" if zero_length else "0",
        "1e-05",
        "0",
        "1e-05",
        "0",
        "1" if nonmanifold else "0",
        "1" if lamina else "0",
        "0",
    ]
    script = 'polyCleanupArgList 4 { %s }' % ", ".join('"%s"' % a for a in args)
    mel.eval(script)
    remaining = cmds.ls(selection=True, long=True) or []
    problems = [s for s in remaining if "." in s]
    cmds.select(targets, replace=True)
    return {"nodes": _longs(targets), "select_only": bool(select_only), "problem_components": problems, "problem_count": len(problems), "mel": script}


@command("modeling.set_smooth_preview", mutates=True)
def set_smooth_preview(nodes: List[str] | None = None, level: int = 1) -> Dict[str, Any]:
    """Viewport smooth preview (the 1/2/3 hotkeys). level 0 turns it off, 1..3 sets the subdivision level."""
    require_maya()
    targets = resolve_targets(nodes)
    level = int(level)
    if level < 0 or level > 3:
        raise BridgeError("level must be 0, 1, 2 or 3")
    shapes: List[str] = []
    for n in targets:
        for shape in _util.shapes_of(n, "mesh"):
            # displaySmoothMesh: 0 off, 1 cage + smooth, 2 smooth only; smoothLevel = subdivision count.
            cmds.setAttr(shape + ".displaySmoothMesh", 2 if level > 0 else 0)
            if level > 0:
                cmds.setAttr(shape + ".smoothLevel", level)
            shapes.append(shape)
    if not shapes:
        raise BridgeError("none of the nodes has a mesh shape")
    return {"shapes": shapes, "level": level}


@command("modeling.array", mutates=True)
def array(
    node: str,
    count: int = 5,
    spacing: float | None = None,
    axis: str = "x",
    radius: float | None = None,
    instance: bool = False,
    name: str | None = None,
) -> Dict[str, Any]:
    """Lay out copies of a node: linear along an axis with ``spacing``, or circular when ``radius`` is given.

    The circle lies in the plane perpendicular to ``axis`` (axis=y gives a ring on the ground).
    """
    require_maya()
    require_nodes([node])
    count = int(count)
    if count < 2 or count > 1000:
        raise BridgeError("count must be between 2 and 1000")
    axis = (axis or "x").lower()
    if axis not in _AXIS_INDEX:
        raise BridgeError("axis must be x, y or z")
    origin = list(cmds.xform(node, query=True, worldSpace=True, translation=True) or [0.0, 0.0, 0.0])
    base_rot = list(cmds.xform(node, query=True, worldSpace=True, rotation=True) or [0.0, 0.0, 0.0])
    ai = _AXIS_INDEX[axis]
    if radius is not None:
        radius = float(radius)
        if radius <= 0:
            raise BridgeError("radius must be positive")
        # Two axes spanning the plane perpendicular to the array axis.
        u, v = [i for i in range(3) if i != ai]
        positions = []
        for i in range(count):
            angle = 2.0 * math.pi * i / count
            pos = list(origin)
            pos[u] = origin[u] + math.cos(angle) * radius
            pos[v] = origin[v] + math.sin(angle) * radius
            rot = list(base_rot)
            rot[ai] = base_rot[ai] + (-math.degrees(angle) if ai == 1 else math.degrees(angle))
            positions.append((pos, rot))
        layout = "circular"
    else:
        if spacing is None:
            bbox = cmds.exactWorldBoundingBox(node) or [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
            spacing = float(bbox[ai + 3] - bbox[ai]) * 1.5 or 1.0
        spacing = float(spacing)
        positions = []
        for i in range(count):
            pos = list(origin)
            pos[ai] = origin[ai] + spacing * i
            positions.append((pos, list(base_rot)))
        layout = "linear"
    items: List[Dict[str, Any]] = []
    for i, (pos, rot) in enumerate(positions):
        if i == 0:
            target = node
        else:
            kw: Dict[str, Any] = {}
            if name:
                kw["name"] = "%s%d" % (name, i)
            made = cmds.instance(node, **kw) if instance else cmds.duplicate(node, returnRootsOnly=True, **kw)
            target = made[0] if isinstance(made, (list, tuple)) else made
        cmds.xform(target, worldSpace=True, translation=pos)
        cmds.xform(target, worldSpace=True, rotation=rot)
        items.append({"node": _long(target), "translate": pos, "rotate": rot})
    return {"layout": layout, "axis": axis, "count": count, "spacing": spacing, "radius": radius, "nodes": items}
