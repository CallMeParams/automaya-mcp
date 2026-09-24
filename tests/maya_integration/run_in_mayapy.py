"""Integration test pattern: run inside a real Maya.

    mayapy tests/maya_integration/run_in_mayapy.py
    (or paste into the Script Editor; it will not create a new scene without --new)

Boots the bridge in this process, opens a real socket client, and exercises
one command per domain against real maya.cmds. Prints a pass/fail table.

In mayapy the client runs on a worker thread while the main thread pumps
bridge commands (``automaya_bridge.pump``), the same shape a render farm or
CI job uses with ``automaya_bridge.serve_forever()``. Inside an interactive
Maya the checks run on a worker in the background and print as they finish.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "maya_plugin"), os.path.join(ROOT, "src")]

import maya.standalone  # type: ignore  # noqa: E402

try:
    maya.standalone.initialize(name="python")
except Exception:
    pass  # already inside an interactive Maya

from maya import cmds  # type: ignore  # noqa: E402

import automaya_bridge  # noqa: E402
from automaya_mcp.connection import MayaConnection  # noqa: E402

CHECKS = [
    ("core.handshake", {}),
    ("scene.get_info", {}),
    ("modeling.create_primitive", {"kind": "cube", "name": "it_cube", "size": 2}),
    ("materials.create", {"type": "standardSurface", "name": "it_mat", "assign_to": ["it_cube"]}),
    ("rig.create_joint_chain", {"positions": [[0, 0, 0], [0, 5, 0], [0, 10, 0]], "names": ["it_j1", "it_j2", "it_j3"]}),
    ("previs.create_camera", {"name": "it_cam", "focal_length": 35}),
    ("anim.set_keyframe", {"nodes": ["it_cube"], "attrs": ["translateY"], "time": 10, "value": 5}),
    ("intel.scene_summary", {"max_nodes": 50}),
    ("intel.find_problems", {}),
    ("introspect.command_help", {"command_name": "polyCube"}),
    ("livelink.snapshot_scene_graph", {}),
    ("livelink.get_mesh_buffers", {"node": "it_cube"}),
    ("fx.list_dynamics", {}),
    ("arnold.status", {}),
    ("core.drain_changes", {"summary": True}),
]


def _run_checks(port: int, out: dict) -> None:
    conn = MayaConnection(port=port, default_timeout=60)
    failures = 0
    try:
        for name, params in CHECKS:
            try:
                result = conn.call(name, params)
                print("PASS %-32s %s" % (name, str(result)[:90]))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("FAIL %-32s %s" % (name, exc))
                remote = getattr(exc, "traceback_text", "") or ""
                if remote:
                    print("     maya traceback (tail):")
                    for line in remote.strip().splitlines()[-6:]:
                        print("       " + line)
                else:
                    traceback.print_exc(limit=2)
    finally:
        conn.disconnect()
        out["failures"] = failures


def main() -> int:
    if "--new" in sys.argv:
        cmds.file(new=True, force=True)
    srv = automaya_bridge.start(port=9899, events=True)
    out: dict = {}
    worker = threading.Thread(target=_run_checks, args=(srv.port, out), name="automaya-checks", daemon=True)
    worker.start()
    if not cmds.about(batch=True):
        # Interactive Maya: the UI thread must stay free to run the commands,
        # so return now; results print to the Script Editor as they arrive.
        print("checks running in the background, results follow")
        return 0
    while worker.is_alive():
        automaya_bridge.pump(timeout=0.05)
    automaya_bridge.stop()
    failures = out.get("failures", len(CHECKS))
    print("\n%d checks, %d failures" % (len(CHECKS), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
