import re

from dynsteer.adapter.loader import parse_milestone_graph
from dynsteer.adapter.toolsandbox.utils.trace import tool_call_from_agent_row, tool_trace_from_row
from dynsteer.adapter.utils import callable_name, callable_spec, rows_from_dataframe
from dynsteer.model import Actor, JsonObject, JsonValue, MilestoneGraph, StageGoalSemanticKind, TaskType
from dynsteer.utils import enum_name, json_safe

def task_description_from_steps(steps: list[dict[str, JsonValue]], fallback: str, first_user_sandbox_message_index: int | None=None) -> str:
    if first_user_sandbox_message_index is not None:
        for step in steps:
            if step.get("actor") == Actor.USER.value and step.get("raw_sandbox_message_index") == first_user_sandbox_message_index and isinstance(step.get("content"), str):
                return str(step["content"])
    for step in steps:
        if step.get("actor") == Actor.USER.value and isinstance(step.get("content"), str) and (not _visible_only_to_user_simulator(step.get("visible_to"))):
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
    snapshot_constraint_kwargs = {str(key): callable_name(value) if callable(value) else json_safe(value) for key, value in dict(partial_keywords).items()}
    column_measures = getattr(constraint, "column_similarity_measure", None) or {}
    reference_index = json_safe(getattr(constraint, "reference_milestone_node_index", None))
    if "guardrail" in snapshot_constraint_name:
        reference = (
            {"type": "milestone_index", "value": reference_index}
            if isinstance(reference_index, int)
            else {"type": "initial_state"}
        )
        stage_goal_semantics = {
            "kind": StageGoalSemanticKind.PRESERVE_STATE.value,
            "namespace": namespace,
            "reference": reference,
            "evidence_source": "structured_scorer",
            "user_visible_required": False,
        }
    elif namespace == "SANDBOX":
        if not rows or not isinstance(rows[0], dict):
            raise ValueError(f"ToolSandbox SANDBOX constraint 缺少 expected row: constraint_id={constraint_id}")
        first = rows[0]
        sender = enum_name(first.get("sender")).strip().upper()
        recipient = enum_name(first.get("recipient")).strip().upper()
        content = first.get("content")
        if _is_user_visible_message_route(sender, recipient):
            stage_goal_semantics = {
                "kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
                "sender": sender,
                "recipient": recipient,
                "content": str(content or ""),
                "match_policy": "semantic_equivalent",
                "evidence_source": "trajectory_or_structured_scorer",
                "user_visible_required": True,
            }
        else:
            tool_call_semantics = _sandbox_tool_call_semantics(first, sender, recipient)
            if tool_call_semantics is None:
                raise ValueError(
                    "ToolSandbox SANDBOX constraint 缺少合法消息或工具证据: "
                    f"constraint_id={constraint_id}, sender={sender}, recipient={recipient}"
                )
            stage_goal_semantics = tool_call_semantics
            if (
                snapshot_constraint_name == "tool_trace_dependant_similarity"
                and stage_goal_semantics.get("kind") == StageGoalSemanticKind.TOOL_CALL.value
            ):
                stage_goal_semantics.pop("arguments", None)
                stage_goal_semantics.update(
                    {
                        "argument_match_policy": "reference_derived",
                        "reference_milestone_node_index": reference_index,
                        "extractor": snapshot_constraint_kwargs.get("extractor"),
                    }
                )
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
        "column_similarity_measure": {
            str(key): callable_spec(value)
            for key, value in dict(column_measures).items()
        },
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
        return parse_milestone_graph(
            {"nodes": [], "edges": [], "minefields": [], "metadata": {"benchmark": "toolsandbox"}}
        )
    milestone_matcher = getattr(evaluation, "milestone_matcher", None)
    minefield_matcher = getattr(evaluation, "minefield_matcher", None)
    nodes = _matcher_nodes(milestone_matcher, "m", True)
    minefields = _matcher_nodes(minefield_matcher, "mf", False)
    return parse_milestone_graph(
        {
            "nodes": nodes,
            "edges": edge_list(milestone_matcher, "m"),
            "minefields": minefields,
            "metadata": {
                "benchmark": "toolsandbox",
                "constraint_semantics": "toolsandbox_custom_metadata",
                "empty_graph_completion_basis": (
                    "minefield_only" if not nodes and minefields else "whole_trajectory"
                ),
            },
        }
    )

def _sandbox_tool_call_semantics(
    row: dict[str, JsonValue], sender: str, recipient: str
) -> JsonObject | None:
    trace = tool_trace_from_row(row)
    is_agent_call = sender == "AGENT" and recipient in {"EXECUTION_ENVIRONMENT", "ENVIRONMENT"}
    is_tool_result = (
        sender in {"EXECUTION_ENVIRONMENT", "ENVIRONMENT"}
        and recipient == "AGENT"
        and trace is not None
    )
    if not is_agent_call and not is_tool_result:
        return None
    tool_call = tool_call_from_agent_row(row, trace)
    if tool_call is None and is_agent_call:
        content = row.get("content")
        if isinstance(content, str) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", content.strip()
        ):
            tool_call = {"name": content.strip(), "arguments": {}}
    if tool_call is None:
        return None
    return {
        "kind": StageGoalSemanticKind.TOOL_CALL.value,
        "tool_name": str(tool_call["name"]),
        "argument_match_policy": "exact",
        "arguments": tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {},
        "evidence_source": "trajectory_or_structured_scorer",
        "user_visible_required": False,
    }

def _is_user_visible_message_route(sender: object, recipient: object) -> bool:
    sender_text = str(sender or "").strip().upper()
    recipient_text = str(recipient or "").strip().upper()
    return recipient_text == "USER" and sender_text in {"AGENT", "ENVIRONMENT", "SYSTEM"}

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
                for constraint_index, constraint in enumerate(
                    getattr(node, "snapshot_constraints", []) or []
                )
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
