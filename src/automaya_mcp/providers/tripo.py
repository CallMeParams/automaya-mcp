"""Tripo 3D (https://api.tripo3d.ai/v2/openapi). Bearer TRIPO_API_KEY.

Every operation is a task: text_to_model, image_to_model, convert_model,
animate_rig, texture_model. Responses are wrapped in ``{"code": 0, "data": {...}}``.
Output URLs are signed and expire after a few minutes, so download promptly.
"""
from __future__ import annotations

from typing import Any, Dict

from .base import GenJob, Provider3D, ProviderError, env_key, http, is_url, map_status, raise_for_status, read_image

BASE = "https://api.tripo3d.ai/v2/openapi"
MODEL_VERSION = "P1-20260311"
RIG_VERSION = "v2.5-20260210"
TEXTURE_VERSION = "v3.0-20250812"

_STATUS = {
    "queued": "queued",
    "running": "running",
    "success": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "banned": "failed",
    "expired": "failed",
    "unknown": "running",
}


class TripoProvider(Provider3D):
    name = "tripo"
    key_env = "TRIPO_API_KEY"
    display_name = "Tripo 3D"

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
            "remesh": False,
            "convert": True,
            "formats": ["glb", "fbx", "obj", "usdz", "gltf"],
        }

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % self.api_key}

    async def _post_task(self, payload: Dict[str, Any]) -> str:
        self.require_configured()
        async with http(headers=self._headers()) as client:
            resp = await client.post(BASE + "/task", json=payload)
        raise_for_status(resp, self.display_name, self.key_env)
        data = _unwrap(resp.json())
        task_id = data.get("task_id")
        if not task_id:
            raise ProviderError("Tripo did not return a task_id: %s" % resp.text[:300], provider=self.name)
        return str(task_id)

    async def _upload(self, path: str) -> str:
        data, mime, filename = read_image(path)
        async with http(timeout=120.0, headers=self._headers()) as client:
            resp = await client.post(BASE + "/upload", files={"file": (filename, data, mime)})
        raise_for_status(resp, self.display_name, self.key_env)
        body = _unwrap(resp.json())
        token = body.get("image_token")
        if not token:
            raise ProviderError("Tripo upload did not return image_token: %s" % resp.text[:300], provider=self.name)
        return str(token)

    def _common(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model_version": opts.get("model_version", MODEL_VERSION)}
        if opts.get("face_limit"):
            payload["face_limit"] = int(opts["face_limit"])
        if "texture" in opts and opts["texture"] is not None:
            payload["texture"] = bool(opts["texture"])
        if "pbr" in opts and opts["pbr"] is not None:
            payload["pbr"] = bool(opts["pbr"])
        if opts.get("quality"):
            q = str(opts["quality"]).lower()
            payload["texture_quality"] = "detailed" if q in ("high", "detailed") else "standard"
        if opts.get("auto_size") is not None:
            payload["auto_size"] = bool(opts["auto_size"])
        return payload

    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        payload = {"type": "text_to_model", "prompt": prompt, **self._common(opts)}
        if opts.get("negative_prompt"):
            payload["negative_prompt"] = opts["negative_prompt"]
        task_id = await self._post_task(payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="text_to_model submitted")

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {"type": "image_to_model", **self._common(opts)}
        if is_url(image_path_or_url):
            payload["file"] = {"type": _ext(image_path_or_url), "url": image_path_or_url}
        else:
            token = await self._upload(image_path_or_url)
            payload["file"] = {"type": _ext(image_path_or_url), "file_token": token}
        task_id = await self._post_task(payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="image_to_model submitted")

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        async with http(headers=self._headers()) as client:
            resp = await client.get(BASE + "/task/%s" % job_id)
        raise_for_status(resp, self.display_name, self.key_env)
        data = _unwrap(resp.json())
        status = map_status(data.get("status"), _STATUS)
        output = data.get("output") or {}
        outputs: Dict[str, str] = {}
        # The main model url is a glb unless a convert task produced another format.
        model_url = output.get("pbr_model") or output.get("model") or output.get("base_model")
        if model_url:
            outputs[_fmt_from_url(model_url, data)] = model_url
        if output.get("base_model") and "base_glb" not in outputs and output.get("base_model") != model_url:
            outputs["base_glb"] = output["base_model"]
        if output.get("rendered_image"):
            outputs["image"] = output["rendered_image"]
        progress = int(data.get("progress") or (100 if status == "succeeded" else 0))
        message = data.get("status", "")
        if status == "failed":
            message = "Tripo task %s: %s" % (data.get("status"), data.get("error") or data.get("message") or "no details")
        return GenJob(
            provider=self.name,
            job_id=job_id,
            status=status,
            progress=progress,
            message=str(message),
            outputs=outputs,
            thumbnail_url=output.get("rendered_image"),
            raw=data,
        )

    async def convert(self, job_id: str, fmt: str, **opts: Any) -> GenJob:
        fmt_up = fmt.upper()
        if fmt_up == "GLB":
            fmt_up = "GLTF"
        if fmt_up not in ("FBX", "OBJ", "USDZ", "GLTF", "STL", "3MF"):
            raise ProviderError("Tripo convert supports FBX, OBJ, USDZ, GLTF, STL, 3MF (got %r)" % fmt, provider=self.name)
        payload: Dict[str, Any] = {"type": "convert_model", "format": fmt_up, "original_model_task_id": job_id}
        if opts.get("quad") is not None:
            payload["quad"] = bool(opts["quad"])
        if opts.get("face_limit"):
            payload["face_limit"] = int(opts["face_limit"])
        if opts.get("fbx_preset"):
            payload["fbx_preset"] = opts["fbx_preset"]
        task_id = await self._post_task(payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="convert_model to %s submitted" % fmt_up, raw={"format": fmt.lower(), "source_task": job_id})

    async def rig(self, job_id: str, **opts: Any) -> GenJob:
        payload = {
            "type": "animate_rig",
            "model_version": opts.get("model_version", RIG_VERSION),
            "original_model_task_id": job_id,
            "out_format": opts.get("out_format", "fbx"),
            "rig_type": opts.get("rig_type", "biped"),
            "spec": opts.get("spec", "mixamo"),
        }
        task_id = await self._post_task(payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="animate_rig submitted", raw={"format": payload["out_format"], "source_task": job_id})

    async def retexture(self, job_id: str, **opts: Any) -> GenJob:
        prompt = opts.get("prompt") or opts.get("text")
        payload: Dict[str, Any] = {"type": "texture_model", "model_version": opts.get("model_version", TEXTURE_VERSION), "original_model_task_id": job_id}
        if prompt:
            payload["texture_prompt"] = {"text": prompt}
        if opts.get("pbr") is not None:
            payload["pbr"] = bool(opts["pbr"])
        task_id = await self._post_task(payload)
        return GenJob(provider=self.name, job_id=task_id, status="queued", message="texture_model submitted", raw={"source_task": job_id})


def _unwrap(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ProviderError("Tripo returned a non JSON-object body", provider="tripo")
    code = body.get("code", 0)
    if code not in (0, "0"):
        msg = body.get("message") or body.get("suggestion") or str(body)[:300]
        if code in (2010,) or "balance" in str(msg).lower():
            raise ProviderError("Tripo reports insufficient credits: %s" % msg, status=402, provider="tripo")
        raise ProviderError("Tripo error %s: %s" % (code, msg), provider="tripo")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _ext(path_or_url: str) -> str:
    low = path_or_url.lower().split("?")[0]
    for ext in ("png", "jpg", "jpeg", "webp"):
        if low.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    return "png"


def _fmt_from_url(url: str, data: Dict[str, Any]) -> str:
    low = url.lower().split("?")[0]
    for fmt in ("fbx", "obj", "usdz", "gltf", "glb", "stl"):
        if low.endswith("." + fmt) or low.endswith(".%s.zip" % fmt):
            return fmt
    inp = data.get("input") or {}
    if isinstance(inp, dict) and inp.get("format"):
        f = str(inp["format"]).lower()
        return "glb" if f == "gltf" else f
    if str(data.get("type", "")) == "animate_rig":
        return "fbx"
    return "glb"
