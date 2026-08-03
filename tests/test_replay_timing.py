import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.experiment.metrics import aggregate_efficiency
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod
from dynsteer.harness.model import HarnessAdvanceResult
from dynsteer.metrics import append_execution_timing, build_replay_timing_metrics, prefix_execution_timing
from dynsteer.model import Actor, EvaluationTerminationState, EventType, StepCost, Trajectory, TrajectoryStep


def _step(index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"step-{index}",
        index=index,
        event_type=EventType.MESSAGE,
        actor=Actor.AGENT,
        cost=StepCost(),
    )


def _trajectory() -> Trajectory:
    trajectory = Trajectory(task_id="task", steps=[])
    steps = [_step(index) for index in range(3)]
    append_execution_timing(
        trajectory,
        HarnessAdvanceResult(steps=steps, snapshots=[], continue_running=False, execution_latency_ms=5),
    )
    for step in steps:
        trajectory.append_step(step)
    return trajectory


def test_batch_timing_is_uniform_and_exact() -> None:
    trajectory = _trajectory()
    values = [step.cost.latency_ms for step in trajectory.steps]
    assert values == [2, 2, 1]
    assert sum(values) == 5
    assert max(values) - min(values) <= 1


def test_prefix_stops_inside_batch() -> None:
    prefix = prefix_execution_timing(_trajectory(), 1)
    assert prefix["latency_ms"] == 4
    assert prefix["timing_available"] is True
    assert prefix["scanned_step_count"] == 2


def test_replay_timing_adds_prefix_to_replay_elapsed() -> None:
    trajectory = _trajectory()
    timing = build_replay_timing_metrics(trajectory, trajectory, EvaluationTerminationState(), 1.25)
    assert timing["default_prefix_execution_seconds"] == 0.005
    assert timing["effective_elapsed_seconds"] == pytest.approx(1.255)


def test_replay_timing_uses_virtual_stop_index() -> None:
    trajectory = _trajectory()
    termination = EvaluationTerminationState(
        should_stop=True,
        termination_detail={"virtual_stop_step_index": 1},
    )
    timing = build_replay_timing_metrics(trajectory, trajectory, termination, 1.0)
    assert timing["default_prefix_execution_seconds"] == 0.004
    assert timing["source_full_execution_seconds"] == 0.005


def test_legacy_trajectory_is_unavailable_not_zero() -> None:
    trajectory = Trajectory(task_id="legacy", steps=[_step(0)])
    prefix = prefix_execution_timing(trajectory, None)
    assert prefix["timing_available"] is False
    timing = build_replay_timing_metrics(trajectory, trajectory, EvaluationTerminationState(), 1.0)
    assert timing["effective_elapsed_seconds"] is None
    assert timing["default_prefix_execution_seconds"] is None


def test_timed_advance_preserves_exception() -> None:
    class FailingHarness(BaseBenchmarkHarness):
        benchmark = "test"

        def list_cases(self, config):
            return []

        def start_case(self, config, case_id, raw_output_dir):
            return object()

        def advance_case(self, session):
            raise RuntimeError("boom")

        def case_finished(self, session):
            return True

    with pytest.raises(RuntimeError, match="boom"):
        FailingHarness().timed_advance_case(object())


def test_efficiency_excludes_unavailable_timing_from_new_averages() -> None:
    results = [
        ExperimentCaseResult(
            experiment_id="e", benchmark="b", case_id="1", model_id="m", repeat_index=0,
            method=ExperimentMethod.DYNSTEER_REPLAY,
            runtime_metrics={"elapsed_seconds": 2.0, "timing_available": True,
                             "default_prefix_execution_seconds": 1.0, "effective_elapsed_seconds": 3.0},
        ),
        ExperimentCaseResult(
            experiment_id="e", benchmark="b", case_id="2", model_id="m", repeat_index=0,
            method=ExperimentMethod.DYNSTEER_REPLAY,
            runtime_metrics={"elapsed_seconds": 4.0, "timing_available": False},
        ),
    ]
    metrics = aggregate_efficiency(results)
    assert metrics["average_elapsed_seconds"] == 3.0
    assert metrics["average_effective_elapsed_seconds"] == 3.0
    assert metrics["effective_timing_available_case_count"] == 1
