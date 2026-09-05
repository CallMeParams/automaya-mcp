# AutoMaya MCP

Control Autodesk Maya 2024 from Claude (Desktop, Code, Cursor, any MCP client) with 244 typed tools, an in-Maya console, free asset libraries, AI 3D generation, and a real time change stream built for driving an external viewport such as Unreal.

```
Claude  <-- MCP stdio -->  automaya_mcp (Python)  <-- framed TCP 9877 -->  AutoMaya bridge inside Maya
                                                                              |-- dockable console (PySide2)
                                                                              |-- OpenMaya change bus
                                                                              '-- NDJSON broadcast 9878 -> Unreal
```

## What makes it different from the other Maya MCPs

| | commandPort servers (most of them) | AutoMaya |
|---|---|---|
| Transport into Maya | MEL/Python commandPort, unframed text, security prompt every session | Plugin socket with 4 byte length prefixed JSON, no prompt |
| Thread safety | Implicit, blocks the UI | Every command marshalled to the main thread, socket I/O off it |
| Failure handling | Half applied edits | Every mutating command runs in an undo chunk and rolls back on error |
| In-DCC UI | None or a port toggle | Console with live log, change feed, REPL, settings and API keys |
| Seeing the scene | Text only | Viewport screenshots and Arnold renders returned as images, budgeted scene summaries, problem finder, snapshot diffs |
| Human edit awareness | None | `maya_drain_changes` tells the agent what you changed by hand |
| Assets | None | Poly Haven, Sketchfab, Poly Pizza |
| AI 3D generation | None | Tripo, Meshy, Hyper3D Rodin, Hunyuan 3D Engine (3.1, world, texture, rig, cloud + local), Replicate, Higgsfield hook |
| Craft knowledge | None | Procedural generators at real scale, lighting and material science, render critique loop, photo to scene |
| Program knowledge | None | Command help, node type schemas, plugin/env/UI introspection, offline doc search |
| External viewport | None | Event broadcast + mesh buffers + scene graph snapshots + USD export, Unreal subscriber included |

## Install

```bash
pip install -e ".[dev]"          # from this repo (or: pip install automaya-mcp once published)
automaya-mcp install-plugin      # writes automaya.mod into ~/Documents/maya/2024/modules
```

Restart Maya 2024. An **AutoMaya** menu appears and the console docks next to the Attribute Editor. The bridge starts on port 9877 automatically (change it from the gear button in the console header).

Add the server to your MCP client:

```json
{
  "mcpServers": {
    "automaya": { "command": "automaya-mcp", "env": { "AUTOMAYA_PORT": "9877" } }
  }
}
```

Claude Desktop: `claude_desktop_config.json`. Claude Code: `.mcp.json` in your project or `claude mcp add automaya -- automaya-mcp`.

Ask Claude: "check the Maya status, then build a 35mm shot camera on a crane rig and block a rough street with primitives."

## Environment variables

| Variable | Purpose |
|---|---|
| `AUTOMAYA_HOST`, `AUTOMAYA_PORT` | bridge address, default 127.0.0.1:9877 |
| `AUTOMAYA_TIMEOUT` | default per command timeout (seconds) |
| `AUTOMAYA_SAFE_MODE=1` | block shell, network and filesystem access inside `maya_execute_python` |
| `AUTOMAYA_MODULES` | comma list to load only some tool modules (smaller context), e.g. `core,scene,modeling,previs,intelligence` |
| `AUTOMAYA_DOWNLOAD_DIR` | where generated and downloaded assets land |
| `TRIPO_API_KEY`, `MESHY_API_KEY`, `RODIN_API_KEY` or `FAL_KEY`, `HUNYUAN_SECRET_ID` + `HUNYUAN_SECRET_KEY` or `HUNYUAN_LOCAL_URL`, `HIGGSFIELD_API_KEY` + `HIGGSFIELD_API_SECRET` (+ `HIGGSFIELD_3D_ENDPOINT`), `REPLICATE_API_TOKEN` (+ `REPLICATE_3D_MODEL`) | 3D generation providers |
| `SKETCHFAB_API_TOKEN`, `POLYPIZZA_API_KEY` | asset libraries (Poly Haven needs no key) |

Keys can also be pasted into the Settings dialog (gear button in the console header, AI 3D APIs page, each provider has a Test button); they are stored in `<MAYA_APP_DIR>/automaya/prefs.json` with 0600 permissions and never sent anywhere except the provider they belong to.

## Tool families

core, scene, modeling, materials, rigging_animation, previs, sim_vfx, arnold, assets, generation, intelligence, livelink, introspect, plus the Craft layer: craft_procgen (parametric buildings, streets, rooms, furniture, terrain, scatter at real scale), craft_light (solar position, HDRI, three point and studio rigs, practicals in lumens and Kelvin, exposure in EV), craft_lookdev (measured PBR library, wear, variation, ACES, render presets), craft_critique (render analysis and reference comparison with concrete fixes), craft_photo (camera match, photo to block, depth relief), craft_plan (scene plan, quality gate). See [docs/CRAFT.md](docs/CRAFT.md). The full list with parameters is in [docs/TOOLS.md](docs/TOOLS.md). Prompts shipped with the server: `asset_creation_strategy`, `previs_shot_workflow`, `unreal_realtime_viewport`, `astra_loop`, `lighting_science`, `photo_to_scene`.

## Real time viewport in Unreal

1. `maya_livelink_start` opens the broadcast port and enables change tracking.
2. In Unreal's Output Log: `py unreal/automaya_subscriber.py` then `automaya_subscriber.start()`; `sync()` pulls the scene graph and spawns placeholder actors; transform edits in Maya move them live.
3. Geometry arrives through `maya_livelink_export_usd` (stage reload) or `maya_livelink_mesh_buffers`.

The schema, coordinate conversion, and the outline for turning the subscriber into a C++ `ILiveLinkSource` are in [docs/UNREAL_BRIDGE.md](docs/UNREAL_BRIDGE.md).

## Development

```bash
python3 -m pytest -q             # 468 tests: protocol, registry, every domain over a real socket against a maya stub
ruff check src maya_plugin tests unreal
mayapy tests/maya_integration/run_in_mayapy.py   # integration pattern inside a real Maya
python3 scripts/gen_tool_catalogue.py            # refresh docs/TOOLS.md
```

Architecture, orchestration diagrams and the module contract live in `docs/`. The `vault/` folder is an Obsidian ready knowledge base about the project for humans and for Claude.

## Security notes

The bridge binds to localhost only. `maya_execute_python` is on by default because that is what makes an agent useful in a DCC; turn on safe mode (console or env) to restrict it. Nothing phones home: there is no telemetry.

## License

MIT
