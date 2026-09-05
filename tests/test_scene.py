"""Unit + integration tests for the scene domain."""
from __future__ import annotations

import os

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import scene
from automaya_bridge.handlers._util import BridgeError


def _ls(fake_maya, mapping=None, selection=None, defaults=None):
    """Install an ls stub that knows long names, selection and default nodes."""
    mapping = mapping or {}
    selection = selection if selection is not None else []
    defaults = defaults or []

    def ls(*args, **kwargs):
        if kwargs.get("selection"):
            return list(selection)
        if kwargs.get("defaultNodes"):
            return list(defaults)
        if args:
            name = args[0]
            if isinstance(name, (list, tuple)):
                return [mapping.get(n, n) for n in name]
            if "*" in name:
                return [v for k, v in mapping.items() if k.startswith(name.rstrip("*"))]
            return [mapping[name]] if name in mapping else []
        if kwargs.get("type"):
            return [v for v in mapping.values()]
        return list(mapping.values()) + list(defaults)

    fake_maya.responses["ls"] = ls


# unit: files ---------------------------------------------------------------
def test_new_refuses_unsaved_then_forces(fake_maya):
    fake_maya.responses["file"] = lambda *a, **k: True if k.get("modified") else ""
    with pytest.raises(BridgeError):
        scene.new()
    out = scene.new(force=True)
    assert out["scene"] == "untitled"
    assert any(k.get("new") and k.get("force") for _, k in fake_maya.calls_to("file"))


def test_open_missing_file(fake_maya, tmp_path):
    with pytest.raises(BridgeError):
        scene.open_scene(str(tmp_path / "nope.ma"))


def test_open_existing(fake_maya, tmp_path):
    path = tmp_path / "shot.ma"
    path.write_text("//Maya ASCII")
    fake_maya.responses["file"] = lambda *a, **k: str(path) if k.get("sceneName") else ""
    out = scene.open_scene(str(path))
    assert out["scene"] == str(path)
    (args, kwargs), = [c for c in fake_maya.calls_to("file") if c[1].get("open")]
    assert args[0] == str(path) and kwargs["force"] is True


def test_save_untitled_requires_path(fake_maya):
    with pytest.raises(BridgeError):
        scene.save()


def test_save_as_picks_type(fake_maya, tmp_path):
    target = str(tmp_path / "out.mb")
    out = scene.save(path=target)
    assert out["type"] == "mayaBinary"
    assert any(k.get("rename") == target for _, k in fake_maya.calls_to("file"))
    assert any(k.get("save") and k["type"] == "mayaBinary" for _, k in fake_maya.calls_to("file"))
    with pytest.raises(BridgeError):
        scene.save(path=str(tmp_path / "out.fbx"))


def test_save_in_place_ascii(fake_maya):
    fake_maya.responses["file"] = lambda *a, **k: "/x/y.ma" if k.get("sceneName") else "/x/y.ma"
    out = scene.save()
    assert out["type"] == "mayaAscii" and out["path"] == "/x/y.ma"


def test_import_file_uses_util(fake_maya, tmp_path):
    path = tmp_path / "thing.obj"
    path.write_text("o thing")
    fake_maya.responses["ls"] = lambda *a, **k: ["|thing"] if fake_maya.calls_to("file") else []
    out = scene.import_file(str(path), group_name="props")
    assert out["path"] == str(path)
    assert any(k.get("i") and k.get("type") == "OBJ" for _, k in fake_maya.calls_to("file"))
    assert fake_maya.calls_to("group")


def test_import_missing(fake_maya):
    with pytest.raises(BridgeError):
        scene.import_file("/no/such.fbx")


def test_export_infers_format_and_restores_selection(fake_maya, tmp_path):
    _ls(fake_maya, {"pCube1": "|pCube1"}, selection=["|persp"])
    target = str(tmp_path / "sub" / "hero.obj")
    out = scene.export(target, nodes=["pCube1"])
    assert out["format"] == "obj" and out["nodes"] == ["pCube1"]
    assert os.path.isdir(os.path.dirname(target))
    assert any(k.get("exportSelected") and k.get("type") == "OBJexport" for _, k in fake_maya.calls_to("file"))
    assert fake_maya.calls_to("select")[-1][0][0] == ["|persp"]


def test_export_bad_format(fake_maya, tmp_path):
    _ls(fake_maya, {"pCube1": "|pCube1"})
    with pytest.raises(BridgeError):
        scene.export(str(tmp_path / "x.xyz"), nodes=["pCube1"])


# unit: queries -------------------------------------------------------------
def test_get_info_counts_and_fps(fake_maya):
    def ls(*a, **k):
        if k.get("type") == "mesh":
            return ["a", "b"]
        if k.get("selection"):
            return ["|a"]
        return []

    fake_maya.responses["ls"] = ls
    fake_maya.responses["currentUnit"] = lambda **k: "film" if k.get("time") else "cm"
    fake_maya.responses["playbackOptions"] = lambda **k: 1.0 if k.get("minTime") else 120.0
    info = scene.get_info()
    assert info["counts"]["meshes"] == 2 and info["counts"]["cameras"] == 0
    assert info["fps"] == 24.0 and info["units"]["time"] == "film"
    assert info["selection"] == ["|a"] and info["scene"] == "untitled"
    assert info["frame_range"]["start"] == 1.0


def test_list_nodes_pagination_and_defaults(fake_maya):
    names = {"n%d" % i: "|n%d" % i for i in range(7)}
    _ls(fake_maya, names, defaults=["|persp"])
    page = scene.list_nodes(limit=3, offset=3)
    assert page["total"] == 7 and page["nodes"] == ["|n3", "|n4", "|n5"] and page["has_more"] is True
    last = scene.list_nodes(limit=3, offset=6)
    assert last["nodes"] == ["|n6"] and last["has_more"] is False
    assert "|persp" not in scene.list_nodes()["nodes"]
    assert "|persp" in scene.list_nodes(include_defaults=True)["nodes"]


def test_list_nodes_pattern_and_type(fake_maya):
    _ls(fake_maya, {"pCube1": "|pCube1", "pSphere1": "|pSphere1"})
    out = scene.list_nodes(pattern="pCube*", type="transform")
    assert out["nodes"] == ["|pCube1"]
    args, kwargs = fake_maya.calls_to("ls")[0]
    assert args == ("pCube*",) and kwargs["type"] == "transform" and kwargs["long"] is True


def test_get_node_info(fake_maya):
    _ls(fake_maya, {"pCube1": "|grp|pCube1"})
    fake_maya.responses["listRelatives"] = lambda *a, **k: ["|grp"] if k.get("parent") else (["|grp|pCube1|pCubeShape1"] if k.get("shapes") else ["|grp|pCube1|child"])
    fake_maya.responses["listConnections"] = lambda *a, **k: ["lambert1"] if a[0].endswith(".surfaceShader") else (["blinn1SG"] if k.get("type") == "shadingEngine" else ["x.out", "y.out"])
    fake_maya.responses["listAttr"] = ["myAttr"]
    fake_maya.responses["getAttr"] = lambda plug, **k: 5 if plug.endswith("myAttr") else [(1.0, 2.0, 3.0)]
    info = scene.get_node_info("pCube1", attributes=["translate"])
    assert info["name"] == "|grp|pCube1" and info["short_name"] == "pCube1"
    assert info["parent"] == "|grp" and info["children"] == ["|grp|pCube1|child"]
    assert info["materials"] == ["lambert1"] and info["incoming_connections"] == 2
    assert info["custom_attrs"] == {"myAttr": 5}
    assert info["attributes"]["translate"] == [1.0, 2.0, 3.0]


def test_get_node_info_missing(fake_maya):
    fake_maya.existing = {"pCube1"}
    with pytest.raises(BridgeError) as exc:
        scene.get_node_info("nope")
    assert "not found" in str(exc.value)


# unit: selection and hierarchy --------------------------------------------
def test_select_replace_add_clear(fake_maya):
    _ls(fake_maya, {"a": "|a"}, selection=["|a"])
    assert scene.select(["a"])["selection"] == ["|a"]
    assert fake_maya.calls_to("select")[-1][1] == {"replace": True}
    scene.select(["a"], add=True)
    assert fake_maya.calls_to("select")[-1][1] == {"add": True}
    assert scene.select(clear=True)["selection"] == []
    with pytest.raises(BridgeError):
        scene.select()


def test_delete_uses_selection(fake_maya):
    _ls(fake_maya, selection=["|a", "|b"])
    assert scene.delete()["deleted"] == ["|a", "|b"]
    assert fake_maya.calls_to("delete")[0][0][0] == ["|a", "|b"]


def test_delete_nothing_selected(fake_maya):
    _ls(fake_maya, selection=[])
    with pytest.raises(BridgeError):
        scene.delete()


def test_rename(fake_maya):
    fake_maya.responses["rename"] = "hero_geo"
    _ls(fake_maya, {"hero_geo": "|hero_geo"})
    assert scene.rename("pCube1", "hero_geo")["name"] == "|hero_geo"
    with pytest.raises(BridgeError):
        scene.rename("pCube1", "  ")


def test_parent_and_world(fake_maya):
    fake_maya.responses["parent"] = ["|grp|a"]
    _ls(fake_maya, {"|grp|a": "|grp|a", "a": "|a", "grp": "|grp"})
    out = scene.parent(["a"], "grp")
    assert out["nodes"] == ["|grp|a"] and out["parent"] == "|grp"
    assert fake_maya.calls_to("parent")[0][0] == (["a"], "grp")
    fake_maya.responses["parent"] = ["|a"]
    out = scene.parent(["a"])
    assert out["parent"] is None and fake_maya.calls_to("parent")[-1][1] == {"world": True}


def test_group(fake_maya):
    fake_maya.responses["group"] = "props_grp"
    _ls(fake_maya, {"props_grp": "|props_grp", "a": "|a"})
    out = scene.group(["a"], name="props_grp")
    assert out["group"] == "|props_grp" and out["members"] == ["|a"]
    assert fake_maya.calls_to("group")[0][1] == {"name": "props_grp"}
    scene.group()
    assert fake_maya.calls_to("group")[-1][1] == {"empty": True}


# unit: attributes ---------------------------------------------------------
def _attr_stub(fake_maya, attr_type, value):
    def get_attr(plug, **k):
        if k.get("type"):
            return attr_type
        if k.get("lock"):
            return False
        return value

    fake_maya.responses["getAttr"] = get_attr
    fake_maya.responses["connectionInfo"] = False


def test_set_attr_float_and_readback(fake_maya):
    _attr_stub(fake_maya, "doubleLinear", 2.5)
    out = scene.set_attr("pCube1", "translateY", 2.5)
    assert out["value"] == 2.5
    assert fake_maya.calls_to("setAttr")[0] == (("pCube1.translateY", 2.5), {})


def test_set_attr_bool_string_enum_vector(fake_maya):
    _attr_stub(fake_maya, "bool", True)
    scene.set_attr("pCube1", "visibility", "off")
    assert fake_maya.calls_to("setAttr")[-1][0] == ("pCube1.visibility", False)
    _attr_stub(fake_maya, "string", "hi")
    scene.set_attr("file1", "fileTextureName", "hi")
    assert fake_maya.calls_to("setAttr")[-1] == (("file1.fileTextureName", "hi"), {"type": "string"})
    _attr_stub(fake_maya, "enum", 2)
    fake_maya.responses["attributeQuery"] = ["None:Linear:Quadratic"]
    scene.set_attr("light1", "decayRate", "quadratic")
    assert fake_maya.calls_to("setAttr")[-1][0] == ("light1.decayRate", 2)
    with pytest.raises(BridgeError):
        scene.set_attr("light1", "decayRate", "bogus")
    _attr_stub(fake_maya, "double3", [(1.0, 2.0, 3.0)])
    out = scene.set_attr("pCube1", "translate", [1, 2, 3])
    assert fake_maya.calls_to("setAttr")[-1] == (("pCube1.translate", 1, 2, 3), {"type": "double3"})
    assert out["value"] == [1.0, 2.0, 3.0]


def test_set_attr_locked_or_connected(fake_maya):
    fake_maya.responses["getAttr"] = lambda plug, **k: True if k.get("lock") else "double"
    with pytest.raises(BridgeError) as exc:
        scene.set_attr("pCube1", "tx", 1)
    assert "locked" in str(exc.value)
    _attr_stub(fake_maya, "double", 0.0)
    fake_maya.responses["connectionInfo"] = lambda plug, **k: "anim.output" if k.get("sourceFromDestination") else True
    with pytest.raises(BridgeError) as exc:
        scene.set_attr("pCube1", "tx", 1)
    assert "connection" in str(exc.value)


def test_set_attr_missing_plug(fake_maya):
    fake_maya.existing = {"pCube1", "pCube1.tx"}
    with pytest.raises(BridgeError):
        scene.set_attr("pCube1", "nope", 1)
    with pytest.raises(BridgeError):
        scene.set_attr("pCube1", "tx")


def test_get_attr_and_set_attrs(fake_maya):
    _attr_stub(fake_maya, "double3", [(0.0, 1.0, 0.0)])
    assert scene.get_attr("pCube1", "translate") == {"node": "pCube1", "attr": "translate", "value": [0.0, 1.0, 0.0], "type": "double3"}
    out = scene.set_attrs("pCube1", {"translate": [0, 1, 0], "rotate": [0, 0, 0]})
    assert set(out["values"]) == {"translate", "rotate"}
    with pytest.raises(BridgeError):
        scene.set_attrs("pCube1", {})


def test_connect_disconnect(fake_maya):
    out = scene.connect_attr("a.tx", "b.tx", force=True)
    assert out["source"] == "a.tx"
    assert fake_maya.calls_to("connectAttr")[0] == (("a.tx", "b.tx"), {"force": True})
    scene.disconnect_attr("a.tx", "b.tx")
    assert fake_maya.calls_to("disconnectAttr")[0][0] == ("a.tx", "b.tx")
    with pytest.raises(BridgeError):
        scene.connect_attr("a", "b.tx")

    def boom(*a, **k):
        raise RuntimeError("not connected")

    fake_maya.responses["disconnectAttr"] = boom
    with pytest.raises(BridgeError):
        scene.disconnect_attr("a.tx", "b.tx")


# unit: undo and settings ---------------------------------------------------
def test_undo_redo_counts(fake_maya):
    assert scene.undo(3)["undone"] == 3 and len(fake_maya.calls_to("undo")) == 3
    calls = {"n": 0}

    def redo():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("nothing to redo")

    fake_maya.responses["redo"] = redo
    assert scene.redo(5)["redone"] == 1


def test_time_unit_parsing():
    assert scene._time_unit_from("film") == "film"
    assert scene._time_unit_from(24) == "film"
    assert scene._time_unit_from("30fps") == "ntsc"
    assert scene._time_unit_from(29.97) == "29.97fps"
    assert scene._time_unit_from(120) == "120fps"
    with pytest.raises(BridgeError):
        scene._time_unit_from("cinema")
    with pytest.raises(BridgeError):
        scene._time_unit_from(0)


def test_settings_mutates(fake_maya):
    fake_maya.responses["currentUnit"] = lambda **k: ("ntsc" if k.get("time") else "m") if k.get("query") else None
    fake_maya.responses["upAxis"] = lambda **k: "z" if k.get("query") else None
    fake_maya.responses["playbackOptions"] = lambda **k: 1001.0 if k.get("minTime") else 1100.0
    out = scene.settings(linear_unit="m", fps=30, up_axis="z", start=1001, end=1100)
    assert out["units"]["linear"] == "m" and out["fps"] == 30.0 and out["up_axis"] == "z"
    assert ((), {"linear": "m"}) in fake_maya.calls_to("currentUnit")
    assert ((), {"time": "ntsc"}) in fake_maya.calls_to("currentUnit")
    assert fake_maya.calls_to("upAxis")[0][1] == {"axis": "z", "rotateView": True}
    assert ((), {"minTime": 1001, "animationStartTime": 1001}) in fake_maya.calls_to("playbackOptions")
    with pytest.raises(BridgeError):
        scene.settings(linear_unit="furlong")
    with pytest.raises(BridgeError):
        scene.settings(up_axis="x")
    with pytest.raises(BridgeError):
        scene.settings(start=10, end=5)


# integration: through the MCP tools over the real socket -------------------
async def test_tool_scene_info(call_tool):
    data = parse(await call_tool("maya_get_scene_info"))
    assert data["scene"] == "untitled" and "counts" in data and data["units"]["linear"] == "cm"


async def test_tool_list_nodes_pagination(call_tool, fake_maya):
    _ls(fake_maya, {"n%d" % i: "|n%d" % i for i in range(5)})
    data = parse(await call_tool("maya_list_nodes", {"params": {"limit": 2, "offset": 4}}))
    assert data["total"] == 5 and data["nodes"] == ["|n4"] and data["has_more"] is False


async def test_tool_set_get_attr(call_tool, fake_maya):
    _attr_stub(fake_maya, "doubleLinear", 3.0)
    data = parse(await call_tool("maya_set_attr", {"params": {"node": "pCube1", "attr": "ty", "value": 3.0}}))
    assert data["value"] == 3.0
    data = parse(await call_tool("maya_get_attr", {"params": {"node": "pCube1", "attr": "ty"}}))
    assert data["type"] == "doubleLinear"


async def test_tool_group_and_parent(call_tool, fake_maya):
    fake_maya.responses["group"] = "grp"
    fake_maya.responses["parent"] = ["|grp|a"]
    _ls(fake_maya, {"grp": "|grp", "a": "|a", "|grp|a": "|grp|a"})
    data = parse(await call_tool("maya_group", {"params": {"nodes": ["a"], "name": "grp"}}))
    assert data["group"] == "|grp"
    data = parse(await call_tool("maya_parent", {"params": {"nodes": ["a"], "parent": "grp"}}))
    assert data["nodes"] == ["|grp|a"]


async def test_tool_error_for_missing_node(call_tool, fake_maya):
    fake_maya.existing = {"real"}
    text = await call_tool("maya_get_node_info", {"params": {"node": "ghost"}})
    assert text.startswith("Error") and "not found" in text


async def test_tool_settings_and_undo(call_tool, fake_maya):
    data = parse(await call_tool("maya_scene_settings", {"params": {"time_unit": "film", "start": 1, "end": 24}}))
    assert "frame_range" in data
    assert ((), {"time": "film"}) in fake_maya.calls_to("currentUnit")
    data = parse(await call_tool("maya_undo", {"params": {"count": 2}}))
    assert data["undone"] == 2


async def test_tool_rejects_unknown_param(call_tool):
    text = await call_tool("maya_select", {"params": {"nodes": ["a"], "bogus": 1}})
    assert "bogus" in text or "Error" in text
