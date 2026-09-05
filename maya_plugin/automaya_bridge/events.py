"""Scene change event bus, the piece that lets an outside process "see" Maya.

Uses OpenMaya 2.0 message callbacks (MDGMessage, MNodeMessage, MEventMessage,
MSceneMessage, MModelMessage) to record what changed, whether Claude or the
human did it. Events land in a ring buffer that:

* the console dock shows as a live "changes" feed
* ``core.drain_changes`` returns to the MCP server so the agent knows what the
  user did between calls (human edit awareness)
* an optional broadcast server on ``event_port`` streams as NDJSON to any
  subscriber (an Unreal Python listener, a custom Live Link source, a web
  viewer). This is the foundation for a real time external viewport: attribute
  edits, transform changes, node adds/removes and time changes stream out the
  moment they happen, and subscribers can request full mesh buffers over the
  command port when a shape is dirtied.
"""
from __future__ import annotations

import collections
import json
import socket
import threading
import time
from typing import Any, Deque, Dict, List, Set

try:
    import maya.api.OpenMaya as om  # type: ignore
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    om = None  # type: ignore
    cmds = None  # type: ignore

EVENT_CAPACITY = 5000
ATTR_SAMPLE_INTERVAL = 1.0 / 60.0  # coalesce attribute spam to 60 Hz per plug


class EventBus:
    def __init__(self, capacity: int = EVENT_CAPACITY) -> None:
        self._events: Deque[Dict[str, Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self._callback_ids: List[Any] = []
        self._node_callbacks: Dict[str, Any] = {}
        self._last_attr_emit: Dict[str, float] = {}
        self._watched: Set[str] = set()  # empty means "everything"
        self.transform_only = False
        self.active = False
        self.broadcaster: Broadcaster | None = None
        self.human_activity = True  # False while a bridge command is running

    # public ----------------------------------------------------------------
    def start(self, watch_all_nodes: bool = True) -> None:
        if om is None or self.active:
            return
        add = self._callback_ids.append
        add(om.MDGMessage.addNodeAddedCallback(self._on_node_added, "dependNode"))
        add(om.MDGMessage.addNodeRemovedCallback(self._on_node_removed, "dependNode"))
        add(om.MDGMessage.addTimeChangeCallback(self._on_time_changed))
        add(om.MEventMessage.addEventCallback("SelectionChanged", self._on_selection_changed))
        add(om.MEventMessage.addEventCallback("SceneOpened", lambda *_: self.emit("scene_opened", file=_scene_name())))
        add(om.MEventMessage.addEventCallback("NewSceneOpened", lambda *_: self.emit("scene_new")))
        add(om.MSceneMessage.addCallback(om.MSceneMessage.kAfterSave, lambda *_: self.emit("scene_saved", file=_scene_name())))
        add(om.MEventMessage.addEventCallback("Undo", lambda *_: self.emit("undo")))
        add(om.MEventMessage.addEventCallback("Redo", lambda *_: self.emit("redo")))
        add(om.MEventMessage.addEventCallback("NameChanged", lambda *_: self.emit("name_changed")))
        if watch_all_nodes:
            self._attach_existing_nodes()
        self.active = True
        self.emit("events_started")

    def stop(self) -> None:
        if om is None:
            return
        for cid in self._callback_ids:
            try:
                om.MMessage.removeCallback(cid)
            except Exception:
                pass
        self._callback_ids = []
        for cid in self._node_callbacks.values():
            try:
                om.MMessage.removeCallback(cid)
            except Exception:
                pass
        self._node_callbacks = {}
        self.active = False
        self.emit("events_stopped")

    def watch(self, nodes: List[str]) -> None:
        """Restrict attribute events to these nodes (empty list watches all)."""
        self._watched = set(nodes)

    def emit(self, kind: str, **data: Any) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), "kind": kind, "human": self.human_activity}
            event.update(data)
            self._events.append(event)
        if self.broadcaster is not None:
            self.broadcaster.publish(event)
        return event

    def drain(self, since_seq: int = 0, limit: int = 500, kinds: List[str] | None = None, human_only: bool = False) -> Dict[str, Any]:
        with self._lock:
            items = [e for e in self._events if e["seq"] > since_seq]
        if kinds:
            items = [e for e in items if e["kind"] in kinds]
        if human_only:
            items = [e for e in items if e.get("human")]
        truncated = len(items) > limit
        items = items[-limit:]
        return {"events": items, "last_seq": self._seq, "truncated": truncated, "active": self.active}

    @property
    def last_seq(self) -> int:
        return self._seq

    def rate(self, window: float = 5.0) -> float:
        """Events per second over the last ``window`` seconds (from the ring buffer)."""
        now = time.time()
        with self._lock:
            recent = [e for e in self._events if now - e["ts"] <= window]
        if not recent or window <= 0:
            return 0.0
        return round(len(recent) / window, 2)

    def watched(self) -> List[str]:
        return sorted(self._watched)

    def summary(self, since_seq: int = 0) -> Dict[str, Any]:
        """Compressed view: which nodes changed, added, removed since seq."""
        with self._lock:
            items = [e for e in self._events if e["seq"] > since_seq]
        changed: Dict[str, Set[str]] = {}
        added: List[str] = []
        removed: List[str] = []
        other: Dict[str, int] = {}
        for e in items:
            k = e["kind"]
            if k == "attr_changed":
                changed.setdefault(e["node"], set()).add(e["attr"])
            elif k == "node_added":
                added.append(e["node"])
            elif k == "node_removed":
                removed.append(e["node"])
            else:
                other[k] = other.get(k, 0) + 1
        return {
            "last_seq": self._seq,
            "changed": {n: sorted(a) for n, a in changed.items()},
            "added": added,
            "removed": removed,
            "other": other,
        }

    # callbacks --------------------------------------------------------------
    def _attach_existing_nodes(self) -> None:
        it = om.MItDependencyNodes()
        while not it.isDone():
            self._attach_node(it.thisNode())
            it.next()

    def _attach_node(self, mobj: Any) -> None:
        try:
            if not mobj.hasFn(om.MFn.kDagNode) and not mobj.hasFn(om.MFn.kDependencyNode):
                return
            handle = om.MObjectHandle(mobj)
            key = str(handle.hashCode())
            if key in self._node_callbacks:
                return
            cid = om.MNodeMessage.addAttributeChangedCallback(mobj, self._on_attr_changed)
            self._node_callbacks[key] = cid
        except Exception:
            pass

    def _on_node_added(self, mobj: Any, *_: Any) -> None:
        name = _node_name(mobj)
        self._attach_node(mobj)
        self.emit("node_added", node=name, type=_node_type(mobj))

    def _on_node_removed(self, mobj: Any, *_: Any) -> None:
        self.emit("node_removed", node=_node_name(mobj), type=_node_type(mobj))

    def _on_time_changed(self, time_obj: Any, *_: Any) -> None:
        try:
            value = time_obj.value
        except Exception:
            value = None
        self.emit("time_changed", frame=value)

    def _on_selection_changed(self, *_: Any) -> None:
        sel = []
        if cmds is not None:
            try:
                sel = cmds.ls(selection=True, long=True) or []
            except Exception:
                sel = []
        self.emit("selection_changed", selection=sel)

    def _on_attr_changed(self, msg: int, plug: Any, other_plug: Any, *_: Any) -> None:
        if not (msg & om.MNodeMessage.kAttributeSet):
            if not (msg & om.MNodeMessage.kConnectionMade or msg & om.MNodeMessage.kConnectionBroken):
                return
        try:
            node = _node_name(plug.node())
            attr = plug.partialName(includeNodeName=False, useLongNames=True)
        except Exception:
            return
        if self._watched and node not in self._watched and node.split("|")[-1] not in self._watched:
            return
        if self.transform_only and attr.split(".")[0] not in _TRANSFORM_ATTRS:
            return
        key = node + "." + attr
        now = time.time()
        if msg & om.MNodeMessage.kAttributeSet:
            if now - self._last_attr_emit.get(key, 0.0) < ATTR_SAMPLE_INTERVAL:
                return
            self._last_attr_emit[key] = now
            self.emit("attr_changed", node=node, attr=attr, value=_plug_value(plug))
        elif msg & om.MNodeMessage.kConnectionMade:
            self.emit("connection_made", node=node, attr=attr, other=_plug_name(other_plug))
        else:
            self.emit("connection_broken", node=node, attr=attr, other=_plug_name(other_plug))


_TRANSFORM_ATTRS = {
    "translate", "translateX", "translateY", "translateZ",
    "rotate", "rotateX", "rotateY", "rotateZ",
    "scale", "scaleX", "scaleY", "scaleZ",
    "visibility", "worldMatrix", "matrix",
}


def _node_name(mobj: Any) -> str:
    try:
        if mobj.hasFn(om.MFn.kDagNode):
            return om.MFnDagNode(mobj).fullPathName()
        return om.MFnDependencyNode(mobj).name()
    except Exception:
        return "<unknown>"


def _node_type(mobj: Any) -> str:
    try:
        return om.MFnDependencyNode(mobj).typeName
    except Exception:
        return "unknown"


def _plug_name(plug: Any) -> str | None:
    try:
        return plug.name() if not plug.isNull else None
    except Exception:
        return None


def _plug_value(plug: Any) -> Any:
    """Cheap scalar read; compound/array plugs return None (caller can query)."""
    try:
        if plug.isCompound or plug.isArray:
            return None
        attr = plug.attribute()
        if attr.hasFn(om.MFn.kNumericAttribute):
            unit = om.MFnNumericAttribute(attr).numericType()
            if unit in (om.MFnNumericData.kBoolean,):
                return plug.asBool()
            if unit in (om.MFnNumericData.kInt, om.MFnNumericData.kLong, om.MFnNumericData.kShort, om.MFnNumericData.kByte):
                return plug.asInt()
            return plug.asDouble()
        if attr.hasFn(om.MFn.kUnitAttribute):
            ua = om.MFnUnitAttribute(attr)
            if ua.unitType() == om.MFnUnitAttribute.kAngle:
                return plug.asMAngle().asDegrees()
            if ua.unitType() == om.MFnUnitAttribute.kDistance:
                return plug.asMDistance().asCentimeters()
            return plug.asDouble()
        if attr.hasFn(om.MFn.kEnumAttribute):
            return plug.asInt()
        if attr.hasFn(om.MFn.kTypedAttribute):
            ta = om.MFnTypedAttribute(attr)
            if ta.attrType() == om.MFnData.kString:
                return plug.asString()
    except Exception:
        return None
    return None


def _scene_name() -> str:
    if cmds is None:
        return ""
    try:
        return cmds.file(query=True, sceneName=True) or ""
    except Exception:
        return ""


class Broadcaster:
    """Fan-out NDJSON stream of events to any TCP subscriber."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9878) -> None:
        self.host = host
        self.port = port
        self.running = False
        self._sock: socket.socket | None = None
        self._subs: List[socket.socket] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.sent = 0
        self.started_at: float | None = None
        self.clients_total = 0
        self.scene_info: Dict[str, Any] = {}

    def start(self) -> None:
        """Bind and start accepting. Call from the main thread so the hello line
        can be seeded with scene units and up axis."""
        if self.running:
            return
        if cmds is not None:
            try:
                self.scene_info = {
                    "unit": cmds.currentUnit(query=True, linear=True),
                    "up_axis": cmds.upAxis(query=True, axis=True),
                    "scene": _scene_name(),
                }
            except Exception:
                self.scene_info = {}
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        s.settimeout(1.0)
        self._sock = s
        self.running = True
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._accept, name="AutoMayaEvents", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        with self._lock:
            subs = list(self._subs)
            self._subs.clear()
        for c in subs:
            try:
                c.close()
            except OSError:
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def _accept(self) -> None:
        assert self._sock is not None
        while self.running:
            try:
                c, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            c.setblocking(True)
            with self._lock:
                self._subs.append(c)
                self.clients_total += 1
            try:
                c.sendall((json.dumps(self.hello()) + "\n").encode("utf-8"))
            except OSError:
                pass

    def hello(self) -> Dict[str, Any]:
        """First line every subscriber receives. Carries what a receiver needs to
        interpret the stream (units, up axis, scene). Built from ``scene_info``
        captured on the main thread at start(), since this runs on the accept thread."""
        info: Dict[str, Any] = {"kind": "hello", "protocol": 1, "ts": time.time(), "event_port": self.port}
        info.update(self.scene_info)
        return info

    def publish(self, event: Dict[str, Any]) -> None:
        if not self.running:
            return
        line = (json.dumps(event, default=str) + "\n").encode("utf-8")
        dead = []
        with self._lock:
            subs = list(self._subs)
        for c in subs:
            try:
                c.sendall(line)
                self.sent += 1
            except OSError:
                dead.append(c)
        if dead:
            with self._lock:
                for c in dead:
                    if c in self._subs:
                        self._subs.remove(c)


BUS = EventBus()
