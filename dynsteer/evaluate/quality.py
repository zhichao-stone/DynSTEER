from __future__ import annotations

from dynsteer.model import Actor, EventType, JsonObject, JsonValue, TaskCase, Trajectory, TrajectoryStep

STATE_MUTATION_TOOL_PREFIXES = ("set_", "modify_", "remove_", "add_", "create_", "delete_", "send_")
QUERY_TOOL_PREFIXES = ("search_", "get_", "find_", "list_")


def build_runtime_quality_diagnostics(task_case: TaskCase, trajectory: Trajectory) -> JsonObject:
    """构造不参与评分的运行期轨迹质量诊断。

    Args:
        task_case: 当前任务定义。
        trajectory: 当前完整轨迹。

    Returns:
        可写入 raw_summary 的工具质量、grounding 与效率诊断。
    """
    if task_case is None or trajectory is None:
        raise ValueError("质量诊断参数不能为空")
    steps = list(trajectory.steps)
    tool_argument_warnings: list[JsonObject] = []
    empty_tool_results: list[JsonObject] = []
    failed_tool_results: list[JsonObject] = []
    grounding_warnings: list[JsonObject] = []
    latest_tool_call: TrajectoryStep | None = None

    for step in steps:
        if step.tool_call is not None or step.event_type == EventType.TOOL_CALL:
            latest_tool_call = step
            tool_argument_warnings.extend(_tool_argument_warnings(step))
            continue
        if step.tool_result is None and step.event_type != EventType.TOOL_RESULT:
            continue
        result = step.tool_result
        tool_name = _tool_name(latest_tool_call)
        success = bool(result.success) if result is not None else False
        exception = result.exception if result is not None else None
        content = result.content if result is not None else None
        if not success or exception:
            failed_tool_results.append(
                {
                    "step_index": step.index,
                    "step_id": step.step_id,
                    "tool_name": tool_name,
                    "exception": exception,
                }
            )
        if success and _is_empty_tool_content(content):
            classification = _classify_empty_tool_result(tool_name)
            empty = {
                "step_index": step.index,
                "step_id": step.step_id,
                "tool_name": tool_name,
                "content": content,
                **classification,
            }
            empty_tool_results.append(empty)
            if classification["severity"] == "warning":
                answer = _next_agent_message(steps, step.index)
                if answer is not None:
                    grounding_warnings.append(
                        {
                            "warning": "agent_answer_after_empty_tool_result",
                            "tool_name": tool_name,
                            "tool_result_step_index": step.index,
                            "answer_step_index": answer.index,
                            "answer_excerpt": _excerpt(answer.content or ""),
                        }
                    )

    empty_tool_warning_count = sum(1 for item in empty_tool_results if item.get("severity") == "warning")
    warning_count = (
        len(tool_argument_warnings)
        + empty_tool_warning_count
        + len(failed_tool_results)
        + len(grounding_warnings)
        + _efficiency_warning_count(steps)
    )
    return {
        "case_id": task_case.case_id,
        "warning_count": warning_count,
        "tool_argument_warnings": tool_argument_warnings,
        "empty_tool_results": empty_tool_results,
        "failed_tool_results": failed_tool_results,
        "grounding_warnings": grounding_warnings,
        "efficiency": _efficiency_diagnostics(steps),
    }


def _classify_empty_tool_result(tool_name: str | None) -> JsonObject:
    """区分空返回是正常无载荷结果，还是查询类风险结果。"""
    if tool_name is None or not str(tool_name).strip():
        return {"severity": "warning", "result_category": "unknown_empty_payload"}
    normalized = str(tool_name).strip().lower()
    if normalized.startswith(STATE_MUTATION_TOOL_PREFIXES):
        return {"severity": "info", "result_category": "state_mutation_no_payload"}
    if normalized.startswith(QUERY_TOOL_PREFIXES) or normalized in {"timestamp_diff"}:
        return {"severity": "warning", "result_category": "query_empty_payload"}
    return {"severity": "warning", "result_category": "unknown_empty_payload"}


def _tool_argument_warnings(step: TrajectoryStep) -> list[JsonObject]:
    """检查工具调用参数中明显非 canonical id 的别名。"""
    if step is None:
        raise ValueError("step 不能为空")
    tool_call = step.tool_call
    if tool_call is None:
        return []
    warnings: list[JsonObject] = []
    for key, value in tool_call.arguments.items():
        if not _looks_like_identifier_key(str(key)):
            continue
        if isinstance(value, str) and value.strip().lower() in {"self", "me", "user", "agent"}:
            warnings.append(
                {
                    "warning": "literal_alias_for_id_argument",
                    "step_index": step.index,
                    "step_id": step.step_id,
                    "tool_name": tool_call.name,
                    "argument_name": str(key),
                    "argument_value": value,
                }
            )
    return warnings


def _efficiency_diagnostics(steps: list[TrajectoryStep]) -> JsonObject:
    """统计首个工具调用前的额外用户轮次与工具调用数量。"""
    if steps is None:
        raise ValueError("steps 不能为空")
    first_user_index = _first_step_index(steps, Actor.USER, None)
    first_tool_index = _first_tool_call_index(steps)
    if first_user_index is None:
        first_user_index = -1
    if first_tool_index is None:
        first_tool_index = max((step.index for step in steps), default=first_user_index) + 1
    extra_user_turns = [
        step.index
        for step in steps
        if step.actor == Actor.USER and first_user_index < step.index < first_tool_index
    ]
    agent_messages = [
        step.index
        for step in steps
        if step.actor == Actor.AGENT
        and step.event_type == EventType.MESSAGE
        and first_user_index < step.index < first_tool_index
    ]
    return {
        "extra_user_turns_before_first_tool_call": len(extra_user_turns),
        "extra_user_turn_step_indices": extra_user_turns,
        "agent_messages_before_first_tool_call": len(agent_messages),
        "agent_message_step_indices_before_first_tool_call": agent_messages,
        "tool_call_count": sum(1 for step in steps if step.tool_call is not None or step.event_type == EventType.TOOL_CALL),
        "step_count": len(steps),
    }


def _efficiency_warning_count(steps: list[TrajectoryStep]) -> int:
    efficiency = _efficiency_diagnostics(steps)
    return 1 if int(efficiency["extra_user_turns_before_first_tool_call"]) > 0 else 0


def _first_step_index(
    steps: list[TrajectoryStep],
    actor: Actor,
    event_type: EventType | None,
) -> int | None:
    for step in steps:
        if step.actor != actor:
            continue
        if event_type is not None and step.event_type != event_type:
            continue
        return step.index
    return None


def _first_tool_call_index(steps: list[TrajectoryStep]) -> int | None:
    for step in steps:
        if step.tool_call is not None or step.event_type == EventType.TOOL_CALL:
            return step.index
    return None


def _tool_name(step: TrajectoryStep | None) -> str | None:
    if step is None or step.tool_call is None:
        return None
    return step.tool_call.name


def _next_agent_message(steps: list[TrajectoryStep], after_index: int) -> TrajectoryStep | None:
    for step in steps:
        if step.index <= after_index:
            continue
        if step.actor != Actor.AGENT:
            continue
        if step.event_type not in {EventType.MESSAGE, EventType.FINAL}:
            continue
        if isinstance(step.content, str) and step.content.strip():
            return step
    return None


def _looks_like_identifier_key(key: str) -> bool:
    if key is None:
        raise ValueError("参数名不能为空")
    return key.endswith("_id") or key.endswith("_person_id")


def _is_empty_tool_content(value: JsonValue) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"", "none", "null", "[]", "{}"}
    if isinstance(value, list | dict):
        return len(value) == 0
    return False


def _excerpt(value: str, limit: int = 160) -> str:
    if value is None:
        raise ValueError("摘要文本不能为空")
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
