"""Photo to scene tools: read a photo server side (Pillow), then ask the plugin
for a matched camera, a first block, or a depth relief. Maya never needs
Pillow; it only receives numbers."""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import imaging
from ._base import READ, WRITE, ToolContext, dumps, error_text

DEPTH_ENV = "DEPTH_ENDPOINT"
STYLES = ("flat", "brick", "glass", "classical")


class InspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Photo to inspect (jpg/png/tif/heic if Pillow can open it)", examples=["/ref/street.jpg"])
    colours: int = Field(default=5, ge=1, le=16, description="How many dominant colours to return")


class CameraMatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Photo to match", examples=["/ref/street.jpg"])
    name: str = Field(default="photoCam", description="Camera name", max_length=80)
    focal_length: float | None = Field(default=None, gt=0, description="35mm equivalent focal in mm. Default: EXIF 35mm value, else EXIF focal scaled by the sensor guess, else 35")
    sensor_width: float = Field(default=36.0, gt=0, description="Film back width in mm for the Maya camera (36 = full frame)")
    depth: float = Field(default=100.0, gt=0, description="Image plane distance from the camera in scene units")
    alpha: float = Field(default=1.0, ge=0, le=1, description="Image plane opacity")
    group: bool = Field(default=True, description="Put the camera in a <name>_match_grp group")
    lock: bool = Field(default=True, description="Lock the match group's transform so the match survives layout")


class BlockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, description="Photo the block is for (used for the note and colour hints); optional")
    width: float = Field(..., gt=0, description="Subject width in cm (known or estimated, e.g. a 12 m facade is 1200)", examples=[1200])
    depth: float = Field(..., gt=0, description="Subject depth in cm", examples=[900])
    height: float | None = Field(default=None, gt=0, description="Subject height in cm; or give floors")
    floors: int | None = Field(default=None, ge=1, le=200, description="Storeys, used with floor_height when height is unknown")
    floor_height: float = Field(default=320.0, gt=0, description="Storey height in cm")
    name: str = Field(default="photoBlock", description="Node name", max_length=80)
    style: str = Field(default="flat", description="flat | brick | glass | classical when procgen.building is available")
    camera: str | None = Field(default=None, description="Matched camera; the block is placed on its forward axis")
    distance: float | None = Field(default=None, gt=0, description="Distance from the camera to the block in cm (default 1000)")


class DepthReliefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    depth_path: str | None = Field(default=None, description="Depth map image (bright = near by default). Omit to try the DEPTH_ENDPOINT provider on image_path")
    image_path: str | None = Field(default=None, description="Source photo, sent to DEPTH_ENDPOINT when depth_path is missing")
    width: float = Field(default=1000.0, gt=0, description="Relief width in cm")
    depth: float | None = Field(default=None, gt=0, description="Relief depth in cm; default keeps the image aspect")
    height: float = Field(default=100.0, description="Displacement amplitude in cm (negative flips it)")
    resolution: int = Field(default=64, ge=2, le=128, description="Samples on the long side (max 128)")
    invert: bool = Field(default=False, description="Treat dark as near instead of bright")
    name: str = Field(default="photoRelief", description="Node name", max_length=80)
    base_y: float = Field(default=0.0, description="Y of the relief base")


def inspect_photo(path: str, colours: int = 5) -> Dict[str, Any]:
    """Everything the agent needs from a photo before building: dims, EXIF, palette, horizon, light."""
    img = imaging.load_image(path)
    gray = imaging.gray_of(img)
    exif = imaging.exif_info(path)
    lum = imaging.luminance_stats(gray)
    cast = imaging.colour_cast(img)
    focal_35 = exif.get("focal_length_35mm")
    if not focal_35 and exif.get("focal_length_mm") and exif.get("sensor_width_guess_mm"):
        focal_35 = round(float(exif["focal_length_mm"]) * 36.0 / float(exif["sensor_width_guess_mm"]), 1)
    return {
        "path": path,
        "image": imaging.image_meta(img),
        "exif": exif,
        "focal_length_35mm_equiv": focal_35,
        "dominant_colours": imaging.dominant_colours(img, colours),
        "horizon": imaging.horizon_estimate(gray),
        "vanishing": imaging.vanishing_hint(gray),
        "luminance": lum,
        "colour": cast,
        "time_of_day": imaging.time_of_day_guess(cast, lum),
        "suggested_camera": {
            "focal_length": focal_35 or 35.0,
            "sensor_width": 36.0,
            "aspect": imaging.image_meta(img)["aspect"],
            "note": "focal is a 35mm equivalent" if focal_35 else "no EXIF focal; 35mm is a guess, refine by matching verticals",
        },
    }


async def fetch_depth_map(image_path: str) -> str:
    """POST the photo to DEPTH_ENDPOINT and save the returned depth image. Raises with a clear message."""
    endpoint = os.environ.get(DEPTH_ENV, "").strip()
    if not endpoint:
        raise RuntimeError("no depth_path given and %s is not set; run a local Depth Anything or MiDaS server and export %s=http://host:port/depth" % (DEPTH_ENV, DEPTH_ENV))
    import httpx

    with open(image_path, "rb") as fh:
        payload = fh.read()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(endpoint, files={"image": (os.path.basename(image_path), payload, "application/octet-stream")})
    if resp.status_code >= 400:
        raise RuntimeError("%s returned HTTP %d: %s" % (DEPTH_ENV, resp.status_code, resp.text[:200]))
    folder = os.path.join(tempfile.gettempdir(), "automaya")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "depth_%d.png" % int(time.time() * 1000))
    with open(out, "wb") as fh:
        fh.write(resp.content)
    return out


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_photo_inspect", annotations={"title": "Inspect a photo", **READ})
    async def maya_photo_inspect(params: InspectInput) -> str:
        """Read a photo before building from it: dimensions and orientation, EXIF focal
        length (plus 35mm equivalent and a sensor guess), dominant colours, horizon
        height, whether edges suggest a frontal or oblique view, luminance, colour cast
        and a time of day guess with a Kelvin hint. Returns a suggested camera block."""
        if not os.path.isfile(params.path):
            return "Error: photo not found: %s" % params.path
        try:
            return dumps(inspect_photo(params.path, params.colours))
        except Exception as exc:  # noqa: BLE001
            return "Error: could not read %s: %s" % (params.path, exc)

    @mcp.tool(name="maya_photo_camera_match", annotations={"title": "Camera matched to a photo", **WRITE})
    async def maya_photo_camera_match(params: CameraMatchInput) -> str:
        """Create a Maya camera whose aspect, film back and focal length match the photo,
        with the photo on an image plane fitted horizontally, inside a locked match
        group. Focal comes from the parameter, else EXIF, else 35mm. Then block
        geometry behind the plane (maya_photo_block) and iterate with
        maya_critique_compare."""
        if not os.path.isfile(params.path):
            return "Error: photo not found: %s" % params.path
        try:
            info = inspect_photo(params.path, 3)
        except Exception as exc:  # noqa: BLE001
            return "Error: could not read %s: %s" % (params.path, exc)
        focal = params.focal_length or info["focal_length_35mm_equiv"]
        payload = {
            "path": os.path.abspath(params.path),
            "image_width": info["image"]["width"],
            "image_height": info["image"]["height"],
            "name": params.name,
            "focal_length": float(focal) if focal else None,
            "sensor_width": params.sensor_width,
            "depth": params.depth,
            "alpha": params.alpha,
            "group": params.group,
            "lock": params.lock,
        }
        try:
            result = await ctx.raw("photo.camera_from_photo", payload, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            return error_text(exc)
        if isinstance(result, dict):
            result["photo"] = {"horizon": info["horizon"], "time_of_day": info["time_of_day"], "exif_focal_35mm": info["focal_length_35mm_equiv"]}
        return dumps(result)

    @mcp.tool(name="maya_photo_block", annotations={"title": "First block from a photo", **WRITE})
    async def maya_photo_block(params: BlockInput) -> str:
        """Build the first blocking volume for the photographed subject at real scale
        (cm), pivot at the base, using procgen.building when the plugin has it (facade
        detail from floors and style) or a plain cube otherwise. With camera set, the
        block lands on the camera's forward axis at `distance` so it sits behind the
        image plane. Follow with maya_critique_compare against the photo."""
        if params.style not in STYLES:
            return "Error: style must be one of %s" % ", ".join(STYLES)
        if params.height is None and params.floors is None:
            return "Error: give height (cm) or floors"
        if params.path and not os.path.isfile(params.path):
            return "Error: photo not found: %s" % params.path
        payload = params.model_dump(exclude={"path"})
        return await ctx.run("photo.block_from_photo", payload, timeout=120.0)

    @mcp.tool(name="maya_photo_depth_relief", annotations={"title": "Depth relief from a depth map", **WRITE})
    async def maya_photo_depth_relief(params: DepthReliefInput) -> str:
        """Turn a depth map into a displaced plane for background layout: the map is
        downsampled server side (up to 128 x 128 samples) and the plugin moves the
        vertices. Give depth_path (any greyscale image, bright = near) or, when the
        DEPTH_ENDPOINT env var points at a local depth model server, image_path
        alone. Never blocks when no provider exists; it tells you what to set."""
        depth_path = params.depth_path
        if not depth_path:
            if not params.image_path:
                return "Error: give depth_path (a depth image) or image_path with %s set" % DEPTH_ENV
            if not os.path.isfile(params.image_path):
                return "Error: image not found: %s" % params.image_path
            try:
                depth_path = await fetch_depth_map(params.image_path)
            except Exception as exc:  # noqa: BLE001
                return "Error: %s" % exc
        if not os.path.isfile(depth_path):
            return "Error: depth map not found: %s" % depth_path
        try:
            grid = imaging.depth_rows(depth_path, params.resolution, params.invert)
        except Exception as exc:  # noqa: BLE001
            return "Error: could not read depth map %s: %s" % (depth_path, exc)
        payload = {"rows": grid["data"], "width": params.width, "depth": params.depth, "height": params.height, "name": params.name, "base_y": params.base_y}
        try:
            result = await ctx.raw("photo.depth_relief", payload, timeout=300.0)
        except Exception as exc:  # noqa: BLE001
            return error_text(exc)
        if isinstance(result, dict):
            result["depth_map"] = {"path": depth_path, "samples": [grid["columns"], grid["rows"]], "source_size": grid["source_size"]}
        return dumps(result)
