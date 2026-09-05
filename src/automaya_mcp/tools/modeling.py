"""Modeling tools: primitives, curves, poly editing, transforms, deformers, layout."""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

Vec3 = List[float]


class CreatePrimitiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="cube", description="cube | sphere | cylinder | cone | plane | torus | pipe | disc | prism | pyramid | helix | platonic", examples=["cylinder"])
    name: str | None = Field(default=None, description="Transform name", examples=["barrel_geo"])
    size: float | None = Field(default=None, gt=0, description="Uniform size fallback for width/height/depth (diameter for round shapes)")
    radius: float | None = Field(default=None, gt=0, description="Radius for sphere/cylinder/cone/torus/pipe/disc/helix/platonic")
    height: float | None = Field(default=None, gt=0, description="Height for cube/cylinder/cone/pipe/prism/helix")
    width: float | None = Field(default=None, gt=0, description="Width (cube/plane/prism/pyramid side); torus section radius; pipe thickness; helix tube radius")
    depth: float | None = Field(default=None, gt=0, description="Depth for cube, or plane length along Z")
    subdivisions: int | None = Field(default=None, ge=1, le=200, description="Subdivisions per axis (sides for disc/prism/pyramid, solid type 0-3 for platonic)")
    translate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="World position [x, y, z]")
    rotate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="Rotation in degrees [x, y, z]")
    scale: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="Scale [x, y, z]")


class CreateCurveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: List[Vec3] = Field(..., min_length=2, description="Control points as [[x, y, z], ...]; a degree 3 curve needs 4 or more", examples=[[[0, 0, 0], [1, 2, 0], [3, 2, 0], [4, 0, 0]]])
    degree: int = Field(default=3, description="1 (linear), 2, 3, 5 or 7")
    closed: bool = Field(default=False, description="Close the curve into a loop")
    name: str | None = Field(default=None, description="Curve name")


class CreateTextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=500, description="Text to spell out as curves")
    font: str = Field(default="Arial", description="Font name available on the machine")
    name: str | None = Field(default=None, description="Group name")


class TransformInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Target nodes (default: selection)")
    translate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="[x, y, z]")
    rotate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="Degrees [x, y, z]")
    scale: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="[x, y, z]")
    relative: bool = Field(default=False, description="Add to the current values instead of replacing them")
    world: bool = Field(default=True, description="World space (false = object/local space)")


class DuplicateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to duplicate (default: selection)")
    count: int = Field(default=1, ge=1, le=1000, description="Number of copies per node")
    offset_translate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="Translation added to each successive copy")
    offset_rotate: Vec3 | None = Field(default=None, min_length=3, max_length=3, description="Rotation (degrees) added to each successive copy")
    instance: bool = Field(default=False, description="Make instances that share the shape instead of full copies")
    name: str | None = Field(default=None, description="Base name for the copies")


class ExtrudeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    components: List[str] | None = Field(default=None, description="Faces like ['f[0:3]', 'f[10]'], edges 'e[..]' or vertices 'vtx[..]'. Omit for all faces.", examples=[["f[0:3]"]])
    distance: float = Field(default=1.0, description="Extrude distance along the local normal (negative goes inwards)")
    thickness: float | None = Field(default=None, description="Wall thickness (faces only)")
    divisions: int = Field(default=1, ge=1, le=50, description="Segments along the extrusion")
    keep_faces_together: bool = Field(default=True, description="Extrude as one block instead of per face")


class BevelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    components: List[str] | None = Field(default=None, description="Edges like ['e[0:11]'] (or faces). Omit to bevel every edge.")
    edges: List[str] | None = Field(default=None, description="Alias for components")
    fraction: float = Field(default=0.5, gt=0, le=1, description="Bevel width as a fraction of edge length")
    segments: int = Field(default=1, ge=1, le=50, description="Bevel segments (more = rounder)")
    chamfer: bool = Field(default=True, description="Chamfer corners")


class BooleanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str = Field(..., min_length=1, description="First mesh")
    b: str = Field(..., min_length=1, description="Second mesh")
    operation: str = Field(default="union", description="union | difference (a minus b) | intersection")
    name: str | None = Field(default=None, description="Result name")


class CombineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Meshes to combine (default: selection)")
    name: str | None = Field(default=None, description="Result name")


class NodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")


class MirrorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    axis: str = Field(default="x", description="x | y | z")
    direction: str = Field(default="+", description="'+' or '-': which side the mirrored half goes")
    merge: bool = Field(default=True, description="Merge vertices along the seam")


class SmoothInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    divisions: int = Field(default=1, ge=1, le=4, description="Subdivision levels")


class ReduceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    percentage: float = Field(default=50.0, gt=0, lt=100, description="Percent of polygons to remove")
    keep_quads: bool = Field(default=False, description="Favour keeping quads")


class FreezeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Targets (default: selection)")
    translate: bool = Field(default=True, description="Freeze translation")
    rotate: bool = Field(default=True, description="Freeze rotation")
    scale: bool = Field(default=True, description="Freeze scale")
    normal: bool = Field(default=False, description="Also freeze normals")


class NodesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Targets (default: selection)")


class UvAutoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Mesh transform")
    method: str = Field(default="automatic", description="automatic | planar | cylindrical | spherical")
    axis: str = Field(default="y", description="Projection axis for planar: x | y | z")


class LatticeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Objects to deform (default: selection)")
    divisions: List[int] | None = Field(default=None, min_length=3, max_length=3, description="Lattice points [s, t, u], each >= 2", examples=[[3, 4, 3]])
    name: str | None = Field(default=None, description="Deformer name")


class NurbsToPolyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="NURBS surface transform")
    quads: bool = Field(default=True, description="Quads (true) or triangles")
    fit: str = Field(default="general", description="general | count | standard | control")
    spans_u: int = Field(default=4, ge=1, le=200, description="Polygons per span in U (general fit)")
    spans_v: int = Field(default=4, ge=1, le=200, description="Polygons per span in V (general fit)")
    name: str | None = Field(default=None, description="Result name")


class RevolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    curve: str = Field(..., min_length=1, description="Profile curve")
    axis: str = Field(default="y", description="Revolve axis: x | y | z")
    degrees: float = Field(default=360.0, gt=0, le=360, description="Sweep angle")
    sections: int = Field(default=8, ge=1, le=200, description="Sections around the axis")
    output_poly: bool = Field(default=False, description="Output polygons instead of a NURBS surface")
    name: str | None = Field(default=None, description="Result name")


class LoftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    curves: List[str] = Field(..., min_length=2, description="Two or more profile curves, in order")
    close: bool = Field(default=False, description="Close the loft back to the first curve")
    uniform: bool = Field(default=True, description="Uniform parameterisation")
    output_poly: bool = Field(default=False, description="Output polygons instead of a NURBS surface")
    name: str | None = Field(default=None, description="Result name")


class CleanupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Meshes to clean (default: selection)")
    nonmanifold: bool = Field(default=True, description="Fix non manifold geometry")
    lamina: bool = Field(default=True, description="Remove lamina faces")
    zero_area: bool = Field(default=True, description="Remove zero area faces")
    zero_length: bool = Field(default=False, description="Remove zero length edges")
    select_only: bool = Field(default=False, description="Only select the problem components, do not fix")


class SmoothPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Meshes (default: selection)")
    level: int = Field(default=1, ge=0, le=3, description="0 off, 1..3 smooth preview level")


class ArrayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Source node (stays as the first element)")
    count: int = Field(default=5, ge=2, le=1000, description="Total elements including the source")
    spacing: float | None = Field(default=None, description="Linear spacing (default: 1.5 x the object's size on that axis)")
    axis: str = Field(default="x", description="Linear direction, or the axis the circle is around: x | y | z")
    radius: float | None = Field(default=None, gt=0, description="Circular layout radius; when set the layout is a ring")
    instance: bool = Field(default=False, description="Use instances instead of copies")
    name: str | None = Field(default=None, description="Base name for copies")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_create_primitive", annotations={"title": "Create primitive", **WRITE})
    async def maya_create_primitive(params: CreatePrimitiveInput) -> str:
        """Create a polygon primitive (cube, sphere, cylinder, cone, plane, torus, pipe,
        disc, prism, pyramid, helix, platonic) with size and placement. Returns the
        transform and shape long names plus a summary. The base building block for
        blockouts and previs."""
        return await ctx.run("modeling.create_primitive", params.model_dump())

    @mcp.tool(name="maya_create_curve", annotations={"title": "Create NURBS curve", **WRITE})
    async def maya_create_curve(params: CreateCurveInput) -> str:
        """Create a NURBS curve through points. Use degree 1 for polylines. Curves
        feed maya_revolve, maya_loft, motion paths and extrusions."""
        return await ctx.run("modeling.create_curve", params.model_dump())

    @mcp.tool(name="maya_create_text", annotations={"title": "Create text curves", **WRITE})
    async def maya_create_text(params: CreateTextInput) -> str:
        """Create NURBS curves spelling a string in a font (textCurves). Bevel or
        planar-surface them afterwards for 3D lettering."""
        return await ctx.run("modeling.create_text", params.model_dump())

    @mcp.tool(name="maya_transform", annotations={"title": "Move, rotate, scale", **WRITE})
    async def maya_transform(params: TransformInput) -> str:
        """Set or offset translate/rotate/scale on nodes, in world or object space.
        Returns the final transform values for each node."""
        return await ctx.run("modeling.transform", params.model_dump())

    @mcp.tool(name="maya_duplicate", annotations={"title": "Duplicate or instance", **WRITE})
    async def maya_duplicate(params: DuplicateInput) -> str:
        """Duplicate nodes N times with a cumulative translate/rotate offset, as
        copies or instances. Returns each copy's name and transform. For rings or
        evenly spaced rows use maya_array."""
        return await ctx.run("modeling.duplicate", params.model_dump())

    @mcp.tool(name="maya_extrude", annotations={"title": "Extrude faces/edges", **WRITE})
    async def maya_extrude(params: ExtrudeInput) -> str:
        """Extrude faces (or edges/vertices) of a mesh by a distance along the normal,
        with optional thickness and divisions. Component strings use Maya syntax
        like f[0:3]."""
        return await ctx.run("modeling.extrude", params.model_dump())

    @mcp.tool(name="maya_bevel", annotations={"title": "Bevel edges", **WRITE})
    async def maya_bevel(params: BevelInput) -> str:
        """Bevel edges of a mesh (polyBevel3) with width as a fraction of edge length
        and a segment count. Omit components to bevel every edge."""
        return await ctx.run("modeling.bevel", params.model_dump())

    @mcp.tool(name="maya_boolean", annotations={"title": "Boolean meshes", **DESTRUCTIVE})
    async def maya_boolean(params: BooleanInput) -> str:
        """Boolean two meshes: union, difference (a minus b) or intersection. The
        inputs are consumed into the result."""
        return await ctx.run("modeling.boolean", params.model_dump())

    @mcp.tool(name="maya_combine", annotations={"title": "Combine meshes", **DESTRUCTIVE})
    async def maya_combine(params: CombineInput) -> str:
        """Combine several meshes into one (polyUnite). Run maya_delete_history
        afterwards if you want a clean result."""
        return await ctx.run("modeling.combine", params.model_dump())

    @mcp.tool(name="maya_separate", annotations={"title": "Separate mesh", **DESTRUCTIVE})
    async def maya_separate(params: NodeInput) -> str:
        """Split a mesh into its disconnected shells, one transform per piece."""
        return await ctx.run("modeling.separate", params.model_dump())

    @mcp.tool(name="maya_mirror", annotations={"title": "Mirror mesh", **WRITE})
    async def maya_mirror(params: MirrorInput) -> str:
        """Mirror a mesh across an axis of its pivot and optionally merge the seam.
        Great for symmetric models built as one half."""
        return await ctx.run("modeling.mirror", params.model_dump())

    @mcp.tool(name="maya_smooth", annotations={"title": "Smooth mesh", **WRITE})
    async def maya_smooth(params: SmoothInput) -> str:
        """Subdivide a mesh (polySmooth). Each division quadruples the face count;
        for viewport only smoothing use maya_set_smooth_preview."""
        return await ctx.run("modeling.smooth", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_poly_reduce", annotations={"title": "Reduce polygons", **WRITE})
    async def maya_poly_reduce(params: ReduceInput) -> str:
        """Reduce polygon count by a percentage while keeping borders, UV borders and
        hard edges."""
        return await ctx.run("modeling.reduce", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_freeze_transforms", annotations={"title": "Freeze transforms", **WRITE})
    async def maya_freeze_transforms(params: FreezeInput) -> str:
        """Bake translate/rotate/scale into the geometry so the transform reads zero
        (makeIdentity). Do this before export or rigging."""
        return await ctx.run("modeling.freeze_transforms", params.model_dump())

    @mcp.tool(name="maya_center_pivot", annotations={"title": "Center pivot", **WRITE})
    async def maya_center_pivot(params: NodesInput) -> str:
        """Move each node's pivot to the center of its bounding box."""
        return await ctx.run("modeling.center_pivot", params.model_dump())

    @mcp.tool(name="maya_delete_history", annotations={"title": "Delete history", **WRITE})
    async def maya_delete_history(params: NodesInput) -> str:
        """Delete construction history on nodes, baking their current shape."""
        return await ctx.run("modeling.delete_history", params.model_dump())

    @mcp.tool(name="maya_mesh_stats", annotations={"title": "Mesh statistics", **READ})
    async def maya_mesh_stats(params: NodeInput) -> str:
        """Vertex, edge, face, triangle, UV and shell counts plus the world bounding
        box of a mesh. Use it to check budgets and sizes."""
        return await ctx.run("modeling.mesh_stats", params.model_dump())

    @mcp.tool(name="maya_uv_auto", annotations={"title": "Auto UVs", **WRITE})
    async def maya_uv_auto(params: UvAutoInput) -> str:
        """Generate UVs with automatic (6 plane), planar, cylindrical or spherical
        projection. Good enough for texturing previs assets."""
        return await ctx.run("modeling.uv_auto", params.model_dump())

    @mcp.tool(name="maya_lattice", annotations={"title": "Lattice deformer", **WRITE})
    async def maya_lattice(params: LatticeInput) -> str:
        """Wrap nodes in an FFD lattice for squash and bend style edits. Returns the
        ffd, lattice and base nodes; move lattice points with maya_transform."""
        return await ctx.run("modeling.lattice", params.model_dump())

    @mcp.tool(name="maya_nurbs_to_poly", annotations={"title": "NURBS to polygons", **WRITE})
    async def maya_nurbs_to_poly(params: NurbsToPolyInput) -> str:
        """Convert a NURBS surface (from revolve/loft) to a polygon mesh."""
        return await ctx.run("modeling.nurbs_to_poly", params.model_dump())

    @mcp.tool(name="maya_revolve", annotations={"title": "Revolve curve", **WRITE})
    async def maya_revolve(params: RevolveInput) -> str:
        """Lathe a profile curve around an axis into a surface (vases, bottles,
        wheels). Set output_poly for a mesh directly."""
        return await ctx.run("modeling.revolve", params.model_dump())

    @mcp.tool(name="maya_loft", annotations={"title": "Loft curves", **WRITE})
    async def maya_loft(params: LoftInput) -> str:
        """Loft a surface through two or more curves in order (hulls, wings, pipes)."""
        return await ctx.run("modeling.loft", params.model_dump())

    @mcp.tool(name="maya_cleanup_mesh", annotations={"title": "Mesh cleanup", **DESTRUCTIVE})
    async def maya_cleanup_mesh(params: CleanupInput) -> str:
        """Run Maya's Mesh Cleanup for non manifold geometry, lamina faces and zero
        area faces. With select_only it reports the problem components instead of
        editing. Run it before booleans, smoothing or export."""
        return await ctx.run("modeling.cleanup", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_set_smooth_preview", annotations={"title": "Smooth preview", **WRITE})
    async def maya_set_smooth_preview(params: SmoothPreviewInput) -> str:
        """Toggle viewport smooth mesh preview (the 1/2/3 keys) on meshes without
        changing the geometry."""
        return await ctx.run("modeling.set_smooth_preview", params.model_dump())

    @mcp.tool(name="maya_array", annotations={"title": "Array layout", **WRITE})
    async def maya_array(params: ArrayInput) -> str:
        """Lay out copies of a node in a row (axis + spacing) or a ring (radius
        around axis), with copies rotated to face outward. Returns every element's
        position. Ideal for fences, columns, wheels and crowd blockouts."""
        return await ctx.run("modeling.array", params.model_dump())
