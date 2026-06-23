import pytest

from dynsteer.adapter.generic import load_milestone_graph, load_task_case, load_trajectory
from dynsteer.model import Actor, EventType, TaskType


def test_load_task_case_minimal() -> None:
    task = load_task_case({"task_id": "t", "task_description": "demo"})

    assert task.task_id == "t"


def test_load_task_case_task_types() -> None:
    task = load_task_case(
        {
            "task_id": "t",
            "task_description": "demo",
            "task_types": ["stateful_tool_task"],
        }
    )

    assert task.task_types == [TaskType.STATEFUL_TOOL]


def test_load_task_case_missing_required_field() -> None:
    with pytest.raises(ValueError):
        load_task_case({"task_id": "t"})


def test_load_trajectory_unknown_fields_enter_raw() -> None:
    trajectory = load_trajectory(
        {
            "run_id": "r",
            "task_id": "t",
            "steps": [
                {
                    "step_id": "s1",
                    "index": 0,
                    "actor": "agent",
                    "event_type": "final",
                    "content": "done",
                    "extra": "kept",
                }
            ],
        }
    )

    step = trajectory.steps[0]
    assert step.actor == Actor.AGENT
    assert step.event_type == EventType.FINAL
    assert step.raw["extra"] == "kept"


def test_load_milestone_graph_edges_and_nodes() -> None:
    graph = load_milestone_graph(
        {
            "nodes": [
                {"milestone_id": "a", "name": "a", "description": "a", "constraints": []},
                {"milestone_id": "b", "name": "b", "description": "b", "constraints": []},
            ],
            "edges": [["a", "b"]],
        }
    )

    assert graph.edges == [("a", "b")]


def test_load_milestone_graph_with_minefield_and_constraint() -> None:
    graph = load_milestone_graph(
        {
            "nodes": [
                {
                    "milestone_id": "m",
                    "name": "m",
                    "description": "m",
                    "constraints": [
                        {
                            "constraint_id": "c",
                            "target": "metric",
                            "selector": "$.ok",
                            "operator": "equals",
                            "expected": True,
                            "hard": True,
                            "weight": 2,
                        }
                    ],
                }
            ],
            "minefields": [
                {
                    "minefield_id": "mf",
                    "name": "mf",
                    "description": "mf",
                    "severity": "fatal",
                    "constraints": [
                        {
                            "constraint_id": "mc",
                            "target": "metric",
                            "selector": "$.unsafe",
                            "operator": "equals",
                            "expected": True,
                        }
                    ],
                    "penalty": {"mode": "fixed", "value": 1},
                }
            ],
            "default_thresholds": {"pass": 0.8},
        }
    )

    assert graph.nodes[0].constraints[0].hard is True
    assert graph.minefields[0].penalty.value == 1.0


def test_load_trajectory_with_tool_and_snapshot() -> None:
    trajectory = load_trajectory(
        {
            "run_id": "r",
            "task_id": "t",
            "steps": [
                {
                    "step_id": "s1",
                    "index": 0,
                    "actor": "agent",
                    "event_type": "tool_call",
                    "tool_call": {"name": "lookup", "arguments": {"q": "x"}},
                    "tool_result": {"success": True, "content": "ok"},
                    "cost": {"tokens": 10, "latency_ms": 20},
                }
            ],
            "snapshots": [
                {
                    "snapshot_id": "snap",
                    "after_step_id": "s1",
                    "after_step_index": 0,
                    "namespaces": {"default": {"ok": True}},
                    "extra": "kept",
                }
            ],
            "final_state": {"ok": True},
            "metrics": {"score": 1},
        }
    )

    assert trajectory.steps[0].tool_call is not None
    assert trajectory.steps[0].cost.tokens == 10
    assert trajectory.snapshots[0].raw["extra"] == "kept"
