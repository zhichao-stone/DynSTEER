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
    """汇总 runs 与 results 目录，生成评估看板使用的数据对象。

    Args:
        runs_dir: 原始运行轨迹目录。
        results_dir: 评估结果目录。
        data_dir: benchmark adapted case 数据目录，默认使用 runs_dir 同级的 data。

    Returns:
        可序列化为 display/data.js 的看板数据。
    """
    if runs_dir is None or results_dir is None:
        raise ValueError("runs_dir 和 results_dir 不能为空")
    try:
        resolved_data_dir = data_dir if data_dir is not None else runs_dir.parent / "data"
        run_items = _discover_runs(runs_dir, results_dir, resolved_data_dir)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "runs": run_items,
        }
    except OSError as exc:
        raise RuntimeError(f"读取展示数据失败: {exc}") from exc


def write_display_data_js(data: JsonObject, output: Path) -> None:
    """将看板数据写成可被静态 HTML 直接载入的 data.js。

    Args:
        data: 看板数据。
        output: data.js 输出路径。
    """
    if data is None or output is None:
        raise ValueError("data 和 output 不能为空")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    output.write_text(f"window.DYNSTEER_DATA = {payload};\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口，生成 display/data.js。"""
    parser = argparse.ArgumentParser(description="生成 DynSTEER 静态评估看板数据")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("display/data.js"))
    args = parser.parse_args(argv)
    data = build_display_data(args.runs_dir, args.results_dir, args.data_dir)
    write_display_data_js(data, args.output)
    return 0


def _discover_runs(runs_dir: Path, results_dir: Path, data_dir: Path) -> list[JsonObject]:
    run_keys = _collect_case_keys(runs_dir) | _collect_case_keys(results_dir)
    grouped: dict[tuple[str, str], list[str]] = {}
    for benchmark, run_id, scenario_id in sorted(run_keys):
        grouped.setdefault((benchmark, run_id), []).append(scenario_id)

    runs: list[JsonObject] = []
    for (benchmark, run_id), scenario_ids in grouped.items():
        scenarios = [
            _build_scenario_payload(runs_dir, results_dir, data_dir, benchmark, run_id, scenario_id)
            for scenario_id in scenario_ids
        ]
        runs.append(
            {
                "benchmark": benchmark,
                "run_id": run_id,
                "summary": _run_summary(scenarios),
                "scenarios": scenarios,
            }
        )
    return runs


def _build_scenario_payload(
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
        "task_id": _first_str(
            summary.get("task_id"),
            trajectory.get("task_id"),
            f"{benchmark}::{scenario_id}",
        ),
        "summary": _summary_payload(summary),
        "trajectory": {"steps": [_step_summary(step) for step in _as_list(trajectory.get("steps"))]},
        "milestone_graph": _graph_summary(raw_summary, adapted_case),
        "stage_reports": [_stage_report_summary(item) for item in _as_list(report.get("stage_reports"))],
        "stage_settlements": [
            _settlement_summary(item) for item in _as_list(raw_summary.get("stage_settlements"))
        ],
        "match_attempts": [
            _match_attempt_summary(item) for item in _as_list(raw_summary.get("milestone_match_attempts"))
        ],
        "minefield_matches": _as_list(report.get("minefield_matches")),
    }


def _collect_case_keys(base_dir: Path) -> set[CaseKey]:
    if base_dir is None or not base_dir.exists():
        return set()
    keys: set[CaseKey] = set()
    for benchmark_dir in base_dir.iterdir():
        if not benchmark_dir.is_dir():
            continue
        for run_dir in benchmark_dir.iterdir():
            if not run_dir.is_dir():
                continue
            for scenario_dir in run_dir.iterdir():
                if scenario_dir.is_dir():
                    keys.add((benchmark_dir.name, run_dir.name, scenario_dir.name))
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
    scores = [
        score
        for score in (_number(_as_dict(scenario.get("summary")).get("overall_score")) for scenario in scenarios)
        if score is not None
    ]
    coverages = [
        coverage
        for coverage in (
            _first_str(_as_dict(scenario.get("summary")).get("milestone_coverage"))
            for scenario in scenarios
        )
        if coverage
    ]
    elapsed_values = [
        value
        for value in (_number(_as_dict(scenario.get("summary")).get("elapsed_seconds")) for scenario in scenarios)
        if value is not None
    ]
    step_values = [
        value
        for value in (_number(_as_dict(scenario.get("summary")).get("step_count")) for scenario in scenarios)
        if value is not None
    ]
    return {
        "scenario_count": len(scenarios),
        "overall_score": round(sum(scores) / len(scores), 4) if scores else None,
        "milestone_coverage": _coverage_summary(coverages),
        "elapsed_seconds": round(sum(elapsed_values), 3) if elapsed_values else None,
        "step_count": int(sum(step_values)) if step_values else None,
    }


def _summary_payload(summary: JsonObject) -> JsonObject:
    if not summary:
        return {}
    metrics = _as_dict(summary.get("runtime_metrics"))
    keys = (
        "run_id",
        "task_id",
        "milestone_coverage",
        "overall_score",
        "stage_count",
        "first_failure_stage_id",
        "elapsed_seconds",
        "step_count",
        "snapshot_count",
        "tool_call_count",
        "llm_call_count",
        "llm_total_tokens",
        "trajectory_total_tokens",
    )
    payload: JsonObject = {}
    for key in keys:
        if key in summary:
            payload[key] = summary[key]
        elif key in metrics:
            payload[key] = metrics[key]
    for key in ("started_at", "finished_at"):
        if key in metrics:
            payload[key] = metrics[key]
    return payload


def _step_summary(step: Any) -> JsonObject:
    item = _as_dict(step)
    return {
        "step_id": _first_str(item.get("step_id")),
        "index": _number(item.get("index")),
        "actor": _first_str(item.get("actor")),
        "event_type": _first_str(item.get("event_type")),
        "timestamp": item.get("timestamp"),
        "content": _text(item.get("content"), limit=4000),
        "tool_call": _tool_call_summary(item.get("tool_call")),
        "tool_result": _tool_result_summary(item.get("tool_result")),
        "sender": _first_str(item.get("sender")),
        "recipient": _first_str(item.get("recipient")),
        "openai_function_name": _first_str(item.get("openai_function_name")),
        "visible_to": _as_list(item.get("visible_to")),
    }


def _graph_summary(raw_summary: JsonObject, adapted_case: JsonObject) -> JsonObject:
    graph = _as_dict(raw_summary.get("milestone_graph_summary")) or _as_dict(
        raw_summary.get("milestone_graph")
    )
    adapted_graph = _as_dict(adapted_case.get("milestone_graph"))
    adapted_nodes = {
        _first_str(_as_dict(node).get("milestone_id"), _as_dict(node).get("id")): _as_dict(node)
        for node in _as_list(adapted_graph.get("nodes"))
    }
    node_values = _as_list(graph.get("nodes")) or _as_list(adapted_graph.get("nodes"))
    edges = _graph_edges(graph, adapted_graph)
    metadata = _graph_metadata_summary(adapted_graph)
    nodes = [
        _node_summary(node, adapted_nodes.get(_first_str(_as_dict(node).get("milestone_id"), _as_dict(node).get("id"))))
        for node in node_values
    ]
    nodes = _with_virtual_graph_nodes(nodes, edges, metadata)
    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": metadata,
    }


def _stage_report_summary(item: Any) -> JsonObject:
    report = _as_dict(item)
    return {
        "stage_id": _first_str(report.get("stage_id")),
        "milestone_id": _first_str(report.get("milestone_id")),
        "evaluator_level": _first_str(report.get("evaluator_level")),
        "status": _first_str(report.get("status")),
        "stage_score": _number(report.get("stage_score")),
        "judge_confidence": _number(report.get("judge_confidence")),
        "uncertainty": _number(report.get("uncertainty")),
        "dimension_scores": _score_map(report.get("dimension_scores")),
        "next_weights": _score_map(report.get("next_weights")),
        "evidence": [_text(value, limit=1200) for value in _as_list(report.get("evidence"))[:8]],
        "diagnosis": [_text(value, limit=1200) for value in _as_list(report.get("diagnosis"))[:8]],
        "fatal": report.get("fatal"),
        "hard_constraints_all_pass": report.get("hard_constraints_all_pass"),
        "metadata": _stage_metadata_summary(report.get("metadata")),
    }


def _settlement_summary(item: Any) -> JsonObject:
    settlement = _as_dict(item)
    metadata = _as_dict(settlement.get("metadata"))
    stage_report = _as_dict(metadata.get("stage_report"))
    return {
        "settlement_id": _first_str(settlement.get("settlement_id")),
        "stage_id": _first_str(stage_report.get("stage_id"), f"runtime:{settlement.get('settlement_id')}"),
        "kind": _first_str(settlement.get("kind")),
        "milestone_id": _first_str(settlement.get("milestone_id")),
        "start_step_index": _number(settlement.get("start_step_index")),
        "end_step_index": _number(settlement.get("end_step_index")),
        "boundary_id": _first_str(settlement.get("boundary_id")),
        "boundary_step_index": _number(settlement.get("boundary_step_index")),
        "stage_anchor_milestone_id": _first_str(metadata.get("stage_anchor_milestone_id")),
        "stage_start_boundary_step_index": _number(metadata.get("stage_start_boundary_step_index")),
        "stage_start_step_index": _number(metadata.get("stage_start_step_index")),
        "stage_end_step_index": _number(metadata.get("stage_end_step_index")),
        "milestone_matching": _milestone_matching_summary(metadata.get("milestone_matching")),
        "status": _first_str(settlement.get("status")),
        "score": _number(settlement.get("score")),
        "checkpointed": settlement.get("checkpointed"),
        "evidence": [_text(value, limit=1200) for value in _as_list(settlement.get("evidence"))[:8]],
    }


def _match_attempt_summary(item: Any) -> JsonObject:
    attempt = _as_dict(item)
    return {
        "step_index": _number(attempt.get("step_index")),
        "step_id": _first_str(attempt.get("step_id")),
        "selected_milestone_id": _first_str(attempt.get("selected_milestone_id")),
        "selected_score": _number(attempt.get("selected_score")),
        "status": _first_str(attempt.get("status")),
    }


def _tool_call_summary(value: Any) -> JsonObject | None:
    tool_call = _as_dict(value)
    if not tool_call:
        return None
    return {
        "name": _first_str(tool_call.get("name")),
        "arguments": tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {},
    }


def _tool_result_summary(value: Any) -> JsonObject | None:
    tool_result = _as_dict(value)
    if not tool_result:
        return None
    return {
        "success": tool_result.get("success"),
        "content": _text(tool_result.get("content"), limit=2000),
        "exception": _text(tool_result.get("exception"), limit=2000),
    }


def _milestone_matching_summary(value: Any) -> JsonObject:
    matching = _as_dict(value)
    if not matching:
        return {}
    return {
        "mode": _first_str(matching.get("mode")),
        "matched": matching.get("matched"),
        "boundary": _boundary_summary(matching.get("boundary")),
        "score": _matching_score_summary(matching.get("score")),
        "ready_milestone_ids_before_match": _string_list(matching.get("ready_milestone_ids_before_match")),
        "matched_milestone_ids_before_match": _string_list(matching.get("matched_milestone_ids_before_match")),
        "predecessor_milestone_ids": _string_list(matching.get("predecessor_milestone_ids")),
        "matched_milestone_ids": _string_list(matching.get("matched_milestone_ids")),
        "pending_required_milestone_ids": _string_list(matching.get("pending_required_milestone_ids")),
        "pending_optional_milestone_ids": _string_list(matching.get("pending_optional_milestone_ids")),
        "total_milestone_count": _number(matching.get("total_milestone_count")),
    }


def _boundary_summary(value: Any) -> JsonObject:
    boundary = _as_dict(value)
    if not boundary:
        return {}
    return {
        "boundary_id": _first_str(boundary.get("boundary_id")),
        "step_index": _number(boundary.get("step_index")),
        "step_id": _first_str(boundary.get("step_id")),
        "snapshot_id": _first_str(boundary.get("snapshot_id")),
        "reason": _first_str(boundary.get("reason")),
    }


def _matching_score_summary(value: Any) -> JsonObject:
    score = _as_dict(value)
    if not score:
        return {}
    return {
        "milestone_id": _first_str(score.get("milestone_id")),
        "boundary_id": _first_str(score.get("boundary_id")),
        "score": _number(score.get("score")),
        "status": _first_str(score.get("status")),
        "missing_ratio": _number(score.get("missing_ratio")),
        "hard_constraints_all_pass": score.get("hard_constraints_all_pass"),
        "constraint_scores": [
            _constraint_score_summary(item) for item in _as_list(score.get("constraint_scores"))[:6]
        ],
    }


def _constraint_score_summary(value: Any) -> JsonObject:
    score = _as_dict(value)
    return {
        "constraint_id": _first_str(score.get("constraint_id")),
        "score": _number(score.get("score")),
        "missing": score.get("missing"),
        "evidence": [_text(item, limit=500) for item in _as_list(score.get("evidence"))[:2]],
    }


def _node_summary(value: Any, adapted_node: JsonObject | None = None) -> JsonObject:
    node = _as_dict(value)
    adapted = _as_dict(adapted_node)
    return {
        "milestone_id": _first_str(node.get("milestone_id"), node.get("id")),
        "name": _first_str(node.get("name"), adapted.get("name"), node.get("milestone_id"), node.get("id")),
        "description": _text(node.get("description") or adapted.get("description"), limit=1200),
        "required": node.get("required") if node.get("required") is not None else adapted.get("required"),
        "constraint_count": _number(node.get("constraint_count")),
        "pass_threshold": _number(node.get("pass_threshold")),
        "stage_anchor_predecessor_id": _first_str(
            node.get("stage_anchor_predecessor_id"),
            adapted.get("stage_anchor_predecessor_id"),
        ),
        "virtual": False,
    }


def _graph_edges(graph: JsonObject, adapted_graph: JsonObject) -> list[JsonObject]:
    metadata = _as_dict(adapted_graph.get("metadata"))
    analysis = _as_dict(metadata.get("graph_analysis"))
    augmented_edges = _as_list(analysis.get("augmented_edges"))
    edge_values = augmented_edges if augmented_edges else _as_list(graph.get("edges"))
    return [_edge_summary(edge) for edge in edge_values]


def _graph_metadata_summary(adapted_graph: JsonObject) -> JsonObject:
    metadata = _as_dict(adapted_graph.get("metadata"))
    analysis = _as_dict(metadata.get("graph_analysis"))
    return {
        "start_node_id": _first_str(analysis.get("start_node_id"), START_NODE_ID),
        "finish_node_id": _first_str(analysis.get("finish_node_id"), FINISH_NODE_ID),
        "finish_stage_anchor_predecessor_id": _first_str(
            analysis.get("finish_stage_anchor_predecessor_id")
        ),
    }


def _with_virtual_graph_nodes(
    nodes: list[JsonObject],
    edges: list[JsonObject],
    metadata: JsonObject,
) -> list[JsonObject]:
    node_ids = {_first_str(node.get("milestone_id")) for node in nodes}
    edge_ids = {
        node_id
        for edge in edges
        for node_id in (_first_str(edge.get("source")), _first_str(edge.get("target")))
        if node_id
    }
    start_id = _first_str(metadata.get("start_node_id"), START_NODE_ID)
    finish_id = _first_str(metadata.get("finish_node_id"), FINISH_NODE_ID)
    result: list[JsonObject] = []
    if start_id in edge_ids and start_id not in node_ids:
        result.append(_virtual_node_summary(start_id, "超级源"))
    result.extend(nodes)
    if finish_id in edge_ids and finish_id not in node_ids:
        result.append(_virtual_node_summary(finish_id, "超级汇"))
    return result


def _virtual_node_summary(milestone_id: str, name: str) -> JsonObject:
    return {
        "milestone_id": milestone_id,
        "name": name,
        "description": name,
        "required": False,
        "constraint_count": None,
        "pass_threshold": None,
        "stage_anchor_predecessor_id": "",
        "virtual": True,
    }


def _edge_summary(value: Any) -> JsonObject:
    edge = _as_dict(value)
    if edge:
        return {
            "source": _first_str(edge.get("source"), edge.get("from"), edge.get("u")),
            "target": _first_str(edge.get("target"), edge.get("to"), edge.get("v")),
            "label": _first_str(edge.get("label")),
        }
    items = _as_list(value)
    return {
        "source": _first_str(items[0] if len(items) > 0 else None),
        "target": _first_str(items[1] if len(items) > 1 else None),
        "label": _first_str(items[2] if len(items) > 2 else None),
    }


def _stage_metadata_summary(value: Any) -> JsonObject:
    metadata = _as_dict(value)
    keys = (
        "stage_anchor_milestone_id",
        "start_boundary_step_index",
        "start_step_index",
        "end_step_index",
        "stage_step_count",
        "task_description",
        "first_stage_step_excerpt",
        "last_stage_step_excerpt",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def _coverage_summary(coverages: list[str]) -> str:
    if not coverages:
        return "unknown"
    unique_values = sorted(set(coverages))
    return unique_values[0] if len(unique_values) == 1 else "mixed"


def _as_dict(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _score_map(value: Any) -> JsonObject:
    scores = _as_dict(value)
    return {
        key: number
        for key, raw in scores.items()
        if isinstance(key, str) and (number := _number(raw)) is not None
    }


def _string_list(value: Any) -> list[str]:
    return [_first_str(item) for item in _as_list(value) if _first_str(item)]


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            return str(value)
    return ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


if __name__ == "__main__":
    raise SystemExit(main())
