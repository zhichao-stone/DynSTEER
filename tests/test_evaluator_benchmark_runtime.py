from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate import DynSTEEREvaluator
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.judges import CheapJudge
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    Dimension,
    EvaluationLevel,
    EventType,
    JsonObject,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


class PassJudge(CheapJudge):
    """在任意等级都返回通过结果的测试 judge，用于驱动 LLM 档位而不访问网络。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            evaluator_level=EvaluationLevel.CHEAP,
            status=StageStatus.PASS,
            stage_score=0.9,
            uncertainty=0.1,
            dimension_scores={dimension: 0.9 for dimension in Dimension},
            evidence=["judge 通过"],
            diagnosis=[],
            judge_confidence=0.95,
        )


def _milestone_with_fatal_minefield() -> TaskCase:
    milestone = Milestone(
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
    graph = MilestoneGraph(nodes=[milestone], minefields=[minefield])
    return TaskCase(task_id="task-1", task_description="测试任务", milestone_graph=graph)


class FakeRuntimeHarness(BaseBenchmarkHarness):
    benchmark = "fake"

    def __init__(self, task_case: TaskCase, batches: list[HarnessAdvanceResult], metrics: JsonObject) -> None:
        super().__init__()
        self.task_case = task_case
        self.batches = list(batches)
        self.metrics = metrics
        self.stopped_reason: str | None = None
        self.torn_down = False

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        self.prepare_config(config)
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case-1")]

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        return {"case_id": case_id, "raw_output_dir": raw_output_dir}

    def task_case_from_session(self, session: object) -> TaskCase:
        return self.task_case

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        if not self.batches:
            raise RuntimeError("benchmark session 未完成但没有新增轨迹步骤")
        return self.batches.pop(0)

    def case_finished(self, session: object) -> bool:
        return False

    def metrics_from_session(self, session: object) -> JsonObject:
        return dict(self.metrics)

    def stop_case(self, session: object, reason: str) -> None:
        self.stopped_reason = reason

    def teardown_case(self, session: object) -> None:
        self.torn_down = True


def _step(index: int, content: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )


def test_runtime_milestone_hit_triggers_fatal_minefield_stop(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[HarnessAdvanceResult(steps=[_step(0, "任务完成")], continue_running=True)],
        metrics={"danger": True},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert result.terminated_by_policy is True
    assert result.termination_code is not None and result.termination_code.startswith("minefield")
    assert harness.stopped_reason is not None
    assert harness.torn_down is True
    assert any(settlement.kind == "milestone" for settlement in result.stage_settlements)


def test_runtime_completes_without_milestone_when_clean(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[HarnessAdvanceResult(steps=[_step(0, "still working")], continue_running=False, reason="自然完成")],
        metrics={"danger": False},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert result.terminated_by_policy is False
    assert result.stage_settlements[-1].kind == "finish"


def test_evaluator_preserves_advance_error_when_teardown_fails(tmp_path: Path) -> None:
    class FailingTeardownHarness(FakeRuntimeHarness):
        def advance_case(self, session: object) -> HarnessAdvanceResult:
            raise RuntimeError("advance boom")

        def teardown_case(self, session: object) -> None:
            self.torn_down = True
            raise RuntimeError("teardown boom")

    harness = FailingTeardownHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[],
        metrics={"danger": False},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    with pytest.raises(RuntimeError, match="advance boom"):
        evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert harness.torn_down is True


def test_evaluator_raises_teardown_error_after_success(tmp_path: Path) -> None:
    from dynsteer.evaluate.models import HarnessTeardownError

    class FailingTeardownHarness(FakeRuntimeHarness):
        def teardown_case(self, session: object) -> None:
            self.torn_down = True
            raise RuntimeError("teardown boom")

    harness = FailingTeardownHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[HarnessAdvanceResult(steps=[_step(0, "still working")], continue_running=False)],
        metrics={"danger": False},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    with pytest.raises(HarnessTeardownError, match="case-1"):
        evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert harness.torn_down is True


def test_generic_harness_teardown_clears_session_references() -> None:
    from dynsteer.adapter.generic.harness import GenericHarness, GenericSession

    task_case = TaskCase(task_id="task-1", task_description="测试任务")
    step = _step(0, "step")
    session = GenericSession(
        task_case=task_case,
        remaining_steps=[step],
        snapshots=[],
        final_state={"large": ["value"]},
        metrics={"metric": 1.0},
    )

    GenericHarness().teardown_case(session)

    assert session.remaining_steps == []
    assert session.snapshots == []
    assert session.final_state is None
    assert session.metrics == {}


def test_toolsandbox_harness_teardown_attempts_all_roles_and_clears_references(tmp_path: Path) -> None:
    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness, ToolSandboxSession

    class Role:
        def __init__(self, name: str, fail: bool = False) -> None:
            self.name = name
            self.fail = fail
            self.torn_down = False

        def teardown(self) -> None:
            self.torn_down = True
            if self.fail:
                raise RuntimeError(f"{self.name} teardown failed")

    first = Role("first", fail=True)
    second = Role("second")
    roles: dict[object, object] = {"first": first, "second": second}
    session = ToolSandboxSession(
        scenario=object(),
        roles=roles,
        context=object(),
        case_id="case-1",
        run_id="run-1",
        raw_output_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="ToolSandbox role 资源释放失败"):
        ToolSandboxHarness().teardown_case(session)

    assert first.torn_down is True
    assert second.torn_down is True
    assert session.roles == {}
    assert session.context is None
    assert session.scenario is None
