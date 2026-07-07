from __future__ import annotations

import json

from dynsteer.judges.prompt import _context_json
from dynsteer.model import (
    Actor,
    Dimension,
    EventType,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def test_context_json_keeps_minimal_interval_contract() -> None:
    interval = StageInterval(
        stage_id="stage:m1",
        milestone_id="m1",
        stage_anchor_milestone_id="m0",
        start_boundary_step_index=1,
        start_step_index=2,
        end_step_index=3,
        status=StageStatus.PASS,
        evidence=["matched"],
    )
    task_case = TaskCase(
        task_id="task",
        task_description="complete task",
        case_id="case",
        stage_goals={"m0->m1": "finish next milestone"},
    )
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            TrajectoryStep(
                step_id="s2",
                index=2,
                actor=Actor.AGENT,
                event_type=EventType.MESSAGE,
                content="done",
            )
        ],
    )

    context = json.loads(
        _context_json(
            interval,
            task_case,
            trajectory,
            {dimension: 1.0 for dimension in Dimension},
        )
    )

    assert context["stage_goal"] == "finish next milestone"
    assert set(context["interval"]) == {"status", "evidence", "milestone_score"}
    assert "stage_id" not in context["interval"]
    assert "milestone_id" not in context["interval"]
    assert "stage_anchor_milestone_id" not in context["interval"]
    assert "start_boundary_step_index" not in context["interval"]
    assert "start_step_index" not in context["interval"]
    assert "end_step_index" not in context["interval"]
    assert context["steps"][0]["index"] == 2
