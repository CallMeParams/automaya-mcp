---
tags: [automaya, architecture]
---
# Architecture

Three processes, two sockets.

1. **MCP client** (Claude Desktop, Claude Code, Cursor) launches `automaya-mcp` over stdio.
2. **automaya_mcp** (FastMCP, Python 3.10+) validates inputs with Pydantic, talks to Maya through `MayaConnection` (one request in flight at a time, reconnect on drop, per call timeouts), and calls external APIs (providers, asset libraries) with httpx.
3. **automaya_bridge** runs inside Maya 2024: `BridgeServer` accepts on 127.0.0.1:9877, one thread per client, every command marshalled to the main thread with `maya.utils.executeInMainThreadWithResult`, results written back from the client thread so the UI never waits on I/O.

Cross cutting:
- `registry.py`: `@command("domain.action", mutates=True)` wraps the handler in a named undo chunk and calls `cmds.undo()` if it raises.
- `events.py`: OpenMaya callbacks (MDGMessage node add/remove, MNodeMessage attribute changed per node, time, selection, scene, undo/redo) into a 5000 event ring buffer; optional `Broadcaster` fans events out as NDJSON on 9878. Events carry `human: true|false` so the agent can tell its own edits from the user's.
- `console.py`: PySide2 `workspaceControl` with Console, Changes, REPL and Settings tabs.
- `prefs.py`: JSON prefs under `MAYA_APP_DIR/automaya/`, API keys env first then prefs, file chmod 0600.

Package layout and the full diagrams are in `docs/ARCHITECTURE.md`. The rule for adding a domain is in `docs/DOMAIN_MODULE_CONTRACT.md` and summarised in [[Bridge Commands]].

Related: [[Wire Protocol]], [[Decisions]]
