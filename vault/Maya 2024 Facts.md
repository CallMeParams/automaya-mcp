---
tags: [automaya, maya]
---
# Maya 2024 facts that shaped the code

- Interpreter: Python 3.10.8, PySide2 5.15.2, shiboken2, Qt 5.15. No pip packages can be assumed inside Maya, so the plugin is stdlib only.
- Threading: `maya.cmds` and OpenMaya are main thread only. Use `maya.utils.executeInMainThreadWithResult` from worker threads; `executeDeferred` does nothing useful in mayapy/batch (no event loop), so the bridge runs inline in batch mode.
- `commandPort`: text protocol, security prompt each session, blocks the UI, cannot return values from multi line Python. That is why AutoMaya ships its own socket plugin.
- Undo: `cmds.undoInfo(openChunk=True, chunkName=...)` / `closeChunk=True`; `cmds.undo()` after a failed chunk restores the scene. Never wrap `cmds.undo` itself in a chunk.
- Imports: FBX (`fbxmaya`), OBJ (`objExport`), Alembic (`AbcImport`/`AbcExport`), USD (`mayaUsdPlugin`), MA/MB. No native glTF/GLB import in 2024: ask providers for FBX (the gen3d tools default to fbx) or install a glTF plugin.
- Cameras: film aperture is inches (`mm / 25.4`), focal length mm, `cmds.viewFit` frames, `cmds.playblast(format="image", compression="png", completeFilename=..., offScreen=True)` for stills.
- Arnold: plugin `mtoa`, render options node `defaultArnoldRenderOptions`, driver `defaultArnoldDriver.aiTranslator` (exr/png/jpeg), `cmds.arnoldRender(camera=..., width=..., height=...)`.
- Units: default cm, Y up, 24 fps ("film"). Unreal is cm, Z up, left handed; see [[Unreal Real Time Viewport]].
- OpenMaya 2 (`maya.api.OpenMaya`): `MFnMesh.getPoints/getNormals/getUVs/getAssignedUVs/getTriangles` for buffers; `MNodeMessage.addAttributeChangedCallback` for change tracking; callbacks must be removed with `MMessage.removeCallback`.
- Doc URL patterns: cmds `https://help.autodesk.com/cloudhelp/2024/ENU/Maya-Tech-Docs/CommandsPython/<cmd>.html`; nodes `.../Nodes/<type>.html`; OpenMaya `https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=Maya_SDK_py_ref_class_open_maya_1_1_m_<snake>_html`.
