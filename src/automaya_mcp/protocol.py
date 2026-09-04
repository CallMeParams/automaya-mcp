"""Wire protocol shared by the Maya plugin and the MCP server.

Frames are a 4 byte big endian unsigned length followed by UTF-8 JSON.
This file is kept byte-identical in ``maya_plugin/automaya_bridge`` and
``src/automaya_mcp`` so the two sides can never drift. Only the stdlib is
allowed here because it runs inside Maya's interpreter.
"""
from __future__ import annotations

import json
import socket
import struct
import uuid
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1
DEFAULT_PORT = 9877
DEFAULT_EVENT_PORT = 9878
DEFAULT_HOST = "127.0.0.1"
MAX_FRAME_BYTES = 64 * 1024 * 1024  # 64 MiB, mesh buffers can be large
HEADER = struct.Struct("!I")


class ProtocolError(Exception):
    """Raised when a frame is malformed or exceeds limits."""


def make_request(command: str, params: Optional[Dict[str, Any]] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    return {"id": request_id or uuid.uuid4().hex, "type": command, "params": params or {}}


def make_success(request_id: Optional[str], result: Any, elapsed_ms: float = 0.0) -> Dict[str, Any]:
    return {"id": request_id, "status": "success", "result": result, "elapsed_ms": round(elapsed_ms, 2)}


def make_error(request_id: Optional[str], message: str, traceback_text: str = "", code: str = "error", elapsed_ms: float = 0.0) -> Dict[str, Any]:
    return {
        "id": request_id,
        "status": "error",
        "code": code,
        "message": message,
        "traceback": traceback_text,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def encode(message: Dict[str, Any]) -> bytes:
    payload = json.dumps(message, default=_json_default).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("frame of %d bytes exceeds MAX_FRAME_BYTES" % len(payload))
    return HEADER.pack(len(payload)) + payload


def _json_default(obj: Any) -> Any:
    """Make Maya specific types (MVector, MMatrix, sets, bytes) serialisable."""
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if hasattr(obj, "__iter__"):
        try:
            return list(obj)
        except TypeError:
            pass
    return str(obj)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(remaining, 1 << 20))
        if not chunk:
            raise ConnectionError("socket closed while reading frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> Dict[str, Any]:
    header = _recv_exact(sock, HEADER.size)
    (length,) = HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("incoming frame of %d bytes exceeds MAX_FRAME_BYTES" % length)
    payload = _recv_exact(sock, length)
    try:
        return json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        raise ProtocolError("invalid JSON frame: %s" % exc)


def write_frame(sock: socket.socket, message: Dict[str, Any]) -> None:
    sock.sendall(encode(message))
