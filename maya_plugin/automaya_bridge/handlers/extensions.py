"""ext.* commands: drop-in Python scripts become bridge commands.

Any ``*.py`` file in one of the extension folders is imported and every public
function it defines is registered as ``ext.<module>.<function>``. The format is
deliberately tiny so scripts written for PatrickPalmer/MayaMCP work unchanged:

* plain functions with type hints and a docstring
* Maya imports inside the function (the file must import outside Maya too)
* JSON friendly arguments and return values
* a string starting with ``"Error:"`` means failure; the wrapper turns that
  into a ``BridgeError`` so the agent gets a proper error envelope

Search folders, in order:

1. ``maya_plugin/extensions/`` shipped with the repo
2. ``<MAYA_APP_DIR>/automaya/tools/`` (created if missing)
3. every folder in ``AUTOMAYA_EXT_PATH`` (``os.pathsep`` separated)

``EXT_INDEX`` mirrors the registry with type information the MCP server uses
to build real tool schemas.
"""
from __future__ import annotations

import functools
import importlib.util
import inspect
import os
import sys
import types
import typing
from typing import Any, Callable, Dict, List

from .. import registry, server
from ..registry import command
from ._util import BridgeError

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

PREFIX = "ext."
RESERVED = {"list", "reload", "source"}
QUERY_PREFIXES = ("get_", "find_", "list_", "query_", "read_", "is_", "has_")
MODULE_PREFIX = "automaya_ext_"
_SIMPLE_TYPES = {str: "str", int: "int", float: "float", bool: "bool", list: "list", dict: "dict", tuple: "list", set: "list"}

# command name -> {module, func, doc, params, mutates, file}
EXT_INDEX: Dict[str, Dict[str, Any]] = {}
# file path -> last error text, so ext.list can show what failed to import
LOAD_ERRORS: Dict[str, str] = {}


# folders ------------------------------------------------------------------------
def repo_folder() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "extensions"))


def user_folder() -> str:
    app_dir = os.environ.get("MAYA_APP_DIR")
    if not app_dir and cmds is not None:
        try:
            app_dir = cmds.internalVar(userAppDir=True)
        except Exception:
            app_dir = None
    if not app_dir:
        app_dir = os.path.join(os.path.expanduser("~"), "maya")
    return os.path.join(app_dir, "automaya", "tools")


def search_folders() -> List[str]:
    folders = [repo_folder(), user_folder()]
    extra = os.environ.get("AUTOMAYA_EXT_PATH", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            folders.append(os.path.abspath(os.path.expanduser(part)))
    seen: List[str] = []
    for f in folders:
        norm = os.path.normpath(f)
        if norm not in seen:
            seen.append(norm)
    return seen


def _ensure_user_folder() -> None:
    try:
        os.makedirs(user_folder(), exist_ok=True)
    except OSError as exc:
        _log("warn", "could not create extension folder %s: %s" % (user_folder(), exc))


def _log(level: str, text: str) -> None:
    try:
        server.LOG.add(level, text)
    except Exception:
        pass


# type mapping ---------------------------------------------------------------------
def _type_name(hint: Any) -> str:
    """Collapse a typing hint to one of str/int/float/bool/list/dict/any."""
    if hint is None or hint is inspect.Parameter.empty or hint is Any:
        return "any"
    if hint in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[hint]
    origin = typing.get_origin(hint)
    if origin is typing.Union or isinstance(hint, getattr(types, "UnionType", ())):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        names = [_type_name(a) for a in args]
        if len(set(names)) == 1:
            return names[0]
        return "any"
    if origin in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[origin]
    if origin is not None:
        return _type_name(origin)
    if isinstance(hint, type):
        for base, name in _SIMPLE_TYPES.items():
            if issubclass(hint, base):
                return name
    return "str"


def _is_optional(hint: Any) -> bool:
    if typing.get_origin(hint) is typing.Union or isinstance(hint, getattr(types, "UnionType", ())):
        return type(None) in typing.get_args(hint)
    return False


def _param_docs(doc: str) -> Dict[str, str]:
    """Pull ``name: text`` lines out of a Google style ``Args:`` block."""
    out: Dict[str, str] = {}
    in_args = False
    current = None
    for raw in doc.splitlines():
        line = raw.strip()
        if line.lower().startswith("args:") or line.lower().startswith("arguments:"):
            in_args = True
            continue
        if not in_args:
            continue
        if line.lower().startswith(("returns:", "return:", "raises:", "yields:", "example", "note")):
            break
        if not line:
            continue
        if ":" in line and not raw.startswith(" " * 12) and not line.startswith(("-", "*")):
            name, _, text = line.partition(":")
            name = name.strip().split(" ")[0]
            if name.isidentifier():
                current = name
                out[current] = text.strip()
                continue
        if current:
            out[current] = (out[current] + " " + line).strip()
    return out


def describe_params(func: Callable[..., Any]) -> Dict[str, Dict[str, Any]]:
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}
    docs = _param_docs(inspect.getdoc(func) or "")
    out: Dict[str, Dict[str, Any]] = {}
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return out
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        hint = hints.get(p.name, p.annotation)
        has_default = p.default is not inspect.Parameter.empty
        entry: Dict[str, Any] = {
            "type": _type_name(hint),
            "required": not has_default,
            "default": _jsonable(p.default) if has_default else None,
            "optional": has_default or _is_optional(hint),
        }
        if p.name in docs:
            entry["description"] = docs[p.name]
        out[p.name] = entry
    return out


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


# loading ------------------------------------------------------------------------
def _module_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _import_file(path: str) -> Any:
    """Import ``path`` as a uniquely named module without touching sys.path."""
    name = MODULE_PREFIX + _module_name(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot build an import spec for %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _wrap(func: Callable[..., Any], cmd_name: str) -> Callable[..., Any]:
    @functools.wraps(func)
    def wrapper(**params: Any) -> Any:
        result = func(**params)
        if isinstance(result, str) and result.startswith("Error:"):
            raise BridgeError(result)
        return result

    wrapper.__automaya_ext__ = cmd_name  # type: ignore[attr-defined]
    return wrapper


def _public_functions(module: Any) -> List[tuple]:
    out = []
    for name, obj in vars(module).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue  # imported from somewhere else, not defined here
        out.append((name, obj))
    return out


def _register_module(path: str, module: Any) -> List[str]:
    mod = _module_name(path)
    if mod in RESERVED:
        _log("warn", "extension %s skipped: module name %r is reserved" % (path, mod))
        return []
    added: List[str] = []
    for func_name, func in _public_functions(module):
        cmd_name = "%s%s.%s" % (PREFIX, mod, func_name)
        if cmd_name in registry._REGISTRY:
            _log("warn", "extension command %s already registered, %s skipped" % (cmd_name, path))
            continue
        mutates = not func_name.startswith(QUERY_PREFIXES)
        command(cmd_name, mutates=mutates)(_wrap(func, cmd_name))
        EXT_INDEX[cmd_name] = {
            "module": mod,
            "func": func_name,
            "doc": inspect.getdoc(func) or "",
            "params": describe_params(func),
            "mutates": mutates,
            "file": path,
        }
        added.append(cmd_name)
    return added


def _clear() -> List[str]:
    removed = [n for n in registry._REGISTRY if n.startswith(PREFIX) and n[len(PREFIX):] not in RESERVED]
    for n in removed:
        registry._REGISTRY.pop(n, None)
    EXT_INDEX.clear()
    LOAD_ERRORS.clear()
    for name in [m for m in sys.modules if m.startswith(MODULE_PREFIX)]:
        sys.modules.pop(name, None)
    return removed


def scan() -> List[str]:
    """Import every script in the search folders and register its functions.

    Existing ``ext.*`` commands are dropped first so this doubles as reload.
    Returns the command names that are registered afterwards.
    """
    _clear()
    _ensure_user_folder()
    seen_modules: Dict[str, str] = {}
    added: List[str] = []
    for folder in search_folders():
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".py") or fname.startswith(("_", ".")):
                continue
            path = os.path.join(folder, fname)
            mod = _module_name(path)
            if mod in seen_modules:
                _log("warn", "extension %s shadowed by %s" % (path, seen_modules[mod]))
                continue
            try:
                module = _import_file(path)
                names = _register_module(path, module)
            except Exception as exc:  # noqa: BLE001, one bad script must not stop the rest
                LOAD_ERRORS[path] = "%s: %s" % (type(exc).__name__, exc)
                _log("error", "extension %s failed to load: %s" % (path, LOAD_ERRORS[path]))
                continue
            seen_modules[mod] = path
            added.extend(names)
            if names:
                _log("info", "extension %s: %d command(s)" % (fname, len(names)))
    return added


# commands -----------------------------------------------------------------------
@command("ext.list")
def list_extensions() -> Dict[str, Any]:
    """Every extension command with its file, params and types, plus the folders scanned."""
    return {"folders": search_folders(), "commands": EXT_INDEX, "errors": LOAD_ERRORS, "count": len(EXT_INDEX)}


@command("ext.reload", mutates=False)
def reload_extensions() -> Dict[str, Any]:
    """Rescan the extension folders. Returns which commands appeared and disappeared."""
    before = set(n for n in registry._REGISTRY if n.startswith(PREFIX) and n[len(PREFIX):] not in RESERVED)
    after = set(scan())
    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "commands": sorted(after),
        "errors": LOAD_ERRORS,
        "folders": search_folders(),
    }


@command("ext.source")
def source(command: str) -> Dict[str, Any]:
    """Return the file and source code behind an ``ext.<module>.<func>`` command."""
    entry = EXT_INDEX.get(command)
    if entry is None:
        raise BridgeError("unknown extension command %r. ext.list shows the loaded ones." % command)
    spec = registry.get(command)
    func = inspect.unwrap(spec.func) if spec else None
    try:
        text = inspect.getsource(func) if func else ""
    except (OSError, TypeError) as exc:
        raise BridgeError("source unavailable for %s: %s" % (command, exc))
    return {"command": command, "file": entry["file"], "func": entry["func"], "module": entry["module"], "source": text}


scan()
