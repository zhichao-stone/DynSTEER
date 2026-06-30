from __future__ import annotations

import pytest

from dynsteer.evaluate.models import RuntimeEvaluationDecision, RuntimeEvaluationState
from dynsteer.evaluate.telemetry import (
    match_attempt_log_extra,
    milestone_checkpoint_log_extra,
    pending_milestones_log_extra,
    policy_stop_log_extra,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Boundary,
    ConstraintScore,
    Dimension,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneScore,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    TrajectoryStep,
)


def _step() -> TrajectoryStep:
    return TrajectoryStep(
        step_id="s2",
        index=2,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content="done",
    )


def _stage_result() -> StageEvaluationResult:
    return StageEvaluationResult(
        stage_id="runtime:st1",
        milestone_id="m1",
        evaluator_level=EvaluationLevel.STANDARD,
        status=StageStatus.FAIL,
        stage_score=0.2,
        uncertainty=0.0,
        dimension_scores={dimension: 0.2 for dimension in Dimension},
        evidence=["step 2: evidence"],
        diagnosis=["The task goal is wrong"],
        judge_confidence=0.91,
        metadata={"milestone_matching": {"score": {"score": 0.9, "status": "pass"}}},
    )


def _state() -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={dimension: 1 / len(Dimension) for dimension in Dimension},
        settlements=[],
        matched_settlements={
            "m1": HarnessStageSettlement(
                settlement_id="st1",
                kind="milestone",
                milestone_id="m1",
                start_step_index=0,
                end_step_index=2,
            )
        },
        stage_reports=[],
        match_attempts=[
            {
                "step_index": 2,
                "selected_milestone_id": "m1",
            }
        ],
    )


def test_match_attempt_log_extra_summarizes_selected_and_best_candidate() -> None:
    attempt_detail = {
        "step_index": 2,
        "step_id": "s2",
        "matched_before": ["m0"],
        "ready_before": ["m1", "m2"],
        "selected_milestone_id": "m1",
        "candidate_scores": [
            {
                "milestone_id": "m2",
                "score": {"score": 0.1, "status": "fail", "constraint_scores": []},
                "selected": False,
                "reject_reason": "status_not_pass",
            },
            {
                "milestone_id": "m1",
                "score": {
                    "score": 0.92,
                    "status": "pass",
                    "constraint_scores": [
                        {"constraint_id": "c1", "score": 0.92, "missing": False},
                    ],
                },
                "selected": True,
                "reject_reason": None,
            },
        ],
    }

    extra = match_attempt_log_extra("case-1", _step(), attempt_detail)

    assert extra["case_id"] == "case-1"
    assert extra["step_index"] == 2
    assert extra["step_id"] == "s2"
    assert extra["step_actor"] == "agent"
    assert extra["step_event_type"] == "message"
    assert extra["ready_milestone_ids"] == ["m1", "m2"]
    assert extra["matched_milestone_ids"] == ["m0"]
    assert extra["selected_milestone_id"] == "m1"
    assert extra["candidate_count"] == 2
    assert extra["best_candidate_milestone_id"] == "m1"
    assert extra["best_candidate_score"] == pytest.approx(0.92)
    assert extra["best_candidate_status"] == "pass"
    assert extra["best_candidate_reject_reason"] is None
    assert extra["best_candidate_constraint_summary"] == [
        {"constraint_id": "c1", "score": 0.92, "missing": False}
    ]


def test_milestone_checkpoint_log_extra_keeps_two_score_layers() -> None:
    milestone = Milestone("m1", "回复用户", "回复完成", constraints=[])
    boundary = Boundary("b0", 2, None, "agent_message", step_id="s2")
    milestone_score = MilestoneScore(
        milestone_id="m1",
        boundary_id="b0",
        score=0.9,
        status=StageStatus.PASS,
        evidence=["milestone pass"],
        constraint_scores=[ConstraintScore("c1", 0.9, False)],
    )

    extra = milestone_checkpoint_log_extra(
        case_id="case-1",
        milestone=milestone,
        boundary=boundary,
        milestone_score=milestone_score,
        stage_result=_stage_result(),
        matched_before={},
        ready_before=["m1"],
    )

    assert extra["milestone_id"] == "m1"
    assert extra["boundary_id"] == "b0"
    assert extra["boundary_step_index"] == 2
    assert extra["milestone_score"] == pytest.approx(0.9)
    assert extra["milestone_status"] == "pass"
    assert extra["stage_id"] == "runtime:st1"
    assert extra["stage_score"] == pytest.approx(0.2)
    assert extra["stage_status"] == "fail"
    assert extra["evaluator_level"] == "standard"
    assert extra["stage_first_evidence"] == "step 2: evidence"
    assert extra["stage_first_diagnosis"] == "The task goal is wrong"


def test_policy_stop_log_extra_distinguishes_milestone_pass_and_stage_fail() -> None:
    task_case = TaskCase(
        task_id="task-1",
        task_description="Turn off cellular",
        milestone_graph=None,
    )
    decision = RuntimeEvaluationDecision(
        checkpoint=None,
        stage_result=_stage_result(),
        next_state=_state(),
        should_stop=True,
        termination_code="stage_failure:m1",
        termination_reason="阶段失败",
    )

    extra = policy_stop_log_extra("case-1", task_case, decision, "stage_failure:m1", "阶段失败")

    assert extra["case_id"] == "case-1"
    assert extra["termination_code"] == "stage_failure:m1"
    assert extra["matched_milestone_ids"] == ["m1"]
    assert extra["pending_required_milestone_ids"] == []
    assert extra["stage_id"] == "runtime:st1"
    assert extra["milestone_id"] == "m1"
    assert extra["milestone_score"] == pytest.approx(0.9)
    assert extra["milestone_status"] == "pass"
    assert extra["stage_score"] == pytest.approx(0.2)
    assert extra["stage_status"] == "fail"
    assert extra["stage_first_diagnosis"] == "The task goal is wrong"
    assert extra["last_match_step_index"] == 2
    assert extra["last_selected_milestone_id"] == "m1"


def test_policy_stop_log_extra_reads_milestone_layer_from_checkpoint_metadata() -> None:
    task_case = TaskCase(task_id="task-1", task_description="Turn off cellular", milestone_graph=None)
    checkpoint = HarnessStageSettlement(
        settlement_id="st1",
        kind="milestone",
        milestone_id="m1",
        start_step_index=0,
        end_step_index=2,
        metadata={"milestone_matching": {"score": {"score": 0.899, "status": "pass"}}},
    )
    stage_result = StageEvaluationResult(
        stage_id="runtime:st1",
        milestone_id="m1",
        evaluator_level=EvaluationLevel.STANDARD,
        status=StageStatus.FAIL,
        stage_score=0.2,
        uncertainty=0.0,
        dimension_scores={dimension: 0.2 for dimension in Dimension},
        metadata={},
    )
    decision = RuntimeEvaluationDecision(
        checkpoint=checkpoint,
        stage_result=stage_result,
        next_state=_state(),
        should_stop=True,
        termination_code="stage_failure:m1",
        termination_reason="阶段失败",
    )

    extra = policy_stop_log_extra("case-1", task_case, decision, "stage_failure:m1", "阶段失败")

    assert extra["milestone_score"] == pytest.approx(0.899)
    assert extra["milestone_status"] == "pass"


def test_pending_milestones_log_extra_reports_blocker_and_best_score() -> None:
    diagnostics = [
        {
            "milestone_id": "m3",
            "required": True,
            "final_state": "pending",
            "blocker": "attempted_but_not_pass",
            "best_score": 0.0,
            "best_status": "fail",
            "best_boundary_step_index": 17,
            "last_reject_reason": "status_not_pass",
            "pending_predecessor_ids": [],
        },
        {
            "milestone_id": "m4",
            "required": True,
            "final_state": "pending",
            "blocker": "predecessor_not_matched",
            "pending_predecessor_ids": ["m3"],
        },
    ]

    extra = pending_milestones_log_extra("case-1", diagnostics)

    assert extra["case_id"] == "case-1"
    assert extra["pending_required_milestone_ids"] == ["m3", "m4"]
    assert extra["pending_milestone_count"] == 2
    assert extra["first_pending_milestone_id"] == "m3"
    assert extra["blocker"] == "attempted_but_not_pass"
    assert extra["best_score"] == pytest.approx(0.0)
    assert extra["best_status"] == "fail"
    assert extra["best_boundary_step_index"] == 17
    assert extra["last_reject_reason"] == "status_not_pass"
    assert extra["pending_predecessor_ids"] == []


def test_log_extra_truncates_large_values() -> None:
    extra = pending_milestones_log_extra(
        "case-1",
        [
            {
                "milestone_id": "m1",
                "required": True,
                "final_state": "pending",
                "blocker": "x" * 500,
            }
        ],
    )

    assert isinstance(extra["blocker"], str)
    assert len(extra["blocker"]) < 200
    assert extra["blocker"].endswith("...")
