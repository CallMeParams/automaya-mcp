"""Live link tools: stream scene changes to an external viewport (Unreal) and
serve the pull side (scene graph, transforms, mesh buffers, live USD)."""
from __future__ import annotations

from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import READ, WRITE, ToolContext


class StartStreamInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int | None = Field(default=None, ge=1024, le=65535, description="Event port; default the plugin's event_port pref (9878)")
    transform_only: bool = Field(default=False, description="Only stream translate/rotate/scale/visibility changes (cheap during playback)")
    nodes: List[str] | None = Field(default=None, description="Restrict attribute events to these nodes; default all")


class SubscribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to watch; empty or omitted watches everything")
    transform_only: bool | None = Field(default=None, description="Switch transform only mode on or off")


class SnapshotGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str | None = Field(default=None, description="Only this node and its descendants; default whole scene")
    include_meshes: bool = Field(default=False, description="Add face/vertex counts and bounds for mesh transforms")
    include_cameras: bool = Field(default=True, description="Include camera transforms with lens data")
    include_lights: bool = Field(default=True, description="Include light transforms with intensity and colour")
    max_nodes: int = Field(default=5000, ge=1, le=100000)


class MeshBuffersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Mesh transform or shape", examples=["pSphere1"])
    world_space: bool = Field(default=True, description="Positions and normals in world space (False: object space)")
    include_normals: bool = Field(default=True)
    include_uvs: bool = Field(default=True)
    triangulate: bool = Field(default=True, description="Also return fan triangulated indices")
    max_chars: int = Field(default=60000, ge=1000, le=20_000_000, description="Truncate the JSON reply beyond this many characters; receivers should pull over the command port instead")


class TransformsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to read; default the selection")


class ExportUsdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, description="Output .usd/.usda/.usdc path; default a temp file", examples=["/tmp/shot010.usda"])
    nodes: List[str] | None = Field(default=None, description="Nodes to export; default the selection")
    animation: bool = Field(default=False, description="Export animation over the frame range")
    start: float | None = Field(default=None, description="First frame (with animation); default playback start")
    end: float | None = Field(default=None, description="Last frame (with animation); default playback end")


class SetFrameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: float = Field(..., description="Frame to move the timeline to", examples=[1001])


class PlayRangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(..., description="Range start frame")
    end: float = Field(..., description="Range end frame")
    play: bool = Field(default=False, description="Start playback after setting the range")
    loop: bool = Field(default=True, description="Loop playback")


class MarkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="Marker name, e.g. 'layout_done'", min_length=1, max_length=120)
    data: Dict[str, Any] | None = Field(default=None, description="Any JSON payload for the receiver")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_livelink_start", annotations={"title": "Start the live event stream", **WRITE})
    async def maya_livelink_start(params: StartStreamInput) -> str:
        """Start streaming scene changes as NDJSON on the event port (default 9878) for
        Unreal or another receiver, and turn on OpenMaya change callbacks. Returns port,
        subscriber count and whether callbacks are active. Run unreal/automaya_subscriber.py
        in Unreal to consume it."""
        return await ctx.run("livelink.start_stream", params.model_dump())

    @mcp.tool(name="maya_livelink_stop", annotations={"title": "Stop the live event stream", **WRITE})
    async def maya_livelink_stop() -> str:
        """Close the event port and disconnect subscribers. Change tracking for
        maya_drain_changes keeps working."""
        return await ctx.run("livelink.stop_stream")

    @mcp.tool(name="maya_livelink_status", annotations={"title": "Live stream status", **READ})
    async def maya_livelink_status() -> str:
        """Port, subscribers, events sent, events per second, last sequence number,
        watched nodes and whether OpenMaya callbacks are active."""
        return await ctx.run("livelink.status")

    @mcp.tool(name="maya_livelink_subscribe", annotations={"title": "Watch specific nodes", **WRITE})
    async def maya_livelink_subscribe(params: SubscribeInput) -> str:
        """Restrict streamed attribute events to a node list (or reset to all) and
        toggle transform only mode. Use it to keep the stream light during playback."""
        return await ctx.run("livelink.subscribe_nodes", params.model_dump())

    @mcp.tool(name="maya_livelink_snapshot", annotations={"title": "Scene graph snapshot for a receiver", **READ})
    async def maya_livelink_snapshot(params: SnapshotGraphInput) -> str:
        """Flat list of every transform with world matrix, local t/r/s, rotate order,
        visibility, camera lens data, light data and optional mesh stats, plus units,
        up axis and fps. This is what a receiver uses to build its mirror scene before
        applying live events."""
        return await ctx.run("livelink.snapshot_scene_graph", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_livelink_mesh_buffers", annotations={"title": "Mesh buffers for a receiver", **READ})
    async def maya_livelink_mesh_buffers(params: MeshBuffersInput) -> str:
        """Positions, normals, uvs, polygon and triangulated indices of one mesh as flat
        lists (OpenMaya 2, with a cmds fallback). Large meshes are truncated in this text
        reply; receivers should call the bridge command port directly, or use
        maya_livelink_export_usd for full assets."""
        data = params.model_dump()
        limit = data.pop("max_chars")
        return await ctx.run("livelink.get_mesh_buffers", data, timeout=300.0, limit=limit)

    @mcp.tool(name="maya_livelink_transforms", annotations={"title": "World matrices for nodes", **READ})
    async def maya_livelink_transforms(params: TransformsInput) -> str:
        """World matrices and local t/r/s for a node list (or the selection) in one
        call. Use it after attr_changed events with null values, or to poll a few
        nodes without callbacks."""
        return await ctx.run("livelink.get_transforms", params.model_dump())

    @mcp.tool(name="maya_livelink_export_usd", annotations={"title": "Live USD export for the receiver", **WRITE})
    async def maya_livelink_export_usd(params: ExportUsdInput) -> str:
        """Export nodes (or the selection) to USD and broadcast a usd_exported event so
        the Unreal subscriber reloads its USD stage. The way to get full geometry and
        materials across; transforms then stay live through the event stream."""
        return await ctx.run("livelink.export_usd_live", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_livelink_set_frame", annotations={"title": "Set the current frame", **WRITE})
    async def maya_livelink_set_frame(params: SetFrameInput) -> str:
        """Move Maya's timeline to a frame (the receiver, or you, drives time). Emits
        time_changed on the stream."""
        return await ctx.run("livelink.set_frame", params.model_dump())

    @mcp.tool(name="maya_livelink_play_range", annotations={"title": "Set playback range", **WRITE})
    async def maya_livelink_play_range(params: PlayRangeInput) -> str:
        """Set Maya's playback range and optionally start looping playback so the
        receiver sees animation streaming."""
        return await ctx.run("livelink.play_range", params.model_dump())

    @mcp.tool(name="maya_livelink_marker", annotations={"title": "Emit a sync marker", **WRITE})
    async def maya_livelink_marker(params: MarkerInput) -> str:
        """Push a named marker with a JSON payload onto the stream, for sync points
        like 'layout_done' or 'take_3' that the receiver can react to or log."""
        return await ctx.run("livelink.emit_marker", params.model_dump())

    @mcp.tool(name="maya_livelink_protocol", annotations={"title": "Stream protocol spec", **READ})
    async def maya_livelink_protocol() -> str:
        """The NDJSON event schema, command port framing, pull commands and the
        Maya to Unreal coordinate conversion, as a dict. Give it to anyone writing a
        receiver."""
        return await ctx.run("livelink.protocol_spec")
