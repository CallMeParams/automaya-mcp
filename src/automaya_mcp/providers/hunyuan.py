"""Tencent Hunyuan3D. Two routes:

* official: Tencent Cloud ``ai3d`` service (host ai3d.tencentcloudapi.com,
  Version 2025-05-13, region HUNYUAN_REGION or ap-guangzhou) signed with
  TC3-HMAC-SHA256 using HUNYUAN_SECRET_ID / HUNYUAN_SECRET_KEY. Generation is
  SubmitHunyuanTo3DProJob / QueryHunyuanTo3DProJob with Model "3.1" by default,
  up to 8 MultiViewImages (ViewType left|right|back|...), GenerateType
  Normal|LowPoly|Geometry|Sketch and PolygonType triangle|quadrilateral.
  Post jobs take a finished job's model URL as File3D: texture
  (SubmitTextureTo3DJob / QueryTextureTo3DJob), uv (SubmitHunyuanTo3DUVJob /
  QueryHunyuanTo3DUVJob), rig (SubmitAutoRiggingJob / QueryAutoRiggingJob),
  reduce_face (SubmitReduceFaceJob / QueryReduceFaceJob). Hunyuan 3D World
  uses the action names in HUNYUAN_WORLD_SUBMIT / HUNYUAN_WORLD_QUERY; the
  defaults are unverified and the error says so if Tencent rejects them.
* local: a Hunyuan3D-2 ``api_server.py`` at HUNYUAN_LOCAL_URL. POST /send ->
  {uid}; GET /status/{uid} -> {status, model_base64}. Text prompts go through
  the same endpoint (``text`` field); local servers only produce GLB.

Job ids are ``official:<JobId>`` (generation), ``official:<kind>:<JobId>``
(post jobs and world) or ``local:<uid>``.
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
DEFAULT_MODEL = "3.1"
MAX_VIEWS = 8
VIEW_TYPES = ("left", "right", "back", "front", "top", "bottom", "front_left", "front_right")
GENERATE_TYPES = ("Normal", "LowPoly", "Geometry", "Sketch")
POLYGON_TYPES = ("triangle", "quadrilateral")
WORLD_SUBMIT_DEFAULT = "SubmitHunyuanWorldJob"
WORLD_QUERY_DEFAULT = "QueryHunyuanWorldJob"

# kind -> (submit action, query action). Job ids carry the kind so poll picks the right query.
POST_JOBS = {
    "texture": ("SubmitTextureTo3DJob", "QueryTextureTo3DJob"),
    "uv": ("SubmitHunyuanTo3DUVJob", "QueryHunyuanTo3DUVJob"),
    "rig": ("SubmitAutoRiggingJob", "QueryAutoRiggingJob"),
    "reduce_face": ("SubmitReduceFaceJob", "QueryReduceFaceJob"),
}
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

    @property
    def region(self) -> str:
        return env_key("HUNYUAN_REGION") or REGION

    def capabilities(self) -> Dict[str, Any]:
        local = self.mode == "local"
        return {
            "text_to_3d": True,
            "image_to_3d": True,
            "multiview": not local,
            "rig": not local,
            "retexture": not local,
            "remesh": not local,
            "convert": False,
            "post_jobs": [] if local else sorted(POST_JOBS),
            "world": not local,
            "formats": ["glb"] if local else ["glb", "obj", "fbx", "usdz"],
            "route": self.mode,
            "model": DEFAULT_MODEL,
        }

    # official ---------------------------------------------------------------
    async def _official_call(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = tc3_headers(self.secret_id or "", self.secret_key or "", action, payload, region=self.region)
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
            if "InvalidAction" in code and action in (self.world_submit_action, self.world_query_action):
                raise ProviderError(
                    "Tencent Cloud does not know the action %r (%s: %s). The Hunyuan 3D World action names are unverified; "
                    "set HUNYUAN_WORLD_SUBMIT / HUNYUAN_WORLD_QUERY to the names from the ai3d API reference." % (action, code, msg),
                    provider=self.name,
                )
            raise ProviderError("Hunyuan3D %s failed: %s: %s" % (action, code, msg), provider=self.name)
        return data

    def _official_common(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        generate_type = str(opts.get("generate_type", "Normal"))
        if generate_type not in GENERATE_TYPES:
            raise ProviderError("generate_type must be one of %s, got %r" % (", ".join(GENERATE_TYPES), generate_type), provider=self.name)
        payload: Dict[str, Any] = {"GenerateType": generate_type, "Model": str(opts.get("model_version") or opts.get("model") or DEFAULT_MODEL)}
        if opts.get("pbr") is not None:
            payload["EnablePBR"] = bool(opts["pbr"])
        if opts.get("face_limit"):
            payload["FaceCount"] = int(opts["face_limit"])
        polygon = opts.get("polygon_type") or ("quadrilateral" if opts.get("quad") else None)
        if polygon:
            polygon = str(polygon).lower()
            if polygon not in POLYGON_TYPES:
                raise ProviderError("polygon_type must be triangle or quadrilateral, got %r" % polygon, provider=self.name)
            payload["PolygonType"] = polygon
        fmt = str(opts.get("format", "")).upper()
        if fmt in ("GLB", "OBJ", "FBX", "USDZ", "STL"):
            payload["ResultFormat"] = fmt
        return payload

    @staticmethod
    def _view_image(entry: Any, index: int) -> Dict[str, Any]:
        """One MultiViewImages item from a path/url string or a {view, image} dict."""
        if isinstance(entry, dict):
            view = str(entry.get("view") or entry.get("ViewType") or "").lower()
            source = entry.get("image") or entry.get("url") or entry.get("path") or ""
        else:
            view, source = "", str(entry)
        if not view:
            view = VIEW_TYPES[index % 3]
        if view not in VIEW_TYPES:
            raise ProviderError("multi view entry %d has unknown view type %r (use one of %s)" % (index, view, ", ".join(VIEW_TYPES)), provider="hunyuan")
        item: Dict[str, Any] = {"ViewType": view}
        if is_url(source):
            item["ViewImageUrl"] = source
        else:
            item["ViewImageBase64"] = image_base64(source)
        return item

    def multi_view_images(self, extra: Any) -> list:
        entries = list(extra or [])
        if len(entries) > MAX_VIEWS:
            raise ProviderError("Hunyuan3D accepts at most %d extra views, got %d" % (MAX_VIEWS, len(entries)), provider=self.name)
        return [self._view_image(e, i) for i, e in enumerate(entries)]

    @property
    def world_submit_action(self) -> str:
        return env_key("HUNYUAN_WORLD_SUBMIT") or WORLD_SUBMIT_DEFAULT

    @property
    def world_query_action(self) -> str:
        return env_key("HUNYUAN_WORLD_QUERY") or WORLD_QUERY_DEFAULT

    async def _official_submit(self, payload: Dict[str, Any], action: str = "SubmitHunyuanTo3DProJob", kind: str = "") -> GenJob:
        data = await self._official_call(action, payload)
        job_id = data.get("JobId")
        if not job_id:
            raise ProviderError("Hunyuan3D %s did not return a JobId: %s" % (action, json.dumps(data)[:300]), provider=self.name)
        full = "official:%s:%s" % (kind, job_id) if kind else "official:%s" % job_id
        return GenJob(provider=self.name, job_id=full, status="queued", message="submitted %s to Tencent Cloud" % action, raw={"RequestId": data.get("RequestId"), "kind": kind or "generate"})

    def _query_action(self, kind: str) -> str:
        if not kind:
            return "QueryHunyuanTo3DProJob"
        if kind == "world":
            return self.world_query_action
        if kind in POST_JOBS:
            return POST_JOBS[kind][1]
        raise ProviderError("unknown Hunyuan3D job kind %r in job id" % kind, provider=self.name)

    async def _official_poll(self, job_id: str, kind: str = "") -> GenJob:
        data = await self._official_call(self._query_action(kind), {"JobId": job_id})
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
        full = "official:%s:%s" % (kind, job_id) if kind else "official:%s" % job_id
        data["kind"] = kind or "generate"
        return GenJob(provider=self.name, job_id=full, status=status, progress=progress, message=message, outputs=outputs, thumbnail_url=thumb, raw=data)

    async def _source_file(self, job_id: str, prefer: tuple = ("glb", "obj", "fbx")) -> Dict[str, str]:
        """File3D {Type, Url} for a finished official job, or a direct model URL."""
        if is_url(job_id):
            ext = os.path.splitext(job_id.split("?")[0])[1].lstrip(".").upper() or "GLB"
            return {"Type": ext, "Url": job_id}
        job = await self.poll(job_id)
        if job.status != "succeeded":
            raise ProviderError("job %s is %s; wait for it to finish before running a post job" % (job_id, job.status), provider=self.name)
        for fmt in prefer:
            if job.outputs.get(fmt):
                return {"Type": fmt.upper(), "Url": job.outputs[fmt]}
        raise ProviderError("job %s has no %s output for a post job (available: %s)" % (job_id, "/".join(prefer), ", ".join(sorted(job.outputs)) or "none"), provider=self.name)

    async def post_job(self, kind: str, job_id: str, **opts: Any) -> GenJob:
        """Run texture | uv | rig | reduce_face on a finished job (or a model URL)."""
        self.require_configured()
        if self.mode != "official":
            raise ProviderError("Hunyuan3D post jobs need the official Tencent Cloud route (HUNYUAN_SECRET_ID / HUNYUAN_SECRET_KEY)", provider=self.name)
        if kind not in POST_JOBS:
            raise ProviderError("unknown post job %r, use one of %s" % (kind, ", ".join(sorted(POST_JOBS))), provider=self.name)
        submit_action = POST_JOBS[kind][0]
        prefer = ("fbx", "glb") if kind == "rig" else ("glb", "obj", "fbx")
        file3d = await self._source_file(job_id, prefer)
        payload: Dict[str, Any]
        if kind == "uv":
            payload = {"File": file3d}
        else:
            payload = {"File3D": file3d}
        if kind == "texture":
            payload["Model"] = str(opts.get("model_version") or opts.get("model") or DEFAULT_MODEL)
            if opts.get("prompt"):
                payload["Prompt"] = opts["prompt"]
            image = opts.get("image") or opts.get("image_style_url")
            if image:
                payload["Image"] = {"Url": image} if is_url(image) else {"Base64": image_base64(image)}
            if opts.get("extra_images"):
                payload["MultiViewImages"] = self.multi_view_images(opts["extra_images"])
            if opts.get("pbr") is not None:
                payload["EnablePBR"] = bool(opts["pbr"])
            if opts.get("keep_uv") is not None:
                payload["EnableKeepUV"] = bool(opts["keep_uv"])
            if opts.get("texture_size"):
                payload["TextureSize"] = int(opts["texture_size"])
        elif kind == "rig":
            if opts.get("motion_type"):
                payload["MotionType"] = int(opts["motion_type"])
        elif kind == "reduce_face":
            polygon = str(opts.get("polygon_type") or opts.get("topology") or "triangle").lower()
            if polygon == "quad":
                polygon = "quadrilateral"
            if polygon not in POLYGON_TYPES:
                raise ProviderError("polygon_type must be triangle or quadrilateral, got %r" % polygon, provider=self.name)
            payload["PolygonType"] = polygon
            if opts.get("face_level"):
                payload["FaceLevel"] = str(opts["face_level"]).lower()
        return await self._official_submit(payload, submit_action, kind)

    async def submit_world(self, prompt: str | None = None, image: str | None = None, **opts: Any) -> GenJob:
        """Hunyuan 3D World (scene generation). Action names come from env until verified."""
        self.require_configured()
        if self.mode != "official":
            raise ProviderError("Hunyuan 3D World needs the official Tencent Cloud route", provider=self.name)
        if not prompt and not image:
            raise ProviderError("world generation needs a prompt or an image", provider=self.name)
        payload: Dict[str, Any] = {}
        if prompt:
            payload["Prompt"] = prompt
        if image:
            if is_url(image):
                payload["ImageUrl"] = image
            else:
                payload["ImageBase64"] = image_base64(image)
        for key in ("Model", "ResultFormat"):
            if opts.get(key.lower()):
                payload[key] = opts[key.lower()]
        return await self._official_submit(payload, self.world_submit_action, "world")

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
            if opts.get("extra_images"):
                payload["MultiViewImages"] = self.multi_view_images(opts["extra_images"])
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
            rest = job_id[len("official:"):]
            kind, sep, tail = rest.partition(":")
            if sep and (kind in POST_JOBS or kind == "world"):
                return await self._official_poll(tail, kind)
            return await self._official_poll(rest)
        if job_id.startswith("local:"):
            return await self._local_poll(job_id[len("local:"):])
        if self.mode == "local":
            return await self._local_poll(job_id)
        return await self._official_poll(job_id)

    async def rig(self, job_id: str, **opts: Any) -> GenJob:
        return await self.post_job("rig", job_id, **opts)

    async def retexture(self, job_id: str, **opts: Any) -> GenJob:
        return await self.post_job("texture", job_id, **opts)

    async def remesh(self, job_id: str, **opts: Any) -> GenJob:
        return await self.post_job("reduce_face", job_id, **opts)

    async def download(self, job: GenJob, fmt: str, dest_dir: str | None = None) -> str:
        url = job.outputs.get((fmt or "").lower(), "")
        if url.startswith("file://"):
            path = url[len("file://"):]
            if os.path.exists(path):
                return path
            raise ProviderError("local Hunyuan3D result %s is gone; poll the job again to re-download" % path, provider=self.name)
        return await super().download(job, fmt, dest_dir)
