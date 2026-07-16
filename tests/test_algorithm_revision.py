from __future__ import annotations

import json

import pytest

from dynsteer.adapter.loader import parse_milestone
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier, ready_milestone_ids
from dynsteer.evaluate.policy import update_evaluation_policy
from dynsteer.evaluate.scoring import GeneralScorer, update_weights
from dynsteer.judges.expensive import ExpensiveJudge
from dynsteer.judges.standard import StandardJudge
from dynsteer.llm.base import BaseLLM
from dynsteer.model import (
    Actor,
    Boundary,
    Constraint,
    ConstraintTarget,
    Dimension,
    EventType,
    EvaluationLevel,
    LLMConfig,
    LLMMessage,
    Milestone,
    MilestoneGraph,
    Operator,
    ScoringContext,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
    initial_evaluation_policy,
)
from dynsteer.prompt.judge import build_judge_prompt


def test_loader_rejects_legacy_required_milestone_field() -> None:
    payload = {
        "milestone_id": "m1",
        "name": "m1",
        "description": "legacy required field",
        "required": False,
        "constraints": [],
    }

    with pytest.raises(ValueError, match="required"):
        parse_milestone(payload)


def test_ready_frontier_tracks_all_parallel_milestones() -> None:
    graph = MilestoneGraph(
        nodes=[
            _milestone("m1"),
            _milestone("m2"),
        ],
        edges=[],
    )
    frontier = initialize_milestone_frontier(graph)

    assert ready_milestone_ids(frontier, matched={}) == ("m1", "m2")


def test_delta_operator_uses_matched_snapshot_reference() -> None:
    constraint = Constraint(
        constraint_id="c1",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.orders.order_1",
        operator=Operator.ADDED,
        reference_milestone_id="m0",
        hard=True,
    )
    milestone = _milestone("m1", constraints=[constraint])
    current_snapshot = StateSnapshot(
        snapshot_id="s1",
        after_step_id="s1",
        after_step_index=1,
        namespaces={"default": {"orders": {"order_1": {"status": "created"}}}},
    )
    reference_snapshot = StateSnapshot(
        snapshot_id="snapshot-not-equal-to-milestone-id",
        after_step_id="s0",
        after_step_index=0,
        namespaces={"default": {"orders": {}}},
    )
    trajectory = Trajectory(
        run_id="r1",
        task_id="t1",
        steps=[_step(1)],
        snapshots=[current_snapshot],
    )
    boundary = Boundary(boundary_id="b1", step_index=1, snapshot_id="s1", reason="test")
    context = ScoringContext(matched_snapshots={"m0": reference_snapshot})

    score = GeneralScorer().score_milestone(
        milestone,
        boundary,
        trajectory,
        reference_snapshots=trajectory.snapshots,
        context=context,
    )

    assert score.status == StageStatus.PASS
    assert score.constraint_scores[0].score == 1.0


def test_update_weights_increases_low_score_and_high_uncertainty_dimensions() -> None:
    current = {dimension: 1.0 / len(Dimension) for dimension in Dimension}
    scores = {dimension: 0.9 for dimension in Dimension}
    scores[Dimension.TOOL_QUALITY] = 0.2
    uncertainty = {dimension: 0.1 for dimension in Dimension}
    uncertainty[Dimension.RECOVERY] = 0.8

    updated = update_weights(current, scores, uncertainty)

    assert updated[Dimension.TOOL_QUALITY] > current[Dimension.TOOL_QUALITY]
    assert updated[Dimension.RECOVERY] > current[Dimension.RECOVERY]


def test_prompt_context_contains_only_requested_rubrics_and_constraint_checks() -> None:
    prompt = build_judge_prompt(
        "standard",
        _interval(),
        _task_case(),
        _trajectory(),
        _weights(),
        target_dimensions=[Dimension.TOOL_QUALITY, Dimension.RECOVERY],
    )
    context = json.loads(prompt.split("Context:\n", 1)[1])

    assert set(context["rubrics"]) == {"tool_quality", "recovery"}
    assert "requested_dimensions" not in context
    assert "structured_milestone_evidence" not in context
    assert "constraint_checks" in context


def test_standard_judge_accepts_partial_dimension_scores() -> None:
    llm = _FakeLLM(
        {
            "status": "pass",
            "dimension_scores": {"tool_quality": 0.8},
            "evidence": ["step 1 tool_call"],
            "diagnosis": ["tool_quality: ok"],
        }
    )
    judge = StandardJudge(llm=llm, standard_passes=1)

    result = judge.evaluate_stage(
        _interval(),
        _task_case(),
        _trajectory(),
        _weights(),
        dimensions=[Dimension.TOOL_QUALITY],
    )

    assert set(result.dimension_scores) == {Dimension.TOOL_QUALITY}
    assert result.dimension_levels == {Dimension.TOOL_QUALITY: EvaluationLevel.STANDARD}
    assert Dimension.TOOL_QUALITY in result.dimension_confidence
    assert "evaluator_level" not in result.to_dict()


def test_expensive_judge_uses_single_dimension_prompt() -> None:
    llm = _FakeLLM(
        {
            "status": "pass",
            "dimension_scores": {"progress": 0.9},
            "evidence": ["step 1 completed"],
            "diagnosis": ["progress: ok"],
        }
    )
    judge = ExpensiveJudge(llm=llm, expensive_passes=1)

    result = judge.evaluate_stage(
        _interval(),
        _task_case(),
        _trajectory(),
        _weights(),
        dimensions=[Dimension.PROGRESS],
    )
    context = json.loads(llm.prompts[0].split("Context:\n", 1)[1])

    assert set(result.dimension_scores) == {Dimension.PROGRESS}
    assert result.dimension_levels == {Dimension.PROGRESS: EvaluationLevel.EXPENSIVE}
    assert set(context["rubrics"]) == {"progress"}


def test_policy_update_returns_next_policy_and_termination_state() -> None:
    result = StageEvaluationResult(
        stage_id="__start__->m1",
        milestone_id="m1",
        status=StageStatus.FAIL,
        stage_score=0.0,
        dimension_scores={dimension: 0.0 for dimension in Dimension},
        dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in Dimension},
        dimension_confidence={dimension: 0.9 for dimension in Dimension},
        dimension_uncertainty={dimension: 0.1 for dimension in Dimension},
    )

    next_policy, termination = update_evaluation_policy(initial_evaluation_policy(), result)

    assert next_policy.reason
    assert termination.should_stop is True
    assert termination.termination_code == "evaluation_policy_stop"


def _milestone(milestone_id: str, constraints: list[Constraint] | None = None) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description=milestone_id,
        constraints=constraints or [
            Constraint(
                constraint_id=f"{milestone_id}_c1",
                target=ConstraintTarget.STEP,
                selector="$",
                operator=Operator.CUSTOM,
            )
        ],
        stage_anchor_predecessor_id="__start__",
    )


def _step(index: int = 1) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content="done",
    )


def _trajectory() -> Trajectory:
    return Trajectory(run_id="r1", task_id="t1", steps=[_step(1)])


def _interval() -> StageInterval:
    return StageInterval(
        stage_id="__start__->m1",
        milestone_id="m1",
        stage_anchor_milestone_id="__start__",
        start_boundary_step_index=0,
        start_step_index=1,
        end_step_index=1,
        status=StageStatus.PASS,
        evidence=["interval evidence"],
    )


def _task_case() -> TaskCase:
    return TaskCase(
        task_id="t1",
        task_description="Complete the task.",
        case_id="case1",
        milestone_graph=MilestoneGraph(nodes=[_milestone("m1")]),
        stage_goals={"__start__->m1": "Complete milestone m1."},
    )


def _weights() -> dict[Dimension, float]:
    return {dimension: 1.0 / len(Dimension) for dimension in Dimension}


class _FakeLLM(BaseLLM):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(LLMConfig(provider="fake", model="fake"))
        self._payload = payload
        self.prompts: list[str] = []

    def _get_response_from_client(
        self,
        client: object,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        self.prompts.append(messages[-1].content)
        return json.dumps(self._payload)

    def _create_client(self) -> object:
        return object()

    def _response_text(self, response: object) -> str:
        return str(response)
