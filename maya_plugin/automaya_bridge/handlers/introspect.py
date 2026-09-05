"""introspect.* commands: program knowledge. What commands, node types, plugins,
UI and settings this Maya has, plus an offline searchable index of command
synopses so the agent can find the right cmds call without guessing.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from typing import Any, Dict, List

from .. import prefs
from ..registry import command
from . import _util
from ._util import BridgeError

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

CMDS_DOC_URL = "https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Tech-Docs/CommandsPython/%s.html"
MEL_DOC_URL = "https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Tech-Docs/Commands/%s.html"
NODE_DOC_URL = "https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Tech-Docs/Nodes/%s.html"
OM_DOC_URL = "https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=Maya_SDK_py_ref_class_open_maya_1_1_%s_html"
OM_INDEX_URL = "https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=Maya_SDK_py_ref_namespace_open_maya_html"
DEVKIT_URL = "https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=Maya_SDK_Maya_Python_API_html"
USER_GUIDE_SEARCH = "https://help.autodesk.com/view/MAYAUL/2024/ENU/?query=%s"

_COMMAND_CACHE: List[str] = []
_INDEX: Dict[str, Dict[str, Any]] = {}
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
INDEX_FILE = "command_index.json"
_FLAG_RE = re.compile(r"^\s*-(\w+)\s+-(\w+)\s*(.*?)\s*$")


# commands ------------------------------------------------------------------
def _all_command_names() -> List[str]:
    """Every cmds function name; cmds.help('*') first, dir(cmds) as fallback."""
    if _COMMAND_CACHE:
        return _COMMAND_CACHE
    names: List[str] = []
    try:
        names = _parse_help_list(cmds.help("*"))
    except Exception:
        names = []
    if not names:
        try:
            names = [n for n in dir(cmds) if re.match(r"^[a-z][A-Za-z0-9_]*$", n) and not n.startswith(("calls", "responses", "reset", "existing"))]
        except Exception:
            names = []
    names = sorted(set(names))
    _COMMAND_CACHE[:] = names
    return names


def _parse_help_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [line.strip() for line in str(raw).splitlines() if line.strip() and " " not in line.strip()]


@command("introspect.list_commands")
def list_commands(prefix: str = "", limit: int = 200, offset: int = 0, contains: str | None = None) -> Dict[str, Any]:
    """Page through maya.cmds command names, optionally filtered by prefix or substring."""
    _util.require_maya()
    names = _all_command_names()
    if prefix:
        try:
            matched = _parse_help_list(cmds.help(prefix + "*"))
        except Exception:
            matched = []
        names = sorted(set(matched)) if matched else [n for n in names if n.startswith(prefix)]
    if contains:
        low = contains.lower()
        names = [n for n in names if low in n.lower()]
    total = len(names)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 2000))
    page = names[offset: offset + limit]
    return {"commands": page, "total": total, "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < total else None}


@command("introspect.command_help")
def command_help(command_name: str) -> Dict[str, Any]:
    """Synopsis and parsed flags for one cmds command, plus the doc URL."""
    _util.require_maya()
    name = command_name.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise BridgeError("command_name must be a plain command name like 'polyCube'")
    text = ""
    for kwargs in ({"syntaxOnly": True}, {}):
        try:
            got = cmds.help(name, **kwargs)
        except Exception:
            got = None
        if got and isinstance(got, str) and got.strip():
            text = got
            break
    known = hasattr(cmds, name) or name in _all_command_names()
    if not text and not known:
        raise BridgeError("no command named %r; try introspect.list_commands(prefix=%r) or introspect.search_docs" % (name, name[:4]))
    flags = _parse_flags(text)
    synopsis = ""
    for line in text.splitlines():
        if line.strip().lower().startswith("synopsis"):
            synopsis = line.strip()
            break
    return {
        "command": name,
        "synopsis": synopsis or (text.strip().splitlines()[0] if text.strip() else ""),
        "flags": flags,
        "flag_count": len(flags),
        "text": text[:20000],
        "docs": {"python": CMDS_DOC_URL % name, "mel": MEL_DOC_URL % name},
        "note": "" if text else "Maya returned no synopsis (the command may be a Python only wrapper); see the docs URL",
    }


def _parse_flags(text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for line in text.splitlines():
        m = _FLAG_RE.match(line)
        if not m:
            continue
        short, long_name, rest = m.groups()
        args = rest.replace("(multi-use)", "").replace("(Query Arg Mandatory)", "").replace("(Query Arg Optional)", "").strip()
        entry = {"short": short, "long": long_name, "args": args}
        if "multi-use" in rest:
            entry["multi"] = "true"
        out.append(entry)
    return out


# node types ----------------------------------------------------------------
@command("introspect.node_type_schema")
def node_type_schema(node_type: str, max_attrs: int = 300, keyable_only: bool = False) -> Dict[str, Any]:
    """Attributes of a node type with type, keyable, default, min/max and enums.

    Creates a temporary node with undo recording paused, reads it, deletes it.
    The scene is unchanged afterwards. Results are cached per session.
    """
    _util.require_maya()
    node_type = node_type.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", node_type):
        raise BridgeError("node_type must be a plain type name like 'polySphere' or 'mesh'")
    cache_key = "%s|%d|%d" % (node_type, int(max_attrs), int(keyable_only))
    if cache_key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[cache_key]
    try:
        cmds.undoInfo(stateWithoutFlush=False)
    except Exception:
        pass
    created: List[str] = []
    try:
        try:
            node = cmds.createNode(node_type, name="automaya_schema_tmp#", skipSelect=True)
        except Exception as exc:
            raise BridgeError("cannot create a %r node (%s). Abstract types have no instances; try introspect.list_node_types(filter=...)" % (node_type, exc))
        if isinstance(node, (list, tuple)):
            node = node[0] if node else ""
        if not node:
            raise BridgeError("createNode returned nothing for %r" % node_type)
        created.append(node)
        try:
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        except Exception:
            parents = []
        created.extend(p for p in parents if p and "automaya_schema_tmp" in p)
        attrs = _read_attrs(node, int(max_attrs), bool(keyable_only))
        inherits = _inheritance(node_type)
        result = {
            "node_type": node_type,
            "inherits": inherits,
            "is_dag": "dagNode" in inherits,
            "is_shape": "shape" in inherits,
            "attributes": attrs,
            "attribute_count": len(attrs),
            "truncated": len(attrs) >= int(max_attrs),
            "docs": NODE_DOC_URL % node_type,
        }
    finally:
        for n in reversed(created):
            try:
                if cmds.objExists(n):
                    cmds.delete(n)
            except Exception:
                pass
        try:
            cmds.undoInfo(stateWithoutFlush=True)
        except Exception:
            pass
    _SCHEMA_CACHE[cache_key] = result
    return result


def _inheritance(node_type: str) -> List[str]:
    try:
        chain = cmds.nodeType(node_type, isTypeName=True, inherited=True) or []
        return list(chain)
    except Exception:
        return []


def _read_attrs(node: str, max_attrs: int, keyable_only: bool) -> List[Dict[str, Any]]:
    try:
        names = cmds.listAttr(node, keyable=True) if keyable_only else cmds.listAttr(node)
    except Exception:
        names = []
    names = [n for n in (names or []) if "." not in n and "[" not in n]
    out: List[Dict[str, Any]] = []
    for a in names[:max_attrs]:
        out.append(_attr_info(node, a))
    return out


def _q(attr: str, node: str, **flag: Any) -> Any:
    try:
        return cmds.attributeQuery(attr, node=node, **flag)
    except Exception:
        return None


def _attr_info(node: str, attr: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": attr}
    try:
        info["type"] = cmds.getAttr("%s.%s" % (node, attr), type=True)
    except Exception:
        info["type"] = _q(attr, node, attributeType=True)
    short = _q(attr, node, shortName=True)
    if short and short != attr:
        info["short"] = short
    for key, flag in (("keyable", "keyable"), ("multi", "multi"), ("hidden", "hidden"), ("readable", "readable"), ("writable", "writable")):
        v = _q(attr, node, **{flag: True})
        if v is not None and (key in ("keyable", "multi", "hidden") and v or key in ("readable", "writable") and v is False):
            info[key] = bool(v)
    if _q(attr, node, minExists=True):
        mn = _q(attr, node, minimum=True)
        if mn:
            info["min"] = mn[0] if len(mn) == 1 else list(mn)
    if _q(attr, node, maxExists=True):
        mx = _q(attr, node, maximum=True)
        if mx:
            info["max"] = mx[0] if len(mx) == 1 else list(mx)
    default = _q(attr, node, listDefault=True)
    if default:
        info["default"] = default[0] if len(default) == 1 else list(default)
    enum = _q(attr, node, listEnum=True)
    if enum:
        info["enum"] = enum[0].split(":") if isinstance(enum, (list, tuple)) else str(enum).split(":")
    children = _q(attr, node, listChildren=True)
    if children:
        info["children"] = list(children)
    parent = _q(attr, node, listParent=True)
    if parent:
        info["parent"] = parent[0] if isinstance(parent, (list, tuple)) else parent
    return info


@command("introspect.list_node_types")
def list_node_types(filter: str | None = None, plugin_only: bool = False, include_inheritance: bool = False, limit: int = 500, offset: int = 0) -> Dict[str, Any]:
    """Concrete node types, optionally only those provided by loaded plugins."""
    _util.require_maya()
    try:
        types = list(cmds.allNodeTypes(includeAbstract=False) or [])
    except Exception:
        types = []
    by_plugin: Dict[str, str] = {}
    if plugin_only or include_inheritance:
        for plug in _loaded_plugins():
            try:
                for t in cmds.pluginInfo(plug, query=True, dependNode=True) or []:
                    by_plugin[t] = plug
            except Exception:
                continue
    if plugin_only:
        types = [t for t in types if t in by_plugin]
    if filter:
        low = filter.lower()
        types = [t for t in types if low in t.lower()]
    types = sorted(set(types))
    total = len(types)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 5000))
    page = types[offset: offset + limit]
    rows: Any
    if include_inheritance:
        rows = [{"type": t, "inherits": _inheritance(t), "plugin": by_plugin.get(t)} for t in page]
    else:
        rows = page
    return {"node_types": rows, "total": total, "offset": offset, "next_offset": offset + limit if offset + limit < total else None, "plugin_only": bool(plugin_only)}


# plugins -------------------------------------------------------------------
def _loaded_plugins() -> List[str]:
    try:
        return list(cmds.pluginInfo(query=True, listPlugins=True) or [])
    except Exception:
        return []


@command("introspect.plugin_info")
def plugin_info(name: str) -> Dict[str, Any]:
    """Version, path, commands and node types of one plugin (loaded or not)."""
    _util.require_maya()
    info: Dict[str, Any] = {"name": name}
    try:
        info["loaded"] = bool(cmds.pluginInfo(name, query=True, loaded=True))
    except Exception:
        info["loaded"] = False
    if not info["loaded"]:
        info["hint"] = "not loaded; scene.* tools load plugins on demand, or call cmds.loadPlugin(%r)" % name
        return info
    for key, flag in (("version", "version"), ("path", "path"), ("vendor", "vendor"), ("api_version", "apiVersion"), ("autoload", "autoload"), ("commands", "command"), ("node_types", "dependNode"), ("data_types", "data"), ("tools", "tool")):
        try:
            v = cmds.pluginInfo(name, query=True, **{flag: True})
        except Exception:
            continue
        if v not in (None, [], ""):
            info[key] = v
    return info


@command("introspect.list_plugins")
def list_plugins(loaded_only: bool = True, details: bool = False) -> Dict[str, Any]:
    """Loaded plugins (and, with loaded_only=False, every plugin file on the plugin path)."""
    _util.require_maya()
    loaded = _loaded_plugins()
    rows: List[Any] = []
    for p in loaded:
        if details:
            rows.append(plugin_info(p))
        else:
            entry: Dict[str, Any] = {"name": p, "loaded": True}
            try:
                entry["version"] = cmds.pluginInfo(p, query=True, version=True)
            except Exception:
                pass
            rows.append(entry)
    available: List[Dict[str, str]] = []
    if not loaded_only:
        try:
            paths = cmds.pluginInfo(query=True, listPluginsPath=True) or []
        except Exception:
            paths = []
        seen = set()
        for folder in paths:
            try:
                for fn in sorted(os.listdir(folder)):
                    stem, ext = os.path.splitext(fn)
                    if ext.lower() in (".so", ".mll", ".bundle", ".py") and stem not in seen and stem not in loaded:
                        seen.add(stem)
                        available.append({"name": stem, "path": os.path.join(folder, fn)})
            except OSError:
                continue
    return {"loaded": rows, "loaded_count": len(loaded), "available": available, "available_count": len(available)}


# environment ---------------------------------------------------------------
@command("introspect.env_info")
def env_info() -> Dict[str, Any]:
    """Maya version, API, Python, OS, key directories, modules, renderer, Qt."""
    _util.require_maya()
    info: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "os": platform.platform(),
        "maya_app_dir": os.environ.get("MAYA_APP_DIR", ""),
        "maya_location": os.environ.get("MAYA_LOCATION", ""),
        "maya_module_path": os.environ.get("MAYA_MODULE_PATH", ""),
    }
    for key, flag in (("maya_version", "version"), ("maya_api", "apiVersion"), ("batch", "batch"), ("install_dir", "installedVersion"), ("os_name", "operatingSystem"), ("cut", "cutIdentifier")):
        try:
            info[key] = cmds.about(**{flag: True})
        except Exception:
            pass
    try:
        info["modules"] = list(cmds.moduleInfo(listModules=True) or [])
    except Exception:
        info["modules"] = []
    try:
        info["script_paths"] = [p for p in (os.environ.get("MAYA_SCRIPT_PATH", "") or "").split(os.pathsep) if p][:40]
    except Exception:
        info["script_paths"] = []
    try:
        info["workspace_root"] = cmds.workspace(query=True, rootDirectory=True)
        info["images_dir"] = cmds.workspace(fileRuleEntry="images")
        info["scenes_dir"] = cmds.workspace(fileRuleEntry="scene")
    except Exception:
        pass
    try:
        info["scene"] = cmds.file(query=True, sceneName=True) or "untitled"
        info["units"] = {"linear": cmds.currentUnit(query=True, linear=True), "angle": cmds.currentUnit(query=True, angle=True), "time": cmds.currentUnit(query=True, time=True)}
        info["up_axis"] = cmds.upAxis(query=True, axis=True)
    except Exception:
        pass
    try:
        info["current_renderer"] = cmds.getAttr("defaultRenderGlobals.currentRenderer")
    except Exception:
        pass
    info["loaded_plugins_count"] = len(_loaded_plugins())
    info["qt"] = _qt_versions()
    try:
        info["openmaya2"] = _has_openmaya()
    except Exception:
        info["openmaya2"] = False
    return info


def _qt_versions() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for mod in ("PySide6", "PySide2"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
            qtcore = __import__(mod + ".QtCore", fromlist=["QtCore"])
            out["qt"] = getattr(qtcore, "qVersion", lambda: "?")()
            break
        except Exception:
            continue
    return out


def _has_openmaya() -> bool:
    try:
        import maya.api.OpenMaya as om  # type: ignore

        return hasattr(om, "MFnMesh")
    except ImportError:
        return False


# UI ---------------------------------------------------------------------------
@command("introspect.ui_tree")
def ui_tree(root: str | None = None, depth: int = 2, max_nodes: int = 500) -> Dict[str, Any]:
    """Windows and panels (or the children of one control) as a tree."""
    _util.require_maya()
    budget = [max(1, int(max_nodes))]
    depth = max(0, int(depth))
    if root:
        if not _ui_exists(root):
            raise BridgeError("no UI element named %r; call introspect.ui_tree() without root to list windows and panels" % root)
        return {"root": root, "tree": _ui_node(root, depth, budget), "truncated": budget[0] <= 0}
    windows = _lsui(windows=True)
    panels = _lsui(panels=True)
    tree = {"windows": [], "panels": []}
    for w in windows:
        if budget[0] <= 0:
            break
        tree["windows"].append(_ui_node(w, depth, budget))
    for p in panels:
        if budget[0] <= 0:
            break
        entry: Dict[str, Any] = {"name": p}
        try:
            entry["type"] = cmds.getPanel(typeOf=p)
        except Exception:
            pass
        try:
            entry["label"] = cmds.panel(p, query=True, label=True)
        except Exception:
            pass
        tree["panels"].append(entry)
        budget[0] -= 1
    return {"root": None, "tree": tree, "truncated": budget[0] <= 0, "window_count": len(windows), "panel_count": len(panels)}


def _lsui(**flag: Any) -> List[str]:
    try:
        return list(cmds.lsUI(**flag) or [])
    except Exception:
        return []


def _ui_exists(name: str) -> bool:
    for fn in ("control", "layout", "window", "menu", "panel"):
        try:
            if getattr(cmds, fn)(name, exists=True):
                return True
        except Exception:
            continue
    return False


def _ui_node(name: str, depth: int, budget: List[int]) -> Dict[str, Any]:
    budget[0] -= 1
    entry: Dict[str, Any] = {"name": name}
    try:
        entry["type"] = cmds.objectTypeUI(name)
    except Exception:
        entry["type"] = "unknown"
    for fn, flag in (("window", "title"), ("control", "label"), ("control", "annotation")):
        try:
            v = getattr(cmds, fn)(name, query=True, **{flag: True})
            if v:
                entry[flag] = v
        except Exception:
            continue
    try:
        entry["visible"] = bool(cmds.control(name, query=True, visible=True))
    except Exception:
        pass
    kids: List[str] = []
    try:
        kids = list(cmds.layout(name, query=True, childArray=True) or [])
    except Exception:
        kids = []
    if not kids and entry.get("type") == "window":
        try:
            kids = list(cmds.window(name, query=True, menuArray=True) or [])
        except Exception:
            kids = []
    if kids:
        entry["child_count"] = len(kids)
        if depth > 0:
            shown = []
            for k in kids:
                if budget[0] <= 0:
                    break
                shown.append(_ui_node(k, depth - 1, budget))
            entry["children"] = shown
    return entry


@command("introspect.list_menus")
def list_menus(main_window_only: bool = True, limit: int = 200) -> Dict[str, Any]:
    """Menus with labels; by default only the main window menu bar."""
    _util.require_maya()
    names: List[str] = []
    if main_window_only and mel is not None:
        try:
            main = mel.eval("$tmp = $gMainWindow")
            names = list(cmds.window(main, query=True, menuArray=True) or []) if main else []
        except Exception:
            names = []
    if not names:
        names = _lsui(menus=True)
    rows = []
    for m in names[: int(limit)]:
        entry: Dict[str, Any] = {"name": m}
        try:
            entry["label"] = cmds.menu(m, query=True, label=True)
        except Exception:
            pass
        try:
            entry["item_count"] = cmds.menu(m, query=True, numberOfItems=True)
        except Exception:
            pass
        rows.append(entry)
    return {"menus": rows, "total": len(names), "hint": "introspect.ui_tree(root=<menu>) lists a menu's items"}


@command("introspect.list_panels")
def list_panels() -> Dict[str, Any]:
    """Every panel with its type, which are visible, and the one with focus."""
    _util.require_maya()
    rows = []
    try:
        visible = set(cmds.getPanel(visiblePanels=True) or [])
    except Exception:
        visible = set()
    try:
        focus = cmds.getPanel(withFocus=True)
    except Exception:
        focus = None
    for p in _lsui(panels=True):
        entry: Dict[str, Any] = {"name": p, "visible": p in visible}
        try:
            entry["type"] = cmds.getPanel(typeOf=p)
        except Exception:
            pass
        if entry.get("type") == "modelPanel":
            try:
                entry["camera"] = cmds.modelPanel(p, query=True, camera=True)
            except Exception:
                pass
        rows.append(entry)
    return {"panels": rows, "focused": focus, "visible": sorted(visible)}


@command("introspect.hotkeys")
def hotkeys(search: str | None = None, limit: int = 100) -> Dict[str, Any]:
    """Best effort hotkey listing from the current hotkey set (assignCommand table)."""
    _util.require_maya()
    out: Dict[str, Any] = {}
    try:
        out["hotkey_set"] = cmds.hotkeySet(query=True, current=True)
    except Exception:
        out["hotkey_set"] = None
    rows: List[Dict[str, Any]] = []
    try:
        n = int(cmds.assignCommand(query=True, numElements=True) or 0)
    except Exception:
        n = 0
    low = (search or "").lower()
    for i in range(1, n + 1):
        try:
            name = cmds.assignCommand(i, query=True, name=True)
            keys = cmds.assignCommand(i, query=True, keyString=True) or []
        except Exception:
            continue
        key = keys[0] if keys and isinstance(keys, (list, tuple)) else (keys if isinstance(keys, str) else "")
        if not key or key == "NONE":
            continue
        entry = {"name": name, "key": key}
        if isinstance(keys, (list, tuple)) and len(keys) >= 4:
            mods = [m for m, on in (("alt", keys[1]), ("ctrl", keys[2]), ("shift", keys[3])) if str(on) == "1"]
            if mods:
                entry["modifiers"] = mods
        try:
            entry["annotation"] = cmds.assignCommand(i, query=True, annotation=True)
        except Exception:
            pass
        if low and low not in json.dumps(entry).lower():
            continue
        rows.append(entry)
        if len(rows) >= int(limit):
            break
    out["hotkeys"] = rows
    out["count"] = len(rows)
    out["note"] = "Maya exposes hotkeys through assignCommand; use the Hotkey Editor for the full list" if not rows else ""
    return out


@command("introspect.option_vars")
def option_vars(prefix: str = "", limit: int = 200, with_values: bool = True) -> Dict[str, Any]:
    """optionVar names (and values) matching a prefix."""
    _util.require_maya()
    try:
        names = sorted(cmds.optionVar(list=True) or [])
    except Exception:
        names = []
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    total = len(names)
    names = names[: int(limit)]
    if not with_values:
        return {"names": names, "total": total}
    values: Dict[str, Any] = {}
    for n in names:
        try:
            values[n] = cmds.optionVar(query=n)
        except Exception:
            values[n] = None
    return {"option_vars": values, "total": total, "shown": len(values)}


@command("introspect.workspace_info")
def workspace_info() -> Dict[str, Any]:
    """Current project: root, file rules, recent files."""
    _util.require_maya()
    info: Dict[str, Any] = {}
    try:
        info["root"] = cmds.workspace(query=True, rootDirectory=True)
        info["active"] = cmds.workspace(query=True, active=True)
    except Exception:
        pass
    rules: Dict[str, str] = {}
    try:
        flat = cmds.workspace(query=True, fileRuleList=True) or []
        for r in flat:
            try:
                rules[r] = cmds.workspace(fileRuleEntry=r)
            except Exception:
                continue
    except Exception:
        pass
    info["file_rules"] = rules
    try:
        recent = cmds.optionVar(query="RecentFilesList")
        info["recent_files"] = list(recent) if isinstance(recent, (list, tuple)) else ([recent] if recent else [])
    except Exception:
        info["recent_files"] = []
    try:
        info["scene"] = cmds.file(query=True, sceneName=True) or "untitled"
        info["modified"] = bool(cmds.file(query=True, modified=True))
    except Exception:
        pass
    return info


# docs -----------------------------------------------------------------------
def _index_path() -> str:
    return os.path.join(os.path.dirname(prefs.prefs_path()), INDEX_FILE)


def _load_index() -> Dict[str, Dict[str, Any]]:
    if _INDEX:
        return _INDEX
    path = _index_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("commands"):
            _INDEX.update(data["commands"])
            return _INDEX
    except (OSError, ValueError):
        pass
    return _INDEX


def build_docs_index(force: bool = False, max_commands: int = 6000) -> Dict[str, Any]:
    """Build (or load) the offline synopsis index. Slow the first time (one cmds.help per command)."""
    if not force and _load_index():
        return {"commands": len(_INDEX), "path": _index_path(), "built": False}
    started = time.time()
    _INDEX.clear()
    for name in _all_command_names()[:max_commands]:
        try:
            text = cmds.help(name)
        except Exception:
            text = ""
        text = text if isinstance(text, str) else ""
        flags = [f["long"] for f in _parse_flags(text)]
        synopsis = ""
        for line in text.splitlines():
            if line.strip().lower().startswith("synopsis"):
                synopsis = line.strip()[:200]
                break
        _INDEX[name] = {"synopsis": synopsis, "flags": flags[:80]}
    try:
        with open(_index_path(), "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "built_at": time.time(), "commands": _INDEX}, fh)
    except OSError:
        pass
    return {"commands": len(_INDEX), "path": _index_path(), "built": True, "seconds": round(time.time() - started, 2)}


@command("introspect.search_docs")
def search_docs(query: str, limit: int = 20, rebuild: bool = False) -> Dict[str, Any]:
    """Search command names, synopses and flag names offline. Builds the index on first use."""
    _util.require_maya()
    q = (query or "").strip().lower()
    if not q:
        raise BridgeError("query must not be empty")
    build = build_docs_index(force=bool(rebuild))
    words = [w for w in re.split(r"[^a-z0-9]+", q) if w]
    hits = []
    for name, entry in _INDEX.items():
        low = name.lower()
        score = 0.0
        if low == q:
            score += 100
        elif low.startswith(q):
            score += 60
        elif q in low:
            score += 40
        for w in words:
            if w in low:
                score += 15
            if any(w in f.lower() for f in entry.get("flags", [])):
                score += 6
            if w in entry.get("synopsis", "").lower():
                score += 3
        if score > 0:
            hits.append((score, name))
    hits.sort(key=lambda h: (-h[0], h[1]))
    results = []
    for score, name in hits[: int(limit)]:
        entry = _INDEX[name]
        results.append({"command": name, "score": score, "synopsis": entry.get("synopsis", ""), "flags": entry.get("flags", [])[:12], "url": CMDS_DOC_URL % name})
    return {"query": query, "results": results, "total_hits": len(hits), "index": build}


def _to_snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


@command("introspect.api_reference_hint")
def api_reference_hint(topic: str) -> Dict[str, Any]:
    """URLs the agent can fetch for docs on a cmds command, node type or OpenMaya class."""
    _util.require_maya()
    t = (topic or "").strip()
    if not t:
        raise BridgeError("topic must be a command, node type or OpenMaya class name")
    out: Dict[str, Any] = {"topic": t, "urls": {}, "patterns": {
        "cmds": CMDS_DOC_URL % "<command>",
        "mel": MEL_DOC_URL % "<command>",
        "node": NODE_DOC_URL % "<nodeType>",
        "openmaya2_class": OM_DOC_URL % "<m_lower_snake>",
        "openmaya2_index": OM_INDEX_URL,
        "python_api_guide": DEVKIT_URL,
        "user_guide_search": USER_GUIDE_SEARCH % "<query>",
    }}
    kind = []
    if re.match(r"^M[A-Z][A-Za-z0-9]*$", t):
        snake = _to_snake(t)
        out["urls"]["openmaya2"] = OM_DOC_URL % snake
        out["urls"]["openmaya2_index"] = OM_INDEX_URL
        kind.append("openmaya_class")
    if hasattr(cmds, t) or t in _all_command_names():
        out["urls"]["cmds"] = CMDS_DOC_URL % t
        out["urls"]["mel"] = MEL_DOC_URL % t
        kind.append("command")
    try:
        if cmds.nodeType(t, isTypeName=True):
            out["urls"]["node"] = NODE_DOC_URL % t
            kind.append("node_type")
    except Exception:
        pass
    if not kind:
        out["urls"]["cmds_guess"] = CMDS_DOC_URL % t
        out["urls"]["search"] = USER_GUIDE_SEARCH % t.replace(" ", "+")
        kind.append("unknown")
    out["kind"] = kind
    return out
