---
tags: [automaya, craft]
---
# Craft layer

Six modules that give the agent artist knowledge instead of just API access. Spec: `docs/CRAFT.md`. Total tools after adding them: 244.

- **craft_procgen** (16 tools): building, street_block, room_shell, stairs, railing, pipes_along_curve, fence, column, furniture_proxy, vehicle_proxy, tree_proxy, rock, terrain, scatter, array_along_curve, grid_array. Real world cm defaults, named parts under a group, frozen transforms, base pivots, bbox and stats in every reply.
- **craft_light** (11): sun_sky (NOAA solar), hdri_dome, three_point, studio, portals, practical, exposure, report, plus offline solar, kelvin_to_rgb, lux_to_arnold. Science lives in `handlers/_science.py` and its verbatim copy `src/automaya_mcp/science.py` (a test enforces identity).
- **craft_lookdev** (7): material (21 measured entries), variation, wear, color_mgmt (ACES 1.3 config shipped with Maya 2024), render_preset, report, library.
- **craft_critique** (4): analyze, compare, render_and_critique, checklist. Pillow based imaging in `src/automaya_mcp/imaging.py`; findings carry `{severity, issue, measure, fix:{tool, change}}`.
- **craft_photo** (4): inspect, camera_match, block, depth_relief (depth map path or `DEPTH_ENDPOINT`).
- **craft_plan** (2): plan_scene, quality_gate. Prompts: astra_loop, lighting_science, photo_to_scene.

Conventions worth knowing: 100 000 lux = 1 Arnold irradiance unit so EV 15 maps to aiExposure 0; aiPhysicalSky azimuth is passed as (azimuth from north minus 90) and both values are returned for checking in a live Maya; Sydney 2026-01-15 solar noon is 13:04 AEDT at 77.3 degrees.

Providers added alongside: Hunyuan 3D Engine 3.1 with multi view, post jobs (texture, uv, rig, reduce_face) and a guarded 3D World action, and a Replicate route (default `tencent/hunyuan-3d-3.1`, any hosted model via `extra.model`).

Console: gear button opens the Settings dialog (General, Connection, AI 3D APIs with per provider Test buttons, Asset Libraries, Live Link, Safe Mode). Keys stay in `prefs.json` with 0600 perms.
