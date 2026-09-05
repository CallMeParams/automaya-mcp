"""Introspection tools: what this Maya knows and can do. Commands, node types,
plugins, environment, UI, hotkeys, optionVars, workspace and offline docs."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import READ, ToolContext


class ListCommandsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = Field(default="", description="Only commands starting with this, e.g. 'poly'", max_length=64)
    contains: str | None = Field(default=None, description="Only commands containing this substring (case insensitive)", max_length=64)
    limit: int = Field(default=200, ge=1, le=2000, description="Page size")
    offset: int = Field(default=0, ge=0, description="Page offset")


class CommandHelpInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_name: str = Field(..., description="A maya.cmds command name", examples=["polyExtrudeFacet"], pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)


class NodeSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_type: str = Field(..., description="Concrete node type, e.g. 'polySphere', 'mesh', 'aiStandardSurface'", examples=["polySphere"], pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=80)
    max_attrs: int = Field(default=300, ge=1, le=2000, description="Cap on attributes returned")
    keyable_only: bool = Field(default=False, description="Only keyable (channel box) attributes")


class ListNodeTypesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: str | None = Field(default=None, description="Substring filter, e.g. 'light'", max_length=64)
    plugin_only: bool = Field(default=False, description="Only types provided by loaded plugins")
    include_inheritance: bool = Field(default=False, description="Add the inheritance chain and providing plugin per type (slower)")
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class PluginInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="Plugin name, e.g. 'mtoa', 'mayaUsdPlugin', 'fbxmaya'", examples=["mtoa"], max_length=120)


class ListPluginsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loaded_only: bool = Field(default=True, description="False also scans the plugin path for loadable plugins")
    details: bool = Field(default=False, description="Include commands and node types per loaded plugin")


class UiTreeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str | None = Field(default=None, description="A window, layout or menu name to expand; default lists windows and panels")
    depth: int = Field(default=2, ge=0, le=10)
    max_nodes: int = Field(default=500, ge=1, le=5000)


class ListMenusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    main_window_only: bool = Field(default=True, description="Only the main window menu bar; False lists every menu")
    limit: int = Field(default=200, ge=1, le=5000)


class HotkeysInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str | None = Field(default=None, description="Substring to match in name, key or annotation", max_length=64)
    limit: int = Field(default=100, ge=1, le=2000)


class OptionVarsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = Field(default="", description="Only optionVars starting with this, e.g. 'render'", max_length=64)
    limit: int = Field(default=200, ge=1, le=5000)
    with_values: bool = Field(default=True, description="Include values (False lists names only)")


class SearchDocsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., description="Words to search for in command names, flags and synopses", examples=["bevel edge"], min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=200)
    rebuild: bool = Field(default=False, description="Force rebuilding the offline index (slow, one cmds.help per command)")


class ApiHintInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(..., description="cmds command, node type or OpenMaya class name (e.g. 'MFnMesh')", examples=["MFnMesh"], min_length=1, max_length=120)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_list_commands", annotations={"title": "List maya.cmds commands", **READ})
    async def maya_list_commands(params: ListCommandsInput) -> str:
        """Page through the maya.cmds command names available in this Maya, filtered by
        prefix or substring. Use it before writing maya_execute_python code when you are
        unsure a command exists; follow up with maya_command_help for its flags."""
        return await ctx.run("introspect.list_commands", params.model_dump())

    @mcp.tool(name="maya_command_help", annotations={"title": "Help for one command", **READ})
    async def maya_command_help(params: CommandHelpInput) -> str:
        """Synopsis and parsed flag list (short, long, argument types) for a cmds command
        straight from Maya, plus the documentation URL. Cheaper and more accurate than
        guessing flags."""
        return await ctx.run("introspect.command_help", params.model_dump())

    @mcp.tool(name="maya_node_type_schema", annotations={"title": "Attributes of a node type", **READ})
    async def maya_node_type_schema(params: NodeSchemaInput) -> str:
        """Every attribute of a node type with type, keyable flag, default, min/max,
        enum values and compound children, plus the inheritance chain. Maya creates a
        temporary node with undo paused and deletes it; the scene is left unchanged.
        Use it before maya_set_attr on unfamiliar node types."""
        return await ctx.run("introspect.node_type_schema", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_list_node_types", annotations={"title": "List node types", **READ})
    async def maya_list_node_types(params: ListNodeTypesInput) -> str:
        """Concrete node types this Maya can create, filtered by substring, optionally
        only plugin provided ones and with inheritance chains. Use it to find the right
        type name for maya_execute_python createNode calls."""
        return await ctx.run("introspect.list_node_types", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_plugin_info", annotations={"title": "Plugin details", **READ})
    async def maya_plugin_info(params: PluginInfoInput) -> str:
        """Loaded state, version, path, vendor, commands and node types of one plugin."""
        return await ctx.run("introspect.plugin_info", params.model_dump())

    @mcp.tool(name="maya_list_plugins", annotations={"title": "List plugins", **READ})
    async def maya_list_plugins(params: ListPluginsInput) -> str:
        """Loaded plugins with versions, and optionally every loadable plugin found on
        the plugin path. Check here before relying on Arnold, USD, FBX, Bullet or Bifrost."""
        return await ctx.run("introspect.list_plugins", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_env_info", annotations={"title": "Maya environment", **READ})
    async def maya_env_info() -> str:
        """Maya version and API, Python, OS, MAYA_APP_DIR, modules, script paths,
        workspace and image directories, units, current renderer, Qt/PySide version
        and whether OpenMaya 2 is importable. Use it to tailor code to this install."""
        return await ctx.run("introspect.env_info")

    @mcp.tool(name="maya_ui_tree", annotations={"title": "UI tree", **READ})
    async def maya_ui_tree(params: UiTreeInput) -> str:
        """Windows and panels, or the child controls of one window/layout/menu, as a
        tree with types, labels and visibility. Use it to find panel names for
        screenshots or to drive UI through maya_execute_python."""
        return await ctx.run("introspect.ui_tree", params.model_dump())

    @mcp.tool(name="maya_list_menus", annotations={"title": "List menus", **READ})
    async def maya_list_menus(params: ListMenusInput) -> str:
        """Menus with labels and item counts (main window menu bar by default)."""
        return await ctx.run("introspect.list_menus", params.model_dump())

    @mcp.tool(name="maya_list_panels", annotations={"title": "List panels", **READ})
    async def maya_list_panels() -> str:
        """Every UI panel with type, visibility, camera for model panels, and which
        panel has focus. Use it to pick a panel for maya_viewport_screenshot."""
        return await ctx.run("introspect.list_panels")

    @mcp.tool(name="maya_hotkeys", annotations={"title": "Hotkeys", **READ})
    async def maya_hotkeys(params: HotkeysInput) -> str:
        """Best effort list of hotkeys in the current hotkey set (name, key, modifiers,
        annotation). Useful when telling the user which key does something."""
        return await ctx.run("introspect.hotkeys", params.model_dump(), timeout=60.0)

    @mcp.tool(name="maya_option_vars", annotations={"title": "optionVars", **READ})
    async def maya_option_vars(params: OptionVarsInput) -> str:
        """Maya preference optionVars matching a prefix, with values. Read only."""
        return await ctx.run("introspect.option_vars", params.model_dump())

    @mcp.tool(name="maya_workspace_info", annotations={"title": "Project / workspace", **READ})
    async def maya_workspace_info() -> str:
        """Current project root, file rules (scenes, images, sourceimages...), recent
        files and whether the open scene has unsaved changes. Use it to decide where
        to save exports and textures."""
        return await ctx.run("introspect.workspace_info")

    @mcp.tool(name="maya_search_docs", annotations={"title": "Search command docs offline", **READ})
    async def maya_search_docs(params: SearchDocsInput) -> str:
        """Search an offline index of every cmds command name, flag list and synopsis
        (built inside Maya on first use and cached). Returns ranked commands with
        doc URLs. Use it when you know what you want to do but not the command name."""
        return await ctx.run("introspect.search_docs", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_api_reference_hint", annotations={"title": "Doc URLs for a topic", **READ})
    async def maya_api_reference_hint(params: ApiHintInput) -> str:
        """Return the Maya 2024 documentation URLs for a cmds command, node type or
        OpenMaya 2 class (and the URL patterns) so you can fetch the reference page."""
        return await ctx.run("introspect.api_reference_hint", params.model_dump())
