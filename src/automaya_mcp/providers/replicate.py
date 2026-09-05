"""Replicate (https://api.replicate.com/v1). Header ``Authorization: Bearer REPLICATE_API_TOKEN``.

Any hosted 3D model works through the same three calls:
``POST /models/{owner}/{name}/predictions`` with ``Prefer: wait=0`` and
``{"input": {...}}`` -> prediction ``{id, status, output}``; then
``GET /predictions/{id}`` until status is succeeded | failed | canceled.
``output`` is a URL, a list of URLs or a dict of named URLs depending on the
model; every URL that looks like a model file becomes an output format.

The default model is ``tencent/hunyuan-3d-3.1`` (inputs ``image`` and
``prompt``). Pass ``model="owner/name"`` in the options to use another one,
and ``extra`` keys go straight into the prediction input.
"""
from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import urlparse

from .base import GenJob, Provider3D, ProviderError, env_key, http, image_data_uri, is_url, map_status, raise_for_status

BASE = "https://api.replicate.com/v1"
DEFAULT_MODEL = "tencent/hunyuan-3d-3.1"
_STATUS = {"starting": "queued", "processing": "running", "succeeded": "succeeded", "failed": "failed", "canceled": "cancelled", "cancelled": "cancelled"}
_MODEL_EXTS = ("glb", "gltf", "fbx", "obj", "usdz", "usd", "stl", "zip", "ply")
_IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif")


def split_model(model: str) -> tuple:
    """'owner/name' or 'owner/name:version' -> (owner, name, version)."""
    ref = (model or DEFAULT_MODEL).strip()
    version = None
    if ":" in ref:
        ref, version = ref.split(":", 1)
    if "/" not in ref:
        raise ProviderError("Replicate model must be 'owner/name', got %r" % model, provider="replicate")
    owner, name = ref.split("/", 1)
    return owner, name, version


def _ext_of(url: str) -> str:
    path = urlparse(url).path if isinstance(url, str) else ""
    return os.path.splitext(path)[1].lstrip(".").lower()


def outputs_from(output: Any) -> tuple:
    """Turn a prediction output (str | list | dict) into ({format: url}, thumbnail)."""
    outputs: Dict[str, str] = {}
    thumb = None

    def add(url: str, hint: str = "") -> None:
        nonlocal thumb
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return
        ext = _ext_of(url)
        if ext in _IMAGE_EXTS:
            thumb = thumb or url
            outputs.setdefault("image", url)
        elif ext in _MODEL_EXTS:
            outputs.setdefault(ext, url)
        elif hint:
            outputs.setdefault(hint.lower(), url)
        else:
            outputs.setdefault("file", url)

    if isinstance(output, str):
        add(output)
    elif isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                for k, v in item.items():
                    add(v, k)
            else:
                add(item)
    elif isinstance(output, dict):
        for k, v in output.items():
            if isinstance(v, list):
                for item in v:
                    add(item, k)
            else:
                add(v, k)
    return outputs, thumb


class ReplicateProvider(Provider3D):
    name = "replicate"
    key_env = "REPLICATE_API_TOKEN"
    display_name = "Replicate"

    def __init__(self, token: str | None = None, model: str | None = None) -> None:
        self._token = token
        self._model = model

    @property
    def token(self) -> str | None:
        return self._token or env_key("REPLICATE_API_TOKEN")

    @property
    def default_model(self) -> str:
        return self._model or env_key("REPLICATE_3D_MODEL") or DEFAULT_MODEL

    def configured(self) -> bool:
        return bool(self.token)

    def how_to_configure(self) -> str:
        return "Set REPLICATE_API_TOKEN (https://replicate.com/account/api-tokens). Optional REPLICATE_3D_MODEL picks the default model (default %s)." % DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        return {
            "text_to_3d": True,
            "image_to_3d": True,
            "multiview": False,
            "rig": False,
            "retexture": False,
            "remesh": False,
            "convert": False,
            "formats": ["glb"],
            "model": self.default_model,
            "note": "any hosted 3D model via options.extra model='owner/name'; outputs depend on the model",
        }

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % self.token, "Content-Type": "application/json"}

    def _job(self, data: Dict[str, Any]) -> GenJob:
        status = map_status(data.get("status"), _STATUS, default="running")
        outputs, thumb = outputs_from(data.get("output"))
        message = str(data.get("error") or data.get("status") or "")
        if status == "failed" and data.get("error"):
            message = "Replicate prediction failed: %s" % data["error"]
        progress = 100 if status == "succeeded" else (50 if status == "running" else 0)
        raw = {k: v for k, v in data.items() if k != "logs"}
        return GenJob(provider=self.name, job_id=str(data.get("id", "")), status=status, progress=progress, message=message, outputs=outputs, thumbnail_url=thumb, raw=raw)

    async def _create(self, model: str, inputs: Dict[str, Any]) -> GenJob:
        self.require_configured()
        owner, name, version = split_model(model)
        body: Dict[str, Any] = {"input": inputs}
        if version:
            url = "%s/predictions" % BASE
            body["version"] = version
        else:
            url = "%s/models/%s/%s/predictions" % (BASE, owner, name)
        headers = self._headers()
        headers["Prefer"] = "wait=0"
        async with http(timeout=90.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        raise_for_status(resp, self.display_name, self.key_env)
        data = resp.json() or {}
        if not data.get("id"):
            raise ProviderError("Replicate did not return a prediction id: %s" % resp.text[:300], provider=self.name)
        job = self._job(data)
        job.raw["model"] = model
        if not job.message:
            job.message = "submitted %s" % model
        return job

    @staticmethod
    def _inputs(opts: Dict[str, Any]) -> tuple:
        model = str(opts.pop("model", "") or "")
        inputs: Dict[str, Any] = {}
        for key in ("format", "quality", "pbr", "texture", "face_limit", "negative_prompt", "quad", "auto_refine", "extra_images", "model_version"):
            opts.pop(key, None)
        inputs.update(opts)
        return model, inputs

    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        model, inputs = self._inputs(dict(opts))
        inputs["prompt"] = prompt
        return await self._create(model or self.default_model, inputs)

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        model, inputs = self._inputs(dict(opts))
        inputs["image"] = image_path_or_url if is_url(image_path_or_url) else image_data_uri(image_path_or_url)
        return await self._create(model or self.default_model, inputs)

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        async with http(timeout=60.0) as client:
            resp = await client.get("%s/predictions/%s" % (BASE, job_id), headers=self._headers())
        raise_for_status(resp, self.display_name, self.key_env)
        return self._job(resp.json() or {})
