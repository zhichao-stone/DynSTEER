from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate import DynSTEEREvaluator
from dynsteer.evaluate.score import GeneralScorer
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.judges import CheapJudge
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintScore,
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
    StateSnapshot,
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


class BatchSnapshotHarness(FakeRuntimeHarness):
    def __init__(self, task_case: TaskCase, batches: list[HarnessAdvanceResult], metrics: JsonObject) -> None:
        super().__init__(task_case=task_case, batches=batches, metrics=metrics)

    def snapshots_from_session(self, session: object) -> list[StateSnapshot]:
        raise AssertionError("Evaluator 不应调用 snapshots_from_session")


def _step(index: int, content: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )


def _state_snapshot(after_step_index: int, done: bool = True) -> StateSnapshot:
    return StateSnapshot(
        snapshot_id=f"state:{after_step_index}",
        after_step_id=f"s{after_step_index}",
        after_step_index=after_step_index,
        namespaces={"default": {"done": done}},
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )


def _state_snapshot_task_case() -> TaskCase:
    milestone = Milestone(
        milestone_id="m1",
        name="状态完成",
        description="done 必须为 true",
        constraints=[
            Constraint(
                constraint_id="c1",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$.done",
                operator=Operator.EQUALS,
                expected=True,
                hard=True,
            )
        ],
    )
    graph = MilestoneGraph(nodes=[milestone])
    return TaskCase(task_id="task-state", task_description="状态任务", milestone_graph=graph)


def _two_step_task_case() -> TaskCase:
    first = Milestone(
        milestone_id="m1",
        name="第一步",
        description="第一步需要说 one",
        constraints=[
            Constraint(
                constraint_id="c1",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected="one",
                hard=True,
            )
        ],
    )
    second = Milestone(
        milestone_id="m2",
        name="第二步",
        description="第二步需要说 two",
        constraints=[
            Constraint(
                constraint_id="c2",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected="two",
                hard=True,
            )
        ],
    )
    graph = MilestoneGraph(nodes=[first, second], edges=[("m1", "m2")])
    return TaskCase(task_id="task-two-step", task_description="两步任务", milestone_graph=graph)


def test_runtime_raw_summary_includes_unmatched_milestone_diagnostics(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_two_step_task_case(),
        batches=[
            HarnessAdvanceResult(
                steps=[_step(0, "one"), _step(1, "not yet")],
                snapshots=[],
                continue_running=False,
                reason="自然完成",
            )
        ],
        metrics={},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    graph_summary = result.raw_summary["milestone_graph_summary"]
    assert graph_summary["total_milestone_count"] == 2
    assert graph_summary["edges"] == [["m1", "m2"]]

    attempts = result.raw_summary["milestone_match_attempts"]
    failed_attempt = next(item for item in attempts if item["step_index"] == 1)
    assert failed_attempt["ready_before"] == ["m2"]
    assert failed_attempt["selected_milestone_id"] is None
    assert failed_attempt["candidate_scores"][0]["milestone_id"] == "m2"
    assert failed_attempt["candidate_scores"][0]["reject_reason"] == "status_not_pass"
    assert failed_attempt["candidate_scores"][0]["score"]["status"] == "fail"

    final = {
        item["milestone_id"]: item
        for item in result.raw_summary["milestone_final_diagnostics"]
    }
    assert final["m1"]["final_state"] == "matched"
    assert final["m2"]["final_state"] == "pending"
    assert final["m2"]["ready_ever"] is True
    assert final["m2"]["attempt_count"] == 1
    assert final["m2"]["blocker"] == "attempted_but_not_pass"


def test_runtime_milestone_hit_triggers_fatal_minefield_stop(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[HarnessAdvanceResult(steps=[_step(0, "任务完成")], snapshots=[], continue_running=True)],
        metrics={"danger": True},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert result.terminated_by_policy is True
    assert result.termination_code is not None and result.termination_code.startswith("minefield")
    assert harness.stopped_reason is not None
    assert harness.torn_down is True
    assert any(settlement.kind == "milestone" for settlement in result.stage_settlements)


def test_runtime_milestone_settlement_includes_trace_and_matching_details(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[HarnessAdvanceResult(steps=[_step(0, "任务完成")], snapshots=[], continue_running=True)],
        metrics={"danger": True},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    settlement = next(item for item in result.stage_settlements if item.kind == "milestone")
    trace = settlement.metadata["stage_trace"]
    assert trace["start_step_index"] == 0
    assert trace["end_step_index"] == 0
    assert trace["step_count"] == 1
    assert trace["steps"][0]["index"] == 0
    assert trace["steps"][0]["actor"] == "agent"
    assert trace["steps"][0]["event_type"] == "message"
    assert trace["steps"][0]["content"] == "任务完成"

    matching = settlement.metadata["milestone_matching"]
    assert matching["mode"] == "runtime_checkpoint"
    assert matching["matched"] is True
    assert matching["milestone"]["milestone_id"] == "m1"
    assert matching["milestone"]["constraint_count"] == 1
    assert matching["boundary"]["step_index"] == 0
    assert matching["boundary"]["reason"] == "agent_message"
    assert matching["ready_milestone_ids_before_match"] == ["m1"]
    assert matching["matched_milestone_ids_before_match"] == []
    assert matching["predecessor_milestone_ids"] == []
    assert matching["score"]["status"] == "pass"
    assert matching["score"]["constraint_scores"][0]["constraint_id"] == "c1"
    assert matching["score"]["constraint_scores"][0]["actual"] == "任务完成"


def test_runtime_completes_without_milestone_when_clean(tmp_path: Path) -> None:
    harness = FakeRuntimeHarness(
        task_case=_milestone_with_fatal_minefield(),
        batches=[
            HarnessAdvanceResult(
                steps=[_step(0, "still working")],
                snapshots=[],
                continue_running=False,
                reason="自然完成",
            )
        ],
        metrics={"danger": False},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert result.terminated_by_policy is False
    assert result.stage_settlements[-1].kind == "finish"
    trace = result.stage_settlements[-1].metadata["stage_trace"]
    assert trace["start_step_index"] == 0
    assert trace["end_step_index"] == 0
    assert trace["step_count"] == 1
    assert trace["steps"][0]["content"] == "still working"
    matching = result.stage_settlements[-1].metadata["milestone_matching"]
    assert matching["mode"] == "runtime_finish"
    assert matching["matched"] is False
    assert matching["matched_milestone_ids"] == []
    assert matching["pending_required_milestone_ids"] == ["m1"]
    assert matching["pending_optional_milestone_ids"] == []
    assert matching["total_milestone_count"] == 1


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
        batches=[HarnessAdvanceResult(steps=[_step(0, "still working")], snapshots=[], continue_running=False)],
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


class RuntimeCustomScorer(GeneralScorer):
    """测试用 runtime scorer：CUSTOM 约束固定通过。"""

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
            evidence=["runtime custom pass"],
            actual=actual,
        )


class CustomScorerHarness(FakeRuntimeHarness):
    def constraint_scorer(self) -> GeneralScorer:
        return RuntimeCustomScorer()


def test_runtime_evaluate_uses_harness_constraint_scorer(tmp_path: Path) -> None:
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
    harness = CustomScorerHarness(
        task_case=task_case,
        batches=[HarnessAdvanceResult(steps=[_step(0, "任意内容")], snapshots=[], continue_running=False)],
        metrics={},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert any(settlement.kind == "milestone" for settlement in result.stage_settlements)


def test_runtime_uses_advance_snapshots_before_checkpoint(tmp_path: Path) -> None:
    harness = BatchSnapshotHarness(
        task_case=_state_snapshot_task_case(),
        batches=[
            HarnessAdvanceResult(
                steps=[_step(0, "触发状态检查")],
                snapshots=[_state_snapshot(0)],
                continue_running=False,
                reason="自然完成",
            )
        ],
        metrics={},
    )
    evaluator = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge())

    result = evaluator.evaluate(harness, "case-1", _config(tmp_path))

    assert [item.milestone_id for item in result.stage_settlements if item.kind == "milestone"] == ["m1"]


def test_runtime_matches_multiple_milestones_from_batch_snapshots(tmp_path: Path) -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m1",
                name="第一步",
                description="phase 为 one",
                constraints=[
                    Constraint(
                        constraint_id="c1",
                        target=ConstraintTarget.STATE_SNAPSHOT,
                        selector="$.phase",
                        operator=Operator.EQUALS,
                        expected="one",
                        hard=True,
                    )
                ],
            ),
            Milestone(
                milestone_id="m2",
                name="第二步",
                description="phase 为 two",
                constraints=[
                    Constraint(
                        constraint_id="c2",
                        target=ConstraintTarget.STATE_SNAPSHOT,
                        selector="$.phase",
                        operator=Operator.EQUALS,
                        expected="two",
                        hard=True,
                    )
                ],
            ),
        ],
        edges=[("m1", "m2")],
    )
    task_case = TaskCase(task_id="task-chain", task_description="链式状态任务", milestone_graph=graph)
    snapshots = [
        StateSnapshot("snap-0", "s0", 0, {"default": {"phase": "one"}}),
        StateSnapshot("snap-1", "s1", 1, {"default": {"phase": "two"}}),
    ]
    harness = BatchSnapshotHarness(
        task_case=task_case,
        batches=[
            HarnessAdvanceResult(
                steps=[_step(0, "第一步"), _step(1, "第二步")],
                snapshots=snapshots,
                continue_running=False,
                reason="自然完成",
            )
        ],
        metrics={},
    )

    result = DynSTEEREvaluator(standard_judge=PassJudge(), expensive_judge=PassJudge()).evaluate(
        harness,
        "case-1",
        _config(tmp_path),
    )

    assert [item.milestone_id for item in result.stage_settlements if item.kind == "milestone"] == ["m1", "m2"]
    assert result.evaluation_report.to_summary_dict()["stage_count"] == 3
