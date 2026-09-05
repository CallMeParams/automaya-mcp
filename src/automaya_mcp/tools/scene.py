"""Scene tools: files, node queries, selection, hierarchy, attributes, undo, settings."""
from __future__ import annotations

from typing import Any, Dict, List, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

AttrValue = Union[float, int, bool, str, List[float], List[int], List[str]]


class NewSceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = Field(default=False, description="Discard unsaved changes without asking")


class OpenSceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Absolute path to a .ma or .mb file", min_length=1, examples=["/projects/shot010/anim_v03.ma"])
    force: bool = Field(default=False, description="Discard unsaved changes in the current scene")


class SaveSceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, description="Save As target (.ma or .mb). Omit to save in place; fails on an untitled scene.", examples=["/projects/shot010/anim_v04.mb"])
    as_ascii: bool = Field(default=False, description="Force Maya ASCII (.ma) even if the path says .mb")


class ImportFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="File to import: obj, fbx, abc, usd/usda/usdc/usdz, ma, mb, glb/gltf", min_length=1)
    namespace: str | None = Field(default=None, description="Put imported nodes in this namespace", examples=["car"])
    group_name: str | None = Field(default=None, description="Group the imported top nodes under a new transform with this name")


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Output file path; the extension picks the format unless format is given", min_length=1, examples=["/tmp/hero.fbx"])
    nodes: List[str] | None = Field(default=None, description="Nodes to export (default: current selection)")
    format: str | None = Field(default=None, description="fbx | obj | abc | usd | ma | mb (default: from extension)")
    animation: bool = Field(default=False, description="Bake and include animation (fbx/usd)")
    start: float | None = Field(default=None, description="Start frame for abc export")
    end: float | None = Field(default=None, description="End frame for abc export")


class ListNodesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str | None = Field(default=None, description="Maya node type filter, e.g. transform, mesh, camera, joint, light, nurbsCurve, lambert", examples=["mesh"])
    pattern: str | None = Field(default=None, description="Wildcard name pattern like 'pCube*' or '*_geo'", examples=["*_geo"])
    selection_only: bool = Field(default=False, description="Only list selected nodes")
    limit: int = Field(default=200, ge=1, le=5000, description="Page size")
    offset: int = Field(default=0, ge=0, description="Page start")
    long: bool = Field(default=True, description="Return full DAG paths")
    include_defaults: bool = Field(default=False, description="Include Maya's default nodes (persp, lambert1, time1 ...)")


class NodeInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node name or DAG path", min_length=1, examples=["pCube1"])
    attributes: List[str] | None = Field(default=None, description="Extra attributes to read, e.g. ['visibility', 'translateX']")


class SelectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to select (ignored with clear=true)")
    add: bool = Field(default=False, description="Add to the current selection instead of replacing it")
    clear: bool = Field(default=False, description="Clear the selection")


class NodesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Target nodes (default: current selection)")


class RenameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Node to rename")
    new_name: str = Field(..., min_length=1, description="New short name; Maya appends a number if it clashes", examples=["hero_geo"])


class ParentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] = Field(..., min_length=1, description="Nodes to reparent")
    parent: str | None = Field(default=None, description="New parent transform. Omit to unparent to world.")


class GroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Nodes to group. Omit for an empty group.")
    name: str | None = Field(default=None, description="Group name", examples=["props_grp"])


class SetAttrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Node name")
    attr: str = Field(..., min_length=1, description="Attribute name, e.g. translateX, visibility, translate (3 values), color", examples=["translateY"])
    value: AttrValue = Field(..., description="New value: number, bool, string, enum name, or a 2/3 element list for compound attributes")


class GetAttrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Node name")
    attr: str = Field(..., min_length=1, description="Attribute name", examples=["translate"])


class SetAttrsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1, description="Node name")
    attrs: Dict[str, Any] = Field(..., description="Mapping of attribute name to value", examples=[{"translateY": 2.0, "visibility": True}])


class ConnectAttrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str = Field(..., min_length=1, description="Source plug 'node.attr'", examples=["locator1.translate"])
    dst: str = Field(..., min_length=1, description="Destination plug 'node.attr'", examples=["pCube1.translate"])
    force: bool = Field(default=False, description="Replace an existing incoming connection")


class DisconnectAttrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str = Field(..., min_length=1, description="Source plug 'node.attr'")
    dst: str = Field(..., min_length=1, description="Destination plug 'node.attr'")


class UndoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1, le=100, description="How many steps")


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linear_unit: str | None = Field(default=None, description="mm | cm | m | km | in | ft | yd | mi")
    angle_unit: str | None = Field(default=None, description="deg | rad")
    time_unit: str | None = Field(default=None, description="Maya time unit: film (24), ntsc (30), pal (25), game (15), show (48), palf, ntscf, or '<n>fps'", examples=["film"])
    fps: float | None = Field(default=None, gt=0, description="Frame rate as a number (alternative to time_unit)", examples=[24])
    up_axis: str | None = Field(default=None, description="y | z")
    start: float | None = Field(default=None, description="Playback and animation start frame")
    end: float | None = Field(default=None, description="Playback and animation end frame")


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_new_scene", annotations={"title": "New scene", **DESTRUCTIVE})
    async def maya_new_scene(params: NewSceneInput) -> str:
        """Start a fresh empty scene. Refuses when there are unsaved changes unless
        force is true, so you never silently throw away the user's work."""
        return await ctx.run("scene.new", params.model_dump())

    @mcp.tool(name="maya_open_scene", annotations={"title": "Open scene file", **DESTRUCTIVE})
    async def maya_open_scene(params: OpenSceneInput) -> str:
        """Open a .ma/.mb file, replacing the current scene. Returns the top level
        nodes. Use maya_import_file to bring a file into the existing scene instead."""
        return await ctx.run("scene.open", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_save_scene", annotations={"title": "Save scene", **WRITE})
    async def maya_save_scene(params: SaveSceneInput) -> str:
        """Save the scene in place, or Save As when a path is given. Returns the
        saved path and file type."""
        return await ctx.run("scene.save", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_import_file", annotations={"title": "Import file", **WRITE})
    async def maya_import_file(params: ImportFileInput) -> str:
        """Import obj/fbx/abc/usd/ma/mb/glb into the open scene, loading the needed
        plugin automatically. Returns the new top level transforms, optionally
        grouped under group_name."""
        return await ctx.run("scene.import_file", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_export", annotations={"title": "Export nodes", **WRITE})
    async def maya_export(params: ExportInput) -> str:
        """Export nodes (or the selection) to fbx, obj, abc, usd, ma or mb. FBX and
        USD can bake animation; Alembic takes a frame range. Use this to hand
        geometry to Unreal or other DCCs."""
        return await ctx.run("scene.export", params.model_dump(), timeout=900.0)

    @mcp.tool(name="maya_get_scene_info", annotations={"title": "Scene overview", **READ})
    async def maya_get_scene_info() -> str:
        """Overview of the open scene: file name, unsaved flag, units, fps, up axis,
        frame range, node counts by type, current selection and references. Cheap;
        call it before planning multi step work."""
        return await ctx.run("scene.get_info")

    @mcp.tool(name="maya_list_nodes", annotations={"title": "List nodes", **READ})
    async def maya_list_nodes(params: ListNodesInput) -> str:
        """List scene nodes filtered by type and/or wildcard pattern, with pagination
        (total, has_more). Default nodes are hidden. Use it to find exact names
        before editing."""
        return await ctx.run("scene.list_nodes", params.model_dump())

    @mcp.tool(name="maya_get_node_info", annotations={"title": "Node details", **READ})
    async def maya_get_node_info(params: NodeInfoInput) -> str:
        """Details for one node: type, transform values, shapes, parent, children,
        assigned materials, incoming connection count, custom attributes and any
        extra attributes you ask for."""
        return await ctx.run("scene.get_node_info", params.model_dump())

    @mcp.tool(name="maya_select", annotations={"title": "Select nodes", **WRITE})
    async def maya_select(params: SelectInput) -> str:
        """Replace, add to, or clear the selection. Returns the resulting selection
        as long names."""
        return await ctx.run("scene.select", params.model_dump())

    @mcp.tool(name="maya_get_selection", annotations={"title": "Current selection", **READ})
    async def maya_get_selection() -> str:
        """Return what is currently selected in Maya (long names). Handy for acting
        on what the user picked."""
        return await ctx.run("scene.get_selection")

    @mcp.tool(name="maya_delete", annotations={"title": "Delete nodes", **DESTRUCTIVE})
    async def maya_delete(params: NodesInput) -> str:
        """Delete nodes (or the selection). Undoable with maya_undo."""
        return await ctx.run("scene.delete", params.model_dump())

    @mcp.tool(name="maya_rename", annotations={"title": "Rename node", **WRITE})
    async def maya_rename(params: RenameInput) -> str:
        """Rename a node. Returns the final long name (Maya may append a number)."""
        return await ctx.run("scene.rename", params.model_dump())

    @mcp.tool(name="maya_parent", annotations={"title": "Reparent nodes", **WRITE})
    async def maya_parent(params: ParentInput) -> str:
        """Parent nodes under another transform, or unparent them to world when
        parent is omitted. Returns the new long names."""
        return await ctx.run("scene.parent", params.model_dump())

    @mcp.tool(name="maya_group", annotations={"title": "Group nodes", **WRITE})
    async def maya_group(params: GroupInput) -> str:
        """Group nodes under a new transform (or make an empty group). Returns the
        group's long name and summary."""
        return await ctx.run("scene.group", params.model_dump())

    @mcp.tool(name="maya_set_attr", annotations={"title": "Set attribute", **WRITE})
    async def maya_set_attr(params: SetAttrInput) -> str:
        """Set one attribute with type detection: numbers, bools, strings, enum names
        and 2/3 element lists (translate, color). Fails clearly if the plug is
        locked or driven by a connection. Returns the value read back."""
        return await ctx.run("scene.set_attr", params.model_dump())

    @mcp.tool(name="maya_get_attr", annotations={"title": "Get attribute", **READ})
    async def maya_get_attr(params: GetAttrInput) -> str:
        """Read one attribute and its Maya type."""
        return await ctx.run("scene.get_attr", params.model_dump())

    @mcp.tool(name="maya_set_attrs", annotations={"title": "Set several attributes", **WRITE})
    async def maya_set_attrs(params: SetAttrsInput) -> str:
        """Set several attributes on one node in a single undo step. Same type
        handling as maya_set_attr."""
        return await ctx.run("scene.set_attrs", params.model_dump())

    @mcp.tool(name="maya_connect_attr", annotations={"title": "Connect attributes", **WRITE})
    async def maya_connect_attr(params: ConnectAttrInput) -> str:
        """Connect a source plug to a destination plug (node.attr). Use force to
        replace an existing input."""
        return await ctx.run("scene.connect_attr", params.model_dump())

    @mcp.tool(name="maya_disconnect_attr", annotations={"title": "Disconnect attributes", **WRITE})
    async def maya_disconnect_attr(params: DisconnectAttrInput) -> str:
        """Break the connection between two plugs."""
        return await ctx.run("scene.disconnect_attr", params.model_dump())

    @mcp.tool(name="maya_undo", annotations={"title": "Undo", **WRITE})
    async def maya_undo(params: UndoInput) -> str:
        """Undo the last operations. Each mutating tool call is one undo step."""
        return await ctx.run("scene.undo", params.model_dump())

    @mcp.tool(name="maya_redo", annotations={"title": "Redo", **WRITE})
    async def maya_redo(params: UndoInput) -> str:
        """Redo previously undone operations."""
        return await ctx.run("scene.redo", params.model_dump())

    @mcp.tool(name="maya_scene_settings", annotations={"title": "Scene settings", **WRITE})
    async def maya_scene_settings(params: SettingsInput) -> str:
        """Change linear/angle units, frame rate (time_unit like 'film' or fps like 30),
        up axis and the playback range. Only the fields you pass are changed;
        returns the resulting settings."""
        return await ctx.run("scene.settings", params.model_dump())
