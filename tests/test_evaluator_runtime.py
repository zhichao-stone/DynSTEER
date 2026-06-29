from __future__ import annotations

import pytest

import dynsteer.evaluate as evaluate_module
from dynsteer.config import ThresholdConfig
from dynsteer.evaluate import DynSTEEREvaluator, JudgeConfigurationError
from dynsteer.evaluate.score import GeneralScorer
from dynsteer.judges import CheapJudge
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    Dimension,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneGraph,
    Operator,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def _step(content: str = "hello") -> TrajectoryStep:
    return TrajectoryStep(
        step_id="s0",
        index=0,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )


def _trajectory(content: str = "hello") -> Trajectory:
    return Trajectory(run_id="run-1", task_id="task-1", steps=[_step(content)])


def _task_with_missing_milestone() -> TaskCase:
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m1",
                name="必须说完成",
                description="轨迹中需要出现完成",
                constraints=[
                    Constraint(
                        constraint_id="c1",
                        target=ConstraintTarget.STEP,
                        selector="content",
                        operator=Operator.CONTAINS,
                        expected="完成",
                        hard=True,
                    )
                ],
            )
        ]
    )
    return TaskCase(task_id="task-1", task_description="测试任务", milestone_graph=graph)


class RecordingJudge(CheapJudge):
    """记录评估等级并固定返回通过结果的测试 judge。"""

    def __init__(self) -> None:
        self.levels: list[EvaluationLevel] = []

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        self.levels.append(EvaluationLevel.CHEAP)
        result = super().evaluate_stage(interval, task_case, trajectory, weights)
        return StageEvaluationResult(
            stage_id=result.stage_id,
            milestone_id=result.milestone_id,
            evaluator_level=EvaluationLevel.CHEAP,
            status=StageStatus.PASS,
            stage_score=0.86,
            uncertainty=0.1,
            dimension_scores={dimension: 0.86 for dimension in Dimension},
            evidence=["llm judge 通过"],
            diagnosis=[],
            judge_confidence=0.9,
        )


def test_module_level_evaluate_trajectory_removed() -> None:
    assert not hasattr(evaluate_module, "evaluate_trajectory")


def test_dynsteer_evaluator_exposes_member_evaluate_trajectory() -> None:
    assert callable(DynSTEEREvaluator().evaluate_trajectory)


def test_standard_or_expensive_requires_real_llm_judge() -> None:
    evaluator = DynSTEEREvaluator(thresholds=ThresholdConfig())

    with pytest.raises(JudgeConfigurationError, match="LLMJudge"):
        evaluator.evaluate_trajectory(_task_with_missing_milestone(), _trajectory("hello"))


def test_standard_level_uses_injected_judge() -> None:
    recording = RecordingJudge()
    evaluator = DynSTEEREvaluator(standard_judge=recording, expensive_judge=recording)

    report = evaluator.evaluate_trajectory(_task_with_missing_milestone(), _trajectory("hello"))

    assert recording.levels == [EvaluationLevel.CHEAP]
    assert report.stage_reports[0].evaluator_level == EvaluationLevel.STANDARD


class AlwaysPassCustomScorer(GeneralScorer):
    """测试用 scorer：CUSTOM 约束固定通过。"""

    def score_custom_constraint(
        self,
        constraint: Constraint,
        source: object,
        reference_source: object | None,
        actual: object,
        reference_value: object,
        context=None,
    ) -> ConstraintScore:
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=1.0,
            missing=False,
            evidence=["custom pass"],
            actual=actual,
        )


def test_evaluate_trajectory_accepts_explicit_scorer() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m-custom",
                name="custom",
                description="custom milestone",
                constraints=[
                    Constraint(
                        constraint_id="c-custom",
                        target=ConstraintTarget.STEP,
                        selector="$",
                        operator=Operator.CUSTOM,
                        hard=True,
                    )
                ],
            )
        ]
    )
    task_case = TaskCase(task_id="task-1", task_description="测试任务", milestone_graph=graph)
    trajectory = _trajectory("任意内容")
    evaluator = DynSTEEREvaluator(standard_judge=RecordingJudge(), expensive_judge=RecordingJudge())

    report = evaluator.evaluate_trajectory(task_case, trajectory, scorer=AlwaysPassCustomScorer())

    assert report.milestone_coverage == "full"
    assert report.stage_reports[0].milestone_id == "m-custom"
