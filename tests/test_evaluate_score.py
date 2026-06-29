from __future__ import annotations

import pytest

from dynsteer.evaluate.score import GeneralScorer
from dynsteer.model import (
    Actor,
    Boundary,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    Operator,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)


def _step(content: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id="s0",
        index=0,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )


def test_general_scorer_scores_common_operator() -> None:
    scorer = GeneralScorer()

    assert scorer.score_operator("任务已完成", Operator.CONTAINS, "完成") == 1.0
    assert scorer.score_operator("任务进行中", Operator.CONTAINS, "完成") == 0.0


def test_general_scorer_rejects_direct_custom_operator_call() -> None:
    scorer = GeneralScorer()

    with pytest.raises(ValueError, match="CUSTOM"):
        scorer.score_operator({"rows": []}, Operator.CUSTOM, {"rows": []})


def test_general_scorer_scores_constraint_from_step() -> None:
    scorer = GeneralScorer()
    constraint = Constraint(
        constraint_id="c1",
        target=ConstraintTarget.STEP,
        selector="$.content",
        operator=Operator.CONTAINS,
        expected="完成",
    )

    result = scorer.score_constraint(constraint, _step("任务已完成"))

    assert result.score == 1.0
    assert result.missing is False
    assert result.actual == "任务已完成"


def test_general_scorer_returns_explicit_unsupported_custom_score() -> None:
    scorer = GeneralScorer()
    constraint = Constraint(
        constraint_id="custom-c1",
        target=ConstraintTarget.STEP,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": []},
        hard=True,
        evaluator_hint="demo",
    )

    result = scorer.score_constraint(constraint, _step("anything"))

    assert result.score == 0.0
    assert result.missing is False
    assert any("Operator.CUSTOM" in item for item in result.evidence)
    assert any("demo" in item for item in result.evidence)


def test_general_scorer_scores_milestone_with_hard_constraint() -> None:
    scorer = GeneralScorer()
    milestone = Milestone(
        milestone_id="m1",
        name="完成",
        description="必须说完成",
        constraints=[
            Constraint(
                constraint_id="c1",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected="完成",
                hard=True,
                threshold=1.0,
            )
        ],
    )
    trajectory = Trajectory(run_id="run-1", task_id="task-1", steps=[_step("任务已完成")])
    boundary = Boundary(boundary_id="b0", step_index=0, snapshot_id=None, reason="agent_message")

    result = scorer.score_milestone(milestone, boundary, trajectory, [])

    assert result.score == 1.0
    assert result.status == StageStatus.PASS
    assert result.hard_constraints_all_pass is True
