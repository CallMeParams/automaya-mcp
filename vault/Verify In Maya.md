---
tags: [automaya, todo]
---
# Verify inside a real Maya 2024

The code was built and tested against a stub; the review pass fixed the API mismatches it could confirm. These items need a live check (run `mayapy tests/maya_integration/run_in_mayapy.py` first, then poke at each):

1. `polyPlatonic -primitive` index order (0 tetra, 1 cube, 2 octa, 3 dodeca, 4 icosa assumed) and `polyDisc -subdivisions` without `-subdivisionMode`.
2. `fx.create_nhair`: the 12 arg `createHair` form, especially arg 7 (dynamic flag), should produce a hairSystem with dynamics on.
3. `fx.cache_ncache`: `doCreateNclothCache 5 {...}` argument order (args 11 to 15).
4. `fx.create_ncloth`: `createNCloth 1` assumed local space output, `0` world space.
5. `arnold.status`: `cmds.arnoldPlugins(getVersion=True)` may not exist (guarded).
6. `_util.set_attr_value` with 4 element lists uses `type="double4"`; may need `float4` or per component sets.
7. `scene.settings` fps: Maya only accepts a fixed list of custom fps strings; `fps=27` will raise.
8. `fx.add_field`: consider `cmds.select(clear=True)` before creating fields so they do not auto connect to the selection.
9. `previs._aim_camera` uses `viewPlace`; confirm it works headless in mayapy.
10. Console dock: check `workspaceControl` docking beside the Attribute Editor on first launch and that the REPL Ctrl+Enter shortcut fires.

Low priority items from the security review, not yet changed: REPL runs outside an undo chunk; `events._node_callbacks` never shrinks (prune on node_removed); `discover_ports` blocks the event loop briefly; provider download errors echo presigned URLs; no size cap on streamed downloads.

Craft layer additions to check live: `aiPhysicalSky.azimuth` convention (handler passes azimuth from north minus 90), `polyCube` face index ordering used by `procgen.building` facade cuts, `polyBoolOp` cutters in `room_shell`, `cmds.imagePlane(camera=..., fileName=...)` fit in `photo.camera_from_photo`, and the gear Settings dialog layout (PySide2 code was reviewed, not executed).
