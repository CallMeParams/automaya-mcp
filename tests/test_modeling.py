"""Unit + integration tests for the modeling domain."""
from __future__ import annotations

import math

import pytest
from maya import mel
from tests.conftest import parse

from automaya_bridge.handlers import modeling
from automaya_bridge.handlers._util import BridgeError


def _mesh(fake_maya, names=("pCube1",), selection=None):
    """Make ``names`` look like poly transforms: long names, a mesh shape each, and xform readback."""
    mapping = {n: "|" + n for n in names}
    selection = selection if selection is not None else []

    def ls(*args, **kwargs):
        if kwargs.get("selection"):
            return list(selection)
        if args:
            name = args[0]
            if isinstance(name, (list, tuple)):
                return [mapping.get(n, n) for n in name]
            return [mapping.get(name, name)]
        return list(mapping.values())

    def list_relatives(node, **kwargs):
        if kwargs.get("shapes"):
            if kwargs.get("type") in (None, "mesh"):
                return ["%s|%sShape" % (mapping.get(node, node), node.strip("|"))]
            return []
        return []

    def xform(node, **kwargs):
        if kwargs.get("query"):
            if kwargs.get("translation"):
                return [1.0, 2.0, 3.0]
            if kwargs.get("rotation"):
                return [0.0, 90.0, 0.0]
            if kwargs.get("scale"):
                return [1.0, 1.0, 1.0]
            if kwargs.get("rotatePivot"):
                return [0.5, 0.5, 0.5]
        return None

    fake_maya.responses["ls"] = ls
    fake_maya.responses["listRelatives"] = list_relatives
    fake_maya.responses["xform"] = xform


# unit: primitives -----------------------------------------------------------
def test_create_cube_flags_and_placement(fake_maya):
    fake_maya.responses["polyCube"] = ["pCube1", "polyCube1"]
    _mesh(fake_maya)
    out = modeling.create_primitive("cube", name="box", width=2, height=3, depth=4, subdivisions=2, translate=[1, 2, 3], rotate=[0, 90, 0])
    assert out["transform"] == "|pCube1" and out["shape"] == "|pCube1|pCube1Shape" and out["history"] == ["polyCube1"]
    (_, kw), = fake_maya.calls_to("polyCube")
    assert kw["width"] == 2 and kw["height"] == 3 and kw["depth"] == 4 and kw["subdivisionsX"] == 2 and kw["name"] == "box" and kw["constructionHistory"] is True
    placed = [(a, k) for a, k in fake_maya.calls_to("xform") if not k.get("query")]
    assert placed[0][1] == {"worldSpace": True, "translation": [1.0, 2.0, 3.0]}
    assert placed[1][1] == {"worldSpace": True, "rotation": [0.0, 90.0, 0.0]}
    assert out["node_summary"]["type"] == "transform" and out["kind"] == "cube"


@pytest.mark.parametrize(
    "kind, cmd, expect",
    [
        ("sphere", "polySphere", {"radius": 2.0, "subdivisionsX": 12}),
        ("cylinder", "polyCylinder", {"radius": 2.0, "height": 5.0, "subdivisionsX": 12}),
        ("cone", "polyCone", {"radius": 2.0, "height": 5.0}),
        ("plane", "polyPlane", {"width": 1.0, "height": 5.0, "subdivisionsX": 12}),
        ("torus", "polyTorus", {"radius": 2.0, "sectionRadius": 1.0}),
        ("pipe", "polyPipe", {"radius": 2.0, "height": 5.0, "thickness": 1.0}),
        ("disc", "polyDisc", {"sides": 12, "radius": 2.0}),  # plugin primitive: no name/ch flags, returns nothing
        ("prism", "polyPrism", {"length": 5.0, "numberOfSides": 12}),
        ("pyramid", "polyPyramid", {"numberOfSides": 12}),
        ("helix", "polyHelix", {"width": 2.0, "height": 5.0}),
        ("platonic", "polyPlatonic", {"primitive": 12, "radius": 2.0}),
    ],
)
def test_create_primitive_kinds(fake_maya, kind, cmd, expect):
    selection_only = cmd in ("polyDisc", "polyPlatonic")
    if selection_only:
        # Real Maya: polyDisc/polyPlatonic return None and leave the new transform selected.
        fake_maya.responses[cmd] = None
        _mesh(fake_maya, ("node1",), selection=["|node1"])
    else:
        fake_maya.responses[cmd] = ["node1", cmd + "1"]
        _mesh(fake_maya, ("node1",))
    out = modeling.create_primitive(kind, radius=2, height=5, subdivisions=12)
    assert out["kind"] == kind and out["transform"] == "|node1"
    (_, kw), = fake_maya.calls_to(cmd)
    for key, value in expect.items():
        assert kw[key] == value, (kind, key, kw)
    if selection_only:
        assert "name" not in kw and "constructionHistory" not in kw


def test_create_primitive_size_fallback_and_bad_kind(fake_maya):
    fake_maya.responses["polySphere"] = ["pSphere1", "polySphere1"]
    _mesh(fake_maya, ("pSphere1",))
    modeling.create_primitive("sphere", size=4)
    assert fake_maya.calls_to("polySphere")[0][1]["radius"] == 2.0
    with pytest.raises(BridgeError):
        modeling.create_primitive("blob")


def test_create_primitive_empty_result(fake_maya):
    fake_maya.responses["polyCube"] = []
    with pytest.raises(BridgeError):
        modeling.create_primitive("cube")


def test_create_curve(fake_maya):
    fake_maya.responses["curve"] = "curve1"
    _mesh(fake_maya, ("curve1",))
    pts = [[0, 0, 0], [1, 1, 0], [2, 0, 0], [3, 1, 0]]
    out = modeling.create_curve(pts, degree=3, closed=True, name="path")
    assert out["transform"] == "|curve1" and out["closed"] is True and out["points"] == 4
    (_, kw), = fake_maya.calls_to("curve")
    assert kw["degree"] == 3 and kw["name"] == "path" and kw["point"][1] == [1.0, 1.0, 0.0]
    assert fake_maya.calls_to("closeCurve")[0][1]["replaceOriginal"] is True
    with pytest.raises(BridgeError):
        modeling.create_curve([[0, 0, 0], [1, 1, 1]], degree=3)
    with pytest.raises(BridgeError):
        modeling.create_curve([[0, 0], [1, 1]], degree=1)


def test_create_text(fake_maya):
    fake_maya.responses["textCurves"] = ["Text_hello", "makeTextCurves1"]
    _mesh(fake_maya, ("Text_hello",))
    out = modeling.create_text("hello", font="Courier")
    assert out["text"] == "hello" and out["transform"] == "|Text_hello"
    assert fake_maya.calls_to("textCurves")[0][1] == {"text": "hello", "font": "Courier", "constructionHistory": False}
    with pytest.raises(BridgeError):
        modeling.create_text("   ")


# unit: transforms -----------------------------------------------------------
def test_transform_absolute_and_relative(fake_maya):
    _mesh(fake_maya)
    out = modeling.transform(["pCube1"], translate=[1, 2, 3], rotate=[0, 90, 0], scale=[2, 2, 2])
    entry = out["nodes"][0]
    assert entry["node"] == "|pCube1" and entry["translate"] == [1.0, 2.0, 3.0] and entry["rotate"] == [0.0, 90.0, 0.0]
    sets = [k for _, k in fake_maya.calls_to("xform") if not k.get("query")]
    assert sets[0] == {"relative": False, "translation": [1.0, 2.0, 3.0], "worldSpace": True}
    assert sets[2] == {"relative": False, "scale": [2.0, 2.0, 2.0]}
    fake_maya.calls.clear()
    modeling.transform(["pCube1"], translate=[0, 1, 0], relative=True, world=False)
    sets = [k for _, k in fake_maya.calls_to("xform") if not k.get("query")]
    assert sets[0] == {"relative": True, "translation": [0.0, 1.0, 0.0], "objectSpace": True}
    with pytest.raises(BridgeError):
        modeling.transform(["pCube1"])


def test_duplicate_with_offsets_and_instances(fake_maya):
    counter = {"n": 0}

    def dup(src, **kw):
        counter["n"] += 1
        return ["copy%d" % counter["n"]]

    fake_maya.responses["duplicate"] = dup
    fake_maya.responses["instance"] = dup
    _mesh(fake_maya, ("pCube1", "copy1", "copy2", "copy3", "copy4"))
    out = modeling.duplicate(["pCube1"], count=3, offset_translate=[2, 0, 0], name="row")
    assert out["count"] == 3 and [c["node"] for c in out["copies"]] == ["|copy1", "|copy2", "|copy3"]
    assert [a[0] for a, _ in fake_maya.calls_to("duplicate")] == ["pCube1", "|copy1", "|copy2"]
    assert fake_maya.calls_to("duplicate")[0][1] == {"returnRootsOnly": True, "name": "row1"}
    offsets = [k for _, k in fake_maya.calls_to("xform") if k.get("relative") and not k.get("query")]
    assert len(offsets) == 3 and offsets[0]["translation"] == [2.0, 0.0, 0.0]
    modeling.duplicate(["pCube1"], instance=True)
    assert fake_maya.calls_to("instance") and out["copies"][0]["instance"] is False
    with pytest.raises(BridgeError):
        modeling.duplicate(["pCube1"], count=0)


# unit: poly editing ---------------------------------------------------------
def test_extrude_faces_edges_vertices(fake_maya):
    fake_maya.responses["polyExtrudeFacet"] = ["polyExtrudeFace1"]
    fake_maya.responses["polyExtrudeEdge"] = ["polyExtrudeEdge1"]
    fake_maya.responses["polyExtrudeVertex"] = ["polyExtrudeVertex1"]
    _mesh(fake_maya)
    out = modeling.extrude("pCube1", ["f[0:3]", "f[5]"], distance=2, thickness=0.5, divisions=3, keep_faces_together=False)
    assert out["node"] == "|pCube1" and out["history"] == ["polyExtrudeFace1"] and out["kind"] == "face"
    args, kw = fake_maya.calls_to("polyExtrudeFacet")[0]
    assert args == ("pCube1.f[0:3]", "pCube1.f[5]")
    assert kw == {"constructionHistory": True, "keepFacesTogether": False, "divisions": 3, "localTranslateZ": 2.0, "thickness": 0.5}
    out = modeling.extrude("pCube1", ["e[0]"], thickness=1)
    assert out["kind"] == "edge" and "thickness" not in fake_maya.calls_to("polyExtrudeEdge")[0][1]
    out = modeling.extrude("pCube1", ["vtx[0]"], distance=0.3)
    assert out["kind"] == "vertex" and fake_maya.calls_to("polyExtrudeVertex")[0][1]["length"] == 0.3
    modeling.extrude("pCube1")
    assert fake_maya.calls_to("polyExtrudeFacet")[-1][0] == ("pCube1",)


def test_bevel(fake_maya):
    fake_maya.responses["polyBevel3"] = ["polyBevel1"]
    _mesh(fake_maya)
    out = modeling.bevel("pCube1", edges=["e[0:11]"], fraction=0.2, segments=3, chamfer=False)
    assert out["history"] == ["polyBevel1"] and out["components"] == ["pCube1.e[0:11]"]
    args, kw = fake_maya.calls_to("polyBevel3")[0]
    assert args == ("pCube1.e[0:11]",) and kw["fraction"] == 0.2 and kw["offsetAsFraction"] == 1 and kw["segments"] == 3 and kw["chamfer"] is False
    with pytest.raises(BridgeError):
        modeling.bevel("pCube1", fraction=1.5)


def test_boolean(fake_maya):
    fake_maya.responses["polyBoolOp"] = ["polySurface1", "polyBoolOp1"]
    _mesh(fake_maya, ("a", "b", "polySurface1"))
    out = modeling.boolean("a", "b", "difference", name="cut")
    assert out["transform"] == "|polySurface1" and out["operation"] == "difference"
    args, kw = fake_maya.calls_to("polyBoolOp")[0]
    assert args == ("a", "b") and kw["operation"] == 2 and kw["name"] == "cut"
    modeling.boolean("a", "b", "intersection")
    assert fake_maya.calls_to("polyBoolOp")[-1][1]["operation"] == 3
    with pytest.raises(BridgeError):
        modeling.boolean("a", "b", "xor")


def test_combine_and_separate(fake_maya):
    fake_maya.responses["polyUnite"] = ["polySurface1", "polyUnite1"]
    _mesh(fake_maya, ("a", "b", "polySurface1"))
    out = modeling.combine(["a", "b"], name="merged")
    assert out["transform"] == "|polySurface1" and out["sources"] == ["|a", "|b"]
    assert fake_maya.calls_to("polyUnite")[0] == (("a", "b"), {"constructionHistory": True, "mergeUVSets": 1, "name": "merged"})
    with pytest.raises(BridgeError):
        modeling.combine(["a"])
    fake_maya.responses["polySeparate"] = ["piece1", "piece2", "polySeparate1"]
    fake_maya.responses["objectType"] = lambda n, **k: not n.startswith("polySeparate")
    _mesh(fake_maya, ("polySurface1", "piece1", "piece2"))
    out = modeling.separate("polySurface1")
    assert out["pieces"] == ["|piece1", "|piece2"] and out["history"] == ["polySeparate1"] and out["count"] == 2


def test_mirror_smooth_reduce(fake_maya):
    fake_maya.responses["polyMirrorFace"] = ["polyMirror1"]
    fake_maya.responses["polySmooth"] = ["polySmoothFace1"]
    fake_maya.responses["polyReduce"] = ["polyReduce1"]
    _mesh(fake_maya)
    out = modeling.mirror("pCube1", axis="z", direction="-", merge=False)
    assert out["axis"] == "z" and out["direction"] == "-"
    kw = fake_maya.calls_to("polyMirrorFace")[0][1]
    assert kw["axis"] == 2 and kw["axisDirection"] == 0 and kw["mergeMode"] == 0
    with pytest.raises(BridgeError):
        modeling.mirror("pCube1", axis="w")
    out = modeling.smooth("pCube1", divisions=2)
    assert out["divisions"] == 2 and fake_maya.calls_to("polySmooth")[0][1]["divisions"] == 2
    with pytest.raises(BridgeError):
        modeling.smooth("pCube1", divisions=9)
    out = modeling.reduce("pCube1", percentage=30)
    kw = fake_maya.calls_to("polyReduce")[0][1]
    assert kw["version"] == 1 and kw["percentage"] == 30.0 and out["history"] == ["polyReduce1"]
    with pytest.raises(BridgeError):
        modeling.reduce("pCube1", percentage=100)


def test_requires_mesh_shape(fake_maya):
    fake_maya.responses["listRelatives"] = []
    with pytest.raises(BridgeError) as exc:
        modeling.smooth("curve1")
    assert "mesh" in str(exc.value)


def test_freeze_center_history(fake_maya):
    _mesh(fake_maya, ("pCube1",), selection=["|pCube1"])
    out = modeling.freeze_transforms(scale=False)
    assert out["nodes"][0]["node"] == "|pCube1"
    args, kw = fake_maya.calls_to("makeIdentity")[0]
    assert args == (["|pCube1"],) and kw["apply"] is True and kw["translate"] is True and kw["scale"] is False
    out = modeling.center_pivot(["pCube1"])
    assert out["nodes"][0]["pivot"] == [0.5, 0.5, 0.5]
    assert ("pCube1",) == fake_maya.calls_to("xform")[-2][0] and fake_maya.calls_to("xform")[-2][1] == {"centerPivots": True}
    out = modeling.delete_history(["pCube1"])
    assert out["nodes"] == ["|pCube1"] and fake_maya.calls_to("delete")[0] == ((["pCube1"],), {"constructionHistory": True})


def test_mesh_stats(fake_maya):
    _mesh(fake_maya)

    def evaluate(shape, **kw):
        if kw.get("boundingBox"):
            return [(-1.0, 1.0), (0.0, 2.0), (-0.5, 0.5)]
        return {"vertex": 8, "edge": 12, "face": 6, "triangle": 12, "uvcoord": 14, "shell": 1}[next(iter(kw))]

    fake_maya.responses["polyEvaluate"] = evaluate
    out = modeling.mesh_stats("pCube1")
    assert out["vertices"] == 8 and out["faces"] == 6 and out["triangles"] == 12 and out["shells"] == 1
    assert out["bounding_box"] == {"min": [-1.0, 0.0, -0.5], "max": [1.0, 2.0, 0.5], "size": [2.0, 2.0, 1.0]}
    assert out["shape"] == "|pCube1|pCube1Shape"


def test_uv_auto_methods(fake_maya):
    fake_maya.responses["polyAutoProjection"] = ["polyAutoProj1"]
    fake_maya.responses["polyProjection"] = ["polyPlanarProj1"]
    _mesh(fake_maya)
    out = modeling.uv_auto("pCube1")
    assert out["method"] == "automatic" and fake_maya.calls_to("polyAutoProjection")[0][0] == ("pCube1.f[*]",)
    modeling.uv_auto("pCube1", "planar", axis="z")
    kw = fake_maya.calls_to("polyProjection")[-1][1]
    assert kw["type"] == "Planar" and kw["mapDirection"] == "z"
    modeling.uv_auto("pCube1", "spherical")
    assert fake_maya.calls_to("polyProjection")[-1][1]["type"] == "Spherical"
    with pytest.raises(BridgeError):
        modeling.uv_auto("pCube1", "cubic")


def test_lattice(fake_maya):
    fake_maya.responses["lattice"] = ["ffd1", "ffd1Lattice", "ffd1Base"]
    _mesh(fake_maya, ("pCube1", "ffd1Lattice", "ffd1Base"))
    out = modeling.lattice(["pCube1"], divisions=[3, 4, 3])
    assert out == {"ffd": "ffd1", "lattice": "|ffd1Lattice", "base": "|ffd1Base", "nodes": ["|pCube1"], "divisions": [3, 4, 3]}
    assert fake_maya.calls_to("lattice")[0][1]["divisions"] == (3, 4, 3)
    with pytest.raises(BridgeError):
        modeling.lattice(["pCube1"], divisions=[1, 2, 2])


def test_nurbs_revolve_loft(fake_maya):
    fake_maya.responses["revolve"] = ["revolvedSurface1", "revolve1"]
    fake_maya.responses["loft"] = ["loftedSurface1", "loft1"]
    fake_maya.responses["nurbsToPoly"] = ["nurbsToPoly1", "nurbsTessellate1"]
    _mesh(fake_maya, ("curve1", "curve2", "revolvedSurface1", "loftedSurface1", "nurbsToPoly1"))
    out = modeling.revolve("curve1", axis="z", degrees=180, sections=16, output_poly=True)
    kw = fake_maya.calls_to("revolve")[0][1]
    assert out["transform"] == "|revolvedSurface1" and kw["axis"] == [0.0, 0.0, 1.0] and kw["endSweep"] == 180.0 and kw["polygon"] == 1
    out = modeling.loft(["curve1", "curve2"], close=True)
    args, kw = fake_maya.calls_to("loft")[0]
    assert args == ("curve1", "curve2") and kw["close"] is True and out["sources"] == ["|curve1", "|curve2"]
    with pytest.raises(BridgeError):
        modeling.loft(["curve1"])
    with pytest.raises(BridgeError):
        modeling.nurbs_to_poly("revolvedSurface1")  # the stub reports a mesh shape, not a nurbsSurface
    fake_maya.responses["listRelatives"] = lambda n, **k: ["|revolvedSurface1|revolvedSurfaceShape1"] if k.get("shapes") else []
    out = modeling.nurbs_to_poly("revolvedSurface1", quads=False, spans_u=2)
    kw = fake_maya.calls_to("nurbsToPoly")[0][1]
    assert out["transform"] == "|nurbsToPoly1" and kw["polygonType"] == 0 and kw["uNumber"] == 2 and kw["format"] == 2


def test_cleanup_mel_args(fake_maya):
    _mesh(fake_maya, ("pCube1",))
    fake_maya.responses["ls"] = lambda *a, **k: ["|pCube1.f[3]", "|pCube1.f[7]"] if k.get("selection") else ["|pCube1"]
    out = modeling.cleanup(["pCube1"], select_only=True, zero_length=True)
    assert out["problem_count"] == 2 and out["problem_components"] == ["|pCube1.f[3]", "|pCube1.f[7]"]
    script = mel.evaluated[-1]
    assert script.startswith('polyCleanupArgList 4 {') and script.count('"') == 36
    values = [v.strip().strip('"') for v in script[script.index("{") + 1 : script.index("}")].split(",")]
    assert values[1] == "1" and values[8] == "1" and values[10] == "1" and values[15] == "1" and values[16] == "1"
    assert fake_maya.calls_to("select")[-1] == ((["pCube1"],), {"replace": True})


def test_set_smooth_preview(fake_maya):
    _mesh(fake_maya, ("a", "b"))
    out = modeling.set_smooth_preview(["a", "b"], level=2)
    assert out["shapes"] == ["|a|aShape", "|b|bShape"]
    sets = [a for a, _ in fake_maya.calls_to("setAttr")]
    assert ("|a|aShape.displaySmoothMesh", 2) in sets and ("|a|aShape.smoothLevel", 2) in sets
    modeling.set_smooth_preview(["a"], level=0)
    assert fake_maya.calls_to("setAttr")[-1][0] == ("|a|aShape.displaySmoothMesh", 0)
    with pytest.raises(BridgeError):
        modeling.set_smooth_preview(["a"], level=4)


def test_array_linear_and_circular(fake_maya):
    counter = {"n": 0}

    def dup(src, **kw):
        counter["n"] += 1
        return ["c%d" % counter["n"]]

    fake_maya.responses["duplicate"] = dup
    fake_maya.responses["instance"] = dup
    fake_maya.responses["exactWorldBoundingBox"] = [0.0, 0.0, 0.0, 2.0, 1.0, 1.0]
    _mesh(fake_maya, ("pCube1", "c1", "c2", "c3", "c4", "c5"))
    fake_maya.responses["xform"] = lambda n, **k: ([1.0, 2.0, 3.0] if k.get("translation") else [0.0, 0.0, 0.0]) if k.get("query") else None
    out = modeling.array("pCube1", count=3, axis="x")
    assert out["layout"] == "linear" and out["spacing"] == 3.0
    assert [n["translate"] for n in out["nodes"]] == [[1.0, 2.0, 3.0], [4.0, 2.0, 3.0], [7.0, 2.0, 3.0]]
    assert out["nodes"][0]["node"] == "|pCube1" and out["nodes"][1]["node"] == "|c1"
    out = modeling.array("pCube1", count=4, axis="y", radius=10, instance=True, name="post")
    assert out["layout"] == "circular" and len(out["nodes"]) == 4
    p = out["nodes"][1]["translate"]
    assert math.isclose(p[0], 1.0 + 10 * math.cos(math.pi / 2), abs_tol=1e-9) and math.isclose(p[2], 3.0 + 10.0) and p[1] == 2.0
    assert out["nodes"][1]["rotate"][1] == -90.0
    assert fake_maya.calls_to("instance")[0][1] == {"name": "post1"}
    with pytest.raises(BridgeError):
        modeling.array("pCube1", count=1)


# integration: through the MCP tools over the real socket --------------------
async def test_tool_create_primitive(call_tool, fake_maya):
    fake_maya.responses["polyCylinder"] = ["pCylinder1", "polyCylinder1"]
    _mesh(fake_maya, ("pCylinder1",))
    data = parse(await call_tool("maya_create_primitive", {"params": {"kind": "cylinder", "radius": 0.5, "height": 3, "translate": [0, 1.5, 0]}}))
    assert data["transform"] == "|pCylinder1" and data["shape"].endswith("Shape") and data["node_summary"]["type"] == "transform"
    assert fake_maya.calls_to("polyCylinder")[0][1]["height"] == 3.0


async def test_tool_transform_returns_values(call_tool, fake_maya):
    _mesh(fake_maya)
    data = parse(await call_tool("maya_transform", {"params": {"nodes": ["pCube1"], "translate": [1, 2, 3], "relative": True}}))
    assert data["nodes"][0]["translate"] == [1.0, 2.0, 3.0]


async def test_tool_extrude_and_stats(call_tool, fake_maya):
    fake_maya.responses["polyExtrudeFacet"] = ["polyExtrudeFace1"]
    fake_maya.responses["polyEvaluate"] = lambda s, **k: [(0, 1), (0, 1), (0, 1)] if k.get("boundingBox") else 6
    _mesh(fake_maya)
    data = parse(await call_tool("maya_extrude", {"params": {"node": "pCube1", "components": ["f[1]"], "distance": 0.5}}))
    assert data["history"] == ["polyExtrudeFace1"]
    data = parse(await call_tool("maya_mesh_stats", {"params": {"node": "pCube1"}}))
    assert data["faces"] == 6 and data["bounding_box"]["size"] == [1.0, 1.0, 1.0]


async def test_tool_array(call_tool, fake_maya):
    fake_maya.responses["duplicate"] = lambda s, **k: ["dup"]
    _mesh(fake_maya, ("pCube1", "dup"))
    data = parse(await call_tool("maya_array", {"params": {"node": "pCube1", "count": 3, "spacing": 2, "axis": "z"}}))
    assert data["layout"] == "linear" and [n["translate"][2] for n in data["nodes"]] == [3.0, 5.0, 7.0]


async def test_tool_error_missing_node(call_tool, fake_maya):
    fake_maya.existing = {"real"}
    text = await call_tool("maya_bevel", {"params": {"node": "ghost"}})
    assert text.startswith("Error") and "not found" in text


async def test_tool_validation_rejects_bad_input(call_tool):
    text = await call_tool("maya_create_primitive", {"params": {"kind": "cube", "translate": [1, 2]}})
    assert "Error" in text or "translate" in text
    text = await call_tool("maya_smooth", {"params": {"node": "pCube1", "divisions": 9}})
    assert "Error" in text or "divisions" in text
