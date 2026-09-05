"""Unit + integration tests for the rigging_animation domain."""
from __future__ import annotations

import os

import pytest
from maya import mel
from tests.conftest import parse

from automaya_bridge.handlers import rigging_animation as ra
from automaya_bridge.handlers._util import BridgeError


def _joint(fake_maya, names):
    """Make the stub answer like a joint chain: joint() echoes the name, nodeType is joint."""
    fake_maya.responses["joint"] = lambda *a, **k: k.get("name", a[0] if a else "joint1")
    fake_maya.responses["nodeType"] = lambda node, **k: "joint" if node.split("|")[-1] in names else "transform"
    fake_maya.responses["ls"] = lambda *a, **k: ["|" + a[0]] if a and isinstance(a[0], str) and k.get("long") else ([] if not a else list(a[0]) if isinstance(a[0], (list, tuple)) else [a[0]])


# unit: rig -------------------------------------------------------------------
def test_create_joint_chain_calls_joint_with_positions(fake_maya):
    _joint(fake_maya, ["L_shoulder", "L_elbow", "L_wrist"])
    out = ra.create_joint_chain([[0, 0, 0], [10, 0, 0], [20, 0, 0]], names=["L_shoulder", "L_elbow", "L_wrist"], radius=0.5)
    calls = fake_maya.calls_to("joint")
    assert len(calls) == 3
    assert calls[1][1]["position"] == [10.0, 0.0, 0.0] and calls[1][1]["radius"] == 0.5 and calls[1][1]["name"] == "L_elbow"
    assert fake_maya.calls_to("select")[0][1] == {"clear": True}
    assert out["root"] == "|L_shoulder" and out["end"] == "|L_wrist" and out["count"] == 3


def test_create_joint_chain_rejects_bad_point(fake_maya):
    with pytest.raises(BridgeError, match="exactly 3"):
        ra.create_joint_chain([[0, 0]])
    with pytest.raises(BridgeError, match="names has"):
        ra.create_joint_chain([[0, 0, 0]], names=["a", "b"])


def test_orient_joints_zeroes_end(fake_maya):
    _joint(fake_maya, ["root", "mid", "tip"])
    fake_maya.responses["listRelatives"] = lambda node, **k: (["|root|mid", "|root|mid|tip"] if k.get("allDescendents") else (["tip"] if node.endswith("mid") else []))
    out = ra.orient_joints("root", orient="xyz", secondary="zup")
    edit = fake_maya.calls_to("joint")[0][1]
    assert edit["orientJoint"] == "xyz" and edit["secondaryAxisOrient"] == "zup" and edit["children"] is True
    assert out["zeroed_end_joints"] == ["|root|mid|tip"]
    assert ("setAttr", ("|root|mid|tip.jointOrient", 0.0, 0.0, 0.0), {}) in fake_maya.calls


def test_orient_joints_rejects_non_joint_and_bad_axis(fake_maya):
    with pytest.raises(BridgeError, match="orient must be"):
        ra.orient_joints("root", orient="abc")
    with pytest.raises(BridgeError, match="not a joint"):
        ra.orient_joints("pCube1")


def test_mirror_joints_flags(fake_maya):
    fake_maya.responses["mirrorJoint"] = ["R_arm", "R_elbow"]
    out = ra.mirror_joints("L_arm", axis="yz", search="L_", replace="R_")
    kwargs = fake_maya.calls_to("mirrorJoint")[0][1]
    assert kwargs["mirrorYZ"] is True and kwargs["mirrorBehavior"] is True and kwargs["searchReplace"] == ("L_", "R_")
    assert out["count"] == 2


def test_bind_skin_maps_methods(fake_maya):
    fake_maya.responses["skinCluster"] = ["skinCluster1"]
    out = ra.bind_skin("body", ["hip", "spine"], max_influences=3, method="dual", bind_method="geodesic")
    args, kwargs = fake_maya.calls_to("skinCluster")[0]
    assert args[0] == ["hip", "spine", "body"]
    assert kwargs["skinMethod"] == 1 and kwargs["bindMethod"] == 3 and kwargs["maximumInfluences"] == 3 and kwargs["toSelectedBones"] is True
    assert out["skin_cluster"] == "skinCluster1"


def test_bind_skin_refuses_double_bind(fake_maya):
    fake_maya.responses["listHistory"] = ["skinCluster1", "bodyShapeOrig"]
    fake_maya.responses["ls"] = lambda *a, **k: ["skinCluster1"] if k.get("type") == "skinCluster" else []
    with pytest.raises(BridgeError, match="already has a skinCluster"):
        ra.bind_skin("body", ["hip"])


def test_create_ik_spline_creates_curve(fake_maya):
    fake_maya.responses["ikHandle"] = ["spineIK", "effector1", "curve1"]
    out = ra.create_ik("spine1", "spine5", solver="ikSplineSolver", name="spineIK")
    kwargs = fake_maya.calls_to("ikHandle")[0][1]
    assert kwargs["solver"] == "ikSplineSolver" and kwargs["createCurve"] is True and kwargs["startJoint"] == "spine1"
    assert out["curve"] == "curve1" and out["handle"] == "spineIK"
    with pytest.raises(BridgeError, match="solver must be"):
        ra.create_ik("a", "b", solver="ikBogus")


def test_create_control_offset_group_and_constraint(fake_maya):
    fake_maya.responses["circle"] = ["L_arm_ctrl", "makeNurbCircle1"]
    fake_maya.responses["listRelatives"] = lambda node, **k: ["|L_arm_ctrl_offset|L_arm_ctrl|L_arm_ctrlShape"] if k.get("shapes") else []
    fake_maya.responses["parentConstraint"] = ["L_wrist_parentConstraint1"]
    out = ra.create_control("L_arm_ctrl", shape="circle", size=3.0, target="L_wrist", constrain="parent", color=13)
    assert fake_maya.calls_to("circle")[0][1]["radius"] == 3.0
    assert fake_maya.calls_to("group")[0][1]["name"] == "L_arm_ctrl_offset"
    assert fake_maya.calls_to("matchTransform")[0][0] == ("L_arm_ctrl_offset", "L_wrist")
    assert fake_maya.calls_to("makeIdentity")[0][1]["apply"] is True
    assert ("setAttr", ("|L_arm_ctrl_offset|L_arm_ctrl|L_arm_ctrlShape.overrideColor", 13), {}) in fake_maya.calls
    assert out["constraint"] == "L_wrist_parentConstraint1" and out["offset_group"] == "L_arm_ctrl_offset"


def test_create_control_cube_uses_curve(fake_maya):
    fake_maya.responses["curve"] = "box_ctrl"
    ra.create_control("box_ctrl", shape="cube", offset_group=False, freeze=False)
    kwargs = fake_maya.calls_to("curve")[0][1]
    assert kwargs["degree"] == 1 and len(kwargs["point"]) == 16
    with pytest.raises(BridgeError, match="shape must be"):
        ra.create_control("x", shape="star")


def test_constrain_aim_flags(fake_maya):
    fake_maya.responses["aimConstraint"] = ["cam_aimConstraint1"]
    out = ra.constrain("aim_loc", "cam", type="aim", maintain_offset=False, aim_vector=[0, 0, -1], world_up_type="object", world_up_object="up_loc")
    kwargs = fake_maya.calls_to("aimConstraint")[0][1]
    assert kwargs["aimVector"] == (0.0, 0.0, -1.0) and kwargs["worldUpObject"] == "up_loc" and kwargs["maintainOffset"] is False
    assert out["constraint"] == "cam_aimConstraint1"
    with pytest.raises(BridgeError, match="same node"):
        ra.constrain("a", "a")


def test_blendshape_and_skin_info(fake_maya):
    fake_maya.responses["blendShape"] = ["faceShapes"]
    fake_maya.responses["aliasAttr"] = ["smile", "weight[0]", "frown", "weight[1]"]
    out = ra.create_blendshape("head", ["smile", "frown"], name="faceShapes")
    assert fake_maya.calls_to("blendShape")[0][0][0] == ["smile", "frown", "head"]
    assert out["weights"] == ["smile", "frown"]

    fake_maya.responses["listHistory"] = ["skinCluster1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["skinCluster1"] if k.get("type") == "skinCluster" else ([a[0]] if a and isinstance(a[0], str) else [])
    fake_maya.responses["skinCluster"] = lambda *a, **k: ["hip", "spine"] if k.get("influence") else 4
    fake_maya.responses["getAttr"] = lambda plug: {"skinCluster1.skinningMethod": 1, "skinCluster1.normalizeWeights": 1, "skinCluster1.envelope": 1.0}[plug]
    info = ra.skin_info("body")
    assert info["method"] == "dual_quaternion" and info["influence_count"] == 2 and info["max_influences"] == 4


def test_copy_skin_weights_binds_destination(fake_maya):
    clusters = {"src": ["skinCluster1"], "dst": []}
    fake_maya.responses["listHistory"] = lambda mesh, **k: clusters[mesh]
    fake_maya.responses["ls"] = lambda *a, **k: list(a[0]) if a and k.get("type") == "skinCluster" else []
    fake_maya.responses["skinCluster"] = lambda *a, **k: ["hip", "spine"] if k.get("influence") else ["skinCluster2"]
    out = ra.copy_skin_weights("src", "dst")
    assert out["created_destination_skin"] is True and out["destination_skin"] == "skinCluster2"
    kwargs = fake_maya.calls_to("copySkinWeights")[0][1]
    assert kwargs["sourceSkin"] == "skinCluster1" and kwargs["influenceAssociation"] == ["oneToOne", "name", "closestJoint"]


def test_reset_bind_pose(fake_maya):
    fake_maya.responses["listHistory"] = ["skinCluster1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["skinCluster1"] if k.get("type") == "skinCluster" else []
    fake_maya.responses["skinCluster"] = ["hip", "spine"]
    fake_maya.responses["listConnections"] = ["bindPose1"]
    fake_maya.responses["dagPose"] = "bindPose2"
    out = ra.reset_bind_pose(mesh="body")
    assert out["removed"] == ["bindPose1"] and out["bind_pose"] == "bindPose2"
    assert fake_maya.calls_to("delete")[0][0] == ("bindPose1",)
    assert fake_maya.calls_to("dagPose")[-1][1]["save"] is True
    with pytest.raises(BridgeError, match="pass mesh"):
        ra.reset_bind_pose()


# unit: anim ------------------------------------------------------------------
def test_set_keyframe_all_channels_and_value(fake_maya):
    fake_maya.responses["setKeyframe"] = 9
    out = ra.set_keyframe(["pCube1"], time=10)
    kwargs = fake_maya.calls_to("setKeyframe")[0][1]
    assert "attribute" not in kwargs and kwargs["time"] == 10.0 and kwargs["inTangentType"] == "auto"
    assert out["keys_set"] == 9 and out["attrs"] == "all keyable"
    ra.set_keyframe(["pCube1"], attrs=["translateY"], time=20, value=5.5, in_tangent="linear", out_tangent="linear")
    kwargs = fake_maya.calls_to("setKeyframe")[1][1]
    assert kwargs["attribute"] == ["translateY"] and kwargs["value"] == 5.5 and kwargs["outTangentType"] == "linear"
    with pytest.raises(BridgeError, match="value needs attrs"):
        ra.set_keyframe(["pCube1"], value=1.0)


def test_get_keyframes_and_delete_keys(fake_maya):
    fake_maya.responses["keyframe"] = lambda *a, **k: [1.0, 12.0, 24.0] if k.get("timeChange") else [0.0, 5.0, 0.0]
    out = ra.get_keyframes("pCube1", "translateY", start=1, end=24)
    assert out["times"] == [1.0, 12.0, 24.0] and out["values"] == [0.0, 5.0, 0.0] and out["count"] == 3
    assert fake_maya.calls_to("keyframe")[0][1]["time"] == (1.0, 24.0)
    fake_maya.responses["cutKey"] = 2
    out = ra.delete_keys(["pCube1"], attrs=["translateY"], start=10, end=20)
    kwargs = fake_maya.calls_to("cutKey")[0][1]
    assert kwargs["clear"] is True and kwargs["time"] == (10.0, 20.0) and out["keys_removed"] == 2


def test_time_range_and_fps(fake_maya):
    fake_maya.responses["playbackOptions"] = lambda **k: 1001.0 if k.get("query") and (k.get("minTime") or k.get("animationStartTime")) else (1100.0 if k.get("query") and (k.get("maxTime") or k.get("animationEndTime")) else (1.0 if k.get("query") else None))
    fake_maya.responses["currentUnit"] = lambda **k: "film" if k.get("query") else None
    fake_maya.responses["currentTime"] = 1050.0
    out = ra.set_time_range(1001, 1100, anim_start=1000, anim_end=1200)
    edit = fake_maya.calls_to("playbackOptions")[0][1]
    assert edit["minTime"] == 1001.0 and edit["animationEndTime"] == 1200.0
    assert out["fps"] == 24.0 and out["current"] == 1050.0
    with pytest.raises(BridgeError, match="end must be"):
        ra.set_time_range(10, 5)
    ra.set_playback_speed("realtime", fps=23.976, loop="once")
    assert fake_maya.calls_to("currentUnit")[-1][1]["time"] == "23.976fps"
    assert ra._unit_from_fps(30) == "ntsc" and ra._fps_from_unit("ntscf") == 60.0


def test_playback_and_current_time(fake_maya):
    fake_maya.responses["currentTime"] = lambda *a, **k: 5.0 if k.get("query") else None
    fake_maya.responses["play"] = lambda **k: True if k.get("query") else None
    out = ra.playback("play", forward=False)
    assert fake_maya.calls_to("play")[0][1] == {"state": True, "forward": False} and out["playing"] is True
    ra.playback("step", frames=3)
    assert ("currentTime", (8.0,), {"edit": True}) in fake_maya.calls
    ra.set_current_time(42)
    assert ("currentTime", (42.0,), {"edit": True}) in fake_maya.calls


def test_bake_uses_playback_range_and_removes_constraints(fake_maya):
    fake_maya.responses["playbackOptions"] = lambda **k: 1.0 if k.get("minTime") else 48.0
    fake_maya.responses["listRelatives"] = lambda node, **k: ["|cam|cam_parentConstraint1"] if k.get("type") == "constraint" else []
    out = ra.bake(["cam"], attrs=["translate", "rotate"], sample_by=2, remove_constraints=True)
    kwargs = fake_maya.calls_to("bakeResults")[0][1]
    assert kwargs["time"] == (1.0, 48.0) and kwargs["sampleBy"] == 2.0 and kwargs["simulation"] is True and kwargs["attribute"] == ["translate", "rotate"]
    assert out["removed_constraints"] == ["|cam|cam_parentConstraint1"]
    with pytest.raises(BridgeError, match="sample_by"):
        ra.bake(["cam"], sample_by=0)


def test_motion_path_tangents_infinity(fake_maya):
    fake_maya.responses["pathAnimation"] = "motionPath1"
    out = ra.motion_path("car", "track_crv", start=1, end=100, up_axis="y", front_axis="z", bank=True)
    kwargs = fake_maya.calls_to("pathAnimation")[0][1]
    assert kwargs["curve"] == "track_crv" and kwargs["followAxis"] == "z" and kwargs["fractionMode"] is True and kwargs["endTimeU"] == 100.0
    assert out["motion_path"] == "motionPath1"
    ra.set_tangents(["car"], in_type="linear", attrs=["translateX"], start=1, end=50, weighted=True)
    kwargs = fake_maya.calls_to("keyTangent")[0][1]
    assert kwargs["edit"] is True and kwargs["outTangentType"] == "linear" and kwargs["weightedTangents"] is True and kwargs["time"] == (1.0, 50.0)
    ra.set_infinity(["car"], pre="cycle", post="cycleRelative")
    assert fake_maya.calls_to("setInfinity")[0][1] == {"preInfinite": "cycle", "postInfinite": "cycleRelative"}
    with pytest.raises(BridgeError, match="post must be"):
        ra.set_infinity(["car"], post="loop")


def test_import_animation_fbx(fake_maya, tmp_path):
    path = tmp_path / "walk.fbx"
    path.write_bytes(b"fbx")
    mel.evaluated.clear()
    out = ra.import_animation(str(path), nodes=["rig:root"], mode="merge")
    assert any(code.startswith("FBXImport -f") for code in mel.evaluated)
    assert 'FBXImportMode -v "merge"' in mel.evaluated
    assert out["format"] == "fbx" and fake_maya.calls_to("select")[0][0] == (["rig:root"],)


def test_import_animation_missing_file_is_error(fake_maya):
    with pytest.raises(BridgeError, match="not found"):
        ra.import_animation(os.path.join(os.sep, "nope", "missing.fbx"))


def test_retarget_hint_and_list_animated(fake_maya):
    fake_maya.existing.add("mocap_hips")
    fake_maya.responses["listRelatives"] = ["a", "b", "c"]
    hint = ra.retarget_hint("mocap_hips", "char_hips")
    assert hint["source_joint_count"] == 4 and hint["target_joint_count"] is None and "HumanIK" in hint["steps"][0]
    fake_maya.existing.clear()

    fake_maya.responses["ls"] = lambda *a, **k: ["pCube1_translateX", "pCube1_rotateY", "blendIn"] if k.get("type") else ([a[0]] if a else [])
    downstream = {"pCube1_translateX.output": ["pCube1.translateX"], "pCube1_rotateY.output": ["pCube1.rotateY"], "blendIn.output": ["animBlendNodeAdditive1.inputB"], "animBlendNodeAdditive1.output": ["pSphere1.translateZ"]}
    fake_maya.responses["listConnections"] = lambda plug, **k: downstream.get(plug, [])
    fake_maya.responses["nodeType"] = lambda node, **k: "animBlendNodeAdditive" if node.startswith("animBlendNode") else "transform"
    fake_maya.responses["listAttr"] = ["output"]
    out = ra.list_animated()
    by_node = {e["node"]: e["attrs"] for e in out["animated"]}
    assert by_node == {"pCube1": ["translateX", "rotateY"], "pSphere1": ["translateZ"]}
    out = ra.list_animated(["pSphere1"])
    assert out["node_count"] == 1 and out["animated"][0]["node"] == "pSphere1"


def test_create_animation_layer(fake_maya):
    fake_maya.responses["animLayer"] = lambda *a, **k: "tweaks" if not k.get("edit") else None
    fake_maya.responses["nodeType"] = "transform"
    out = ra.create_animation_layer("tweaks", nodes=["pCube1"], attrs=["translateY"], override=True, mute=True)
    calls = fake_maya.calls_to("animLayer")
    assert calls[0][1]["override"] is True
    assert calls[1][1]["attribute"] == ["pCube1.translateY"]
    assert out["members"] == ["pCube1.translateY"] and out["mute"] is True


# integration: through the socket + tool layer ------------------------------------
async def test_tool_create_joint_chain(call_tool, fake_maya):
    _joint(fake_maya, ["a", "b"])
    data = parse(await call_tool("maya_create_joint_chain", {"params": {"positions": [[0, 0, 0], [5, 0, 0]], "names": ["a", "b"]}}))
    assert data["joints"] == ["|a", "|b"] and data["count"] == 2


async def test_tool_set_keyframe_and_time_range(call_tool, fake_maya):
    fake_maya.responses["setKeyframe"] = 3
    data = parse(await call_tool("maya_set_keyframe", {"params": {"nodes": ["pCube1"], "attrs": ["translateX", "translateY", "translateZ"], "time": 12}}))
    assert data["keys_set"] == 3 and data["time"] == 12.0
    data = parse(await call_tool("maya_get_time_range"))
    assert data["fps"] is None or isinstance(data["fps"], float)


async def test_tool_retarget_hint(call_tool):
    data = parse(await call_tool("maya_retarget_hint", {"params": {"source_root": "mocap_hips", "target_root": "hero_hips"}}))
    assert len(data["steps"]) == 7 and "hero_hips" in data["steps"][3]


async def test_tool_error_path_bad_node(call_tool, fake_maya):
    fake_maya.existing.add("pCube1")
    text = await call_tool("maya_bind_skin", {"params": {"mesh": "pCube1", "joints": ["ghost_joint"]}})
    assert text.startswith("Error") and "ghost_joint" in text


async def test_tool_rejects_unknown_param(call_tool):
    text = await call_tool("maya_set_current_time", {"params": {"frame": 5, "bogus": 1}})
    assert "Error" in text or "bogus" in text
