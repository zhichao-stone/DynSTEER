import re

from dynsteer.adapter.loader import parse_milestone_graph
from dynsteer.adapter.toolsandbox.utils.trace import tool_trace_items
from dynsteer.adapter.utils import callable_name, callable_spec, rows_from_dataframe
from dynsteer.model import Actor, JsonObject, JsonValue, MilestoneGraph, StageGoalSemanticKind, TaskType
from dynsteer.utils import enum_name, json_safe


def task_description_from_steps(
    steps: list[dict[str, JsonValue]], fallback: str, first_user_sandbox_message_index: int | None = None
) -> str:
    if first_user_sandbox_message_index is not None:
        for step in steps:
            if (
                step.get("actor") == Actor.USER.value
                and step.get("raw_sandbox_message_index") == first_user_sandbox_message_index
                and isinstance(step.get("content"), str)
            ):
                return str(step["content"])
    for step in steps:
        if (
            step.get("actor") == Actor.USER.value
            and isinstance(step.get("content"), str)
            and not _visible_only_to_user_simulator(step.get("visible_to"))
        ):
            return str(step["content"])
    for step in steps:
        if step.get("actor") == Actor.USER.value and isinstance(step.get("content"), str):
            return str(step["content"])
    return fallback


def task_types_from_categories(categories: list[object]) -> list[TaskType]:
    names = {enum_name(item) for item in categories}
    result: list[TaskType] = []
    if "STATE_DEPENDENCY" in names or "SINGLE_TOOL_CALL" in names or "MULTIPLE_TOOL_CALL" in names:
        result.append(TaskType.STATEFUL_TOOL)
    if "MULTIPLE_USER_TURN" in names:
        result.append(TaskType.DIALOGUE_INTERACTION)
    if "INSUFFICIENT_INFORMATION" in names:
        result.append(TaskType.SAFETY_SENSITIVE)
    if not result:
        result.append(TaskType.STATEFUL_TOOL)
    return result


def constraint_from_snapshot_constraint(constraint_id: str, constraint: object) -> dict[str, JsonValue]:
    namespace = enum_name(getattr(constraint, "database_namespace", None))
    target_dataframe = getattr(constraint, "target_dataframe", None)
    rows = [json_safe(row) for row in rows_from_dataframe(target_dataframe)]
    snapshot_constraint = getattr(constraint, "snapshot_constraint", None)
    base_snapshot_constraint = getattr(snapshot_constraint, "func", snapshot_constraint)
    snapshot_constraint_name = getattr(base_snapshot_constraint, "__name__", str(base_snapshot_constraint))
    snapshot_constraint_module = getattr(base_snapshot_constraint, "__module__", None)
    partial_keywords = getattr(snapshot_constraint, "keywords", None) or {}
    snapshot_constraint_kwargs = {
        str(key): callable_name(value) if callable(value) else json_safe(value)
        for key, value in dict(partial_keywords).items()
    }
    column_measures = getattr(constraint, "column_similarity_measure", None) or {}
    reference_index = json_safe(getattr(constraint, "reference_milestone_node_index", None))
    # guardrail 表达相对参考 milestone 的状态保持，不表达目标数据库为空。
    if "guardrail" in snapshot_constraint_name:
        reference = {"type": "milestone_index", "value": reference_index} if isinstance(reference_index, int) else {"type": "initial_state"}
        stage_goal_semantics = {
            "kind": StageGoalSemanticKind.PRESERVE_STATE.value,
            "namespace": namespace,
            "reference": reference,
            "evidence_source": "structured_scorer",
            "user_visible_required": False,
        }
    # SANDBOX namespace 表达对话消息或工具调用目标。
    elif namespace == "SANDBOX":
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        tool_call_semantics = _sandbox_tool_call_semantics(first) if isinstance(first, dict) else None
        if tool_call_semantics is not None:
            stage_goal_semantics = tool_call_semantics
        else:
            sender = first.get("sender") if isinstance(first, dict) else None
            recipient = first.get("recipient") if isinstance(first, dict) else None
            content = first.get("content") if isinstance(first, dict) else None
            stage_goal_semantics = {
                "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
                "sender": str(sender or "AGENT"),
                "recipient": str(recipient or "USER"),
                "content": str(content or ""),
                "match_policy": "semantic_equivalent",
                "evidence_source": "trajectory_or_structured_scorer",
                "user_visible_required": _is_user_visible_message_route(sender, recipient),
            }
    # 其他 snapshot constraint 表达目标 state namespace 的设置或校验。
    else:
        expected = dict(rows[0]) if len(rows) == 1 and isinstance(rows[0], dict) else list(rows)
        stage_goal_semantics = {
            "kind": StageGoalSemanticKind.SET_STATE.value,
            "namespace": namespace,
            "expected": expected,
            "evidence_source": "structured_scorer",
            "user_visible_required": False,
        }
    toolsandbox_metadata = {
        "database_namespace": namespace,
        "snapshot_constraint": snapshot_constraint_name,
        "snapshot_constraint_module": snapshot_constraint_module,
        "snapshot_constraint_kwargs": snapshot_constraint_kwargs,
        "reference_milestone_node_index": reference_index,
        "column_similarity_measure": {str(key): callable_spec(value) for key, value in dict(column_measures).items()},
        "guardrail": "guardrail" in snapshot_constraint_name,
    }
    return {
        "constraint_id": constraint_id,
        "target": "state_snapshot",
        "namespace": namespace,
        "selector": "$",
        "operator": "custom",
        "expected": {"rows": rows, "columns": list(rows[0].keys()) if rows else []},
        "weight": 1.0,
        "threshold": 1.0,
        "hard": True,
        "evaluator_hint": "toolsandbox",
        "stage_goal_semantics": stage_goal_semantics,
        "metadata": {"toolsandbox": toolsandbox_metadata},
    }


def edge_list(matcher: object | None, prefix: str) -> list[list[str]]:
    if matcher is None:
        return []
    milestones = list(getattr(matcher, "milestones", []) or [])
    raw_edges = getattr(matcher, "edge_list", None)
    edges = raw_edges if raw_edges is not None else [(index, index + 1) for index in range(len(milestones) - 1)]
    return [[f"{prefix}{source}", f"{prefix}{target}"] for source, target in list(edges or [])]


def milestone_graph_from_scenario(scenario: object) -> MilestoneGraph:
    evaluation = getattr(scenario, "evaluation", None)
    if evaluation is None:
        return parse_milestone_graph({"nodes": [], "edges": [], "minefields": [], "metadata": {"benchmark": "toolsandbox"}})
    milestone_matcher = getattr(evaluation, "milestone_matcher", None)
    minefield_matcher = getattr(evaluation, "minefield_matcher", None)
    return parse_milestone_graph(
        {
            "nodes": _matcher_nodes(milestone_matcher, "m", True),
            "edges": edge_list(milestone_matcher, "m"),
            "minefields": _matcher_nodes(minefield_matcher, "mf", False),
            "metadata": {"benchmark": "toolsandbox", "constraint_semantics": "toolsandbox_custom_metadata"},
        }
    )


def _sandbox_tool_call_semantics(row: dict[str, JsonValue]) -> JsonObject | None:
    traces = tool_trace_items(row.get("tool_trace"))
    if traces:
        trace = traces[0]
        tool_name = trace.get("tool_name")
        if isinstance(tool_name, str) and tool_name.strip():
            arguments = trace.get("arguments")
            return {
                "kind": StageGoalSemanticKind.TOOL_CALL.value,
                "tool_name": tool_name.strip(),
                "arguments": arguments if isinstance(arguments, dict) else {},
                "evidence_source": "trajectory_or_structured_scorer",
                "user_visible_required": False,
            }
    raw_name = row.get("openai_function_name")
    tool_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else ""
    if not tool_name:
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", content.strip())
            tool_name = match.group(1) if match is not None else ""
    sender = str(row.get("sender") or "").strip().upper()
    recipient = str(row.get("recipient") or "").strip().upper()
    if not tool_name and not (sender == "AGENT" and recipient in {"EXECUTION_ENVIRONMENT", "ENVIRONMENT"}):
        return None
    return {
        "kind": StageGoalSemanticKind.TOOL_CALL.value,
        "tool_name": tool_name or "unknown",
        "arguments": {},
        "evidence_source": "trajectory_or_structured_scorer",
        "user_visible_required": False,
    }


def _is_user_visible_message_route(sender: object, recipient: object) -> bool:
    sender_text = str(sender or "").strip().upper()
    recipient_text = str(recipient or "").strip().upper()
    return recipient_text == "USER" and sender_text in {"AGENT", "ENVIRONMENT", "SYSTEM", ""}


def _matcher_nodes(matcher: object | None, prefix: str, is_milestone: bool) -> list[dict[str, JsonValue]]:
    if matcher is None:
        return []
    label = "milestone" if is_milestone else "minefield"
    id_key = "milestone_id" if is_milestone else "minefield_id"
    result: list[dict[str, JsonValue]] = []
    for index, node in enumerate(getattr(matcher, "milestones", []) or []):
        item: dict[str, JsonValue] = {
            id_key: f"{prefix}{index}",
            "name": f"ToolSandbox {label} {index}",
            "description": f"ToolSandbox {label} {index}",
            "constraints": [
                constraint_from_snapshot_constraint(f"{prefix}{index}_c{constraint_index}", constraint)
                for constraint_index, constraint in enumerate(getattr(node, "snapshot_constraints", []) or [])
            ],
            "metadata": {"toolsandbox": {f"{label}_index": index}},
        }
        if not is_milestone:
            item["severity"] = "fatal"
            item["penalty"] = {"mode": "fixed", "value": 1.0}
        result.append(item)
    return result


def _visible_only_to_user_simulator(value: JsonValue) -> bool:
    if not isinstance(value, list) or len(value) != 1:
        return False
    return str(value[0]) == "USER"
