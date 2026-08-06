from collections.abc import Callable

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import HarnessAdvanceResult
from dynsteer.metrics import append_execution_timing
from dynsteer.model import Trajectory
from dynsteer.model import TrajectoryStep


def advance_session_trajectory(
    harness: BaseBenchmarkHarness,
    session: object,
    trajectory: Trajectory,
) -> HarnessAdvanceResult:
    """推进 session，并唯一负责同步批次轨迹、快照、状态和指标。"""
    advance = harness.timed_advance_case(session)
    append_execution_timing(trajectory, advance)
    trajectory.extend_snapshots(advance.snapshots)
    trajectory.final_state = harness.final_state_from_session(session)
    trajectory.metrics = harness.metrics_from_session(session)
    return advance


def collect_session_trajectory(
    harness: BaseBenchmarkHarness,
    session: object,
    trajectory: Trajectory,
    on_step: Callable[[TrajectoryStep], tuple[int, bool]],
    on_finish: Callable[[], tuple[int, bool]],
    on_batch: Callable[[int], None] | None = None,
) -> None:
    """推进并收集完整 session；回调只负责每步附加行为和停止决策。"""
    while True:
        advance = advance_session_trajectory(harness, session, trajectory)
        completed = 0
        stopped = False
        for step in advance.steps:
            trajectory.append_step(step)
            count, stopped = on_step(step)
            completed += count
            if stopped:
                break
        if not stopped and not advance.continue_running:
            count, stopped = on_finish()
            completed += count
        if on_batch is not None and completed:
            on_batch(completed)
        if stopped or not advance.continue_running:
            return
