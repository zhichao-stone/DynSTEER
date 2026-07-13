from __future__ import annotations

from pathlib import Path


def test_display_uses_searchable_case_combobox() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "function renderRunRow()" in html
    assert "function renderScenarioRow()" in html
    assert "run-search-input" in html
    assert "case-search-input" in html
    assert "function renderTopbarSelector(config)" in html
    assert "function renderTopbarOptions(container, matches, activeIndex, config)" in html
    assert "function filterSelectorItemsByPrefix(items, query, labelForItem)" in html
    assert "width: min(760px, 100%);" in html
    assert "flex: 0 1 760px;" in html
    assert "width: min(420px, 100%);" in html
    assert "flex: 0 1 420px;" in html
    assert ".scenario-row {\n      position: relative;\n      overflow: visible;" in html
    assert ".run-row {\n      border-bottom: 1px solid var(--line);\n      position: relative;\n      overflow: visible;" in html
    assert ".topbar-select:focus-within" in html
    assert "max-height: calc(34px * 9 + 12px);" in html
    assert "top: 50%;" in html
    assert "transform: translateY(-65%) rotate(45deg);" in html
    assert "suppressNextClickOpen" in html
    assert 'input.addEventListener("mousedown"' in html
    assert "function selectedMatchIndex(matches, selectedIndex)" in html
    assert "function scrollActiveOptionToCenter(container)" in html
    assert "selectedMatchIndex(matches, config.selectedIndex)" in html
    assert "window.requestAnimationFrame" in html
    assert "active.offsetTop - (container.clientHeight - active.offsetHeight) / 2" in html
    assert "function runId(run)" in html
    assert "function scenarioCaseId(scenario)" in html
    assert ".startsWith(normalizedQuery)" in html
    assert "无匹配 case" in html
    assert "无匹配 run" in html
    assert "scenario-button" not in html
    assert "run-button" not in html


def test_display_expands_selected_milestone_in_place() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "function milestoneInlineDetail(milestoneId, scenario" in html
    assert "function selectedMilestoneBox(milestoneId)" in html
    assert "state.selectedMilestoneId = isSelected ? null : milestoneId;" in html
    assert "width: isSelected ? 320 : 148" in html
    assert "height: isSelected ? 218 : 48" in html
    assert 'meta.setAttribute("y", "38");' in html
    assert "foreignObject" in html
    assert "function appendConstraintDetailSection(container, constraints)" in html
    assert 'const item = el("details", "constraint-item");' in html
    assert "function constraintItemTitle(constraint, index)" in html
    assert 'const section = el("details");' in html
    assert 'const heading = el("summary", "detail-summary");' in html
    assert "event.stopPropagation()" in html
    assert "body.append(svg, milestoneDetailCard(state.selectedMilestoneId, scenario))" not in html
    assert "function milestoneDetailCard(milestoneId, scenario)" not in html


def test_display_uses_compact_graph_and_two_row_legend() -> None:
    html = Path("display/index.html").read_text(encoding="utf-8")

    assert "min-width: 0;" in html
    assert "overflow-x: hidden;" in html
    assert "const layerGap = 92;" in html
    assert "Math.max(220," in html
    assert "function legendRow(items)" in html
    assert 'legend.append(legendRow([["pass", "通过"], ["fail", "失败或 fatal"]])' in html
    assert 'legend.append(legendRow([["selected", "当前选中/阶段路径"], ["virtual", "虚拟节点"]])' in html
