---
tags: [automaya, moc]
---
# AutoMaya MCP vault

Knowledge base for the AutoMaya MCP project: an MCP server plus in-Maya bridge that lets Claude drive Autodesk Maya 2024. Open this folder as an Obsidian vault, or point Claude at `CLAUDE.md` in the repo root, which links here.

## Map of content
- [[Architecture]] how the pieces fit, thread model, package layout
- [[Wire Protocol]] frames, request and response shapes, event stream schema
- [[Tool Catalogue]] every MCP tool with parameters (generated)
- [[Bridge Commands]] the plugin side command namespace and how to add one
- [[Maya 2024 Facts]] interpreter, Qt, threading rules, import formats, gotchas
- [[Providers]] Tripo, Meshy, Rodin, Hunyuan, Higgsfield, Poly Haven, Sketchfab, Poly Pizza contracts
- [[Unreal Real Time Viewport]] the event stream, coordinate conversion, Live Link path
- [[Competitor Teardown]] what the other Maya MCPs do and where AutoMaya goes past them
- [[Decisions]] architecture decision log
- [[Testing]] how the stub, fake bridge and mayapy runner work
- [[Agent Playbook]] how Claude should behave when using these tools
- [[Roadmap]] what is next

## Quick facts
- Repo: `automaya-mcp`, package `automaya_mcp` (server) and `automaya_bridge` (plugin)
- Ports: 9877 commands, 9878 event broadcast, loopback only
- 198 tools in 13 modules, 3 prompts, 323 tests
- Owner: Adam Waters, Senior Previs Artist, Sydney
