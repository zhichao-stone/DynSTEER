from __future__ import annotations

from dynsteer.agent.openai_tool_agent import ToolAgentResult
from dynsteer.model import JsonObject, TrajectoryStep


def agent_trajectory_steps(*, task_id: str, agent_result: ToolAgentResult) -> list[TrajectoryStep]:
    """Returns the shared Agent-generated trajectory step."""
    if not task_id.strip():
        raise ValueError("We can't be empty.")
    return list(agent_result.steps)


def usage_metrics(agent_result: ToolAgentResult) -> JsonObject:
    """Summarizes token, time-consuming and stopped reasons for sharing Agent."""
    wall_clock_ms = sum(
        step.cost.latency_ms or 0
        for step in agent_result.steps
        if step.event_type.value == "tool_result"
    )
    return {
        "prompt_tokens": agent_result.prompt_tokens,
        "completion_tokens": agent_result.completion_tokens,
        "total_tokens": agent_result.total_tokens,
        "tool_call_count": sum(1 for step in agent_result.steps if step.tool_call is not None),
        "agent_wall_clock_ms": wall_clock_ms,
        "stop_reason": agent_result.stop_reason,
        "agent_usage_available": agent_result.total_tokens is not None,
    }
