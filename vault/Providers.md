---
tags: [automaya, providers, api]
---
# Providers (researched 2026-09-04)

Every generator implements `Provider3D`: `submit_text`, `submit_image`, `poll`, `download`, optional `rig`, `retexture`, `remesh`, `convert`. Jobs are `GenJob` objects (`provider`, `job_id`, `status`, `progress`, `outputs{format: url}`). Signed URLs expire fast (Tripo about 5 minutes), so `maya_gen3d_import` re-polls before downloading.

| Provider | Env | Base | Notes |
|---|---|---|---|
| Tripo | `TRIPO_API_KEY` | `https://api.tripo3d.ai/v2/openapi` | `POST /task` with `type` text_to_model / image_to_model / convert_model / animate_rig / texture_model; `GET /task/{id}`; envelope `{code, data}`; model version `P1-20260311` |
| Meshy | `MESHY_API_KEY` | `https://api.meshy.ai` | text is two stage (preview then refine, handled inside poll), image `/openapi/v1/image-to-3d`, retexture, remesh, rigging, animations; status PENDING/IN_PROGRESS/SUCCEEDED/FAILED |
| Rodin | `RODIN_API_KEY` or `FAL_KEY` | `https://api.hyper3d.com/api/v2` or `https://queue.fal.run/fal-ai/hyper3d/rodin` | multipart `/rodin`, `/status` by subscription_key, `/download` by task uuid; fal trial key `vibecoding` |
| Hunyuan3D | `HUNYUAN_SECRET_ID` + `HUNYUAN_SECRET_KEY` (+ `HUNYUAN_REGION`) or `HUNYUAN_LOCAL_URL` | `ai3d.tencentcloudapi.com` v2025-05-13 or local `api_server.py` | TC3-HMAC-SHA256 signing, `SubmitHunyuanTo3DProJob` / `QueryHunyuanTo3DProJob` with Model 3.1, up to 8 `MultiViewImages`, `GenerateType`, `PolygonType`; post jobs `SubmitTextureTo3DJob`, `SubmitHunyuanTo3DUVJob`, `SubmitAutoRiggingJob`, `SubmitReduceFaceJob` take `File3D {Type, Url}` from a finished job; world actions from `HUNYUAN_WORLD_SUBMIT` / `HUNYUAN_WORLD_QUERY` (unverified defaults); local: `POST /send`, `GET /status/{uid}` returns `model_base64` |
| Replicate | `REPLICATE_API_TOKEN` (+ `REPLICATE_3D_MODEL`) | `https://api.replicate.com/v1` | `POST /models/{owner}/{name}/predictions` with `Prefer: wait=0` and `{input}`, `GET /predictions/{id}` status starting/processing/succeeded/failed/canceled; default `tencent/hunyuan-3d-3.1`, any model via `extra.model` |
| Higgsfield | `HIGGSFIELD_API_KEY` + `HIGGSFIELD_API_SECRET` + `HIGGSFIELD_3D_ENDPOINT` | `https://api.higgsfield.ai` | no public 3D REST route documented yet; 3D lives in their MCP `generate_3d`. Provider is a hook that activates when an endpoint is published |
| Poly Haven | none | `https://api.polyhaven.com` | `/assets`, `/search`, `/categories/{type}`, `/files/{id}`; HDRIs to `aiSkyDomeLight`, texture sets to a PBR network, models as FBX with included textures |
| Sketchfab | `SKETCHFAB_API_TOKEN` | `https://api.sketchfab.com/v3` | `/search?type=models&downloadable=true`, `/models/{uid}/download` gives glb/gltf/usdz/source; prefer `source` when it is fbx/obj |
| Poly Pizza | `POLYPIZZA_API_KEY` | `https://api.poly.pizza/v1.1` | `x-auth-token` header, `/search/{keyword}`, all models are GLB (needs a glTF importer) |

Adding a provider: subclass `Provider3D` in `src/automaya_mcp/providers/`, register in `registry.PROVIDERS`, mock its HTTP in `tests/test_providers.py` with respx.
