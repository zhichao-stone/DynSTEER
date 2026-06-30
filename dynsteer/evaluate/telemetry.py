from __future__ import annotations

from dynsteer.evaluate.models import RuntimeEvaluationDecision
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    JsonObject,
    JsonValue,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    TaskCase,
    TrajectoryStep,
)

_TEXT_LIMIT = 160
_LIST_LIMIT = 8


def match_attempt_log_extra(case_id: str, step: TrajectoryStep, attempt_detail: JsonObject) -> JsonObject:
    """构造 checkpoint 匹配尝试日志摘要。

    Args:
        case_id: benchmark case ID。
        step: 当前触发检查的轨迹步骤。
        attempt_detail: milestone 匹配诊断详情。

    Returns:
        可放入 logger extra 的轻量 JSON 摘要。
    """
    if case_id is None or step is None or attempt_detail is None:
        raise ValueError("匹配尝试日志参数不能为空")
    candidates = _dict_list(attempt_detail.get("candidate_scores"))
    best_candidate = _best_candidate(candidates)
    best_score = _score_object(best_candidate)
    extra: JsonObject = {
        "case_id": str(case_id),
        "step_index": step.index,
        "step_id": step.step_id,
        "step_actor": step.actor.value,
        "step_event_type": step.event_type.value,
        "ready_milestone_ids": _string_list(attempt_detail.get("ready_before")),
        "matched_milestone_ids": _string_list(attempt_detail.get("matched_before")),
        "selected_milestone_id": _optional_str(attempt_detail.get("selected_milestone_id")),
        "candidate_count": len(candidates),
        "best_candidate_milestone_id": _optional_str(best_candidate.get("milestone_id") if best_candidate else None),
        "best_candidate_score": _optional_float(best_score.get("score") if best_score else None),
        "best_candidate_status": _optional_str(best_score.get("status") if best_score else None),
        "best_candidate_reject_reason": _optional_str(
            best_candidate.get("reject_reason") if best_candidate else None
        ),
        "best_candidate_constraint_summary": _constraint_summary(best_score),
    }
    return _sanitize_extra(extra)


def milestone_checkpoint_log_extra(
    case_id: str,
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    stage_result: StageEvaluationResult,
    matched_before: dict[str, HarnessStageSettlement],
    ready_before: list[str],
) -> JsonObject:
    """构造 milestone 命中日志摘要。

    Args:
        case_id: benchmark case ID。
        milestone: 当前命中的 milestone。
        boundary: 当前命中的边界。
        milestone_score: milestone 匹配评分。
        stage_result: 对应阶段评估结果。
        matched_before: 命中前已经结算的 milestone。
        ready_before: 命中前可匹配 milestone ID。

    Returns:
        可放入 logger extra 的轻量 JSON 摘要。
    """
    if (
        case_id is None
        or milestone is None
        or boundary is None
        or milestone_score is None
        or stage_result is None
        or matched_before is None
        or ready_before is None
    ):
        raise ValueError("checkpoint 日志参数不能为空")
    extra: JsonObject = {
        "case_id": str(case_id),
        "milestone_id": milestone.milestone_id,
        "boundary_id": boundary.boundary_id,
        "boundary_step_index": boundary.step_index,
        "ready_milestone_ids_before_match": list(ready_before),
        "matched_milestone_ids_before_match": sorted(matched_before),
        "milestone_score": milestone_score.score,
        "milestone_status": milestone_score.status.value,
        "stage_id": stage_result.stage_id,
        "stage_score": stage_result.stage_score,
        "stage_status": stage_result.status.value,
        "evaluator_level": stage_result.evaluator_level.value,
        "stage_first_evidence": _first_text(stage_result.evidence),
        "stage_first_diagnosis": _first_text(stage_result.diagnosis),
    }
    return _sanitize_extra(extra)


def policy_stop_log_extra(
    case_id: str,
    task_case: TaskCase,
    decision: RuntimeEvaluationDecision,
    termination_code: str | None,
    termination_reason: str | None,
) -> JsonObject:
    """构造策略提前终止 warning 摘要。

    Args:
        case_id: benchmark case ID。
        task_case: 当前任务定义。
        decision: 触发终止的运行期决策。
        termination_code: 终止代码。
        termination_reason: 中文终止原因。

    Returns:
        可放入 logger extra 的轻量 JSON 摘要。
    """
    if case_id is None or task_case is None or decision is None:
        raise ValueError("策略终止日志参数不能为空")
    stage_result = decision.stage_result
    matched_ids = sorted(decision.next_state.matched_settlements)
    pending_ids = _pending_required_ids(task_case.milestone_graph, matched_ids)
    milestone_score, milestone_status = _milestone_layer_from_stage(stage_result)
    last_attempt = _last_match_attempt(decision.next_state.match_attempts)
    extra: JsonObject = {
        "case_id": str(case_id),
        "termination_code": termination_code,
        "termination_reason": termination_reason,
        "matched_milestone_ids": matched_ids,
        "pending_required_milestone_ids": pending_ids,
        "stage_id": stage_result.stage_id if stage_result is not None else None,
        "milestone_id": stage_result.milestone_id if stage_result is not None else None,
        "milestone_score": milestone_score,
        "milestone_status": milestone_status,
        "stage_score": stage_result.stage_score if stage_result is not None else None,
        "stage_status": stage_result.status.value if stage_result is not None else None,
        "evaluator_level": stage_result.evaluator_level.value if stage_result is not None else None,
        "stage_first_evidence": _first_text(stage_result.evidence) if stage_result is not None else None,
        "stage_first_diagnosis": _first_text(stage_result.diagnosis) if stage_result is not None else None,
        "last_match_step_index": last_attempt.get("step_index") if last_attempt is not None else None,
        "last_selected_milestone_id": (
            last_attempt.get("selected_milestone_id") if last_attempt is not None else None
        ),
    }
    return _sanitize_extra(extra)


def pending_milestones_log_extra(case_id: str, diagnostics: list[JsonObject]) -> JsonObject:
    """构造自然结束后 pending required milestone 摘要。

    Args:
        case_id: benchmark case ID。
        diagnostics: `build_final_milestone_diagnostics()` 返回的最终诊断列表。

    Returns:
        可放入 logger extra 的轻量 JSON 摘要。
    """
    if case_id is None or diagnostics is None:
        raise ValueError("pending milestone 日志参数不能为空")
    pending = [
        item
        for item in diagnostics
        if item.get("required") is True and item.get("final_state") != "matched"
    ]
    first = pending[0] if pending else {}
    extra: JsonObject = {
        "case_id": str(case_id),
        "pending_required_milestone_ids": [str(item.get("milestone_id")) for item in pending],
        "pending_milestone_count": len(pending),
        "first_pending_milestone_id": _optional_str(first.get("milestone_id")),
        "blocker": _optional_str(first.get("blocker")),
        "best_score": _optional_float(first.get("best_score")),
        "best_status": _optional_str(first.get("best_status")),
        "best_boundary_step_index": _optional_int(first.get("best_boundary_step_index")),
        "last_reject_reason": _optional_str(first.get("last_reject_reason")),
        "pending_predecessor_ids": _string_list(first.get("pending_predecessor_ids")),
    }
    return _sanitize_extra(extra)


def _pending_required_ids(graph: MilestoneGraph | None, matched_ids: list[str]) -> list[str]:
    if graph is None:
        return []
    matched = set(matched_ids)
    return sorted(node.milestone_id for node in graph.nodes if node.required and node.milestone_id not in matched)


def _milestone_layer_from_stage(stage_result: StageEvaluationResult | None) -> tuple[float | None, str | None]:
    if stage_result is None:
        return None, None
    matching = stage_result.metadata.get("milestone_matching")
    if not isinstance(matching, dict):
        return None, None
    score = matching.get("score")
    if not isinstance(score, dict):
        return None, None
    return _optional_float(score.get("score")), _optional_str(score.get("status"))


def _last_match_attempt(attempts: list[JsonObject]) -> JsonObject | None:
    for item in reversed(attempts):
        if isinstance(item, dict):
            return item
    return None


def _best_candidate(candidates: list[JsonObject]) -> JsonObject | None:
    scored = [candidate for candidate in candidates if _score_object(candidate) is not None]
    if not scored:
        return candidates[0] if candidates else None
    selected = [candidate for candidate in scored if candidate.get("selected") is True]
    if selected:
        return selected[0]
    return max(scored, key=lambda candidate: _optional_float(_score_object(candidate).get("score")) or 0.0)


def _score_object(candidate: JsonObject | None) -> JsonObject | None:
    if candidate is None:
        return None
    score = candidate.get("score")
    return score if isinstance(score, dict) else None


def _constraint_summary(score: JsonObject | None) -> list[JsonObject]:
    if score is None:
        return []
    constraints = score.get("constraint_scores")
    if not isinstance(constraints, list):
        return []
    result: list[JsonObject] = []
    for item in constraints[:_LIST_LIMIT]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "constraint_id": _optional_str(item.get("constraint_id")),
                "score": _optional_float(item.get("score")),
                "missing": bool(item.get("missing")),
            }
        )
    return result


def _first_text(values: list[str]) -> str | None:
    if not values:
        return None
    return _truncate(str(values[0]))


def _dict_list(value: JsonValue | object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return _truncate(str(value))


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _sanitize_extra(value: JsonValue | JsonObject) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("日志 extra 必须是 JSON 对象")
    return {str(key): _sanitize_value(item) for key, item in value.items()}


def _sanitize_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:_LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in list(value.items())[:_LIST_LIMIT]}
    return _truncate(str(value))


def _truncate(value: str, limit: int = _TEXT_LIMIT) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
