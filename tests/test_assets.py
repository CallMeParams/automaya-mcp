"""Asset libraries: Poly Haven, Sketchfab, Poly Pizza clients + plugin handlers + tools."""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest
import respx
from tests.conftest import parse
from tests.test_generation import arm_import

from automaya_bridge.handlers import assets as assets_handler
from automaya_bridge.registry import invoke
from automaya_mcp.providers import ProviderError
from automaya_mcp.providers.assets import PolyHaven, PolyPizza, Sketchfab

PH = "https://api.polyhaven.com"
SF = "https://api.sketchfab.com/v3"
PZ = "https://api.poly.pizza/v1.1"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for var in ("SKETCHFAB_API_TOKEN", "POLYPIZZA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUTOMAYA_DOWNLOAD_DIR", str(tmp_path / "dl"))
    assets_handler.IMPORT_LOG.clear()


def hdri_files():
    return {"hdri": {"1k": {"hdr": {"url": "https://dl.test/sky_1k.hdr", "size": 1}, "exr": {"url": "https://dl.test/sky_1k.exr"}}, "4k": {"hdr": {"url": "https://dl.test/sky_4k.hdr"}}}}


def texture_files():
    res = lambda name: {"2k": {"jpg": {"url": "https://dl.test/%s_2k.jpg" % name}, "png": {"url": "https://dl.test/%s_2k.png" % name}}}  # noqa: E731
    files = {"Diffuse": res("diff"), "nor_gl": res("nor"), "Rough": res("rough"), "arm": res("arm"), "AO": res("ao")}
    files["Displacement"] = {"2k": {"png": {"url": "https://dl.test/disp_2k.png"}, "exr": {"url": "https://dl.test/disp_2k.exr"}}}
    files["blend"] = {"2k": {"blend": {"url": "https://dl.test/x.blend"}}}
    return files


def model_files():
    return {"fbx": {"2k": {"fbx": {"url": "https://dl.test/rock_2k.fbx", "include": {"textures/rock_diff_2k.jpg": {"url": "https://dl.test/rock_diff_2k.jpg"}}}}}, "gltf": {"2k": {"gltf": {"url": "https://dl.test/rock_2k.gltf"}}}}


# Poly Haven client -----------------------------------------------------------------
async def test_polyhaven_search_categories_and_user_agent():
    ph = PolyHaven()
    with respx.mock(base_url=PH) as mock:
        cats = mock.get("/categories/hdris").mock(return_value=httpx.Response(200, json={"outdoor": 300, "sky": 120}))
        assert await ph.categories("hdri") == {"outdoor": 300, "sky": 120}
        assert cats.calls[0].request.headers["User-Agent"] == "automaya-mcp"
        mock.get("/search", params={"q": "brick", "t": "textures"}).mock(return_value=httpx.Response(200, json={"brick_wall": {"name": "Brick Wall", "type": 1, "categories": ["brick", "wall"], "tags": ["red"], "thumbnail_url": "https://t/1.png"}, "other": {"name": "Other", "type": 1, "categories": ["floor"]}}))
        out = await ph.search("brick", "textures", categories=["wall"])
        assert out == [{"id": "brick_wall", "name": "Brick Wall", "type": "textures", "categories": ["brick", "wall"], "tags": ["red"], "download_count": None, "thumbnail_url": "https://t/1.png"}]
        mock.get("/assets", params={"type": "models"}).mock(return_value=httpx.Response(200, json={"rock": {"name": "Rock", "type": 2}}))
        assert (await ph.search("", "models"))[0]["id"] == "rock"
    with pytest.raises(ProviderError):
        await ph.categories("sounds")


async def test_polyhaven_hdri_texture_model_downloads(tmp_path):
    ph = PolyHaven()
    with respx.mock(base_url=PH) as mock:
        mock.get("/files/sky").mock(return_value=httpx.Response(200, json=hdri_files()))
        mock.get("/files/brick").mock(return_value=httpx.Response(200, json=texture_files()))
        mock.get("/files/rock").mock(return_value=httpx.Response(200, json=model_files()))
        mock.route(host="dl.test").mock(return_value=httpx.Response(200, content=b"bytes"))
        hdri = await ph.download_hdri("sky", "2k", "hdr")
        assert hdri["resolution"] == "1k" and hdri["path"].endswith("sky_1k.hdr") and hdri["url"] == "https://dl.test/sky_1k.hdr"
        hdri = await ph.download_hdri("sky", "8k", "exr")
        assert hdri["resolution"] == "4k" and hdri["format"] == "hdr"
        tex = await ph.download_texture_set("brick", "2k", "jpg")
        assert sorted(tex["maps"]) == ["ao", "arm", "base_color", "displacement", "normal", "roughness"]
        assert tex["maps"]["displacement"].endswith(".png") and tex["maps"]["base_color"].endswith(".jpg")
        model = await ph.download_model("rock", "2k", "fbx")
        assert model["path"].endswith("rock_2k.fbx") and model["textures"][0].endswith("textures/rock_diff_2k.jpg")
        with pytest.raises(ProviderError) as exc:
            await ph.download_hdri("brick")
        assert "not an HDRI" in str(exc.value)
        with pytest.raises(ProviderError) as exc:
            await ph.download_model("rock", fmt="blend")
        assert "available" in str(exc.value)


# Sketchfab client -----------------------------------------------------------------
async def test_sketchfab_search_preview_download(monkeypatch):
    sf = Sketchfab()
    with pytest.raises(ProviderError) as exc:
        await sf.download_links("abc")
    assert "SKETCHFAB_API_TOKEN" in str(exc.value)
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "tok")
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("model/scene.fbx", "fbx")
        zf.writestr("model/tex.png", "png")
    with respx.mock(base_url=SF) as mock:
        search = mock.get("/search").mock(return_value=httpx.Response(200, json={
            "results": [{"uid": "u1", "name": "Car", "user": {"displayName": "Ann"}, "license": {"label": "CC BY"}, "faceCount": 1200, "isDownloadable": True, "animationCount": 0, "thumbnails": {"images": [{"width": 256, "url": "https://sf.test/s.jpg"}, {"width": 1024, "url": "https://sf.test/l.jpg"}]}, "viewerUrl": "https://sketchfab.com/u1"}],
            "next": "https://api.sketchfab.com/v3/search?type=models&cursor=bmV4dA&q=car",
        }))
        out = await sf.search("car", count=5, license="by")
        params = dict(search.calls[0].request.url.params)
        assert params["type"] == "models" and params["downloadable"] == "true" and params["license"] == "by" and params["q"] == "car"
        assert search.calls[0].request.headers["Authorization"] == "Token tok"
        assert out["next_cursor"] == "bmV4dA" and out["results"][0]["thumbnail_url"] == "https://sf.test/s.jpg" and out["results"][0]["license"] == "CC BY"
        mock.get("/models/u1").mock(return_value=httpx.Response(200, json={"uid": "u1", "name": "Car", "thumbnails": {"images": [{"width": 2048, "url": "https://sf.test/xl.png"}, {"width": 1024, "url": "https://sf.test/l.png"}]}}))
        mock.get("https://sf.test/l.png").mock(return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}))
        data, fmt, info = await sf.thumbnail_bytes("u1")
        assert data == b"\x89PNG" and fmt == "png" and info["name"] == "Car"
        mock.get("/models/u1/download").mock(return_value=httpx.Response(200, json={"glb": {"url": "https://sf.test/u1.glb", "size": 10}, "source": {"url": "https://sf.test/u1_src.zip", "size": 20}}))
        mock.get("https://sf.test/u1_src.zip").mock(return_value=httpx.Response(200, content=src.getvalue()))
        dl = await sf.download("u1", "source")
        assert dl["path"].endswith("scene.fbx") and len(dl["files"]) == 1
        with pytest.raises(ProviderError) as exc:
            await sf.download("u1", "usdz")
        assert "no 'usdz' download" in str(exc.value)


# Poly Pizza client ------------------------------------------------------------------
async def test_polypizza_search_and_download(monkeypatch):
    pz = PolyPizza()
    with pytest.raises(ProviderError) as exc:
        await pz.search("tree")
    assert "POLYPIZZA_API_KEY" in str(exc.value)
    monkeypatch.setenv("POLYPIZZA_API_KEY", "pk")
    with respx.mock(base_url=PZ) as mock:
        s = mock.get("/search/tree").mock(return_value=httpx.Response(200, json={"total": 1, "results": [{"ID": "m1", "Title": "Tree", "Download": "https://pz.test/m1.glb", "Attribution": "Tree by Bob [CC-BY]", "TriCount": 300, "Licence": "CC-BY", "Thumbnail": "https://pz.test/t.png"}]}))
        out = await pz.search("tree", limit=5, page=1)
        assert s.calls[0].request.headers["x-auth-token"] == "pk" and dict(s.calls[0].request.url.params) == {"limit": "5", "page": "1"}
        assert out["results"][0] == {"id": "m1", "title": "Tree", "download_url": "https://pz.test/m1.glb", "attribution": "Tree by Bob [CC-BY]", "creator": None, "tri_count": 300, "licence": "CC-BY", "thumbnail_url": "https://pz.test/t.png", "animated": None}
        mock.get("/model/m1").mock(return_value=httpx.Response(200, json={"ID": "m1", "Title": "Tree", "Download": "https://pz.test/m1.glb", "Licence": "CC-BY"}))
        mock.get("https://pz.test/m1.glb").mock(return_value=httpx.Response(200, content=b"glb"))
        dl = await pz.download("m1")
        assert dl["path"].endswith("m1.glb") and dl["title"] == "Tree"


# plugin handlers ------------------------------------------------------------------
def test_import_model_handler(fake_maya, tmp_path):
    obj = tmp_path / "a.obj"
    obj.write_text("v 0 0 0\n")
    arm_import(fake_maya, "|a")
    fake_maya.responses["exactWorldBoundingBox"] = [-1.0, 0.5, -1.0, 1.0, 2.5, 1.0]
    out = assets_handler.import_model(path=str(obj), name="grp", group=True, center=True)
    assert out["top_nodes"] == ["grp"]
    assert fake_maya.calls_to("group")[0][1]["name"] == "grp"
    assert fake_maya.calls_to("xform")[0][1] == {"centerPivots": True}
    assert fake_maya.calls_to("move")[0][0] == (0.0, -0.5, 0.0, "grp")
    assert assets_handler.list_imported()[-1]["kind"] == "model"
    resp = invoke("assets.import_model", {"path": str(tmp_path / "missing.fbx")})
    assert resp["status"] == "error" and "file not found" in resp["message"]


def test_create_skydome_with_and_without_arnold(fake_maya, tmp_path):
    hdr = tmp_path / "sky.hdr"
    hdr.write_bytes(b"#?RADIANCE")
    fake_maya.responses["listRelatives"] = lambda *a, **k: ["|sky"] if k.get("parent") else ["|sky|skyShape"]
    fake_maya.responses["ls"] = lambda *a, **k: [a[0]] if a else []
    out = assets_handler.create_skydome(path=str(hdr), name="sky", intensity=2.0, rotation=45.0)
    assert out["arnold"] is True and out["shape"] == "|sky|skyShape"
    node_types = [a[0] for a, _ in fake_maya.calls_to("shadingNode")]
    assert "aiSkyDomeLight" in node_types and "file" in node_types
    set_attrs = {a[0]: a[1] for a, _ in fake_maya.calls_to("setAttr") if len(a) > 1}
    assert set_attrs["|sky|skyShape.intensity"] == 2.0 and set_attrs["sky.rotateY"] == 45.0
    assert set_attrs["sky_file.fileTextureName"] == str(hdr)

    fake_maya.reset()
    fake_maya.responses["pluginInfo"] = lambda *a, **k: False if k.get("loaded") else []
    fake_maya.responses["loadPlugin"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no mtoa"))
    fake_maya.responses["polySphere"] = ["envSphere", "polySphere1"]
    fake_maya.responses["ls"] = lambda *a, **k: ["|envSphere"]
    out = assets_handler.create_skydome(path=str(hdr), name="envSphere")
    assert out["arnold"] is False and out["light"] == "|envSphere" and "polyNormal" in [n for n, _, _ in fake_maya.calls]
    assert "surfaceShader" in [a[0] for a, _ in fake_maya.calls_to("shadingNode")]


def test_import_texture_set_minimal_and_delegated(fake_maya, tmp_path, monkeypatch):
    paths = {}
    for k in ("base_color", "normal", "roughness", "arm", "displacement"):
        p = tmp_path / ("%s.png" % k)
        p.write_bytes(b"x")
        paths[k] = str(p)
    # Packed ARM forces the local wiring path.
    out = assets_handler.import_texture_set(maps=paths, name="brick", assign_to=["pCube1"])
    assert out["via"] == "assets.minimal" and out["shader"] == "brick" and out["shading_group"] == "brickSG"
    assert sorted(out["file_nodes"]) == ["arm", "base_color", "displacement", "normal", "roughness"]
    conns = [a for a, _ in fake_maya.calls_to("connectAttr")]
    assert ("brick_arm.outColorG", "brick.specularRoughness") in conns and ("brick_bump2d.outNormal", "brick.normalCamera") in conns
    assert ("brick_dispShader.displacement", "brickSG.displacementShader") in conns
    assert fake_maya.calls_to("sets")[-1][1].get("forceElement") == "brickSG"

    fake_maya.reset()
    called = {}

    def fake_pbr(**kwargs):
        called.update(kwargs)
        return {"material": "wood", "shading_group": "woodSG"}

    from automaya_bridge.handlers import materials

    monkeypatch.setattr(materials, "create_pbr_network", fake_pbr)
    simple = {k: v for k, v in paths.items() if k != "arm"}
    out = assets_handler.import_texture_set(maps=simple, name="wood")
    assert out["via"] == "materials.create_pbr_network" and out["shader"] == "wood"
    assert called["base_color"] == paths["base_color"] and called["normal"] == paths["normal"] and called["shader_type"] == "standardSurface"

    resp = invoke("assets.import_texture_set", {"maps": {"base_color": "/nope.png"}})
    assert resp["status"] == "error" and "not found" in resp["message"]


# tools -------------------------------------------------------------------------------
async def test_polyhaven_tools(call_tool, fake_maya):
    arm_import(fake_maya, "|rock")
    fake_maya.responses["listRelatives"] = lambda *a, **k: ["|sky"] if k.get("parent") else ["|sky|skyShape"]
    with respx.mock(base_url=PH) as mock:
        mock.get("/categories/textures").mock(return_value=httpx.Response(200, json={"brick": 40}))
        assert parse(await call_tool("maya_polyhaven_categories", {"params": {"type": "textures"}})) == {"brick": 40}
        mock.get("/search").mock(return_value=httpx.Response(200, json={"sky": {"name": "Sky", "type": 0, "categories": ["outdoor"]}}))
        data = parse(await call_tool("maya_polyhaven_search", {"params": {"query": "sky", "type": "hdris"}}))
        assert data["results"][0]["id"] == "sky"
        mock.get("/files/sky").mock(return_value=httpx.Response(200, json=hdri_files()))
        mock.get("/files/brick").mock(return_value=httpx.Response(200, json=texture_files()))
        mock.get("/files/rock").mock(return_value=httpx.Response(200, json=model_files()))
        mock.route(host="dl.test").mock(return_value=httpx.Response(200, content=b"bytes"))
        data = parse(await call_tool("maya_polyhaven_download", {"params": {"asset_id": "sky", "type": "hdri", "resolution": "1k", "intensity": 1.5}}))
        assert data["download"]["path"].endswith("sky_1k.hdr") and data["maya"]["arnold"] is True and data["maya"]["light"].endswith("sky")
        data = parse(await call_tool("maya_polyhaven_download", {"params": {"asset_id": "brick", "type": "textures", "assign_to": ["pCube1"]}}))
        assert data["maya"]["shader"] == "brick" and "arm" in data["download"]["maps"]
        data = parse(await call_tool("maya_polyhaven_download", {"params": {"asset_id": "rock", "type": "models", "scale": 100}}))
        assert data["maya"]["top_nodes"] == ["|rock"] and data["download"]["textures"]
        data = parse(await call_tool("maya_polyhaven_download", {"params": {"asset_id": "rock", "type": "models", "import_into_maya": False}}))
        assert "maya" not in data and data["path"].endswith(".fbx")
    text = await call_tool("maya_polyhaven_download", {"params": {"asset_id": "x", "type": "sounds"}})
    assert text.startswith("Error")


async def test_sketchfab_tools(call_tool, fake_maya, monkeypatch):
    text = await call_tool("maya_sketchfab_download", {"params": {"uid": "abcd"}})
    assert "SKETCHFAB_API_TOKEN" in text
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "tok")
    arm_import(fake_maya, "|car")
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("scene.obj", "v 0 0 0")
    with respx.mock(base_url=SF) as mock:
        mock.get("/search").mock(return_value=httpx.Response(200, json={"results": [{"uid": "abcd", "name": "Car", "thumbnails": {"images": []}}]}))
        data = parse(await call_tool("maya_sketchfab_search", {"params": {"query": "car"}}))
        assert data["results"][0]["uid"] == "abcd"
        mock.get("/models/abcd").mock(return_value=httpx.Response(200, json={"uid": "abcd", "name": "Car", "faceCount": 5, "thumbnails": {"images": [{"width": 512, "url": "https://sf.test/t.jpg"}]}}))
        mock.get("https://sf.test/t.jpg").mock(return_value=httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}))
        text = await call_tool("maya_sketchfab_preview", {"params": {"uid": "abcd"}})
        assert text.startswith("<image ") and '"name": "Car"' in text
        mock.get("/models/abcd/download").mock(return_value=httpx.Response(200, json={"source": {"url": "https://sf.test/src.zip"}}))
        mock.get("https://sf.test/src.zip").mock(return_value=httpx.Response(200, content=src.getvalue()))
        data = parse(await call_tool("maya_sketchfab_download", {"params": {"uid": "abcd", "format": "source", "name": "car"}}))
        assert data["download"]["path"].endswith("scene.obj") and data["maya"]["top_nodes"] == ["car"]


async def test_polypizza_tools(call_tool, fake_maya, monkeypatch):
    monkeypatch.setenv("POLYPIZZA_API_KEY", "pk")
    with respx.mock(base_url=PZ) as mock:
        mock.get("/search/tree").mock(return_value=httpx.Response(200, json={"results": [{"ID": "m1", "Title": "Tree", "Download": "https://pz.test/m1.glb"}]}))
        data = parse(await call_tool("maya_polypizza_search", {"params": {"query": "tree"}}))
        assert data["results"][0]["id"] == "m1"
        mock.get("/model/m1").mock(return_value=httpx.Response(200, json={"ID": "m1", "Title": "Tree", "Download": "https://pz.test/m1.glb", "Attribution": "Tree by Bob"}))
        mock.get("https://pz.test/m1.glb").mock(return_value=httpx.Response(200, content=b"glb"))
        # No glTF importer in the stub: pluginInfo says loaded, but file import yields no nodes -> error is surfaced, download kept.
        data = parse(await call_tool("maya_polypizza_download", {"params": {"model_id": "m1"}}))
        assert data["download"]["path"].endswith("m1.glb") and data["download"]["attribution"] == "Tree by Bob"
        assert "error" in data and "glTF" in data["note"]
