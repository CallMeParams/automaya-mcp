"""Tencent Hunyuan3D. Two routes:

* official: Tencent Cloud ``ai3d`` service (host ai3d.tencentcloudapi.com,
  Version 2025-05-13, region ap-guangzhou) signed with TC3-HMAC-SHA256 using
  HUNYUAN_SECRET_ID / HUNYUAN_SECRET_KEY. Actions SubmitHunyuanTo3DProJob and
  QueryHunyuanTo3DProJob.
* local: a Hunyuan3D-2 ``api_server.py`` at HUNYUAN_LOCAL_URL. POST /send ->
  {uid}; GET /status/{uid} -> {status, model_base64}. Text prompts go through
  the same endpoint (``text`` field); local servers only produce GLB.

Job ids are ``official:<JobId>`` or ``local:<uid>``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict

from .base import GenJob, Provider3D, ProviderError, download_dir, env_key, http, image_base64, is_url, map_status, raise_for_status, safe_name

HOST = "ai3d.tencentcloudapi.com"
SERVICE = "ai3d"
VERSION = "2025-05-13"
REGION = "ap-guangzhou"
ALGORITHM = "TC3-HMAC-SHA256"
CONTENT_TYPE = "application/json; charset=utf-8"

_OFFICIAL_STATUS = {"WAIT": "queued", "RUN": "running", "DONE": "succeeded", "FAIL": "failed"}
_LOCAL_STATUS = {"pending": "queued", "queued": "queued", "processing": "running", "running": "running", "completed": "succeeded", "done": "succeeded", "success": "succeeded", "error": "failed", "failed": "failed"}

# local:<uid> -> path of the decoded model (local servers hand back base64, not a url)
_LOCAL_FILES: Dict[str, str] = {}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def tc3_headers(secret_id: str, secret_key: str, action: str, payload: Dict[str, Any], timestamp: int | None = None, region: str = REGION) -> Dict[str, str]:
    """Build the signed header set for one Tencent Cloud API v3 request."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ts = int(timestamp if timestamp is not None else time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    canonical_headers = "content-type:%s\nhost:%s\n" % (CONTENT_TYPE, HOST)
    signed_headers = "content-type;host"
    canonical_request = "\n".join(["POST", "/", "", canonical_headers, signed_headers, _sha256_hex(body)])

    credential_scope = "%s/%s/tc3_request" % (date, SERVICE)
    string_to_sign = "\n".join([ALGORITHM, str(ts), credential_scope, _sha256_hex(canonical_request.encode("utf-8"))])

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = "%s Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (ALGORITHM, secret_id, credential_scope, signed_headers, signature)
    return {
        "Authorization": authorization,
        "Content-Type": CONTENT_TYPE,
        "Host": HOST,
        "X-TC-Action": action,
        "X-TC-Version": VERSION,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": region,
    }


class HunyuanProvider(Provider3D):
    name = "hunyuan"
    key_env = "HUNYUAN_SECRET_ID/HUNYUAN_SECRET_KEY or HUNYUAN_LOCAL_URL"
    display_name = "Tencent Hunyuan3D"

    def __init__(self, secret_id: str | None = None, secret_key: str | None = None, local_url: str | None = None, mode: str | None = None) -> None:
        self._id = secret_id
        self._secret = secret_key
        self._local = local_url
        self._mode = mode

    @property
    def secret_id(self) -> str | None:
        return self._id or env_key("HUNYUAN_SECRET_ID")

    @property
    def secret_key(self) -> str | None:
        return self._secret or env_key("HUNYUAN_SECRET_KEY")

    @property
    def local_url(self) -> str | None:
        url = self._local or env_key("HUNYUAN_LOCAL_URL")
        return url.rstrip("/") if url else None

    @property
    def mode(self) -> str | None:
        if self._mode:
            return self._mode
        forced = env_key("HUNYUAN_MODE")
        if forced in ("official", "local"):
            return forced
        if self.secret_id and self.secret_key:
            return "official"
        if self.local_url:
            return "local"
        return None

    def configured(self) -> bool:
        return self.mode is not None

    def how_to_configure(self) -> str:
        return (
            "Set HUNYUAN_SECRET_ID and HUNYUAN_SECRET_KEY (Tencent Cloud, ai3d service enabled) for the hosted API, "
            "or HUNYUAN_LOCAL_URL (e.g. http://localhost:8081) for a local Hunyuan3D-2 api_server. HUNYUAN_MODE=official|local forces a route."
        )

    def capabilities(self) -> Dict[str, Any]:
        local = self.mode == "local"
        return {
            "text_to_3d": True,
            "image_to_3d": True,
            "multiview": not local,
            "rig": False,
            "retexture": False,
            "remesh": False,
            "convert": False,
            "formats": ["glb"] if local else ["glb", "obj", "fbx", "usdz"],
            "route": self.mode,
        }

    # official ---------------------------------------------------------------
    async def _official_call(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = tc3_headers(self.secret_id or "", self.secret_key or "", action, payload)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        async with http(timeout=90.0) as client:
            resp = await client.post("https://%s/" % HOST, content=body, headers=headers)
        raise_for_status(resp, self.display_name, "HUNYUAN_SECRET_ID/HUNYUAN_SECRET_KEY")
        data = (resp.json() or {}).get("Response") or {}
        err = data.get("Error")
        if err:
            code = str(err.get("Code", ""))
            msg = err.get("Message", "")
            if "AuthFailure" in code:
                raise ProviderError("Tencent Cloud rejected the signature (%s: %s). Check HUNYUAN_SECRET_ID / HUNYUAN_SECRET_KEY and that the ai3d service is enabled." % (code, msg), status=401, provider=self.name)
            if "Balance" in code or "Arrears" in code or "ResourceInsufficient" in code:
                raise ProviderError("Tencent Cloud reports insufficient balance or quota (%s: %s)." % (code, msg), status=402, provider=self.name)
            if "RequestLimitExceeded" in code:
                raise ProviderError("Tencent Cloud rate limit hit (%s). Retry shortly." % code, status=429, provider=self.name)
            raise ProviderError("Hunyuan3D %s failed: %s: %s" % (action, code, msg), provider=self.name)
        return data

    def _official_common(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"GenerateType": opts.get("generate_type", "Normal"), "Model": str(opts.get("model_version", "3.1"))}
        if opts.get("pbr") is not None:
            payload["EnablePBR"] = bool(opts["pbr"])
        if opts.get("face_limit"):
            payload["FaceCount"] = int(opts["face_limit"])
        fmt = str(opts.get("format", "")).upper()
        if fmt in ("GLB", "OBJ", "FBX", "USDZ", "STL"):
            payload["ResultFormat"] = fmt
        return payload

    async def _official_submit(self, payload: Dict[str, Any]) -> GenJob:
        data = await self._official_call("SubmitHunyuanTo3DProJob", payload)
        job_id = data.get("JobId")
        if not job_id:
            raise ProviderError("Hunyuan3D did not return a JobId: %s" % json.dumps(data)[:300], provider=self.name)
        return GenJob(provider=self.name, job_id="official:%s" % job_id, status="queued", message="submitted to Tencent Cloud", raw={"RequestId": data.get("RequestId")})

    async def _official_poll(self, job_id: str) -> GenJob:
        data = await self._official_call("QueryHunyuanTo3DProJob", {"JobId": job_id})
        status = map_status(data.get("Status"), _OFFICIAL_STATUS)
        outputs: Dict[str, str] = {}
        thumb = None
        for f in data.get("ResultFile3Ds") or []:
            t = str(f.get("Type", "")).lower()
            url = f.get("Url")
            if not url:
                continue
            if t in ("glb", "obj", "fbx", "usdz", "stl", "gltf"):
                outputs[t] = url
            elif t in ("png", "jpg", "jpeg", "gif", "image"):
                thumb = thumb or url
                outputs.setdefault("image", url)
            else:
                outputs[t or "file"] = url
            if f.get("PreviewImageUrl"):
                thumb = thumb or f["PreviewImageUrl"]
        message = str(data.get("Status", ""))
        if status == "failed":
            message = "Hunyuan3D job failed: %s %s" % (data.get("ErrorCode", ""), data.get("ErrorMessage", ""))
        progress = 100 if status == "succeeded" else (50 if status == "running" else 0)
        return GenJob(provider=self.name, job_id="official:%s" % job_id, status=status, progress=progress, message=message, outputs=outputs, thumbnail_url=thumb, raw=data)

    # local --------------------------------------------------------------------
    async def _local_submit(self, payload: Dict[str, Any]) -> GenJob:
        async with http(timeout=120.0) as client:
            resp = await client.post(self.local_url + "/send", json=payload)
        raise_for_status(resp, "Hunyuan3D local server", "HUNYUAN_LOCAL_URL")
        body = resp.json() or {}
        uid = body.get("uid")
        if not uid:
            raise ProviderError("local Hunyuan3D server did not return uid: %s" % resp.text[:300], provider=self.name)
        return GenJob(provider=self.name, job_id="local:%s" % uid, status="queued", message="submitted to %s" % self.local_url)

    async def _local_poll(self, uid: str) -> GenJob:
        job_id = "local:%s" % uid
        async with http(timeout=120.0) as client:
            resp = await client.get("%s/status/%s" % (self.local_url, uid))
        raise_for_status(resp, "Hunyuan3D local server", "HUNYUAN_LOCAL_URL")
        body = resp.json() or {}
        status = map_status(body.get("status"), _LOCAL_STATUS, default="running")
        outputs: Dict[str, str] = {}
        if body.get("model_base64"):
            status = "succeeded"
            path = _LOCAL_FILES.get(job_id)
            if not path or not os.path.exists(path):
                path = os.path.join(download_dir(), "hunyuan_local_%s.glb" % safe_name(uid))
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(body["model_base64"]))
                _LOCAL_FILES[job_id] = path
            outputs["glb"] = "file://" + path
            body = {k: v for k, v in body.items() if k != "model_base64"}
            body["local_path"] = path
        message = str(body.get("message") or body.get("status") or "")
        return GenJob(provider=self.name, job_id=job_id, status=status, progress=100 if status == "succeeded" else 0, message=message, outputs=outputs, raw=body)

    # public -----------------------------------------------------------------
    async def submit_text(self, prompt: str, **opts: Any) -> GenJob:
        self.require_configured()
        if self.mode == "official":
            return await self._official_submit({"Prompt": prompt, **self._official_common(opts)})
        payload: Dict[str, Any] = {"text": prompt, "texture": bool(opts.get("texture", True))}
        if opts.get("face_limit"):
            payload["face_count"] = int(opts["face_limit"])
        return await self._local_submit(payload)

    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob:
        self.require_configured()
        if self.mode == "official":
            payload = self._official_common(opts)
            if is_url(image_path_or_url):
                payload["ImageUrl"] = image_path_or_url
            else:
                payload["ImageBase64"] = image_base64(image_path_or_url)
            if opts.get("prompt"):
                payload["Prompt"] = opts["prompt"]
            return await self._official_submit(payload)
        if is_url(image_path_or_url):
            async with http(timeout=120.0) as client:
                resp = await client.get(image_path_or_url)
            raise_for_status(resp, "image fetch", "")
            b64 = base64.b64encode(resp.content).decode("ascii")
        else:
            b64 = image_base64(image_path_or_url)
        payload = {"image": b64, "texture": bool(opts.get("texture", True))}
        if opts.get("face_limit"):
            payload["face_count"] = int(opts["face_limit"])
        return await self._local_submit(payload)

    async def poll(self, job_id: str) -> GenJob:
        self.require_configured()
        if job_id.startswith("official:"):
            return await self._official_poll(job_id[len("official:"):])
        if job_id.startswith("local:"):
            return await self._local_poll(job_id[len("local:"):])
        if self.mode == "local":
            return await self._local_poll(job_id)
        return await self._official_poll(job_id)

    async def download(self, job: GenJob, fmt: str, dest_dir: str | None = None) -> str:
        url = job.outputs.get((fmt or "").lower(), "")
        if url.startswith("file://"):
            path = url[len("file://"):]
            if os.path.exists(path):
                return path
            raise ProviderError("local Hunyuan3D result %s is gone; poll the job again to re-download" % path, provider=self.name)
        return await super().download(job, fmt, dest_dir)
