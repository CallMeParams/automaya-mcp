---
tags: [automaya, claude]
---
# Agent playbook

How Claude should use AutoMaya well.

1. `maya_get_status` first. If it fails, tell the user how to start the bridge (AutoMaya menu or `automaya_bridge.start()`).
2. Look before touching: `maya_scene_summary`, `maya_viewport_screenshot`, `maya_get_selection`. After every batch of edits, screenshot again.
3. Prefer typed tools. Fall back to `maya_execute_python` for anything missing, and say so, because that is a signal a tool should exist.
4. Call `maya_drain_changes` before resuming multi step work; the user may have moved things by hand.
5. Names: keep them meaningful, group imports, freeze transforms and delete history on finished assets, check `maya_find_problems` before handing off.
6. Assets: free libraries first, AI generation for a single hero object, always at real world scale (cm).
7. Previs: set fps and range with `maya_setup_scene_for_previs`, cameras through `maya_create_camera` or the crane rig, playblast to verify motion, register shots in the sequencer.
8. Long jobs (renders, simulations, generation): pass a timeout, poll rather than wait blindly.
9. Unreal: `maya_livelink_start`, `maya_livelink_subscribe` to the nodes that matter, `transform_only` during playback.
10. Never assume a plugin is loaded; the tools load mtoa, bullet, AbcExport, mayaUsdPlugin on demand and explain if they are missing.
