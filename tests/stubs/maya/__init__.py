"""Recording stub of the ``maya`` package so plugin handlers can be unit tested
outside Maya. ``cmds.<anything>(...)`` records the call and returns whatever
``cmds.responses`` maps that command to (a value or a callable), else a
sensible default. Tests reset it with ``cmds.reset()``.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Callable, Dict, List, Tuple


class _CmdsStub(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("maya.cmds")
        self.calls: List[Tuple[str, tuple, dict]] = []
        self.responses: Dict[str, Any] = {}
        self.existing: set = set()

    def reset(self) -> None:
        self.calls.clear()
        self.responses.clear()
        self.existing.clear()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("__"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            resp = self.responses.get(name, _MISSING)
            if resp is not _MISSING:
                return resp(*args, **kwargs) if callable(resp) else resp
            return self._default(name, args, kwargs)

        return _call

    def calls_to(self, name: str) -> List[Tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]

    def _default(self, name: str, args: tuple, kwargs: dict) -> Any:
        if name == "objExists":
            return (args[0] in self.existing) if self.existing else True
        if name == "about":
            if kwargs.get("batch"):
                return True
            if kwargs.get("version"):
                return "2024"
            if kwargs.get("apiVersion"):
                return 20240000
            return "stub"
        if name == "pluginInfo":
            return True if kwargs.get("loaded") else []
        if name == "ls":
            return ["|stubNode"] if kwargs.get("selection") else []
        if name in ("nodeType", "objectType"):
            if kwargs.get("isType"):
                return kwargs["isType"] == "transform"
            return "transform"
        if name == "getAttr":
            return [(0.0, 0.0, 0.0)]
        if name == "currentUnit":
            return "cm"
        if name == "upAxis":
            return "y"
        if name == "file":
            return ""
        if name == "rename":
            return args[1] if len(args) > 1 else args[0]
        if name in ("createNode", "shadingNode", "sets", "group", "nucleus", "ikHandle"):
            if name == "ikHandle":
                return [kwargs.get("name", "ikHandle1"), "effector1"]
            return kwargs.get("name") or kwargs.get("n") or ((args[0] if args and isinstance(args[0], str) else name) + "1")
        if name == "playbackOptions":
            return 1.0
        if name in ("polySphere", "polyCube", "polyCylinder", "polyPlane", "polyTorus", "polyCone", "camera", "joint", "group", "duplicate", "circle", "curve", "spaceLocator", "shadingNode", "sets", "createNode", "polyPipe", "polyDisc", "polyPrism", "polyPyramid", "polyHelix", "polyPlatonic", "polySuperShape", "instance", "rename", "ikHandle", "parent", "aimConstraint", "pointConstraint", "orientConstraint", "parentConstraint", "listRelatives", "listConnections", "listAttr", "listHistory", "polyEvaluate", "polyInfo", "polyExtrudeFacet", "polyBevel3", "polyBoolOp", "polyUnite", "polySeparate", "polyMirrorFace", "polySmooth", "polyReduce", "lattice", "cluster", "nonLinear", "textCurves", "extrude", "loft", "revolve", "nurbsToPoly", "polyAutoProjection", "polyCleanupArgList", "imagePlane", "playblast", "render", "keyframe", "listCameras", "ambientLight", "directionalLight", "pointLight", "spotLight", "areaLight", "skinCluster", "bakeResults", "pathAnimation", "blendShape", "particle", "nParticle", "emitter", "gravity", "turbulence", "nucleus", "fluidEmitter", "instancer", "lookThru", "shot", "sequenceManager", "menu", "menuItem", "workspaceControl", "help", "namespace", "referenceQuery"):
            return []
        return None


class _MelStub(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("maya.mel")
        self.evaluated: List[str] = []
        self.responses: Dict[str, Any] = {}

    def eval(self, code: str) -> Any:
        self.evaluated.append(code)
        for key, val in self.responses.items():
            if key in code:
                return val(code) if callable(val) else val
        return None


class _UtilsStub(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("maya.utils")

    @staticmethod
    def executeInMainThreadWithResult(func: Callable[[], Any], *args: Any) -> Any:
        return func(*args)

    @staticmethod
    def executeDeferred(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


_MISSING = object()

cmds = _CmdsStub()
mel = _MelStub()
utils = _UtilsStub()
OpenMayaUI = types.ModuleType("maya.OpenMayaUI")

sys.modules["maya.cmds"] = cmds
sys.modules["maya.mel"] = mel
sys.modules["maya.utils"] = utils
sys.modules["maya.OpenMayaUI"] = OpenMayaUI
