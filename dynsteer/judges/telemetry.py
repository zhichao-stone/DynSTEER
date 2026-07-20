import hashlib

from dynsteer.model import JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory, TrajectoryStep
from dynsteer.stage import stage_trajectory_steps
from dynsteer.stage import resolve_stage_goal
from dynsteer.utils import compact_text, first_text, string_list


def judge_input_metadata(
    interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, prompt: str, prompt_type: str | None = None
) -> JsonObject:
    """构造 LLM judge 调用前输入快照元数据。"""
    if interval is None or task_case is None or trajectory is None or prompt is None:
        raise ValueError("judge 输入快照参数不能为空")
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
    """构造 LLM judge 输出快照元数据。"""
    if result is None or input_metadata is None:
        raise ValueError("judge 输出快照参数不能为空")
    return {
        "judge_status": result.status.value,
        "judge_dimension_scores": {dimension.value: score for dimension, score in result.dimension_scores.items()},
        "judge_dimension_confidence_avg": _average_confidence(result),
        "judge_first_diagnosis": first_text(result.diagnosis, 240),
        "judge_first_evidence": first_text(result.evidence, 240),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }


def judge_payload_output_metadata(payload: JsonObject, input_metadata: JsonObject) -> JsonObject:
    """根据已校验 payload 构造中间轮次输出快照。"""
    if payload is None or input_metadata is None:
        raise ValueError("judge payload 输出快照参数不能为空")
    return {
        "judge_status": str(payload.get("status")),
        "judge_dimension_scores": _payload_dimension_scores(payload),
        "judge_first_diagnosis": first_text(string_list(payload.get("diagnosis")), 240),
        "judge_first_evidence": first_text(string_list(payload.get("evidence")), 240),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }


def _payload_dimension_scores(payload: JsonObject) -> JsonObject:
    value = payload.get("dimension_scores")
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, int | float)}


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
        return compact_text(
            f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_call={step.tool_call.name}", 240
        )
    if step.tool_result is not None:
        return compact_text(
            f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_result={step.tool_result.success}", 240
        )
    return compact_text(f"step {step.index} {step.actor.value}/{step.event_type.value}", 240)
