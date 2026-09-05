"""Core tools: status, code execution, logs, change feed.

This module is the reference pattern for every other tool module.
"""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import safety
from ..connection import discover_ports
from ._base import DESTRUCTIVE, READ, WRITE, ToolContext, dumps


class ExecutePythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., description="Python source to run inside Maya. cmds, mel, om (OpenMaya 2) and pm (pymel) are preloaded; the value of a trailing expression is returned.", min_length=1, max_length=200_000)
    timeout: float = Field(default=120.0, description="Seconds to wait for completion", ge=1, le=3600)


class ExecuteMelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., description="MEL source, e.g. 'polySphere -r 2'", min_length=1, max_length=100_000)


class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=50, ge=1, le=2000, description="Number of most recent log lines")
    level: str | None = Field(default=None, description="Filter: cmd, ok, error, warn, info, repl")


class DrainChangesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    since_seq: int | None = Field(default=None, ge=0, description="Return events after this sequence number. Omit to continue from the last drain in this session.")
    limit: int = Field(default=200, ge=1, le=5000)
    kinds: List[str] | None = Field(default=None, description="Only these kinds: attr_changed, node_added, node_removed, selection_changed, time_changed, connection_made, connection_broken, scene_opened, scene_saved, undo, redo")
    human_only: bool = Field(default=False, description="Only events caused by the user, not by this agent")
    summary: bool = Field(default=True, description="True: compact per node summary. False: raw event list")


class EventsControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(default="status", description="start | stop | watch | status")
    nodes: List[str] | None = Field(default=None, description="For action=watch: restrict attribute events to these nodes (empty list = all)")
    transform_only: bool | None = Field(default=None, description="Only emit translate/rotate/scale/visibility changes")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_get_status", annotations={"title": "Maya bridge status", **READ})
    async def maya_get_status() -> str:
        """Check the connection to Maya and return version, scene, units, enabled
        integrations and which provider keys are configured. Call this first in
        a session. If Maya is unreachable, the reply says how to start the bridge
        and lists any other Maya sessions found on nearby ports."""
        try:
            info = await ctx.raw("core.handshake", timeout=10.0)
            ctx.bridge.handshake = info
            info = dict(info)
            info["commands"] = len(info.get("commands", []))
            info["server_safe_mode"] = safety.safe_mode_enabled()
            return dumps(info)
        except Exception as exc:  # noqa: BLE001
            ports = discover_ports()
            return dumps({
                "connected": False,
                "error": str(exc),
                "other_bridges_on_ports": ports,
                "fix": "In Maya: import automaya_bridge; automaya_bridge.start()  (or open the AutoMaya console and press Connect). Set AUTOMAYA_PORT if you changed the port.",
            })

    @mcp.tool(name="maya_execute_python", annotations={"title": "Run Python in Maya", **DESTRUCTIVE})
    async def maya_execute_python(params: ExecutePythonInput) -> str:
        """Execute arbitrary Python inside the live Maya session, wrapped in an undo
        chunk that is rolled back if it raises. Use this for anything the typed
        tools do not cover. Returns stdout, the value of a trailing expression, or
        the traceback. Globals persist between calls. Blocked in safe mode when the
        code touches the shell, network or filesystem."""
        if safety.safe_mode_enabled():
            problems = safety.validate(params.code)
            if problems:
                return "Error: rejected by safe mode:\n- " + "\n- ".join(problems)
        return await ctx.run("core.execute_python", {"code": params.code}, timeout=params.timeout)

    @mcp.tool(name="maya_execute_mel", annotations={"title": "Run MEL in Maya", **DESTRUCTIVE})
    async def maya_execute_mel(params: ExecuteMelInput) -> str:
        """Execute a MEL snippet. Handy for commands that only exist in MEL
        (FBXExport options, some UI and render globals helpers)."""
        return await ctx.run("core.execute_mel", {"code": params.code})

    @mcp.tool(name="maya_get_console_log", annotations={"title": "Read the AutoMaya console log", **READ})
    async def maya_get_console_log(params: LogInput) -> str:
        """Return recent lines from the in-Maya console: every command run, its
        timing, results and errors. Useful after a failure to see what Maya said."""
        return await ctx.run("core.get_log", params.model_dump())

    @mcp.tool(name="maya_drain_changes", annotations={"title": "What changed in the scene", **READ})
    async def maya_drain_changes(params: DrainChangesInput) -> str:
        """Human edit awareness. Returns scene changes recorded by OpenMaya callbacks
        since the last drain: attribute edits, nodes added or removed, selection and
        time changes, with a flag telling whether the user or this agent caused them.
        Call it before continuing multi step work so you build on what the user did."""
        since = params.since_seq if params.since_seq is not None else ctx.last_event_seq
        try:
            result = await ctx.raw("core.drain_changes", {
                "since_seq": since, "limit": params.limit, "kinds": params.kinds,
                "human_only": params.human_only, "summary": params.summary,
            })
        except Exception as exc:  # noqa: BLE001
            from ._base import error_text

            return error_text(exc)
        if isinstance(result, dict) and "last_seq" in result:
            ctx.last_event_seq = int(result["last_seq"])
        return dumps(result)

    @mcp.tool(name="maya_events_control", annotations={"title": "Control change tracking", **WRITE})
    async def maya_events_control(params: EventsControlInput) -> str:
        """Start or stop change tracking, restrict it to specific nodes, or limit it
        to transform channels (cheap enough to leave on during animation playback)."""
        return await ctx.run("core.events_control", params.model_dump())

    @mcp.tool(name="maya_list_bridge_commands", annotations={"title": "List plugin commands", **READ})
    async def maya_list_bridge_commands() -> str:
        """List every low level command the installed plugin build supports, with
        parameters. Use when a tool reports unknown_command to see what this plugin
        version can do."""
        return await ctx.run("core.list_commands")
