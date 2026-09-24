"""Batch mode pump: socket threads hand work to the main thread."""
from __future__ import annotations

import threading

from automaya_bridge import server


def test_pump_runs_jobs_on_the_main_thread(monkeypatch):
    monkeypatch.setattr(server, "_BATCH_MODE", True)
    seen = {}

    def job():
        seen["main"] = threading.current_thread() is threading.main_thread()
        return 42

    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("value", server.run_on_main_thread(job)))
    server._PUMP_ACTIVE.set()
    try:
        worker.start()
        while worker.is_alive():
            server.pump(timeout=0.02)
    finally:
        server._PUMP_ACTIVE.clear()
    assert result["value"] == 42
    assert seen["main"] is True


def test_pump_hands_exceptions_back(monkeypatch):
    monkeypatch.setattr(server, "_BATCH_MODE", True)

    def boom():
        raise ValueError("nope")

    caught = {}

    def call():
        try:
            server.run_on_main_thread(boom)
        except ValueError as exc:
            caught["msg"] = str(exc)

    worker = threading.Thread(target=call)
    server._PUMP_ACTIVE.set()
    try:
        worker.start()
        while worker.is_alive():
            server.pump(timeout=0.02)
    finally:
        server._PUMP_ACTIVE.clear()
    assert caught["msg"] == "nope"


def test_serve_forever_stops_and_releases_waiters(monkeypatch):
    monkeypatch.setattr(server, "_BATCH_MODE", True)
    stop = threading.Event()
    stop.set()
    server.serve_forever(stop_event=stop)
    assert not server._PUMP_ACTIVE.is_set()


def test_without_pump_runs_inline(monkeypatch):
    monkeypatch.setattr(server, "_BATCH_MODE", True)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", server.run_on_main_thread(lambda: threading.current_thread().name)))
    t.start()
    t.join(2)
    assert out["v"] == t.name
