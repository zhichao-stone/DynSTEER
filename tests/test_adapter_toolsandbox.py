from dynsteer.adapter.toolsandbox import load_toolsandbox_experiment
from dynsteer.model import Actor, EventType


def test_toolsandbox_loader_delegates_generic_shape() -> None:
    task, trajectory = load_toolsandbox_experiment(
        {
            "task": {"task_id": "t", "task_description": "demo"},
            "trajectory": {
                "run_id": "r",
                "task_id": "t",
                "steps": [
                    {"step_id": "s1", "index": 0, "actor": "agent", "event_type": "final"}
                ],
            },
            "milestone_graph": {"nodes": [], "edges": [], "minefields": []},
        }
    )

    assert task.milestone_graph is not None
    assert trajectory.steps[0].event_type == EventType.FINAL


def test_toolsandbox_loader_builds_trajectory_from_messages() -> None:
    task, trajectory = load_toolsandbox_experiment(
        {
            "id": "case-1",
            "instruction": "complete task",
            "conversation": [
                {"role": "user", "content": "start"},
                {"role": "assistant", "content": "calling", "tool_call": {"name": "lookup", "arguments": {}}},
                {"role": "tool", "tool_result": {"success": True, "content": {"ok": True}}},
                {"role": "assistant", "content": "done", "final": True},
            ],
        }
    )

    assert task.task_id == "case-1"
    assert trajectory.steps[1].actor == Actor.AGENT
    assert trajectory.steps[2].event_type == EventType.TOOL_RESULT
    assert trajectory.steps[3].event_type == EventType.FINAL

