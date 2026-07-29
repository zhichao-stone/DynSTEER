from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.runtime import selected_candidate_from_attempt
from dynsteer.model import JsonObject, JsonValue, RuntimeEvaluationDecision, StageEvaluationResult, TaskCase
from dynsteer.utils import compact_text, first_text, optional_str
_TEXT_LIMIT = 160
_LIST_LIMIT = 8

def policy_stop_log_extra(task_case: TaskCase, decision: RuntimeEvaluationDecision) -> JsonObject:
    """构造策略提前终止 warning 摘要。"""
    stage_result = decision.stage_result
    matched_ids = sorted(decision.next_state.matched_settlements)
    matched_set = set(matched_ids)
    pending_ids = sorted((node.milestone_id for node in task_case.milestone_graph.nodes if node.milestone_id not in matched_set))
    milestone_score, milestone_status = _milestone_layer(stage_result)
    if milestone_score is None and milestone_status is None:
        milestone_score, milestone_status = _milestone_layer(decision.checkpoint)
    last_attempt = next((item for item in reversed(decision.next_state.match_attempts) if isinstance(item, dict)), None)
    selected_candidate = selected_candidate_from_attempt(last_attempt) if last_attempt is not None else None
    dimension_levels = {}
    if stage_result is not None:
        dimension_levels = {dimension.value: level.value for dimension, level in stage_result.dimension_levels.items()}
    extra: JsonObject = {'case_id': str(task_case.case_id), 'termination_code': decision.termination.termination_code, 'termination_reason': decision.termination.termination_reason, 'matched_milestone_ids': matched_ids, 'pending_milestone_ids': pending_ids, 'stage_id': stage_result.stage_id if stage_result is not None else None, 'milestone_id': stage_result.milestone_id if stage_result is not None else None, 'milestone_score': milestone_score, 'milestone_status': milestone_status, 'stage_score': stage_result.stage_score if stage_result is not None else None, 'stage_status': stage_result.status.value if stage_result is not None else None, 'dimension_levels': dimension_levels, 'stage_first_evidence': first_text(stage_result.evidence, _TEXT_LIMIT) if stage_result is not None else None, 'stage_first_diagnosis': first_text(stage_result.diagnosis, _TEXT_LIMIT) if stage_result is not None else None, 'last_match_step_index': last_attempt.get('step_index') if last_attempt is not None else None, 'last_selected_milestone_id': selected_candidate.get('milestone_id') if selected_candidate is not None else None}
    return _sanitize_extra(extra)

def _milestone_layer(source: StageEvaluationResult | HarnessStageSettlement | None) -> tuple[float | None, str | None]:
    if source is None:
        return (None, None)
    matching = source.metadata.get('milestone_matching')
    if not isinstance(matching, dict):
        return (None, None)
    score = matching.get('score')
    if not isinstance(score, dict):
        return (None, None)
    return (_optional_float(score.get('score')), optional_str(score.get('status')))

def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)

def _sanitize_extra(value: JsonValue | JsonObject) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError('日志 extra 必须是 JSON 对象')
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
