"""Unit + integration tests for the procgen domain."""
from __future__ import annotations

import math
import random
import struct
import zlib

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import procgen
from automaya_bridge.handlers._util import BridgeError


def _scene(fake_maya, bbox=(-600.0, 0.0, -500.0, 600.0, 960.0, 500.0), faces=6):
    """Primitives echo their requested name, bounding boxes and face counts are fixed."""
    def named(cmd):
        return lambda *a, **kw: [kw.get("name", cmd), cmd + "1"]

    for cmd in ("polyCube", "polyCylinder", "polyCone", "polySphere", "polyPlane", "polyCreateFacet", "polyUnite", "polyBoolOp"):
        fake_maya.responses[cmd] = named(cmd)
    fake_maya.responses["exactWorldBoundingBox"] = lambda *a, **k: list(bbox)
    fake_maya.responses["polyEvaluate"] = lambda n, **k: faces if k.get("face") else (16 if k.get("vertex") else 40000.0)


def _boxes(fake_maya):
    """(name, width, height, depth, translate) of every polyCube built, with the position it ended at."""
    out = []
    for name, args, kw in fake_maya.calls:
        if name == "polyCube":
            out.append([kw["name"], kw["width"], kw["height"], kw["depth"], None])
        elif name == "xform" and not kw.get("query") and "translation" in kw and out and args[0] == out[-1][0]:
            out[-1][4] = kw["translation"]
    return out


# pure helpers -----------------------------------------------------------------
def test_value_noise_is_deterministic_and_bounded():
    a = [procgen.value_noise(x * 0.37, x * 0.11, seed=7, octaves=4) for x in range(200)]
    b = [procgen.value_noise(x * 0.37, x * 0.11, seed=7, octaves=4) for x in range(200)]
    c = [procgen.value_noise(x * 0.37, x * 0.11, seed=8, octaves=4) for x in range(200)]
    assert a == b and a != c
    assert all(0.0 <= v <= 1.0 for v in a) and max(a) - min(a) > 0.2
    # lattice points return the raw hash, halfway points blend their neighbours
    assert procgen._smooth_noise(2.0, 3.0, 1) == procgen._hash01(2, 3, 1)
    mid = procgen._smooth_noise(2.5, 3.0, 1)
    assert min(procgen._hash01(2, 3, 1), procgen._hash01(3, 3, 1)) <= mid <= max(procgen._hash01(2, 3, 1), procgen._hash01(3, 3, 1))


def test_terrain_height_scales_with_amplitude():
    h1 = procgen.terrain_height(123.0, -456.0, 3, 4, 300.0, 2500.0)
    h2 = procgen.terrain_height(123.0, -456.0, 3, 4, 600.0, 2500.0)
    assert math.isclose(h2, 2.0 * h1) and abs(h1) <= 300.0


def test_poisson_pick_respects_min_distance():
    rng = random.Random(1)
    points, rejected = procgen.poisson_pick(lambda: [rng.uniform(0, 100), 0.0, rng.uniform(0, 100)], 40, 12.0, 2000)
    assert 1 < len(points) <= 40 and rejected > 0
    for i, p in enumerate(points):
        for q in points[i + 1 :]:
            assert math.dist(p, q) >= 12.0
    rng2 = random.Random(1)
    again, _ = procgen.poisson_pick(lambda: [rng2.uniform(0, 100), 0.0, rng2.uniform(0, 100)], 40, 12.0, 2000)
    assert again == points


def test_cube_face_index_ranges():
    sx, sy, sz = 4, 3, 2
    total = 2 * (sx * sy + sx * sz + sz * sy)
    assert procgen.cube_face("front", 0, 0, sx, sy, sz) == 0
    assert procgen.cube_face("front", 2, 3, sx, sy, sz) == sx * sy - 1
    assert procgen.cube_face("top", 0, 0, sx, sy, sz) == sx * sy
    assert procgen.cube_face("back", 0, 0, sx, sy, sz) == sx * sy + sx * sz
    assert procgen.cube_face("right", 1, 1, sx, sy, sz) == 2 * sx * sy + 2 * sx * sz + 1 * sz + 1
    assert procgen.cube_face("left", 2, 1, sx, sy, sz) == total - 1


def _png(tmp_path, rows, depth=8):
    """Write a greyscale PNG with a mix of scanline filters so the unfilter code is exercised."""
    w, h = len(rows[0]), len(rows)
    raw = b""
    prev = [0] * w
    for i, row in enumerate(rows):
        ftype = i % 3  # none, sub, up
        if depth == 16:
            line = struct.pack(">%dH" % w, *row)
            raw += bytes([0]) + line  # keep 16 bit rows unfiltered
            continue
        if ftype == 1:
            enc = [(row[j] - (row[j - 1] if j else 0)) & 0xFF for j in range(w)]
        elif ftype == 2:
            enc = [(row[j] - prev[j]) & 0xFF for j in range(w)]
        else:
            enc = list(row)
        raw += bytes([ftype]) + bytes(enc)
        prev = row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, depth, 0, 0, 0, 0)
    path = tmp_path / "hm.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return str(path)


def test_read_heightmap_png_and_pgm(tmp_path):
    rows = [[0, 128, 255], [10, 20, 30], [255, 255, 0], [64, 64, 64]]
    w, h, grid = procgen.read_heightmap(_png(tmp_path, rows))
    assert (w, h) == (3, 4)
    assert [[round(v * 255) for v in r] for r in grid] == rows
    w, h, grid16 = procgen.read_heightmap(_png(tmp_path, [[0, 65535], [32768, 1000]], depth=16))
    assert (w, h) == (2, 2) and grid16[0][1] == 1.0 and math.isclose(grid16[1][0], 32768 / 65535)
    pgm = tmp_path / "hm.pgm"
    pgm.write_bytes(b"P5\n2 2\n255\n" + bytes([0, 255, 128, 64]))
    w, h, g = procgen.read_heightmap(str(pgm))
    assert (w, h) == (2, 2) and g[0][1] == 1.0 and math.isclose(g[1][0], 128 / 255)
    assert math.isclose(procgen.sample_heightmap(g, 0.5, 0.0), 0.5)
    bad = tmp_path / "hm.txt"
    bad.write_bytes(b"nope")
    with pytest.raises(BridgeError):
        procgen.read_heightmap(str(bad))


# unit: generators -------------------------------------------------------------
def test_building_defaults_and_facade_grid(fake_maya):
    _scene(fake_maya, faces=10)
    out = procgen.building(floors=3, width=1200, depth=1000, window_spacing=300)
    body = _boxes(fake_maya)[0]
    assert body[:4] == ["building_body_geo", 1200.0, 960.0, 1000.0] and body[4] == [0.0, 480.0, 0.0]
    kw = fake_maya.calls_to("polyCube")[0][1]
    assert kw["subdivisionsX"] == 4 and kw["subdivisionsY"] == 3 and kw["subdivisionsZ"] == 3
    assert out["window_columns"] == [4, 3] and out["floors"] == 3 and out["floor_height"] == 320.0 and out["reveal"] == 15.0
    # every side of every floor gets an opening: (4 + 4 + 3 + 3) x 3
    assert out["facade"]["openings"] == 42
    extrudes = fake_maya.calls_to("polyExtrudeFacet")
    scaled = [(a, k) for a, k in extrudes if "localScaleX" in k]
    recessed = [(a, k) for a, k in extrudes if k.get("localTranslateZ") == -15.0]
    # front/back windows (bay 300), side windows (bay 333) and the door are three extrude groups
    assert len(scaled) == len(recessed) == 3 and sum(len(a) for a, _ in scaled) == 42
    win = next(k for a, k in scaled if len(a) == 23)
    assert math.isclose(win["localScaleX"], 120 / 300, abs_tol=1e-4) and math.isclose(win["localScaleY"], 150 / 320, abs_tol=1e-4) and math.isclose(win["localTranslateY"], 90 + 75 - 160)
    side = next(k for a, k in scaled if len(a) == 18)
    assert math.isclose(side["localScaleX"], 120 / (1000 / 3), abs_tol=1e-4)
    door_faces, door = next((a, k) for a, k in scaled if len(a) == 1)
    assert door_faces == ("building_body_geo.f[%d]" % procgen.cube_face("front", 0, 2, 4, 3, 3),)
    assert math.isclose(door["localScaleX"], 90 / 300, abs_tol=1e-4) and math.isclose(door["localScaleY"], 210 / 320, abs_tol=1e-4) and math.isclose(door["localTranslateY"], 105 - 160)
    assert all(k["keepFacesTogether"] is False for _, k in extrudes)
    cornice = _boxes(fake_maya)[1]
    assert cornice[:4] == ["building_cornice_geo", 1240.0, 25.0, 1040.0] and cornice[4][1] == 960.0 - 12.5
    assert out["group"] == "building_grp" and out["parts"] == ["building_body_geo", "building_cornice_geo"]
    assert out["stats"] == {"faces": 20, "width": 1200.0, "depth": 1000.0, "height": 960.0, "parts": 2}
    assert out["bbox"]["min"] == [-600.0, 0.0, -500.0]
    frozen = [a[0] for a, k in fake_maya.calls_to("makeIdentity") if k.get("apply")]
    assert frozen == ["building_body_geo", "building_cornice_geo"]
    pivots = [k["pivots"] for a, k in fake_maya.calls_to("xform") if "pivots" in k]
    assert pivots[-1] == [0.0, 0.0, 0.0] and len(pivots) == 3
    assert fake_maya.calls_to("group")[0][1] == {"name": "building_grp"}


def test_building_shopfront_roofs_and_style(fake_maya):
    _scene(fake_maya)
    out = procgen.building(name="shop", floors=2, shopfront=True, style="glass", roof="parapet", mullions=True, cornice=False)
    assert out["reveal"] == 5.0 and out["roof"] == "parapet"
    scaled = [(a, k) for a, k in fake_maya.calls_to("polyExtrudeFacet") if "localScaleX" in k]
    shop = next(k for a, k in scaled if len(a) == 3)  # 4 front ground bays minus the door
    assert math.isclose(shop["localScaleY"], (320 - 50) / 320, abs_tol=1e-4) and math.isclose(shop["localTranslateY"], 135 - 160)
    parapet = [k for a, k in fake_maya.calls_to("polyExtrudeFacet") if a == ("shop_parapet_geo.f[1]",)]
    assert parapet[0]["offset"] == 20.0 and parapet[1]["localTranslateZ"] == -80.0
    names = [n for n, *_ in _boxes(fake_maya)]
    assert "shop_parapet_geo" in names and sum(1 for n in names if "mullion" in n) == 2 * (4 + 4 + 3 + 3)
    assert fake_maya.calls_to("polyUnite")[0][1]["name"] == "shop_mullions_geo"
    assert out["parts"] == ["shop_body_geo", "shop_mullions_geo", "shop_parapet_geo"]

    fake_maya.reset()
    _scene(fake_maya)
    procgen.building(name="house", floors=1, width=800, depth=600, roof="pitched")
    facet = fake_maya.calls_to("polyCreateFacet")[0][1]
    x0 = -400 - 30
    assert facet["name"] == "house_roof_geo" and facet["point"][0] == (x0, 320.0, -330.0) and facet["point"][2] == (x0, 320.0, 330.0)
    assert math.isclose(facet["point"][1][1], 320.0 + 330.0 * math.tan(math.radians(30)))
    sweep = [k for a, k in fake_maya.calls_to("polyExtrudeFacet") if a == ("house_roof_geo.f[0]",)][0]
    assert sweep["translate"] == [860.0, 0.0, 0.0]

    fake_maya.reset()
    _scene(fake_maya)
    procgen.building(name="hip", floors=1, width=800, depth=600, roof="hip", cornice=False, entrance=False)
    hip = [k for a, k in fake_maya.calls_to("polyExtrudeFacet") if a == ("hip_roof_geo.f[1]",)][0]
    assert hip["localScaleY"] == 0.02 and math.isclose(hip["localScaleX"], 200 / 860, abs_tol=1e-4) and hip["localTranslateZ"] > 0
    assert not any(len(a) == 1 and a[0].startswith("hip_body_geo") for a, k in fake_maya.calls_to("polyExtrudeFacet"))  # no door


def test_building_footprint_and_errors(fake_maya):
    _scene(fake_maya)
    out = procgen.building(name="odd", floors=2, footprint=[[0, 0], [1000, 0], [1000, 600], [400, 600], [400, 300], [0, 300]], cornice=False)
    facet = fake_maya.calls_to("polyCreateFacet")[0][1]
    assert facet["point"][3] == (400.0, 0.0, 600.0) and facet["name"] == "odd_body_geo"
    assert fake_maya.calls_to("polyExtrudeFacet")[0][1]["translate"] == [0.0, 640.0, 0.0]
    assert out["facade"]["openings"] == 0 and not fake_maya.calls_to("polyCube")
    with pytest.raises(BridgeError):
        procgen.building(style="gothic")
    with pytest.raises(BridgeError):
        procgen.building(roof="dome")
    with pytest.raises(BridgeError):
        procgen.building(sill=200, window_height=150, floor_height=320)
    with pytest.raises(BridgeError):
        procgen.building(footprint=[[0, 0], [1, 1]])


def test_street_block_layout_is_seeded(fake_maya):
    _scene(fake_maya)
    out = procgen.street_block(name="st", lots=2, lot_width=1000, road_width=700, sidewalk=250, seed=5, lamp_spacing=1000, tree_spacing=1000)
    assert out["length"] == 2000 and out["lanes"] == 2 and out["seed"] == 5 and len(out["buildings"]) == 4
    boxes = {b[0]: b for b in _boxes(fake_maya)}
    assert boxes["st_road_geo"][1:4] == [2000.0, 2.0, 700.0] and boxes["st_road_geo"][4] == [0.0, -1.0, 0.0]
    assert boxes["st_sidewalk_north_geo"][1:4] == [2000.0, 15.0, 250.0] and boxes["st_sidewalk_north_geo"][4] == [0.0, 7.5, 475.0]
    assert boxes["st_sidewalk_south_geo"][4] == [0.0, 7.5, -475.0]
    north = [b for b in out["buildings"] if "north" in b["group"]]
    assert [b["x"] for b in north] == [-500.0, 500.0] and all(b["z"] == 350 + 250 + 500 for b in north)
    assert all(2 <= b["floors"] <= 5 for b in out["buildings"])
    rotated = [a[0] for a, k in fake_maya.calls_to("xform") if k.get("rotation") == [0.0, 180.0, 0.0]]
    assert rotated == ["st_south_0_grp", "st_south_1_grp"]
    poles = [k["name"] for a, k in fake_maya.calls_to("polyCylinder") if "pole" in k["name"]]
    assert len(poles) == 6  # 3 per side
    floors_a = [b["floors"] for b in out["buildings"]]
    fake_maya.reset()
    _scene(fake_maya)
    floors_b = [b["floors"] for b in procgen.street_block(name="st", lots=2, lot_width=1000, seed=5)["buildings"]]
    assert floors_a == floors_b
    with pytest.raises(BridgeError):
        procgen.street_block(floors_min=6, floors_max=2)


def test_room_shell_slabs_and_openings(fake_maya):
    _scene(fake_maya)
    out = procgen.room_shell(name="rm", width=500, depth=400, height=280, wall_thickness=20, openings=[{"wall": "front", "kind": "door"}, {"wall": "left", "kind": "window", "offset": 80}])
    boxes = {b[0]: b for b in _boxes(fake_maya)}
    assert boxes["rm_floor_geo"][1:4] == [540.0, 20.0, 440.0] and boxes["rm_floor_geo"][4] == [0.0, -10.0, 0.0]
    assert boxes["rm_ceiling_geo"][4] == [0.0, 290.0, 0.0]
    assert boxes["rm_wall_front_geo"][1:4] == [540.0, 280.0, 20.0] and boxes["rm_wall_front_geo"][4] == [0.0, 140.0, 210.0]
    assert boxes["rm_wall_left_geo"][1:4] == [20.0, 280.0, 400.0] and boxes["rm_wall_left_geo"][4] == [-260.0, 140.0, 0.0]
    assert boxes["rm_cut0"][1:4] == [90.0, 210.0, 60.0] and boxes["rm_cut0"][4] == [0.0, 105.0, 210.0]
    assert boxes["rm_cut1"][1:4] == [60.0, 150.0, 120.0] and boxes["rm_cut1"][4] == [-260.0, 165.0, 80.0]
    bools = fake_maya.calls_to("polyBoolOp")
    assert bools[0][0] == ("rm_wall_front_geo", "rm_cut0") and bools[0][1]["operation"] == 2 and bools[1][0][0] == "rm_wall_left_geo"
    assert out["openings"] == 2 and out["interior"] == [500.0, 280.0, 400.0] and len(out["parts"]) == 6
    with pytest.raises(BridgeError):
        procgen.room_shell(openings=[{"wall": "roof"}])


def test_stairs_from_total_rise(fake_maya):
    _scene(fake_maya)
    out = procgen.stairs(name="s", total_rise=280, rise=17, run=28, width=120, landing=100)
    assert out["steps"] == 17 and out["total_rise"] == 289.0 and out["total_run"] == 17 * 28 + 100 and out["angle_deg"] == 31.26
    boxes = _boxes(fake_maya)
    assert len(boxes) == 18 and boxes[0][1:4] == [120.0, 17.0, 28.0] and boxes[0][4] == [0.0, 8.5, 14.0]
    assert boxes[16][2] == 17 * 17.0 and boxes[17][0] == "s_landing" and boxes[17][4][2] == 17 * 28 + 50.0
    unite = fake_maya.calls_to("polyUnite")[0]
    assert len(unite[0]) == 18 and unite[1]["name"] == "s_geo" and unite[1]["constructionHistory"] is False
    assert out["parts"] == ["s_geo"]
    with pytest.raises(BridgeError):
        procgen.stairs(rise=100)


def test_railing_straight_and_on_curve(fake_maya):
    _scene(fake_maya)
    fake_maya.responses["angleBetween"] = lambda **k: [0.0, 0.0, -90.0]
    out = procgen.railing(name="r", length=300, height=100, post_spacing=120, mid_rails=1)
    assert out["posts"] == 4 and out["rails"] == 2
    cyls = fake_maya.calls_to("polyCylinder")
    posts = [k for a, k in cyls if "post" in k["name"]]
    assert len(posts) == 4 and posts[0]["height"] == 100.0 and posts[0]["radius"] == 2.0
    rails = [k for a, k in cyls if "rail" in k["name"]]
    assert len(rails) == 6 and math.isclose(rails[0]["height"], 100.0)
    fake_maya.reset()
    _scene(fake_maya)
    fake_maya.responses["arclen"] = 500.0
    fake_maya.responses["pointOnCurve"] = lambda c, **k: [k["parameter"] * 500.0, 0.0, 10.0] if k.get("position") else [1.0, 0.0, 0.0]
    out = procgen.railing(name="r", curve="path", post_spacing=100)
    assert out["posts"] == 6 and out["curve"] == "path"
    assert fake_maya.calls_to("pointOnCurve")[0][1]["turnOnPercentage"] is True


def test_pipes_along_curve(fake_maya):
    _scene(fake_maya)
    fake_maya.responses["arclen"] = 800.0
    fake_maya.responses["pointOnCurve"] = lambda c, **k: [5.0, 6.0, 7.0] if k.get("position") else [0.0, 0.0, 1.0]
    out = procgen.pipes_along_curve(name="p", curve="crv", radius=5, segments=12, count=2)
    assert out["divisions"] == 40 and out["count"] == 2 and out["arc_length"] == 800.0
    ext = fake_maya.calls_to("polyExtrudeFacet")
    assert [a for a, k in ext] == [("p_0_geo.f[13]",), ("p_1_geo.f[13]",)] and ext[0][1]["inputCurve"] == "crv" and ext[0][1]["divisions"] == 40
    starts = [k["translation"] for a, k in fake_maya.calls_to("xform") if "translation" in k and not k.get("query")]
    assert starts[0] == [5.0 - 7.5, 6.0, 7.0] and starts[1] == [5.0 + 7.5, 6.0, 7.0]
    with pytest.raises(BridgeError):
        procgen.pipes_along_curve(curve="")


def test_fence_counts(fake_maya):
    _scene(fake_maya)
    out = procgen.fence(name="f", length=1000, height=120, post_spacing=200, rails=2, picket_width=8, picket_gap=6)
    assert out["posts"] == 6 and out["rails"] == 2 and out["pickets"] == 71
    boxes = _boxes(fake_maya)
    assert boxes[0][1:4] == [10.0, 125.0, 10.0] and boxes[5][4][0] == 1000.0
    rails = [b for b in boxes if "rail" in b[0]]
    assert rails[0][4][1] == 40.0 + 2.0 and rails[1][4][1] == 80.0 + 2.0
    assert len(fake_maya.calls_to("polyUnite")[0][0]) == 6 + 2 + 71
    with pytest.raises(BridgeError):
        procgen.fence(length=100000, picket_width=1, picket_gap=0)


@pytest.mark.parametrize("order, parts, diameter", [("plain", 1, 50.0), ("doric", 5, 50.0), ("ionic", 6, 400 / 9), ("corinthian", 5, 40.0)])
def test_column_orders(fake_maya, order, parts, diameter):
    _scene(fake_maya)
    out = procgen.column(name="c", order=order, height=400)
    assert len(out["parts"]) == parts and math.isclose(out["diameter"], diameter)
    scale = fake_maya.calls_to("scale")[0]
    assert scale[0][:3] == (0.85, 1.0, 0.85) and scale[0][3] == "c_shaft_geo.vtx[24:47]"
    assert math.isclose(scale[1]["pivot"][1], out["shaft_height"] + (0 if order == "plain" else diameter / 4.0))


def test_furniture_and_vehicle_dimensions(fake_maya):
    _scene(fake_maya)
    out = procgen.furniture_proxy(kind="table")
    boxes = _boxes(fake_maya)
    assert out["dimensions"] == [120.0, 75.0, 80.0] and boxes[0][:4] == ["table_top_geo", 120.0, 4.0, 80.0] and boxes[0][4][1] == 73.0
    assert len(boxes) == 5 and boxes[1][2] == 71.0
    fake_maya.reset()
    _scene(fake_maya)
    out = procgen.furniture_proxy(kind="chair", name="ch")
    boxes = {b[0]: b for b in _boxes(fake_maya)}
    assert boxes["ch_seat_geo"][4][1] == 43.0 and boxes["ch_back_geo"][2] == 45.0 and out["group"] == "ch_grp"
    fake_maya.reset()
    _scene(fake_maya)
    out = procgen.vehicle_proxy(kind="car")
    boxes = {b[0]: b for b in _boxes(fake_maya)}
    assert out["dimensions"] == [450.0, 180.0, 150.0] and out["forward"] == "+x" and boxes["car_body_geo"][1] == 450.0 and boxes["car_body_geo"][3] == 180.0
    wheels = [k for a, k in fake_maya.calls_to("polyCylinder")]
    assert len(wheels) == 4 and wheels[0]["radius"] == 32.5
    tilted = [a[0] for a, k in fake_maya.calls_to("xform") if k.get("rotation") == [90.0, 0.0, 0.0]]
    assert len(tilted) == 4
    with pytest.raises(BridgeError):
        procgen.furniture_proxy(kind="piano")
    with pytest.raises(BridgeError):
        procgen.vehicle_proxy(kind="tank")


def test_tree_and_rock(fake_maya):
    _scene(fake_maya)
    out = procgen.tree_proxy(name="t", height=800, canopy="conical", trunk_ratio=0.25)
    assert out["trunk_height"] == 200.0 and out["parts"] == ["t_trunk_geo", "t_canopy_geo"]
    cone = fake_maya.calls_to("polyCone")[0][1]
    assert cone["height"] == 600.0 and math.isclose(cone["radius"], 210.0)
    fake_maya.reset()
    _scene(fake_maya)
    procgen.tree_proxy(canopy="umbrella")
    assert any(k.get("scale") == [1.3, 0.45, 1.3] for a, k in fake_maya.calls_to("xform"))

    fake_maya.reset()
    _scene(fake_maya)
    verts = [(math.cos(i), 35.0 + math.sin(i), math.sin(i * 2)) for i in range(16)]
    fake_maya.responses["xform"] = lambda n, **k: [c for v in verts for c in v] if k.get("query") else None
    out = procgen.rock(name="rk", size=100, seed=9, subdivisions=8)
    moved_a = [k["translation"] for a, k in fake_maya.calls_to("xform") if ".vtx[" in a[0] and not k.get("query")]
    assert out["vertices"] == 16 and out["seed"] == 9 and len(moved_a) == 16
    assert any(math.dist(moved_a[i], verts[i]) > 0.5 for i in range(16))
    assert fake_maya.calls_to("polySphere")[0][1]["subdivisionsX"] == 8
    fake_maya.reset()
    _scene(fake_maya)
    fake_maya.responses["xform"] = lambda n, **k: [c for v in verts for c in v] if k.get("query") else None
    procgen.rock(name="rk", size=100, seed=9, subdivisions=8)
    moved_b = [k["translation"] for a, k in fake_maya.calls_to("xform") if ".vtx[" in a[0] and not k.get("query")]
    assert moved_a == moved_b


def test_terrain_noise_and_heightmap(fake_maya, tmp_path):
    _scene(fake_maya)
    sub = 3
    grid = [(-500.0 + 1000.0 * c / sub, 0.0, -500.0 + 1000.0 * r / sub) for r in range(sub + 1) for c in range(sub + 1)]
    fake_maya.responses["polyEvaluate"] = lambda n, **k: len(grid) if k.get("vertex") else 9
    fake_maya.responses["xform"] = lambda n, **k: [c for v in grid for c in v] if k.get("query") else None
    out = procgen.terrain(name="ter", width=1000, depth=1000, subdivisions=sub, height=100, seed=4, octaves=3)
    plane = fake_maya.calls_to("polyPlane")[0][1]
    assert plane["width"] == 1000.0 and plane["height"] == 1000.0 and plane["subdivisionsX"] == sub and plane["name"] == "ter_geo"
    moves = [(a[0], k["translation"]) for a, k in fake_maya.calls_to("xform") if ".vtx[" in a[0] and not k.get("query")]
    assert len(moves) == 16 and moves[0][0] == "ter_geo.vtx[0]" and out["source"] == "noise" and out["feature_size"] == 250.0
    for (x, _, z), (_, t) in zip(grid, moves):
        assert t[0] == x and t[2] == z and math.isclose(t[1], procgen.terrain_height(x, z, 4, 3, 100.0, 250.0))
    assert -100.0 <= out["height_range"][0] <= out["height_range"][1] <= 100.0
    path = _png(tmp_path, [[0, 255], [255, 0]])
    fake_maya.reset()
    _scene(fake_maya)
    fake_maya.responses["polyEvaluate"] = lambda n, **k: len(grid) if k.get("vertex") else 9
    fake_maya.responses["xform"] = lambda n, **k: [c for v in grid for c in v] if k.get("query") else None
    out = procgen.terrain(name="ter", width=1000, depth=1000, subdivisions=sub, height=200, heightmap=path)
    moves = [k["translation"] for a, k in fake_maya.calls_to("xform") if ".vtx[" in a[0] and not k.get("query")]
    assert out["source"] == "heightmap" and moves[0][1] == 0.0 and moves[3][1] == 200.0 and moves[12][1] == 200.0 and moves[15][1] == 0.0
    assert math.isclose(moves[5][1], 200.0 * (1 / 3 * 2 / 3 + 2 / 3 * 1 / 3))


def test_scatter_on_surface_faces(fake_maya):
    _scene(fake_maya)
    fake_maya.existing.update({"src", "ground"})
    verts = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 0.0, 100.0), (0.0, 0.0, 100.0)]
    fake_maya.responses["polyEvaluate"] = lambda n, **k: 4 if k.get("vertex") else (1 if k.get("face") else 10000.0)
    fake_maya.responses["xform"] = lambda n, **k: [c for v in verts for c in v] if k.get("query") else None
    fake_maya.responses["polyInfo"] = lambda n, **k: ["FACE      0:      0      1      2      3 \n"] if k.get("faceToVertex") else ["FACE_NORMAL      0: 0.000000 1.000000 0.000000\n"]
    counter = {"n": 0}

    def inst(src, **kw):
        counter["n"] += 1
        return [kw["name"]]

    fake_maya.responses["instance"] = inst
    out = procgen.scatter(name="sc", sources=["src"], surface="ground", count=20, min_distance=15.0, seed=11, scale_range=[0.5, 2.0], align_to_normal=True)
    assert out["stats"]["mode"] == "faces" and 1 < out["stats"]["count"] <= 20 and out["stats"]["rejected"] > 0 and out["seed"] == 11
    assert out["parts"][0] == "sc_0" and len(out["parts"]) == counter["n"]
    for p in out["positions"]:
        assert 0.0 <= p[0] <= 100.0 and p[1] == 0.0 and 0.0 <= p[2] <= 100.0
    for i, p in enumerate(out["positions"]):
        for q in out["positions"][i + 1 :]:
            assert math.dist(p, q) >= 15.0 - 1e-6
    scales = [k["scale"][0] for a, k in fake_maya.calls_to("xform") if "scale" in k]
    assert all(0.5 <= s <= 2.0 for s in scales) and len(scales) == out["stats"]["count"]
    assert fake_maya.calls_to("angleBetween")[0][1]["v2"] == [0.0, 1.0, 0.0]
    first = out["positions"]
    fake_maya.reset()
    _scene(fake_maya)
    fake_maya.existing.update({"src", "ground"})
    fake_maya.responses["polyEvaluate"] = lambda n, **k: 4 if k.get("vertex") else (1 if k.get("face") else 10000.0)
    fake_maya.responses["xform"] = lambda n, **k: [c for v in verts for c in v] if k.get("query") else None
    fake_maya.responses["polyInfo"] = lambda n, **k: ["FACE      0:      0      1      2      3 \n"] if k.get("faceToVertex") else ["FACE_NORMAL      0: 0.000000 1.000000 0.000000\n"]
    fake_maya.responses["instance"] = inst
    out = procgen.scatter(name="sc", sources=["src"], surface="ground", count=20, min_distance=15.0, seed=11, scale_range=[0.5, 2.0], align_to_normal=True)
    assert out["positions"] == first
    # density: 1 per square metre on a 1 m2 surface gives one instance
    out = procgen.scatter(name="d", sources=["src"], surface="ground", density=3.0, seed=1)
    assert out["stats"]["requested"] == 3


def test_scatter_fallbacks_and_errors(fake_maya):
    _scene(fake_maya)
    fake_maya.existing.update({"src", "ground"})
    fake_maya.responses["instance"] = lambda src, **kw: [kw["name"]]
    # no faces readable: fall back to the top of the surface bbox
    fake_maya.responses["polyInfo"] = lambda n, **k: []
    out = procgen.scatter(sources=["src"], surface="ground", count=5, seed=2)
    assert out["stats"]["mode"] == "bbox" and all(p[1] == 960.0 and -600 <= p[0] <= 600 for p in out["positions"])
    # no surface: ground rectangle
    fake_maya.calls.clear()
    out = procgen.scatter(sources=["src"], count=5, seed=2, bounds=[[0, 0], [10, 10]], rotation_random=0)
    assert out["stats"]["mode"] == "ground" and all(0 <= p[0] <= 10 and p[1] == 0.0 for p in out["positions"])
    rotations = [k["rotation"] for a, k in fake_maya.calls_to("xform") if "rotation" in k]
    assert rotations and all(r == [0.0, 0.0, 0.0] for r in rotations)
    with pytest.raises(BridgeError):
        procgen.scatter(sources=[])
    with pytest.raises(BridgeError):
        procgen.scatter(sources=["missing"])
    # a min_distance larger than the area keeps only the first point and reports the rest as rejected
    out = procgen.scatter(sources=["src"], count=50, min_distance=1000.0, bounds=[[0, 0], [10, 10]], seed=1)
    assert out["stats"]["count"] == 1 and out["stats"]["rejected"] == 50 * 30 - 1


def test_array_along_curve_and_grid(fake_maya):
    _scene(fake_maya, bbox=(0.0, 0.0, 0.0, 100.0, 50.0, 40.0))
    fake_maya.existing.update({"lamp", "crv"})
    fake_maya.responses["pointOnCurve"] = lambda c, **k: [k["parameter"] * 900.0, 0.0, 5.0] if k.get("position") else [0.0, 0.0, 1.0]
    fake_maya.responses["instance"] = lambda src, **kw: [kw["name"]]
    fake_maya.responses["duplicate"] = lambda src, **kw: [kw["name"]]
    fake_maya.responses["angleBetween"] = lambda **k: [0.0, -90.0, 0.0]
    out = procgen.array_along_curve(name="row", node="lamp", curve="crv", count=4, forward_axis="x")
    assert out["stats"]["count"] == 4 and out["positions"] == [[0.0, 0.0, 5.0], [300.0, 0.0, 5.0], [600.0, 0.0, 5.0], [900.0, 0.0, 5.0]]
    assert len(fake_maya.calls_to("instance")) == 4 and not fake_maya.calls_to("duplicate")
    assert fake_maya.calls_to("angleBetween")[0][1] == {"euler": True, "v1": [1.0, 0.0, 0.0], "v2": [0.0, 0.0, 1.0]}
    assert [k["rotation"] for a, k in fake_maya.calls_to("xform") if "rotation" in k] == [[0.0, -90.0, 0.0]] * 4
    fake_maya.reset()
    _scene(fake_maya, bbox=(0.0, 0.0, 0.0, 100.0, 50.0, 40.0))
    fake_maya.existing.add("lamp")
    fake_maya.responses["duplicate"] = lambda src, **kw: [kw["name"]]
    fake_maya.responses["xform"] = lambda n, **k: [10.0, 0.0, 20.0] if k.get("query") else None
    out = procgen.grid_array(name="g", node="lamp", rows=2, columns=3, instance=False)
    assert out["spacing"] == [150.0, 60.0] and out["stats"]["count"] == 6 and out["parts"][-1] == "g_1_2"
    assert out["positions"][0] == [10.0, 0.0, 20.0] and out["positions"][5] == [10.0 + 300.0, 0.0, 20.0 + 60.0]
    assert len(fake_maya.calls_to("duplicate")) == 6
    j1 = procgen.grid_array(node="lamp", rows=2, columns=2, jitter=5.0, seed=3)["positions"]
    j2 = procgen.grid_array(node="lamp", rows=2, columns=2, jitter=5.0, seed=3)["positions"]
    assert j1 == j2 and j1[1][0] != 160.0
    with pytest.raises(BridgeError):
        procgen.grid_array(node="lamp", rows=200, columns=200)
    with pytest.raises(BridgeError):
        procgen.array_along_curve(node="lamp", curve="")


# integration: through the MCP tools over the real socket ----------------------
async def test_tool_building(call_tool, fake_maya):
    _scene(fake_maya, faces=12)
    data = parse(await call_tool("maya_procgen_building", {"params": {"name": "blk", "floors": 4, "width": 1500, "depth": 900, "roof": "parapet", "shopfront": True}}))
    assert data["group"] == "blk_grp" and data["parts"] == ["blk_body_geo", "blk_cornice_geo", "blk_parapet_geo"]
    assert data["stats"]["faces"] == 36 and data["stats"]["height"] == 960.0 and data["floors"] == 4 and data["window_columns"] == [5, 3]
    assert fake_maya.calls_to("polyCube")[0][1]["height"] == 1280.0


async def test_tool_furniture_and_scatter(call_tool, fake_maya):
    _scene(fake_maya)
    data = parse(await call_tool("maya_procgen_furniture_proxy", {"params": {"kind": "sofa"}}))
    assert data["kind"] == "sofa" and data["dimensions"] == [200.0, 85.0, 90.0] and len(data["parts"]) == 4
    fake_maya.existing.update({"sofa_grp"})
    fake_maya.responses["instance"] = lambda src, **kw: [kw["name"]]
    data = parse(await call_tool("maya_procgen_scatter", {"params": {"sources": ["sofa_grp"], "count": 6, "seed": 1, "bounds": [[0, 0], [1000, 1000]]}}))
    assert data["stats"]["count"] == 6 and data["seed"] == 1 and len(data["positions"]) == 6


async def test_tool_validation_and_maya_errors(call_tool, fake_maya):
    _scene(fake_maya)
    text = await call_tool("maya_procgen_stairs", {"params": {"rise": 500}})
    assert text.startswith("Error")
    text = await call_tool("maya_procgen_column", {"params": {"order": "composite"}})
    assert text.startswith("Error") and "order must be one of" in text
    fake_maya.existing.add("something")
    text = await call_tool("maya_procgen_scatter", {"params": {"sources": ["missing_tree"]}})
    assert "not found" in text
