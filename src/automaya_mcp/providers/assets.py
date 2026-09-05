"""Free asset library clients: Poly Haven, Sketchfab, Poly Pizza.

All calls are async httpx. Downloads land in ``download_dir()`` (or a folder
the caller passes) and return local paths that the Maya plugin can import.
"""
from __future__ import annotations

import os
import zipfile
from typing import Any, Dict, List

from .base import ProviderError, download_dir, download_url, env_key, http, raise_for_status, safe_name

POLYHAVEN_BASE = "https://api.polyhaven.com"
SKETCHFAB_BASE = "https://api.sketchfab.com/v3"
POLYPIZZA_BASE = "https://api.poly.pizza/v1.1"

POLYHAVEN_TYPES = ("hdris", "textures", "models")
# Poly Haven map names in the files dict -> what the Maya material handler expects
TEXTURE_MAP_ALIASES = {
    "Diffuse": "base_color",
    "diff": "base_color",
    "Color": "base_color",
    "nor_gl": "normal",
    "Normal": "normal",
    "nor_dx": "normal_dx",
    "Rough": "roughness",
    "Roughness": "roughness",
    "Metal": "metalness",
    "Metalness": "metalness",
    "arm": "arm",
    "ARM": "arm",
    "AO": "ao",
    "Displacement": "displacement",
    "disp": "displacement",
    "Bump": "bump",
    "spec": "specular",
    "Specular": "specular",
    "Translucent": "translucent",
    "Opacity": "opacity",
}


def _inside(folder: str, rel: str) -> str | None:
    """Join ``rel`` (a path the remote API chose) under ``folder``; None when it escapes."""
    root = os.path.realpath(folder)
    dest = os.path.realpath(os.path.join(root, rel.lstrip("/")))
    if dest == root or not dest.startswith(root + os.sep):
        return None
    return dest


# Poly Haven ---------------------------------------------------------------
class PolyHaven:
    name = "polyhaven"

    def __init__(self, base: str = POLYHAVEN_BASE) -> None:
        self.base = base

    def _client(self, timeout: float = 60.0):
        return http(timeout=timeout, headers={"User-Agent": "automaya-mcp"})

    async def _get(self, path: str, params: Dict[str, Any] | None = None) -> Any:
        async with self._client() as client:
            resp = await client.get(self.base + path, params={k: v for k, v in (params or {}).items() if v not in (None, "")})
        raise_for_status(resp, "Poly Haven", "")
        return resp.json()

    @staticmethod
    def _type(asset_type: str) -> str:
        t = (asset_type or "").lower().rstrip("s")
        mapping = {"hdri": "hdris", "texture": "textures", "model": "models", "all": "all", "": "all"}
        if t not in mapping:
            raise ProviderError("Poly Haven type must be hdris, textures or models (got %r)" % asset_type)
        return mapping[t]

    async def categories(self, asset_type: str) -> Dict[str, int]:
        return await self._get("/categories/%s" % self._type(asset_type))

    async def assets(self, asset_type: str = "all", categories: List[str] | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        t = self._type(asset_type)
        if t != "all":
            params["type"] = t
        if categories:
            params["categories"] = ",".join(categories)
        return await self._get("/assets", params)

    async def search(self, query: str = "", asset_type: str = "all", categories: List[str] | None = None, limit: int = 20) -> List[Dict[str, Any]]:
        t = self._type(asset_type)
        if query:
            params: Dict[str, Any] = {"q": query}
            if t != "all":
                params["t"] = t
            data = await self._get("/search", params)
            # /search returns {id: {...}} like /assets, or a list in newer builds
            if isinstance(data, list):
                data = {(d.get("id") or d.get("name")): d for d in data if isinstance(d, dict)}
            if categories:
                cats = set(c.lower() for c in categories)
                data = {k: v for k, v in data.items() if cats & set(c.lower() for c in v.get("categories", []))}
        else:
            data = await self.assets(t, categories)
        out = []
        for aid, info in list(data.items())[: max(1, limit)]:
            out.append({
                "id": aid,
                "name": info.get("name", aid),
                "type": {0: "hdris", 1: "textures", 2: "models"}.get(info.get("type"), info.get("type")),
                "categories": info.get("categories", []),
                "tags": info.get("tags", [])[:8],
                "download_count": info.get("download_count"),
                "thumbnail_url": info.get("thumbnail_url"),
            })
        return out

    async def files(self, asset_id: str) -> Dict[str, Any]:
        return await self._get("/files/%s" % asset_id)

    @staticmethod
    def pick_resolution(entry: Dict[str, Any], resolution: str | None) -> str:
        keys = [k for k in entry if isinstance(entry[k], dict)]
        if not keys:
            raise ProviderError("no resolutions available in %s" % list(entry))
        if resolution and resolution in entry:
            return resolution
        order = sorted(keys, key=lambda k: int("".join(ch for ch in k if ch.isdigit()) or 0))
        if resolution:
            want = int("".join(ch for ch in resolution if ch.isdigit()) or 0)
            best = [k for k in order if int("".join(ch for ch in k if ch.isdigit()) or 0) <= want]
            return best[-1] if best else order[0]
        return order[len(order) // 2] if len(order) > 2 else order[0]

    async def download_hdri(self, asset_id: str, resolution: str | None = "2k", fmt: str = "hdr", folder: str | None = None) -> Dict[str, Any]:
        files = await self.files(asset_id)
        entry = files.get("hdri")
        if not entry:
            raise ProviderError("%s is not an HDRI (files keys: %s)" % (asset_id, list(files)))
        res = self.pick_resolution(entry, resolution)
        fmt = (fmt or "hdr").lower()
        variants = entry[res]
        if fmt not in variants:
            fmt = "hdr" if "hdr" in variants else next(iter(variants))
        url = variants[fmt]["url"]
        folder = folder or os.path.join(download_dir(), "polyhaven", safe_name(asset_id))
        path = await download_url(url, os.path.join(folder, "%s_%s.%s" % (safe_name(asset_id), res, safe_name(fmt))))
        return {"asset_id": asset_id, "type": "hdris", "resolution": res, "format": fmt, "path": path, "url": url}

    async def download_texture_set(self, asset_id: str, resolution: str | None = "2k", fmt: str = "jpg", folder: str | None = None, maps: List[str] | None = None) -> Dict[str, Any]:
        files = await self.files(asset_id)
        folder = folder or os.path.join(download_dir(), "polyhaven", safe_name(asset_id))
        result: Dict[str, str] = {}
        res_used = None
        for map_name, entry in files.items():
            if not isinstance(entry, dict) or map_name in ("hdri", "blend", "gltf", "fbx", "usd"):
                continue
            alias = TEXTURE_MAP_ALIASES.get(map_name)
            if not alias:
                continue
            if maps and alias not in maps and map_name not in maps:
                continue
            if alias in result:
                continue
            try:
                res = self.pick_resolution(entry, resolution)
            except ProviderError:
                continue
            variants = entry[res]
            f = fmt.lower()
            if f not in variants:
                # Displacement and normal maps look bad as jpg; fall back to whatever exists.
                f = "png" if "png" in variants else ("exr" if "exr" in variants else next(iter(variants)))
            url = variants[f]["url"]
            path = await download_url(url, os.path.join(folder, "%s_%s_%s.%s" % (safe_name(asset_id), safe_name(alias), res, safe_name(f))))
            result[alias] = path
            res_used = res
        if not result:
            raise ProviderError("%s has no texture maps (files keys: %s)" % (asset_id, list(files)))
        return {"asset_id": asset_id, "type": "textures", "resolution": res_used, "maps": result, "folder": folder}

    async def download_model(self, asset_id: str, resolution: str | None = "2k", fmt: str = "fbx", folder: str | None = None) -> Dict[str, Any]:
        files = await self.files(asset_id)
        fmt = (fmt or "fbx").lower()
        entry = files.get(fmt)
        if not entry:
            available = [k for k in ("fbx", "gltf", "blend", "usd") if k in files]
            raise ProviderError("%s has no %r download (available: %s)" % (asset_id, fmt, available))
        res = self.pick_resolution(entry, resolution)
        variant = entry[res].get(fmt) or next(iter(entry[res].values()))
        folder = folder or os.path.join(download_dir(), "polyhaven", safe_name(asset_id))
        main_url = variant["url"]
        ext = os.path.splitext(main_url.split("?")[0])[1] or "." + fmt
        main_path = await download_url(main_url, os.path.join(folder, "%s_%s%s" % (safe_name(asset_id), res, safe_name(ext))))
        textures: List[str] = []
        for rel, info in (variant.get("include") or {}).items():
            url = info.get("url") if isinstance(info, dict) else None
            if not url:
                continue
            dest = _inside(folder, rel.replace("\\", "/"))
            if dest is None:
                continue  # the API named a path outside the asset folder, never write there
            await download_url(url, dest)
            textures.append(dest)
        if ext == ".zip":
            with zipfile.ZipFile(main_path) as zf:
                zf.extractall(folder)
            inner = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if f.lower().endswith("." + fmt)]
            if inner:
                main_path = inner[0]
        return {"asset_id": asset_id, "type": "models", "resolution": res, "format": fmt, "path": main_path, "textures": textures, "folder": folder}


# Sketchfab ----------------------------------------------------------------
class Sketchfab:
    name = "sketchfab"
    key_env = "SKETCHFAB_API_TOKEN"

    def __init__(self, token: str | None = None, base: str = SKETCHFAB_BASE) -> None:
        self._token = token
        self.base = base

    @property
    def token(self) -> str | None:
        return self._token or env_key(self.key_env)

    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Token %s" % self.token} if self.token else {}

    def require_token(self) -> None:
        if not self.token:
            raise ProviderError("Sketchfab needs an API token for this call. Set SKETCHFAB_API_TOKEN (Sketchfab settings > Password & API).")

    async def _get(self, path: str, params: Dict[str, Any] | None = None, auth: bool = False) -> Any:
        if auth:
            self.require_token()
        async with http(headers=self._headers()) as client:
            resp = await client.get(self.base + path, params={k: v for k, v in (params or {}).items() if v not in (None, "")})
        raise_for_status(resp, "Sketchfab", self.key_env)
        return resp.json()

    async def search(self, query: str, count: int = 20, cursor: str | None = None, license: str | None = None, downloadable: bool = True, categories: str | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"type": "models", "q": query, "count": count, "cursor": cursor, "sort_by": "-likeCount", "archives_flavours": "false"}
        if downloadable:
            params["downloadable"] = "true"
        if license:
            params["license"] = license
        if categories:
            params["categories"] = categories
        data = await self._get("/search", params)
        results = []
        for m in data.get("results", []):
            thumbs = ((m.get("thumbnails") or {}).get("images") or [])
            thumb = None
            if thumbs:
                thumb = sorted(thumbs, key=lambda t: abs((t.get("width") or 0) - 512))[0].get("url")
            results.append({
                "uid": m.get("uid"),
                "name": m.get("name"),
                "author": (m.get("user") or {}).get("displayName") or (m.get("user") or {}).get("username"),
                "license": (m.get("license") or {}).get("label") if isinstance(m.get("license"), dict) else m.get("license"),
                "face_count": m.get("faceCount"),
                "vertex_count": m.get("vertexCount"),
                "downloadable": m.get("isDownloadable"),
                "animated": m.get("animationCount", 0) > 0,
                "thumbnail_url": thumb,
                "viewer_url": m.get("viewerUrl"),
            })
        nxt = data.get("next")
        cursor_next = None
        if nxt and "cursor=" in nxt:
            cursor_next = nxt.split("cursor=", 1)[1].split("&", 1)[0]
        return {"results": results, "next_cursor": cursor_next}

    async def model(self, uid: str) -> Dict[str, Any]:
        return await self._get("/models/%s" % uid)

    async def thumbnail_bytes(self, uid: str, max_width: int = 1024) -> tuple:
        info = await self.model(uid)
        images = (info.get("thumbnails") or {}).get("images") or []
        if not images:
            raise ProviderError("model %s has no thumbnails" % uid)
        best = sorted([i for i in images if (i.get("width") or 0) <= max_width] or images, key=lambda i: -(i.get("width") or 0))[0]
        async with http() as client:
            resp = await client.get(best["url"])
        raise_for_status(resp, "Sketchfab thumbnail", "")
        ctype = resp.headers.get("content-type", "image/jpeg")
        fmt = "png" if "png" in ctype else "jpeg"
        return resp.content, fmt, info

    async def download_links(self, uid: str) -> Dict[str, Any]:
        return await self._get("/models/%s/download" % uid, auth=True)

    async def download(self, uid: str, fmt: str = "glb", folder: str | None = None) -> Dict[str, Any]:
        fmt = (fmt or "glb").lower()
        links = await self.download_links(uid)
        entry = links.get(fmt)
        if not entry or not entry.get("url"):
            available = [k for k, v in links.items() if isinstance(v, dict) and v.get("url")]
            raise ProviderError("Sketchfab model %s has no %r download (available: %s). The model may not be downloadable." % (uid, fmt, available))
        folder = folder or os.path.join(download_dir(), "sketchfab", safe_name(uid))
        url = entry["url"]
        ext = ".zip" if fmt in ("gltf", "source") else "." + fmt
        archive = await download_url(url, os.path.join(folder, "%s_%s%s" % (safe_name(uid), safe_name(fmt), ext)))
        result: Dict[str, Any] = {"uid": uid, "format": fmt, "path": archive, "folder": folder, "size": entry.get("size")}
        if ext == ".zip" or zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(folder)
            found: List[str] = []
            for r, _, fs in os.walk(folder):
                for f in fs:
                    if f.lower().endswith((".fbx", ".obj", ".gltf", ".glb", ".usdz", ".dae", ".blend", ".ma", ".mb", ".abc")):
                        found.append(os.path.join(r, f))
            result["files"] = found
            for pref in (".fbx", ".obj", ".gltf", ".glb", ".usdz", ".abc", ".dae"):
                hit = [f for f in found if f.lower().endswith(pref)]
                if hit:
                    result["path"] = hit[0]
                    break
        return result


# Poly Pizza ----------------------------------------------------------------
class PolyPizza:
    name = "polypizza"
    key_env = "POLYPIZZA_API_KEY"

    def __init__(self, token: str | None = None, base: str = POLYPIZZA_BASE) -> None:
        self._token = token
        self.base = base

    @property
    def token(self) -> str | None:
        return self._token or env_key(self.key_env)

    def configured(self) -> bool:
        return bool(self.token)

    def require_token(self) -> None:
        if not self.token:
            raise ProviderError("Poly Pizza needs an API key. Set POLYPIZZA_API_KEY (free at https://poly.pizza/settings/api).")

    async def _get(self, path: str, params: Dict[str, Any] | None = None) -> Any:
        self.require_token()
        async with http(headers={"x-auth-token": self.token or ""}) as client:
            resp = await client.get(self.base + path, params=params)
        raise_for_status(resp, "Poly Pizza", self.key_env)
        return resp.json()

    @staticmethod
    def _shape(m: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": m.get("ID") or m.get("id"),
            "title": m.get("Title"),
            "download_url": m.get("Download"),
            "attribution": m.get("Attribution"),
            "creator": (m.get("Creator") or {}).get("Username") if isinstance(m.get("Creator"), dict) else m.get("Creator"),
            "tri_count": m.get("TriCount"),
            "licence": m.get("Licence"),
            "thumbnail_url": m.get("Thumbnail"),
            "animated": m.get("Animated"),
        }

    async def search(self, keyword: str, limit: int = 20, page: int = 0) -> Dict[str, Any]:
        data = await self._get("/search/%s" % keyword, {"limit": limit, "page": page})
        results = data.get("results", data if isinstance(data, list) else [])
        return {"results": [self._shape(m) for m in results], "total": data.get("total") if isinstance(data, dict) else None}

    async def model(self, model_id: str) -> Dict[str, Any]:
        return self._shape(await self._get("/model/%s" % model_id))

    async def download(self, model_id: str, folder: str | None = None) -> Dict[str, Any]:
        info = await self.model(model_id)
        url = info.get("download_url")
        if not url:
            raise ProviderError("Poly Pizza model %s has no download url" % model_id)
        folder = folder or os.path.join(download_dir(), "polypizza")
        path = await download_url(url, os.path.join(folder, "%s.glb" % safe_name(model_id)))
        info["path"] = path
        return info
