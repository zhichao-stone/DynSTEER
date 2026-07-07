from __future__ import annotations

from dynsteer.graph import START_NODE_ID
from dynsteer.model import (
    MilestoneGraph,
    MilestoneMapping,
    StageInterval,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)


def build_stage_intervals(
    graph: MilestoneGraph,
    mapping: MilestoneMapping,
    trajectory: Trajectory,
) -> list[StageInterval]:
    """根据 milestone 匹配结果构造阶段区间。

    Args:
        graph: 已增强的 milestone DAG。
        mapping: milestone 到边界的匹配结果。
        trajectory: 待评估轨迹。

    Returns:
        使用 `(start_boundary_step_index, end_step_index]` 语义的阶段区间列表。
    """
    if graph is None or mapping is None or trajectory is None:
        raise ValueError("build_stage_intervals 入参不能为空")

    first_index = trajectory.first_step_index
    successor_by_boundary = trajectory.successor_by_boundary
    intervals: list[StageInterval] = []
    end_by_milestone: dict[str, int] = {}

    for milestone in graph.nodes:
        milestone_id = milestone.milestone_id
        item = mapping.assignments.get(milestone_id)
        if item is None:
            if milestone_id in mapping.missing_required:
                intervals.append(
                    StageInterval(
                        stage_id=f"stage:{milestone_id}",
                        milestone_id=milestone_id,
                        stage_anchor_milestone_id=None,
                        start_boundary_step_index=-1,
                        start_step_index=-1,
                        end_step_index=-1,
                        status=StageStatus.MISSING,
                        evidence=[f"milestone {milestone_id} 缺失"],
                    )
                )
            continue

        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone_id}")
        if anchor_id == START_NODE_ID:
            boundary_index = first_index - 1
        elif anchor_id in end_by_milestone:
            boundary_index = end_by_milestone[anchor_id]
        else:
            raise ValueError(f"stage anchor 尚未匹配: milestone={milestone_id}, anchor={anchor_id}")

        start_step_index = stage_start_step_index(
            successor_by_boundary,
            boundary_index,
            item.boundary_step_index,
        )
        end_by_milestone[milestone_id] = item.boundary_step_index
        intervals.append(
            StageInterval(
                stage_id=f"stage:{milestone_id}",
                milestone_id=milestone_id,
                stage_anchor_milestone_id=anchor_id,
                start_boundary_step_index=boundary_index,
                start_step_index=start_step_index,
                end_step_index=item.boundary_step_index,
                status=item.score.status,
                milestone_score=item.score,
                evidence=list(item.score.evidence),
            )
        )

    return intervals


def stage_start_step_index(
    successor_by_boundary: dict[int, int],
    boundary_index: int,
    end_step_index: int,
) -> int:
    """基于 boundary 后继表返回 `(boundary, end]` 内首个真实 step index。"""
    if successor_by_boundary is None:
        raise ValueError("successor_by_boundary 不能为空")
    candidate = successor_by_boundary.get(boundary_index)
    if candidate is not None and candidate <= end_step_index:
        return candidate
    return end_step_index


def stage_trajectory_steps(interval: StageInterval, trajectory: Trajectory) -> list[TrajectoryStep]:
    """返回当前阶段 `(start_boundary_step_index, end_step_index]` 内的轨迹步骤。"""
    if interval is None or trajectory is None:
        raise ValueError("阶段轨迹参数不能为空")
    
    return trajectory.get_interval(interval.start_boundary_step_index, interval.end_step_index)
