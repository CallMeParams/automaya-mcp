"""Materials tools: shaders, assignment, texture wiring, PBR networks, texture management."""
from __future__ import annotations

from typing import Any, Dict, List, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

SHADER_TYPES = "lambert | blinn | phong | standardSurface | aiStandardSurface | aiFlat | useBackground"


class CreateMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(default="standardSurface", description="Shader type: " + SHADER_TYPES + ". aiStandardSurface/aiFlat need Arnold.", pattern="^(lambert|blinn|phong|standardSurface|aiStandardSurface|aiFlat|useBackground)$")
    name: str | None = Field(default=None, description="Shader node name, e.g. 'redPaint'. Maya adds a number if taken.", max_length=200)
    color: List[float] | None = Field(default=None, description="Main colour as [r, g, b] in 0..1 (color or baseColor depending on type)", min_length=3, max_length=3, examples=[[0.8, 0.1, 0.1]])
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra attributes to set: {'specularRoughness': 0.3, 'metalness': 1.0}. Aliases like roughness/metallic/diffuse are accepted.")
    assign_to: List[str] | None = Field(default=None, description="Objects, shapes or face components (pCube1.f[0:5]) to assign the new material to")


class AssignMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Existing shader node name, e.g. 'lambert2' or 'redPaint'", min_length=1)
    nodes: List[str] | None = Field(default=None, description="Objects, shapes or face components. Omit to use the current selection.")


class ListMaterialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    with_assignments: bool = Field(default=True, description="Include the objects each material is assigned to")
    include_defaults: bool = Field(default=False, description="Include lambert1, standardSurface1, particleCloud1, shaderGlow1")


class GetMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Shader node name", min_length=1)
    max_attrs: int = Field(default=150, ge=1, le=1000, description="Cap on the number of attributes returned")


class SetMaterialAttrsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Shader node name", min_length=1)
    attrs: Dict[str, Any] = Field(..., description="Attribute values: colours as [r, g, b], floats, bools or strings. Example: {'baseColor': [1, 0.5, 0], 'specularRoughness': 0.4, 'metalness': 1}", min_length=1)


class SetTextureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="Shader node name", min_length=1)
    attribute: str = Field(..., description="Target attribute: baseColor, color, specularRoughness, metalness, normalCamera, opacity, emissionColor ... (aliases like roughness/normal work)", min_length=1)
    path: str = Field(..., description="Texture file path. <UDIM> tokens are supported.", min_length=1, examples=["/textures/wood_basecolor.png"])
    color_space: str | None = Field(default=None, description="sRGB, Raw, ACEScg ... Default: sRGB for colour maps, Raw for data maps (roughness, metalness, normal)")
    is_normal: bool = Field(default=False, description="Treat the file as a tangent space normal map and insert a bump2d node")
    uv_tiling: Union[float, List[float]] | None = Field(default=None, description="repeatUV as a number or [u, v]", examples=[2, [4, 4]])
    name: str | None = Field(default=None, description="Base name for the file and place2dTexture nodes")


class CreatePbrNetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="pbrMaterial", description="Shader name", min_length=1, max_length=200)
    base_color: str | None = Field(default=None, description="Base colour / albedo map path")
    roughness: str | None = Field(default=None, description="Roughness map path (Raw)")
    metalness: str | None = Field(default=None, description="Metalness map path (Raw)")
    normal: str | None = Field(default=None, description="Tangent space normal map path")
    displacement: str | None = Field(default=None, description="Height / displacement map path; creates a displacementShader on the shading group")
    ao: str | None = Field(default=None, description="Ambient occlusion map, multiplied into base colour")
    opacity: str | None = Field(default=None, description="Opacity map path (Raw)")
    emission: str | None = Field(default=None, description="Emission colour map path; also sets emission weight to 1")
    shader_type: str = Field(default="standardSurface", description="standardSurface (any renderer) or aiStandardSurface (Arnold)", pattern="^(standardSurface|aiStandardSurface)$")
    assign_to: List[str] | None = Field(default=None, description="Objects to assign the material to")
    uv_tiling: Union[float, List[float]] | None = Field(default=None, description="repeatUV for every map, number or [u, v]")
    displacement_scale: float = Field(default=1.0, description="displacementShader scale", ge=0)


class ListTexturesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    missing_only: bool = Field(default=False, description="Only return file nodes whose image is missing on disk")


class RepathTexturesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(..., description="Substring (or regex when regex=true) to find in every file texture path", min_length=1, examples=["C:/old_project/sourceimages"])
    replace: str = Field(default="", description="Replacement text")
    dry_run: bool = Field(default=False, description="Preview the changes without applying them")
    regex: bool = Field(default=False, description="Treat search as a regular expression")


class ConvertMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: str = Field(..., description="lambert, blinn or phong shader to convert", min_length=1)
    to_type: str = Field(default="standardSurface", description="standardSurface or aiStandardSurface", pattern="^(standardSurface|aiStandardSurface)$")
    delete_old: bool = Field(default=True, description="Delete the original shader after reconnecting its shading groups")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_create_material", annotations={"title": "Create a material", **WRITE})
    async def maya_create_material(params: CreateMaterialInput) -> str:
        """Create a shader with its shading group, optionally set colour and other
        attributes, and assign it to objects in one call. Use standardSurface for
        renderer agnostic PBR, aiStandardSurface when rendering with Arnold, lambert
        for flat previs colours. Returns the shader, shading group and what was assigned."""
        return await ctx.run("materials.create", params.model_dump())

    @mcp.tool(name="maya_assign_material", annotations={"title": "Assign a material", **WRITE})
    async def maya_assign_material(params: AssignMaterialInput) -> str:
        """Assign an existing material to objects, shapes or face selections such as
        pCube1.f[0:5]. Uses the current selection when nodes is omitted. Creates a
        shading group if the shader has none."""
        return await ctx.run("materials.assign", params.model_dump())

    @mcp.tool(name="maya_list_materials", annotations={"title": "List materials", **READ})
    async def maya_list_materials(params: ListMaterialsInput) -> str:
        """List materials in the scene with their type, shading groups and the objects
        they are assigned to. Default materials are hidden unless include_defaults is set."""
        return await ctx.run("materials.list", params.model_dump())

    @mcp.tool(name="maya_get_material", annotations={"title": "Inspect a material", **READ})
    async def maya_get_material(params: GetMaterialInput) -> str:
        """Inspect one material: attribute values, incoming connections (which texture
        feeds which attribute), upstream file textures with on-disk status, and the
        objects it is assigned to. Use before editing a look you did not build."""
        return await ctx.run("materials.get", params.model_dump())

    @mcp.tool(name="maya_set_material_attrs", annotations={"title": "Set material attributes", **WRITE})
    async def maya_set_material_attrs(params: SetMaterialAttrsInput) -> str:
        """Set several material attributes at once. Colours are [r, g, b] lists, other
        values are floats, bools or strings. Aliases (roughness, metallic, diffuse,
        normal) map to the right attribute for the shader type."""
        return await ctx.run("materials.set_attrs", params.model_dump())

    @mcp.tool(name="maya_set_texture", annotations={"title": "Connect a texture", **WRITE})
    async def maya_set_texture(params: SetTextureInput) -> str:
        """Wire an image file into a material attribute: creates a file node with a
        fully connected place2dTexture, picks sRGB for colour maps and Raw for data
        maps, and inserts a tangent space bump2d for normal maps. Returns the nodes
        created and whether the file exists on disk."""
        return await ctx.run("materials.set_texture", params.model_dump())

    @mcp.tool(name="maya_create_pbr_network", annotations={"title": "Build a PBR shading network", **WRITE})
    async def maya_create_pbr_network(params: CreatePbrNetworkInput) -> str:
        """Build a complete PBR material from texture paths in one call: base colour,
        roughness, metalness, normal (bump2d), displacement (displacementShader),
        AO (multiplied into base colour), opacity and emission. Ideal after a Poly
        Haven or AI texture download. Reports any map missing on disk."""
        return await ctx.run("materials.create_pbr_network", params.model_dump())

    @mcp.tool(name="maya_remove_unused_materials", annotations={"title": "Delete unused shading nodes", **DESTRUCTIVE})
    async def maya_remove_unused_materials() -> str:
        """Delete shaders, shading groups and textures that are not assigned to any
        object (same as Hypershade > Edit > Delete Unused Nodes). Returns what was removed."""
        return await ctx.run("materials.remove_unused")

    @mcp.tool(name="maya_list_textures", annotations={"title": "List file textures", **READ})
    async def maya_list_textures(params: ListTexturesInput) -> str:
        """List every file texture node with its path, colour space, whether the image
        exists on disk and what it feeds. Use missing_only to find broken paths before
        rendering or handing the scene off."""
        return await ctx.run("materials.list_textures", params.model_dump())

    @mcp.tool(name="maya_repath_textures", annotations={"title": "Repath file textures", **WRITE})
    async def maya_repath_textures(params: RepathTexturesInput) -> str:
        """Search and replace inside every file texture path, for example after moving
        a project between machines. Run with dry_run first to preview, then apply.
        Reports whether each new path resolves on disk."""
        return await ctx.run("materials.repath_textures", params.model_dump())

    @mcp.tool(name="maya_convert_material", annotations={"title": "Convert a legacy material", **DESTRUCTIVE})
    async def maya_convert_material(params: ConvertMaterialInput) -> str:
        """Convert a lambert, blinn or phong shader into standardSurface or
        aiStandardSurface. Colour, textures, normal maps, transparency and
        incandescence are mapped; specular roughness is approximated from eccentricity
        or cosine power. Shading groups are reconnected and the old shader deleted."""
        return await ctx.run("materials.convert", params.model_dump())
