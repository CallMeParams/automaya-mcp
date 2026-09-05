---
tags: [automaya, testing]
---
# Testing

Three layers.

1. **Unit**: handler functions called directly with the recording `maya` stub (`tests/stubs/maya`). `fake_maya.responses["polyCube"] = [...]` sets return values; `fake_maya.calls_to("polyCube")` asserts flags. `mel.responses` works the same for MEL.
2. **Integration**: `call_tool("maya_x", {"params": {...}})` runs the real FastMCP tool, real `MayaConnection`, real socket, real `BridgeServer` and registry, against the stub. Validation failures come back as `Error: ...` text like a real client would see.
3. **Real Maya**: `mayapy tests/maya_integration/run_in_mayapy.py` (or paste in the Script Editor) exercises one command per domain against real cmds.

Providers use `respx` to mock HTTP. Run everything with `python3 -m pytest -q` (323 tests) and `ruff check src maya_plugin tests unreal`.
