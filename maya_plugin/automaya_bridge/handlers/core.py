"""core.* commands: handshake, code execution, logs, change feed."""
from __future__ import annotations

import ast
import contextlib
import io
import os
import platform
import sys
import traceback
from typing import Any, Dict, List

from .. import events, prefs, protocol, registry, server
from ..registry import command
from ._util import BridgeError

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore

_BLOCKED_IMPORTS = {"subprocess", "ctypes", "shutil", "socket", "http", "urllib", "ftplib", "smtplib", "pickle", "marshal"}
_BLOCKED_NAMES = {"eval", "exec", "compile", "__import__", "open"}


def _validate_code(code: str) -> None:
    """Static check used when safe mode is on. Blocks shell/network/file access."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise BridgeError("syntax error in code: %s" % exc)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            for n in names:
                if n in _BLOCKED_IMPORTS or n == "os":
                    raise BridgeError("safe mode blocks import of %r" % n)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_NAMES:
            raise BridgeError("safe mode blocks call to %s()" % node.func.id)
        if isinstance(node, ast.Attribute) and node.attr in ("system", "popen", "remove", "rmtree", "unlink"):
            raise BridgeError("safe mode blocks attribute %r" % node.attr)


_EXEC_GLOBALS: Dict[str, Any] = {}


def _exec_namespace() -> Dict[str, Any]:
    if not _EXEC_GLOBALS:
        _EXEC_GLOBALS["__name__"] = "__automaya__"
        if cmds is not None:
            _EXEC_GLOBALS["cmds"] = cmds
            _EXEC_GLOBALS["mel"] = mel
            try:
                import maya.api.OpenMaya as om  # type: ignore

                _EXEC_GLOBALS["om"] = om
            except ImportError:
                pass
            try:
                import pymel.core as pm  # type: ignore

                _EXEC_GLOBALS["pm"] = pm
            except Exception:
                pass
    return _EXEC_GLOBALS


@command("core.ping")
def ping() -> Dict[str, Any]:
    return {"pong": True}


@command("core.handshake")
def handshake() -> Dict[str, Any]:
    """Version and capability negotiation, called once per MCP session."""
    p = prefs.load()
    info: Dict[str, Any] = {
        "plugin_version": server.PLUGIN_VERSION,
        "protocol_version": protocol.PROTOCOL_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "commands": registry.names(),
        "integrations": p.get("integrations", {}),
        "keys_configured": prefs.configured_keys(),
        "safe_mode": bool(p.get("safe_mode")) or os.environ.get("AUTOMAYA_SAFE_MODE") == "1",
        "events_active": events.BUS.active,
        "broadcast": events.BUS.broadcaster is not None and events.BUS.broadcaster.running,
    }
    if cmds is not None:
        try:
            info["maya_version"] = cmds.about(version=True)
            info["maya_api"] = cmds.about(apiVersion=True)
            info["batch"] = cmds.about(batch=True)
            info["scene"] = cmds.file(query=True, sceneName=True) or "untitled"
            info["units"] = {"linear": cmds.currentUnit(query=True, linear=True), "angle": cmds.currentUnit(query=True, angle=True), "time": cmds.currentUnit(query=True, time=True)}
            info["up_axis"] = cmds.upAxis(query=True, axis=True)
        except Exception:
            pass
    return info


@command("core.list_commands")
def list_commands() -> Dict[str, Any]:
    return registry.describe()


@command("core.execute_python", mutates=True)
def execute_python(code: str, allow_unsafe: bool = False, capture: bool = True) -> Dict[str, Any]:
    """Run Python inside Maya. Globals persist between calls (cmds, mel, om, pm preloaded).

    If the last statement is an expression its value is returned as ``result``.
    """
    if not isinstance(code, str) or not code.strip():
        raise BridgeError("code must be a non empty string")
    if len(code) > 200_000:
        raise BridgeError("code exceeds 200 KB; split it into smaller calls")
    safe = (prefs.load().get("safe_mode") or os.environ.get("AUTOMAYA_SAFE_MODE") == "1") and not allow_unsafe
    if safe:
        _validate_code(code)
    ns = _exec_namespace()
    stdout = io.StringIO()
    result: Any = None
    error: str | None = None
    try:
        tree = ast.parse(code, mode="exec")
        last_expr = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_expr = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(stdout) if capture else contextlib.nullcontext():
            exec(compile(tree, "<automaya>", "exec"), ns)  # noqa: S102, this is the tool's purpose
            if last_expr is not None:
                result = eval(compile(last_expr, "<automaya>", "eval"), ns)  # noqa: S307
    except Exception:
        error = traceback.format_exc()
    out = {"stdout": stdout.getvalue()[-20000:], "result": _jsonable(result), "error": error}
    if error:
        raise BridgeError("python raised:\n" + error.strip().splitlines()[-1] + "\n" + error[-4000:])
    return out


@command("core.execute_mel", mutates=True)
def execute_mel(code: str) -> Dict[str, Any]:
    if mel is None:
        raise BridgeError("MEL is unavailable outside Maya")
    try:
        return {"result": _jsonable(mel.eval(code))}
    except RuntimeError as exc:
        raise BridgeError("MEL error: %s" % exc)


@command("core.get_log")
def get_log(count: int = 100, level: str | None = None) -> List[Dict[str, Any]]:
    return server.LOG.tail(int(count), level)


@command("core.drain_changes")
def drain_changes(since_seq: int = 0, limit: int = 500, kinds: List[str] | None = None, human_only: bool = False, summary: bool = False) -> Dict[str, Any]:
    """Return scene change events since ``since_seq``. Starts tracking if needed."""
    if not events.BUS.active:
        events.BUS.start()
    if summary:
        return events.BUS.summary(since_seq=int(since_seq))
    return events.BUS.drain(since_seq=int(since_seq), limit=int(limit), kinds=kinds, human_only=human_only)


@command("core.events_control", mutates=False)
def events_control(action: str = "status", nodes: List[str] | None = None, transform_only: bool | None = None) -> Dict[str, Any]:
    if action == "start":
        events.BUS.start()
    elif action == "stop":
        events.BUS.stop()
    elif action == "watch":
        events.BUS.watch(nodes or [])
    if transform_only is not None:
        events.BUS.transform_only = bool(transform_only)
    return {"active": events.BUS.active, "watched": sorted(events.BUS._watched), "transform_only": events.BUS.transform_only}


@command("core.set_agent_activity")
def set_agent_activity(human: bool = True) -> Dict[str, Any]:
    """Server toggles this around batches so events are labelled agent vs human."""
    events.BUS.human_activity = bool(human)
    return {"human": events.BUS.human_activity}


def _jsonable(value: Any) -> Any:
    try:
        import json

        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        return repr(value)
