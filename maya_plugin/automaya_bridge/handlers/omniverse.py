"""omni.* commands: NVIDIA Omniverse Maya Native Connector, detected at runtime.

The connector (Maya 2024.2 with USD 22.11 on Windows, installed from the NVIDIA
NGC Catalog) adds an "Omniverse" main menu and an Omniverse shelf, and needs
Autodesk's mayaUsd plugin. Its scripting API is not documented, so nothing here
assumes a command name: we look for modules, plugins, menus, shelf tabs, MEL
procs and help entries that mention omni, and drive the connector through the
menu items and shelf buttons it actually installed.

When the connector is missing (or Maya is 2025+, where NVIDIA discontinued it)
everything falls back to the plain mayaUsd path: export a USD layer locally and
open it in USD Composer or the Unreal USD stage.
"""
from __future__ import annotations

import inspect
import os
import re
from typing import Any, Dict, List, Tuple

from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

OMNI_RX = re.compile(r"omni", re.IGNORECASE)
HINT_RX = re.compile(r"omni|live", re.IGNORECASE)
CANDIDATE_PROCS = [
    "OmniverseExport",
    "omniverseExport",
    "OmniverseExportSelection",
    "OmniverseOpen",
    "OmniverseSave",
    "omniCreateLiveSession",
    "omniJoinLiveSession",
    "omniLeaveLiveSession",
    "omniEndLiveSession",
    "omniMergeLiveSession",
]
HELP_PATTERNS = ["*omni*", "*Omni*"]
STARTUP_CAMERAS = {"|persp", "|top", "|front", "|side"}
USD_EXTS = (".usd", ".usda", ".usdc")
NUCLEUS_EXTS = USD_EXTS + (".live",)

# action -> label keywords (whole words); the label or its menu path must also say live or session
LIVE_ACTIONS: Dict[str, List[str]] = {
    "create": ["create", "new", "start"],
    "join": ["join"],
    "leave": ["leave", "quit", "exit"],
    "end": ["end", "stop", "close"],
    "merge": ["merge"],
    "share": ["share", "link"],
}

SETUP_STEPS = [
    "Download the Omniverse Maya Native Connector installer (.exe) from the NVIDIA NGC Catalog; it targets Maya 2024.2 (USD 22.11) on Windows.",
    "Close Maya and run the installer; it drops a module file into $MyDocuments/maya/2024/modules.",
    "Restart Maya, load mayaUsdPlugin in the Plug-in Manager, and check for the Omniverse menu and shelf.",
    "Point it at a Nucleus server: your studio's Enterprise Nucleus Server (NGC, needs an Enterprise License). The Omniverse Launcher and Nucleus Workstation were deprecated on 1 Oct 2025, so a free local Nucleus is no longer the supported route.",
    "Log in to Nucleus from the Omniverse menu (Open Content browses omniverse://localhost/...).",
    "Save the scene to Nucleus, start a live session, then open the same stage in USD Composer (now built from NVIDIA's Kit App Template on GitHub) with Live mode on.",
]
DEPRECATION = ("NVIDIA discontinued the Omniverse Maya Native Connector for Maya 2025 and newer and points users to "
               "Autodesk's mayaUsd from GitHub. Use maya_omni_export_usd (mayaUsd) or maya_livelink_export_usd plus the "
               "Unreal subscriber instead; those survive Maya upgrades.")


# small helpers -------------------------------------------------------------------
def _s(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _lst(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _try(func: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception:  # noqa: BLE001, detection must never raise
        return None


def _maya_version() -> Tuple[str, int | None]:
    raw = _s(_try(cmds.about, version=True)) if cmds is not None else ""
    m = re.search(r"(\d{4})", raw)
    return raw, (int(m.group(1)) if m else None)


# detection ----------------------------------------------------------------------
def _modules() -> List[str]:
    return [m for m in _lst(_try(cmds.moduleInfo, listModules=True)) if isinstance(m, str) and OMNI_RX.search(m)]


def _plugins() -> List[str]:
    return [p for p in _lst(_try(cmds.pluginInfo, query=True, listPlugins=True)) if isinstance(p, str) and OMNI_RX.search(p)]


def _omni_menus() -> List[Dict[str, str]]:
    found = []
    for name in _lst(_try(cmds.lsUI, menus=True)):
        if not isinstance(name, str):
            continue
        label = _s(_try(cmds.menu, name, query=True, label=True))
        if re.search(r"omniverse", label, re.IGNORECASE) or re.search(r"omniverse", name, re.IGNORECASE):
            found.append({"name": name, "label": label or name})
    return found


def _shelf_top() -> str:
    top = _s(_try(mel.eval, "$automayaTmp = $gShelfTopLevel")) if mel is not None else ""
    return top or "ShelfLayout"


def _omni_shelves() -> List[Dict[str, str]]:
    top = _shelf_top()
    tabs = _lst(_try(cmds.shelfTabLayout, top, query=True, childArray=True))
    return [{"name": t, "path": "%s|%s" % (top, t)} for t in tabs if isinstance(t, str) and OMNI_RX.search(t)]


def _maya_usd() -> Dict[str, Any]:
    loaded = bool(_try(cmds.pluginInfo, "mayaUsdPlugin", query=True, loaded=True))
    version = _try(cmds.pluginInfo, "mayaUsdPlugin", query=True, version=True) if loaded else None
    return {"loaded": loaded, "version": version if isinstance(version, str) and version else None}


def _live_hints() -> List[Dict[str, Any]]:
    names = [n for n in _lst(_try(cmds.optionVar, list=True)) if isinstance(n, str) and HINT_RX.search(n)]
    hints = []
    for n in names[:50]:
        val = _try(cmds.optionVar, q=n)
        hints.append({"name": n, "value": val if isinstance(val, (str, int, float, bool, list)) else str(val)})
    return hints


def _detect() -> Dict[str, Any]:
    modules, plugins, menus, shelves = _modules(), _plugins(), _omni_menus(), _omni_shelves()
    return {
        "installed": bool(modules or plugins or menus or shelves),
        "modules": modules,
        "plugins": plugins,
        "menus": [m["label"] for m in menus],
        "shelves": [s["name"] for s in shelves],
    }


# UI walking ----------------------------------------------------------------------
def _item_entry(full: str, label_path: List[str]) -> Dict[str, Any] | None:
    if _try(cmds.menuItem, full, query=True, divider=True) is True:
        return None
    label = _s(_try(cmds.menuItem, full, query=True, label=True)) or full.rsplit("|", 1)[-1]
    cmd = _try(cmds.menuItem, full, query=True, command=True)
    source_type = _s(_try(cmds.menuItem, full, query=True, sourceType=True)) or "mel"
    return {"label": label, "path": " > ".join(label_path + [label]), "name": full, "source_type": source_type, "_cmd": cmd}


def _walk_menu(menu: str, label_path: List[str], depth: int = 0) -> List[Dict[str, Any]]:
    if depth > 6:
        return []
    items = _lst(_try(cmds.menu, menu, query=True, itemArray=True))
    if not items and depth == 0:
        # many menus build their items lazily in a postMenuCommand; run it once
        post = _try(cmds.menu, menu, query=True, postMenuCommand=True)
        if callable(post):
            _try(post)
        elif isinstance(post, str) and post.strip():
            _try(mel.eval, post)
        items = _lst(_try(cmds.menu, menu, query=True, itemArray=True))
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, str):
            continue
        full = "%s|%s" % (menu, item)
        if _try(cmds.menuItem, full, query=True, subMenu=True) is True:
            sub_label = _s(_try(cmds.menuItem, full, query=True, label=True)) or item
            out.extend(_walk_menu(full, label_path + [sub_label], depth + 1))
            continue
        entry = _item_entry(full, label_path)
        if entry is not None:
            out.append(entry)
    return out


def _menu_entries() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in _omni_menus():
        out.extend(_walk_menu(m["name"], [m["label"]]))
    return out


def _shelf_entries() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for shelf in _omni_shelves():
        for btn in _lst(_try(cmds.shelfLayout, shelf["path"], query=True, childArray=True)):
            if not isinstance(btn, str):
                continue
            full = "%s|%s" % (shelf["path"], btn)
            label = _s(_try(cmds.shelfButton, full, query=True, label=True)) or _s(_try(cmds.shelfButton, full, query=True, annotation=True)) or btn
            cmd = _try(cmds.shelfButton, full, query=True, command=True)
            source_type = _s(_try(cmds.shelfButton, full, query=True, sourceType=True)) or "mel"
            out.append({"label": label, "path": "%s > %s" % (shelf["name"], label), "name": full, "source_type": source_type, "_cmd": cmd})
    return out


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    cmd = entry.get("_cmd")
    if callable(cmd):
        shown = "<python callable %s>" % getattr(cmd, "__name__", type(cmd).__name__)
    else:
        shown = cmd if isinstance(cmd, str) else ""
    return {"label": entry["label"], "command": shown, "source_type": entry["source_type"], "path": entry["path"]}


def _execute(entry: Dict[str, Any]) -> Any:
    """Run a menu item or shelf button command the way Maya would."""
    cmd = entry.get("_cmd")
    if callable(cmd):
        try:
            params = inspect.signature(cmd).parameters.values()
            wants_arg = any(p.kind in (p.VAR_POSITIONAL, p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params)
        except (TypeError, ValueError):
            wants_arg = False
        return cmd(False) if wants_arg else cmd()
    if not isinstance(cmd, str) or not cmd.strip():
        raise BridgeError("%r has no command attached; it may only open a submenu" % entry["label"])
    if entry.get("source_type", "").lower() == "python":
        scope: Dict[str, Any] = {"cmds": cmds, "mel": mel, "__name__": "__automaya_omni__"}
        exec(compile(cmd, "<omniverse %s>" % entry["label"], "exec"), scope)  # noqa: S102, runs the connector's own UI code
        return None
    return mel.eval(cmd)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _entries_for(source: str) -> List[Dict[str, Any]]:
    if source == "menu":
        return _menu_entries()
    if source == "shelf":
        return _shelf_entries()
    raise BridgeError("source must be 'menu' or 'shelf'")


def _discovered_procs() -> List[str]:
    found = []
    for name in CANDIDATE_PROCS:
        what = _s(_try(mel.eval, 'whatIs "%s"' % name))
        low = what.lower()
        if low and "unknown" not in low and ("procedure" in low or "command" in low or "script" in low):
            found.append(name)
    return found


def _help_commands() -> List[str]:
    found: List[str] = []
    for pattern in HELP_PATTERNS:
        for c in _lst(_try(cmds.help, pattern, list=True)):
            if isinstance(c, str) and OMNI_RX.search(c) and c not in found:
                found.append(c)
    return found


def _connector_missing_msg() -> str:
    return ("the Omniverse Maya Native Connector is not installed or not loaded (no Omniverse menu, shelf, module or plugin found). "
            "Use the mayaUsd path instead: maya_livelink_export_usd (or maya_omni_export_usd with a local path) and reload the stage "
            "in USD Composer or the Unreal USD stage. See maya_omni_status for install steps.")


# commands ------------------------------------------------------------------------
@command("omni.status")
def status() -> Dict[str, Any]:
    """Detect the Omniverse connector, mayaUsd, Maya version and live session hints."""
    _util.require_maya()
    connector = _detect()
    raw, year = _maya_version()
    out: Dict[str, Any] = {
        "connector": connector,
        "path": "connector" if connector["installed"] else "usd_only",
        "maya_usd": _maya_usd(),
        "maya_version": raw,
        "live_session_hints": _live_hints(),
        "setup_steps": [] if connector["installed"] else list(SETUP_STEPS),
        "supported": "Maya 2024.2 (USD 22.11) on Windows; 2023.3 and 2022.4 untested",
    }
    if year is not None and year >= 2025:
        out["warning"] = DEPRECATION
    if not out["maya_usd"]["loaded"]:
        out["maya_usd"]["hint"] = "load mayaUsdPlugin (Windows > Settings/Preferences > Plug-in Manager); both paths need it"
    return out


@command("omni.list_commands")
def list_commands() -> Dict[str, Any]:
    """Everything omni the session exposes: help entries, MEL procs, menu items, shelf buttons."""
    _util.require_maya()
    return {
        "commands": _help_commands(),
        "procs": _discovered_procs(),
        "menu_items": [_public(e) for e in _menu_entries()],
        "shelf_buttons": [_public(e) for e in _shelf_entries()],
    }


def _pick(entries: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    needle = label.strip().lower()
    matches = [e for e in entries if needle in e["label"].lower()]
    exact = [e for e in matches if e["label"].lower() == needle]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise BridgeError("no Omniverse item matches %r. Available: %s" % (label, ", ".join(e["path"] for e in entries) or "none (connector not found)"))
    raise BridgeError("%r is ambiguous, it matches: %s. Use a longer label." % (label, ", ".join(e["path"] for e in matches)))


@command("omni.run_menu_item", mutates=True)
def run_menu_item(label: str, source: str = "menu") -> Dict[str, Any]:
    """Run one Omniverse menu item or shelf button, found by case insensitive label substring."""
    _util.require_maya()
    if not label or not label.strip():
        raise BridgeError("label is required, e.g. 'Save Content'; see omni.list_commands")
    entries = _entries_for(source)
    if not entries:
        raise BridgeError(_connector_missing_msg())
    entry = _pick(entries, label)
    result = _execute(entry)
    return {"ran": True, "source": source, **_public(entry), "result": _jsonable(result)}


def _export_targets(nodes: List[str] | None) -> List[str]:
    if nodes:
        return [(cmds.ls(n, long=True) or [n])[0] for n in _util.require_nodes(nodes)]
    tops = [n for n in _lst(cmds.ls(assemblies=True, long=True)) if n not in STARTUP_CAMERAS]
    if not tops:
        raise BridgeError("the scene has no top level transforms to export; pass nodes or build something first")
    return tops


@command("omni.export_usd", mutates=True)
def export_usd(path: str, nodes: List[str] | None = None, animation: bool = False, start: float | None = None, end: float | None = None) -> Dict[str, Any]:
    """Export to a local USD file (mayaUsd) or, with the connector, to an omniverse:// Nucleus URL."""
    _util.require_maya()
    if not path:
        raise BridgeError("path is required: a local .usd/.usda/.usdc file or an omniverse://server/path.usd URL")
    targets = _export_targets(nodes)
    if animation and (start is None or end is None):
        start = float(cmds.playbackOptions(query=True, minTime=True)) if start is None else start
        end = float(cmds.playbackOptions(query=True, maxTime=True)) if end is None else end
    if path.lower().startswith("omniverse://"):
        return _export_nucleus(path, targets)
    if os.path.splitext(path)[1].lower() not in USD_EXTS:
        raise BridgeError("path must end in .usd, .usda or .usdc")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    out = _util.export_selection(path, targets, "usd", {"animation": bool(animation), "start": start, "end": end})
    return {
        "path": out,
        "via": "mayaUsd",
        "nodes": targets,
        "animation": bool(animation),
        "start": start,
        "end": end,
        "note": "opens in Omniverse Kit apps (USD Composer) and the Unreal USD stage; save it to Nucleus from the Omniverse menu if you need an omniverse:// URL",
    }


def _export_nucleus(path: str, targets: List[str]) -> Dict[str, Any]:
    if os.path.splitext(path)[1].lower() not in NUCLEUS_EXTS:
        raise BridgeError("Nucleus path must end in .usd, .usda, .usdc or .live")
    if not _detect()["installed"]:
        raise BridgeError("writing to %s needs the Omniverse connector. %s" % (path, _connector_missing_msg()))
    procs = [p for p in _discovered_procs() + _help_commands() if "export" in p.lower()]
    cmds.select(targets, replace=True)
    tried = []
    for proc in procs:
        try:
            mel.eval('%s "%s"' % (proc, path.replace("\\", "/")))
        except Exception as exc:  # noqa: BLE001, try the next candidate
            tried.append("%s: %s" % (proc, exc))
            continue
        return {"path": path, "via": "connector", "proc": proc, "nodes": targets, "tried": tried}
    labels = [e["path"] for e in _menu_entries()]
    raise BridgeError(
        "no connector export proc accepted a path (tried: %s). The connector's Export Selection menu opens a dialog, so it is not "
        "driven blindly. Export locally with a .usd path, then use Omniverse > Save Content / Export Selection to put it on Nucleus. "
        "Menu items found: %s" % ("; ".join(tried) or "none discovered", ", ".join(labels) or "none"))


def _score_live(entry: Dict[str, Any], keywords: List[str]) -> int:
    label = entry["label"].lower()
    context = (entry["path"] + " " + entry["label"]).lower()
    if not any(re.search(r"\b%s\b" % re.escape(k), label) for k in keywords):
        return 0
    if not re.search(r"live|session", context):
        return 0
    score = 1
    if "live" in context:
        score += 1
    if "session" in context:
        score += 1
    return score


@command("omni.live_session", mutates=True)
def live_session(action: str, session_name: str = "") -> Dict[str, Any]:
    """Create, join, leave, end, merge or share an Omniverse live session through the connector UI."""
    _util.require_maya()
    action = (action or "").strip().lower()
    if action not in LIVE_ACTIONS:
        raise BridgeError("action must be one of %s" % ", ".join(LIVE_ACTIONS))
    menu, shelf = _menu_entries(), _shelf_entries()
    if not menu and not shelf and not _detect()["installed"]:
        raise BridgeError(_connector_missing_msg())
    chosen = None
    source = ""
    for src, entries in (("menu", menu), ("shelf", shelf)):
        scored = [(e, _score_live(e, LIVE_ACTIONS[action])) for e in entries]
        scored = [(e, s) for e, s in scored if s > 0]
        if not scored:
            continue
        best = max(s for _, s in scored)
        top = [e for e, s in scored if s == best]
        if len(top) > 1:
            raise BridgeError("more than one %s item matches: %s. Use maya_omni_run_menu_item with the exact label." % (
                action, ", ".join(e["path"] for e in top)))
        chosen, source = top[0], src
        break
    if chosen is None:
        labels = [e["path"] for e in menu + shelf]
        raise BridgeError("no Omniverse menu item or shelf button looks like '%s live session'. Found: %s" % (
            action, ", ".join(labels) or "none"))
    result = _execute(chosen)
    out = {"action": action, "ran": True, "source": source, **_public(chosen), "result": _jsonable(result)}
    if session_name:
        out["note"] = ("the connector's API is undocumented, so session_name %r is not passed through; type it into the "
                       "session dialog the connector opened" % session_name)
    return out


@command("omni.nucleus_hint")
def nucleus_hint() -> Dict[str, Any]:
    """Static guidance for Nucleus URLs and the USD Composer RTX viewport workflow."""
    shot = "<shot>"
    if cmds is not None:
        scene = _s(_try(cmds.file, query=True, sceneName=True))
        if scene:
            shot = os.path.splitext(os.path.basename(scene))[0] or shot
    return {
        "url_shape": "omniverse://localhost/Projects/<name>/<shot>.usd",
        "suggested_url": "omniverse://localhost/Projects/<name>/%s.usd" % shot,
        "login": "Log in to your studio Enterprise Nucleus Server from the Omniverse menu in Maya. The Omniverse Launcher, Navigator and Nucleus Workstation were deprecated on 1 Oct 2025; without Enterprise Nucleus use the file based mayaUsd route in `fallback`.",
        "viewport_workflow": [
            "Save the Maya scene to Nucleus (Omniverse > Save Content) as omniverse://localhost/Projects/<name>/<shot>.usd.",
            "Create a live session from the Omniverse shelf (maya_omni_live_session action=create).",
            "Open the same stage in USD Composer (build it from NVIDIA's Kit App Template on GitHub) and switch Live mode on, joining the same session.",
            "Set the renderer to RTX Real-Time; edits in Maya now sync both ways.",
            "Merge the live session into the stage when you are done (action=merge), then end it.",
        ],
        "deprecation": DEPRECATION,
        "fallback": "Without the connector or Nucleus: maya_omni_export_usd to a local .usd (mayaUsd), open it in a Kit app built from the Kit App Template (USD Composer) or in Unreal's USD Stage actor, re export and reload the layer to refresh. This works on Maya 2024 through 2026 but is file based, not a live session.",
    }
