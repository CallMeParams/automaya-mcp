"""Planning and gating: turn a brief into a build plan skeleton, and run the
quality gate (lint + material report + critique) that decides whether a shot
is done. Both are the bookends of the build, render, critique, fix loop."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import imaging
from ._base import READ, ToolContext, dumps, error_text
from .craft_critique import CHECKLISTS, KINDS, SOURCES, capture

# Real world sizes in cm (w, h, d) the agent should not have to guess.
ASSET_DIMS: Dict[str, Dict[str, Any]] = {
    "door": {"dims": [90, 210, 5], "source": "maya_procgen_room_shell openings"},
    "window": {"dims": [120, 140, 10], "source": "maya_procgen_building window params"},
    "table": {"dims": [160, 75, 90], "source": "maya_procgen_furniture_proxy table"},
    "desk": {"dims": [140, 75, 70], "source": "maya_procgen_furniture_proxy desk"},
    "chair": {"dims": [45, 90, 50], "source": "maya_procgen_furniture_proxy chair"},
    "sofa": {"dims": [220, 85, 95], "source": "maya_procgen_furniture_proxy sofa"},
    "bed": {"dims": [160, 55, 200], "source": "maya_procgen_furniture_proxy bed"},
    "shelf": {"dims": [90, 200, 35], "source": "maya_procgen_furniture_proxy shelf"},
    "lamp": {"dims": [40, 150, 40], "source": "maya_procgen_furniture_proxy lamp + maya_light_practical"},
    "car": {"dims": [450, 150, 180], "source": "maya_procgen_vehicle_proxy car or Sketchfab"},
    "van": {"dims": [520, 200, 200], "source": "maya_procgen_vehicle_proxy van"},
    "bus": {"dims": [1200, 320, 255], "source": "maya_procgen_vehicle_proxy bus"},
    "person": {"dims": [50, 170, 30], "source": "maya_create_primitive capsule proxy or Poly Haven scan"},
    "character": {"dims": [50, 170, 30], "source": "AI generation (one hero) or library rig"},
    "tree": {"dims": [600, 1200, 600], "source": "maya_procgen_tree_proxy"},
    "building": {"dims": [1500, 1280, 1200], "source": "maya_procgen_building"},
    "house": {"dims": [1000, 700, 900], "source": "maya_procgen_building floors=2 pitched roof"},
    "street": {"dims": [3000, 0, 8000], "source": "maya_procgen_street_block"},
    "road": {"dims": [700, 0, 8000], "source": "maya_procgen_street_block"},
    "room": {"dims": [500, 280, 400], "source": "maya_procgen_room_shell"},
    "kitchen": {"dims": [400, 270, 350], "source": "maya_procgen_room_shell + furniture proxies"},
    "office": {"dims": [600, 280, 500], "source": "maya_procgen_room_shell + desks"},
    "stairs": {"dims": [100, 280, 400], "source": "maya_procgen_stairs"},
    "column": {"dims": [60, 400, 60], "source": "maya_procgen_column"},
    "fence": {"dims": [1000, 120, 5], "source": "maya_procgen_fence"},
    "rock": {"dims": [150, 100, 120], "source": "maya_procgen_rock"},
    "terrain": {"dims": [10000, 300, 10000], "source": "maya_procgen_terrain"},
    "ship": {"dims": [3000, 800, 600], "source": "AI generation from turnaround views, then procgen details"},
    "spaceship": {"dims": [2500, 500, 1500], "source": "AI generation (one hero) or kitbash"},
}

LINT_MEDIUM = ("non_manifold", "lamina", "missing_textures", "far_from_origin", "bbox_scale", "zero_area")
LINT_TOOLS = {
    "non_manifold": "maya_cleanup_mesh", "lamina": "maya_cleanup_mesh", "zero_area": "maya_cleanup_mesh", "ngons": "maya_cleanup_mesh",
    "unfrozen_transforms": "maya_freeze_transforms", "non_uniform_scale": "maya_freeze_transforms", "missing_textures": "maya_repath_textures",
    "duplicate_names": "maya_rename", "empty_groups": "maya_delete", "unused_materials": "maya_remove_unused_materials",
    "construction_history": "maya_delete_history", "far_from_origin": "maya_transform", "bbox_scale": "maya_transform",
}
PLURALS = {"people": "person", "men": "person", "women": "person", "man": "person", "woman": "person", "cars": "car", "trees": "tree", "houses": "house", "buildings": "building", "shelves": "shelf", "benches": "chair"}
SHOT_SIZES = ["wide establishing", "medium", "close up", "insert"]
TIME_WORDS = {
    "night": ("night", 4000, "practicals + moonlight (maya_light_sun_sky at negative elevation or hdri_dome night)"),
    "dawn": ("dawn", 3500, "maya_light_sun_sky low elevation, warm key, cool fill"),
    "sunrise": ("dawn", 3500, "maya_light_sun_sky elevation 5 to 10 degrees"),
    "dusk": ("dusk", 3200, "maya_light_sun_sky elevation 2 to 5 degrees, sky turbidity high"),
    "sunset": ("dusk", 3200, "maya_light_sun_sky elevation 2 to 5 degrees, sky turbidity high"),
    "golden": ("golden hour", 3400, "maya_light_sun_sky elevation 10 degrees"),
    "overcast": ("overcast day", 6500, "maya_light_hdri_dome overcast, soft shadows"),
    "rain": ("overcast day", 6500, "maya_light_hdri_dome overcast + maya_fx_precipitation_preset"),
    "noon": ("midday", 5600, "maya_light_sun_sky elevation 60 degrees"),
    "midday": ("midday", 5600, "maya_light_sun_sky elevation 60 degrees"),
    "morning": ("morning", 5000, "maya_light_sun_sky elevation 25 degrees"),
    "afternoon": ("afternoon", 5200, "maya_light_sun_sky elevation 35 degrees"),
}
INTERIOR_WORDS = ("interior", "room", "kitchen", "office", "bedroom", "inside", "hall", "corridor", "studio", "apartment", "lobby")
EXTERIOR_WORDS = ("exterior", "street", "city", "outside", "landscape", "forest", "beach", "desert", "park", "rooftop", "road", "space")


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: str = Field(..., description="What to build, in plain words", min_length=3, max_length=4000, examples=["A rainy night street with a diner and a parked car, two shots"])
    kind: str = Field(default="previs", description="previs | lookdev | arch, sets the quality gate rubric")
    shots: int | None = Field(default=None, ge=1, le=20, description="How many shots to plan; default from the brief or 1")
    fps: float = Field(default=24.0, gt=0, description="Frames per second for the shot ranges")
    aspect: float = Field(default=16.0 / 9.0, gt=0, description="Delivery aspect ratio")


class GateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="previs", description="previs | lookdev | arch rubric")
    camera: str | None = Field(default=None, description="Camera to capture through")
    width: int = Field(default=1280, ge=64, le=4096, description="Capture width")
    height: int = Field(default=720, ge=64, le=4096, description="Capture height")
    source: str = Field(default="viewport", description="viewport (fast) or arnold (real render)")
    run_lint: bool = Field(default=True, description="Run maya_find_problems")
    run_materials: bool = Field(default=True, description="Run lookdev.material_report when the plugin has it")
    max_medium: int = Field(default=2, ge=0, le=20, description="How many medium findings are tolerated before failing")


def plan_scene(brief: str, kind: str = "previs", shots: int | None = None, fps: float = 24.0, aspect: float = 16.0 / 9.0) -> Dict[str, Any]:
    """Skeleton plan from a brief. Pure function so it is easy to test and reuse."""
    text = brief.lower()
    words = set(re.findall(r"[a-z]+", text))
    interior = any(w in text for w in INTERIOR_WORDS)
    exterior = any(w in text for w in EXTERIOR_WORDS)
    setting = "interior" if interior and not exterior else ("exterior" if exterior else "unspecified (assume exterior)")
    time_key = next((k for k in TIME_WORDS if k in text), None)
    time_label, kelvin, light_recipe = TIME_WORDS[time_key] if time_key else ("day", 5600, "maya_light_sun_sky elevation 45 degrees or maya_light_hdri_dome daylight")

    assets: List[Dict[str, Any]] = []
    for alias, key in PLURALS.items():
        if alias in words:
            words.add(key)
    for key, spec in ASSET_DIMS.items():
        if key in words or (key + "s") in words:
            assets.append({"name": key, "dims_cm": spec["dims"], "source": spec["source"], "status": "todo"})
    if not assets:
        assets.append({"name": "hero subject from the brief", "dims_cm": None, "source": "maya_procgen_* or AI generation for one hero", "status": "todo", "note": "no known nouns found; fill dims before building"})
    assets.append({"name": "ground or floor", "dims_cm": [5000, 0, 5000] if setting != "interior" else [600, 0, 600], "source": "maya_create_primitive plane or maya_procgen_terrain", "status": "todo"})

    m = re.search(r"(\d+)\s*shots?", text)
    count = shots or (int(m.group(1)) if m else 1)
    count = max(1, min(20, count))
    shot_list = []
    frame = 1001
    for i in range(count):
        size = SHOT_SIZES[i % len(SHOT_SIZES)]
        focal = {"wide establishing": 24, "medium": 35, "close up": 65, "insert": 100}[size]
        length = 96 if size == "wide establishing" else 72
        shot_list.append({
            "name": "sh%03d" % ((i + 1) * 10),
            "size": size,
            "camera": {"name": "sh%03d_cam" % ((i + 1) * 10), "focal_length": focal, "sensor_width": 36.0, "aspect": round(aspect, 4), "height_cm": 160},
            "range": [frame, frame + length - 1],
            "fps": fps,
            "intent": "fill in: what the audience learns in this shot",
            "tools": ["maya_create_camera", "maya_frame_selected", "maya_create_sequence_shot"],
        })
        frame += length

    lighting = {
        "setting": setting,
        "time_of_day": time_label,
        "kelvin": kelvin,
        "recipe": light_recipe,
        "key": "sun or window (maya_light_sun_sky / maya_light_interior_portals)" if setting != "interior" or time_key not in ("night",) else "practicals (maya_light_practical) with a soft fill",
        "fill": "sky dome or bounce, 2 to 3 stops under key",
        "rim": "optional, separate subject from background",
        "exposure": "maya_light_exposure ev by eye then check with maya_critique_analyze: mean luminance 0.3 to 0.55",
        "tools": ["maya_light_sun_sky", "maya_light_hdri_dome", "maya_light_three_point", "maya_light_practical", "maya_light_exposure"],
    }
    render = {
        "renderer": "arnold",
        "preview": "maya_lookdev_render_preset preview, 960x540, for the loop",
        "final": "maya_lookdev_render_preset production at delivery size",
        "color": "maya_lookdev_color_management aces13",
        "resolution": [1920, int(round(1920 / aspect))],
        "aovs": ["beauty", "diffuse", "specular", "Z"] if kind == "lookdev" else ["beauty"],
        "tools": ["maya_render_frame", "maya_render_and_critique", "maya_quality_gate"],
    }
    return {
        "brief": brief,
        "kind": kind,
        "units": "cm, y up, real world scale",
        "setting": setting,
        "shots": shot_list,
        "assets": assets,
        "lighting": lighting,
        "render": render,
        "gate": {"kind": kind, "checklist": CHECKLISTS.get(kind, CHECKLISTS["previs"]), "pass_rule": "maya_quality_gate passes: no high findings, at most two medium, lint clean"},
        "passes": [
            "1 layout: ground, hero blocks at real dims, cameras from the shot list, screenshot",
            "2 detail: procgen or library assets replacing blocks, scatter dressing, freeze transforms",
            "3 lighting: recipe above, exposure, render preview, maya_render_and_critique",
            "4 lookdev: measured materials, wear, colour management, critique again",
            "5 gate: maya_quality_gate; fix the ranked list; repeat until pass",
        ],
        "open_questions": ["confirm hero dims", "confirm time of day and mood", "confirm delivery aspect and length"],
    }


def rank_fixes(critique: Dict[str, Any] | None, lint: Any, materials: Any) -> List[Dict[str, Any]]:
    fixes: List[Dict[str, Any]] = []
    if isinstance(critique, dict):
        for f in critique.get("findings", []):
            fixes.append({"severity": f["severity"], "area": "image", "issue": f["issue"], "measure": f["measure"], "tool": f["fix"]["tool"], "change": f["fix"]["change"]})
    if isinstance(lint, dict) and isinstance(lint.get("problems"), dict):
        # intel.find_problems: {"problems": {check: [{node, detail, fix, severity}]}}
        for check, items in lint["problems"].items():
            if not items:
                continue
            first = items[0] if isinstance(items[0], dict) else {}
            sev = "medium" if (check in LINT_MEDIUM or first.get("severity") == "error") else "low"
            fixes.append({"severity": sev, "area": "scene", "issue": "lint: %s" % check, "measure": "%d node(s), e.g. %s" % (len(items), first.get("node") or first.get("nodes") or "?"),
                          "tool": LINT_TOOLS.get(check, "maya_find_problems"), "change": first.get("fix") or first.get("detail") or "see maya_find_problems"})
    if isinstance(materials, dict):
        # lookdev.material_report: {"flagged": [{material, type, issues: [str]}]}
        for item in (materials.get("flagged") or materials.get("issues") or [])[:50]:
            if not isinstance(item, dict):
                continue
            issues = item.get("issues") or item.get("issue") or item.get("problem")
            if not issues:
                continue
            text = "; ".join(str(i) for i in issues) if isinstance(issues, (list, tuple)) else str(issues)
            fixes.append({"severity": "medium", "area": "material", "issue": text, "measure": str(item.get("material") or item.get("name") or ""),
                          "tool": "maya_set_material_attrs", "change": item.get("fix") or "bring the value back into the measured range (maya_lookdev_measured_material)"})
    order = {"high": 0, "medium": 1, "low": 2}
    fixes.sort(key=lambda f: order.get(f["severity"], 3))
    return fixes


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_plan_scene", annotations={"title": "Plan a scene from a brief", **READ})
    async def maya_plan_scene(params: PlanInput) -> str:
        """Turn a brief into a build plan skeleton you then fill in: shot list with
        cameras (focal, sensor, ranges), asset list with real world dimensions in cm
        and where to get each one, a lighting plan (time of day, Kelvin, recipe) and a
        render plan, plus the passes to build in and the gate to pass. No Maya call;
        do this first, then build pass by pass."""
        if params.kind not in KINDS:
            return "Error: kind must be one of %s" % ", ".join(KINDS)
        return dumps(plan_scene(params.brief, params.kind, params.shots, params.fps, params.aspect))

    @mcp.tool(name="maya_quality_gate", annotations={"title": "Quality gate", **READ})
    async def maya_quality_gate(params: GateInput) -> str:
        """Decide whether the shot is done: runs the scene lint (maya_find_problems),
        the material report when the plugin has lookdev.material_report, and the
        image critique on a fresh viewport screenshot or Arnold render, then returns
        pass or fail with the ranked fixes (tool + change). Loop on the fixes until
        it passes; do not call a shot finished before it does."""
        if params.kind not in KINDS:
            return "Error: kind must be one of %s" % ", ".join(KINDS)
        if params.source not in SOURCES:
            return "Error: source must be one of %s" % ", ".join(SOURCES)
        report: Dict[str, Any] = {"kind": params.kind, "steps": {}}
        lint = None
        if params.run_lint:
            try:
                lint = await ctx.raw("intel.find_problems", {"max_per_check": 20}, timeout=300.0)
                report["steps"]["lint"] = "ok"
            except Exception as exc:  # noqa: BLE001
                report["steps"]["lint"] = error_text(exc)
        materials = None
        if params.run_materials:
            try:
                commands = await ctx.raw("core.list_commands", {}, timeout=30.0)
                if isinstance(commands, dict) and "lookdev.material_report" in commands:
                    materials = await ctx.raw("lookdev.material_report", {}, timeout=120.0)
                    report["steps"]["materials"] = "ok"
                else:
                    report["steps"]["materials"] = "skipped: lookdev.material_report not registered in this plugin build"
            except Exception as exc:  # noqa: BLE001
                report["steps"]["materials"] = error_text(exc)
        critique = None
        try:
            cap = await capture(ctx, params.source, params.camera, params.width, params.height)
            critique = imaging.analyze(cap["path"], params.kind)
            critique.pop("histogram", None)
            report["steps"]["critique"] = "ok"
            report["image_path"] = cap["path"]
        except Exception as exc:  # noqa: BLE001
            report["steps"]["critique"] = error_text(exc)
        fixes = rank_fixes(critique, lint, materials)
        highs = sum(1 for f in fixes if f["severity"] == "high")
        mediums = sum(1 for f in fixes if f["severity"] == "medium")
        failed_steps = [k for k, v in report["steps"].items() if str(v).startswith("Error")]
        passed = highs == 0 and mediums <= params.max_medium and not failed_steps and critique is not None
        report.update({
            "passed": passed,
            "verdict": "PASS" if passed else "FAIL",
            "counts": {"high": highs, "medium": mediums, "low": sum(1 for f in fixes if f["severity"] == "low")},
            "reason": "clean" if passed else ("steps failed: %s" % ", ".join(failed_steps) if failed_steps else "%d high, %d medium findings (max %d medium allowed)" % (highs, mediums, params.max_medium)),
            "fixes": fixes,
            "critique_summary": {k: critique[k] for k in ("luminance", "clipping", "rms_contrast", "colour", "score") if critique and k in critique} if critique else None,
            "checklist": CHECKLISTS[params.kind],
        })
        return dumps(report)
