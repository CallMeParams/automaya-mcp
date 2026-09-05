"""Meshy (https://api.meshy.ai). Bearer MESHY_API_KEY.

Text to 3D is two stage: ``preview`` (geometry) then ``refine`` (textures).
``submit_text`` returns the preview job. When ``auto_refine`` is on (default)
``poll`` submits the refine task as soon as the preview succeeds and from then
on reports the refine task under the original job id, so the caller keeps a
single handle. The mapping lives in ``_REFINE`` for the life of the server.
"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx

from .base import GenJob, Provider3D, ProviderError, env_key, http, image_data_uri, is_url, map_status, raise_for_status

BASE = "https://api.meshy.ai"
TEXT_PATH = "/openapi/v2/text-to-3d"
IMAGE_PATH = "/openapi/v1/image-to-3d"
RETEXTURE_PATH = "/openapi/v1/retexture"
REMESH_PATH = "/openapi/v1/remesh"
RIG_PATH = "/openapi/v1/rigging"
ANIM_PATH = "/openapi/v1/animations"
DEFAULT_MODEL = "meshy-7"
DEFAULT_FORMATS = ["glb", "fbx", "obj", "usdz"]

_STATUS = {"PENDING": "queued", "IN_PROGRESS": "running", "SUCCEEDED": "succeeded", "FAILED": "failed", "CANCELED": "cancelled", "CANCELLED": "cancelled", "EXPIRED": "failed"}

# job_id -> endpoint path used to poll it
_KIND: Dict[str, str] = {}
# preview job id -> {"refine_id": str|None, "opts": dict}
_REFINE: Dict[str, Dict[str, Any]] = {}
_POLL_ORDER = [TEXT_PATH, IMAGE_PATH, RETEXTURE_PATH, REMESH_PATH, RIG_PATH, ANIM_PATH]


class MeshyProvider(Provider3D):
    name = "meshy"
    key_env = "MESHY_API_KEY"
    display_name = "Meshy"

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key

    @property
    def api_key(self) -> str | None:
        return self._key or env_key(self.key_env)

    def configured(self) -> bool:
        return bool(self.api_key)

    def capabilities(self) -> Dict[str, Any]:
        return {
            "text_to_3d": True,
            "image_to_3d": True,
            "multiview": True,
            "rig": True,
            "retexture": True,
            "remesh": True,
            "convert": False,
            "formats": list(DEFAULT_FORMATS),
        }

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % self.api_key}

    async def _post(self, path: str, payload: Dict[str, Any]) -> str:
        self.require_configured()
        async with http(headers=self._headers()) as client:
            resp = await client.post(BASE + path, json=payload)
        raise_for_status(resp, self.display_name, self.key_env)
        body = resp.json()
        result = body.get("result") if isinstance(body, dict) else None
        if not result:
            raise ProviderError("Meshy did not return a task id: %s" % resp.text[:300], provider=self.name)
        task_id = str(result)
        _KIND[task_id] = path
        return task_id

    async def _get(self, path: str, job_id: str) -> Dict[str, Any]:
        async with http(headers=self._headers()) as client:
            resp = await client.get("%s%s/%s" % (BASE, path, job_id))
        raise_for_status(resp, self.display_name, self.key_env)
        body = resp.json()
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _gen_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if opts.get("topology"):
            out["topology"] = opts["topology"]
        if opts.get("face_limit") or opts.get("target_polycount"):
            out["target_polycount"] = int(opts.get("target_polycount") or opts["face_limit"])
        if opts.get("should_remesh") is not None:
            out["should_remesh"] = bool(opts["should_remesh"])
        fmts = opts.get("target_formats") or DEFAULT_FORMATS
        out["target_formats"] = list(fmts)
        return out

    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {"mode": "preview", "prompt": prompt, "ai_model": opts.get("ai_model", DEFAULT_MODEL), **self._gen_opts(opts)}
        if opts.get("negative_prompt"):
            payload["negative_prompt"] = opts["negative_prompt"]
        if opts.get("model_type"):
            payload["model_type"] = opts["model_type"]
        if opts.get("art_style"):
            payload["art_style"] = opts["art_style"]
        task_id = await self._post(TEXT_PATH, payload)
        auto_refine = opts.get("auto_refine", True)
        _REFINE[task_id] = {
            "refine_id": None,
            "auto": bool(auto_refine),
            "opts": {
                "enable_pbr": opts.get("pbr", opts.get("enable_pbr", True)),
                "texture_resolution": opts.get("texture_resolution") or ("2048" if str(opts.get("quality", "")).lower() in ("high", "detailed") else None),
                "ai_model": payload["ai_model"],
                "target_formats": payload["target_formats"],
            },
        }
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="preview submitted (auto_refine=%s)" % auto_refine, raw={"stage": "preview"})

    async def submit_refine(self, preview_task_id: str, **opts: Any) -> str:
        payload: Dict[str, Any] = {"mode": "refine", "preview_task_id": preview_task_id}
        if opts.get("enable_pbr") is not None:
            payload["enable_pbr"] = bool(opts["enable_pbr"])
        if opts.get("texture_resolution"):
            payload["texture_resolution"] = str(opts["texture_resolution"])
        if opts.get("ai_model"):
            payload["ai_model"] = opts["ai_model"]
        if opts.get("target_formats"):
            payload["target_formats"] = list(opts["target_formats"])
        return await self._post(TEXT_PATH, payload)

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        image_url = image_path_or_url if is_url(image_path_or_url) else image_data_uri(image_path_or_url)
        payload: Dict[str, Any] = {
            "image_url": image_url,
            "ai_model": opts.get("ai_model", DEFAULT_MODEL),
            "should_texture": opts.get("texture", True),
            "enable_pbr": opts.get("pbr", opts.get("enable_pbr", True)),
            **self._gen_opts(opts),
        }
        if opts.get("texture_prompt"):
            payload["texture_prompt"] = opts["texture_prompt"]
        task_id = await self._post(IMAGE_PATH, payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="image-to-3d submitted")

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        link = _REFINE.get(job_id)
        if link and link.get("refine_id"):
            job = await self._poll_raw(link["refine_id"], TEXT_PATH)
            job.job_id = job_id
            job.raw["refine_task_id"] = link["refine_id"]
            job.raw["stage"] = "refine"
            job.message = "refine: " + job.message
            return job
        job = await self._poll_raw(job_id, _KIND.get(job_id))
        if link and link.get("auto") and job.status == "succeeded" and not link.get("refine_id"):
            refine_id = await self.submit_refine(job_id, **link["opts"])
            link["refine_id"] = refine_id
            job.status = "running"
            job.progress = 50
            job.message = "preview done, refine submitted (%s)" % refine_id
            job.raw["refine_task_id"] = refine_id
            job.raw["stage"] = "refine"
        elif link:
            job.raw["stage"] = "preview"
        return job

    async def _poll_raw(self, job_id: str, path: str | None) -> GenJob:
        paths: List[str] = [path] if path else _POLL_ORDER
        data: Dict[str, Any] = {}
        last: ProviderError | None = None
        for p in paths:
            try:
                data = await self._get(p, job_id)
                _KIND[job_id] = p
                break
            except ProviderError as exc:
                if exc.status == 404 and len(paths) > 1:
                    last = exc
                    continue
                raise
        else:
            raise last or ProviderError("Meshy job %s not found" % job_id, provider=self.name)
        status = map_status(data.get("status"), _STATUS)
        outputs: Dict[str, str] = {}
        for k, v in (data.get("model_urls") or {}).items():
            if v:
                outputs[str(k).lower()] = v
        for key in ("rigged_character_fbx_url", "rigged_character_glb_url", "animation_fbx_url", "animation_glb_url"):
            if data.get(key):
                outputs["fbx" if "fbx" in key else "glb"] = data[key]
        if isinstance(data.get("result"), dict):
            for key, v in data["result"].items():
                if isinstance(v, str) and v.startswith("http"):
                    outputs["fbx" if "fbx" in key else "glb" if "glb" in key else key] = v
        textures = data.get("texture_urls")
        if isinstance(textures, list) and textures and isinstance(textures[0], dict):
            for k, v in textures[0].items():
                if v:
                    outputs["tex_%s" % k] = v
        message = str(data.get("status", ""))
        if status == "failed":
            err = data.get("task_error") or {}
            message = "Meshy task failed: %s" % (err.get("message") if isinstance(err, dict) else err or "no details")
        return GenJob(
            provider=self.name,
            job_id=job_id,
            status=status,
            progress=int(data.get("progress") or (100 if status == "succeeded" else 0)),
            message=message,
            outputs=outputs,
            thumbnail_url=data.get("thumbnail_url"),
            raw=data,
        )

    @staticmethod
    def _source(job_id: str, opts: Dict[str, Any]) -> Dict[str, Any]:
        if opts.get("model_url"):
            return {"model_url": opts["model_url"]}
        link = _REFINE.get(job_id)
        if link and link.get("refine_id"):
            return {"input_task_id": link["refine_id"]}
        return {"input_task_id": job_id}

    async def retexture(self, job_id: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {**self._source(job_id, opts), "enable_pbr": opts.get("pbr", True), "target_formats": opts.get("target_formats") or DEFAULT_FORMATS}
        if opts.get("prompt"):
            payload["text_style_prompt"] = opts["prompt"]
        if opts.get("image_style_url"):
            payload["image_style_url"] = opts["image_style_url"]
        if opts.get("ai_model"):
            payload["ai_model"] = opts["ai_model"]
        task_id = await self._post(RETEXTURE_PATH, payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="retexture submitted", raw={"source_task": job_id})

    async def remesh(self, job_id: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {**self._source(job_id, opts), "target_formats": opts.get("target_formats") or DEFAULT_FORMATS}
        if opts.get("topology"):
            payload["topology"] = opts["topology"]
        if opts.get("target_polycount") or opts.get("face_limit"):
            payload["target_polycount"] = int(opts.get("target_polycount") or opts["face_limit"])
        task_id = await self._post(REMESH_PATH, payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="remesh submitted", raw={"source_task": job_id})

    async def rig(self, job_id: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {**self._source(job_id, opts)}
        if opts.get("height_meters"):
            payload["height_meters"] = float(opts["height_meters"])
        task_id = await self._post(RIG_PATH, payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="rigging submitted", raw={"source_task": job_id})

    async def animate(self, rig_task_id: str, action_id: int, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {"rig_task_id": rig_task_id, "action_id": int(action_id)}
        task_id = await self._post(ANIM_PATH, payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="animation submitted", raw={"rig_task": rig_task_id})


def is_http_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.HTTPError, ProviderError))
