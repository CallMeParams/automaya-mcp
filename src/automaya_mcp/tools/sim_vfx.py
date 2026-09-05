"""sim_vfx tools: nCloth, nParticles, fields, fluids, Bullet, nHair, caching,
Bifrost, MASH and quick previs FX presets. Each tool maps to one fx.* bridge
command; plugins are loaded on demand and a missing one comes back as a clear
error rather than a crash.
"""
from __future__ import annotations

from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

Vec3 = List[float]


class NClothInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(..., description="Polygon mesh (transform or shape) to turn into nCloth", examples=["pPlane1"])
    preset: str = Field(default="none", description="Maya nCloth preset: none, silk, tshirt, burlap, leather, thickLeather, heavyDenim, chainMail, rubberSheet, solidRubber, waterBalloon, plasticShell, concrete, putty, loosePlastic, softSheetMetal, airBag, beachBall, honey, lava")
    nucleus: str | None = Field(default=None, description="Existing nucleus solver to reuse (omit to use the active one or create a new one)")
    local_space: bool = Field(default=False, description="Use local space output (true) instead of world space")
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra nCloth attributes to set, e.g. {'stretchResistance': 40, 'thickness': 0.05}")


class NClothColliderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(..., description="Polygon mesh that cloth and particles should collide with", examples=["pSphere1"])
    nucleus: str | None = Field(default=None, description="Nucleus solver to attach the collider to")
    thickness: float | None = Field(default=None, ge=0, description="Collision thickness on the nRigid")


class NParticleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="nParticle1", description="Name for the particle transform", min_length=1)
    style: str = Field(default="points", description="Render style: points, balls, cloud, thick, water, streak, sprites")
    emitter_type: str = Field(default="omni", description="omni, directional or volume (cube volume emitter)")
    rate: float = Field(default=100.0, ge=0, description="Particles emitted per second")
    position: Vec3 | None = Field(default=None, description="Emitter position [x, y, z]", examples=[[0, 5, 0]])
    direction: Vec3 | None = Field(default=None, description="Direction for directional emitters, default [0, -1, 0]")
    speed: float = Field(default=1.0, ge=0, description="Emission speed")
    spread: float = Field(default=0.0, ge=0, le=1, description="Cone spread for directional emitters (0 to 1)")
    lifespan: float | None = Field(default=None, gt=0, description="Constant particle lifespan in seconds")
    nucleus: str | None = Field(default=None, description="Nucleus solver to reuse")
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra nParticleShape attributes, e.g. {'radius': 0.2, 'conserve': 0.98}")


class FieldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(default="gravity", description="gravity, turbulence, vortex, air, drag, radial, uniform, newton, volumeAxis")
    targets: List[str] | None = Field(default=None, description="Particles, nCloth, fluids or rigid bodies to connect the field to (omit for the selection, empty for none)")
    magnitude: float = Field(default=9.8, description="Field magnitude")
    attenuation: float = Field(default=0.0, ge=0, description="Falloff with distance (0 = none)")
    position: Vec3 | None = Field(default=None, description="Field position [x, y, z]")
    direction: Vec3 | None = Field(default=None, description="Direction for gravity / uniform fields")
    max_distance: float | None = Field(default=None, ge=0, description="Max distance of influence (omit for unlimited)")
    name: str | None = Field(default=None, description="Name for the field node")
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra field attributes, e.g. {'frequency': 2.0} for turbulence")


class FluidInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="3d", description="3d, 2d, ocean or pond")
    name: str | None = Field(default=None, description="Name for the fluid transform")
    resolution: List[int] | None = Field(default=None, description="Voxel resolution [x, y, z] (3d) or [x, y] (2d); default 10^3 / 40^2")
    size: Vec3 | None = Field(default=None, description="Container size in scene units, [x, y, z] or [x, y]")
    emitter: str = Field(default="point", description="point (omni emitter at position), mesh (surface emitter from emitter_mesh) or none")
    emitter_mesh: str | None = Field(default=None, description="Mesh to emit from when emitter='mesh'")
    position: Vec3 | None = Field(default=None, description="Container position [x, y, z]")
    density: float = Field(default=1.0, ge=0, description="Density emission rate")
    heat: float = Field(default=0.0, ge=0, description="Heat emission rate (needed for fire)")
    fuel: float = Field(default=0.0, ge=0, description="Fuel emission rate (needed for fire)")
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra fluidShape attributes, e.g. {'buoyancy': 3, 'dissipation': 0.1}")


class RigidBodyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Meshes to make rigid bodies (omit for the selection)")
    active: bool = Field(default=True, description="True for a dynamic body, false for a static collider")
    mass: float = Field(default=1.0, ge=0, description="Mass (ignored for static bodies)")
    friction: float = Field(default=0.5, ge=0, description="Surface friction")
    bounciness: float = Field(default=0.1, ge=0, description="Restitution")
    shape: str = Field(default="auto", description="Collider shape: auto (hull), box, sphere, hull, mesh, capsule, cylinder, plane")
    initial_velocity: Vec3 | None = Field(default=None, description="Initial velocity [x, y, z]")


class NHairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(..., description="Polygon mesh to grow hair on", examples=["pSphere1"])
    count: int = Field(default=64, ge=1, le=10000, description="Approximate follicle count (laid out as a square grid)")
    length: float = Field(default=5.0, gt=0, description="Hair length")
    points_per_hair: int = Field(default=10, ge=2, le=200, description="CV count per hair curve")
    preset: str | None = Field(default=None, description="hairSystem attribute preset name, if any")
    attrs: Dict[str, Any] | None = Field(default=None, description="Extra hairSystemShape attributes, e.g. {'hairsPerClump': 20, 'clumpWidth': 0.5}")


class InstancerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_nodes: List[str] = Field(..., min_length=1, description="Objects to instance onto each particle", examples=[["rock1", "rock2"]])
    particle: str = Field(..., description="Particle / nParticle system (transform or shape)")
    name: str = Field(default="instancer1", description="Instancer node name")
    cycle: bool = Field(default=False, description="Cycle through the sources sequentially instead of using the first")


class BakeSimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Transforms to bake (omit for the selection)")
    start: float | None = Field(default=None, description="Start frame (default: timeline start)")
    end: float | None = Field(default=None, description="End frame (default: timeline end)")
    attrs: List[str] | None = Field(default=None, description="Attributes to bake; default translate + rotate")
    sample_by: float = Field(default=1.0, gt=0, description="Sample every N frames")


class NCacheInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="nCloth / nParticle / hairSystem nodes (omit for the selection)")
    start: float | None = Field(default=None, description="Start frame (default: timeline start)")
    end: float | None = Field(default=None, description="End frame (default: timeline end)")
    directory: str | None = Field(default=None, description="Cache directory (default: project data/nCache)")
    name: str | None = Field(default=None, description="Cache file base name")
    one_file: bool = Field(default=True, description="One file for the whole range (false = one file per frame)")
    fmt: str = Field(default="mcx", description="mcx or mcc")


class AlembicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Output .abc path", examples=["/tmp/sim.abc"])
    nodes: List[str] | None = Field(default=None, description="Root nodes to export (omit for the selection)")
    start: float | None = Field(default=None, description="Start frame (default: timeline start)")
    end: float | None = Field(default=None, description="End frame (default: timeline end)")
    uv: bool = Field(default=True, description="Write UVs")
    world_space: bool = Field(default=True, description="Bake world space transforms")
    step: float = Field(default=1.0, gt=0, description="Frame step")
    strip_namespaces: bool = Field(default=False, description="Strip namespaces from node names")


class NucleusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nucleus: str | None = Field(default=None, description="Nucleus node (default: first in the scene)")
    gravity: float | None = Field(default=None, description="Gravity magnitude (Maya default 9.8)")
    gravity_direction: Vec3 | None = Field(default=None, description="Gravity direction [x, y, z]")
    air_density: float | None = Field(default=None, ge=0)
    wind_speed: float | None = Field(default=None, ge=0)
    wind_direction: Vec3 | None = Field(default=None)
    wind_noise: float | None = Field(default=None, ge=0)
    time_scale: float | None = Field(default=None, gt=0)
    substeps: int | None = Field(default=None, ge=1, le=64)
    max_collision_iterations: int | None = Field(default=None, ge=1)
    start_frame: float | None = Field(default=None)
    space_scale: float | None = Field(default=None, gt=0, description="Scene unit scale: 0.01 for cm scenes modelled at real size")
    enable: bool | None = Field(default=None, description="Enable or disable the solver")


class ListDynamicsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: List[str] | None = Field(default=None, description="Restrict to groups: nuclei, ncloth, nrigid, nparticles, fluids, hair, bullet, fields, emitters, instancers, caches, bifrost, mash")


class RunSimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float | None = Field(default=None, description="Start frame (default: timeline start)")
    end: float | None = Field(default=None, description="End frame (default: timeline end)")
    step: float = Field(default=1.0, gt=0, description="Frame step")
    max_frames: int = Field(default=5000, ge=1, description="Safety cap on frames evaluated")
    timeout: float = Field(default=600.0, ge=1, le=3600, description="Seconds to wait")


class DeleteDynamicsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Dynamics nodes to delete (omit for the selection)")
    all: bool = Field(default=False, description="Delete every dynamics node in the scene")


class BifrostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="bifrostGraph1", min_length=1, description="Graph name")


class MashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Meshes to scatter (omit for the selection)")
    count: int = Field(default=10, ge=1, le=100000, description="Point count")
    distribution: str = Field(default="linear", description="linear, radial, spherical, random, grid or mesh")
    name: str = Field(default="MASH1", min_length=1, description="Network name")
    geometry_type: str = Field(default="instancer", description="instancer (fast) or mesh (repro mesh)")


class ExplosionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="explosion", min_length=1)
    position: Vec3 | None = Field(default=None, description="Origin [x, y, z]")
    scale: float = Field(default=1.0, gt=0, description="Overall size multiplier")
    frames: int = Field(default=30, ge=4, description="Rough duration in frames")


class DustInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="dust", min_length=1)
    position: Vec3 | None = Field(default=None, description="Origin [x, y, z]")
    scale: float = Field(default=1.0, gt=0)
    rate: float = Field(default=60.0, ge=0, description="Particles per second")


class PrecipitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="rain", description="rain or snow")
    name: str | None = Field(default=None)
    position: Vec3 | None = Field(default=None, description="Emitter centre; default [0, height, 0]")
    width: float = Field(default=20.0, gt=0, description="Emitter footprint")
    height: float = Field(default=15.0, description="Emitter height when position is omitted")
    rate: float = Field(default=500.0, ge=0)


class DebrisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="debris", min_length=1)
    position: Vec3 | None = Field(default=None, description="Origin [x, y, z]")
    count: int = Field(default=60, ge=1, le=5000, description="Chunk count")
    scale: float = Field(default=1.0, gt=0)
    use_bullet: bool = Field(default=False, description="True: real cube chunks with Bullet rigid bodies. False: instanced cubes on particles")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_create_ncloth", annotations={"title": "Create nCloth", **WRITE})
    async def maya_create_ncloth(params: NClothInput) -> str:
        """Turn a polygon mesh into simulated nCloth, optionally applying a Maya
        fabric preset (silk, tshirt, burlap, leather...). Reuses a nucleus solver
        when given. Returns the nCloth shape, its nucleus and any attrs applied.
        Add colliders with maya_create_ncloth_collider and scrub with
        maya_run_simulation."""
        return await ctx.run("fx.create_ncloth", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_create_ncloth_collider", annotations={"title": "Create nucleus collider", **WRITE})
    async def maya_create_ncloth_collider(params: NClothColliderInput) -> str:
        """Make a mesh a passive collider (nRigid) so nCloth and nParticles bounce
        off it. Returns the nRigid node."""
        return await ctx.run("fx.create_ncloth_collider", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_create_nparticle", annotations={"title": "Create nParticles", **WRITE})
    async def maya_create_nparticle(params: NParticleInput) -> str:
        """Create an nParticle system with an omni, directional or volume emitter and
        a render style (points, balls, cloud, thick, water, streak). Returns the
        particle shape, emitter and nucleus. Use maya_add_field for gravity, wind
        and turbulence."""
        return await ctx.run("fx.create_nparticle", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_add_field", annotations={"title": "Add dynamics field", **WRITE})
    async def maya_add_field(params: FieldInput) -> str:
        """Create a dynamics field (gravity, turbulence, vortex, air, drag, radial,
        uniform, newton, volumeAxis) and connect it to particles, nCloth, fluids or
        rigid bodies. Pass targets=[] to create it unconnected. Returns the field
        node and what it was wired to."""
        return await ctx.run("fx.add_field", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_create_fluid", annotations={"title": "Create fluid", **WRITE})
    async def maya_create_fluid(params: FluidInput) -> str:
        """Create a 3D or 2D fluid container (smoke, fire when heat and fuel are set),
        an ocean or a pond. Optionally adds a point emitter or a surface emitter
        from a mesh. Returns the fluidShape, transform and emitter. Fluids are
        heavy: keep resolution modest for previs."""
        return await ctx.run("fx.create_fluid", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_create_rigid_body", annotations={"title": "Create Bullet rigid body", **WRITE})
    async def maya_create_rigid_body(params: RigidBodyInput) -> str:
        """Create Bullet rigid bodies for meshes (active = falls and collides, inactive
        = static ground). Loads the bullet plugin on demand and errors clearly if it
        is missing. Returns each body's bulletRigidBodyShape and the solver. Bake
        with maya_bake_simulation when the sim looks right."""
        return await ctx.run("fx.create_rigid_body", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_create_nhair", annotations={"title": "Create nHair", **WRITE})
    async def maya_create_nhair(params: NHairInput) -> str:
        """Grow dynamic nHair on a mesh as a square grid of follicles with the given
        length. Returns the hairSystem and follicle count. Set attrs like
        hairsPerClump and clumpWidth for the look."""
        return await ctx.run("fx.create_nhair", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_create_instancer", annotations={"title": "Instance geometry on particles", **WRITE})
    async def maya_create_instancer(params: InstancerInput) -> str:
        """Instance source objects onto every particle of a particle system
        (particleInstancer). Good for debris, crowds and scattered props that
        follow a sim. Returns the instancer node."""
        return await ctx.run("fx.create_instancer", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_bake_simulation", annotations={"title": "Bake simulation to keys", **WRITE})
    async def maya_bake_simulation(params: BakeSimInput) -> str:
        """Bake simulated transforms (Bullet bodies, dynamics driven objects) to
        keyframes over a frame range using bakeResults with simulation on.
        Slow for long ranges; the timeout scales with the range."""
        return await ctx.run("fx.bake_simulation", params.model_dump(), timeout=900.0)

    @mcp.tool(name="maya_cache_ncache", annotations={"title": "Create nCache", **WRITE})
    async def maya_cache_ncache(params: NCacheInput) -> str:
        """Write an nCache for nCloth, nParticle or hairSystem nodes so playback no
        longer resimulates. Returns the cacheFile nodes and the MEL used."""
        return await ctx.run("fx.cache_ncache", params.model_dump(), timeout=1800.0)

    @mcp.tool(name="maya_cache_alembic", annotations={"title": "Export Alembic cache", **WRITE})
    async def maya_cache_alembic(params: AlembicInput) -> str:
        """Export nodes to an Alembic (.abc) cache over a frame range with AbcExport,
        loading the plugin on demand. Use for handing simulated meshes to Unreal,
        Houdini or a render farm. Returns the path, roots and job string."""
        return await ctx.run("fx.cache_alembic", params.model_dump(), timeout=1800.0)

    @mcp.tool(name="maya_set_nucleus", annotations={"title": "Set nucleus solver settings", **WRITE})
    async def maya_set_nucleus(params: NucleusInput) -> str:
        """Set nucleus solver attributes: gravity and its direction, air density,
        wind, time scale, substeps, start frame, space scale, enable. Only the
        values you pass change. Defaults to the first nucleus in the scene."""
        return await ctx.run("fx.set_nucleus", params.model_dump())

    @mcp.tool(name="maya_list_dynamics", annotations={"title": "List dynamics nodes", **READ})
    async def maya_list_dynamics(params: ListDynamicsInput) -> str:
        """List every dynamics node in the scene grouped by kind (nuclei, nCloth,
        nRigid, nParticles, fluids, hair, Bullet, fields, emitters, instancers,
        caches, Bifrost, MASH) with the solver each belongs to."""
        return await ctx.run("fx.list_dynamics", params.model_dump())

    @mcp.tool(name="maya_run_simulation", annotations={"title": "Run simulation", **WRITE})
    async def maya_run_simulation(params: RunSimInput) -> str:
        """Scrub the timeline frame by frame so every simulation evaluates, then
        report total time, average and slowest frame. Use after setting up
        dynamics and before screenshots, baking or caching."""
        return await ctx.run("fx.run_simulation", {k: v for k, v in params.model_dump().items() if k != "timeout"}, timeout=params.timeout)

    @mcp.tool(name="maya_delete_dynamics", annotations={"title": "Delete dynamics nodes", **DESTRUCTIVE})
    async def maya_delete_dynamics(params: DeleteDynamicsInput) -> str:
        """Delete dynamics nodes (nCloth, particles, fluids, fields, emitters,
        Bullet bodies, instancers) or all of them with all=true. The source meshes
        are kept. Undoable."""
        return await ctx.run("fx.delete_dynamics", params.model_dump())

    @mcp.tool(name="maya_create_bifrost_graph", annotations={"title": "Create Bifrost graph", **WRITE})
    async def maya_create_bifrost_graph(params: BifrostInput) -> str:
        """Create an empty Bifrost graph (bifrostGraphShape), loading the plugin on
        demand, and return a hint on adding compounds with vnnCompound and
        vnnConnect through maya_execute_python."""
        return await ctx.run("fx.create_bifrost_graph", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_create_mash_network", annotations={"title": "Create MASH network", **WRITE})
    async def maya_create_mash_network(params: MashInput) -> str:
        """Scatter meshes with a MASH network: point count and a linear, radial,
        spherical, random, grid or mesh distribution. Errors clearly when the
        MASH plugin or python API is missing. Returns the waiter, distribute and
        instancer nodes."""
        return await ctx.run("fx.create_mash_network", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_fx_explosion_preset", annotations={"title": "Previs explosion preset", **WRITE})
    async def maya_fx_explosion_preset(params: ExplosionInput) -> str:
        """Build a quick previs explosion at a point: a burst of ball particles, a
        fluid fireball with a keyed emitter, gravity and turbulence, all grouped.
        Then call maya_run_simulation and tune the fluid shading."""
        return await ctx.run("fx.create_explosion_preset", params.model_dump(), timeout=180.0)

    @mcp.tool(name="maya_fx_dust_preset", annotations={"title": "Previs dust preset", **WRITE})
    async def maya_fx_dust_preset(params: DustInput) -> str:
        """Build drifting dust: slow cloud particles from a volume emitter with a light
        wind and turbulence, grouped under one node."""
        return await ctx.run("fx.create_dust_preset", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_fx_precipitation_preset", annotations={"title": "Previs rain or snow preset", **WRITE})
    async def maya_fx_precipitation_preset(params: PrecipitationInput) -> str:
        """Build rain (fast streaks under strong gravity) or snow (slow points with
        turbulence and drag) from a volume emitter above the scene, grouped."""
        return await ctx.run("fx.create_precipitation_preset", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_fx_debris_preset", annotations={"title": "Previs debris preset", **WRITE})
    async def maya_fx_debris_preset(params: DebrisInput) -> str:
        """Build a debris burst: small cubes instanced onto an nParticle burst with
        gravity, or, with use_bullet, real cube chunks as Bullet rigid bodies.
        Returns the chunk, particle, instancer and fields."""
        return await ctx.run("fx.create_debris_preset", params.model_dump(), timeout=180.0)
