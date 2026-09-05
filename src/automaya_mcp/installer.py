"""`automaya-mcp install-plugin`: put the bridge where Maya 2024 finds it.

Writes a module file (automaya.mod) into the user's Maya modules folder that
points at this package's bundled ``maya_plugin`` directory, so nothing is
copied and upgrades are just a pip upgrade. Also prints the MCP client config
snippet. Use ``--copy`` to copy the plugin instead (for machines where the
pip install lives somewhere Maya cannot read).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def plugin_source() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent / "maya_plugin", here.parents[2] / "maya_plugin"):
        if (candidate / "automaya_bridge").is_dir():
            return candidate
    raise SystemExit("could not find the bundled maya_plugin folder next to the package")


def maya_app_dir() -> Path:
    env = os.environ.get("MAYA_APP_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "win32":
        return home / "Documents" / "maya"
    if sys.platform == "darwin":
        return home / "Library" / "Preferences" / "Autodesk" / "maya"
    return home / "maya"


def client_config(port: int = 9877) -> dict:
    return {
        "mcpServers": {
            "automaya": {
                "command": "automaya-mcp",
                "args": [],
                "env": {"AUTOMAYA_PORT": str(port)},
            }
        }
    }


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(prog="automaya-mcp install-plugin")
    parser.add_argument("--maya-version", default="2024")
    parser.add_argument("--copy", action="store_true", help="copy the plugin into the Maya app dir instead of linking via .mod")
    parser.add_argument("--dest", help="override the Maya app dir")
    args = parser.parse_args(argv)

    app_dir = Path(args.dest) if args.dest else maya_app_dir()
    modules_dir = app_dir / args.maya_version / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    src = plugin_source()

    if args.copy:
        target = app_dir / "automaya_plugin"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__"))
        root = target
    else:
        root = src

    mod_text = "+ MAYAVERSION:%s automaya 1.0.0 %s\nPYTHONPATH +:= .\nPYTHONPATH +:= scripts\nMAYA_SCRIPT_PATH +:= scripts\n" % (args.maya_version, str(root).replace("\\", "/"))
    mod_path = modules_dir / "automaya.mod"
    mod_path.write_text(mod_text, encoding="utf-8")

    print("Installed module file: %s" % mod_path)
    print("Plugin root:           %s" % root)
    print("\nRestart Maya %s. The AutoMaya menu and console appear automatically." % args.maya_version)
    print("If Maya was already open: import automaya_bridge; automaya_bridge.start(); automaya_bridge.show_console()")
    print("\nAdd this to your MCP client config (Claude Desktop: claude_desktop_config.json, Claude Code: .mcp.json):")
    print(json.dumps(client_config(), indent=2))


if __name__ == "__main__":
    main()
