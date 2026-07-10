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


def test_display_exposes_milestone_detail_and_graph_positioning_hooks() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "function milestoneDetailCard(milestoneId, scenario)" in html
    assert "function constraintDetailText(constraint)" in html
    assert "function scrollToMilestoneNode(milestoneId)" in html
    assert 'group.dataset.milestoneId = milestoneId' in html
    assert 'body.append(svg, milestoneDetailCard(state.selectedMilestoneId, scenario))' in html
    assert "scrollToMilestoneNode(milestoneId)" in html


def test_display_supports_panel_resize_scale_and_graph_legend() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert '--trajectory-fr: 34' in html
    assert '--graph-fr: 30' in html
    assert '--stage-fr: 36' in html
    assert 'class="panel-splitter trajectory-graph-splitter"' in html
    assert 'class="panel-splitter graph-stage-splitter"' in html
    assert "function initPanelResize()" in html
    assert "function updatePanelScales()" in html
    assert "--panel-scale" in html
    assert "graphLegend()" in html
    assert "通过" in html
    assert "失败或 fatal" in html


def test_stage_score_sections_use_unified_detail_heading_style() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert 'const heading = el("summary", "detail-summary")' in html
    assert '.score-section .section-title' not in html
    assert '.score-section summary {' not in html


def test_display_keeps_selected_stage_visible_after_stage_click() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "function scrollToStageCard(stageId)" in html
    assert "card.dataset.stageId = stageId" in html
    assert "requestAnimationFrame(() => scrollToStageCard(stageId))" in html
    assert 'panel.querySelector(`[data-stage-id="${stageId}"]`)' in html
