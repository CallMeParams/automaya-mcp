"""Free asset library tools: Poly Haven, Sketchfab, Poly Pizza.

Search and download happen on the server; the plugin imports local files,
builds a skydome from an HDRI, or wires a PBR network from a texture set.
"""
from __future__ import annotations

from typing import Any, List

from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, ConfigDict, Field

from ..providers import ProviderError
from ..providers.assets import PolyHaven, PolyPizza, Sketchfab
from ._base import EXTERNAL_READ, EXTERNAL_WRITE, ToolContext, dumps, error_text

GLTF_NOTE = "glb/gltf need a glTF importer in Maya 2024 (Autodesk glTF plugin). Prefer 'source' when the source archive is fbx or obj."


def _err(exc: Exception) -> str:
    if isinstance(exc, ProviderError):
        return "Error: %s" % exc
    return error_text(exc)


class PolyHavenCategoriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(default="hdris", description="hdris | textures | models", examples=["textures"])


class PolyHavenSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", description="Free text search, e.g. 'brick wall' or 'sunset'. Empty lists everything of the type.")
    type: str = Field(default="all", description="hdris | textures | models | all")
    categories: List[str] | None = Field(default=None, description="Poly Haven categories to filter by (see maya_polyhaven_categories)")
    limit: int = Field(default=20, ge=1, le=100)


class PolyHavenDownloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(..., description="Poly Haven asset id, e.g. 'kloofendal_48d_partly_cloudy_puresky'")
    type: str = Field(..., description="hdris | textures | models")
    resolution: str = Field(default="2k", description="1k | 2k | 4k | 8k (falls back to the nearest available)")
    format: str | None = Field(default=None, description="hdris: hdr|exr. textures: jpg|png|exr. models: fbx|gltf. Defaults hdr / jpg / fbx.")
    name: str | None = Field(default=None, description="Node or material name in Maya")
    assign_to: List[str] | None = Field(default=None, description="textures only: assign the material to these nodes")
    intensity: float = Field(default=1.0, description="hdris only: skydome intensity")
    rotation: float = Field(default=0.0, description="hdris only: Y rotation in degrees")
    scale: float | None = Field(default=None, gt=0, description="models only: uniform scale after import")
    import_into_maya: bool = Field(default=True, description="False to only download and return the path(s)")


class SketchfabSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, description="Search text")
    count: int = Field(default=20, ge=1, le=24)
    cursor: str | None = Field(default=None, description="Pagination cursor from a previous result")
    license: str | None = Field(default=None, description="Sketchfab license slug filter, e.g. 'by' (CC BY) or 'cc0'")
    downloadable: bool = Field(default=True)


class SketchfabPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: str = Field(..., min_length=4, description="Sketchfab model uid")


class SketchfabDownloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: str = Field(..., min_length=4, description="Sketchfab model uid")
    format: str = Field(default="source", description="source | glb | gltf | usdz. " + GLTF_NOTE)
    name: str | None = Field(default=None, description="Group/top node name in Maya")
    scale: float | None = Field(default=None, gt=0)
    import_into_maya: bool = Field(default=True)


class PolyPizzaSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, description="Keyword, e.g. 'tree'")
    limit: int = Field(default=20, ge=1, le=50)
    page: int = Field(default=0, ge=0)


class PolyPizzaDownloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(..., min_length=1, description="Poly Pizza model id from search")
    name: str | None = Field(default=None)
    scale: float | None = Field(default=None, gt=0)
    import_into_maya: bool = Field(default=True)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    polyhaven = PolyHaven()
    sketchfab = Sketchfab()
    polypizza = PolyPizza()

    @mcp.tool(name="maya_polyhaven_categories", annotations={"title": "Poly Haven categories", **EXTERNAL_READ})
    async def maya_polyhaven_categories(params: PolyHavenCategoriesInput) -> str:
        """List Poly Haven categories for hdris, textures or models with asset
        counts. No API key needed (CC0 assets)."""
        try:
            return dumps(await polyhaven.categories(params.type))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_polyhaven_search", annotations={"title": "Search Poly Haven", **EXTERNAL_READ})
    async def maya_polyhaven_search(params: PolyHavenSearchInput) -> str:
        """Search Poly Haven (free CC0 HDRIs, PBR texture sets and models).
        Returns ids, names, categories and thumbnails. Use the id with
        maya_polyhaven_download."""
        try:
            return dumps({"results": await polyhaven.search(params.query, params.type, params.categories, params.limit)})
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_polyhaven_download", annotations={"title": "Download a Poly Haven asset into Maya", **EXTERNAL_WRITE})
    async def maya_polyhaven_download(params: PolyHavenDownloadInput) -> str:
        """Download a Poly Haven asset and bring it into Maya: an HDRI becomes an
        aiSkyDomeLight (or an environment sphere without Arnold), a texture set
        becomes a standardSurface PBR network (optionally assigned), a model is
        imported as FBX with its textures. Returns the created nodes and paths."""
        t = params.type.lower().rstrip("s") + "s"
        try:
            if t == "hdris":
                dl = await polyhaven.download_hdri(params.asset_id, params.resolution, params.format or "hdr")
                if not params.import_into_maya:
                    return dumps(dl)
                result = await ctx.raw("assets.create_skydome", {"path": dl["path"], "name": params.name or params.asset_id, "intensity": params.intensity, "rotation": params.rotation})
                return dumps({"download": dl, "maya": result})
            if t == "textures":
                dl = await polyhaven.download_texture_set(params.asset_id, params.resolution, params.format or "jpg")
                if not params.import_into_maya:
                    return dumps(dl)
                result = await ctx.raw("assets.import_texture_set", {"maps": dl["maps"], "name": params.name or params.asset_id, "assign_to": params.assign_to})
                return dumps({"download": dl, "maya": result})
            if t == "models":
                dl = await polyhaven.download_model(params.asset_id, params.resolution, params.format or "fbx")
                if not params.import_into_maya:
                    return dumps(dl)
                result = await ctx.raw("assets.import_model", {"path": dl["path"], "name": params.name, "scale": params.scale}, timeout=600.0)
                return dumps({"download": dl, "maya": result})
            return "Error: type must be hdris, textures or models"
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_sketchfab_search", annotations={"title": "Search Sketchfab", **EXTERNAL_READ})
    async def maya_sketchfab_search(params: SketchfabSearchInput) -> str:
        """Search downloadable Sketchfab models (Creative Commons). Returns uid,
        name, author, license, face count and a thumbnail URL. Preview with
        maya_sketchfab_preview, then maya_sketchfab_download (needs
        SKETCHFAB_API_TOKEN). Respect the license attribution."""
        try:
            return dumps(await sketchfab.search(params.query, params.count, params.cursor, params.license, params.downloadable))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_sketchfab_preview", annotations={"title": "Preview a Sketchfab model", **EXTERNAL_READ})
    async def maya_sketchfab_preview(params: SketchfabPreviewInput) -> Any:
        """Fetch a Sketchfab model's thumbnail as an image plus its metadata
        (name, author, license, counts) so you can judge it before downloading."""
        try:
            data, fmt, info = await sketchfab.thumbnail_bytes(params.uid)
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
        meta = {
            "uid": info.get("uid"),
            "name": info.get("name"),
            "author": (info.get("user") or {}).get("displayName"),
            "license": (info.get("license") or {}).get("label") if isinstance(info.get("license"), dict) else info.get("license"),
            "face_count": info.get("faceCount"),
            "vertex_count": info.get("vertexCount"),
            "downloadable": info.get("isDownloadable"),
            "description": (info.get("description") or "")[:400],
        }
        return [Image(data=data, format=fmt), dumps(meta, 4000)]

    @mcp.tool(name="maya_sketchfab_download", annotations={"title": "Download a Sketchfab model into Maya", **EXTERNAL_WRITE})
    async def maya_sketchfab_download(params: SketchfabDownloadInput) -> str:
        """Download a Sketchfab model (needs SKETCHFAB_API_TOKEN and a
        downloadable model) and import it. format 'source' fetches the original
        upload, best when it is fbx/obj; glb/gltf need a glTF importer in Maya
        2024. Archives are extracted and the first importable file is used."""
        try:
            dl = await sketchfab.download(params.uid, params.format)
            if not params.import_into_maya:
                return dumps(dl)
            path = dl["path"]
            if path.lower().endswith(".zip"):
                return dumps({"download": dl, "error": "archive contained no importable model file", "note": GLTF_NOTE})
            result = await ctx.raw("assets.import_model", {"path": path, "name": params.name, "group": bool(params.name), "scale": params.scale}, timeout=600.0)
            return dumps({"download": dl, "maya": result})
        except Exception as exc:  # noqa: BLE001
            text = _err(exc)
            if "glTF" in text:
                text += "\nHint: retry with format='source' or pick a model whose source is fbx/obj."
            return text

    @mcp.tool(name="maya_polypizza_search", annotations={"title": "Search Poly Pizza", **EXTERNAL_READ})
    async def maya_polypizza_search(params: PolyPizzaSearchInput) -> str:
        """Search Poly Pizza low poly models (CC BY / CC0; needs POLYPIZZA_API_KEY).
        Returns id, title, triangle count, licence, attribution and thumbnail."""
        try:
            return dumps(await polypizza.search(params.query, params.limit, params.page))
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @mcp.tool(name="maya_polypizza_download", annotations={"title": "Download a Poly Pizza model into Maya", **EXTERNAL_WRITE})
    async def maya_polypizza_download(params: PolyPizzaDownloadInput) -> str:
        """Download a Poly Pizza model (GLB) and import it. Requires a glTF
        importer in Maya 2024; without one the download path is returned so
        you can convert it externally. Includes the attribution string."""
        try:
            dl = await polypizza.download(params.model_id)
            if not params.import_into_maya:
                return dumps(dl)
            try:
                result = await ctx.raw("assets.import_model", {"path": dl["path"], "name": params.name, "scale": params.scale}, timeout=600.0)
            except Exception as exc:  # noqa: BLE001
                return dumps({"download": dl, "error": str(exc), "note": GLTF_NOTE})
            return dumps({"download": dl, "maya": result})
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
