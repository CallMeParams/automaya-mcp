"""craft_lookdev tools: measured materials, variation, wear, colour management, render presets and a material audit."""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import science as sci
from ._base import READ, WRITE, ToolContext, dumps

MATERIAL_NAMES = "|".join(sorted(set(sci.MEASURED_MATERIALS) | set(sci.MATERIAL_ALIASES)))


class MeasuredMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="concrete", description="Library entry: " + ", ".join(sci.material_names()), pattern="^(%s)$" % MATERIAL_NAMES)
    material_name: str | None = Field(default=None, description="Shader node name; default <preset>_mat", max_length=200)
    assign_to: List[str] | None = Field(default=None, description="Transforms, shapes or faces to assign to")
    breakup: float = Field(default=0.0, description="Procedural break-up amount 0..1 into colour and roughness (aiNoise)", ge=0, le=1)
    breakup_scale: float = Field(default=1.0, description="Noise scale in scene units; larger is coarser", gt=0)
    cell_noise: bool = Field(default=False, description="Use aiCellNoise (stones, cells) instead of aiNoise")
    triplanar: str = Field(default="auto", description="auto (when the mesh has no UVs) | on | off", pattern="^(auto|on|off)$")
    color_override: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Replace the measured baseColor (painted metal, coloured fabric)")


class VariationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Source aiStandardSurface or standardSurface", min_length=1)
    count: int = Field(default=5, description="Number of variants", ge=1, le=200)
    hue_jitter: float = Field(default=8.0, description="Hue jitter in degrees", ge=0, le=180)
    value_jitter: float = Field(default=0.1, description="Brightness jitter fraction", ge=0, le=1)
    roughness_jitter: float = Field(default=0.1, ge=0, le=1)
    seed: int = Field(default=0, ge=0)
    assign_to: List[str] | None = Field(default=None, description="Nodes to assign round robin (crowds, building blocks)")


class WearInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Material to add wear to", min_length=1)
    edge_amount: float = Field(default=0.5, description="Edge wear via aiCurvature, 0 disables", ge=0, le=1)
    dirt_amount: float = Field(default=0.5, description="Dirt in crevices via aiAmbientOcclusion, 0 disables", ge=0, le=1)
    edge_color: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Exposed edge colour; default bare steel for metals, lighter base for dielectrics")
    dirt_color: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Dirt colour, default dark brown")
    edge_radius: float = Field(default=1.0, description="Curvature sampling radius in scene units", gt=0)
    dirt_distance: float = Field(default=20.0, description="AO far clip in scene units", gt=0)
    edge_metal: bool | None = Field(default=None, description="Force metallic edges; default follows the base metalness")


class ColorManagementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(default="aces13", description="aces13 (Maya 2024 ACES 1.3 config) | aces2 | srgb (legacy)", pattern="^(aces13|aces2|srgb)$")
    view_transform: str | None = Field(default=None, description="Override the view, e.g. 'Un-tone-mapped (sRGB)'")
    rendering_space: str | None = Field(default=None, description="Override the rendering space, e.g. ACEScg")
    config_path: str | None = Field(default=None, description="Explicit config.ocio path instead of the Maya shipped one")


class RenderPresetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quality: str = Field(default="preview", description="preview (fast, png) | production (adaptive, OIDN, exr + AOVs) | final (high samples, cryptomatte)", pattern="^(preview|production|final)$")
    width: int | None = Field(default=None, ge=1, le=16384)
    height: int | None = Field(default=None, ge=1, le=16384)
    camera: str | None = Field(default=None, description="Make this camera the renderable one")


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_defaults: bool = Field(default=False, description="Also audit lambert1 and the other default shaders")


class LibraryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, description="One entry to describe; omit for the whole table")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_lookdev_material", annotations={"title": "Measured material", **WRITE})
    async def maya_lookdev_material(params: MeasuredMaterialInput) -> str:
        """Create an aiStandardSurface from measured values (albedo, roughness,
        metalness, IOR, coat, SSS, transmission) for concrete, asphalt, brick,
        plaster, woods, metals, glass, water, rubber, leather, fabric, skin, snow,
        sand or grass. Optional aiNoise/aiCellNoise break-up and aiTriplanar when
        the mesh has no UVs. Without Arnold builds a standardSurface (path 'maya')."""
        return await ctx.run("lookdev.measured_material", params.model_dump())

    @mcp.tool(name="maya_lookdev_variation", annotations={"title": "Material variations", **WRITE})
    async def maya_lookdev_variation(params: VariationInput) -> str:
        """Make N copies of a PBR material with jittered hue, value and roughness for
        crowds, bricks or building blocks, optionally assigned round robin to a list
        of nodes. Deterministic per seed."""
        return await ctx.run("lookdev.material_variation", params.model_dump())

    @mcp.tool(name="maya_lookdev_wear", annotations={"title": "Edge wear and dirt", **WRITE})
    async def maya_lookdev_wear(params: WearInput) -> str:
        """Layer edge wear (aiCurvature mask) and crevice dirt (aiAmbientOcclusion
        mask) over a material using aiLayerShader (both) or aiMixShader (one) and
        rewire its shading group. Arnold only. Amounts 0..1; set one to 0 to skip it."""
        return await ctx.run("lookdev.wear", params.model_dump())

    @mcp.tool(name="maya_lookdev_color_mgmt", annotations={"title": "Colour management (OCIO)", **WRITE})
    async def maya_lookdev_color_mgmt(params: ColorManagementInput) -> str:
        """Enable OCIO colour management: aces13 loads the ACES 1.3 config Maya 2024
        ships with the ACES 1.0 SDR-video view and ACEScg rendering space; aces2
        tries the ACES 2.0 view and falls back; srgb is the legacy pipeline. Do this
        before lookdev so renders and the viewport match."""
        return await ctx.run("lookdev.color_management", params.model_dump())

    @mcp.tool(name="maya_lookdev_render_preset", annotations={"title": "Arnold render preset", **WRITE})
    async def maya_lookdev_render_preset(params: RenderPresetInput) -> str:
        """Apply a consistent Arnold preset: preview (AA 3, no denoise, png),
        production (AA 5 adaptive, OIDN, exr with diffuse/specular/N/Z AOVs) or
        final (AA 8 adaptive, full AOVs plus cryptomatte). Optionally sets the
        resolution and renderable camera. Arnold only."""
        return await ctx.run("lookdev.render_preset", params.model_dump())

    @mcp.tool(name="maya_lookdev_report", annotations={"title": "Material audit", **READ})
    async def maya_lookdev_report(params: ReportInput) -> str:
        """Audit every shader for values outside plausible ranges: albedo above 0.9
        or nearly black, dark metals, roughness 0 dielectrics, odd IOR, metals that
        transmit, and legacy lambert/blinn/phong shaders. Returns the flagged list
        with a fix per issue."""
        return await ctx.run("lookdev.material_report", params.model_dump())

    @mcp.tool(name="maya_lookdev_library", annotations={"title": "Measured material table (offline)", **READ})
    async def maya_lookdev_library(params: LibraryInput) -> str:
        """Read the measured material table (linear albedo or F0, roughness,
        metalness, IOR, coat, SSS, transmission, notes with sources) without Maya.
        Pass a name for one entry, or nothing for the full list."""
        if params.name:
            try:
                return dumps(sci.measured_material(params.name))
            except KeyError as exc:
                return "Error: %s" % exc.args[0]
        return dumps({"count": len(sci.MEASURED_MATERIALS), "names": sci.material_names(), "aliases": sci.MATERIAL_ALIASES, "materials": sci.MEASURED_MATERIALS})
