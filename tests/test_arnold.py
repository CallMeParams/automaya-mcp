"""Unit + integration tests for the arnold domain."""
from __future__ import annotations

import os

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import arnold
from automaya_bridge.handlers._util import BridgeError


def _no_mtoa(fake_maya):
    """Make mtoa look missing and unloadable."""
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False

    def _boom(*a, **k):
        raise RuntimeError("Plug-in, 'mtoa', was not found on MAYA_PLUG_IN_PATH")

    fake_maya.responses["loadPlugin"] = _boom


def _dag_stub(fake_maya):
    """Shapes end with 'Shape' and live under a transform of the same base name."""
    fake_maya.responses["objectType"] = lambda n, **k: (not n.endswith("Shape")) if k.get("isType") == "transform" else "transform"
    fake_maya.responses["nodeType"] = lambda n, **k: "mesh" if n.endswith("Shape") else "transform"

    def _rel(n, **k):
        if k.get("parent"):
            return [n.rsplit("|", 1)[0] if "|" in n else "|" + n[:-5]] if n.endswith("Shape") else []
        if k.get("shapes"):
            return [] if n.endswith("Shape") else ["%s|%sShape" % (n if n.startswith("|") else "|" + n, n.lstrip("|"))]
        return []

    fake_maya.responses["listRelatives"] = _rel


def _set_calls(fake_maya):
    return {a[0]: (a[1:] if len(a) > 2 else a[1]) for a, k in fake_maya.calls_to("setAttr")}


# unit: status and the plugin guard -----------------------------------------
def test_status_reports_loaded_and_version(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: True if k.get("loaded") else "5.3.4.1"
    fake_maya.responses["getAttr"] = lambda plug, **k: "arnold"
    out = arnold.status()
    assert out["loaded"] is True and out["mtoa_version"] == "5.3.4.1" and out["renderer_is_arnold"] is True


def test_status_when_missing_does_not_raise(fake_maya):
    _no_mtoa(fake_maya)
    out = arnold.status()
    assert out["loaded"] is False and "Plug-in Manager" in out["hint"]


@pytest.mark.parametrize("call", [
    lambda: arnold.create_light(type="area"),
    lambda: arnold.set_render_settings(camera_aa=3),
    lambda: arnold.get_render_settings(),
    lambda: arnold.render_frame(),
    lambda: arnold.render_sequence(start=1, end=2),
    lambda: arnold.create_aov("diffuse"),
    lambda: arnold.list_aovs(),
    lambda: arnold.set_ai_attributes(["pCube1"], opaque=False),
])
def test_commands_raise_without_mtoa(fake_maya, call):
    _no_mtoa(fake_maya)
    with pytest.raises(BridgeError) as exc:
        call()
    assert "mtoa" in str(exc.value) and "Plug-in Manager" in str(exc.value)


# unit: lights --------------------------------------------------------------
def test_create_area_light_sets_attrs_and_renderer(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["shadingNode"] = lambda t, **k: k["name"]
    out = arnold.create_light(type="area", name="keyLight", intensity=10, exposure=2.5, color=[1, 0.9, 0.8], translate=[0, 5, 5], rotate=[-45, 0, 0], scale=[2, 2, 2], samples=3, cast_shadows=True)
    assert out["node_type"] == "aiAreaLight" and out["warnings"] == []
    assert out["shape"].endswith("keyLightShape") and out["transform"].endswith("keyLight")
    sets = _set_calls(fake_maya)
    assert sets["defaultRenderGlobals.currentRenderer"] == "arnold"
    shape = out["shape"]
    assert sets[shape + ".intensity"] == 10.0 and sets[shape + ".exposure"] == 2.5
    assert sets[shape + ".color"] == (1.0, 0.9, 0.8) and sets[shape + ".aiSamples"] == 3 and sets[shape + ".aiCastShadows"] == 1
    assert sets[out["transform"] + ".translate"] == (0.0, 5.0, 5.0)
    assert (("aiAreaLight",), {"asLight": True, "name": "keyLightShape"}) in fake_maya.calls_to("shadingNode")


def test_create_skydome_with_hdri(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["shadingNode"] = lambda t, **k: k["name"]
    out = arnold.create_light(type="skydome", name="env", hdri_path="/hdr/studio.exr", exposure=-1)
    assert out["hdri_file_node"] == "env_hdri_file" and out["hdri_exists_on_disk"] is False
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    assert ("env_hdri_file.outColor", out["shape"] + ".color") in connects
    assert len([a for a in connects if a[0].startswith("env_hdri_place2d.")]) == 18
    sets = _set_calls(fake_maya)
    assert sets["env_hdri_file.colorSpace"] == "Raw" and sets["env_hdri_file.fileTextureName"] == "/hdr/studio.exr"


def test_create_maya_light_uses_ai_exposure(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["nodeType"] = lambda n, **k: "spotLight" if n.endswith("Shape") else "transform"
    fake_maya.responses["spotLight"] = lambda **k: "|" + k["name"] + "|" + k["name"] + "Shape"
    out = arnold.create_light(type="spot", name="rim", exposure=1, cast_shadows=False)
    sets = _set_calls(fake_maya)
    assert out["node_type"] == "spotLight"
    assert sets[out["shape"] + ".aiExposure"] == 1.0 and sets[out["shape"] + ".useRayTraceShadows"] == 0


def test_mesh_light_needs_mesh_and_wires_in_mesh(fake_maya):
    _dag_stub(fake_maya)
    with pytest.raises(BridgeError) as exc:
        arnold.create_light(type="mesh")
    assert "mesh" in str(exc.value)
    out = arnold.create_light(type="mesh", name="glow", mesh="lamp")
    assert out["mesh_shape"] == "|lamp|lampShape"
    assert (("|lamp|lampShape.outMesh", "glowShape.inMesh"), {"force": True}) in fake_maya.calls_to("connectAttr")
    create = fake_maya.calls_to("createNode")[0]
    assert create[0] == ("aiMeshLight",) and create[1]["parent"] == "|lamp"


def test_unknown_light_type(fake_maya):
    with pytest.raises(BridgeError):
        arnold.create_light(type="laser")


def test_list_lights(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["ls"] = lambda *a, **k: ["|env|envShape"] if k.get("type") == "aiSkyDomeLight" else []
    fake_maya.responses["nodeType"] = lambda n, **k: "aiSkyDomeLight"
    fake_maya.responses["listConnections"] = lambda *a, **k: ["hdriFile"]
    fake_maya.responses["getAttr"] = lambda plug, **k: "/hdr/x.exr" if plug.endswith("fileTextureName") else 1.0
    out = arnold.list_lights()
    assert out["count"] == 1 and out["lights"][0]["hdri"] == "/hdr/x.exr" and out["lights"][0]["transform"] == "|env"


# unit: render settings -----------------------------------------------------
def test_set_render_settings_writes_globals(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: ["|persp|perspShape", "|shotCam|shotCamShape"] if k.get("type") == "camera" else []
    _dag_stub(fake_maya)
    fake_maya.responses["nodeType"] = lambda n, **k: "camera" if n.endswith("Shape") else "transform"
    out = arnold.set_render_settings(camera_aa=5, diffuse=2, specular=2, transmission=1, sss=1, volume=0, adaptive=True, max_aa=10, threshold=0.02,
                                     width=1920, height=1080, start_frame=1, end_frame=24, image_format="exr", output_prefix="out/<Scene>", motion_blur=True, camera="shotCam", denoiser="optix")
    sets = _set_calls(fake_maya)
    assert sets["defaultArnoldRenderOptions.AASamples"] == 5 and sets["defaultArnoldRenderOptions.GIDiffuseSamples"] == 2
    assert sets["defaultArnoldRenderOptions.enableAdaptiveSampling"] == 1 and sets["defaultArnoldRenderOptions.AASamplesMax"] == 10
    assert sets["defaultArnoldRenderOptions.AAAdaptiveThreshold"] == 0.02 and sets["defaultArnoldRenderOptions.motion_blur_enable"] == 1
    assert sets["defaultArnoldRenderOptions.denoiseBeauty"] == 1
    assert sets["defaultResolution.width"] == 1920 and sets["defaultResolution.height"] == 1080
    assert sets["defaultResolution.deviceAspectRatio"] == pytest.approx(1920 / 1080)
    assert sets["defaultRenderGlobals.startFrame"] == 1.0 and sets["defaultRenderGlobals.endFrame"] == 24.0 and sets["defaultRenderGlobals.animation"] == 1
    assert sets["defaultArnoldDriver.aiTranslator"] == "exr" and sets["defaultRenderGlobals.imageFilePrefix"] == "out/<Scene>"
    assert sets["|shotCam|shotCamShape.renderable"] == 1 and sets["|persp|perspShape.renderable"] == 0
    assert out["applied"]["denoiser"] == "optix" and out["applied"]["renderable_camera"] == "|shotCam|shotCamShape"
    assert "settings" in out and out["warnings"] == []


def test_set_render_settings_oidn_imager(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: []
    arnold.set_render_settings(denoiser="oidn")
    assert fake_maya.calls_to("createNode")[0][0] == ("aiImagerDenoiserOidn",)
    assert (("aiImagerDenoiserOidn1.message", "defaultArnoldRenderOptions.imagers[0]"), {"force": True}) in fake_maya.calls_to("connectAttr")


def test_set_render_settings_bad_format(fake_maya):
    with pytest.raises(BridgeError):
        arnold.set_render_settings(image_format="bmp")


def test_get_render_settings_shape(fake_maya):
    fake_maya.responses["getAttr"] = lambda plug, **k: 3
    out = arnold.get_render_settings()
    assert out["sampling"]["camera_aa"] == 3 and out["resolution"] == {"width": 3, "height": 3} and "images_dir" in out


# unit: rendering -----------------------------------------------------------
def _workspace(tmp_path):
    def _ws(*a, **k):
        if k.get("rootDirectory"):
            return str(tmp_path)
        if k.get("fileRuleEntry"):
            return "images"
        return None

    return _ws


def test_render_frame_returns_png(fake_maya, tmp_path):
    fake_maya.responses["workspace"] = _workspace(tmp_path)
    images = tmp_path / "images"

    def _render(**kwargs):
        images.mkdir(exist_ok=True)
        (images / "shot_0001.png").write_bytes(b"\x89PNG fake")
        assert kwargs["seq"] == "5" and kwargs["camera"] == "perspShape"

    fake_maya.responses["arnoldRender"] = _render
    fake_maya.responses["ls"] = lambda *a, **k: []
    fake_maya.responses["getAttr"] = lambda plug, **k: 640
    out = arnold.render_frame(frame=5, width=640, height=480, output_path=str(tmp_path / "out" / "frame.png"))
    assert out["format"] == "png" and out["image_base64"] and out["path"].endswith("frame.png")
    assert os.path.isfile(out["path"]) and out["frame"] == 5
    assert (("5.0",), {"edit": True}) not in fake_maya.calls_to("currentTime")
    assert fake_maya.calls_to("currentTime")[0] == ((5.0,), {"edit": True})


def test_render_frame_no_output_notes_it(fake_maya, tmp_path):
    fake_maya.responses["workspace"] = _workspace(tmp_path)
    fake_maya.responses["ls"] = lambda *a, **k: []
    fake_maya.responses["arnoldRender"] = lambda **k: None
    out = arnold.render_frame()
    assert out["path"] is None and "no new file" in out["note"] and "image_base64" not in out


def test_render_frame_falls_back_to_mel(fake_maya, tmp_path):
    from maya import mel

    fake_maya.responses["workspace"] = _workspace(tmp_path)
    fake_maya.responses["ls"] = lambda *a, **k: []

    def _old(**k):
        raise TypeError("Invalid flag 'seq'")

    fake_maya.responses["arnoldRender"] = _old
    mel.evaluated.clear()
    fake_maya.responses["nodeType"] = lambda n, **k: "camera"
    arnold.render_frame(camera="perspShape", frame=2)
    assert mel.evaluated[-1] == 'arnoldRender -seq "2" -cam "perspShape"'


def test_render_sequence_collects_paths(fake_maya, tmp_path):
    fake_maya.responses["workspace"] = _workspace(tmp_path)
    images = tmp_path / "images"

    def _render(**kwargs):
        images.mkdir(exist_ok=True)
        assert kwargs["seq"] == "1-3"
        for f in (1, 2, 3):
            (images / ("seq_%04d.exr" % f)).write_bytes(b"exr")

    fake_maya.responses["arnoldRender"] = _render
    out = arnold.render_sequence(start=1, end=3)
    assert out["frame_count"] == 3 and all(p.endswith(".exr") for p in out["paths"])
    sets = _set_calls(fake_maya)
    assert sets["defaultRenderGlobals.startFrame"] == 1.0 and sets["defaultRenderGlobals.endFrame"] == 3.0
    with pytest.raises(BridgeError):
        arnold.render_sequence(start=5, end=1)


# unit: AOVs and shape attributes --------------------------------------------
def test_create_aov_manual_fallback(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: []
    out = arnold.create_aov("diffuse")
    assert out["node"] == "aiAOV_diffuse" and out["data_type"] == "rgb" and out["via"] == "manual"
    sets = _set_calls(fake_maya)
    assert sets["aiAOV_diffuse.name"] == "diffuse" and sets["aiAOV_diffuse.type"] == 5 and sets["aiAOV_diffuse.enabled"] == 1
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    assert ("aiAOV_diffuse.message", "defaultArnoldRenderOptions.aovList[0]") in connects
    assert ("defaultArnoldDriver.message", "aiAOV_diffuse.outputs[0].driver") in connects
    assert ("defaultArnoldFilter.message", "aiAOV_diffuse.outputs[0].filter") in connects
    out = arnold.create_aov("Z")
    assert out["data_type"] == "float" and _set_calls(fake_maya)["aiAOV_Z.type"] == 4
    out = arnold.create_aov("crypto_object")
    assert "exr" in out["note"] and _set_calls(fake_maya)["defaultArnoldDriver.aiTranslator"] == "exr"


def test_create_aov_reuses_existing_and_validates_type(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: ["aiAOV_N"] if k.get("type") == "aiAOV" else []
    fake_maya.responses["getAttr"] = lambda plug, **k: "N" if plug.endswith(".name") else 7
    out = arnold.create_aov("N", enabled=False)
    assert out["existing"] is True and not fake_maya.calls_to("createNode")
    assert arnold.list_aovs() == {"count": 1, "aovs": [{"node": "aiAOV_N", "name": "N", "data_type": "vector", "enabled": 7}]}
    with pytest.raises(BridgeError):
        arnold.create_aov("weird", data_type="matrix")


def test_set_ai_attributes_on_shapes(fake_maya):
    _dag_stub(fake_maya)
    out = arnold.set_ai_attributes(["pCube1"], subdivision_type="catclark", subdivision_iterations=2, opaque=False, visible_in_diffuse_reflection=False, displacement_height=0.5)
    sets = _set_calls(fake_maya)
    shape = "|pCube1|pCube1Shape"
    assert sets[shape + ".aiSubdivType"] == 1 and sets[shape + ".aiSubdivIterations"] == 2 and sets[shape + ".aiOpaque"] == 0
    assert sets[shape + ".aiVisibleInDiffuseReflection"] == 0 and sets[shape + ".aiDispHeight"] == 0.5
    assert out["shapes"][0]["shape"] == shape and len(out["shapes"][0]["set"]) == 5
    with pytest.raises(BridgeError):
        arnold.set_ai_attributes(["pCube1"], glow=True)
    with pytest.raises(BridgeError):
        arnold.set_ai_attributes(["pCube1"], subdivision_type="loop")


# integration: through the socket -------------------------------------------
async def test_tool_arnold_status(call_tool, fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: True if k.get("loaded") else "5.3.4.1"
    data = parse(await call_tool("maya_arnold_status"))
    assert data["loaded"] is True and data["mtoa_version"] == "5.3.4.1"


async def test_tool_create_light_and_settings(call_tool, fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["shadingNode"] = lambda t, **k: k["name"]
    data = parse(await call_tool("maya_create_arnold_light", {"params": {"type": "area", "name": "fill", "exposure": 3}}))
    assert data["node_type"] == "aiAreaLight" and data["shape"].endswith("fillShape")
    data = parse(await call_tool("maya_set_render_settings", {"params": {"camera_aa": 4, "image_format": "png"}}))
    assert data["applied"]["defaultArnoldRenderOptions.AASamples"] == 4 and data["applied"]["defaultArnoldDriver.aiTranslator"] == "png"
    data = parse(await call_tool("maya_set_arnold_attributes", {"params": {"nodes": ["pCube1"], "subdivision_type": "catclark", "subdivision_iterations": 1}}))
    assert data["values"] == {"aiSubdivType": 1, "aiSubdivIterations": 1}


async def test_tool_render_frame_returns_image(call_tool, fake_maya, tmp_path):
    fake_maya.responses["workspace"] = _workspace(tmp_path)
    fake_maya.responses["ls"] = lambda *a, **k: []

    def _render(**kwargs):
        (tmp_path / "images").mkdir(exist_ok=True)
        (tmp_path / "images" / "beauty.png").write_bytes(b"\x89PNG" + b"\x00" * 16)

    fake_maya.responses["arnoldRender"] = _render
    text = await call_tool("maya_render_frame", {"params": {"frame": 1}})
    assert "<image" in text and "beauty.png" in text


async def test_tool_error_path_missing_mtoa(call_tool, fake_maya):
    _no_mtoa(fake_maya)
    text = await call_tool("maya_create_aov", {"params": {"name": "diffuse"}})
    assert text.startswith("Error") and "mtoa" in text and "Plug-in Manager" in text


async def test_tool_rejects_bad_denoiser(call_tool):
    text = await call_tool("maya_set_render_settings", {"params": {"denoiser": "magic"}})
    assert "Error" in text or "validation" in text.lower()
