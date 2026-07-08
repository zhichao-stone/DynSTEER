from dynsteer.evaluate.quality import build_runtime_quality_diagnostics
from dynsteer.model import Actor, EventType, TaskCase, ToolCall, ToolResult, Trajectory, TrajectoryStep


def _step(
    index: int,
    actor: Actor,
    event_type: EventType,
    content: str | None = None,
    tool_call: ToolCall | None = None,
    tool_result: ToolResult | None = None,
) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=actor,
        event_type=event_type,
        content=content,
        tool_call=tool_call,
        tool_result=tool_result,
    )


def test_quality_diagnostics_flags_literal_self_person_id_and_empty_result() -> None:
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            _step(0, Actor.USER, EventType.MESSAGE, "Update the last person I texted."),
            _step(
                1,
                Actor.AGENT,
                EventType.TOOL_CALL,
                tool_call=ToolCall("search_messages", {"sender_person_id": "self"}),
            ),
            _step(2, Actor.ENVIRONMENT, EventType.TOOL_RESULT, tool_result=ToolResult(True, [])),
        ],
    )

    diagnostics = build_runtime_quality_diagnostics(TaskCase("task", "desc", "case"), trajectory)

    assert diagnostics["warning_count"] == 2
    assert diagnostics["tool_argument_warnings"][0]["warning"] == "literal_alias_for_id_argument"
    assert diagnostics["empty_tool_results"][0]["tool_name"] == "search_messages"


def test_quality_diagnostics_flags_answer_after_empty_tool_result() -> None:
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            _step(0, Actor.USER, EventType.MESSAGE, "Where am I?"),
            _step(
                1,
                Actor.AGENT,
                EventType.TOOL_CALL,
                tool_call=ToolCall("search_lat_lon", {"latitude": 37.334606, "longitude": -122.009102}),
            ),
            _step(2, Actor.ENVIRONMENT, EventType.TOOL_RESULT, tool_result=ToolResult(True, None)),
            _step(3, Actor.AGENT, EventType.MESSAGE, "You are in Cupertino, California."),
        ],
    )

    diagnostics = build_runtime_quality_diagnostics(TaskCase("task", "desc", "case"), trajectory)

    assert diagnostics["grounding_warnings"][0]["warning"] == "agent_answer_after_empty_tool_result"
    assert diagnostics["grounding_warnings"][0]["tool_name"] == "search_lat_lon"
    assert diagnostics["warning_count"] == 2
    assert diagnostics["empty_tool_results"][0]["severity"] == "warning"
    assert diagnostics["empty_tool_results"][0]["result_category"] == "query_empty_payload"


def test_quality_diagnostics_marks_state_mutation_empty_result_as_info() -> None:
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            _step(0, Actor.USER, EventType.MESSAGE, "Turn on Wi-Fi."),
            _step(
                1,
                Actor.AGENT,
                EventType.TOOL_CALL,
                tool_call=ToolCall("set_wifi_status", {"on": True}),
            ),
            _step(2, Actor.ENVIRONMENT, EventType.TOOL_RESULT, tool_result=ToolResult(True, None)),
            _step(3, Actor.AGENT, EventType.MESSAGE, "Wi-Fi is now on."),
        ],
    )

    diagnostics = build_runtime_quality_diagnostics(TaskCase("task", "desc", "case"), trajectory)

    assert diagnostics["warning_count"] == 0
    assert diagnostics["empty_tool_results"][0]["tool_name"] == "set_wifi_status"
    assert diagnostics["empty_tool_results"][0]["severity"] == "info"
    assert diagnostics["empty_tool_results"][0]["result_category"] == "state_mutation_no_payload"
    assert diagnostics["grounding_warnings"] == []


def test_quality_diagnostics_counts_extra_user_turn_before_first_tool_call() -> None:
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            _step(0, Actor.USER, EventType.MESSAGE, "Delete the next reminder."),
            _step(1, Actor.AGENT, EventType.MESSAGE, "Which reminder do you mean?"),
            _step(2, Actor.USER, EventType.MESSAGE, "The next one."),
            _step(3, Actor.AGENT, EventType.TOOL_CALL, tool_call=ToolCall("get_current_timestamp", {})),
        ],
    )

    diagnostics = build_runtime_quality_diagnostics(TaskCase("task", "desc", "case"), trajectory)

    assert diagnostics["efficiency"]["extra_user_turns_before_first_tool_call"] == 1
    assert diagnostics["efficiency"]["agent_messages_before_first_tool_call"] == 1
