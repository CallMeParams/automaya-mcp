"""Unit + integration tests for the previs domain."""
from __future__ import annotations

import base64
import math

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import previs
from automaya_bridge.handlers._util import BridgeError

# A valid 1x1 transparent PNG (67 bytes).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

CAM_ATTRS = {
    "focalLength": 35.0,
    "horizontalFilmAperture": 36.0 / 25.4,
    "verticalFilmAperture": 20.25 / 25.4,
    "centerOfInterest": 100.0,
    "nearClipDistance": 1.0,
    "farClipDistance": 100000.0,
    "depthOfField": 0,
    "fStop": 5.6,
    "focusDistance": 500.0,
    "overscan": 1.3,
    "displayResolution": 1,
    "displayFilmGate": 0,
    "displaySafeAction": 1,
    "displaySafeTitle": 0,
    "filmFit": 1,
    "renderable": 1,
}


def _camera(fake_maya, name="shotCam", attrs=None, position=(0.0, 150.0, 500.0)):
    """Make the stub describe ``name`` as a camera transform with a camera shape."""
    values = dict(CAM_ATTRS)
    values.update(attrs or {})
    shape = "|%s|%sShape" % (name, name)

    def node_type(node, **k):
        if k.get("isType"):
            return k["isType"] == "transform" and not node.endswith("Shape")
        return "camera" if node.endswith("Shape") else "transform"

    def list_relatives(node, **k):
        if k.get("shapes") and node.split("|")[-1] == name and k.get("type") in (None, "camera"):
            return [shape]
        if k.get("parent") and node == shape:
            return ["|" + name]
        return []

    def get_attr(plug, **k):
        attr = plug.split(".")[-1]
        if attr in values:
            return values[attr]
        return [(0.0, 0.0, 0.0)]

    def xform(node, **k):
        if k.get("query"):
            if k.get("translation") or k.get("rotatePivot"):
                return list(position)
            if k.get("rotation"):
                return [0.0, 0.0, 0.0]
            if k.get("matrix"):
                return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, position[0], position[1], position[2], 1]
        return None

    fake_maya.responses["camera"] = lambda *a, **k: [k.get("name", name), k.get("name", name) + "Shape"] if not k.get("query") else False
    fake_maya.responses["nodeType"] = node_type
    fake_maya.responses["objectType"] = node_type
    fake_maya.responses["listRelatives"] = list_relatives
    fake_maya.responses["getAttr"] = get_attr
    fake_maya.responses["xform"] = xform
    fake_maya.responses["attributeQuery"] = False
    fake_maya.responses["ls"] = lambda *a, **k: ([shape] if k.get("type") == "camera" else ([a[0]] if a and isinstance(a[0], str) else []))
    return shape


# unit ----------------------------------------------------------------------------
def test_create_camera_converts_sensor_to_inches(fake_maya):
    _camera(fake_maya)
    out = previs.create_camera("shotCam", focal_length=35, sensor_width=36, aspect=16 / 9, translate=[0, 150, 500], aim=[0, 100, 0], display_safe_title=True)
    kwargs = fake_maya.calls_to("camera")[0][1]
    assert math.isclose(kwargs["horizontalFilmAperture"], 36 / 25.4)
    assert math.isclose(kwargs["verticalFilmAperture"], (36 / (16 / 9)) / 25.4)
    assert kwargs["filmFit"] == "horizontal" and kwargs["focalLength"] == 35.0
    assert ("setAttr", ("shotCamShape.displaySafeTitle", 1), {}) in fake_maya.calls
    place = fake_maya.calls_to("viewPlace")[0][1]
    assert place["eye"] == (0.0, 150.0, 500.0) and place["lookAt"] == (0.0, 100.0, 0.0)
    assert out["camera"] == "shotCam" and math.isclose(out["fov"]["horizontal"], 54.432, abs_tol=0.01)
    assert out["sensor_mm"]["width"] == 36.0


def test_create_camera_rejects_bad_clip(fake_maya):
    with pytest.raises(BridgeError, match="clip planes"):
        previs.create_camera("c", near_clip=10, far_clip=5)
    with pytest.raises(BridgeError, match="film_fit"):
        previs.create_camera("c", film_fit="diagonal")


def test_camera_info_fov_and_aim(fake_maya):
    _camera(fake_maya, attrs={"focalLength": 50.0, "horizontalFilmAperture": 24.89 / 25.4, "verticalFilmAperture": 14.0 / 25.4})
    info = previs.camera_info("shotCam")
    expected_h = math.degrees(2 * math.atan(24.89 / (2 * 50.0)))
    assert math.isclose(info["fov"]["horizontal"], round(expected_h, 3), abs_tol=0.001)
    assert info["sensor_mm"] == {"width": 24.89, "height": 14.0, "aspect": round(24.89 / 14.0, 4)}
    assert info["aim"] == [0.0, 150.0, 400.0] and info["forward"] == [-0.0, -0.0, -1.0]
    assert info["film_fit"] == "horizontal" and info["dof"]["f_stop"] == 5.6 and info["shots"] == []


def test_camera_info_rejects_non_camera(fake_maya):
    _camera(fake_maya)
    with pytest.raises(BridgeError, match="not a camera"):
        previs.camera_info("pCube1")


def test_set_lens_from_fov(fake_maya):
    _camera(fake_maya)
    previs.set_lens("shotCam", field_of_view=54.432, f_stop=2.8, focus_distance=300, depth_of_field=True)
    focal = [c for c in fake_maya.calls_to("setAttr") if c[0][0].endswith(".focalLength")][0][0][1]
    assert math.isclose(focal, 35.0, abs_tol=0.01)
    assert ("setAttr", ("|shotCam|shotCamShape.depthOfField", 1), {}) in fake_maya.calls
    assert ("setAttr", ("|shotCam|shotCamShape.fStop", 2.8), {}) in fake_maya.calls
    with pytest.raises(BridgeError, match="not both"):
        previs.set_lens("shotCam", focal_length=50, field_of_view=40)


def test_shot_camera_rig_crane_structure(fake_maya):
    _camera(fake_maya, name="hero_cam")
    fake_maya.responses["group"] = lambda *a, **k: k["name"]
    out = previs.create_shot_camera_rig("hero", rig_type="crane", translate=[0, 200, 600], arm_length=400)
    nodes = out["nodes"]
    assert nodes["shot_ctrl"] == "hero_shot_ctrl" and nodes["crane_base"] == "hero_crane_base" and nodes["head"] == "hero_head"
    groups = {c[1]["name"]: c[1].get("parent") for c in fake_maya.calls_to("group")}
    assert groups["hero_dolly"] == "hero_shot_ctrl" and groups["hero_crane_base"] == "hero_dolly" and groups["hero_arm"] == "hero_crane_base" and groups["hero_head"] == "hero_arm"
    assert ("parent", ("hero_cam", "hero_head"), {}) in fake_maya.calls
    head_xform = [c for c in fake_maya.calls_to("xform") if c[0] == ("hero_head",) and c[1].get("translation")][0]
    assert head_xform[1]["translation"] == [400.0, 0.0, 0.0]
    locked = [c[0][0] for c in fake_maya.calls_to("setAttr") if c[1].get("lock")]
    assert "hero_arm.rotateX" in locked and "hero_arm.rotateZ" not in locked and "hero_head.rotateY" not in locked


def test_shot_camera_rig_aim_uses_aim_constraint(fake_maya):
    _camera(fake_maya, name="sh010_cam")
    fake_maya.responses["group"] = lambda *a, **k: k["name"]
    fake_maya.responses["spaceLocator"] = lambda *a, **k: [k["name"]]
    fake_maya.responses["aimConstraint"] = ["sh010_cam_aimConstraint1"]
    out = previs.create_shot_camera_rig("sh010", rig_type="aim", aim=[0, 100, 0])
    kwargs = fake_maya.calls_to("aimConstraint")[0][1]
    assert kwargs["aimVector"] == (0, 0, -1) and kwargs["worldUpObject"] == "sh010_up"
    assert out["nodes"]["aim"] == "sh010_aim" and out["nodes"]["aim_constraint"] == "sh010_cam_aimConstraint1"
    with pytest.raises(BridgeError, match="rig_type"):
        previs.create_shot_camera_rig("x", rig_type="steadicam")


def test_list_cameras_skips_defaults(fake_maya):
    cams = ["|persp|perspShape", "|shotCam|shotCamShape"]
    fake_maya.responses["ls"] = lambda *a, **k: cams if k.get("type") == "camera" else []
    fake_maya.responses["listRelatives"] = lambda node, **k: [node.rsplit("|", 1)[0]] if k.get("parent") else []
    fake_maya.responses["objectType"] = lambda node, **k: False
    fake_maya.responses["camera"] = lambda node, **k: node.startswith("|persp")
    fake_maya.responses["getAttr"] = 35.0
    out = previs.list_cameras()
    assert [c["camera"] for c in out["cameras"]] == ["|shotCam"]
    out = previs.list_cameras(include_default=True)
    assert out["count"] == 2 and out["cameras"][0]["default"] is True


def test_playblast_single_frame_returns_png(fake_maya, tmp_path):
    _camera(fake_maya)
    fake_maya.responses["about"] = lambda **k: False if k.get("batch") else "stub"
    fake_maya.responses["getPanel"] = lambda **k: ["modelPanel4"] if k.get("visiblePanels") else ("modelPanel" if k.get("typeOf") else None)

    def fake_playblast(**kwargs):
        with open(kwargs["completeFilename"], "wb") as fh:
            fh.write(PNG_1X1)
        return kwargs["completeFilename"]

    fake_maya.responses["playblast"] = fake_playblast
    target = str(tmp_path / "check.png")
    out = previs.playblast(camera="shotCam", frame=1012, width=960, height=540, filename=target)
    kwargs = fake_maya.calls_to("playblast")[0][1]
    assert kwargs["format"] == "image" and kwargs["compression"] == "png" and kwargs["frame"] == [1012.0]
    assert kwargs["viewer"] is False and kwargs["offScreen"] is True and kwargs["widthHeight"] == (960, 540) and kwargs["forceOverwrite"] is True
    assert kwargs["editorPanelName"] == "modelPanel4"
    assert fake_maya.calls_to("modelPanel")[0][1]["camera"] == "shotCam"
    assert base64.b64decode(out["image_base64"]) == PNG_1X1 and out["format"] == "png" and out["path"] == target


def test_playblast_range_returns_path(fake_maya, tmp_path):
    fake_maya.responses["playblast"] = str(tmp_path / "blast.mov")
    fake_maya.responses["playbackOptions"] = lambda **k: 1001.0 if k.get("minTime") else 1048.0
    out = previs.playblast(format="qt", width=1280, height=720, filename=str(tmp_path / "blast"))
    kwargs = fake_maya.calls_to("playblast")[0][1]
    assert kwargs["startTime"] == 1001.0 and kwargs["endTime"] == 1048.0 and kwargs["compression"] == "H.264" and kwargs["format"] == "qt"
    assert out["path"].endswith("blast.mov") and out["start"] == 1001.0


def test_playblast_needs_viewport(fake_maya):
    _camera(fake_maya)
    fake_maya.responses["getPanel"] = None
    with pytest.raises(BridgeError, match="model panel"):
        previs.playblast(camera="shotCam", frame=1)


def test_viewport_settings_and_resolution(fake_maya):
    fake_maya.responses["getPanel"] = lambda **k: "modelPanel4" if k.get("withFocus") else ("modelPanel" if k.get("typeOf") else None)
    out = previs.viewport_settings(display_mode="smoothShaded", textures=True, aa=True, ao=False, hide=["joints", "locators"])
    kwargs = fake_maya.calls_to("modelEditor")[0][1]
    assert kwargs["displayAppearance"] == "smoothShaded" and kwargs["displayTextures"] is True and kwargs["joints"] is False
    assert ("setAttr", ("hardwareRenderingGlobals.multiSampleEnable", 1), {}) in fake_maya.calls
    assert ("setAttr", ("hardwareRenderingGlobals.ssaoEnable", 0), {}) in fake_maya.calls
    assert out["panel"] == "modelPanel4"
    res = previs.set_resolution(2048, 858)
    assert ("setAttr", ("defaultResolution.width", 2048), {}) in fake_maya.calls
    assert math.isclose(res["device_aspect"], 2048 / 858, rel_tol=1e-5)


def test_sequence_shot_and_listing(fake_maya):
    _camera(fake_maya)
    shots = {"sh010": {"startTime": 1001.0, "endTime": 1048.0, "sequenceStartTime": 1.0, "sequenceEndTime": 48.0, "currentCamera": "shotCam", "track": 1, "shotName": "sh010", "scale": 1.0}}

    def shot(name, **k):
        if k.get("query"):
            flag = [f for f in k if f != "query"][0]
            return shots[name].get(flag)
        return name

    fake_maya.responses["shot"] = shot
    out = previs.create_sequence_shot("sh010", 1001, 1048, sequence_start=1, camera="shotCam", track=1)
    kwargs = fake_maya.calls_to("shot")[0][1]
    assert kwargs["sequenceEndTime"] == 48.0 and kwargs["currentCamera"] == "shotCam" and kwargs["track"] == 1
    assert out["duration"] == 48.0
    fake_maya.responses["ls"] = lambda *a, **k: ["sh010"] if k.get("type") == "shot" else []
    fake_maya.responses["sequenceManager"] = lambda **k: 12.0 if k.get("currentTime") else ["sh010"]
    info = previs.camera_sequencer_info()
    assert info["sequence_range"] == [1.0, 48.0] and info["shot_details"][0]["camera"] == "shotCam"
    assert previs.camera_info("shotCam")["shots"] == ["sh010"]


def test_image_plane_locator_measure(fake_maya, tmp_path):
    shape = _camera(fake_maya)
    plate = tmp_path / "plate.jpg"
    plate.write_bytes(b"jpg")
    fake_maya.responses["imagePlane"] = ["imagePlane1", "imagePlaneShape1"]
    out = previs.add_image_plane("shotCam", str(plate), depth=250, alpha=0.5, fit="horizontal", offset=[0.1, 0])
    kwargs = fake_maya.calls_to("imagePlane")[0][1]
    assert kwargs["camera"] == shape and kwargs["fileName"].endswith("plate.jpg")
    assert ("setAttr", ("imagePlaneShape1.fit", 2), {}) in fake_maya.calls
    assert ("setAttr", ("imagePlaneShape1.alphaGain", 0.5), {}) in fake_maya.calls
    assert out["depth"] == 250.0
    with pytest.raises(BridgeError, match="not found"):
        previs.add_image_plane("shotCam", str(tmp_path / "missing.jpg"))

    fake_maya.responses["spaceLocator"] = ["mark_A"]
    loc = previs.create_locator("mark_A", pos=[10, 0, 20])
    assert loc["locator"] == "mark_A" and fake_maya.calls_to("xform")[-1][1]["translation"] == [10.0, 0.0, 20.0]
    dist = previs.measure_distance([0, 0, 0], [3, 4, 0])
    assert dist["distance"] == 5.0 and dist["unit"] == "cm"


def test_set_camera_key_and_notes(fake_maya):
    _camera(fake_maya)
    out = previs.set_camera_key("shotCam", 1010, translate=[1, 2, 3], focal_length=85, tangent="linear")
    keys = fake_maya.calls_to("setKeyframe")
    assert len(keys) == 4 and keys[0][1] == {"attribute": "translateX", "time": 1010.0, "value": 1.0, "inTangentType": "linear", "outTangentType": "linear"}
    assert keys[3][0] == ("|shotCam|shotCamShape",) and keys[3][1]["attribute"] == "focalLength"
    assert out["keyed"][-1] == "|shotCam|shotCamShape.focalLength"
    with pytest.raises(BridgeError, match="nothing to key"):
        previs.set_camera_key("shotCam", 1)

    previs.shot_notes("shotCam", "wide establishing, slow push in")
    assert fake_maya.calls_to("addAttr")[0][1] == {"longName": "notes", "dataType": "string"}
    assert ("setAttr", ("shotCam.notes", "wide establishing, slow push in"), {"type": "string"}) in fake_maya.calls


def test_turntable_orbits_camera(fake_maya):
    _camera(fake_maya, name="hero_turntableCam")
    fake_maya.responses["exactWorldBoundingBox"] = [-50, 0, -50, 50, 180, 50]
    fake_maya.responses["group"] = lambda *a, **k: k["name"]
    fake_maya.responses["playbackOptions"] = 1001.0
    out = previs.create_turntable("hero", frames=120)
    assert out["center"] == [0.0, 90.0, 0.0] and out["radius"] == 450.0 and out["start"] == 1001.0 and out["end"] == 1120.0
    keys = fake_maya.calls_to("setKeyframe")
    assert keys[0][1]["value"] == 0.0 and keys[1][1]["value"] == 360.0 and keys[1][1]["time"] == 1121.0 and keys[1][1]["outTangentType"] == "linear"
    assert fake_maya.calls_to("setInfinity")[0][1]["postInfinite"] == "cycle"
    assert ("parent", ("hero_turntableCam", "hero_turntable"), {}) in fake_maya.calls


def test_setup_scene_for_previs(fake_maya):
    out = previs.setup_scene_for_previs(fps=24, units="cm", start=1001, end=1100, aspect=2.39, width=2048)
    units = [c[1] for c in fake_maya.calls_to("currentUnit")]
    assert {"linear": "cm"} in units and any(u.get("time") == "film" for u in units)
    assert ("setAttr", ("defaultResolution.height", 857), {}) in fake_maya.calls
    assert out["resolution"]["width"] == 2048 and out["playback"] == "realtime"
    with pytest.raises(BridgeError, match="units must be"):
        previs.setup_scene_for_previs(units="parsec")


# integration -----------------------------------------------------------------------
async def test_tool_create_camera(call_tool, fake_maya):
    _camera(fake_maya)
    data = parse(await call_tool("maya_create_camera", {"params": {"name": "shotCam", "focal_length": 35, "sensor_width": 36, "sensor_height": 20.25}}))
    assert data["camera"] == "shotCam" and data["sensor_mm"]["width"] == 36.0 and data["fov"]["horizontal"] > 50


async def test_tool_playblast_returns_image(call_tool, fake_maya, tmp_path):
    _camera(fake_maya)
    fake_maya.responses["about"] = lambda **k: False if k.get("batch") else "stub"
    fake_maya.responses["getPanel"] = lambda **k: ["modelPanel4"] if k.get("visiblePanels") else ("modelPanel" if k.get("typeOf") else None)

    def fake_playblast(**kwargs):
        with open(kwargs["completeFilename"], "wb") as fh:
            fh.write(PNG_1X1)
        return kwargs["completeFilename"]

    fake_maya.responses["playblast"] = fake_playblast
    text = await call_tool("maya_playblast", {"params": {"camera": "shotCam", "frame": 5, "filename": str(tmp_path / "f.png")}})
    assert text.startswith("<image ") and '"frame": 5.0' in text and '"format": "png"' in text


async def test_tool_measure_distance(call_tool):
    data = parse(await call_tool("maya_measure_distance", {"params": {"a": [0, 0, 0], "b": [0, 3, 4]}}))
    assert data["distance"] == 5.0


async def test_tool_error_path_missing_camera(call_tool, fake_maya):
    fake_maya.existing.add("pCube1")
    text = await call_tool("maya_camera_info", {"params": {"camera": "ghostCam"}})
    assert text.startswith("Error") and "ghostCam" in text and "not found" in text


async def test_tool_validation_rejects_bad_resolution(call_tool):
    text = await call_tool("maya_set_resolution", {"params": {"width": 0, "height": 1080}})
    assert "Error" in text or "greater" in text
