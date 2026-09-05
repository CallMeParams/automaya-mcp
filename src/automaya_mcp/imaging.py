"""Pure image analysis for the craft critique and photo tools.

Everything here runs server side with Pillow; numpy speeds up a few loops when
it is installed but nothing depends on it. Images are analysed at a reduced
working size (long side ``WORK_SIZE`` px) so pure Python loops stay fast.

Values are plain floats in 0..1 (luminance) or 0..255 (RGB) so the numbers in
a critique read like what an artist sees in a histogram or a colour picker.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from PIL import Image, ImageOps

try:  # optional acceleration
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

WORK_SIZE = 256
SHADOW_CLIP = 0.02
HIGHLIGHT_CLIP = 0.98

# Rec.709 luma weights on linear-ish display values; good enough for critique.
LUMA = (0.2126, 0.7152, 0.0722)

# EXIF tag ids we care about (avoid importing PIL.ExifTags tables that changed across versions).
EXIF_TAGS = {
    271: "make",
    272: "model",
    274: "orientation",
    306: "datetime",
    33434: "exposure_time",
    33437: "f_number",
    34855: "iso",
    37386: "focal_length_mm",
    41989: "focal_length_35mm",
}


@dataclass
class Gray:
    """A row major luminance buffer in 0..1."""

    width: int
    height: int
    data: List[float]

    def at(self, x: int, y: int) -> float:
        return self.data[y * self.width + x]

    def row(self, y: int) -> List[float]:
        return self.data[y * self.width:(y + 1) * self.width]


# loading ---------------------------------------------------------------------
def load_image(source: Any) -> Image.Image:
    """Open a path (or pass through an Image), honour EXIF orientation, return RGB."""
    if isinstance(source, Image.Image):
        img = source
    else:
        img = Image.open(str(source))
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:  # noqa: BLE001, some formats have broken EXIF blocks
            pass
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _pixels(img: Image.Image) -> list:
    """Flat pixel list; Pillow 12 renamed getdata."""
    getter = getattr(img, "get_flattened_data", None)
    return list(getter()) if getter is not None else list(img.getdata())


def downsample(img: Image.Image, max_side: int = WORK_SIZE) -> Image.Image:
    w, h = img.size
    scale = max(w, h) / float(max_side)
    if scale <= 1.0:
        return img
    return img.resize((max(1, int(round(w / scale))), max(1, int(round(h / scale)))), Image.BILINEAR)


def gray_of(img: Image.Image, max_side: int = WORK_SIZE) -> Gray:
    small = downsample(load_image(img), max_side)
    w, h = small.size
    if np is not None:
        arr = np.asarray(small, dtype=np.float64) / 255.0
        luma = arr[..., 0] * LUMA[0] + arr[..., 1] * LUMA[1] + arr[..., 2] * LUMA[2]
        return Gray(w, h, luma.reshape(-1).tolist())
    px = _pixels(small)
    return Gray(w, h, [(r * LUMA[0] + g * LUMA[1] + b * LUMA[2]) / 255.0 for r, g, b in px])


def image_meta(img: Image.Image) -> Dict[str, Any]:
    w, h = img.size
    return {
        "width": w,
        "height": h,
        "aspect": round(w / float(h), 4) if h else None,
        "orientation": "landscape" if w > h else ("portrait" if h > w else "square"),
        "megapixels": round(w * h / 1e6, 2),
    }


# statistics -----------------------------------------------------------------
def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def luminance_stats(gray: Gray) -> Dict[str, float]:
    """Mean, median, std, min, max and 1/99 percentiles of luminance (0..1)."""
    data = gray.data
    n = len(data) or 1
    mean = sum(data) / n
    var = sum((v - mean) ** 2 for v in data) / n
    s = sorted(data)
    return {
        "mean": round(mean, 4),
        "median": round(_percentile(s, 0.5), 4),
        "std": round(math.sqrt(var), 4),
        "min": round(s[0], 4) if s else 0.0,
        "max": round(s[-1], 4) if s else 0.0,
        "p1": round(_percentile(s, 0.01), 4),
        "p99": round(_percentile(s, 0.99), 4),
    }


def histogram(img: Image.Image, bins: int = 16) -> Dict[str, List[float]]:
    """Normalised histograms (fractions summing to 1) for luma and each RGB channel."""
    small = downsample(load_image(img))
    gray = gray_of(small)
    out: Dict[str, List[float]] = {"luma": _hist(gray.data, bins, 1.0)}
    for name, band in zip(("r", "g", "b"), small.split()):
        values = [v / 255.0 for v in _pixels(band)]
        out[name] = _hist(values, bins, 1.0)
    return out


def _hist(values: Sequence[float], bins: int, top: float) -> List[float]:
    counts = [0] * bins
    n = len(values) or 1
    for v in values:
        i = int(v / top * bins)
        counts[min(bins - 1, max(0, i))] += 1
    return [round(c / n, 5) for c in counts]


def clipping(gray: Gray, low: float = SHADOW_CLIP, high: float = HIGHLIGHT_CLIP) -> Dict[str, float]:
    """Percent of pixels crushed to black or blown to white."""
    n = len(gray.data) or 1
    shadows = sum(1 for v in gray.data if v <= low)
    highlights = sum(1 for v in gray.data if v >= high)
    return {"shadows_pct": round(100.0 * shadows / n, 2), "highlights_pct": round(100.0 * highlights / n, 2)}


def rms_contrast(gray: Gray) -> float:
    """Standard deviation of luminance, the usual RMS contrast measure (0..0.5)."""
    n = len(gray.data) or 1
    mean = sum(gray.data) / n
    return round(math.sqrt(sum((v - mean) ** 2 for v in gray.data) / n), 4)


def colour_cast(img: Image.Image) -> Dict[str, Any]:
    """Mean RGB, drift of each channel from the grey mean and a plain name for the cast."""
    small = downsample(load_image(img))
    px = _pixels(small)
    n = len(px) or 1
    mean = [sum(p[i] for p in px) / n for i in range(3)]
    grey = sum(mean) / 3.0
    drift = [m - grey for m in mean]
    strength = max(abs(d) for d in drift)
    r, g, b = drift
    if strength < 4.0:
        name = "neutral"
    elif r > 0 and b < 0:
        name = "warm"
    elif b > 0 and r < 0:
        name = "cool"
    elif g > 0 and g >= max(r, b):
        name = "green"
    elif g < 0 and abs(g) >= max(abs(r), abs(b)):
        name = "magenta"
    else:
        name = "mixed"
    return {
        "mean_rgb": [round(m, 1) for m in mean],
        "drift_rgb": [round(d, 1) for d in drift],
        "strength": round(strength, 1),
        "cast": name,
        "warmth": round((mean[0] - mean[2]) / 255.0, 3),
    }


def sharpness(gray: Gray) -> float:
    """Variance of the Laplacian; higher is sharper. Below ~0.0005 reads as soft or empty."""
    w, h = gray.width, gray.height
    if w < 3 or h < 3:
        return 0.0
    if np is not None:
        a = np.asarray(gray.data, dtype=np.float64).reshape(h, w)
        lap = -4.0 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
        return round(float(lap.var()), 6)
    vals: List[float] = []
    d = gray.data
    for y in range(1, h - 1):
        base = y * w
        for x in range(1, w - 1):
            i = base + x
            vals.append(-4.0 * d[i] + d[i - w] + d[i + w] + d[i - 1] + d[i + 1])
    n = len(vals) or 1
    mean = sum(vals) / n
    return round(sum((v - mean) ** 2 for v in vals) / n, 6)


# composition ----------------------------------------------------------------
def region_grid(gray: Gray, cols: int, rows: int) -> List[List[Dict[str, float]]]:
    """Mean and std of luminance per cell, rows top to bottom, cols left to right."""
    out: List[List[Dict[str, float]]] = []
    for r in range(rows):
        y0 = r * gray.height // rows
        y1 = (r + 1) * gray.height // rows if r < rows - 1 else gray.height
        row: List[Dict[str, float]] = []
        for c in range(cols):
            x0 = c * gray.width // cols
            x1 = (c + 1) * gray.width // cols if c < cols - 1 else gray.width
            cell = [gray.data[y * gray.width + x] for y in range(y0, y1) for x in range(x0, x1)]
            n = len(cell) or 1
            mean = sum(cell) / n
            std = math.sqrt(sum((v - mean) ** 2 for v in cell) / n)
            row.append({"mean": round(mean, 4), "std": round(std, 4)})
        out.append(row)
    return out


def thirds_balance(gray: Gray) -> Dict[str, Any]:
    """Rule of thirds grid of mean luminance plus where the bright mass sits.

    ``mass_center`` is the luminance weighted centroid in 0..1 (x right, y down);
    ``left_right`` and ``top_bottom`` are signed balances in -1..1 (positive means
    the right or bottom side carries more light).
    """
    grid = region_grid(gray, 3, 3)
    w, h = gray.width, gray.height
    total = sum(gray.data) or 1e-9
    cx = sum(gray.data[y * w + x] * (x + 0.5) for y in range(h) for x in range(w)) / total / w
    cy = sum(gray.data[y * w + x] * (y + 0.5) for y in range(h) for x in range(w)) / total / h
    left = sum(gray.data[y * w + x] for y in range(h) for x in range(w // 2))
    right = sum(gray.data[y * w + x] for y in range(h) for x in range(w // 2, w))
    top = sum(gray.data[y * w + x] for y in range(h // 2) for x in range(w))
    bottom = sum(gray.data[y * w + x] for y in range(h // 2, h) for x in range(w))
    return {
        "grid": [[cell["mean"] for cell in row] for row in grid],
        "mass_center": [round(cx, 3), round(cy, 3)],
        "left_right": round((right - left) / max(total, 1e-9), 3),
        "top_bottom": round((bottom - top) / max(total, 1e-9), 3),
        "center_weighted": bool(abs(cx - 0.5) < 0.08 and abs(cy - 0.5) < 0.08),
    }


def empty_regions(gray: Gray, cols: int = 4, rows: int = 4, std_threshold: float = 0.02) -> Dict[str, Any]:
    """Cells with almost no luminance variation (flat sky, black void, blank wall)."""
    grid = region_grid(gray, cols, rows)
    flat = []
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell["std"] < std_threshold:
                flat.append({"row": r, "col": c, "mean": cell["mean"], "std": cell["std"]})
    total = cols * rows
    return {"cells": flat, "empty_pct": round(100.0 * len(flat) / total, 1), "grid": [cols, rows]}


def horizon_estimate(gray: Gray) -> Dict[str, Any]:
    """Row with the strongest horizontal edge energy (mean |d/dy| across the row).

    Returns ``row_fraction`` (0 top .. 1 bottom), the ``strength`` relative to the
    average row and ``confident`` when it stands well clear of the rest.
    """
    w, h = gray.width, gray.height
    if h < 3:
        return {"row_fraction": None, "strength": 0.0, "confident": False}
    energy: List[float] = []
    d = gray.data
    for y in range(h - 1):
        a = y * w
        b = a + w
        energy.append(sum(abs(d[b + x] - d[a + x]) for x in range(w)) / w)
    peak = max(range(len(energy)), key=lambda i: energy[i])
    avg = sum(energy) / len(energy) or 1e-9
    strength = energy[peak] / avg if avg > 1e-9 else 0.0
    return {
        "row_fraction": round((peak + 1) / float(h), 3),
        "strength": round(strength, 2),
        "confident": bool(strength > 4.0 and energy[peak] > 0.02),
    }


def edge_energy(gray: Gray) -> Dict[str, float]:
    """Mean gradient magnitude split into horizontal edges (d/dy) and vertical edges (d/dx)."""
    w, h = gray.width, gray.height
    d = gray.data
    if w < 2 or h < 2:
        return {"horizontal": 0.0, "vertical": 0.0}
    horiz = sum(abs(d[(y + 1) * w + x] - d[y * w + x]) for y in range(h - 1) for x in range(w)) / ((h - 1) * w)
    vert = sum(abs(d[y * w + x + 1] - d[y * w + x]) for y in range(h) for x in range(w - 1)) / (h * (w - 1))
    return {"horizontal": round(horiz, 5), "vertical": round(vert, 5)}


def dominant_colours(img: Image.Image, count: int = 5) -> List[Dict[str, Any]]:
    """Palette from Pillow's quantizer, biggest share first, as RGB and hex."""
    small = downsample(load_image(img), 128)
    count = max(1, min(int(count), 32))
    q = small.quantize(colors=count, method=Image.Quantize.MEDIANCUT if hasattr(Image, "Quantize") else 0)
    palette = q.getpalette() or []
    colours = q.getcolors(maxcolors=count * 4) or []
    total = sum(n for n, _ in colours) or 1
    out = []
    for n, idx in sorted(colours, reverse=True)[:count]:
        rgb = palette[idx * 3:idx * 3 + 3]
        if len(rgb) < 3:
            continue
        out.append({"rgb": [int(v) for v in rgb], "hex": "#%02x%02x%02x" % tuple(int(v) for v in rgb), "share": round(n / total, 3)})
    return out


# exif ----------------------------------------------------------------------
def exif_info(source: Any) -> Dict[str, Any]:
    """Focal length, 35mm equivalent, orientation, camera and exposure from EXIF (missing keys are None)."""
    out: Dict[str, Any] = {k: None for k in EXIF_TAGS.values()}
    out["sensor_width_guess_mm"] = None
    try:
        img = source if isinstance(source, Image.Image) else Image.open(str(source))
        raw = img.getexif()
        exif = dict(raw)
        try:  # focal length and friends live in the Exif IFD on newer Pillow
            exif.update(dict(raw.get_ifd(0x8769)))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        return out
    for tag, key in EXIF_TAGS.items():
        if tag in exif:
            out[key] = _plain(exif[tag])
    if out["focal_length_mm"] and out["focal_length_35mm"]:
        crop = float(out["focal_length_35mm"]) / float(out["focal_length_mm"])
        out["sensor_width_guess_mm"] = round(36.0 / crop, 2) if crop > 0 else None
    return out


def _plain(value: Any) -> Any:
    try:
        if hasattr(value, "numerator") and hasattr(value, "denominator") and not isinstance(value, int):
            return round(float(value.numerator) / float(value.denominator), 4) if value.denominator else None
        if isinstance(value, bytes):
            return value.decode("utf-8", "ignore").strip("\x00")
        if isinstance(value, (tuple, list)):
            return [_plain(v) for v in value]
        if isinstance(value, float):
            return round(value, 4)
        return value if isinstance(value, (int, str)) else str(value)
    except Exception:  # noqa: BLE001
        return None


# whole image analysis -------------------------------------------------------
def analyze(source: Any, kind: str = "previs") -> Dict[str, Any]:
    """All metrics for one image plus plain language findings with Maya fixes."""
    img = load_image(source)
    gray = gray_of(img)
    metrics: Dict[str, Any] = {
        "image": image_meta(img),
        "luminance": luminance_stats(gray),
        "clipping": clipping(gray),
        "rms_contrast": rms_contrast(gray),
        "colour": colour_cast(img),
        "sharpness": sharpness(gray),
        "thirds": thirds_balance(gray),
        "empty": empty_regions(gray),
        "horizon": horizon_estimate(gray),
        "histogram": histogram(img, 8),
        "dominant_colours": dominant_colours(img, 5),
    }
    metrics["findings"] = findings_for(metrics, kind)
    metrics["score"] = score_for(metrics["findings"])
    return metrics


def findings_for(m: Dict[str, Any], kind: str = "previs") -> List[Dict[str, Any]]:
    """Turn metrics into ranked findings; each carries a concrete Maya fix."""
    out: List[Dict[str, Any]] = []
    lum = m["luminance"]
    clip = m["clipping"]
    col = m["colour"]

    def add(severity: str, issue: str, measure: str, tool: str, change: str) -> None:
        out.append({"severity": severity, "issue": issue, "measure": measure, "fix": {"tool": tool, "change": change}})

    if clip["highlights_pct"] > 8.0:
        add("high", "Highlights are blown out over a large area", "%.1f%% of pixels at or above %.0f%% white" % (clip["highlights_pct"], HIGHLIGHT_CLIP * 100),
            "maya_light_exposure", "lower camera exposure by about %.1f stops, or drop the sky/key intensity; check with maya_critique_analyze again" % min(2.0, clip["highlights_pct"] / 10.0 + 0.5))
    elif clip["highlights_pct"] > 2.0:
        add("medium", "Some highlights are clipping", "%.1f%% of pixels blown" % clip["highlights_pct"],
            "maya_light_exposure", "lower exposure by 0.5 stop or reduce the brightest light's exposure attribute by 0.5")
    if clip["shadows_pct"] > 25.0:
        add("high", "Shadows are crushed to black", "%.1f%% of pixels at or below %.0f%% grey" % (clip["shadows_pct"], SHADOW_CLIP * 100),
            "maya_light_three_point", "raise fill (fill ratio from -3 to -2 stops) or add a maya_light_hdri_dome at low intensity for ambient bounce")
    elif clip["shadows_pct"] > 8.0:
        add("medium", "Shadows are heavy", "%.1f%% of pixels near black" % clip["shadows_pct"],
            "maya_light_three_point", "raise the fill light by 1 stop or add a bounce card (aiAreaLight, low exposure) opposite the key")
    if lum["mean"] < 0.18 and clip["shadows_pct"] <= 25.0:
        add("medium", "Image is dark overall", "mean luminance %.2f, a lit scene usually sits 0.30 to 0.55" % lum["mean"],
            "maya_light_exposure", "raise camera exposure by about %.1f stops" % min(3.0, max(0.5, math.log2(0.4 / max(lum["mean"], 0.01)))))
    elif lum["mean"] > 0.75 and clip["highlights_pct"] <= 8.0:
        add("medium", "Image is bright and flat", "mean luminance %.2f" % lum["mean"],
            "maya_light_exposure", "lower exposure by 1 stop and add a darker foreground element or a rim light for shape")
    if m["rms_contrast"] < 0.08:
        add("medium", "Low contrast, the image reads flat", "RMS contrast %.3f (0.15 to 0.25 is punchy)" % m["rms_contrast"],
            "maya_light_three_point", "increase key to fill ratio to 3 stops, or use maya_lookdev_color_management with an ACES view transform")
    elif m["rms_contrast"] > 0.32:
        add("low", "Very high contrast", "RMS contrast %.3f" % m["rms_contrast"],
            "maya_light_three_point", "soften the key (larger area light) and bring the fill up 1 stop")
    if col["strength"] > 18.0:
        add("medium", "Strong %s colour cast" % col["cast"], "mean RGB %s, drift %s" % (col["mean_rgb"], col["drift_rgb"]),
            "maya_light_practical", "neutralise the key light colour temperature (5600K daylight, 3200K tungsten) or set the intended white balance in maya_lookdev_color_management")
    if m["sharpness"] < 0.0003 and m["empty"]["empty_pct"] < 90.0:
        add("medium", "Image is soft or lacks surface detail", "Laplacian variance %.6f" % m["sharpness"],
            "maya_lookdev_measured_material", "add procedural break-up and roughness variation, raise AA samples with maya_lookdev_render_preset production, check depth of field on the camera")
    if m["empty"]["empty_pct"] >= 50.0:
        cells = m["empty"]["cells"]
        where = "top" if cells and sum(c["row"] for c in cells) / len(cells) < 1.5 else "bottom"
        add("medium", "Large flat areas with nothing in them", "%.0f%% of the 4x4 grid is empty, mostly %s" % (m["empty"]["empty_pct"], where),
            "maya_frame_selected", "reframe tighter on the subject with maya_frame_selected, add set dressing (maya_procgen_scatter) or an HDRI with detail in the sky")
    thirds = m["thirds"]
    if abs(thirds["left_right"]) > 0.35:
        side = "right" if thirds["left_right"] > 0 else "left"
        add("low", "Light mass sits heavily on the %s" % side, "left/right balance %.2f" % thirds["left_right"],
            "maya_set_camera_lens", "pan the camera or move the key light so the bright mass lands on a thirds line; use maya_transform on the camera")
    if thirds["center_weighted"] and kind in ("previs", "arch"):
        add("low", "Subject is dead centre", "mass center %s" % thirds["mass_center"],
            "maya_transform", "offset the camera so the subject sits on a thirds intersection (mass_center near 0.33 or 0.67)")
    hz = m["horizon"]
    if hz.get("confident") and hz["row_fraction"] is not None and abs(hz["row_fraction"] - 0.5) < 0.04 and kind != "lookdev":
        add("low", "Horizon splits the frame in half", "strong horizontal edge at %.0f%% height" % (hz["row_fraction"] * 100),
            "maya_transform", "tilt the camera so the horizon lands near 33%% or 67%% of frame height")
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: order[f["severity"]])
    return out


def score_for(findings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """0..100 where 100 is clean; high findings cost 25, medium 10, low 3."""
    cost = {"high": 25, "medium": 10, "low": 3}
    score = max(0, 100 - sum(cost.get(f["severity"], 0) for f in findings))
    return {"value": score, "high": sum(1 for f in findings if f["severity"] == "high"), "medium": sum(1 for f in findings if f["severity"] == "medium"), "low": sum(1 for f in findings if f["severity"] == "low")}


# comparison ------------------------------------------------------------------
def compare(render: Any, reference: Any, cols: int = 4, rows: int = 4) -> Dict[str, Any]:
    """Compare a render with a reference photo at the same working size.

    Returns a per region luminance error grid, an edge map difference, a
    histogram distance (0 identical .. 2 disjoint), both horizon estimates and
    a plain language list of what differs.
    """
    a_img = load_image(render)
    b_img = load_image(reference)
    size = _common_size(a_img.size, b_img.size)
    a_small = a_img.resize(size, Image.BILINEAR)
    b_small = b_img.resize(size, Image.BILINEAR)
    a = gray_of(a_small, max(size))
    b = gray_of(b_small, max(size))
    grid_a = region_grid(a, cols, rows)
    grid_b = region_grid(b, cols, rows)
    error = [[round(abs(ca["mean"] - cb["mean"]), 4) for ca, cb in zip(ra, rb)] for ra, rb in zip(grid_a, grid_b)]
    worst = sorted(((error[r][c], r, c) for r in range(rows) for c in range(cols)), reverse=True)[:3]
    hist_a = histogram(a_small, 16)
    hist_b = histogram(b_small, 16)
    hist_dist = {k: round(sum(abs(x - y) for x, y in zip(hist_a[k], hist_b[k])), 4) for k in ("luma", "r", "g", "b")}
    edges_a = edge_energy(a)
    edges_b = edge_energy(b)
    edge_diff = round(abs(edges_a["horizontal"] + edges_a["vertical"] - edges_b["horizontal"] - edges_b["vertical"]), 5)
    hz_a = horizon_estimate(a)
    hz_b = horizon_estimate(b)
    lum_a = luminance_stats(a)
    lum_b = luminance_stats(b)
    col_a = colour_cast(a_small)
    col_b = colour_cast(b_small)
    differences: List[Dict[str, Any]] = []
    d_mean = lum_a["mean"] - lum_b["mean"]
    if abs(d_mean) > 0.08:
        stops = math.log2(max(lum_a["mean"], 0.01) / max(lum_b["mean"], 0.01))
        differences.append({"what": "overall brightness", "measure": "render mean %.2f vs reference %.2f (%.1f stops)" % (lum_a["mean"], lum_b["mean"], stops),
                            "fix": {"tool": "maya_light_exposure", "change": "%s exposure by %.1f stops" % ("lower" if d_mean > 0 else "raise", abs(stops))}})
    if abs(lum_a["std"] - lum_b["std"]) > 0.05:
        differences.append({"what": "contrast", "measure": "render RMS %.3f vs reference %.3f" % (lum_a["std"], lum_b["std"]),
                            "fix": {"tool": "maya_light_three_point", "change": "%s the key to fill ratio" % ("reduce" if lum_a["std"] > lum_b["std"] else "increase")}})
    if abs(col_a["warmth"] - col_b["warmth"]) > 0.06:
        differences.append({"what": "colour temperature", "measure": "render warmth %.2f vs reference %.2f" % (col_a["warmth"], col_b["warmth"]),
                            "fix": {"tool": "maya_light_practical", "change": "%s the key light colour temperature" % ("cool" if col_a["warmth"] > col_b["warmth"] else "warm")}})
    if hz_a.get("confident") and hz_b.get("confident") and hz_a["row_fraction"] is not None and hz_b["row_fraction"] is not None and abs(hz_a["row_fraction"] - hz_b["row_fraction"]) > 0.05:
        differences.append({"what": "horizon height", "measure": "render %.0f%% vs reference %.0f%% of frame height" % (hz_a["row_fraction"] * 100, hz_b["row_fraction"] * 100),
                            "fix": {"tool": "maya_transform", "change": "tilt the camera %s so the horizon matches (a horizon too high in frame means the camera looks down too much)" % ("up" if hz_a["row_fraction"] < hz_b["row_fraction"] else "down")}})
    if edge_diff > 0.01:
        differences.append({"what": "amount of detail", "measure": "edge energy render %.3f vs reference %.3f" % (edges_a["horizontal"] + edges_a["vertical"], edges_b["horizontal"] + edges_b["vertical"]),
                            "fix": {"tool": "maya_lookdev_measured_material", "change": "%s surface detail (break-up textures, wear, set dressing)" % ("reduce" if edge_diff > 0 and (edges_a["horizontal"] + edges_a["vertical"]) > (edges_b["horizontal"] + edges_b["vertical"]) else "add")}})
    for err, r, c in worst:
        if err > 0.15:
            differences.append({"what": "region %d,%d brightness" % (r, c), "measure": "luminance differs by %.2f (render %.2f vs reference %.2f)" % (err, grid_a[r][c]["mean"], grid_b[r][c]["mean"]),
                                "fix": {"tool": "maya_create_arnold_light", "change": "add or dim a light aimed at that part of the frame (row %d of %d from top, col %d of %d from left)" % (r + 1, rows, c + 1, cols)}})
    return {
        "size": list(size),
        "region_error": error,
        "worst_regions": [{"row": r, "col": c, "error": e} for e, r, c in worst],
        "mean_region_error": round(sum(sum(row) for row in error) / (rows * cols), 4),
        "histogram_distance": hist_dist,
        "edge_energy": {"render": edges_a, "reference": edges_b, "difference": edge_diff},
        "horizon": {"render": hz_a, "reference": hz_b},
        "luminance": {"render": lum_a, "reference": lum_b},
        "colour": {"render": col_a, "reference": col_b},
        "differences": differences,
        "similarity": round(max(0.0, 1.0 - hist_dist["luma"] / 2.0 - sum(sum(row) for row in error) / (rows * cols)), 3),
    }


def _common_size(a: Tuple[int, int], b: Tuple[int, int], max_side: int = WORK_SIZE) -> Tuple[int, int]:
    """Working size that keeps the reference aspect, capped at max_side."""
    w, h = b
    scale = max(w, h) / float(max_side)
    if scale > 1.0:
        w, h = int(round(w / scale)), int(round(h / scale))
    return max(2, w), max(2, h)


# photo helpers --------------------------------------------------------------
def time_of_day_guess(cast: Dict[str, Any], lum: Dict[str, float]) -> Dict[str, Any]:
    """Rough time of day from warmth and brightness. A hint, not a measurement."""
    warmth = cast.get("warmth", 0.0)
    mean = lum.get("mean", 0.5)
    if mean < 0.15:
        label, kelvin = "night", 4000
    elif warmth > 0.12:
        label, kelvin = "golden hour or tungsten interior", 3200
    elif warmth < -0.06:
        label, kelvin = "blue hour or overcast", 7500
    elif mean > 0.6:
        label, kelvin = "midday daylight", 5600
    else:
        label, kelvin = "daylight", 5600
    return {"label": label, "kelvin_hint": kelvin, "warmth": warmth, "mean_luminance": mean}


def vanishing_hint(gray: Gray) -> Dict[str, Any]:
    """Whether the strong edges are mostly horizontal, vertical or mixed (oblique view)."""
    e = edge_energy(gray)
    total = e["horizontal"] + e["vertical"] or 1e-9
    ratio = e["horizontal"] / total
    if ratio > 0.62:
        hint = "frontal view, horizontal lines dominate (one point perspective likely)"
    elif ratio < 0.38:
        hint = "verticals dominate, tall subject or tilted camera"
    else:
        hint = "mixed edges, oblique view (two point perspective likely)"
    return {"horizontal_share": round(ratio, 3), "edge_energy": e, "hint": hint}


def depth_rows(source: Any, max_size: int = 128, invert: bool = False) -> Dict[str, Any]:
    """Downsample a depth map to <= max_size x max_size rows of floats 0..1 for the plugin."""
    img = load_image(source)
    small = downsample(img, max_size)
    gray = gray_of(small, max_size)
    lo, hi = min(gray.data), max(gray.data)
    span = (hi - lo) or 1.0
    rows: List[List[float]] = []
    for y in range(gray.height):
        row = [(v - lo) / span for v in gray.row(y)]
        if invert:
            row = [1.0 - v for v in row]
        rows.append([round(v, 4) for v in row])
    return {"columns": gray.width, "rows": gray.height, "data": rows, "source_size": list(img.size)}


def png_bytes(img: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
