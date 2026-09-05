"""Previs tools: cameras, lenses, shot rigs, playblasts, camera sequencer, turntables."""
from __future__ import annotations

from typing import Any, List, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

Point = Union[str, List[float]]


class CreateCameraInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="shotCam", min_length=1, description="Camera name, e.g. 'sh010_cam'")
    focal_length: float = Field(default=35.0, gt=0, description="Lens in mm")
    sensor_width: float = Field(default=36.0, gt=0, description="Sensor width in mm (36 full frame, 24.89 Alexa Mini 16:9, 23.76 Super 35)")
    sensor_height: float | None = Field(default=None, gt=0, description="Sensor height in mm; derived from aspect (or 16:9) when omitted")
    aspect: float | None = Field(default=None, gt=0, description="Sensor aspect used when sensor_height is omitted, e.g. 2.39")
    near_clip: float = Field(default=1.0, gt=0)
    far_clip: float = Field(default=100000.0, gt=0)
    translate: List[float] | None = Field(default=None, description="World position [x, y, z]")
    rotate: List[float] | None = Field(default=None, description="Rotation [x, y, z] degrees (ignored when aim is given)")
    aim: Point | None = Field(default=None, description="Node name or [x, y, z] the camera should look at")
    film_fit: str = Field(default="horizontal", description="fill, horizontal, vertical or overscan")
    display_resolution: bool = Field(default=True, description="Show the resolution gate")
    display_film_gate: bool = Field(default=False)
    display_safe_action: bool = Field(default=True)
    display_safe_title: bool = Field(default=False)
    overscan: float = Field(default=1.3, ge=1.0, le=3.0, description="Viewport overscan around the gate")
    locked: bool = Field(default=False, description="Lock transform channels")
    notes: str | None = Field(default=None, description="Shot notes stored on the camera")


class ShotRigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="shotCam", min_length=1, description="Rig prefix; nodes become <name>_shot_ctrl, <name>_cam ...")
    rig_type: str = Field(default="aim", description="aim: camera group plus aim/up locators. crane: dolly > crane_base > arm > head > camera")
    focal_length: float = Field(default=35.0, gt=0)
    sensor_width: float = Field(default=36.0, gt=0)
    sensor_height: float | None = Field(default=None, gt=0)
    translate: List[float] | None = Field(default=None, description="Camera start position")
    aim: Point | None = Field(default=None, description="Node or point to look at")
    arm_length: float = Field(default=300.0, gt=0, description="crane only: arm length in scene units")
    lock_channels: bool = Field(default=True, description="Lock channels that a real rig would not expose")


class SetLensInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera transform or shape")
    focal_length: float | None = Field(default=None, gt=0, description="mm")
    field_of_view: float | None = Field(default=None, gt=0, lt=180, description="Horizontal FOV in degrees (alternative to focal_length)")
    f_stop: float | None = Field(default=None, gt=0)
    focus_distance: float | None = Field(default=None, gt=0, description="Scene units")
    depth_of_field: bool | None = Field(default=None, description="Enable DOF on the camera")


class ListCamerasInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_default: bool = Field(default=False, description="Include persp/top/front/side")


class LookThroughInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera to look through")
    panel: str | None = Field(default=None, description="Model panel name; defaults to the focused viewport")


class FrameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to frame with; defaults to the active view")
    nodes: List[str] | None = Field(default=None, description="Nodes to frame; omit for the selection")
    all: bool = Field(default=False, description="Frame everything")
    fit_factor: float = Field(default=0.9, gt=0, le=2, description="1.0 fills the view, smaller leaves margin")


class PlayblastInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to blast through (set on the viewport first)")
    frame: float | None = Field(default=None, description="Single frame: returns a PNG image. Omit for a range")
    start: float | None = Field(default=None, description="Range start (defaults to playback start)")
    end: float | None = Field(default=None, description="Range end (defaults to playback end)")
    width: int = Field(default=1920, ge=16, le=8192)
    height: int = Field(default=1080, ge=16, le=8192)
    format: str = Field(default="image", description="image (png sequence), qt, avfoundation (macOS), avi (Windows)")
    filename: str | None = Field(default=None, description="Output path or base name; temp folder when omitted")
    quality: int = Field(default=100, ge=1, le=100)
    percent: int = Field(default=100, ge=1, le=100)
    offscreen: bool = Field(default=True, description="Render off screen (does not require the viewport to be unobstructed)")
    show_ornaments: bool = Field(default=False, description="Show HUD, gates and manipulators")
    compression: str | None = Field(default=None, description="Codec for movie formats, e.g. H.264")
    panel: str | None = Field(default=None, description="Model panel to use")


class ViewportSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    panel: str | None = Field(default=None, description="Model panel; defaults to the focused viewport")
    display_mode: str | None = Field(default=None, description="wireframe, smoothShaded, flatShaded, boundingBox or points")
    textures: bool | None = Field(default=None)
    lights: str | None = Field(default=None, description="default, all, selected, flat or none")
    shadows: bool | None = Field(default=None)
    wireframe_on_shaded: bool | None = Field(default=None)
    grid: bool | None = Field(default=None)
    hud: bool | None = Field(default=None, description="Heads up display")
    aa: bool | None = Field(default=None, description="Viewport 2.0 multisample anti aliasing")
    ao: bool | None = Field(default=None, description="Screen space ambient occlusion")
    motion_blur: bool | None = Field(default=None, description="Viewport 2.0 motion blur")
    hide: List[str] | None = Field(default=None, description="Object types to hide: cameras, locators, joints, nurbsCurves, polymeshes, imagePlane, lights ...")
    show: List[str] | None = Field(default=None, description="Object types to show")


class SetResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(..., ge=1, le=16384, examples=[1920])
    height: int = Field(..., ge=1, le=16384, examples=[1080])
    pixel_aspect: float = Field(default=1.0, gt=0, description="Pixel aspect (2.0 for anamorphic)")
    device_aspect: float | None = Field(default=None, gt=0, description="Derived from width/height * pixel_aspect when omitted")


class SequenceShotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, description="Shot name, e.g. 'sh010'")
    start: float = Field(..., description="Scene frame the shot starts on")
    end: float = Field(..., description="Scene frame the shot ends on")
    sequence_start: float | None = Field(default=None, description="Where the shot sits on the sequence timeline (defaults to start)")
    camera: str | None = Field(default=None, description="Camera the shot uses")
    track: int | None = Field(default=None, ge=1, description="Sequencer track number")


class ImagePlaneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera to attach the plane to")
    path: str = Field(..., description="Image path (or first frame of a sequence)")
    depth: float = Field(default=100.0, gt=0, description="Distance from the camera")
    alpha: float = Field(default=1.0, ge=0, le=1, description="Plane opacity")
    fit: str = Field(default="best", description="fill, best, horizontal, vertical or to_size")
    offset: List[float] | None = Field(default=None, description="[x, y] offset in film back inches")
    only_in_camera: bool = Field(default=True, description="Show only when looking through this camera")
    sequence: bool = Field(default=False, description="Use frame extension for image sequences")


class LocatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="locator1", min_length=1)
    pos: List[float] | None = Field(default=None, description="World position [x, y, z]")
    parent: str | None = Field(default=None)
    size: float = Field(default=1.0, gt=0, description="Display size")


class MeasureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: Point = Field(..., description="Node name or [x, y, z]")
    b: Point = Field(..., description="Node name or [x, y, z]")


class CameraKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera transform or shape")
    frame: float = Field(..., description="Frame to key")
    translate: List[float] | None = Field(default=None, description="World position [x, y, z]")
    rotate: List[float] | None = Field(default=None, description="Rotation [x, y, z] degrees")
    focal_length: float | None = Field(default=None, gt=0, description="Key a zoom")
    focus_distance: float | None = Field(default=None, gt=0, description="Key a rack focus")
    tangent: str = Field(default="auto", description="Tangent type for the new keys")


class CameraInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera transform or shape")


class ShotNotesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(..., description="Camera that carries the notes")
    notes: str | None = Field(default=None, description="Text to store; omit to read")
    append: bool = Field(default=False, description="Append to existing notes instead of replacing")


class TurntableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Asset to turn around")
    frames: int = Field(default=120, ge=2, le=10000, description="Frames for one full revolution")
    radius: float | None = Field(default=None, gt=0, description="Camera distance; derived from the bounding box when omitted")
    camera: str | None = Field(default=None, description="Existing camera to keep still while the object spins; omit to create an orbiting camera")
    focal_length: float = Field(default=50.0, gt=0)
    start: float | None = Field(default=None, description="First frame; defaults to playback start")
    height: float | None = Field(default=None, description="Camera height; slightly above the object center when omitted")


class SetupSceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fps: float = Field(default=24.0, gt=0, description="24, 25, 30, 23.976, 29.97, 48, 60 ...")
    units: str = Field(default="cm", description="mm, cm, m, in, ft or yd")
    start: float = Field(default=1001.0, description="First frame (1001 is the usual pipeline start)")
    end: float = Field(default=1100.0)
    aspect: float | None = Field(default=None, gt=0, description="Render aspect; height is derived from width when given (2.39 for scope)")
    width: int = Field(default=1920, ge=1, le=16384)
    height: int = Field(default=1080, ge=1, le=16384)
    playback_realtime: bool = Field(default=True)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_create_camera", annotations={"title": "Create previs camera", **WRITE})
    async def maya_create_camera(params: CreateCameraInput) -> str:
        """Create a camera with a real sensor size in mm (film aperture is converted
        to inches), focal length, clip planes, gate display and an optional aim
        target. Returns full camera info including horizontal/vertical FOV."""
        return await ctx.run("previs.create_camera", params.model_dump())

    @mcp.tool(name="maya_create_shot_camera_rig", annotations={"title": "Create shot camera rig", **WRITE})
    async def maya_create_shot_camera_rig(params: ShotRigInput) -> str:
        """Build a production style camera rig: 'aim' gives a shot_ctrl group with
        a camera group and aim/up locators; 'crane' gives dolly > crane_base >
        arm > head > camera with channels locked to what a real crane offers.
        Returns a node map and which channels to animate."""
        return await ctx.run("previs.create_shot_camera_rig", params.model_dump())

    @mcp.tool(name="maya_set_camera_lens", annotations={"title": "Set camera lens", **WRITE})
    async def maya_set_camera_lens(params: SetLensInput) -> str:
        """Set focal length (or horizontal FOV), f-stop, focus distance and depth
        of field on a camera. Returns the updated camera info."""
        return await ctx.run("previs.set_lens", params.model_dump())

    @mcp.tool(name="maya_list_cameras", annotations={"title": "List cameras", **READ})
    async def maya_list_cameras(params: ListCamerasInput) -> str:
        """List scene cameras with focal length, position and sequencer shot
        assignments. Default cameras are skipped unless asked for."""
        return await ctx.run("previs.list_cameras", params.model_dump())

    @mcp.tool(name="maya_look_through", annotations={"title": "Look through camera", **WRITE})
    async def maya_look_through(params: LookThroughInput) -> str:
        """Point a viewport at a camera. Needs the Maya GUI."""
        return await ctx.run("previs.look_through", params.model_dump())

    @mcp.tool(name="maya_frame_selected", annotations={"title": "Frame objects", **WRITE})
    async def maya_frame_selected(params: FrameInput) -> str:
        """Frame nodes, the selection or everything in a camera (viewFit)."""
        return await ctx.run("previs.frame", params.model_dump())

    @mcp.tool(name="maya_playblast", annotations={"title": "Playblast", **WRITE})
    async def maya_playblast(params: PlayblastInput) -> Any:
        """Playblast through a camera. With frame set you get the PNG back as an
        image (use it to check composition). Without it a movie or png sequence
        is written and the path returned. Needs a viewport (GUI session)."""
        if params.frame is not None:
            return await ctx.image("previs.playblast", params.model_dump(), timeout=120.0)
        return await ctx.run("previs.playblast", params.model_dump(), timeout=1800.0)

    @mcp.tool(name="maya_viewport_settings", annotations={"title": "Viewport settings", **WRITE})
    async def maya_viewport_settings(params: ViewportSettingsInput) -> str:
        """Set viewport shading, textures, lights, shadows, grid, HUD, object
        type visibility and Viewport 2.0 AA/AO/motion blur before a playblast."""
        return await ctx.run("previs.viewport_settings", params.model_dump())

    @mcp.tool(name="maya_set_resolution", annotations={"title": "Set render resolution", **WRITE})
    async def maya_set_resolution(params: SetResolutionInput) -> str:
        """Set render resolution, pixel aspect and device aspect on
        defaultResolution (drives the resolution gate and playblast size)."""
        return await ctx.run("previs.set_resolution", params.model_dump())

    @mcp.tool(name="maya_create_sequence_shot", annotations={"title": "Create sequencer shot", **WRITE})
    async def maya_create_sequence_shot(params: SequenceShotInput) -> str:
        """Create a Camera Sequencer shot covering a scene frame range with a
        camera, placed at a sequence time on a track."""
        return await ctx.run("previs.create_sequence_shot", params.model_dump())

    @mcp.tool(name="maya_list_shots", annotations={"title": "List sequencer shots", **READ})
    async def maya_list_shots() -> str:
        """List Camera Sequencer shots with scene/sequence ranges and cameras."""
        return await ctx.run("previs.list_shots")

    @mcp.tool(name="maya_camera_sequencer_info", annotations={"title": "Camera sequencer info", **READ})
    async def maya_camera_sequencer_info() -> str:
        """Current shot, sequence time, sequence range and every shot."""
        return await ctx.run("previs.camera_sequencer_info")

    @mcp.tool(name="maya_add_image_plane", annotations={"title": "Add image plane", **WRITE})
    async def maya_add_image_plane(params: ImagePlaneInput) -> str:
        """Attach an image or plate sequence to a camera as an image plane for
        reference or match moving."""
        return await ctx.run("previs.add_image_plane", params.model_dump())

    @mcp.tool(name="maya_create_locator", annotations={"title": "Create locator", **WRITE})
    async def maya_create_locator(params: LocatorInput) -> str:
        """Create a locator at a world position (blocking marks, eyelines, aim
        targets)."""
        return await ctx.run("previs.create_locator", params.model_dump())

    @mcp.tool(name="maya_measure_distance", annotations={"title": "Measure distance", **READ})
    async def maya_measure_distance(params: MeasureInput) -> str:
        """Distance and delta between two nodes or points in scene units."""
        return await ctx.run("previs.measure_distance", params.model_dump())

    @mcp.tool(name="maya_set_camera_key", annotations={"title": "Key camera", **WRITE})
    async def maya_set_camera_key(params: CameraKeyInput) -> str:
        """Key a camera's position, rotation, focal length and focus distance at a
        frame. Call it per beat to block a camera move."""
        return await ctx.run("previs.set_camera_key", params.model_dump())

    @mcp.tool(name="maya_camera_info", annotations={"title": "Camera info", **READ})
    async def maya_camera_info(params: CameraInfoInput) -> str:
        """Lens, sensor size, FOV, position, aim point, clip planes, DOF, gate
        display, sequencer shots, notes and key count for a camera."""
        return await ctx.run("previs.camera_info", params.model_dump())

    @mcp.tool(name="maya_shot_notes", annotations={"title": "Shot notes on camera", **WRITE})
    async def maya_shot_notes(params: ShotNotesInput) -> str:
        """Read or write free text notes stored on a camera (a 'notes' string
        attribute). Omit notes to read."""
        return await ctx.run("previs.shot_notes", params.model_dump())

    @mcp.tool(name="maya_create_turntable", annotations={"title": "Create turntable", **WRITE})
    async def maya_create_turntable(params: TurntableInput) -> str:
        """Turntable an asset: a new camera orbits it over N frames (linear,
        cycling), or with an existing camera the asset spins instead. Sets the
        playback range to one revolution."""
        return await ctx.run("previs.create_turntable", params.model_dump())

    @mcp.tool(name="maya_setup_scene_for_previs", annotations={"title": "Set up scene for previs", **DESTRUCTIVE})
    async def maya_setup_scene_for_previs(params: SetupSceneInput) -> str:
        """One call previs scene prep: fps, linear units, frame range, render
        resolution/aspect and real time playback."""
        return await ctx.run("previs.setup_scene_for_previs", params.model_dump())
