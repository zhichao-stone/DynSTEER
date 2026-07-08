from dynsteer.evaluate.models import RuntimeEvaluationState
from dynsteer.evaluate.runtime import runtime_diagnostics_summary
from dynsteer.model import Actor, Dimension, EventType, TaskCase, ToolCall, ToolResult, Trajectory, TrajectoryStep


def test_runtime_diagnostics_summary_contains_quality_diagnostics() -> None:
    task_case = TaskCase(task_id="task", task_description="desc", case_id="case")
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            TrajectoryStep(
                step_id="s0",
                index=0,
                actor=Actor.AGENT,
                event_type=EventType.TOOL_CALL,
                tool_call=ToolCall("search_lat_lon", {"latitude": 37.334606}),
            ),
            TrajectoryStep(
                step_id="s1",
                index=1,
                actor=Actor.ENVIRONMENT,
                event_type=EventType.TOOL_RESULT,
                tool_result=ToolResult(True, None),
            ),
        ],
    )
    state = RuntimeEvaluationState(
        weights={dimension: 1.0 for dimension in Dimension},
        settlements=[],
        matched_settlements={},
        stage_reports=[],
        match_attempts=[],
    )

    summary = runtime_diagnostics_summary(task_case=task_case, trajectory=trajectory, state=state)

    assert summary["runtime_quality_diagnostics"]["empty_tool_results"][0]["tool_name"] == "search_lat_lon"
    assert summary["runtime_quality_diagnostics"]["empty_tool_results"][0]["severity"] == "warning"
    assert summary["runtime_quality_diagnostics"]["empty_tool_results"][0]["result_category"] == "query_empty_payload"
