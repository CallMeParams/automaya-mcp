---
tags: [automaya, plugin]
---
# Bridge commands

Namespaces: `core`, `scene`, `modeling`, `materials`, `arnold`, `rig`, `anim`, `previs`, `fx`, `assets`, `gen`, `intel`, `introspect`, `livelink`, `ext` (drop-in scripts, see below). `maya_list_bridge_commands` returns the live list with parameters.

## Adding a command (3 files)
1. `maya_plugin/automaya_bridge/handlers/<domain>.py`: stdlib + maya only, `@command("domain.action", mutates=True)`, keyword params with defaults, raise `BridgeError("what went wrong and how to fix it")`, return JSON friendly dicts with long node names.
2. `src/automaya_mcp/tools/<domain>.py`: Pydantic input model (`extra="forbid"`, every field described), `@mcp.tool(name="maya_verb_noun", annotations=READ|WRITE|DESTRUCTIVE|EXTERNAL_*)`, body `await ctx.run("domain.action", params.model_dump())` or `ctx.image(...)` for pictures.
3. `tests/test_<domain>.py`: unit on the handler with `fake_maya.responses`, integration via `call_tool`, one error path.

Helpers in `handlers/_util.py`: `ensure_plugin`, `resolve_targets`, `shapes_of`, `transform_of`, `node_summary`, `set_attr_value`, `create_file_texture`, `import_file`, `export_selection`, `download`, `world_bbox`, `long_names`.

## Extensions (drop a script, it becomes commands)

`handlers/extensions.py` scans, in order, `maya_plugin/extensions/`, `<MAYA_APP_DIR>/automaya/tools/` (created if missing) and every folder in `AUTOMAYA_EXT_PATH` (`os.pathsep` separated). Each `*.py` is imported under a unique module name (no `sys.path` changes) and every public function defined in it (not imported names, nothing starting with `_`) is registered as `ext.<module>.<func>`. `mutates=True` unless the name starts with `get_`, `find_`, `list_`, `query_`, `read_`, `is_` or `has_`. The wrapper calls the function with the params as keywords; a string return starting with `"Error:"` is raised as `BridgeError` so the agent gets the normal error envelope. A file that fails to import is logged to the console and skipped; the rest still load.

`EXT_INDEX` (module level) records `{command: {module, func, doc, params: {name: {type, default, required, optional, description}}, mutates, file}}` with types collapsed to `str/int/float/bool/list/dict/any` from `typing.get_type_hints`. The MCP server reads it through `ext.list` at startup and builds one typed tool per command (`maya_ext_<module>_<func>`, 60 char cap, docstring as description, `Args:` lines as field descriptions).

Commands: `ext.list` (index + folders + load errors), `ext.reload` (drops `ext.*` from the registry, rescans, returns added/removed; the server refreshes its dynamic tools and sends `tools/list_changed` when a session is live), `ext.source(command)` (file path and `inspect.getsource`). The generic `maya_ext_call(command, params)` runs any of them without a typed tool. PatrickPalmer/MayaMCP scripts follow the same format and load unchanged. Examples: `maya_plugin/extensions/modeling_tools.py`, `rigging_tools.py`; tests in `tests/test_extensions.py`.

Native skin weight commands (in `rig`): `rig.get_skin_cluster` (findRelatedSkinCluster, history fallback), `rig.get_vertex_weights` (list or "all", 2000 vertex cap with `truncated`), `rig.set_vertex_weights` (one skinPercent with several `transformValue` pairs), `rig.prune_weights`.

Related: [[Testing]]
