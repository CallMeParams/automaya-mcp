"""AutoMaya MCP launcher for Maya 2024's mayapy, for machines without Python 3.10+.

One time setup (Windows paths shown):

    "C:\\Program Files\\Autodesk\\Maya2024\\bin\\mayapy.exe" -m pip install --target %USERPROFILE%\\.automaya\\site mcp==1.27.0 httpx pydantic pillow pywin32
    copy scripts\\mayapy_launch.py %USERPROFILE%\\.automaya\\launch.py

Usage:  mayapy launch.py            run the MCP server on stdio
        mayapy launch.py --smoke    import check, prints tool count

AUTOMAYA_REPO points at the repo if it is not in ~/Documents/GitHub/automaya-mcp.
Write this file as UTF-8 without a BOM (Windows PowerShell 5 Set-Content adds one).
"""
import os
import sys

base = os.path.join(os.path.expanduser("~"), ".automaya")
site = os.path.join(base, "site")
repo = os.environ.get("AUTOMAYA_REPO") or os.path.join(os.path.expanduser("~"), "Documents", "GitHub", "automaya-mcp")
# --target installs skip .pth files, so pywin32's paths are added by hand
for p in (site, os.path.join(site, "win32"), os.path.join(site, "win32", "lib"), os.path.join(repo, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
dll = os.path.join(site, "pywin32_system32")
if os.path.isdir(dll):
    os.add_dll_directory(dll)
    os.environ["PATH"] = dll + os.pathsep + os.environ.get("PATH", "")

if "--smoke" in sys.argv:
    import asyncio

    import mcp.server.stdio  # noqa: F401

    from automaya_mcp.server import create_app

    print("tools", len(asyncio.run(create_app().list_tools())))
else:
    from automaya_mcp.server import main

    main(list(sys.argv[1:]))
