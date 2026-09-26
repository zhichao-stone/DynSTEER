import ast
import json
import re
from dynsteer.adapter.toolsandbox.utils.roles import role_to_actor, role_to_recipient
from dynsteer.model import EventType, JsonObject, JsonValue
from dynsteer.utils import enum_name, json_safe

def tool_trace_items(raw_trace: object) -> list[JsonObject]:
    """Parse a ToolSandbox tool_trace field into a list of objects."""
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
            items.extend((dict(nested) for nested in parsed_item if isinstance(nested, dict)))
    return items

def tool_trace_from_row(row: dict[str, object]) -> dict[str, JsonValue] | None:
    trace_items = tool_trace_items(row.get("tool_trace"))
    return trace_items[0] if trace_items else None

def tool_arguments_from_agent_content(content: object) -> JsonObject:
    if not isinstance(content, str) or not content.strip():
        return {}
    match = re.search("[A-Za-z_][A-Za-z0-9_]*_parameters\\s*=\\s*(\\{.*?\\})(?:\\r?\\n|$)", content, flags=re.DOTALL)
    if match is None:
        return {}

    try:
        parsed = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return {}

    if not isinstance(parsed, dict):
        return {}
    safe = json_safe(parsed)
    return safe if isinstance(safe, dict) else {}

def tool_call_from_agent_row(row: dict[str, object], trace: dict[str, JsonValue] | None) -> JsonObject | None:
    parsed_arguments = tool_arguments_from_agent_content(row.get("content"))
    if trace is not None and isinstance(trace.get("tool_name"), str) and str(trace.get("tool_name")).strip():
        arguments = trace.get("arguments")
        return {
            "name": str(trace["tool_name"]).strip(),
            "arguments": arguments if isinstance(arguments, dict) else parsed_arguments,
        }
    if isinstance(row.get("openai_function_name"), str) and str(row.get("openai_function_name")).strip():
        return {"name": str(row["openai_function_name"]).strip(), "arguments": parsed_arguments}

    content = row.get("content")
    if not isinstance(content, str):
        return None
    parsed_call = _tool_call_from_content(content)
    if parsed_call is None:
        return None
    return parsed_call

def sandbox_message_index(row: dict[str, object]) -> int:
    value = row.get("sandbox_message_index")
    return value if isinstance(value, int) else -1

def sandbox_rows_to_step_dicts(rows: list[dict[str, object]]) -> list[dict[str, JsonValue]]:
    steps: list[dict[str, JsonValue]] = []
    for row in rows:
        raw_index = sandbox_message_index(row)
        step_index = raw_index if raw_index >= 0 else len(steps)
        sender, recipient = row.get("sender"), row.get("recipient")
        trace = tool_trace_from_row(row)
        actor, recipient_actor = role_to_actor(sender, recipient), role_to_recipient(recipient)
        event_type = EventType.MESSAGE.value
        tool_call: JsonObject | None = None
        tool_result: JsonObject | None = None
        if enum_name(sender) == "AGENT" and enum_name(recipient) == "EXECUTION_ENVIRONMENT":
            event_type = EventType.TOOL_CALL.value
            tool_call = tool_call_from_agent_row(row, trace)
        elif enum_name(sender) == "EXECUTION_ENVIRONMENT" and enum_name(recipient) == "AGENT":
            event_type = EventType.TOOL_RESULT.value
            tool_call_exception = row.get("tool_call_exception")
            tool_result = {
                "success": tool_call_exception is None,
                "content": trace.get("result") if trace is not None else json_safe(row.get("content")),
                "exception": tool_call_exception if isinstance(tool_call_exception, str) else None,
            }
        steps.append(
            {
                "step_id": f"s{step_index}", "index": step_index,
                "actor": actor, "recipient": recipient_actor,
                "event_type": event_type,
                "content": row.get("content") if isinstance(row.get("content"), str) else None,
                "tool_call": tool_call, "tool_result": tool_result,
                "raw_sandbox_message_index": json_safe(row.get("sandbox_message_index")),
                "raw_sender": enum_name(sender), "raw_recipient": enum_name(recipient),
                "openai_tool_call_id": json_safe(row.get("openai_tool_call_id")),
                "openai_function_name": json_safe(row.get("openai_function_name")),
                "visible_to": json_safe(row.get("visible_to")),
            }
        )
    return steps

def _parse_tool_trace_value(value: object) -> JsonValue:
    """Parsing individual tool_trace values to JSON security values."""
    if isinstance(value, str):
        try:
            return json_safe(json.loads(value))
        except json.JSONDecodeError:
            return None
    return json_safe(value)


def _tool_call_from_content(content: str) -> JsonObject | None:
    """Parsing complete`name(...)`Expression and its keyword parameters."""
    if re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*\([\s\S]*\)\s*", content) is None:
        return None
    try:
        expression = ast.parse(content.strip(), mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
        return None
    arguments: JsonObject = {}
    for keyword in expression.keywords:
        if keyword.arg is None:
            return None
        try:
            value = ast.literal_eval(keyword.value)
        except (ValueError, TypeError):
            return None
        arguments[keyword.arg] = json_safe(value)
    return {"name": expression.func.id, "arguments": arguments}
