"""Shared provider interface: GenJob model, Provider3D ABC, http client, errors.

Every 3D generator is treated the same way: ``submit -> poll -> download``.
Provider modules translate their own API shapes into ``GenJob`` so the tool
layer never sees provider specifics.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import tempfile
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

USER_AGENT = "automaya-mcp/1.0"
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
FORMAT_EXT = {"glb": ".glb", "gltf": ".gltf", "fbx": ".fbx", "obj": ".obj", "usdz": ".usdz", "usd": ".usd", "stl": ".stl", "zip": ".zip", "image": ".png"}


class ProviderError(Exception):
    """Raised for any provider failure. The message says how to fix it."""

    def __init__(self, message: str, status: int | None = None, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.provider = provider


class GenJob(BaseModel):
    provider: str
    job_id: str
    status: str = Field(default="queued", description="queued | running | succeeded | failed | cancelled")
    progress: int = Field(default=0, ge=0, le=100)
    message: str = ""
    outputs: Dict[str, str] = Field(default_factory=dict, description="format -> download url")
    thumbnail_url: str | None = None
    raw: Dict[str, Any] = Field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def brief(self) -> Dict[str, Any]:
        """Compact dict for tool output (drops the raw payload)."""
        return {
            "provider": self.provider,
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "formats": sorted(self.outputs),
            "thumbnail_url": self.thumbnail_url,
        }


def http(timeout: float = 60.0, **kwargs: Any) -> httpx.AsyncClient:
    """AsyncClient factory with sane timeouts and our User-Agent."""
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}) or {})
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0), headers=headers, follow_redirects=True, **kwargs)


def env_key(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def raise_for_status(resp: httpx.Response, provider: str, key_env: str) -> None:
    """Turn HTTP failures into ProviderError with an actionable message."""
    code = resp.status_code
    if code < 400:
        return
    body = resp.text[:400]
    if code in (401, 403):
        msg = "%s rejected the API key (HTTP %d). Set the %s environment variable to a valid key and restart the MCP server." % (provider, code, key_env)
    elif code == 402:
        msg = "%s reports insufficient credits (HTTP 402). Top up your %s account balance before generating again." % (provider, provider)
    elif code == 429:
        msg = "%s is rate limiting this key (HTTP 429). Wait a minute and retry, or reduce concurrent jobs." % provider
    elif code == 404:
        msg = "%s returned 404 for %s. The job id may be wrong or expired." % (provider, resp.request.url.path)
    else:
        msg = "%s returned HTTP %d: %s" % (provider, code, body)
    raise ProviderError(msg, status=code, provider=provider)


def is_url(value: str) -> bool:
    try:
        p = urlparse(value)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def read_image(path_or_url: str) -> tuple:
    """Return (bytes, mime, filename) for a local image path. Raises for a missing file."""
    if not os.path.isfile(path_or_url):
        raise ProviderError("image file not found: %s (pass a local path or an http(s) URL)" % path_or_url)
    mime = mimetypes.guess_type(path_or_url)[0] or "image/png"
    with open(path_or_url, "rb") as fh:
        data = fh.read()
    return data, mime, os.path.basename(path_or_url)


def image_data_uri(path: str) -> str:
    data, mime, _ = read_image(path)
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def image_base64(path: str) -> str:
    data, _, _ = read_image(path)
    return base64.b64encode(data).decode("ascii")


def download_dir() -> str:
    folder = os.environ.get("AUTOMAYA_DOWNLOAD_DIR") or os.path.join(tempfile.gettempdir(), "automaya", "downloads")
    os.makedirs(folder, exist_ok=True)
    return folder


def safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)[:80] or "asset"


async def download_url(url: str, dest: str, headers: Dict[str, str] | None = None, timeout: float = 300.0) -> str:
    """Stream ``url`` to ``dest`` and return ``dest``."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    async with http(timeout=timeout, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise ProviderError("download failed with HTTP %d for %s" % (resp.status_code, url), status=resp.status_code)
            with open(dest, "wb") as out:
                async for chunk in resp.aiter_bytes(1 << 20):
                    out.write(chunk)
    return dest


def ext_for(fmt: str, url: str = "") -> str:
    fmt = (fmt or "").lower()
    if fmt in FORMAT_EXT:
        return FORMAT_EXT[fmt]
    path = urlparse(url).path if url else ""
    ext = os.path.splitext(path)[1]
    return ext or ".bin"


class Provider3D(ABC):
    """Base class for AI 3D generators."""

    name: str = "base"
    key_env: str = ""
    display_name: str = "Provider"

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        """Keys: text_to_3d, image_to_3d, multiview, rig, retexture, remesh (bools), formats (list)."""

    def how_to_configure(self) -> str:
        return "Set the %s environment variable before starting the MCP server." % self.key_env

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderError("%s is not configured. %s" % (self.display_name, self.how_to_configure()), provider=self.name)

    @abstractmethod
    async def submit_text(self, prompt: str, **opts: Any) -> GenJob: ...

    @abstractmethod
    async def submit_image(self, image_path_or_url: str, **opts: Any) -> GenJob: ...

    @abstractmethod
    async def poll(self, job_id: str) -> GenJob: ...

    async def download(self, job: GenJob, fmt: str, dest_dir: str | None = None) -> str:
        """Download ``fmt`` output of a finished job to ``dest_dir`` and return the path."""
        fmt = (fmt or "").lower()
        if job.status != "succeeded":
            raise ProviderError("job %s is %s, nothing to download yet" % (job.job_id, job.status), provider=self.name)
        url = job.outputs.get(fmt)
        if not url:
            available = ", ".join(sorted(job.outputs)) or "none"
            raise ProviderError("job %s has no %r output (available: %s). Use convert or pick another format." % (job.job_id, fmt, available), provider=self.name)
        dest_dir = dest_dir or download_dir()
        dest = os.path.join(dest_dir, "%s_%s%s" % (self.name, safe_name(job.job_id), ext_for(fmt, url)))
        return await download_url(url, dest, headers=self.download_headers())

    def download_headers(self) -> Dict[str, str] | None:
        return None

    async def rig(self, job_id: str, **opts: Any) -> GenJob:
        raise ProviderError("%s does not support rigging" % self.display_name, provider=self.name)

    async def retexture(self, job_id: str, **opts: Any) -> GenJob:
        raise ProviderError("%s does not support retexturing" % self.display_name, provider=self.name)

    async def remesh(self, job_id: str, **opts: Any) -> GenJob:
        raise ProviderError("%s does not support remeshing" % self.display_name, provider=self.name)

    async def convert(self, job_id: str, fmt: str, **opts: Any) -> GenJob:
        raise ProviderError("%s does not support format conversion" % self.display_name, provider=self.name)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "configured": self.configured(),
            "capabilities": self.capabilities(),
            "configure": self.how_to_configure(),
        }


def map_status(value: Any, table: Dict[str, str], default: str = "running") -> str:
    key = str(value or "").strip()
    return table.get(key, table.get(key.upper(), table.get(key.lower(), default)))


def formats_from(outputs: Dict[str, str]) -> List[str]:
    return sorted(k for k, v in outputs.items() if v)
