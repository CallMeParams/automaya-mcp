"""Unit + integration tests for the craft_light domain (science, light.* handlers, maya_light_* tools)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from tests.conftest import parse

from automaya_bridge.handlers import _science as sci
from automaya_bridge.handlers import light
from automaya_bridge.handlers._util import BridgeError
from automaya_mcp import science as server_science

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _no_mtoa(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False

    def _boom(*a, **k):
        raise RuntimeError("Plug-in, 'mtoa', was not found on MAYA_PLUG_IN_PATH")

    fake_maya.responses["loadPlugin"] = _boom


def _dag_stub(fake_maya):
    """Shapes end with 'Shape' and live under a transform of the same base name; 'persp' is a camera."""
    fake_maya.responses["objectType"] = lambda n, **k: (not n.endswith("Shape")) if k.get("isType") == "transform" else "transform"
    fake_maya.responses["nodeType"] = lambda n, **k: "camera" if n.endswith("perspShape") else ("mesh" if n.endswith("Shape") else "transform")

    def _rel(n, **k):
        if k.get("parent"):
            return [n.rsplit("|", 1)[0] if "|" in n else "|" + n[:-5]] if n.endswith("Shape") else []
        if k.get("shapes"):
            return [] if n.endswith("Shape") else ["%s|%sShape" % (n if n.startswith("|") else "|" + n, n.lstrip("|"))]
        return []

    fake_maya.responses["listRelatives"] = _rel
    fake_maya.responses["shadingNode"] = lambda t, **k: k["name"]
    fake_maya.responses["exactWorldBoundingBox"] = lambda *a, **k: [-50.0, 0.0, -50.0, 50.0, 180.0, 50.0]


def _set_calls(fake_maya):
    return {a[0]: (a[1:] if len(a) > 2 else a[1]) for a, k in fake_maya.calls_to("setAttr")}


# science: solar -------------------------------------------------------------
def test_solar_sydney_january():
    when = sci.local_to_utc("2026-01-15", "12:00", 11)  # AEDT
    assert when.strftime("%Y-%m-%d %H:%M") == "2026-01-15 01:00"
    sun = sci.solar_position(-33.8688, 151.2093, when)
    assert 69.0 < sun["elevation"] < 73.0  # an hour before solar noon
    assert 40.0 < sun["azimuth"] < 65.0  # summer morning sun sits in the north east
    assert -22.0 < sun["declination"] < -20.5
    noon = sci.solar_position(-33.8688, 151.2093, sci.local_to_utc("2026-01-15", "13:04", 11))
    assert 75.0 < noon["elevation"] < 80.0  # solar noon is about 13:04 AEDT, 90 - |lat - decl| = 77.3
    assert abs(noon["hour_angle"]) < 1.0


def test_solar_london_summer_noon_and_night():
    sun = sci.solar_position(51.5, -0.12, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    assert 61.0 < sun["elevation"] < 63.0 and 175.0 < sun["azimuth"] < 185.0
    night = sci.solar_position(51.5, -0.12, datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc))
    assert night["elevation"] < 0
    with pytest.raises(ValueError):
        sci.solar_position(95.0, 0.0, datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_sun_vectors_and_light_rotation():
    assert sci.sun_direction(90.0, 0.0) == pytest.approx([0.0, 1.0, 0.0], abs=1e-6)
    east = sci.sun_direction(0.0, 90.0)
    assert east[0] == pytest.approx(1.0, abs=1e-6) and abs(east[2]) < 1e-6
    assert sci.sun_light_rotation(30.0, 90.0) == [-30.0, 90.0, 0.0]
    assert sci.sun_light_rotation(45.0, 180.0) == [-45.0, 0.0, 0.0]
    assert sci.aim_rotation([0, 0, 10], [0, 0, 0])[:2] == pytest.approx([0.0, 0.0])
    assert sci.aim_rotation([10, 0, 0], [0, 0, 0])[1] == pytest.approx(90.0)
    assert sci.aim_rotation([0, 10, 0], [0, 0, 0])[0] == pytest.approx(-90.0)


# science: colour and exposure -----------------------------------------------
def test_kelvin_to_rgb_white_and_warm():
    white = sci.kelvin_to_rgb(6500)
    assert all(v > 0.9 for v in white) and white[0] == 1.0
    warm = sci.kelvin_to_rgb(3200)
    assert warm[0] > warm[1] > warm[2] and warm[2] < 0.3
    cool = sci.kelvin_to_rgb(10000)
    assert cool[2] == 1.0 and cool[0] < cool[2]
    assert sci.kelvin_to_rgb(500) == sci.kelvin_to_rgb(1000)  # clamped
    srgb = sci.kelvin_to_rgb(3200, linear=False)
    assert srgb[1] > warm[1]  # encoded values are brighter than linear


def test_ev_from_camera_settings():
    assert sci.ev_from(100, 2.8, 1 / 125) == pytest.approx(9.94, abs=0.05)
    assert sci.ev_from(100, 16, 1 / 100) == pytest.approx(14.64, abs=0.05)  # sunny 16
    assert sci.ev_from(400, 2.8, 1 / 125) == pytest.approx(7.94, abs=0.05)  # 4x ISO is 2 stops
    with pytest.raises(ValueError):
        sci.ev_from(0, 2.8, 0.01)
    settings = sci.ev_to_settings(15.0, iso=100, fstop=16)
    assert settings["shutter"] == pytest.approx(1 / 128, rel=0.01) and settings["shutter_fraction"] == "1/128"


def test_exposure_conventions():
    assert sci.exposure_value_to_arnold(15.0) == 0.0
    assert sci.exposure_value_to_arnold(7.0) == 8.0
    assert sci.arnold_to_exposure_value(8.0) == 7.0
    assert sci.ev_from_illuminance(100000) == pytest.approx(15.29, abs=0.01)
    assert sci.illuminance_from_ev(sci.ev_from_illuminance(500)) == pytest.approx(500, rel=0.01)
    assert sci.split_intensity_exposure(6.0) == (1.5, 2.0)
    assert sci.split_intensity_exposure(0.0) == (0.0, 0.0)


def test_lumens_to_arnold_and_lux():
    bulb = sci.lumens_to_arnold_intensity(800)
    assert bulb["candela"] == pytest.approx(63.66, abs=0.01) and bulb["radiant_watts"] == pytest.approx(800 / 683, rel=1e-3)
    assert bulb["intensity"] * 2 ** bulb["exposure"] == pytest.approx(bulb["arnold_intensity_raw"], rel=1e-3)
    assert bulb["arnold_intensity_raw"] == pytest.approx(63.66 / 100000 * 10000, rel=1e-3)
    assert sci.lux_to_arnold_irradiance(100000) == 1.0
    assert sci.watts_to_lumens(60, 15) == 900
    with pytest.raises(ValueError):
        sci.lumens_to_arnold_intensity(-1)


def test_sky_illuminance_estimate():
    high = sci.sky_illuminance_estimate(60)
    assert 90000 < high["global_horizontal_lux"] < 120000 and 14.5 < high["ev100"] < 16
    low = sci.sky_illuminance_estimate(10)
    assert low["global_horizontal_lux"] < high["global_horizontal_lux"] and low["direct_normal_lux"] < high["direct_normal_lux"]
    dusk = sci.sky_illuminance_estimate(-6)
    assert dusk["sun_above_horizon"] is False and 1 < dusk["global_horizontal_lux"] < 10
    assert sci.sun_kelvin_estimate(60) == 5800 and sci.sun_kelvin_estimate(0) == 2500 and 2500 < sci.sun_kelvin_estimate(10) < 5800


def test_science_copies_are_identical():
    plugin = open(os.path.join(ROOT, "maya_plugin", "automaya_bridge", "handlers", "_science.py"), encoding="utf-8").read()
    server = open(os.path.join(ROOT, "src", "automaya_mcp", "science.py"), encoding="utf-8").read()
    assert plugin == server, "copy maya_plugin/automaya_bridge/handlers/_science.py over src/automaya_mcp/science.py"
    assert server_science.kelvin_to_rgb(6500) == sci.kelvin_to_rgb(6500)


# handlers: sun and sky -------------------------------------------------------
def test_sun_sky_arnold_builds_physical_sky_and_sun(fake_maya):
    _dag_stub(fake_maya)
    out = light.sun_sky(lat=-33.87, lon=151.21, date="2026-01-15", time="12:00", utc_offset=11, name="sun")
    assert out["path"] == "arnold" and out["sun_shape"] == "|sun|sunShape" and out["physical_sky"] == "sunPhysicalSky"
    assert 69 < out["solar"]["elevation"] < 73 and out["ev100"] > 14
    connects = [a for a, k in fake_maya.calls_to("connectAttr")]
    assert ("sunPhysicalSky.outColor", "|sunSky|sunSkyShape.color") in connects
    sets = _set_calls(fake_maya)
    assert sets["defaultRenderGlobals.currentRenderer"] == "arnold"
    assert sets["sunPhysicalSky.elevation"] == pytest.approx(out["solar"]["elevation"])
    assert sets["sunPhysicalSky.azimuth"] == pytest.approx((out["solar"]["azimuth"] - 90) % 360)
    assert sets["sunPhysicalSky.enableSun"] == 0 and sets["sunPhysicalSky.turbidity"] == 3.0
    assert sets["|sun.rotate"][0] == pytest.approx(-out["solar"]["elevation"])
    assert sets["|sun|sunShape.aiExposure"] == out["sun_exposure"] and sets["|sun|sunShape.intensity"] == out["sun_intensity"]
    assert out["sun_intensity"] * 2 ** out["sun_exposure"] == pytest.approx(out["illuminance"]["direct_normal_lux"] / 100000, rel=1e-3)


def test_sun_sky_falls_back_to_maya_light(fake_maya):
    _dag_stub(fake_maya)
    _no_mtoa(fake_maya)
    out = light.sun_sky(lat=51.5, lon=-0.12, date="2026-06-21", time="23:30", utc_offset=1)
    assert out["path"] == "maya" and "physical_sky" not in out and "mtoa" in out["note"]
    assert "below the horizon" in out["note"] and out["solar"]["elevation"] < 0
    assert not [a for a, k in fake_maya.calls_to("shadingNode") if a[0] == "aiPhysicalSky"]


def test_sun_sky_rejects_bad_date(fake_maya):
    with pytest.raises(BridgeError) as exc:
        light.sun_sky(date="15/01/2026")
    assert "YYYY-MM-DD" in str(exc.value)


def test_hdri_dome_wires_file_and_needs_arnold(fake_maya):
    _dag_stub(fake_maya)
    out = light.hdri_dome("/hdr/studio.exr", rotation=90, exposure=-1, camera_visible=False, ground_projection=True)
    assert out["shape"] == "|hdriDome|hdriDomeShape" and out["file_node"] == "hdriDome_hdri_file"
    assert ("hdriDome_hdri_file.outColor", "|hdriDome|hdriDomeShape.color") in [a for a, k in fake_maya.calls_to("connectAttr")]
    sets = _set_calls(fake_maya)
    assert sets[out["transform"] + ".rotate"] == (0.0, 90.0, 0.0) and sets["|hdriDome|hdriDomeShape.camera"] == 0.0 and sets["hdriDome_hdri_file.colorSpace"] == "Raw"
    assert "aiShadowMatte" in out["ground_projection_hint"]
    _no_mtoa(fake_maya)
    with pytest.raises(BridgeError) as exc:
        light.hdri_dome("/hdr/studio.exr")
    assert "needs Arnold" in str(exc.value)


# handlers: rigs ---------------------------------------------------------------
def test_three_point_places_and_exposes_from_bounds(fake_maya):
    _dag_stub(fake_maya)
    out = light.three_point(subject=["hero"], kelvin=3200, fill_stops=-2, rim_stops=1)
    assert out["path"] == "arnold" and out["bbox"]["center"] == [0.0, 90.0, 0.0] and out["distance"] == pytest.approx(225.0)
    key, fill, rim = out["lights"]["key"], out["lights"]["fill"], out["lights"]["rim"]
    assert key["shape"] == "|keyLight|keyLightShape"
    assert key["position"][1] > out["bbox"]["center"][1] and key["position"][2] > 0  # camera side, above centre
    assert rim["position"][2] < 0  # behind the subject
    assert fill["exposure"] == pytest.approx(key["exposure"] - 2) and rim["exposure"] == pytest.approx(key["exposure"] + 1)
    assert key["exposure"] == pytest.approx(out["key_exposure"]) and 14 < key["exposure"] < 17  # 2^exp ~ 0.8 * 225^2
    assert key["color"]["via"] == "aiColorTemperature" and key["color"]["kelvin"] == 3200
    sets = _set_calls(fake_maya)
    assert sets[key["shape"] + ".aiColorTemperature"] == 3200.0 and sets[key["shape"] + ".aiUseColorTemperature"] == 1
    assert sets[key["shape"] + ".aiTranslator"] == "quad" and sets[key["transform"] + ".scale"] == (90.0, 90.0, 90.0)
    assert fake_maya.calls_to("group")[0][1]["name"] == "threePointRig"


def test_three_point_maya_fallback_bakes_kelvin_into_color(fake_maya):
    _dag_stub(fake_maya)
    _no_mtoa(fake_maya)
    out = light.three_point(subject=["hero"], kelvin=3200)
    assert out["path"] == "maya" and any("areaLight" in w for w in out["warnings"])
    assert fake_maya.calls_to("areaLight")[0][1]["name"] == "keyLight"
    key = out["lights"]["key"]
    assert key["color"]["via"] == "color"
    sets = _set_calls(fake_maya)
    assert sets[key["shape"] + ".color"] == tuple(sci.kelvin_to_rgb(3200)) and key["shape"] + ".aiExposure" in sets


def test_studio_styles(fake_maya):
    _dag_stub(fake_maya)
    out = light.studio(subject=["hero"], style="rembrandt")
    assert out["style"] == "rembrandt" and out["ratios_stops"]["fill"] == -3.0 and out["lights"]["key"]["name"] == "rembrandt_keyLight"
    assert out["lights"]["key"]["color"]["kelvin"] == 4300.0
    with pytest.raises(BridgeError):
        light.studio(subject=["hero"], style="disco")


def test_interior_portals_from_window_bounds(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["exactWorldBoundingBox"] = lambda *a, **k: [100.0, 80.0, -60.0, 104.0, 200.0, 60.0]  # thin in X
    fake_maya.responses["ls"] = lambda *a, **k: []
    out = light.interior_portals(windows=["window1"])
    assert out["count"] == 1 and out["portals"][0]["normal_axis"] == "x" and out["skydome_present"] is False
    sets = _set_calls(fake_maya)
    portal = out["portals"][0]["transform"]
    assert sets[portal + ".rotate"] == (0.0, 90.0, 0.0) and sets[portal + ".scale"] == (60.0, 60.0, 1.0) and sets[portal + ".translate"] == (102.0, 140.0, 0.0)
    assert any("skydome" in w.lower() for w in out["warnings"])
    with pytest.raises(BridgeError):
        light.interior_portals(windows=[])


def test_practical_photometry_and_shapes(fake_maya):
    _dag_stub(fake_maya)
    out = light.practical(kind="bulb", position=[10, 200, 0])
    assert out["path"] == "arnold" and out["lumens"] == 800 and out["kelvin"] == 2700
    sets = _set_calls(fake_maya)
    shape = out["shape"]
    assert sets[shape + ".aiTranslator"] == "disk" and sets[shape + ".intensity"] == out["photometry"]["intensity"] and sets[shape + ".exposure"] == out["photometry"]["exposure"]
    assert sets[shape + ".aiColorTemperature"] == 2700.0 and sets[out["transform"] + ".translate"] == (10.0, 200.0, 0.0)
    tube = light.practical(kind="tube", watts=36, name="strip")
    assert tube["lumens"] == pytest.approx(36 * 70) and _set_calls(fake_maya)[tube["shape"] + ".aiTranslator"] == "cylinder"
    neon = light.practical(kind="neon", color=[1, 0.1, 0.3])
    assert neon["color"]["via"] == "color"
    with pytest.raises(BridgeError):
        light.practical(kind="laser")


def test_practical_falls_back_to_point_light(fake_maya):
    _dag_stub(fake_maya)
    _no_mtoa(fake_maya)
    out = light.practical(kind="candle")
    assert out["path"] == "maya" and fake_maya.calls_to("pointLight")[0][1]["name"] == "candleLight"
    assert out["shape"] + ".aiExposure" in _set_calls(fake_maya)


# handlers: exposure and report ------------------------------------------------
def test_exposure_from_ev_and_camera_settings(fake_maya):
    _dag_stub(fake_maya)
    out = light.exposure(ev=7)
    assert out["path"] == "arnold" and out["ai_exposure"] == 8.0 and out["camera"] == "|persp|perspShape"
    assert _set_calls(fake_maya)["|persp|perspShape.aiExposure"] == 8.0
    out = light.exposure(camera="persp", iso=100, fstop=2.8, shutter=1 / 125)
    assert out["ev"] == pytest.approx(9.94, abs=0.05) and out["ai_exposure"] == pytest.approx(5.06, abs=0.05)
    with pytest.raises(BridgeError) as exc:
        light.exposure(iso=100)
    assert "shutter" in str(exc.value)


def test_exposure_without_arnold_sets_viewport(fake_maya):
    _dag_stub(fake_maya)
    _no_mtoa(fake_maya)
    fake_maya.responses["getPanel"] = lambda **k: ["modelPanel4"]
    out = light.exposure(ev=12)
    assert out["path"] == "maya" and out["viewport_panels"] == ["modelPanel4"]
    assert fake_maya.calls_to("modelEditor")[0] == (("modelPanel4",), {"edit": True, "exposure": 3.0})


def test_light_report_sums_illuminance(fake_maya):
    _dag_stub(fake_maya)
    fake_maya.responses["ls"] = lambda *a, **k: {"directionalLight": ["|sun|sunShape"], "aiAreaLight": ["|key|keyShape"]}.get(k.get("type"), [])

    def _node_type(n, **k):
        return {"|sun|sunShape": "directionalLight", "|key|keyShape": "aiAreaLight"}.get(n, "camera" if n.endswith("perspShape") else "transform")

    fake_maya.responses["nodeType"] = _node_type

    def _get(plug, **k):
        if plug.endswith(".intensity"):
            return 1.0
        if plug.endswith(".exposure"):
            return 10.0
        if plug.endswith(".aiExposure"):
            return 0.0 if "sun" in plug else 2.0
        if plug.endswith(".translate"):
            return [(0.0, 100.0, 0.0)]
        return [(1.0, 1.0, 1.0)]

    fake_maya.responses["getAttr"] = _get
    out = light.light_report(target=[0, 0, 0])
    assert out["count"] == 2 and out["lights"][0]["node_type"] == "directionalLight" and out["lights"][0]["irradiance_arnold"] == 1.0
    assert out["lights"][1]["law"] == "inverse_square" and out["lights"][1]["irradiance_arnold"] == pytest.approx(1024 / 10000)
    assert out["scene_ev100"] == pytest.approx(sci.ev_from_illuminance(110240), abs=0.01)
    assert out["camera_ai_exposure"] == 2.0 and "over exposed" in out["verdict"]


def test_offline_helpers_as_commands(fake_maya):
    assert light.kelvin_rgb(6500)["rgb_linear"] == sci.kelvin_to_rgb(6500)
    out = light.lux_to_arnold(lux=500, lumens=800)
    assert out["distant_intensity"] == 0.005 and out["camera_ai_exposure"] == pytest.approx(15 - sci.ev_from_illuminance(500)) and out["point_light"]["candela"] > 60
    with pytest.raises(BridgeError):
        light.lux_to_arnold()


# integration: through the socket ---------------------------------------------
async def test_tool_sun_sky(call_tool, fake_maya):
    _dag_stub(fake_maya)
    data = parse(await call_tool("maya_light_sun_sky", {"params": {"lat": -33.87, "lon": 151.21, "date": "2026-01-15", "time": "12:00", "utc_offset": 11}}))
    assert data["path"] == "arnold" and 69 < data["solar"]["elevation"] < 73 and data["physical_sky"] == "sunPhysicalSky"


async def test_tool_three_point_and_practical(call_tool, fake_maya):
    _dag_stub(fake_maya)
    data = parse(await call_tool("maya_light_three_point", {"params": {"subject": ["hero"], "kelvin": 5600, "fill_stops": -1}}))
    assert data["lights"]["fill"]["exposure"] == pytest.approx(data["lights"]["key"]["exposure"] - 1)
    data = parse(await call_tool("maya_light_practical", {"params": {"kind": "screen", "lumens": 300}}))
    assert data["kind"] == "screen" and data["photometry"]["solid_angle_sr"] == pytest.approx(3.14159, abs=1e-3)


async def test_tool_offline_science(call_tool):
    data = parse(await call_tool("maya_light_solar", {"params": {"lat": -33.87, "lon": 151.21, "date": "2026-01-15", "time": "13:04", "utc_offset": 11}}))
    assert 75 < data["solar"]["elevation"] < 80 and data["sun_kelvin"] == 5800
    data = parse(await call_tool("maya_light_kelvin_to_rgb", {"params": {"kelvin": 6500}}))
    assert all(v > 0.9 for v in data["rgb_linear"])
    data = parse(await call_tool("maya_light_lux_to_arnold", {"params": {"lux": 100000, "lumens": 800}}))
    assert data["distant_intensity"] == 1.0 and data["point_light"]["lumens"] == 800
    assert "Error" in await call_tool("maya_light_lux_to_arnold", {"params": {}})


async def test_tool_error_paths(call_tool, fake_maya):
    _dag_stub(fake_maya)
    _no_mtoa(fake_maya)
    text = await call_tool("maya_light_hdri_dome", {"params": {"path": "/hdr/x.exr"}})
    assert text.startswith("Error") and "needs Arnold" in text
    text = await call_tool("maya_light_practical", {"params": {"kind": "laser"}})
    assert "Error" in text or "validation" in text.lower()
