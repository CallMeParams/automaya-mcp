"""Shared fixtures.

``fake_maya``  the recording ``maya.cmds`` stub (reset per test)
``bridge``     a real ``BridgeServer`` from the plugin, bound to a free port,
               running the real registry against the stub. Tools are tested
               end to end over the actual socket protocol.
``app``        a FastMCP app wired to that bridge
``call_tool``  helper that invokes an MCP tool by name and returns its text
"""
from __future__ import annotations

import json
import socket
from typing import Any, Dict

import pytest

import maya  # noqa: F401, installs the stub into sys.modules
from maya import cmds

from automaya_bridge import handlers, server as plugin_server
from automaya_mcp.connection import MayaConnection
from automaya_mcp.server import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def fake_maya():
    handlers.load_all()
    cmds.reset()
    yield cmds
    cmds.reset()


@pytest.fixture()
def bridge(fake_maya):
    srv = plugin_server.BridgeServer(port=_free_port())
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture()
def connection(bridge):
    conn = MayaConnection(port=bridge.port, default_timeout=10.0)
    yield conn
    conn.disconnect()


@pytest.fixture()
def app(connection):
    return create_app(connection)


@pytest.fixture()
def call_tool(app):
    async def _call(name: str, arguments: Dict[str, Any] | None = None) -> str:
        result = await app.call_tool(name, arguments or {})
        # FastMCP returns (content_list, structured) or content list depending on version
        content = result[0] if isinstance(result, tuple) else result
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
            elif getattr(item, "data", None) is not None:
                texts.append("<image %s bytes>" % len(item.data))
        return "\n".join(texts)

    return _call


def parse(text: str) -> Any:
    """Parse a tool's JSON reply, raising with the raw text on failure."""
    try:
        return json.loads(text)
    except ValueError:
        raise AssertionError("tool did not return JSON:\n" + text)
