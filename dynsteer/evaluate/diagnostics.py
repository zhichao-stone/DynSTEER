from __future__ import annotations

import json

from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    Constraint,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageInterval,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import stage_trajectory_steps
from dynsteer.utils import compact_text, json_safe


def trajectory_step_to_dict(step: TrajectoryStep) -> JsonObject:
    return json_safe(step)  # type: ignore[return-value]


def build_stage_trace(
    trajectory: Trajectory,
    interval: StageInterval,
) -> JsonObject:
    steps = [
        trajectory_step_to_dict(step)
        for step in stage_trajectory_steps(interval, trajectory)
    ]
    return {
        "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
        "start_boundary_step_index": interval.start_boundary_step_index,
        "start_step_index": interval.start_step_index,
        "end_step_index": interval.end_step_index,
        "interval_semantics": "(start_boundary_step_index, end_step_index]",
        "step_count": len(steps),
        "steps": steps,
    }


def milestone_score_to_dict(score: MilestoneScore) -> JsonObject:
    return json_safe(score)  # type: ignore[return-value]


def milestone_summary_to_dict(milestone: Milestone) -> JsonObject:
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "pass_threshold": milestone.pass_threshold,
        "constraint_count": len(milestone.constraints),
        "metadata": dict(milestone.metadata),
    }


def constraint_summary_to_dict(constraint: Constraint) -> JsonObject:
    expected = constraint.expected
    expected_summary: JsonObject = {"type": type(expected).__name__}
    if isinstance(expected, dict):
        rows = expected.get("rows")
        columns = expected.get("columns")
        expected_summary = {
            "type": "dict",
            "row_count": len(rows) if isinstance(rows, list) else None,
            "columns": list(columns) if isinstance(columns, list) else None,
        }
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "namespace": constraint.namespace,
        "selector": constraint.selector,
        "operator": constraint.operator.value,
        "weight": constraint.weight,
        "threshold": constraint.threshold,
        "hard": constraint.hard,
        "evaluator_hint": constraint.evaluator_hint,
        "reference_milestone_id": constraint.reference_milestone_id,
        "expected_summary": expected_summary,
        "metadata": dict(constraint.metadata),
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _format_number(value: object) -> str:
    number = _number(value)
    return "unknown" if number is None else f"{number:.3f}"


def _compact_json(value: object, limit: int = 360) -> str | None:
    if value is None:
        return None
    safe_value = json_safe(value)
    text = safe_value if isinstance(safe_value, str) else json.dumps(safe_value, ensure_ascii=False)
    return compact_text(text, limit)


def _constraint_by_id(milestone: Milestone) -> dict[str, Constraint]:
    return {constraint.constraint_id: constraint for constraint in milestone.constraints}


def _constraint_goal_hint(constraint: Constraint | None) -> str:
    if constraint is None:
        return ""
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    kind = str(semantics.get("kind") or "")
    if kind == "emit_message":
        return "需要向用户发出符合语义要求的消息"
    if kind == "set_state":
        namespace = constraint.namespace or "state"
        return f"需要让 {namespace} 状态达到目标值"
    if kind == "preserve_state":
        namespace = constraint.namespace or "state"
        return f"需要保持 {namespace} 状态不被破坏"
    if kind == "tool_call":
        return "需要调用符合要求的工具"

    metadata = constraint.metadata.get("toolsandbox")
    if isinstance(metadata, dict):
        namespace = constraint.namespace or str(metadata.get("database_namespace") or "")
        measure = str(metadata.get("snapshot_constraint") or "")
        if metadata.get("guardrail"):
            return f"需要保持 {namespace or 'state'} guardrail 通过"
        if namespace:
            return f"需要满足 {namespace} 的 {measure or 'snapshot'} 约束"
    return f"target={constraint.target.value}, operator={constraint.operator.value}"


def _constraint_failure_detail(
    constraint: Constraint | None,
    score: JsonObject,
) -> JsonObject:
    threshold = constraint.threshold if constraint is not None else 1.0
    evidence = score.get("evidence")
    metadata = constraint.metadata.get("toolsandbox") if constraint is not None else None
    toolsandbox_measure = (
        str(metadata.get("snapshot_constraint") or "") if isinstance(metadata, dict) else ""
    )
    actual_excerpt = _compact_json(score.get("actual"), 420) if "actual" in score else None
    detail: JsonObject = {
        "constraint_id": str(score.get("constraint_id") or (constraint.constraint_id if constraint else "constraint")),
        "score": score.get("score"),
        "threshold": threshold,
        "missing": bool(score.get("missing")),
        "hard": constraint.hard if constraint is not None else None,
        "target": constraint.target.value if constraint is not None else None,
        "namespace": constraint.namespace if constraint is not None else None,
        "operator": constraint.operator.value if constraint is not None else None,
        "semantic_kind": (
            str(constraint.stage_goal_semantics.get("kind") or "")
            if constraint is not None and isinstance(constraint.stage_goal_semantics, dict)
            else ""
        ),
        "goal_hint": _constraint_goal_hint(constraint),
        "toolsandbox_measure": toolsandbox_measure,
        "evidence": [
            compact_text(item, 220)
            for item in evidence[:2]
        ] if isinstance(evidence, list) else [],
        "actual_excerpt": actual_excerpt,
    }
    if constraint is not None:
        detail["expected_summary"] = constraint_summary_to_dict(constraint)["expected_summary"]
    return detail


def _failed_constraint_details(milestone: Milestone, score_payload: JsonObject | None) -> list[JsonObject]:
    if not isinstance(score_payload, dict):
        return []
    raw_scores = score_payload.get("constraint_scores")
    if not isinstance(raw_scores, list):
        return []
    constraints = _constraint_by_id(milestone)
    details: list[tuple[float, JsonObject]] = []
    fallbacks: list[tuple[float, JsonObject]] = []
    for raw_score in raw_scores:
        if not isinstance(raw_score, dict):
            continue
        constraint_id = str(raw_score.get("constraint_id") or "")
        constraint = constraints.get(constraint_id)
        threshold = constraint.threshold if constraint is not None else 1.0
        score_value = _number(raw_score.get("score"))
        missing = bool(raw_score.get("missing"))
        detail = _constraint_failure_detail(constraint, raw_score)
        sort_key = score_value if score_value is not None else -1.0
        fallbacks.append((sort_key, detail))
        if missing or score_value is None or score_value < threshold:
            details.append((sort_key, detail))
    selected = details or sorted(fallbacks, key=lambda item: item[0])[:1]
    return [detail for _, detail in sorted(selected, key=lambda item: item[0])[:5]]


def _constraint_failure_line(detail: JsonObject) -> str:
    constraint_id = str(detail.get("constraint_id") or "constraint")
    score = _format_number(detail.get("score"))
    threshold = _format_number(detail.get("threshold"))
    goal_hint = str(detail.get("goal_hint") or "")
    evidence = detail.get("evidence")
    evidence_text = str(evidence[0]) if isinstance(evidence, list) and evidence else ""
    parts = [f"{constraint_id} 得分 {score}，低于阈值 {threshold}"]
    if goal_hint:
        parts.append(goal_hint)
    if evidence_text:
        parts.append(f"证据：{evidence_text}")
    return "；".join(parts)


def _pending_failure_diagnostics(
    milestone: Milestone,
    blocker: str,
    common: JsonObject,
    best_entry: JsonObject | None,
    last_entry: JsonObject | None,
    pending_predecessors: list[str],
) -> JsonObject:
    attempt_count = int(common.get("attempt_count") or 0)
    score_payload = best_entry.get("score") if isinstance(best_entry, dict) else None
    boundary = best_entry.get("boundary") if isinstance(best_entry, dict) else None
    failed_constraints = _failed_constraint_details(milestone, score_payload if isinstance(score_payload, dict) else None)
    threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8

    if blocker == "attempted_but_not_pass":
        step_text = ""
        if isinstance(boundary, dict) and boundary.get("step_index") is not None:
            step_text = f"；最佳候选位于 step={boundary.get('step_index')}"
        status_text = str(score_payload.get("status") or common.get("best_status") or "unknown") if isinstance(score_payload, dict) else str(common.get("best_status") or "unknown")
        summary = (
            f"milestone {milestone.milestone_id} 已尝试匹配 {attempt_count} 次，但没有候选达到通过阈值 "
            f"{threshold:.3f}{step_text}，最佳得分 {_format_number(common.get('best_score'))}（status={status_text}）。"
        )
        reasons = [summary]
        if failed_constraints:
            lead = _constraint_failure_line(failed_constraints[0])
            summary = f"{summary} 主要未满足约束：{lead}。"
            reasons[0] = summary
            reasons.extend(_constraint_failure_line(item) for item in failed_constraints[1:])
        elif isinstance(last_entry, dict) and last_entry.get("reject_reason"):
            reasons.append(f"最后一次拒绝原因：{last_entry.get('reject_reason')}")
        return {
            "failure_summary": summary,
            "failure_reasons": reasons,
            "failed_constraints": failed_constraints,
        }

    if blocker == "predecessor_not_matched":
        missing_text = ", ".join(pending_predecessors) if pending_predecessors else "unknown"
        summary = f"milestone {milestone.milestone_id} 尚未进入可评估状态，因为前驱 milestone 未完成：{missing_text}。"
        return {
            "failure_summary": summary,
            "failure_reasons": [summary],
            "failed_constraints": failed_constraints,
        }

    if blocker == "ready_without_candidate":
        summary = f"milestone {milestone.milestone_id} 曾经 ready，但运行结束前没有出现可评分的候选边界。"
    else:
        summary = f"milestone {milestone.milestone_id} 从未 ready；可能仍缺少前序阶段证据或轨迹未推进到该阶段。"
    return {
        "failure_summary": summary,
        "failure_reasons": [summary],
        "failed_constraints": failed_constraints,
    }


def build_milestone_graph_summary(graph: MilestoneGraph) -> JsonObject:
    return {
        "total_milestone_count": len(graph.nodes),
        "mandatory_milestone_ids": [node.milestone_id for node in graph.nodes],
        "edges": [[source, target] for source, target in graph.edges],
        "nodes": [
            {
                **milestone_summary_to_dict(node),
                "constraints": [constraint_summary_to_dict(constraint) for constraint in node.constraints],
            }
            for node in graph.nodes
        ],
    }


def boundary_to_dict(boundary: Boundary) -> JsonObject:
    return json_safe(boundary)  # type: ignore[return-value]


def build_milestone_candidate_detail(
    milestone: Milestone,
    boundary: Boundary | None,
    score: MilestoneScore | None,
    selected: bool,
    reject_reason: str | None,
) -> JsonObject:
    return {
        "milestone_id": milestone.milestone_id,
        "boundary": boundary_to_dict(boundary) if boundary is not None else None,
        "score": milestone_score_to_dict(score) if score is not None else None,
        "selected": selected,
        "reject_reason": reject_reason,
    }


def build_milestone_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    ready_milestone_ids_before_match: list[str],
) -> JsonObject:
    return {
        "mode": "runtime_checkpoint",
        "matched": True,
        "milestone": milestone_summary_to_dict(milestone),
        "boundary": boundary_to_dict(boundary),
        "score": milestone_score_to_dict(milestone_score),
        "ready_milestone_ids_before_match": list(ready_milestone_ids_before_match),
        "matched_milestone_ids_before_match": sorted(matched),
        "predecessor_milestone_ids": list(milestone.dependency_predecessor_ids),
    }


def build_final_milestone_diagnostics(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    match_attempts: list[JsonObject],
) -> list[JsonObject]:
    milestone_ids = {node.milestone_id for node in graph.nodes}
    attempts_by_milestone: dict[str, list[JsonObject]] = {milestone_id: [] for milestone_id in milestone_ids}
    ready_seen: set[str] = set()
    for attempt in match_attempts:
        ready_ids = attempt.get("ready_before")
        if isinstance(ready_ids, list):
            ready_seen.update(str(item) for item in ready_ids if str(item) in milestone_ids)
        raw_candidates = attempt.get("candidate_scores")
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if isinstance(candidate, dict) and str(candidate.get("milestone_id")) in milestone_ids:
                    attempts_by_milestone[str(candidate["milestone_id"])].append(candidate)

    diagnostics: list[JsonObject] = []
    for node in graph.nodes:
        candidate_entries = attempts_by_milestone[node.milestone_id]
        scored_entries = [entry for entry in candidate_entries if isinstance(entry.get("score"), dict)]
        best_entry = max(scored_entries, key=lambda item: float(item["score"].get("score", 0.0)), default=None)
        last_entry = candidate_entries[-1] if candidate_entries else None
        pending_predecessors = [item for item in node.dependency_predecessor_ids if item not in matched]
        best_score = best_entry.get("score") if isinstance(best_entry, dict) else None
        best_boundary = best_entry.get("boundary") if isinstance(best_entry, dict) else None
        common: JsonObject = {
            "milestone_id": node.milestone_id,
            "mandatory": True,
            "dependency_predecessor_ids": list(node.dependency_predecessor_ids),
            "stage_anchor_milestone_id": node.stage_anchor_predecessor_id,
            "ready_ever": node.milestone_id in ready_seen,
            "attempt_count": len(candidate_entries),
            "best_score": best_score.get("score") if isinstance(best_score, dict) else None,
            "best_status": best_score.get("status") if isinstance(best_score, dict) else None,
            "best_boundary_step_index": (
                best_boundary.get("step_index") if isinstance(best_boundary, dict) else None
            ),
        }

        if node.milestone_id in matched:
            settlement = matched[node.milestone_id]
            diagnostics.append(
                {
                    **common,
                    "final_state": "matched",
                    "blocker": None,
                    "settlement_id": settlement.settlement_id,
                    "boundary_step_index": settlement.boundary_step_index,
                    "stage_start_boundary_step_index": settlement.metadata.get("stage_start_boundary_step_index"),
                    "stage_start_step_index": settlement.start_step_index,
                    "stage_end_step_index": settlement.end_step_index,
                    "best_score": common["best_score"] if common["best_score"] is not None else settlement.score,
                    "best_status": common["best_status"] if common["best_status"] is not None else settlement.status,
                    "best_boundary_step_index": common["best_boundary_step_index"]
                    if common["best_boundary_step_index"] is not None
                    else settlement.boundary_step_index,
                    "last_reject_reason": None,
                    "pending_predecessor_ids": [],
                }
            )
            continue

        if candidate_entries:
            blocker = "attempted_but_not_pass"
        elif pending_predecessors:
            blocker = "predecessor_not_matched"
        elif common["ready_ever"]:
            blocker = "ready_without_candidate"
        else:
            blocker = "not_ready"
        failure_diagnostics = _pending_failure_diagnostics(
            milestone=node,
            blocker=blocker,
            common=common,
            best_entry=best_entry,
            last_entry=last_entry,
            pending_predecessors=pending_predecessors,
        )
        diagnostics.append(
            {
                **common,
                "final_state": "pending",
                "blocker": blocker,
                "settlement_id": None,
                "boundary_step_index": None,
                "stage_start_boundary_step_index": None,
                "stage_start_step_index": None,
                "stage_end_step_index": None,
                "last_reject_reason": last_entry.get("reject_reason") if isinstance(last_entry, dict) else None,
                "pending_predecessor_ids": pending_predecessors,
                **failure_diagnostics,
            }
        )
    return diagnostics


def build_finish_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> JsonObject:
    matched_ids = set(matched)
    milestone_ids = {node.milestone_id for node in graph.nodes}
    return {
        "mode": "runtime_finish",
        "matched": False,
        "matched_milestone_ids": sorted(matched_ids),
        "pending_milestone_ids": sorted(milestone_ids - matched_ids),
        "total_milestone_count": len(graph.nodes),
    }
