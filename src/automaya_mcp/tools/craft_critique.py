"""Critique tools: measure a render or screenshot and say, in plain words with
numbers, what is wrong and which Maya tool fixes it. Runs server side with
Pillow; Maya is only asked for the pixels."""
from __future__ import annotations

import base64
import os
import tempfile
import time
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, ConfigDict, Field

from .. import imaging
from ._base import READ, ToolContext, dumps, error_text

KINDS = ("previs", "lookdev", "arch")
SOURCES = ("viewport", "arnold")

CHECKLISTS: Dict[str, List[Dict[str, str]]] = {
    "lookdev": [
        {"item": "Exposure", "target": "mean luminance 0.30 to 0.55, highlights clipped under 2%, shadows crushed under 8%", "tool": "maya_light_exposure"},
        {"item": "Material plausibility", "target": "albedo under 0.9, metals with black base colour, dielectrics roughness above 0.05", "tool": "maya_lookdev_material_report"},
        {"item": "Surface detail", "target": "Laplacian variance above 0.0005 on the subject, break-up in roughness and colour", "tool": "maya_lookdev_measured_material"},
        {"item": "Colour management", "target": "ACES view transform, no unintended cast (drift under 12)", "tool": "maya_lookdev_color_management"},
        {"item": "Key to fill", "target": "RMS contrast 0.15 to 0.28, shadows readable", "tool": "maya_light_three_point"},
        {"item": "Render quality", "target": "no visible noise at review size, AA samples per maya_lookdev_render_preset production", "tool": "maya_lookdev_render_preset"},
    ],
    "previs": [
        {"item": "Scale", "target": "real world units, doors 210 cm, people 170 cm, bounding boxes checked", "tool": "maya_get_bounding_box"},
        {"item": "Camera", "target": "real sensor and focal, aspect matches delivery, horizon not centred unless intended", "tool": "maya_create_camera"},
        {"item": "Composition", "target": "subject on a thirds intersection, under 50% empty cells, readable silhouette", "tool": "maya_frame_selected"},
        {"item": "Readability", "target": "mean luminance 0.25 to 0.6, subject separated from background by value", "tool": "maya_light_three_point"},
        {"item": "Continuity", "target": "shot registered in the camera sequencer with the right range", "tool": "maya_create_sequence_shot"},
        {"item": "Hygiene", "target": "maya_find_problems clean: no unfrozen transforms, no far from origin nodes, no duplicate names", "tool": "maya_find_problems"},
    ],
    "arch": [
        {"item": "Verticals", "target": "vertical edges parallel (two point perspective) unless a deliberate tilt", "tool": "maya_set_camera_lens"},
        {"item": "Eye height", "target": "camera 150 to 170 cm off the floor for street level, horizon in the lower third", "tool": "maya_transform"},
        {"item": "Sun", "target": "physically plausible sun elevation and azimuth for the site and time", "tool": "maya_light_sun_sky"},
        {"item": "Materials", "target": "measured albedo for concrete, brick, glass; glass with IOR 1.5 and thin walls", "tool": "maya_lookdev_measured_material"},
        {"item": "Exposure", "target": "sky not blown (highlights under 3%), shadow side readable (shadows under 10%)", "tool": "maya_light_exposure"},
        {"item": "Context", "target": "ground plane, entourage proxies, neighbouring blocks so scale reads", "tool": "maya_procgen_street_block"},
    ],
}


class AnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, description="Image file to analyse (png/jpg/tif/exr converted by Pillow). Omit to capture from Maya", examples=["/renders/sh010_v003.png"])
    use_last_render: bool = Field(default=False, description="Capture a fresh image from Maya instead of reading a file: arnold.render_frame or a viewport screenshot per `source`")
    source: str = Field(default="viewport", description="Where to capture from when use_last_render is true: viewport (fast) or arnold (real render)")
    camera: str | None = Field(default=None, description="Camera to capture through; default the active view or the renderable camera")
    width: int = Field(default=1280, ge=64, le=4096, description="Capture width when capturing from Maya")
    height: int = Field(default=720, ge=64, le=4096, description="Capture height when capturing from Maya")
    kind: str = Field(default="previs", description="Review rubric: previs | lookdev | arch. Changes which composition findings matter")


class CompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_path: str = Field(..., description="Reference photo or concept image", examples=["/ref/plate.jpg"])
    render_path: str | None = Field(default=None, description="Render to compare. Omit to reuse the last capture or grab a fresh one")
    use_last_render: bool = Field(default=False, description="Capture a fresh image from Maya per `source` when render_path is omitted")
    source: str = Field(default="viewport", description="viewport | arnold when capturing")
    camera: str | None = Field(default=None, description="Camera to capture through")
    width: int = Field(default=1280, ge=64, le=4096, description="Capture width")
    height: int = Field(default=720, ge=64, le=4096, description="Capture height")
    grid: int = Field(default=4, ge=2, le=12, description="Region grid size for the error map (grid x grid)")


class RenderCritiqueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to render; default the renderable camera")
    width: int = Field(default=960, ge=64, le=4096, description="Render width")
    height: int = Field(default=540, ge=64, le=4096, description="Render height")
    source: str = Field(default="arnold", description="arnold (real render) or viewport (screenshot)")
    kind: str = Field(default="lookdev", description="previs | lookdev | arch rubric")
    return_image: bool = Field(default=True, description="Also return the rendered image so you can look at it")


class ChecklistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="previs", description="previs | lookdev | arch")


def temp_image_path(prefix: str = "critique") -> str:
    folder = os.path.join(tempfile.gettempdir(), "automaya")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "%s_%d.png" % (prefix, int(time.time() * 1000)))


async def capture(ctx: ToolContext, source: str, camera: str | None, width: int, height: int) -> Dict[str, Any]:
    """Ask Maya for pixels and write them to a temp PNG. Returns {path, meta, data}."""
    if source not in SOURCES:
        raise ValueError("source must be one of %s" % ", ".join(SOURCES))
    if source == "arnold":
        result = await ctx.raw("arnold.render_frame", {"camera": camera, "width": width, "height": height}, timeout=900.0)
    else:
        result = await ctx.raw("intel.viewport_screenshot", {"camera": camera, "width": width, "height": height}, timeout=60.0)
    if not isinstance(result, dict) or not result.get("image_base64"):
        raise ValueError("Maya returned no image (%s). %s" % (source, (result or {}).get("note", "") if isinstance(result, dict) else result))
    data = base64.b64decode(result["image_base64"])
    path = temp_image_path(source)
    with open(path, "wb") as fh:
        fh.write(data)
    meta = {k: v for k, v in result.items() if k != "image_base64"}
    meta["captured_path"] = path
    ctx.last_render_path = path  # type: ignore[attr-defined]
    return {"path": path, "meta": meta, "data": data, "format": result.get("format", "png")}


def _check_kind(kind: str) -> str | None:
    if kind not in KINDS:
        return "Error: kind must be one of %s" % ", ".join(KINDS)
    return None


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_critique_analyze", annotations={"title": "Critique a render or screenshot", **READ})
    async def maya_critique_analyze(params: AnalyzeInput) -> str:
        """Measure an image and critique it like a compositing supervisor: luminance
        stats, clipped shadows and highlights %, RMS contrast, colour cast, sharpness,
        thirds balance, empty regions, horizon, histogram and palette. Every finding
        says what is wrong, the number behind it and the Maya tool plus change that
        fixes it. Give a path, or set use_last_render to capture from Maya first."""
        bad = _check_kind(params.kind)
        if bad:
            return bad
        path = params.path
        meta: Dict[str, Any] = {}
        if not path:
            if not params.use_last_render:
                last = getattr(ctx, "last_render_path", None)
                if not last:
                    return "Error: give path or set use_last_render=true to capture from Maya"
                path = last
            else:
                try:
                    cap = await capture(ctx, params.source, params.camera, params.width, params.height)
                except Exception as exc:  # noqa: BLE001
                    return error_text(exc)
                path, meta = cap["path"], cap["meta"]
        if not os.path.isfile(path):
            return "Error: image not found: %s" % path
        try:
            result = imaging.analyze(path, params.kind)
        except Exception as exc:  # noqa: BLE001
            return "Error: could not analyse %s: %s" % (path, exc)
        result["path"] = path
        result["kind"] = params.kind
        if meta:
            result["capture"] = meta
        result["summary"] = _summary(result)
        return dumps(result)

    @mcp.tool(name="maya_critique_compare", annotations={"title": "Compare a render with a reference", **READ})
    async def maya_critique_compare(params: CompareInput) -> str:
        """Compare a render (file, last capture, or a fresh capture) with a reference
        photo: per region luminance error grid, histogram distance per channel, edge
        energy, horizon height for both and a list of what differs with the Maya fix
        for each. Use it in a loop: adjust, re-render, compare, until similarity
        stops improving."""
        if not os.path.isfile(params.reference_path):
            return "Error: reference not found: %s" % params.reference_path
        path = params.render_path
        meta: Dict[str, Any] = {}
        if not path:
            if params.use_last_render:
                try:
                    cap = await capture(ctx, params.source, params.camera, params.width, params.height)
                except Exception as exc:  # noqa: BLE001
                    return error_text(exc)
                path, meta = cap["path"], cap["meta"]
            else:
                path = getattr(ctx, "last_render_path", None)
                if not path:
                    return "Error: give render_path or set use_last_render=true"
        if not os.path.isfile(path):
            return "Error: render not found: %s" % path
        try:
            result = imaging.compare(path, params.reference_path, params.grid, params.grid)
        except Exception as exc:  # noqa: BLE001
            return "Error: could not compare: %s" % exc
        result["render_path"] = path
        result["reference_path"] = params.reference_path
        if meta:
            result["capture"] = meta
        result["summary"] = "similarity %.2f; %d differences, biggest: %s" % (
            result["similarity"], len(result["differences"]), result["differences"][0]["what"] if result["differences"] else "none")
        return dumps(result)

    @mcp.tool(name="maya_render_and_critique", annotations={"title": "Render then critique", **READ})
    async def maya_render_and_critique(params: RenderCritiqueInput):
        """Render one frame (Arnold, or a viewport screenshot) and run the critique on
        it in one call. Returns the image so you can look, plus the metrics and
        ranked findings with fixes. This is the inspect step of the build, render,
        critique, fix loop."""
        bad = _check_kind(params.kind)
        if bad:
            return bad
        try:
            cap = await capture(ctx, params.source, params.camera, params.width, params.height)
        except Exception as exc:  # noqa: BLE001
            return error_text(exc)
        try:
            result = imaging.analyze(cap["path"], params.kind)
        except Exception as exc:  # noqa: BLE001
            return "Error: rendered but could not analyse %s: %s" % (cap["path"], exc)
        result["path"] = cap["path"]
        result["kind"] = params.kind
        result["capture"] = cap["meta"]
        result["summary"] = _summary(result)
        result.pop("histogram", None)
        text = dumps(result)
        if params.return_image:
            return [Image(data=cap["data"], format=cap["format"]), text]
        return text

    @mcp.tool(name="maya_critique_checklist", annotations={"title": "Review checklist", **READ})
    async def maya_critique_checklist(params: ChecklistInput) -> str:
        """The review rubric for a kind of work (previs, lookdev, arch): each item with
        its numeric target and the tool that addresses it. Score your render against it
        before calling a shot done, and use maya_quality_gate to automate the check."""
        bad = _check_kind(params.kind)
        if bad:
            return bad
        return dumps({"kind": params.kind, "items": CHECKLISTS[params.kind], "score_with": "maya_critique_analyze and maya_find_problems", "pass_rule": "no high findings, at most two medium"})


def _summary(result: Dict[str, Any]) -> str:
    lum = result["luminance"]
    clip = result["clipping"]
    findings = result["findings"]
    head = "mean luminance %.2f, contrast %.2f, %.1f%% blown, %.1f%% crushed, %s cast, score %d/100" % (
        lum["mean"], result["rms_contrast"], clip["highlights_pct"], clip["shadows_pct"], result["colour"]["cast"], result["score"]["value"])
    if not findings:
        return head + ". Nothing to fix from the numbers; judge composition and story by eye."
    top = findings[0]
    return head + ". Top fix: %s (%s) via %s: %s" % (top["issue"], top["measure"], top["fix"]["tool"], top["fix"]["change"])
