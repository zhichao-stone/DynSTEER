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
    """根据轨迹步骤内容判断该步骤是否可作为候选边界原因。

    Args:
        step: 待检查的轨迹步骤。

    Returns:
        候选边界原因；不构成候选边界时返回 None。
    """
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
    """查找给定步骤之前最近的状态快照 ID。

    Args:
        step_index: 候选边界对应的步骤序号。
        snapshots: 轨迹中的状态快照列表。

    Returns:
        最近快照的 snapshot_id；不存在可用快照时返回 None。
    """
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= step_index]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.after_step_index)
    return latest.snapshot_id


def _choose_reason(current: str | None, incoming: str) -> str:
    """按优先级合并同一 step 上的候选边界原因。

    Args:
        current: 当前已记录的原因。
        incoming: 新发现的候选原因。

    Returns:
        优先级更高的候选边界原因。
    """
    if current is None:
        return incoming
    if _REASON_PRIORITY[incoming] > _REASON_PRIORITY[current]:
        return incoming
    return current


def boundary_step(trajectory: Trajectory, boundary: Boundary) -> TrajectoryStep | None:
    """查找候选边界命中的轨迹步骤。

    Args:
        trajectory: Agent 执行轨迹。
        boundary: 候选阶段边界。

    Returns:
        与 boundary.step_index 匹配的轨迹步骤；不存在时返回 None。
    """
    for step in trajectory.steps:
        if step.index == boundary.step_index:
            return step
    return None


def boundary_snapshot(boundary: Boundary, snapshots: list[StateSnapshot]) -> StateSnapshot | None:
    """查找候选边界对应的状态快照。

    Args:
        boundary: 候选阶段边界。
        snapshots: 可供匹配的状态快照列表。

    Returns:
        优先返回 boundary.snapshot_id 指定的快照；未指定时返回不晚于边界步骤的最近快照。
    """
    if boundary.snapshot_id is not None:
        for snapshot in snapshots:
            if snapshot.snapshot_id == boundary.snapshot_id:
                return snapshot
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= boundary.step_index]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.after_step_index)


def generate_candidate_boundaries(trajectory: Trajectory) -> list[Boundary]:
    """根据轨迹事件生成候选阶段边界。

    该函数扫描轨迹步骤，按事件类型和参与者选择可能代表阶段结束的位置，并为每个
    候选位置绑定最近的状态快照。

    Args:
        trajectory: Agent 执行轨迹。

    Returns:
        按 step_index 升序排列的候选边界列表。

    Example:
        >>> trajectory = Trajectory(run_id="r1", task_id="t1", steps=[...])
        >>> boundaries = generate_candidate_boundaries(trajectory)
        >>> boundaries[0].reason
        'agent_message'
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
