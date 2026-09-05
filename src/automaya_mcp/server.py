"""AutoMaya MCP server entry point.

Run ``automaya-mcp`` (stdio) or ``python -m automaya_mcp``. Environment:

  AUTOMAYA_HOST / AUTOMAYA_PORT   bridge address (default 127.0.0.1:9877)
  AUTOMAYA_TIMEOUT                default per command timeout in seconds
  AUTOMAYA_SAFE_MODE=1            reject shell/network/file access in python tools
  AUTOMAYA_MODULES=core,scene,... load only these tool modules (smaller tool list)
  <PROVIDER>_API_KEY              see providers/ for the exact names
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

from mcp.server.fastmcp import FastMCP

from .connection import MayaConnection, connection_from_env
from .tools._base import ToolContext

TOOL_MODULES = [
    "core",
    "scene",
    "modeling",
    "materials",
    "rigging_animation",
    "previs",
    "sim_vfx",
    "arnold",
    "assets",
    "generation",
    "intelligence",
    "livelink",
    "introspect",
    "craft_procgen",
    "craft_light",
    "craft_lookdev",
    "craft_critique",
    "craft_photo",
    "craft_plan",
]

log = logging.getLogger("automaya")


def create_app(bridge: MayaConnection | None = None, modules: list | None = None) -> FastMCP:
    bridge = bridge or connection_from_env()
    ctx = ToolContext(bridge)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[Dict[str, object]]:
        # Connect lazily: the server must start even if Maya is not up yet.
        try:
            if bridge.connect(timeout=1.5):
                info = bridge.do_handshake()
                log.info("connected to Maya %s, plugin %s", info.get("maya_version"), info.get("plugin_version"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Maya not reachable at startup: %s", exc)
        try:
            yield {"bridge": bridge}
        finally:
            bridge.disconnect()

    mcp = FastMCP(
        "automaya_mcp",
        instructions=(
            "AutoMaya controls a live Autodesk Maya 2024 session. Start with maya_get_status. "
            "Prefer typed tools over maya_execute_python; use maya_viewport_screenshot and "
            "maya_scene_summary to verify results; call maya_drain_changes to learn what the user "
            "edited by hand. For assets: free libraries first (Poly Haven, Sketchfab, Poly Pizza), "
            "AI generation (Tripo, Meshy, Rodin, Hunyuan, Higgsfield) for one hero object at a time."
        ),
        lifespan=lifespan,
    )

    if modules is None:
        env_modules = os.environ.get("AUTOMAYA_MODULES", "").strip()
        modules = [m.strip() for m in env_modules.split(",") if m.strip()] if env_modules else TOOL_MODULES
        unknown = [m for m in modules if m not in TOOL_MODULES]
        if unknown:
            raise SystemExit("AUTOMAYA_MODULES has unknown modules %s; valid: %s" % (unknown, ", ".join(TOOL_MODULES)))
    for name in modules:
        module = importlib.import_module("automaya_mcp.tools.%s" % name)
        module.register(mcp, ctx)

    from . import prompts

    prompts.register(mcp, ctx)
    return mcp


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s %(levelname)s %(message)s")
    if argv and argv[0] == "install-plugin":
        from .installer import main as install_main

        install_main(argv[1:])
        return
    if argv and argv[0] == "discover":
        from .connection import discover_ports

        print("bridges found on ports:", discover_ports())
        return
    create_app().run(transport="stdio")


if __name__ == "__main__":
    main()
