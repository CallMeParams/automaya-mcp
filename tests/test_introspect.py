"""Unit + integration tests for the introspect domain."""
from __future__ import annotations

import json
import os

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import introspect
from automaya_bridge.handlers._util import BridgeError

POLYCUBE_HELP = """Synopsis: polyCube [flags]
Flags:
   -q -query
   -e -edit
   -ax -axis           Float Float Float
   -ch -constructionHistory on|off
   -d -depth           Float
   -n -name            String
   -sx -subdivisionsX  Int
"""


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    """Keep the docs index and caches out of the user's prefs dir and fresh per test."""
    monkeypatch.setenv("MAYA_APP_DIR", str(tmp_path))
    introspect._INDEX.clear()
    introspect._COMMAND_CACHE.clear()
    introspect._SCHEMA_CACHE.clear()
    yield
    introspect._INDEX.clear()
    introspect._COMMAND_CACHE.clear()
    introspect._SCHEMA_CACHE.clear()


def _help(fake_maya):
    def help_(pattern=None, **kw):
        # Real Maya: help takes a regex and only returns the matching names with list=True.
        if kw.get("list"):
            if pattern == ".*":
                return ["polyCube", "polySphere", "polyExtrudeFacet", "ls", "xform"]
            if pattern and pattern.startswith("^") and pattern.endswith(".*"):
                prefix = pattern[1:-2].replace("\\", "")
                return [c for c in ("polyCube", "polySphere", "polyExtrudeFacet", "ls", "xform") if c.startswith(prefix)]
            return []
        if pattern == "polyCube":
            return POLYCUBE_HELP
        if pattern == "polySphere":
            return "Synopsis: polySphere [flags]\n   -r -radius Float\n"
        if pattern == "polyExtrudeFacet":
            return "Synopsis: polyExtrudeFacet [flags]\n   -ltz -localTranslateZ Float\n   -kft -keepFacesTogether on|off\n"
        return ""

    fake_maya.responses["help"] = help_


# unit: commands ----------------------------------------------------------------
def test_list_commands_prefix_and_paging(fake_maya):
    _help(fake_maya)
    out = introspect.list_commands(prefix="poly", limit=2)
    assert out["commands"] == ["polyCube", "polyExtrudeFacet"] and out["total"] == 3 and out["next_offset"] == 2
    page2 = introspect.list_commands(prefix="poly", limit=2, offset=2)
    assert page2["commands"] == ["polySphere"] and page2["next_offset"] is None
    assert introspect.list_commands(contains="FORM")["commands"] == ["xform"]


def test_list_commands_falls_back_to_dir(fake_maya):
    fake_maya.responses["help"] = None
    out = introspect.list_commands()
    assert isinstance(out["commands"], list) and out["total"] == len(out["commands"])


def test_command_help_parses_flags(fake_maya):
    _help(fake_maya)
    out = introspect.command_help("polyCube")
    assert out["synopsis"].startswith("Synopsis: polyCube")
    flags = {f["long"]: f for f in out["flags"]}
    assert flags["axis"]["short"] == "ax" and flags["axis"]["args"] == "Float Float Float"
    assert flags["name"]["args"] == "String" and out["flag_count"] == 7
    assert out["docs"]["python"].endswith("/CommandsPython/polyCube.html")


def test_command_help_unknown(fake_maya):
    _help(fake_maya)
    with pytest.raises(BridgeError, match="no command named 'nope'"):
        introspect.command_help("nope")
    with pytest.raises(BridgeError, match="plain command name"):
        introspect.command_help("poly Cube; rm")


# unit: node types ----------------------------------------------------------------
def test_node_type_schema_creates_and_deletes_temp_node(fake_maya):
    fake_maya.responses["createNode"] = "automaya_schema_tmp1"
    fake_maya.responses["listRelatives"] = lambda n, **kw: ["automaya_schema_tmp_parent"] if kw.get("parent") else []
    fake_maya.responses["listAttr"] = ["radius", "subdivisionsAxis", "message"]
    fake_maya.responses["nodeType"] = lambda *a, **kw: ["dependNode", "polyPrimitive", "polySphere"] if kw.get("inherited") else "polySphere"

    def attr_query(attr, **kw):
        if kw.get("attributeType"):
            return "double"
        if kw.get("keyable"):
            return attr == "radius"
        if kw.get("minExists"):
            return attr == "radius"
        if kw.get("minimum"):
            return [0.0]
        if kw.get("listDefault"):
            return [1.0] if attr == "radius" else ([20] if attr == "subdivisionsAxis" else [])
        if kw.get("listEnum"):
            return []
        return False if kw.get("multi") or kw.get("hidden") else (True if kw.get("readable") or kw.get("writable") else None)

    fake_maya.responses["attributeQuery"] = attr_query
    fake_maya.responses["getAttr"] = lambda plug, **kw: "float" if kw.get("type") and "radius" in plug else ("long" if kw.get("type") else None)
    out = introspect.node_type_schema("polySphere")
    assert out["inherits"][-1] == "polySphere" and out["is_dag"] is False
    radius = next(a for a in out["attributes"] if a["name"] == "radius")
    assert radius == {"name": "radius", "type": "float", "keyable": True, "min": 0.0, "default": 1.0}
    # undo was paused and resumed, temp node (and its parent) deleted
    undo_calls = [k for _, k in fake_maya.calls_to("undoInfo")]
    assert undo_calls[0] == {"stateWithoutFlush": False} and undo_calls[-1] == {"stateWithoutFlush": True}
    deleted = [a[0] for a, _ in fake_maya.calls_to("delete")]
    assert deleted == ["automaya_schema_tmp_parent", "automaya_schema_tmp1"]
    # cached: second call does not create again
    introspect.node_type_schema("polySphere")
    assert len(fake_maya.calls_to("createNode")) == 1


def test_node_type_schema_abstract_type(fake_maya):
    def boom(*a, **kw):
        raise RuntimeError("No node type 'shape'")

    fake_maya.responses["createNode"] = boom
    with pytest.raises(BridgeError, match="cannot create a 'shape' node"):
        introspect.node_type_schema("shape")
    assert fake_maya.calls_to("undoInfo")[-1][1] == {"stateWithoutFlush": True}


def test_list_node_types_filter_and_plugin_only(fake_maya):
    fake_maya.responses["allNodeTypes"] = ["mesh", "aiStandardSurface", "aiSkyDomeLight", "pointLight"]
    fake_maya.responses["pluginInfo"] = lambda *a, **kw: ["mtoa"] if kw.get("listPlugins") else (["aiStandardSurface", "aiSkyDomeLight"] if kw.get("dependNode") else True)
    out = introspect.list_node_types(filter="light")
    assert out["node_types"] == ["aiSkyDomeLight", "pointLight"]
    out = introspect.list_node_types(plugin_only=True, include_inheritance=True)
    assert [r["type"] for r in out["node_types"]] == ["aiSkyDomeLight", "aiStandardSurface"] and out["node_types"][0]["plugin"] == "mtoa"


# unit: plugins + env --------------------------------------------------------------
def test_plugin_info_and_list(fake_maya, tmp_path):
    (tmp_path / "fooPlugin.so").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")

    def plugin_info(*a, **kw):
        if kw.get("listPlugins"):
            return ["mtoa"]
        if kw.get("listPluginsPath"):
            return [str(tmp_path)]
        if kw.get("loaded"):
            return a[0] == "mtoa"
        if kw.get("version"):
            return "5.3.1"
        if kw.get("command"):
            return ["arnoldRender"]
        if kw.get("dependNode"):
            return ["aiStandardSurface"]
        return None

    fake_maya.responses["pluginInfo"] = plugin_info
    info = introspect.plugin_info("mtoa")
    assert info["loaded"] and info["version"] == "5.3.1" and info["commands"] == ["arnoldRender"] and "path" not in info
    assert introspect.plugin_info("bullet")["loaded"] is False and "hint" in introspect.plugin_info("bullet")
    out = introspect.list_plugins(loaded_only=False)
    assert out["loaded"] == [{"name": "mtoa", "loaded": True, "version": "5.3.1"}]
    assert out["available"] == [{"name": "fooPlugin", "path": str(tmp_path / "fooPlugin.so")}]


def test_env_info(fake_maya, tmp_path):
    fake_maya.responses["workspace"] = lambda *a, **kw: "/proj" if kw.get("rootDirectory") else "images"
    fake_maya.responses["moduleInfo"] = ["mtoa", "mayaUsd"]
    fake_maya.responses["getAttr"] = "arnold"
    out = introspect.env_info()
    assert out["maya_version"] == "2024" and out["maya_api"] == 20240000 and out["modules"] == ["mtoa", "mayaUsd"]
    assert out["workspace_root"] == "/proj" and out["current_renderer"] == "arnold" and out["openmaya2"] is False
    assert out["maya_app_dir"] == str(tmp_path) and out["loaded_plugins_count"] == 0


# unit: UI + settings ---------------------------------------------------------------
def test_ui_tree_and_panels(fake_maya):
    fake_maya.responses["lsUI"] = lambda **kw: ["MayaWindow"] if kw.get("windows") else (["modelPanel4", "outlinerPanel1"] if kw.get("panels") else [])
    fake_maya.responses["objectTypeUI"] = lambda n: "window" if n == "MayaWindow" else "rowLayout"
    fake_maya.responses["layout"] = lambda n, **kw: ["formLayout1"] if n == "MayaWindow" and kw.get("childArray") else []
    fake_maya.responses["getPanel"] = lambda **kw: ("modelPanel" if kw["typeOf"] == "modelPanel4" else "outlinerPanel") if kw.get("typeOf") else (["modelPanel4"] if kw.get("visiblePanels") else "modelPanel4")
    fake_maya.responses["modelPanel"] = "persp"
    out = introspect.ui_tree(depth=1)
    assert out["tree"]["windows"][0]["name"] == "MayaWindow" and out["tree"]["windows"][0]["children"][0]["name"] == "formLayout1"
    assert [p["name"] for p in out["tree"]["panels"]] == ["modelPanel4", "outlinerPanel1"]
    panels = introspect.list_panels()
    assert panels["focused"] == "modelPanel4" and panels["panels"][0] == {"name": "modelPanel4", "visible": True, "type": "modelPanel", "camera": "persp"}
    fake_maya.responses["control"] = False
    fake_maya.responses["window"] = False
    fake_maya.responses["menu"] = False
    fake_maya.responses["panel"] = False
    with pytest.raises(BridgeError, match="no UI element"):
        introspect.ui_tree(root="ghost")


def test_hotkeys_option_vars_workspace(fake_maya):
    fake_maya.responses["hotkeySet"] = "Maya_Default"
    fake_maya.responses["assignCommand"] = lambda *a, **kw: 2 if kw.get("numElements") else ("Undo" if kw.get("name") else (["z", "0", "1", "0"] if kw.get("keyString") else "undo it"))
    hk = introspect.hotkeys(search="undo")
    assert hk["hotkey_set"] == "Maya_Default" and hk["count"] == 2 and hk["hotkeys"][0] == {"name": "Undo", "key": "z", "modifiers": ["ctrl"], "annotation": "undo it"}
    fake_maya.responses["optionVar"] = lambda **kw: ["renderViewA", "renderViewB", "other"] if kw.get("list") else ({"renderViewA": 1, "RecentFilesList": ["/a.ma"]}.get(kw.get("query"), "x"))
    ov = introspect.option_vars(prefix="render", limit=1)
    assert ov == {"option_vars": {"renderViewA": 1}, "total": 2, "shown": 1}
    fake_maya.responses["workspace"] = lambda *a, **kw: "/proj" if kw.get("rootDirectory") else ("proj" if kw.get("active") else (["scene", "images"] if kw.get("fileRuleList") else kw.get("fileRuleEntry", "") + "_dir"))
    ws = introspect.workspace_info()
    assert ws["root"] == "/proj" and ws["file_rules"] == {"scene": "scene_dir", "images": "images_dir"} and ws["recent_files"] == ["/a.ma"]


# unit: docs -----------------------------------------------------------------------
def test_search_docs_builds_cached_index_and_ranks(fake_maya):
    _help(fake_maya)
    out = introspect.search_docs("extrude faces together")
    assert out["index"]["built"] is True and out["index"]["commands"] == 5
    assert out["results"][0]["command"] == "polyExtrudeFacet" and "keepFacesTogether" in out["results"][0]["flags"]
    assert os.path.exists(out["index"]["path"])
    with open(out["index"]["path"]) as fh:
        assert "polyCube" in json.load(fh)["commands"]
    # second search reuses the in memory / cached index
    help_calls = len(fake_maya.calls_to("help"))
    again = introspect.search_docs("polyCube")
    assert again["index"]["built"] is False and again["results"][0]["command"] == "polyCube" and again["results"][0]["score"] >= 100
    assert len(fake_maya.calls_to("help")) == help_calls
    with pytest.raises(BridgeError, match="query"):
        introspect.search_docs("  ")


def test_api_reference_hint(fake_maya):
    _help(fake_maya)
    fake_maya.responses["nodeType"] = lambda *a, **kw: a[0] == "mesh"
    om = introspect.api_reference_hint("MFnMesh")
    assert om["kind"] == ["openmaya_class"] and om["urls"]["openmaya2"].endswith("open_maya_1_1_m_fn_mesh_html")
    cmd = introspect.api_reference_hint("polyCube")
    assert "command" in cmd["kind"] and cmd["urls"]["cmds"].endswith("CommandsPython/polyCube.html")
    node = introspect.api_reference_hint("mesh")
    assert "node_type" in node["kind"] and node["urls"]["node"].endswith("Nodes/mesh.html")
    assert introspect.api_reference_hint("banana")["kind"] == ["unknown"]
    assert introspect._to_snake("MFnDagNode") == "m_fn_dag_node" and introspect._to_snake("MItMeshPolygon") == "m_it_mesh_polygon"


# integration ---------------------------------------------------------------------
async def test_tool_list_and_help(call_tool, fake_maya):
    _help(fake_maya)
    data = parse(await call_tool("maya_list_commands", {"params": {"prefix": "poly"}}))
    assert data["total"] == 3
    data = parse(await call_tool("maya_command_help", {"params": {"command_name": "polyCube"}}))
    assert data["flag_count"] == 7


async def test_tool_command_help_rejects_bad_name(call_tool, fake_maya):
    text = await call_tool("maya_command_help", {"params": {"command_name": "poly cube"}})
    assert text.startswith("Error")


async def test_tool_env_and_search(call_tool, fake_maya):
    _help(fake_maya)
    data = parse(await call_tool("maya_env_info"))
    assert data["maya_version"] == "2024"
    data = parse(await call_tool("maya_search_docs", {"params": {"query": "sphere radius"}}))
    assert data["results"][0]["command"] == "polySphere"


async def test_tool_node_schema_error_surface(call_tool, fake_maya):
    def boom(*a, **kw):
        raise RuntimeError("bad type")

    fake_maya.responses["createNode"] = boom
    text = await call_tool("maya_node_type_schema", {"params": {"node_type": "ghostType"}})
    assert text.startswith("Error") and "cannot create" in text
