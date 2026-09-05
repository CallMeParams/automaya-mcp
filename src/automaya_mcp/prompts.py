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

    @mcp.prompt(name="astra_loop")
    def astra_loop() -> str:
        """Plan, build in passes, render, critique, fix, repeat until the quality gate passes."""
        return (
            "You are building a scene in Maya the way a senior artist does: in passes, checking your own output every time.\n"
            "1. Plan: maya_plan_scene(brief) gives shots, assets with real dimensions in cm, lighting and render plans. Confirm the open questions or decide them yourself and say so.\n"
            "2. Layout pass: ground, hero blocks at the planned dims (maya_procgen_* or maya_create_primitive), cameras from the shot list (maya_create_camera, real sensor, focal), "
            "register shots (maya_create_sequence_shot). maya_viewport_screenshot and maya_get_bounding_box to check scale before going on.\n"
            "3. Detail pass: replace blocks with procgen or library assets, scatter dressing, freeze transforms, name and group everything. maya_find_problems must be clean.\n"
            "4. Lighting pass: follow the plan's recipe (maya_light_sun_sky, maya_light_hdri_dome, maya_light_three_point, maya_light_practical), set exposure with maya_light_exposure.\n"
            "5. Inspect: maya_render_and_critique (arnold for lookdev, viewport for previs). Read the findings: each has a number and a fix naming the tool and the change. Apply the top fixes only, "
            "then render again. Do not change more than two or three things between renders or you will not know what helped.\n"
            "6. Lookdev pass: maya_lookdev_measured_material for real surfaces, maya_lookdev_wear for edges and dirt, maya_lookdev_color_management aces13.\n"
            "7. Gate: maya_quality_gate(kind). Fix its ranked list until it passes. Only then tell the user the shot is done, with the final numbers (mean luminance, clipping, score).\n"
            "Rules: real world units, no magic numbers you cannot explain, keep the scene editable (history only where it helps), and screenshot after every pass so the user sees progress."
        )

    @mcp.prompt(name="lighting_science")
    def lighting_science() -> str:
        """Light with physics first, taste second."""
        return (
            "Lighting in AutoMaya is measured, not guessed.\n"
            "1. Decide the source of light from the brief: sun (maya_light_sun_sky with lat, lon, date, time gives elevation, azimuth and an EV estimate), sky or HDRI (maya_light_hdri_dome), "
            "windows (maya_light_interior_portals), practicals (maya_light_practical with real lumens or watts and Kelvin).\n"
            "2. Colour temperature: daylight 5600K, overcast 6500K, tungsten 3200K, candle 1900K, sodium street 2200K, cool white LED 4000K. maya_light_kelvin_to_rgb converts.\n"
            "3. Ratios in stops: key to fill 2 to 3 stops for drama, 1 stop for beauty; rim 0 to 1 stop over key. maya_light_three_point takes ratios directly.\n"
            "4. Exposure: set the camera with maya_light_exposure (EV, or ISO f-stop shutter). Sunny day EV 15, overcast EV 12, interior day EV 7, night street EV 3.\n"
            "5. Verify with numbers: maya_critique_analyze wants mean luminance 0.30 to 0.55, highlights clipped under 2%, shadows crushed under 8%, RMS contrast 0.15 to 0.28, colour drift under 12. "
            "maya_light_report lists each light's contribution so you know what to turn down.\n"
            "6. Materials are part of lighting: albedo above 0.9 or roughness 0 on dielectrics breaks the exposure; maya_lookdev_material_report finds them.\n"
            "7. Change one thing per render and compare the numbers before and after."
        )

    @mcp.prompt(name="photo_to_scene")
    def photo_to_scene() -> str:
        """Rebuild a photographed place in Maya, camera matched, at real scale."""
        return (
            "1. maya_photo_inspect(path): note the 35mm equivalent focal, aspect, horizon height, view hint (frontal or oblique) and time of day.\n"
            "2. maya_photo_camera_match(path): a camera with the photo on its image plane in a locked match group. Look through it (maya_look_through).\n"
            "3. Get one real dimension from the user or from known objects in the photo (door 210 cm, car 450 cm, storey 320 cm) and call maya_photo_block with width, depth, floors or height, camera and a distance guess.\n"
            "4. Line up: adjust distance and camera height (maya_transform on the camera) until the block's verticals and base sit on the photo. If verticals converge differently, adjust the focal with maya_set_camera_lens.\n"
            "5. Iterate: maya_critique_compare(reference_path=photo, use_last_render=true). Fix the biggest region errors and horizon delta first.\n"
            "6. Detail: replace blocks with maya_procgen_building (style, floors, windows from what you see), add maya_photo_depth_relief for the background when a depth map is available, light per the time of day guess (lighting_science prompt).\n"
            "7. Lookdev from the palette: maya_lookdev_measured_material with the dominant colours as tints. Finish with maya_quality_gate(kind='arch').\n"
            "Expect a one shot result with imperfections: match the camera and big masses first, and tell the user which dims were assumed."
        )
