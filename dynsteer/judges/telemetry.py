from __future__ import annotations

import hashlib

from dynsteer.model import JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory, TrajectoryStep
from dynsteer.stage import stage_trajectory_steps
from dynsteer.stage_goal import resolve_stage_goal


def judge_input_metadata(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    prompt: str,
    prompt_type: str | None = None,
) -> JsonObject:
    """构造 LLM judge 调用前输入快照元数据。

    Args:
        interval: 当前阶段区间。
        task_case: 当前任务定义。
        trajectory: 当前轨迹。
        prompt: 已渲染 prompt。
        prompt_type: 可选 prompt 类型，例如 focus、risk、adjudication。

    Returns:
        可写入 StageEvaluationResult.metadata 或日志 extra 的轻量输入快照。
    """
    if interval is None or task_case is None or trajectory is None or prompt is None:
        raise ValueError("judge 输入快照参数不能为空")
    stage_goal = resolve_stage_goal(interval, task_case)
    stage_steps = stage_trajectory_steps(interval, trajectory)
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
        "prompt_task_description_excerpt": _excerpt(task_case.task_description),
        "stage_goal_objective_excerpt": _excerpt(stage_goal),
        "stage_step_count": len(stage_steps),
        "first_stage_step_excerpt": _step_excerpt(first_step),
        "last_stage_step_excerpt": _step_excerpt(last_step),
    }
    if prompt_type is not None:
        metadata["prompt_type"] = prompt_type
    return metadata


def judge_result_output_metadata(result: StageEvaluationResult, input_metadata: JsonObject) -> JsonObject:
    """构造 LLM judge 输出快照元数据。

    Args:
        result: 已转换出的阶段评估结果。
        input_metadata: 同一次 LLM 调用的输入快照。

    Returns:
        包含状态、分数、置信度、首条诊断和证据的输出摘要。
    """
    if result is None or input_metadata is None:
        raise ValueError("judge 输出快照参数不能为空")
    return {
        "judge_status": result.status.value,
        "judge_stage_score": result.stage_score,
        "judge_confidence": result.judge_confidence,
        "judge_first_diagnosis": _first_text(result.diagnosis),
        "judge_first_evidence": _first_text(result.evidence),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }


def judge_payload_output_metadata(
    payload: JsonObject,
    stage_score: float,
    input_metadata: JsonObject,
) -> JsonObject:
    """根据已校验 payload 构造中间轮次输出快照。

    Args:
        payload: 单次 LLM judge 返回的 JSON 对象。
        stage_score: 根据维度分数和权重计算出的阶段分数。
        input_metadata: 同一次 LLM 调用的输入快照。

    Returns:
        中间轮次可写入 metadata 的输出摘要。
    """
    if payload is None or input_metadata is None:
        raise ValueError("judge payload 输出快照参数不能为空")
    return {
        "judge_status": str(payload.get("status")),
        "judge_stage_score": stage_score,
        "judge_confidence": payload.get("judge_confidence"),
        "judge_first_diagnosis": _first_text(_string_list(payload.get("diagnosis"))),
        "judge_first_evidence": _first_text(_string_list(payload.get("evidence"))),
        "prompt_context_digest": input_metadata.get("prompt_context_digest"),
    }


def _step_excerpt(step: TrajectoryStep | None) -> str | None:
    if step is None:
        return None
    content = step.content
    if isinstance(content, str) and content.strip():
        return _excerpt(f"step {step.index} {step.actor.value}/{step.event_type.value}: {content}")
    if step.tool_call is not None:
        return _excerpt(f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_call={step.tool_call.name}")
    if step.tool_result is not None:
        return _excerpt(
            f"step {step.index} {step.actor.value}/{step.event_type.value}: tool_result={step.tool_result.success}"
        )
    return _excerpt(f"step {step.index} {step.actor.value}/{step.event_type.value}")


def _first_text(values: list[str]) -> str | None:
    if not values:
        return None
    return _excerpt(values[0])


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _excerpt(value: str, limit: int = 240) -> str:
    if value is None:
        raise ValueError("摘要文本不能为空")
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
