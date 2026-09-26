from dynsteer.model import StageInterval, Trajectory, TrajectoryStep

def stage_trajectory_steps(interval: StageInterval, trajectory: Trajectory) -> list[TrajectoryStep]:
    """Go back to the current stage`(start_boundary_step_index, end_step_index]`Intra-trajectory steps."""
    return trajectory.get_interval(interval.start_boundary_step_index, interval.end_step_index)
