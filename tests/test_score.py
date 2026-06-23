from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    Operator,
    StageStatus,
    StateSnapshot,
    ToolCall,
    ToolResult,
    Trajectory,
    TrajectoryStep,
    Actor,
)
from dynsteer.score import score_constraint, score_milestone, score_operator, select_value


def test_select_value_dot_and_index() -> None:
    source = {"a": {"b": [{"c": 3}]}}

    assert select_value(source, "$.a.b[0].c") == 3
    assert select_value(source, "$") == source
    assert select_value(source, "$.a.b[9].c") is None
    assert select_value(source, "a.b") is None


def test_score_operator_basic_cases() -> None:
    assert score_operator("send", Operator.ONE_OF, ["send", "reply"]) == 1.0
    assert score_operator("hello world", Operator.CONTAINS, "world") == 1.0
    assert score_operator(["a", "b"], Operator.CONTAINS, "a") == 1.0
    assert score_operator({"a": 1}, Operator.CONTAINS, "a") == 1.0
    assert score_operator("hello", Operator.FUZZY_MATCH, "hallo") > 0.6
    assert score_operator("new", Operator.ADDED, None) == 1.0
    assert score_operator("new", Operator.UPDATED, "old") == 1.0
    assert score_operator(None, Operator.REMOVED, "old") == 1.0
    assert score_operator("x", Operator.CUSTOM, "x") == 0.0


def test_score_operator_json_subsumes() -> None:
    actual = {"name": "tool", "args": {"x": 1, "y": 2}}
    expected = {"args": {"x": 1}}

    assert score_operator(actual, Operator.JSON_SUBSUMES, expected) == 1.0


def test_score_constraint_marks_missing_selector() -> None:
    constraint = Constraint(
        constraint_id="c1",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.missing",
        operator=Operator.EQUALS,
        expected=True,
    )
    snapshot = StateSnapshot(
        snapshot_id="b1",
        after_step_id="s1",
        after_step_index=1,
        namespaces={"default": {"ok": True}},
    )

    result = score_constraint(constraint, snapshot, None)

    assert result.missing is True
    assert result.score == 0.0


def test_hard_constraint_failure_zeroes_milestone() -> None:
    constraint = Constraint(
        constraint_id="c1",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.done",
        operator=Operator.EQUALS,
        expected=True,
        hard=True,
    )
    milestone = Milestone(
        milestone_id="m1",
        name="done",
        description="done",
        constraints=[constraint],
    )
    boundary = Boundary(boundary_id="b1", step_index=1, snapshot_id="snap-1", reason="final")
    trajectory = Trajectory(run_id="r", task_id="t", steps=[])
    snapshots = [
        StateSnapshot(
            snapshot_id="snap-1",
            after_step_id="s1",
            after_step_index=1,
            namespaces={"default": {"done": False}},
        )
    ]

    result = score_milestone(milestone, boundary, trajectory, snapshots)

    assert result.score == 0.0
    assert result.status == StageStatus.FAIL


def test_unchanged_since_compares_reference_snapshot() -> None:
    constraint = Constraint(
        constraint_id="c1",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$.value",
        operator=Operator.UNCHANGED_SINCE,
        reference_milestone_id="ref",
    )
    snapshot = StateSnapshot(
        snapshot_id="current",
        after_step_id="s2",
        after_step_index=2,
        namespaces={"default": {"value": 7}},
    )
    reference = StateSnapshot(
        snapshot_id="ref",
        after_step_id="s1",
        after_step_index=1,
        namespaces={"default": {"value": 7}},
    )

    result = score_constraint(constraint, snapshot, reference)

    assert result.score == 1.0


def test_score_constraint_reads_tool_call_and_result() -> None:
    call_constraint = Constraint(
        constraint_id="call",
        target=ConstraintTarget.TOOL_CALL,
        selector="$.name",
        operator=Operator.EQUALS,
        expected="lookup",
    )
    result_constraint = Constraint(
        constraint_id="result",
        target=ConstraintTarget.TOOL_RESULT,
        selector="$.success",
        operator=Operator.EQUALS,
        expected=True,
    )
    step = TrajectoryStep(
        step_id="s1",
        index=1,
        actor=Actor.AGENT,
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCall(name="lookup", arguments={}),
        tool_result=ToolResult(success=True),
    )

    assert score_constraint(call_constraint, step, None).score == 1.0
    assert score_constraint(result_constraint, step, None).score == 1.0
