"""Dockable AutoMaya console for Maya 2024 (PySide2).

Tabs:
  Console   live log of every command the agent runs, with results and errors
  Changes   the OpenMaya change feed (what the human or the agent modified)
  REPL      run Python in Maya with the same undo wrapper the agent uses

The gear button in the header opens the Settings dialog
(``settings_dialog.SettingsDialog``): general, connection, AI 3D API keys
with a Test button per provider, asset libraries, live link and safe mode.

Opens with ``automaya_bridge.show_console()`` or from the AutoMaya menu.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from . import events, prefs, protocol, server, settings_dialog

try:
    from maya import OpenMayaUI as omui
    from maya import cmds  # type: ignore
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    from shiboken2 import wrapInstance  # type: ignore
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

CONTROL_NAME = "AutoMayaConsoleWorkspaceControl"
WIDGET_NAME = "AutoMayaConsoleWidget"

_LEVEL_COLORS = {
    "cmd": "#7fb4ff",
    "ok": "#8ee38e",
    "error": "#ff7b7b",
    "warn": "#ffd27f",
    "info": "#bbbbbb",
    "repl": "#e3c8ff",
}



def _maya_main_window() -> QtWidgets.QWidget | None:
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


if HAVE_QT:

    class _Signals(QtCore.QObject):
        log = QtCore.Signal(dict)
        event = QtCore.Signal(dict)

    class ConsoleWidget(QtWidgets.QWidget):
        def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName(WIDGET_NAME)
            self.signals = _Signals()
            self.signals.log.connect(self._append_log)
            self.signals.event.connect(self._append_event)
            self._prefs = prefs.load()
            self._build()
            server.LOG.subscribe(self.signals.log.emit)
            self._event_timer = QtCore.QTimer(self)
            self._event_timer.timeout.connect(self._poll_events)
            self._event_timer.start(250)
            self._last_seq = 0
            self._status_timer = QtCore.QTimer(self)
            self._status_timer.timeout.connect(self._refresh_status)
            self._status_timer.start(1000)
            for entry in server.LOG.tail(200):
                self._append_log(entry)
            self._refresh_status()

        # ui -----------------------------------------------------------------
        def _build(self) -> None:
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)

            header = QtWidgets.QHBoxLayout()
            self.status_dot = QtWidgets.QLabel("●")
            self.status_label = QtWidgets.QLabel("stopped")
            self.port_spin = QtWidgets.QSpinBox()
            self.port_spin.setRange(1024, 65535)
            self.port_spin.setValue(int(self._prefs.get("port", protocol.DEFAULT_PORT)))
            self.toggle_btn = QtWidgets.QPushButton("Connect")
            self.toggle_btn.clicked.connect(self._toggle_server)
            header.addWidget(self.status_dot)
            header.addWidget(self.status_label, 1)
            header.addWidget(QtWidgets.QLabel("Port"))
            header.addWidget(self.port_spin)
            header.addWidget(self.toggle_btn)
            self.settings_btn = QtWidgets.QToolButton()
            self.settings_btn.setText("\u2699")
            self.settings_btn.setToolTip("Settings (ports, API keys, safe mode)")
            self.settings_btn.setAutoRaise(True)
            self.settings_btn.clicked.connect(self._open_settings)
            header.addWidget(self.settings_btn)
            layout.addLayout(header)

            self.tabs = QtWidgets.QTabWidget()
            layout.addWidget(self.tabs, 1)

            # console tab
            console = QtWidgets.QWidget()
            cl = QtWidgets.QVBoxLayout(console)
            self.log_view = QtWidgets.QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setMaximumBlockCount(3000)
            self.log_view.setFont(QtGui.QFont("Consolas" if QtCore.QSysInfo.productType() == "windows" else "Menlo", 9))
            cl.addWidget(self.log_view)
            row = QtWidgets.QHBoxLayout()
            clear = QtWidgets.QPushButton("Clear")
            clear.clicked.connect(self.log_view.clear)
            self.stats_label = QtWidgets.QLabel("")
            row.addWidget(self.stats_label, 1)
            row.addWidget(clear)
            cl.addLayout(row)
            self.tabs.addTab(console, "Console")

            # changes tab
            changes = QtWidgets.QWidget()
            chl = QtWidgets.QVBoxLayout(changes)
            self.event_view = QtWidgets.QPlainTextEdit()
            self.event_view.setReadOnly(True)
            self.event_view.setMaximumBlockCount(3000)
            chl.addWidget(self.event_view)
            erow = QtWidgets.QHBoxLayout()
            self.events_btn = QtWidgets.QPushButton("Start change tracking")
            self.events_btn.clicked.connect(self._toggle_events)
            self.stream_btn = QtWidgets.QPushButton("Start broadcast")
            self.stream_btn.clicked.connect(self._toggle_stream)
            self.transform_only = QtWidgets.QCheckBox("Transforms only")
            self.transform_only.setChecked(bool(self._prefs.get("transform_only", True)))
            events.BUS.transform_only = self.transform_only.isChecked()
            self.transform_only.toggled.connect(lambda v: setattr(events.BUS, "transform_only", v))
            self.event_port = int(self._prefs.get("event_port", protocol.DEFAULT_EVENT_PORT))
            erow.addWidget(self.events_btn)
            erow.addWidget(self.stream_btn)
            erow.addWidget(self.transform_only)
            erow.addStretch(1)
            chl.addLayout(erow)
            self.tabs.addTab(changes, "Changes")

            # repl tab
            repl = QtWidgets.QWidget()
            rl = QtWidgets.QVBoxLayout(repl)
            self.repl_input = QtWidgets.QPlainTextEdit()
            self.repl_input.setPlaceholderText("Python, with cmds / pm / om already imported. Ctrl+Enter to run.")
            self.repl_input.installEventFilter(self)
            rl.addWidget(self.repl_input, 1)
            run = QtWidgets.QPushButton("Run (Ctrl+Enter)")
            run.clicked.connect(self._run_repl)
            rl.addWidget(run)
            self.tabs.addTab(repl, "REPL")

        # behaviour ----------------------------------------------------------
        def eventFilter(self, obj: Any, ev: Any) -> bool:  # noqa: N802
            if obj is self.repl_input and ev.type() == QtCore.QEvent.KeyPress:
                if ev.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and ev.modifiers() & QtCore.Qt.ControlModifier:
                    self._run_repl()
                    return True
            return super().eventFilter(obj, ev)

        def _save_pref(self, key: str, value: Any) -> None:
            data = prefs.load()
            data[key] = value
            prefs.save(data)

        def _open_settings(self) -> None:
            if settings_dialog.open_settings(self) != QtWidgets.QDialog.Accepted:
                return
            self._prefs = prefs.load()
            srv = server.get_server()
            if not (srv is not None and srv.running):
                self.port_spin.setValue(int(self._prefs.get("port", protocol.DEFAULT_PORT)))
            self.event_port = int(self._prefs.get("event_port", protocol.DEFAULT_EVENT_PORT))
            self.transform_only.setChecked(bool(self._prefs.get("transform_only", True)))
            server.LOG.add("info", "settings saved")

        def _toggle_server(self) -> None:
            srv = server.get_server()
            if srv is not None and srv.running:
                server.stop()
            else:
                port = int(self.port_spin.value())
                self._save_pref("port", port)
                try:
                    server.start(port=port)
                except OSError as exc:
                    server.LOG.add("error", "could not bind port %d: %s" % (port, exc))
            self._refresh_status()

        def _toggle_events(self) -> None:
            if events.BUS.active:
                events.BUS.stop()
            else:
                events.BUS.start()
            self._refresh_status()

        def _toggle_stream(self) -> None:
            bc = events.BUS.broadcaster
            if bc is not None and bc.running:
                bc.stop()
                events.BUS.broadcaster = None
            else:
                bc = events.Broadcaster(port=int(self.event_port))
                try:
                    bc.start()
                    events.BUS.broadcaster = bc
                    if not events.BUS.active:
                        events.BUS.start()
                except OSError as exc:
                    server.LOG.add("error", "could not start broadcast: %s" % exc)
            self._refresh_status()

        def _run_repl(self) -> None:
            code = self.repl_input.toPlainText()
            if not code.strip():
                return
            from .handlers import core as core_handlers

            server.LOG.add("repl", code.strip().splitlines()[0][:120] + (" ..." if "\n" in code.strip() else ""))
            result = core_handlers.run_python(code, allow_unsafe=True)
            out = result.get("stdout") or ""
            if result.get("result") is not None:
                out += ("\n" if out else "") + repr(result["result"])
            if result.get("error"):
                server.LOG.add("error", result["error"])
            elif out:
                server.LOG.add("ok", out)

        def _refresh_status(self) -> None:
            srv = server.get_server()
            running = srv is not None and srv.running
            self.status_dot.setStyleSheet("color: %s; font-size: 14px" % ("#8ee38e" if running else "#ff7b7b"))
            self.status_label.setText("connected on port %d" % srv.port if running else "stopped")
            self.toggle_btn.setText("Disconnect" if running else "Connect")
            self.port_spin.setEnabled(not running)
            if srv is not None:
                s = srv.stats
                self.stats_label.setText("commands %d   errors %d   clients %d" % (s["commands"], s["errors"], s["clients_total"]))
            self.events_btn.setText("Stop change tracking" if events.BUS.active else "Start change tracking")
            bc = events.BUS.broadcaster
            streaming = bc is not None and bc.running
            self.stream_btn.setText("Stop broadcast (%d subs)" % bc.subscriber_count() if streaming else "Start broadcast")

        def _poll_events(self) -> None:
            data = events.BUS.drain(since_seq=self._last_seq, limit=200)
            for e in data["events"]:
                self.signals.event.emit(e)
            self._last_seq = data["last_seq"]

        def _append_log(self, entry: Dict[str, Any]) -> None:
            color = _LEVEL_COLORS.get(entry.get("level", "info"), "#bbbbbb")
            ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", time.time())))
            text = entry.get("text", "")
            extra = ""
            if entry.get("params") and entry["params"] != "{}":
                extra = " " + entry["params"]
            html = '<span style="color:#777">%s</span> <span style="color:%s">%s</span><span style="color:#999">%s</span>' % (
                ts, color, _escape(text), _escape(extra))
            self.log_view.appendHtml(html)

        def _append_event(self, e: Dict[str, Any]) -> None:
            who = "you" if e.get("human") else "agent"
            payload = {k: v for k, v in e.items() if k not in ("seq", "ts", "kind", "human")}
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", time.time())))
            self.event_view.appendHtml('<span style="color:#777">%s</span> <span style="color:#ffd27f">%s</span> <b>%s</b> %s' % (
                ts, who, e.get("kind"), _escape(json.dumps(payload, default=str)[:300])))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def show() -> None:
    """Create or raise the dockable console."""
    if not HAVE_QT:
        raise RuntimeError("PySide2 is not available in this Maya session")
    if cmds.workspaceControl(CONTROL_NAME, exists=True):
        cmds.workspaceControl(CONTROL_NAME, edit=True, restore=True)
        return
    cmds.workspaceControl(
        CONTROL_NAME,
        label="AutoMaya",
        retain=False,
        floating=False,
        dockToControl=("AttributeEditor", "left") if cmds.workspaceControl("AttributeEditor", exists=True) else None,
        initialWidth=420,
        initialHeight=600,
    )
    ptr = omui.MQtUtil.findControl(CONTROL_NAME)
    host = wrapInstance(int(ptr), QtWidgets.QWidget)
    widget = ConsoleWidget()
    host.layout().addWidget(widget)


def close() -> None:
    if HAVE_QT and cmds.workspaceControl(CONTROL_NAME, exists=True):
        cmds.deleteUI(CONTROL_NAME)
