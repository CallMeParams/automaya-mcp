---
tags: [automaya, research, astra]
---
# Astra research (2026-09-05)

What is public about how OpenAI's GPT-6 Astra does 3D, and what AutoMaya takes from it.

## Confirmed from OpenAI's own posts
- Astra does not generate meshes. It writes Blender Python (`bpy`) to build an editable scene (geometry, materials, lighting, cameras), renders with Cycles, and inspects its own renders to iterate. It then writes an FBX plus JSON export pipeline into Unreal 5 and packages interactive apps. (Architectural visualization post)
- For games it builds assets in Blender ("193 editable meshes", 14,968 triangles in eight material batches for a ship), generates terrain, water and atmosphere procedurally in Three.js/TSL, and checks itself with Playwright screenshots, renderer counters and a JS state interface. (Building games post)
- Long reasoning budgets are part of the method: 20 to 40 minutes and tens of thousands of tokens on planning and self correction at max effort. (Deconstruction article, secondary)
- Published benchmark: BenchCAD 95.9. Demos: house from text to UE5 walkthrough, ship from generated turnaround views, real estate listing photos to a 3D model ("one shot with imperfections"), villa comparisons. The "photo of a building recreated the whole block" post is on X and could not be read from here; it fits the listing to model pattern (vision reasoning plus procedural code, not photogrammetry).
- No mention of Maya anywhere.

## Unverified
- "Mosaic Alpha FDM" / "GPT Ultima Alpha" as internal codenames. Treat as rumour.

## What transfers to Maya (implemented in the Craft layer)
| Astra behaviour | AutoMaya equivalent |
|---|---|
| Procedural scene code at real scale | `maya_procgen_*` generators (buildings, streets, rooms, furniture, terrain, scatter) in cm with stats and bbox returned |
| Lighting set in code | `maya_light_*`: NOAA solar position to aiPhysicalSky + sun, HDRI dome, three point and studio rigs in stops, practicals in lumens and Kelvin, exposure in EV |
| Materials in code | `maya_lookdev_*`: measured PBR library, wear and variation networks, ACES colour management, render presets |
| Render, look, fix loop | `maya_render_and_critique`, `maya_critique_compare`, `maya_quality_gate`, prompt `astra_loop` |
| Photo input | `maya_photo_inspect`, `maya_photo_camera_match`, `maya_photo_block`, `maya_photo_depth_relief` |
| Export to Unreal | livelink stream, USD export, Unreal subscriber |

Where AutoMaya can be better: the knowledge is in typed tools, so any model (not only Astra) gets correct flags, physically plausible values and a critique loop with numeric findings, and the human sees every step in the console with undo safety.

Sources: OpenAI architectural visualization with Astra, OpenAI building games with Astra, explainx demo roundup, atalupadhyay deconstruction article, Engadget coverage.
