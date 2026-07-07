from __future__ import annotations

import pytest

from dynsteer.model import Actor, EventType, Trajectory, TrajectoryStep


def _step(index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=f"step {index}",
    )


def test_trajectory_builds_step_index_context_from_initial_steps() -> None:
    trajectory = Trajectory(run_id="run", task_id="task", steps=[_step(10), _step(12), _step(20)])

    assert trajectory.first_step_index == 10
    assert trajectory.successor_by_boundary == {9: 10, 10: 12, 12: 20}


def test_empty_trajectory_has_empty_step_index_context() -> None:
    trajectory = Trajectory(run_id="run", task_id="task", steps=[])

    assert trajectory.first_step_index == 0
    assert trajectory.successor_by_boundary == {}


def test_append_step_updates_step_index_context_incrementally() -> None:
    trajectory = Trajectory(run_id="run", task_id="task", steps=[_step(10), _step(20)])

    trajectory.append_step(_step(30))

    assert [step.index for step in trajectory.steps] == [10, 20, 30]
    assert trajectory.first_step_index == 10
    assert trajectory.successor_by_boundary == {9: 10, 10: 20, 20: 30}


def test_append_step_rejects_non_increasing_indexes() -> None:
    trajectory = Trajectory(run_id="run", task_id="task", steps=[_step(10), _step(20)])

    with pytest.raises(ValueError, match="递增"):
        trajectory.append_step(_step(20))
