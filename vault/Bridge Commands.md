---
tags: [automaya, plugin]
---
# Bridge commands

Namespaces: `core`, `scene`, `modeling`, `materials`, `arnold`, `rig`, `anim`, `previs`, `fx`, `assets`, `gen`, `intel`, `introspect`, `livelink`. `maya_list_bridge_commands` returns the live list with parameters.

## Adding a command (3 files)
1. `maya_plugin/automaya_bridge/handlers/<domain>.py`: stdlib + maya only, `@command("domain.action", mutates=True)`, keyword params with defaults, raise `BridgeError("what went wrong and how to fix it")`, return JSON friendly dicts with long node names.
2. `src/automaya_mcp/tools/<domain>.py`: Pydantic input model (`extra="forbid"`, every field described), `@mcp.tool(name="maya_verb_noun", annotations=READ|WRITE|DESTRUCTIVE|EXTERNAL_*)`, body `await ctx.run("domain.action", params.model_dump())` or `ctx.image(...)` for pictures.
3. `tests/test_<domain>.py`: unit on the handler with `fake_maya.responses`, integration via `call_tool`, one error path.

Helpers in `handlers/_util.py`: `ensure_plugin`, `resolve_targets`, `shapes_of`, `transform_of`, `node_summary`, `set_attr_value`, `create_file_texture`, `import_file`, `export_selection`, `download`, `world_bbox`, `long_names`.

Related: [[Testing]]
