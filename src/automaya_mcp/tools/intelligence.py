"""Scene intelligence tools: see the viewport, summarise the scene, diff it,
lint it, and describe nodes in plain language."""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import READ, ToolContext

CHECK_NAMES = "non_manifold, lamina, zero_area, ngons, unfrozen_transforms, non_uniform_scale, missing_textures, duplicate_names, empty_groups, unused_materials, construction_history, far_from_origin, bbox_scale"


class ScreenshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to look through (e.g. 'persp', 'shotCam'). Default: the active panel's camera", examples=["persp"])
    width: int = Field(default=1280, ge=64, le=4096, description="Image width in pixels")
    height: int = Field(default=720, ge=64, le=4096, description="Image height in pixels")
    panel: str | None = Field(default=None, description="Model panel name, e.g. 'modelPanel4'. Default: the focused or first visible model panel")
    display_mode: str | None = Field(default=None, description="wireframe | shaded | textured | flat | boundingbox. Default: leave the panel as is")


class SceneSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_nodes: int = Field(default=200, ge=1, le=5000, description="Token budget: stop after this many nodes")
    depth: int = Field(default=3, ge=0, le=20, description="How many hierarchy levels below the assemblies to include")
    include_attrs: bool = Field(default=False, description="Add translate/rotate/scale to every node")


class SnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, description="Optional label, e.g. 'before layout pass'", max_length=120)


class DiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(..., description="Id returned by maya_scene_snapshot, e.g. 'snap_1'", examples=["snap_1"])
    snapshot_b: str | None = Field(default=None, description="Compare against this snapshot instead of the live scene")
    tolerance: float = Field(default=1e-4, ge=0, description="Ignore transform deltas smaller than this")
    max_items: int = Field(default=200, ge=1, le=5000, description="Cap per category")


class FindProblemsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: List[str] | None = Field(default=None, description="Subset of checks to run; default all. Valid: " + CHECK_NAMES)
    nodes: List[str] | None = Field(default=None, description="Limit the lint to these nodes and their descendants; default whole scene")
    far_threshold: float = Field(default=10000.0, gt=0, description="Distance from origin (scene units) that counts as far")
    max_per_check: int = Field(default=50, ge=1, le=1000, description="Cap findings per check")


class HistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Transform or shape whose construction history to list", examples=["pCube1"])
    max_attrs: int = Field(default=8, ge=0, le=50, description="Keyable attributes to read per history node")


class BBoxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to measure; default the selection")


class DescribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node to describe", examples=["|group1|pSphere1"])


class CountPolysInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Count only these nodes (and children); default whole scene")
    top: int = Field(default=20, ge=1, le=500, description="How many of the heaviest meshes to list")


class InspectSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_components: int = Field(default=50, ge=0, le=5000, description="How many component names to include as a sample")


class VisibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_items: int = Field(default=100, ge=1, le=5000, description="Cap per category")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_viewport_screenshot", annotations={"title": "Screenshot the viewport", **READ})
    async def maya_viewport_screenshot(params: ScreenshotInput):
        """Capture the Maya viewport as a PNG image (one frame offscreen playblast) so
        you can see what the user sees. Use it to verify modeling, layout and lighting
        results. Needs an interactive Maya (not mayapy); for batch use maya_render_frame.
        Returns the image plus camera, panel and path."""
        return await ctx.image("intel.viewport_screenshot", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_scene_summary", annotations={"title": "Summarise the scene", **READ})
    async def maya_scene_summary(params: SceneSummaryInput) -> str:
        """Token budgeted, hierarchical picture of the scene: assemblies and children
        with kind (mesh/camera/light/group), face counts, bounding boxes, materials,
        visibility and animated flags, plus scene totals and units. Call it at the
        start of a task and after big changes. Raise depth/max_nodes to see more."""
        return await ctx.run("intel.scene_summary", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_scene_snapshot", annotations={"title": "Snapshot the scene for diffing", **READ})
    async def maya_scene_snapshot(params: SnapshotInput) -> str:
        """Record every transform's type, parent, translate/rotate/scale and face count
        and return a snapshot id. Take one before a risky operation or before handing
        control to the user, then call maya_scene_diff to learn exactly what changed."""
        return await ctx.run("intel.snapshot", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_scene_diff", annotations={"title": "Diff the scene against a snapshot", **READ})
    async def maya_scene_diff(params: DiffInput) -> str:
        """Compare a snapshot with the live scene (or another snapshot): nodes added,
        removed, moved (t/r/s deltas) and changed (parent, kind, face count). Cheap way
        to confirm an operation did what you expected or to see the user's edits."""
        return await ctx.run("intel.diff", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_find_problems", annotations={"title": "Lint the scene", **READ})
    async def maya_find_problems(params: FindProblemsInput) -> str:
        """Scene lint: non manifold and lamina geometry, ngons, zero area faces,
        unfrozen transforms, non uniform or negative scale, missing textures,
        duplicate names, empty groups, unused materials, construction history, far
        from origin nodes and suspicious bounding box sizes. Every finding names the
        node and a fix. Run before export, rigging or hand off to Unreal."""
        return await ctx.run("intel.find_problems", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_inspect_selection", annotations={"title": "Inspect the selection", **READ})
    async def maya_inspect_selection(params: InspectSelectionInput) -> str:
        """Rich view of what the user has selected: objects with kind, transforms,
        faces, materials and bounds, or component selections (vertex/edge/face/uv
        counts, converted counts, bounds). Use it when the user says 'this' or 'these'."""
        return await ctx.run("intel.inspect_selection", params.model_dump())

    @mcp.tool(name="maya_get_history_stack", annotations={"title": "Construction history of a node", **READ})
    async def maya_get_history_stack(params: HistoryInput) -> str:
        """List the construction history nodes behind a mesh (polyCube, polyExtrude,
        bevel...) oldest first with their key attribute values. Use it to tweak an
        earlier modeling step instead of redoing it, or to decide whether to delete history."""
        return await ctx.run("intel.get_history_stack", params.model_dump())

    @mcp.tool(name="maya_get_bounding_box", annotations={"title": "World bounding box", **READ})
    async def maya_get_bounding_box(params: BBoxInput) -> str:
        """World space bounding box (min, max, size, center) of nodes or the selection,
        combined and per node, in scene units. Use it for placement and scale checks."""
        return await ctx.run("intel.get_bounding_box", params.model_dump())

    @mcp.tool(name="maya_describe_node", annotations={"title": "Describe a node in plain language", **READ})
    async def maya_describe_node(params: DescribeInput) -> str:
        """One paragraph describing a node: kind, hierarchy, mesh stats, materials,
        history, size, transform and state, plus the same facts as a dict. Good for
        explaining a scene element to the user or grounding your own reasoning."""
        return await ctx.run("intel.describe_for_llm", params.model_dump())

    @mcp.tool(name="maya_count_polys", annotations={"title": "Polygon counts", **READ})
    async def maya_count_polys(params: CountPolysInput) -> str:
        """Face, triangle, vertex and edge totals for the scene or given nodes, with
        the heaviest meshes listed first. Use it to check budgets before export or
        to pick candidates for maya_poly_reduce."""
        return await ctx.run("intel.count_polys", params.model_dump(), timeout=120.0)

    @mcp.tool(name="maya_visibility_report", annotations={"title": "Why is something not visible", **READ})
    async def maya_visibility_report(params: VisibilityInput) -> str:
        """Explain missing objects: hidden nodes, nodes hidden by a parent, hidden or
        templated display layers, lod visibility, template/reference overrides,
        intermediate shapes and isolate select panels. Use it when a screenshot
        does not show what the scene summary says exists."""
        return await ctx.run("intel.visibility_report", params.model_dump())
