from __future__ import annotations

from pathlib import Path

from dynsteer.boundary import candidate_boundary_for_current_step
from dynsteer.config import ThresholdConfig
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.milestone import analyze_milestone_step
from dynsteer.evaluate.models import RuntimeEvaluationState
from dynsteer.evaluate.score import GeneralScorer, ScoringContext
from dynsteer.evaluate.weights import select_initial_weights
from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.judges.base import BaseJudge
from dynsteer.model import (
    Actor,
    Boundary,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    Dimension,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    Operator,
    StageEvaluationResult,
    StageGoalSemanticKind,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


class WarnMessageScorer(GeneralScorer):
    def score_milestone(
        self,
        milestone: Milestone,
        boundary: Boundary,
        trajectory: Trajectory,
        reference_snapshots: list[StateSnapshot],
        context: ScoringContext | None = None,
    ) -> MilestoneScore:
        return MilestoneScore(
            milestone_id=milestone.milestone_id,
            boundary_id=boundary.boundary_id,
            score=0.78,
            status=StageStatus.WARN,
            evidence=["message semantic match score is below pass threshold"],
            missing_ratio=0.0,
            hard_constraints_all_pass=True,
            constraint_scores=[
                ConstraintScore("m5_c0", score=0.611, missing=False),
                ConstraintScore("m5_c1", score=1.0, missing=False),
            ],
        )


class WarnStateScorer(GeneralScorer):
    def score_milestone(
        self,
        milestone: Milestone,
        boundary: Boundary,
        trajectory: Trajectory,
        reference_snapshots: list[StateSnapshot],
        context: ScoringContext | None = None,
    ) -> MilestoneScore:
        return MilestoneScore(
            milestone_id=milestone.milestone_id,
            boundary_id=boundary.boundary_id,
            score=0.78,
            status=StageStatus.WARN,
            evidence=["state update score is below pass threshold"],
            missing_ratio=0.0,
            hard_constraints_all_pass=True,
            constraint_scores=[ConstraintScore("m3_c0", score=0.78, missing=False)],
        )


def _message_milestone() -> Milestone:
    milestone = Milestone(
        milestone_id="m5",
        name="city response",
        description="Tell the user the current city",
        constraints=[
            Constraint(
                constraint_id="m5_c0",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                namespace="SANDBOX",
                hard=True,
                threshold=1.0,
                stage_goal_semantics={
                    "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
                    "sender": "AGENT",
                    "recipient": "USER",
                    "content": "You are currently in Cupertino",
                    "match_policy": "semantic_equivalent",
                },
            ),
            Constraint(
                constraint_id="m5_c1",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                namespace="CONTACT",
                hard=True,
                threshold=1.0,
                stage_goal_semantics={
                    "kind": StageGoalSemanticKind.PRESERVE_STATE.value,
                    "namespace": "CONTACT",
                },
            ),
        ],
    )
    milestone.stage_anchor_predecessor_id = "__start__"
    return milestone


def _state_milestone() -> Milestone:
    milestone = Milestone(
        milestone_id="m3",
        name="contact update",
        description="Update contact phone number",
        constraints=[
            Constraint(
                constraint_id="m3_c0",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                namespace="CONTACT",
                hard=True,
                threshold=1.0,
                stage_goal_semantics={
                    "kind": StageGoalSemanticKind.SET_STATE.value,
                    "namespace": "CONTACT",
                    "expected": {"phone_number": "+10293847563"},
                },
            )
        ],
    )
    milestone.stage_anchor_predecessor_id = "__start__"
    return milestone


def _task_case(milestone: Milestone) -> TaskCase:
    return TaskCase(
        task_id="toolsandbox::case",
        task_description="test task",
        case_id="case",
        milestone_graph=MilestoneGraph(nodes=[milestone]),
        stage_goals={
            f"__start__->{milestone.milestone_id}": "Complete current milestone.",
        },
    )


def _trajectory() -> tuple[Trajectory, TrajectoryStep]:
    step = TrajectoryStep(
        step_id="s50",
        index=50,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content="Based on your coordinates, you are in Cupertino, California.",
    )
    trajectory = Trajectory(
        run_id="run",
        task_id="toolsandbox::case",
        steps=[],
        snapshots=[
            StateSnapshot(
                snapshot_id="snap50",
                after_step_id="s50",
                after_step_index=50,
                namespaces={"SANDBOX": [], "CONTACT": []},
            )
        ],
    )
    trajectory.append_step(step)
    return trajectory, step


def test_semantic_message_warn_returns_hit_for_llm_review() -> None:
    task_case = _task_case(_message_milestone())
    trajectory, step = _trajectory()

    analysis = analyze_milestone_step(
        task_case=task_case,
        trajectory=trajectory,
        step=step,
        matched={},
        scorer=WarnMessageScorer(),
    )

    assert analysis.hit is not None
    assert analysis.hit[0].milestone_id == "m5"
    assert analysis.hit[2].status == StageStatus.WARN
    assert analysis.attempt_detail is not None
    assert analysis.attempt_detail["llm_semantic_review"]["status"] == "candidate"


def test_state_warn_does_not_return_hit_for_llm_review() -> None:
    task_case = _task_case(_state_milestone())
    trajectory, step = _trajectory()

    analysis = analyze_milestone_step(
        task_case=task_case,
        trajectory=trajectory,
        step=step,
        matched={},
        scorer=WarnStateScorer(),
    )

    assert analysis.hit is None
    assert analysis.attempt_detail is not None
    assert "llm_semantic_review" not in analysis.attempt_detail


class StaticJudge(BaseJudge):
    def __init__(self, status: StageStatus, stage_score: float) -> None:
        self._status = status
        self._stage_score = stage_score

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
            evaluator_level=EvaluationLevel.STANDARD,
            status=self._status,
            stage_score=self._stage_score,
            uncertainty=0.0,
            dimension_scores={dimension: self._stage_score for dimension in Dimension},
            evidence=["standard judge reviewed semantic message"],
            diagnosis=["semantic message review result"],
            hard_constraints_all_pass=True,
            required_fields_missing_ratio=0.0,
            judge_confidence=1.0,
        )


def _state(task_case: TaskCase) -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights=select_initial_weights(task_case),
        settlements=[
            HarnessStageSettlement(
                settlement_id="st0",
                kind="start",
                milestone_id=None,
                start_step_index=0,
                end_step_index=0,
            )
        ],
        matched_settlements={},
        stage_reports=[],
        match_attempts=[],
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )


def _warn_hit_inputs() -> tuple[TaskCase, Trajectory, TrajectoryStep, Boundary, MilestoneScore]:
    task_case = _task_case(_message_milestone())
    trajectory, step = _trajectory()
    boundary = candidate_boundary_for_current_step(trajectory, step)
    score = WarnMessageScorer().score_milestone(
        task_case.milestone_graph.nodes[0],
        boundary,
        trajectory,
        trajectory.snapshots,
    )
    return task_case, trajectory, step, boundary, score


def test_warn_hit_is_settled_when_standard_judge_passes(tmp_path: Path) -> None:
    task_case, trajectory, _, boundary, score = _warn_hit_inputs()
    milestone = task_case.milestone_graph.nodes[0]
    state = _state(task_case)
    evaluator = DynSTEEREvaluator(
        standard_judge=StaticJudge(StageStatus.PASS, 1.0),
        thresholds=ThresholdConfig(pass_threshold=0.8),
    )

    decision = evaluator._evaluate_checkpoint(
        config=_config(tmp_path),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        scorer=WarnMessageScorer(),
        milestone=milestone,
        boundary=boundary,
        milestone_score=score,
    )

    assert decision.checkpoint is not None
    assert "m5" in decision.next_state.matched_settlements
    assert decision.stage_result is not None
    assert decision.stage_result.status == StageStatus.PASS


def test_warn_hit_is_not_settled_when_standard_judge_rejects(tmp_path: Path) -> None:
    task_case, trajectory, _, boundary, score = _warn_hit_inputs()
    milestone = task_case.milestone_graph.nodes[0]
    state = _state(task_case)
    evaluator = DynSTEEREvaluator(
        standard_judge=StaticJudge(StageStatus.WARN, 0.65),
        thresholds=ThresholdConfig(pass_threshold=0.8),
    )

    decision = evaluator._evaluate_checkpoint(
        config=_config(tmp_path),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        scorer=WarnMessageScorer(),
        milestone=milestone,
        boundary=boundary,
        milestone_score=score,
    )

    assert decision.checkpoint is None
    assert "m5" not in decision.next_state.matched_settlements
    assert decision.stage_result is not None
    assert decision.stage_result.status == StageStatus.WARN
