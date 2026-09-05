"""Unit + integration tests for the craft_lookdev domain (material science, lookdev.* handlers, maya_lookdev_* tools)."""
from __future__ import annotations

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import _science as sci
from automaya_bridge.handlers import lookdev
from automaya_bridge.handlers._util import BridgeError


def _no_mtoa(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False

    def _boom(*a, **k):
        raise RuntimeError("Plug-in, 'mtoa', was not found on MAYA_PLUG_IN_PATH")

    fake_maya.responses["loadPlugin"] = _boom


def _shading_stub(fake_maya):
    fake_maya.responses["shadingNode"] = lambda t, **k: k["name"]
    fake_maya.responses["objectType"] = lambda n, **k: (not n.endswith("Shape")) if k.get("isType") == "transform" else "transform"
    fake_maya.responses["listRelatives"] = lambda n, **k: ["|%s|%sShape" % (n.lstrip("|"), n.lstrip("|"))] if k.get("shapes") and not n.endswith("Shape") else []


def _set_calls(fake_maya):
    return {a[0]: (a[1:] if len(a) > 2 else a[1]) for a, k in fake_maya.calls_to("setAttr")}


def _connects(fake_maya):
    return [a for a, k in fake_maya.calls_to("connectAttr")]


# science: material table ------------------------------------------------------
def test_measured_material_table_and_aliases():
    steel = sci.measured_material("steel")
    assert steel["metalness"] == 1.0 and steel["baseColor"] == [0.56, 0.57, 0.58] and "Lagarde" in steel["notes"]
    assert sci.measured_material("Aluminum")["name"] == "aluminium" and sci.measured_material("oak")["name"] == "wood_oak"
    glass = sci.measured_material("glass")
    assert glass["transmission"] == 1.0 and glass["ior"] == 1.52
    for name in ("concrete", "asphalt", "brick", "plaster", "wood_oak", "wood_walnut", "wood_pine", "painted_metal", "steel", "aluminium", "copper", "gold", "glass", "water", "rubber", "leather", "fabric", "skin", "snow", "sand", "grass"):
        spec = sci.MEASURED_MATERIALS[name]
        assert len(spec["baseColor"]) == 3 and all(0.0 <= v <= 1.0 for v in spec["baseColor"]) and 0.0 <= spec["roughness"] <= 1.0 and "notes" in spec
    with pytest.raises(KeyError) as exc:
        sci.measured_material("unobtainium")
    assert "concrete" in str(exc.value)


def test_material_issues_rules():
    assert sci.material_issues("aiStandardSurface", [0.5, 0.5, 0.5], 0.5, 0.0, 1.5) == []
    assert sci.material_issues("aiStandardSurface", [0.56, 0.57, 0.58], 0.4, 1.0, 1.5) == []
    assert any("above 0.9" in i for i in sci.material_issues("aiStandardSurface", [0.95, 0.95, 0.95], 0.5, 0.0, 1.5))
    assert any("nearly black" in i for i in sci.material_issues("aiStandardSurface", [0.0, 0.0, 0.0], 0.5, 0.0, 1.5))
    assert any("dark baseColor" in i for i in sci.material_issues("aiStandardSurface", [0.02, 0.02, 0.02], 0.5, 1.0, 1.5))
    assert any("mirror" in i for i in sci.material_issues("aiStandardSurface", [0.5, 0.5, 0.5], 0.0, 0.0, 1.5))
    assert sci.material_issues("aiStandardSurface", [1.0, 1.0, 1.0], 0.0, 0.0, 1.5, transmission=1.0) == []  # glass is allowed to be perfect
    assert any("IOR" in i for i in sci.material_issues("aiStandardSurface", [0.5, 0.5, 0.5], 0.5, 0.0, 3.2))
    assert any("transmit" in i for i in sci.material_issues("aiStandardSurface", [0.9, 0.9, 0.9], 0.3, 1.0, 1.5, transmission=0.5))


def test_hsv_roundtrip():
    for rgb in ([0.262, 0.095, 0.061], [0.1, 0.8, 0.3], [0.5, 0.5, 0.5]):
        assert sci.hsv_to_rgb(sci.rgb_to_hsv(rgb)) == pytest.approx(rgb, abs=1e-4)


# handlers: measured material ------------------------------------------------
def test_measured_material_arnold_with_breakup_and_triplanar(fake_maya):
    _shading_stub(fake_maya)
    fake_maya.responses["polyEvaluate"] = lambda *a, **k: 0  # no UVs
    out = lookdev.measured_material("brick", assign_to=["wall"], breakup=0.4, breakup_scale=25.0)
    assert out["path"] == "arnold" and out["type"] == "aiStandardSurface" and out["material"] == "brick_mat" and out["preset"] == "brick"
    assert out["triplanar"] is True and out["assigned"] == ["wall"]
    sets = _set_calls(fake_maya)
    assert sets["brick_mat.baseColor"] == (0.262, 0.095, 0.061) and sets["brick_mat.specularRoughness"] == 0.9 and sets["brick_mat.metalness"] == 0.0
    assert sets["brick_mat.specularIOR"] == 1.5 and sets["brick_mat.base"] == 1.0
    assert sets["brick_mat_noise.coordSpace"] == 2 and sets["brick_mat_noise.scale"] == (25.0, 25.0, 25.0)
    connects = _connects(fake_maya)
    assert ("brick_mat_noise.outColor", "brick_mat_triplanar.input") in connects
    assert ("brick_mat_triplanar.outColor", "brick_mat.baseColor") in connects
    assert ("brick_mat_roughNoise.outColorR", "brick_mat.specularRoughness") in connects
    assert (("brick_mat.outColor", "brick_matSG.surfaceShader"), {"force": True}) in fake_maya.calls_to("connectAttr")
    assert fake_maya.calls_to("sets")[-1] == ((["wall"],), {"edit": True, "forceElement": "brick_matSG"})


def test_measured_material_skips_triplanar_when_uvs_exist_and_handles_glass(fake_maya):
    _shading_stub(fake_maya)
    fake_maya.responses["polyEvaluate"] = lambda *a, **k: 24
    out = lookdev.measured_material("glass", assign_to=["pane"], breakup=0.2, cell_noise=True)
    assert out["triplanar"] is False and "triplanar" not in out["utility_nodes"] and out["utility_nodes"]["noise"] == "glass_mat_cellNoise"
    sets = _set_calls(fake_maya)
    assert sets["glass_mat.base"] == 0.0 and sets["glass_mat.transmission"] == 1.0 and sets["glass_mat.specularIOR"] == 1.52
    assert ("glass_mat_cellNoise.outColor", "glass_mat.baseColor") in _connects(fake_maya)
    out = lookdev.measured_material("fabric", material_name="cloth", triplanar="off", color_override=[0.2, 0.1, 0.4])
    assert out["values"]["baseColor"] == [0.2, 0.1, 0.4] and _set_calls(fake_maya)["cloth.sheen"] == 0.5


def test_measured_material_maya_fallback_and_unknown(fake_maya):
    _shading_stub(fake_maya)
    _no_mtoa(fake_maya)
    out = lookdev.measured_material("concrete", breakup=0.5, triplanar="on")
    assert out["path"] == "maya" and out["type"] == "standardSurface" and out["triplanar"] is False
    assert any("aiNoise" in w for w in out["warnings"]) and out["utility_nodes"] == {}
    assert fake_maya.calls_to("shadingNode")[0][0] == ("standardSurface",)
    with pytest.raises(BridgeError) as exc:
        lookdev.measured_material("kryptonite")
    assert "concrete" in str(exc.value)
    with pytest.raises(BridgeError):
        lookdev.measured_material("concrete", triplanar="maybe")


# handlers: variation and wear -------------------------------------------------
def test_material_variation_is_deterministic_and_round_robin(fake_maya):
    _shading_stub(fake_maya)
    fake_maya.responses["nodeType"] = lambda n, **k: "aiStandardSurface"
    fake_maya.responses["getAttr"] = lambda plug, **k: [(0.262, 0.095, 0.061)] if plug.endswith("baseColor") else (0.9 if plug.endswith("specularRoughness") else 1.0)
    out = lookdev.material_variation("brick_mat", count=3, hue_jitter=10, value_jitter=0.2, roughness_jitter=0.1, seed=7, assign_to=["a", "b", "c", "d"])
    assert out["count"] == 3 and [v["material"] for v in out["variants"]] == ["brick_mat_var01", "brick_mat_var02", "brick_mat_var03"]
    assert out["variants"][0]["assigned"] == ["a", "d"] and out["variants"][1]["assigned"] == ["b"]
    colours = [v["baseColor"] for v in out["variants"]]
    assert len({tuple(c) for c in colours}) == 3 and all(0.8 <= v["roughness"] <= 1.0 for v in out["variants"])
    assert all(c[0] > c[1] > c[2] for c in colours)  # still brick coloured
    again = lookdev.material_variation("brick_mat", count=3, hue_jitter=10, value_jitter=0.2, roughness_jitter=0.1, seed=7)
    assert [v["baseColor"] for v in again["variants"]] == colours
    assert _set_calls(fake_maya)["brick_mat_var01.metalness"] == 1.0  # copied scalar attrs
    fake_maya.responses["nodeType"] = lambda n, **k: "lambert"
    with pytest.raises(BridgeError):
        lookdev.material_variation("lambert2")


def test_wear_layers_edges_and_dirt(fake_maya):
    _shading_stub(fake_maya)
    fake_maya.responses["listConnections"] = lambda *a, **k: ["steelSG"]
    fake_maya.responses["getAttr"] = lambda plug, **k: [(0.56, 0.57, 0.58)] if plug.endswith("baseColor") else 1.0
    out = lookdev.wear("steel", edge_amount=0.6, dirt_amount=0.3, dirt_distance=15)
    assert out["top_type"] == "aiLayerShader" and out["top_shader"] == "steel_wearLayers" and [layer["kind"] for layer in out["layers"]] == ["edge", "dirt"]
    connects = _connects(fake_maya)
    assert ("steel.outColor", "steel_wearLayers.input1") in connects
    assert ("steel_edgeShader.outColor", "steel_wearLayers.input2") in connects and ("steel_edgeMask.outColorR", "steel_wearLayers.mix2") in connects
    assert ("steel_dirtShader.outColor", "steel_wearLayers.input3") in connects and ("steel_dirtMask.outColorR", "steel_wearLayers.mix3") in connects
    assert ("steel_wearLayers.outColor", "steelSG.surfaceShader") in connects
    sets = _set_calls(fake_maya)
    assert sets["steel_edgeShader.metalness"] == 1.0 and sets["steel_dirtMask.farClip"] == 15.0 and sets["steel_dirtMask.black"] == (0.3, 0.3, 0.3)
    assert sets["steel_wearLayers.enable3"] == 1 and sets["steel_wearLayers.name3"] == "dirt"


def test_wear_single_mask_uses_mix_shader_and_errors(fake_maya):
    _shading_stub(fake_maya)
    fake_maya.responses["getAttr"] = lambda plug, **k: [(0.5, 0.5, 0.5)] if plug.endswith("baseColor") else 0.0
    out = lookdev.wear("paint", edge_amount=0.0, dirt_amount=0.8)
    assert out["top_type"] == "aiMixShader" and out["layers"][0]["kind"] == "dirt"
    connects = _connects(fake_maya)
    assert ("paint.outColor", "paint_wearMix.shader1") in connects and ("paint_dirtShader.outColor", "paint_wearMix.shader2") in connects and ("paint_dirtMask.outColorR", "paint_wearMix.mix") in connects
    assert out["shading_groups"] == ["paint_wearMixSG"] and any("no shading group" in w for w in out["warnings"])
    with pytest.raises(BridgeError):
        lookdev.wear("paint", edge_amount=0, dirt_amount=0)
    _no_mtoa(fake_maya)
    with pytest.raises(BridgeError) as exc:
        lookdev.wear("paint")
    assert "needs Arnold" in str(exc.value)


# handlers: colour management and presets ---------------------------------------
def test_color_management_aces13(fake_maya, monkeypatch):
    monkeypatch.setenv("MAYA_LOCATION", "/opt/maya2024")
    out = lookdev.color_management("aces13")
    assert out["config_path"] == "/opt/maya2024/resources/OCIO-configs/Maya2022-default/config.ocio" and out["config_exists"] is False
    assert out["view_transform"] == "ACES 1.0 SDR-video (sRGB)" and out["rendering_space"] == "ACEScg"
    calls = [k for a, k in fake_maya.calls_to("colorManagementPrefs")]
    assert {"edit": True, "cmEnabled": True} in calls and {"edit": True, "configFilePath": out["config_path"]} in calls
    assert {"edit": True, "viewTransform": "ACES 1.0 SDR-video (sRGB)"} in calls and {"edit": True, "renderingSpaceName": "ACEScg"} in calls
    assert any("not found on disk" in w for w in out["warnings"])
    with pytest.raises(BridgeError):
        lookdev.color_management("rec2020")


def test_color_management_aces2_falls_back(fake_maya):
    def _prefs(**k):
        if k.get("viewTransform", "").startswith("ACES 2.0"):
            raise RuntimeError("Unknown view transform")
        return None

    fake_maya.responses["colorManagementPrefs"] = _prefs
    out = lookdev.color_management("aces2")
    assert out["view_transform"] == "ACES 1.0 SDR-video (sRGB)" and any("unavailable" in w for w in out["warnings"])
    with pytest.raises(BridgeError):
        lookdev.color_management("aces2", view_transform="ACES 2.0 HDR")


def test_render_preset_production_sets_sampling_and_aovs(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: []
    out = lookdev.render_preset("production", width=1920, height=1080)
    assert out["quality"] == "production" and out["aovs"] == ["diffuse", "specular", "N", "Z"]
    applied = out["applied"]
    assert applied["defaultArnoldRenderOptions.AASamples"] == 5 and applied["defaultArnoldRenderOptions.enableAdaptiveSampling"] is True
    assert applied["denoiser"] == "oidn" and applied["defaultArnoldDriver.aiTranslator"] == "exr" and applied["defaultResolution.width"] == 1920
    assert out["lock_sampling_pattern"] is True
    preview = lookdev.render_preset("preview")
    assert preview["applied"]["defaultArnoldRenderOptions.AASamples"] == 3 and preview["aovs"] == [] and preview["applied"]["denoiser"] == "none"
    with pytest.raises(BridgeError):
        lookdev.render_preset("ultra")
    _no_mtoa(fake_maya)
    with pytest.raises(BridgeError):
        lookdev.render_preset("final")


def test_material_report_flags_bad_values(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: ["white", "chrome", "old", "lambert1"] if k.get("materials") else []
    fake_maya.responses["nodeType"] = lambda n, **k: {"white": "aiStandardSurface", "chrome": "aiStandardSurface", "old": "blinn", "lambert1": "lambert"}[n]

    def _get(plug, **k):
        node, attr = plug.split(".")
        table = {
            "white": {"baseColor": [(0.95, 0.95, 0.95)], "specularRoughness": 0.0, "metalness": 0.0, "specularIOR": 1.5, "transmission": 0.0},
            "chrome": {"baseColor": [(0.02, 0.02, 0.02)], "specularRoughness": 0.2, "metalness": 1.0, "specularIOR": 1.5, "transmission": 0.0},
            "old": {"color": [(0.5, 0.5, 0.5)]},
            "lambert1": {"color": [(0.5, 0.5, 0.5)]},
        }
        return table[node][attr]

    fake_maya.responses["getAttr"] = _get
    out = lookdev.material_report()
    assert out["count"] == 3 and out["flagged_count"] == 3
    by_name = {m["material"]: m for m in out["flagged"]}
    assert any("above 0.9" in i for i in by_name["white"]["issues"]) and any("mirror" in i for i in by_name["white"]["issues"])
    assert any("dark baseColor" in i for i in by_name["chrome"]["issues"])
    assert any("legacy blinn" in i for i in by_name["old"]["issues"])
    assert lookdev.material_report(include_defaults=True)["count"] == 4


def test_material_library_command(fake_maya):
    out = lookdev.material_library()
    assert out["count"] == len(sci.MEASURED_MATERIALS) and "steel" in out["names"] and out["aliases"]["oak"] == "wood_oak"


# integration: through the socket ---------------------------------------------
async def test_tool_measured_material(call_tool, fake_maya):
    _shading_stub(fake_maya)
    data = parse(await call_tool("maya_lookdev_material", {"params": {"name": "copper", "assign_to": ["pipe"], "breakup": 0.2, "triplanar": "on"}}))
    assert data["material"] == "copper_mat" and data["values"]["metalness"] == 1.0 and data["triplanar"] is True and data["assigned"] == ["pipe"]


async def test_tool_report_and_preset(call_tool, fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: []
    data = parse(await call_tool("maya_lookdev_report", {"params": {}}))
    assert data == {"count": 0, "flagged_count": 0, "flagged": [], "materials": []}
    data = parse(await call_tool("maya_lookdev_render_preset", {"params": {"quality": "final"}}))
    assert "crypto_object" in data["aovs"] and data["applied"]["defaultArnoldRenderOptions.AASamples"] == 8


async def test_tool_library_offline(call_tool):
    data = parse(await call_tool("maya_lookdev_library", {"params": {"name": "gold"}}))
    assert data["name"] == "gold" and data["metalness"] == 1.0
    data = parse(await call_tool("maya_lookdev_library", {"params": {}}))
    assert data["count"] >= 21
    assert "Error" in await call_tool("maya_lookdev_library", {"params": {"name": "vibranium"}})


async def test_tool_error_paths(call_tool, fake_maya):
    _no_mtoa(fake_maya)
    text = await call_tool("maya_lookdev_wear", {"params": {"material": "steel"}})
    assert text.startswith("Error") and "needs Arnold" in text
    text = await call_tool("maya_lookdev_material", {"params": {"name": "kryptonite"}})
    assert "Error" in text or "validation" in text.lower()
