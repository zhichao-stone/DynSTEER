from __future__ import annotations

from collections import Counter

from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import JsonObject, MilestoneGraph
from dynsteer.utils import canonical_json


def canonical_graph_semantics(graph: MilestoneGraph, tool_aliases: dict[str, str] | None = None) -> JsonObject:
    """按具体工具 occurrence 提取 operation、拓扑和 fatal minefield 语义。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    operation_by_id: dict[str, str] = {}
    occurrence_counts: Counter[str] = Counter()
    operations: list[str] = []
    for node in graph.nodes:
        tool_name = _tool_name_from_node(node)
        if tool_name is None:
            continue
        tool_name = (tool_aliases or {}).get(tool_name, tool_name)
        occurrence = occurrence_counts[tool_name]
        occurrence_counts[tool_name] += 1
        operations.append(tool_name)
        operation_by_id[node.milestone_id] = canonical_json({
            "tool_name": tool_name,
            "occurrence_index": occurrence,
        })
    topology = sorted(
        canonical_json([operation_by_id[source], operation_by_id[target]])
        for source, target in graph.edges
        if source in operation_by_id and target in operation_by_id
    )
    minefields = sorted(
        identity for item in graph.minefields if item.severity == "fatal"
        and (identity := _minefield_identity(item, tool_aliases or {})) is not None
    )
    dispositions = sorted({
        str(value) for value in graph.metadata.get("turn_dispositions", {}).values()
    })
    return {
        "dispositions": dispositions,
        "operations": sorted(operations),
        "topology": topology,
        "minefields": minefields,
        "responses": [],
    }

def compare_input_coverage(
    reference: MilestoneGraph,
    view: GeneratorTaskView,
    tool_aliases: dict[str, str] | None = None,
) -> JsonObject:
    """检查 reference 的具体工具是否存在于公开 TOOL_CALL evidence。"""
    available = {
        str(item.metadata["tool_name"])
        for item in view.evidence_catalog
        if isinstance(item.metadata.get("tool_name"), str)
    }
    reference_tools = [
        (tool_aliases or {}).get(name, name) for node in reference.nodes
        if (name := _tool_name_from_node(node)) is not None
    ]
    uncovered = sorted(tool for tool in reference_tools if tool not in available)
    return {
        "covered": not uncovered,
        "uncovered_count": len(uncovered),
        "uncovered": [{"kind": "unknown_tool", "item": tool} for tool in uncovered],
        "available_tool_count": len(available),
        "reference_tool_multiset": dict(Counter(reference_tools)),
    }

def _tool_name_from_node(node: object) -> str | None:
    return _tool_name_from_constraints(getattr(node, "constraints", []))

def _tool_name_from_constraints(constraints: object) -> str | None:
    setting_tools = {
        "low_battery_mode": "set_low_battery_mode_status",
        "location_service": "set_location_service_status",
        "cellular": "set_cellular_service_status", "wifi": "set_wifi_status",
    }
    state_tools = {
        ("MESSAGING", "addition_similarity"): "send_message_with_phone_number",
        ("CONTACT", "addition_similarity"): "add_contact", ("CONTACT", "update_similarity"): "modify_contact",
        ("CONTACT", "removal_similarity"): "remove_contact", ("REMINDER", "addition_similarity"): "add_reminder",
        ("REMINDER", "update_similarity"): "modify_reminder", ("REMINDER", "removal_similarity"): "remove_reminder",
    }
    for constraint in constraints:
        semantic = getattr(constraint, "stage_goal_semantics", None)
        if isinstance(semantic, dict) and isinstance(semantic.get("tool_name"), str):
            return str(semantic["tool_name"])
        if isinstance(semantic, dict) and semantic.get("kind") == "set_state":
            expected = semantic.get("expected")
            if semantic.get("namespace") == "SETTING" and isinstance(expected, dict):
                return next((setting_tools[key] for key in expected if key in setting_tools), None)
            metadata = getattr(constraint, "metadata", {}).get("toolsandbox", {})
            snapshot = metadata.get("snapshot_constraint") if isinstance(metadata, dict) else None
            tool_name = state_tools.get((str(semantic.get("namespace")), str(snapshot)))
            if tool_name is not None:
                return tool_name
        expected = getattr(constraint, "expected", None)
        target = getattr(constraint, "target", None)
        if getattr(target, "value", None) == "tool_call" and isinstance(expected, str):
            return expected
    return None

def _minefield_identity(item: object, aliases: dict[str, str]) -> str | None:
    tool_name = _tool_name_from_constraints(getattr(item, "constraints", []))
    tool_name = aliases.get(tool_name, tool_name) if tool_name else None
    return canonical_json({"tool_name": tool_name, "severity": "fatal"}) if tool_name else None
