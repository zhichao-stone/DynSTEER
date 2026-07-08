from dynsteer.adapter.toolsandbox.adapter import _tool_trace_stage_goal_semantics, task_description_from_steps
from dynsteer.model import Constraint, ConstraintTarget, Operator, StageGoalSemanticKind
from dynsteer.stage_goal import _constraint_goal_text_from_semantics


def test_tool_trace_stage_goal_semantics_reads_tool_name_and_arguments() -> None:
    semantics = _tool_trace_stage_goal_semantics(
        {
            "sender": "EXECUTION_ENVIRONMENT",
            "recipient": "AGENT",
            "tool_trace": "{\"tool_name\": \"search_messages\", \"arguments\": {\"sender_person_id\": \"3815\"}}",
        }
    )

    assert semantics == {
        "kind": StageGoalSemanticKind.TOOL_CALL.value,
        "tool_name": "search_messages",
        "arguments": {"sender_person_id": "3815"},
        "evidence_source": "trajectory_or_structured_scorer",
        "user_visible_required": False,
    }


def test_tool_trace_stage_goal_semantics_reads_json_array_string() -> None:
    semantics = _tool_trace_stage_goal_semantics(
        {
            "sender": "EXECUTION_ENVIRONMENT",
            "recipient": "AGENT",
            "tool_trace": "[{\"tool_name\": \"search_reminder\", \"arguments\": {}}]",
        }
    )

    assert semantics == {
        "kind": StageGoalSemanticKind.TOOL_CALL.value,
        "tool_name": "search_reminder",
        "arguments": {},
        "evidence_source": "trajectory_or_structured_scorer",
        "user_visible_required": False,
    }


def test_tool_trace_stage_goal_semantics_ignores_message_rows_without_tool_trace() -> None:
    semantics = _tool_trace_stage_goal_semantics(
        {
            "sender": "AGENT",
            "recipient": "USER",
            "content": "Done",
        }
    )

    assert semantics is None


def test_task_description_from_steps_prefers_first_user_sandbox_message_index() -> None:
    description = task_description_from_steps(
        [
            {
                "actor": "user",
                "content": "I want to send a message to someone.",
                "raw_sandbox_message_index": 3,
                "visible_to": ["USER"],
            },
            {
                "actor": "user",
                "content": "Turn off cellular",
                "raw_sandbox_message_index": 15,
                "visible_to": None,
            },
        ],
        fallback="cellular_off",
        first_user_sandbox_message_index=15,
    )

    assert description == "Turn off cellular"


def test_tool_trace_semantics_generates_tool_call_stage_goal_text() -> None:
    semantics = _tool_trace_stage_goal_semantics(
        {
            "tool_trace": "{\"tool_name\": \"search_lat_lon\", \"arguments\": {\"latitude\": 37.334606}}",
        }
    )
    constraint = Constraint(
        constraint_id="m4_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        namespace="SANDBOX",
        stage_goal_semantics=semantics,
    )

    assert _constraint_goal_text_from_semantics(constraint) == (
        "Call tool search_lat_lon with arguments compatible with {\"latitude\": 37.334606}."
    )
