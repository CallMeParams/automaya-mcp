---
tags: [automaya, protocol]
---
# Wire protocol

Shared file `protocol.py` (identical copies in plugin and server). Frame = 4 byte big endian unsigned length + UTF-8 JSON. Max frame 64 MiB (mesh buffers).

Request
```json
{"id": "hex", "type": "modeling.create_primitive", "params": {"kind": "cube", "size": 2}}
```
Response
```json
{"id": "hex", "status": "success", "result": {...}, "elapsed_ms": 3.1}
{"id": "hex", "status": "error", "code": "bad_params|unknown_command|protocol|error", "message": "...", "traceback": "...", "elapsed_ms": 1.0}
```
Special: `core.ping`, `core.handshake` (versions, Maya version, command list, integrations, key flags, safe mode, events active), `core.list_commands`.

## Event stream (port 9878, NDJSON, one object per line)
First line is `hello` (protocol, unit, up axis, scene, fps). Then events with `seq`, `ts`, `kind`, `human` plus kind specific fields:
- `attr_changed` node, attr, value (scalar or null)
- `node_added` / `node_removed` node, type
- `time_changed` frame
- `selection_changed` selection[]
- `connection_made` / `connection_broken` node, attr, other
- `scene_opened`, `scene_new`, `scene_saved`, `undo`, `redo`, `name_changed`
- `marker` name, data (emitted by `maya_livelink_marker`)
- `usd_exported` path

Attribute events are coalesced to 60 Hz per plug. `transform_only` limits them to translate/rotate/scale/visibility/matrix. `watch(nodes)` limits them to named nodes.

Related: [[Unreal Real Time Viewport]]
