"""Socket server that lives inside Maya.

Design (mirrors what makes Blender MCP reliable, adapted to Maya):

* one daemon thread accepts connections on 127.0.0.1:<port>
* one daemon thread per client reads length prefixed JSON frames
* every command is marshalled to Maya's main thread with
  ``maya.utils.executeInMainThreadWithResult`` so ``cmds`` is always safe;
  in batch / mayapy sessions (no UI event loop) the main thread calls
  ``serve_forever()`` (or ``pump()`` from its own loop) and socket threads
  queue work for it, because ``cmds`` misbehaves off the main thread there
* results go back on the client thread, so the UI is never blocked by I/O
* a ring buffer of log lines feeds the console dock and ``core.get_log``
"""
from __future__ import annotations

import collections
import queue
import socket
import threading
import time
import traceback
from typing import Any, Callable, Deque, Dict, List

from . import protocol, registry

try:
    from maya import cmds  # type: ignore
    from maya import utils as maya_utils
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    maya_utils = None  # type: ignore

PLUGIN_VERSION = "1.0.0"
LOG_CAPACITY = 2000
MAX_CLIENTS = 16  # one thread per client; anything beyond this is refused
BODY_TIMEOUT = 30.0  # seconds a client gets to deliver a frame body after its header

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

    def tail(self, n: int = 200, level: str | None = None) -> List[Dict[str, Any]]:
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


_BATCH_MODE: bool | None = None  # cached on the main thread by BridgeServer.start()


def _in_batch_mode() -> bool:
    """Whether this session has no UI event loop. Cached because it is asked on
    the socket threads, where calling ``cmds`` is not allowed."""
    global _BATCH_MODE
    if _BATCH_MODE is None:
        if cmds is None:
            _BATCH_MODE = True
        else:
            try:
                _BATCH_MODE = bool(cmds.about(batch=True))
            except Exception:
                _BATCH_MODE = True
    return _BATCH_MODE


_MAIN_QUEUE: queue.Queue[tuple] = queue.Queue()
_PUMP_ACTIVE = threading.Event()


def _on_main_thread() -> bool:
    return threading.get_ident() == threading.main_thread().ident


def run_on_main_thread(func: Callable[[], Any]) -> Any:
    """Execute ``func`` on Maya's main thread and return its result.

    Interactive Maya: ``executeInMainThreadWithResult``. Batch (mayapy): if the
    main thread is pumping, queue the job and wait; otherwise run inline (plain
    Python tests with the maya stub, or a caller already on the main thread).
    """
    if maya_utils is not None and not _in_batch_mode():
        return maya_utils.executeInMainThreadWithResult(func)
    if _on_main_thread() or not _PUMP_ACTIVE.is_set():
        return func()
    done = threading.Event()
    box: Dict[str, Any] = {}
    _MAIN_QUEUE.put((func, done, box))
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def pump(timeout: float = 0.05, max_jobs: int = 64) -> int:
    """Run queued bridge jobs on the calling (main) thread. Returns how many ran.

    Call this from your own loop in mayapy if you do not want ``serve_forever``.
    """
    _PUMP_ACTIVE.set()
    ran = 0
    wait = timeout
    while ran < max_jobs:
        try:
            func, done, box = _MAIN_QUEUE.get(timeout=wait)
        except queue.Empty:
            break
        try:
            box["result"] = func()
        except BaseException as exc:  # handed back to the socket thread
            box["error"] = exc
        finally:
            done.set()
        ran += 1
        wait = 0.0
    return ran


def serve_forever(stop_event: threading.Event | None = None, poll: float = 0.05) -> None:
    """Block the main thread and execute bridge commands until stopped.

    For mayapy / render farm / CI: ``automaya_bridge.start(); automaya_bridge.serve_forever()``.
    Ctrl+C or setting ``stop_event`` returns.
    """
    _PUMP_ACTIVE.set()
    try:
        while stop_event is None or not stop_event.is_set():
            pump(timeout=poll)
    except KeyboardInterrupt:
        pass
    finally:
        _PUMP_ACTIVE.clear()
        # release anyone still waiting so their sockets get an answer
        while True:
            try:
                _func, done, box = _MAIN_QUEUE.get_nowait()
            except queue.Empty:
                break
            box["error"] = RuntimeError("bridge pump stopped")
            done.set()


class BridgeServer:
    def __init__(self, host: str = protocol.DEFAULT_HOST, port: int = protocol.DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.running = False
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
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
        _in_batch_mode()  # prime the cache here, on the main thread
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
            except TimeoutError:
                continue
            except OSError:
                break
            client.settimeout(None)
            with self._clients_lock:
                if len(self._clients) >= MAX_CLIENTS:
                    full = True
                else:
                    full = False
                    self._clients.append(client)
            if full:
                LOG.add("warn", "refused client %s:%d, %d clients already connected" % (addr[0], addr[1], MAX_CLIENTS))
                try:
                    client.settimeout(2.0)
                    protocol.write_frame(client, protocol.make_error(None, "too many clients connected to this bridge", code="busy"))
                except OSError:
                    pass
                try:
                    client.close()
                except OSError:
                    pass
                continue
            self.stats["clients_total"] += 1
            LOG.add("info", "client connected from %s:%d" % addr)
            t = threading.Thread(target=self._client_loop, args=(client, addr), daemon=True)
            t.start()

    def _client_loop(self, client: socket.socket, addr: Any) -> None:
        try:
            while self.running:
                try:
                    request = protocol.read_frame(client, body_timeout=BODY_TIMEOUT)
                except (OSError, protocol.ProtocolError) as exc:
                    if isinstance(exc, protocol.ProtocolError):
                        try:
                            protocol.write_frame(client, protocol.make_error(None, str(exc), code="protocol"))
                        except OSError:
                            pass
                    break
                if not isinstance(request, dict):
                    try:
                        protocol.write_frame(client, protocol.make_error(None, "request must be a JSON object", code="protocol"))
                    except OSError:
                        pass
                    break
                response = self.dispatch(request)
                try:
                    protocol.write_frame(client, response)
                except protocol.ProtocolError as exc:
                    # The result is too big for one frame. Keep the connection: dropping it
                    # would make the client reconnect and run the same command again.
                    try:
                        protocol.write_frame(client, protocol.make_error(request.get("id"), "%s. Narrow the query or paginate." % exc, code="too_large"))
                    except OSError:
                        break
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


_SERVER: BridgeServer | None = None


def get_server() -> BridgeServer | None:
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
