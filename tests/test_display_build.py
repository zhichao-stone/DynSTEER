from __future__ import annotations

import json
from pathlib import Path

from display.build import _settlement_summary, build_display_data


def test_build_display_data_exports_stage_definitions(tmp_path: Path) -> None:
    runs_dir, results_dir, data_dir = _write_display_fixture(tmp_path)

    data = build_display_data(runs_dir, results_dir, data_dir)
    scenario = data["runs"][0]["scenarios"][0]

    assert scenario["stage_definitions"][0]["stage_id"] == "__start__->m0"
    assert scenario["stage_definitions"][-1]["stage_id"] == "m4->__finish__"
    assert len(scenario["stage_definitions"]) == 2
    assert all("runtime:" not in item["stage_id"] for item in scenario["stage_definitions"])


def test_build_display_data_uses_explicit_finish_stage_goal_once(tmp_path: Path) -> None:
    runs_dir, results_dir, data_dir = _write_display_fixture(
        tmp_path,
        stage_goals={
            "__start__->m0": "First stage",
            "m4->__finish__": "Custom finish stage",
        },
    )

    data = build_display_data(runs_dir, results_dir, data_dir)
    definitions = data["runs"][0]["scenarios"][0]["stage_definitions"]

    assert [item["stage_id"] for item in definitions] == ["__start__->m0", "m4->__finish__"]
    assert definitions[-1]["stage_goal"] == "Custom finish stage"


def test_settlement_summary_does_not_generate_runtime_stage_id() -> None:
    summary = _settlement_summary({"settlement_id": "st1", "metadata": {}})

    assert summary["stage_id"] == ""


def test_build_display_data_normalizes_legacy_runtime_stage_ids(tmp_path: Path) -> None:
    runs_dir, results_dir, data_dir = _write_display_fixture(
        tmp_path,
        stage_goals={"__start__->m0": "First stage", "m3->m4": "Fourth stage"},
    )
    summary_path = results_dir / "toolsandbox" / "run-1" / "case-1" / "summary.json"
    _write_json(summary_path, {"task_id": "task-1", "overall_score": 0.5, "first_failure_stage_id": "runtime:fail:m4"})
    report_path = results_dir / "toolsandbox" / "run-1" / "case-1" / "report.json"
    _write_json(
        report_path,
        {
            "stage_reports": [
                {
                    "stage_id": "runtime:st1",
                    "milestone_id": "m0",
                    "status": "pass",
                    "metadata": {"prompt_input": {"stage_id": "runtime:st1"}},
                },
                {
                    "stage_id": "runtime:fail:m4",
                    "milestone_id": "m4",
                    "status": "fail",
                    "metadata": {"synthetic_pending_required": True},
                },
                {
                    "stage_id": "runtime:st2",
                    "milestone_id": None,
                    "status": "pass",
                    "evidence": ["finish"],
                },
            ],
            "minefield_matches": [],
        },
    )

    data = build_display_data(runs_dir, results_dir, data_dir)
    scenario = data["runs"][0]["scenarios"][0]

    assert [item["stage_id"] for item in scenario["stage_reports"]] == ["__start__->m0", "m3->m4"]
    assert all(item["milestone_id"] != "__finish__" for item in scenario["stage_reports"])
    assert scenario["summary"]["first_failure_stage_id"] == "m3->m4"
    assert scenario["stage_reports"][0]["metadata"]["prompt_input"]["stage_id"] == "__start__->m0"
    assert '"stage_id": "runtime:' not in json.dumps(scenario, ensure_ascii=False)


def _write_display_fixture(
    tmp_path: Path,
    stage_goals: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    data_dir = tmp_path / "data"
    run_case = runs_dir / "toolsandbox" / "run-1" / "case-1"
    result_case = results_dir / "toolsandbox" / "run-1" / "case-1"
    adapted_case = data_dir / "toolsandbox" / "adapted_cases" / "case-1.json"
    run_case.mkdir(parents=True)
    result_case.mkdir(parents=True)
    adapted_case.parent.mkdir(parents=True)

    _write_json(run_case / "trajectory.json", {"task_id": "task-1", "steps": []})
    _write_json(
        run_case / "raw_summary.json",
        {
            "milestone_graph_summary": {},
            "stage_settlements": [
                {
                    "settlement_id": "st0",
                    "kind": "milestone",
                    "milestone_id": "m0",
                    "start_step_index": 1,
                    "end_step_index": 2,
                    "status": "pass",
                    "metadata": {"stage_report": {"stage_id": "__start__->m0"}},
                }
            ],
        },
    )
    _write_json(result_case / "summary.json", {"task_id": "task-1", "overall_score": 1.0})
    _write_json(result_case / "report.json", {"stage_reports": [], "minefield_matches": []})
    _write_json(
        adapted_case,
        {
            "stage_goals": stage_goals or {"__start__->m0": "First stage"},
            "milestone_graph": {
                "nodes": [
                    {
                        "milestone_id": "m0",
                        "name": "M0",
                        "description": "First milestone",
                        "required": True,
                        "constraints": [],
                        "stage_anchor_predecessor_id": "__start__",
                    },
                    {
                        "milestone_id": "m4",
                        "name": "M4",
                        "description": "Last milestone",
                        "required": True,
                        "constraints": [],
                        "stage_anchor_predecessor_id": "m0",
                    },
                ],
                "metadata": {
                    "graph_analysis": {
                        "start_node_id": "__start__",
                        "finish_node_id": "__finish__",
                        "finish_stage_anchor_predecessor_id": "m4",
                        "augmented_edges": [["__start__", "m0"], ["m4", "__finish__"]],
                    }
                },
            },
        },
    )
    return runs_dir, results_dir, data_dir


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
