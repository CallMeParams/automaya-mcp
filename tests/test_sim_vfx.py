"""Unit + integration tests for the sim_vfx domain (fx.* commands, maya_* tools)."""
from __future__ import annotations

import sys
import types

import pytest
from maya import mel
from tests.conftest import parse

from automaya_bridge.handlers import sim_vfx
from automaya_bridge.handlers._util import BridgeError


@pytest.fixture(autouse=True)
def _reset_mel():
    mel.evaluated.clear()
    mel.responses.clear()
    yield
    mel.evaluated.clear()
    mel.responses.clear()


def _mesh_relatives(*args, **kwargs):
    """listRelatives stub: transforms have one mesh shape, shapes have one parent."""
    node = args[0] if args else ""
    if kwargs.get("shapes"):
        return [node + "Shape"]
    if kwargs.get("parent"):
        return [node.replace("Shape", "")]
    return []


def _node_types(mapping):
    def _nodeType(node, **kwargs):
        return mapping.get(node, "transform")

    return _nodeType


# unit: nCloth ------------------------------------------------------------------
def test_create_ncloth_applies_preset_and_attrs(fake_maya):
    fake_maya.responses["listRelatives"] = _mesh_relatives
    fake_maya.responses["listConnections"] = ["nucleus1"]
    mel.responses["createNCloth"] = ["nClothShape1"]
    out = sim_vfx.create_ncloth(mesh="pPlane1", preset="silk", attrs={"thickness": 0.05})
    assert out["ncloth"] == "nClothShape1" and out["nucleus"] == "nucleus1"
    assert out["preset_applied"] is True and out["attrs"] == {"thickness": 0.05}
    assert any(c.startswith("createNCloth 0") for c in mel.evaluated)
    assert any("applyAttrPreset" in c and "silk" in c for c in mel.evaluated)
    assert fake_maya.calls_to("select")[0][0] == ("pPlane1Shape",)
    assert ("nClothShape1.thickness", 0.05) == fake_maya.calls_to("setAttr")[0][0]


def test_create_ncloth_rejects_bad_preset_and_non_mesh(fake_maya):
    with pytest.raises(BridgeError, match="preset"):
        sim_vfx.create_ncloth(mesh="pPlane1", preset="velvet")
    with pytest.raises(BridgeError, match="no mesh shape"):
        sim_vfx.create_ncloth(mesh="locator1")


def test_create_ncloth_missing_node(fake_maya):
    fake_maya.existing.add("something_else")
    with pytest.raises(BridgeError, match="not found"):
        sim_vfx.create_ncloth(mesh="ghost")


def test_create_ncloth_reuses_nucleus(fake_maya):
    fake_maya.responses["listRelatives"] = _mesh_relatives
    fake_maya.responses["nodeType"] = _node_types({"nucleus2": "nucleus"})
    mel.responses["createNCloth"] = ["nClothShape2"]
    sim_vfx.create_ncloth(mesh="pPlane1", nucleus="nucleus2", local_space=True)
    assert 'setActiveNucleusNode("nucleus2")' in mel.evaluated[0]
    assert "createNCloth 1" in mel.evaluated


def test_create_ncloth_collider(fake_maya):
    fake_maya.responses["listRelatives"] = _mesh_relatives
    mel.responses["makeCollideNCloth"] = ["nRigidShape1"]
    out = sim_vfx.create_ncloth_collider(mesh="ground", thickness=0.2)
    assert out["nrigid"] == "nRigidShape1"
    assert fake_maya.calls_to("setAttr")[0][0] == ("nRigidShape1.thickness", 0.2)


# unit: particles and fields ----------------------------------------------------------
def test_create_nparticle_directional(fake_maya):
    fake_maya.responses["nParticle"] = ["spark", "sparkShape"]
    fake_maya.responses["emitter"] = ["spark_emitter"]
    out = sim_vfx.create_nparticle(name="spark", style="balls", emitter_type="directional", rate=50, direction=[0, 1, 0], lifespan=2.0, position=[1, 2, 3])
    assert out["particle"] == "sparkShape" and out["emitter"] == "spark_emitter"
    ekw = fake_maya.calls_to("emitter")[0][1]
    assert ekw["type"] == "direction" and ekw["directionY"] == 1.0 and ekw["position"] == (1.0, 2.0, 3.0)
    set_calls = [c[0] for c in fake_maya.calls_to("setAttr")]
    assert ("sparkShape.particleRenderType", 4) in set_calls
    assert ("sparkShape.lifespan", 2.0) in set_calls
    assert fake_maya.calls_to("connectDynamic")[0] == (("sparkShape",), {"emitters": "spark_emitter"})


def test_create_nparticle_bad_style(fake_maya):
    with pytest.raises(BridgeError, match="style"):
        sim_vfx.create_nparticle(style="glitter")
    with pytest.raises(BridgeError, match="emitter type"):
        sim_vfx.create_nparticle(emitter_type="spiral")


def test_add_field_connects_targets(fake_maya):
    fake_maya.responses["turbulence"] = ["turbulenceField1"]
    out = sim_vfx.add_field(type="turbulence", targets=["nParticleShape1"], magnitude=5, attenuation=1, position=[0, 1, 0], attrs={"frequency": 2})
    assert out["field"] == "turbulenceField1" and out["connected_to"] == ["nParticleShape1"]
    assert fake_maya.calls_to("connectDynamic")[0] == (("nParticleShape1",), {"fields": "turbulenceField1"})
    kw = fake_maya.calls_to("turbulence")[0][1]
    assert kw["magnitude"] == 5.0 and kw["position"] == (0.0, 1.0, 0.0)


def test_add_field_unknown_type(fake_maya):
    with pytest.raises(BridgeError, match="field type"):
        sim_vfx.add_field(type="magnet")


# unit: fluids ------------------------------------------------------------------------
def test_create_fluid_3d_point_emitter(fake_maya):
    mel.responses["create3DFluid"] = "fluidShape1"
    fake_maya.responses["listRelatives"] = _mesh_relatives
    fake_maya.responses["nodeType"] = _node_types({"fluidShape1": "fluidShape"})
    fake_maya.responses["objectType"] = lambda node, **kw: node == "fluid1" if kw.get("isType") == "transform" else "x"
    fake_maya.responses["fluidEmitter"] = ["fluidEmitter1"]
    out = sim_vfx.create_fluid(kind="3d", resolution=[8, 8, 8], size=[5, 5, 5], density=2.0, heat=1.0)
    assert out["fluid"] == "fluidShape1" and out["emitter"] == "fluidEmitter1"
    assert "create3DFluid 8 8 8 5 5 5" in mel.evaluated
    ekw = fake_maya.calls_to("fluidEmitter")[0][1]
    assert ekw["type"] == "omni" and ekw["densityEmissionRate"] == 2.0 and ekw["heatEmissionRate"] == 1.0


def test_create_fluid_mesh_emitter_needs_mesh(fake_maya):
    with pytest.raises(BridgeError, match="emitter_mesh"):
        sim_vfx.create_fluid(kind="3d", emitter="mesh")
    with pytest.raises(BridgeError, match="fluid kind"):
        sim_vfx.create_fluid(kind="lava")


def test_create_fluid_ocean(fake_maya):
    mel.responses["CreateOcean"] = ["oceanShape1"]
    out = sim_vfx.create_fluid(kind="ocean")
    assert out["fluid"] == "oceanShape1" and out["emitter"] is None


# unit: Bullet -----------------------------------------------------------------------
class _FakeRigidBody(types.ModuleType):
    def __init__(self):
        super().__init__("maya.app.mayabullet.RigidBody")
        self.calls = []
        mod = self

        class eShapeType:
            kColliderBox = 0
            kColliderSphere = 1
            kColliderHull = 3
            kColliderMesh = 4

        class eBodyType:
            kStaticBody = 0
            kDynamicRigidBody = 2

        class CreateRigidBody:
            @staticmethod
            def command(transformName=None, bAttachSelected=True, colliderShapeType=None, bodyType=None, mass=1.0, friction=0.5, restitution=0.1):
                mod.calls.append(dict(transformName=transformName, colliderShapeType=colliderShapeType, bodyType=bodyType, mass=mass, friction=friction, restitution=restitution))
                return ["bulletRigidBodyShape%d" % len(mod.calls)]

        self.eShapeType = eShapeType
        self.eBodyType = eBodyType
        self.CreateRigidBody = CreateRigidBody


@pytest.fixture()
def fake_bullet(monkeypatch):
    mod = _FakeRigidBody()
    monkeypatch.setitem(sys.modules, "maya.app.mayabullet.RigidBody", mod)
    return mod


def test_create_rigid_body_uses_bullet_api(fake_maya, fake_bullet):
    out = sim_vfx.create_rigid_body(nodes=["crate1", "crate2"], active=True, mass=3.0, shape="box", initial_velocity=[0, 0, 1])
    assert [b["rigid_body"] for b in out["bodies"]] == ["bulletRigidBodyShape1", "bulletRigidBodyShape2"]
    assert fake_bullet.calls[0]["colliderShapeType"] == 0 and fake_bullet.calls[0]["bodyType"] == 2 and fake_bullet.calls[0]["mass"] == 3.0
    assert "initialVelocity" not in fake_bullet.calls[0]  # filtered: the fake API does not accept it
    assert any(c[1].get("loaded") for c in fake_maya.calls_to("pluginInfo"))


def test_create_rigid_body_static(fake_maya, fake_bullet):
    out = sim_vfx.create_rigid_body(nodes=["floor"], active=False, shape="mesh")
    assert out["active"] is False and fake_bullet.calls[0]["bodyType"] == 0 and fake_bullet.calls[0]["colliderShapeType"] == 4


def test_create_rigid_body_missing_plugin(fake_maya, monkeypatch):
    monkeypatch.delitem(sys.modules, "maya.app.mayabullet.RigidBody", raising=False)
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False
    fake_maya.responses["loadPlugin"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no bullet"))
    with pytest.raises(BridgeError, match="bullet"):
        sim_vfx.create_rigid_body(nodes=["crate1"])


def test_create_rigid_body_missing_python_api(fake_maya, monkeypatch):
    monkeypatch.delitem(sys.modules, "maya.app.mayabullet.RigidBody", raising=False)
    with pytest.raises(BridgeError, match="mayabullet"):
        sim_vfx.create_rigid_body(nodes=["crate1"])


def test_create_rigid_body_bad_shape(fake_maya):
    with pytest.raises(BridgeError, match="collider shape"):
        sim_vfx.create_rigid_body(nodes=["crate1"], shape="donut")


# unit: hair, instancer, caching -----------------------------------------------------
def test_create_nhair(fake_maya):
    fake_maya.responses["listRelatives"] = _mesh_relatives
    fake_maya.responses["ls"] = lambda *a, **k: ["hairSystemShape1", "follicle1", "follicle2"] if k.get("long") and not a else []
    fake_maya.responses["nodeType"] = _node_types({"hairSystemShape1": "hairSystem", "follicle1": "follicle", "follicle2": "follicle"})
    fake_maya.responses["listConnections"] = []
    calls = {"n": 0}

    def _ls(*a, **k):
        calls["n"] += 1
        if a:
            return list(a)
        return [] if calls["n"] == 1 else ["hairSystemShape1", "follicle1", "follicle2"]

    fake_maya.responses["ls"] = _ls
    out = sim_vfx.create_nhair(mesh="head", count=16, length=3.0, attrs={"hairsPerClump": 12})
    assert out["hair_system"] == "hairSystemShape1" and out["follicle_count"] == 2 and out["grid"] == [4, 4]
    assert "createHair 4 4 10 0 0 0 0 3 0 1 1 1" in mel.evaluated


def test_create_instancer(fake_maya):
    fake_maya.responses["nodeType"] = _node_types({"debrisShape": "nParticle"})
    fake_maya.responses["particleInstancer"] = "instancer1"
    out = sim_vfx.create_instancer(source_nodes=["rock1", "rock2"], particle="debrisShape", cycle=True)
    assert out["instancer"] == "instancer1"
    kw = fake_maya.calls_to("particleInstancer")[0][1]
    assert kw["object"] == ["rock1", "rock2"] and kw["cycle"] == "sequential"
    with pytest.raises(BridgeError, match="source_nodes"):
        sim_vfx.create_instancer(source_nodes=[], particle="debrisShape")


def test_bake_simulation_defaults_to_timeline(fake_maya):
    fake_maya.responses["playbackOptions"] = lambda **k: 1.0 if k.get("minTime") else 48.0
    out = sim_vfx.bake_simulation(nodes=["crate1"])
    assert out["start"] == 1.0 and out["end"] == 48.0
    kw = fake_maya.calls_to("bakeResults")[0][1]
    assert kw["simulation"] is True and kw["time"] == (1.0, 48.0) and "tx" in kw["attribute"]
    with pytest.raises(BridgeError, match="before start"):
        sim_vfx.bake_simulation(nodes=["crate1"], start=10, end=5)


def test_cache_ncache_builds_mel(fake_maya):
    fake_maya.responses["nodeType"] = _node_types({"nClothShape1": "nCloth"})
    mel.responses["doCreateNclothCache"] = ["cacheFile1"]
    out = sim_vfx.cache_ncache(nodes=["nClothShape1"], start=1, end=24, directory="C:\\caches", name="cloth")
    assert out["cache_nodes"] == ["cacheFile1"]
    assert out["mel"].startswith('doCreateNclothCache 5 { "2", "1", "24", "OneFile", "1", "C:/caches", "0", "cloth"')
    with pytest.raises(BridgeError, match="nucleus objects"):
        sim_vfx.cache_ncache(nodes=["pCube1"], start=1, end=2)


def test_cache_alembic_job(fake_maya, tmp_path):
    path = str(tmp_path / "out" / "sim.abc")
    out = sim_vfx.cache_alembic(path=path, nodes=["crate1", "crate2"], start=1, end=10, world_space=True, uv=False)
    job = fake_maya.calls_to("AbcExport")[0][1]["j"]
    assert "-frameRange 1 10" in job and "-worldSpace" in job and "-uvWrite" not in job
    assert "-root crate1" in job and "-root crate2" in job and job.endswith("-file " + path.replace("\\", "/"))
    assert out["exists"] is False and (tmp_path / "out").is_dir()
    with pytest.raises(BridgeError, match=".abc"):
        sim_vfx.cache_alembic(path="/tmp/x.fbx", nodes=["crate1"])


def test_cache_alembic_missing_plugin(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False
    fake_maya.responses["loadPlugin"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(BridgeError, match="AbcExport"):
        sim_vfx.cache_alembic(path="/tmp/x.abc", nodes=["crate1"])


# unit: nucleus, listing, running, deleting ------------------------------------------
def test_set_nucleus(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: ["nucleus1"] if k.get("type") == "nucleus" else (list(a) if a else [])
    fake_maya.responses["nodeType"] = _node_types({"nucleus1": "nucleus"})
    out = sim_vfx.set_nucleus(gravity=3.0, gravity_direction=[0, 0, -1], substeps=4, wind_speed=2.0)
    assert out["nucleus"] == "nucleus1"
    calls = [c[0] for c in fake_maya.calls_to("setAttr")]
    assert ("nucleus1.gravity", 3.0) in calls and ("nucleus1.gravityDirection", 0.0, 0.0, -1.0) in calls and ("nucleus1.subSteps", 4) in calls


def test_set_nucleus_none_in_scene(fake_maya):
    with pytest.raises(BridgeError, match="no nucleus"):
        sim_vfx.set_nucleus(gravity=1.0)


def test_list_dynamics(fake_maya):
    fake_maya.responses["ls"] = lambda *a, **k: {"nCloth": ["nClothShape1"], "nucleus": ["nucleus1"], "gravityField": ["gravityField1"]}.get(k.get("type"), [])
    fake_maya.responses["listConnections"] = ["nucleus1"]
    fake_maya.responses["getAttr"] = True
    out = sim_vfx.list_dynamics()
    assert out["ncloth"][0] == {"name": "nClothShape1", "type": "nCloth", "nucleus": "nucleus1", "enabled": True}
    assert out["nuclei"] == [{"name": "nucleus1", "type": "nucleus"}] and out["total"] == 3
    with pytest.raises(BridgeError, match="unknown group"):
        sim_vfx.list_dynamics(groups=["ghosts"])


def test_run_simulation_scrubs_frames(fake_maya):
    out = sim_vfx.run_simulation(start=1, end=5, step=2)
    frames = [c[0][0] for c in fake_maya.calls_to("currentTime")]
    assert frames == [1.0, 3.0, 5.0] and out["frames"] == 3 and "avg_ms_per_frame" in out
    with pytest.raises(BridgeError, match="max_frames"):
        sim_vfx.run_simulation(start=1, end=100, max_frames=10)
    with pytest.raises(BridgeError, match="step"):
        sim_vfx.run_simulation(start=1, end=2, step=0)


def test_delete_dynamics_deletes_transform_of_shapes(fake_maya):
    fake_maya.responses["nodeType"] = _node_types({"nClothShape1": "nCloth"})
    fake_maya.responses["objectType"] = lambda node, **kw: node != "nClothShape1"
    fake_maya.responses["listRelatives"] = _mesh_relatives
    out = sim_vfx.delete_dynamics(nodes=["nClothShape1", "gravityField1"])
    assert out["deleted"] == ["nCloth1", "gravityField1"]


# unit: Bifrost and MASH ---------------------------------------------------------------
def test_create_bifrost_graph(fake_maya):
    fake_maya.responses["createNode"] = "bifrostGraph1Shape"
    fake_maya.responses["rename"] = lambda n, new: new
    out = sim_vfx.create_bifrost_graph(name="bifrostGraph1")
    assert out["graph"] == "bifrostGraph1Shape" and "vnnCompound" in out["hint"]
    assert fake_maya.calls_to("createNode")[0][0] == ("bifrostGraphShape",)


def test_create_bifrost_graph_missing_plugin(fake_maya):
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False
    fake_maya.responses["loadPlugin"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(BridgeError, match="bifrostGraph"):
        sim_vfx.create_bifrost_graph()


@pytest.fixture()
def fake_mash(monkeypatch):
    mod = types.ModuleType("MASH.api")
    created = {}

    class Network:
        def __init__(self, waiter=None):
            self.waiter = None
            self.distribute = None
            self.instancer = None

        def createNetwork(self, name="MASH1", geometry="Instancer"):
            created["name"] = name
            created["geometry"] = geometry
            self.waiter = name
            self.distribute = name + "_Distribute"
            self.instancer = name + "_Instancer"

    mod.Network = Network
    mod.created = created
    monkeypatch.setitem(sys.modules, "MASH.api", mod)
    return mod


def test_create_mash_network(fake_maya, fake_mash):
    out = sim_vfx.create_mash_network(nodes=["leaf"], count=40, distribution="grid", name="Leaves")
    assert out["waiter"] == "Leaves" and out["distribute"] == "Leaves_Distribute" and fake_mash.created["geometry"] == "Instancer"
    calls = [c[0] for c in fake_maya.calls_to("setAttr")]
    assert ("Leaves_Distribute.pointCount", 40) in calls and ("Leaves_Distribute.arrangement", 4) in calls


def test_create_mash_network_missing(fake_maya, monkeypatch):
    monkeypatch.delitem(sys.modules, "MASH.api", raising=False)
    monkeypatch.setitem(sys.modules, "MASH", None)
    with pytest.raises(BridgeError, match="MASH"):
        sim_vfx.create_mash_network(nodes=["leaf"])
    with pytest.raises(BridgeError, match="distribution"):
        sim_vfx.create_mash_network(nodes=["leaf"], distribution="spiral")


# unit: presets -------------------------------------------------------------------------
def _preset_scene(fake_maya):
    fake_maya.responses["nParticle"] = lambda **k: [k["name"], k["name"] + "Shape"]
    fake_maya.responses["emitter"] = lambda **k: [k["name"]]
    fake_maya.responses["gravity"] = lambda **k: [k.get("name", "gravityField1")]
    fake_maya.responses["turbulence"] = lambda **k: [k.get("name", "turbulenceField1")]
    fake_maya.responses["uniform"] = lambda **k: [k.get("name", "uniformField1")]
    fake_maya.responses["fluidEmitter"] = lambda *a, **k: [k["name"]]
    fake_maya.responses["group"] = lambda *a, **k: k["name"]
    fake_maya.responses["polyCube"] = lambda **k: [k["name"], "polyCube1"]
    fake_maya.responses["particleInstancer"] = lambda *a, **k: k["name"]
    fake_maya.responses["listRelatives"] = _mesh_relatives
    mel.responses["create3DFluid"] = "fireShape"


def test_explosion_preset(fake_maya):
    _preset_scene(fake_maya)
    out = sim_vfx.create_explosion_preset(name="boom", position=[0, 1, 0], scale=2.0)
    assert out["particle"] == "boom_debrisShape" and out["fluid"] == "boom_fireballShape" and out["group"] == "boom_grp"
    assert out["fields"] == ["boom_gravity", "boom_turbulence"]
    assert "create3DFluid 32 32 32 20 20 20" in mel.evaluated
    keyed = [c[1]["attribute"] for c in fake_maya.calls_to("setKeyframe")]
    assert "rate" in keyed and "densityEmissionRate" in keyed
    with pytest.raises(BridgeError, match="scale"):
        sim_vfx.create_explosion_preset(scale=0)


def test_dust_and_precipitation_presets(fake_maya):
    _preset_scene(fake_maya)
    dust = sim_vfx.create_dust_preset(name="dust")
    assert dust["fields"] == ["dust_wind", "dust_turbulence"]
    rain = sim_vfx.create_precipitation_preset(kind="rain")
    assert rain["fields"] == ["rain_gravity"] and rain["emitter"] == "rain_emitter"
    snow = sim_vfx.create_precipitation_preset(kind="snow", name="blizzard")
    assert snow["fields"] == ["blizzard_gravity", "blizzard_turbulence"]
    with pytest.raises(BridgeError, match="rain or snow"):
        sim_vfx.create_precipitation_preset(kind="hail")


def test_debris_preset_particles(fake_maya):
    _preset_scene(fake_maya)
    out = sim_vfx.create_debris_preset(name="rubble", count=20)
    assert out["mode"] == "particles" and out["instancer"] == "rubble_instancer" and out["chunk"] == "rubble_chunk"
    assert ("rubble_chunk.visibility", 0) in [c[0] for c in fake_maya.calls_to("setAttr")]


def test_debris_preset_bullet(fake_maya, fake_bullet):
    _preset_scene(fake_maya)
    out = sim_vfx.create_debris_preset(name="rubble", count=8, use_bullet=True)
    assert out["mode"] == "bullet" and len(out["chunks"]) == 8 and len(out["bodies"]) == 8
    assert fake_bullet.calls[0]["colliderShapeType"] == 0


# integration: real socket + registry, stub maya ---------------------------------------------
async def test_tool_create_nparticle(call_tool, fake_maya):
    fake_maya.responses["nParticle"] = ["mist", "mistShape"]
    fake_maya.responses["emitter"] = ["mist_emitter"]
    data = parse(await call_tool("maya_create_nparticle", {"params": {"name": "mist", "style": "cloud", "emitter_type": "volume", "rate": 20}}))
    assert data["particle"] == "mistShape" and data["emitter"] == "mist_emitter" and data["style"] == "cloud"


async def test_tool_create_ncloth_and_list(call_tool, fake_maya):
    fake_maya.responses["listRelatives"] = _mesh_relatives
    mel.responses["createNCloth"] = ["nClothShape1"]
    data = parse(await call_tool("maya_create_ncloth", {"params": {"mesh": "flag", "preset": "silk"}}))
    assert data["ncloth"] == "nClothShape1" and data["preset"] == "silk"
    listing = parse(await call_tool("maya_list_dynamics", {"params": {"groups": ["ncloth", "nuclei"]}}))
    assert set(listing) == {"ncloth", "nuclei", "total"}


async def test_tool_run_simulation_and_nucleus_error(call_tool, fake_maya):
    data = parse(await call_tool("maya_run_simulation", {"params": {"start": 1, "end": 3}}))
    assert data["frames"] == 3
    text = await call_tool("maya_set_nucleus", {"params": {"gravity": 2.0}})
    assert text.startswith("Error") and "no nucleus" in text


async def test_tool_rigid_body_missing_bullet(call_tool, fake_maya, monkeypatch):
    monkeypatch.delitem(sys.modules, "maya.app.mayabullet.RigidBody", raising=False)
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False
    fake_maya.responses["loadPlugin"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not installed"))
    text = await call_tool("maya_create_rigid_body", {"params": {"nodes": ["crate1"]}})
    assert text.startswith("Error") and "bullet" in text and "Plug-in Manager" in text


async def test_tool_rejects_unknown_param(call_tool):
    text = await call_tool("maya_add_field", {"params": {"type": "gravity", "power": 3}})
    assert text.startswith("Error") and "power" in text


async def test_tool_explosion_preset(call_tool, fake_maya):
    _preset_scene(fake_maya)
    data = parse(await call_tool("maya_fx_explosion_preset", {"params": {"name": "kaboom", "scale": 0.5}}))
    assert data["preset"] == "explosion" and data["group"] == "kaboom_grp"
    assert "create3DFluid 8 8 8 5 5 5" in mel.evaluated
