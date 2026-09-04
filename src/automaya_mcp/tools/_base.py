"""Shared plumbing for tool modules.

Every tool module exposes ``register(mcp, ctx)``. Tools call ``ctx.run`` which
sends a bridge command and turns the outcome into text the agent can act on:
JSON on success, an "Error:" line plus the Maya traceback tail on failure.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Image

from ..connection import MayaConnection, MayaError, MayaUnavailable

READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
EXTERNAL_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
EXTERNAL_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}

MAX_TEXT = 60_000


def dumps(data: Any, limit: int = MAX_TEXT) -> str:
    text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    if len(text) > limit:
        return text[:limit] + "\n... (truncated %d chars; narrow the query or paginate)" % (len(text) - limit)
    return text


def error_text(exc: Exception) -> str:
    if isinstance(exc, MayaUnavailable):
        return "Error: " + str(exc)
    if isinstance(exc, MayaError):
        tail = exc.traceback_text.strip().splitlines()[-6:] if exc.traceback_text else []
        msg = "Error (%s): %s" % (exc.code, exc)
        if tail:
            msg += "\nMaya traceback tail:\n" + "\n".join(tail)
        return msg
    return "Error: %s: %s" % (type(exc).__name__, exc)


class ToolContext:
    def __init__(self, bridge: MayaConnection) -> None:
        self.bridge = bridge
        self.last_event_seq = 0

    async def run(self, command: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, limit: int = MAX_TEXT) -> str:
        try:
            result = await self.bridge.acall(command, _clean(params), timeout)
        except Exception as exc:  # noqa: BLE001
            return error_text(exc)
        return dumps(result, limit)

    async def raw(self, command: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Like run but returns the Python result and re-raises errors."""
        return await self.bridge.acall(command, _clean(params), timeout)

    async def image(self, command: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """Run a command whose result has ``image_base64`` and ``format`` and return an MCP Image."""
        try:
            result = await self.bridge.acall(command, _clean(params), timeout)
        except Exception as exc:  # noqa: BLE001
            return error_text(exc)
        if not isinstance(result, dict) or "image_base64" not in result:
            return dumps(result)
        data = base64.b64decode(result["image_base64"])
        fmt = result.get("format", "png")
        meta = {k: v for k, v in result.items() if k != "image_base64"}
        return [Image(data=data, format=fmt), dumps(meta, 4000)]


def _clean(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop None values so plugin defaults apply."""
    if not params:
        return {}
    return {k: v for k, v in params.items() if v is not None}
