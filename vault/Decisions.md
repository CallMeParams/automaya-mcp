---
tags: [automaya, adr]
---
# Decisions

- **ADR 1 Own socket plugin instead of commandPort.** Framing, no security prompt, return values, main thread marshalling, UI stays responsive.
- **ADR 2 Length prefixed JSON.** Blender MCP's "parse until it works" loop fails on nested braces in strings and wastes CPU; a 4 byte header is trivial in both stdlib and C++.
- **ADR 3 Typed handlers live in Maya.** Tools are real operations, not code strings; the plugin can be tested against a stub and reused by non MCP clients (the REPL, Unreal).
- **ADR 4 Undo chunk per mutating command with rollback.** A failed tool must not leave a half edited scene.
- **ADR 5 Provider calls happen server side.** Maya's interpreter has no httpx and no async; the plugin only ever receives a local path.
- **ADR 6 FBX as the default generated format.** Maya 2024 cannot import glTF natively.
- **ADR 7 Events over callbacks, not polling.** `MNodeMessage` per node is cheap and precise; coalesced to 60 Hz per plug.
- **ADR 8 No telemetry.** Studio machines; nothing leaves the box except provider requests.
- **ADR 9 `AUTOMAYA_MODULES`.** 198 tools is a lot of context; users can load a subset per client.
- **ADR 10 Higgsfield is a hook, not a fake.** No documented public 3D REST route as of research date; the provider activates when an endpoint is configured and otherwise says so.
