"""Socket server that lives inside Maya.

Design (mirrors what makes Blender MCP reliable, adapted to Maya):

* one daemon thread accepts connections on 127.0.0.1:<port>
* one daemon thread per client reads length prefixed JSON frames
* every command is marshalled to Maya's main thread with
  ``maya.utils.executeInMainThreadWithResult`` so ``cmds`` is always safe;
  in batch / mayapy sessions (no UI event loop) it runs inline instead
* results go back on the client thread, so the UI is never blocked by I/O
* a ring buffer of log lines feeds the console dock and ``core.get_log``
"""
from __future__ import annotations

import collections
import socket
import threading
import time
import traceback
from typing import Any, Callable, Deque, Dict, List, Optional

from . import protocol, registry

try:
    from maya import cmds, utils as maya_utils  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    maya_utils = None  # type: ignore

PLUGIN_VERSION = "1.0.0"
LOG_CAPACITY = 2000

LogListener = Callable[[Dict[str, Any]], None]


class RingLog:
    def __init__(self, capacity: int = LOG_CAPACITY) -> None:
        self._items: Deque[Dict[str, Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._listeners: List[LogListener] = []

    def add(self, level: str, text: str, **extra: Any) -> None:
        entry = {"ts": time.time(), "level": level, "text": text}
        entry.update(extra)
        with self._lock:
            self._items.append(entry)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(entry)
            except Exception:  # a broken UI listener must not kill the server
                pass

    def tail(self, n: int = 200, level: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._items)
        if level:
            items = [i for i in items if i["level"] == level]
        return items[-n:]

    def subscribe(self, cb: LogListener) -> None:
        with self._lock:
            self._listeners.append(cb)

    def unsubscribe(self, cb: LogListener) -> None:
        with self._lock:
            if cb in self._listeners:
                self._listeners.remove(cb)


LOG = RingLog()


def _in_batch_mode() -> bool:
    if cmds is None:
        return True
    try:
        return bool(cmds.about(batch=True))
    except Exception:
        return True


def run_on_main_thread(func: Callable[[], Any]) -> Any:
    """Execute ``func`` on Maya's main thread and return its result."""
    if maya_utils is None or _in_batch_mode():
        return func()
    return maya_utils.executeInMainThreadWithResult(func)


class BridgeServer:
    def __init__(self, host: str = protocol.DEFAULT_HOST, port: int = protocol.DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.running = False
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
        self.stats = {"commands": 0, "errors": 0, "started_at": None, "clients_total": 0}

    # lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self.running:
            LOG.add("info", "bridge already running on %s:%d" % (self.host, self.port))
            return
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            LOG.add("warn", "binding to a non loopback host exposes Maya to the network")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(8)
        sock.settimeout(1.0)
        self._sock = sock
        self.running = True
        self.stats["started_at"] = time.time()
        self._thread = threading.Thread(target=self._accept_loop, name="AutoMayaBridge", daemon=True)
        self._thread.start()
        LOG.add("info", "AutoMaya bridge listening on %s:%d" % (self.host, self.port))

    def stop(self) -> None:
        self.running = False
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
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
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        LOG.add("info", "AutoMaya bridge stopped")

    # threads -------------------------------------------------------------
    def _accept_loop(self) -> None:
        assert self._sock is not None
        while self.running:
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.settimeout(None)
            with self._clients_lock:
                self._clients.append(client)
            self.stats["clients_total"] += 1
            LOG.add("info", "client connected from %s:%d" % addr)
            t = threading.Thread(target=self._client_loop, args=(client, addr), daemon=True)
            t.start()

    def _client_loop(self, client: socket.socket, addr: Any) -> None:
        try:
            while self.running:
                try:
                    request = protocol.read_frame(client)
                except (ConnectionError, protocol.ProtocolError) as exc:
                    if isinstance(exc, protocol.ProtocolError):
                        try:
                            protocol.write_frame(client, protocol.make_error(None, str(exc), code="protocol"))
                        except OSError:
                            pass
                    break
                response = self.dispatch(request)
                try:
                    protocol.write_frame(client, response)
                except OSError:
                    break
        finally:
            with self._clients_lock:
                if client in self._clients:
                    self._clients.remove(client)
            try:
                client.close()
            except OSError:
                pass
            LOG.add("info", "client %s:%d disconnected" % addr)

    # dispatch ------------------------------------------------------------
    def dispatch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = request.get("id")
        name = request.get("type") or ""
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return protocol.make_error(request_id, "params must be an object", code="bad_params")
        self.stats["commands"] += 1
        LOG.add("cmd", name, params=_short(params), id=request_id)
        try:
            response = run_on_main_thread(lambda: _invoke_as_agent(name, params))
        except Exception as exc:  # executeInMainThreadWithResult itself failed
            response = protocol.make_error(request_id, "main thread dispatch failed: %s" % exc, traceback.format_exc())
        response["id"] = request_id
        if response.get("status") == "error":
            self.stats["errors"] += 1
            LOG.add("error", "%s -> %s" % (name, response.get("message")), id=request_id)
        else:
            LOG.add("ok", "%s (%.1f ms)" % (name, response.get("elapsed_ms", 0.0)), id=request_id, result=_short(response.get("result")))
        return response


def _invoke_as_agent(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Run on the main thread; label change events as agent driven meanwhile."""
    from . import events

    previous = events.BUS.human_activity
    events.BUS.human_activity = False
    try:
        return registry.invoke(name, params)
    finally:
        events.BUS.human_activity = previous


def _short(value: Any, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


_SERVER: Optional[BridgeServer] = None


def get_server() -> Optional[BridgeServer]:
    return _SERVER


def start(port: int = protocol.DEFAULT_PORT, host: str = protocol.DEFAULT_HOST) -> BridgeServer:
    global _SERVER
    if _SERVER is not None and _SERVER.running:
        if _SERVER.port == port:
            return _SERVER
        _SERVER.stop()
    from . import handlers  # noqa: F401, registers every domain command

    handlers.load_all()
    _SERVER = BridgeServer(host, port)
    _SERVER.start()
    return _SERVER


def stop() -> None:
    global _SERVER
    if _SERVER is not None:
        _SERVER.stop()
        _SERVER = None
