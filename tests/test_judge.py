from dynsteer.judge import LocalJudge
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    MilestoneScore,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


def test_local_judge_scores_pass_interval() -> None:
    judge = LocalJudge()
    interval = StageInterval(
        stage_id="stage:a",
        milestone_id="a",
        start_step_index=0,
        end_step_index=1,
        status=StageStatus.PASS,
        milestone_score=MilestoneScore("a", "b1", 0.9, StageStatus.PASS, missing_ratio=0.0),
    )

    result = judge.evaluate_stage(
        interval,
        TaskCase("t", "demo"),
        Trajectory("r", "t", []),
        EvaluationLevel.CHEAP,
        {dimension: 1 / len(Dimension) for dimension in Dimension},
    )

    assert result.stage_score == 0.9
    assert result.judge_confidence > 0.7


def test_local_judge_diagnoses_missing_interval() -> None:
    judge = LocalJudge()
    interval = StageInterval(
        stage_id="stage:a",
        milestone_id="a",
        start_step_index=-1,
        end_step_index=-1,
        status=StageStatus.MISSING,
    )

    result = judge.evaluate_stage(
        interval,
        TaskCase("t", "demo"),
        Trajectory("r", "t", []),
        EvaluationLevel.STANDARD,
        {},
    )

    assert result.status == StageStatus.MISSING
    assert result.dimension_scores[Dimension.PROGRESS] <= 0.2
    assert result.diagnosis

