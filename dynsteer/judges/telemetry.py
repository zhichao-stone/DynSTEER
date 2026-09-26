import hashlib
from dynsteer.model import JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory, TrajectoryStep, ValidatedJudgePayload
from dynsteer.stage import stage_trajectory_steps
from dynsteer.stage import resolve_stage_goal
from dynsteer.utils import compact_text, first_text

def judge_input_metadata(interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, prompt: str, prompt_type: str | None=None) -> JsonObject:
    """Constructs a snapshot metadata input before the LLM judge call."""
    if interval is None or task_case is None or trajectory is None or (prompt is None):
        raise ValueError('Judge input snapshots cannot be empty')
    stage_goal = resolve_stage_goal(interval, task_case)
    stage_steps = stage_trajectory_steps(interval, trajectory)
    constraint_check_count = 0
    if interval.milestone_score is not None:
        constraint_check_count = len(interval.milestone_score.constraint_scores)
    first_step = stage_steps[0] if stage_steps else None
    last_step = stage_steps[-1] if stage_steps else None
    metadata: JsonObject = {
        "stage_id": interval.stage_id,
        "milestone_id": interval.milestone_id,
        "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
        "start_boundary_step_index": interval.start_boundary_step_index,
        "start_step_index": interval.start_step_index,
        "end_step_index": interval.end_step_index,
        "task_description": task_case.task_description,
        "prompt_context_digest": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_task_description_excerpt": compact_text(task_case.task_description, 240),
        "stage_goal_objective_excerpt": compact_text(stage_goal, 240),
        "stage_step_count": len(stage_steps),
        "constraint_check_count": constraint_check_count,
        "first_stage_step_excerpt": _step_excerpt(first_step),
        "last_stage_step_excerpt": _step_excerpt(last_step),
    }
    if prompt_type is not None:
        metadata["prompt_type"] = prompt_type
    return metadata

def judge_result_output_metadata(result: StageEvaluationResult, input_metadata: JsonObject) -> JsonObject:
    """Constructs the LLM output snapshot metadata."""
    if result is None or input_metadata is None:
        raise ValueError('Judge output snapshot parameters cannot be empty')
    return {
        "judge_status": result.status.value,
        "judge_dimension_scores": {
            dimension.value: score
            for dimension, score in result.dimension_scores.items()
        },
        "judge_dimension_confidence_avg": _average_confidence(result),
        "judge_first_diagnosis": first_text(result.diagnosis, 240),
        "judge_first_evidence": first_text(result.evidence, 240),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }

def judge_payload_output_metadata(payload: ValidatedJudgePayload, input_metadata: JsonObject) -> JsonObject:
    """A snapshot of the intermediate wheel output based on the verified Payload construction."""
    if payload is None or input_metadata is None:
        raise ValueError('Judge payload output snapshots cannot be empty')
    return {
        "judge_status": payload.status.value,
        "judge_dimension_scores": {dimension.value: score for dimension, score in payload.dimension_scores.items()},
        "judge_first_diagnosis": first_text(payload.diagnosis, 240),
        "judge_first_evidence": first_text(payload.evidence, 240),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }

def _average_confidence(result: StageEvaluationResult) -> float | None:
    if result.dimension_confidence is None or not result.dimension_confidence:
        return None
    return sum(result.dimension_confidence.values()) / len(result.dimension_confidence)

def _step_excerpt(step: TrajectoryStep | None) -> str | None:
    if step is None:
        return None
    content = step.content
    if isinstance(content, str) and content.strip():
        return compact_text(f"step {step.index} {step.actor.value}/{step.event_type.value}: {content}", 240)
    if step.tool_call is not None:
        return compact_text(f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_call={step.tool_call.name}", 240)
    if step.tool_result is not None:
        return compact_text(f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_result={step.tool_result.success}", 240)
    return compact_text(f"step {step.index} {step.actor.value}/{step.event_type.value}", 240)
