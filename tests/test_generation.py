"""Generation: plugin handler units + tool integration with mocked providers."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from tests.conftest import parse

from automaya_bridge.handlers import assets as assets_handler
from automaya_bridge.handlers import generation as gen_handler
from automaya_bridge.registry import invoke
from automaya_mcp.providers import registry
from automaya_mcp.tools import generation as gen_tools


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for var in ("TRIPO_API_KEY", "MESHY_API_KEY", "RODIN_API_KEY", "FAL_KEY", "HUNYUAN_SECRET_ID", "HUNYUAN_SECRET_KEY", "HUNYUAN_LOCAL_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUTOMAYA_DOWNLOAD_DIR", str(tmp_path / "dl"))
    registry.reset()
    gen_tools.JOBS.clear()
    gen_tools.PROMPTS.clear()
    orig = gen_tools.wait_for
    monkeypatch.setattr(gen_tools, "wait_for", lambda p, j, m, c: orig(p, j, m, c, interval=0.01))
    yield
    registry.reset()


@pytest.fixture()
def obj_file(tmp_path):
    p = tmp_path / "chair.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    return str(p)


def arm_import(fake_maya, node="|genChair"):
    """Make cmds.ls(assemblies=True) report a new top node after cmds.file."""
    state = {"n": 0}

    def ls(*args, **kwargs):
        if kwargs.get("assemblies"):
            state["n"] += 1
            return [] if state["n"] == 1 else [node]
        if args and kwargs.get("long"):
            first = args[0] if isinstance(args[0], str) else args[0][0]
            return [first]
        return []

    fake_maya.responses["ls"] = ls


# handler units ----------------------------------------------------------------
def test_gen_import_result_tags_node(fake_maya, obj_file):
    arm_import(fake_maya)
    fake_maya.responses["attributeQuery"] = False
    out = gen_handler.import_result(path=obj_file, provider="tripo", job_id="t1", prompt="a chair", scale=100.0, freeze=True)
    assert out["top_nodes"] == ["|genChair"] and out["provider"] == "tripo"
    file_call = fake_maya.calls_to("file")[0]
    assert file_call[0][0] == obj_file and file_call[1]["type"] == "OBJ"
    added = [k["longName"] for _, k in fake_maya.calls_to("addAttr")]
    assert added == ["automaya_provider", "automaya_job", "automaya_prompt"]
    set_values = [a[1] for a, _ in fake_maya.calls_to("setAttr")]
    assert "tripo" in set_values and "t1" in set_values
    assert fake_maya.calls_to("scale")[0][0][:3] == (100.0, 100.0, 100.0)
    assert fake_maya.calls_to("makeIdentity")
    assert assets_handler.IMPORT_LOG[-1]["kind"] == "generated" and assets_handler.IMPORT_LOG[-1]["job_id"] == "t1"


def test_gen_import_result_missing_file(fake_maya):
    resp = invoke("gen.import_result", {"path": "/nope/x.fbx"})
    assert resp["status"] == "error" and "file not found" in resp["message"]


# tool integration ----------------------------------------------------------------
async def test_list_providers_tool(call_tool):
    data = parse(await call_tool("maya_gen3d_list_providers"))
    assert [p["name"] for p in data["providers"]] == ["tripo", "meshy", "rodin", "hunyuan", "higgsfield"]
    assert "TRIPO_API_KEY" in data["providers"][0]["configure"]


async def test_text_to_3d_unconfigured_names_env(call_tool):
    text = await call_tool("maya_gen3d_text_to_3d", {"params": {"provider": "meshy", "prompt": "a lamp"}})
    assert text.startswith("Error") and "MESHY_API_KEY" in text
    text = await call_tool("maya_gen3d_text_to_3d", {"params": {"provider": "bogus", "prompt": "a lamp"}})
    assert "unknown provider" in text


async def test_text_to_3d_wait_poll_and_import(call_tool, fake_maya, monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    arm_import(fake_maya, "|tripo_chair")
    with respx.mock(base_url="https://api.tripo3d.ai/v2/openapi") as mock:
        mock.post("/task").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "t1"}}))
        mock.get("/task/t1").mock(side_effect=[
            httpx.Response(200, json={"code": 0, "data": {"status": "running", "progress": 10, "output": {}}}),
            httpx.Response(200, json={"code": 0, "data": {"status": "success", "progress": 100, "output": {"pbr_model": "https://cdn.test/m.obj"}}}),
            httpx.Response(200, json={"code": 0, "data": {"status": "success", "progress": 100, "output": {"pbr_model": "https://cdn.test/m.obj"}}}),
        ])
        mock.get("https://cdn.test/m.obj").mock(return_value=httpx.Response(200, content=b"v 0 0 0\n"))
        data = parse(await call_tool("maya_gen3d_text_to_3d", {"params": {"provider": "tripo", "prompt": "a chair", "wait": True, "max_wait": 5, "options": {"face_limit": 4000}}}))
        assert data["status"] == "succeeded" and data["formats"] == ["obj"] and "maya_gen3d_import" in data["next"]
        assert ("tripo", "t1") in gen_tools.JOBS
        data = parse(await call_tool("maya_gen3d_import", {"params": {"provider": "tripo", "job_id": "t1", "format": "obj", "name": "chair", "scale": 100}}))
    assert data["top_nodes"] == ["chair"] and data["format"] == "obj"
    assert data["downloaded"].endswith(".obj") and data["provider"] == "tripo"
    assert fake_maya.calls_to("rename")[0][0] == ("|tripo_chair", "chair")


async def test_import_converts_missing_format_on_tripo(call_tool, fake_maya, monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    arm_import(fake_maya, "|conv")
    with respx.mock(base_url="https://api.tripo3d.ai/v2/openapi") as mock:
        mock.get("/task/t1").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"status": "success", "output": {"pbr_model": "https://cdn.test/m.glb"}}}))
        post = mock.post("/task").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "c1"}}))
        mock.get("/task/c1").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"status": "success", "type": "convert_model", "input": {"format": "FBX"}, "output": {"model": "https://cdn.test/m.fbx"}}}))
        mock.get("https://cdn.test/m.fbx").mock(return_value=httpx.Response(200, content=b"fbx"))
        data = parse(await call_tool("maya_gen3d_import", {"params": {"provider": "tripo", "job_id": "t1"}}))
        assert json.loads(post.calls[0].request.content)["format"] == "FBX"
    assert data["note"].startswith("converted via job c1") and data["format"] == "fbx"
    assert fake_maya.calls_to("file")[0][1]["type"] == "FBX"


async def test_import_falls_back_without_convert(call_tool, fake_maya, monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "mk")
    arm_import(fake_maya, "|meshy1")
    with respx.mock(base_url="https://api.meshy.ai") as mock:
        mock.get("/openapi/v1/image-to-3d/i1").mock(return_value=httpx.Response(200, json={"status": "SUCCEEDED", "model_urls": {"obj": "https://m.test/a.obj"}}))
        mock.get("/openapi/v2/text-to-3d/i1").mock(return_value=httpx.Response(404, json={}))
        mock.get("https://m.test/a.obj").mock(return_value=httpx.Response(200, content=b"v"))
        data = parse(await call_tool("maya_gen3d_import", {"params": {"provider": "meshy", "job_id": "i1", "format": "fbx"}}))
    assert data["format"] == "obj" and "importing obj instead" in data["note"]


async def test_import_not_done_and_poll_tool(call_tool, monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "mk")
    with respx.mock(base_url="https://api.meshy.ai") as mock:
        mock.get("/openapi/v2/text-to-3d/p1").mock(return_value=httpx.Response(200, json={"status": "IN_PROGRESS", "progress": 55}))
        data = parse(await call_tool("maya_gen3d_poll", {"params": {"provider": "meshy", "job_id": "p1"}}))
        assert data["status"] == "running" and data["progress"] == 55 and "maya_gen3d_poll" in data["next"]
        data = parse(await call_tool("maya_gen3d_import", {"params": {"provider": "meshy", "job_id": "p1"}}))
        assert "cannot import yet" in data["error"]


async def test_image_to_3d_missing_local_file(call_tool, monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    text = await call_tool("maya_gen3d_image_to_3d", {"params": {"provider": "tripo", "image": "/no/such.png"}})
    assert text.startswith("Error") and "not found" in text


async def test_rig_retexture_remesh_convert_tools(call_tool, monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "mk")
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    with respx.mock() as mock:
        mock.post("https://api.meshy.ai/openapi/v1/rigging").mock(return_value=httpx.Response(200, json={"result": "rg"}))
        mock.post("https://api.meshy.ai/openapi/v1/retexture").mock(return_value=httpx.Response(200, json={"result": "rt"}))
        mock.post("https://api.meshy.ai/openapi/v1/remesh").mock(return_value=httpx.Response(200, json={"result": "rm"}))
        mock.post("https://api.tripo3d.ai/v2/openapi/task").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "cv"}}))
        assert parse(await call_tool("maya_gen3d_rig", {"params": {"provider": "meshy", "job_id": "x", "height_meters": 1.7}}))["job_id"] == "rg"
        assert parse(await call_tool("maya_gen3d_retexture", {"params": {"provider": "meshy", "job_id": "x", "prompt": "bronze"}}))["job_id"] == "rt"
        assert parse(await call_tool("maya_gen3d_remesh", {"params": {"job_id": "x", "target_polycount": 3000}}))["job_id"] == "rm"
        assert parse(await call_tool("maya_gen3d_convert", {"params": {"job_id": "x", "format": "obj"}}))["job_id"] == "cv"
    text = await call_tool("maya_gen3d_remesh", {"params": {"provider": "tripo", "job_id": "x"}})
    assert "does not support remeshing" in text
