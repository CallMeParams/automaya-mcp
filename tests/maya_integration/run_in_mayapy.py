"""Integration test pattern: run inside a real Maya.

    mayapy tests/maya_integration/run_in_mayapy.py
    (or paste into the Script Editor; it will not create a new scene without --new)

Boots the bridge in this process, opens a real socket client, and exercises
one command per domain against real maya.cmds. Prints a pass/fail table.
"""
from __future__ import annotations

import os
import sys
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
    ("introspect.command_help", {"command": "polyCube"}),
    ("livelink.snapshot_scene_graph", {}),
    ("livelink.get_mesh_buffers", {"node": "it_cube"}),
    ("fx.list_dynamics", {}),
    ("arnold.status", {}),
    ("core.drain_changes", {"summary": True}),
]


def main() -> int:
    if "--new" in sys.argv:
        cmds.file(new=True, force=True)
    srv = automaya_bridge.start(port=9899, events=True)
    conn = MayaConnection(port=srv.port, default_timeout=60)
    failures = 0
    for name, params in CHECKS:
        try:
            result = conn.call(name, params)
            print("PASS %-32s %s" % (name, str(result)[:90]))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("FAIL %-32s %s" % (name, exc))
            traceback.print_exc(limit=1)
    conn.disconnect()
    automaya_bridge.stop()
    print("\n%d checks, %d failures" % (len(CHECKS), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
