from dynsteer.evaluate import compute_uncertainty, evaluate_trajectory, select_evaluation_level
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    Dimension,
    EventType,
    EvaluationLevel,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def test_uncertainty_is_bounded() -> None:
    value = compute_uncertainty(
        top1_score=0.8,
        top2_score=0.7,
        missing_ratio=0.2,
        stage_score=0.61,
        evidence_conflict=True,
        judge_uncertainty=0.3,
    )

    assert 0.0 <= value <= 1.0


def test_empty_milestone_graph_returns_none_coverage() -> None:
    task = TaskCase(task_id="t", task_description="demo", milestone_graph=MilestoneGraph())
    trajectory = Trajectory(run_id="r", task_id="t", steps=[])

    report = evaluate_trajectory(task, trajectory)

    assert report.milestone_coverage == "none"
    assert report.stage_reports == []


def test_select_evaluation_level_cheap_pass() -> None:
    result = StageEvaluationResult(
        stage_id="s",
        milestone_id="m",
        evaluator_level=EvaluationLevel.CHEAP,
        status=StageStatus.PASS,
        stage_score=0.95,
        uncertainty=0.05,
        dimension_scores={dimension: 0.9 for dimension in Dimension},
        hard_constraints_all_pass=True,
        required_fields_missing_ratio=0.0,
        minefield_score=0.0,
        judge_confidence=0.9,
    )

    assert select_evaluation_level(result).level == EvaluationLevel.CHEAP


def test_select_evaluation_level_expensive_for_low_confidence_standard() -> None:
    result = StageEvaluationResult(
        stage_id="s",
        milestone_id="m",
        evaluator_level=EvaluationLevel.STANDARD,
        status=StageStatus.WARN,
        stage_score=0.62,
        uncertainty=0.5,
        dimension_scores={dimension: 0.5 for dimension in Dimension},
        hard_constraints_all_pass=True,
        required_fields_missing_ratio=0.1,
        minefield_score=0.3,
        judge_confidence=0.3,
    )

    decision = select_evaluation_level(result)

    assert decision.level == EvaluationLevel.EXPENSIVE


def test_fatal_minefield_triggers_failure_report() -> None:
    minefield = Minefield(
        minefield_id="mf",
        name="fatal",
        description="fatal",
        severity="fatal",
        constraints=[
            Constraint(
                constraint_id="mf-c",
                target=ConstraintTarget.METRIC,
                selector="$.unsafe",
                operator=Operator.EQUALS,
                expected=True,
            )
        ],
        penalty=MinefieldPenalty(mode="fixed", value=1.0),
    )
    task = TaskCase(
        task_id="t",
        task_description="demo",
        milestone_graph=MilestoneGraph(minefields=[minefield]),
    )
    trajectory = Trajectory(run_id="r", task_id="t", steps=[], metrics={"unsafe": True})

    report = evaluate_trajectory(task, trajectory)

    assert report.first_failure_stage_id == "minefield:mf"
    assert report.overall_score == 0.0


def test_evaluate_trajectory_with_single_milestone() -> None:
    milestone = Milestone(
        milestone_id="m1",
        name="final",
        description="final answer",
        constraints=[
            Constraint(
                constraint_id="c1",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected="Done",
            )
        ],
    )
    task = TaskCase(
        task_id="t",
        task_description="demo",
        milestone_graph=MilestoneGraph(nodes=[milestone]),
    )
    trajectory = Trajectory(
        run_id="r",
        task_id="t",
        steps=[
            TrajectoryStep("s0", 0, Actor.USER, EventType.MESSAGE, content="please"),
            TrajectoryStep("s1", 1, Actor.AGENT, EventType.FINAL, content="Done."),
        ],
    )

    report = evaluate_trajectory(task, trajectory)

    assert report.milestone_coverage == "full"
    assert len(report.stage_reports) == 1
    assert report.stage_reports[0].stage_score == 1.0
