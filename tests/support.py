from collections.abc import Iterable
from pathlib import Path

from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.model import (
    Constraint,
    ConstraintTarget,
    Dimension,
    EvaluationLevel,
    Minefield,
    MinefieldPenalty,
    Milestone,
    MilestoneGraph,
    Operator,
    RuntimeEvaluationState,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


def make_empty_task_case(
    case_id: str = "empty_case",
    minefields: list[Minefield] | None = None,
) -> TaskCase:
    """构造没有固定 milestone 的测试 case。"""
    return TaskCase(
        task_id=case_id,
        task_description="Add the requested contact after receiving sufficient information.",
        case_id=case_id,
        milestone_graph=MilestoneGraph(nodes=[], minefields=list(minefields or [])),
    )


def make_nonempty_task_case(case_id: str = "normal_case") -> TaskCase:
    """构造包含一个真实 milestone 的测试 case。"""
    milestone = Milestone(
        milestone_id="m1",
        name="Complete task",
        description="Agent completes the requested task.",
        constraints=[
            Constraint(
                constraint_id="c1",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected="done",
            )
        ],
        stage_anchor_predecessor_id="__start__",
    )
    return TaskCase(
        task_id=case_id,
        task_description="Complete the task.",
        case_id=case_id,
        milestone_graph=MilestoneGraph(nodes=[milestone], edges=[]),
    )


def make_minefield(minefield_id: str = "mf0") -> Minefield:
    """构造用于计数和 fatal 状态测试的 minefield。"""
    return Minefield(
        minefield_id=minefield_id,
        name="Forbidden contact modification",
        description="Do not modify contacts without sufficient permission.",
        severity="fatal",
        constraints=[],
        penalty=MinefieldPenalty(mode="fixed", value=1.0),
    )


def make_trajectory(run_id: str = "source_run", task_id: str = "empty_case") -> Trajectory:
    """构造无需真实 step 的完整轨迹。"""
    return Trajectory(
        run_id=run_id,
        task_id=task_id,
        steps=[],
        final_state={"CONTACT": {"rows": [{"name": "Ada Lovelace"}]}},
    )


def make_runtime_state(
    fatal_minefield: bool = False,
    minefield_matches: list[dict[str, object]] | None = None,
) -> RuntimeEvaluationState:
    """构造 finish/report 测试所需的运行期状态。"""
    matches = list(minefield_matches or [])
    state = RuntimeEvaluationState(
        weights={dimension: 1 / len(Dimension) for dimension in Dimension},
        settlements=[
            HarnessStageSettlement(
                settlement_id="st0",
                kind="start",
                milestone_id=None,
                start_step_index=0,
                end_step_index=0,
                evidence=["start 结算节点"],
            )
        ],
    )
    state.fatal_minefield = fatal_minefield
    state.minefield_matches = matches
    state.max_minefield_score = 1.0 if fatal_minefield else 0.0
    return state


def make_harness_config(tmp_path: Path, default_reference: dict[str, object] | None = None) -> HarnessRunConfig:
    """构造 replay 测试使用的最小 HarnessRunConfig。"""
    metadata: dict[str, object] = {"method": "dynsteer_replay"}
    if default_reference is not None:
        metadata["default_reference"] = default_reference
    return HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata=metadata,
    )


def make_finish_stage(status: StageStatus) -> StageEvaluationResult:
    """构造空图 coverage 测试所需的 finish stage。"""
    score = 0.9 if status in {StageStatus.PASS, StageStatus.WARN, StageStatus.AMBIGUOUS} else 0.0
    return StageEvaluationResult(
        stage_id="__start__->__finish__",
        milestone_id="__finish__",
        status=status,
        stage_score=score,
        dimension_scores={Dimension.PROGRESS: score},
        metadata={"finish_stage_evaluation": {"coverage_basis": "whole_trajectory"}},
    )


class FakeStandardJudge:
    """只实现 evaluate_stage 的测试替身。"""

    def __init__(self, status: StageStatus, score: float) -> None:
        self.status = status
        self.score = score
        self.calls: list[tuple[StageInterval, list[Dimension]]] = []

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """返回预设维度分，模拟 DynSTEER standard judge。"""
        target_dimensions = list(dimensions or Dimension)
        self.calls.append((interval, target_dimensions))
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            status=self.status,
            stage_score=0.0,
            dimension_scores={dimension: self.score for dimension in target_dimensions},
            dimension_levels={dimension: EvaluationLevel.STANDARD for dimension in target_dimensions},
            dimension_confidence={dimension: 0.9 for dimension in target_dimensions},
            dimension_uncertainty={dimension: 0.1 for dimension in target_dimensions},
            evidence=["fake standard judge evidence"],
            diagnosis=["fake standard judge diagnosis"],
            metadata={"fake_standard_judge": True},
        )
