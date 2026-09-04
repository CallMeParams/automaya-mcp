"""Client side of the bridge: framed socket client with lock, reconnect,
timeouts and multi session discovery.

Only one request is in flight at a time per connection (the lock), which
matches Maya executing commands serially on its main thread. Long running
commands (renders, simulations, generation imports) pass an explicit timeout.
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from . import protocol


class MayaError(Exception):
    """Raised when the plugin returns status=error. ``payload`` has details."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}

    @property
    def code(self) -> str:
        return str(self.payload.get("code", "error"))

    @property
    def traceback_text(self) -> str:
        return str(self.payload.get("traceback", ""))


class MayaUnavailable(MayaError):
    """Raised when no Maya bridge can be reached."""


class MayaConnection:
    def __init__(self, host: str = protocol.DEFAULT_HOST, port: int = protocol.DEFAULT_PORT, default_timeout: float = 120.0) -> None:
        self.host = host
        self.port = port
        self.default_timeout = default_timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self.handshake: Dict[str, Any] = {}
        self.last_error: Optional[str] = None

    # connection ------------------------------------------------------------
    def connect(self, timeout: float = 3.0) -> bool:
        with self._lock:
            return self._connect_locked(timeout)

    def _connect_locked(self, timeout: float = 3.0) -> bool:
        if self._sock is not None:
            return True
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self._sock = None
            return False

    def disconnect(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # requests --------------------------------------------------------------
    def call(self, command: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Send one command and return its ``result``; raises MayaError on failure."""
        timeout = timeout or self.default_timeout
        with self._lock:
            if not self._connect_locked():
                raise MayaUnavailable(
                    "Cannot reach Maya on %s:%d (%s). In Maya run: import automaya_bridge; automaya_bridge.start() "
                    "or press Connect in the AutoMaya console." % (self.host, self.port, self.last_error)
                )
            assert self._sock is not None
            request = protocol.make_request(command, params)
            try:
                self._sock.settimeout(timeout)
                protocol.write_frame(self._sock, request)
                response = protocol.read_frame(self._sock)
            except socket.timeout:
                self._close_locked()
                raise MayaError("Maya did not answer %s within %.0fs. Maya may be busy (render, simulation, modal dialog)." % (command, timeout))
            except (OSError, protocol.ProtocolError) as exc:
                self._close_locked()
                # one retry after reconnect covers Maya restarts between calls
                if self._connect_locked():
                    try:
                        self._sock.settimeout(timeout)
                        protocol.write_frame(self._sock, request)
                        response = protocol.read_frame(self._sock)
                    except (OSError, protocol.ProtocolError) as exc2:
                        self._close_locked()
                        raise MayaUnavailable("Lost connection to Maya: %s" % exc2)
                else:
                    raise MayaUnavailable("Lost connection to Maya: %s" % exc)
        if response.get("id") not in (None, request["id"]):
            raise MayaError("response id mismatch (got %s expected %s)" % (response.get("id"), request["id"]))
        if response.get("status") != "success":
            raise MayaError(response.get("message", "unknown error from Maya"), response)
        return response.get("result")

    async def acall(self, command: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Async wrapper so tools never block the MCP event loop."""
        return await asyncio.to_thread(self.call, command, params, timeout)

    def do_handshake(self) -> Dict[str, Any]:
        self.handshake = self.call("core.handshake", timeout=10.0) or {}
        return self.handshake


def discover_ports(host: str = protocol.DEFAULT_HOST, ports: Optional[List[int]] = None, timeout: float = 0.3) -> List[int]:
    """Probe candidate ports for a live bridge (for multiple Maya sessions)."""
    ports = ports or list(range(protocol.DEFAULT_PORT, protocol.DEFAULT_PORT + 10))
    found = []
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout * 4)
                protocol.write_frame(s, protocol.make_request("core.ping"))
                resp = protocol.read_frame(s)
                if resp.get("status") == "success":
                    found.append(port)
        except (OSError, protocol.ProtocolError, ValueError):
            continue
    return found


def connection_from_env() -> MayaConnection:
    host = os.environ.get("AUTOMAYA_HOST", protocol.DEFAULT_HOST)
    port = int(os.environ.get("AUTOMAYA_PORT", protocol.DEFAULT_PORT))
    timeout = float(os.environ.get("AUTOMAYA_TIMEOUT", "120"))
    return MayaConnection(host, port, timeout)


class EventSubscriber:
    """Reads the NDJSON broadcast stream (event port) in a background thread."""

    def __init__(self, host: str = protocol.DEFAULT_HOST, port: int = protocol.DEFAULT_EVENT_PORT) -> None:
        self.host = host
        self.port = port
        self.events: List[Dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import json

        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=2.0) as s:
                    s.settimeout(1.0)
                    buf = b""
                    while not self._stop.is_set():
                        try:
                            chunk = s.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            try:
                                self.events.append(json.loads(line))
                            except ValueError:
                                pass
            except OSError:
                time.sleep(1.0)
