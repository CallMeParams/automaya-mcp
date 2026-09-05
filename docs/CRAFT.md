# AutoMaya Craft layer

Goal: make any MCP client (Claude, Cursor, other CLIs) build, light and render in Maya like a senior artist, without depending on a hosted "3D model". This is the answer to code driven 3D systems such as OpenAI's Astra, whose public workflow is: write procedural scene code at real scale, set lights and materials in code, render, inspect the render, fix, repeat, with a long reasoning budget. AutoMaya turns each of those steps into a typed tool with the domain knowledge baked in, so the agent spends its reasoning on the creative decisions instead of on remembering Maya flags or physics.

What we know about Astra (sources in vault/Astra Research.md): it does not emit meshes, it writes Blender Python and Three.js/TSL; it builds editable scenes (193 meshes for a ship), exports FBX + JSON to Unreal; it renders and inspects its own output with tools (Playwright screenshots, renderer counters); demos include a house from text, a ship from generated turnaround views, a real estate listing to 3D ("one shot with imperfections"). Its published benchmark is BenchCAD 95.9. Nothing about Maya. The leaked "Mosaic Alpha FDM" name is unverified.

## Modules (all follow docs/DOMAIN_MODULE_CONTRACT.md)

### craft_procgen  (plugin handler `procgen.*`, tools `maya_procgen_*`)
Parametric generators at real world scale (cm), named, grouped, pivot at base, UVs and material slots ready:
- building(footprint or width/depth, floors, floor_height=320, style flat|brick|glass|classical, window w/h/spacing, sill, mullions, ground floor shopfront, cornice, parapet or pitched/hip roof, entrance) via extrude/inset of facade grids
- street_block(lots, road width, sidewalk, curb, lamp posts, trees proxies) using building()
- room_shell(width, depth, height, wall thickness, openings list for doors/windows) with inward facing normals kept correct
- stairs(rise, run, steps, width, landing), railing(curve or length, post spacing), pipes_along_curve(radius, segments), fence, column(order)
- furniture_proxy(kind table|chair|sofa|bed|desk|shelf|lamp, real dimensions table) and vehicle_proxy(kind car|van|bus dims)
- tree_proxy(height, canopy style), rock(noise displaced sphere), terrain(width, depth, subdivisions, noise octaves or heightmap path)
- scatter(source nodes, surface, count, density, min distance, align to normal, random rot/scale, seed) instancing
- array_along_curve(node, curve, count, align), grid_array
All return long names plus bounding box and a `stats` block (faces, dims) so the agent can verify scale.

### craft_light  (handler `light.*`, tools `maya_light_*`)
Lighting science with Arnold first, viewport fallback:
- sun_sky(lat, lon, date, time, timezone, intensity) solar position (NOAA equations, computed server side, no network) drives aiPhysicalSky + directional sun with elevation/azimuth, sky turbidity; returns elevation/azimuth/EV estimate
- hdri_dome(path, rotation, intensity, exposure, camera visible, ground projection hint)
- three_point(subject node, key/fill/rim ratio in stops, key angle/elevation, softness, color temperature K) placing aiAreaLights by subject bounds
- studio(subject, style softbox|butterfly|rembrandt|rim heavy), interior_portals(windows list) with aiLightPortal
- practical(kind bulb|tube|neon|candle|screen, lumens or watts, kelvin) converting to Arnold intensity/exposure and RGB from Kelvin (Planck approx)
- exposure(camera, ev or iso/fstop/shutter) sets aiExposure on camera and `defaultArnoldRenderOptions` tone settings; `light_report(camera)` returns each light's contribution guess and EV.
- kelvin_to_rgb, lux_to_arnold helpers exposed as tools for reasoning

### craft_lookdev  (handler `lookdev.*`, tools `maya_lookdev_*`)
- measured_material(name from library) creating aiStandardSurface with measured albedo/roughness/metalness/IOR/SSS/coat values (concrete, asphalt, brick, plaster, wood oak/walnut/pine, painted metal, steel, aluminium, copper, gold, glass, water, rubber, leather, fabric, skin, snow, sand, grass proxy) plus optional procedural break-up (aiNoise/aiCellNoise into roughness and color) and triplanar (aiTriplanar) when the mesh has no UVs
- material_variation(material, count, hue/rough jitter) for crowds and blocks
- wear(material, edges amount via aiCurvature, dirt amount via aiAmbientOcclusion) layering with aiLayerShader/aiMixShader
- color_management(profile aces13|aces2|srgb) sets OCIO and view transform; `render_preset(quality preview|production|final)` sets AA/GI/denoiser/AOVs consistently
- material_report(scene) listing shaders with values outside plausible ranges (albedo > 0.9, metal with non black base, roughness 0 dielectrics)

### craft_critique  (server side, Pillow + numpy optional; tools `maya_critique_*`)
- analyze_image(path or last render): histogram, clipped shadows/highlights %, mean luminance vs target, contrast (RMS), colour cast (mean RGB drift), sharpness proxy (Laplacian variance), thirds mass balance, empty regions, plus a plain language critique list with concrete Maya fixes (raise fill, lower sky exposure, move camera)
- compare_to_reference(render, reference photo): resized luminance and edge maps, per region error map, colour histogram distance, horizon line estimate for both, a "what differs" list
- render_and_critique(camera, width, height, iterations 1) convenience calling arnold.render_frame then analyze
- checklist(kind lookdev|previs|arch) returns the review rubric the agent should score against

### craft_photo  (server side tools `maya_photo_*` + handler `photo.*`)
- inspect_photo(path): EXIF focal length and sensor guess, dimensions, orientation, dominant colours, horizon estimate (strong horizontal edges), vanishing hint (line clusters), time of day guess from colour temperature
- camera_from_photo(path, camera name): creates a camera with matching aspect, focal, film back, image plane with the photo at the right fit, and a locked "match" group
- block_from_photo(path, subject dims known in cm, floors...) builds a first procgen building behind the image plane sized from the given dimensions; then the agent iterates with compare_to_reference
- depth_relief(image, depth map path or provider) hook: if a depth map exists (from a local Depth Anything/MiDaS endpoint set in settings, or a Replicate model), displaces a plane to make a relief for background layout. Never blocks if no provider.

### craft_plan  (prompts + tools)
- `maya_plan_scene(brief)` returns a structured build plan skeleton (shot list, asset list with dimensions and sources, lighting plan, render plan) the agent fills in; `maya_quality_gate(kind)` runs find_problems + material_report + critique on a fresh render and returns pass/fail with the ranked fixes
- prompts: `astra_loop` (plan, build in passes, render, critique, fix, repeat until gate passes), `lighting_science`, `photo_to_scene`

## Providers added
- Hunyuan 3D Engine (Tencent ai3d, Model "3.1", multi view up to 8, GenerateType Normal|LowPoly|Geometry|Sketch, PBR) plus post jobs SubmitTextureTo3DJob, SubmitHunyuanTo3DUVJob, SubmitAutoRiggingJob, SubmitReduceFaceJob; Hunyuan 3D World as a guarded action `HUNYUAN_WORLD_ACTION` env until the action name is verified
- Replicate route (`REPLICATE_API_TOKEN`): tencent/hunyuan-3d-3.1 and other hosted models via `POST /v1/models/{owner}/{name}/predictions`, `GET /v1/predictions/{id}`, output URL(s)

## Console
A gear button in the header opens a Settings dialog with a category list: General, Connection, AI 3D APIs (one row per provider: enable toggle, key fields with show/hide, Test button that pings the provider status endpoint from inside Maya with urllib), Asset Libraries, Live Link, Safe Mode. The old Settings tab is removed; keys still live in prefs.json (0600).
