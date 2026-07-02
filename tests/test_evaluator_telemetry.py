from __future__ import annotations

import pytest

from dynsteer.evaluate.models import RuntimeEvaluationDecision, RuntimeEvaluationState
from dynsteer.evaluate.telemetry import (
    milestone_checkpoint_log_extra,
    policy_stop_log_extra,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    ConstraintScore,
    Dimension,
    EvaluationLevel,
    Milestone,
    MilestoneScore,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
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
    task_case = TaskCase(task_id="task-1", task_description="Turn off cellular", case_id="case-1",
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
    task_case = TaskCase(task_id="task-1", task_description="Turn off cellular", case_id="case-1", milestone_graph=None)
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
