"""Unit + integration tests for the omniverse domain (connector detected at runtime)."""
from __future__ import annotations

import pytest
from maya import mel
from tests.conftest import parse

from automaya_bridge import registry
from automaya_bridge.handlers import omniverse
from automaya_bridge.handlers._util import BridgeError

LOG: list = []


def _py_callable(*args):
    LOG.append(("callable", args))
    return "called"


# fake connector UI --------------------------------------------------------------
MENUS = {"mainFileMenu": "File", "OmniverseMenu": "Omniverse"}
MENU_ITEMS = {
    "OmniverseMenu": ["openContent", "saveContent", "div1", "exportSel", "liveSub"],
    "OmniverseMenu|liveSub": ["liveCreate", "liveJoin", "liveEnd", "liveMerge", "prefsItem"],
    "mainFileMenu": ["fileOpen"],
}
ITEMS = {
    "openContent": {"label": "Open Content", "command": "OmniverseOpen", "sourceType": "mel"},
    "saveContent": {"label": "Save Content", "command": "sel = cmds.ls(sl=True)", "sourceType": "python"},
    "div1": {"divider": True},
    "exportSel": {"label": "Export Selection", "command": "OmniverseExportSelection", "sourceType": "mel"},
    "liveSub": {"label": "Live Session", "subMenu": True},
    "liveCreate": {"label": "Create Session", "command": _py_callable, "sourceType": "python"},
    "liveJoin": {"label": "Join Session", "command": "omniJoinLiveSession", "sourceType": "mel"},
    "liveEnd": {"label": "End Session", "command": "omniEndLiveSession", "sourceType": "mel"},
    "liveMerge": {"label": "Merge Session", "command": "omniMergeLiveSession", "sourceType": "mel"},
    "prefsItem": {"label": "Session Preferences", "command": "print('prefs')", "sourceType": "python"},
}
SHELF_BUTTONS = {
    "btnLiveLeave": {"label": "Leave Live", "command": "omniLeaveLiveSession", "sourceType": "mel"},
    "btnShare": {"label": "Share Live Link", "command": "print('share')", "sourceType": "python"},
}
PROCS = {"OmniverseExport", "OmniverseOpen", "omniJoinLiveSession", "omniEndLiveSession", "omniMergeLiveSession", "omniLeaveLiveSession"}


def _menu(name, **kw):
    if kw.get("label"):
        return MENUS.get(name, "")
    if kw.get("itemArray"):
        return MENU_ITEMS.get(name, [])
    return None


def _menu_item(full, **kw):
    item = ITEMS.get(full.rsplit("|", 1)[-1], {})
    for flag in ("label", "command", "sourceType", "subMenu", "divider"):
        if kw.get(flag):
            return item.get(flag, False if flag in ("subMenu", "divider") else "")
    return None


def _shelf_button(full, **kw):
    item = SHELF_BUTTONS.get(full.rsplit("|", 1)[-1], {})
    for flag in ("label", "command", "sourceType", "annotation"):
        if kw.get(flag):
            return item.get(flag, "")
    return None


def _mel_dispatch(code):
    if "gShelfTopLevel" in code:
        return "ShelfLayout"
    if code.startswith("whatIs"):
        name = code.split('"')[1]
        return "Mel procedure found in: C:/omni/%s.mel" % name if name in PROCS else "Unknown"
    if code.startswith("OmniverseExport "):
        return None
    return None


def _plugin_info(*args, **kw):
    if kw.get("listPlugins"):
        return ["mayaUsdPlugin", "fbxmaya", "OmniverseMayaConnector"] if CONNECTOR["on"] else ["mayaUsdPlugin", "fbxmaya"]
    if kw.get("loaded"):
        return True
    if kw.get("version"):
        return "0.25.0"
    return []


CONNECTOR = {"on": True}


@pytest.fixture()
def omni(fake_maya):
    """Install the fake connector UI; tests flip CONNECTOR['on'] for the absent case."""
    LOG.clear()
    mel.responses.clear()
    CONNECTOR["on"] = True
    fake_maya.responses["moduleInfo"] = lambda **kw: ["mayaUsd", "OmniverseMayaNative"] if CONNECTOR["on"] else ["mayaUsd"]
    fake_maya.responses["pluginInfo"] = _plugin_info
    fake_maya.responses["lsUI"] = lambda **kw: (["mainFileMenu", "OmniverseMenu"] if CONNECTOR["on"] else ["mainFileMenu"]) if kw.get("menus") else []
    fake_maya.responses["menu"] = _menu
    fake_maya.responses["menuItem"] = _menu_item
    fake_maya.responses["shelfTabLayout"] = lambda *a, **kw: ["Custom", "Omniverse"] if CONNECTOR["on"] else ["Custom"]
    fake_maya.responses["shelfLayout"] = lambda path, **kw: list(SHELF_BUTTONS) if path.endswith("Omniverse") else ["customBtn"]
    fake_maya.responses["shelfButton"] = _shelf_button
    fake_maya.responses["optionVar"] = lambda *a, **kw: ["omniLastServer", "gridSize", "omniLiveSessionName"] if kw.get("list") else "localhost"
    fake_maya.responses["help"] = lambda pattern, **kw: ["omniCommandPort"] if pattern == "*omni*" else ["OmniverseExport"]
    mel.responses[""] = _mel_dispatch
    yield fake_maya
    mel.responses.clear()
    CONNECTOR["on"] = True


def _absent(fake_maya):
    CONNECTOR["on"] = False


# status -------------------------------------------------------------------------
def test_status_connector_present(omni):
    out = omniverse.status()
    assert out["path"] == "connector"
    c = out["connector"]
    assert c["installed"] and c["menus"] == ["Omniverse"] and c["shelves"] == ["Omniverse"]
    assert "OmniverseMayaNative" in c["modules"] and c["plugins"] == ["OmniverseMayaConnector"]
    assert out["maya_usd"] == {"loaded": True, "version": "0.25.0"}
    assert out["setup_steps"] == [] and "warning" not in out
    assert {h["name"] for h in out["live_session_hints"]} == {"omniLastServer", "omniLiveSessionName"}


def test_status_connector_absent_gives_setup_steps(omni):
    _absent(omni)
    out = omniverse.status()
    assert out["path"] == "usd_only" and out["connector"]["installed"] is False
    assert any("NGC" in s for s in out["setup_steps"]) and any("USD Composer" in s for s in out["setup_steps"])


def test_status_warns_on_2025(omni):
    omni.responses["about"] = lambda **kw: "2025.2" if kw.get("version") else "stub"
    out = omniverse.status()
    assert "discontinued" in out["warning"] and "mayaUsd" in out["warning"]


# list_commands ------------------------------------------------------------------
def test_list_commands_walks_menu_submenu_and_shelf(omni):
    out = omniverse.list_commands()
    assert out["commands"] == ["omniCommandPort", "OmniverseExport"]
    assert out["procs"] == [p for p in omniverse.CANDIDATE_PROCS if p in PROCS]
    labels = [m["label"] for m in out["menu_items"]]
    assert "Live Session" not in labels and "File" not in labels  # submenu header and other menus skipped
    assert labels[:3] == ["Open Content", "Save Content", "Export Selection"]
    create = next(m for m in out["menu_items"] if m["label"] == "Create Session")
    assert create["path"] == "Omniverse > Live Session > Create Session"
    assert create["command"].startswith("<python callable")
    assert [b["label"] for b in out["shelf_buttons"]] == ["Leave Live", "Share Live Link"]
    assert out["shelf_buttons"][0]["path"] == "Omniverse > Leave Live"


def test_list_commands_empty_without_connector(omni):
    _absent(omni)
    out = omniverse.list_commands()
    assert out["menu_items"] == [] and out["shelf_buttons"] == []


# run_menu_item ------------------------------------------------------------------
def test_run_menu_item_python_source(omni):
    out = omniverse.run_menu_item(label="save content")
    assert out["ran"] and out["label"] == "Save Content" and out["source_type"] == "python"
    assert omni.calls_to("ls")  # the exec'd code ran against cmds


def test_run_menu_item_mel_source(omni):
    out = omniverse.run_menu_item(label="Open")
    assert out["label"] == "Open Content"
    assert "OmniverseOpen" in mel.evaluated


def test_run_menu_item_callable_and_shelf(omni):
    omniverse.run_menu_item(label="Create Session")
    assert LOG and LOG[-1][0] == "callable"
    out = omniverse.run_menu_item(label="leave", source="shelf")
    assert out["source"] == "shelf" and "omniLeaveLiveSession" in mel.evaluated


def test_run_menu_item_ambiguous_lists_matches(omni):
    with pytest.raises(BridgeError) as exc:
        omniverse.run_menu_item(label="session")
    msg = str(exc.value)
    assert "ambiguous" in msg and "Join Session" in msg and "Merge Session" in msg


def test_run_menu_item_missing_lists_labels(omni):
    with pytest.raises(BridgeError) as exc:
        omniverse.run_menu_item(label="teleport")
    assert "Available" in str(exc.value) and "Open Content" in str(exc.value)


def test_run_menu_item_is_mutating():
    assert registry.get("omni.run_menu_item").mutates is True
    assert registry.get("omni.status").mutates is False


# export_usd ---------------------------------------------------------------------
def test_export_usd_local_uses_util_path(omni, tmp_path):
    omni.responses["ls"] = lambda *a, **kw: ["|persp", "|top", "|front", "|side", "|set", "|hero"] if kw.get("assemblies") else []
    target = str(tmp_path / "out" / "sh010.usda")
    out = omniverse.export_usd(path=target)
    assert out["via"] == "mayaUsd" and out["path"] == target and out["nodes"] == ["|set", "|hero"]
    files = omni.calls_to("file")
    assert files and files[-1][1]["type"] == "USD Export" and files[-1][0][0] == target
    assert omni.calls_to("select")[-1][0][0] == ["|set", "|hero"]
    assert "USD Composer" in out["note"]


def test_export_usd_bad_extension(omni):
    with pytest.raises(BridgeError):
        omniverse.export_usd(path="/tmp/out.fbx", nodes=["pCube1"])


def test_export_usd_nucleus_without_connector_errors(omni):
    _absent(omni)
    with pytest.raises(BridgeError) as exc:
        omniverse.export_usd(path="omniverse://localhost/Projects/demo/sh010.usd", nodes=["pCube1"])
    assert "connector" in str(exc.value) and "mayaUsd" in str(exc.value)
    assert not omni.calls_to("file")


def test_export_usd_nucleus_with_connector_proc(omni):
    out = omniverse.export_usd(path="omniverse://localhost/Projects/demo/sh010.usd", nodes=["pCube1"])
    assert out["via"] == "connector" and out["proc"] == "OmniverseExport"
    assert 'OmniverseExport "omniverse://localhost/Projects/demo/sh010.usd"' in mel.evaluated


def test_export_usd_nucleus_proc_failure_lists_labels(omni):
    def boom(code):
        if code.startswith("OmniverseExport "):
            raise RuntimeError("wrong args")
        return _mel_dispatch(code)

    mel.responses[""] = boom
    with pytest.raises(BridgeError) as exc:
        omniverse.export_usd(path="omniverse://localhost/p/x.usd", nodes=["pCube1"])
    msg = str(exc.value)
    assert "wrong args" in msg and "Export Selection" in msg


# live_session -------------------------------------------------------------------
def test_live_session_create_and_join(omni):
    out = omniverse.live_session(action="create", session_name="layout")
    assert out["label"] == "Create Session" and out["source"] == "menu" and "layout" in out["note"]
    assert LOG[-1][0] == "callable"
    out = omniverse.live_session(action="join")
    assert out["label"] == "Join Session" and "omniJoinLiveSession" in mel.evaluated


def test_live_session_falls_back_to_shelf(omni):
    out = omniverse.live_session(action="leave")
    assert out["source"] == "shelf" and out["label"] == "Leave Live"
    out = omniverse.live_session(action="share")
    assert out["label"] == "Share Live Link"


def test_live_session_absent_suggests_usd(omni):
    _absent(omni)
    with pytest.raises(BridgeError) as exc:
        omniverse.live_session(action="create")
    assert "maya_livelink_export_usd" in str(exc.value)


def test_live_session_bad_action(omni):
    with pytest.raises(BridgeError):
        omniverse.live_session(action="teleport")


# integration --------------------------------------------------------------------
async def test_tool_status_roundtrip(call_tool, omni):
    data = parse(await call_tool("maya_omni_status"))
    assert data["path"] == "connector" and data["maya_usd"]["loaded"] is True


async def test_tool_nucleus_hint(call_tool, fake_maya):
    fake_maya.responses["file"] = lambda *a, **kw: "C:/shots/sh020_layout.ma" if kw.get("sceneName") else ""
    data = parse(await call_tool("maya_omni_nucleus_hint"))
    assert data["url_shape"] == "omniverse://localhost/Projects/<name>/<shot>.usd"
    assert data["suggested_url"].endswith("sh020_layout.usd")
    assert any("RTX Real-Time" in s for s in data["viewport_workflow"]) and "discontinued" in data["deprecation"]


async def test_tool_live_session_rejects_bad_action(call_tool, omni):
    text = await call_tool("maya_omni_live_session", {"params": {"action": "teleport"}})
    assert text.startswith("Error")
