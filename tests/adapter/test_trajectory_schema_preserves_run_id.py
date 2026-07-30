from __future__ import annotations

from dynsteer.adapter.loader import load_trajectory
from dynsteer.harness.outputs import trajectory_to_json
from dynsteer.model import Trajectory


def test_trajectory_to_json_preserves_run_id() -> None:
    trajectory = Trajectory(
        task_id="task-1",
        steps=[],
        snapshots=[],
        raw={"run_id": "legacy", "note": "keep"},
    )

    payload = trajectory_to_json(trajectory)

    assert payload["task_id"] == "task-1"
    assert payload["note"] == "keep"
    assert payload["run_id"] == "legacy"


def test_load_trajectory_accepts_payload_without_run_id() -> None:
    trajectory = load_trajectory(
        {
            "task_id": "task-1",
            "steps": [
                {
                    "step_id": "step-1",
                    "index": 0,
                    "actor": "evaluator",
                    "event_type": "message",
                    "content": "hello",
                }
            ],
            "snapshots": [
                {
                    "snapshot_id": "snapshot-1",
                    "after_step_id": "step-1",
                    "after_step_index": 0,
                    "namespaces": {},
                }
            ],
            "final_state": {"value": 1},
            "metrics": {"step_count": 1},
        }
    )

    assert trajectory.task_id == "task-1"
    assert trajectory.raw == {}
    assert trajectory.steps[0].step_id == "step-1"
