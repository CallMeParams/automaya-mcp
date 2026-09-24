"""Unit + integration tests for the drop-in extension loader and its MCP tools."""
from __future__ import annotations

import os
import textwrap

import pytest
from maya import mel
from pydantic import ValidationError
from tests.conftest import parse

from automaya_bridge import registry
from automaya_bridge.handlers import extensions as ext
from automaya_bridge.handlers._util import BridgeError
from automaya_mcp.tools import extensions as ext_tools


@pytest.fixture()
def user_folder(tmp_path, monkeypatch):
    """Point the user drop folder at a temp dir and rescan after the test so
    other tests only see the shipped scripts."""
    app_dir = tmp_path / "mayaapp"
    monkeypatch.setenv("MAYA_APP_DIR", str(app_dir))
    monkeypatch.delenv("AUTOMAYA_EXT_PATH", raising=False)
    yield app_dir / "automaya" / "tools"
    monkeypatch.undo()
    ext.scan()


def _write(folder, name, body):
    os.makedirs(folder, exist_ok=True)
    path = folder / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# unit: loader --------------------------------------------------------------------
def test_shipped_scripts_are_registered(fake_maya):
    names = registry.names()
    assert "ext.modeling_tools.extrude_faces" in names
    assert "ext.modeling_tools.bevel_edges" in names
    assert "ext.modeling_tools.boolean_meshes" in names
    assert "ext.rigging_tools.get_vertex_weights" in names
    assert "ext.rigging_tools.find_skin_cluster" in names
    assert "ext.list" in names and "ext.reload" in names and "ext.source" in names
    # helpers imported into the script (Optional, List ...) must not become commands
    assert not any(n.startswith("ext.modeling_tools.Optional") for n in names)


def test_mutates_follows_name_prefix(fake_maya):
    assert registry.get("ext.modeling_tools.extrude_faces").mutates is True
    assert registry.get("ext.rigging_tools.get_vertex_weights").mutates is False
    assert registry.get("ext.rigging_tools.find_skin_cluster").mutates is False
    assert registry.get("ext.rigging_tools.set_vertex_weight").mutates is True


def test_index_has_types_docs_and_file(fake_maya):
    entry = ext.EXT_INDEX["ext.modeling_tools.extrude_faces"]
    assert entry["module"] == "modeling_tools" and entry["func"] == "extrude_faces"
    assert entry["file"].endswith(os.path.join("extensions", "modeling_tools.py"))
    assert entry["doc"].startswith("Extrude polygon faces")
    p = entry["params"]
    assert p["mesh"] == {"type": "str", "required": True, "default": None, "optional": False, "description": 'Transform or shape name, e.g. "pCube1".'}
    assert p["faces"]["type"] == "list" and p["faces"]["required"] is False and p["faces"]["optional"] is True
    assert p["thickness"]["type"] == "float" and p["thickness"]["default"] == 1.0
    assert p["divisions"]["type"] == "int" and p["keep_faces_together"]["type"] == "bool"


def test_signature_and_doc_survive_wrapping(fake_maya):
    spec = registry.get("ext.rigging_tools.get_vertex_weights")
    assert "vertex" in spec.signature and spec.signature["mesh"] == "required"
    assert spec.doc.startswith("Read the skin weights of one vertex")


def test_wrapper_turns_error_string_into_bridge_error(fake_maya):
    fake_maya.existing.add("pCube1")
    spec = registry.get("ext.modeling_tools.extrude_faces")
    with pytest.raises(BridgeError, match="does not exist"):
        spec.func(mesh="ghost")
    # a good return passes through untouched
    fake_maya.responses["polyExtrudeFacet"] = ["polyExtrudeFace1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|pCube1"]
    out = spec.func(mesh="pCube1", faces=[0, 1])
    assert out == {"mesh": "|pCube1", "history_node": "polyExtrudeFace1", "faces": 2}


def test_invoke_reports_error_envelope(fake_maya):
    fake_maya.existing.add("pCube1")
    resp = registry.invoke("ext.modeling_tools.boolean_meshes", {"mesh_a": "pCube1", "mesh_b": "pCube1", "operation": "xor"})
    assert resp["status"] == "error" and "operation must be one of" in resp["message"]


def test_shipped_rigging_script_uses_mel_stub(fake_maya):
    mel.responses.clear()
    mel.responses["findRelatedSkinCluster"] = "skinCluster1"
    fake_maya.responses["skinCluster"] = ["hip", "spine"]
    fake_maya.responses["skinPercent"] = [0.25, 0.75]
    fake_maya.responses["ls"] = lambda *a, **k: ["|body"]
    fake_maya.responses["getAttr"] = lambda plug: 4 if plug.endswith("maxInfluences") else 0
    try:
        out = registry.get("ext.rigging_tools.get_vertex_weights").func(mesh="body", vertex=3)
    finally:
        mel.responses.clear()
    assert out["weights"] == {"hip": 0.25, "spine": 0.75} and out["vertex"] == "body.vtx[3]"


def test_bad_user_script_does_not_break_loading(fake_maya, user_folder):
    _write(user_folder, "broken.py", "def fine(x: int) -> int:\n    return x\nraise RuntimeError('boom at import')\n")
    _write(user_folder, "good.py", '''
        def get_thing(name: str, count: int = 2) -> dict:
            """Say hi."""
            return {"name": name, "count": count}

        def _private(): pass
        ''')
    added = ext.scan()
    assert "ext.good.get_thing" in added
    assert not any(n.startswith("ext.broken.") for n in added)
    assert any("broken.py" in k for k in ext.LOAD_ERRORS)
    assert "boom at import" in list(ext.LOAD_ERRORS.values())[0]
    assert "ext.good._private" not in registry.names()
    assert registry.get("ext.good.get_thing").mutates is False
    assert "ext.modeling_tools.extrude_faces" in registry.names()  # shipped ones still there


def test_user_folder_is_created_and_listed(fake_maya, user_folder):
    assert not user_folder.exists()
    ext.scan()
    assert user_folder.is_dir()
    listing = ext.list_extensions()
    assert str(user_folder) in listing["folders"] and listing["folders"][0] == ext.repo_folder()


def test_ext_path_env_adds_folders(fake_maya, user_folder, tmp_path, monkeypatch):
    extra = tmp_path / "studio_tools"
    _write(extra, "studio.py", "def do_it(mesh: str) -> str:\n    return 'Error: nope'\n")
    monkeypatch.setenv("AUTOMAYA_EXT_PATH", str(extra) + os.pathsep + str(tmp_path / "missing"))
    ext.scan()
    assert str(extra) in ext.search_folders()
    assert "ext.studio.do_it" in registry.names()
    with pytest.raises(BridgeError, match="nope"):
        registry.get("ext.studio.do_it").func(mesh="x")


def test_reload_adds_and_removes(fake_maya, user_folder):
    path = _write(user_folder, "temp_tool.py", "def make_thing(name: str) -> dict:\n    return {'name': name}\n")
    out = ext.reload_extensions()
    assert "ext.temp_tool.make_thing" in out["added"] and out["removed"] == []
    assert registry.get("ext.temp_tool.make_thing").mutates is True
    path.unlink()
    out = ext.reload_extensions()
    assert "ext.temp_tool.make_thing" in out["removed"] and out["added"] == []
    assert registry.get("ext.temp_tool.make_thing") is None
    assert "ext.list" in registry.names() and "ext.reload" in registry.names()


def test_source_returns_code(fake_maya):
    out = ext.source("ext.modeling_tools.bevel_edges")
    assert out["file"].endswith("modeling_tools.py") and "def bevel_edges(" in out["source"]
    with pytest.raises(BridgeError, match="unknown extension command"):
        ext.source("ext.nothing.here")


def test_reserved_module_name_is_skipped(fake_maya, user_folder):
    _write(user_folder, "list.py", "def get_all() -> list:\n    return []\n")
    ext.scan()
    assert "ext.list.get_all" not in registry.names()
    assert registry.get("ext.list") is not None


# unit: server side helpers --------------------------------------------------------
def test_tool_name_cleaning_and_uniqueness():
    assert ext_tools.tool_name_for("modeling_tools", "extrude_faces") == "maya_ext_modeling_tools_extrude_faces"
    long = ext_tools.tool_name_for("a" * 40, "b" * 40)
    assert len(long) <= 60 and long.startswith("maya_ext_")
    taken = {"maya_ext_x_y"}
    assert ext_tools.tool_name_for("x", "y", taken) == "maya_ext_x_y_2"
    assert ext_tools.tool_name_for("My Tools", "do-it!", set()) == "maya_ext_my_tools_do_it"


def test_build_model_types_and_optionals():
    model = ext_tools.build_model("ext.m.f", {
        "mesh": {"type": "str", "required": True},
        "faces": {"type": "list", "required": False, "default": None, "description": "Face ids"},
        "depth": {"type": "float", "required": False, "default": 1.0},
        "flag": {"type": "bool", "required": False, "default": True},
        "opts": {"type": "dict", "required": False, "default": None},
    })
    schema = model.model_json_schema()
    assert schema["required"] == ["mesh"] and schema["additionalProperties"] is False
    assert schema["properties"]["faces"]["description"] == "Face ids"
    assert schema["properties"]["depth"]["default"] == 1.0
    inst = model(mesh="pCube1", faces=[1, 2])
    assert inst.model_dump()["flag"] is True
    with pytest.raises(ValidationError):
        model(mesh="pCube1", bogus=1)


# integration: dynamic MCP tools over the socket --------------------------------------
async def test_dynamic_tools_registered_from_live_bridge(app):
    tools = {t.name: t for t in await app.list_tools()}
    assert "maya_ext_rigging_tools_get_vertex_weights" in tools
    assert "maya_ext_modeling_tools_extrude_faces" in tools
    tool = tools["maya_ext_rigging_tools_get_vertex_weights"]
    assert tool.description.startswith("Read the skin weights of one vertex")
    text = str(tool.inputSchema)
    assert "vertex" in text and "min_weight" in text
    assert tool.annotations.readOnlyHint is True
    assert tools["maya_ext_modeling_tools_extrude_faces"].annotations.readOnlyHint is False


async def test_dynamic_tool_call(call_tool, fake_maya):
    fake_maya.existing.add("pCube1")
    fake_maya.responses["polyExtrudeFacet"] = ["polyExtrudeFace1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|pCube1"]
    data = parse(await call_tool("maya_ext_modeling_tools_extrude_faces", {"params": {"mesh": "pCube1", "faces": [0], "thickness": 2.5}}))
    assert data == {"mesh": "|pCube1", "history_node": "polyExtrudeFace1", "faces": 1}
    kwargs = fake_maya.calls_to("polyExtrudeFacet")[0][1]
    assert kwargs["localTranslateZ"] == 2.5
    text = await call_tool("maya_ext_modeling_tools_extrude_faces", {"params": {"mesh": "ghost"}})
    assert text.startswith("Error") and "does not exist" in text


async def test_ext_call_and_list_and_source(call_tool, fake_maya):
    fake_maya.existing.add("pCube1")
    fake_maya.responses["polyBevel3"] = ["polyBevel1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|pCube1"]
    fake_maya.responses["polyEvaluate"] = 12
    data = parse(await call_tool("maya_ext_call", {"params": {"command": "ext.modeling_tools.bevel_edges", "params": {"mesh": "pCube1", "fraction": 0.3}}}))
    assert data["history_node"] == "polyBevel1" and data["edges"] == 12
    text = await call_tool("maya_ext_call", {"params": {"command": "core.ping", "params": {}}})
    assert text.startswith("Error") and "ext." in text
    listing = parse(await call_tool("maya_ext_list"))
    assert "ext.modeling_tools.extrude_faces" in listing["commands"] and listing["count"] >= 8
    src = parse(await call_tool("maya_ext_source", {"params": {"command": "ext.rigging_tools.find_skin_cluster"}}))
    assert "findRelatedSkinCluster" in src["source"]


async def test_ext_reload_tool_refreshes_dynamic_tools(call_tool, app, fake_maya, user_folder):
    _write(user_folder, "late_tool.py", "def get_late(name: str) -> dict:\n    return {'late': name}\n")
    data = parse(await call_tool("maya_ext_reload"))
    assert "ext.late_tool.get_late" in data["added"]
    assert "maya_ext_late_tool_get_late" in data["tools_added"]
    assert data["client_notified"] is False and "note" in data
    names = {t.name for t in await app.list_tools()}
    assert "maya_ext_late_tool_get_late" in names
    out = parse(await call_tool("maya_ext_late_tool_get_late", {"params": {"name": "x"}}))
    assert out == {"late": "x"}
