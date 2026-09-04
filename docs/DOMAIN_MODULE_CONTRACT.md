# Contract for a domain module

Each domain ships three files. Read `tools/core.py`, `handlers/core.py`, `handlers/_util.py`, `tools/_base.py`, `tests/conftest.py` and `tests/test_core.py` first; they are the reference.

## 1. Plugin handler: `maya_plugin/automaya_bridge/handlers/<domain>.py`

* Stdlib + `maya` only. Import with the guarded pattern:
  ```python
  try:
      from maya import cmds, mel
  except ImportError:
      cmds = None; mel = None
  ```
* Register commands with `@command("<domain>.<action>", mutates=True|False)` from `..registry`. `mutates=True` for anything that edits the scene (gets an undo chunk + rollback).
* Signature = keyword params with defaults; the server passes only non None params. Validate cheaply and raise `BridgeError("...how to fix...")` from `._util`.
* Return JSON serialisable dicts/lists. For anything that creates nodes, return the long names (`cmds.ls(x, long=True)`), and reuse `_util.node_summary`.
* Use `_util.ensure_plugin("mtoa")` style for optional plugins, `_util.resolve_targets(nodes)` for "nodes or selection".
* Images: return `{"image_base64": ..., "format": "png", "width": w, "height": h, "path": p}`.
* Never block for long without a reason; long jobs accept `timeout` on the server side.

## 2. Server tools: `src/automaya_mcp/tools/<domain>.py`

* `def register(mcp: FastMCP, ctx: ToolContext) -> None:` defining tools with `@mcp.tool(name="maya_<verb_noun>", annotations={"title": ..., **READ|WRITE|DESTRUCTIVE|EXTERNAL_READ|EXTERNAL_WRITE})`.
* One Pydantic input model per tool (`model_config = ConfigDict(extra="forbid")`, every Field has a description, constraints and an example where useful). Tools with no inputs take no params.
* Body is normally `return await ctx.run("<domain>.<action>", params.model_dump(), timeout=...)`. Screenshots/renders use `return await ctx.image(...)`.
* Docstrings are the tool description the agent sees: what it does, when to use it, when not to, what it returns. Keep them under 120 words.
* Tool names stay under 40 chars, prefix `maya_`.

## 3. Tests: `tests/test_<domain>.py`

* Unit pattern: call the handler function directly with `fake_maya` (the stub), set `fake_maya.responses["polyCube"] = ["pCube1", "polyCube1"]` etc., assert on `fake_maya.calls_to("polyCube")` and the returned dict.
* Integration pattern: `await call_tool("maya_xxx", {"params": {...}})` through the real socket + registry, `parse()` the JSON.
* At least one error path per domain (bad node, missing plugin).
* Run `python3 -m pytest tests/test_<domain>.py -q` and `ruff check <your files>`; both must be clean.

## Style

No em dashes or en dashes anywhere in code comments, docstrings or docs (the owner's rule; hyphens only where a dictionary word needs one). Casual, clear docstrings. No emoji.
