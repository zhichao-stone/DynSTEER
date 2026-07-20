from dynsteer.model import StageInterval, Trajectory, TrajectoryStep


def stage_start_step_index(successor_by_boundary: dict[int, int], boundary_index: int, end_step_index: int) -> int:
    """基于 boundary 后继表返回 `(boundary, end]` 内首个真实 step index。"""
    if successor_by_boundary is None:
        raise ValueError("successor_by_boundary 不能为空")
    candidate = successor_by_boundary.get(boundary_index)
    if candidate is not None and candidate <= end_step_index:
        return candidate
    return end_step_index


def stage_trajectory_steps(interval: StageInterval, trajectory: Trajectory) -> list[TrajectoryStep]:
    """返回当前阶段 `(start_boundary_step_index, end_step_index]` 内的轨迹步骤。"""
    return trajectory.get_interval(interval.start_boundary_step_index, interval.end_step_index)
