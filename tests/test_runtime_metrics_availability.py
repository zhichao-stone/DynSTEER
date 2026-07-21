from dynsteer.metrics import build_runtime_metrics
from dynsteer.model import Actor, EventType, StepCost, Trajectory, TrajectoryStep


def test_runtime_metrics_marks_unavailable_trajectory_cost() -> None:
    """覆盖没有 step cost 时 0 值只是不可用兜底。"""
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[TrajectoryStep(step_id="s1", index=1, actor=Actor.AGENT, event_type=EventType.MESSAGE)],
    )

    metrics = build_runtime_metrics(
        started_monotonic=1.0,
        finished_monotonic=2.0,
        started_at="start",
        finished_at="finish",
        trajectory=trajectory,
        llm_calls=[],
        agent_step_count=1,
    )

    assert metrics["trajectory_total_tokens"] == 0
    assert metrics["trajectory_total_latency_ms"] == 0
    assert metrics["trajectory_cost_available"] is False
    assert metrics["trajectory_latency_available"] is False


def test_runtime_metrics_marks_available_trajectory_cost() -> None:
    """覆盖存在任一 step cost 时可用性字段为 true。"""
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            TrajectoryStep(
                step_id="s1",
                index=1,
                actor=Actor.AGENT,
                event_type=EventType.MESSAGE,
                cost=StepCost(tokens=7, latency_ms=42),
            )
        ],
    )

    metrics = build_runtime_metrics(
        started_monotonic=1.0,
        finished_monotonic=2.0,
        started_at="start",
        finished_at="finish",
        trajectory=trajectory,
        llm_calls=[],
        agent_step_count=1,
    )

    assert metrics["trajectory_total_tokens"] == 7
    assert metrics["trajectory_total_latency_ms"] == 42
    assert metrics["trajectory_cost_available"] is True
    assert metrics["trajectory_latency_available"] is True
