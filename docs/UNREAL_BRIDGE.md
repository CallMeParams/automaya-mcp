# AutoMaya to Unreal: the live viewport bridge

This document is for anyone writing a receiver for the AutoMaya event stream,
whether that is the shipped Python subscriber (`unreal/automaya_subscriber.py`),
a web viewer, or a proper C++ `ILiveLinkSource`. It covers the two sockets,
the NDJSON schema, the pull commands, the coordinate conversion, and how the
pieces map onto Unreal's Live Link frame data.

The same information is available at runtime from `maya_livelink_protocol`
(bridge command `livelink.protocol_spec`).

## 1. Two sockets, one plugin

| Socket | Default | Direction | Framing |
|--------|---------|-----------|---------|
| Command port | `127.0.0.1:9877` | request / response | 4 byte big endian unsigned length + UTF-8 JSON |
| Event port | `127.0.0.1:9878` | server push only | one JSON object per line (NDJSON) |

Both bind to loopback only. The event port is started by `maya_livelink_start`
(or `livelink.start_stream`); the command port is always up while the bridge runs.

Command request: `{"id": "<any>", "type": "livelink.snapshot_scene_graph", "params": {...}}`
Command response: `{"id", "status": "success" | "error", "result" | "message", "elapsed_ms"}`

The subscriber's `BridgeClient.call` is a complete reference implementation
(25 lines). The bridge executes every command on Maya's main thread, so a
receiver may call it from any thread but should expect latency in the tens of
milliseconds and should not spam it per frame; use the event stream for that.

## 2. Event stream schema (protocol 1)

Every line except `hello` carries:

| Field | Type | Meaning |
|-------|------|---------|
| `seq` | int | monotonic per Maya session; gaps mean the ring buffer wrapped before you connected |
| `ts` | float | unix seconds |
| `kind` | string | event kind, below |
| `human` | bool | true when the user, not the agent, caused it |

| kind | payload | notes |
|------|---------|-------|
| `hello` | `protocol, event_port, unit, up_axis, scene` | first line on connect, no seq. `unit` is Maya's linear unit (`cm`, `m`, ...), `up_axis` is `y` or `z` |
| `attr_changed` | `node, attr, value` | `attr` is the long name (`translateX`, `rotate`, `visibility`, `focalLength`). `value` is a scalar; compound plugs (`translate` set as a whole) arrive with `value: null`, pull with `livelink.get_transforms` |
| `node_added` / `node_removed` | `node, type` | transforms, shapes and DG nodes alike; filter on `type` |
| `time_changed` | `frame` | timeline moved (scrub, play, or `livelink.set_frame`) |
| `selection_changed` | `selection: [dag paths]` | |
| `connection_made` / `connection_broken` | `node, attr, other` | |
| `name_changed` | | re-sync names with a snapshot |
| `scene_opened` / `scene_new` / `scene_saved` | `file` | drop your mirror scene on open/new |
| `undo` / `redo` | | values follow as `attr_changed` |
| `usd_exported` | `path, nodes, animation, start, end` | written by `livelink.export_usd_live`; reload your USD stage |
| `play_range` | `start, end, playing, loop` | |
| `marker` | `name, data` | custom sync point from `maya_livelink_marker` |
| `events_started` / `events_stopped` | | OpenMaya callbacks toggled |

Attribute events are coalesced to 60 Hz per plug inside Maya. When
`transform_only` is on, only translate/rotate/scale/visibility/matrix
attributes are streamed; `livelink.subscribe_nodes` restricts them further to
a node list.

## 3. Pull commands

| Command | Use |
|---------|-----|
| `livelink.snapshot_scene_graph(root, include_meshes, include_cameras, include_lights)` | flat list of transforms: `path, name, type (mesh/camera/light/group/joint/locator/curve), parent, world_matrix (16 floats), translate, rotate, scale, rotate_order, visible`, plus `camera {focal_length, horizontal_aperture_in, vertical_aperture_in, near_clip, far_clip, orthographic, ortho_width, focus_distance, f_stop}`, `light {light_type, intensity, color, exposure}`, `mesh {faces, vertices, triangles, bbox}` and scene `unit, up_axis, fps, frame, frame_range` |
| `livelink.get_transforms(nodes)` | world matrices + local t/r/s for a node list |
| `livelink.get_mesh_buffers(node, world_space, include_normals, include_uvs, triangulate)` | `positions` (flat xyz), `normals` (per vertex), `uvs` (flat uv), `uv_ids` (per face corner), `face_vertex_counts`, `face_vertex_indices`, `indices` (fan triangulated), `uv_indices` (per triangle corner), `counts`, `bbox`, `winding` |
| `livelink.export_usd_live(path, nodes, animation, start, end)` | writes USD and emits `usd_exported` |
| `livelink.set_frame(frame)`, `livelink.play_range(start, end, play, loop)` | let the receiver drive Maya time |
| `livelink.emit_marker(name, data)` | inject a marker into the stream |

Mesh buffers over 2 million faces are refused; use the USD path. The MCP tool
`maya_livelink_mesh_buffers` truncates its text reply, so receivers should
always use the command port directly for geometry.

## 4. Coordinate conversion

Maya: right handed, Y up (default), centimeters (default), rotations in degrees
with a per node rotate order. Matrices are row major with the translation in
row 3 (`p' = p * M`).

Unreal: left handed, Z up, centimeters. `FMatrix` is also row major with the
translation in row 3, so no transposition is needed.

Let `P` be the permutation that swaps Y and Z:

```
P = | 1 0 0 0 |
    | 0 0 1 0 |
    | 0 1 0 0 |
    | 0 0 0 1 |        P is symmetric and P * P = I
```

* Position: `UE(x, y, z) = (maya_x, maya_z, maya_y)`. The swap is a
  reflection (det = -1), which is exactly the right handed to left handed
  change, so nothing else is negated.
* Full transform: `M_ue = P * M_maya * P`. This holds for world matrices and
  for local matrices alike, because `P * (A * B) * P = (P * A * P) * (P * B * P)`.
  The subscriber applies world matrices from a snapshot, attaches actors to
  their parent actors with "keep world", then applies local matrices for live
  events with `set_actor_relative_*`.
* Rotation: do not remap Euler angles axis by axis, it is only correct for one
  rotate order. Rebuild the Maya matrix from `translate/rotate/scale` honouring
  `rotate_order` (`R = R_first * R_second * R_third` in row vector form; see
  `maya_local_matrix`), conjugate by `P`, then extract the rotator using
  `FMatrix::Rotator`'s formulas (`decompose_unreal`). Spot checks: Maya
  rotateY +90 becomes Unreal yaw -90, Maya rotateX +90 becomes roll +90,
  Maya rotateZ +90 becomes pitch +90.
* Scale: `UE(sx, sy, sz) = (maya_sx, maya_sz, maya_sy)`. A negative
  determinant (mirrored geometry) is pushed into the Z scale so the rotator
  stays a proper rotation.
* Units: multiply translations by `UNIT_TO_CM[hello.unit]`. Scale values are
  unitless and untouched.
* Z up Maya scenes (`up_axis == "z"`): only the handedness differs; use
  `P = diag(1, -1, 1, 1)` (negate Y) instead of the swap. The shipped
  subscriber assumes Y up and logs the hello line so you can see which you have.
* Mesh winding: Maya faces are counter clockwise when viewed from outside;
  after the axis swap they become clockwise, so reverse each triangle
  (`a, c, b`) before building Unreal geometry. Normals convert like positions.
* Cameras: Maya cameras look down local -Z, Unreal cameras look down local +X.
  After converting the transform, compose an extra rotator (`CAMERA_FIX`,
  yaw -90) on the camera actor. Focal length is mm in both; Maya film
  aperture is inches (`* 25.4` for CineCamera sensor width/height).
  Maya near/far clip are in scene units.
* Assumptions: rotate/scale pivots at the origin (freeze transforms first;
  `maya_find_problems` reports `unfrozen_transforms`), no joint orient or
  shear, no non uniform scale on parents of rotated children (Maya and Unreal
  disagree on shear propagation). Skinned characters go through USD/FBX, not
  the transform stream.

## 5. Running the Python subscriber

1. In Maya: `maya_livelink_start` (or the console's Stream toggle). Optional:
   `maya_livelink_subscribe` with the nodes you care about and
   `transform_only: true` during animation.
2. In Unreal (Editor Preferences > Plugins > Python enabled), Output Log set
   to Python, or the Python console:
   ```python
   import sys; sys.path.append(r"C:/path/to/automaya-mcp/unreal")
   import automaya_subscriber as am
   am.start()      # background reader + slate post tick applier
   am.sync()       # snapshot -> actors (spawns placeholders for missing names)
   am.status()
   ```
   `py "C:/path/to/automaya-mcp/unreal/automaya_subscriber.py"` does
   `start()` + `sync()` in one go.
3. Move things in Maya. Transforms update every editor tick. New nodes need a
   `sync()` (or the agent calling `maya_livelink_snapshot` and you re-running
   sync) because Maya sends `node_added` before the node has any geometry.
4. Geometry: `maya_livelink_export_usd` (nodes or selection) writes a USD file
   and emits `usd_exported`; the subscriber loads it into a `UsdStageActor`
   named `AutoMayaUsdStage` (USD Importer plugin) or, without that plugin,
   imports it with an `AssetImportTask` into `/Game/AutoMaya`. Actors that came
   from USD are matched by name like everything else, so transform events keep
   working after the import.

Actor matching is by label: the Maya short name with namespaces stripped
(`char:body` -> `body`). Set `LABEL_PREFIX` to keep mirrored actors apart.
Placeholders are: `StaticMeshActor` with the engine cube scaled to the Maya
bounding box, `CineCameraActor` with lens data, `PointLight` /
`SpotLight` / `DirectionalLight`, and an empty `Actor` for groups.

Threading: the socket reader is a daemon thread that only parses JSON and
queues. All `unreal.*` calls happen inside the Slate post tick callback on the
game thread (`MAX_APPLY_PER_TICK` events per tick).

## 6. Turning this into a C++ ILiveLinkSource

The Python subscriber proves the protocol; for production (animation at 60 Hz,
many characters, sequencer recording) implement a Live Link source in a plugin.
Outline:

```
Source/AutoMayaLiveLink/
  AutoMayaLiveLinkModule.cpp         IModuleInterface, registers the source factory
  AutoMayaLiveLinkSourceFactory.h/.cpp
      ULiveLinkSourceFactory: CreateSource() builds FAutoMayaLiveLinkSource
      from a "host:event_port[:command_port]" string; BuildCreationPanel gives a text box
  AutoMayaLiveLinkSource.h/.cpp
      class FAutoMayaLiveLinkSource : public ILiveLinkSource, public FRunnable
        ReceiveClient(ILiveLinkClient*, FGuid SourceGuid)
        IsSourceStillValid()  -> socket connected
        RequestSourceShutdown()
        GetSourceType/Machine/Status()
        Run()  -> FSocket connect to event port, read lines, FJsonSerializer parse,
                  dispatch on "kind" (see table below)
  AutoMayaBridgeClient.h/.cpp
      framed request/response over the command port (4 byte BE length + JSON)
      used once at connect for livelink.snapshot_scene_graph, and whenever an
      attr_changed arrives with value null (compound plug) for livelink.get_transforms
  AutoMayaConversion.h
      FTransform MayaToUnreal(const FMatrix& MayaRowMajor, float UnitScale)
      exactly the P * M * P conjugation above; FMatrix::ToTransform() does the decomposition
```

Subject naming: one Live Link subject per Maya transform, named by the DAG
path with `|` replaced by `.` (the Live Link UI dislikes pipes), role chosen
from the snapshot `type`.

Mapping to Live Link frame data:

| Our data | Live Link |
|----------|-----------|
| snapshot `type == mesh/group/joint/locator/curve` | `ULiveLinkTransformRole`; static data `FLiveLinkTransformStaticData` (`bIsLocationSupported/RotationSupported/ScaleSupported = true`) |
| snapshot `type == camera` | `ULiveLinkCameraRole`; static data `FLiveLinkCameraStaticData` with `bIsFieldOfViewSupported`, `bIsFocalLengthSupported`, `bIsApertureSupported`, `bIsFocusDistanceSupported`, `FilmBackWidth = horizontal_aperture_in * 25.4`, `FilmBackHeight = vertical_aperture_in * 25.4` |
| snapshot `type == light` | `ULiveLinkLightRole`; `FLiveLinkLightStaticData` with intensity/color support flags |
| `world_matrix` / local t,r,s (after conversion) | `FLiveLinkTransformFrameData::Transform` (`FTransform`). Feed local transforms and set `FLiveLinkTransformStaticData` parent via `ULiveLinkTransformRole` hierarchy, or feed world transforms and no hierarchy; the subscriber does the latter for simplicity |
| `attr_changed focalLength` | `FLiveLinkCameraFrameData::FocalLength` (mm, same units) |
| `attr_changed fStop` | `FLiveLinkCameraFrameData::Aperture` |
| `attr_changed focusDistance` | `FLiveLinkCameraFrameData::FocusDistance` (scale by UnitScale) |
| camera aperture + focal length | `FLiveLinkCameraFrameData::FieldOfView = 2 * atan(FilmBackWidth / (2 * FocalLength))` in degrees if you want FOV rather than lens data |
| `attr_changed intensity/color` | `FLiveLinkLightFrameData::Intensity`, `LightColor` |
| `ts` | `FLiveLinkBaseFrameData::WorldTime` (`FLiveLinkWorldTime(ts)`) |
| `time_changed frame` + snapshot `fps` | `FLiveLinkBaseFrameData::MetaData.SceneTime = FQualifiedFrameTime(FFrameTime(frame), FFrameRate(fps, 1))`, lets Sequencer record with Maya timecode |
| `seq` | `MetaData.StringMetaData["seq"]` for debugging drops |
| `node_added` | push static data for a new subject (after pulling its transform) |
| `node_removed`, `scene_opened`, `scene_new` | `ILiveLinkClient::RemoveSubject_AnyThread` / clear all subjects |
| `usd_exported` | outside Live Link's scope: broadcast a multicast delegate so an editor utility reloads the `AUsdStageActor` |
| `marker` | `FLiveLinkBaseFrameData::MetaData.StringMetaData["marker"]` on the next frame, or a delegate |

Push frames with `ILiveLinkClient::PushSubjectFrameData_AnyThread(SubjectKey, MoveTemp(FrameData))`
from the reader thread; the client handles marshalling. Because Maya coalesces
attribute events per plug, batch the `attr_changed` lines received within one
read into one frame per subject before pushing.

Interpolation: Live Link's frame interpolation settings (Project Settings >
Live Link > Default Settings) smooth the 60 Hz stream; set the source's
`FLiveLinkSourceSettings::Mode` to `Latest` for a manipulator style viewport,
or `Timecode` when the receiver drives Maya time through `livelink.set_frame`.

## 7. Alternative: Autodesk's Maya Live Link plugin

Autodesk ships `MayaLiveLinkPlugin` (Unreal Engine `Engine/Extras/MayaLiveLinkPlugin`)
which streams joints and cameras through the same Live Link roles, but it is
built per Maya version, has no scripting hooks and knows nothing about
AutoMaya's agent. The two can run side by side: use Autodesk's for skeletal
animation, AutoMaya's for scene layout, cameras, lights, markers and USD
reloads driven by the agent.
