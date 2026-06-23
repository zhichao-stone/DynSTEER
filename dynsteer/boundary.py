from __future__ import annotations

from dynsteer.model import Actor, Boundary, EventType, StateSnapshot, Trajectory, TrajectoryStep

_REASON_PRIORITY: dict[str, int] = {
    "last_step": 0,
    "user_reply": 1,
    "agent_message": 2,
    "tool_result": 3,
    "state_update": 4,
    "final": 5,
    "error": 6,
}


def _step_reason(step: TrajectoryStep) -> str | None:
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
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= step_index]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.after_step_index)
    return latest.snapshot_id


def _choose_reason(current: str | None, incoming: str) -> str:
    if current is None:
        return incoming
    if _REASON_PRIORITY[incoming] > _REASON_PRIORITY[current]:
        return incoming
    return current


def generate_candidate_boundaries(trajectory: Trajectory) -> list[Boundary]:
    """根据轨迹事件生成候选阶段边界。

    Args:
        trajectory: Agent 执行轨迹。

    Returns:
        按 step_index 升序排列的候选边界列表。
    """
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    if len(trajectory.steps) == 0:
        return []

    reasons_by_step: dict[int, str] = {}
    step_by_index: dict[int, TrajectoryStep] = {}
    for step in trajectory.steps:
        step_by_index[step.index] = step
        reason = _step_reason(step)
        if reason is not None:
            reasons_by_step[step.index] = _choose_reason(reasons_by_step.get(step.index), reason)

    last_step = max(trajectory.steps, key=lambda item: item.index)
    reasons_by_step[last_step.index] = _choose_reason(reasons_by_step.get(last_step.index), "last_step")

    boundaries: list[Boundary] = []
    for step_index in sorted(reasons_by_step):
        step = step_by_index.get(step_index)
        if step is None:
            continue
        boundary_id = f"b{len(boundaries)}"
        boundaries.append(
            Boundary(
                boundary_id=boundary_id,
                step_index=step_index,
                snapshot_id=_latest_snapshot_id(step_index, trajectory.snapshots),
                reason=reasons_by_step[step_index],
                step_id=step.step_id,
            )
        )
    return boundaries
