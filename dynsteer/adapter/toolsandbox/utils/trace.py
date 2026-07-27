import ast
import json
import re

from dynsteer.adapter.toolsandbox.utils.roles import role_to_actor, role_to_recipient
from dynsteer.model import EventType, JsonObject, JsonValue
from dynsteer.utils import enum_name, json_safe


def tool_trace_items(raw_trace: object) -> list[JsonObject]:
    """解析 ToolSandbox tool_trace 字段为对象列表。

    入参：
        raw_trace: 可能来自 Polars row、JSON 文件或目标约束的 tool_trace 值。
    输出：
        保序解析出的 tool trace 对象列表；非法 JSON 字符串返回空列表。
    """
    trace_value = _parse_tool_trace_value(raw_trace)
    if isinstance(trace_value, dict):
        return [trace_value]
    if not isinstance(trace_value, list):
        return []
    items: list[JsonObject] = []
    for item in trace_value:
        parsed_item = _parse_tool_trace_value(item)
        if isinstance(parsed_item, dict):
            items.append(parsed_item)
        elif isinstance(parsed_item, list):
            items.extend(dict(nested) for nested in parsed_item if isinstance(nested, dict))
    return items


def tool_trace_from_row(row: dict[str, object]) -> dict[str, JsonValue] | None:
    raw_trace = row.get("tool_trace")
    trace_items = tool_trace_items(raw_trace)
    if not trace_items:
        return None
    return trace_items[0]


def tool_arguments_from_agent_content(content: object) -> JsonObject:
    if not isinstance(content, str) or not content.strip():
        return {}
    # ToolSandbox agent content 常以 *_parameters = {...} 形式记录工具参数。
    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*_parameters\s*=\s*(\{.*?\})(?:\r?\n|$)", content, flags=re.DOTALL)
    if match is None:
        return {}
    # 使用 literal_eval 解析 Python 字面量，避免执行 agent content 中的任意代码。
    try:
        parsed = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    safe = json_safe(parsed)
    return safe if isinstance(safe, dict) else {}


def tool_call_from_agent_row(row: dict[str, object], trace: dict[str, JsonValue] | None) -> JsonObject | None:
    # agent content 中的参数可补足 tool_trace 或 openai function 字段缺失的问题。
    parsed_arguments = tool_arguments_from_agent_content(row.get("content"))
    # 优先使用 tool_trace 中的工具名，因为它最接近执行环境真实调用。
    if trace is not None and isinstance(trace.get("tool_name"), str) and trace.get("tool_name"):
        arguments = trace.get("arguments")
        return {
            "name": str(trace["tool_name"]),
            "arguments": arguments if isinstance(arguments, dict) else parsed_arguments,
        }
    # 其次使用 OpenAI function name，并复用从 agent content 中解析出的参数。
    if isinstance(row.get("openai_function_name"), str) and row.get("openai_function_name"):
        return {"name": str(row["openai_function_name"]), "arguments": parsed_arguments}
    content = row.get("content")
    if not isinstance(content, str):
        return None
    # 最后从代码文本中兜底提取函数名。
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)
    if match is None:
        return None
    return {"name": match.group(1), "arguments": parsed_arguments}


def sandbox_message_index(row: dict[str, object]) -> int:
    value = row.get("sandbox_message_index")
    if isinstance(value, int):
        return value
    return -1


def sandbox_rows_to_step_dicts(rows: list[dict[str, object]]) -> list[dict[str, JsonValue]]:
    steps: list[dict[str, JsonValue]] = []
    for row in rows:
        raw_index = sandbox_message_index(row)
        step_index = raw_index if raw_index >= 0 else len(steps)
        sender = row.get("sender")
        recipient = row.get("recipient")
        trace = tool_trace_from_row(row)
        actor = role_to_actor(sender, recipient)
        recipient_actor = role_to_recipient(recipient)
        event_type = EventType.MESSAGE.value
        tool_call: JsonObject | None = None
        tool_result: JsonObject | None = None
        if enum_name(sender) == "AGENT" and enum_name(recipient) == "EXECUTION_ENVIRONMENT":
            event_type = EventType.TOOL_CALL.value
            tool_call = tool_call_from_agent_row(row, trace)
        elif enum_name(sender) == "EXECUTION_ENVIRONMENT" and enum_name(recipient) == "AGENT":
            event_type = EventType.TOOL_RESULT.value
            tool_result = {
                "success": row.get("tool_call_exception") is None,
                "content": trace.get("result") if trace is not None else json_safe(row.get("content")),
                "exception": row.get("tool_call_exception") if isinstance(row.get("tool_call_exception"), str) else None,
            }
        steps.append(
            {
                "step_id": f"s{step_index}",
                "index": step_index,
                "actor": actor,
                "recipient": recipient_actor,
                "event_type": event_type,
                "content": row.get("content") if isinstance(row.get("content"), str) else None,
                "tool_call": tool_call,
                "tool_result": tool_result,
                "raw_sandbox_message_index": json_safe(row.get("sandbox_message_index")),
                "raw_sender": enum_name(sender),
                "raw_recipient": enum_name(recipient),
                "openai_tool_call_id": json_safe(row.get("openai_tool_call_id")),
                "openai_function_name": json_safe(row.get("openai_function_name")),
                "visible_to": json_safe(row.get("visible_to")),
            }
        )
    return steps


def _parse_tool_trace_value(value: object) -> JsonValue:
    """解析单个 tool_trace 值为 JSON 安全值。"""
    if isinstance(value, str):
        try:
            return json_safe(json.loads(value))
        except json.JSONDecodeError:
            return None
    return json_safe(value)
