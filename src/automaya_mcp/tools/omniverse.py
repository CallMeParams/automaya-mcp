"""Omniverse tools: detect and drive the NVIDIA Omniverse Maya Native Connector
(Maya 2024.2), with a plain mayaUsd fallback that works everywhere."""
from __future__ import annotations

from typing import List, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import READ, WRITE, ToolContext


class RunMenuItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(..., min_length=1, max_length=200, description="Case insensitive substring of the menu item or shelf button label", examples=["Save Content"])
    source: Literal["menu", "shelf"] = Field(default="menu", description="Search the Omniverse main menu or the Omniverse shelf")


class OmniExportUsdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., min_length=1, description="Local .usd/.usda/.usdc file, or omniverse://server/path.usd (needs the connector)",
                      examples=["C:/shots/sh010_layout.usda", "omniverse://localhost/Projects/demo/sh010.usd"])
    nodes: List[str] | None = Field(default=None, description="Nodes to export; default every top level transform except the startup cameras")
    animation: bool = Field(default=False, description="Export animation over the frame range")
    start: float | None = Field(default=None, description="First frame (with animation); default playback start")
    end: float | None = Field(default=None, description="Last frame (with animation); default playback end")


class LiveSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "join", "leave", "end", "merge", "share"] = Field(..., description="What to do with the live session")
    session_name: str = Field(default="", max_length=120, description="Session name, reported back only; the connector dialog asks for it")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_omni_status", annotations={"title": "Omniverse connector status", **READ})
    async def maya_omni_status() -> str:
        """Check whether the NVIDIA Omniverse Maya connector is installed (modules, plugins,
        Omniverse menu or shelf), whether mayaUsd is loaded and its version, the Maya
        version and live session hints. Returns path 'connector' or 'usd_only', setup
        steps when the connector is missing, and a warning on Maya 2025+ where NVIDIA
        discontinued it. Call this first before any other maya_omni_* tool."""
        return await ctx.run("omni.status")

    @mcp.tool(name="maya_omni_list_commands", annotations={"title": "List Omniverse commands and menu items", **READ})
    async def maya_omni_list_commands() -> str:
        """List what the connector actually exposes in this session: help entries and MEL
        procs mentioning omni, every Omniverse menu item (submenus walked, with label,
        command, source type and path) and every Omniverse shelf button. The connector's
        scripting API is undocumented, so use this to find labels for
        maya_omni_run_menu_item."""
        return await ctx.run("omni.list_commands")

    @mcp.tool(name="maya_omni_run_menu_item", annotations={"title": "Run an Omniverse menu item", **WRITE})
    async def maya_omni_run_menu_item(params: RunMenuItemInput) -> str:
        """Run one Omniverse menu item or shelf button found by case insensitive label
        substring, exactly as clicking it would (Python or MEL). Many items open a dialog
        the user must finish. Errors list the available labels when nothing or more than
        one item matches. Get labels from maya_omni_list_commands."""
        return await ctx.run("omni.run_menu_item", params.model_dump())

    @mcp.tool(name="maya_omni_export_usd", annotations={"title": "Export USD for Omniverse", **WRITE})
    async def maya_omni_export_usd(params: OmniExportUsdInput) -> str:
        """Export nodes (default all top level transforms) to USD. A local path goes
        through mayaUsd and opens in USD Composer and the Unreal USD stage. An
        omniverse:// URL needs the connector and only works if it exposes an export proc
        that takes a path; otherwise the error explains how to export locally and save to
        Nucleus from the Omniverse menu. Returns path, via and the nodes."""
        return await ctx.run("omni.export_usd", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_omni_live_session", annotations={"title": "Omniverse live session", **WRITE})
    async def maya_omni_live_session(params: LiveSessionInput) -> str:
        """Create, join, leave, end, merge or share an Omniverse live session by running the
        matching connector menu item or shelf button. Live sessions sync edits both ways
        with USD Composer (the RTX viewport). Without the connector it errors and points
        at the mayaUsd path (maya_livelink_export_usd plus reloading in Kit or Unreal)."""
        return await ctx.run("omni.live_session", params.model_dump())

    @mcp.tool(name="maya_omni_nucleus_hint", annotations={"title": "Nucleus and RTX viewport guide", **READ})
    async def maya_omni_nucleus_hint() -> str:
        """Nucleus URL shape (omniverse://localhost/Projects/<name>/<shot>.usd) with a
        suggestion from the scene name, how to log in to Enterprise Nucleus, the
        step by step USD Composer RTX live viewport workflow and the Maya 2025+
        deprecation note. Read only guidance, changes nothing."""
        return await ctx.run("omni.nucleus_hint")
