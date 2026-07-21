import json
from pathlib import Path

from display.build import build_display_data


def test_build_display_data_exposes_termination_and_unsettled_finish(tmp_path: Path) -> None:
    """覆盖策略提前终止时展示数据会合成 finish 未结算报告。"""
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    data_dir = tmp_path / "data"
    case_dir = runs_dir / "toolsandbox" / "run1" / "case1"
    result_dir = results_dir / "toolsandbox" / "run1" / "case1"
    adapted_dir = data_dir / "toolsandbox" / "adapted_cases"
    case_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    adapted_dir.mkdir(parents=True)

    _write_json(case_dir / "trajectory.json", {"task_id": "task", "steps": []})
    _write_json(
        case_dir / "raw_summary.json",
        {
            "terminated_by_policy": True,
            "termination_code": "minefield:mf0",
            "termination_reason": "触发 fatal minefield，提前终止执行：mf0",
            "termination_detail": {
                "minefield_matches": [{"minefield_id": "mf0", "score": 1.0, "fatal": True}]
            },
            "stage_settlements": [],
        },
    )
    _write_json(result_dir / "summary.json", {"task_id": "task", "runtime_metrics": {}})
    _write_json(result_dir / "report.json", {"stage_reports": [], "minefield_matches": []})
    _write_json(
        adapted_dir / "case1.json",
        {
            "stage_goals": {},
            "milestone_graph": {
                "nodes": [],
                "edges": [],
                "minefields": [
                    {
                        "minefield_id": "mf0",
                        "name": "禁止直接计算假日差值",
                        "description": "信息不足时不能调用 timestamp_diff。",
                        "severity": "fatal",
                        "constraints": [
                            {
                                "constraint_id": "mf0_c0",
                                "target": "state_snapshot",
                                "selector": "$",
                                "operator": "custom",
                                "expected": {
                                    "rows": [
                                        {
                                            "sender": "AGENT",
                                            "recipient": "EXECUTION_ENVIRONMENT",
                                            "content": "timestamp_diff",
                                        }
                                    ],
                                    "columns": ["sender", "recipient", "content"],
                                },
                                "namespace": "SANDBOX",
                                "threshold": 1.0,
                                "hard": True,
                                "evaluator_hint": "toolsandbox",
                                "stage_goal_semantics": {
                                    "kind": "emit_message",
                                    "sender": "AGENT",
                                    "recipient": "EXECUTION_ENVIRONMENT",
                                    "content": "timestamp_diff",
                                    "match_policy": "semantic_equivalent",
                                },
                            }
                        ],
                        "penalty": {"mode": "fixed", "value": 1.0},
                    }
                ],
                "metadata": {
                    "graph_analysis": {
                        "start_node_id": "__start__",
                        "finish_node_id": "__finish__",
                        "finish_stage_anchor_predecessor_id": "__start__",
                    }
                },
            },
        },
    )

    scenario = build_display_data(runs_dir, results_dir, data_dir)["runs"][0]["scenarios"][0]

    assert scenario["termination"]["terminated_by_policy"] is True
    assert scenario["stage_reports"][0]["status"] == "terminated"
    assert scenario["stage_reports"][0]["metadata"]["finish_unsettled_due_to_termination"] is True
    evidence = "\n".join(scenario["stage_reports"][0]["evidence"])
    assert "timestamp_diff" in evidence
    match = scenario["stage_reports"][0]["metadata"]["termination"]["termination_detail"]["minefield_matches"][0]
    assert match["severity"] == "fatal"
    assert match["constraints"][0]["semantic_summary"] == "AGENT -> EXECUTION_ENVIRONMENT: timestamp_diff"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
