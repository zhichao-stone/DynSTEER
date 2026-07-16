from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dynsteer.utils import clean_evidence_items

JsonObject = dict[str, Any]
CaseKey = tuple[str, str, str]
START_NODE_ID = "__start__"
FINISH_NODE_ID = "__finish__"
DEFAULT_FINISH_STAGE_GOAL = "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"


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
    all_stage_definitions = _stage_definitions(adapted_case)
    stage_reports, report_stage_id_map = _stage_reports(report.get("stage_reports", []), all_stage_definitions)
    stage_definitions = _active_stage_definitions(all_stage_definitions, stage_reports)
    stage_settlements, settlement_stage_id_map = _settlement_summaries(
        raw_summary.get("stage_settlements", []),
        all_stage_definitions,
    )
    stage_settlements = _active_stage_settlements(stage_settlements, stage_reports)
    stage_id_map = {**settlement_stage_id_map, **report_stage_id_map}
    return {
        "scenario_id": scenario_id,
        "task_id": str(summary.get("task_id") or trajectory.get("task_id") or f"{benchmark}::{scenario_id}"),
        "summary": _normalize_summary_stage_ids(_summary_payload(summary), stage_definitions, stage_id_map),
        "trajectory": {"steps": trajectory.get("steps", [])},
        "milestone_graph": _graph_summary(raw_summary, adapted_case),
        "stage_definitions": stage_definitions,
        "stage_reports": stage_reports,
        "stage_settlements": stage_settlements,
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


def _stage_reports(reports: Any, definitions: list[JsonObject]) -> tuple[list[JsonObject], dict[str, str]]:
    if not isinstance(reports, list):
        return [], {}
    normalized: list[JsonObject] = []
    stage_id_map: dict[str, str] = {}
    terminal_before_finish = False
    for report in reports:
        if not isinstance(report, dict):
            continue
        item = _json_copy(report)
        old_stage_id = str(item.get("stage_id") or "")
        definition = _stage_definition_for_report(item, definitions)
        new_stage_id = _normalized_stage_id(old_stage_id, definitions, stage_id_map, definition)
        if terminal_before_finish and definition is not None and definition.get("milestone_id") == FINISH_NODE_ID:
            if old_stage_id and old_stage_id != new_stage_id:
                stage_id_map[old_stage_id] = new_stage_id
            continue
        if new_stage_id:
            item = _replace_string(item, old_stage_id, new_stage_id)
            item["stage_id"] = new_stage_id
            if definition is not None:
                if not item.get("milestone_id"):
                    item["milestone_id"] = definition.get("milestone_id")
                metadata = item.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                    item["metadata"] = metadata
                metadata["stage_goal"] = definition.get("stage_goal")
                metadata["stage_anchor_milestone_id"] = definition.get("anchor_milestone_id")
            if old_stage_id and old_stage_id != new_stage_id:
                stage_id_map[old_stage_id] = new_stage_id
        if isinstance(item.get("evidence"), list):
            item["evidence"] = clean_evidence_items([_text(value, 1200) for value in item["evidence"]])
        normalized.append(item)
        if item.get("milestone_id") != FINISH_NODE_ID and _is_terminal_stage_status(item.get("status")):
            terminal_before_finish = True
    return normalized, stage_id_map


def _settlement_summaries(settlements: Any, definitions: list[JsonObject]) -> tuple[list[JsonObject], dict[str, str]]:
    if not isinstance(settlements, list):
        return [], {}
    summaries: list[JsonObject] = []
    stage_id_map: dict[str, str] = {}
    for settlement in settlements:
        if not isinstance(settlement, dict):
            continue
        old_stage_id = _settlement_stage_id(settlement)
        summary = _settlement_summary(settlement, definitions)
        new_stage_id = str(summary.get("stage_id") or "")
        if old_stage_id and new_stage_id and old_stage_id != new_stage_id:
            stage_id_map[old_stage_id] = new_stage_id
        summaries.append(summary)
    return summaries, stage_id_map


def _active_stage_definitions(definitions: list[JsonObject], reports: list[JsonObject]) -> list[JsonObject]:
    if not reports:
        return definitions
    if not any(
        report.get("milestone_id") != FINISH_NODE_ID and _is_terminal_stage_status(report.get("status"))
        for report in reports
    ):
        return definitions
    reported_ids = {str(report.get("stage_id") or "") for report in reports}
    finish_definition = _finish_definition(definitions)
    return [
        definition
        for definition in definitions
        if str(definition.get("stage_id") or "") in reported_ids
        or _should_keep_unreported_finish_definition(definition, finish_definition, reports)
    ]


def _active_stage_settlements(settlements: list[JsonObject], reports: list[JsonObject]) -> list[JsonObject]:
    if not any(
        report.get("milestone_id") != FINISH_NODE_ID and _is_terminal_stage_status(report.get("status"))
        for report in reports
    ):
        return settlements
    return [
        settlement
        for settlement in settlements
        if settlement.get("kind") != "finish" and settlement.get("milestone_id") != FINISH_NODE_ID
    ]


def _settlement_summary(settlement: JsonObject, definitions: list[JsonObject] | None = None) -> JsonObject:
    metadata = settlement.get("metadata", {}) if isinstance(settlement.get("metadata"), dict) else {}
    stage_report = metadata.get("stage_report", {}) if isinstance(metadata.get("stage_report"), dict) else {}
    old_stage_id = str(stage_report.get("stage_id") or "")
    definition = _stage_definition_for_settlement(settlement, definitions or [])
    stage_id = _normalized_stage_id(old_stage_id, definitions or [], {}, definition)
    milestone_id = settlement.get("milestone_id")
    if not milestone_id and definition is not None:
        milestone_id = definition.get("milestone_id")
    return {
        "settlement_id": str(settlement.get("settlement_id") or ""),
        "stage_id": stage_id,
        "kind": str(settlement.get("kind") or ""),
        "milestone_id": str(milestone_id or ""),
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
        "evidence": clean_evidence_items([_text(value, 1200) for value in settlement.get("evidence", [])], 8),
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
        "pending_milestone_ids": _string_list(value.get("pending_milestone_ids")),
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
    return {
        "constraint_id": str(score.get("constraint_id") or ""),
        "score": _number(score.get("score")),
        "missing": score.get("missing"),
        "evidence": clean_evidence_items([_text(item, 500) for item in score.get("evidence", [])], 2),
    }


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
        "constraints": _constraint_definitions(adapted.get("constraints") or node.get("constraints")),
        "virtual": False,
    }


def _settlement_stage_id(settlement: JsonObject) -> str:
    metadata = settlement.get("metadata", {}) if isinstance(settlement.get("metadata"), dict) else {}
    stage_report = metadata.get("stage_report", {}) if isinstance(metadata.get("stage_report"), dict) else {}
    return str(stage_report.get("stage_id") or "")


def _stage_definitions(adapted_case: JsonObject) -> list[JsonObject]:
    if adapted_case is None:
        raise ValueError("adapted_case 不能为空")
    stage_goals = adapted_case.get("stage_goals", {})
    if not isinstance(stage_goals, dict):
        return []
    definitions: list[JsonObject] = []
    for stage_id, stage_goal in stage_goals.items():
        anchor_id, milestone_id = _split_stage_id(str(stage_id))
        if milestone_id == FINISH_NODE_ID:
            continue
        definitions.append(
            {
                "stage_id": str(stage_id),
                "anchor_milestone_id": anchor_id,
                "milestone_id": milestone_id,
                "stage_goal": _text(stage_goal, 1200),
            }
        )
    finish_definition = _finish_stage_definition(adapted_case, stage_goals)
    if finish_definition:
        definitions.append(finish_definition)
    return definitions


def _normalize_summary_stage_ids(
    summary: JsonObject,
    definitions: list[JsonObject],
    stage_id_map: dict[str, str],
) -> JsonObject:
    if summary is None:
        raise ValueError("summary 不能为空")
    normalized = _json_copy(summary)
    first_failure = normalized.get("first_failure_stage_id")
    if isinstance(first_failure, str):
        normalized["first_failure_stage_id"] = _normalized_stage_id(first_failure, definitions, stage_id_map, None)
    return normalized


def _stage_definition_for_report(report: JsonObject, definitions: list[JsonObject]) -> JsonObject | None:
    if report is None:
        raise ValueError("stage report 不能为空")
    stage_id = str(report.get("stage_id") or "")
    direct = _definition_for_stage_id(stage_id, definitions)
    if direct is not None:
        return direct
    milestone_id = report.get("milestone_id")
    if isinstance(milestone_id, str) and milestone_id:
        return _definition_for_milestone(milestone_id, definitions)
    if not milestone_id:
        return _finish_definition(definitions)
    return None


def _stage_definition_for_settlement(settlement: JsonObject, definitions: list[JsonObject]) -> JsonObject | None:
    if settlement is None:
        raise ValueError("settlement 不能为空")
    stage_id = _settlement_stage_id(settlement)
    direct = _definition_for_stage_id(stage_id, definitions)
    if direct is not None:
        return direct
    milestone_id = settlement.get("milestone_id")
    if isinstance(milestone_id, str) and milestone_id:
        return _definition_for_milestone(milestone_id, definitions)
    if settlement.get("kind") == "finish":
        return _finish_definition(definitions)
    return None


def _definition_for_stage_id(stage_id: str, definitions: list[JsonObject]) -> JsonObject | None:
    if not stage_id:
        return None
    for definition in definitions:
        if definition.get("stage_id") == stage_id:
            return definition
    legacy_milestone_id = _legacy_pending_milestone_id(stage_id)
    return _definition_for_milestone(legacy_milestone_id, definitions) if legacy_milestone_id else None


def _definition_for_milestone(milestone_id: str, definitions: list[JsonObject]) -> JsonObject | None:
    if not milestone_id:
        return None
    return next((definition for definition in definitions if definition.get("milestone_id") == milestone_id), None)


def _finish_definition(definitions: list[JsonObject]) -> JsonObject | None:
    return _definition_for_milestone(FINISH_NODE_ID, definitions)


def _should_keep_unreported_finish_definition(
    definition: JsonObject,
    finish_definition: JsonObject | None,
    reports: list[JsonObject],
) -> bool:
    if finish_definition is None or definition is not finish_definition:
        return False
    anchor_id = str(finish_definition.get("anchor_milestone_id") or "")
    if not anchor_id:
        return False
    return any(str(report.get("milestone_id") or "") == anchor_id for report in reports)


def _legacy_pending_milestone_id(stage_id: str) -> str:
    for prefix in ("runtime:fail:", "runtime:missing:"):
        if stage_id.startswith(prefix):
            return stage_id[len(prefix):]
    return ""


def _normalized_stage_id(
    stage_id: str,
    definitions: list[JsonObject],
    stage_id_map: dict[str, str],
    definition: JsonObject | None,
) -> str:
    if definition is not None:
        return str(definition.get("stage_id") or "")
    if stage_id in stage_id_map:
        return stage_id_map[stage_id]
    direct = _definition_for_stage_id(stage_id, definitions)
    if direct is not None:
        return str(direct.get("stage_id") or "")
    return stage_id


def _is_terminal_stage_status(status: Any) -> bool:
    return str(status or "").strip().lower() in {"fail", "missing", "invalid", "fatal"}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _replace_string(value: Any, source: str, target: str) -> Any:
    if not source or source == target:
        return value
    if isinstance(value, str):
        return target if value == source else value
    if isinstance(value, list):
        return [_replace_string(item, source, target) for item in value]
    if isinstance(value, dict):
        return {key: _replace_string(item, source, target) for key, item in value.items()}
    return value


def _finish_stage_definition(adapted_case: JsonObject, stage_goals: JsonObject) -> JsonObject:
    if adapted_case is None or stage_goals is None:
        raise ValueError("finish 阶段定义参数不能为空")
    graph = adapted_case.get("milestone_graph", {})
    metadata = graph.get("metadata", {}) if isinstance(graph, dict) else {}
    analysis = metadata.get("graph_analysis", {}) if isinstance(metadata, dict) else {}
    finish_id = str(analysis.get("finish_node_id") or FINISH_NODE_ID) if isinstance(analysis, dict) else FINISH_NODE_ID
    anchor_id = str(analysis.get("finish_stage_anchor_predecessor_id") or "") if isinstance(analysis, dict) else ""
    if not anchor_id:
        return {}
    stage_id = f"{anchor_id}->{finish_id}"
    return {
        "stage_id": stage_id,
        "anchor_milestone_id": anchor_id,
        "milestone_id": finish_id,
        "stage_goal": _text(stage_goals.get(stage_id) or DEFAULT_FINISH_STAGE_GOAL, 1200),
    }


def _split_stage_id(stage_id: str) -> tuple[str, str]:
    if not isinstance(stage_id, str) or "->" not in stage_id:
        raise ValueError(f"stage_id 必须使用 anchor->milestone 格式: {stage_id}")
    anchor_id, milestone_id = stage_id.split("->", 1)
    if not anchor_id or not milestone_id:
        raise ValueError(f"stage_id 必须包含非空 anchor 和 milestone: {stage_id}")
    return anchor_id, milestone_id


def _constraint_definitions(value: Any) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [_constraint_definition(item) for item in value[:8] if isinstance(item, dict)]


def _constraint_definition(constraint: JsonObject) -> JsonObject:
    return {
        "constraint_id": str(constraint.get("constraint_id") or ""),
        "target": str(constraint.get("target") or ""),
        "namespace": str(constraint.get("namespace") or ""),
        "operator": str(constraint.get("operator") or ""),
        "threshold": _number(constraint.get("threshold")),
        "hard": constraint.get("hard"),
        "evaluator_hint": _text(constraint.get("evaluator_hint"), 240),
        "expected_summary": _expected_summary(constraint.get("expected")),
        "expected_detail": _expected_detail(constraint.get("expected")),
    }


def _expected_summary(expected: Any) -> str:
    if isinstance(expected, dict):
        rows = expected.get("rows")
        columns = expected.get("columns")
        parts: list[str] = []
        if isinstance(rows, list):
            parts.append(f"rows={len(rows)}")
        if isinstance(columns, list):
            parts.append(f"columns={','.join(str(item) for item in columns[:4])}")
        if parts:
            return "; ".join(parts)
    return _text(expected, 240)


def _expected_detail(expected: Any) -> str:
    return _text(expected, 1200)


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
