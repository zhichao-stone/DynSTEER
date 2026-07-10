from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

JsonObject = dict[str, Any]
CaseKey = tuple[str, str, str]
START_NODE_ID = "__start__"
FINISH_NODE_ID = "__finish__"


def build_display_data(runs_dir: Path, results_dir: Path, data_dir: Path | None = None) -> JsonObject:
    if runs_dir is None or results_dir is None:
        raise ValueError("runs_dir 和 results_dir 不能为空")
    try:
        resolved_data_dir = data_dir if data_dir is not None else runs_dir.parent / "data"
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "runs": _discover_runs(runs_dir, results_dir, resolved_data_dir),
        }
    except OSError as exc:
        raise RuntimeError(f"读取展示数据失败: {exc}") from exc


def write_display_data_js(data: JsonObject, output: Path) -> None:
    if data is None or output is None:
        raise ValueError("data 和 output 不能为空")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    output.write_text(f"window.DYNSTEER_DATA = {payload};\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 DynSTEER 静态评估看板数据")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("display/data.js"))
    args = parser.parse_args(argv)
    write_display_data_js(build_display_data(args.runs_dir, args.results_dir, args.data_dir), args.output)
    return 0


def _discover_runs(runs_dir: Path, results_dir: Path, data_dir: Path) -> list[JsonObject]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for benchmark, run_id, scenario_id in sorted(_collect_case_keys(runs_dir) | _collect_case_keys(results_dir)):
        grouped.setdefault((benchmark, run_id), []).append(scenario_id)
    return [
        {
            "benchmark": benchmark,
            "run_id": run_id,
            "summary": _run_summary(scenarios),
            "scenarios": scenarios,
        }
        for (benchmark, run_id), scenario_ids in grouped.items()
        for scenarios in [[_scenario(runs_dir, results_dir, data_dir, benchmark, run_id, item) for item in scenario_ids]]
    ]


def _scenario(
    runs_dir: Path,
    results_dir: Path,
    data_dir: Path,
    benchmark: str,
    run_id: str,
    scenario_id: str,
) -> JsonObject:
    run_case_dir = runs_dir / benchmark / run_id / scenario_id
    result_case_dir = results_dir / benchmark / run_id / scenario_id
    trajectory = _load_json(run_case_dir / "trajectory.json")
    raw_summary = _load_json(run_case_dir / "raw_summary.json")
    summary = _load_json(result_case_dir / "summary.json")
    report = _load_json(result_case_dir / "report.json")
    adapted_case = _load_json(data_dir / benchmark / "adapted_cases" / f"{scenario_id}.json")
    return {
        "scenario_id": scenario_id,
        "task_id": str(summary.get("task_id") or trajectory.get("task_id") or f"{benchmark}::{scenario_id}"),
        "summary": _summary_payload(summary),
        "trajectory": {"steps": trajectory.get("steps", [])},
        "milestone_graph": _graph_summary(raw_summary, adapted_case),
        "stage_reports": report.get("stage_reports", []),
        "stage_settlements": [_settlement_summary(item) for item in raw_summary.get("stage_settlements", [])],
        "match_attempts": raw_summary.get("milestone_match_attempts", []),
        "minefield_matches": report.get("minefield_matches", []),
    }


def _collect_case_keys(base_dir: Path) -> set[CaseKey]:
    if base_dir is None or not base_dir.exists():
        return set()
    keys: set[CaseKey] = set()
    for benchmark_dir in base_dir.iterdir():
        if not benchmark_dir.is_dir():
            continue
        for run_dir in benchmark_dir.iterdir():
            if run_dir.is_dir():
                keys.update(
                    (benchmark_dir.name, run_dir.name, scenario_dir.name)
                    for scenario_dir in run_dir.iterdir()
                    if scenario_dir.is_dir()
                )
    return keys


def _load_json(path: Path) -> JsonObject:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 文件格式错误: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON 文件顶层必须是对象: {path}")
    return payload


def _run_summary(scenarios: list[JsonObject]) -> JsonObject:
    summaries = [scenario.get("summary", {}) for scenario in scenarios]
    scores = [float(item["overall_score"]) for item in summaries if isinstance(item, dict) and isinstance(item.get("overall_score"), (int, float))]
    coverages = [str(item["milestone_coverage"]) for item in summaries if isinstance(item, dict) and item.get("milestone_coverage")]
    elapsed = [float(item["elapsed_seconds"]) for item in summaries if isinstance(item, dict) and isinstance(item.get("elapsed_seconds"), (int, float))]
    steps = [float(item["step_count"]) for item in summaries if isinstance(item, dict) and isinstance(item.get("step_count"), (int, float))]
    return {
        "scenario_count": len(scenarios),
        "overall_score": round(sum(scores) / len(scores), 4) if scores else None,
        "milestone_coverage": _coverage_summary(coverages),
        "elapsed_seconds": round(sum(elapsed), 3) if elapsed else None,
        "step_count": int(sum(steps)) if steps else None,
    }


def _summary_payload(summary: JsonObject) -> JsonObject:
    metrics = summary.get("runtime_metrics", {})
    keys = (
        "run_id", "task_id", "milestone_coverage", "overall_score", "stage_count",
        "first_failure_stage_id", "elapsed_seconds", "step_count", "snapshot_count",
        "tool_call_count", "llm_call_count", "llm_total_tokens", "trajectory_total_tokens",
    )
    payload = {key: summary[key] for key in keys if key in summary}
    if isinstance(metrics, dict):
        payload.update({key: metrics[key] for key in keys if key not in payload and key in metrics})
        payload.update({key: metrics[key] for key in ("started_at", "finished_at") if key in metrics})
    return payload


def _graph_summary(raw_summary: JsonObject, adapted_case: JsonObject) -> JsonObject:
    graph = raw_summary.get("milestone_graph_summary", {})
    adapted_graph = adapted_case.get("milestone_graph", {})
    adapted_nodes = {node.get("milestone_id"): node for node in adapted_graph.get("nodes", []) if isinstance(node, dict)}
    nodes = [
        _node_summary(node, adapted_nodes.get(node.get("milestone_id")))
        for node in (graph.get("nodes") or adapted_graph.get("nodes") or [])
        if isinstance(node, dict)
    ]
    metadata = _graph_metadata_summary(adapted_graph if isinstance(adapted_graph, dict) else {})
    edges = _graph_edges(graph if isinstance(graph, dict) else {}, adapted_graph if isinstance(adapted_graph, dict) else {})
    return {"nodes": _with_virtual_graph_nodes(nodes, edges, metadata), "edges": edges, "metadata": metadata}


def _settlement_summary(settlement: JsonObject) -> JsonObject:
    metadata = settlement.get("metadata", {}) if isinstance(settlement.get("metadata"), dict) else {}
    stage_report = metadata.get("stage_report", {}) if isinstance(metadata.get("stage_report"), dict) else {}
    return {
        "settlement_id": str(settlement.get("settlement_id") or ""),
        "stage_id": str(stage_report.get("stage_id") or f"runtime:{settlement.get('settlement_id')}"),
        "kind": str(settlement.get("kind") or ""),
        "milestone_id": str(settlement.get("milestone_id") or ""),
        "start_step_index": _number(settlement.get("start_step_index")),
        "end_step_index": _number(settlement.get("end_step_index")),
        "boundary_id": str(settlement.get("boundary_id") or ""),
        "boundary_step_index": _number(settlement.get("boundary_step_index")),
        "stage_anchor_milestone_id": str(metadata.get("stage_anchor_milestone_id") or ""),
        "stage_start_boundary_step_index": _number(metadata.get("stage_start_boundary_step_index")),
        "stage_start_step_index": _number(metadata.get("stage_start_step_index")),
        "stage_end_step_index": _number(metadata.get("stage_end_step_index")),
        "milestone_matching": _milestone_matching_summary(metadata.get("milestone_matching")),
        "status": str(settlement.get("status") or ""),
        "score": _number(settlement.get("score")),
        "checkpointed": settlement.get("checkpointed"),
        "evidence": [_text(value, 1200) for value in settlement.get("evidence", [])[:8]],
    }


def _milestone_matching_summary(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return {
        "mode": str(value.get("mode") or ""),
        "matched": value.get("matched"),
        "boundary": _boundary_summary(value.get("boundary")),
        "score": _matching_score_summary(value.get("score")),
        "ready_milestone_ids_before_match": _string_list(value.get("ready_milestone_ids_before_match")),
        "matched_milestone_ids_before_match": _string_list(value.get("matched_milestone_ids_before_match")),
        "predecessor_milestone_ids": _string_list(value.get("predecessor_milestone_ids")),
        "matched_milestone_ids": _string_list(value.get("matched_milestone_ids")),
        "pending_required_milestone_ids": _string_list(value.get("pending_required_milestone_ids")),
        "pending_optional_milestone_ids": _string_list(value.get("pending_optional_milestone_ids")),
        "total_milestone_count": _number(value.get("total_milestone_count")),
    }


def _boundary_summary(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return {"boundary_id": str(value.get("boundary_id") or ""), "step_index": _number(value.get("step_index")), "step_id": str(value.get("step_id") or ""), "snapshot_id": str(value.get("snapshot_id") or ""), "reason": str(value.get("reason") or "")}


def _matching_score_summary(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    scores = value.get("constraint_scores", [])
    return {
        "milestone_id": str(value.get("milestone_id") or ""),
        "boundary_id": str(value.get("boundary_id") or ""),
        "score": _number(value.get("score")),
        "status": str(value.get("status") or ""),
        "missing_ratio": _number(value.get("missing_ratio")),
        "hard_constraints_all_pass": value.get("hard_constraints_all_pass"),
        "constraint_scores": [_constraint_score_summary(item) for item in scores[:6] if isinstance(item, dict)],
    }


def _constraint_score_summary(score: JsonObject) -> JsonObject:
    return {"constraint_id": str(score.get("constraint_id") or ""), "score": _number(score.get("score")), "missing": score.get("missing"), "evidence": [_text(item, 500) for item in score.get("evidence", [])[:2]]}


def _node_summary(node: JsonObject, adapted_node: JsonObject | None = None) -> JsonObject:
    adapted = adapted_node or {}
    return {
        "milestone_id": str(node.get("milestone_id") or ""),
        "name": str(node.get("name") or adapted.get("name") or node.get("milestone_id") or ""),
        "description": _text(node.get("description") or adapted.get("description"), 1200),
        "required": node.get("required") if node.get("required") is not None else adapted.get("required"),
        "constraint_count": _number(node.get("constraint_count")),
        "pass_threshold": _number(node.get("pass_threshold")),
        "stage_anchor_predecessor_id": str(node.get("stage_anchor_predecessor_id") or adapted.get("stage_anchor_predecessor_id") or ""),
        "virtual": False,
    }


def _graph_edges(graph: JsonObject, adapted_graph: JsonObject) -> list[JsonObject]:
    analysis = adapted_graph.get("metadata", {}).get("graph_analysis", {})
    edge_values = analysis.get("augmented_edges") or graph.get("edges") or []
    return [_edge_summary(edge) for edge in edge_values]


def _graph_metadata_summary(adapted_graph: JsonObject) -> JsonObject:
    analysis = adapted_graph.get("metadata", {}).get("graph_analysis", {})
    return {
        "start_node_id": str(analysis.get("start_node_id") or START_NODE_ID),
        "finish_node_id": str(analysis.get("finish_node_id") or FINISH_NODE_ID),
        "finish_stage_anchor_predecessor_id": str(analysis.get("finish_stage_anchor_predecessor_id") or ""),
    }


def _with_virtual_graph_nodes(nodes: list[JsonObject], edges: list[JsonObject], metadata: JsonObject) -> list[JsonObject]:
    node_ids = {str(node.get("milestone_id") or "") for node in nodes}
    edge_ids = {str(edge.get(key) or "") for edge in edges for key in ("source", "target")}
    result = []
    start_id = str(metadata.get("start_node_id") or START_NODE_ID)
    finish_id = str(metadata.get("finish_node_id") or FINISH_NODE_ID)
    if start_id in edge_ids and start_id not in node_ids:
        result.append(_virtual_node_summary(start_id, "超级源"))
    result.extend(nodes)
    if finish_id in edge_ids and finish_id not in node_ids:
        result.append(_virtual_node_summary(finish_id, "超级汇"))
    return result


def _virtual_node_summary(milestone_id: str, name: str) -> JsonObject:
    return {"milestone_id": milestone_id, "name": name, "description": name, "required": False, "constraint_count": None, "pass_threshold": None, "stage_anchor_predecessor_id": "", "virtual": True}


def _edge_summary(value: Any) -> JsonObject:
    items = list(value) if isinstance(value, (list, tuple)) else []
    return {"source": str(items[0]) if len(items) > 0 else "", "target": str(items[1]) if len(items) > 1 else "", "label": str(items[2]) if len(items) > 2 else ""}


def _coverage_summary(coverages: list[str]) -> str:
    unique_values = sorted(set(coverages))
    return unique_values[0] if len(unique_values) == 1 else "mixed" if unique_values else "unknown"


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value is not None else ""
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == "__main__":
    raise SystemExit(main())
