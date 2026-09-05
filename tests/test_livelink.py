"""Unit + integration tests for the livelink domain (event stream + pull side)."""
from __future__ import annotations

import json
import socket

import pytest
from tests.conftest import _free_port, parse

from automaya_bridge import events
from automaya_bridge.handlers import livelink
from automaya_bridge.handlers._util import BridgeError


@pytest.fixture(autouse=True)
def _clean_bus():
    """Every test starts with no broadcaster and a fresh watch list."""
    bus = events.BUS
    if bus.broadcaster is not None:
        bus.broadcaster.stop()
    bus.broadcaster = None
    bus.watch([])
    bus.transform_only = False
    yield
    if bus.broadcaster is not None:
        bus.broadcaster.stop()
    bus.broadcaster = None
    bus.watch([])
    bus.transform_only = False


def _readline(sock: socket.socket) -> dict:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.decode("utf-8"))


def _scene(fake_maya):
    """cam1 (camera), cube (mesh, a quad + a triangle), grp (empty)."""
    shapes = {"|cam1": ["|cam1|cam1Shape"], "|cube": ["|cube|cubeShape"], "|key": ["|key|keyShape"]}
    types = {"|cam1|cam1Shape": "camera", "|cube|cubeShape": "mesh", "|key|keyShape": "pointLight"}

    def ls(*args, **kw):
        if kw.get("selection"):
            return ["|cube"]
        if kw.get("type") == "transform":
            if args:
                return [n for n in ("|cam1", "|cube", "|grp", "|key") if n.startswith(args[0])]
            return ["|cam1", "|cube", "|grp", "|key"]
        if kw.get("type") == "joint":
            return []
        if args and kw.get("long"):
            a = args[0] if isinstance(args[0], str) else args[0][0]
            return [a if a.startswith("|") else "|" + a]
        return []

    def list_relatives(node, **kw):
        if kw.get("shapes"):
            found = shapes.get(node, [])
            if kw.get("type"):
                found = [s for s in found if types.get(s) == kw["type"]]
            return found
        if kw.get("parent"):
            p = node.rsplit("|", 1)[0]
            return [p] if p else []
        return []

    def get_attr(plug, **kw):
        attr = plug.split(".")[-1]
        defaults = {"translate": [(1.0, 2.0, 3.0)], "rotate": [(0.0, 90.0, 0.0)], "scale": [(1.0, 1.0, 1.0)], "visibility": True, "rotateOrder": 2,
                    "focalLength": 50.0, "horizontalFilmAperture": 1.417, "verticalFilmAperture": 0.945, "nearClipPlane": 1.0, "farClipPlane": 5000.0,
                    "orthographic": False, "intensity": 2.0, "color": [(1.0, 0.5, 0.25)]}
        return defaults.get(attr)

    def xform(node, **kw):
        if kw.get("matrix"):
            return [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 2, 3, 1]
        if kw.get("translation"):
            # 5 verts of a quad + triangle sharing an edge: square in xz plane plus a peak
            return [0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 2, 1, 0.5]
        return None

    fake_maya.responses["ls"] = ls
    fake_maya.responses["listRelatives"] = list_relatives
    fake_maya.responses["nodeType"] = lambda n, **kw: types.get(n, "transform")
    fake_maya.responses["objectType"] = lambda n, **kw: (types.get(n, "transform") == kw["isType"]) if kw.get("isType") else types.get(n, "transform")
    fake_maya.responses["getAttr"] = get_attr
    fake_maya.responses["xform"] = xform
    fake_maya.responses["polyEvaluate"] = lambda s, **kw: {"face": 2, "vertex": 5, "triangle": 3}[next(iter(kw))]
    fake_maya.responses["polyInfo"] = lambda s, **kw: ["FACE      0:      0      1      2      3", "FACE      1:      1      4      2"] if kw.get("faceToVertex") else []
    fake_maya.responses["currentTime"] = 7.0
    fake_maya.responses["currentUnit"] = lambda **kw: "film" if kw.get("time") else "cm"
    fake_maya.existing.update({"|cam1", "|cube", "|grp", "|key", "cube", "cam1", "grp", "key"})


# unit: stream control ----------------------------------------------------------
def test_start_status_stop_stream(fake_maya):
    port = _free_port()
    out = livelink.start_stream(port=port, transform_only=True)
    assert out["active"] is True and out["port"] == port and out["subscribers"] == 0 and out["transform_only"] is True
    assert out["callbacks"] is False and "note" in out  # no OpenMaya in tests
    assert events.BUS.broadcaster is not None and events.BUS.broadcaster.running
    st = livelink.status()
    assert st["active"] and st["events_sent"] == 0 and st["openmaya"] is False and isinstance(st["events_per_sec"], float)
    out = livelink.stop_stream()
    assert out["active"] is False and not events.BUS.broadcaster.running


def test_start_stream_bad_port_and_restart_on_new_port(fake_maya):
    with pytest.raises(BridgeError, match="port must be"):
        livelink.start_stream(port=80)
    p1, p2 = _free_port(), _free_port()
    livelink.start_stream(port=p1)
    first = events.BUS.broadcaster
    livelink.start_stream(port=p2)
    assert not first.running and events.BUS.broadcaster.port == p2


def test_start_stream_port_in_use(fake_maya):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(BridgeError, match="cannot bind event port"):
            livelink.start_stream(port=port)
    finally:
        blocker.close()


def test_subscribe_nodes(fake_maya):
    _scene(fake_maya)
    out = livelink.subscribe_nodes(["cube"], transform_only=True)
    assert out["watched"] == ["|cube"] and out["transform_only"] is True
    assert livelink.subscribe_nodes([])["watched"] == []
    fake_maya.existing.clear()
    fake_maya.existing.add("x")
    with pytest.raises(BridgeError, match="not found"):
        livelink.subscribe_nodes(["ghost"])


def test_marker_reaches_raw_subscriber(fake_maya):
    port = _free_port()
    livelink.start_stream(port=port)
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        hello = _readline(client)
        assert hello["kind"] == "hello" and hello["protocol"] == 1 and hello["event_port"] == port and hello["unit"] == "cm"
        for _ in range(50):
            if events.BUS.broadcaster.subscriber_count():
                break
        out = livelink.emit_marker("sync_a", {"take": 3})
        assert out["delivered_to"] == 1 and out["event"]["kind"] == "marker"
        line = _readline(client)
        assert line["kind"] == "marker" and line["name"] == "sync_a" and line["data"] == {"take": 3} and line["seq"] == out["event"]["seq"]
        assert livelink.status()["events_sent"] == 1 and livelink.status()["subscribers"] == 1
    finally:
        client.close()


def test_emit_marker_requires_name(fake_maya):
    with pytest.raises(BridgeError, match="name"):
        livelink.emit_marker("")


# unit: pull side ----------------------------------------------------------------
def test_snapshot_scene_graph(fake_maya):
    _scene(fake_maya)
    out = livelink.snapshot_scene_graph(include_meshes=True)
    by = {n["path"]: n for n in out["nodes"]}
    assert set(by) == {"|cam1", "|cube", "|grp", "|key"} and out["count"] == 4
    assert by["|cube"]["type"] == "mesh" and by["|cube"]["mesh"]["faces"] == 2 and by["|cube"]["world_matrix"][12:15] == [1.0, 2.0, 3.0]
    assert by["|cube"]["rotate_order"] == "zxy" and by["|cube"]["translate"] == [1.0, 2.0, 3.0] and by["|cube"]["parent"] is None
    assert by["|cam1"]["camera"]["focal_length"] == 50.0 and by["|cam1"]["camera"]["far_clip"] == 5000.0
    assert by["|key"]["light"] == {"light_type": "pointLight", "intensity": 2.0, "color": [1.0, 0.5, 0.25], "exposure": 0.0}
    assert by["|grp"]["type"] == "group" and "shape" not in by["|grp"]
    assert out["unit"] == "cm" and out["up_axis"] == "y" and out["fps"] == 24.0 and out["frame"] == 7.0
    filtered = livelink.snapshot_scene_graph(include_cameras=False, include_lights=False)
    assert {n["path"] for n in filtered["nodes"]} == {"|cube", "|grp"}
    rooted = livelink.snapshot_scene_graph(root="|cube")
    assert [n["path"] for n in rooted["nodes"]] == ["|cube"] and "mesh" not in rooted["nodes"][0]


def test_get_transforms_uses_selection(fake_maya):
    _scene(fake_maya)
    out = livelink.get_transforms()
    assert out["transforms"][0]["path"] == "|cube" and out["transforms"][0]["world_matrix"][0] == 0.0 and out["frame"] == 7.0


def test_mesh_buffers_cmds_fallback(fake_maya):
    _scene(fake_maya)
    out = livelink.get_mesh_buffers("cube")
    assert out["backend"] == "cmds" and out["shape"] == "|cube|cubeShape"
    assert out["counts"] == {"vertices": 5, "faces": 2, "triangles": 3, "uvs": 0, "normals": 5}
    assert out["face_vertex_counts"] == [4, 3] and out["face_vertex_indices"] == [0, 1, 2, 3, 1, 4, 2]
    assert out["indices"] == [0, 1, 2, 0, 2, 3, 1, 4, 2]
    assert out["bbox"]["min"] == [0, 0, 0] and out["bbox"]["max"] == [2, 1, 1]
    # the flat quad's untouched corner normal points straight along y (sign depends on winding)
    n0 = out["normals"][0:3]
    assert abs(abs(n0[1]) - 1.0) < 1e-6 and n0[0] == 0.0
    assert out["uvs"] == [] and any("cmds fallback" in n for n in out["notes"])
    (_, kw), = fake_maya.calls_to("xform")
    assert kw["worldSpace"] is True and kw["translation"] is True
    no_tri = livelink.get_mesh_buffers("cube", triangulate=False, include_normals=False)
    assert no_tri["indices"] == [] and no_tri["counts"]["triangles"] == 3 and no_tri["normals"] == []


def test_mesh_buffers_errors(fake_maya):
    _scene(fake_maya)
    with pytest.raises(BridgeError, match="no mesh shape"):
        livelink.get_mesh_buffers("grp")
    fake_maya.responses["polyEvaluate"] = lambda s, **kw: 5_000_000
    with pytest.raises(BridgeError, match="export_usd_live"):
        livelink.get_mesh_buffers("cube")


def test_mesh_buffers_openmaya_path_isolated():
    """The OM path is a pure function of the OM module; drive it with a fake."""

    class Vec:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class FakeFn:
        def __init__(self, dag):
            self.dag = dag

        def getPoints(self, space):
            return [Vec(0, 0, 0), Vec(1, 0, 0), Vec(1, 0, 1), Vec(0, 0, 1)]

        def getVertices(self):
            return [4], [0, 1, 2, 3]

        def getVertexNormals(self, angle_weighted, space):
            return [Vec(0, 1, 0)] * 4

        def getUVs(self):
            return [0.0, 1.0, 1.0, 0.0], [0.0, 0.0, 1.0, 1.0]

        def getAssignedUVs(self):
            return [4], [0, 1, 2, 3]

    class FakeSel:
        def add(self, name):
            self.name = name

        def getDagPath(self, i):
            return "dag:" + self.name

    class FakeSpace:
        kWorld = "world"
        kObject = "object"

    class FakeOM:
        MSelectionList = FakeSel
        MFnMesh = FakeFn
        MSpace = FakeSpace

    data = livelink._mesh_buffers_om(FakeOM, "cubeShape", True, True, True)
    assert data["positions"] == [0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1] and data["face_vertex_counts"] == [4]
    assert data["normals"][:3] == [0, 1, 0] and data["uvs"] == [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0] and data["uv_ids"] == [0, 1, 2, 3]
    tri, tri_uv = livelink._triangulate(data["face_vertex_counts"], data["face_vertex_indices"], data["uv_ids"])
    assert tri == [0, 1, 2, 0, 2, 3] and tri_uv == [0, 1, 2, 0, 2, 3]


def test_export_usd_live_emits_event(fake_maya, tmp_path):
    _scene(fake_maya)
    fake_maya.responses["playbackOptions"] = lambda **kw: 1.0 if kw.get("minTime") else 24.0
    path = str(tmp_path / "shot.usda")
    seq_before = events.BUS.last_seq
    out = livelink.export_usd_live(path=path, nodes=["cube"], animation=True)
    assert out["path"] == path and out["nodes"] == ["|cube"] and out["start"] == 1.0 and out["end"] == 24.0 and out["seq"] == seq_before + 1
    (args, kw), = [(a, k) for a, k in fake_maya.calls_to("file") if k.get("exportSelected")]
    assert args[0] == path and kw["type"] == "USD Export" and "animation=1" in kw["options"] and "startTime=1.0;endTime=24.0" in kw["options"]
    ev = events.BUS.drain(since_seq=seq_before)["events"][-1]
    assert ev["kind"] == "usd_exported" and ev["path"] == path
    with pytest.raises(BridgeError, match="usd"):
        livelink.export_usd_live(path=str(tmp_path / "x.fbx"), nodes=["cube"])


def test_set_frame_and_play_range(fake_maya):
    _scene(fake_maya)
    fake_maya.responses["playbackOptions"] = lambda **kw: 1.0 if kw.get("minTime") else (24.0 if kw.get("maxTime") else None)
    out = livelink.set_frame(15)
    assert out == {"frame": 15.0, "fps": 24.0}
    (args, kw), = fake_maya.calls_to("currentTime")
    assert args == (15.0,) and kw["edit"] is True
    assert events.BUS.drain(since_seq=0)["events"][-1]["kind"] == "time_changed"
    out = livelink.play_range(10, 20, play=True)
    assert out["playing"] is True and out["start"] == 10.0
    assert fake_maya.calls_to("play")[0][1]["state"] is True
    with pytest.raises(BridgeError, match="end must be"):
        livelink.play_range(20, 10)


def test_protocol_spec_covers_every_bus_event_kind():
    spec = livelink.protocol_spec()
    for kind in ("hello", "attr_changed", "node_added", "node_removed", "time_changed", "selection_changed", "marker", "usd_exported"):
        assert kind in spec["events"]
    assert "coordinate_conversion" in spec and spec["version"] == 1


# integration --------------------------------------------------------------------
async def test_tool_stream_roundtrip_with_raw_socket(call_tool, fake_maya):
    port = _free_port()
    data = parse(await call_tool("maya_livelink_start", {"params": {"port": port}}))
    assert data["active"] is True and data["port"] == port
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        assert _readline(client)["kind"] == "hello"
        for _ in range(50):
            if events.BUS.broadcaster.subscriber_count():
                break
        out = parse(await call_tool("maya_livelink_marker", {"params": {"name": "shot_start", "data": {"shot": "010"}}}))
        assert out["delivered_to"] == 1
        line = _readline(client)
        assert line["kind"] == "marker" and line["name"] == "shot_start" and line["data"]["shot"] == "010"
        st = parse(await call_tool("maya_livelink_status"))
        assert st["subscribers"] == 1 and st["events_sent"] == 1
    finally:
        client.close()
    assert parse(await call_tool("maya_livelink_stop"))["active"] is False


async def test_tool_snapshot_and_mesh_buffers(call_tool, fake_maya):
    _scene(fake_maya)
    data = parse(await call_tool("maya_livelink_snapshot", {"params": {"include_meshes": True}}))
    assert data["count"] == 4
    data = parse(await call_tool("maya_livelink_mesh_buffers", {"params": {"node": "cube", "max_chars": 100000}}))
    assert data["counts"]["triangles"] == 3 and data["backend"] == "cmds"
    text = await call_tool("maya_livelink_mesh_buffers", {"params": {"node": "grp"}})
    assert text.startswith("Error") and "no mesh shape" in text


async def test_tool_protocol_and_validation(call_tool, fake_maya):
    data = parse(await call_tool("maya_livelink_protocol"))
    assert "events" in data
    text = await call_tool("maya_livelink_start", {"params": {"port": 10}})
    assert text.startswith("Error")
