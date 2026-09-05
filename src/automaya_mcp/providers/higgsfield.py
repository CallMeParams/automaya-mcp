"""Higgsfield (https://api.higgsfield.ai). Header ``Authorization: Key <KEY>:<SECRET>``.

As of 2026-09-04 Higgsfield exposes 3D generation through its MCP server
(the ``generate_3d`` tool) and has not published a public REST path for it.
This provider is therefore only ``configured()`` when HIGGSFIELD_3D_ENDPOINT
names the request path (for example ``/tripo-ai/tripo-3d/generate``) in
addition to the key pair. When the endpoint is set, requests follow the
Higgsfield queue convention: POST <endpoint> {prompt|image_url, ...} ->
{request_id}; GET /requests/{id}/status -> queued|in_progress|completed|failed
with results[0].raw.url on completion.
"""
from __future__ import annotations

from typing import Any, Dict

from .base import GenJob, Provider3D, ProviderError, env_key, http, image_data_uri, is_url, map_status, raise_for_status

BASE = "https://api.higgsfield.ai"
_STATUS = {"queued": "queued", "in_queue": "queued", "in_progress": "running", "processing": "running", "completed": "succeeded", "failed": "failed", "nsfw": "failed", "cancelled": "cancelled", "canceled": "cancelled"}

MCP_NOTE = (
    "Higgsfield 3D is currently exposed only through the Higgsfield MCP server (tool 'generate_3d'); no public REST path is documented. "
    "Use that MCP tool to generate, download the resulting model file, then import it with maya_scene_import or assets.import_model. "
    "If Higgsfield publishes a REST route, set HIGGSFIELD_3D_ENDPOINT to its path (e.g. /tripo-ai/tripo-3d/generate) together with "
    "HIGGSFIELD_API_KEY and HIGGSFIELD_API_SECRET and this provider will use it."
)


class HiggsfieldProvider(Provider3D):
    name = "higgsfield"
    key_env = "HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET + HIGGSFIELD_3D_ENDPOINT"
    display_name = "Higgsfield"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, endpoint: str | None = None) -> None:
        self._key = api_key
        self._secret = api_secret
        self._endpoint = endpoint

    @property
    def api_key(self) -> str | None:
        return self._key or env_key("HIGGSFIELD_API_KEY")

    @property
    def api_secret(self) -> str | None:
        return self._secret or env_key("HIGGSFIELD_API_SECRET")

    @property
    def endpoint(self) -> str | None:
        ep = self._endpoint or env_key("HIGGSFIELD_3D_ENDPOINT")
        if not ep:
            return None
        return ep if ep.startswith("http") else BASE + ("/" if not ep.startswith("/") else "") + ep

    def has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def configured(self) -> bool:
        return self.has_keys() and bool(self.endpoint)

    def how_to_configure(self) -> str:
        if self.has_keys() and not self.endpoint:
            return "Keys found but HIGGSFIELD_3D_ENDPOINT is unset. " + MCP_NOTE
        return "Set HIGGSFIELD_API_KEY and HIGGSFIELD_API_SECRET. " + MCP_NOTE

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
            "note": "REST route not publicly documented; see configure text",
        }

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderError("Higgsfield 3D is not usable over REST from here. " + self.how_to_configure(), provider=self.name)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Key %s:%s" % (self.api_key, self.api_secret)}

    async def _submit(self, payload: Dict[str, Any]) -> GenJob:
        self.require_configured()
        async with http(headers=self._headers()) as client:
            resp = await client.post(self.endpoint, json=payload)
        raise_for_status(resp, self.display_name, "HIGGSFIELD_API_KEY/HIGGSFIELD_API_SECRET")
        body = resp.json() or {}
        rid = body.get("request_id") or body.get("id")
        if not rid:
            raise ProviderError("Higgsfield did not return a request id: %s" % resp.text[:300], provider=self.name)
        return GenJob(provider=self.name, job_id=str(rid), status="queued", message="submitted to %s" % self.endpoint, raw=body)

    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {"prompt": prompt}
        for k in ("quality", "face_limit", "pbr", "seed"):
            if opts.get(k) is not None:
                payload[k] = opts[k]
        return await self._submit(payload)

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        payload: Dict[str, Any] = {"image_url": image_path_or_url if is_url(image_path_or_url) else image_data_uri(image_path_or_url)}
        if opts.get("prompt"):
            payload["prompt"] = opts["prompt"]
        return await self._submit(payload)

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        async with http(headers=self._headers()) as client:
            resp = await client.get("%s/requests/%s/status" % (BASE, job_id))
        raise_for_status(resp, self.display_name, "HIGGSFIELD_API_KEY/HIGGSFIELD_API_SECRET")
        body = resp.json() or {}
        status = map_status(body.get("status"), _STATUS)
        outputs: Dict[str, str] = {}
        thumb = None
        for res in body.get("results") or []:
            raw = (res or {}).get("raw") or {}
            url = raw.get("url") or res.get("url")
            if not url:
                continue
            tail = url.split("?")[0].rsplit("/", 1)[-1]
            ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else "glb"
            if ext in ("png", "jpg", "jpeg", "webp"):
                thumb = thumb or url
                outputs.setdefault("image", url)
            else:
                outputs.setdefault(ext, url)
        message = str(body.get("status", ""))
        if status == "failed":
            message = "Higgsfield request failed: %s" % (body.get("error") or body.get("message") or "no details")
        return GenJob(provider=self.name, job_id=job_id, status=status, progress=100 if status == "succeeded" else (50 if status == "running" else 0), message=message, outputs=outputs, thumbnail_url=thumb, raw=body)
