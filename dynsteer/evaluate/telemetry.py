from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.log import sanitize_log_value
from dynsteer.evaluate.runtime import selected_candidate_from_attempt
from dynsteer.model import JsonObject, RuntimeEvaluationDecision, RuntimeEvaluationState, StageEvaluationResult, TaskCase
from dynsteer.utils import as_number, first_text, optional_str


_TEXT_LIMIT = 160

def policy_stop_log_extra(task_case: TaskCase, state: RuntimeEvaluationState, decision: RuntimeEvaluationDecision) -> JsonObject:
    """The tectonic policy terminates prematurely the warning summary."""
    stage_result = decision.stage_result
    matched_ids = sorted(state.matched_settlements)
    matched_set = set(matched_ids)
    pending_ids = sorted((node.milestone_id for node in task_case.milestone_graph.nodes if node.milestone_id not in matched_set))
    milestone_score, milestone_status = _milestone_layer(stage_result)
    if milestone_score is None and milestone_status is None:
        milestone_score, milestone_status = _milestone_layer(decision.checkpoint)
    last_attempt = next((item for item in reversed(state.match_attempts) if isinstance(item, dict)), None)
    selected_candidate = selected_candidate_from_attempt(last_attempt) if last_attempt is not None else None
    dimension_levels = {}
    if stage_result is not None:
        dimension_levels = {dimension.value: level.value for dimension, level in stage_result.dimension_levels.items()}
    extra: JsonObject = {
        "case_id": str(task_case.case_id),
        "termination_code": decision.termination.termination_code,
        "termination_reason": decision.termination.termination_reason,
        "matched_milestone_ids": matched_ids,
        "pending_milestone_ids": pending_ids,
        "stage_id": stage_result.stage_id if stage_result is not None else None,
        "milestone_id": stage_result.milestone_id if stage_result is not None else None,
        "milestone_score": milestone_score,
        "milestone_status": milestone_status,
        "stage_score": stage_result.stage_score if stage_result is not None else None,
        "stage_status": stage_result.status.value if stage_result is not None else None,
        "dimension_levels": dimension_levels,
        "stage_first_evidence": first_text(stage_result.evidence, _TEXT_LIMIT)
        if stage_result is not None
        else None,
        "stage_first_diagnosis": first_text(stage_result.diagnosis, _TEXT_LIMIT)
        if stage_result is not None
        else None,
        "last_match_step_index": last_attempt.get("step_index") if last_attempt is not None else None,
        "last_selected_milestone_id": selected_candidate.get("milestone_id")
        if selected_candidate is not None
        else None,
    }
    return {key: sanitize_log_value(value) for key, value in extra.items()}

def _milestone_layer(source: StageEvaluationResult | HarnessStageSettlement | None) -> tuple[float | None, str | None]:
    if source is None:
        return (None, None)
    matching = source.metadata.get("milestone_matching")
    if not isinstance(matching, dict):
        return (None, None)
    score = matching.get("score")
    if not isinstance(score, dict):
        return (None, None)
    return (as_number(score.get("score")), optional_str(score.get("status")))
