from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.scoring import GeneralScorer, overall_score
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_raw_step_minefields
from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.metrics import build_runtime_metrics
from dynsteer.model import (
    Actor,
    AgentStepProtocolError,
    AgentStepTracker,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    StageStatus,
    TaskCase,
    ThresholdConfig,
    ToolCall,
    ToolResult,
    Trajectory,
    TrajectoryStep,
)


def test_agent_step_tracker_closes_tool_and_dialogue_steps() -> None:
    tracker = AgentStepTracker()
    tool_call = _step(1, Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, tool_call=ToolCall("get_city", {}))
    tool_result = _step(2, Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, tool_result=ToolResult(True))

    assert tracker.ingest(tool_call) is None
    assert tracker.completed_count == 0
    assert tracker.ingest(tool_result) is tool_result
    assert tracker.pending_outbound is None
    assert tracker.completed_count == 1

    message = _step(3, Actor.AGENT, Actor.USER, EventType.MESSAGE)
    user_reply = _step(4, Actor.USER, Actor.AGENT, EventType.MESSAGE)
    assert tracker.ingest(message) is None
    assert tracker.ingest(user_reply) is user_reply
    assert tracker.completed_count == 2


def test_agent_step_tracker_handles_finalize_and_protocol_errors() -> None:
    tracker = AgentStepTracker()
    terminal = _step(1, Actor.AGENT, Actor.USER, EventType.MESSAGE)
    assert tracker.ingest(terminal) is None
    assert tracker.finalize() is terminal
    assert tracker.completed_count == 1

    tool_tracker = AgentStepTracker()
    tool_call = _step(2, Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, tool_call=ToolCall("set_flag", {}))
    assert tool_tracker.ingest(tool_call) is None
    assert tool_tracker.finalize() is None
    assert tool_tracker.completed_count == 0
    assert tool_tracker.pending_outbound is tool_call

    with pytest.raises(AgentStepProtocolError):
        tool_tracker.ingest(_step(3, Actor.AGENT, Actor.USER, EventType.MESSAGE))


def test_unclosed_outbound_does_not_create_match_attempt_or_no_progress() -> None:
    graph = _graph(_tool_milestone("m0", "__start__", "target_tool"))
    task_case = _task_case(graph)
    state = _state(graph)
    trajectory = _trajectory()
    config = _config(ready_frontier_patience=1)
    outbound = _step(
        1,
        Actor.AGENT,
        Actor.ENVIRONMENT,
        EventType.TOOL_CALL,
        tool_call=ToolCall("other_tool", {}),
    )

    trajectory.append_step(outbound)
    assert evaluate_raw_step_minefields(config, task_case, trajectory, state, outbound, GeneralScorer()) is None
    assert state.agent_step_tracker.ingest(outbound) is None

    assert state.agent_step_tracker.completed_count == 0
    assert state.match_attempts == []
    assert state.ready_frontier_progress_watch is None


def test_tool_call_milestone_matches_on_tool_result_closure() -> None:
    graph = _graph(_tool_milestone("m0", "__start__", "target_tool"))
    task_case = _task_case(graph)
    state = _state(graph)
    trajectory = _trajectory()
    config = _config()
    outbound = _step(
        1,
        Actor.AGENT,
        Actor.ENVIRONMENT,
        EventType.TOOL_CALL,
        tool_call=ToolCall("target_tool", {}),
    )
    feedback = _step(2, Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, tool_result=ToolResult(True))
    trajectory.append_step(outbound)
    trajectory.append_step(feedback)
    assert state.agent_step_tracker.ingest(outbound) is None
    assert state.agent_step_tracker.ingest(feedback) is feedback

    checkpointed: dict[str, str] = {}
    decision = evaluate_agent_step(
        config=config,
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        step=feedback,
        scorer=GeneralScorer(),
        standard_judge=None,
        thresholds=ThresholdConfig(),
        evaluate_checkpoint=lambda **kwargs: _checkpoint(kwargs, state, checkpointed),
    )

    assert decision is None
    assert checkpointed == {"milestone_id": "m0"}
    assert state.match_attempts[0]["selected_milestone_id"] == "m0"


def test_ready_frontier_no_progress_counts_completed_agent_steps_only() -> None:
    graph = _graph(_tool_milestone("m0", "__start__", "target_tool"))
    task_case = _task_case(graph)
    state = _state(graph)
    trajectory = _trajectory()
    config = _config(ready_frontier_patience=1)

    first_feedback = _append_pair(trajectory, state.agent_step_tracker, 1, "other_tool")
    assert first_feedback is not None
    first_decision = evaluate_agent_step(
        config,
        task_case,
        trajectory,
        state,
        first_feedback,
        GeneralScorer(),
        None,
        ThresholdConfig(),
        lambda **kwargs: RuntimeEvaluationDecision(None, None, state),
    )
    assert first_decision is None
    assert state.agent_step_tracker.completed_count == 1
    assert state.ready_frontier_progress_watch is not None
    assert state.ready_frontier_progress_watch.stale_frontier_observation_count == 0

    second_feedback = _append_pair(trajectory, state.agent_step_tracker, 3, "other_tool")
    assert second_feedback is not None
    second_decision = evaluate_agent_step(
        config,
        task_case,
        trajectory,
        state,
        second_feedback,
        GeneralScorer(),
        None,
        ThresholdConfig(),
        lambda **kwargs: RuntimeEvaluationDecision(None, None, state),
    )

    assert second_decision is not None
    assert second_decision.termination.should_stop is True
    assert second_decision.termination.termination_code == "milestone_no_progress:m0"
    assert state.agent_step_tracker.completed_count == 2


def test_fatal_minefield_guard_runs_before_agent_step_counting() -> None:
    graph = MilestoneGraph(
        nodes=[],
        minefields=[
            Minefield(
                "mf0",
                "dangerous tool",
                "dangerous tool",
                "fatal",
                [Constraint("mf0_c0", ConstraintTarget.TOOL_CALL, "$.name", Operator.EQUALS, "delete_all")],
                MinefieldPenalty("fixed", 1.0),
            )
        ],
    )
    task_case = _task_case(graph)
    state = _state(graph)
    trajectory = _trajectory()
    outbound = _step(
        1,
        Actor.AGENT,
        Actor.ENVIRONMENT,
        EventType.TOOL_CALL,
        tool_call=ToolCall("delete_all", {}),
    )
    trajectory.append_step(outbound)

    decision = evaluate_raw_step_minefields(_config(), task_case, trajectory, state, outbound, GeneralScorer())

    assert decision is not None
    assert decision.termination.should_stop is True
    assert state.agent_step_tracker.completed_count == 0
    assert state.match_attempts == []


def test_pending_stage_helper_includes_policy_terminated_cases_once() -> None:
    graph = _graph(
        _tool_milestone("m0", "__start__", "missing_tool"),
        _tool_milestone("m1", "m0", "missing_tool", predecessors=["m0"]),
    )
    task_case = _task_case(graph)
    state = _state(graph)
    state.match_attempts.append(
        {
            "step_index": 1,
            "ready_before": ["m0"],
            "candidate_scores": [
                {
                    "milestone_id": "m0",
                    "boundary": {"boundary_id": "b1", "step_index": 1},
                    "score": {"score": 0.0, "status": StageStatus.FAIL.value},
                }
            ],
        }
    )
    evaluator = DynSTEEREvaluator()

    pending = evaluator._new_pending_stage_reports(task_case, state)
    state.stage_reports.append(pending[0])
    deduplicated = evaluator._new_pending_stage_reports(task_case, state)

    assert [item.milestone_id for item in pending] == ["m0", "m1"]
    assert pending[0].status == StageStatus.FAIL
    assert pending[1].status == StageStatus.MISSING
    assert [item.milestone_id for item in deduplicated] == ["m1"]


def test_runtime_metrics_separate_agent_and_raw_step_counts() -> None:
    trajectory = _trajectory()
    trajectory.append_step(_step(1, Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, ToolCall("x", {})))
    trajectory.append_step(_step(2, Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, tool_result=ToolResult(True)))

    metrics = build_runtime_metrics(
        started_monotonic=1.0,
        finished_monotonic=3.5,
        started_at="start",
        finished_at="finish",
        trajectory=trajectory,
        llm_calls=[],
        agent_step_count=1,
    )

    assert metrics["step_count"] == 1
    assert metrics["raw_step_count"] == 2
    assert metrics["tool_call_count"] == 1


def test_empty_stage_reports_score_zero() -> None:
    assert overall_score([], 0.0) == 0.0
    assert overall_score([], 1.0) == 0.0


def _config(ready_frontier_patience: int = 8) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=Path("."),
        ready_frontier_patience=ready_frontier_patience,
    )


def _task_case(graph: MilestoneGraph) -> TaskCase:
    return TaskCase(
        task_id="task-1",
        task_description="test task",
        case_id="case-1",
        milestone_graph=graph,
    )


def _state(graph: MilestoneGraph) -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={},
        settlements=[],
        matched_settlements={},
        stage_reports=[],
        match_attempts=[],
        milestone_frontier=initialize_milestone_frontier(graph),
    )


def _trajectory() -> Trajectory:
    return Trajectory(run_id="run-1", task_id="task-1", steps=[])


def _graph(*milestones: Milestone) -> MilestoneGraph:
    edges = [
        (predecessor_id, milestone.milestone_id)
        for milestone in milestones
        for predecessor_id in milestone.dependency_predecessor_ids
    ]
    return MilestoneGraph(nodes=list(milestones), edges=edges)


def _tool_milestone(
    milestone_id: str,
    anchor_id: str,
    tool_name: str,
    predecessors: list[str] | None = None,
) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description=milestone_id,
        constraints=[
            Constraint(
                constraint_id=f"{milestone_id}_c0",
                target=ConstraintTarget.TOOL_CALL,
                selector="$.name",
                operator=Operator.EQUALS,
                expected=tool_name,
                hard=True,
            )
        ],
        dependency_predecessor_ids=list(predecessors or []),
        stage_anchor_predecessor_id=anchor_id,
    )


def _append_pair(
    trajectory: Trajectory,
    tracker: AgentStepTracker,
    start_index: int,
    tool_name: str,
) -> TrajectoryStep | None:
    outbound = _step(
        start_index,
        Actor.AGENT,
        Actor.ENVIRONMENT,
        EventType.TOOL_CALL,
        tool_call=ToolCall(tool_name, {}),
    )
    feedback = _step(
        start_index + 1,
        Actor.ENVIRONMENT,
        Actor.AGENT,
        EventType.TOOL_RESULT,
        tool_result=ToolResult(True),
    )
    trajectory.append_step(outbound)
    trajectory.append_step(feedback)
    tracker.ingest(outbound)
    return tracker.ingest(feedback)


def _checkpoint(
    kwargs: dict[str, object],
    state: RuntimeEvaluationState,
    checkpointed: dict[str, str],
) -> RuntimeEvaluationDecision:
    milestone = kwargs["milestone"]
    boundary = kwargs["boundary"]
    checkpointed["milestone_id"] = milestone.milestone_id
    settlement = HarnessStageSettlement(
        "st-m0",
        "milestone",
        milestone.milestone_id,
        1,
        boundary.step_index,
        boundary_id=boundary.boundary_id,
        boundary_step_index=boundary.step_index,
        score=1.0,
        status=StageStatus.PASS.value,
    )
    state.matched_settlements[milestone.milestone_id] = settlement
    return RuntimeEvaluationDecision(settlement, None, state)


def _step(
    index: int,
    actor: Actor,
    recipient: Actor,
    event_type: EventType,
    tool_call: ToolCall | None = None,
    tool_result: ToolResult | None = None,
) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=actor,
        recipient=recipient,
        event_type=event_type,
        content="step",
        tool_call=tool_call,
        tool_result=tool_result,
        raw={"openai_tool_call_id": f"raw-{index}"},
    )
