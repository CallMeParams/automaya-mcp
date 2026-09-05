"""Provider contracts mocked with respx: Tripo, Meshy, Rodin, Hunyuan, Higgsfield, Replicate."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from automaya_mcp.providers import ProviderError, get_provider, list_providers, registry
from automaya_mcp.providers import meshy as meshy_mod
from automaya_mcp.providers.base import raise_for_status
from automaya_mcp.providers.higgsfield import HiggsfieldProvider
from automaya_mcp.providers.hunyuan import HunyuanProvider, tc3_headers
from automaya_mcp.providers.meshy import MeshyProvider
from automaya_mcp.providers.replicate import ReplicateProvider, outputs_from, split_model
from automaya_mcp.providers.rodin import RodinProvider
from automaya_mcp.providers.tripo import TripoProvider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("TRIPO_API_KEY", "MESHY_API_KEY", "RODIN_API_KEY", "FAL_KEY", "RODIN_MODE", "HUNYUAN_SECRET_ID", "HUNYUAN_SECRET_KEY", "HUNYUAN_LOCAL_URL", "HUNYUAN_MODE", "HIGGSFIELD_API_KEY", "HIGGSFIELD_API_SECRET", "HIGGSFIELD_3D_ENDPOINT", "HUNYUAN_REGION", "HUNYUAN_WORLD_SUBMIT", "HUNYUAN_WORLD_QUERY", "REPLICATE_API_TOKEN", "REPLICATE_3D_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUTOMAYA_DOWNLOAD_DIR", str(tmp_path / "dl"))
    registry.reset()
    meshy_mod._KIND.clear()
    meshy_mod._REFINE.clear()
    yield
    registry.reset()


@pytest.fixture()
def png(tmp_path):
    p = tmp_path / "ref.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return str(p)


# registry + errors --------------------------------------------------------
def test_registry_lists_all_unconfigured():
    names = [p["name"] for p in list_providers()]
    assert names == ["tripo", "meshy", "rodin", "hunyuan", "higgsfield", "replicate"]
    assert all(p["configured"] is False for p in list_providers())
    with pytest.raises(ProviderError) as exc:
        get_provider("nope")
    assert "unknown provider" in str(exc.value)
    assert get_provider("hyper3d").name == "rodin"


async def test_missing_key_message_names_env_var():
    with pytest.raises(ProviderError) as exc:
        await TripoProvider().submit_text("a chair")
    assert "TRIPO_API_KEY" in str(exc.value)
    with pytest.raises(ProviderError) as exc:
        await MeshyProvider().poll("x")
    assert "MESHY_API_KEY" in str(exc.value)
    with pytest.raises(ProviderError) as exc:
        await RodinProvider().submit_text("x")
    assert "RODIN_API_KEY" in str(exc.value) and "FAL_KEY" in str(exc.value)
    with pytest.raises(ProviderError) as exc:
        await HunyuanProvider().submit_text("x")
    assert "HUNYUAN_SECRET_ID" in str(exc.value) and "HUNYUAN_LOCAL_URL" in str(exc.value)


def test_raise_for_status_messages():
    req = httpx.Request("GET", "https://x.test/task/1")
    for code, needle in ((401, "TRIPO_API_KEY"), (402, "credits"), (429, "rate limit"), (404, "expired"), (500, "HTTP 500")):
        with pytest.raises(ProviderError) as exc:
            raise_for_status(httpx.Response(code, request=req, text="boom"), "Tripo", "TRIPO_API_KEY")
        assert needle in str(exc.value)
        assert exc.value.status == code


# Tripo ----------------------------------------------------------------------
async def test_tripo_text_poll_download(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    p = get_provider("tripo")
    assert p.configured()
    with respx.mock(base_url="https://api.tripo3d.ai/v2/openapi") as mock:
        post = mock.post("/task").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "t1"}}))
        mock.get("/task/t1").mock(side_effect=[
            httpx.Response(200, json={"code": 0, "data": {"task_id": "t1", "status": "running", "progress": 40, "output": {}}}),
            httpx.Response(200, json={"code": 0, "data": {"task_id": "t1", "status": "success", "progress": 100, "output": {"pbr_model": "https://cdn.test/m.glb?sig=1", "rendered_image": "https://cdn.test/i.png"}}}),
        ])
        job = await p.submit_text("a wooden chair", face_limit=5000, pbr=True, quality="high", negative_prompt="blurry")
        body = json.loads(post.calls[0].request.content)
        assert body["type"] == "text_to_model" and body["model_version"] == "P1-20260311"
        assert body["face_limit"] == 5000 and body["texture_quality"] == "detailed" and body["negative_prompt"] == "blurry"
        assert post.calls[0].request.headers["Authorization"] == "Bearer tk"
        assert job.job_id == "t1" and job.status == "queued"
        job = await p.poll("t1")
        assert job.status == "running" and job.progress == 40
        job = await p.poll("t1")
        assert job.status == "succeeded" and job.outputs["glb"].startswith("https://cdn.test/m.glb")
        assert job.thumbnail_url == "https://cdn.test/i.png"
    with respx.mock() as mock:
        mock.get("https://cdn.test/m.glb").mock(return_value=httpx.Response(200, content=b"glTF-bytes"))
        path = await p.download(job, "glb")
    assert path.endswith(".glb") and open(path, "rb").read() == b"glTF-bytes"
    with pytest.raises(ProviderError) as exc:
        await p.download(job, "fbx")
    assert "no 'fbx' output" in str(exc.value)


async def test_tripo_image_upload_convert_rig_retexture(monkeypatch, png):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    p = get_provider("tripo")
    with respx.mock(base_url="https://api.tripo3d.ai/v2/openapi") as mock:
        up = mock.post("/upload").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"image_token": "img123"}}))
        post = mock.post("/task").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "t2"}}))
        job = await p.submit_image(png)
        assert up.called and b"filename=\"ref.png\"" in up.calls[0].request.content
        body = json.loads(post.calls[0].request.content)
        assert body["type"] == "image_to_model" and body["file"] == {"type": "png", "file_token": "img123"}
        await p.submit_image("https://img.test/a.jpg")
        assert json.loads(post.calls[1].request.content)["file"] == {"type": "jpg", "url": "https://img.test/a.jpg"}
        conv = await p.convert("t2", "fbx", quad=True, face_limit=8000)
        body = json.loads(post.calls[2].request.content)
        assert body == {"type": "convert_model", "format": "FBX", "original_model_task_id": "t2", "quad": True, "face_limit": 8000}
        assert conv.raw["format"] == "fbx"
        await p.rig("t2")
        body = json.loads(post.calls[3].request.content)
        assert body["type"] == "animate_rig" and body["spec"] == "mixamo" and body["model_version"] == "v2.5-20260210"
        await p.retexture("t2", prompt="rusty metal")
        body = json.loads(post.calls[4].request.content)
        assert body["type"] == "texture_model" and body["texture_prompt"] == {"text": "rusty metal"} and body["model_version"] == "v3.0-20250812"
        assert job.job_id == "t2"
        mock.get("/task/t9").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "t9", "status": "success", "type": "convert_model", "input": {"format": "FBX"}, "output": {"model": "https://cdn.test/out"}}}))
        job = await p.poll("t9")
        assert "fbx" in job.outputs


async def test_tripo_envelope_error_and_http_error(monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", "tk")
    p = get_provider("tripo")
    with respx.mock(base_url="https://api.tripo3d.ai/v2/openapi") as mock:
        mock.post("/task").mock(return_value=httpx.Response(200, json={"code": 2010, "message": "insufficient balance"}))
        with pytest.raises(ProviderError) as exc:
            await p.submit_text("x")
        assert "credits" in str(exc.value)
        mock.get("/task/bad").mock(return_value=httpx.Response(401, json={}))
        with pytest.raises(ProviderError) as exc:
            await p.poll("bad")
        assert "TRIPO_API_KEY" in str(exc.value)
        mock.get("/task/f").mock(return_value=httpx.Response(200, json={"code": 0, "data": {"status": "banned", "output": {}}}))
        job = await p.poll("f")
        assert job.status == "failed"


# Meshy ----------------------------------------------------------------------
async def test_meshy_preview_then_refine(monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "mk")
    p = get_provider("meshy")
    with respx.mock(base_url="https://api.meshy.ai") as mock:
        post = mock.post("/openapi/v2/text-to-3d").mock(side_effect=[
            httpx.Response(200, json={"result": "prev1"}),
            httpx.Response(200, json={"result": "ref1"}),
        ])
        mock.get("/openapi/v2/text-to-3d/prev1").mock(side_effect=[
            httpx.Response(200, json={"id": "prev1", "status": "IN_PROGRESS", "progress": 30}),
            httpx.Response(200, json={"id": "prev1", "status": "SUCCEEDED", "progress": 100, "model_urls": {"glb": "https://m.test/p.glb"}}),
        ])
        mock.get("/openapi/v2/text-to-3d/ref1").mock(return_value=httpx.Response(200, json={
            "id": "ref1", "status": "SUCCEEDED", "progress": 100,
            "model_urls": {"glb": "https://m.test/r.glb", "fbx": "https://m.test/r.fbx", "obj": "", "usdz": "https://m.test/r.usdz"},
            "thumbnail_url": "https://m.test/t.png",
        }))
        job = await p.submit_text("a robot", face_limit=30000, quality="high")
        body = json.loads(post.calls[0].request.content)
        assert body["mode"] == "preview" and body["ai_model"] == "meshy-7" and body["target_polycount"] == 30000
        assert body["target_formats"] == ["glb", "fbx", "obj", "usdz"]
        assert post.calls[0].request.headers["Authorization"] == "Bearer mk"
        assert job.job_id == "prev1"
        j = await p.poll("prev1")
        assert j.status == "running" and j.raw["stage"] == "preview"
        j = await p.poll("prev1")
        assert j.status == "running" and j.raw["refine_task_id"] == "ref1"
        refine = json.loads(post.calls[1].request.content)
        assert refine == {"mode": "refine", "preview_task_id": "prev1", "enable_pbr": True, "texture_resolution": "2048", "ai_model": "meshy-7", "target_formats": ["glb", "fbx", "obj", "usdz"]}
        j = await p.poll("prev1")
        assert j.status == "succeeded" and j.job_id == "prev1"
        assert sorted(j.outputs) == ["fbx", "glb", "usdz"] and j.thumbnail_url == "https://m.test/t.png"


async def test_meshy_no_auto_refine_image_and_post_ops(monkeypatch, png):
    monkeypatch.setenv("MESHY_API_KEY", "mk")
    p = get_provider("meshy")
    with respx.mock(base_url="https://api.meshy.ai") as mock:
        mock.post("/openapi/v2/text-to-3d").mock(return_value=httpx.Response(200, json={"result": "p2"}))
        mock.get("/openapi/v2/text-to-3d/p2").mock(return_value=httpx.Response(200, json={"status": "SUCCEEDED", "model_urls": {"glb": "u"}}))
        job = await p.submit_text("x", auto_refine=False)
        j = await p.poll(job.job_id)
        assert j.status == "succeeded" and j.raw["stage"] == "preview"

        img = mock.post("/openapi/v1/image-to-3d").mock(return_value=httpx.Response(200, json={"result": "i1"}))
        await p.submit_image(png, pbr=False)
        body = json.loads(img.calls[0].request.content)
        assert body["image_url"].startswith("data:image/png;base64,") and body["enable_pbr"] is False
        await p.submit_image("https://x.test/a.png")
        assert json.loads(img.calls[1].request.content)["image_url"] == "https://x.test/a.png"
        mock.get("/openapi/v1/image-to-3d/i1").mock(return_value=httpx.Response(200, json={"status": "PENDING", "progress": 0}))
        assert (await p.poll("i1")).status == "queued"

        rt = mock.post("/openapi/v1/retexture").mock(return_value=httpx.Response(200, json={"result": "rt1"}))
        await p.retexture("i1", prompt="gold")
        assert json.loads(rt.calls[0].request.content)["text_style_prompt"] == "gold"
        assert json.loads(rt.calls[0].request.content)["input_task_id"] == "i1"
        rm = mock.post("/openapi/v1/remesh").mock(return_value=httpx.Response(200, json={"result": "rm1"}))
        await p.remesh("i1", target_polycount=5000, topology="quad")
        assert json.loads(rm.calls[0].request.content)["target_polycount"] == 5000
        rg = mock.post("/openapi/v1/rigging").mock(return_value=httpx.Response(200, json={"result": "rg1"}))
        await p.rig("i1", height_meters=1.8)
        assert json.loads(rg.calls[0].request.content) == {"input_task_id": "i1", "height_meters": 1.8}
        mock.get("/openapi/v1/rigging/rg1").mock(return_value=httpx.Response(200, json={"status": "SUCCEEDED", "result": {"rigged_character_fbx_url": "https://m.test/rig.fbx", "rigged_character_glb_url": "https://m.test/rig.glb"}}))
        j = await p.poll("rg1")
        assert j.outputs["fbx"].endswith("rig.fbx") and j.outputs["glb"].endswith("rig.glb")
        an = mock.post("/openapi/v1/animations").mock(return_value=httpx.Response(200, json={"result": "an1"}))
        await p.animate("rg1", 3)
        assert json.loads(an.calls[0].request.content) == {"rig_task_id": "rg1", "action_id": 3}

        mock.get("/openapi/v2/text-to-3d/unknown").mock(return_value=httpx.Response(404, json={}))
        mock.get("/openapi/v1/image-to-3d/unknown").mock(return_value=httpx.Response(200, json={"status": "FAILED", "task_error": {"message": "bad input"}}))
        j = await p.poll("unknown")
        assert j.status == "failed" and "bad input" in j.message


# Rodin ------------------------------------------------------------------------
async def test_rodin_main_route(monkeypatch, png):
    monkeypatch.setenv("RODIN_API_KEY", "rk")
    p = get_provider("rodin")
    assert p.mode == "main"
    with respx.mock(base_url="https://api.hyper3d.com/api/v2") as mock:
        sub = mock.post("/rodin").mock(return_value=httpx.Response(200, json={"uuid": "u1", "jobs": {"uuids": ["j1"], "subscription_key": "sk1"}}))
        mock.post("/status").mock(side_effect=[
            httpx.Response(200, json={"jobs": [{"uuid": "j1", "status": "Generating"}]}),
            httpx.Response(200, json={"jobs": [{"uuid": "j1", "status": "Done"}]}),
        ])
        mock.post("/download").mock(return_value=httpx.Response(200, json={"list": [{"name": "model.fbx", "url": "https://r.test/m.fbx"}, {"name": "preview.png", "url": "https://r.test/p.png"}]}))
        job = await p.submit_image(png, prompt="a lamp", format="fbx", quad=True)
        req = sub.calls[0].request
        assert req.headers["Authorization"] == "Bearer rk"
        assert b'name="images"' in req.content and b'name="prompt"' in req.content and b"Gen-2.5-Medium" in req.content and b"Quad" in req.content
        assert job.job_id == "main:u1:sk1"
        j = await p.poll(job.job_id)
        assert j.status == "running"
        j = await p.poll(job.job_id)
        assert j.status == "succeeded" and j.outputs["fbx"] == "https://r.test/m.fbx" and j.thumbnail_url == "https://r.test/p.png"
        assert json.loads(mock.calls[-1].request.content) == {"task_uuid": "u1"}


async def test_rodin_fal_route(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "vibecoding")
    p = get_provider("rodin")
    assert p.mode == "fal"
    with respx.mock(base_url="https://queue.fal.run/fal-ai/hyper3d") as mock:
        sub = mock.post("/rodin").mock(return_value=httpx.Response(200, json={"request_id": "r1"}))
        mock.get("/requests/r1/status").mock(side_effect=[
            httpx.Response(200, json={"status": "IN_QUEUE"}),
            httpx.Response(200, json={"status": "COMPLETED"}),
        ])
        mock.get("/requests/r1").mock(return_value=httpx.Response(200, json={"model_mesh": {"url": "https://fal.test/out.glb"}}))
        job = await p.submit_text("a teapot")
        assert sub.calls[0].request.headers["Authorization"] == "Key vibecoding"
        body = json.loads(sub.calls[0].request.content)
        assert body["prompt"] == "a teapot" and body["tier"] == "Gen-2.5-Medium" and body["material"] == "PBR"
        assert job.job_id == "fal:r1"
        assert (await p.poll("fal:r1")).status == "queued"
        j = await p.poll("fal:r1")
        assert j.status == "succeeded" and j.outputs["glb"] == "https://fal.test/out.glb"


# Hunyuan ----------------------------------------------------------------------
def test_tc3_signature_is_deterministic():
    h = tc3_headers("AKID", "SECRET", "SubmitHunyuanTo3DProJob", {"Prompt": "cat"}, timestamp=1700000000)
    assert h["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKID/2023-11-14/ai3d/tc3_request, SignedHeaders=content-type;host, Signature=")
    assert h["X-TC-Action"] == "SubmitHunyuanTo3DProJob" and h["X-TC-Version"] == "2025-05-13" and h["X-TC-Region"] == "ap-guangzhou"
    assert h["Content-Type"] == "application/json; charset=utf-8" and h["Host"] == "ai3d.tencentcloudapi.com"
    assert h == tc3_headers("AKID", "SECRET", "SubmitHunyuanTo3DProJob", {"Prompt": "cat"}, timestamp=1700000000)
    assert h["Authorization"] != tc3_headers("AKID", "OTHER", "SubmitHunyuanTo3DProJob", {"Prompt": "cat"}, timestamp=1700000000)["Authorization"]


async def test_hunyuan_official(monkeypatch, png):
    monkeypatch.setenv("HUNYUAN_SECRET_ID", "id")
    monkeypatch.setenv("HUNYUAN_SECRET_KEY", "key")
    p = get_provider("hunyuan")
    assert p.mode == "official"
    with respx.mock(base_url="https://ai3d.tencentcloudapi.com") as mock:
        route = mock.post("/").mock(side_effect=[
            httpx.Response(200, json={"Response": {"JobId": "job-1", "RequestId": "r"}}),
            httpx.Response(200, json={"Response": {"Status": "RUN"}}),
            httpx.Response(200, json={"Response": {"Status": "DONE", "ResultFile3Ds": [{"Type": "OBJ", "Url": "https://t.test/a.obj", "PreviewImageUrl": "https://t.test/a.png"}]}}),
            httpx.Response(200, json={"Response": {"JobId": "job-2"}}),
            httpx.Response(200, json={"Response": {"Error": {"Code": "AuthFailure.SignatureFailure", "Message": "bad sig"}}}),
        ])
        job = await p.submit_text("a cat", pbr=True, face_limit=20000, format="obj")
        req = route.calls[0].request
        assert req.headers["Authorization"].startswith("TC3-HMAC-SHA256")
        assert req.headers["X-TC-Action"] == "SubmitHunyuanTo3DProJob"
        assert json.loads(req.content) == {"Prompt": "a cat", "GenerateType": "Normal", "Model": "3.1", "EnablePBR": True, "FaceCount": 20000, "ResultFormat": "OBJ"}
        assert job.job_id == "official:job-1"
        assert (await p.poll(job.job_id)).status == "running"
        assert route.calls[1].request.headers["X-TC-Action"] == "QueryHunyuanTo3DProJob"
        assert json.loads(route.calls[1].request.content) == {"JobId": "job-1"}
        j = await p.poll(job.job_id)
        assert j.status == "succeeded" and j.outputs["obj"] == "https://t.test/a.obj" and j.thumbnail_url == "https://t.test/a.png"
        await p.submit_image(png)
        assert "ImageBase64" in json.loads(route.calls[3].request.content)
        with pytest.raises(ProviderError) as exc:
            await p.submit_text("y")
        assert "HUNYUAN_SECRET_KEY" in str(exc.value) and exc.value.status == 401


async def test_hunyuan_local(monkeypatch, png):
    monkeypatch.setenv("HUNYUAN_LOCAL_URL", "http://localhost:8081/")
    p = get_provider("hunyuan")
    assert p.mode == "local" and p.capabilities()["formats"] == ["glb"]
    with respx.mock(base_url="http://localhost:8081") as mock:
        send = mock.post("/send").mock(return_value=httpx.Response(200, json={"uid": "abc"}))
        mock.get("/status/abc").mock(side_effect=[
            httpx.Response(200, json={"status": "processing"}),
            httpx.Response(200, json={"status": "completed", "model_base64": "Z2xURi1sb2NhbA=="}),
        ])
        job = await p.submit_image(png)
        assert "image" in json.loads(send.calls[0].request.content)
        assert job.job_id == "local:abc"
        assert (await p.poll(job.job_id)).status == "running"
        j = await p.poll(job.job_id)
        assert j.status == "succeeded" and j.outputs["glb"].startswith("file://")
        path = await p.download(j, "glb")
        assert open(path, "rb").read() == b"glTF-local"


async def test_hunyuan_multiview_generate_type_polygon(monkeypatch, png):
    monkeypatch.setenv("HUNYUAN_SECRET_ID", "id")
    monkeypatch.setenv("HUNYUAN_SECRET_KEY", "key")
    monkeypatch.setenv("HUNYUAN_REGION", "ap-singapore")
    p = get_provider("hunyuan")
    caps = p.capabilities()
    assert caps["model"] == "3.1" and caps["rig"] and caps["post_jobs"] == ["reduce_face", "rig", "texture", "uv"]
    with respx.mock(base_url="https://ai3d.tencentcloudapi.com") as mock:
        route = mock.post("/").mock(return_value=httpx.Response(200, json={"Response": {"JobId": "mv-1"}}))
        views = ["https://v.test/left.png", {"view": "back", "image": png}, {"view": "right", "image": "https://v.test/right.png"}]
        job = await p.submit_image("https://v.test/front.png", extra_images=views, generate_type="LowPoly", polygon_type="quadrilateral", pbr=False)
        req = route.calls[0].request
        assert req.headers["X-TC-Action"] == "SubmitHunyuanTo3DProJob" and req.headers["X-TC-Region"] == "ap-singapore"
        body = json.loads(req.content)
        assert body["Model"] == "3.1" and body["GenerateType"] == "LowPoly" and body["PolygonType"] == "quadrilateral" and body["EnablePBR"] is False
        assert body["ImageUrl"] == "https://v.test/front.png"
        assert [v["ViewType"] for v in body["MultiViewImages"]] == ["left", "back", "right"]
        assert body["MultiViewImages"][0]["ViewImageUrl"] == "https://v.test/left.png"
        assert "ViewImageBase64" in body["MultiViewImages"][1]
        assert job.job_id == "official:mv-1"
        with pytest.raises(ProviderError) as exc:
            await p.submit_text("x", generate_type="Fancy")
        assert "generate_type" in str(exc.value)
        with pytest.raises(ProviderError) as exc:
            await p.submit_image("https://v.test/a.png", extra_images=["https://v.test/%d.png" % i for i in range(9)])
        assert "at most 8" in str(exc.value)
        with pytest.raises(ProviderError) as exc:
            await p.submit_image("https://v.test/a.png", extra_images=[{"view": "diagonal", "image": "https://v.test/d.png"}])
        assert "view type" in str(exc.value)


async def test_hunyuan_post_jobs(monkeypatch, png):
    monkeypatch.setenv("HUNYUAN_SECRET_ID", "id")
    monkeypatch.setenv("HUNYUAN_SECRET_KEY", "key")
    p = get_provider("hunyuan")
    done = {"Response": {"Status": "DONE", "ResultFile3Ds": [{"Type": "GLB", "Url": "https://t.test/a.glb"}, {"Type": "FBX", "Url": "https://t.test/a.fbx"}]}}
    with respx.mock(base_url="https://ai3d.tencentcloudapi.com") as mock:
        route = mock.post("/").mock(side_effect=[
            httpx.Response(200, json=done),  # source poll for texture
            httpx.Response(200, json={"Response": {"JobId": "tx-1"}}),
            httpx.Response(200, json={"Response": {"Status": "RUN"}}),
            httpx.Response(200, json=done),  # source poll for uv
            httpx.Response(200, json={"Response": {"JobId": "uv-1"}}),
            httpx.Response(200, json=done),  # source poll for rig
            httpx.Response(200, json={"Response": {"JobId": "rg-1"}}),
            httpx.Response(200, json={"Response": {"Status": "DONE", "ResultFile3Ds": [{"Type": "FBX", "Url": "https://t.test/rigged.fbx"}]}}),
            httpx.Response(200, json={"Response": {"JobId": "rf-1"}}),  # reduce_face from a URL, no source poll
        ])
        job = await p.post_job("texture", "official:job-1", prompt="rusty iron", image=png, pbr=True, keep_uv=True, texture_size=2048)
        assert route.calls[0].request.headers["X-TC-Action"] == "QueryHunyuanTo3DProJob"
        assert route.calls[1].request.headers["X-TC-Action"] == "SubmitTextureTo3DJob"
        body = json.loads(route.calls[1].request.content)
        assert body["File3D"] == {"Type": "GLB", "Url": "https://t.test/a.glb"} and body["Prompt"] == "rusty iron" and body["Model"] == "3.1"
        assert "Base64" in body["Image"] and body["EnablePBR"] is True and body["EnableKeepUV"] is True and body["TextureSize"] == 2048
        assert job.job_id == "official:texture:tx-1" and job.raw["kind"] == "texture"
        j = await p.poll(job.job_id)
        assert route.calls[2].request.headers["X-TC-Action"] == "QueryTextureTo3DJob"
        assert json.loads(route.calls[2].request.content) == {"JobId": "tx-1"} and j.status == "running" and j.job_id == "official:texture:tx-1"

        job = await p.post_job("uv", "official:job-1")
        assert route.calls[4].request.headers["X-TC-Action"] == "SubmitHunyuanTo3DUVJob"
        assert json.loads(route.calls[4].request.content) == {"File": {"Type": "GLB", "Url": "https://t.test/a.glb"}}
        assert job.job_id == "official:uv:uv-1"

        job = await p.rig("official:job-1", motion_type=3)
        assert route.calls[6].request.headers["X-TC-Action"] == "SubmitAutoRiggingJob"
        assert json.loads(route.calls[6].request.content) == {"File3D": {"Type": "FBX", "Url": "https://t.test/a.fbx"}, "MotionType": 3}
        j = await p.poll(job.job_id)
        assert route.calls[7].request.headers["X-TC-Action"] == "QueryAutoRiggingJob"
        assert j.status == "succeeded" and j.outputs["fbx"] == "https://t.test/rigged.fbx"

        job = await p.remesh("https://cdn.test/mesh.obj", topology="quad", face_level="low")
        assert route.calls[8].request.headers["X-TC-Action"] == "SubmitReduceFaceJob"
        assert json.loads(route.calls[8].request.content) == {"File3D": {"Type": "OBJ", "Url": "https://cdn.test/mesh.obj"}, "PolygonType": "quadrilateral", "FaceLevel": "low"}
        assert job.job_id == "official:reduce_face:rf-1"
    with pytest.raises(ProviderError) as exc:
        await p.post_job("bake", "official:job-1")
    assert "unknown post job" in str(exc.value)


async def test_hunyuan_post_job_needs_official_route(monkeypatch):
    monkeypatch.setenv("HUNYUAN_LOCAL_URL", "http://localhost:8081")
    p = get_provider("hunyuan")
    with pytest.raises(ProviderError) as exc:
        await p.post_job("uv", "local:abc")
    assert "official" in str(exc.value)
    with pytest.raises(ProviderError):
        await p.submit_world(prompt="a forest")


async def test_hunyuan_world_actions_from_env_and_invalid_action(monkeypatch, png):
    monkeypatch.setenv("HUNYUAN_SECRET_ID", "id")
    monkeypatch.setenv("HUNYUAN_SECRET_KEY", "key")
    p = get_provider("hunyuan")
    with respx.mock(base_url="https://ai3d.tencentcloudapi.com") as mock:
        route = mock.post("/").mock(side_effect=[
            httpx.Response(200, json={"Response": {"JobId": "w-1"}}),
            httpx.Response(200, json={"Response": {"Status": "WAIT"}}),
            httpx.Response(200, json={"Response": {"Error": {"Code": "InvalidAction", "Message": "no such action"}}}),
        ])
        job = await p.submit_world(prompt="a mossy forest clearing", image=png)
        assert route.calls[0].request.headers["X-TC-Action"] == "SubmitHunyuanWorldJob"
        body = json.loads(route.calls[0].request.content)
        assert body["Prompt"] == "a mossy forest clearing" and "ImageBase64" in body
        assert job.job_id == "official:world:w-1"
        j = await p.poll(job.job_id)
        assert route.calls[1].request.headers["X-TC-Action"] == "QueryHunyuanWorldJob" and j.status == "queued"
        with pytest.raises(ProviderError) as exc:
            await p.submit_world(prompt="again")
        assert "unverified" in str(exc.value) and "HUNYUAN_WORLD_SUBMIT" in str(exc.value)
    with pytest.raises(ProviderError):
        await p.submit_world()
    monkeypatch.setenv("HUNYUAN_WORLD_SUBMIT", "SubmitHunyuanWorldProJob")
    monkeypatch.setenv("HUNYUAN_WORLD_QUERY", "QueryHunyuanWorldProJob")
    with respx.mock(base_url="https://ai3d.tencentcloudapi.com") as mock:
        route = mock.post("/").mock(side_effect=[
            httpx.Response(200, json={"Response": {"JobId": "w-2"}}),
            httpx.Response(200, json={"Response": {"Status": "DONE", "ResultFile3Ds": [{"Type": "GLB", "Url": "https://t.test/world.glb"}]}}),
        ])
        job = await p.submit_world(image="https://img.test/pano.jpg")
        assert route.calls[0].request.headers["X-TC-Action"] == "SubmitHunyuanWorldProJob"
        assert json.loads(route.calls[0].request.content) == {"ImageUrl": "https://img.test/pano.jpg"}
        j = await p.poll(job.job_id)
        assert route.calls[1].request.headers["X-TC-Action"] == "QueryHunyuanWorldProJob" and j.outputs["glb"].endswith("world.glb")


# Replicate ------------------------------------------------------------------------
def test_replicate_helpers():
    assert split_model("tencent/hunyuan-3d-3.1") == ("tencent", "hunyuan-3d-3.1", None)
    assert split_model("a/b:abc123") == ("a", "b", "abc123")
    with pytest.raises(ProviderError):
        split_model("nolash")
    assert outputs_from("https://r.test/out.glb") == ({"glb": "https://r.test/out.glb"}, None)
    outs, thumb = outputs_from(["https://r.test/p.png", "https://r.test/m.obj", "https://r.test/file"])
    assert outs == {"image": "https://r.test/p.png", "obj": "https://r.test/m.obj", "file": "https://r.test/file"} and thumb == "https://r.test/p.png"
    outs, _ = outputs_from({"mesh": "https://r.test/x?dl=1", "textures": ["https://r.test/t.png"]})
    assert outs == {"mesh": "https://r.test/x?dl=1", "image": "https://r.test/t.png"}
    assert outputs_from(None) == ({}, None)


async def test_replicate_text_image_poll_download(monkeypatch, png, tmp_path):
    with pytest.raises(ProviderError) as exc:
        await ReplicateProvider().submit_text("x")
    assert "REPLICATE_API_TOKEN" in str(exc.value)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_tok")
    p = get_provider("replicate")
    assert p.configured() and p.capabilities()["model"] == "tencent/hunyuan-3d-3.1"
    with respx.mock(base_url="https://api.replicate.com/v1") as mock:
        create = mock.post("/models/tencent/hunyuan-3d-3.1/predictions").mock(return_value=httpx.Response(201, json={"id": "pr1", "status": "starting", "output": None}))
        mock.get("/predictions/pr1").mock(side_effect=[
            httpx.Response(200, json={"id": "pr1", "status": "processing", "output": None, "logs": "..."}),
            httpx.Response(200, json={"id": "pr1", "status": "succeeded", "output": {"mesh": "https://replicate.delivery/x/out.glb", "preview": "https://replicate.delivery/x/p.png"}}),
        ])
        job = await p.submit_text("a stone lantern", pbr=True, face_limit=1000, extra_images=None)
        req = create.calls[0].request
        assert req.headers["Authorization"] == "Bearer r8_tok" and req.headers["Prefer"] == "wait=0"
        assert json.loads(req.content) == {"input": {"prompt": "a stone lantern"}}
        assert job.job_id == "pr1" and job.status == "queued"
        j = await p.poll("pr1")
        assert j.status == "running" and "logs" not in j.raw
        j = await p.poll("pr1")
        assert j.status == "succeeded" and j.outputs["glb"] == "https://replicate.delivery/x/out.glb" and j.thumbnail_url.endswith("p.png")

        other = mock.post("/models/someone/world-model/predictions").mock(return_value=httpx.Response(201, json={"id": "pr2", "status": "starting"}))
        await p.submit_image(png, model="someone/world-model", steps=20)
        body = json.loads(other.calls[0].request.content)
        assert body["input"]["image"].startswith("data:image/png;base64,") and body["input"]["steps"] == 20
        versioned = mock.post("/predictions").mock(return_value=httpx.Response(201, json={"id": "pr3", "status": "starting"}))
        await p.submit_image("https://img.test/a.jpg", model="a/b:deadbeef")
        assert json.loads(versioned.calls[0].request.content) == {"input": {"image": "https://img.test/a.jpg"}, "version": "deadbeef"}
        mock.get("/predictions/bad").mock(return_value=httpx.Response(200, json={"id": "bad", "status": "failed", "error": "OOM"}))
        j = await p.poll("bad")
        assert j.status == "failed" and "OOM" in j.message
        mock.get("/predictions/nope").mock(return_value=httpx.Response(401, json={}))
        with pytest.raises(ProviderError) as exc:
            await p.poll("nope")
        assert "REPLICATE_API_TOKEN" in str(exc.value)
    with respx.mock() as mock:
        mock.get("https://replicate.delivery/x/out.glb").mock(return_value=httpx.Response(200, content=b"glb-bytes"))
        succeeded = await _succeeded_replicate(p)
        path = await p.download(succeeded, "glb")
    assert path.endswith(".glb") and open(path, "rb").read() == b"glb-bytes"


async def _succeeded_replicate(p):
    with respx.mock(base_url="https://api.replicate.com/v1") as mock:
        mock.get("/predictions/pr1").mock(return_value=httpx.Response(200, json={"id": "pr1", "status": "succeeded", "output": "https://replicate.delivery/x/out.glb"}))
        return await p.poll("pr1")


# Higgsfield ---------------------------------------------------------------------
async def test_higgsfield_honest_when_no_endpoint(monkeypatch):
    p = HiggsfieldProvider()
    assert not p.configured()
    with pytest.raises(ProviderError) as exc:
        await p.submit_text("x")
    assert "generate_3d" in str(exc.value) and "HIGGSFIELD_3D_ENDPOINT" in str(exc.value)
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "k")
    monkeypatch.setenv("HIGGSFIELD_API_SECRET", "s")
    assert not p.configured() and "HIGGSFIELD_3D_ENDPOINT is unset" in p.how_to_configure()


async def test_higgsfield_with_endpoint(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "k")
    monkeypatch.setenv("HIGGSFIELD_API_SECRET", "s")
    monkeypatch.setenv("HIGGSFIELD_3D_ENDPOINT", "/tripo-ai/tripo-3d/generate")
    p = get_provider("higgsfield")
    assert p.configured()
    with respx.mock(base_url="https://api.higgsfield.ai") as mock:
        sub = mock.post("/tripo-ai/tripo-3d/generate").mock(return_value=httpx.Response(200, json={"request_id": "h1"}))
        mock.get("/requests/h1/status").mock(side_effect=[
            httpx.Response(200, json={"status": "in_progress"}),
            httpx.Response(200, json={"status": "completed", "results": [{"raw": {"url": "https://h.test/o.glb"}}]}),
        ])
        job = await p.submit_text("a mug")
        assert sub.calls[0].request.headers["Authorization"] == "Key k:s"
        assert (await p.poll(job.job_id)).status == "running"
        j = await p.poll(job.job_id)
        assert j.status == "succeeded" and j.outputs["glb"] == "https://h.test/o.glb"
