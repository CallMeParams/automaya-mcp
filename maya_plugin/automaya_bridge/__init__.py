"""AutoMaya bridge: the part of AutoMaya MCP that runs inside Maya 2024.

Usage from the Script Editor::

    import automaya_bridge
    automaya_bridge.start()          # socket server on 127.0.0.1:9877
    automaya_bridge.show_console()   # dockable console
    automaya_bridge.stop()

With the module file installed (``scripts/install_plugin.py``) this happens
automatically on launch via ``userSetup.py`` and an "AutoMaya" menu appears.
"""
from __future__ import annotations

from . import prefs, protocol, server

__version__ = server.PLUGIN_VERSION
__all__ = ["start", "stop", "show_console", "install_menu", "__version__"]


def start(port: int | None = None, events: bool | None = None) -> server.BridgeServer:
    p = prefs.load()
    srv = server.start(port=int(port or p.get("port", protocol.DEFAULT_PORT)))
    track = p.get("auto_events", True) if events is None else events
    if track:
        from . import events as _events

        _events.BUS.start()
    return srv


def stop() -> None:
    from . import events as _events

    if _events.BUS.broadcaster is not None:
        _events.BUS.broadcaster.stop()
        _events.BUS.broadcaster = None
    _events.BUS.stop()
    server.stop()


def show_console() -> None:
    from . import console

    console.show()


def install_menu() -> None:
    """Add an AutoMaya menu to the main window (no-op in batch mode)."""
    try:
        from maya import cmds, mel  # type: ignore
    except ImportError:
        return
    if cmds.about(batch=True):
        return
    if cmds.menu("AutoMayaMenu", exists=True):
        cmds.deleteUI("AutoMayaMenu")
    main_window = mel.eval("$tmp = $gMainWindow")
    cmds.menu("AutoMayaMenu", label="AutoMaya", parent=main_window, tearOff=True)
    cmds.menuItem(label="Show Console", command=lambda *_: show_console())
    cmds.menuItem(label="Start Bridge", command=lambda *_: start())
    cmds.menuItem(label="Stop Bridge", command=lambda *_: stop())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="Version %s" % __version__, enable=False)
