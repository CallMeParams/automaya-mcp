"""rig.* and anim.* commands: joints, skinning, IK, controls, keys, baking, layers.

Everything here is plain ``maya.cmds``; rigging conventions follow what a
production rigger expects (offset groups above controls, zeroed end joint
orients, dual quaternion as an opt in, sparse free bakes off by default).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence

from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore


# constants --------------------------------------------------------------------
JOINT_ORIENTS = ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx", "none")
SECONDARY_AXES = ("xup", "xdown", "yup", "ydown", "zup", "zdown", "none")
MIRROR_AXES = {"XY": "mirrorXY", "YZ": "mirrorYZ", "XZ": "mirrorXZ"}
SKIN_METHODS = {"classic": 0, "linear": 0, "dual": 1, "dual_quaternion": 1, "blended": 2, "weight_blended": 2}
BIND_METHODS = {"closest": 0, "closest_distance": 0, "hierarchy": 1, "closest_in_hierarchy": 1, "heat": 2, "heat_map": 2, "geodesic": 3, "geodesic_voxel": 3}
IK_SOLVERS = ("ikRPsolver", "ikSCsolver", "ikSplineSolver")
CONSTRAINT_TYPES = ("parent", "point", "orient", "aim", "scale")
TANGENT_TYPES = ("spline", "linear", "fast", "slow", "flat", "step", "stepnext", "fixed", "clamped", "plateau", "auto")
INFINITY_TYPES = ("constant", "linear", "cycle", "cycleRelative", "oscillate")
FPS_TO_UNIT = {15: "game", 24: "film", 25: "pal", 30: "ntsc", 48: "show", 50: "palf", 60: "ntscf"}
UNIT_TO_FPS = {v: k for k, v in FPS_TO_UNIT.items()}


# helpers ----------------------------------------------------------------------
def _vec3(value: Any, label: str) -> List[float]:
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise BridgeError("%s must be a list of 3 numbers, got %r" % (label, value)) from None
    if len(out) != 3:
        raise BridgeError("%s must have exactly 3 numbers, got %d" % (label, len(out)))
    return out


def _choice(value: str, allowed: Sequence[str], label: str) -> str:
    if value not in allowed:
        raise BridgeError("%s must be one of %s, got %r" % (label, ", ".join(allowed), value))
    return value


def _time_range(start: float | None, end: float | None) -> tuple | None:
    """Build a (start, end) tuple for time= flags. Open ends fall back to the playback range."""
    if start is None and end is None:
        return None
    if start is None:
        start = cmds.playbackOptions(query=True, minTime=True)
    if end is None:
        end = cmds.playbackOptions(query=True, maxTime=True)
    if end < start:
        raise BridgeError("end (%s) must not be before start (%s)" % (end, start))
    return (float(start), float(end))


def _playback_range() -> tuple:
    return (float(cmds.playbackOptions(query=True, minTime=True)), float(cmds.playbackOptions(query=True, maxTime=True)))


def _skin_cluster_of(mesh: str) -> str | None:
    history = cmds.listHistory(mesh, pruneDagObjects=True) or []
    clusters = cmds.ls(history, type="skinCluster") or []
    return clusters[0] if clusters else None


def _fps_from_unit(unit: str) -> float | None:
    if unit in UNIT_TO_FPS:
        return float(UNIT_TO_FPS[unit])
    if isinstance(unit, str) and unit.endswith("fps"):
        try:
            return float(unit[:-3])
        except ValueError:
            return None
    return None


def _unit_from_fps(fps: float) -> str:
    if float(fps).is_integer() and int(fps) in FPS_TO_UNIT:
        return FPS_TO_UNIT[int(fps)]
    return "%gfps" % fps


def _joint_summary(joint: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": joint}
    try:
        info["position"] = cmds.xform(joint, query=True, worldSpace=True, translation=True)
        info["joint_orient"] = cmds.getAttr(joint + ".jointOrient")[0]
    except Exception:
        pass
    return info


# rig.* -------------------------------------------------------------------------
@command("rig.create_joint_chain", mutates=True)
def create_joint_chain(positions: List[List[float]], names: List[str] | None = None, parent: str | None = None, radius: float = 1.0) -> Dict[str, Any]:
    """Create a joint chain through world space positions, optionally under a parent."""
    _util.require_maya()
    if not positions:
        raise BridgeError("positions must contain at least one [x, y, z] point")
    points = [_vec3(p, "positions[%d]" % i) for i, p in enumerate(positions)]
    if names and len(names) != len(points):
        raise BridgeError("names has %d entries but positions has %d" % (len(names), len(points)))
    cmds.select(clear=True)
    if parent:
        _util.require_nodes([parent])
        cmds.select(parent, replace=True)
    created: List[str] = []
    for i, point in enumerate(points):
        kwargs: Dict[str, Any] = {"position": point, "radius": float(radius), "absolute": True}
        if names:
            kwargs["name"] = names[i]
        joint = cmds.joint(**kwargs)
        # The stub returns [] for joint; real Maya returns the joint name.
        joint = joint if isinstance(joint, str) and joint else (names[i] if names else "joint%d" % (i + 1))
        created.append(_util.long_name(joint))
    cmds.select(clear=True)
    return {"joints": created, "root": created[0], "end": created[-1], "count": len(created), "parent": parent}


@command("rig.orient_joints", mutates=True)
def orient_joints(root: str, orient: str = "xyz", secondary: str = "yup", zero_end: bool = True) -> Dict[str, Any]:
    """Orient a joint hierarchy (primary axis down the bone, secondary axis up) and zero end joints."""
    _util.require_maya()
    _util.require_nodes([root])
    _choice(orient, JOINT_ORIENTS, "orient")
    _choice(secondary, SECONDARY_AXES, "secondary")
    if cmds.nodeType(root) != "joint":
        raise BridgeError("%s is not a joint; pass the root joint of the chain" % root)
    cmds.joint(root, edit=True, orientJoint=orient, secondaryAxisOrient=secondary, children=True, zeroScaleOrient=True)
    descendants = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    zeroed: List[str] = []
    if zero_end:
        for joint in descendants:
            kids = cmds.listRelatives(joint, children=True, type="joint") or []
            if not kids:
                cmds.setAttr(joint + ".jointOrient", 0.0, 0.0, 0.0)
                zeroed.append(joint)
    return {"root": _util.long_name(root), "orient": orient, "secondary": secondary, "joint_count": len(descendants) + 1, "zeroed_end_joints": zeroed}


@command("rig.mirror_joints", mutates=True)
def mirror_joints(root: str, axis: str = "YZ", search: str = "L_", replace: str = "R_", behavior: bool = True) -> Dict[str, Any]:
    """Mirror a joint chain across a world plane with a name search/replace (L_ to R_ by default)."""
    _util.require_maya()
    _util.require_nodes([root])
    axis = axis.upper()
    if axis not in MIRROR_AXES:
        raise BridgeError("axis must be XY, YZ or XZ, got %r" % axis)
    kwargs: Dict[str, Any] = {MIRROR_AXES[axis]: True, "searchReplace": (search, replace)}
    if behavior:
        kwargs["mirrorBehavior"] = True
    new = cmds.mirrorJoint(root, **kwargs) or []
    return {"source_root": _util.long_name(root), "mirrored": _util.long_names(new), "axis": axis, "count": len(new)}


@command("rig.bind_skin", mutates=True)
def bind_skin(mesh: str, joints: List[str], max_influences: int = 4, method: str = "classic", bind_method: str = "closest", dropoff_rate: float = 4.0, name: str | None = None) -> Dict[str, Any]:
    """Bind a mesh to joints with a skinCluster (classic linear or dual quaternion)."""
    _util.require_maya()
    if not joints:
        raise BridgeError("joints must list at least one joint to bind to")
    _util.require_nodes([mesh] + list(joints))
    if method not in SKIN_METHODS:
        raise BridgeError("method must be one of %s" % ", ".join(sorted(SKIN_METHODS)))
    if bind_method not in BIND_METHODS:
        raise BridgeError("bind_method must be one of %s" % ", ".join(sorted(BIND_METHODS)))
    if _skin_cluster_of(mesh):
        raise BridgeError("%s already has a skinCluster; detach it first (rig.skin_info shows it)" % mesh)
    kwargs: Dict[str, Any] = {
        "toSelectedBones": True,
        "maximumInfluences": int(max_influences),
        "obeyMaxInfluences": True,
        "skinMethod": SKIN_METHODS[method],
        "bindMethod": BIND_METHODS[bind_method],
        "normalizeWeights": 1,
        "dropoffRate": float(dropoff_rate),
        "removeUnusedInfluence": False,
    }
    if name:
        kwargs["name"] = name
    result = cmds.skinCluster(list(joints) + [mesh], **kwargs) or []
    cluster = result[0] if result else (name or "skinCluster1")
    return {"skin_cluster": cluster, "mesh": _util.long_name(mesh), "influences": _util.long_names(joints), "method": method, "bind_method": bind_method, "max_influences": int(max_influences)}


@command("rig.create_ik", mutates=True)
def create_ik(start_joint: str, end_joint: str, solver: str = "ikRPsolver", name: str | None = None, curve: str | None = None, spans: int = 4) -> Dict[str, Any]:
    """Create an IK handle between two joints (rotate plane, single chain or spline)."""
    _util.require_maya()
    _util.require_nodes([start_joint, end_joint])
    _choice(solver, IK_SOLVERS, "solver")
    kwargs: Dict[str, Any] = {"startJoint": start_joint, "endEffector": end_joint, "solver": solver}
    if name:
        kwargs["name"] = name
    if solver == "ikSplineSolver":
        if curve:
            _util.require_nodes([curve])
            kwargs.update({"curve": curve, "createCurve": False, "parentCurve": False, "rootOnCurve": True})
        else:
            kwargs.update({"createCurve": True, "simplifyCurve": True, "numSpans": int(spans), "parentCurve": False, "rootOnCurve": True})
    result = cmds.ikHandle(**kwargs) or []
    handle = result[0] if len(result) > 0 else (name or "ikHandle1")
    out: Dict[str, Any] = {"handle": _util.long_name(handle), "effector": result[1] if len(result) > 1 else None, "solver": solver, "start_joint": _util.long_name(start_joint), "end_joint": _util.long_name(end_joint)}
    if solver == "ikSplineSolver":
        out["curve"] = _util.long_name(result[2]) if len(result) > 2 else curve
    return out


def _control_curve(name: str, shape: str, size: float) -> str:
    s = float(size)
    if shape == "circle":
        return cmds.circle(name=name, normal=(0, 1, 0), radius=s, constructionHistory=False)[0]
    if shape == "square":
        pts = [(-s, 0, -s), (s, 0, -s), (s, 0, s), (-s, 0, s), (-s, 0, -s)]
    elif shape == "cube":
        pts = [(-s, s, s), (s, s, s), (s, s, -s), (-s, s, -s), (-s, s, s), (-s, -s, s), (s, -s, s), (s, s, s), (s, -s, s), (s, -s, -s),
               (s, s, -s), (s, -s, -s), (-s, -s, -s), (-s, s, -s), (-s, -s, -s), (-s, -s, s)]
    elif shape == "arrow":
        pts = [(0, 0, s), (s * 0.5, 0, 0), (s * 0.25, 0, 0), (s * 0.25, 0, -s), (-s * 0.25, 0, -s), (-s * 0.25, 0, 0), (-s * 0.5, 0, 0), (0, 0, s)]
    else:
        raise BridgeError("shape must be circle, square, cube or arrow, got %r" % shape)
    return cmds.curve(name=name, degree=1, point=pts)


@command("rig.create_control", mutates=True)
def create_control(name: str, shape: str = "circle", size: float = 1.0, target: str | None = None, constrain: str | None = None,
                   color: int | None = None, freeze: bool = True, offset_group: bool = True) -> Dict[str, Any]:
    """Create a NURBS control curve with an offset group, snapped to a target and optionally driving it."""
    _util.require_maya()
    if not name:
        raise BridgeError("name is required")
    if constrain is not None and constrain not in ("parent", "point", "orient"):
        raise BridgeError("constrain must be parent, point or orient")
    if constrain and not target:
        raise BridgeError("constrain needs a target node")
    if target:
        _util.require_nodes([target])
    if color is not None and not 0 <= int(color) <= 31:
        raise BridgeError("color is a Maya index color 0..31 (13 red, 6 blue, 17 yellow)")
    ctrl = _control_curve(name, shape, size) or name
    if freeze:
        cmds.makeIdentity(ctrl, apply=True, translate=True, rotate=True, scale=True, normal=False)
    if color is not None:
        for shp in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
            cmds.setAttr(shp + ".overrideEnabled", 1)
            cmds.setAttr(shp + ".overrideColor", int(color))
    top = ctrl
    grp = None
    if offset_group:
        grp = cmds.group(ctrl, name=name + "_offset") or (name + "_offset")
        top = grp
    if target:
        cmds.matchTransform(top, target, position=True, rotation=True, scale=False)
    constraint = None
    if constrain:
        fn = {"parent": cmds.parentConstraint, "point": cmds.pointConstraint, "orient": cmds.orientConstraint}[constrain]
        res = fn(ctrl, target, maintainOffset=True) or []
        constraint = res[0] if res else None
    return {"control": _util.long_name(ctrl), "offset_group": _util.long_name(grp) if grp else None, "shape": shape, "target": target, "constraint": constraint, "color": color}


@command("rig.constrain", mutates=True)
def constrain(driver: str, driven: str, type: str = "parent", maintain_offset: bool = True, weight: float = 1.0, skip: List[str] | None = None,
              aim_vector: List[float] | None = None, up_vector: List[float] | None = None, world_up_type: str = "scene", world_up_object: str | None = None) -> Dict[str, Any]:
    """Constrain driven to driver (parent, point, orient, aim or scale)."""
    _util.require_maya()
    _util.require_nodes([driver, driven])
    _choice(type, CONSTRAINT_TYPES, "type")
    if driver == driven:
        raise BridgeError("driver and driven are the same node")
    kwargs: Dict[str, Any] = {"maintainOffset": bool(maintain_offset), "weight": float(weight)}
    if skip:
        if type == "parent":
            kwargs["skipTranslate"] = skip
            kwargs["skipRotate"] = skip
        else:
            kwargs["skip"] = skip
    if type == "aim":
        kwargs["aimVector"] = tuple(_vec3(aim_vector, "aim_vector")) if aim_vector else (1.0, 0.0, 0.0)
        kwargs["upVector"] = tuple(_vec3(up_vector, "up_vector")) if up_vector else (0.0, 1.0, 0.0)
        kwargs["worldUpType"] = world_up_type
        if world_up_object:
            _util.require_nodes([world_up_object])
            kwargs["worldUpObject"] = world_up_object
    fn = {"parent": cmds.parentConstraint, "point": cmds.pointConstraint, "orient": cmds.orientConstraint, "aim": cmds.aimConstraint, "scale": cmds.scaleConstraint}[type]
    res = fn(driver, driven, **kwargs) or []
    return {"constraint": res[0] if res else None, "type": type, "driver": _util.long_name(driver), "driven": _util.long_name(driven), "maintain_offset": bool(maintain_offset)}


@command("rig.create_blendshape", mutates=True)
def create_blendshape(base: str, targets: List[str], name: str | None = None, front_of_chain: bool = True) -> Dict[str, Any]:
    """Create a blendShape deformer on base with the given target meshes."""
    _util.require_maya()
    if not targets:
        raise BridgeError("targets must list at least one target mesh")
    _util.require_nodes([base] + list(targets))
    kwargs: Dict[str, Any] = {"frontOfChain": bool(front_of_chain)}
    if name:
        kwargs["name"] = name
    res = cmds.blendShape(list(targets) + [base], **kwargs) or []
    node = res[0] if res else (name or "blendShape1")
    aliases = cmds.aliasAttr(node, query=True) or []
    weights = [aliases[i] for i in range(0, len(aliases), 2)] if aliases else [t.split("|")[-1] for t in targets]
    return {"blendshape": node, "base": _util.long_name(base), "targets": _util.long_names(targets), "weights": weights}


@command("rig.skin_info")
def skin_info(mesh: str) -> Dict[str, Any]:
    """Describe the skinCluster on a mesh: influences, method, max influences."""
    _util.require_maya()
    _util.require_nodes([mesh])
    cluster = _skin_cluster_of(mesh)
    if not cluster:
        return {"mesh": _util.long_name(mesh), "skin_cluster": None, "influences": []}
    influences = cmds.skinCluster(cluster, query=True, influence=True) or []
    method_idx = cmds.getAttr(cluster + ".skinningMethod")
    method_names = {0: "classic", 1: "dual_quaternion", 2: "weight_blended"}
    return {
        "mesh": _util.long_name(mesh),
        "skin_cluster": cluster,
        "influences": _util.long_names(influences),
        "influence_count": len(influences),
        "max_influences": cmds.skinCluster(cluster, query=True, maximumInfluences=True),
        "method": method_names.get(method_idx, method_idx),
        "normalize_weights": cmds.getAttr(cluster + ".normalizeWeights"),
        "envelope": cmds.getAttr(cluster + ".envelope"),
    }


@command("rig.copy_skin_weights", mutates=True)
def copy_skin_weights(src: str, dst: str, surface_association: str = "closestPoint", influence_association: List[str] | None = None, max_influences: int = 4) -> Dict[str, Any]:
    """Copy skin weights from one bound mesh to another; binds dst to the same joints if needed."""
    _util.require_maya()
    _util.require_nodes([src, dst])
    _choice(surface_association, ("closestPoint", "rayCast", "closestComponent", "uvSpace"), "surface_association")
    src_cluster = _skin_cluster_of(src)
    if not src_cluster:
        raise BridgeError("%s has no skinCluster to copy from" % src)
    dst_cluster = _skin_cluster_of(dst)
    created = False
    if not dst_cluster:
        influences = cmds.skinCluster(src_cluster, query=True, influence=True) or []
        res = cmds.skinCluster(list(influences) + [dst], toSelectedBones=True, maximumInfluences=int(max_influences), obeyMaxInfluences=True, normalizeWeights=1) or []
        dst_cluster = res[0] if res else "skinCluster1"
        created = True
    assoc = influence_association or ["oneToOne", "name", "closestJoint"]
    cmds.copySkinWeights(sourceSkin=src_cluster, destinationSkin=dst_cluster, noMirror=True, surfaceAssociation=surface_association, influenceAssociation=assoc)
    return {"source_skin": src_cluster, "destination_skin": dst_cluster, "created_destination_skin": created, "surface_association": surface_association, "influence_association": assoc}


@command("rig.reset_bind_pose", mutates=True)
def reset_bind_pose(mesh: str | None = None, joints: List[str] | None = None, go_to_bind_pose: bool = False) -> Dict[str, Any]:
    """Re-save the bind pose from the current joint pose (or restore joints to it with go_to_bind_pose)."""
    _util.require_maya()
    if not mesh and not joints:
        raise BridgeError("pass mesh (a skinned mesh) or joints")
    cluster = None
    influences: List[str] = list(joints or [])
    if mesh:
        _util.require_nodes([mesh])
        cluster = _skin_cluster_of(mesh)
        if not cluster:
            raise BridgeError("%s has no skinCluster" % mesh)
        influences = cmds.skinCluster(cluster, query=True, influence=True) or influences
    else:
        _util.require_nodes(influences)
    if not influences:
        raise BridgeError("no influences found to save a bind pose for")
    if go_to_bind_pose:
        cmds.dagPose(influences, restore=True, bindPose=True)
        return {"action": "restored", "influences": _util.long_names(influences), "skin_cluster": cluster}
    old_poses = []
    if cluster:
        old_poses = cmds.listConnections(cluster + ".bindPose", type="dagPose") or []
    else:
        old_poses = cmds.dagPose(influences, query=True, bindPose=True) or []
    for pose in set(old_poses):
        if cmds.objExists(pose):
            cmds.delete(pose)
    new_pose = cmds.dagPose(influences, save=True, bindPose=True, name="bindPose1")
    if cluster and new_pose:
        try:
            cmds.connectAttr(new_pose + ".message", cluster + ".bindPose", force=True)
        except Exception:
            pass
    return {"action": "reset", "bind_pose": new_pose, "removed": list(set(old_poses)), "influences": _util.long_names(influences), "skin_cluster": cluster}


# anim.* ------------------------------------------------------------------------
@command("anim.set_keyframe", mutates=True)
def set_keyframe(nodes: List[str] | None = None, attrs: List[str] | None = None, time: float | None = None, value: float | None = None,
                 in_tangent: str = "auto", out_tangent: str = "auto") -> Dict[str, Any]:
    """Key attributes (all keyable channels when attrs is omitted) at a frame."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    _choice(in_tangent, TANGENT_TYPES, "in_tangent")
    _choice(out_tangent, TANGENT_TYPES, "out_tangent")
    if value is not None and not attrs:
        raise BridgeError("value needs attrs so the key knows which channel to set")
    kwargs: Dict[str, Any] = {"inTangentType": in_tangent, "outTangentType": out_tangent}
    if attrs:
        kwargs["attribute"] = list(attrs)
    if time is not None:
        kwargs["time"] = float(time)
    if value is not None:
        kwargs["value"] = float(value)
    count = cmds.setKeyframe(targets, **kwargs)
    frame = float(time) if time is not None else cmds.currentTime(query=True)
    return {"nodes": targets, "attrs": attrs or "all keyable", "time": frame, "keys_set": count if isinstance(count, int) else None}


@command("anim.get_keyframes")
def get_keyframes(node: str, attr: str, start: float | None = None, end: float | None = None, include_tangents: bool = False) -> Dict[str, Any]:
    """Return key times and values for one attribute, optionally within a frame range."""
    _util.require_maya()
    _util.require_nodes([node])
    kwargs: Dict[str, Any] = {"attribute": attr, "query": True}
    rng = _time_range(start, end)
    if rng:
        kwargs["time"] = rng
    times = cmds.keyframe(node, timeChange=True, **kwargs) or []
    values = cmds.keyframe(node, valueChange=True, **kwargs) or []
    out: Dict[str, Any] = {"node": _util.long_name(node), "attr": attr, "times": list(times), "values": list(values), "count": len(times)}
    if include_tangents and times:
        tk = {"attribute": attr, "query": True}
        if rng:
            tk["time"] = rng
        out["in_tangents"] = cmds.keyTangent(node, inTangentType=True, **tk) or []
        out["out_tangents"] = cmds.keyTangent(node, outTangentType=True, **tk) or []
    return out


@command("anim.delete_keys", mutates=True)
def delete_keys(nodes: List[str] | None = None, attrs: List[str] | None = None, start: float | None = None, end: float | None = None) -> Dict[str, Any]:
    """Remove keys on nodes (all channels or given attrs), optionally only inside a frame range."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    kwargs: Dict[str, Any] = {"clear": True}
    if attrs:
        kwargs["attribute"] = list(attrs)
    rng = _time_range(start, end)
    if rng:
        kwargs["time"] = rng
    removed = cmds.cutKey(targets, **kwargs)
    return {"nodes": targets, "attrs": attrs or "all", "range": rng, "keys_removed": removed if isinstance(removed, int) else None}


@command("anim.set_time_range", mutates=True)
def set_time_range(start: float, end: float, anim_start: float | None = None, anim_end: float | None = None) -> Dict[str, Any]:
    """Set the playback range (and optionally the wider animation range)."""
    _util.require_maya()
    if end < start:
        raise BridgeError("end must be >= start")
    a_start = float(anim_start) if anim_start is not None else min(float(start), float(cmds.playbackOptions(query=True, animationStartTime=True) or start))
    a_end = float(anim_end) if anim_end is not None else max(float(end), float(cmds.playbackOptions(query=True, animationEndTime=True) or end))
    cmds.playbackOptions(animationStartTime=a_start, animationEndTime=a_end, minTime=float(start), maxTime=float(end))
    return get_time_range()


@command("anim.get_time_range")
def get_time_range() -> Dict[str, Any]:
    """Playback range, animation range, current frame and fps."""
    _util.require_maya()
    unit = cmds.currentUnit(query=True, time=True)
    return {
        "start": cmds.playbackOptions(query=True, minTime=True),
        "end": cmds.playbackOptions(query=True, maxTime=True),
        "anim_start": cmds.playbackOptions(query=True, animationStartTime=True),
        "anim_end": cmds.playbackOptions(query=True, animationEndTime=True),
        "current": cmds.currentTime(query=True),
        "time_unit": unit,
        "fps": _fps_from_unit(unit),
        "playback_speed": cmds.playbackOptions(query=True, playbackSpeed=True),
        "loop": cmds.playbackOptions(query=True, loop=True),
    }


@command("anim.set_current_time", mutates=True)
def set_current_time(frame: float) -> Dict[str, Any]:
    """Move the time slider to a frame."""
    _util.require_maya()
    cmds.currentTime(float(frame), edit=True)
    return {"current": float(frame)}


@command("anim.playback", mutates=True)
def playback(action: str = "play", forward: bool = True, frames: int = 1) -> Dict[str, Any]:
    """Play, stop, toggle or step the timeline."""
    _util.require_maya()
    _choice(action, ("play", "stop", "step", "toggle", "status"), "action")
    if action == "play":
        cmds.play(state=True, forward=bool(forward))
    elif action == "stop":
        cmds.play(state=False)
    elif action == "toggle":
        playing = bool(cmds.play(query=True, state=True))
        cmds.play(state=not playing, forward=bool(forward))
    elif action == "step":
        now = float(cmds.currentTime(query=True) or 0.0)
        cmds.currentTime(now + (int(frames) if forward else -int(frames)), edit=True)
    return {"action": action, "playing": bool(cmds.play(query=True, state=True)), "current": cmds.currentTime(query=True)}


@command("anim.bake", mutates=True)
def bake(nodes: List[str] | None = None, start: float | None = None, end: float | None = None, attrs: List[str] | None = None,
         sample_by: float = 1.0, simulation: bool = True, preserve_outside_keys: bool = True, remove_constraints: bool = False) -> Dict[str, Any]:
    """Bake animation onto nodes over a frame range (constraints, expressions, IK become keys)."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    rng = _time_range(start, end) or _playback_range()
    if float(sample_by) <= 0:
        raise BridgeError("sample_by must be > 0")
    kwargs: Dict[str, Any] = {
        "time": rng,
        "sampleBy": float(sample_by),
        "simulation": bool(simulation),
        "preserveOutsideKeys": bool(preserve_outside_keys),
        "disableImplicitControl": True,
        "minimizeRotation": True,
        "sparseAnimCurveBake": False,
        "removeBakedAttributeFromLayer": False,
        "bakeOnOverrideLayer": False,
    }
    if attrs:
        kwargs["attribute"] = list(attrs)
    cmds.bakeResults(targets, **kwargs)
    removed = []
    if remove_constraints:
        for node in targets:
            for con in cmds.listRelatives(node, children=True, type="constraint", fullPath=True) or []:
                cmds.delete(con)
                removed.append(con)
    return {"nodes": targets, "start": rng[0], "end": rng[1], "attrs": attrs or "all keyable", "sample_by": float(sample_by), "removed_constraints": removed}


@command("anim.motion_path", mutates=True)
def motion_path(node: str, curve: str, start: float | None = None, end: float | None = None, follow: bool = True, up_axis: str = "y",
                front_axis: str = "x", world_up_type: str = "scene", world_up_object: str | None = None, bank: bool = False, parametric: bool = False) -> Dict[str, Any]:
    """Attach a node to a curve with a motionPath node over a frame range."""
    _util.require_maya()
    _util.require_nodes([node, curve])
    _choice(up_axis.lower(), ("x", "y", "z"), "up_axis")
    _choice(front_axis.lower(), ("x", "y", "z"), "front_axis")
    _choice(world_up_type, ("scene", "object", "objectrotation", "vector", "normal"), "world_up_type")
    rng = _time_range(start, end) or _playback_range()
    kwargs: Dict[str, Any] = {
        "curve": curve,
        "startTimeU": rng[0],
        "endTimeU": rng[1],
        "follow": bool(follow),
        "followAxis": front_axis.lower(),
        "upAxis": up_axis.lower(),
        "worldUpType": world_up_type,
        "fractionMode": not parametric,
        "bank": bool(bank),
    }
    if world_up_object:
        _util.require_nodes([world_up_object])
        kwargs["worldUpObject"] = world_up_object
    mp = cmds.pathAnimation(node, **kwargs)
    return {"motion_path": mp if isinstance(mp, str) else None, "node": _util.long_name(node), "curve": _util.long_name(curve), "start": rng[0], "end": rng[1], "follow": bool(follow)}


@command("anim.set_tangents", mutates=True)
def set_tangents(nodes: List[str] | None = None, in_type: str = "auto", out_type: str | None = None, attrs: List[str] | None = None,
                 start: float | None = None, end: float | None = None, weighted: bool | None = None) -> Dict[str, Any]:
    """Set in/out tangent types on existing keys (all keys, or only inside a range)."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    out_type = out_type or in_type
    _choice(in_type, TANGENT_TYPES, "in_type")
    _choice(out_type, TANGENT_TYPES, "out_type")
    kwargs: Dict[str, Any] = {"edit": True, "inTangentType": in_type, "outTangentType": out_type}
    if attrs:
        kwargs["attribute"] = list(attrs)
    rng = _time_range(start, end)
    if rng:
        kwargs["time"] = rng
    if weighted is not None:
        kwargs["weightedTangents"] = bool(weighted)
    cmds.keyTangent(targets, **kwargs)
    return {"nodes": targets, "in_type": in_type, "out_type": out_type, "attrs": attrs or "all", "range": rng}


@command("anim.set_infinity", mutates=True)
def set_infinity(nodes: List[str] | None = None, pre: str = "constant", post: str = "constant", attrs: List[str] | None = None) -> Dict[str, Any]:
    """Set pre and post infinity on anim curves (cycle, cycleRelative, oscillate, linear, constant)."""
    _util.require_maya()
    targets = _util.resolve_targets(nodes)
    _choice(pre, INFINITY_TYPES, "pre")
    _choice(post, INFINITY_TYPES, "post")
    kwargs: Dict[str, Any] = {"preInfinite": pre, "postInfinite": post}
    if attrs:
        kwargs["attribute"] = list(attrs)
    cmds.setInfinity(targets, **kwargs)
    return {"nodes": targets, "pre": pre, "post": post, "attrs": attrs or "all"}


@command("anim.import_animation", mutates=True)
def import_animation(path: str, nodes: List[str] | None = None, mode: str = "merge") -> Dict[str, Any]:
    """Import animation from an FBX (onto matching names) or ATOM file (onto selected/given nodes)."""
    _util.require_maya()
    if not path or not os.path.isfile(path):
        raise BridgeError("animation file not found: %r" % path)
    ext = os.path.splitext(path)[1].lower()
    _choice(mode, ("merge", "add", "exclusive"), "mode")
    if nodes:
        _util.require_nodes(nodes)
        cmds.select(nodes, replace=True)
    before = set(cmds.ls(type="animCurve") or [])
    if ext == ".fbx":
        _util.ensure_plugin("fbxmaya")
        mel.eval("FBXResetImport")
        mel.eval('FBXImportMode -v "%s"' % {"merge": "merge", "add": "add", "exclusive": "exmerge"}[mode])
        mel.eval("FBXImportFillTimeline -v false")
        mel.eval('FBXImport -f "%s"' % path.replace("\\", "/"))
    elif ext == ".atom":
        _util.ensure_plugin("atomImportExport")
        if not nodes and not (cmds.ls(selection=True) or []):
            raise BridgeError("ATOM import needs nodes (or a selection) to apply the curves to")
        options = ";;targetTime=3;option=%s;match=hierarchy;;selected=selectedOnly;search=;replace=;prefix=;suffix=;mapFile=;" % ("insert" if mode == "add" else "scaleReplace")
        cmds.file(path, i=True, type="atomImport", ra=True, namespace="atom", options=options)
    else:
        raise BridgeError("unsupported animation file %r (use .fbx or .atom)" % ext)
    after = set(cmds.ls(type="animCurve") or [])
    return {"path": path, "format": ext[1:], "mode": mode, "nodes": nodes, "new_anim_curves": len(after - before)}


@command("anim.retarget_hint")
def retarget_hint(source_root: str, target_root: str) -> Dict[str, Any]:
    """Human readable HumanIK retarget steps for two skeletons (no automation, just the recipe)."""
    _util.require_maya()
    info: Dict[str, Any] = {"source_root": source_root, "target_root": target_root}
    for label, root in (("source", source_root), ("target", target_root)):
        if cmds.objExists(root):
            joints = cmds.listRelatives(root, allDescendents=True, type="joint") or []
            info[label + "_joint_count"] = len(joints) + 1
        else:
            info[label + "_joint_count"] = None
            info[label + "_warning"] = "%s does not exist in the scene" % root
    info["steps"] = [
        "1. Window > Animation Editors > HumanIK. Both skeletons should be in a T pose or A pose facing +Z with the root at the origin.",
        "2. Select the source root (%s), click 'Create Character Definition', then map joints (Hips, Spine, Head, Arms, Legs). Use 'Auto' if the naming follows a known template." % source_root,
        "3. Lock the source definition (the padlock). Green means every mandatory bone is mapped.",
        "4. Repeat for the target root (%s): new character, map, lock." % target_root,
        "5. In the HumanIK Character menu choose the target as Character and the source as Source. Motion now streams live.",
        "6. Bake: Edit > Bake to Skeleton (or use anim.bake on the target joints over the shot range) to get plain keys.",
        "7. Fix offsets in the Character Controls > Edit > Retarget Specific settings (Match Source, Reach, Pull) before baking.",
    ]
    info["mel_helpers"] = [
        'hikCreateCharacter(1)  // new definition',
        'hikSetCurrentCharacter("Character1")',
        'hikBakeCharacter(0)  // bake to skeleton',
    ]
    return info


def _anim_curve_targets(curve: str, depth: int = 0) -> List[str]:
    """Plugs driven by an anim curve, looking through anim layer blend nodes."""
    plugs = cmds.listConnections(curve + ".output", plugs=True, source=False, destination=True) or []
    out: List[str] = []
    for plug in plugs:
        node = plug.split(".")[0]
        ntype = cmds.nodeType(node) or ""
        if ntype.startswith("animBlendNode") and depth < 8:
            for out_plug in (cmds.listAttr(node, string="output*") or ["output"]):
                for downstream in cmds.listConnections("%s.%s" % (node, out_plug), plugs=True, source=False, destination=True) or []:
                    out.extend(_anim_curve_targets_from_plug(downstream, depth + 1))
        else:
            out.append(plug)
    return out


def _anim_curve_targets_from_plug(plug: str, depth: int) -> List[str]:
    node = plug.split(".")[0]
    ntype = cmds.nodeType(node) or ""
    if ntype.startswith("animBlendNode") and depth < 8:
        result: List[str] = []
        for downstream in cmds.listConnections(node + ".output", plugs=True, source=False, destination=True) or []:
            result.extend(_anim_curve_targets_from_plug(downstream, depth + 1))
        return result
    return [plug]


@command("anim.list_animated")
def list_animated(nodes: List[str] | None = None) -> Dict[str, Any]:
    """Which nodes have anim curves and on which attributes (all nodes when nodes is omitted)."""
    _util.require_maya()
    curves = cmds.ls(type=("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")) or []
    wanted = None
    if nodes:
        _util.require_nodes(nodes)
        wanted = set()
        for n in nodes:
            wanted.add(n)
            wanted.add(_util.long_name(n))
            wanted.add(n.split("|")[-1])
    per_node: Dict[str, Dict[str, Any]] = {}
    for curve in curves:
        for plug in _anim_curve_targets(curve):
            node, _, attr = plug.partition(".")
            if wanted is not None and node not in wanted and node.split("|")[-1] not in wanted:
                continue
            entry = per_node.setdefault(node, {"attrs": [], "curves": []})
            if attr not in entry["attrs"]:
                entry["attrs"].append(attr)
            entry["curves"].append(curve)
    return {"animated": [{"node": n, "attrs": d["attrs"], "curve_count": len(d["curves"])} for n, d in sorted(per_node.items())], "node_count": len(per_node), "curve_count": len(curves)}


@command("anim.set_playback_speed", mutates=True)
def set_playback_speed(mode: str = "realtime", fps: float | None = None, loop: str | None = None) -> Dict[str, Any]:
    """Playback speed (realtime, every_frame, half, double), optional scene fps and loop mode."""
    _util.require_maya()
    speeds = {"realtime": 1.0, "every_frame": 0.0, "half": 0.5, "double": 2.0}
    _choice(mode, tuple(speeds), "mode")
    if fps is not None:
        if fps <= 0:
            raise BridgeError("fps must be positive")
        cmds.currentUnit(time=_unit_from_fps(float(fps)), updateAnimation=False)
    cmds.playbackOptions(playbackSpeed=speeds[mode])
    if mode == "every_frame":
        cmds.playbackOptions(maxPlaybackSpeed=0)
    if loop is not None:
        _choice(loop, ("once", "continuous", "oscillate"), "loop")
        cmds.playbackOptions(loop=loop)
    return {"mode": mode, "fps": fps if fps is not None else _fps_from_unit(cmds.currentUnit(query=True, time=True)), "loop": loop or cmds.playbackOptions(query=True, loop=True)}


@command("anim.create_animation_layer", mutates=True)
def create_animation_layer(name: str, nodes: List[str] | None = None, attrs: List[str] | None = None, override: bool = False, mute: bool = False, solo: bool = False) -> Dict[str, Any]:
    """Create an animation layer and add nodes (or specific node attributes) to it."""
    _util.require_maya()
    if not name:
        raise BridgeError("name is required")
    if cmds.objExists(name) and cmds.nodeType(name) == "animLayer":
        raise BridgeError("animation layer %r already exists" % name)
    layer = cmds.animLayer(name, override=bool(override)) or name
    added: List[str] = []
    if nodes:
        _util.require_nodes(nodes)
        if attrs:
            plugs = ["%s.%s" % (n, a) for n in nodes for a in attrs]
            cmds.animLayer(layer, edit=True, attribute=plugs)
            added = plugs
        else:
            cmds.select(nodes, replace=True)
            cmds.animLayer(layer, edit=True, addSelectedObjects=True)
            added = _util.long_names(nodes)
    if mute:
        cmds.animLayer(layer, edit=True, mute=True)
    if solo:
        cmds.animLayer(layer, edit=True, solo=True)
    return {"layer": layer, "override": bool(override), "members": added, "mute": bool(mute), "solo": bool(solo)}

