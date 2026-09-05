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
    chunk_calls = [k for _, k in fake_maya.calls_to("undoInfo") if k.get("openChunk") or k.get("closeChunk")]
    assert len(chunk_calls) == 2 and "undo" in names
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


# unit: security review follow ups -------------------------------------------
def test_registry_skips_undo_when_chunk_was_empty(fake_maya):
    """A handler that fails before touching the scene leaves an empty chunk;
    Maya drops those, so undo() would revert the user's previous edit."""
    fake_maya.responses["undoInfo"] = lambda *a, **kw: "Move" if kw.get("query") else None

    @registry.command("test.early_fail", mutates=True)
    def early_fail():
        raise RuntimeError("bad input")

    resp = registry.invoke("test.early_fail", {})
    assert resp["status"] == "error"
    assert "undo" not in [c[0] for c in fake_maya.calls]
    del registry._REGISTRY["test.early_fail"]


def test_registry_undoes_when_chunk_is_on_queue(fake_maya):
    opened = {}

    def undo_info(*a, **kw):
        if kw.get("openChunk"):
            opened["name"] = kw["chunkName"]
        if kw.get("query"):
            return opened["name"]
        return None

    fake_maya.responses["undoInfo"] = undo_info

    @registry.command("test.partial", mutates=True)
    def partial():
        fake_maya.polyCube()
        raise RuntimeError("half done")

    registry.invoke("test.partial", {})
    assert "undo" in [c[0] for c in fake_maya.calls]
    del registry._REGISTRY["test.partial"]


def test_registry_type_error_inside_handler_is_not_bad_params(fake_maya):
    @registry.command("test.inner_type_error")
    def inner():
        return float(None)

    resp = registry.invoke("test.inner_type_error", {})
    assert resp["status"] == "error" and resp["code"] == "error" and "TypeError" in resp["message"]
    del registry._REGISTRY["test.inner_type_error"]


def test_allow_unsafe_is_not_reachable_over_the_wire(fake_maya, monkeypatch):
    monkeypatch.setenv("AUTOMAYA_SAFE_MODE", "1")
    resp = registry.invoke("core.execute_python", {"code": "import subprocess", "allow_unsafe": True})
    assert resp["status"] == "error" and resp["code"] == "bad_params"
    resp = registry.invoke("core.execute_python", {"code": "import subprocess"})
    assert resp["status"] == "error" and "safe mode" in resp["message"]


def test_plugin_safe_mode_blocks_reflection_and_mel(fake_maya, monkeypatch):
    monkeypatch.setenv("AUTOMAYA_SAFE_MODE", "1")
    for code in (
        "getattr(__builtins__, 'ev' + 'al')('1')",
        "import importlib; importlib.import_module('os')",
        "().__class__.__base__.__subclasses__()",
        "mel.eval('system(\"ls\")')",
        "cmds.python('import os')",
        "import sys; sys.modules['os']",
    ):
        resp = registry.invoke("core.execute_python", {"code": code})
        assert resp["status"] == "error" and "safe mode" in resp["message"], code
    resp = registry.invoke("core.execute_mel", {"code": 'system("rm -rf /")'})
    assert resp["status"] == "error" and "safe mode" in resp["message"]
    assert registry.invoke("core.execute_python", {"code": "cmds.polySphere()"})["status"] == "success"


def test_server_safety_validators_cover_bypasses():
    assert safety.validate("getattr(os, 'system')")
    assert safety.validate("x.__globals__")
    assert safety.validate("import pathlib")
    assert safety.validate("mel.eval('x')")
    assert safety.validate("sel = cmds.ls(sl=True); cmds.rename(sel[0], 'a')") == []
    assert safety.validate_mel('sysFile -delete "x"') and safety.validate_mel("polySphere -r 2") == []


async def test_tool_safe_mode_blocks_mel(call_tool, monkeypatch):
    monkeypatch.setenv("AUTOMAYA_SAFE_MODE", "1")
    text = await call_tool("maya_execute_mel", {"params": {"code": 'python("import os")'}})
    assert "safe mode" in text


def test_oversize_response_keeps_connection(connection, fake_maya, monkeypatch):
    """A result too large for one frame is reported as an error, the socket stays
    up, and the client does not resend the (mutating) command."""
    monkeypatch.setattr(protocol, "MAX_FRAME_BYTES", 4096)

    @registry.command("test.huge")
    def huge():
        return "x" * 8192

    try:
        with pytest.raises(MayaError) as exc:
            connection.call("test.huge")
        assert exc.value.code == "too_large"
        assert connection.connected and connection.call("core.ping") == {"pong": True}
    finally:
        del registry._REGISTRY["test.huge"]


def test_server_drops_client_that_stalls_after_header(bridge, monkeypatch):
    from automaya_bridge import server as plugin_server

    monkeypatch.setattr(plugin_server, "BODY_TIMEOUT", 0.3)
    with socket.create_connection(("127.0.0.1", bridge.port), timeout=5) as s:
        s.sendall(protocol.HEADER.pack(1_000_000))  # promise a body, never send it
        err = protocol.read_frame(s)
        assert err["status"] == "error" and err["code"] == "protocol" and "timed out" in err["message"]
        assert s.recv(1) == b""  # server closed the connection


def test_server_refuses_clients_beyond_the_cap(bridge, monkeypatch):
    from automaya_bridge import server as plugin_server

    monkeypatch.setattr(plugin_server, "MAX_CLIENTS", 1)
    first = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
    try:
        protocol.write_frame(first, protocol.make_request("core.ping"))
        assert protocol.read_frame(first)["status"] == "success"
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=5) as second:
            second.settimeout(5)
            assert protocol.read_frame(second)["code"] == "busy"
    finally:
        first.close()


def test_broadcaster_publish_never_blocks_main_thread(fake_maya):
    """publish() only queues; a subscriber that stops reading is dropped by the
    writer thread instead of stalling the caller."""
    import time

    from automaya_bridge import events

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    bc = events.Broadcaster(port=port)
    bc.start()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        for _ in range(50):
            if bc.subscriber_count():
                break
            time.sleep(0.02)
        started = time.monotonic()
        big = {"kind": "blob", "data": "y" * 200_000}
        for _ in range(200):  # 40 MB into a client that never reads
            bc.publish(big)
        assert time.monotonic() - started < 2.0
        for _ in range(100):
            if bc.subscriber_count() == 0:
                break
            time.sleep(0.1)
        assert bc.subscriber_count() == 0
        client.close()
    finally:
        bc.stop()
