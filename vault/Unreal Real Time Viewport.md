---
tags: [automaya, unreal, livelink]
---
# Unreal real time viewport

Goal: a Weta style live view where Maya stays the authoring tool and Unreal renders what the artist is doing, without exporting.

## Pieces already in AutoMaya
- `maya_livelink_start` starts change tracking and the broadcast server (9878).
- `maya_livelink_subscribe` limits the stream to chosen nodes, `transform_only` keeps it cheap during playback.
- `maya_livelink_snapshot` gives the receiver the initial hierarchy: path, type, parent, world matrix (16 floats), local TRS, rotate order, visibility, camera lens data, light data, mesh counts, plus unit, up axis and fps.
- `maya_livelink_mesh_buffers` returns positions, normals, uvs, triangle indices for a mesh (OpenMaya path, cmds fallback).
- `maya_livelink_export_usd` writes a USD file for a stage reload when full geometry fidelity is wanted.
- `maya_livelink_set_frame`, `maya_livelink_play_range`, `maya_livelink_marker` let the receiver drive or sync time.
- `unreal/automaya_subscriber.py`: Editor Python. `start()` connects to 9878 on a background thread and applies transform events on the game thread through a Slate post tick callback; `sync()` calls the command port for a snapshot and spawns placeholder actors; USD helpers reload a stage actor.

## Coordinate conversion
Maya: right handed, Y up, cm. Unreal: left handed, Z up, cm. Position (x, y, z) in Maya becomes (x, z, y) in Unreal (swap Y and Z), which flips handedness. Rotations are converted by conjugating the Maya matrix with the swap matrix and decomposing to an Unreal rotator (the subscriber does this in pure Python and was checked against known cases, e.g. Maya rotateY +90 becomes Unreal yaw -90). Camera focal length maps directly; Unreal sensor width defaults to the Maya horizontal aperture in mm.

## Going from Python subscriber to a real Live Link source
A C++ `ILiveLinkSource` that reads the same NDJSON: transforms become `FLiveLinkTransformFrameData`, cameras `FLiveLinkCameraFrameData`, lights `FLiveLinkLightFrameData`; `time_changed` maps to the frame's `WorldTime` and `MetaData.SceneTime`. Outline and class list in `docs/UNREAL_BRIDGE.md`. Autodesk's own Maya Live Link plugin can run alongside for skeletal streaming.

Related: [[Wire Protocol]], [[Roadmap]]
