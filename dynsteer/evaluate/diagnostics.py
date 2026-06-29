from __future__ import annotations

from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    ConstraintScore,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    Trajectory,
    TrajectoryStep,
)


def trajectory_step_to_dict(step: TrajectoryStep) -> JsonObject:
    """将单个轨迹步骤转换为 raw_summary 可写入的诊断字典。

    Args:
        step: Agent 轨迹中的单步事件。

    Returns:
        JSON 可序列化的轨迹步骤诊断信息。
    """
    if step is None:
        raise ValueError("step 不能为空")
    tool_call = None
    if step.tool_call is not None:
        tool_call = {
            "name": step.tool_call.name,
            "arguments": dict(step.tool_call.arguments),
        }
    tool_result = None
    if step.tool_result is not None:
        tool_result = {
            "success": step.tool_result.success,
            "content": step.tool_result.content,
            "exception": step.tool_result.exception,
        }
    return {
        "step_id": step.step_id,
        "index": step.index,
        "actor": step.actor.value,
        "event_type": step.event_type.value,
        "timestamp": step.timestamp,
        "content": step.content,
        "tool_call": tool_call,
        "tool_result": tool_result,
        "state_delta_refs": list(step.state_delta_refs),
        "cost": {
            "tokens": step.cost.tokens,
            "latency_ms": step.cost.latency_ms,
        },
        "raw": dict(step.raw),
    }


def build_stage_trace(
    trajectory: Trajectory,
    start_step_index: int,
    end_step_index: int,
) -> JsonObject:
    """构造阶段覆盖的轨迹步骤诊断信息。

    Args:
        trajectory: 当前运行期已采集的 Agent 轨迹。
        start_step_index: 阶段起始 step index。
        end_step_index: 阶段结束 step index。

    Returns:
        阶段闭区间内的轨迹步骤列表和范围摘要。
    """
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    if start_step_index < 0 or end_step_index < 0 or end_step_index < start_step_index:
        raise ValueError("阶段 step index 范围非法")
    steps = [
        trajectory_step_to_dict(step)
        for step in trajectory.steps
        if start_step_index <= step.index <= end_step_index
    ]
    return {
        "start_step_index": start_step_index,
        "end_step_index": end_step_index,
        "step_count": len(steps),
        "steps": steps,
    }


def constraint_score_to_dict(score: ConstraintScore) -> JsonObject:
    """将单条约束评分转换为诊断字典。

    Args:
        score: 单条约束评分结果。

    Returns:
        JSON 可序列化的约束评分详情。
    """
    if score is None:
        raise ValueError("score 不能为空")
    return {
        "constraint_id": score.constraint_id,
        "score": score.score,
        "missing": score.missing,
        "evidence": list(score.evidence),
        "actual": score.actual,
    }


def milestone_score_to_dict(score: MilestoneScore) -> JsonObject:
    """将 milestone 评分转换为诊断字典。

    Args:
        score: milestone 评分结果。

    Returns:
        JSON 可序列化的 milestone 匹配评分详情。
    """
    if score is None:
        raise ValueError("score 不能为空")
    return {
        "milestone_id": score.milestone_id,
        "boundary_id": score.boundary_id,
        "score": score.score,
        "status": score.status.value,
        "evidence": list(score.evidence),
        "missing_ratio": score.missing_ratio,
        "hard_constraints_all_pass": score.hard_constraints_all_pass,
        "constraint_scores": [constraint_score_to_dict(item) for item in score.constraint_scores],
    }


def milestone_summary_to_dict(milestone: Milestone) -> JsonObject:
    """将 milestone 定义转换为简要诊断字典。

    Args:
        milestone: 当前命中的 milestone。

    Returns:
        milestone 的基础定义摘要。
    """
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "required": milestone.required,
        "pass_threshold": milestone.pass_threshold,
        "constraint_count": len(milestone.constraints),
        "metadata": dict(milestone.metadata),
    }


def boundary_to_dict(boundary: Boundary) -> JsonObject:
    """将候选边界转换为诊断字典。

    Args:
        boundary: 触发 milestone 的候选边界。

    Returns:
        JSON 可序列化的边界详情。
    """
    if boundary is None:
        raise ValueError("boundary 不能为空")
    return {
        "boundary_id": boundary.boundary_id,
        "step_index": boundary.step_index,
        "step_id": boundary.step_id,
        "snapshot_id": boundary.snapshot_id,
        "reason": boundary.reason,
    }


def build_milestone_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    ready_milestone_ids_before_match: list[str],
) -> JsonObject:
    """构造运行期 milestone checkpoint 的匹配诊断详情。

    Args:
        graph: 当前任务的 milestone DAG。
        matched: 命中前已经结算的 milestone 映射。
        milestone: 当前命中的 milestone。
        boundary: 当前命中的候选边界。
        milestone_score: 当前边界上的 milestone 评分。
        ready_milestone_ids_before_match: 命中前可被匹配的 milestone ID 列表。

    Returns:
        可写入 settlement metadata 的 milestone 匹配诊断详情。
    """
    if (
        graph is None
        or matched is None
        or milestone is None
        or boundary is None
        or milestone_score is None
        or ready_milestone_ids_before_match is None
    ):
        raise ValueError("milestone 匹配诊断参数不能为空")
    predecessor_milestone_ids = [source for source, target in graph.edges if target == milestone.milestone_id]
    return {
        "mode": "runtime_checkpoint",
        "matched": True,
        "milestone": milestone_summary_to_dict(milestone),
        "boundary": boundary_to_dict(boundary),
        "score": milestone_score_to_dict(milestone_score),
        "ready_milestone_ids_before_match": list(ready_milestone_ids_before_match),
        "matched_milestone_ids_before_match": sorted(matched),
        "predecessor_milestone_ids": predecessor_milestone_ids,
    }


def build_finish_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> JsonObject:
    """构造 finish 阶段的 milestone 匹配收尾诊断详情。

    Args:
        graph: 当前任务的 milestone DAG。
        matched: 已经结算的 milestone 映射。

    Returns:
        finish 阶段的已命中和未命中 milestone 摘要。
    """
    if graph is None or matched is None:
        raise ValueError("finish 匹配诊断参数不能为空")
    matched_ids = set(matched)
    required_ids = {node.milestone_id for node in graph.nodes if node.required}
    optional_ids = {node.milestone_id for node in graph.nodes if not node.required}
    return {
        "mode": "runtime_finish",
        "matched": False,
        "matched_milestone_ids": sorted(matched_ids),
        "pending_required_milestone_ids": sorted(required_ids - matched_ids),
        "pending_optional_milestone_ids": sorted(optional_ids - matched_ids),
        "total_milestone_count": len(graph.nodes),
    }
