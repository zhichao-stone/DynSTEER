from __future__ import annotations

import json
from pathlib import Path

from display.build import build_display_data, write_display_data_js


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_display_data_merges_run_and_result_payloads(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    run_dir = runs_dir / "toolsandbox" / "run_1" / "cellular_off"
    result_dir = results_dir / "toolsandbox" / "run_1" / "cellular_off"

    _write_json(
        run_dir / "trajectory.json",
        {
            "run_id": "run_1",
            "task_id": "toolsandbox::cellular_off",
            "steps": [
                {
                    "step_id": "s1",
                    "index": 1,
                    "actor": "agent",
                    "event_type": "tool_call",
                    "content": "call tool",
                },
                {
                    "step_id": "s2",
                    "index": 2,
                    "actor": "environment",
                    "event_type": "tool_result",
                    "content": "ok",
                },
            ],
        },
    )
    _write_json(
        run_dir / "raw_summary.json",
        {
            "milestone_graph_summary": {
                "nodes": [{"milestone_id": "m0", "name": "里程碑0", "required": True}],
                "edges": [],
            },
            "stage_settlements": [
                {
                    "settlement_id": "st1",
                    "kind": "milestone",
                    "milestone_id": "m0",
                    "start_step_index": 1,
                    "end_step_index": 2,
                    "status": "pass",
                    "score": 1.0,
                }
            ],
            "milestone_match_attempts": [
                {"step_index": 2, "step_id": "s2", "selected_milestone_id": "m0"}
            ],
        },
    )
    _write_json(
        result_dir / "summary.json",
        {
            "run_id": "run_1",
            "task_id": "toolsandbox::cellular_off",
            "overall_score": 1.0,
            "milestone_coverage": "full",
            "elapsed_seconds": 12.5,
            "step_count": 2,
        },
    )
    _write_json(
        result_dir / "report.json",
        {
            "stage_reports": [
                {
                    "stage_id": "runtime:st1",
                    "milestone_id": "m0",
                    "status": "pass",
                    "stage_score": 1.0,
                    "judge_confidence": 0.9,
                    "evidence": ["工具结果满足约束"],
                }
            ],
            "minefield_matches": [],
        },
    )

    data = build_display_data(runs_dir=runs_dir, results_dir=results_dir)

    assert data["runs"][0]["benchmark"] == "toolsandbox"
    assert data["runs"][0]["run_id"] == "run_1"
    scenario = data["runs"][0]["scenarios"][0]
    assert scenario["scenario_id"] == "cellular_off"
    assert scenario["summary"]["overall_score"] == 1.0
    assert scenario["trajectory"]["steps"][0]["actor"] == "agent"
    assert scenario["milestone_graph"]["nodes"][0]["milestone_id"] == "m0"
    assert scenario["stage_reports"][0]["stage_id"] == "runtime:st1"
    assert scenario["stage_settlements"][0]["start_step_index"] == 1
    assert scenario["match_attempts"][0]["selected_milestone_id"] == "m0"


def test_build_display_data_uses_adapted_augmented_graph_and_stage_anchor(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    data_dir = tmp_path / "data"
    run_dir = runs_dir / "toolsandbox" / "run_1" / "case_a"
    result_dir = results_dir / "toolsandbox" / "run_1" / "case_a"

    _write_json(
        run_dir / "trajectory.json",
        {
            "task_id": "toolsandbox::case_a",
            "steps": [{"step_id": "s1", "index": 1, "actor": "agent", "event_type": "message"}],
        },
    )
    _write_json(
        run_dir / "raw_summary.json",
        {
            "milestone_graph_summary": {
                "nodes": [{"milestone_id": "m0", "name": "ToolSandbox milestone 0"}],
                "edges": [],
            },
            "stage_settlements": [
                {
                    "settlement_id": "st1",
                    "kind": "milestone",
                    "milestone_id": "m0",
                    "start_step_index": 1,
                    "end_step_index": 1,
                    "metadata": {"stage_anchor_milestone_id": "__start__"},
                }
            ],
        },
    )
    _write_json(result_dir / "summary.json", {"overall_score": 1.0, "milestone_coverage": "full"})
    _write_json(result_dir / "report.json", {"stage_reports": [], "minefield_matches": []})
    _write_json(
        data_dir / "toolsandbox" / "adapted_cases" / "case_a.json",
        {
            "milestone_graph": {
                "nodes": [
                    {
                        "milestone_id": "m0",
                        "name": "ToolSandbox milestone 0",
                        "required": True,
                        "stage_anchor_predecessor_id": "__start__",
                    }
                ],
                "edges": [],
                "metadata": {
                    "graph_analysis": {
                        "augmented_edges": [["__start__", "m0"], ["m0", "__finish__"]],
                        "finish_stage_anchor_predecessor_id": "m0",
                    }
                },
            }
        },
    )

    data = build_display_data(runs_dir=runs_dir, results_dir=results_dir)

    graph = data["runs"][0]["scenarios"][0]["milestone_graph"]
    node_ids = {node["milestone_id"] for node in graph["nodes"]}
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    settlement = data["runs"][0]["scenarios"][0]["stage_settlements"][0]

    assert {"__start__", "m0", "__finish__"} <= node_ids
    assert {("__start__", "m0"), ("m0", "__finish__")} <= edges
    assert graph["nodes"][1]["stage_anchor_predecessor_id"] == "__start__"
    assert graph["metadata"]["finish_stage_anchor_predecessor_id"] == "m0"
    assert settlement["stage_anchor_milestone_id"] == "__start__"


def test_build_display_data_exports_stage_dynamics_and_milestone_matching(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    run_dir = runs_dir / "toolsandbox" / "run_1" / "case_dynamic"
    result_dir = results_dir / "toolsandbox" / "run_1" / "case_dynamic"

    _write_json(
        run_dir / "trajectory.json",
        {
            "task_id": "toolsandbox::case_dynamic",
            "steps": [{"step_id": "s2", "index": 2, "actor": "environment", "event_type": "tool_result"}],
        },
    )
    _write_json(
        run_dir / "raw_summary.json",
        {
            "milestone_graph_summary": {
                "nodes": [{"milestone_id": "m0", "name": "里程碑0", "required": True}],
                "edges": [],
            },
            "stage_settlements": [
                {
                    "settlement_id": "st1",
                    "kind": "milestone",
                    "milestone_id": "m0",
                    "start_step_index": 1,
                    "end_step_index": 2,
                    "metadata": {
                        "stage_report": {
                            "stage_id": "runtime:st1",
                            "milestone_id": "m0",
                            "evaluator_level": "standard",
                            "stage_score": 0.8,
                            "dimension_scores": {"progress": 0.7, "safety": 1.0},
                            "next_weights": {"progress": 0.6, "safety": 0.4},
                        },
                        "milestone_matching": {
                            "mode": "runtime_checkpoint",
                            "boundary": {"step_index": 2, "step_id": "s2"},
                            "score": {
                                "score": 0.8,
                                "status": "pass",
                                "hard_constraints_all_pass": True,
                                "constraint_scores": [
                                    {
                                        "constraint_id": "m0_c0",
                                        "score": 0.8,
                                        "missing": False,
                                        "evidence": ["匹配到了关键状态"],
                                        "actual": [{"large": "value"}],
                                    }
                                ],
                            },
                        },
                    },
                }
            ],
        },
    )
    _write_json(result_dir / "summary.json", {"overall_score": 0.8, "milestone_coverage": "full"})
    _write_json(
        result_dir / "report.json",
        {
            "stage_reports": [
                {
                    "stage_id": "runtime:st1",
                    "milestone_id": "m0",
                    "evaluator_level": "standard",
                    "status": "pass",
                    "stage_score": 0.8,
                    "judge_confidence": 0.9,
                    "uncertainty": 0.1,
                    "dimension_scores": {"progress": 0.7, "safety": 1.0},
                    "next_weights": {"progress": 0.6, "safety": 0.4},
                }
            ],
            "minefield_matches": [],
        },
    )

    data = build_display_data(runs_dir=runs_dir, results_dir=results_dir)

    scenario = data["runs"][0]["scenarios"][0]
    report = scenario["stage_reports"][0]
    matching = scenario["stage_settlements"][0]["milestone_matching"]

    assert report["next_weights"] == {"progress": 0.6, "safety": 0.4}
    assert matching["mode"] == "runtime_checkpoint"
    assert matching["boundary"]["step_index"] == 2
    assert matching["score"]["score"] == 0.8
    assert matching["score"]["constraint_scores"][0]["constraint_id"] == "m0_c0"
    assert "actual" not in matching["score"]["constraint_scores"][0]


def test_write_display_data_js_writes_safe_global_assignment(tmp_path: Path) -> None:
    output = tmp_path / "display" / "data.js"

    write_display_data_js({"runs": [{"run_id": "run_1", "scenarios": []}]}, output)

    text = output.read_text(encoding="utf-8")
    assert text.startswith("window.DYNSTEER_DATA = ")
    assert '"run_id": "run_1"' in text
    assert text.endswith(";\n")


def test_static_dashboard_is_offline_and_uses_safe_text_rendering() -> None:
    html_path = Path(__file__).parents[1] / "display" / "index.html"

    text = html_path.read_text(encoding="utf-8")

    assert '<script defer src="./data.js"></script>' in text
    assert "http://" not in text
    assert "https://" not in text
    assert "textContent" in text


def test_static_dashboard_keeps_page_stable_and_highlights_stage_nodes() -> None:
    html_path = Path(__file__).parents[1] / "display" / "index.html"

    text = html_path.read_text(encoding="utf-8")

    assert "scrollIntoView" not in text
    assert "stageMilestoneIds" in text
    assert "selected-stage" in text


def test_static_dashboard_renders_stage_dynamics_and_milestone_details() -> None:
    html_path = Path(__file__).parents[1] / "display" / "index.html"

    text = html_path.read_text(encoding="utf-8")

    assert "dimensionRows" in text
    assert "weightRows" in text
    assert "score-row" in text
    assert "milestoneMatchingFor" in text
    assert "milestone-detail" in text


def test_static_dashboard_uses_collapsible_stage_details() -> None:
    html_path = Path(__file__).parents[1] / "display" / "index.html"

    text = html_path.read_text(encoding="utf-8")

    assert "stage-detail-card" in text
    assert "stage-summary" in text
    assert "stage-detail-body" in text
    assert "score-section" in text
    assert "stageScoreSection" in text


def test_static_dashboard_offsets_score_rows_from_collapsible_border() -> None:
    html_path = Path(__file__).parents[1] / "display" / "index.html"

    text = html_path.read_text(encoding="utf-8")

    assert ".score-section .score-row" in text
    assert "padding: 0 10px" in text
    assert ".score-section .score-row:last-child" in text
