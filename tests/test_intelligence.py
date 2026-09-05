"""Unit + integration tests for the intelligence (intel.*) domain."""
from __future__ import annotations

import os
import struct
import zlib

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import intelligence
from automaya_bridge.handlers._util import BridgeError


def _tiny_png(w=4, h=2) -> bytes:
    """Smallest valid PNG we can write without PIL."""
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _scene(fake_maya, transforms=("|grp", "|grp|cube", "|light1"), selection=()):
    """A tiny world: grp > cube (mesh, 12 faces), plus a light."""
    tree = {"|grp": ["|grp|cube"], "|grp|cube": [], "|light1": []}
    shapes = {"|grp|cube": ["|grp|cube|cubeShape"], "|light1": ["|light1|light1Shape"]}
    types = {"|grp|cube|cubeShape": "mesh", "|light1|light1Shape": "pointLight"}

    def ls(*args, **kw):
        if kw.get("selection"):
            return list(selection)
        if kw.get("assemblies"):
            return ["|grp", "|light1"]
        if kw.get("type") == "transform":
            if args:
                return [n for n in transforms if any(n.startswith(a) for a in (args[0] if isinstance(args[0], list) else [args[0]]))]
            return list(transforms)
        if kw.get("type") == "mesh":
            return ["|grp|cube|cubeShape"]
        if kw.get("type") == "joint" or kw.get("type") in ("file", "shadingEngine", "displayLayer"):
            return []
        if kw.get("lights"):
            return ["|light1|light1Shape"]
        if kw.get("materials"):
            return ["lambert1", "myMat"]
        if kw.get("references"):
            return []
        if args and kw.get("long"):
            a = args[0] if isinstance(args[0], str) else args[0][0]
            return [a if a.startswith("|") else "|" + a]
        return []

    def list_relatives(node, **kw):
        if kw.get("shapes"):
            found = shapes.get(node, [])
            if kw.get("type"):
                found = [s for s in found if types.get(s) == kw["type"]]
            return found
        if kw.get("children"):
            kids = list(tree.get(node, []))
            if kw.get("type") == "transform":
                return kids
            return kids + shapes.get(node, [])
        if kw.get("parent"):
            p = node.rsplit("|", 1)[0]
            return [p] if p else []
        return []

    def node_type(n, **kw):
        return types.get(n, "transform")

    def object_type(n, **kw):
        if kw.get("isType"):
            return node_type(n) == kw["isType"]
        return node_type(n)

    def poly_evaluate(shape, **kw):
        return {"face": 12, "triangle": 24, "vertex": 8, "edge": 18}[next(iter(kw))]

    def get_attr(plug, **kw):
        attr = plug.split(".")[-1]
        if attr in ("translate", "rotate"):
            return [(0.0, 0.0, 0.0)] if "cube" not in plug else [(1.0, 2.0, 3.0)]
        if attr == "scale":
            return [(1.0, 1.0, 1.0)]
        if attr == "visibility":
            return "light1" not in plug
        return 0

    fake_maya.responses["ls"] = ls
    fake_maya.responses["listRelatives"] = list_relatives
    fake_maya.responses["nodeType"] = node_type
    fake_maya.responses["objectType"] = object_type
    fake_maya.responses["polyEvaluate"] = poly_evaluate
    fake_maya.responses["getAttr"] = get_attr
    fake_maya.responses["exactWorldBoundingBox"] = [-1.0, -1.0, -1.0, 1.0, 1.0, 1.0]
    fake_maya.responses["listConnections"] = lambda n, **kw: ["mySG"] if kw.get("type") == "shadingEngine" else (["myMat"] if n.endswith(".surfaceShader") else [])
    fake_maya.responses["keyframe"] = lambda n, **kw: 3 if "cube" in n else 0
    fake_maya.existing.update(transforms)
    fake_maya.existing.update(shapes["|grp|cube"] + shapes["|light1"])
    fake_maya.existing.update({"cube", "grp", "light1"})


# unit: screenshot -------------------------------------------------------------
def test_screenshot_offscreen_playblast_returns_png(fake_maya):
    fake_maya.responses["about"] = lambda **kw: False if kw.get("batch") else "2024"
    fake_maya.responses["getPanel"] = lambda **kw: "modelPanel4" if kw.get("withFocus") else ("modelPanel" if kw.get("typeOf") else [])
    fake_maya.responses["currentTime"] = 12.0
    fake_maya.responses["modelPanel"] = "persp"

    def playblast(**kw):
        with open(kw["completeFilename"], "wb") as fh:
            fh.write(_tiny_png(4, 2))
        return kw["completeFilename"]

    fake_maya.responses["playblast"] = playblast
    out = intelligence.viewport_screenshot(width=320, height=200, display_mode="wireframe")
    assert out["format"] == "png" and out["width"] == 4 and out["height"] == 2 and out["camera"] == "persp"
    assert out["image_base64"].startswith("iVBOR") and os.path.exists(out["path"])
    (_, kw), = fake_maya.calls_to("playblast")
    assert kw["offScreen"] is True and kw["widthHeight"] == [320, 200] and kw["viewer"] is False and kw["frame"] == [12.0]
    # display mode was applied and restored
    edits = [k for _, k in fake_maya.calls_to("modelEditor") if k.get("edit")]
    assert edits[0]["displayAppearance"] == "wireframe" and "displayAppearance" in edits[-1]


def test_screenshot_refuses_batch(fake_maya):
    with pytest.raises(BridgeError, match="interactive"):
        intelligence.viewport_screenshot()


def test_screenshot_bad_camera(fake_maya):
    fake_maya.responses["about"] = lambda **kw: False
    fake_maya.existing.add("persp")
    with pytest.raises(BridgeError, match="camera 'nope'"):
        intelligence.viewport_screenshot(camera="nope")


# unit: summary -----------------------------------------------------------------
def test_scene_summary_hierarchy_and_totals(fake_maya):
    _scene(fake_maya)
    out = intelligence.scene_summary(depth=2)
    names = {a["name"]: a for a in out["assemblies"]}
    assert set(names) == {"grp", "light1"}
    cube = names["grp"]["children"][0]
    assert cube["kind"] == "mesh" and cube["faces"] == 12 and cube["materials"] == ["myMat"] and cube["animated"] is True
    assert cube["bbox_size"] == [2.0, 2.0, 2.0]
    assert names["light1"]["kind"] == "light" and names["light1"]["visible"] is False
    assert out["totals"]["meshes"] == 1 and out["totals"]["faces"] == 12 and out["totals"]["lights"] == 1
    assert out["truncated"] is False


def test_scene_summary_budget(fake_maya):
    _scene(fake_maya)
    out = intelligence.scene_summary(max_nodes=1, depth=3)
    assert out["truncated"] is True and len(out["assemblies"]) == 1
    assert out["assemblies"][0]["children"] == [] and out["assemblies"][0]["children_truncated"] == 1


# unit: snapshot + diff --------------------------------------------------------
def test_snapshot_and_diff_detect_moves_adds_removes(fake_maya):
    _scene(fake_maya)
    snap = intelligence.snapshot(label="before")
    assert snap["node_count"] == 3 and snap["snapshot_id"].startswith("snap_")
    # move the cube, drop the light, add a new node
    _scene(fake_maya, transforms=("|grp", "|grp|cube", "|newThing"))
    base = fake_maya.responses["getAttr"]
    fake_maya.responses["getAttr"] = lambda plug, **kw: [(5.0, 2.0, 3.0)] if plug == "|grp|cube.translate" else base(plug, **kw)
    out = intelligence.diff(snap["snapshot_id"])
    assert out["added"] == ["|newThing"] and out["removed"] == ["|light1"]
    assert out["moved"][0]["node"] == "|grp|cube" and out["moved"][0]["t"]["to"] == [5.0, 2.0, 3.0]
    assert out["counts"]["moved"] == 1 and out["counts"]["unchanged"] == 1
    assert intelligence.list_snapshots()[-1]["label"] == "before"


def test_diff_unknown_snapshot(fake_maya):
    with pytest.raises(BridgeError, match="unknown snapshot"):
        intelligence.diff("snap_999")


# unit: problems ----------------------------------------------------------------
def test_find_problems_groups_findings_with_fixes(fake_maya, tmp_path):
    _scene(fake_maya)
    fake_maya.responses["polyInfo"] = lambda m, **kw: ["e[3]", "e[4]"] if kw.get("nonManifoldEdges") else (["FACE 0: 0 1 2 3 4"] if kw.get("faceToVertex") else [])
    base_ls = fake_maya.responses["ls"]
    fake_maya.responses["ls"] = lambda *a, **kw: ["file1"] if kw.get("type") == "file" else base_ls(*a, **kw)
    base_get = fake_maya.responses["getAttr"]
    fake_maya.responses["getAttr"] = lambda plug, **kw: str(tmp_path / "missing.png") if plug.endswith("fileTextureName") else base_get(plug, **kw)
    fake_maya.responses["xform"] = lambda n, **kw: [20000.0, 0.0, 0.0] if "light" in n else [0.0, 0.0, 0.0]
    fake_maya.responses["listHistory"] = lambda n, **kw: ["polyCube1", n]
    fake_maya.responses["nodeType"] = lambda n, **kw: {"polyCube1": "polyCube", "|grp|cube|cubeShape": "mesh", "|light1|light1Shape": "pointLight"}.get(n, "transform")
    out = intelligence.find_problems()
    p = out["problems"]
    assert p["non_manifold"][0]["node"] == "|grp|cube|cubeShape" and "Cleanup" in p["non_manifold"][0]["fix"]
    assert p["ngons"][0]["faces"] == [0]
    assert p["unfrozen_transforms"][0]["node"] == "|grp|cube" and "freeze" in p["unfrozen_transforms"][0]["fix"]
    assert p["missing_textures"][0]["node"] == "file1" and p["missing_textures"][0]["severity"] == "error"
    assert p["far_from_origin"][0]["node"] == "|light1" and p["far_from_origin"][0]["distance"] == 20000.0
    assert p["construction_history"][0]["history"] == ["polyCube1"]
    assert "zero_area" in out["skipped"]  # no OpenMaya in tests
    assert out["total"] == sum(out["counts"].values()) and out["checks_run"] == intelligence.ALL_CHECKS


def test_find_problems_unknown_check(fake_maya):
    with pytest.raises(BridgeError, match="unknown checks"):
        intelligence.find_problems(checks=["nope"])


def test_find_problems_duplicate_and_empty(fake_maya):
    _scene(fake_maya, transforms=("|a|thing", "|b|thing", "|empty"))
    out = intelligence.find_problems(checks=["duplicate_names", "empty_groups"])
    assert out["problems"]["duplicate_names"][0]["nodes"] == ["|a|thing", "|b|thing"]
    assert {f["node"] for f in out["problems"]["empty_groups"]} == {"|a|thing", "|b|thing", "|empty"}


# unit: selection, history, bbox, describe, counts, visibility ------------------
def test_inspect_selection_components(fake_maya):
    _scene(fake_maya)
    comps = ["|grp|cube.f[0]", "|grp|cube.f[1]", "|grp|cube.vtx[3]"]
    base_ls = fake_maya.responses["ls"]

    def ls(*a, **kw):
        if kw.get("selection") and kw.get("objectsOnly"):
            return ["|grp|cube"]
        if kw.get("selection"):
            return comps
        if kw.get("flatten") and a:
            return list(a[0])
        return base_ls(*a, **kw)

    fake_maya.responses["ls"] = ls
    fake_maya.responses["polyListComponentConversion"] = lambda c, **kw: ["|grp|cube.f[0]", "|grp|cube.f[1]"] if kw.get("toFace") else ["x"] * 4
    out = intelligence.inspect_selection()
    assert out["count"] == 3 and out["objects"][0]["kind"] == "mesh" and out["objects"][0]["faces"] == 12
    assert out["components"]["counts"] == {"faces": 2, "vertices": 1} and out["components"]["converted"]["faces"] == 2


def test_inspect_selection_empty(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **kw: []
    assert "nothing selected" in intelligence.inspect_selection()["hint"]


def test_history_stack_and_attrs(fake_maya):
    fake_maya.responses["listHistory"] = ["polyBevel1", "polyCube1", "cubeShape"]
    fake_maya.responses["nodeType"] = lambda n, **kw: {"polyBevel1": "polyBevel3", "polyCube1": "polyCube"}.get(n, "mesh")
    fake_maya.responses["listAttr"] = lambda n, **kw: ["width", "height"] if n == "polyCube1" else ["offset"]
    fake_maya.responses["getAttr"] = lambda plug, **kw: 2.5
    out = intelligence.get_history_stack("cubeShape")
    assert [h["type"] for h in out["history"]] == ["polyCube", "polyBevel3"]
    assert out["history"][0]["attrs"] == {"width": 2.5, "height": 2.5} and "delete_history" in out["fix"]


def test_bounding_box(fake_maya):
    fake_maya.responses["exactWorldBoundingBox"] = [0.0, 0.0, 0.0, 2.0, 4.0, 6.0]
    out = intelligence.get_bounding_box(["pCube1"])
    assert out["size"] == [2.0, 4.0, 6.0] and out["center"] == [1.0, 2.0, 3.0] and "pCube1" in out["per_node"]
    fake_maya.responses["exactWorldBoundingBox"] = None
    with pytest.raises(BridgeError, match="no bounding box"):
        intelligence.get_bounding_box(["pCube1"])


def test_describe_for_llm_paragraph(fake_maya):
    _scene(fake_maya)
    out = intelligence.describe_for_llm("|grp|cube")
    text = out["description"]
    assert text.startswith("cube is a mesh, parented under grp.")
    assert "12 faces (24 triangles, 8 vertices), shaded with myMat" in text and "animated" in text
    assert out["facts"]["translate"] == [1.0, 2.0, 3.0] and out["facts"]["bbox"]["size"] == [2.0, 2.0, 2.0]


def test_count_polys(fake_maya):
    _scene(fake_maya)
    out = intelligence.count_polys()
    assert out["totals"] == {"faces": 12, "triangles": 24, "vertices": 8, "edges": 18} and out["heaviest"][0]["transform"] == "|grp|cube"


def test_visibility_report(fake_maya):
    _scene(fake_maya)
    fake_maya.responses["attributeQuery"] = False
    out = intelligence.visibility_report()
    assert out["hidden"] == ["|light1"] and out["counts"]["hidden"] == 1 and out["hidden_by_parent"] == []


# integration ------------------------------------------------------------------
async def test_tool_scene_summary(call_tool, fake_maya):
    _scene(fake_maya)
    data = parse(await call_tool("maya_scene_summary", {"params": {"depth": 1}}))
    assert data["assembly_count"] == 2 and data["totals"]["faces"] == 12


async def test_tool_snapshot_then_diff(call_tool, fake_maya):
    _scene(fake_maya)
    snap = parse(await call_tool("maya_scene_snapshot", {"params": {"label": "t"}}))
    data = parse(await call_tool("maya_scene_diff", {"params": {"snapshot_id": snap["snapshot_id"]}}))
    assert data["counts"] == {"added": 0, "removed": 0, "moved": 0, "changed": 0, "unchanged": 3}


async def test_tool_find_problems_error_path(call_tool, fake_maya):
    text = await call_tool("maya_find_problems", {"params": {"checks": ["bogus"]}})
    assert text.startswith("Error") and "unknown checks" in text


async def test_tool_screenshot_returns_image(call_tool, fake_maya):
    fake_maya.responses["about"] = lambda **kw: False if kw.get("batch") else "2024"
    fake_maya.responses["getPanel"] = lambda **kw: "modelPanel4" if kw.get("withFocus") else ("modelPanel" if kw.get("typeOf") else [])
    fake_maya.responses["modelPanel"] = "persp"

    def playblast(**kw):
        with open(kw["completeFilename"], "wb") as fh:
            fh.write(_tiny_png())
        return kw["completeFilename"]

    fake_maya.responses["playblast"] = playblast
    text = await call_tool("maya_viewport_screenshot", {"params": {"width": 640, "height": 360}})
    assert "<image" in text and '"camera": "persp"' in text


async def test_tool_describe_node(call_tool, fake_maya):
    _scene(fake_maya)
    data = parse(await call_tool("maya_describe_node", {"params": {"node": "|grp|cube"}}))
    assert data["facts"]["kind"] == "mesh"
