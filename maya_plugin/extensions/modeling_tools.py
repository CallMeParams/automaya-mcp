"""Drop-in modeling tools (AutoMaya extension format, also PatrickPalmer/MayaMCP compatible).

Every public function here becomes a bridge command ``ext.modeling_tools.<name>``
and an MCP tool ``maya_ext_modeling_tools_<name>``. Rules for this format:

* Maya imports happen inside the function, never at module level.
* Arguments are plain JSON types with type hints; the docstring is the tool description.
* Return JSON serialisable values; on failure return a string starting with "Error:".
"""
from __future__ import annotations

from typing import Dict, List, Optional, Union


def extrude_faces(mesh: str, faces: Optional[List[int]] = None, thickness: float = 1.0, offset: float = 0.0,
                  divisions: int = 1, keep_faces_together: bool = True) -> Union[Dict, str]:
    """Extrude polygon faces on a mesh.

    Args:
        mesh: Transform or shape name, e.g. "pCube1".
        faces: Face indices to extrude, e.g. [0, 1, 2]. None extrudes every face.
        thickness: Distance to push along the face normal in scene units (negative pushes inward).
        offset: Inset amount applied to the extruded faces (positive shrinks them).
        divisions: Number of segments along the extrusion.
        keep_faces_together: True extrudes the selection as one shell, False extrudes each face separately.

    Returns:
        {"mesh": long name, "history_node": polyExtrudeFace node, "faces": count extruded}
        or an "Error: ..." string.
    """
    try:
        from maya import cmds

        if not cmds.objExists(mesh):
            return "Error: mesh %r does not exist" % mesh
        comps = ["%s.f[%d]" % (mesh, i) for i in faces] if faces else ["%s.f[*]" % mesh]
        nodes = cmds.polyExtrudeFacet(comps, localTranslateZ=float(thickness), offset=float(offset),
                                      divisions=int(divisions), keepFacesTogether=bool(keep_faces_together),
                                      constructionHistory=True)
        count = len(faces) if faces else cmds.polyEvaluate(mesh, face=True)
        return {"mesh": cmds.ls(mesh, long=True)[0], "history_node": nodes[0] if nodes else None, "faces": count}
    except Exception as exc:  # noqa: BLE001
        return "Error: extrude_faces failed: %s" % exc


def bevel_edges(mesh: str, edges: Optional[List[int]] = None, fraction: float = 0.2, segments: int = 1,
                chamfer: bool = True, depth: float = 1.0) -> Union[Dict, str]:
    """Bevel edges with fractional width control (polyBevel3).

    Args:
        mesh: Transform or shape name.
        edges: Edge indices, e.g. [4, 5, 6, 7]. None bevels every edge.
        fraction: Bevel width as a fraction (0 to 1) of the shortest adjacent edge.
        segments: Number of bevel segments (1 = flat chamfer, more = rounded).
        chamfer: True for a chamfered profile, False for a mitered one.
        depth: Bevel depth multiplier (1 = flat, less than 1 concave, more convex).

    Returns:
        {"mesh": long name, "history_node": polyBevel3 node, "edges": count} or "Error: ...".
    """
    try:
        from maya import cmds

        if not cmds.objExists(mesh):
            return "Error: mesh %r does not exist" % mesh
        if not 0.0 < float(fraction) <= 1.0:
            return "Error: fraction must be between 0 and 1"
        comps = ["%s.e[%d]" % (mesh, i) for i in edges] if edges else ["%s.e[*]" % mesh]
        nodes = cmds.polyBevel3(comps, fraction=float(fraction), offsetAsFraction=True, segments=int(segments),
                                chamfer=bool(chamfer), depth=float(depth), worldSpace=True, smoothingAngle=30,
                                mergeVertices=True, mergeVertexTolerance=0.0001, constructionHistory=True)
        count = len(edges) if edges else cmds.polyEvaluate(mesh, edge=True)
        return {"mesh": cmds.ls(mesh, long=True)[0], "history_node": nodes[0] if nodes else None, "edges": count}
    except Exception as exc:  # noqa: BLE001
        return "Error: bevel_edges failed: %s" % exc


def boolean_meshes(mesh_a: str, mesh_b: str, operation: str = "union", name: str = "",
                   delete_history: bool = False) -> Union[Dict, str]:
    """Boolean two meshes (polyBoolOp): union, difference (A minus B) or intersection.

    Args:
        mesh_a: First mesh transform. For difference this is the mesh that is kept.
        mesh_b: Second mesh transform. For difference this is the cutter.
        operation: "union", "difference" or "intersection".
        name: Optional name for the result transform.
        delete_history: True bakes the result so the inputs are gone for good.

    Returns:
        {"result": long name, "operation": op, "history_node": node or None} or "Error: ...".
    """
    try:
        from maya import cmds

        ops = {"union": 1, "difference": 2, "intersection": 3}
        if operation not in ops:
            return "Error: operation must be one of %s" % ", ".join(ops)
        for m in (mesh_a, mesh_b):
            if not cmds.objExists(m):
                return "Error: mesh %r does not exist" % m
        result = cmds.polyBoolOp(mesh_a, mesh_b, op=ops[operation], constructionHistory=True,
                                 name=name or ("%s_%s" % (mesh_a.split("|")[-1], operation)))
        transform = result[0]
        history = result[1] if len(result) > 1 else None
        if delete_history:
            cmds.delete(transform, constructionHistory=True)
            history = None
        return {"result": cmds.ls(transform, long=True)[0], "operation": operation, "history_node": history}
    except Exception as exc:  # noqa: BLE001
        return "Error: boolean_meshes failed: %s" % exc
