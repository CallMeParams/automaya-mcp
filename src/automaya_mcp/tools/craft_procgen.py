"""Procedural generation tools: buildings, streets, rooms, stairs, railings, pipes, fences,
columns, furniture and vehicle proxies, trees, rocks, terrain, scatter and arrays.

Everything is real world scale in cm, grouped, pivot at the base centre, transforms frozen,
and returns {group, parts, bbox, stats} so the agent can check what it got.
"""
from __future__ import annotations

from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import WRITE, ToolContext


class BuildingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="building", description="Base name; parts become <name>_<part>_geo under <name>_grp", examples=["tenement_a"])
    width: float = Field(default=1200.0, ge=200, le=50000, description="Facade width along X in cm")
    depth: float = Field(default=1000.0, ge=200, le=50000, description="Depth along Z in cm")
    floors: int = Field(default=3, ge=1, le=120, description="Number of floors")
    floor_height: float = Field(default=320.0, ge=200, le=1000, description="Floor to floor height in cm (320 is a typical residential floor)")
    style: str = Field(default="flat", description="flat | brick | glass | classical (sets the window reveal depth)")
    window_width: float = Field(default=120.0, ge=20, le=1000, description="Window width in cm")
    window_height: float = Field(default=150.0, ge=20, le=1000, description="Window height in cm")
    window_spacing: float = Field(default=300.0, ge=30, le=5000, description="Bay width: one window per this many cm of facade")
    sill: float = Field(default=90.0, ge=0, le=500, description="Sill height above the floor in cm")
    mullions: bool = Field(default=False, description="Add a vertical mullion bar per window")
    shopfront: bool = Field(default=False, description="Ground floor front becomes tall shop glazing")
    cornice: bool = Field(default=True, description="Projecting band at the top of the wall")
    roof: str = Field(default="flat", description="flat | parapet | pitched | hip")
    entrance: bool = Field(default=True, description="Cut a 210 x 90 door in the middle front bay")
    footprint: List[List[float]] | None = Field(default=None, min_length=3, description="Custom plan as [[x, z], ...] to extrude instead of the box (no windows)", examples=[[[0, 0], [1200, 0], [1200, 800], [0, 800]]])
    reveal: float | None = Field(default=None, ge=0, le=200, description="Window recess depth in cm (default follows style)")


class StreetBlockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="block", description="Base name for the whole block")
    lots: int = Field(default=4, ge=1, le=40, description="Buildings per side of the road")
    lot_width: float = Field(default=1200.0, ge=300, le=10000, description="Lot width along the road in cm")
    lot_depth: float = Field(default=1000.0, ge=300, le=10000, description="Building depth in cm")
    road_width: float = Field(default=700.0, ge=300, le=5000, description="Road width in cm (350 per lane)")
    sidewalk: float = Field(default=250.0, ge=0, le=1000, description="Sidewalk width in cm (0 for none)")
    curb: float = Field(default=15.0, ge=0, le=50, description="Curb height in cm")
    floors_min: int = Field(default=2, ge=1, le=100, description="Lowest floor count to draw from")
    floors_max: int = Field(default=5, ge=1, le=100, description="Highest floor count to draw from")
    style: str = Field(default="flat", description="Building style: flat | brick | glass | classical")
    lamp_spacing: float = Field(default=1500.0, ge=200, le=10000, description="Lamp post spacing in cm")
    tree_spacing: float = Field(default=1000.0, ge=200, le=10000, description="Tree spacing in cm")
    lamps: bool = Field(default=True, description="Add lamp posts")
    trees: bool = Field(default=True, description="Add tree proxies")
    seed: int | None = Field(default=None, ge=0, description="Random seed for floors, roofs and tree heights")


class Opening(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wall: str = Field(..., description="front | back | left | right")
    kind: str = Field(default="door", description="door (90 x 210 on the floor) | window (120 x 150, sill 90)")
    width: float | None = Field(default=None, gt=0, description="Opening width in cm")
    height: float | None = Field(default=None, gt=0, description="Opening height in cm")
    sill: float | None = Field(default=None, ge=0, description="Bottom of the opening above the floor")
    offset: float = Field(default=0.0, description="Slide along the wall from its centre in cm")


class RoomShellInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="room", description="Base name")
    width: float = Field(default=500.0, ge=100, le=20000, description="Interior width (X) in cm")
    depth: float = Field(default=400.0, ge=100, le=20000, description="Interior depth (Z) in cm")
    height: float = Field(default=280.0, ge=100, le=2000, description="Interior height in cm")
    wall_thickness: float = Field(default=20.0, ge=1, le=200, description="Wall and slab thickness in cm")
    ceiling: bool = Field(default=True, description="Include a ceiling slab (false leaves the room open for cameras)")
    openings: List[Opening] | None = Field(default=None, description="Doors and windows to cut", examples=[[{"wall": "front", "kind": "door"}, {"wall": "left", "kind": "window", "offset": 80}]])


class StairsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="stairs", description="Base name")
    rise: float = Field(default=17.0, ge=5, le=40, description="Step rise in cm (17 is code typical)")
    run: float = Field(default=28.0, ge=15, le=60, description="Step run (tread depth) in cm")
    steps: int | None = Field(default=None, ge=1, le=400, description="Step count (default 12, ignored when total_rise is given)")
    total_rise: float | None = Field(default=None, gt=0, description="Height to climb in cm; step count is derived")
    width: float = Field(default=100.0, ge=30, le=2000, description="Stair width in cm")
    landing: float = Field(default=0.0, ge=0, le=1000, description="Landing depth at the top in cm (0 for none)")


class RailingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="railing", description="Base name")
    length: float = Field(default=300.0, ge=20, le=100000, description="Length along +X in cm (ignored with a curve)")
    height: float = Field(default=100.0, ge=20, le=300, description="Top rail height in cm")
    post_spacing: float = Field(default=120.0, ge=20, le=1000, description="Distance between posts in cm")
    post_diameter: float = Field(default=4.0, ge=1, le=30, description="Post diameter in cm")
    rail_diameter: float = Field(default=4.0, ge=1, le=30, description="Rail diameter in cm")
    mid_rails: int = Field(default=1, ge=0, le=10, description="Intermediate rails below the top rail")
    curve: str | None = Field(default=None, description="NURBS curve to follow instead of a straight run")


class PipesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="pipe", description="Base name")
    curve: str = Field(..., min_length=1, description="NURBS curve to sweep along")
    radius: float = Field(default=5.0, ge=0.2, le=500, description="Pipe radius in cm")
    segments: int = Field(default=12, ge=3, le=64, description="Sides around the pipe")
    count: int = Field(default=1, ge=1, le=50, description="Parallel pipes")
    spacing: float | None = Field(default=None, gt=0, description="Centre to centre spacing of parallel pipes (default 3 x radius)")
    divisions: int | None = Field(default=None, ge=1, le=500, description="Segments along the curve (default from arc length)")


class FenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="fence", description="Base name")
    length: float = Field(default=1000.0, ge=50, le=100000, description="Length along +X in cm")
    height: float = Field(default=120.0, ge=30, le=400, description="Fence height in cm")
    post_spacing: float = Field(default=200.0, ge=30, le=1000, description="Post spacing in cm")
    rails: int = Field(default=2, ge=1, le=6, description="Horizontal rails")
    pickets: bool = Field(default=True, description="Add vertical pickets")
    picket_width: float = Field(default=8.0, ge=1, le=50, description="Picket width in cm")
    picket_gap: float = Field(default=6.0, ge=0, le=100, description="Gap between pickets in cm")
    post_size: float = Field(default=10.0, ge=2, le=50, description="Square post size in cm")


class ColumnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="column", description="Base name")
    order: str = Field(default="doric", description="plain | tuscan | doric | ionic | corinthian")
    height: float = Field(default=400.0, ge=50, le=5000, description="Total height in cm")
    diameter: float | None = Field(default=None, ge=5, le=1000, description="Shaft diameter in cm (default from the order's proportion)")
    taper: float = Field(default=0.85, ge=0.5, le=1.0, description="Top diameter as a fraction of the bottom")


class FurnitureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, description="Base name (default: the kind)")
    kind: str = Field(default="table", description="table | chair | sofa | bed | desk | shelf | lamp")
    width: float | None = Field(default=None, ge=10, le=1000, description="Override width in cm")
    height: float | None = Field(default=None, ge=10, le=400, description="Override height in cm")
    depth: float | None = Field(default=None, ge=10, le=1000, description="Override depth in cm")


class VehicleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, description="Base name (default: the kind)")
    kind: str = Field(default="car", description="car | van | bus")
    length: float | None = Field(default=None, ge=100, le=3000, description="Override length in cm")
    width: float | None = Field(default=None, ge=50, le=400, description="Override width in cm")
    height: float | None = Field(default=None, ge=50, le=500, description="Override height in cm")


class TreeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="tree", description="Base name")
    height: float = Field(default=800.0, ge=50, le=10000, description="Total height in cm")
    canopy: str = Field(default="round", description="round | conical | columnar | umbrella")
    trunk_ratio: float = Field(default=0.3, ge=0.05, le=0.9, description="Trunk height as a fraction of the total")


class RockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="rock", description="Base name")
    size: float = Field(default=100.0, ge=1, le=10000, description="Overall size in cm")
    subdivisions: int = Field(default=12, ge=4, le=60, description="Sphere subdivisions before displacement")
    noise: float = Field(default=0.25, ge=0, le=1, description="Displacement amount as a fraction of size")
    seed: int | None = Field(default=None, ge=0, description="Random seed")
    flatten: float = Field(default=0.7, ge=0.2, le=1.5, description="Vertical squash (1 = round)")


class TerrainInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="terrain", description="Base name")
    width: float = Field(default=10000.0, ge=10, le=1000000, description="Width along X in cm")
    depth: float = Field(default=10000.0, ge=10, le=1000000, description="Depth along Z in cm")
    subdivisions: int = Field(default=50, ge=1, le=250, description="Subdivisions per axis")
    height: float = Field(default=300.0, ge=0, le=100000, description="Height amplitude in cm")
    octaves: int = Field(default=4, ge=1, le=8, description="Noise octaves (more = finer detail)")
    feature_size: float | None = Field(default=None, gt=0, description="Size of the largest hills in cm (default a quarter of the plane)")
    seed: int | None = Field(default=None, ge=0, description="Random seed")
    heightmap: str | None = Field(default=None, description="Path to an 8/16 bit PNG or PGM heightmap on the Maya machine (overrides noise)")


class ScatterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="scatter", description="Base name for instances and their group")
    sources: List[str] = Field(..., min_length=1, description="Nodes to instance (picked at random per point)", examples=[["tree_grp", "rock_grp"]])
    surface: str | None = Field(default=None, description="Mesh to scatter on; omit to use bounds on the ground plane")
    count: int | None = Field(default=None, ge=1, le=5000, description="Instances to place (default 50)")
    density: float | None = Field(default=None, ge=0, le=1000, description="Instances per square metre of surface (overrides count)")
    min_distance: float = Field(default=0.0, ge=0, description="Minimum spacing between instances in cm")
    align_to_normal: bool = Field(default=False, description="Tilt instances to the surface normal")
    rotation_random: float = Field(default=360.0, ge=0, le=360, description="Random yaw range in degrees")
    scale_range: List[float] | None = Field(default=None, min_length=2, max_length=2, description="Uniform scale range [min, max]", examples=[[0.8, 1.2]])
    bounds: List[List[float]] | None = Field(default=None, min_length=2, max_length=2, description="Ground rectangle [[x0, z0], [x1, z1]] when no surface is given")
    seed: int | None = Field(default=None, ge=0, description="Random seed")


class ArrayAlongCurveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="array", description="Base name")
    node: str = Field(..., min_length=1, description="Node to repeat")
    curve: str = Field(..., min_length=1, description="NURBS curve to follow")
    count: int = Field(default=10, ge=1, le=2000, description="Copies along the curve")
    align: bool = Field(default=True, description="Aim the forward axis along the curve tangent")
    instance: bool = Field(default=True, description="Instances instead of full copies")
    forward_axis: str = Field(default="x", description="Which local axis points along the curve: x | y | z")


class GridArrayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="grid", description="Base name")
    node: str = Field(..., min_length=1, description="Node to repeat")
    rows: int = Field(default=3, ge=1, le=200, description="Rows along Z")
    columns: int = Field(default=3, ge=1, le=200, description="Columns along X")
    spacing_x: float | None = Field(default=None, gt=0, description="Column spacing in cm (default 1.5 x the node width)")
    spacing_z: float | None = Field(default=None, gt=0, description="Row spacing in cm (default 1.5 x the node depth)")
    jitter: float = Field(default=0.0, ge=0, description="Random position jitter in cm")
    instance: bool = Field(default=True, description="Instances instead of full copies")
    seed: int | None = Field(default=None, ge=0, description="Random seed for the jitter")


def _dump(params: BaseModel) -> Dict[str, Any]:
    return params.model_dump()


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_procgen_building", annotations={"title": "Procedural building", **WRITE})
    async def maya_procgen_building(params: BuildingInput) -> str:
        """Build a parametric building at real scale: a subdivided box with a window grid cut
        by scaling and recessing faces, optional shopfront, door, cornice, mullions and a
        flat, parapet, pitched or hip roof. Use for city blockouts and photo matching; pass
        a footprint for non rectangular plans. Returns group, parts, bbox and stats."""
        return await ctx.run("procgen.building", _dump(params), timeout=120)

    @mcp.tool(name="maya_procgen_street_block", annotations={"title": "Procedural street block", **WRITE})
    async def maya_procgen_street_block(params: StreetBlockInput) -> str:
        """Lay out a street along X: road, raised sidewalks with a curb, a row of buildings on
        each side with seeded floor counts, plus lamp posts and tree proxies. Good for a
        first environment pass before dressing. Returns the group, per building info and stats."""
        return await ctx.run("procgen.street_block", _dump(params), timeout=600)

    @mcp.tool(name="maya_procgen_room_shell", annotations={"title": "Procedural room shell", **WRITE})
    async def maya_procgen_room_shell(params: RoomShellInput) -> str:
        """Build an interior room from closed slabs (floor, ceiling, four walls) so normals
        are correct from inside and outside, and cut doors and windows with booleans.
        Interior sizes are given; walls grow outwards. Returns group, parts, bbox, stats."""
        return await ctx.run("procgen.room_shell", _dump(params), timeout=120)

    @mcp.tool(name="maya_procgen_stairs", annotations={"title": "Procedural stairs", **WRITE})
    async def maya_procgen_stairs(params: StairsInput) -> str:
        """Solid straight stair climbing along +Z from rise/run and either a step count or
        the total height, with an optional landing. Returns angle and totals with the mesh."""
        return await ctx.run("procgen.stairs", _dump(params))

    @mcp.tool(name="maya_procgen_railing", annotations={"title": "Procedural railing", **WRITE})
    async def maya_procgen_railing(params: RailingInput) -> str:
        """Posts and rails, straight along +X or following a NURBS curve. Single combined mesh."""
        return await ctx.run("procgen.railing", _dump(params))

    @mcp.tool(name="maya_procgen_pipes_along_curve", annotations={"title": "Pipes along curve", **WRITE})
    async def maya_procgen_pipes_along_curve(params: PipesInput) -> str:
        """Sweep one or more round pipes along a NURBS curve (extrude with inputCurve).
        Parallel pipes are offset along X at the curve start."""
        return await ctx.run("procgen.pipes_along_curve", _dump(params))

    @mcp.tool(name="maya_procgen_fence", annotations={"title": "Procedural fence", **WRITE})
    async def maya_procgen_fence(params: FenceInput) -> str:
        """Fence along +X with square posts, horizontal rails and optional pickets, combined
        into one mesh. Turn pickets off for a ranch style rail fence."""
        return await ctx.run("procgen.fence", _dump(params))

    @mcp.tool(name="maya_procgen_column", annotations={"title": "Classical column", **WRITE})
    async def maya_procgen_column(params: ColumnInput) -> str:
        """Column proxy with a tapered shaft, base and an order specific capital (tuscan,
        doric, ionic, corinthian) or a plain cylinder. Diameter follows classical ratios
        unless given."""
        return await ctx.run("procgen.column", _dump(params))

    @mcp.tool(name="maya_procgen_furniture_proxy", annotations={"title": "Furniture proxy", **WRITE})
    async def maya_procgen_furniture_proxy(params: FurnitureInput) -> str:
        """Blockout furniture at real dimensions: table, chair, sofa, bed, desk, shelf, lamp.
        Use to dress interiors quickly before real assets arrive."""
        return await ctx.run("procgen.furniture_proxy", _dump(params))

    @mcp.tool(name="maya_procgen_vehicle_proxy", annotations={"title": "Vehicle proxy", **WRITE})
    async def maya_procgen_vehicle_proxy(params: VehicleInput) -> str:
        """Blockout car, van or bus at real size, facing +X, with wheels. Handy for scale
        checks in street scenes."""
        return await ctx.run("procgen.vehicle_proxy", _dump(params))

    @mcp.tool(name="maya_procgen_tree_proxy", annotations={"title": "Tree proxy", **WRITE})
    async def maya_procgen_tree_proxy(params: TreeInput) -> str:
        """Trunk plus canopy proxy (round, conical, columnar, umbrella) for layout and
        scattering. Swap for real foliage later."""
        return await ctx.run("procgen.tree_proxy", _dump(params))

    @mcp.tool(name="maya_procgen_rock", annotations={"title": "Procedural rock", **WRITE})
    async def maya_procgen_rock(params: RockInput) -> str:
        """Noise displaced, squashed sphere for rocks and boulders. Deterministic per seed."""
        return await ctx.run("procgen.rock", _dump(params))

    @mcp.tool(name="maya_procgen_terrain", annotations={"title": "Procedural terrain", **WRITE})
    async def maya_procgen_terrain(params: TerrainInput) -> str:
        """Plane displaced by layered value noise (deterministic per seed) or by a PNG/PGM
        heightmap. Returns the height range so you can place assets on it or scatter over it."""
        return await ctx.run("procgen.terrain", _dump(params), timeout=300)

    @mcp.tool(name="maya_procgen_scatter", annotations={"title": "Scatter instances", **WRITE})
    async def maya_procgen_scatter(params: ScatterInput) -> str:
        """Instance source nodes over a surface (random points on its faces, or by density per
        square metre) or over a ground rectangle, with minimum distance rejection, random yaw,
        scale range and optional normal alignment. Returns the group and sample positions."""
        return await ctx.run("procgen.scatter", _dump(params), timeout=300)

    @mcp.tool(name="maya_procgen_array_along_curve", annotations={"title": "Array along curve", **WRITE})
    async def maya_procgen_array_along_curve(params: ArrayAlongCurveInput) -> str:
        """Repeat a node evenly along a NURBS curve, optionally aiming its forward axis
        along the tangent. Use for lamp rows, fences on curves, convoys."""
        return await ctx.run("procgen.array_along_curve", _dump(params))

    @mcp.tool(name="maya_procgen_grid_array", annotations={"title": "Grid array", **WRITE})
    async def maya_procgen_grid_array(params: GridArrayInput) -> str:
        """Rows x columns copies of a node on the ground with optional seeded jitter.
        Spacing defaults to 1.5 x the node's footprint."""
        return await ctx.run("procgen.grid_array", _dump(params))
