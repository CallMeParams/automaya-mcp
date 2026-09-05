"""Hyper3D Rodin. Two routes:

* main site: https://api.hyper3d.com/api/v2 with Bearer RODIN_API_KEY.
  POST /rodin (multipart) -> {uuid, jobs:{uuids, subscription_key}};
  POST /status {subscription_key} -> jobs[].status; POST /download {task_uuid}.
* fal.ai: https://queue.fal.run/fal-ai/hyper3d/rodin with ``Authorization: Key FAL_KEY``.
  FAL_KEY may be the literal "vibecoding" free trial key used by blender-mcp.

Job ids are prefixed ``main:`` or ``fal:`` so poll knows which route to use.
For the main route the id also carries the subscription key: ``main:<uuid>:<sub>``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import GenJob, Provider3D, ProviderError, env_key, http, image_data_uri, is_url, map_status, raise_for_status, read_image

MAIN_BASE = "https://api.hyper3d.com/api/v2"
FAL_BASE = "https://queue.fal.run/fal-ai/hyper3d"
FAL_TRIAL_KEY = "vibecoding"
DEFAULT_TIER = "Gen-2.5-Medium"

_MAIN_STATUS = {"Waiting": "queued", "Generating": "running", "Done": "succeeded", "Failed": "failed", "Cancelled": "cancelled", "Canceled": "cancelled"}
_FAL_STATUS = {"IN_QUEUE": "queued", "IN_PROGRESS": "running", "COMPLETED": "succeeded", "FAILED": "failed", "CANCELLED": "cancelled"}


class RodinProvider(Provider3D):
    name = "rodin"
    key_env = "RODIN_API_KEY or FAL_KEY"
    display_name = "Hyper3D Rodin"

    def __init__(self, api_key: str | None = None, fal_key: str | None = None, mode: str | None = None) -> None:
        self._key = api_key
        self._fal = fal_key
        self._mode = mode

    @property
    def main_key(self) -> str | None:
        return self._key or env_key("RODIN_API_KEY")

    @property
    def fal_key(self) -> str | None:
        return self._fal or env_key("FAL_KEY")

    @property
    def mode(self) -> str | None:
        if self._mode:
            return self._mode
        forced = env_key("RODIN_MODE")
        if forced in ("main", "fal"):
            return forced
        if self.main_key:
            return "main"
        if self.fal_key:
            return "fal"
        return None

    def configured(self) -> bool:
        return self.mode is not None

    def how_to_configure(self) -> str:
        return (
            "Set RODIN_API_KEY (hyper3d.com account) or FAL_KEY (fal.ai; the value 'vibecoding' enables the free trial route). "
            "Set RODIN_MODE=main|fal to force a route when both keys exist."
        )

    def capabilities(self) -> Dict[str, Any]:
        return {
            "text_to_3d": True,
            "image_to_3d": True,
            "multiview": True,
            "rig": False,
            "retexture": False,
            "remesh": False,
            "convert": False,
            "formats": ["glb", "fbx", "obj", "usdz"],
            "route": self.mode,
        }

    # main route -----------------------------------------------------------
    def _main_headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % self.main_key}

    async def _main_submit(self, prompt: str | None, images: List[str], opts: Dict[str, Any]) -> GenJob:
        data: Dict[str, Any] = {
            "tier": opts.get("tier", DEFAULT_TIER),
            "geometry_file_format": str(opts.get("format", "glb")).lower(),
            "material": opts.get("material", "PBR"),
            "quality": opts.get("quality", "medium"),
            "mesh_mode": "Quad" if opts.get("quad") else opts.get("mesh_mode", "Raw"),
        }
        if prompt:
            data["prompt"] = prompt
        if opts.get("seed") is not None:
            data["seed"] = int(opts["seed"])
        files: List[tuple] = []
        for img in images:
            if is_url(img):
                async with http(timeout=120.0) as client:
                    resp = await client.get(img)
                    raise_for_status(resp, "image fetch", "")
                    files.append(("images", (img.rsplit("/", 1)[-1] or "image.png", resp.content, resp.headers.get("content-type", "image/png"))))
            else:
                blob, mime, fname = read_image(img)
                files.append(("images", (fname, blob, mime)))
        async with http(timeout=180.0, headers=self._main_headers()) as client:
            resp = await client.post(MAIN_BASE + "/rodin", data=data, files=files or None)
        raise_for_status(resp, self.display_name, "RODIN_API_KEY")
        body = resp.json()
        if body.get("error"):
            raise ProviderError("Rodin error: %s" % body["error"], provider=self.name)
        uuid = body.get("uuid")
        sub = (body.get("jobs") or {}).get("subscription_key")
        if not uuid or not sub:
            raise ProviderError("Rodin did not return uuid/subscription_key: %s" % resp.text[:300], provider=self.name)
        job_id = "main:%s:%s" % (uuid, sub)
        return GenJob(provider=self.name, job_id=job_id, status="queued", message="submitted via hyper3d.com", raw={"uuid": uuid, "format": data["geometry_file_format"]})

    async def _main_poll(self, uuid: str, sub: str) -> GenJob:
        job_id = "main:%s:%s" % (uuid, sub)
        async with http(headers=self._main_headers()) as client:
            resp = await client.post(MAIN_BASE + "/status", json={"subscription_key": sub})
        raise_for_status(resp, self.display_name, "RODIN_API_KEY")
        jobs = (resp.json() or {}).get("jobs") or []
        statuses = [map_status(j.get("status"), _MAIN_STATUS) for j in jobs]
        if not statuses:
            status = "queued"
        elif any(s == "failed" for s in statuses):
            status = "failed"
        elif all(s == "succeeded" for s in statuses):
            status = "succeeded"
        elif any(s == "running" for s in statuses):
            status = "running"
        else:
            status = "queued"
        done = sum(1 for s in statuses if s == "succeeded")
        progress = int(100 * done / len(statuses)) if statuses else 0
        outputs: Dict[str, str] = {}
        thumb = None
        if status == "succeeded":
            async with http(headers=self._main_headers()) as client:
                dl = await client.post(MAIN_BASE + "/download", json={"task_uuid": uuid})
            raise_for_status(dl, self.display_name, "RODIN_API_KEY")
            items = (dl.json() or {}).get("list") or []
            for item in items:
                name = str(item.get("name", "")).lower()
                url = item.get("url")
                if not url:
                    continue
                ext = name.rsplit(".", 1)[-1] if "." in name else ""
                if ext in ("glb", "fbx", "obj", "usdz", "gltf", "stl"):
                    outputs[ext] = url
                elif ext in ("png", "jpg", "jpeg", "webp"):
                    thumb = thumb or url
                    outputs.setdefault("image", url)
                else:
                    outputs[name or "file"] = url
        return GenJob(provider=self.name, job_id=job_id, status=status, progress=progress, message=", ".join(str(j.get("status")) for j in jobs), outputs=outputs, thumbnail_url=thumb, raw={"jobs": jobs})

    # fal route ------------------------------------------------------------
    def _fal_headers(self) -> Dict[str, str]:
        return {"Authorization": "Key %s" % self.fal_key}

    async def _fal_submit(self, prompt: str | None, images: List[str], opts: Dict[str, Any]) -> GenJob:
        payload: Dict[str, Any] = {
            "tier": opts.get("tier", DEFAULT_TIER),
            "geometry_file_format": str(opts.get("format", "glb")).lower(),
            "material": opts.get("material", "PBR"),
            "quality": opts.get("quality", "medium"),
        }
        if prompt:
            payload["prompt"] = prompt
        if images:
            urls: List[str] = []
            for img in images:
                urls.append(img if is_url(img) else image_data_uri(img))
            payload["input_image_urls"] = urls
        if opts.get("quad"):
            payload["use_quad"] = True
        async with http(timeout=120.0, headers=self._fal_headers()) as client:
            resp = await client.post(FAL_BASE + "/rodin", json=payload)
        raise_for_status(resp, self.display_name + " (fal)", "FAL_KEY")
        body = resp.json()
        rid = body.get("request_id")
        if not rid:
            raise ProviderError("fal did not return request_id: %s" % resp.text[:300], provider=self.name)
        return GenJob(provider=self.name, job_id="fal:%s" % rid, status="queued", message="submitted via fal.ai", raw={"request_id": rid, "format": payload["geometry_file_format"]})

    async def _fal_poll(self, rid: str) -> GenJob:
        job_id = "fal:%s" % rid
        async with http(headers=self._fal_headers()) as client:
            resp = await client.get("%s/requests/%s/status" % (FAL_BASE, rid))
            raise_for_status(resp, self.display_name + " (fal)", "FAL_KEY")
            body = resp.json() or {}
            status = map_status(body.get("status"), _FAL_STATUS)
            outputs: Dict[str, str] = {}
            thumb = None
            if status == "succeeded":
                res = await client.get("%s/requests/%s" % (FAL_BASE, rid))
                raise_for_status(res, self.display_name + " (fal)", "FAL_KEY")
                data = res.json() or {}
                mesh = data.get("model_mesh") or {}
                url = mesh.get("url")
                if url:
                    tail = url.split("?")[0].rsplit("/", 1)[-1]
                    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else "glb"
                    outputs[ext] = url
                for tex in data.get("textures") or []:
                    if isinstance(tex, dict) and tex.get("url"):
                        thumb = thumb or tex["url"]
                        outputs["tex_%s" % (tex.get("file_name") or len(outputs))] = tex["url"]
                body = {**body, "result": data}
        return GenJob(provider=self.name, job_id=job_id, status=status, progress=100 if status == "succeeded" else (10 if status == "queued" else 50), message=str(body.get("status", "")), outputs=outputs, thumbnail_url=thumb, raw=body)

    # public ---------------------------------------------------------------
    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        self.require_configured()
        if self.mode == "main":
            return await self._main_submit(prompt, [], opts)
        return await self._fal_submit(prompt, [], opts)

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        self.require_configured()
        images = [image_path_or_url] + list(opts.get("extra_images") or [])
        prompt = opts.get("prompt")
        if self.mode == "main":
            return await self._main_submit(prompt, images, opts)
        return await self._fal_submit(prompt, images, opts)

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        if job_id.startswith("main:"):
            parts = job_id.split(":", 2)
            if len(parts) != 3:
                raise ProviderError("malformed Rodin job id %r (expected main:<uuid>:<subscription_key>)" % job_id, provider=self.name)
            return await self._main_poll(parts[1], parts[2])
        if job_id.startswith("fal:"):
            return await self._fal_poll(job_id[4:])
        # Bare id: assume the active route.
        if self.mode == "fal":
            return await self._fal_poll(job_id)
        raise ProviderError("Rodin job ids look like main:<uuid>:<key> or fal:<request_id>; got %r" % job_id, provider=self.name)
