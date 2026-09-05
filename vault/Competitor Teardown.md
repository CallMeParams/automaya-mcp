---
tags: [automaya, research]
---
# Competitor teardown (2026-09-04)

| Project | Stars | Into Maya | Tools | UI | Assets/AI | Weak spot |
|---|---|---|---|---|---|---|
| PatrickPalmer/MayaMCP | 83 | MEL commandPort 50007, two sockets per call | 15 | none | none | security prompt, no return values from multi line code |
| dcc-mcp/dcc-mcp-maya | 48 | plugin + Rust sidecar, HTTP MCP | 79 | none | none | heavy install chain |
| chadrik/maya-mcp-server | 31 | Python commandPort, session discovery | 5 | none | none | code execution only |
| GimbalGoats/GG_MayaMCP | 13 | commandPort 7001, MCPB bundle | 71 | port toggle | none | raw code off by default, blocks UI |
| Jeffreytsai1004/maya-mcp-server | 6 | custom plugin socket (blender-mcp port) | 10 | none | none | small, no screenshots |
| abrahamADSK/maya-mcp | 1 | commandPort 8100 | 16 | console dock | self hosted Vision3D | macOS only, Python 3.13 |
| AYDJI/Autodesk-Maya-MCP | 1 | commandPort 4434 | ~30 | none | none | no threading design |

Blender MCP (26.8k stars) is the reference: queue drained on the main thread, `{"type","params"}` protocol, sidebar with integration toggles and keys, PolyHaven/Sketchfab/PolyPizza/Hyper3D/Hunyuan, `asset_creation_strategy` prompt.

AutoMaya covers every gap in that table plus: framed protocol, undo rollback, human edit awareness, image returning screenshots and renders, program introspection, and the Unreal event stream.
