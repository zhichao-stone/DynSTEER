from __future__ import annotations

from dynsteer.model import Actor, Boundary, EventType, StateSnapshot, Trajectory, TrajectoryStep


def _step_reason(step: TrajectoryStep) -> str | None:
    """根据轨迹步骤内容判断该步骤是否可作为候选边界原因。"""
    if step.event_type == EventType.ERROR:
        return "error"
    if step.event_type == EventType.FINAL:
        return "final"
    if step.event_type in {EventType.STATE_UPDATE, EventType.ARTIFACT_UPDATE}:
        return "state_update"
    if step.event_type == EventType.TOOL_RESULT:
        return "tool_result"
    if step.actor == Actor.AGENT and step.event_type == EventType.MESSAGE:
        return "agent_message"
    if step.actor == Actor.USER and step.event_type == EventType.MESSAGE:
        return "user_reply"
    return None


def _latest_snapshot_id(step_index: int, snapshots: list[StateSnapshot]) -> str | None:
    """查找给定步骤之前最近的状态快照 ID。"""
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= step_index]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.after_step_index)
    return latest.snapshot_id


def boundary_step(trajectory: Trajectory, boundary: Boundary) -> TrajectoryStep | None:
    """查找候选边界命中的轨迹步骤。"""
    for step in trajectory.steps:
        if step.index == boundary.step_index:
            return step
    return None


def boundary_snapshot(boundary: Boundary, snapshots: list[StateSnapshot]) -> StateSnapshot | None:
    """查找候选边界对应的状态快照。"""
    if boundary.snapshot_id is not None:
        for snapshot in snapshots:
            if snapshot.snapshot_id == boundary.snapshot_id:
                return snapshot
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= boundary.step_index]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.after_step_index)


def candidate_boundary_for_current_step(
    trajectory: Trajectory,
    step: TrajectoryStep,
) -> Boundary:
    """基于当前新增 step 构造运行期唯一候选边界。"""
    if trajectory is None or step is None:
        raise ValueError("trajectory 和 step 不能为空")
    return Boundary(
        boundary_id=f"runtime:b{step.index}",
        step_index=step.index,
        snapshot_id=_latest_snapshot_id(step.index, trajectory.snapshots),
        reason=_step_reason(step) or "last_step",
        step_id=step.step_id,
    )

