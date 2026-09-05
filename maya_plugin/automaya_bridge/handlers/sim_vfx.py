"""fx.* commands: nCloth, nParticles, fields, fluids, Bullet, nHair, caching,
Bifrost, MASH and a few quick previs FX presets.

Everything optional (Bullet, MASH, Bifrost, AbcExport) is imported or loaded
lazily inside the handler and turned into a BridgeError with a fix hint, so a
missing plugin never takes the bridge down.
"""
from __future__ import annotations

import importlib
import inspect
import math
import os
import time
from typing import Any, Dict, List, Sequence

from ..registry import command
from ._util import BridgeError, ensure_plugin, new_nodes_since, node_summary, require_maya, require_nodes, resolve_targets, shapes_of, transform_of

try:
    from maya import cmds, mel  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore
    mel = None  # type: ignore


# nParticle render types (particleRenderType attribute)
_PARTICLE_STYLES = {"points": 3, "balls": 4, "cloud": 8, "thick": 8, "water": 7, "spheres": 4, "streak": 6, "sprites": 5}
_EMITTER_TYPES = ("omni", "direction", "directional", "volume", "surface", "curve")
_FIELD_COMMANDS = {
    "gravity": "gravity",
    "turbulence": "turbulence",
    "vortex": "vortex",
    "air": "air",
    "drag": "drag",
    "radial": "radial",
    "uniform": "uniform",
    "newton": "newton",
    "volumeAxis": "volumeAxis",
}
_FIELD_TYPES = ("gravityField", "turbulenceField", "vortexField", "airField", "dragField", "radialField", "uniformField", "newtonField", "volumeAxisField")
_NCLOTH_PRESETS = ("none", "silk", "tshirt", "burlap", "leather", "thickLeather", "heavyDenim", "chainMail", "rubberSheet", "solidRubber", "waterBalloon", "plasticShell", "concrete", "putty", "loosePlastic", "softSheetMetal", "airBag", "beachBall", "honey", "lava")
_FLUID_KINDS = ("3d", "2d", "ocean", "pond")
_RIGID_SHAPES = ("auto", "box", "sphere", "hull", "mesh", "capsule", "cylinder", "plane")
_MASH_ARRANGEMENTS = {"linear": 0, "radial": 1, "spherical": 2, "random": 3, "grid": 4, "mesh": 6}


# helpers -------------------------------------------------------------------
def _long(nodes: Sequence[str]) -> List[str]:
    """Long names for a list of nodes, keeping order and dropping blanks."""
    out: List[str] = []
    for n in nodes or []:
        if not n:
            continue
        found = cmds.ls(n, long=True) or [n]
        out.append(found[0])
    return out


def _mesh_shape(node: str) -> str:
    shapes = shapes_of(node, "mesh")
    if not shapes:
        raise BridgeError("%r has no mesh shape; nCloth, colliders and hair need a polygon mesh" % node)
    return shapes[0]


def _shape_or_self(node: str, shape_type: str | None = None) -> str:
    shapes = shapes_of(node, shape_type)
    return shapes[0] if shapes else node


def _set_attrs(node: str, attrs: Dict[str, Any] | None) -> Dict[str, Any]:
    """Set a dict of attribute values on ``node``; returns what was applied."""
    applied: Dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        plug = "%s.%s" % (node, key)
        try:
            if isinstance(value, (list, tuple)):
                cmds.setAttr(plug, *value)
            elif isinstance(value, str):
                cmds.setAttr(plug, value, type="string")
            else:
                cmds.setAttr(plug, value)
            applied[key] = value
        except Exception as exc:
            raise BridgeError("could not set %s = %r: %s" % (plug, value, exc)) from exc
    return applied


def _pos_kwargs(position: Sequence[float] | None) -> Dict[str, Any]:
    if position is None:
        return {}
    if len(position) != 3:
        raise BridgeError("position must be [x, y, z]")
    return {"position": tuple(float(v) for v in position)}


def _connect_dynamic(target: str, **kwargs: Any) -> None:
    try:
        cmds.connectDynamic(target, **kwargs)
    except Exception as exc:
        raise BridgeError("connectDynamic failed for %s (%s): %s" % (target, kwargs, exc)) from exc


def _active_nucleus(nucleus: str | None) -> str | None:
    """Make ``nucleus`` the active solver so createNCloth / nParticle reuse it."""
    if not nucleus:
        return None
    require_nodes([nucleus])
    if cmds.nodeType(nucleus) != "nucleus":
        raise BridgeError("%r is not a nucleus node" % nucleus)
    try:
        mel.eval('setActiveNucleusNode("%s")' % nucleus)
    except Exception:
        pass
    return nucleus


def _apply_preset(node: str, preset: str) -> bool:
    """Apply a Maya attribute preset (silk, burlap...). Returns whether it took."""
    if not preset or preset == "none":
        return False
    try:
        result = mel.eval('applyAttrPreset("%s", "%s", 1)' % (node, preset))
        return result is None or bool(result)
    except Exception:
        return False


def _frame_range(start: float | None, end: float | None) -> tuple:
    s = float(start) if start is not None else float(cmds.playbackOptions(query=True, minTime=True))
    e = float(end) if end is not None else float(cmds.playbackOptions(query=True, maxTime=True))
    if e < s:
        raise BridgeError("end (%s) is before start (%s)" % (e, s))
    return s, e


def _created(before: Sequence[str]) -> List[str]:
    return new_nodes_since(before)


# nCloth ---------------------------------------------------------------------
@command("fx.create_ncloth", mutates=True)
def create_ncloth(
    mesh: str,
    preset: str = "none",
    nucleus: str | None = None,
    local_space: bool = False,
    attrs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Turn a polygon mesh into nCloth (mel createNCloth) and optionally apply a preset."""
    require_maya()
    require_nodes([mesh])
    if preset not in _NCLOTH_PRESETS:
        raise BridgeError("unknown nCloth preset %r; use one of %s" % (preset, ", ".join(_NCLOTH_PRESETS)))
    shape = _mesh_shape(mesh)
    _active_nucleus(nucleus)
    before = cmds.ls(long=True) or []
    cmds.select(shape, replace=True)
    cloth_shapes = mel.eval("createNCloth %d" % (1 if local_space else 0)) or []
    if isinstance(cloth_shapes, str):
        cloth_shapes = [cloth_shapes]
    if not cloth_shapes:
        raise BridgeError("createNCloth returned nothing for %s; is it a polygon mesh with faces?" % mesh)
    cloth = _long(cloth_shapes)[0]
    preset_applied = _apply_preset(cloth, preset)
    applied = _set_attrs(cloth, attrs)
    solvers = cmds.listConnections(cloth, type="nucleus") or []
    return {
        "ncloth": cloth,
        "transform": transform_of(cloth),
        "mesh": _long([mesh])[0],
        "nucleus": _long(solvers)[0] if solvers else None,
        "preset": preset,
        "preset_applied": preset_applied,
        "attrs": applied,
        "summary": node_summary(transform_of(cloth)),
        "new_nodes": _created(before),
    }


@command("fx.create_ncloth_collider", mutates=True)
def create_ncloth_collider(mesh: str, nucleus: str | None = None, thickness: float | None = None) -> Dict[str, Any]:
    """Make a mesh a passive nucleus collider (mel makeCollideNCloth)."""
    require_maya()
    require_nodes([mesh])
    shape = _mesh_shape(mesh)
    _active_nucleus(nucleus)
    before = cmds.ls(long=True) or []
    cmds.select(shape, replace=True)
    rigids = mel.eval("makeCollideNCloth") or []
    if isinstance(rigids, str):
        rigids = [rigids]
    if not rigids:
        rigids = [n for n in _created(before) if cmds.nodeType(n) == "nRigid"]
    if not rigids:
        raise BridgeError("makeCollideNCloth created no nRigid for %s" % mesh)
    rigid = _long(rigids)[0]
    if thickness is not None:
        cmds.setAttr(rigid + ".thickness", float(thickness))
    return {"nrigid": rigid, "mesh": _long([mesh])[0], "new_nodes": _created(before)}


# nParticles -------------------------------------------------------------------
@command("fx.create_nparticle", mutates=True)
def create_nparticle(
    name: str = "nParticle1",
    style: str = "points",
    emitter_type: str = "omni",
    rate: float = 100.0,
    position: Sequence[float] | None = None,
    direction: Sequence[float] | None = None,
    speed: float = 1.0,
    spread: float = 0.0,
    lifespan: float | None = None,
    nucleus: str | None = None,
    attrs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create an nParticle system with an emitter (omni / directional / volume)."""
    require_maya()
    if style not in _PARTICLE_STYLES:
        raise BridgeError("unknown particle style %r; use one of %s" % (style, ", ".join(sorted(_PARTICLE_STYLES))))
    etype = "direction" if emitter_type == "directional" else emitter_type
    if etype not in _EMITTER_TYPES:
        raise BridgeError("unknown emitter type %r; use omni, directional or volume" % emitter_type)
    _active_nucleus(nucleus)
    before = cmds.ls(long=True) or []
    result = cmds.nParticle(name=name) or []
    if not result:
        raise BridgeError("cmds.nParticle returned nothing")
    transform, shape = (result[0], result[1]) if len(result) > 1 else (result[0], _shape_or_self(result[0], "nParticle"))
    shape = _long([shape])[0]
    transform = _long([transform])[0]
    cmds.setAttr(shape + ".particleRenderType", _PARTICLE_STYLES[style])
    if style == "thick":
        try:
            cmds.setAttr(shape + ".opacity", 1.0)
        except Exception:
            pass
    if lifespan is not None:
        cmds.setAttr(shape + ".lifespanMode", 1)
        cmds.setAttr(shape + ".lifespan", float(lifespan))
    ekw: Dict[str, Any] = {"type": etype, "rate": float(rate), "speed": float(speed), "name": name + "_emitter"}
    ekw.update(_pos_kwargs(position))
    if etype == "direction":
        d = direction or (0.0, -1.0, 0.0)
        if len(d) != 3:
            raise BridgeError("direction must be [x, y, z]")
        ekw.update({"directionX": float(d[0]), "directionY": float(d[1]), "directionZ": float(d[2]), "spread": float(spread)})
    if etype == "volume":
        ekw["volumeShape"] = "cube"
    emitter = cmds.emitter(**ekw) or []
    emitter_name = _long([emitter[0]])[0] if emitter else None
    if emitter_name:
        _connect_dynamic(shape, emitters=emitter_name)
    applied = _set_attrs(shape, attrs)
    solvers = cmds.listConnections(shape, type="nucleus") or []
    return {
        "particle": shape,
        "transform": transform,
        "emitter": emitter_name,
        "style": style,
        "nucleus": _long(solvers)[0] if solvers else None,
        "attrs": applied,
        "summary": node_summary(transform),
        "new_nodes": _created(before),
    }


# fields -----------------------------------------------------------------------
@command("fx.add_field", mutates=True)
def add_field(
    type: str = "gravity",
    targets: Sequence[str] | None = None,
    magnitude: float = 9.8,
    attenuation: float = 0.0,
    position: Sequence[float] | None = None,
    direction: Sequence[float] | None = None,
    max_distance: float | None = None,
    name: str | None = None,
    attrs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create a dynamics field and connect it to particles, nCloth, fluids or rigid bodies."""
    require_maya()
    if type not in _FIELD_COMMANDS:
        raise BridgeError("unknown field type %r; use one of %s" % (type, ", ".join(_FIELD_COMMANDS)))
    nodes = resolve_targets(targets) if targets else []
    before = cmds.ls(long=True) or []
    kwargs: Dict[str, Any] = {"magnitude": float(magnitude), "attenuation": float(attenuation)}
    kwargs.update(_pos_kwargs(position))
    if name:
        kwargs["name"] = name
    if max_distance is not None:
        kwargs["maxDistance"] = float(max_distance)
    if direction is not None and type in ("gravity", "uniform"):
        if len(direction) != 3:
            raise BridgeError("direction must be [x, y, z]")
        kwargs.update({"directionX": float(direction[0]), "directionY": float(direction[1]), "directionZ": float(direction[2])})
    created = getattr(cmds, _FIELD_COMMANDS[type])(**kwargs) or []
    if isinstance(created, str):
        created = [created]
    if not created:
        raise BridgeError("cmds.%s returned nothing" % _FIELD_COMMANDS[type])
    field = _long([created[0]])[0]
    connected: List[str] = []
    for node in nodes:
        target = _shape_or_self(node)
        _connect_dynamic(target, fields=field)
        connected.append(target)
    applied = _set_attrs(field, attrs)
    return {"field": field, "type": type, "connected_to": connected, "attrs": applied, "new_nodes": _created(before)}


# fluids -----------------------------------------------------------------------
@command("fx.create_fluid", mutates=True)
def create_fluid(
    kind: str = "3d",
    name: str | None = None,
    resolution: Sequence[int] | None = None,
    size: Sequence[float] | None = None,
    emitter: str = "point",
    emitter_mesh: str | None = None,
    position: Sequence[float] | None = None,
    density: float = 1.0,
    heat: float = 0.0,
    fuel: float = 0.0,
    attrs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create a 3D / 2D fluid container, an ocean or a pond, with an optional emitter."""
    require_maya()
    if kind not in _FLUID_KINDS:
        raise BridgeError("unknown fluid kind %r; use 3d, 2d, ocean or pond" % kind)
    if emitter not in ("point", "mesh", "none"):
        raise BridgeError("emitter must be point, mesh or none")
    if emitter == "mesh":
        if not emitter_mesh:
            raise BridgeError("emitter='mesh' needs emitter_mesh")
        require_nodes([emitter_mesh])
    before = cmds.ls(long=True) or []
    if kind == "3d":
        res = list(resolution or (10, 10, 10))
        sz = list(size or (10.0, 10.0, 10.0))
        if len(res) != 3 or len(sz) != 3:
            raise BridgeError("3d fluids need resolution [x, y, z] and size [x, y, z]")
        fluid = mel.eval("create3DFluid %d %d %d %g %g %g" % (int(res[0]), int(res[1]), int(res[2]), sz[0], sz[1], sz[2]))
    elif kind == "2d":
        res = list(resolution or (40, 40))
        sz = list(size or (10.0, 10.0))
        if len(res) < 2 or len(sz) < 2:
            raise BridgeError("2d fluids need resolution [x, y] and size [x, y]")
        fluid = mel.eval("create2DFluid %d %d %g %g" % (int(res[0]), int(res[1]), sz[0], sz[1]))
    elif kind == "ocean":
        fluid = mel.eval("CreateOcean")
    else:
        fluid = mel.eval("CreatePond")
    if isinstance(fluid, (list, tuple)):
        fluid = fluid[0] if fluid else None
    if not fluid:
        candidates = [n for n in _created(before) if cmds.nodeType(n) == "fluidShape"]
        fluid = candidates[0] if candidates else None
    if not fluid:
        raise BridgeError("fluid creation for kind=%r returned nothing" % kind)
    shape = _shape_or_self(_long([fluid])[0], "fluidShape")
    transform = transform_of(shape)
    if name:
        transform = cmds.rename(transform, name)
        shape = _shape_or_self(transform, "fluidShape")
    if position is not None and kind in ("3d", "2d"):
        p = _pos_kwargs(position)["position"]
        cmds.setAttr(transform + ".translate", *p)
    emitter_node = None
    if emitter != "none" and kind in ("3d", "2d"):
        ekw: Dict[str, Any] = {
            "densityEmissionRate": float(density),
            "heatEmissionRate": float(heat),
            "fuelEmissionRate": float(fuel),
            "name": (name or "fluid") + "_emitter",
        }
        if emitter == "mesh":
            ekw["type"] = "surface"
            cmds.select(emitter_mesh, shape, replace=True)
            created = cmds.fluidEmitter(**ekw) or []
        else:
            ekw["type"] = "omni"
            created = cmds.fluidEmitter(shape, **ekw) or []
        if isinstance(created, str):
            created = [created]
        if created:
            emitter_node = _long([created[0]])[0]
            _connect_dynamic(shape, emitters=emitter_node)
    applied = _set_attrs(shape, attrs)
    return {
        "fluid": shape,
        "transform": transform,
        "kind": kind,
        "emitter": emitter_node,
        "attrs": applied,
        "summary": node_summary(transform),
        "new_nodes": _created(before),
    }


# Bullet rigid bodies ------------------------------------------------------------
def _bullet_module() -> Any:
    ensure_plugin("bullet", "Enable Bullet in Windows > Settings/Preferences > Plug-in Manager (bullet.mll / bullet.so).")
    try:
        return importlib.import_module("maya.app.mayabullet.RigidBody")
    except ImportError as exc:
        raise BridgeError("Bullet python API (maya.app.mayabullet) is missing: %s. Install the Bullet plugin that ships with Maya." % exc) from exc


def _enum(mod: Any, enum_name: str, member: str, fallback: int) -> Any:
    enum = getattr(mod, enum_name, None)
    return getattr(enum, member, fallback) if enum is not None else fallback


def _filter_kwargs(func: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only kwargs the callable accepts (Bullet's API changed between versions)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


@command("fx.create_rigid_body", mutates=True)
def create_rigid_body(
    nodes: Sequence[str] | None = None,
    active: bool = True,
    mass: float = 1.0,
    friction: float = 0.5,
    bounciness: float = 0.1,
    shape: str = "auto",
    initial_velocity: Sequence[float] | None = None,
) -> Dict[str, Any]:
    """Create Bullet rigid bodies for the given meshes (active = dynamic, else static)."""
    require_maya()
    if shape not in _RIGID_SHAPES:
        raise BridgeError("unknown collider shape %r; use one of %s" % (shape, ", ".join(_RIGID_SHAPES)))
    targets = resolve_targets(nodes)
    rb = _bullet_module()
    shape_member = {
        "auto": "kColliderHull", "box": "kColliderBox", "sphere": "kColliderSphere", "hull": "kColliderHull",
        "mesh": "kColliderMesh", "capsule": "kColliderCapsule", "cylinder": "kColliderCylinder", "plane": "kColliderPlane",
    }[shape]
    shape_default = {"box": 0, "sphere": 1, "capsule": 2, "hull": 3, "auto": 3, "mesh": 4, "cylinder": 5, "plane": 6}[shape]
    collider = _enum(rb, "eShapeType", shape_member, shape_default)
    body_type = _enum(rb, "eBodyType", "kDynamicRigidBody" if active else "kStaticBody", 2 if active else 0)
    creator_cls = getattr(rb, "CreateRigidBody", None)
    if creator_cls is None:
        raise BridgeError("maya.app.mayabullet.RigidBody has no CreateRigidBody; this Maya build is not supported")
    before = cmds.ls(long=True) or []
    bodies: List[Dict[str, Any]] = []
    for node in targets:
        xform = transform_of(node)
        kwargs: Dict[str, Any] = {
            "transformName": xform,
            "bAttachSelected": False,
            "colliderShapeType": collider,
            "bodyType": body_type,
            "mass": float(mass),
            "friction": float(friction),
            "restitution": float(bounciness),
        }
        if initial_velocity is not None:
            if len(initial_velocity) != 3:
                raise BridgeError("initial_velocity must be [x, y, z]")
            kwargs["initialVelocity"] = tuple(float(v) for v in initial_velocity)
        func = getattr(creator_cls, "command", None)
        try:
            if func is not None:
                out = func(**_filter_kwargs(func, kwargs))
            else:
                cmds.select(xform, replace=True)
                out = creator_cls().executeCommandCB()
        except Exception as exc:
            raise BridgeError("Bullet CreateRigidBody failed for %s: %s" % (xform, exc)) from exc
        shapes = out if isinstance(out, (list, tuple)) else ([out] if isinstance(out, str) else [])
        shapes = [s for s in shapes if s]
        if not shapes:
            shapes = [n for n in _created(before) if cmds.nodeType(n) == "bulletRigidBodyShape" and not any(n == b["rigid_body"] for b in bodies)]
        rigid = _long([shapes[0]])[0] if shapes else None
        bodies.append({"node": xform, "rigid_body": rigid})
    solvers = cmds.ls(type="bulletSolverShape", long=True) or []
    return {"bodies": bodies, "active": active, "shape": shape, "solver": solvers[0] if solvers else None, "new_nodes": _created(before)}


# nHair --------------------------------------------------------------------------
@command("fx.create_nhair", mutates=True)
def create_nhair(
    mesh: str,
    count: int = 64,
    length: float = 5.0,
    points_per_hair: int = 10,
    preset: str | None = None,
    attrs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Grow nHair on a mesh (mel createHair) with a uniform grid of follicles."""
    require_maya()
    require_nodes([mesh])
    if count < 1:
        raise BridgeError("count must be at least 1")
    shape = _mesh_shape(mesh)
    side = max(1, int(round(math.sqrt(count))))
    before = cmds.ls(long=True) or []
    cmds.select(shape, replace=True)
    # createHair uCount vCount pointsPerHair randomization edgeBound equalize dynamic length passive hairsPerClump ... (see createHair.mel)
    mel.eval("createHair %d %d %d 0 0 0 0 %g 0 1 1 1" % (side, side, int(points_per_hair), float(length)))
    new = _created(before)
    systems = [n for n in new if cmds.nodeType(n) == "hairSystem"]
    if not systems:
        systems = cmds.ls(type="hairSystem", long=True) or []
    if not systems:
        raise BridgeError("createHair created no hairSystem on %s" % mesh)
    hair = systems[-1]
    follicles = [n for n in new if cmds.nodeType(n) == "follicle"]
    preset_applied = _apply_preset(hair, preset) if preset else False
    applied = _set_attrs(hair, attrs)
    return {
        "hair_system": hair,
        "mesh": _long([mesh])[0],
        "follicle_count": len(follicles),
        "grid": [side, side],
        "length": float(length),
        "preset_applied": preset_applied,
        "attrs": applied,
        "new_nodes": new,
    }


# instancer ----------------------------------------------------------------------
@command("fx.create_instancer", mutates=True)
def create_instancer(source_nodes: Sequence[str], particle: str, name: str = "instancer1", cycle: bool = False) -> Dict[str, Any]:
    """Instance one or more source objects onto every particle of a particle system."""
    require_maya()
    if not source_nodes:
        raise BridgeError("source_nodes must list at least one object to instance")
    require_nodes(list(source_nodes) + [particle])
    pshape = _shape_or_self(particle, "nParticle")
    if cmds.nodeType(pshape) not in ("nParticle", "particle"):
        pshape = _shape_or_self(particle, "particle")
    kwargs: Dict[str, Any] = {"addObject": True, "object": list(source_nodes), "name": name}
    if cycle:
        kwargs["cycle"] = "sequential"  # particleInstancer -cycle accepts "none" or "sequential"
    try:
        inst = cmds.particleInstancer(pshape, **kwargs)
    except Exception as exc:
        raise BridgeError("particleInstancer failed on %s: %s" % (particle, exc)) from exc
    inst_name = _long([inst])[0] if isinstance(inst, str) and inst else (_long(inst)[0] if inst else None)
    if not inst_name:
        found = cmds.ls(type="instancer", long=True) or []
        inst_name = found[-1] if found else None
    return {"instancer": inst_name, "particle": pshape, "sources": _long(source_nodes)}


# baking and caching -------------------------------------------------------------
@command("fx.bake_simulation", mutates=True)
def bake_simulation(
    nodes: Sequence[str] | None = None,
    start: float | None = None,
    end: float | None = None,
    attrs: Sequence[str] | None = None,
    sample_by: float = 1.0,
) -> Dict[str, Any]:
    """Bake simulated transforms (rigid bodies, dynamics) to keyframes via bakeResults."""
    require_maya()
    targets = resolve_targets(nodes)
    s, e = _frame_range(start, end)
    xforms = [transform_of(n) for n in targets]
    kwargs: Dict[str, Any] = {"simulation": True, "time": (s, e), "sampleBy": float(sample_by), "preserveOutsideKeys": True}
    kwargs["attribute"] = list(attrs) if attrs else ["tx", "ty", "tz", "rx", "ry", "rz"]
    cmds.bakeResults(xforms, **kwargs)
    return {"baked": _long(xforms), "start": s, "end": e, "attrs": kwargs["attribute"]}


@command("fx.cache_ncache", mutates=True)
def cache_ncache(
    nodes: Sequence[str] | None = None,
    start: float | None = None,
    end: float | None = None,
    directory: str | None = None,
    name: str | None = None,
    one_file: bool = True,
    fmt: str = "mcx",
) -> Dict[str, Any]:
    """Create an nCache (geometry cache) for nCloth / nParticle nodes (mel doCreateNclothCache)."""
    require_maya()
    if fmt not in ("mcx", "mcc"):
        raise BridgeError("fmt must be mcx or mcc")
    targets = resolve_targets(nodes)
    shapes: List[str] = []
    for n in targets:
        found = [s for s in shapes_of(n) if cmds.nodeType(s) in ("nCloth", "nParticle", "hairSystem")]
        if not found and cmds.nodeType(n) in ("nCloth", "nParticle", "hairSystem"):
            found = [n]
        if not found:
            raise BridgeError("%s is not an nCloth / nParticle / hairSystem; nCache needs nucleus objects" % n)
        shapes.extend(found)
    s, e = _frame_range(start, end)
    directory = directory or ""
    before = cmds.ls(long=True) or []
    cmds.select(shapes, replace=True)
    args = [
        "2", "%g" % s, "%g" % e, "OneFile" if one_file else "OneFilePerFrame", "1",
        directory.replace("\\", "/"), "0", name or "", "0", "add", "0", "1", "1", "0", "1", fmt,
    ]
    code = "doCreateNclothCache 5 { %s }" % ", ".join('"%s"' % a for a in args)
    try:
        out = mel.eval(code)
    except Exception as exc:
        raise BridgeError("doCreateNclothCache failed: %s" % exc) from exc
    caches = list(out) if isinstance(out, (list, tuple)) else ([out] if isinstance(out, str) and out else [])
    if not caches:
        caches = [n for n in _created(before) if cmds.nodeType(n) == "cacheFile"]
    return {"cache_nodes": _long(caches), "shapes": _long(shapes), "start": s, "end": e, "directory": directory or None, "format": fmt, "mel": code}


@command("fx.cache_alembic", mutates=False)
def cache_alembic(
    path: str,
    nodes: Sequence[str] | None = None,
    start: float | None = None,
    end: float | None = None,
    uv: bool = True,
    world_space: bool = True,
    step: float = 1.0,
    strip_namespaces: bool = False,
) -> Dict[str, Any]:
    """Export nodes to an Alembic cache with AbcExport (loads the plugin on demand)."""
    require_maya()
    if not path.lower().endswith(".abc"):
        raise BridgeError("path must end in .abc")
    targets = resolve_targets(nodes)
    ensure_plugin("AbcExport", "Enable AbcExport in the Plug-in Manager.")
    s, e = _frame_range(start, end)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    parts = ["-frameRange %g %g" % (s, e), "-step %g" % float(step), "-dataFormat ogawa"]
    if uv:
        parts.append("-uvWrite")
    if world_space:
        parts.append("-worldSpace")
    if strip_namespaces:
        parts.append("-stripNamespaces")
    roots = _long([transform_of(n) for n in targets])
    for r in roots:
        parts.append("-root %s" % r)
    parts.append("-file %s" % path.replace("\\", "/"))
    job = " ".join(parts)
    try:
        cmds.AbcExport(j=job)
    except Exception as exc:
        raise BridgeError("AbcExport failed: %s (job: %s)" % (exc, job)) from exc
    return {"path": path, "roots": roots, "start": s, "end": e, "job": job, "exists": os.path.exists(path)}


# nucleus ------------------------------------------------------------------------
@command("fx.set_nucleus", mutates=True)
def set_nucleus(
    nucleus: str | None = None,
    gravity: float | None = None,
    gravity_direction: Sequence[float] | None = None,
    air_density: float | None = None,
    wind_speed: float | None = None,
    wind_direction: Sequence[float] | None = None,
    wind_noise: float | None = None,
    time_scale: float | None = None,
    substeps: int | None = None,
    max_collision_iterations: int | None = None,
    start_frame: float | None = None,
    space_scale: float | None = None,
    enable: bool | None = None,
) -> Dict[str, Any]:
    """Set solver attributes on a nucleus node (defaults to the first one in the scene)."""
    require_maya()
    if nucleus:
        require_nodes([nucleus])
    else:
        found = cmds.ls(type="nucleus", long=True) or []
        if not found:
            raise BridgeError("no nucleus node in the scene; create nCloth or nParticles first")
        nucleus = found[0]
    if cmds.nodeType(nucleus) != "nucleus":
        raise BridgeError("%r is not a nucleus node" % nucleus)
    attrs: Dict[str, Any] = {}
    for key, value in (
        ("gravity", gravity), ("airDensity", air_density), ("windSpeed", wind_speed), ("windNoise", wind_noise),
        ("timeScale", time_scale), ("subSteps", substeps), ("maxCollisionIterations", max_collision_iterations),
        ("startFrame", start_frame), ("spaceScale", space_scale), ("enable", enable),
    ):
        if value is not None:
            attrs[key] = value
    for key, vec in (("gravityDirection", gravity_direction), ("windDirection", wind_direction)):
        if vec is not None:
            if len(vec) != 3:
                raise BridgeError("%s must be [x, y, z]" % key)
            attrs[key] = [float(v) for v in vec]
    applied = _set_attrs(nucleus, attrs)
    return {"nucleus": _long([nucleus])[0], "attrs": applied}


# inspection ---------------------------------------------------------------------
_LIST_GROUPS = {
    "nuclei": ("nucleus",),
    "ncloth": ("nCloth",),
    "nrigid": ("nRigid",),
    "nparticles": ("nParticle", "particle"),
    "fluids": ("fluidShape",),
    "hair": ("hairSystem",),
    "bullet": ("bulletRigidBodyShape", "bulletSolverShape", "bulletRigidBodyConstraintShape"),
    "fields": _FIELD_TYPES,
    "emitters": ("pointEmitter", "fluidEmitter"),
    "instancers": ("instancer",),
    "caches": ("cacheFile",),
    "bifrost": ("bifrostGraphShape",),
    "mash": ("MASH_Waiter",),
}


@command("fx.list_dynamics", mutates=False)
def list_dynamics(groups: Sequence[str] | None = None) -> Dict[str, Any]:
    """List every dynamics node in the scene grouped by kind, with the solver each belongs to."""
    require_maya()
    wanted = list(groups) if groups else list(_LIST_GROUPS)
    unknown = [g for g in wanted if g not in _LIST_GROUPS]
    if unknown:
        raise BridgeError("unknown group(s) %s; valid: %s" % (", ".join(unknown), ", ".join(_LIST_GROUPS)))
    out: Dict[str, Any] = {}
    total = 0
    for group in wanted:
        entries: List[Dict[str, Any]] = []
        for ntype in _LIST_GROUPS[group]:
            try:
                nodes = cmds.ls(type=ntype, long=True) or []
            except Exception:
                nodes = []
            for n in nodes:
                entry: Dict[str, Any] = {"name": n, "type": ntype}
                if ntype in ("nCloth", "nRigid", "nParticle", "hairSystem"):
                    solvers = cmds.listConnections(n, type="nucleus") or []
                    entry["nucleus"] = solvers[0] if solvers else None
                    try:
                        entry["enabled"] = bool(cmds.getAttr(n + ".isDynamic"))
                    except Exception:
                        pass
                entries.append(entry)
        out[group] = entries
        total += len(entries)
    out["total"] = total
    return out


@command("fx.run_simulation", mutates=True)
def run_simulation(start: float | None = None, end: float | None = None, step: float = 1.0, max_frames: int = 5000) -> Dict[str, Any]:
    """Scrub the timeline from start to end so dynamics evaluate; returns timing per frame."""
    require_maya()
    if step <= 0:
        raise BridgeError("step must be positive")
    s, e = _frame_range(start, end)
    frames = int(math.floor((e - s) / step)) + 1
    if frames > max_frames:
        raise BridgeError("%d frames exceeds max_frames=%d; raise max_frames or narrow the range" % (frames, max_frames))
    t0 = time.perf_counter()
    slowest = (0.0, s)
    frame = s
    evaluated = 0
    while frame <= e + 1e-9:
        f0 = time.perf_counter()
        cmds.currentTime(frame, edit=True)
        dt = time.perf_counter() - f0
        if dt > slowest[0]:
            slowest = (dt, frame)
        evaluated += 1
        frame += step
    total = time.perf_counter() - t0
    return {
        "start": s,
        "end": e,
        "step": step,
        "frames": evaluated,
        "elapsed_s": round(total, 4),
        "avg_ms_per_frame": round(total / max(evaluated, 1) * 1000.0, 3),
        "slowest_frame": {"frame": slowest[1], "ms": round(slowest[0] * 1000.0, 3)},
        "current_time": e,
    }


@command("fx.delete_dynamics", mutates=True)
def delete_dynamics(nodes: Sequence[str] | None = None, all: bool = False) -> Dict[str, Any]:
    """Delete dynamics nodes (or every dynamics node when all=True). Meshes stay."""
    require_maya()
    if all:
        targets: List[str] = []
        for types in _LIST_GROUPS.values():
            for t in types:
                try:
                    targets.extend(cmds.ls(type=t, long=True) or [])
                except Exception:
                    pass
    else:
        targets = resolve_targets(nodes)
    deleted: List[str] = []
    for n in targets:
        if not cmds.objExists(n):
            continue
        victim = n
        if cmds.nodeType(n) in ("nCloth", "nRigid", "nParticle", "fluidShape", "hairSystem", "instancer", "pointEmitter", "fluidEmitter", "bulletRigidBodyShape") or cmds.nodeType(n) in _FIELD_TYPES:
            victim = transform_of(n)
        try:
            cmds.delete(victim)
            deleted.append(victim)
        except Exception as exc:
            raise BridgeError("could not delete %s: %s" % (victim, exc)) from exc
    return {"deleted": deleted, "count": len(deleted)}


# Bifrost and MASH -----------------------------------------------------------
@command("fx.create_bifrost_graph", mutates=True)
def create_bifrost_graph(name: str = "bifrostGraph1") -> Dict[str, Any]:
    """Create an empty Bifrost graph shape (plugin loaded on demand) plus a how-to for adding compounds."""
    require_maya()
    ensure_plugin("bifrostGraph", "Bifrost ships with Maya; enable bifrostGraph in the Plug-in Manager.")
    before = cmds.ls(long=True) or []
    try:
        shape = cmds.createNode("bifrostGraphShape", name=name + "Shape")
    except Exception as exc:
        raise BridgeError("could not create bifrostGraphShape: %s" % exc) from exc
    shape = _long([shape])[0]
    transform = transform_of(shape)
    try:
        transform = cmds.rename(transform, name)
    except Exception:
        pass
    return {
        "graph": shape,
        "transform": transform,
        "new_nodes": _created(before),
        "hint": (
            "Add compounds with cmds.vnnCompound(graph, '/', addNode='BifrostGraph,Core::Geometry,create_mesh_cube'), "
            "connect with cmds.vnnConnect(graph, '/create_mesh_cube.cube_mesh', '/output.out_mesh'), "
            "list nodes with cmds.vnnCompound(graph, '/', listNodes=True), and expose an output port with "
            "cmds.vnnNode(graph, '/output', createInputPort=('out_mesh', 'Object')). Use maya_execute_python for these."
        ),
    }


@command("fx.create_mash_network", mutates=True)
def create_mash_network(
    nodes: Sequence[str] | None = None,
    count: int = 10,
    distribution: str = "linear",
    name: str = "MASH1",
    geometry_type: str = "instancer",
) -> Dict[str, Any]:
    """Create a MASH network from the given meshes (guarded import of MASH.api)."""
    require_maya()
    if distribution not in _MASH_ARRANGEMENTS:
        raise BridgeError("unknown distribution %r; use one of %s" % (distribution, ", ".join(_MASH_ARRANGEMENTS)))
    if geometry_type not in ("instancer", "mesh"):
        raise BridgeError("geometry_type must be instancer or mesh")
    if count < 1:
        raise BridgeError("count must be at least 1")
    targets = resolve_targets(nodes)
    try:
        ensure_plugin("MASH", "MASH ships with Maya; enable MASH in the Plug-in Manager.")
    except BridgeError:
        raise
    try:
        mapi = importlib.import_module("MASH.api")
    except ImportError as exc:
        raise BridgeError("MASH python API is not importable (%s); MASH ships with Maya 2016.5+ and needs the MASH plugin loaded" % exc) from exc
    before = cmds.ls(long=True) or []
    cmds.select([transform_of(n) for n in targets], replace=True)
    try:
        network = mapi.Network()
        network.createNetwork(name=name, geometry="Instancer" if geometry_type == "instancer" else "Mesh")
        distribute = getattr(network, "distribute", None)
        waiter = getattr(network, "waiter", None)
    except Exception as exc:
        raise BridgeError("MASH createNetwork failed: %s" % exc) from exc
    if distribute:
        _set_attrs(distribute, {"pointCount": int(count), "arrangement": _MASH_ARRANGEMENTS[distribution]})
    return {
        "network": name,
        "waiter": waiter,
        "distribute": distribute,
        "instancer": getattr(network, "instancer", None),
        "count": int(count),
        "distribution": distribution,
        "sources": _long(targets),
        "new_nodes": _created(before),
        "hint": "Add nodes with MASH.api.Network(waiter).addNode('MASH_Random') in maya_execute_python.",
    }


# quick previs presets -------------------------------------------------------
def _preset_group(name: str, members: Sequence[str]) -> str:
    xforms = []
    for m in members:
        if not m or not cmds.objExists(m):
            continue
        x = transform_of(m)
        if x not in xforms:
            xforms.append(x)
    if not xforms:
        return ""
    grp = cmds.group(xforms, name=name)
    return _long([grp])[0] if grp else ""


@command("fx.create_explosion_preset", mutates=True)
def create_explosion_preset(name: str = "explosion", position: Sequence[float] | None = None, scale: float = 1.0, frames: int = 30) -> Dict[str, Any]:
    """Quick previs explosion: burst of ball particles, an expanding fluid fireball and gravity."""
    require_maya()
    if scale <= 0:
        raise BridgeError("scale must be positive")
    pos = list(position or (0.0, 0.0, 0.0))
    before = cmds.ls(long=True) or []
    parts = create_nparticle(
        name=name + "_debris", style="balls", emitter_type="omni", rate=400.0 * scale, position=pos, speed=8.0 * scale,
        lifespan=2.0, attrs={"radius": 0.15 * scale, "conserve": 0.95},
    )
    emitter = parts["emitter"]
    if emitter:
        # emit for a short burst only
        cmds.setKeyframe(emitter, attribute="rate", time=1, value=400.0 * scale)
        cmds.setKeyframe(emitter, attribute="rate", time=max(2, frames // 6), value=0.0)
    res = max(8, int(16 * scale))
    fluid = create_fluid(
        kind="3d", name=name + "_fireball", resolution=[res, res, res], size=[10.0 * scale] * 3, emitter="point", position=pos,
        density=2.0, heat=2.0, fuel=1.0, attrs={"buoyancy": 5.0, "dissipation": 0.2, "densityTension": 0.05},
    )
    fluid_emitter = fluid["emitter"]
    if fluid_emitter:
        cmds.setKeyframe(fluid_emitter, attribute="densityEmissionRate", time=1, value=2.0)
        cmds.setKeyframe(fluid_emitter, attribute="densityEmissionRate", time=max(2, frames // 5), value=0.0)
    grav = add_field(type="gravity", targets=[parts["particle"]], magnitude=9.8, name=name + "_gravity")
    turb = add_field(type="turbulence", targets=[parts["particle"], fluid["fluid"]], magnitude=6.0 * scale, attenuation=0.5, position=pos, name=name + "_turbulence")
    group = _preset_group(name + "_grp", [parts["transform"], fluid["transform"], grav["field"], turb["field"]])
    return {
        "preset": "explosion", "group": group, "particle": parts["particle"], "fluid": fluid["fluid"],
        "fields": [grav["field"], turb["field"]], "position": pos, "scale": scale, "new_nodes": _created(before),
        "hint": "Run fx.run_simulation over the first %d frames, then tweak the fluid shading (incandescence, opacity) for the look." % frames,
    }


@command("fx.create_dust_preset", mutates=True)
def create_dust_preset(name: str = "dust", position: Sequence[float] | None = None, scale: float = 1.0, rate: float = 60.0) -> Dict[str, Any]:
    """Quick previs dust: slow cloud particles drifting in a light wind with turbulence."""
    require_maya()
    pos = list(position or (0.0, 0.0, 0.0))
    before = cmds.ls(long=True) or []
    parts = create_nparticle(
        name=name + "_cloud", style="cloud", emitter_type="volume", rate=rate, position=pos, speed=0.2 * scale, lifespan=6.0,
        attrs={"radius": 0.8 * scale, "conserve": 0.9, "opacity": 0.15},
    )
    if parts["emitter"]:
        try:
            cmds.setAttr(parts["emitter"] + ".scale", 4.0 * scale, 1.0 * scale, 4.0 * scale)
        except Exception:
            pass
    wind = add_field(type="uniform", targets=[parts["particle"]], magnitude=0.4 * scale, direction=[1.0, 0.1, 0.0], name=name + "_wind")
    turb = add_field(type="turbulence", targets=[parts["particle"]], magnitude=1.5 * scale, attenuation=0.0, position=pos, name=name + "_turbulence")
    group = _preset_group(name + "_grp", [parts["transform"], wind["field"], turb["field"]])
    return {"preset": "dust", "group": group, "particle": parts["particle"], "fields": [wind["field"], turb["field"]], "new_nodes": _created(before)}


@command("fx.create_precipitation_preset", mutates=True)
def create_precipitation_preset(kind: str = "rain", name: str | None = None, position: Sequence[float] | None = None, width: float = 20.0, height: float = 15.0, rate: float = 500.0) -> Dict[str, Any]:
    """Quick previs rain or snow falling from a volume emitter above the scene."""
    require_maya()
    if kind not in ("rain", "snow"):
        raise BridgeError("kind must be rain or snow")
    name = name or kind
    pos = list(position or (0.0, height, 0.0))
    before = cmds.ls(long=True) or []
    if kind == "rain":
        parts = create_nparticle(name=name, style="streak", emitter_type="volume", rate=rate, position=pos, speed=0.0, lifespan=3.0, attrs={"radius": 0.03, "conserve": 1.0})
        fields = [add_field(type="gravity", targets=[parts["particle"]], magnitude=30.0, name=name + "_gravity")["field"]]
    else:
        parts = create_nparticle(name=name, style="points", emitter_type="volume", rate=rate * 0.4, position=pos, speed=0.0, lifespan=12.0, attrs={"radius": 0.08, "conserve": 0.98, "drag": 0.2})
        fields = [
            add_field(type="gravity", targets=[parts["particle"]], magnitude=1.5, name=name + "_gravity")["field"],
            add_field(type="turbulence", targets=[parts["particle"]], magnitude=2.0, attenuation=0.0, name=name + "_turbulence")["field"],
        ]
    if parts["emitter"]:
        try:
            cmds.setAttr(parts["emitter"] + ".scale", float(width), 0.5, float(width))
        except Exception:
            pass
    group = _preset_group(name + "_grp", [parts["transform"]] + fields)
    return {"preset": kind, "group": group, "particle": parts["particle"], "emitter": parts["emitter"], "fields": fields, "new_nodes": _created(before)}


@command("fx.create_debris_preset", mutates=True)
def create_debris_preset(name: str = "debris", position: Sequence[float] | None = None, count: int = 60, scale: float = 1.0, use_bullet: bool = False) -> Dict[str, Any]:
    """Quick previs debris: small cube chunks instanced onto burst particles (or Bullet bodies when use_bullet)."""
    require_maya()
    if count < 1:
        raise BridgeError("count must be at least 1")
    pos = list(position or (0.0, 0.0, 0.0))
    before = cmds.ls(long=True) or []
    if use_bullet:
        chunks: List[str] = []
        side = max(1, int(round(count ** (1.0 / 3.0))))
        idx = 0
        for x in range(side):
            for y in range(side):
                for z in range(side):
                    if idx >= count:
                        break
                    cube = cmds.polyCube(name="%s_chunk%d" % (name, idx + 1), width=0.4 * scale, height=0.4 * scale, depth=0.4 * scale)
                    xform = cube[0] if cube else None
                    if xform:
                        cmds.setAttr(xform + ".translate", pos[0] + (x - side / 2.0) * 0.5 * scale, pos[1] + 1.0 + y * 0.5 * scale, pos[2] + (z - side / 2.0) * 0.5 * scale)
                        chunks.append(_long([xform])[0])
                    idx += 1
        bodies = create_rigid_body(nodes=chunks, active=True, mass=0.5 * scale, friction=0.6, bounciness=0.2, shape="box")
        group = _preset_group(name + "_grp", chunks)
        return {"preset": "debris", "mode": "bullet", "group": group, "chunks": chunks, "bodies": bodies["bodies"], "new_nodes": _created(before)}
    cube = cmds.polyCube(name=name + "_chunk", width=0.3 * scale, height=0.3 * scale, depth=0.3 * scale)
    chunk = _long([cube[0]])[0] if cube else None
    if not chunk:
        raise BridgeError("polyCube failed while building the debris chunk")
    try:
        cmds.setAttr(chunk + ".visibility", 0)
    except Exception:
        pass
    parts = create_nparticle(
        name=name + "_particles", style="points", emitter_type="omni", rate=float(count) * 4.0, position=pos, speed=6.0 * scale, lifespan=4.0,
        attrs={"conserve": 0.97, "radius": 0.1 * scale},
    )
    if parts["emitter"]:
        cmds.setKeyframe(parts["emitter"], attribute="rate", time=1, value=float(count) * 4.0)
        cmds.setKeyframe(parts["emitter"], attribute="rate", time=2, value=0.0)
    inst = create_instancer(source_nodes=[chunk], particle=parts["particle"], name=name + "_instancer")
    grav = add_field(type="gravity", targets=[parts["particle"]], magnitude=9.8, name=name + "_gravity")
    group = _preset_group(name + "_grp", [chunk, parts["transform"], inst["instancer"] or "", grav["field"]])
    return {
        "preset": "debris", "mode": "particles", "group": group, "chunk": chunk, "particle": parts["particle"],
        "instancer": inst["instancer"], "fields": [grav["field"]], "new_nodes": _created(before),
        "hint": "Add rotationPP with an expression or swap the chunk for real fractured pieces via fx.create_instancer.",
    }
