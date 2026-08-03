import pytest

from dynsteer.adapter.loader import load_trajectory
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.adapter.toolsandbox.utils.trajectory import trajectory_from_sandbox_rows
from dynsteer.harness.outputs import trajectory_to_json
from dynsteer.model import AgentStepTracker, Trajectory


def _rows(result_order: tuple[str, str] = ("A", "B")) -> list[dict[str, object]]:
    return [
        {
            "sandbox_message_index": 16,
            "sender": "AGENT",
            "recipient": "EXECUTION_ENVIRONMENT",
            "content": "first(value=1)",
            "openai_tool_call_id": "A",
            "openai_function_name": "first",
        },
        {
            "sandbox_message_index": 17,
            "sender": "AGENT",
            "recipient": "EXECUTION_ENVIRONMENT",
            "content": "second(value=2)",
            "openai_tool_call_id": "B",
            "openai_function_name": "second",
        },
        {
            "sandbox_message_index": 18,
            "sender": "EXECUTION_ENVIRONMENT",
            "recipient": "AGENT",
            "content": f"result-{result_order[0]}",
            "openai_tool_call_id": result_order[0],
        },
        {
            "sandbox_message_index": 19,
            "sender": "EXECUTION_ENVIRONMENT",
            "recipient": "AGENT",
            "content": f"result-{result_order[1]}",
            "openai_tool_call_id": result_order[1],
        },
    ]


def _closures(trajectory: Trajectory) -> list[tuple[str, str]]:
    tracker = AgentStepTracker()
    closures = []
    for step in trajectory.steps:
        closure = tracker.ingest(step)
        if closure is not None:
            closures.append((closure.steps[0].step_id, closure.steps[-1].step_id))
    assert tracker.completed_count == 2
    return closures


@pytest.mark.parametrize(
    ("result_order", "expected"),
    [
        (("A", "B"), [("s16", "s18"), ("s17", "s19")]),
        (("B", "A"), [("s17", "s18"), ("s16", "s19")]),
    ],
)
def test_toolsandbox_parallel_rows_preserve_ids_and_close_independently(
    result_order: tuple[str, str],
    expected: list[tuple[str, str]],
) -> None:
    step_dicts = sandbox_rows_to_step_dicts(_rows(result_order))

    assert [step["openai_tool_call_id"] for step in step_dicts] == ["A", "B", *result_order]
    trajectory = trajectory_from_sandbox_rows("task", step_dicts)
    assert [step.raw["openai_tool_call_id"] for step in trajectory.steps] == ["A", "B", *result_order]
    assert _closures(trajectory) == expected


def test_serializer_loader_round_trip_preserves_parallel_closures() -> None:
    trajectory = trajectory_from_sandbox_rows("task", sandbox_rows_to_step_dicts(_rows()))

    replay_trajectory = load_trajectory(trajectory_to_json(trajectory))

    assert [step.raw["openai_tool_call_id"] for step in replay_trajectory.steps] == ["A", "B", "A", "B"]
    assert _closures(replay_trajectory) == [("s16", "s18"), ("s17", "s19")]
