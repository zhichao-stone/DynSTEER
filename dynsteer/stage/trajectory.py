from dynsteer.model import StageInterval, Trajectory, TrajectoryStep

def stage_trajectory_steps(interval: StageInterval, trajectory: Trajectory) -> list[TrajectoryStep]:
    """返回当前阶段 `(start_boundary_step_index, end_step_index]` 内的轨迹步骤。"""
    return trajectory.get_interval(interval.start_boundary_step_index, interval.end_step_index)
