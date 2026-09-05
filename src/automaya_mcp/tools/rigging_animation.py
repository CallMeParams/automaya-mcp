"""Rigging and animation tools: joints, skinning, IK, controls, keys, baking, layers."""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ._base import DESTRUCTIVE, READ, WRITE, ToolContext

NODES_DESC = "Node names (long or short). Omit to use the current selection."


# rig inputs ----------------------------------------------------------------
class JointChainInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positions: List[List[float]] = Field(..., description="World positions [x, y, z] in chain order", min_length=1, examples=[[[0, 0, 0], [0, 10, 0], [0, 20, 0]]])
    names: List[str] | None = Field(default=None, description="One name per position, e.g. ['L_shoulder', 'L_elbow', 'L_wrist']")
    parent: str | None = Field(default=None, description="Existing node to parent the root joint under")
    radius: float = Field(default=1.0, gt=0, description="Joint display radius")


class OrientJointsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(..., description="Root joint of the chain")
    orient: str = Field(default="xyz", description="Primary/secondary axis order: xyz, yzx, zxy, xzy, yxz, zyx, none")
    secondary: str = Field(default="yup", description="Secondary axis world direction: xup, xdown, yup, ydown, zup, zdown, none")
    zero_end: bool = Field(default=True, description="Zero the joint orient of end joints")


class MirrorJointsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(..., description="Root joint to mirror (with its children)")
    axis: str = Field(default="YZ", description="Mirror plane: XY, YZ or XZ")
    search: str = Field(default="L_", description="Substring to replace in names")
    replace: str = Field(default="R_", description="Replacement substring")
    behavior: bool = Field(default=True, description="Mirror behavior (opposite rotations) instead of orientation")


class BindSkinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(..., description="Mesh transform to bind")
    joints: List[str] = Field(..., min_length=1, description="Influence joints")
    max_influences: int = Field(default=4, ge=1, le=16, description="Max influences per vertex (4 is game safe)")
    method: str = Field(default="classic", description="classic (linear), dual (dual quaternion) or blended")
    bind_method: str = Field(default="closest", description="closest, hierarchy, heat or geodesic")
    dropoff_rate: float = Field(default=4.0, gt=0, le=10, description="Weight falloff rate")
    name: str | None = Field(default=None, description="Name for the skinCluster")


class CreateIkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_joint: str = Field(..., description="First joint of the IK chain")
    end_joint: str = Field(..., description="Last joint (end effector goes here)")
    solver: str = Field(default="ikRPsolver", description="ikRPsolver (rotate plane, limbs), ikSCsolver (single chain) or ikSplineSolver (spines, tails)")
    name: str | None = Field(default=None, description="IK handle name")
    curve: str | None = Field(default=None, description="Existing curve for ikSplineSolver; omit to auto create one")
    spans: int = Field(default=4, ge=1, le=64, description="Spans for an auto created spline curve")


class CreateControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., description="Control name, e.g. 'L_arm_ik_ctrl'", min_length=1)
    shape: str = Field(default="circle", description="circle, square, cube or arrow")
    size: float = Field(default=1.0, gt=0, description="Control radius / half size")
    target: str | None = Field(default=None, description="Node to match position and rotation (joint, locator ...)")
    constrain: str | None = Field(default=None, description="Drive target with a parent, point or orient constraint")
    color: int | None = Field(default=None, ge=0, le=31, description="Maya index color (13 red, 6 blue, 17 yellow, 14 green)")
    freeze: bool = Field(default=True, description="Freeze transforms on the control curve")
    offset_group: bool = Field(default=True, description="Create a zeroed offset group above the control")


class ConstrainInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    driver: str = Field(..., description="Driving node")
    driven: str = Field(..., description="Node that follows")
    type: str = Field(default="parent", description="parent, point, orient, aim or scale")
    maintain_offset: bool = Field(default=True, description="Keep the current offset between driver and driven")
    weight: float = Field(default=1.0, ge=0, le=1)
    skip: List[str] | None = Field(default=None, description="Axes to skip: x, y, z")
    aim_vector: List[float] | None = Field(default=None, description="aim only: local aim axis, default [1, 0, 0]")
    up_vector: List[float] | None = Field(default=None, description="aim only: local up axis, default [0, 1, 0]")
    world_up_type: str = Field(default="scene", description="aim only: scene, object, objectrotation, vector, none")
    world_up_object: str | None = Field(default=None, description="aim only: up object for object/objectrotation")


class BlendshapeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: str = Field(..., description="Base mesh that gets the deformer")
    targets: List[str] = Field(..., min_length=1, description="Target shape meshes (same topology)")
    name: str | None = Field(default=None, description="blendShape node name")
    front_of_chain: bool = Field(default=True, description="Insert before skinning in the deformation order")


class SkinInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str = Field(..., description="Skinned mesh transform")


class CopySkinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str = Field(..., description="Skinned source mesh")
    dst: str = Field(..., description="Destination mesh (bound to the same joints if it has no skin yet)")
    surface_association: str = Field(default="closestPoint", description="closestPoint, rayCast, closestComponent or uvSpace")
    influence_association: List[str] | None = Field(default=None, description="Order of influence matching, default ['oneToOne', 'name', 'closestJoint']")
    max_influences: int = Field(default=4, ge=1, le=16)


class ResetBindPoseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mesh: str | None = Field(default=None, description="Skinned mesh whose influences get a fresh bind pose")
    joints: List[str] | None = Field(default=None, description="Or explicit joints")
    go_to_bind_pose: bool = Field(default=False, description="True: move joints back to the stored bind pose instead of re-saving it")


# anim inputs ---------------------------------------------------------------
class SetKeyframeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description=NODES_DESC)
    attrs: List[str] | None = Field(default=None, description="Attributes to key (translateX, rotate, visibility ...). Omit for all keyable channels")
    time: float | None = Field(default=None, description="Frame; omit for the current frame")
    value: float | None = Field(default=None, description="Value to key (needs attrs); omit to key current values")
    in_tangent: str = Field(default="auto", description="spline, linear, fast, slow, flat, step, stepnext, fixed, clamped, plateau, auto")
    out_tangent: str = Field(default="auto")


class GetKeyframesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Animated node")
    attr: str = Field(..., description="Attribute, e.g. translateX", examples=["translateX"])
    start: float | None = Field(default=None, description="Range start frame")
    end: float | None = Field(default=None, description="Range end frame")
    include_tangents: bool = Field(default=False, description="Also return in/out tangent types per key")


class DeleteKeysInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description=NODES_DESC)
    attrs: List[str] | None = Field(default=None, description="Only these attributes; omit for all")
    start: float | None = Field(default=None, description="Only keys from this frame")
    end: float | None = Field(default=None, description="Only keys up to this frame")


class SetTimeRangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(..., description="Playback start frame", examples=[1001])
    end: float = Field(..., description="Playback end frame", examples=[1100])
    anim_start: float | None = Field(default=None, description="Outer animation range start (defaults to include start)")
    anim_end: float | None = Field(default=None, description="Outer animation range end")


class SetCurrentTimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: float = Field(..., description="Frame to go to")


class PlaybackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(default="play", description="play, stop, toggle, step or status")
    forward: bool = Field(default=True, description="Direction for play/step")
    frames: int = Field(default=1, ge=1, description="Frames per step")


class BakeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description=NODES_DESC)
    start: float | None = Field(default=None, description="Defaults to playback start")
    end: float | None = Field(default=None, description="Defaults to playback end")
    attrs: List[str] | None = Field(default=None, description="Only these attributes; omit for all keyable")
    sample_by: float = Field(default=1.0, gt=0, description="Frame step between baked keys")
    simulation: bool = Field(default=True, description="Evaluate the whole scene per frame (needed for dynamics, expressions, IK)")
    preserve_outside_keys: bool = Field(default=True, description="Keep keys outside the baked range")
    remove_constraints: bool = Field(default=False, description="Delete constraints under the nodes after baking")


class MotionPathInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node to move along the curve")
    curve: str = Field(..., description="NURBS curve transform")
    start: float | None = Field(default=None, description="Defaults to playback start")
    end: float | None = Field(default=None, description="Defaults to playback end")
    follow: bool = Field(default=True, description="Orient the node along the curve")
    up_axis: str = Field(default="y", description="x, y or z")
    front_axis: str = Field(default="x", description="Axis that points along the curve: x, y or z")
    world_up_type: str = Field(default="scene", description="scene, object, objectrotation, vector or normal")
    world_up_object: str | None = Field(default=None)
    bank: bool = Field(default=False, description="Bank into turns")
    parametric: bool = Field(default=False, description="Parametric length instead of even (fraction) spacing")


class SetTangentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description=NODES_DESC)
    in_type: str = Field(default="auto", description="spline, linear, fast, slow, flat, step, stepnext, fixed, clamped, plateau, auto")
    out_type: str | None = Field(default=None, description="Defaults to in_type")
    attrs: List[str] | None = Field(default=None)
    start: float | None = Field(default=None, description="Only keys inside start..end")
    end: float | None = Field(default=None)
    weighted: bool | None = Field(default=None, description="Switch weighted tangents on/off")


class SetInfinityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description=NODES_DESC)
    pre: str = Field(default="constant", description="constant, linear, cycle, cycleRelative or oscillate")
    post: str = Field(default="constant", description="constant, linear, cycle, cycleRelative or oscillate")
    attrs: List[str] | None = Field(default=None)


class ImportAnimationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Absolute path to a .fbx or .atom file")
    nodes: List[str] | None = Field(default=None, description="Nodes to apply the animation to (required for ATOM)")
    mode: str = Field(default="merge", description="merge, add or exclusive")


class RetargetHintInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_root: str = Field(..., description="Root joint of the animated skeleton")
    target_root: str = Field(..., description="Root joint of the skeleton that should receive the motion")


class ListAnimatedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: List[str] | None = Field(default=None, description="Restrict to these nodes; omit for the whole scene")


class PlaybackSpeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="realtime", description="realtime, every_frame, half or double")
    fps: float | None = Field(default=None, gt=0, description="Also set the scene frame rate (24, 25, 30, 23.976 ...)")
    loop: str | None = Field(default=None, description="once, continuous or oscillate")


class AnimLayerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, description="Layer name")
    nodes: List[str] | None = Field(default=None, description="Nodes to add to the layer")
    attrs: List[str] | None = Field(default=None, description="Only these attributes of the nodes")
    override: bool = Field(default=False, description="Override layer instead of additive")
    mute: bool = Field(default=False)
    solo: bool = Field(default=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    # rig ---------------------------------------------------------------
    @mcp.tool(name="maya_create_joint_chain", annotations={"title": "Create joint chain", **WRITE})
    async def maya_create_joint_chain(params: JointChainInput) -> str:
        """Create a joint chain through world positions (root first), optionally
        under a parent. Follow with maya_orient_joints so the axes are clean.
        Returns the long joint names."""
        return await ctx.run("rig.create_joint_chain", params.model_dump())

    @mcp.tool(name="maya_orient_joints", annotations={"title": "Orient joints", **WRITE})
    async def maya_orient_joints(params: OrientJointsInput) -> str:
        """Orient a whole joint hierarchy (primary axis down the bone, secondary
        axis toward a world direction) and zero the end joints. Do this before
        skinning or adding IK."""
        return await ctx.run("rig.orient_joints", params.model_dump())

    @mcp.tool(name="maya_mirror_joints", annotations={"title": "Mirror joints", **WRITE})
    async def maya_mirror_joints(params: MirrorJointsInput) -> str:
        """Mirror a joint chain across a world plane with name search/replace,
        e.g. L_ to R_. Returns the new joints."""
        return await ctx.run("rig.mirror_joints", params.model_dump())

    @mcp.tool(name="maya_bind_skin", annotations={"title": "Bind skin", **WRITE})
    async def maya_bind_skin(params: BindSkinInput) -> str:
        """Smooth bind a mesh to joints with a skinCluster. Fails if the mesh is
        already skinned (see maya_skin_info). Returns the skinCluster name."""
        return await ctx.run("rig.bind_skin", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_create_ik", annotations={"title": "Create IK handle", **WRITE})
    async def maya_create_ik(params: CreateIkInput) -> str:
        """Create an IK handle between two joints: rotate plane for limbs, single
        chain for simple two joint setups, spline for spines and tails."""
        return await ctx.run("rig.create_ik", params.model_dump())

    @mcp.tool(name="maya_create_control", annotations={"title": "Create control curve", **WRITE})
    async def maya_create_control(params: CreateControlInput) -> str:
        """Create a NURBS control (circle, square, cube, arrow) with an offset
        group, snapped to a target and optionally constraining it. Returns control
        and offset group names."""
        return await ctx.run("rig.create_control", params.model_dump())

    @mcp.tool(name="maya_constrain", annotations={"title": "Constrain", **WRITE})
    async def maya_constrain(params: ConstrainInput) -> str:
        """Add a parent, point, orient, aim or scale constraint from driver to
        driven. Use maintain_offset=false to snap driven onto driver."""
        return await ctx.run("rig.constrain", params.model_dump())

    @mcp.tool(name="maya_create_blendshape", annotations={"title": "Create blendShape", **WRITE})
    async def maya_create_blendshape(params: BlendshapeInput) -> str:
        """Create a blendShape deformer on a base mesh from target meshes with the
        same topology. Returns the node and the weight names to animate."""
        return await ctx.run("rig.create_blendshape", params.model_dump())

    @mcp.tool(name="maya_skin_info", annotations={"title": "Skin cluster info", **READ})
    async def maya_skin_info(params: SkinInfoInput) -> str:
        """Report the skinCluster on a mesh: influences, method, max influences."""
        return await ctx.run("rig.skin_info", params.model_dump())

    @mcp.tool(name="maya_copy_skin_weights", annotations={"title": "Copy skin weights", **WRITE})
    async def maya_copy_skin_weights(params: CopySkinInput) -> str:
        """Copy skin weights from one mesh to another (binding the destination to
        the same joints if needed). Good for proxy to final mesh transfers."""
        return await ctx.run("rig.copy_skin_weights", params.model_dump(), timeout=300.0)

    @mcp.tool(name="maya_reset_bind_pose", annotations={"title": "Reset bind pose", **DESTRUCTIVE})
    async def maya_reset_bind_pose(params: ResetBindPoseInput) -> str:
        """Re-save the bind pose from the current joint pose (fixes 'skin was
        bound in a different pose' errors), or with go_to_bind_pose restore the
        joints to the stored pose."""
        return await ctx.run("rig.reset_bind_pose", params.model_dump())

    # anim --------------------------------------------------------------
    @mcp.tool(name="maya_set_keyframe", annotations={"title": "Set keyframe", **WRITE})
    async def maya_set_keyframe(params: SetKeyframeInput) -> str:
        """Key nodes at a frame: all keyable channels, or given attrs, optionally
        with an explicit value and tangent types."""
        return await ctx.run("anim.set_keyframe", params.model_dump())

    @mcp.tool(name="maya_get_keyframes", annotations={"title": "Get keyframes", **READ})
    async def maya_get_keyframes(params: GetKeyframesInput) -> str:
        """Return key times and values for one attribute, optionally in a range."""
        return await ctx.run("anim.get_keyframes", params.model_dump())

    @mcp.tool(name="maya_delete_keys", annotations={"title": "Delete keys", **DESTRUCTIVE})
    async def maya_delete_keys(params: DeleteKeysInput) -> str:
        """Remove keys on nodes, all or only some attributes, optionally inside a
        frame range."""
        return await ctx.run("anim.delete_keys", params.model_dump())

    @mcp.tool(name="maya_set_time_range", annotations={"title": "Set time range", **WRITE})
    async def maya_set_time_range(params: SetTimeRangeInput) -> str:
        """Set the playback range and optionally the outer animation range."""
        return await ctx.run("anim.set_time_range", params.model_dump())

    @mcp.tool(name="maya_get_time_range", annotations={"title": "Get time range", **READ})
    async def maya_get_time_range() -> str:
        """Playback and animation range, current frame, fps and playback speed."""
        return await ctx.run("anim.get_time_range")

    @mcp.tool(name="maya_set_current_time", annotations={"title": "Set current frame", **WRITE})
    async def maya_set_current_time(params: SetCurrentTimeInput) -> str:
        """Move the time slider to a frame."""
        return await ctx.run("anim.set_current_time", params.model_dump())

    @mcp.tool(name="maya_playback", annotations={"title": "Playback control", **WRITE})
    async def maya_playback(params: PlaybackInput) -> str:
        """Play, stop, toggle or step the timeline. Playback keeps running in Maya
        after this returns; call again with action=stop."""
        return await ctx.run("anim.playback", params.model_dump())

    @mcp.tool(name="maya_bake_animation", annotations={"title": "Bake animation", **DESTRUCTIVE})
    async def maya_bake_animation(params: BakeInput) -> str:
        """Bake constraints, IK, expressions or motion paths down to plain keys
        over a range (bakeResults). Use before FBX export or handing off to Unreal."""
        return await ctx.run("anim.bake", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_motion_path", annotations={"title": "Attach to motion path", **WRITE})
    async def maya_motion_path(params: MotionPathInput) -> str:
        """Attach a node (camera, vehicle, character root) to a curve with a
        motionPath over a frame range, with follow and banking options."""
        return await ctx.run("anim.motion_path", params.model_dump())

    @mcp.tool(name="maya_set_tangents", annotations={"title": "Set key tangents", **WRITE})
    async def maya_set_tangents(params: SetTangentsInput) -> str:
        """Change tangent types on existing keys (all, or only in a frame range):
        linear for mechanical moves, flat for holds, step for pose to pose."""
        return await ctx.run("anim.set_tangents", params.model_dump())

    @mcp.tool(name="maya_set_infinity", annotations={"title": "Set curve infinity", **WRITE})
    async def maya_set_infinity(params: SetInfinityInput) -> str:
        """Set pre/post infinity on anim curves: cycle for loops, cycleRelative
        for walk cycles that travel, linear to extrapolate."""
        return await ctx.run("anim.set_infinity", params.model_dump())

    @mcp.tool(name="maya_import_animation", annotations={"title": "Import animation", **WRITE})
    async def maya_import_animation(params: ImportAnimationInput) -> str:
        """Import animation from an FBX (onto nodes with matching names) or an
        ATOM file (onto the given nodes). Reports how many anim curves arrived."""
        return await ctx.run("anim.import_animation", params.model_dump(), timeout=600.0)

    @mcp.tool(name="maya_retarget_hint", annotations={"title": "HumanIK retarget steps", **READ})
    async def maya_retarget_hint(params: RetargetHintInput) -> str:
        """Human readable HumanIK recipe for retargeting motion between two
        skeletons (nothing is automated; use it to guide the user or to plan
        maya_execute_mel calls)."""
        return await ctx.run("anim.retarget_hint", params.model_dump())

    @mcp.tool(name="maya_list_animated", annotations={"title": "List animated nodes", **READ})
    async def maya_list_animated(params: ListAnimatedInput) -> str:
        """Which nodes carry anim curves and on which attributes (looks through
        animation layers)."""
        return await ctx.run("anim.list_animated", params.model_dump())

    @mcp.tool(name="maya_set_playback_speed", annotations={"title": "Set playback speed", **WRITE})
    async def maya_set_playback_speed(params: PlaybackSpeedInput) -> str:
        """Set playback to real time or every frame (and optionally the scene fps
        and loop mode)."""
        return await ctx.run("anim.set_playback_speed", params.model_dump())

    @mcp.tool(name="maya_create_animation_layer", annotations={"title": "Create animation layer", **WRITE})
    async def maya_create_animation_layer(params: AnimLayerInput) -> str:
        """Create an animation layer (additive or override) and add nodes or
        specific attributes to it, for non destructive tweaks on top of mocap."""
        return await ctx.run("anim.create_animation_layer", params.model_dump())
