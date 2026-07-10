from __future__ import annotations

from pathlib import Path


def test_display_uses_fixed_stage_title() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert '${report.stage_id || "unknown"}:${report.status || "unknown"}' in html
    assert "stageIdWithoutRuntime" not in html
    assert "最终审计 stN" not in html


def test_display_renders_stage_definitions_as_skeleton() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "scenario.stage_definitions || []" in html
    assert "emptyStageReport(definition)" in html
    assert "stageDefinitionForMilestone(milestoneId, scenario)" in html
