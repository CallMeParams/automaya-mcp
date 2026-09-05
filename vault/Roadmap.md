---
tags: [automaya, roadmap]
---
# Roadmap

Done in 1.0: bridge, console, 198 tools across 13 domains, providers, asset libraries, event stream, Unreal subscriber, 323 tests.

Next candidates
- C++ `ILiveLinkSource` reading the NDJSON stream (skeletal + camera + transforms with time sync).
- Skeletal streaming: joint hierarchies as `FLiveLinkSkeletonStaticData`, driven by `attr_changed` on joints.
- Mesh delta streaming: `MPolyMessage`/`MNodeMessage` dirty flags to push only changed vertex buffers.
- MCPB bundle for one click Claude Desktop install; PyPI release.
- Session discovery in the console (multiple Maya instances, port picker).
- Multi frame playblast returned as a contact sheet image.
- HumanIK automation for retargeting.
- Provider additions: Luma Genie, Stability 3D, local TripoSR/Hunyuan3D-2 mini.
- Verify against a live Maya 2024 install using `tests/maya_integration/run_in_mayapy.py` and fix any flag mismatches found.
