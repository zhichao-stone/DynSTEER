from __future__ import annotations

from dynsteer.evaluate.runtime import RuntimeEvaluationDecision
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    JsonObject,
    JsonValue,
    MilestoneGraph,
    StageEvaluationResult,
    TaskCase,
)
from dynsteer.utils import compact_text, first_text

_TEXT_LIMIT = 160
_LIST_LIMIT = 8


def policy_stop_log_extra(
    case_id: str,
    task_case: TaskCase,
    decision: RuntimeEvaluationDecision,
    termination_code: str | None,
    termination_reason: str | None,
) -> JsonObject:
    """构造策略提前终止 warning 摘要。"""
    if case_id is None or task_case is None or decision is None:
        raise ValueError("策略终止日志参数不能为空")
    stage_result = decision.stage_result
    matched_ids = sorted(decision.next_state.matched_settlements)
    pending_ids = _pending_required_ids(task_case.milestone_graph, matched_ids)
    milestone_score, milestone_status = _milestone_layer_from_stage(stage_result)
    if milestone_score is None and milestone_status is None:
        milestone_score, milestone_status = _milestone_layer_from_checkpoint(decision.checkpoint)
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
        "stage_first_evidence": first_text(stage_result.evidence, _TEXT_LIMIT) if stage_result is not None else None,
        "stage_first_diagnosis": first_text(stage_result.diagnosis, _TEXT_LIMIT) if stage_result is not None else None,
        "last_match_step_index": last_attempt.get("step_index") if last_attempt is not None else None,
        "last_selected_milestone_id": (
            last_attempt.get("selected_milestone_id") if last_attempt is not None else None
        ),
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


def _milestone_layer_from_checkpoint(
    checkpoint: HarnessStageSettlement | None,
) -> tuple[float | None, str | None]:
    if checkpoint is None:
        return None, None
    matching = checkpoint.metadata.get("milestone_matching")
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


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return compact_text(value, _TEXT_LIMIT)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _sanitize_extra(value: JsonValue | JsonObject) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("日志 extra 必须是 JSON 对象")
    return {str(key): _sanitize_value(item) for key, item in value.items()}


def _sanitize_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return compact_text(value, _TEXT_LIMIT)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:_LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in list(value.items())[:_LIST_LIMIT]}
    return compact_text(value, _TEXT_LIMIT)
