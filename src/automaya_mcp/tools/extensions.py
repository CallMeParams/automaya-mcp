"""Extension tools: drop-in scripts in Maya become MCP tools.

The plugin scans ``maya_plugin/extensions/``, ``<MAYA_APP_DIR>/automaya/tools/``
and ``AUTOMAYA_EXT_PATH`` and registers every public function as
``ext.<module>.<func>``. This module exposes the generic tools (list, reload,
call, source) and, when Maya is reachable at startup, one typed MCP tool per
extension function named ``maya_ext_<module>_<func>`` with the script's
docstring and a schema built from its type hints.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, create_model

from ._base import READ, WRITE, ToolContext, dumps

log = logging.getLogger("automaya")

MAX_TOOL_NAME = 60
TOOL_PREFIX = "maya_ext_"
_TYPE_MAP = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "dict": dict, "any": Any}



def dynamic_tools(mcp: FastMCP) -> Dict[str, str]:
    """tool name -> ext command for the tools this module registered on ``mcp``."""
    store = getattr(mcp, "_automaya_ext_tools", None)
    if store is None:
        store = {}
        mcp._automaya_ext_tools = store  # type: ignore[attr-defined]
    return store


class ExtCallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(..., description="Extension command name, e.g. 'ext.modeling_tools.extrude_faces' (see maya_ext_list)", min_length=5)
    params: Dict[str, Any] = Field(default_factory=dict, description="Keyword arguments for the function, JSON types only")
    timeout: float = Field(default=120.0, ge=1, le=3600, description="Seconds to wait")


class ExtSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(..., description="Extension command name, e.g. 'ext.rigging_tools.find_skin_cluster'", min_length=5)


def tool_name_for(module: str, func: str, taken: set | None = None) -> str:
    """maya_ext_<module>_<func>, cleaned to [a-z0-9_], capped at 60 chars, unique."""
    raw = "%s%s_%s" % (TOOL_PREFIX, module, func)
    name = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
    name = re.sub(r"_+", "_", name)[:MAX_TOOL_NAME].rstrip("_")
    if taken is None:
        return name
    base, i = name, 2
    while name in taken:
        suffix = "_%d" % i
        name = base[: MAX_TOOL_NAME - len(suffix)] + suffix
        i += 1
    return name


def build_model(command: str, params: Dict[str, Dict[str, Any]]) -> type:
    """Pydantic model from the plugin's EXT_INDEX param descriptions."""
    fields: Dict[str, Any] = {}
    for name, info in params.items():
        py_type = _TYPE_MAP.get(str(info.get("type", "str")), str)
        desc = info.get("description") or "%s (%s)" % (name, info.get("type", "str"))
        if info.get("required", False):
            fields[name] = (py_type, Field(..., description=desc))
        else:
            default = info.get("default")
            fields[name] = (Optional[py_type], Field(default=default, description=desc))  # noqa: UP045, runtime object for create_model
    model_name = "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", command) if part) + "Input"
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


def _make_tool(ctx: ToolContext, command: str, model: type):
    async def ext_tool(params: model) -> str:  # type: ignore[valid-type]
        return await ctx.run(command, params.model_dump(), timeout=300.0)

    ext_tool.__annotations__ = {"params": model, "return": str}
    return ext_tool


def register_dynamic(mcp: FastMCP, ctx: ToolContext, index: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Register (or refresh) one MCP tool per extension command. Returns added/removed names."""
    wanted: Dict[str, str] = {}
    dynamic = dynamic_tools(mcp)
    taken = set(t.name for t in mcp._tool_manager.list_tools()) - set(dynamic)
    for command in sorted(index):
        entry = index[command]
        name = tool_name_for(str(entry.get("module", "")), str(entry.get("func", "")), taken)
        taken.add(name)
        wanted[name] = command
    removed = [n for n in list(dynamic) if n not in wanted]
    for n in removed:
        try:
            mcp.remove_tool(n)
        except Exception:  # noqa: BLE001
            pass
        dynamic.pop(n, None)
    added: List[str] = []
    for name, command in wanted.items():
        entry = index[command]
        if name in dynamic:
            try:
                mcp.remove_tool(name)
            except Exception:  # noqa: BLE001
                pass
        else:
            added.append(name)
        model = build_model(command, entry.get("params") or {})
        doc = (entry.get("doc") or "").strip() or "Extension function %s from %s" % (entry.get("func"), entry.get("file"))
        hints = WRITE if entry.get("mutates", True) else READ
        mcp.add_tool(_make_tool(ctx, command, model), name=name, description=doc, annotations={"title": "Extension: %s" % command, **hints})
        dynamic[name] = command
    return {"added": added, "removed": removed}


def _try_initial_sync(mcp: FastMCP, ctx: ToolContext) -> None:
    try:
        if not ctx.bridge.connect(timeout=1.0):
            return
        listing = ctx.bridge.call("ext.list", timeout=5.0)
    except Exception as exc:  # noqa: BLE001, Maya may simply not be up yet
        log.info("extension tools not registered at startup (%s)", exc)
        return
    if isinstance(listing, dict) and isinstance(listing.get("commands"), dict):
        result = register_dynamic(mcp, ctx, listing["commands"])
        if result["added"]:
            log.info("registered %d extension tool(s): %s", len(result["added"]), ", ".join(result["added"]))


async def _notify_tools_changed(mcp: FastMCP) -> bool:
    """Tell the client the tool list changed, when a session is available."""
    try:
        session = mcp.get_context().session
        await session.send_tool_list_changed()
        return True
    except Exception:  # noqa: BLE001, no request context or client does not support it
        return False


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_ext_list", annotations={"title": "List extension scripts", **READ})
    async def maya_ext_list() -> str:
        """List drop-in extension commands loaded in Maya: every public function
        from maya_plugin/extensions, <MAYA_APP_DIR>/automaya/tools and
        AUTOMAYA_EXT_PATH, with file, docstring, parameter types and whether it
        mutates the scene. Also shows which files failed to import. Call the
        commands with maya_ext_call, or with their maya_ext_<module>_<func> tool
        when it exists."""
        return await ctx.run("ext.list")

    @mcp.tool(name="maya_ext_reload", annotations={"title": "Reload extension scripts", **WRITE})
    async def maya_ext_reload() -> str:
        """Rescan the extension folders after adding or editing a script. New
        functions are callable at once through maya_ext_call; the typed
        maya_ext_<module>_<func> tools are refreshed in this server and the
        client is told the tool list changed when it supports that, otherwise
        they appear on the next server start."""
        try:
            result = await ctx.raw("ext.reload", timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            from ._base import error_text

            return error_text(exc)
        try:
            listing = await ctx.raw("ext.list", timeout=10.0)
            sync = register_dynamic(mcp, ctx, listing.get("commands", {}))
        except Exception as exc:  # noqa: BLE001
            sync = {"added": [], "removed": [], "error": str(exc)}
        notified = await _notify_tools_changed(mcp)
        result = dict(result)
        result["tools_added"] = sync.get("added", [])
        result["tools_removed"] = sync.get("removed", [])
        result["client_notified"] = notified
        result["note"] = (
            "Typed tools refreshed in this server and the client was notified." if notified
            else "Typed maya_ext_* tools are refreshed in this server; if your client does not pick them up, use maya_ext_call now and restart the server to see them listed."
        )
        return dumps(result)

    @mcp.tool(name="maya_ext_call", annotations={"title": "Call an extension function", **WRITE})
    async def maya_ext_call(params: ExtCallInput) -> str:
        """Call any extension command by name with a params dict. Works even when
        Maya was not running at server start (so no typed tool exists yet).
        Use maya_ext_list to find names and parameter types."""
        if not params.command.startswith("ext."):
            return "Error: command must start with 'ext.' (see maya_ext_list)"
        return await ctx.run(params.command, params.params, timeout=params.timeout)

    @mcp.tool(name="maya_ext_source", annotations={"title": "Show extension source", **READ})
    async def maya_ext_source(params: ExtSourceInput) -> str:
        """Return the file path and Python source of an extension function, so
        you can read exactly what a drop-in tool does before calling it or
        suggest a fix when it fails."""
        return await ctx.run("ext.source", {"command": params.command})

    _try_initial_sync(mcp, ctx)
