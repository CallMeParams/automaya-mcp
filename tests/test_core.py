"""Unit + integration patterns for the protocol, registry and core tools."""
from __future__ import annotations

import socket
import threading

import pytest
from tests.conftest import parse

from automaya_bridge import protocol, registry
from automaya_mcp import safety
from automaya_mcp.connection import MayaError, MayaUnavailable


# unit: protocol framing ----------------------------------------------------
def test_frame_roundtrip():
    a, b = socket.socketpair()
    msg = protocol.make_request("core.ping", {"x": [1, 2, {"y": "ü"}]})
    threading.Thread(target=protocol.write_frame, args=(a, msg)).start()
    assert protocol.read_frame(b) == msg
    a.close()
    b.close()


def test_frame_rejects_oversize():
    with pytest.raises(protocol.ProtocolError):
        protocol.encode({"blob": "x" * (protocol.MAX_FRAME_BYTES + 1)})


def test_json_default_handles_sets_and_tuples():
    out = protocol.encode({"s": {1}, "t": (1, 2)})
    assert b"[1]" in out and b"[1, 2]" in out


# unit: registry ------------------------------------------------------------
def test_registry_unknown_and_bad_params(fake_maya):
    resp = registry.invoke("nope.nothing", {})
    assert resp["status"] == "error" and resp["code"] == "unknown_command"
    resp = registry.invoke("core.ping", {"bogus": 1})
    assert resp["code"] == "bad_params" and "Accepted params" in resp["message"]


def test_registry_undo_on_failure(fake_maya):
    @registry.command("test.boom", mutates=True)
    def boom():
        raise RuntimeError("kaboom")

    resp = registry.invoke("test.boom", {})
    assert resp["status"] == "error" and "kaboom" in resp["message"]
    names = [c[0] for c in fake_maya.calls]
    assert names.count("undoInfo") == 2 and "undo" in names
    del registry._REGISTRY["test.boom"]


# unit: safety -------------------------------------------------------------
def test_safety_validator():
    assert safety.validate("import os\nos.system('rm -rf /')")
    assert safety.validate("cmds.polySphere()") == []
    assert safety.validate("def (") and "syntax" in safety.validate("def (")[0]


# integration: real socket, real server, stub maya ---------------------------
def test_connection_ping_and_handshake(connection):
    assert connection.call("core.ping") == {"pong": True}
    info = connection.do_handshake()
    assert info["protocol_version"] == protocol.PROTOCOL_VERSION
    assert "core.execute_python" in info["commands"]


def test_connection_error_surface(connection):
    with pytest.raises(MayaError) as exc:
        connection.call("core.execute_python", {"code": "raise ValueError('bad')"})
    assert "ValueError" in str(exc.value)


def test_unavailable_message():
    from automaya_mcp.connection import MayaConnection

    conn = MayaConnection(port=1)
    with pytest.raises(MayaUnavailable) as exc:
        conn.call("core.ping")
    assert "automaya_bridge.start()" in str(exc.value)


async def test_tool_status(call_tool):
    data = parse(await call_tool("maya_get_status"))
    assert data["maya_version"] == "2024" and data["commands"] > 5


async def test_tool_execute_python_returns_expression(call_tool):
    data = parse(await call_tool("maya_execute_python", {"params": {"code": "x = 2\nx * 21"}}))
    assert data["result"] == 42


async def test_tool_execute_python_error_text(call_tool):
    text = await call_tool("maya_execute_python", {"params": {"code": "1/0"}})
    assert text.startswith("Error") and "ZeroDivisionError" in text


async def test_tool_safe_mode_blocks(call_tool, monkeypatch):
    monkeypatch.setenv("AUTOMAYA_SAFE_MODE", "1")
    text = await call_tool("maya_execute_python", {"params": {"code": "import subprocess"}})
    assert "safe mode" in text


async def test_drain_changes_without_openmaya(call_tool):
    data = parse(await call_tool("maya_drain_changes", {"params": {"summary": False}}))
    assert data["events"] == [] and data["active"] is False
