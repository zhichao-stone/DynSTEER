from __future__ import annotations

from types import MethodType

import pytest

from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.policy import EvaluationPolicyUpdate, initial_evaluation_policy
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.evaluate.runtime import RuntimeEvaluationState, pending_required_stage_results
from dynsteer.harness.model import HarnessAdvanceResult, HarnessRunConfig, HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Boundary,
    Dimension,
    EvaluationLevel,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def test_pending_required_stage_id_uses_stage_goal_key() -> None:
    task_case = _task_case()
    state = RuntimeEvaluationState(
        weights=_weights(),
        settlements=[],
        matched_settlements={"m3": _matched_settlement("m3", 9)},
        stage_reports=[],
        match_attempts=[
            {
                "ready_before": ["m4"],
                "candidate_scores": [
                    {
                        "milestone_id": "m4",
                        "boundary": {"step_index": 12},
                        "score": {"score": 0.2, "status": "fail"},
                        "reject_reason": "below_threshold",
                    }
                ],
            }
        ],
    )

    results = pending_required_stage_results(task_case, state)

    assert len(results) == 1
    assert results[0].stage_id == "m3->m4"
    assert results[0].status == StageStatus.FAIL
    assert "runtime:" not in results[0].stage_id
    assert results[0].metadata["stage_anchor_milestone_id"] == "m3"


def test_pending_required_stage_id_requires_anchor() -> None:
    task_case = _task_case()
    task_case.milestone_graph.nodes[-1].stage_anchor_predecessor_id = None
    state = RuntimeEvaluationState(
        weights=_weights(),
        settlements=[],
        matched_settlements={"m3": _matched_settlement("m3", 9)},
        stage_reports=[],
        match_attempts=[],
    )

    with pytest.raises(ValueError, match="stage_anchor_predecessor_id"):
        pending_required_stage_results(task_case, state)


def test_milestone_stage_id_uses_anchor_to_milestone_key() -> None:
    evaluator = DynSTEEREvaluator()
    evaluator._evaluate_stage = MethodType(_fake_evaluate_stage, evaluator)  # type: ignore[method-assign]
    task_case = _task_case()
    trajectory = _trajectory()
    milestone = task_case.milestone_graph.nodes[-1]

    settlement, stage_result, _, _ = evaluator._append_milestone_settlement(
        settlements=[],
        matched={"m3": _matched_settlement("m3", 2)},
        task_case=task_case,
        trajectory=trajectory,
        milestone=milestone,
        boundary=Boundary(boundary_id="b3", step_index=3, snapshot_id=None, reason="test"),
        milestone_score=MilestoneScore(
            milestone_id="m4",
            boundary_id="b3",
            score=1.0,
            status=StageStatus.PASS,
            evidence=["matched"],
        ),
        weights=_weights(),
        evaluation_policy=initial_evaluation_policy(),
        scorer=None,  # type: ignore[arg-type]
    )

    assert settlement.metadata["stage_report"]["stage_id"] == "m3->m4"
    assert stage_result.stage_id == "m3->m4"
    assert "runtime:" not in stage_result.stage_id


def test_finish_stage_id_uses_finish_anchor_key() -> None:
    evaluator = DynSTEEREvaluator()
    evaluator._evaluate_stage = MethodType(_fake_evaluate_stage, evaluator)  # type: ignore[method-assign]
    task_case = _task_case()

    _, stage_result, _, _ = evaluator._finish_settlement(
        settlements=[],
        task_case=task_case,
        trajectory=_trajectory(),
        matched={"m4": _matched_settlement("m4", 2)},
        weights=_weights(),
        evaluation_policy=initial_evaluation_policy(),
        scorer=None,  # type: ignore[arg-type]
    )

    assert stage_result.stage_id == "m4->__finish__"
    assert stage_result.milestone_id == "__finish__"
    assert "runtime:" not in stage_result.stage_id


def test_evaluate_skips_finish_when_required_milestone_is_pending(tmp_path) -> None:
    evaluator = DynSTEEREvaluator()
    task_case = _task_case()
    harness = _NaturalEndHarness()

    result = evaluator.evaluate(
        harness=harness,
        config=HarnessRunConfig(
            benchmark="testbench",
            data_root=tmp_path,
            runs_dir=tmp_path / "runs",
            results_dir=tmp_path / "results",
            metadata={"run_id": "run"},
        ),
        task_case=task_case,
    )

    assert [stage.stage_id for stage in result.evaluation_report.stage_reports] == ["__start__->m3", "m3->m4"]
    assert all(stage.milestone_id != "__finish__" for stage in result.evaluation_report.stage_reports)
    assert all(settlement.kind != "finish" for settlement in result.stage_settlements)


def _fake_evaluate_stage(
    self: DynSTEEREvaluator,
    interval,
    task_case,
    trajectory,
    weights,
    scorer,
    evaluation_policy,
):
    result = StageEvaluationResult(
        stage_id=interval.stage_id,
        milestone_id=interval.milestone_id,
        evaluator_level=EvaluationLevel.CHEAP,
        status=interval.status,
        stage_score=1.0,
        uncertainty=0.0,
        dimension_scores=_weights(),
        evidence=list(interval.evidence),
    )
    update = EvaluationPolicyUpdate(
        current_policy=evaluation_policy,
        next_policy=evaluation_policy,
        should_stop=False,
        termination_code=None,
        termination_reason=None,
    )
    return result, dict(weights), update


def _task_case() -> TaskCase:
    graph = MilestoneGraph(
        nodes=[
            Milestone(
                milestone_id="m3",
                name="M3",
                description="Complete m3",
                constraints=[],
                required=True,
                stage_anchor_predecessor_id="__start__",
            ),
            Milestone(
                milestone_id="m4",
                name="M4",
                description="Complete m4",
                constraints=[],
                required=True,
                dependency_predecessor_ids=["m3"],
                stage_anchor_predecessor_id="m3",
            ),
        ],
        edges=[("m3", "m4")],
        metadata={
            "graph_analysis": {
                "finish_node_id": "__finish__",
                "finish_stage_anchor_predecessor_id": "m4",
            }
        },
    )
    return TaskCase(
        task_id="task",
        task_description="test task",
        case_id="case",
        milestone_graph=graph,
        stage_goals={"__start__->m3": "Complete m3", "m3->m4": "Complete m4"},
    )


def _trajectory() -> Trajectory:
    return Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            TrajectoryStep(step_id="s1", index=1, actor=Actor.USER, event_type=EventType.MESSAGE, content="start"),
            TrajectoryStep(step_id="s2", index=2, actor=Actor.AGENT, event_type=EventType.MESSAGE, content="m3"),
            TrajectoryStep(step_id="s3", index=3, actor=Actor.AGENT, event_type=EventType.FINAL, content="done"),
        ],
    )


def _matched_settlement(milestone_id: str, end_step_index: int) -> HarnessStageSettlement:
    return HarnessStageSettlement(
        settlement_id=f"st-{milestone_id}",
        kind="milestone",
        milestone_id=milestone_id,
        start_step_index=end_step_index,
        end_step_index=end_step_index,
        boundary_step_index=end_step_index,
        score=1.0,
        status="pass",
    )


def _weights() -> dict[Dimension, float]:
    return {dimension: 1.0 for dimension in Dimension}


class _NaturalEndHarness:
    benchmark = "testbench"

    def prepare_config(self, config: HarnessRunConfig) -> None:
        pass

    def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        return str(config.metadata.get("run_id") or "run")

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir) -> object:
        return {"advanced": False}

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        if isinstance(session, dict) and not session["advanced"]:
            session["advanced"] = True
            return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)
        raise AssertionError("advance_case should only be called once")

    def final_state_from_session(self, session: object):
        return {}

    def metrics_from_session(self, session: object):
        return {}

    def raw_summary_from_session(self, session: object):
        return {}

    def teardown_case(self, session: object) -> None:
        pass

    def stop_case(self, session: object, reason: str) -> None:
        raise AssertionError("natural pending required should not call stop_case after session already ended")

    def constraint_scorer(self):
        return GeneralScorer()
