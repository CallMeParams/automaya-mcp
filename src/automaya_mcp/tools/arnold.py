"""Arnold tools: status, lights, render settings, AOVs, per object attributes, rendering."""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import READ, WRITE, ToolContext

RENDER_TIMEOUT = 3600.0
SEQUENCE_TIMEOUT = 6 * 3600.0


class CreateLightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(default="area", description="area | skydome | mesh | photometric | distant | point | spot", pattern="^(area|skydome|mesh|photometric|distant|directional|point|spot)$")
    name: str | None = Field(default=None, description="Light name, e.g. 'keyLight'", max_length=200)
    intensity: float | None = Field(default=None, description="Light intensity (Arnold lights default to 1)", ge=0)
    exposure: float | None = Field(default=None, description="Exposure in stops; each +1 doubles the light", ge=-20, le=30)
    color: List[float] | None = Field(default=None, description="[r, g, b] in 0..1", min_length=3, max_length=3)
    translate: List[float] | None = Field(default=None, description="World position [x, y, z]", min_length=3, max_length=3)
    rotate: List[float] | None = Field(default=None, description="Rotation in degrees [x, y, z]", min_length=3, max_length=3)
    scale: List[float] | None = Field(default=None, description="Scale [x, y, z]; for area lights this is the emitter size", min_length=3, max_length=3)
    hdri_path: str | None = Field(default=None, description="For skydome: path to an .hdr/.exr, wired through a Raw file texture")
    samples: int | None = Field(default=None, description="Arnold light samples (aiSamples)", ge=0, le=64)
    cast_shadows: bool | None = Field(default=None, description="Whether the light casts shadows")
    mesh: str | None = Field(default=None, description="For mesh lights: the mesh transform or shape to emit from")


class RenderSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_aa: int | None = Field(default=None, description="Camera (AA) samples; 3 preview, 5-8 final", ge=1, le=64)
    diffuse: int | None = Field(default=None, description="Diffuse samples", ge=0, le=64)
    specular: int | None = Field(default=None, description="Specular samples", ge=0, le=64)
    transmission: int | None = Field(default=None, description="Transmission samples", ge=0, le=64)
    sss: int | None = Field(default=None, description="Subsurface samples", ge=0, le=64)
    volume: int | None = Field(default=None, description="Volume indirect samples", ge=0, le=64)
    adaptive: bool | None = Field(default=None, description="Enable adaptive sampling")
    max_aa: int | None = Field(default=None, description="Adaptive max camera samples", ge=1, le=128)
    threshold: float | None = Field(default=None, description="Adaptive threshold (0.015 default, lower is cleaner)", ge=0, le=1)
    denoiser: str | None = Field(default=None, description="none | oidn (Intel imager) | optix (NVIDIA, Arnold RenderView)", pattern="^(none|oidn|optix)$")
    width: int | None = Field(default=None, description="Image width in pixels", ge=1, le=16384)
    height: int | None = Field(default=None, description="Image height in pixels", ge=1, le=16384)
    start_frame: float | None = Field(default=None, description="First frame of the render range")
    end_frame: float | None = Field(default=None, description="Last frame of the render range")
    animation: bool | None = Field(default=None, description="Render a frame sequence (name.####.ext) instead of a single image")
    image_format: str | None = Field(default=None, description="exr | png | jpeg | tif", pattern="^(exr|png|jpe?g|tiff?|deepexr|maya)$")
    output_prefix: str | None = Field(default=None, description="Image file prefix, may include folders and <Scene>, <Camera>, <RenderLayer> tokens", examples=["shots/<Scene>/<Camera>/beauty"])
    motion_blur: bool | None = Field(default=None, description="Enable Arnold motion blur")
    camera: str | None = Field(default=None, description="Make this camera the only renderable one")
    ray_depth_total: int | None = Field(default=None, description="Total ray depth (GITotalDepth)", ge=0, le=64)


class RenderFrameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to render through; default is the renderable camera")
    width: int | None = Field(default=None, description="Override width for this render", ge=1, le=16384)
    height: int | None = Field(default=None, description="Override height for this render", ge=1, le=16384)
    frame: float | None = Field(default=None, description="Frame to render; default is the current time")
    output_path: str | None = Field(default=None, description="Copy the rendered image here; the extension (png/jpg/exr) also selects the driver format")
    timeout: float = Field(default=RENDER_TIMEOUT, description="Seconds to wait for the render", ge=10, le=SEQUENCE_TIMEOUT)


class RenderSequenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float | None = Field(default=None, description="First frame; default from render globals")
    end: float | None = Field(default=None, description="Last frame; default from render globals")
    camera: str | None = Field(default=None, description="Camera to render through")
    width: int | None = Field(default=None, ge=1, le=16384)
    height: int | None = Field(default=None, ge=1, le=16384)
    step: int = Field(default=1, description="Frame step", ge=1, le=1000)
    timeout: float = Field(default=SEQUENCE_TIMEOUT, description="Seconds to wait for the whole sequence", ge=10, le=24 * 3600)


class CreateAovInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="AOV name: diffuse, specular, coat, sss, emission, N, P, Z, motionvector, crypto_object, crypto_material, crypto_asset ...", min_length=1, examples=["diffuse", "Z", "crypto_object"])
    data_type: str | None = Field(default=None, description="rgb | rgba | float | vector | vector2 | int | uint | bool. Default is picked from the name (Z=float, N=vector, else rgb).", pattern="^(rgb|rgba|float|vector|vector2|int|uint|bool|string|pointer)$")
    enabled: bool = Field(default=True, description="Enable the AOV for rendering")


class SetAiAttributesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Transforms or mesh shapes; omit to use the selection")
    subdivision_type: str | None = Field(default=None, description="none | catclark | linear", pattern="^(none|catclark|linear)$")
    subdivision_iterations: int | None = Field(default=None, ge=0, le=10, description="Subdivision iterations (aiSubdivIterations)")
    subdivision_adaptive_error: float | None = Field(default=None, ge=0, description="Adaptive subdivision error in pixels (0 disables)")
    opaque: bool | None = Field(default=None, description="aiOpaque; turn off for cutout opacity maps")
    matte: bool | None = Field(default=None, description="aiMatte: render as a holdout")
    self_shadows: bool | None = Field(default=None, description="aiSelfShadows")
    cast_shadows: bool | None = Field(default=None, description="castsShadows")
    receive_shadows: bool | None = Field(default=None, description="receiveShadows")
    visible_in_camera: bool | None = Field(default=None, description="primaryVisibility")
    visible_in_diffuse_reflection: bool | None = Field(default=None)
    visible_in_specular_reflection: bool | None = Field(default=None)
    visible_in_diffuse_transmission: bool | None = Field(default=None)
    visible_in_specular_transmission: bool | None = Field(default=None)
    visible_in_volume: bool | None = Field(default=None)
    displacement_height: float | None = Field(default=None, description="aiDispHeight (scene units)")
    displacement_padding: float | None = Field(default=None, description="aiDispPadding, bounding box padding for displacement")
    displacement_zero_value: float | None = Field(default=None, description="aiDispZeroValue, 0.5 for mid grey height maps")
    displacement_autobump: bool | None = Field(default=None, description="aiDispAutobump")
    motion_blur: bool | None = Field(default=None, description="Per object motion blur toggle")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_arnold_status", annotations={"title": "Arnold (mtoa) status", **READ})
    async def maya_arnold_status() -> str:
        """Check whether the Arnold plugin (mtoa) is loaded, its version, and whether
        Arnold is the current renderer. Tries to load the plugin quietly. Call this
        before other Arnold tools; if loaded is false the reply says how to fix it."""
        return await ctx.run("arnold.status")

    @mcp.tool(name="maya_create_arnold_light", annotations={"title": "Create an Arnold light", **WRITE})
    async def maya_create_arnold_light(params: CreateLightInput) -> str:
        """Create a light for Arnold rendering: area, skydome (with optional HDRI),
        mesh (emitting geometry), photometric (IES), distant, point or spot. Sets
        intensity, exposure, colour, transform, samples and shadows. Also switches
        the current renderer to Arnold. Returns the transform and shape long names."""
        return await ctx.run("arnold.create_light", params.model_dump())

    @mcp.tool(name="maya_list_lights", annotations={"title": "List lights", **READ})
    async def maya_list_lights() -> str:
        """List every Arnold and Maya light with intensity, exposure, colour, position
        and, for skydomes, the connected HDRI path."""
        return await ctx.run("arnold.list_lights")

    @mcp.tool(name="maya_set_render_settings", annotations={"title": "Set Arnold render settings", **WRITE})
    async def maya_set_render_settings(params: RenderSettingsInput) -> str:
        """Configure Arnold: sampling (camera AA, diffuse, specular, transmission,
        SSS, volume), adaptive sampling, denoiser, resolution, frame range, output
        format and prefix, motion blur and the renderable camera. Only the fields you
        pass change. Returns what was applied plus the full current settings."""
        return await ctx.run("arnold.set_render_settings", params.model_dump())

    @mcp.tool(name="maya_get_render_settings", annotations={"title": "Get Arnold render settings", **READ})
    async def maya_get_render_settings() -> str:
        """Read the current Arnold sampling, ray depth, resolution, frame range,
        output format/prefix, images folder, renderable cameras and AOV count."""
        return await ctx.run("arnold.get_render_settings")

    @mcp.tool(name="maya_render_frame", annotations={"title": "Render a frame with Arnold", **WRITE})
    async def maya_render_frame(params: RenderFrameInput):
        """Render one frame with Arnold to the project images folder and return the
        image (png/jpeg) or its path (exr). Slow: seconds to minutes depending on
        settings; lower camera_aa first for previews. Use maya_viewport_screenshot
        for a quick look instead."""
        data = params.model_dump()
        timeout = data.pop("timeout")
        return await ctx.image("arnold.render_frame", data, timeout=timeout)

    @mcp.tool(name="maya_render_sequence", annotations={"title": "Render a sequence with Arnold", **WRITE})
    async def maya_render_sequence(params: RenderSequenceInput) -> str:
        """Render a frame range with Arnold (Render Sequence) and return the written
        file paths. Blocks Maya for the whole render; set a sensible range and
        sampling first with maya_set_render_settings."""
        data = params.model_dump()
        timeout = data.pop("timeout")
        return await ctx.run("arnold.render_sequence", data, timeout=timeout)

    @mcp.tool(name="maya_create_aov", annotations={"title": "Create an Arnold AOV", **WRITE})
    async def maya_create_aov(params: CreateAovInput) -> str:
        """Add a render pass (AOV) such as diffuse, specular, N, Z or crypto_object.
        Uses the mtoa AOV interface when available, else builds the aiAOV node by
        hand. Cryptomatte passes switch the driver to merged EXR."""
        return await ctx.run("arnold.create_aov", params.model_dump())

    @mcp.tool(name="maya_list_aovs", annotations={"title": "List Arnold AOVs", **READ})
    async def maya_list_aovs() -> str:
        """List the AOVs configured for Arnold with their data type and enabled flag."""
        return await ctx.run("arnold.list_aovs")

    @mcp.tool(name="maya_set_arnold_attributes", annotations={"title": "Set per object Arnold attributes", **WRITE})
    async def maya_set_arnold_attributes(params: SetAiAttributesInput) -> str:
        """Set Arnold shape attributes on meshes: subdivision (catclark + iterations),
        opaque, matte, self shadows, visibility flags and displacement height, padding
        and zero value. Works on transforms or shapes, or the selection. Returns which
        attributes were set per shape and any warnings."""
        return await ctx.run("arnold.set_ai_attributes", params.model_dump())
