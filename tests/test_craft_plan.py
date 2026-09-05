"""Tests for maya_plan_scene and maya_quality_gate."""
from __future__ import annotations

from tests.conftest import parse
from tests.synthetic_images import dark, noisy

from automaya_mcp import imaging
from automaya_mcp.tools import craft_plan


# unit ------------------------------------------------------------------------------
def test_plan_fills_shots_assets_and_lighting():
    plan = craft_plan.plan_scene("A rainy night street with a diner, a parked car and two people, 3 shots", kind="previs")
    assert plan["setting"] == "exterior" and plan["lighting"]["time_of_day"] == "night" and plan["lighting"]["kelvin"] == 4000
    assert [s["name"] for s in plan["shots"]] == ["sh010", "sh020", "sh030"]
    assert plan["shots"][0]["camera"]["focal_length"] == 24 and plan["shots"][0]["range"] == [1001, 1096]
    assert plan["shots"][1]["range"][0] == 1097
    names = [a["name"] for a in plan["assets"]]
    assert "car" in names and "street" in names and "person" in names and names[-1] == "ground or floor"
    car = next(a for a in plan["assets"] if a["name"] == "car")
    assert car["dims_cm"] == [450, 150, 180] and car["source"].startswith("maya_procgen_vehicle_proxy")
    assert plan["render"]["resolution"] == [1920, 1080] and plan["gate"]["kind"] == "previs"
    assert len(plan["passes"]) == 5 and plan["units"].startswith("cm")


def test_plan_interior_and_unknown_nouns():
    plan = craft_plan.plan_scene("moody kitchen at dawn", kind="lookdev", shots=1, aspect=2.39)
    assert plan["setting"] == "interior" and plan["lighting"]["time_of_day"] == "dawn"
    assert any(a["name"] == "kitchen" for a in plan["assets"])
    assert plan["render"]["resolution"] == [1920, 803] and "Z" in plan["render"]["aovs"]
    empty = craft_plan.plan_scene("something abstract", shots=2)
    assert empty["assets"][0]["dims_cm"] is None and "note" in empty["assets"][0] and len(empty["shots"]) == 2


def test_rank_fixes_merges_sources():
    critique = imaging.analyze(dark())
    lint = {"problems": {"unfrozen_transforms": [{"node": "|a", "detail": "scaled", "fix": "freeze", "severity": "warning"}],
                         "missing_textures": [{"node": "file1", "detail": "gone", "fix": "repath", "severity": "error"}]}}
    materials = {"flagged": [{"material": "concrete", "type": "aiStandardSurface", "issues": ["albedo 0.95 above 0.9"], "fix": "lower base colour"}]}
    fixes = craft_plan.rank_fixes(critique, lint, materials)
    sev = [f["severity"] for f in fixes]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])
    assert fixes[0]["area"] == "image" and fixes[0]["tool"] == "maya_light_three_point"
    lint_fixes = {f["issue"]: f for f in fixes if f["area"] == "scene"}
    assert lint_fixes["lint: missing_textures"]["severity"] == "medium" and lint_fixes["lint: missing_textures"]["tool"] == "maya_repath_textures"
    assert lint_fixes["lint: unfrozen_transforms"]["severity"] == "low" and lint_fixes["lint: unfrozen_transforms"]["tool"] == "maya_freeze_transforms"
    mat = next(f for f in fixes if f["area"] == "material")
    assert mat["measure"] == "concrete" and mat["change"] == "lower base colour"
    assert craft_plan.rank_fixes(None, None, None) == []


# integration ------------------------------------------------------------------------
def _viewport(fake_maya, img):
    fake_maya.responses["about"] = lambda **kw: False if kw.get("batch") else "2024"
    fake_maya.responses["getPanel"] = lambda **kw: "modelPanel4" if kw.get("withFocus") else ("modelPanel" if kw.get("typeOf") else [])
    fake_maya.responses["modelPanel"] = "persp"

    def playblast(**kw):
        with open(kw["completeFilename"], "wb") as fh:
            fh.write(imaging.png_bytes(img))
        return kw["completeFilename"]

    fake_maya.responses["playblast"] = playblast


async def test_tool_plan_scene(call_tool):
    data = parse(await call_tool("maya_plan_scene", {"params": {"brief": "sunset rooftop with a sofa, 2 shots"}}))
    assert data["lighting"]["time_of_day"] == "dusk" and len(data["shots"]) == 2
    assert any(a["name"] == "sofa" and a["dims_cm"] == [220, 85, 95] for a in data["assets"])


async def test_tool_quality_gate_fails_on_dark_render(call_tool, fake_maya):
    _viewport(fake_maya, dark())
    data = parse(await call_tool("maya_quality_gate", {"params": {"kind": "previs", "source": "viewport"}}))
    assert data["verdict"] == "FAIL" and data["passed"] is False
    assert data["steps"] == {"lint": "ok", "materials": "ok", "critique": "ok"}
    assert data["fixes"][0]["severity"] == "high" and data["fixes"][0]["tool"] == "maya_light_three_point"
    assert data["counts"]["high"] >= 1 and data["critique_summary"]["clipping"]["shadows_pct"] == 100.0


async def test_tool_quality_gate_passes_clean_scene(call_tool, fake_maya):
    _viewport(fake_maya, noisy())
    data = parse(await call_tool("maya_quality_gate", {"params": {"kind": "lookdev", "run_materials": False, "max_medium": 3}}))
    assert data["verdict"] == "PASS" and data["counts"]["high"] == 0 and "materials" not in data["steps"]
    assert data["checklist"][0]["item"] == "Exposure"


async def test_tool_quality_gate_reports_capture_failure(call_tool, fake_maya):
    # batch Maya: the screenshot step fails, the gate says so instead of passing
    data = parse(await call_tool("maya_quality_gate", {"params": {"run_lint": False, "run_materials": False}}))
    assert data["verdict"] == "FAIL" and data["steps"]["critique"].startswith("Error") and "critique" in data["reason"]


async def test_tool_plan_error_path(call_tool):
    assert (await call_tool("maya_plan_scene", {"params": {"brief": "a thing", "kind": "vfx"}})).startswith("Error: kind")
    assert (await call_tool("maya_quality_gate", {"params": {"source": "opengl"}})).startswith("Error: source")
