from __future__ import annotations

import json
from pathlib import Path

import pytest

from display.build import build_display_data
from dynsteer.adapter.loader import parse_milestone
from dynsteer.evaluate.final import build_finish_verification
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier, ready_milestone_ids
from dynsteer.evaluate.policy import update_evaluation_policy
from dynsteer.evaluate.scoring import GeneralScorer, update_weights
from dynsteer.harness.model import HarnessStageSettlement
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
    RuntimeEvaluationState,
    StageEvaluationResult,
    StageGoalSemanticKind,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
    initial_evaluation_policy,
)
from dynsteer.prompt.judge import build_judge_prompt
from dynsteer.stage import generate_stage_evaluation_specs
from dynsteer.utils import clean_evidence_items


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


def test_stage_evaluation_specs_focus_on_state_tool_without_interaction() -> None:
    tool_constraint = Constraint(
        constraint_id="m1_tool",
        target=ConstraintTarget.TOOL_CALL,
        selector="$.name",
        operator=Operator.EQUALS,
        expected="set_wifi",
        stage_goal_semantics={"kind": StageGoalSemanticKind.TOOL_CALL.value, "tool_name": "set_wifi"},
    )
    state_constraint = Constraint(
        constraint_id="m1_state",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.wifi.enabled",
        operator=Operator.EQUALS,
        expected=True,
        stage_goal_semantics={"kind": StageGoalSemanticKind.SET_STATE.value, "namespace": "wifi"},
    )
    task_case = _task_case_with_milestone(
        _milestone("m1", constraints=[tool_constraint, state_constraint]),
        stage_goal="Fix issue by enabling WiFi.",
    )

    specs = generate_stage_evaluation_specs(task_case)
    focus = set(specs["__start__->m1"].focus_dimensions)

    assert {Dimension.PROGRESS, Dimension.EFFICIENCY, Dimension.TOOL_QUALITY, Dimension.STATE_CONSISTENCY, Dimension.RECOVERY}.issubset(focus)
    assert Dimension.INTERACTION_QUALITY not in focus


def test_stage_evaluation_specs_include_interaction_for_user_message() -> None:
    message_constraint = Constraint(
        constraint_id="m1_message",
        target=ConstraintTarget.STEP,
        selector="$.content",
        operator=Operator.CONTAINS,
        expected="done",
        stage_goal_semantics={
            "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
            "user_visible_required": True,
        },
    )
    task_case = _task_case_with_milestone(_milestone("m1", constraints=[message_constraint]))

    specs = generate_stage_evaluation_specs(task_case)

    assert Dimension.INTERACTION_QUALITY in specs["__start__->m1"].focus_dimensions


def test_policy_does_not_stop_on_quality_status_fail_when_score_passes() -> None:
    result = StageEvaluationResult(
        stage_id="__start__->m1",
        milestone_id="m1",
        status=StageStatus.FAIL,
        stage_score=0.86,
        dimension_scores={Dimension.PROGRESS: 0.95, Dimension.INTERACTION_QUALITY: 0.2},
        dimension_levels={Dimension.PROGRESS: EvaluationLevel.CHEAP, Dimension.INTERACTION_QUALITY: EvaluationLevel.EXPENSIVE},
        dimension_confidence={Dimension.PROGRESS: 0.9, Dimension.INTERACTION_QUALITY: 0.8},
        dimension_uncertainty={Dimension.PROGRESS: 0.1, Dimension.INTERACTION_QUALITY: 0.2},
        metadata={"low_score_dimensions": [{"dimension": "interaction_quality"}]},
    )

    next_policy, termination = update_evaluation_policy(initial_evaluation_policy(), result)

    assert termination.should_stop is False
    assert next_policy.dimension_levels[Dimension.INTERACTION_QUALITY] == EvaluationLevel.EXPENSIVE


def test_finish_verification_passes_when_all_milestones_and_terminal_state_hold() -> None:
    task_case = _finish_task_case()
    trajectory = _finish_trajectory(enabled=True)
    matched = _matched_settlements()

    payload = build_finish_verification(
        task_case,
        trajectory,
        matched,
        _runtime_state(task_case.milestone_graph),
        GeneralScorer(),
    )

    assert payload["status"] == StageStatus.PASS.value
    assert payload["all_milestones_matched"] is True
    assert any("user -> environment calls end_conversation" in item for item in payload["evidence"])


def test_finish_verification_skips_terminal_emit_message_recheck() -> None:
    task_case = _finish_task_case_with_message_constraint()
    trajectory = _finish_trajectory(enabled=True)
    matched = _matched_settlements()

    payload = build_finish_verification(
        task_case,
        trajectory,
        matched,
        _runtime_state(task_case.milestone_graph),
        GeneralScorer(),
    )

    assert payload["status"] == StageStatus.PASS.value
    assert payload["terminal_state_checks"][0]["status"] == StageStatus.PASS.value
    assert payload["terminal_message_checks"][0]["constraint_ids"] == ["m1_message"]
    assert payload["terminal_message_checks"][0]["recheck_skipped"] is True


def test_finish_verification_fails_when_terminal_state_is_broken() -> None:
    task_case = _finish_task_case()
    trajectory = _finish_trajectory(enabled=False)
    matched = _matched_settlements()

    payload = build_finish_verification(
        task_case,
        trajectory,
        matched,
        _runtime_state(task_case.milestone_graph),
        GeneralScorer(),
    )

    assert payload["status"] == StageStatus.FAIL.value
    assert payload["terminal_state_checks"][0]["status"] == StageStatus.FAIL.value


def test_display_build_keeps_finish_definition_after_anchor_failure(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    results_dir = tmp_path / "results"
    data_dir = tmp_path / "data"
    case_dir = runs_dir / "bench" / "run1" / "case1"
    result_dir = results_dir / "bench" / "run1" / "case1"
    adapted_dir = data_dir / "bench" / "adapted_cases"
    case_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    adapted_dir.mkdir(parents=True)

    _write_json(case_dir / "trajectory.json", {"task_id": "task1", "steps": []})
    _write_json(case_dir / "raw_summary.json", {"stage_settlements": []})
    _write_json(result_dir / "summary.json", {"task_id": "task1", "overall_score": 0.2, "milestone_coverage": "partial"})
    _write_json(
        result_dir / "report.json",
        {
            "stage_reports": [
                {
                    "stage_id": "m3->m4",
                    "milestone_id": "m4",
                    "status": "fail",
                    "stage_score": 0.1,
                    "dimension_scores": {},
                    "dimension_levels": {},
                    "dimension_confidence": {},
                    "dimension_uncertainty": {},
                    "metadata": {},
                }
            ],
            "minefield_matches": [],
        },
    )
    _write_json(
        adapted_dir / "case1.json",
        {
            "stage_goals": {"m3->m4": "完成 m4"},
            "milestone_graph": {
                "nodes": [
                    {
                        "milestone_id": "m4",
                        "name": "m4",
                        "description": "terminal milestone",
                        "stage_anchor_predecessor_id": "m3",
                        "constraints": [],
                    }
                ],
                "metadata": {
                    "graph_analysis": {
                        "start_node_id": "__start__",
                        "finish_node_id": "__finish__",
                        "finish_stage_anchor_predecessor_id": "m4",
                        "augmented_edges": [["m3", "m4"], ["m4", "__finish__"]],
                    }
                },
            },
        },
    )

    data = build_display_data(runs_dir, results_dir, data_dir)
    scenario = data["runs"][0]["scenarios"][0]
    stage_ids = [definition["stage_id"] for definition in scenario["stage_definitions"]]

    assert stage_ids == ["m3->m4", "m4->__finish__"]


def test_clean_evidence_items_removes_bare_step_duplicates() -> None:
    assert clean_evidence_items(["step 3", "step 3: agent -> user sends done", "step 4-5", "step 5: user -> environment calls end_conversation"]) == [
        "step 3: agent -> user sends done",
        "step 5: user -> environment calls end_conversation",
    ]


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


def _task_case_with_milestone(milestone: Milestone, stage_goal: str = "Complete milestone m1.") -> TaskCase:
    return TaskCase(
        task_id="t1",
        task_description="Complete the task.",
        case_id="case1",
        milestone_graph=MilestoneGraph(nodes=[milestone]),
        stage_goals={f"{milestone.stage_anchor_predecessor_id}->{milestone.milestone_id}": stage_goal},
    )


def _finish_task_case() -> TaskCase:
    constraint = Constraint(
        constraint_id="m1_state",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.wifi.enabled",
        operator=Operator.EQUALS,
        expected=True,
        hard=True,
        stage_goal_semantics={"kind": StageGoalSemanticKind.SET_STATE.value, "namespace": "wifi"},
    )
    milestone = _milestone("m1", constraints=[constraint])
    return _task_case_with_milestone(milestone)


def _finish_task_case_with_message_constraint() -> TaskCase:
    state_constraint = Constraint(
        constraint_id="m1_state",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.wifi.enabled",
        operator=Operator.EQUALS,
        expected=True,
        hard=True,
        stage_goal_semantics={"kind": StageGoalSemanticKind.SET_STATE.value, "namespace": "wifi"},
    )
    message_constraint = Constraint(
        constraint_id="m1_message",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"sender": "AGENT", "recipient": "USER", "content": "done"}]},
        namespace="SANDBOX",
        hard=True,
        stage_goal_semantics={
            "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
            "user_visible_required": True,
        },
    )
    milestone = _milestone("m1", constraints=[message_constraint, state_constraint])
    return _task_case_with_milestone(milestone)


def _finish_trajectory(enabled: bool) -> Trajectory:
    return Trajectory(
        run_id="r1",
        task_id="t1",
        steps=[
            TrajectoryStep(
                step_id="s1",
                index=1,
                actor=Actor.AGENT,
                recipient=Actor.ENVIRONMENT,
                event_type=EventType.TOOL_CALL,
                content="set_wifi",
            ),
            TrajectoryStep(
                step_id="s2",
                index=2,
                actor=Actor.USER,
                recipient=Actor.ENVIRONMENT,
                event_type=EventType.MESSAGE,
                content="end_conversation",
            ),
        ],
        snapshots=[
            StateSnapshot(
                snapshot_id="snap2",
                after_step_id="s2",
                after_step_index=2,
                namespaces={"default": {"wifi": {"enabled": enabled}}},
            )
        ],
    )


def _matched_settlements() -> dict[str, HarnessStageSettlement]:
    return {
        "m1": HarnessStageSettlement(
            settlement_id="st1",
            kind="milestone",
            milestone_id="m1",
            start_step_index=1,
            end_step_index=1,
            boundary_id="b1",
            boundary_step_index=1,
            score=1.0,
            status=StageStatus.PASS.value,
        )
    }


def _runtime_state(graph: MilestoneGraph) -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={dimension: 1.0 / len(Dimension) for dimension in Dimension},
        settlements=[],
        matched_settlements=_matched_settlements(),
        stage_reports=[],
        match_attempts=[],
        milestone_frontier=initialize_milestone_frontier(graph),
    )


def _weights() -> dict[Dimension, float]:
    return {dimension: 1.0 / len(Dimension) for dimension in Dimension}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
