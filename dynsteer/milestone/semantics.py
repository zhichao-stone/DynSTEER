from __future__ import annotations

from collections import Counter

from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import JsonObject, MilestoneGraph
from dynsteer.utils import canonical_json


def canonical_graph_semantics(
    graph: MilestoneGraph,
    tool_aliases: dict[str, str] | None = None,
    view: GeneratorTaskView | None = None,
) -> JsonObject:
    """Extract goal, operation, topology, minefield, and preserve semantics."""
    if graph is None:
        raise ValueError("You can't be empty.")
    operation_by_id: dict[str, str] = {}
    occurrence_counts: Counter[str] = Counter()
    operations: list[str] = []
    goals: list[str] = []
    preserves: list[str] = []
    for node in graph.nodes:
        semantic_constraints = [
            (constraint, constraint.stage_goal_semantics)
            for constraint in node.constraints
            if isinstance(constraint.stage_goal_semantics, dict)
        ]
        for _, semantic in semantic_constraints:
            if semantic.get("kind") == "preserve_state":
                preserves.append(canonical_json({key: semantic.get(key) for key in ("kind", "namespace", "reference")}))
        primary = next(
            (
                (constraint, semantic)
                for constraint, semantic in semantic_constraints
                if semantic.get("kind") != "preserve_state"
            ),
            None,
        )
        constraint, semantic = primary if primary is not None else (None, None)
        kind = semantic.get("kind") if isinstance(semantic, dict) else None
        if kind == "set_state":
            canonical_state = _canonical_state_goal(semantic, constraint, view)
            identity = canonical_json(canonical_state)
            goals.append(identity)
            operation_by_id[node.milestone_id] = identity

            tool_name = _tool_name_from_node(node)
            if tool_name is not None:
                tool_name = (tool_aliases or {}).get(tool_name, tool_name)
                expansion_count = 1
                if canonical_state.get("cardinality") == "all":
                    rows = _origin_expected_rows(semantic.get("expected"))
                    if len(rows) > 1:
                        expansion_count = len(rows)
                    elif view is not None:
                        ns = str(canonical_state.get("namespace"))
                        initial_rows = _initial_namespace_rows(ns, view)
                        if len(initial_rows) > 1:
                            match_dict = canonical_state.get("match", {})
                            if isinstance(match_dict, dict) and match_dict:
                                matched = [
                                    r for r in initial_rows
                                    if all(
                                        not isinstance(v, (str, int, float, bool))
                                        or r.get(k) == v
                                        for k, v in match_dict.items()
                                    )
                                ]
                                if len(matched) > 1:
                                    expansion_count = len(matched)
                            else:
                                expansion_count = len(initial_rows)
                for _ in range(expansion_count):
                    occurrence = occurrence_counts[tool_name]
                    occurrence_counts[tool_name] += 1
                    operations.append(tool_name)
        elif kind == "emit_message":
            identity = canonical_json({key: semantic.get(key) for key in ("kind", "sender", "recipient", "content")})
            goals.append(identity)
            operation_by_id[node.milestone_id] = identity
        else:
            tool_name = _tool_name_from_node(node)
            if tool_name is None:
                continue
            tool_name = (tool_aliases or {}).get(tool_name, tool_name)
            occurrence = occurrence_counts[tool_name]
            occurrence_counts[tool_name] += 1
            operations.append(tool_name)
            operation_by_id[node.milestone_id] = canonical_json({"tool_name": tool_name, "occurrence_index": occurrence})
    topology = sorted(
        canonical_json([operation_by_id[source], operation_by_id[target]])
        for source, target in graph.edges
        if source in operation_by_id and target in operation_by_id
    )
    minefields = sorted(
        identity for item in graph.minefields if item.severity == "fatal"
        and (
            identity := _minefield_identity(
                item, tool_aliases or {}, view
            )
        ) is not None
    )
    raw_dispositions = graph.metadata.get("turn_dispositions", {})
    dispositions = (
        [canonical_json({"turn_id": str(key), "disposition": str(value)})
         for key, value in sorted(raw_dispositions.items())]
        if isinstance(raw_dispositions, dict) else []
    )
    return {
        "dispositions": dispositions,
        "goals": sorted(goals),
        "operations": sorted(operations),
        "topology": topology,
        "minefields": minefields,
        "preserves": sorted(preserves),
    }

def compare_input_coverage(
    reference: MilestoneGraph,
    view: GeneratorTaskView,
    tool_aliases: dict[str, str] | None = None,
) -> JsonObject:
    """Checks whether the specific tool for reference exists in the public TOOL_CALL event."""
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
        if isinstance(semantic, dict):
            if isinstance(semantic.get("tool_name"), str) and semantic["tool_name"]:
                return str(semantic["tool_name"])
            if isinstance(semantic.get("executor_tool_name"), str) and semantic["executor_tool_name"]:
                return str(semantic["executor_tool_name"])
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

def _minefield_identity(
    item: object,
    aliases: dict[str, str],
    view: GeneratorTaskView | None,
) -> str | None:
    tool_name = _tool_name_from_constraints(getattr(item, "constraints", []))
    tool_name = aliases.get(tool_name, tool_name) if tool_name else None
    metadata = getattr(item, "metadata", {})
    reason_code = metadata.get("reason_code") if isinstance(metadata, dict) else None
    if not isinstance(reason_code, str) and tool_name is not None:
        required = _required_tool_arguments(tool_name, view)
        arguments = next(
            (
                semantic.get("arguments")
                for constraint in getattr(item, "constraints", [])
                if isinstance(
                    (semantic := getattr(
                        constraint, "stage_goal_semantics", None
                    )),
                    dict,
                )
                and semantic.get("kind") == "tool_call"
            ),
            None,
        )
        if required and isinstance(arguments, dict) and not required.issubset(arguments):
            reason_code = "missing_required_input"
        elif required and (arguments is None or not arguments):
            reason_code = "missing_required_input"
        else:
            contract = view.tool_contracts.get(tool_name, {}) if view is not None else {}
            writes = contract.get("writes", []) if isinstance(contract, dict) else []
            state_effect = contract.get("state_effect", []) if isinstance(contract, dict) else []
            reason_code = (
                "unsafe_side_effect"
                if (isinstance(writes, list) and writes)
                or (isinstance(state_effect, (list, dict)) and state_effect)
                else "unsafe_tool_call"
            )
    return canonical_json({
        "tool_name": tool_name, "severity": "fatal", "reason_code": reason_code,
    }) if tool_name else None


def _required_tool_arguments(
    tool_name: str, view: GeneratorTaskView | None
) -> set[str]:
    if view is None:
        return set()
    tools = view.tool_schema.get("tools", [])
    if not isinstance(tools, list):
        return set()
    for item in tools:
        function = item.get("function", {}) if isinstance(item, dict) else {}
        if not isinstance(function, dict) or function.get("name") != tool_name:
            continue
        parameters = function.get("parameters", {})
        required = parameters.get("required", []) if isinstance(parameters, dict) else []
        return {str(field) for field in required} if isinstance(required, list) else set()
    return set()


def _canonical_goal_value(value: object) -> object:
    """Keeps a public value and places the runtime producer output in a dynamic position."""
    if isinstance(value, dict):
        if value.get("source") == "node_output":
            return {
                "source": "dynamic",
                "cardinality": value.get("cardinality"),
            }
        if value.get("source") == "public_literal":
            return _canonical_goal_value(value.get("value"))
        return {str(key): _canonical_goal_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_goal_value(item) for item in value]
    return value


def _canonical_state_goal(
    semantic: JsonObject,
    constraint: object,
    view: GeneratorTaskView | None,
) -> JsonObject:
    """The public semantic symbolic goal is harmonized with the public seman snapshot goal."""
    if semantic.get("operation") in {"add", "update", "remove", "set"}:
        return {
            key: _canonical_goal_value(semantic.get(key))
            for key in (
                "kind", "namespace", "operation", "cardinality", "match", "values"
            )
        }
    namespace = str(semantic.get("namespace") or getattr(constraint, "namespace", ""))
    operation = _origin_state_operation(namespace, constraint)
    rows = _origin_expected_rows(semantic.get("expected"))
    cardinality = "all" if len(rows) > 1 else "one"
    match_fields, value_fields = _origin_state_fields(
        namespace, operation, rows, view
    )
    return {
        "kind": "set_state",
        "namespace": namespace,
        "operation": operation,
        "cardinality": cardinality,
        "match": _origin_group_identity(
            rows, match_fields, cardinality, view, dynamic=True
        ),
        "values": _origin_group_identity(
            rows, value_fields, cardinality, view, dynamic=False
        ),
    }


def _origin_state_operation(namespace: str, constraint: object) -> str | None:
    metadata = getattr(constraint, "metadata", {})
    toolsandbox = metadata.get("toolsandbox", {}) if isinstance(metadata, dict) else {}
    measure = (
        toolsandbox.get("snapshot_constraint")
        if isinstance(toolsandbox, dict) else None
    )
    operation = {
        "addition_similarity": "add",
        "update_similarity": "update",
        "removal_similarity": "remove",
    }.get(str(measure))
    return "set" if namespace == "SETTING" and operation == "update" else operation


def _origin_expected_rows(expected: object) -> list[JsonObject]:
    if isinstance(expected, dict):
        return [expected]
    if isinstance(expected, list):
        return [item for item in expected if isinstance(item, dict)]
    return []


def _origin_state_fields(
    namespace: str,
    operation: str | None,
    rows: list[JsonObject],
    view: GeneratorTaskView | None,
) -> tuple[set[str], set[str]]:
    row_fields = {str(key) for row in rows for key in row}
    required, allowed = _state_contract_fields(
        namespace, operation, row_fields, view
    )
    if operation == "add":
        return set(), (required or allowed or row_fields)
    if operation == "remove":
        return (required or allowed or row_fields), set()
    if operation == "set":
        return set(), (required or allowed or row_fields)
    initial_rows = _initial_namespace_rows(namespace, view)
    changed: set[str] = set()
    for row in rows:
        reference = _matching_initial_row(row, initial_rows, required)
        if reference is None:
            changed.update(row)
            continue
        changed.update(
            str(key) for key, value in row.items()
            if reference.get(key) != value
        )
    return required, (changed - required) & (allowed or changed)


def _state_contract_fields(
    namespace: str,
    operation: str | None,
    row_fields: set[str],
    view: GeneratorTaskView | None,
) -> tuple[set[str], set[str]]:
    if view is None:
        return set(), set()
    candidates: list[tuple[str, JsonObject]] = []
    for name, value in view.tool_contracts.items():
        if not isinstance(value, dict):
            continue
        effect = value.get("effect")
        if (
            isinstance(effect, dict)
            and effect.get("namespace") == namespace
            and effect.get("operation") == operation
        ):
            candidates.append((str(name), value))
    if not candidates:
        return set(), set()
    ranked = sorted(
        candidates,
        key=lambda item: (
            -len(set(item[1].get("required_dynamic_inputs", [])) & row_fields),
            item[0],
        ),
    )
    name, contract = ranked[0]
    required = {
        str(field) for field in contract.get("required_dynamic_inputs", [])
    }
    argument_fields = _tool_argument_fields(name, view)
    executor_arguments = contract.get("executor_arguments", {})
    if isinstance(executor_arguments, dict):
        allowed = {
            str(executor_arguments.get(field, field)) for field in argument_fields
        }
    else:
        allowed = argument_fields
    return required, allowed


def _tool_argument_fields(tool_name: str, view: GeneratorTaskView) -> set[str]:
    tools = view.tool_schema.get("tools", [])
    if not isinstance(tools, list):
        return set()
    for item in tools:
        function = item.get("function", {}) if isinstance(item, dict) else {}
        if not isinstance(function, dict) or function.get("name") != tool_name:
            continue
        parameters = function.get("parameters", {})
        properties = (
            parameters.get("properties", {})
            if isinstance(parameters, dict) else {}
        )
        return {str(field) for field in properties} if isinstance(properties, dict) else set()
    return set()


def _initial_namespace_rows(
    namespace: str, view: GeneratorTaskView | None
) -> list[JsonObject]:
    if view is None:
        return []
    namespaces = view.simulation_state.get("namespaces", {})
    rows = namespaces.get(namespace, []) if isinstance(namespaces, dict) else []
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _matching_initial_row(
    target: JsonObject,
    initial_rows: list[JsonObject],
    required: set[str],
) -> JsonObject | None:
    exact = [
        row for row in initial_rows
        if required and all(row.get(field) == target.get(field) for field in required)
    ]
    if len(exact) == 1:
        return exact[0]
    ranked = sorted(
        initial_rows,
        key=lambda row: -sum(
            row.get(field) == value for field, value in target.items()
        ),
    )
    return ranked[0] if ranked else None


def _origin_group_identity(
    rows: list[JsonObject],
    fields: set[str],
    cardinality: str,
    view: GeneratorTaskView | None,
    *,
    dynamic: bool,
) -> JsonObject:
    result: JsonObject = {}
    for field in sorted(fields):
        values = [row.get(field) for row in rows if field in row]
        if not values:
            continue
        if dynamic or len({canonical_json(value) for value in values}) > 1:
            result[field] = {"source": "dynamic", "cardinality": cardinality}
            continue
        value = values[0]
        result[field] = (
            value if _origin_value_is_public(value, field, view)
            else {"source": "dynamic", "cardinality": "one"}
        )
    return result


def _origin_value_is_public(
    value: object,
    field: str,
    view: GeneratorTaskView | None,
) -> bool:
    if isinstance(value, bool) or value is None:
        return True
    if view is None or isinstance(value, (dict, list)):
        return False
    text = str(value).strip().casefold()
    if not text:
        return False
    public_text = "\n".join(
        [turn.instruction for turn in view.turns]
        + [
            str(item.get("value", ""))
            for item in view.public_assets
            if item.get("visibility", "agent") == "agent"
        ]
    ).casefold()
    if text in public_text:
        return True
    return False
