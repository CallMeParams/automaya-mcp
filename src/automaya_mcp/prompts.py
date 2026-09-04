"""MCP prompts: curated workflows the agent can load on demand."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools._base import ToolContext


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.prompt(name="asset_creation_strategy")
    def asset_creation_strategy() -> str:
        """How to source or generate assets in Maya with AutoMaya."""
        return (
            "1. Call maya_get_status to see which integrations and keys are available.\n"
            "2. Call maya_scene_summary and maya_viewport_screenshot to understand the current scene.\n"
            "3. Sourcing order: Poly Haven (free, CC0) for HDRIs, textures and props; Sketchfab or Poly Pizza for "
            "stylised or specific models; AI generation (Tripo, Meshy, Rodin, Hunyuan, Higgsfield) for one hero "
            "object at a time, never a whole scene.\n"
            "4. After every import: maya_get_node_info on the new top node, fix scale/units, freeze transforms, "
            "assign or repair materials, then screenshot to verify.\n"
            "5. Use maya_drain_changes before continuing so you respect edits the user made by hand.\n"
            "6. Keep the scene organised: group imports, name nodes, delete history when appropriate."
        )

    @mcp.prompt(name="previs_shot_workflow")
    def previs_shot_workflow() -> str:
        """Blocking a previs shot in Maya."""
        return (
            "1. maya_set_time_range and set fps/units first (maya_scene_settings).\n"
            "2. Block set dressing with primitives or library assets at real world scale.\n"
            "3. maya_create_shot_camera with focal length, sensor and aspect; use maya_frame_camera and maya_look_through.\n"
            "4. Key camera and character proxies with maya_set_keyframe, check with maya_playblast (returns an image or movie).\n"
            "5. Register the shot in the camera sequencer (maya_create_sequence_shot) so editorial can read it.\n"
            "6. Export via maya_export (usd/fbx/abc) or stream to Unreal with maya_livelink_start_stream."
        )

    @mcp.prompt(name="unreal_realtime_viewport")
    def unreal_realtime_viewport() -> str:
        """Drive a live external viewport (Unreal) from Maya."""
        return (
            "1. maya_livelink_start_stream to open the broadcast port and enable change tracking.\n"
            "2. maya_livelink_subscribe_nodes for the transforms/cameras you want streamed, transform_only=true for speed.\n"
            "3. maya_snapshot_scene_graph once so the receiver can build the initial hierarchy; "
            "maya_get_mesh_buffers per mesh (or maya_export usd) for geometry.\n"
            "4. On the Unreal side run unreal/automaya_subscriber.py (Editor Python) which applies transform events live.\n"
            "5. maya_livelink_status shows subscriber count and events per second."
        )
