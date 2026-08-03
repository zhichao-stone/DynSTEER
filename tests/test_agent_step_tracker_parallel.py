import pytest

from dynsteer.model import (
    Actor,
    AgentStepProtocolError,
    AgentStepTracker,
    EventType,
    TrajectoryStep,
)


def _step(
    step_id: str,
    index: int,
    actor: Actor,
    recipient: Actor,
    event_type: EventType,
    call_id: str | None = None,
) -> TrajectoryStep:
    raw = {"openai_tool_call_id": call_id} if call_id is not None else {}
    return TrajectoryStep(
        step_id=step_id,
        index=index,
        actor=actor,
        recipient=recipient,
        event_type=event_type,
        raw=raw,
    )


def _tool_call(step_id: str, index: int, call_id: str | None = None) -> TrajectoryStep:
    return _step(step_id, index, Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, call_id)


def _tool_result(step_id: str, index: int, call_id: str | None = None) -> TrajectoryStep:
    return _step(step_id, index, Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, call_id)


def test_serial_tool_call_and_result_close_unchanged() -> None:
    tracker = AgentStepTracker()

    assert tracker.ingest(_tool_call("s1", 1)) is None
    closure = tracker.ingest(_tool_result("s2", 2))

    assert closure is not None
    assert [step.step_id for step in closure.steps] == ["s1", "s2"]
    assert tracker.completed_count == 1


def test_agent_user_conversation_closes_before_next_outbound() -> None:
    tracker = AgentStepTracker()
    outbound = _step("s1", 1, Actor.AGENT, Actor.USER, EventType.MESSAGE)
    feedback = _step("s2", 2, Actor.USER, Actor.AGENT, EventType.MESSAGE)

    assert tracker.ingest(outbound) is None
    closure = tracker.ingest(feedback)
    assert closure is not None
    assert [step.step_id for step in closure.steps] == ["s1", "s2"]
    assert tracker.ingest(_tool_call("s3", 3)) is None


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (("A", "B"), (("s1", "s3"), ("s2", "s4"))),
        (("B", "A"), (("s2", "s3"), ("s1", "s4"))),
    ],
)
def test_parallel_tool_results_match_by_call_id(
    results: tuple[str, str],
    expected: tuple[tuple[str, str], tuple[str, str]],
) -> None:
    tracker = AgentStepTracker()
    assert tracker.ingest(_tool_call("s1", 1, "A")) is None
    assert tracker.ingest(_tool_call("s2", 2, "B")) is None

    closures = [
        tracker.ingest(_tool_result("s3", 3, results[0])),
        tracker.ingest(_tool_result("s4", 4, results[1])),
    ]

    assert all(closure is not None for closure in closures)
    assert tuple(tuple(step.step_id for step in closure.steps) for closure in closures if closure) == expected
    assert tracker.completed_count == 2
    assert tracker.pending_outbounds == {}


def test_first_parallel_result_removes_only_matching_pending() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))
    tracker.ingest(_tool_call("s2", 2, "B"))

    closure = tracker.ingest(_tool_result("s3", 3, "A"))

    assert closure is not None
    assert set(tracker.pending_outbounds) == {"tool:B"}
    assert tracker.completed_count == 1


def test_parallel_outbound_missing_call_id_fails() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))

    with pytest.raises(AgentStepProtocolError, match="缺少 correlation id"):
        tracker.ingest(_tool_call("s2", 2))


def test_parallel_outbound_duplicate_call_id_fails() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))

    with pytest.raises(AgentStepProtocolError, match="重复 correlation id"):
        tracker.ingest(_tool_call("s2", 2, "A"))


def test_unknown_result_call_id_fails_without_route_fallback() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))

    with pytest.raises(AgentStepProtocolError, match="未知 correlation id") as exc_info:
        tracker.ingest(_tool_result("s2", 2, "unknown"))

    assert "current_step_id=s2" in str(exc_info.value)
    assert "current_call_id=unknown" in str(exc_info.value)
    assert tracker.completed_count == 0


def test_parallel_result_without_call_id_fails() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))
    tracker.ingest(_tool_call("s2", 2, "B"))

    with pytest.raises(AgentStepProtocolError, match="并行 feedback 缺少 correlation id"):
        tracker.ingest(_tool_result("s3", 3))


def test_pending_user_message_rejects_next_agent_outbound() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_step("s1", 1, Actor.AGENT, Actor.USER, EventType.MESSAGE))

    with pytest.raises(AgentStepProtocolError, match="尚未闭合"):
        tracker.ingest(_tool_call("s2", 2, "A"))


def test_unique_serial_feedback_without_id_uses_reciprocal_route() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))

    closure = tracker.ingest(_tool_result("s2", 2))

    assert closure is not None
    assert [step.step_id for step in closure.steps] == ["s1", "s2"]


def test_finalize_only_self_closes_unique_terminal_user_message() -> None:
    terminal_tracker = AgentStepTracker()
    terminal_tracker.ingest(_step("s1", 1, Actor.AGENT, Actor.USER, EventType.FINAL))

    closure = terminal_tracker.finalize()

    assert closure is not None
    assert [step.step_id for step in closure.steps] == ["s1"]
    assert terminal_tracker.completed_count == 1

    tool_tracker = AgentStepTracker()
    tool_tracker.ingest(_tool_call("s1", 1, "A"))
    assert tool_tracker.finalize() is None
    assert tool_tracker.completed_count == 0


def test_finalize_does_not_merge_multiple_unfinished_tools() -> None:
    tracker = AgentStepTracker()
    tracker.ingest(_tool_call("s1", 1, "A"))
    tracker.ingest(_tool_call("s2", 2, "B"))

    assert tracker.finalize() is None
    assert tracker.completed_count == 0
    assert set(tracker.pending_outbounds) == {"tool:A", "tool:B"}
