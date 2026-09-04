"""Auto start hook installed by scripts/install_plugin.py.

Runs when Maya finishes initialising. Deferred so the main window exists before
the menu and console are created.
"""
try:
    from maya import cmds, utils  # type: ignore

    def _automaya_boot() -> None:
        try:
            import automaya_bridge
            from automaya_bridge import prefs

            p = prefs.load()
            automaya_bridge.install_menu()
            if p.get("auto_start", True):
                automaya_bridge.start()
                if not cmds.about(batch=True):
                    automaya_bridge.show_console()
        except Exception as exc:  # never break Maya startup
            print("[AutoMaya] startup failed: %s" % exc)

    if cmds.about(batch=True):
        _automaya_boot()
    else:
        utils.executeDeferred(_automaya_boot)
except ImportError:
    pass
