"""Unit + integration tests for the photo domain (photo.* handlers, maya_photo_* tools)."""
from __future__ import annotations

import math

import pytest
from tests.conftest import parse
from tests.synthetic_images import gradient, horizon, save

from automaya_bridge import registry
from automaya_bridge.handlers import photo
from automaya_bridge.handlers._util import BridgeError
from automaya_mcp.tools import craft_photo


def _camera_stub(fake_maya, name="photoCam"):
    fake_maya.responses["camera"] = lambda **k: [k["name"], k["name"] + "Shape"]
    fake_maya.responses["imagePlane"] = ["imagePlane1", "imagePlaneShape1"]
    fake_maya.responses["group"] = lambda *a, **k: k["name"]
    fake_maya.responses["ls"] = lambda *a, **k: ([a[0] if a[0].startswith("|") else "|" + a[0]] if a and isinstance(a[0], str) and k.get("long") else [])


# unit: camera_from_photo ---------------------------------------------------------------
def test_camera_from_photo_matches_aspect_and_focal(fake_maya, tmp_path):
    _camera_stub(fake_maya)
    plate = save(horizon(300, 200), tmp_path, "plate.jpg")
    out = photo.camera_from_photo(plate, 300, 200, name="matchCam", focal_length=24.0, sensor_width=36.0, depth=250)
    (_, kw), = fake_maya.calls_to("camera")
    assert kw["focalLength"] == 24.0 and kw["filmFit"] == "horizontal"
    assert abs(kw["horizontalFilmAperture"] * 25.4 - 36.0) < 1e-6
    assert abs(kw["verticalFilmAperture"] * 25.4 - 24.0) < 1e-6  # 36 / 1.5
    (_, ipkw), = fake_maya.calls_to("imagePlane")
    assert ipkw["camera"] == "matchCamShape" and ipkw["fileName"].endswith("plate.jpg")
    assert ("setAttr", ("imagePlaneShape1.fit", 2), {}) in fake_maya.calls
    assert ("setAttr", ("imagePlaneShape1.depth", 250.0), {}) in fake_maya.calls
    assert ("setAttr", ("matchCam_match_grp.translate",), {"lock": True}) in fake_maya.calls
    assert ("setAttr", ("defaultResolution.width", 300), {}) in fake_maya.calls
    assert out["aspect"] == 1.5 and out["focal_source"] == "given" and out["group"] == "|matchCam_match_grp"
    assert abs(out["horizontal_fov_deg"] - math.degrees(2 * math.atan(36 / 48))) < 0.01


def test_camera_from_photo_defaults_and_errors(fake_maya, tmp_path):
    _camera_stub(fake_maya)
    plate = save(gradient(), tmp_path, "p.png")
    out = photo.camera_from_photo(plate, 200, 100, group=False)
    assert out["focal_length"] == 35.0 and out["focal_source"] == "default_35mm" and out["group"] is None
    assert not fake_maya.calls_to("group")
    with pytest.raises(BridgeError, match="not found"):
        photo.camera_from_photo(str(tmp_path / "missing.jpg"), 10, 10)
    with pytest.raises(BridgeError, match="image_height must be positive"):
        photo.camera_from_photo(plate, 200, 0)


# unit: block_from_photo -----------------------------------------------------------------
def test_block_from_photo_polycube_fallback(fake_maya):
    fake_maya.responses["polyCube"] = lambda **k: [k["name"], "polyCube1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|" + a[0]] if a and k.get("long") else []
    fake_maya.responses["exactWorldBoundingBox"] = [-600, 0, -450, 600, 960, 450]
    saved = registry._REGISTRY.pop("procgen.building", None)
    try:
        out = photo.block_from_photo(1200, 900, floors=3, name="diner")
    finally:
        if saved is not None:
            registry._REGISTRY["procgen.building"] = saved
    (_, kw), = fake_maya.calls_to("polyCube")
    assert kw == {"name": "diner", "width": 1200.0, "height": 960.0, "depth": 900.0}
    assert out["via"] == "polyCube" and out["dims_cm"] == [1200.0, 960.0, 900.0] and out["floors"] == 3
    assert out["bbox"]["size"] == [1200, 960, 900]
    # pivot at the base: the cube was lifted by half its height
    moves = [k for _, k in fake_maya.calls_to("xform") if k.get("translation")]
    assert moves[0]["translation"] == [0.0, 480.0, 0.0]


def test_block_from_photo_uses_procgen_and_camera(fake_maya):
    calls = {}

    def fake_building(width=0, depth=0, floors=1, floor_height=320.0, style="flat", name="b", extra=None):
        calls.update(width=width, depth=depth, floors=floors, floor_height=floor_height, style=style, name=name)
        return {"node": "|" + name, "faces": 10}

    saved = registry._REGISTRY.get("procgen.building")
    registry._REGISTRY.pop("procgen.building", None)
    registry.command("procgen.building", mutates=True)(fake_building)
    fake_maya.existing.add("photoCam")
    fake_maya.responses["xform"] = lambda n, **k: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 160, 500, 1] if k.get("query") else None
    fake_maya.responses["ls"] = lambda *a, **k: [a[0]] if a and k.get("long") else []
    try:
        out = photo.block_from_photo(1000, 800, height=640, style="brick", name="shop", camera="photoCam", distance=1500)
    finally:
        registry._REGISTRY.pop("procgen.building", None)
        if saved is not None:
            registry._REGISTRY["procgen.building"] = saved
    assert calls == {"width": 1000.0, "depth": 800.0, "floors": 2, "floor_height": 320.0, "style": "brick", "name": "shop"}
    assert out["via"] == "procgen.building" and out["node"] == "|shop"
    assert out["placed_at"] == [0.0, 0.0, -1000.0]  # 500 forward 1500 along -Z, dropped to the ground
    assert not fake_maya.calls_to("polyCube")


def test_block_from_photo_needs_height_or_floors(fake_maya):
    with pytest.raises(BridgeError, match="height"):
        photo.block_from_photo(100, 100)


# unit: depth_relief ----------------------------------------------------------------------
def test_depth_relief_displaces_vertices(fake_maya):
    fake_maya.responses["polyPlane"] = lambda **k: [k["name"], "polyPlane1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|" + a[0]] if a and k.get("long") else []
    rows = [[0.0, 0.5, 1.0], [0.25, 0.0, 0.75]]  # top row is the far edge
    out = photo.depth_relief(rows, width=300, height=40, name="relief")
    (_, kw), = fake_maya.calls_to("polyPlane")
    assert kw["subdivisionsX"] == 2 and kw["subdivisionsY"] == 1 and kw["width"] == 300.0 and kw["height"] == 200.0
    moves = {a[0]: k["translation"][1] for a, k in fake_maya.calls_to("xform") if a and ".vtx[" in a[0]}
    # image row 0 lands on vertex row 1 (far, -Z); row 1 on vertex row 0 (near)
    assert moves == {"relief.vtx[4]": 20.0, "relief.vtx[5]": 40.0, "relief.vtx[0]": 10.0, "relief.vtx[2]": 30.0}
    assert out["displaced"] == 4 and out["vertices"] == 6 and out["samples"] == [3, 2] and out["height_range"] == [0.0, 1.0]


def test_depth_relief_rejects_bad_grids(fake_maya):
    with pytest.raises(BridgeError, match="non empty"):
        photo.depth_relief([])
    with pytest.raises(BridgeError, match="same number"):
        photo.depth_relief([[0, 1], [0]])
    with pytest.raises(BridgeError, match="128"):
        photo.depth_relief([[0.0] * 200, [0.0] * 200])


# server side helpers ---------------------------------------------------------------------
def test_inspect_photo_reads_the_picture(tmp_path):
    path = save(horizon(320, 180), tmp_path, "street.png")
    info = craft_photo.inspect_photo(path, 3)
    assert info["image"]["aspect"] == 1.7778 and info["image"]["orientation"] == "landscape"
    assert info["horizon"]["confident"] and abs(info["horizon"]["row_fraction"] - 0.667) < 0.03
    assert info["time_of_day"]["label"] == "blue hour or overcast" and info["time_of_day"]["kelvin_hint"] == 7500
    assert info["suggested_camera"]["focal_length"] == 35.0 and "guess" in info["suggested_camera"]["note"]
    assert info["vanishing"]["horizontal_share"] > 0.9  # one strong horizontal line, no verticals
    assert len(info["dominant_colours"]) >= 2


async def test_fetch_depth_map_without_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("DEPTH_ENDPOINT", raising=False)
    path = save(gradient(), tmp_path, "x.png")
    with pytest.raises(RuntimeError, match="DEPTH_ENDPOINT"):
        await craft_photo.fetch_depth_map(path)


# integration --------------------------------------------------------------------------------
async def test_tool_photo_inspect(call_tool, tmp_path):
    path = save(horizon(), tmp_path, "h.png")
    data = parse(await call_tool("maya_photo_inspect", {"params": {"path": path, "colours": 2}}))
    assert data["colour"]["cast"] == "cool" and data["exif"]["focal_length_mm"] is None


async def test_tool_camera_match(call_tool, fake_maya, tmp_path):
    _camera_stub(fake_maya)
    path = save(horizon(400, 300), tmp_path, "plate.png")
    data = parse(await call_tool("maya_photo_camera_match", {"params": {"path": path, "name": "plateCam", "focal_length": 50}}))
    assert data["camera"] == "|plateCam" and data["aspect"] == 1.3333 and data["resolution"] == [400, 300]
    assert data["photo"]["horizon"]["confident"] is True
    (_, kw), = fake_maya.calls_to("camera")
    assert kw["focalLength"] == 50.0


async def test_tool_block_and_relief(call_tool, fake_maya, tmp_path):
    fake_maya.responses["polyCube"] = lambda **k: [k["name"], "polyCube1"]
    fake_maya.responses["polyPlane"] = lambda **k: [k["name"], "polyPlane1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|" + a[0]] if a and k.get("long") else []
    saved = registry._REGISTRY.pop("procgen.building", None)
    try:
        data = parse(await call_tool("maya_photo_block", {"params": {"width": 800, "depth": 600, "height": 320}}))
    finally:
        if saved is not None:
            registry._REGISTRY["procgen.building"] = saved
    assert data["node"] == "|photoBlock" and data["floors"] == 1
    depth = save(gradient(64, 32), tmp_path, "depth.png")
    data = parse(await call_tool("maya_photo_depth_relief", {"params": {"depth_path": depth, "resolution": 8, "width": 400, "height": 50}}))
    assert data["samples"] == [8, 4] and data["depth_map"]["source_size"] == [64, 32] and data["dims_cm"][2] == 200.0


async def test_tool_photo_error_paths(call_tool, monkeypatch):
    monkeypatch.delenv("DEPTH_ENDPOINT", raising=False)
    assert (await call_tool("maya_photo_inspect", {"params": {"path": "/nope.jpg"}})).startswith("Error: photo not found")
    assert (await call_tool("maya_photo_block", {"params": {"width": 10, "depth": 10}})).startswith("Error: give height")
    assert "style must be" in await call_tool("maya_photo_block", {"params": {"width": 10, "depth": 10, "height": 5, "style": "gothic"}})
    text = await call_tool("maya_photo_depth_relief", {"params": {}})
    assert text.startswith("Error") and "DEPTH_ENDPOINT" in text
