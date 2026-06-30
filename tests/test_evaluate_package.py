from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.evaluate import DynSTEEREvaluator, JudgeConfigurationError
from dynsteer.evaluate.score import GeneralScorer
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.judges import ExpensiveJudge, StandardJudge
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    Dimension,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryEvaluationReport,
    TrajectoryStep,
)


def _provider_env() -> dict[str, str]:
    return {
        "DYNSTEER_JUDGE_PROVIDER": "openai_compatible",
        "DYNSTEER_JUDGE_MODEL": "judge-model",
        "DYNSTEER_JUDGE_BASE_URL": "https://example.invalid/v1",
        "DYNSTEER_JUDGE_API_KEY": "secret-token",
    }


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


def _trajectory(content: str = "hello") -> Trajectory:
    step = TrajectoryStep(
        step_id="s0",
        index=0,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )
    return Trajectory(run_id="run-1", task_id="task-1", steps=[step])


def test_imports_remain_available() -> None:
    assert DynSTEEREvaluator is not None
    assert issubclass(JudgeConfigurationError, RuntimeError)


def test_from_env_without_provider_is_cheap_only() -> None:
    evaluator = DynSTEEREvaluator.from_env({})

    with pytest.raises(JudgeConfigurationError, match="LLMJudge"):
        evaluator.evaluate_trajectory(_task_with_missing_milestone(), _trajectory("hello"))


def test_from_env_with_provider_enables_standard_and_expensive() -> None:
    evaluator = DynSTEEREvaluator.from_env(_provider_env())

    assert isinstance(evaluator._standard_judge, StandardJudge)
    assert isinstance(evaluator._expensive_judge, ExpensiveJudge)


def test_evaluate_minefields_member_detects_fatal() -> None:
    minefield = Minefield(
        minefield_id="mf1",
        name="致命操作",
        description="禁止触发危险指标",
        severity="fatal",
        constraints=[
            Constraint(
                constraint_id="mf-c1",
                target=ConstraintTarget.METRIC,
                selector="$.danger",
                operator=Operator.EQUALS,
                expected=True,
            )
        ],
        penalty=MinefieldPenalty(mode="multiplier", value=0.0),
    )
    graph = MilestoneGraph(minefields=[minefield])
    trajectory = Trajectory(run_id="run-1", task_id="task-1", steps=[], metrics={"danger": True})

    matches, max_score, fatal = DynSTEEREvaluator().evaluate_minefields(graph, trajectory)

    assert fatal is True
    assert max_score == pytest.approx(1.0)
    assert matches[0]["minefield_id"] == "mf1"


def test_select_evaluation_level_member_escalates_low_confidence() -> None:
    result = StageEvaluationResult(
        stage_id="stage:m1",
        milestone_id="m1",
        evaluator_level=EvaluationLevel.STANDARD,
        status=StageStatus.WARN,
        stage_score=0.7,
        uncertainty=0.1,
        dimension_scores={dimension: 0.7 for dimension in Dimension},
        judge_confidence=0.2,
    )

    decision = DynSTEEREvaluator().select_evaluation_level(result)

    assert decision.level == EvaluationLevel.EXPENSIVE


def _passing_milestone_task() -> TaskCase:
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m1",
                name="说出完成",
                description="轨迹中需要出现完成",
                constraints=[
                    Constraint(
                        constraint_id="c1",
                        target=ConstraintTarget.STEP,
                        selector="$.content",
                        operator=Operator.CONTAINS,
                        expected="完成",
                    )
                ],
            )
        ]
    )
    return TaskCase(task_id="task-1", task_description="测试任务", milestone_graph=graph)


def test_evaluate_trajectory_cheap_pass_needs_no_llm() -> None:
    evaluator = DynSTEEREvaluator.from_env({})

    report = evaluator.evaluate_trajectory(_passing_milestone_task(), _trajectory("任务完成"))

    assert report.milestone_coverage == "full"
    assert report.overall_score == pytest.approx(1.0)
    assert report.stage_reports[0].evaluator_level == EvaluationLevel.CHEAP
    assert report.first_failure_stage_id is None


def test_evaluate_trajectory_without_milestones_is_none_coverage() -> None:
    task_case = TaskCase(task_id="task-1", task_description="测试任务")

    report = DynSTEEREvaluator().evaluate_trajectory(task_case, _trajectory("hello"))

    assert report.milestone_coverage == "none"
    assert report.overall_score == pytest.approx(1.0)
    assert report.stage_reports == []


def test_report_summary_stage_count_only_counts_matched_milestone_stages() -> None:
    report = TrajectoryEvaluationReport(
        run_id="run-1",
        task_id="task-1",
        milestone_coverage="partial",
        overall_score=0.5,
        stage_reports=[
            StageEvaluationResult(
                stage_id="runtime:st1",
                milestone_id="m0",
                evaluator_level=EvaluationLevel.CHEAP,
                status=StageStatus.FAIL,
                stage_score=0.2,
                uncertainty=0.1,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
            ),
            StageEvaluationResult(
                stage_id="runtime:missing:m1",
                milestone_id="m1",
                evaluator_level=EvaluationLevel.CHEAP,
                status=StageStatus.FAIL,
                stage_score=0.0,
                uncertainty=0.0,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
            ),
            StageEvaluationResult(
                stage_id="runtime:st2",
                milestone_id=None,
                evaluator_level=EvaluationLevel.CHEAP,
                status=StageStatus.PASS,
                stage_score=1.0,
                uncertainty=0.0,
                dimension_scores={dimension: 1.0 for dimension in Dimension},
            ),
        ],
    )

    assert report.to_summary_dict()["stage_count"] == 1


def test_evaluate_trajectory_fatal_minefield_short_circuits() -> None:
    minefield = Minefield(
        minefield_id="mf1",
        name="致命操作",
        description="禁止触发危险指标",
        severity="fatal",
        constraints=[
            Constraint(
                constraint_id="mf-c1",
                target=ConstraintTarget.METRIC,
                selector="$.danger",
                operator=Operator.EQUALS,
                expected=True,
            )
        ],
        penalty=MinefieldPenalty(mode="multiplier", value=0.0),
    )
    task_case = TaskCase(
        task_id="task-1",
        task_description="测试任务",
        milestone_graph=MilestoneGraph(minefields=[minefield]),
    )
    trajectory = Trajectory(run_id="run-1", task_id="task-1", steps=[], metrics={"danger": True})

    report = DynSTEEREvaluator().evaluate_trajectory(task_case, trajectory)

    assert report.overall_score == pytest.approx(0.0)
    assert report.first_failure_stage_id == "minefield:mf1"
    assert report.stage_reports == []


class _LanguageHarness:
    benchmark = "fake"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark="fake", case_id="case-1")]

    def prepare_config(self, config: HarnessRunConfig) -> None:
        return None

    def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        return "run-1"

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: object) -> object:
        return {"case_id": case_id}

    def task_case_from_session(self, session: object) -> TaskCase:
        return TaskCase(task_id="task-1", task_description="测试任务", metadata={"source": "test"})

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="done")

    def raw_summary_from_session(self, session: object) -> dict[str, object]:
        return {}

    def final_state_from_session(self, session: object) -> dict[str, object] | None:
        return None

    def metrics_from_session(self, session: object) -> dict[str, object]:
        return {}

    def constraint_scorer(self) -> GeneralScorer:
        return GeneralScorer()

    def teardown_case(self, session: object) -> None:
        return None


def test_evaluate_merges_run_config_language_into_task_metadata(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1", "language": "zh"},
    )

    result = DynSTEEREvaluator().evaluate(_LanguageHarness(), "case-1", config)  # type: ignore[arg-type]

    assert result.task_case.metadata["source"] == "test"
    assert result.task_case.metadata["language"] == "zh"
