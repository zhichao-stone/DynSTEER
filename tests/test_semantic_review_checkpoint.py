from pathlib import Path
from typing import Iterable

import pytest

from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.matching.milestone import _is_llm_semantic_review_candidate
from dynsteer.evaluate.settlement import evaluate_checkpoint
from dynsteer.evaluate.step import evaluate_agent_step
from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.judges import CheapJudge
from dynsteer.model import (
    Actor,
    Boundary,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    Dimension,
    DynamicWeightConfig,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    MilestoneStepAnalysis,
    Operator,
    RuntimeEvaluationState,
    StageEvaluationResult,
    StageEvaluationSpec,
    StageGoalSemanticKind,
    StageInterval,
    StageStatus,
    TaskCase,
    ThresholdConfig,
    Trajectory,
    TrajectoryStep,
)


class FakeStandardJudge:
    def __init__(self) -> None:
        self.called_dimensions: list[Dimension] = []

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """返回高分 standard 复判结果。"""
        target_dimensions = list(dimensions or [])
        self.called_dimensions = target_dimensions
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            status=StageStatus.PASS,
            stage_score=0.0,
            dimension_scores={dimension: 1.0 for dimension in target_dimensions},
            dimension_levels={dimension: EvaluationLevel.STANDARD for dimension in target_dimensions},
            dimension_confidence={dimension: 0.95 for dimension in target_dimensions},
            dimension_uncertainty={dimension: 0.05 for dimension in target_dimensions},
            evidence=["standard semantic review accepted"],
            diagnosis=[],
            hard_constraints_all_pass=True,
        )


def test_semantic_review_forces_standard_dimensions_and_accepts_checkpoint() -> None:
    """覆盖 WARN semantic candidate 会强制 standard judge 复判。"""
    task_case, graph, milestone, trajectory, boundary, milestone_score = _semantic_case()
    state = _runtime_state(graph)
    standard_judge = FakeStandardJudge()

    decision = evaluate_checkpoint(
        config=_config(),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        milestone=milestone,
        boundary=boundary,
        milestone_score=milestone_score,
        cheap_judge=CheapJudge(),
        standard_judge=standard_judge,
        expensive_judge=None,
        thresholds=ThresholdConfig(),
        weight_config=DynamicWeightConfig(),
        force_standard_dimensions=[Dimension.PROGRESS, Dimension.INTERACTION_QUALITY],
    )

    assert standard_judge.called_dimensions == [Dimension.PROGRESS, Dimension.INTERACTION_QUALITY]
    assert decision.checkpoint is not None
    assert decision.stage_result is not None
    semantic_review = decision.stage_result.metadata["semantic_review"]
    assert semantic_review["standard_judge_status"] == "called"
    assert semantic_review["settlement_accepted"] is True


def test_fail_semantic_message_candidate_can_request_standard_review() -> None:
    """覆盖语义消息 cheap 分数低于 WARN 但具备同向消息时仍进入复判候选。"""
    _, _, milestone, _, _, milestone_score = _semantic_case(
        score_value=0.46,
        status=StageStatus.FAIL,
        hard_constraints_all_pass=False,
    )

    assert _is_llm_semantic_review_candidate(milestone, milestone_score) is True


def test_semantic_review_accepts_fail_candidate_when_only_message_similarity_failed() -> None:
    """覆盖 standard 复判接受后解除仅由消息相似度造成的 hard fail。"""
    task_case, graph, milestone, trajectory, boundary, milestone_score = _semantic_case(
        score_value=0.46,
        status=StageStatus.FAIL,
        hard_constraints_all_pass=False,
    )
    state = _runtime_state(graph)
    standard_judge = FakeStandardJudge()

    decision = evaluate_checkpoint(
        config=_config(),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        milestone=milestone,
        boundary=boundary,
        milestone_score=milestone_score,
        cheap_judge=CheapJudge(),
        standard_judge=standard_judge,
        expensive_judge=None,
        thresholds=ThresholdConfig(),
        weight_config=DynamicWeightConfig(),
        force_standard_dimensions=[Dimension.PROGRESS, Dimension.INTERACTION_QUALITY],
    )

    assert decision.checkpoint is not None
    assert decision.stage_result is not None
    assert decision.stage_result.status == StageStatus.PASS
    semantic_review = decision.stage_result.metadata["semantic_review"]
    assert semantic_review["semantic_review_passed"] is True
    assert semantic_review["structural_failure_cleared"] is True
    assert semantic_review["settlement_accepted"] is True


def test_semantic_review_candidate_skips_when_standard_judge_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖缺少 standard judge 时只记录 skipped，不调用 checkpoint。"""
    task_case, graph, milestone, trajectory, boundary, milestone_score = _semantic_case()
    state = _runtime_state(graph)
    attempt_detail = {"llm_semantic_review": {"status": "candidate"}, "candidate_scores": []}

    def fake_analyze_milestone_step(*args: object, **kwargs: object) -> MilestoneStepAnalysis:
        return MilestoneStepAnalysis(
            hit=(milestone, boundary, milestone_score),
            attempt_detail=attempt_detail,
            requires_semantic_review=True,
        )

    def fail_checkpoint(**kwargs: object) -> object:
        raise AssertionError("缺少 standard judge 时不应进入 checkpoint")

    monkeypatch.setattr("dynsteer.evaluate.step.analyze_milestone_step", fake_analyze_milestone_step)

    decision = evaluate_agent_step(
        config=_config(stop_on_ready_frontier_no_progress=False),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        step=trajectory.steps[0],
        scorer=CheapJudge(),
        standard_judge=None,
        thresholds=ThresholdConfig(),
        evaluate_checkpoint=fail_checkpoint,
    )

    assert decision is None
    assert state.match_attempts[0]["llm_semantic_review"]["status"] == "skipped_no_standard_judge"


def _semantic_case(
    score_value: float = 0.79,
    status: StageStatus = StageStatus.WARN,
    hard_constraints_all_pass: bool = True,
) -> tuple[TaskCase, MilestoneGraph, Milestone, Trajectory, Boundary, MilestoneScore]:
    constraint = Constraint(
        constraint_id="c0",
        target=ConstraintTarget.STEP,
        selector="$.content",
        operator=Operator.FUZZY_MATCH,
        expected="turn cellular on",
        hard=True,
        stage_goal_semantics={
            "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
            "sender": Actor.AGENT.name,
            "recipient": Actor.USER.name,
            "match_policy": "semantic_equivalent",
        },
    )
    milestone = Milestone(
        milestone_id="m0",
        name="m0",
        description="emit message",
        constraints=[constraint],
        pass_threshold=0.8,
        stage_anchor_predecessor_id="__start__",
    )
    graph = MilestoneGraph(nodes=[milestone], edges=[])
    stage_id = "__start__->m0"
    task_case = TaskCase(
        task_id="task",
        task_description="desc",
        case_id="case",
        milestone_graph=graph,
        stage_goals={stage_id: "Emit a semantic message."},
        stage_evaluation_specs={
            stage_id: StageEvaluationSpec(
                focus_dimensions=[Dimension.PROGRESS, Dimension.EFFICIENCY, Dimension.INTERACTION_QUALITY]
            )
        },
    )
    step = TrajectoryStep(
        step_id="s1",
        index=1,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        recipient=Actor.USER,
        content="cellular is now enabled",
    )
    trajectory = Trajectory(run_id="run", task_id="task", steps=[step])
    boundary = Boundary(boundary_id="b1", step_index=1, snapshot_id=None, reason="test", step_id="s1")
    milestone_score = MilestoneScore(
        milestone_id="m0",
        boundary_id="b1",
        score=score_value,
        status=status,
        evidence=["semantic candidate cheap score"],
        missing_ratio=0.0,
        hard_constraints_all_pass=hard_constraints_all_pass,
        constraint_scores=[
            ConstraintScore(
                constraint_id="c0",
                score=score_value,
                missing=False,
                evidence=["semantic candidate cheap score"],
                actual=[{"sender": "AGENT", "recipient": "USER", "content": step.content}],
            )
        ],
    )
    return task_case, graph, milestone, trajectory, boundary, milestone_score


def _runtime_state(graph: MilestoneGraph) -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={dimension: 1.0 for dimension in Dimension},
        settlements=[
            HarnessStageSettlement(
                settlement_id="st0",
                kind="start",
                milestone_id=None,
                start_step_index=0,
                end_step_index=0,
            )
        ],
        milestone_frontier=initialize_milestone_frontier(graph),
    )


def _config(stop_on_ready_frontier_no_progress: bool = True) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=Path("data/toolsandbox"),
        stop_on_ready_frontier_no_progress=stop_on_ready_frontier_no_progress,
    )
