"""AI 3D generation tools: Tripo, Meshy, Rodin, Hunyuan3D, Higgsfield.

Network calls happen here on the server. Maya only receives a local path via
``gen.import_result``. A small in-memory job store keeps the last GenJob per
(provider, job_id) so import can find output URLs without re-polling.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Tuple

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..providers import GenJob, ProviderError, get_provider, list_providers
from ..providers.base import download_dir
from ._base import EXTERNAL_READ, EXTERNAL_WRITE, ToolContext, dumps

JOBS: Dict[Tuple[str, str], GenJob] = {}
PROMPTS: Dict[Tuple[str, str], str] = {}

FORMAT_NOTE = "fbx is the default because Maya 2024 has no native glTF importer; glb/gltf only import when a glTF plugin is installed."


def remember(job: GenJob, prompt: str | None = None) -> GenJob:
    JOBS[(job.provider, job.job_id)] = job
    if prompt:
        PROMPTS[(job.provider, job.job_id)] = prompt
    return job


def _err(exc: Exception) -> str:
    if isinstance(exc, ProviderError):
        return "Error: %s" % exc
    return "Error: %s: %s" % (type(exc).__name__, exc)


async def wait_for(provider: Any, job: GenJob, max_wait: float, context: Context | None, interval: float = 5.0) -> GenJob:
    """Poll until the job finishes or ``max_wait`` seconds pass, reporting progress."""
    started = time.monotonic()
    while not job.done and time.monotonic() - started < max_wait:
        await asyncio.sleep(interval)
        job = remember(await provider.poll(job.job_id))
        if context is not None:
            try:
                await context.report_progress(job.progress, 100, "%s %s: %s" % (job.provider, job.status, job.message))
            except Exception:  # noqa: BLE001, progress is best effort
                pass
    return job


def _describe_wait(job: GenJob, waited: bool, max_wait: float) -> Dict[str, Any]:
    out = job.brief()
    if job.status == "succeeded":
        out["next"] = "maya_gen3d_import with provider=%s job_id=%s" % (job.provider, job.job_id)
    elif waited and not job.done:
        out["next"] = "still %s after %.0fs; call maya_gen3d_poll (or wait again) with job_id=%s" % (job.status, max_wait, job.job_id)
    elif not job.done:
        out["next"] = "call maya_gen3d_poll with job_id=%s (generation takes 1-5 minutes)" % job.job_id
    return out


class GenOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str = Field(default="fbx", description="Preferred output format: fbx (default), obj, glb, usdz. " + FORMAT_NOTE, examples=["fbx"])
    quality: str | None = Field(default=None, description="standard | high (provider maps this to texture detail)")
    pbr: bool | None = Field(default=True, description="Generate PBR maps when the provider supports them")
    texture: bool | None = Field(default=True, description="Generate textures at all (False for a plain mesh)")
    face_limit: int | None = Field(default=None, ge=500, le=2_000_000, description="Target polygon count")
    negative_prompt: str | None = Field(default=None, description="What to avoid (Tripo, Meshy)")
    quad: bool | None = Field(default=None, description="Ask for quad topology where supported (Tripo convert, Rodin)")
    auto_refine: bool = Field(default=True, description="Meshy only: automatically run the refine (texture) stage after preview")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Provider specific passthrough options (e.g. Meshy art_style, Tripo model_version, Rodin tier)")


class TextTo3DInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="tripo | meshy | rodin | hunyuan | higgsfield", examples=["tripo"])
    prompt: str = Field(..., min_length=2, max_length=2000, description="Description of the object. Be concrete: material, style, single object, neutral pose.")
    options: GenOptions = Field(default_factory=GenOptions)
    wait: bool = Field(default=False, description="Block and poll until done or max_wait elapses")
    max_wait: float = Field(default=240.0, ge=5, le=1800, description="Seconds to wait when wait=True")


class ImageTo3DInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="tripo | meshy | rodin | hunyuan | higgsfield")
    image: str = Field(..., description="Local image path or http(s) URL. Local files are uploaded or inlined as needed.", examples=["/tmp/ref.png"])
    prompt: str | None = Field(default=None, description="Optional text hint (Rodin, Hunyuan)")
    extra_images: List[str] | None = Field(default=None, description="More views of the same object for multiview providers")
    options: GenOptions = Field(default_factory=GenOptions)
    wait: bool = Field(default=False)
    max_wait: float = Field(default=240.0, ge=5, le=1800)


class PollInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="Provider that owns the job")
    job_id: str = Field(..., min_length=1, description="Job id returned by a submit tool")
    wait: bool = Field(default=False, description="Keep polling until done or max_wait elapses")
    max_wait: float = Field(default=120.0, ge=5, le=1800)


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="Provider that owns the job")
    job_id: str = Field(..., min_length=1)
    format: str = Field(default="fbx", description="Which output to download: fbx, obj, glb, usdz. Falls back to the first available format when this one is missing. " + FORMAT_NOTE)
    name: str | None = Field(default=None, description="Rename the imported top node (or the group)")
    group: bool = Field(default=False, description="Group the imported nodes")
    scale: float | None = Field(default=None, gt=0, description="Uniform scale applied after import (Maya works in cm; most generators output metres)")
    freeze: bool = Field(default=False, description="Freeze transforms after scaling")
    center: bool = Field(default=False, description="Center the pivot and drop the model onto the origin")
    convert_if_missing: bool = Field(default=True, description="Tripo only: submit a convert_model task when the format is not available yet")


class RigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="tripo | meshy")
    job_id: str = Field(..., min_length=1)
    height_meters: float | None = Field(default=None, gt=0, description="Meshy: character height in metres")
    rig_type: str = Field(default="biped", description="Tripo: biped | quadruped")
    spec: str = Field(default="mixamo", description="Tripo: rig spec, mixamo or tripo")
    out_format: str = Field(default="fbx", description="Tripo: fbx | glb")
    wait: bool = Field(default=False)
    max_wait: float = Field(default=240.0, ge=5, le=1800)


class RetextureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(..., description="tripo | meshy")
    job_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=2, max_length=1000, description="Texture style description")
    pbr: bool = Field(default=True)
    image_style_url: str | None = Field(default=None, description="Meshy: style reference image URL instead of text")
    wait: bool = Field(default=False)
    max_wait: float = Field(default=240.0, ge=5, le=1800)


class RemeshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="meshy", description="meshy")
    job_id: str = Field(..., min_length=1)
    target_polycount: int = Field(default=20000, ge=100, le=1_000_000)
    topology: str = Field(default="quad", description="quad | triangle")
    formats: List[str] = Field(default_factory=lambda: ["fbx", "glb", "obj"], description="Formats to produce")
    wait: bool = Field(default=False)
    max_wait: float = Field(default=240.0, ge=5, le=1800)


class ConvertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="tripo", description="tripo")
    job_id: str = Field(..., min_length=1)
    format: str = Field(default="fbx", description="FBX | OBJ | USDZ | GLTF | STL")
    quad: bool | None = Field(default=None, description="Quad remesh during conversion")
    face_limit: int | None = Field(default=None, ge=500, le=2_000_000)
    fbx_preset: str | None = Field(default=None, description="Tripo fbx preset, e.g. 'blender' or 'unity'")
    wait: bool = Field(default=False)
    max_wait: float = Field(default=240.0, ge=5, le=1800)


def _opts(o: GenOptions) -> Dict[str, Any]:
    out: Dict[str, Any] = {"format": o.format, "pbr": o.pbr, "texture": o.texture, "auto_refine": o.auto_refine}
    if o.quality:
        out["quality"] = o.quality
    if o.face_limit:
        out["face_limit"] = o.face_limit
    if o.negative_prompt:
        out["negative_prompt"] = o.negative_prompt
    if o.quad is not None:
        out["quad"] = o.quad
    out.update(o.extra or {})
    return out


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_gen3d_list_providers", annotations={"title": "List AI 3D providers", **EXTERNAL_READ})
    async def maya_gen3d_list_providers() -> str:
        """List the AI 3D generation providers (Tripo, Meshy, Rodin, Hunyuan3D,
        Higgsfield): whether each is configured, its capabilities (text, image,
        rig, retexture, remesh, convert, formats) and which environment variables
        enable it. Call before generating to pick a configured provider."""
        return dumps({"providers": list_providers(), "download_dir": download_dir(), "note": FORMAT_NOTE})

    @mcp.tool(name="maya_gen3d_text_to_3d", annotations={"title": "Generate 3D from text", **EXTERNAL_WRITE})
    async def maya_gen3d_text_to_3d(params: TextTo3DInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Submit a text prompt to an AI 3D provider. Returns the job id and
        status; generation takes 1-5 minutes. Set wait=True to poll here with
        progress, else call maya_gen3d_poll later. Then maya_gen3d_import brings
        the result into Maya. Use for one hero object at a time; prefer free
        libraries (Poly Haven, Sketchfab) for generic props."""
        try:
            provider = get_provider(params.provider)
            job = remember(await provider.submit_text(params.prompt, **_opts(params.options)), params.prompt)
            waited = False
            if params.wait:
                waited = True
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, waited, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_image_to_3d", annotations={"title": "Generate 3D from image", **EXTERNAL_WRITE})
    async def maya_gen3d_image_to_3d(params: ImageTo3DInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Submit a reference image (local path or URL) to an AI 3D provider.
        Local files are uploaded (Tripo, Rodin) or inlined as data URIs (Meshy,
        Hunyuan). Returns the job id; poll with maya_gen3d_poll or set wait=True,
        then import with maya_gen3d_import."""
        try:
            provider = get_provider(params.provider)
            opts = _opts(params.options)
            if params.prompt:
                opts["prompt"] = params.prompt
            if params.extra_images:
                opts["extra_images"] = params.extra_images
            job = remember(await provider.submit_image(params.image, **opts), params.prompt)
            waited = False
            if params.wait:
                waited = True
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, waited, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_poll", annotations={"title": "Poll a generation job", **EXTERNAL_READ})
    async def maya_gen3d_poll(params: PollInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Check a generation job: status (queued, running, succeeded, failed,
        cancelled), progress and available output formats. For Meshy text jobs
        this also kicks off the refine stage when the preview finishes. wait=True
        keeps polling up to max_wait seconds."""
        try:
            provider = get_provider(params.provider)
            job = remember(await provider.poll(params.job_id))
            waited = False
            if params.wait and not job.done:
                waited = True
                job = await wait_for(provider, job, params.max_wait, context)
            out = _describe_wait(job, waited, params.max_wait)
            out["outputs"] = job.outputs
            return dumps(out)
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_import", annotations={"title": "Import a generated model", **EXTERNAL_WRITE})
    async def maya_gen3d_import(params: ImportInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Download a finished generation (default fbx) to the local download
        folder and import it into Maya, tagging the top node with the provider
        and job id. Falls back to another available format when the requested
        one is missing (Tripo can convert on demand). Returns the new node
        names; scale=100 is common when the provider outputs metres."""
        try:
            provider = get_provider(params.provider)
            job = JOBS.get((provider.name, params.job_id))
            if job is None or not job.done:
                job = remember(await provider.poll(params.job_id))
            if job.status != "succeeded":
                return dumps({**job.brief(), "error": "job is %s, cannot import yet" % job.status})
            fmt = params.format.lower()
            note = None
            if fmt not in job.outputs:
                if params.convert_if_missing and provider.capabilities().get("convert"):
                    conv = remember(await provider.convert(params.job_id, fmt))
                    conv = await wait_for(provider, conv, 300.0, context)
                    if conv.status == "succeeded" and fmt in conv.outputs:
                        job = conv
                        note = "converted via job %s" % conv.job_id
                    elif conv.status == "succeeded" and conv.outputs:
                        job = conv
                        fmt = next(iter(conv.outputs))
                        note = "converted via job %s; provider returned %s" % (conv.job_id, fmt)
                    else:
                        return dumps({**conv.brief(), "error": "conversion to %s did not finish" % fmt})
                else:
                    candidates = [f for f in ("fbx", "obj", "usdz", "glb", "gltf") if f in job.outputs]
                    if not candidates:
                        return dumps({**job.brief(), "error": "no model outputs to import", "outputs": job.outputs})
                    note = "%s not available, importing %s instead" % (fmt, candidates[0])
                    fmt = candidates[0]
            # Fresh URLs matter: Tripo links expire within minutes.
            if job.provider == "tripo":
                job = remember(await provider.poll(job.job_id))
            path = await provider.download(job, fmt)
            result = await ctx.raw("gen.import_result", {
                "path": path,
                "name": params.name,
                "group": params.group,
                "scale": params.scale,
                "freeze": params.freeze,
                "center": params.center,
                "provider": provider.name,
                "job_id": params.job_id,
                "prompt": PROMPTS.get((provider.name, params.job_id), ""),
            }, timeout=600.0)
            if isinstance(result, dict):
                result["format"] = fmt
                result["downloaded"] = path
                if note:
                    result["note"] = note
            return dumps(result)
        except Exception as exc:  # noqa: BLE001
            from ._base import error_text

            return error_text(exc) if not isinstance(exc, ProviderError) else _err(exc)

    @mcp.tool(name="maya_gen3d_rig", annotations={"title": "Auto rig a generated model", **EXTERNAL_WRITE})
    async def maya_gen3d_rig(params: RigInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Ask the provider to auto rig a finished generation (Tripo: mixamo
        style biped/quadruped FBX; Meshy: humanoid rig). Returns a new job id;
        poll it, then maya_gen3d_import with format fbx."""
        try:
            provider = get_provider(params.provider)
            opts: Dict[str, Any] = {"rig_type": params.rig_type, "spec": params.spec, "out_format": params.out_format}
            if params.height_meters:
                opts["height_meters"] = params.height_meters
            job = remember(await provider.rig(params.job_id, **opts))
            if params.wait:
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, params.wait, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_retexture", annotations={"title": "Retexture a generated model", **EXTERNAL_WRITE})
    async def maya_gen3d_retexture(params: RetextureInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Generate new textures for a finished job from a style prompt (Tripo,
        Meshy) or a style image (Meshy). Returns a new job id to poll and import."""
        try:
            provider = get_provider(params.provider)
            job = remember(await provider.retexture(params.job_id, prompt=params.prompt, pbr=params.pbr, image_style_url=params.image_style_url), params.prompt)
            if params.wait:
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, params.wait, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_remesh", annotations={"title": "Remesh a generated model", **EXTERNAL_WRITE})
    async def maya_gen3d_remesh(params: RemeshInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Remesh a finished Meshy job to a target polycount and topology (quad
        or triangle). Returns a new job id to poll and import."""
        try:
            provider = get_provider(params.provider)
            job = remember(await provider.remesh(params.job_id, target_polycount=params.target_polycount, topology=params.topology, target_formats=params.formats))
            if params.wait:
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, params.wait, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_gen3d_convert", annotations={"title": "Convert a generated model", **EXTERNAL_WRITE})
    async def maya_gen3d_convert(params: ConvertInput, context: Context = None) -> str:  # type: ignore[assignment]
        """Tripo only: convert a finished job to FBX, OBJ, USDZ, GLTF or STL,
        optionally quad remeshing with a face limit. Returns a new job id whose
        output is the converted file."""
        try:
            provider = get_provider(params.provider)
            job = remember(await provider.convert(params.job_id, params.format, quad=params.quad, face_limit=params.face_limit, fbx_preset=params.fbx_preset))
            if params.wait:
                job = await wait_for(provider, job, params.max_wait, context)
            return dumps(_describe_wait(job, params.wait, params.max_wait))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
