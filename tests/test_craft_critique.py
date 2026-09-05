"""Tests for the imaging module and the maya_critique_* tools."""
from __future__ import annotations

import base64

from tests.conftest import parse
from tests.synthetic_images import dark, gradient, horizon, noisy, overexposed, save

from automaya_mcp import imaging
from automaya_mcp.tools import craft_critique


# imaging: numbers -----------------------------------------------------------------
def test_gradient_stats_and_histogram():
    gray = imaging.gray_of(gradient())
    stats = imaging.luminance_stats(gray)
    assert abs(stats["mean"] - 0.5) < 0.02 and stats["min"] == 0.0 and stats["max"] == 1.0
    assert 0.27 < imaging.rms_contrast(gray) < 0.31
    hist = imaging.histogram(gradient(), 8)
    assert abs(sum(hist["luma"]) - 1.0) < 1e-3
    assert all(0.1 < b < 0.15 for b in hist["luma"])  # flat ramp fills every bin evenly
    clip = imaging.clipping(gray)
    assert clip["shadows_pct"] < 4.0 and clip["highlights_pct"] < 4.0
    assert imaging.colour_cast(gradient())["cast"] == "neutral"


def test_overexposed_block_clips_highlights():
    gray = imaging.gray_of(overexposed())
    clip = imaging.clipping(gray)
    assert 45.0 < clip["highlights_pct"] < 56.0 and clip["shadows_pct"] == 0.0
    thirds = imaging.thirds_balance(gray)
    assert thirds["left_right"] < -0.5 and thirds["mass_center"][0] < 0.35
    empty = imaging.empty_regions(gray)
    assert empty["empty_pct"] >= 75.0  # flat white and flat grey cells


def test_dark_image_is_crushed():
    gray = imaging.gray_of(dark())
    assert imaging.luminance_stats(gray)["mean"] < 0.02
    assert imaging.clipping(gray)["shadows_pct"] == 100.0
    assert imaging.sharpness(gray) == 0.0
    assert imaging.empty_regions(gray)["empty_pct"] == 100.0


def test_horizon_estimate_and_cast():
    gray = imaging.gray_of(horizon())
    hz = imaging.horizon_estimate(gray)
    assert hz["confident"] is True and abs(hz["row_fraction"] - 0.667) < 0.03
    cast = imaging.colour_cast(horizon())
    assert cast["cast"] == "cool" and cast["warmth"] < -0.1
    palette = imaging.dominant_colours(horizon(), 2)
    assert palette[0]["rgb"] == [120, 160, 220] and palette[0]["share"] > 0.6
    assert all(abs(a - b) < 12 for a, b in zip(palette[1]["rgb"], (60, 80, 40))) and palette[1]["share"] > 0.25
    # a flat image has no confident horizon
    assert imaging.horizon_estimate(imaging.gray_of(dark()))["confident"] is False


def test_sharpness_orders_detail():
    assert imaging.sharpness(imaging.gray_of(noisy())) > imaging.sharpness(imaging.gray_of(gradient())) > 0.0


def test_pure_python_path_matches_numpy(monkeypatch):
    if imaging.np is None:
        return
    with_np = imaging.gray_of(noisy())
    sharp_np = imaging.sharpness(with_np)
    monkeypatch.setattr(imaging, "np", None)
    without = imaging.gray_of(noisy())
    assert max(abs(a - b) for a, b in zip(with_np.data, without.data)) < 1e-9
    assert abs(imaging.sharpness(without) - sharp_np) < 1e-6


def test_exif_missing_is_none(tmp_path):
    path = save(gradient(), tmp_path, "g.png")
    exif = imaging.exif_info(path)
    assert exif["focal_length_mm"] is None and exif["orientation"] is None


def test_exif_focal_is_read(tmp_path):
    from PIL import Image

    img = gradient()
    exif = Image.Exif()
    exif[274] = 1
    exif[272] = "TestCam"
    ifd = exif.get_ifd(0x8769)
    ifd[37386] = 24.0
    ifd[41989] = 36
    path = str(tmp_path / "e.jpg")
    img.save(path, exif=exif.tobytes())
    info = imaging.exif_info(path)
    assert info["focal_length_mm"] == 24.0 and info["focal_length_35mm"] == 36 and info["model"] == "TestCam"
    assert info["sensor_width_guess_mm"] == 24.0


# imaging: findings ----------------------------------------------------------------
def test_findings_carry_maya_fixes():
    result = imaging.analyze(overexposed(), "lookdev")
    issues = [f["issue"] for f in result["findings"]]
    assert any("blown" in i for i in issues)
    top = result["findings"][0]
    assert top["severity"] == "high" and top["fix"]["tool"] == "maya_light_exposure" and "stops" in top["fix"]["change"]
    assert result["score"]["value"] < 75
    assert all(set(f) == {"severity", "issue", "measure", "fix"} for f in result["findings"])
    for f in result["findings"]:
        assert f["fix"]["tool"].startswith("maya_") and f["fix"]["change"]


def test_dark_findings():
    result = imaging.analyze(dark())
    assert result["findings"][0]["issue"].startswith("Shadows are crushed")
    assert result["findings"][0]["fix"]["tool"] == "maya_light_three_point"


def test_clean_image_scores_high():
    result = imaging.analyze(noisy(), "lookdev")
    assert result["score"]["high"] == 0 and result["score"]["value"] >= 90


def test_no_em_dashes_in_findings():
    for img in (gradient(), overexposed(), dark(), horizon()):
        text = str(imaging.analyze(img)["findings"])
        assert "\u2014" not in text and "\u2013" not in text


# imaging: compare -----------------------------------------------------------------
def test_compare_identical_is_similar():
    out = imaging.compare(horizon(), horizon())
    assert out["similarity"] > 0.99 and out["mean_region_error"] == 0.0
    assert out["histogram_distance"]["luma"] == 0.0 and out["differences"] == []


def test_compare_reports_differences():
    out = imaging.compare(horizon(split=0.33), horizon(split=0.66))
    whats = [d["what"] for d in out["differences"]]
    assert "horizon height" in whats
    hz = next(d for d in out["differences"] if d["what"] == "horizon height")
    assert hz["fix"]["tool"] == "maya_transform" and "up" in hz["fix"]["change"]
    assert out["worst_regions"][0]["error"] > 0.2
    out2 = imaging.compare(dark(), overexposed())
    assert any(d["what"] == "overall brightness" and d["fix"]["tool"] == "maya_light_exposure" for d in out2["differences"])
    assert out2["histogram_distance"]["luma"] > 0.5


def test_depth_rows_downsampled_and_normalised():
    grid = imaging.depth_rows(gradient(400, 200), 16)
    assert grid["columns"] == 16 and grid["rows"] == 8
    assert grid["data"][0][0] == 0.0 and grid["data"][0][-1] == 1.0
    inv = imaging.depth_rows(gradient(400, 200), 16, invert=True)
    assert inv["data"][0][0] == 1.0


# tools ----------------------------------------------------------------------------
async def test_tool_analyze_path(call_tool, tmp_path):
    path = save(overexposed(), tmp_path, "over.png")
    data = parse(await call_tool("maya_critique_analyze", {"params": {"path": path, "kind": "lookdev"}}))
    assert data["clipping"]["highlights_pct"] > 45
    assert data["findings"][0]["fix"]["tool"] == "maya_light_exposure"
    assert "score" in data["summary"] and "Top fix" in data["summary"]


async def test_tool_analyze_captures_from_viewport(call_tool, fake_maya, tmp_path):
    fake_maya.responses["about"] = lambda **kw: False if kw.get("batch") else "2024"
    fake_maya.responses["getPanel"] = lambda **kw: "modelPanel4" if kw.get("withFocus") else ("modelPanel" if kw.get("typeOf") else [])
    fake_maya.responses["modelPanel"] = "persp"
    png = base64.b64decode(base64.b64encode(_png_bytes(dark())))

    def playblast(**kw):
        with open(kw["completeFilename"], "wb") as fh:
            fh.write(png)
        return kw["completeFilename"]

    fake_maya.responses["playblast"] = playblast
    data = parse(await call_tool("maya_critique_analyze", {"params": {"use_last_render": True, "source": "viewport"}}))
    assert data["capture"]["camera"] == "persp" and data["path"].endswith(".png")
    assert data["clipping"]["shadows_pct"] == 100.0
    # the capture is remembered for compare
    ref = save(horizon(), tmp_path, "ref.png")
    cmp_data = parse(await call_tool("maya_critique_compare", {"params": {"reference_path": ref}}))
    assert cmp_data["render_path"] == data["path"] and any(d["what"] == "overall brightness" for d in cmp_data["differences"])


async def test_tool_compare_files(call_tool, tmp_path):
    a = save(horizon(split=0.33), tmp_path, "a.png")
    b = save(horizon(split=0.66), tmp_path, "b.png")
    data = parse(await call_tool("maya_critique_compare", {"params": {"render_path": a, "reference_path": b, "grid": 3}}))
    assert len(data["region_error"]) == 3 and data["summary"].startswith("similarity")


async def test_tool_render_and_critique(call_tool, fake_maya):
    fake_maya.responses["about"] = lambda **kw: False if kw.get("batch") else "2024"
    fake_maya.responses["getPanel"] = lambda **kw: "modelPanel4" if kw.get("withFocus") else ("modelPanel" if kw.get("typeOf") else [])
    fake_maya.responses["modelPanel"] = "persp"

    def playblast(**kw):
        with open(kw["completeFilename"], "wb") as fh:
            fh.write(_png_bytes(overexposed()))
        return kw["completeFilename"]

    fake_maya.responses["playblast"] = playblast
    text = await call_tool("maya_render_and_critique", {"params": {"source": "viewport", "kind": "previs", "width": 320, "height": 180}})
    assert "<image" in text and '"maya_light_exposure"' in text and '"histogram"' not in text


async def test_tool_checklist(call_tool):
    data = parse(await call_tool("maya_critique_checklist", {"params": {"kind": "arch"}}))
    assert data["kind"] == "arch" and any(i["tool"] == "maya_light_sun_sky" for i in data["items"])
    assert all(set(i) == {"item", "target", "tool"} for i in data["items"])


async def test_tool_error_paths(call_tool):
    assert (await call_tool("maya_critique_analyze", {"params": {"path": "/nope/missing.png"}})).startswith("Error: image not found")
    assert (await call_tool("maya_critique_checklist", {"params": {"kind": "vfx"}})).startswith("Error: kind")
    text = await call_tool("maya_critique_compare", {"params": {"reference_path": "/nope/ref.png"}})
    assert text.startswith("Error: reference not found")
    # capture failure (batch Maya) is reported, not raised
    text = await call_tool("maya_render_and_critique", {"params": {"source": "viewport"}})
    assert text.startswith("Error") and "interactive" in text


def test_capture_rejects_bad_source():
    import asyncio

    class Ctx:
        pass

    try:
        asyncio.run(craft_critique.capture(Ctx(), "opengl", None, 10, 10))  # type: ignore[arg-type]
    except ValueError as exc:
        assert "source must be" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _png_bytes(img) -> bytes:
    return imaging.png_bytes(img)
