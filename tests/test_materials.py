"""Unit + integration tests for the materials domain."""
from __future__ import annotations

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import _util, materials
from automaya_bridge.handlers._util import BridgeError


def _shading_node_by_type(names=None):
    """shadingNode stub that returns a name based on the node type requested."""
    names = names or {}
    counter = {}

    def _make(node_type, **kwargs):
        if node_type in names:
            return names[node_type]
        counter[node_type] = counter.get(node_type, 0) + 1
        return "%s%d" % (node_type, counter[node_type])

    return _make


def _sets_stub(sg_name="testSG"):
    def _sets(*args, **kwargs):
        if kwargs.get("empty"):
            return sg_name
        if kwargs.get("query"):
            return ["pCube1"]
        return []

    return _sets


def _attr_exists(*known):
    known = set(known)

    def _query(attr, node=None, exists=False):
        return attr in known

    return _query


# unit: create / assign -----------------------------------------------------
def test_create_material_builds_shading_group(fake_maya):
    fake_maya.responses["shadingNode"] = _shading_node_by_type({"standardSurface": "redPaint"})
    fake_maya.responses["sets"] = _sets_stub("redPaintSG")
    fake_maya.responses["attributeQuery"] = _attr_exists("baseColor", "specularRoughness", "metalness")
    out = materials.create(type="standardSurface", name="redPaint", color=[0.8, 0.1, 0.1], attrs={"roughness": 0.3, "metalness": True}, assign_to=["pCube1"])
    assert out["material"] == "redPaint" and out["shading_group"] == "redPaintSG"
    create_call = [k for a, k in fake_maya.calls_to("sets") if k.get("empty")][0]
    assert create_call["renderable"] and create_call["noSurfaceShader"]
    assert (("redPaint.outColor", "redPaintSG.surfaceShader"), {"force": True}) in fake_maya.calls_to("connectAttr")
    set_calls = fake_maya.calls_to("setAttr")
    assert (("redPaint.baseColor", 0.8, 0.1, 0.1), {"type": "double3"}) in set_calls
    assert (("redPaint.specularRoughness", 0.3), {}) in set_calls
    assert (("redPaint.metalness", 1), {}) in set_calls
    assert out["attrs_set"] == ["baseColor", "specularRoughness", "metalness"]
    assign_call = [k for a, k in fake_maya.calls_to("sets") if k.get("forceElement")][0]
    assert assign_call["forceElement"] == "redPaintSG" and out["assigned"] == ["pCube1"]


def test_create_material_rejects_unknown_type(fake_maya):
    with pytest.raises(BridgeError) as exc:
        materials.create(type="toon")
    assert "standardSurface" in str(exc.value)
    assert not fake_maya.calls_to("shadingNode")


def test_create_arnold_material_requires_mtoa(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False

    def _boom(*a, **k):
        raise RuntimeError("no mtoa")

    fake_maya.responses["loadPlugin"] = _boom
    with pytest.raises(BridgeError) as exc:
        materials.create(type="aiStandardSurface")
    assert "mtoa" in str(exc.value)


def test_assign_uses_existing_shading_group(fake_maya):
    fake_maya.responses["listConnections"] = lambda plug, **k: ["blinn1SG"] if plug == "blinn1.outColor" and k.get("type") == "shadingEngine" else []
    out = materials.assign("blinn1", ["pSphere1.f[0:10]", "pCube1"])
    assert out["shading_group"] == "blinn1SG"
    assert fake_maya.calls_to("sets") == [((["pSphere1.f[0:10]", "pCube1"],), {"edit": True, "forceElement": "blinn1SG"})]


def test_assign_reports_missing_nodes(fake_maya):
    fake_maya.existing = {"blinn1"}
    with pytest.raises(BridgeError) as exc:
        materials.assign("blinn1", ["ghost"])
    assert "ghost" in str(exc.value)


# unit: set_texture --------------------------------------------------------
def test_set_texture_normal_map_inserts_bump2d(fake_maya):
    fake_maya.responses["shadingNode"] = _shading_node_by_type({"file": "nrm_file", "place2dTexture": "nrm_p2d", "bump2d": "nrm_bump"})
    fake_maya.responses["attributeQuery"] = _attr_exists("baseColor", "normalCamera", "specularRoughness")
    out = materials.set_texture("mat1", "normal", "/tex/wood_normal.png", is_normal=True, uv_tiling=[2, 3])
    assert out["attribute"] == "normalCamera" and out["kind"] == "normal" and out["color_space"] == "Raw"
    assert out["bump_node"] == "nrm_bump" and out["exists_on_disk"] is False
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    p2d_links = [a for a in connects if a[0].startswith("nrm_p2d.") and a[1].startswith("nrm_file.")]
    assert len(p2d_links) == 18
    assert ("nrm_p2d.outUV", "nrm_file.uvCoord") in connects and ("nrm_p2d.outUvFilterSize", "nrm_file.uvFilterSize") in connects
    assert ("nrm_file.outAlpha", "nrm_bump.bumpValue") in connects and ("nrm_bump.outNormal", "mat1.normalCamera") in connects
    sets = fake_maya.calls_to("setAttr")
    assert (("nrm_bump.bumpInterp", 1), {}) in sets
    assert (("nrm_file.colorSpace", "Raw"), {"type": "string"}) in sets
    assert (("nrm_file.fileTextureName", "/tex/wood_normal.png"), {"type": "string"}) in sets
    assert (("nrm_p2d.repeatU", 2.0), {}) in sets and (("nrm_p2d.repeatV", 3.0), {}) in sets


def test_set_texture_color_and_scalar_maps(fake_maya):
    fake_maya.responses["shadingNode"] = _shading_node_by_type()
    fake_maya.responses["attributeQuery"] = _attr_exists("color", "specularRoughness")
    out = materials.set_texture("lambert2", "baseColor", "/tex/albedo.jpg")
    assert out["attribute"] == "color" and out["kind"] == "color" and out["color_space"] == "sRGB"
    assert (("file1.outColor", "lambert2.color"), {"force": True}) in fake_maya.calls_to("connectAttr")
    out = materials.set_texture("lambert2", "roughness", "/tex/rough.png")
    assert out["kind"] == "scalar" and out["color_space"] == "Raw"
    assert (("file2.outAlpha", "lambert2.specularRoughness"), {"force": True}) in fake_maya.calls_to("connectAttr")
    assert (("file2.alphaIsLuminance", 1), {}) in fake_maya.calls_to("setAttr")


def test_set_texture_unknown_attribute(fake_maya):
    fake_maya.responses["attributeQuery"] = _attr_exists("color")
    with pytest.raises(BridgeError) as exc:
        materials.set_texture("lambert2", "glitter", "/tex/x.png")
    assert "glitter" in str(exc.value) and not fake_maya.calls_to("shadingNode")


# unit: pbr network / convert ----------------------------------------------
def test_create_pbr_network_with_displacement_and_ao(fake_maya):
    fake_maya.responses["shadingNode"] = _shading_node_by_type({"standardSurface": "wood", "displacementShader": "wood_disp", "multiplyDivide": "wood_ao"})
    fake_maya.responses["sets"] = _sets_stub("woodSG")
    fake_maya.responses["attributeQuery"] = _attr_exists("baseColor", "specularRoughness", "metalness", "normalCamera", "opacity", "emissionColor")
    out = materials.create_pbr_network(
        name="wood", base_color="/t/bc.png", roughness="/t/r.png", metalness="/t/m.png", normal="/t/n.png",
        displacement="/t/d.exr", ao="/t/ao.png", emission="/t/e.png", assign_to=["pPlane1"],
    )
    assert out["material"] == "wood" and set(out["maps"]) == {"baseColor", "roughness", "metalness", "normal", "displacement", "ao", "emission"}
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    assert ("wood_disp.displacement", "woodSG.displacementShader") in connects
    assert ("wood_ao.output", "wood.baseColor") in connects
    assert any(a[1] == "wood_ao.input1" for a in connects) and any(a[1] == "wood_ao.input2" for a in connects)
    assert (("wood.emission", 1.0), {}) in fake_maya.calls_to("setAttr")
    assert sorted(out["missing_on_disk"]) == sorted(out["maps"])


def test_convert_blinn_to_standard_surface(fake_maya):
    fake_maya.responses["nodeType"] = lambda n, **k: "blinn" if n == "blinn1" else "file"
    fake_maya.responses["shadingNode"] = _shading_node_by_type({"standardSurface": "blinn1_standardSurface"})

    def _get(plug, **k):
        return {
            "blinn1.color": [(0.5, 0.2, 0.1)], "blinn1.eccentricity": 0.25, "blinn1.specularRollOff": 0.7,
            "blinn1.transparency": [(0.5, 0.5, 0.5)], "blinn1.incandescence": [(0.0, 0.0, 0.0)], "blinn1.specularColor": [(1.0, 1.0, 1.0)],
        }.get(plug)

    fake_maya.responses["getAttr"] = _get

    def _conn(plug, **k):
        if k.get("type") == "shadingEngine":
            return ["blinn1SG"]
        if plug == "blinn1.normalCamera" and k.get("plugs"):
            return ["bump1.outNormal"]
        return []

    fake_maya.responses["listConnections"] = _conn
    out = materials.convert("blinn1", "standardSurface")
    assert out["material"] == "blinn1_standardSurface" and out["from_type"] == "blinn"
    assert out["mapped"]["baseColor"] == [0.5, 0.2, 0.1]
    assert out["mapped"]["normalCamera"] == "bump1.outNormal"
    assert out["mapped"]["opacity"] == [0.5, 0.5, 0.5]
    assert out["mapped"]["specularRoughness"] == pytest.approx(0.5)
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    assert ("bump1.outNormal", "blinn1_standardSurface.normalCamera") in connects
    assert ("blinn1_standardSurface.outColor", "blinn1SG.surfaceShader") in connects
    assert fake_maya.calls_to("delete") == [(("blinn1",), {})]


def test_convert_rejects_non_legacy(fake_maya):
    fake_maya.responses["nodeType"] = lambda n, **k: "aiFlat"
    with pytest.raises(BridgeError):
        materials.convert("flat1")


# unit: textures -----------------------------------------------------------
def test_list_and_repath_textures(fake_maya, tmp_path):
    real = tmp_path / "ok.png"
    real.write_bytes(b"x")
    paths = {"file1": str(real), "file2": "C:/old/missing.png"}
    fake_maya.responses["ls"] = lambda *a, **k: ["file1", "file2"] if k.get("type") == "file" else []
    fake_maya.responses["getAttr"] = lambda plug, **k: paths.get(plug.split(".")[0]) if plug.endswith("fileTextureName") else "sRGB"
    out = materials.list_textures()
    assert out["count"] == 2 and out["missing"] == ["file2"]
    assert materials.list_textures(missing_only=True)["count"] == 1
    out = materials.repath_textures("C:/old", str(tmp_path), dry_run=True)
    assert out["changed_count"] == 1 and out["changed"][0]["node"] == "file2" and not fake_maya.calls_to("setAttr")
    out = materials.repath_textures("C:/old", str(tmp_path))
    assert fake_maya.calls_to("setAttr") == [(("file2.fileTextureName", str(tmp_path) + "/missing.png"), {"type": "string"})]


def test_texture_exists_handles_udim(tmp_path):
    (tmp_path / "wood_1001.png").write_bytes(b"x")
    assert materials._texture_exists(str(tmp_path / "wood_<UDIM>.png"))
    assert not materials._texture_exists(str(tmp_path / "stone_<UDIM>.png"))
    assert not materials._texture_exists("")


def test_set_attr_value_type_handling(fake_maya):
    _util.set_attr_value("n", "s", "text")
    _util.set_attr_value("n", "b", False)
    _util.set_attr_value("n", "v", (1, 2, 3))
    assert fake_maya.calls_to("setAttr") == [
        (("n.s", "text"), {"type": "string"}),
        (("n.b", 0), {}),
        (("n.v", 1.0, 2.0, 3.0), {"type": "double3"}),
    ]
    with pytest.raises(BridgeError):
        _util.set_attr_value("n", "v", [1, 2, 3, 4, 5])


# integration: through the socket -------------------------------------------
async def test_tool_create_and_list_materials(call_tool, fake_maya):
    fake_maya.responses["shadingNode"] = _shading_node_by_type({"lambert": "previsGrey"})
    fake_maya.responses["sets"] = _sets_stub("previsGreySG")
    data = parse(await call_tool("maya_create_material", {"params": {"type": "lambert", "name": "previsGrey", "color": [0.5, 0.5, 0.5], "assign_to": ["pCube1"]}}))
    assert data["material"] == "previsGrey" and data["assigned"] == ["pCube1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["lambert1", "previsGrey"] if k.get("materials") else []
    fake_maya.responses["nodeType"] = lambda n, **k: "lambert"
    fake_maya.responses["listConnections"] = lambda plug, **k: ["previsGreySG"] if plug.startswith("previsGrey.outColor") else []
    data = parse(await call_tool("maya_list_materials", {"params": {}}))
    assert data["count"] == 1 and data["materials"][0]["name"] == "previsGrey" and data["materials"][0]["assigned"] == ["pCube1"]
    data = parse(await call_tool("maya_list_materials", {"params": {"include_defaults": True}}))
    assert data["count"] == 2


async def test_tool_get_material(call_tool, fake_maya):
    fake_maya.responses["listAttr"] = lambda n, **k: ["baseColor", "specularRoughness"]
    fake_maya.responses["getAttr"] = lambda plug, **k: [(1.0, 0.0, 0.0)] if plug.endswith("baseColor") else 0.4
    fake_maya.responses["listConnections"] = lambda *a, **k: ["mat.baseColor", "file7.outColor"] if k.get("connections") else []
    fake_maya.responses["listHistory"] = lambda n, **k: ["mat", "file7"]
    fake_maya.responses["nodeType"] = lambda n, **k: "file" if n == "file7" else "standardSurface"
    data = parse(await call_tool("maya_get_material", {"params": {"material": "mat"}}))
    assert data["attrs"] == {"baseColor": [1.0, 0.0, 0.0], "specularRoughness": 0.4}
    assert data["inputs"][0]["attribute"] == "baseColor" and data["inputs"][0]["source_type"] == "file"
    assert data["textures"][0]["node"] == "file7"


async def test_tool_error_path_missing_material(call_tool, fake_maya):
    fake_maya.existing = {"lambert1"}
    text = await call_tool("maya_set_material_attrs", {"params": {"material": "nope", "attrs": {"color": [1, 0, 0]}}})
    assert text.startswith("Error") and "nope" in text


async def test_tool_rejects_bad_shader_type(call_tool):
    text = await call_tool("maya_create_material", {"params": {"type": "toon"}})
    assert "Error" in text or "validation" in text.lower()
