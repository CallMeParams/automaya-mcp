"""AutoMaya live viewport subscriber for Unreal Editor (Python).

Mirrors a Maya scene in the open Unreal level, live:

* connects to the AutoMaya event port (127.0.0.1:9878) on a background thread
  and reads NDJSON events (see docs/UNREAL_BRIDGE.md and livelink.protocol_spec)
* applies translate / rotate / scale / visibility changes to actors matched by
  name, on the game thread through a Slate post tick callback
* ``sync()`` pulls ``livelink.snapshot_scene_graph`` over the command port and
  spawns placeholder actors (StaticMeshActor cube, CineCameraActor, lights,
  empty actors for groups) with the right transforms and parenting
* ``usd_exported`` events (from ``maya_livelink_export_usd``) load the USD file
  into a UsdStageActor, or import it as assets when the USD plugin is absent

Run from the Output Log (Python mode) or the Python console:

    import sys; sys.path.append(r"C:/path/to/automaya-mcp/unreal")
    import automaya_subscriber as am
    am.start()      # connect, then keep applying events every tick
    am.sync()       # build/refresh the mirror scene from Maya
    am.stop()

Or ``py "C:/path/to/automaya_subscriber.py"`` which calls ``start()`` and
``sync()`` and leaves the module in ``__main__`` (use ``py "stop()"``).

Coordinate conversion (documented assumptions):

* Maya is right handed, Y up, cm (the plugin reports units in the hello line;
  non cm scenes are scaled by ``UNIT_TO_CM``). Unreal is left handed, Z up, cm.
* Position: UE (x, y, z) = (maya x, maya z, maya y). Swapping Y and Z is a
  reflection, which is exactly the handedness change Unreal needs, so no extra
  sign flip is applied.
* Rotation: never remapped per Euler axis. The Maya local matrix is rebuilt
  from translate/rotate/scale honouring ``rotate_order``, conjugated by the
  swap (M_ue = P * M * P), then decomposed to an FRotator with Unreal's own
  matrix to rotator rules. Rotate/scale pivots and joint orients are assumed
  to be at identity (freeze transforms in Maya first; maya_find_problems
  flags unfrozen transforms).
* Scale: UE (sx, sy, sz) = (maya sx, maya sz, maya sy).
* Cameras: Maya focal length (mm) and horizontal film aperture (inches) map
  to CineCamera focal length and sensor width (mm). Maya cameras look down
  -Z; Unreal cameras look down +X. The extra rotation is applied on top of
  the converted transform (CAMERA_FIX).

Only the ``unreal`` import is Unreal specific; the math helpers are plain
Python so they can be unit tested outside the editor.
"""
from __future__ import annotations

import json
import math
import queue
import socket
import struct
import threading
import time
import traceback
from typing import Any, Dict, List, Tuple

try:
    import unreal  # type: ignore
except ImportError:  # running outside the editor (tests, docs)
    unreal = None  # type: ignore

EVENT_HOST = "127.0.0.1"
EVENT_PORT = 9878
COMMAND_PORT = 9877
PLACEHOLDER_MESH = "/Engine/BasicShapes/Cube.Cube"
PLACEHOLDER_SIZE_CM = 100.0  # the engine cube is 100 cm; placeholders are scaled to the Maya bbox when known
MAX_APPLY_PER_TICK = 200
UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "m": 100.0, "km": 100000.0, "in": 2.54, "ft": 30.48, "yd": 91.44, "mi": 160934.4}
LABEL_PREFIX = ""  # set to e.g. "maya_" to keep mirrored actors apart from native ones


# pure math ---------------------------------------------------------------------
def _mat_mul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _identity() -> List[List[float]]:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _rot_x(deg: float) -> List[List[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1, 0, 0, 0], [0, c, s, 0], [0, -s, c, 0], [0, 0, 0, 1]]


def _rot_y(deg: float) -> List[List[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0, -s, 0], [0, 1, 0, 0], [s, 0, c, 0], [0, 0, 0, 1]]


def _rot_z(deg: float) -> List[List[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


_AXIS = {"x": _rot_x, "y": _rot_y, "z": _rot_z}


def maya_local_matrix(t: List[float], r: List[float], s: List[float], order: str = "xyz") -> List[List[float]]:
    """Maya's transform matrix (row vector convention, translation in row 3):
    M = S * R * T with R = R_first * R_second * R_third for the rotate order."""
    m = _identity()
    for i in range(3):
        m[i][i] = float(s[i])
    rot = _identity()
    for axis in order.lower():
        idx = "xyz".index(axis)
        rot = _mat_mul(rot, _AXIS[axis](float(r[idx])))
    m = _mat_mul(m, rot)
    m[3][0], m[3][1], m[3][2] = float(t[0]), float(t[1]), float(t[2])
    return m


def flat_to_matrix(values: List[float]) -> List[List[float]]:
    return [[float(values[i * 4 + j]) for j in range(4)] for i in range(4)]


SWAP_YZ = [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]


def maya_to_unreal_matrix(m: List[List[float]], unit_scale: float = 1.0) -> List[List[float]]:
    """Conjugate by the Y/Z swap (P is its own inverse) and scale translation to cm."""
    out = _mat_mul(_mat_mul(SWAP_YZ, m), SWAP_YZ)
    for j in range(3):
        out[3][j] *= unit_scale
    return out


def decompose_unreal(m: List[List[float]]) -> Tuple[List[float], List[float], List[float]]:
    """Return (location, rotator [pitch, yaw, roll] in degrees, scale3d) from an
    Unreal convention row major matrix, using FMatrix::Rotator's formulas."""
    axes = [m[i][:3] for i in range(3)]
    scale = [math.sqrt(sum(c * c for c in a)) or 1.0 for a in axes]
    x, y, z = [[c / scale[i] for c in axes[i]] for i in range(3)]
    pitch = math.degrees(math.atan2(x[2], math.sqrt(x[0] * x[0] + x[1] * x[1])))
    yaw = math.degrees(math.atan2(x[1], x[0]))
    sy_axis = [-math.sin(math.radians(yaw)), math.cos(math.radians(yaw)), 0.0]
    roll = math.degrees(math.atan2(sum(a * b for a, b in zip(z, sy_axis)), sum(a * b for a, b in zip(y, sy_axis))))
    # a negative determinant means one mirrored axis; push it into scale so the rotator stays proper
    det = (x[0] * (y[1] * z[2] - y[2] * z[1]) - x[1] * (y[0] * z[2] - y[2] * z[0]) + x[2] * (y[0] * z[1] - y[1] * z[0]))
    if det < 0:
        scale[2] = -scale[2]
    return [m[3][0], m[3][1], m[3][2]], [pitch, yaw, roll], scale


def convert_trs(t: List[float], r: List[float], s: List[float], order: str = "xyz", unit_scale: float = 1.0) -> Tuple[List[float], List[float], List[float]]:
    """Maya local t/r/s -> Unreal (location, rotator, scale3d)."""
    return decompose_unreal(maya_to_unreal_matrix(maya_local_matrix(t, r, s, order), unit_scale))


def convert_world_matrix(flat: List[float], unit_scale: float = 1.0) -> Tuple[List[float], List[float], List[float]]:
    return decompose_unreal(maya_to_unreal_matrix(flat_to_matrix(flat), unit_scale))


# bridge command client ---------------------------------------------------------
class BridgeClient:
    """Framed client for the AutoMaya command port (4 byte big endian length + JSON)."""

    def __init__(self, host: str = EVENT_HOST, port: int = COMMAND_PORT, timeout: float = 120.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def call(self, command: str, params: Dict[str, Any] | None = None) -> Any:
        req = {"id": "ue-%d" % int(time.time() * 1000), "type": command, "params": params or {}}
        payload = json.dumps(req).encode("utf-8")
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(struct.pack("!I", len(payload)) + payload)
            header = _recv_exact(sock, 4)
            body = _recv_exact(sock, struct.unpack("!I", header)[0])
        resp = json.loads(body.decode("utf-8"))
        if resp.get("status") != "success":
            raise RuntimeError("bridge %s failed: %s" % (command, resp.get("message")))
        return resp.get("result")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("bridge closed the connection")
        buf += chunk
    return buf


# event listener thread ----------------------------------------------------------
class EventListener(threading.Thread):
    def __init__(self, out_queue: queue.Queue[Dict[str, Any]], host: str = EVENT_HOST, port: int = EVENT_PORT) -> None:
        super().__init__(name="AutoMayaEventListener", daemon=True)
        self.queue = out_queue
        self.host, self.port = host, port
        self.running = True
        self.connected = False
        self.hello: Dict[str, Any] = {}
        self.received = 0

    def run(self) -> None:
        while self.running:
            try:
                with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
                    sock.settimeout(1.0)
                    self.connected = True
                    self._read_lines(sock)
            except (OSError, ConnectionError):
                pass
            finally:
                self.connected = False
            if self.running:
                time.sleep(2.0)  # reconnect backoff

    def _read_lines(self, sock: socket.socket) -> None:
        buf = b""
        while self.running:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:  # noqa: UP041, Unreal 5.0-5.3 run Python 3.9 where this is not TimeoutError
                continue
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                self.received += 1
                if event.get("kind") == "hello":
                    self.hello = event
                self.queue.put(event)

    def stop(self) -> None:
        self.running = False


# mirror scene ----------------------------------------------------------------------
class NodeState:
    __slots__ = ("path", "kind", "t", "r", "s", "order", "visible", "parent")

    def __init__(self, entry: Dict[str, Any]) -> None:
        self.path = entry["path"]
        self.kind = entry.get("type", "group")
        self.t = list(entry.get("translate", [0, 0, 0]))
        self.r = list(entry.get("rotate", [0, 0, 0]))
        self.s = list(entry.get("scale", [1, 1, 1]))
        self.order = entry.get("rotate_order", "xyz")
        self.visible = bool(entry.get("visible", True))
        self.parent = entry.get("parent")

    def apply_attr(self, attr: str, value: Any) -> bool:
        base = attr.split(".")[0]
        if value is None:
            return False
        for name, target in (("translate", self.t), ("rotate", self.r), ("scale", self.s)):
            if base == name and isinstance(value, (list, tuple)) and len(value) == 3:
                target[:] = [float(v) for v in value]
                return True
            if base.startswith(name) and len(base) == len(name) + 1:
                target["XYZ".index(base[-1])] = float(value)
                return True
        if base == "visibility":
            self.visible = bool(value)
            return True
        if base == "rotateOrder":
            orders = ["xyz", "yzx", "zxy", "xzy", "yxz", "zyx"]
            self.order = orders[int(value) % 6]
            return True
        return False


class MirrorScene:
    """Owns the actor map and applies Maya state to Unreal actors. Game thread only."""

    CAMERA_FIX = (0.0, -90.0, 0.0)  # extra rotator so a Maya -Z camera looks down Unreal +X

    def __init__(self) -> None:
        self.nodes: Dict[str, NodeState] = {}
        self.actors: Dict[str, Any] = {}  # maya path -> actor
        self.unit_scale = 1.0
        self.up_axis = "y"

    # helpers
    @staticmethod
    def label_for(path: str) -> str:
        short = path.split("|")[-1].split(":")[-1]
        return LABEL_PREFIX + short

    def _find_actor_by_label(self, label: str) -> Any:
        for actor in _all_actors():
            try:
                if actor.get_actor_label() == label:
                    return actor
            except Exception:
                continue
        return None

    def actor_for(self, path: str) -> Any:
        actor = self.actors.get(path)
        if actor is not None and _actor_valid(actor):
            return actor
        actor = self._find_actor_by_label(self.label_for(path))
        if actor is not None:
            self.actors[path] = actor
        return actor

    # build
    def load_snapshot(self, snap: Dict[str, Any], spawn_missing: bool = True) -> Dict[str, int]:
        self.unit_scale = UNIT_TO_CM.get(snap.get("unit", "cm"), 1.0)
        self.up_axis = snap.get("up_axis", "y")
        stats = {"matched": 0, "spawned": 0, "skipped": 0}
        entries = sorted(snap.get("nodes", []), key=lambda e: e["path"].count("|"))  # parents first
        for entry in entries:
            state = NodeState(entry)
            self.nodes[state.path] = state
            actor = self.actor_for(state.path)
            if actor is None and spawn_missing:
                actor = _spawn_for(state, entry)
                if actor is not None:
                    actor.set_actor_label(self.label_for(state.path))
                    self.actors[state.path] = actor
                    stats["spawned"] += 1
            elif actor is not None:
                stats["matched"] += 1
            if actor is None:
                stats["skipped"] += 1
                continue
            self._apply_world(actor, entry)
            _apply_extras(actor, state, entry)
        for path, state in self.nodes.items():
            actor = self.actors.get(path)
            parent = self.actors.get(state.parent) if state.parent else None
            if actor is not None and parent is not None:
                try:
                    actor.attach_to_actor(parent, "", unreal.AttachmentRule.KEEP_WORLD, unreal.AttachmentRule.KEEP_WORLD, unreal.AttachmentRule.KEEP_WORLD, False)
                except Exception:
                    pass
        return stats

    def _apply_world(self, actor: Any, entry: Dict[str, Any]) -> None:
        loc, rot, scale = convert_world_matrix(entry.get("world_matrix") or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], self.unit_scale)
        if entry.get("type") == "camera":
            rot = _compose_rotator(rot, self.CAMERA_FIX)
        _set_actor_transform(actor, loc, rot, scale, relative=False)
        _set_actor_hidden(actor, not entry.get("visible", True))

    # live
    def apply_attr_event(self, event: Dict[str, Any]) -> bool:
        node = event.get("node", "")
        state = self.nodes.get(node) or self.nodes.get(_match_short(self.nodes, node) or "")
        if state is None:
            return False
        if not state.apply_attr(event.get("attr", ""), event.get("value")):
            return False
        actor = self.actor_for(state.path)
        if actor is None:
            return False
        loc, rot, scale = convert_trs(state.t, state.r, state.s, state.order, self.unit_scale)
        if state.kind == "camera":
            rot = _compose_rotator(rot, self.CAMERA_FIX)
        has_parent = bool(state.parent and self.actors.get(state.parent) is not None)
        _set_actor_transform(actor, loc, rot, scale, relative=has_parent)
        _set_actor_hidden(actor, not state.visible)
        return True

    def apply_transforms(self, rows: List[Dict[str, Any]]) -> int:
        n = 0
        for row in rows:
            actor = self.actor_for(row["path"])
            if actor is None:
                continue
            self._apply_world(actor, row)
            n += 1
        return n

    def remove(self, node: str) -> None:
        path = node if node in self.nodes else _match_short(self.nodes, node)
        if not path:
            return
        actor = self.actors.pop(path, None)
        self.nodes.pop(path, None)
        if actor is not None and _actor_valid(actor):
            try:
                _actor_subsystem().destroy_actor(actor) if _actor_subsystem() else unreal.EditorLevelLibrary.destroy_actor(actor)
            except Exception:
                pass


def _match_short(nodes: Dict[str, NodeState], name: str) -> str | None:
    short = name.split("|")[-1]
    for path in nodes:
        if path.split("|")[-1] == short:
            return path
    return None


def _compose_rotator(base: List[float], extra: Tuple[float, float, float]) -> List[float]:
    if unreal is None:
        return [base[0] + extra[0], base[1] + extra[1], base[2] + extra[2]]
    r = unreal.MathLibrary.compose_rotators(unreal.Rotator(roll=extra[2], pitch=extra[0], yaw=extra[1]), unreal.Rotator(roll=base[2], pitch=base[0], yaw=base[1]))
    return [r.pitch, r.yaw, r.roll]


# unreal glue ------------------------------------------------------------------------
def _actor_subsystem() -> Any:
    if unreal is None:
        return None
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception:
        return None


def _all_actors() -> List[Any]:
    if unreal is None:
        return []
    sub = _actor_subsystem()
    try:
        return list(sub.get_all_level_actors()) if sub else list(unreal.EditorLevelLibrary.get_all_level_actors())
    except Exception:
        return []


def _actor_valid(actor: Any) -> bool:
    try:
        return bool(actor) and not actor.is_actor_being_destroyed()
    except Exception:
        return False


def _spawn(cls: Any, loc: Any = None) -> Any:
    sub = _actor_subsystem()
    location = loc or unreal.Vector(0, 0, 0)
    try:
        return sub.spawn_actor_from_class(cls, location) if sub else unreal.EditorLevelLibrary.spawn_actor_from_class(cls, location)
    except Exception:
        traceback.print_exc()
        return None


def _spawn_for(state: NodeState, entry: Dict[str, Any]) -> Any:
    if unreal is None:
        return None
    kind = state.kind
    if kind == "mesh":
        actor = _spawn(unreal.StaticMeshActor)
        if actor is not None:
            mesh = unreal.EditorAssetLibrary.load_asset(PLACEHOLDER_MESH)
            comp = actor.static_mesh_component
            if mesh is not None and comp is not None:
                comp.set_static_mesh(mesh)
                comp.set_mobility(unreal.ComponentMobility.MOVABLE)
        return actor
    if kind == "camera":
        return _spawn(unreal.CineCameraActor)
    if kind == "light":
        lt = (entry.get("light") or {}).get("light_type", "")
        cls = unreal.DirectionalLight if "directional" in lt.lower() else (unreal.SpotLight if "spot" in lt.lower() else unreal.PointLight)
        return _spawn(cls)
    return _spawn(unreal.Actor)


def _apply_extras(actor: Any, state: NodeState, entry: Dict[str, Any]) -> None:
    if unreal is None:
        return
    try:
        if state.kind == "camera" and "camera" in entry and hasattr(actor, "get_cine_camera_component"):
            cam = entry["camera"]
            comp = actor.get_cine_camera_component()
            comp.set_editor_property("current_focal_length", float(cam.get("focal_length", 35.0)))
            fb = comp.get_editor_property("filmback")
            fb.set_editor_property("sensor_width", float(cam.get("horizontal_aperture_in", 1.417)) * 25.4)
            fb.set_editor_property("sensor_height", float(cam.get("vertical_aperture_in", 0.945)) * 25.4)
            comp.set_editor_property("filmback", fb)
        elif state.kind == "light" and "light" in entry:
            light = entry["light"]
            comp = actor.get_component_by_class(unreal.LightComponent)
            if comp is not None:
                comp.set_intensity(float(light.get("intensity", 1.0)) * 1000.0)  # Maya 1.0 ~ 1000 lm is a workable default
                c = light.get("color", [1, 1, 1])
                comp.set_light_color(unreal.LinearColor(c[0], c[1], c[2], 1.0))
        elif state.kind == "mesh" and (entry.get("mesh") or {}).get("bbox"):
            size = entry["mesh"]["bbox"]["size"]
            comp = actor.static_mesh_component
            if comp is not None and all(size):
                comp.set_relative_scale3d(unreal.Vector(size[0] / PLACEHOLDER_SIZE_CM, size[2] / PLACEHOLDER_SIZE_CM, size[1] / PLACEHOLDER_SIZE_CM))
    except Exception:
        traceback.print_exc()


def _set_actor_transform(actor: Any, loc: List[float], rot: List[float], scale: List[float], relative: bool) -> None:
    if unreal is None:
        return
    location = unreal.Vector(loc[0], loc[1], loc[2])
    rotator = unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1])
    scale3d = unreal.Vector(scale[0], scale[1], scale[2])
    if relative:
        actor.set_actor_relative_location(location, False, False)
        actor.set_actor_relative_rotation(rotator, False, False)
        actor.set_actor_relative_scale3d(scale3d)
    else:
        actor.set_actor_location_and_rotation(location, rotator, False, False)
        actor.set_actor_scale3d(scale3d)


def _set_actor_hidden(actor: Any, hidden: bool) -> None:
    try:
        actor.set_actor_hidden_in_game(hidden)
        actor.set_is_temporarily_hidden_in_editor(hidden)
    except Exception:
        pass


def import_usd(path: str) -> Any:
    """Load a USD file into a UsdStageActor (live stage) or import it as assets."""
    if unreal is None:
        return None
    stage_cls = getattr(unreal, "UsdStageActor", None)
    if stage_cls is not None:
        actor = None
        for a in _all_actors():
            if isinstance(a, stage_cls) and a.get_actor_label() == LABEL_PREFIX + "AutoMayaUsdStage":
                actor = a
                break
        if actor is None:
            actor = _spawn(stage_cls)
            if actor is not None:
                actor.set_actor_label(LABEL_PREFIX + "AutoMayaUsdStage")
        if actor is not None:
            actor.set_editor_property("root_layer", unreal.FilePath(path))
            try:
                actor.set_editor_property("root_layer", unreal.FilePath(""))
                actor.set_editor_property("root_layer", unreal.FilePath(path))  # toggling forces a reload of the same path
            except Exception:
                pass
            return actor
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", path)
    task.set_editor_property("destination_path", "/Game/AutoMaya")
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", False)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return task.get_editor_property("imported_object_paths")


# controller -----------------------------------------------------------------------------
class Subscriber:
    def __init__(self, event_port: int = EVENT_PORT, command_port: int = COMMAND_PORT) -> None:
        self.queue: queue.Queue[Dict[str, Any]] = queue.Queue()
        self.listener = EventListener(self.queue, EVENT_HOST, event_port)
        self.bridge = BridgeClient(EVENT_HOST, command_port)
        self.scene = MirrorScene()
        self.tick_handle: Any = None
        self.applied = 0
        self.dropped = 0
        self.last_frame: float | None = None
        self.markers: List[Dict[str, Any]] = []
        self.auto_usd = True
        self.log_events = False

    def start(self) -> Subscriber:
        if not self.listener.is_alive():
            self.listener.start()
        if unreal is not None and self.tick_handle is None:
            self.tick_handle = unreal.register_slate_post_tick_callback(self._tick)
        _log("AutoMaya subscriber started (events %d, commands %d)" % (self.listener.port, self.bridge.port))
        return self

    def stop(self) -> None:
        self.listener.stop()
        if unreal is not None and self.tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self.tick_handle)
            self.tick_handle = None
        _log("AutoMaya subscriber stopped")

    def sync(self, root: str | None = None, spawn_missing: bool = True) -> Dict[str, int]:
        snap = self.bridge.call("livelink.snapshot_scene_graph", {"root": root, "include_meshes": True})
        stats = self.scene.load_snapshot(snap, spawn_missing)
        _log("AutoMaya sync: %s (unit %s, %d nodes)" % (stats, snap.get("unit"), snap.get("count", 0)))
        return stats

    def refresh_transforms(self, nodes: List[str]) -> int:
        rows = self.bridge.call("livelink.get_transforms", {"nodes": nodes})["transforms"]
        return self.scene.apply_transforms(rows)

    def export_and_load_usd(self, nodes: List[str] | None = None, animation: bool = False) -> str:
        out = self.bridge.call("livelink.export_usd_live", {"nodes": nodes, "animation": animation})
        import_usd(out["path"])
        return out["path"]

    def set_maya_frame(self, frame: float) -> None:
        self.bridge.call("livelink.set_frame", {"frame": frame})

    def status(self) -> Dict[str, Any]:
        return {
            "connected": self.listener.connected, "received": self.listener.received, "applied": self.applied, "dropped": self.dropped,
            "queued": self.queue.qsize(), "nodes": len(self.scene.nodes), "actors": len(self.scene.actors), "hello": self.listener.hello,
            "last_frame": self.last_frame, "markers": self.markers[-5:],
        }

    # game thread
    def _tick(self, _delta: float) -> None:
        for _ in range(MAX_APPLY_PER_TICK):
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(event)
            except Exception:
                traceback.print_exc()

    def drain(self) -> int:
        """Apply queued events now (for use outside the tick, e.g. tests)."""
        n = 0
        while True:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                return n
            self._handle(event)
            n += 1

    def _handle(self, event: Dict[str, Any]) -> None:
        kind = event.get("kind")
        if self.log_events:
            _log("event %s" % json.dumps(event)[:300])
        if kind == "hello":
            self.scene.unit_scale = UNIT_TO_CM.get(event.get("unit", "cm"), 1.0)
        elif kind == "attr_changed":
            if self.scene.apply_attr_event(event):
                self.applied += 1
            elif event.get("value") is None and event.get("attr", "").split(".")[0] in ("translate", "rotate", "scale"):
                # compound plug set (e.g. from xform): pull the real values
                node = event.get("node")
                try:
                    self.refresh_transforms([node])
                    self.applied += 1
                except Exception:
                    self.dropped += 1
            else:
                self.dropped += 1
        elif kind == "node_removed":
            self.scene.remove(event.get("node", ""))
        elif kind == "node_added":
            pass  # transforms arrive through attr events; call sync() to spawn actors for new nodes
        elif kind == "time_changed":
            self.last_frame = event.get("frame")
        elif kind == "usd_exported" and self.auto_usd:
            import_usd(event.get("path", ""))
        elif kind == "marker":
            self.markers.append(event)
        elif kind in ("scene_opened", "scene_new"):
            self.scene.nodes.clear()
            self.scene.actors.clear()


def _log(msg: str) -> None:
    if unreal is not None:
        unreal.log(msg)
    else:
        print(msg)


# module level convenience -------------------------------------------------------------------
SUBSCRIBER: Subscriber | None = None


def start(event_port: int = EVENT_PORT, command_port: int = COMMAND_PORT) -> Subscriber:
    global SUBSCRIBER
    if SUBSCRIBER is not None:
        SUBSCRIBER.stop()
    SUBSCRIBER = Subscriber(event_port, command_port).start()
    return SUBSCRIBER


def stop() -> None:
    global SUBSCRIBER
    if SUBSCRIBER is not None:
        SUBSCRIBER.stop()
        SUBSCRIBER = None


def sync(root: str | None = None) -> Dict[str, int]:
    return (SUBSCRIBER or start()).sync(root)


def status() -> Dict[str, Any]:
    return SUBSCRIBER.status() if SUBSCRIBER else {"connected": False}


if __name__ == "__main__":
    start()
    try:
        sync()
    except Exception as exc:  # Maya may not be up yet; events still stream in later
        _log("AutoMaya sync skipped: %s" % exc)
