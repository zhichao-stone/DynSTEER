from dynsteer.language import TaskLanguage
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    Operator,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.prompt.judge import build_judge_prompt


def _contact_constraint(expected_rows: list[dict[str, object]]) -> Constraint:
    return Constraint(
        constraint_id="m0_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": expected_rows, "columns": list(expected_rows[0].keys()) if expected_rows else []},
        namespace="CONTACT",
        threshold=1.0,
        hard=True,
        stage_goal_semantics={"kind": "set_state", "namespace": "CONTACT", "expected": expected_rows},
        metadata={"toolsandbox": {"database_namespace": "CONTACT"}},
    )


def _build_state_task_case() -> tuple[TaskCase, StageInterval, Trajectory]:
    expected_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293847563",
            "relationship": "enemy",
            "is_self": False,
        }
    ]
    constraint = _contact_constraint(expected_rows)
    milestone = Milestone(
        milestone_id="m0",
        name="ToolSandbox milestone 0",
        description="ToolSandbox milestone 0",
        constraints=[constraint],
        metadata={"toolsandbox": {"milestone_index": 0}},
        stage_anchor_predecessor_id="__start__",
    )
    milestone_graph = MilestoneGraph(nodes=[milestone], edges=[], minefields=[], default_thresholds={})
    task_case = TaskCase(
        task_id="toolsandbox::test",
        task_description="Make the contact state consistent",
        case_id="test_case",
        milestone_graph=milestone_graph,
        stage_goals={"__start__->m0": "Complete milestone m0."},
    )
    actual_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293847563",
            "relationship": "enemy",
            "is_self": False,
        }
    ]
    score = MilestoneScore(
        milestone_id="m0",
        boundary_id="b0",
        score=1.0,
        status=StageStatus.PASS,
        evidence=["ToolSandbox custom constraint m0_c0 score 1.000 (update_similarity)"],
        constraint_scores=[
            ConstraintScore(
                constraint_id="m0_c0",
                score=1.0,
                missing=False,
                evidence=["ToolSandbox custom constraint m0_c0 score 1.000 (update_similarity)"],
                actual=actual_rows,
            )
        ],
    )
    interval = StageInterval(
        stage_id="__start__->m0",
        milestone_id="m0",
        stage_anchor_milestone_id="__start__",
        start_boundary_step_index=0,
        start_step_index=1,
        end_step_index=1,
        status=StageStatus.PASS,
        milestone_score=score,
    )
    trajectory = Trajectory(
        run_id="run-1",
        task_id="toolsandbox::test",
        steps=[
            TrajectoryStep(
                step_id="step-1",
                index=1,
                actor=Actor.AGENT,
                recipient=Actor.USER,
                event_type=EventType.MESSAGE,
                content="done",
            )
        ],
    )
    return task_case, interval, trajectory


def test_standard_prompt_keeps_lightweight_constraint_checks_and_state_excerpt() -> None:
    task_case, interval, trajectory = _build_state_task_case()
    prompt = build_judge_prompt("standard", interval, task_case, trajectory, language=TaskLanguage.ENGLISH)

    assert "constraint_checks" in prompt
    assert "actual_summary" not in prompt
    assert "expected_summary" not in prompt
    assert "structured_pass" not in prompt
    assert "Do not infer that a row is absent" in prompt
    assert "matched_expected_rows=1/1" in prompt
    assert "actual_excerpt" in prompt
