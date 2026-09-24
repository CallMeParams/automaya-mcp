"""Drop-in rigging and weight painting tools (AutoMaya extension format, PatrickPalmer compatible).

Public functions become ``ext.rigging_tools.<name>`` bridge commands and
``maya_ext_rigging_tools_<name>`` MCP tools. Maya imports stay inside functions,
inputs are JSON types, returns are JSON or an "Error: ..." string.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Union


def create_joint_chain(positions: List[List[float]], names: Optional[List[str]] = None, parent: str = "",
                       radius: float = 1.0, orient: bool = True) -> Union[Dict, str]:
    """Create a parented joint chain from world space coordinates.

    Args:
        positions: List of [x, y, z] points in scene units, root first, e.g. [[0,0,0],[0,10,0],[0,20,0]].
        names: Optional joint names, same length as positions. Missing names are auto generated.
        parent: Optional existing transform to parent the root joint under.
        radius: Joint display radius.
        orient: True runs joint orient (xyz, y up) on the chain after creation.

    Returns:
        {"joints": [long names root to tip], "root": name, "tip": name} or "Error: ...".
    """
    try:
        from maya import cmds

        if not positions or len(positions) < 1:
            return "Error: positions must contain at least one [x, y, z] point"
        if names and len(names) != len(positions):
            return "Error: names must match positions in length"
        if parent and not cmds.objExists(parent):
            return "Error: parent %r does not exist" % parent
        cmds.select(clear=True)
        if parent:
            cmds.select(parent)
        joints = []
        for i, pos in enumerate(positions):
            if len(pos) != 3:
                return "Error: position %d is not [x, y, z]" % i
            kwargs = {"position": [float(v) for v in pos], "radius": float(radius), "absolute": True}
            if names:
                kwargs["name"] = names[i]
            joints.append(cmds.joint(**kwargs))
        if orient and len(joints) > 1:
            cmds.joint(joints[0], edit=True, orientJoint="xyz", secondaryAxisOrient="yup", children=True, zeroScaleOrient=True)
        long_names = cmds.ls(joints, long=True)
        return {"joints": long_names, "root": long_names[0], "tip": long_names[-1]}
    except Exception as exc:  # noqa: BLE001
        return "Error: create_joint_chain failed: %s" % exc


def find_skin_cluster(mesh: str) -> Union[Dict, str]:
    """Find the skinCluster deforming a mesh and list its influences.

    Args:
        mesh: Transform or shape name of a skinned mesh (or the current selection if empty).

    Returns:
        {"mesh": long name, "skin_cluster": node, "influences": [joint names], "max_influences": int,
         "method": "classic|dual_quaternion|weight_blended"} or "Error: ..." when the mesh is not skinned.
    """
    try:
        from maya import cmds, mel

        if not mesh:
            sel = cmds.ls(selection=True, long=True)
            if not sel:
                return "Error: no mesh given and nothing is selected"
            mesh = sel[0]
        if not cmds.objExists(mesh):
            return "Error: mesh %r does not exist" % mesh
        cluster = mel.eval('findRelatedSkinCluster("%s")' % mesh)
        if not cluster:
            history = cmds.listHistory(mesh, pruneDagObjects=True) or []
            clusters = cmds.ls(history, type="skinCluster")
            cluster = clusters[0] if clusters else None
        if not cluster:
            return "Error: %s has no skinCluster; bind it first" % mesh
        methods = {0: "classic", 1: "dual_quaternion", 2: "weight_blended"}
        return {
            "mesh": cmds.ls(mesh, long=True)[0],
            "skin_cluster": cluster,
            "influences": cmds.skinCluster(cluster, query=True, influence=True) or [],
            "max_influences": cmds.getAttr(cluster + ".maxInfluences"),
            "method": methods.get(cmds.getAttr(cluster + ".skinningMethod"), "unknown"),
        }
    except Exception as exc:  # noqa: BLE001
        return "Error: find_skin_cluster failed: %s" % exc


def get_vertex_weights(mesh: str, vertex: int, skin_cluster: str = "", min_weight: float = 0.0001) -> Union[Dict, str]:
    """Read the skin weights of one vertex for every influencing joint.

    Args:
        mesh: Skinned mesh transform or shape.
        vertex: Vertex index, e.g. 42 for pCube1.vtx[42].
        skin_cluster: Optional skinCluster name; found automatically when empty.
        min_weight: Influences below this weight are left out of the result.

    Returns:
        {"vertex": "mesh.vtx[n]", "skin_cluster": node, "weights": {"joint": weight, ...}, "total": sum}
        or "Error: ...".
    """
    try:
        from maya import cmds

        info = find_skin_cluster(mesh) if not skin_cluster else None
        if isinstance(info, str):
            return info
        cluster = skin_cluster or info["skin_cluster"]
        comp = "%s.vtx[%d]" % (mesh, int(vertex))
        if not cmds.objExists(comp):
            return "Error: %s does not exist" % comp
        influences = cmds.skinCluster(cluster, query=True, influence=True) or []
        values = cmds.skinPercent(cluster, comp, query=True, value=True) or []
        weights = {j: round(float(w), 6) for j, w in zip(influences, values) if float(w) >= float(min_weight)}
        return {"vertex": comp, "skin_cluster": cluster, "weights": weights, "total": round(sum(weights.values()), 6)}
    except Exception as exc:  # noqa: BLE001
        return "Error: get_vertex_weights failed: %s" % exc


def set_vertex_weight(mesh: str, vertex: int, joint: str, weight: float, skin_cluster: str = "",
                      normalize: bool = True, additional_vertices: Optional[List[int]] = None) -> Union[Dict, str]:
    """Set the weight of one joint on a vertex (and optionally more vertices) with normalisation.

    Args:
        mesh: Skinned mesh transform or shape.
        vertex: Vertex index to edit.
        joint: Influence joint name; must already be in the skinCluster.
        weight: New weight 0 to 1 for that joint. Other influences are renormalised when normalize is True.
        skin_cluster: Optional skinCluster name; found automatically when empty.
        normalize: True keeps the vertex total at 1.0 by adjusting the other influences.
        additional_vertices: Optional extra vertex indices that get the same edit.

    Returns:
        {"vertices": [components], "joint": joint, "weight": value, "result": weights of the first vertex}
        or "Error: ...".
    """
    try:
        from maya import cmds

        if not 0.0 <= float(weight) <= 1.0:
            return "Error: weight must be between 0 and 1"
        info = find_skin_cluster(mesh) if not skin_cluster else None
        if isinstance(info, str):
            return info
        cluster = skin_cluster or info["skin_cluster"]
        influences = cmds.skinCluster(cluster, query=True, influence=True) or []
        if joint not in influences and joint.split("|")[-1] not in [i.split("|")[-1] for i in influences]:
            return "Error: %s is not an influence of %s; add it with skinCluster -addInfluence first" % (joint, cluster)
        indices = [int(vertex)] + [int(v) for v in (additional_vertices or [])]
        comps = ["%s.vtx[%d]" % (mesh, i) for i in indices]
        cmds.skinPercent(cluster, comps, transformValue=[(joint, float(weight))], normalize=bool(normalize))
        after = get_vertex_weights(mesh, indices[0], skin_cluster=cluster)
        return {"vertices": comps, "joint": joint, "weight": float(weight), "result": after if isinstance(after, dict) else None}
    except Exception as exc:  # noqa: BLE001
        return "Error: set_vertex_weight failed: %s" % exc


def prune_and_normalize(mesh: str, prune_below: float = 0.01, skin_cluster: str = "") -> Union[Dict, str]:
    """Prune tiny weights on the whole mesh and renormalise every vertex to 1.0.

    Args:
        mesh: Skinned mesh.
        prune_below: Weights under this value are set to zero before normalising.
        skin_cluster: Optional skinCluster name; found automatically when empty.

    Returns:
        {"skin_cluster": node, "pruned_below": value, "vertices": count} or "Error: ...".
    """
    try:
        from maya import cmds

        info = find_skin_cluster(mesh) if not skin_cluster else None
        if isinstance(info, str):
            return info
        cluster = skin_cluster or info["skin_cluster"]
        cmds.skinPercent(cluster, "%s.vtx[*]" % mesh, pruneWeights=float(prune_below))
        cmds.skinPercent(cluster, "%s.vtx[*]" % mesh, normalize=True)
        return {"skin_cluster": cluster, "pruned_below": float(prune_below), "vertices": cmds.polyEvaluate(mesh, vertex=True)}
    except Exception as exc:  # noqa: BLE001
        return "Error: prune_and_normalize failed: %s" % exc
