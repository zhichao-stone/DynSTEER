from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dynsteer.graph import (
    build_adjacency,
    enrich_milestone_graph,
    topological_order,
    transitive_reduction,
)
from dynsteer.milestone.model import (
    GenerationReport,
    GeneratorTaskView,
    MilestoneGenerationConfig,
    PublicEvidence,
    TurnDisposition,
)
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    JsonObject,
    JsonValue,
    LLMMessage,
    Milestone,
    MilestoneGraph,
    Minefield,
    MinefieldPenalty,
    Operator,
    StageGoalSemanticKind,
)
from dynsteer.prompt.template import MilestonePromptBuilder
from dynsteer.utils import json_safe, record_raw_response, stable_json_digest


if TYPE_CHECKING:
    from dynsteer.llm.base import BaseLLM


logger = logging.getLogger(__name__)
_DISPOSITIONS = {"executable", "needs_clarification", "no_action", "response_only"}
_NODE_KINDS = {"tool_call", "set_state", "emit_message"}
_STATE_OPERATIONS = {"add", "update", "remove", "set"}
_CARDINALITIES = {"one", "all"}
_MINEFIELD_REASONS = {"missing_required_input", "tool_unavailable", "unsafe_side_effect", "unsafe_tool_call"}
_STATE_FIELD_ALIASES = {
    "wifi_enabled": "wifi",
    "cellular_enabled": "cellular",
    "cellular_service_enabled": "cellular",
    "location_service_enabled": "location_service",
    "low_battery_mode_enabled": "low_battery_mode",
}
_NODE_FATAL_ISSUES = {
    "unknown_evidence", "invalid_arguments", "invalid_state_goal",
    "state_contract_mismatch", "state_contract_unscorable",
    "incomplete_state_goal", "state_required_input_missing",
    "invalid_message_route", "empty_content_requirement",
}
_FIELD_LOCAL_ISSUES = {
    "missing_tool_argument", "unknown_tool_argument", "invalid_value_source",
    "unknown_public_source", "public_literal_mismatch", "invalid_output_binding",
    "argument_enum_mismatch", "argument_type_mismatch",
    "argument_required_property_missing", "argument_unknown_property",
    "state_field_unknown",
}
_FORBIDDEN_KEYS = {
    "milestone_matcher", "minefield_matcher", "evaluation", "verifier",
    "reference_graph", "trajectory", "final_state", "simulation_state", "tool_contracts",
    "environment_rules",
}
_GRAPH_FIELDS = {"dispositions", "nodes", "edges", "minefields"}
_NODE_FIELDS = {
    "tool_call": {"local_id", "turn_id", "kind", "evidence_id", "arguments"},
    "set_state": {
        "local_id", "turn_id", "kind", "namespace", "operation", "cardinality",
        "match", "values", "executor_evidence_id",
    },
    "emit_message": {
        "local_id", "turn_id", "kind", "sender", "recipient", "content_requirement",
    },
}
_MINEFIELD_FIELDS = {
    "turn_id", "evidence_id", "severity", "reason_code", "missing_inputs",
}


def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_file: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]:
    """Generates a stand-alone candidate map, which is verified with certainty and compiled by a strict majority of the polymers."""
    if view is None or config is None or llm is None:
        raise ValueError('View, Config and llm cannot be empty')
    evidence = _evidence_catalog(view)
    prompts = MilestonePromptBuilder(view, config)
    _assert_no_forbidden_generation_inputs(prompts.payload)
    observations: list[_CanonicalGraphObservation] = []
    candidates: list[JsonObject] = []
    issues: list[JsonObject] = []
    repair_actions: list[JsonObject] = []
    response_digests: list[str] = []
    raw_records: dict[str, JsonObject] = {}
    counters: Counter[str] = Counter()

    # Each is an independent request; it is immediately suspended when the target has been achieved.
    for batch_index in range(config.max_candidate_batch_count):
        if len(observations) >= config.target_candidate_graph_count:
            break
        counters["request_count"] += 1
        raw, request_ok = _request_candidate_batch(
            prompts.generation(batch_index, _batch_focus(batch_index)), llm
        )
        if request_ok:
            counters["request_success_count"] += 1
            response_digests.append(stable_json_digest(raw))
        if request_ok:
            record_raw_response(
                raw, response_output_file, f"batch_{batch_index + 1:02d}", raw_records
            )
        parsed, batch_issues, top_level_ok, returned = _parse_candidate_batch(raw, batch_index)
        issues.extend(batch_issues)
        counters["returned_graph_count"] += returned
        counters["rejected_graph_count"] += returned - len(parsed)
        if top_level_ok:
            counters["top_level_success_count"] += 1
        counters["parsed_graph_count"] += len(parsed)
        canonical: list[_CanonicalGraphObservation] = []
        for graph_index, candidate in parsed:
            candidate, normalization_repairs = _normalize_candidate_graph(
                candidate, view, evidence, batch_index, graph_index
            )
            repair_actions.extend(normalization_repairs)
            for action in normalization_repairs:
                counters[str(action.get("action", "unknown"))] += 1
            candidate, repairs = _repair_candidate_graph(
                candidate, view, evidence, batch_index, graph_index
            )
            repair_actions.extend(repairs)
            validation = _validate_candidate_graph(
                candidate, view, evidence, batch_index, graph_index
            )
            issues.extend(validation.issues)
            if validation.graph is None:
                counters["rejected_graph_count"] += 1
                counters["fatal_parse_graph_count"] += 1
                candidates.append(_candidate_summary(batch_index, graph_index, "rejected", None, "fatal_parse"))
                continue
            if validation.status == "valid":
                counters["valid_graph_count"] += 1
            else:
                counters["partial_graph_count"] += 1
                counters["node_pruned_in_partial_count"] += validation.pruned_node_count
                counters["binding_unresolved_count"] += validation.unresolved_binding_count
                counters["field_unresolved_count"] += validation.field_unresolved_count
            observation = _canonicalize_candidate_graph(
                validation.graph, view, batch_index, graph_index, validation.status
            )
            canonical.append(observation)
        accepted, duplicate_count = _deduplicate_within_batch(canonical)
        counters["within_batch_duplicate_count"] += duplicate_count
        remaining = config.target_candidate_graph_count - len(observations)
        selected = accepted[:remaining]
        overflow = accepted[remaining:]
        for observation in canonical:
            status = (
                "accepted" if observation in selected
                else "overflow" if observation in overflow
                else "within_batch_duplicate"
            )
            candidates.append(_candidate_summary(
                batch_index, observation.graph_index, status, observation.signature,
                observation.status,
            ))
        observations.extend(selected)

    signature_counts = Counter(item.signature for item in observations)
    generation_failed = counters["top_level_success_count"] == 0
    if generation_failed:
        graph = _empty_graph(view, "generation_failed")
        support: JsonObject = {}
        dispositions = {turn.turn_id: "response_only" for turn in view.turns}
    else:
        graph, dispositions, support, aggregation_issues = _aggregate_and_compile(
            observations, view, evidence
        )
        issues.extend(aggregation_issues)
        counters["field_unresolved_count"] += sum(
            item.metadata.get("field_binding_status") == "unresolved"
            for item in graph.nodes
        )
    issue_codes = {str(item.get("code")) for item in issues}
    empty_reason = (
        "generation_failed" if generation_failed
        else "all_candidates_rejected" if not observations and counters["parsed_graph_count"]
        else "no_observation" if not observations
        else None if graph.nodes or graph.minefields
        else "empty_after_binding_closure" if issue_codes & {
            "unresolved_state_binding", "dangling_argument_binding", "node_pruned",
        }
        else "empty_after_state_projection" if counters["terminal_state_projection"]
        else "empty_after_terminal_majority" if issue_codes & {
            "orphan_support_chain", "disposition_conflict",
        }
        else "empty_after_aggregation"
    )
    graph.metadata.update({
        "source": "generated", "view_digest": view.digest(),
        "turn_dispositions": dispositions,
        "disposition": next(iter(dispositions.values()), "no_action"),
        "empty_reason": empty_reason,
    })
    report = GenerationReport(
        generation_status="generation_failed" if generation_failed else "generated",
        turn_dispositions=dispositions,
        target_candidate_graph_count=config.target_candidate_graph_count,
        max_candidate_batch_count=config.max_candidate_batch_count,
        request_count=counters["request_count"],
        request_success_count=counters["request_success_count"],
        returned_graph_count=counters["returned_graph_count"],
        parsed_graph_count=counters["parsed_graph_count"],
        valid_graph_count=counters["valid_graph_count"],
        accepted_observation_count=len(observations),
        global_unique_graph_count=len(signature_counts),
        within_batch_duplicate_count=counters["within_batch_duplicate_count"],
        rejected_graph_count=counters["rejected_graph_count"],
        target_reached=len(observations) >= config.target_candidate_graph_count,
        graph_returned=not generation_failed,
        graph_empty=not graph.nodes and not graph.minefields,
        empty_reason=empty_reason,
        aggregated_node_count=len(graph.nodes),
        aggregated_edge_count=len(graph.edges),
        minefield_count=len(graph.minefields),
        low_sample_count=len(observations) < config.target_candidate_graph_count,
        low_diversity=len(signature_counts) < 2,
        partial_graph_count=counters["partial_graph_count"],
        fatal_parse_graph_count=counters["fatal_parse_graph_count"],
        node_pruned_in_partial_count=counters["node_pruned_in_partial_count"],
        binding_unresolved_count=counters["binding_unresolved_count"],
        repaired_graph_count=len({
            (item.get("batch_index"), item.get("graph_index"))
            for item in repair_actions
        }),
        repair_action_count=len(repair_actions),
        terminal_state_projected_count=counters["terminal_state_projection"],
        state_field_repaired_count=counters["state_field"],
        literal_derivation_count=sum(
            str(item.get("code")) == "literal_derivation" for item in issues
        ),
        field_unresolved_count=counters["field_unresolved_count"],
        empty_after_terminal_majority_count=int(
            empty_reason == "empty_after_terminal_majority"
        ),
        cross_request_signature_counts=dict(sorted(signature_counts.items())),
        repair_actions=tuple(repair_actions),
        candidate_summaries=tuple(candidates),
        aggregation_support=support,
        validation_issues=tuple(issues),
        response_digests=tuple(response_digests),
        reasons=tuple(str(item.get("message", item.get("code", ""))) for item in issues),
    )
    logger.info(
        'Milestone graph aggregation completed.',
        extra={'event': 'Milestone candidates were aggregated', 'case_id': view.case_id,
               'Observations': len(observations), 'Nodes': len(graph.nodes), 'Edges': len(graph.edges)},
    )
    return graph, report


@dataclass(frozen=True)
class _ValueSource:
    source: Literal["public_literal", "node_output"]
    source_ref: str | None = None
    value: JsonValue = None
    producer_local_id: str | None = None
    selector: str | None = None
    cardinality: Literal["one", "all"] | None = None


@dataclass(frozen=True)
class _CandidateNode:
    local_id: str
    turn_id: str
    kind: Literal["tool_call", "set_state", "emit_message"]
    data: JsonObject


@dataclass(frozen=True)
class _CandidateMinefield:
    turn_id: str
    evidence_id: str
    severity: str
    reason_code: str
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True)
class _CandidateGraph:
    dispositions: tuple[tuple[str, TurnDisposition], ...]
    nodes: tuple[_CandidateNode, ...]
    edges: tuple[tuple[str, str], ...]
    minefields: tuple[_CandidateMinefield, ...]


@dataclass(frozen=True)
class _CanonicalNode:
    key: str
    identity: JsonObject
    candidate: _CandidateNode
    local_nodes: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class _ValidationResult:
    graph: _CandidateGraph | None
    status: Literal["valid", "partial", "fatal_parse"]
    issues: tuple[JsonObject, ...] = ()
    pruned_node_count: int = 0
    unresolved_binding_count: int = 0
    field_unresolved_count: int = 0


@dataclass(frozen=True)
class _CanonicalGraphObservation:
    batch_index: int
    graph_index: int
    status: Literal["valid", "partial"]
    dispositions: tuple[tuple[str, TurnDisposition], ...]
    nodes: tuple[_CanonicalNode, ...]
    edges: tuple[tuple[str, str], ...]
    minefields: tuple[_CandidateMinefield, ...]
    signature: str


def _request_candidate_batch(prompt: str, llm: BaseLLM) -> tuple[str, bool]:
    try:
        return llm.chat([LLMMessage(role="user", content=prompt)], response_format="json_object"), True
    except Exception as exc:  # LLM boundary: Failed to convert to auditable batch.
        logger.warning('failed to obtain an LLM response', extra={'event': 'failed to obtain an LLM response', 'error': str(exc)})
        return "", False


def _batch_focus(batch_index: int) -> str:
    return ("minimality", "alternative", "dependency_safety")[batch_index % 3]


def _parse_candidate_batch(
    raw: str, batch_index: int
) -> tuple[list[tuple[int, _CandidateGraph]], list[JsonObject], bool, int]:
    issues: list[JsonObject] = []
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return [], [_violation("batch_json_error", batch_index, message=f"Batch JSON invalid:{exc}")], False, 0
    if not isinstance(payload, dict) or set(payload) != {"graphs"} or not isinstance(payload.get("graphs"), list):
        return [], [_violation("batch_schema_error", batch_index, message='Top layers, graphs, must be arrays.')], False, 0
    raw_graphs = payload["graphs"]
    if len(raw_graphs) > 2:
        return [], [_violation("batch_schema_error", batch_index, message="We can't get more than two singles.")], False, len(raw_graphs)
    if len(raw_graphs) < 2:
        issues.append(_violation("batch_incomplete", batch_index, message='Less than two individual returns of candidate maps'))
    parsed: list[tuple[int, _CandidateGraph]] = []
    for graph_index, value in enumerate(raw_graphs):
        try:
            parsed.append((graph_index, _parse_candidate_graph(value)))
        except (TypeError, ValueError) as exc:
            issues.append(_violation(
                "graph_schema_error", batch_index, graph_index, message=f"Candidature structure is invalid:{exc}"
            ))
    return parsed, issues, True, len(raw_graphs)


def _parse_candidate_graph(value: object) -> _CandidateGraph:
    if not isinstance(value, dict):
        raise TypeError('Graph must be an object')
    if set(value) != _GRAPH_FIELDS:
        raise ValueError('Graph must and can only include dispositions/nodes/edges/minefields')
    dispositions = value.get("dispositions")
    nodes = value.get("nodes")
    edges = value.get("edges")
    minefields = value.get("minefields")
    if not isinstance(dispositions, dict) or not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(minefields, list):
        raise TypeError('Dispositions/nodes/edges/minefields type error')
    parsed_nodes: list[_CandidateNode] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise TypeError('Node must be an object')
        local_id, turn_id, kind = node.get("local_id"), node.get("turn_id"), node.get("kind")
        if not all(isinstance(item, str) for item in (local_id, turn_id, kind)):
            raise TypeError('Node identification field must be a string')
        if kind not in _NODE_FIELDS or set(node) != _NODE_FIELDS[kind]:
            raise ValueError(f"{kind} node fields do not match the exact schema")
        parsed_nodes.append(_CandidateNode(local_id, turn_id, kind, json_safe(node)))
    parsed_edges: list[tuple[str, str]] = []
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            raise TypeError('edge must be [source_local_id, target_local_id] binary array')
        source, target = edge
        if not isinstance(source, str) or not isinstance(target, str):
            raise TypeError('The edge end must be a string')
        parsed_edges.append((source, target))
    parsed_minefields: list[_CandidateMinefield] = []
    for item in minefields:
        if not isinstance(item, dict):
            raise TypeError('Minefield must be an object')
        if set(item) != _MINEFIELD_FIELDS:
            raise ValueError('Minefield field does not match precision schema')
        missing = item["missing_inputs"]
        if not isinstance(missing, list) or not all(isinstance(field, str) for field in missing):
            raise TypeError('missing_inputs must be string arrays')
        if not all(isinstance(item.get(key), str) for key in ("turn_id", "evidence_id", "severity", "reason_code")):
            raise TypeError('Minefield identifier field must be a string')
        parsed_minefields.append(_CandidateMinefield(
            item["turn_id"], item["evidence_id"], item["severity"], item["reason_code"],
            tuple(sorted(missing)),
        ))
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in dispositions.items()):
        raise TypeError('Dispositions must be the object of a string to a string')
    return _CandidateGraph(
        tuple(sorted((str(key), str(item)) for key, item in dispositions.items())),
        tuple(parsed_nodes), tuple(parsed_edges), tuple(parsed_minefields),
    )


def _normalize_candidate_graph(
    candidate: _CandidateGraph,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
    batch_index: int,
    graph_index: int,
) -> tuple[_CandidateGraph, list[JsonObject]]:
    """Performs a terminal state projection, application and state field certainty combination before general restoration."""
    repairs: list[JsonObject] = []
    outgoing_ids = {source for source, _ in candidate.edges}
    normalized_nodes: list[_CandidateNode] = []

    def record(node_id: str, action: str, before: object, after: object, basis: str) -> None:
        repairs.append({
            "batch_index": batch_index, "graph_index": graph_index,
            "node_id": node_id, "action": action,
            "before": before, "after": after, "basis": basis,
        })

    for node in candidate.nodes:
        if (
            node.kind == "tool_call" and node.local_id not in outgoing_ids
            and (projected := _project_terminal_state_node(node, view, evidence)) is not None
        ):
            normalized_nodes.append(projected)
            record(
                node.local_id, "terminal_state_projection", "tool_call", "set_state",
                "terminal tool has unique state effect and no downstream consumer",
            )
            continue
        if node.kind == "set_state":
            state_node, state_repairs = _normalize_state_node(
                node, view, evidence, batch_index, graph_index
            )
            normalized_nodes.append(state_node)
            repairs.extend(state_repairs)
            continue
        normalized_nodes.append(node)
    return _CandidateGraph(
        candidate.dispositions, tuple(normalized_nodes), candidate.edges,
        candidate.minefields,
    ), repairs


def _project_terminal_state_node(
    node: _CandidateNode, view: GeneratorTaskView, evidence: dict[str, PublicEvidence]
) -> _CandidateNode | None:
    """Project the non-output terminal status tool as set_state goal."""
    evidence_id = str(node.data.get("evidence_id"))
    contract = _contract_for_evidence(evidence_id, view, evidence)
    effect = contract.get("effect")
    state_fields = contract.get("state_fields")
    if (
        contract.get("state_evaluator") != "toolsandbox_snapshot"
        or contract.get("outputs") != {}
        or not isinstance(effect, dict) or not isinstance(state_fields, dict)
    ):
        return None
    state_arguments = defaultdict(list)
    executor_arguments = contract.get("executor_arguments", {})
    if isinstance(executor_arguments, dict):
        for argument, state_field in executor_arguments.items():
            state_arguments[str(state_field)].append(str(argument))
    argument_targets = {
        arguments[0]: state_field
        for state_field, arguments in state_arguments.items()
        if len(arguments) == 1
    }
    match: JsonObject = {}
    values: JsonObject = {}
    arguments = node.data.get("arguments", {})
    if not isinstance(arguments, dict):
        return None
    for argument, source in sorted(arguments.items()):
        state_field = argument_targets.get(
            str(argument), str(argument) if argument in state_fields else None
        )
        if state_field is None or state_field not in state_fields:
            return None
        metadata = state_fields[state_field]
        role = metadata.get("role", "value") if isinstance(metadata, dict) else "value"
        target = match if role == "match" else values
        target[state_field] = source
    return _CandidateNode(node.local_id, node.turn_id, "set_state", {
        "local_id": node.local_id, "turn_id": node.turn_id, "kind": "set_state",
        "namespace": effect.get("namespace"), "operation": effect.get("operation"),
        "cardinality": "one", "match": match, "values": values,
        "executor_evidence_id": evidence_id,
    })


def _normalize_state_node(
    node: _CandidateNode, view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence], batch_index: int, graph_index: int,
) -> tuple[_CandidateNode, list[JsonObject]]:
    """Fix the state application with the only field aliases by the public executor contract."""
    repairs: list[JsonObject] = []
    data = dict(node.data)
    evidence_id = str(data.get("executor_evidence_id"))
    contract = _contract_for_evidence(evidence_id, view, evidence)
    effect = contract.get("effect") if isinstance(contract, dict) else None
    if (
        data.get("namespace") == "MESSAGING" and data.get("operation") == "send"
        and isinstance(effect, dict) and effect.get("operation") == "add"
    ):
        before = data["operation"]
        data["operation"] = "add"
        repairs.append({
            "batch_index": batch_index, "graph_index": graph_index,
            "node_id": node.local_id, "action": "state_operation",
            "before": before, "after": "add", "basis": "executor state effect contract",
        })
    state_fields = contract.get("state_fields") if isinstance(contract, dict) else {}
    if isinstance(state_fields, dict) and state_fields:
        for group in ("match", "values"):
            fields = dict(data.get(group, {})) if isinstance(data.get(group, {}), dict) else {}
            for field in list(fields):
                repaired = _STATE_FIELD_ALIASES.get(str(field))
                if (
                    repaired is not None and field not in state_fields
                    and repaired in state_fields and repaired not in fields
                ):
                    source = fields.pop(field)
                    fields[repaired] = source
                    data[group] = fields
                    repairs.append({
                        "batch_index": batch_index, "graph_index": graph_index,
                        "node_id": node.local_id, "action": "state_field",
                        "field": f"{group}.{repaired}", "before": field,
                        "after": repaired, "basis": "unique state field alias",
                    })
    return replace(node, data=data), repairs


def _repair_candidate_graph(
    candidate: _CandidateGraph,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
    batch_index: int,
    graph_index: int,
) -> tuple[_CandidateGraph, list[JsonObject]]:
    """Execute the only determinable restoration source_ref and output binding before semantic verification."""
    repairs: list[JsonObject] = []
    nodes_by_id = {node.local_id: node for node in candidate.nodes}
    public_sources = {
        *(turn.source_ref for turn in view.turns),
        *(str(item.get("source_ref")) for item in view.public_assets),
    }

    def record(node_id: str, field: str, action: str, before: object, after: object, basis: str) -> None:
        repairs.append({
            "batch_index": batch_index, "graph_index": graph_index,
            "node_id": node_id, "field": field, "action": action,
            "before": before, "after": after, "basis": basis,
        })

    for node in candidate.nodes:
        for group in ("arguments", "match", "values"):
            values = node.data.get(group, {})
            if not isinstance(values, dict):
                continue
            for field, source in list(values.items()):
                if not isinstance(source, dict):
                    continue
                if source.get("source") == "public_literal":
                    source_ref = source.get("source_ref")
                    if isinstance(source_ref, str) and source_ref not in public_sources and source_ref.endswith(":0"):
                        matches = {item for item in public_sources if item == source_ref[:-2]}
                        if len(matches) == 1:
                            source["source_ref"] = next(iter(matches))
                            record(node.local_id, f"{group}.{field}", "source_ref", source_ref, source["source_ref"], "unique_zero_suffix_alias")
                elif source.get("source") == "node_output":
                    producer = nodes_by_id.get(str(source.get("producer_local_id")))
                    producer_evidence = evidence.get(str(producer.data.get("evidence_id"))) if producer is not None else None
                    contract = _contract_for_evidence(str(producer.data.get("evidence_id")), view, evidence) if producer_evidence is not None else {}
                    outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
                    if not isinstance(outputs, dict) or not outputs:
                        continue
                    selectors = sorted({
                        str(item.get("selector"))
                        for item in outputs.values()
                        if isinstance(item, dict) and isinstance(item.get("selector"), str)
                    })
                    selector = source.get("selector")
                    repaired_selector: str | None = None
                    if isinstance(selector, str) and selector not in selectors:
                        normalized = re.sub(r"\[\d+\]", "", selector)
                        if len(selectors) == 1:
                            repaired_selector = selectors[0]
                        elif normalized in selectors:
                            repaired_selector = normalized
                        elif "$" in selectors and selector in {"$.timestamp", "$.value", "$.result"}:
                            repaired_selector = "$"
                        if repaired_selector is not None:
                            source["selector"] = repaired_selector
                            record(node.local_id, f"{group}.{field}", "selector", selector, repaired_selector, "unique_output_contract")
                    output = next((
                        item for item in outputs.values()
                        if isinstance(item, dict) and item.get("selector") == source.get("selector")
                    ), None)
                    cardinalities = (
                        [item for item in output.get("cardinality", []) if item in _CARDINALITIES]
                        if isinstance(output, dict) else []
                    )
                    if len(cardinalities) == 1 and source.get("cardinality") != cardinalities[0]:
                        before = source.get("cardinality")
                        source["cardinality"] = cardinalities[0]
                        record(node.local_id, f"{group}.{field}", "cardinality", before, cardinalities[0], "unique_output_contract")
    return candidate, repairs


def _validate_candidate_graph(
    candidate: _CandidateGraph,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
    batch_index: int,
    graph_index: int,
) -> _ValidationResult:
    """Approve the candidate map; local errors remove only local nodes or binting and do not discard the whole map."""
    issues: list[JsonObject] = []
    turn_ids = {turn.turn_id for turn in view.turns}
    turn_order = {turn.turn_id: index for index, turn in enumerate(view.turns)}
    dispositions = dict(candidate.dispositions)
    if set(dispositions) != turn_ids or any(value not in _DISPOSITIONS for value in dispositions.values()):
        issues.append(_violation("invalid_dispositions", batch_index, graph_index, message='Disposition must accurately overwrite all turn'))
        return _ValidationResult(None, "fatal_parse", tuple(issues))

    kept_nodes: list[_CandidateNode] = []
    unresolved_fields = 0
    seen_ids: set[str] = set()
    for node in candidate.nodes:
        if not node.local_id or node.local_id in seen_ids:
            issues.append(_violation("duplicate_node_id", batch_index, graph_index, node_id=node.local_id, message='Local_id is empty or repeated'))
            continue
        seen_ids.add(node.local_id)
        if node.turn_id not in turn_ids or node.kind not in _NODE_KINDS:
            issues.append(_violation("invalid_node", batch_index, graph_index, node.turn_id, node.local_id, message='Node turn or kind is invalid'))
            continue
        if node.kind in {"tool_call", "set_state"} and dispositions.get(node.turn_id) != "executable":
            issues.append(_violation("node_on_non_executable_turn", batch_index, graph_index, node.turn_id, node.local_id, message='Non-executable turn cannot contain tools or status targets'))
            continue
        node_issues = _validate_node(node, view, evidence, batch_index, graph_index)
        issues.extend(node_issues)
        fatal_issues = [item for item in node_issues if str(item.get("code")) in _NODE_FATAL_ISSUES]
        local_issues = [item for item in node_issues if str(item.get("code")) in _FIELD_LOCAL_ISSUES]
        if fatal_issues:
            continue
        if local_issues:
            node, _removed_fields = _remove_local_issue_fields(node, local_issues)
            unresolved_fields += len(local_issues)
            for item in local_issues:
                issues.append(_violation(
                    "field_binding_unresolved", batch_index, graph_index,
                    node.turn_id, node.local_id, str(item.get("field")),
                    'Local progress/ schema error downgraded',
                ))
            if node.kind == "set_state":
                contract = _contract_for_evidence(
                    str(node.data.get("executor_evidence_id")), view, evidence
                )
                state_fields = set(node.data.get("match", {})) | set(node.data.get("values", {}))
                if not set(contract.get("required_dynamic_inputs", [])).issubset(state_fields):
                    issues.append(_violation(
                        "state_required_input_missing", batch_index, graph_index,
                        node.turn_id, node.local_id,
                        message='Status target missing dynamic input for executor contract requirements',
                    ))
                    continue
        kept_nodes.append(node)

    # Distinguished across nodes. Unable to close the necessary field removes the corresponding node.
    pruned_nodes: set[str] = set()
    unresolved_bindings = 0
    binding_edges: set[tuple[str, str]] = set()
    node_by_id = {node.local_id: node for node in kept_nodes}
    for node in kept_nodes:
        if node.local_id in pruned_nodes:
            continue
        for group in ("arguments", "match", "values"):
            values = node.data.get(group, {})
            if not isinstance(values, dict):
                continue
            for field, source in list(values.items()):
                if not isinstance(source, dict) or source.get("source") != "node_output":
                    continue
                producer_id = str(source.get("producer_local_id"))
                producer = node_by_id.get(producer_id)
                producer_evidence = evidence.get(str(producer.data.get("evidence_id"))) if producer is not None else None
                contract = _contract_for_evidence(str(producer.data.get("evidence_id")), view, evidence) if producer_evidence is not None else {}
                outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
                output = next((
                    item for item in outputs.values() if isinstance(item, dict)
                    and item.get("selector") == source.get("selector")
                ), None) if isinstance(outputs, dict) else None
                valid = (
                    producer is not None and producer.kind == "tool_call"
                    and producer.turn_id in turn_order
                    and turn_order[producer.turn_id] <= turn_order[node.turn_id]
                    and isinstance(output, dict)
                    and source.get("cardinality") in output.get("cardinality", [])
                    and _schema_types_compatible(
                        output.get("type"),
                        _binding_target_schema(node, group, str(field), view, evidence).get("type"),
                    )
                )
                if valid:
                    assert producer is not None
                    binding_edges.add((producer.local_id, node.local_id))
                    continue
                unresolved_bindings += 1
                issues.append(_violation(
                    "binding_unresolved", batch_index, graph_index, node.turn_id,
                    node.local_id, f"{group}.{field}", 'Producer output binding',
                ))
                values.pop(field, None)
                required_missing = False
                if node.kind == "tool_call":
                    schema = _tool_parameter_schema(_tool_name(evidence[str(node.data["evidence_id"])]), view)
                    required_missing = field in schema.get("required", [])
                else:
                    executor_contract = _contract_for_evidence(
                        str(node.data.get("executor_evidence_id")), view, evidence
                    )
                    required_missing = field in executor_contract.get("required_dynamic_inputs", [])
                if required_missing:
                    pruned_nodes.add(node.local_id)
                    break

    retained_nodes: list[_CandidateNode] = []
    for node in kept_nodes:
        if node.local_id in pruned_nodes:
            issues.append(_violation("node_pruned", batch_index, graph_index, node.turn_id, node.local_id, message='Must binding cannot close, delete this node'))
            continue
        if node.kind == "set_state":
            operation, cardinality = node.data.get("operation"), node.data.get("cardinality")
            match, values = node.data.get("match", {}), node.data.get("values", {})
            incomplete = (
                operation not in _STATE_OPERATIONS
                or cardinality not in _CARDINALITIES
                or not isinstance(match, dict)
                or not isinstance(values, dict)
                or (operation in {"add", "update", "set"} and not values)
                or (operation == "remove" and not match)
            )
            if incomplete:
                issues.append(_violation("state_goal_pruned", batch_index, graph_index, node.turn_id, node.local_id, message='Status target incomplete after partial repair'))
                continue
        retained_nodes.append(node)

    retained_ids = {node.local_id for node in retained_nodes}
    explicit_edges: list[tuple[str, str]] = []
    for source, target in candidate.edges:
        if source not in retained_ids or target not in retained_ids or source == target:
            issues.append(_violation("invalid_edge", batch_index, graph_index, node_id=source, message='Erge endpoint does not exist or self-ring'))
            continue
        if (source, target) not in explicit_edges:
            explicit_edges.append((source, target))
        else:
            issues.append(_violation("duplicate_edge", batch_index, graph_index, message='Erge, repeat.'))
    # After the node has been cut, previously recorded binting edge may be suspended; this is discarded together.
    valid_binding_edges = {
        edge for edge in binding_edges
        if edge[0] in retained_ids and edge[1] in retained_ids
    }
    ordered_edges = sorted(dict.fromkeys([*explicit_edges, *valid_binding_edges]))
    accepted_edges = _acyclic_edges(retained_ids, ordered_edges, issues)

    for turn_id, disposition in dispositions.items():
        if disposition == "executable" and not any(
            node.turn_id == turn_id and _terminal_node(node, view) for node in retained_nodes
        ):
            issues.append(_violation("missing_terminal_goal", batch_index, graph_index, turn_id, message='Exactable turn missing terminal target'))

    kept_minefields: list[_CandidateMinefield] = []
    for raw_minefield in candidate.minefields:
        contract = _contract_for_evidence(raw_minefield.evidence_id, view, evidence)
        tool_name = _tool_name(evidence[raw_minefield.evidence_id])
        reason = _normalized_minefield_reason(raw_minefield, contract, view, tool_name)
        minefield = replace(raw_minefield, reason_code=reason) if reason is not None else raw_minefield
        if reason == "missing_required_input":
            unavailable = _minefield_unavailable_inputs(minefield, candidate, view, evidence)
            minefield = replace(minefield, missing_inputs=tuple(sorted(unavailable)))
        valid = (
            reason is not None
            and minefield.turn_id in turn_ids
            and minefield.severity == "fatal"
            and bool(contract)
            and (
                minefield.reason_code != "unsafe_side_effect"
                or bool(contract.get("writes"))
            )
            and (
                minefield.reason_code != "unsafe_tool_call"
                or not contract.get("writes")
            )
            and not (
                contract.get("writes")
                and _write_operation_recoverable(minefield, view, evidence)
            )
            and (
                minefield.reason_code != "missing_required_input"
                or (
                    bool(minefield.missing_inputs)
                    and set(minefield.missing_inputs).issubset(
                        _minefield_allowed_missing_inputs(contract, tool_name, view)
                    )
                )
            )
        )
        if not valid:
            issues.append(_violation("invalid_minefield", batch_index, graph_index, minefield.turn_id, message='Minefield cannot be verified by the current tool contract'))
            continue
        conflicts = any(
            node.turn_id == minefield.turn_id
            and (
                node.data.get("executor_evidence_id") == minefield.evidence_id
                or node.data.get("evidence_id") == minefield.evidence_id
            )
            for node in retained_nodes
        )
        if conflicts:
            issues.append(_violation("minefield_terminal_conflict", batch_index, graph_index, minefield.turn_id, message="The same tool can't be called simultaneously"))
            continue
        kept_minefields.append(minefield)

    original_has_semantics = bool(candidate.nodes or candidate.minefields)
    repaired_has_semantics = bool(retained_nodes or kept_minefields)
    if original_has_semantics and not repaired_has_semantics:
        return _ValidationResult(
            None, "fatal_parse", tuple(issues), len(candidate.nodes),
            unresolved_bindings, unresolved_fields,
        )
    graph = _CandidateGraph(
        candidate.dispositions, tuple(retained_nodes), tuple(accepted_edges), tuple(kept_minefields)
    )
    status: Literal["valid", "partial"] = "valid" if not issues else "partial"
    return _ValidationResult(
        graph, status, tuple(issues),
        len(candidate.nodes) - len(retained_nodes), unresolved_bindings,
        unresolved_fields,
    )

def _remove_local_issue_fields(
    node: _CandidateNode, issues: list[JsonObject]
) -> tuple[_CandidateNode, int]:
    """downgrades the field level to delete the corresponding field instead of deleting the node."""
    data = dict(node.data)
    removed: set[str] = set()
    for issue in issues:
        field = str(issue.get("field") or "")
        if not field:
            continue
        if node.kind == "tool_call" and field.startswith("arguments."):
            parts = field.split(".", 2)
            if len(parts) >= 2:
                name = parts[1]
                arguments = dict(data.get("arguments", {}))
                if name in arguments:
                    arguments.pop(name)
                    data["arguments"] = arguments
                    removed.add(f"arguments.{name}")
        elif node.kind == "set_state":
            name = field.split(".", 1)[0]
            for group in ("match", "values"):
                values = dict(data.get(group, {}))
                if name in values:
                    values.pop(name)
                    data[group] = values
                    removed.add(f"{group}.{name}")
    return replace(node, data=data), len(removed)


def _validate_node(
    node: _CandidateNode, view: GeneratorTaskView, evidence: dict[str, PublicEvidence],
    batch_index: int, graph_index: int,
) -> list[JsonObject]:
    issues: list[JsonObject] = []
    data = node.data
    if node.kind == "emit_message":
        if data.get("sender") != "AGENT" or data.get("recipient") != "USER":
            issues.append(_violation("invalid_message_route", batch_index, graph_index, node.turn_id, node.local_id, message='Emit_message must be AGENTUSER'))
        if not isinstance(data.get("content_requirement"), str) or not str(data["content_requirement"]).strip():
            issues.append(_violation("empty_content_requirement", batch_index, graph_index, node.turn_id, node.local_id, message='Message requests cannot be empty.'))
        return issues
    evidence_id = data.get("evidence_id") if node.kind == "tool_call" else data.get("executor_evidence_id")
    if not isinstance(evidence_id, str) or evidence_id not in evidence:
        return [_violation("unknown_evidence", batch_index, graph_index, node.turn_id, node.local_id, message='event_id does not exist')]
    if node.kind == "tool_call":
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            return [_violation("invalid_arguments", batch_index, graph_index, node.turn_id, node.local_id, message='Aguments must be objects')]
        schema = _tool_parameter_schema(_tool_name(evidence[evidence_id]), view)
        issues.extend(_validate_tool_arguments(arguments, schema, node, view, batch_index, graph_index))
    else:
        operation, cardinality = data.get("operation"), data.get("cardinality")
        match, values = data.get("match", {}), data.get("values", {})
        contract = _contract_for_evidence(evidence_id, view, evidence)
        effect = contract.get("effect") if isinstance(contract, dict) else None
        if operation not in _STATE_OPERATIONS or cardinality not in _CARDINALITIES or not isinstance(match, dict) or not isinstance(values, dict):
            issues.append(_violation("invalid_state_goal", batch_index, graph_index, node.turn_id, node.local_id, message='Status target structure is invalid'))
        elif not isinstance(effect, dict) or effect.get("namespace") != data.get("namespace") or effect.get("operation") != operation:
            issues.append(_violation("state_contract_mismatch", batch_index, graph_index, node.turn_id, node.local_id, message='Status target is not in line with tool effect contract'))
        elif contract.get("state_evaluator") != "toolsandbox_snapshot":
            issues.append(_violation(
                "state_contract_unscorable", batch_index, graph_index,
                node.turn_id, node.local_id,
                message='adapter does not declare status rating contract supported by compiler',
            ))
        elif (operation == "add" and not values) or (operation in {"update", "set"} and not values) or (operation == "remove" and not match):
            issues.append(_violation("incomplete_state_goal", batch_index, graph_index, node.turn_id, node.local_id, message='Status target missing match or value'))
        elif not set(contract.get("required_dynamic_inputs", [])).issubset(set(match) | set(values)):
            issues.append(_violation("state_required_input_missing", batch_index, graph_index, node.turn_id, node.local_id, message='Status target missing dynamic input for executor contract requirements'))
        state_fields = contract.get("state_fields", {})
        if isinstance(state_fields, dict) and state_fields:
            for field in (set(match) | set(values)) - set(state_fields):
                group = "match" if field in match else "values"
                issues.append(_violation(
                    "state_field_unknown", batch_index, graph_index, node.turn_id,
                    node.local_id, f"{group}.{field}", 'The state field is not in the executor public contract',
                ))
        for field, source in [*match.items(), *values.items()]:
            source_issues = _validate_value_source(
                source, node, view, batch_index, graph_index, field
            )
            issues.extend(source_issues)
            if (
                not source_issues
                and isinstance(source, dict)
                and source.get("source") == "public_literal"
            ):
                schema = _binding_target_schema(
                    node, "state", str(field), view, evidence
                )
                issues.extend(_validate_schema_value(
                    source.get("value"), schema, batch_index, graph_index,
                    node, str(field),
                ))
    return issues


def _minefield_allowed_missing_inputs(
    contract: JsonObject, tool_name: str, view: GeneratorTaskView
) -> set[str]:
    """Write effect contracts or read-only tools schema obtains a collection of input that can be claimed to be missing."""
    if not isinstance(contract, dict):
        return set()
    required = {
        str(item) for item in contract.get("required_dynamic_inputs", [])
        if isinstance(item, str)
    }
    if contract.get("writes"):
        return required
    schema_required = _tool_parameter_schema(tool_name, view).get("required", [])
    return required | {
        str(item) for item in schema_required if isinstance(item, str)
    }


def _normalized_minefield_reason(
    minefield: _CandidateMinefield, contract: JsonObject,
    view: GeneratorTaskView, tool_name: str,
) -> str | None:
    """One candidate by public contract, reason, avoids the same kind of fatal tool being spell-out by reason."""
    reason = minefield.reason_code
    if reason not in _MINEFIELD_REASONS or not isinstance(contract, dict):
        return None
    required = _minefield_allowed_missing_inputs(contract, tool_name, view)
    missing_valid = (
        bool(minefield.missing_inputs)
        and set(minefield.missing_inputs).issubset(required)
    )
    if reason == "missing_required_input" and not missing_valid:
        return None if contract.get("writes") else "unsafe_tool_call"
    if reason == "unsafe_side_effect" and not contract.get("writes"):
        return "unsafe_tool_call"
    if reason == "unsafe_tool_call" and contract.get("writes"):
        return None
    return reason


def _minefield_unavailable_inputs(
    minefield: _CandidateMinefield, candidate: _CandidateGraph,
    view: GeneratorTaskView, evidence: dict[str, PublicEvidence],
) -> set[str]:
    """Removes fields for which a candidate map or task view is recoverable, returns the missing input."""
    missing = set(minefield.missing_inputs)
    for node in candidate.nodes:
        if node.turn_id != minefield.turn_id:
            continue
        for group in ("arguments", "match", "values"):
            values = node.data.get(group, {})
            if isinstance(values, dict):
                missing.difference_update(values)
    return {
        field for field in missing
        if not _minefield_input_recoverable(field, minefield, view, evidence)
    }


def _minefield_input_recoverable(
    field: str, minefield: _CandidateMinefield,
    view: GeneratorTaskView, evidence: dict[str, PublicEvidence],
) -> bool:
    """Whether a single missing input can be restored from a publicly available task text and a visible producer contract."""
    evidence_item = evidence.get(minefield.evidence_id)
    if evidence_item is None:
        return False
    tool_name = _tool_name(evidence_item)
    schema = _tool_parameter_schema(tool_name, view)
    properties = schema.get("properties", {})
    target_type = properties.get(field, {}).get("type") if isinstance(properties, dict) else None
    instructions = _minefield_recovery_instructions(view)
    return (
        _public_literal_field_recoverable(field, target_type, instructions)
        or _timestamp_input_recoverable(field, target_type, view, evidence, instructions)
        or _producer_output_recoverable(field, target_type, view, evidence, instructions)
    )


def _write_operation_recoverable(
    minefield: _CandidateMinefield, view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
) -> bool:
    """The contract for writing the operation shall not be reported as fatal if it must be fully recoverable."""
    contract = _contract_for_evidence(minefield.evidence_id, view, evidence)
    required = {
        str(item) for item in contract.get("required_dynamic_inputs", [])
        if isinstance(item, str)
    }
    return all(
        _minefield_input_recoverable(field, minefield, view, evidence)
        for field in required
    )


def _instruction_content_recoverable(instruction: str) -> bool:
    """Defines the natural language from the alarm/message command."""
    patterns = (
        r"(?is)\bremind me to\s+(.+?)(?=\s+(?:tomorrow|next|on|at|by|in)\b|$)",
        r"(?is)\breminder to\s+(.+?)(?=\s+(?:tomorrow|next|on|at|by|in)\b|$)",
    )
    return any(
        (match := re.search(pattern, instruction)) is not None
        and bool(match.group(1).strip(" .?!"))
        for pattern in patterns
    )


def _minefield_recovery_instructions(view: GeneratorTaskView) -> tuple[str, ...]:
    """Only for reliability aggregations, collects agent commands and evaluator-only task commands."""
    return (
        *(turn.instruction for turn in view.turns),
        *(
            str(item.get("value")) for item in view.public_assets
            if isinstance(item.get("value"), str)
            and item.get("visibility") == "evaluator_only"
        ),
    )


def _public_literal_field_recoverable(
    field: str, target_type: object, instructions: tuple[str, ...]
) -> bool:
    """To determine whether structured public metrics can be restored by the only expression in the instruction."""
    for instruction in instructions:
        date_components = _unique_date_components(instruction)
        time_components = _unique_time_components(instruction)
        if field in date_components or field in time_components:
            return True
        if field == "phone_number" and re.search(r"(?<!\d)\+?\d{7,15}(?!\d)", instruction):
            return True
        if field == "content" and _instruction_content_recoverable(instruction):
            return True
        if target_type in {"integer", "number"} and _numeric_unit_value(instruction, field) is not None:
            return True
    return False


def _timestamp_input_recoverable(
    field: str, target_type: object, view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence], instructions: tuple[str, ...],
) -> bool:
    """Only timestamp with a single date plus time and a visible conversion tool is considered recoverable."""
    if (
        target_type != "number"
        or not (field == "timestamp" or field.endswith("_timestamp"))
    ):
        return False
    if _relative_date_recoverable(view, evidence, instructions):
        return True
    for instruction in instructions:
        if not (_unique_date_components(instruction) and _unique_time_components(instruction)):
            continue
        for item in evidence.values():
            tool_name = _tool_name(item)
            contract = _contract_for_evidence(item.evidence_id, view, evidence)
            outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
            schema = _tool_parameter_schema(tool_name, view)
            required = {
                str(value) for value in schema.get("required", [])
                if isinstance(value, str)
            }
            if (
                not contract.get("writes")
                and required == {"year", "month", "day", "hour", "minute", "second"}
                and any(
                    isinstance(output, dict) and output.get("type") == "number"
                    for output in outputs.values()
                )
            ):
                return True
    return False


def _relative_date_recoverable(
    view: GeneratorTaskView, evidence: dict[str, PublicEvidence],
    instructions: tuple[str, ...],
) -> bool:
    """Restores a clear relative date synonym when the current time and date decomposition tool is visible."""
    relative_date = any(
        re.search(
            r"(?is)\b(?:tomorrow|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|(?:in|after)\s+(?:\d+|one|two)\s+(?:day|week)s?)\b",
            instruction,
        ) is not None
        for instruction in instructions
    )
    if not relative_date:
        return False
    has_current = has_datetime_info = False
    for item in evidence.values():
        tool_name = _tool_name(item)
        contract = _contract_for_evidence(item.evidence_id, view, evidence)
        outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
        schema = _tool_parameter_schema(tool_name, view)
        required = set(schema.get("required", []))
        numeric_output = any(
            isinstance(output, dict) and output.get("type") == "number"
            for output in outputs.values()
        )
        tokens = set(re.split(r"_", tool_name.casefold()))
        if (
            not contract.get("writes") and not required and numeric_output
            and {"current", "timestamp"}.issubset(tokens)
        ):
            has_current = True
        if (
            not contract.get("writes") and required == {"timestamp"}
            and {"year", "month", "day", "isoweekday"}.issubset(outputs)
        ):
            has_datetime_info = True
    return has_current and has_datetime_info


def _producer_output_recoverable(
    field: str, target_type: object, view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence], instructions: tuple[str, ...],
) -> bool:
    """Finds the same name or suffix field in a read-only producer output with the synonym of the task."""
    instruction_text = " ".join(instructions).casefold()
    for item in evidence.values():
        tool_name = _tool_name(item)
        if not _producer_semantically_relevant(tool_name, instruction_text):
            continue
        contract = _contract_for_evidence(item.evidence_id, view, evidence)
        outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
        schema = _tool_parameter_schema(tool_name, view)
        required = schema.get("required", [])
        if contract.get("writes") or not isinstance(outputs, dict) or required:
            continue
        for output_name, output in outputs.items():
            compatible = (
                isinstance(output, dict)
                and (output_name == field or output_name.endswith(f"_{field}"))
                and output.get("cardinality") == ["one"]
                and _schema_types_compatible(output.get("type"), target_type)
            )
            if compatible:
                return True
    return False


def _producer_semantically_relevant(tool_name: str, instruction_text: str) -> bool:
    """Use non-common word character overlap to bind producer to restore and avoid using irrelevant full searches as a source."""
    generic_tokens = {"search", "get", "find", "read", "list", "query", "with", "to", "by"}
    tokens = {
        token.removesuffix("s")
        for token in re.split(r"[_]+", tool_name.casefold())
        if token.isalpha() and token not in generic_tokens
    }
    return any(re.search(rf"\b{re.escape(token)}\b", instruction_text) for token in tokens)


def _validate_tool_arguments(
    arguments: JsonObject, schema: JsonObject, node: _CandidateNode, view: GeneratorTaskView,
    batch_index: int, graph_index: int,
) -> list[JsonObject]:
    issues: list[JsonObject] = []
    properties = schema.get("properties", {}) if isinstance(schema.get("properties", {}), dict) else {}
    required = schema.get("required", []) if isinstance(schema.get("required", []), list) else []
    for name in required:
        if name not in arguments:
            issues.append(_violation("missing_tool_argument", batch_index, graph_index, node.turn_id, node.local_id, f"arguments.{name}", 'Lack of required tool parameters'))
    if schema.get("additionalProperties") is False:
        for name in set(arguments) - set(properties):
            issues.append(_violation("unknown_tool_argument", batch_index, graph_index, node.turn_id, node.local_id, f"arguments.{name}", 'Include Unknown Tool Parameters'))
    for name, value in arguments.items():
        source_issues = _validate_value_source(value, node, view, batch_index, graph_index, f"arguments.{name}")
        issues.extend(source_issues)
        if not source_issues and isinstance(value, dict) and value.get("source") == "public_literal":
            issues.extend(_validate_schema_value(value.get("value"), properties.get(name, {}), batch_index, graph_index, node, f"arguments.{name}"))
    return issues


def _validate_value_source(
    value: object, node: _CandidateNode, view: GeneratorTaskView,
    batch_index: int, graph_index: int, field: str,
) -> list[JsonObject]:
    parsed = _value_source(value)
    if parsed is None:
        return [_violation("invalid_value_source", batch_index, graph_index, node.turn_id, node.local_id, field, 'Value Source Invalid')]
    assert isinstance(value, dict)
    if parsed.source == "public_literal":
        if set(value) != {"source", "source_ref", "value"}:
            return [_violation("invalid_value_source", batch_index, graph_index, node.turn_id, node.local_id, field, 'Public_literal field does not match accurate schema')]
        turn_sources = {turn.source_ref: turn.instruction for turn in view.turns}
        asset_sources = {str(item.get("source_ref")): item.get("value") for item in view.public_assets if item.get("visibility", "agent") == "agent"}
        public_state_sources = _public_state_sources(view.public_state)
        source_ref = parsed.source_ref
        if source_ref not in turn_sources and source_ref not in asset_sources and source_ref not in public_state_sources:
            return [_violation("unknown_public_source", batch_index, graph_index, node.turn_id, node.local_id, field, 'Public source is unknown')]
        literal = parsed.value
        if source_ref in asset_sources:
            asset_value = asset_sources[str(source_ref)]
            literal_visible = (
                _literal_appears_in_instruction(literal, asset_value, field)
                if isinstance(asset_value, str) else asset_value == literal
            )
            if not literal_visible:
                return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, 'Public literal does not match source values')]
        if source_ref in turn_sources and not _literal_appears_in_instruction(
            literal, turn_sources[str(source_ref)], field
        ):
            if _public_literal_derived_from_instruction(
                literal, turn_sources[str(source_ref)], field
            ):
                return [_violation(
                    "literal_derivation", batch_index, graph_index, node.turn_id,
                    node.local_id, field, 'Public literal is derived from structural expression',
                )]
            return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, 'Public literal does not appear in instruction')]
        if source_ref in public_state_sources and public_state_sources[str(source_ref)] != literal:
            return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, 'Public source value does not match the public_state source value')]
    else:
        if set(value) != {"source", "producer_local_id", "selector", "cardinality"}:
            return [_violation("invalid_output_binding", batch_index, graph_index, node.turn_id, node.local_id, field, 'Node_output field does not match precision schema')]
        if not isinstance(value.get("producer_local_id"), str) or not isinstance(value.get("selector"), str) or value.get("cardinality") not in _CARDINALITIES:
            return [_violation("invalid_output_binding", batch_index, graph_index, node.turn_id, node.local_id, field, 'Node_output binting')]
    return []


def _value_source(value: object) -> _ValueSource | None:
    """Converts the exact source object to an internal immutable representation of the compiler."""
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    if source == "public_literal":
        return _ValueSource(
            source="public_literal",
            source_ref=(
                str(value["source_ref"])
                if isinstance(value.get("source_ref"), str) else None
            ),
            value=value.get("value"),
        )
    if source == "node_output":
        cardinality = value.get("cardinality")
        return _ValueSource(
            source="node_output",
            producer_local_id=(
                str(value["producer_local_id"])
                if isinstance(value.get("producer_local_id"), str) else None
            ),
            selector=(
                str(value["selector"])
                if isinstance(value.get("selector"), str) else None
            ),
            cardinality=(
                cardinality if cardinality in _CARDINALITIES else None
            ),
        )
    return None


def _public_state_sources(public_state: JsonObject) -> dict[str, JsonValue]:
    """Expand open-state leaves to stabilize`public_state:<path>`Source."""
    result: dict[str, JsonValue] = {}

    def visit(value: JsonValue, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, (*path, str(key)))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))
            return
        if path:
            result[f"public_state:{'.'.join(path)}"] = value

    visit(public_state, ())
    return result


def _validate_schema_value(
    value: JsonValue, schema: object, batch_index: int, graph_index: int,
    node: _CandidateNode, field: str,
) -> list[JsonObject]:
    if not isinstance(schema, dict) or not schema:
        return []
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        return [_violation("argument_enum_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, 'Parameters do not belong to enum')]
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    checks = {"object": lambda item: isinstance(item, dict), "array": lambda item: isinstance(item, list),
              "string": lambda item: isinstance(item, str), "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
              "integer": lambda item: isinstance(item, int) and not isinstance(item, bool), "boolean": lambda item: isinstance(item, bool),
              "null": lambda item: item is None}
    if expected is not None and not any(checks.get(str(item), lambda _: True)(value) for item in types):
        return [_violation("argument_type_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, 'Parameter type does not match schema')]
    issues: list[JsonObject] = []
    if isinstance(value, dict):
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required", []) if isinstance(schema.get("required"), list) else []
        for name in required:
            if name not in value:
                issues.append(_violation("argument_required_property_missing", batch_index, graph_index, node.turn_id, node.local_id, f"{field}.{name}", 'Object Missing Required Fields'))
        if schema.get("additionalProperties") is False:
            for name in set(value) - set(properties):
                issues.append(_violation("argument_unknown_property", batch_index, graph_index, node.turn_id, node.local_id, f"{field}.{name}", 'Object contains unknown fields'))
        for name, item in value.items():
            issues.extend(_validate_schema_value(item, properties.get(name, {}), batch_index, graph_index, node, f"{field}.{name}"))
    elif isinstance(value, list):
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            issues.extend(_validate_schema_value(item, item_schema, batch_index, graph_index, node, f"{field}[{index}]"))
    return issues


def _canonicalize_candidate_graph(
    candidate: _CandidateGraph, view: GeneratorTaskView, batch_index: int,
    graph_index: int, status: Literal["valid", "partial"],
) -> _CanonicalGraphObservation:
    base_by_id = {
        node.local_id: _node_identity(node, view, candidate)
        for node in candidate.nodes
    }
    predecessors = {node.local_id: [] for node in candidate.nodes}
    successors = {node.local_id: [] for node in candidate.nodes}
    for source, target in candidate.edges:
        if source not in predecessors or target not in successors:
            continue
        predecessors[target].append(source)
        successors[source].append(target)
    order, _ = topological_order(predecessors, successors)
    order_index = {local_id: index for index, local_id in enumerate(order)}
    canonical_nodes: list[_CanonicalNode] = []
    local_to_key: dict[str, str] = {}
    grouped: dict[str, list[_CandidateNode]] = defaultdict(list)
    for node in candidate.nodes:
        grouped[stable_json_digest(base_by_id[node.local_id])].append(node)

    # (a) The same operation in the same observation, which retains only one representative node;
    # Local_nodes still records all local IDs for dynamic binding solver.
    key_by_digest = {
        base_digest: stable_json_digest(base_by_id[nodes[0].local_id])
        for base_digest, nodes in grouped.items()
    }
    for node in candidate.nodes:
        local_to_key[node.local_id] = key_by_digest[
            stable_json_digest(base_by_id[node.local_id])
        ]
    local_nodes = tuple(sorted((
        node.local_id,
        local_to_key[node.local_id],
        node.turn_id,
        str(node.data.get("evidence_id") or node.data.get("executor_evidence_id") or ""),
    ) for node in candidate.nodes))
    for base_digest, nodes in sorted(grouped.items()):
        representative = min(nodes, key=lambda item: (
            order_index[item.local_id],
            tuple(sorted(stable_json_digest(base_by_id[source]) for source in predecessors[item.local_id])),
            tuple(sorted(stable_json_digest(base_by_id[target]) for target in successors[item.local_id])),
            item.local_id,
        ))
        canonical_nodes.append(_CanonicalNode(
            key_by_digest[base_digest], base_by_id[representative.local_id], representative, local_nodes,
        ))
    edges = tuple(sorted((local_to_key[source], local_to_key[target]) for source, target in candidate.edges))
    minefields = tuple(sorted(candidate.minefields, key=lambda item: (item.turn_id, item.evidence_id, item.reason_code, item.missing_inputs)))
    signature = stable_json_digest({
        "dispositions": candidate.dispositions,
        "nodes": sorted(item.key for item in canonical_nodes),
        "edges": edges,
        "minefields": json_safe(minefields),
    })
    return _CanonicalGraphObservation(batch_index, graph_index, status, candidate.dispositions, tuple(canonical_nodes), edges, minefields, signature)


def _node_identity(
    node: _CandidateNode, view: GeneratorTaskView,
    candidate: _CandidateGraph | None = None,
) -> JsonObject:
    """Draws a stable cross-candidature presence; field binting is determined by an independent majority."""
    data = node.data
    if node.kind == "emit_message":
        if candidate is not None and _has_dynamic_answer_producer(node, candidate, view):
            return {
                "turn_id": node.turn_id, "kind": node.kind,
                "answer": _dynamic_answer_identity(node, candidate, view),
            }
        return {
            "turn_id": node.turn_id, "kind": node.kind,
            "sender": str(data.get("sender", "")).upper(),
            "recipient": str(data.get("recipient", "")).upper(),
            "content_requirement": " ".join(str(data.get("content_requirement", "")).split()).casefold(),
        }
    if node.kind == "tool_call":
        return {
            "turn_id": node.turn_id, "kind": node.kind,
            "evidence_id": str(data.get("evidence_id")),
        }
    bindings: JsonObject = {}
    for group in ("match", "values"):
        target = dict(data.get(group, {})) if isinstance(data.get(group, {}), dict) else {}
        bindings.update({
            f"{group}.{name}": _binding_intent(value)
            for name, value in sorted(target.items())
        })
    return {
        "turn_id": node.turn_id, "kind": node.kind,
        "namespace": str(data.get("namespace")), "operation": str(data.get("operation")),
        "state_intents": bindings,
    }


def _has_dynamic_answer_producer(
    node: _CandidateNode, candidate: _CandidateGraph, view: GeneratorTaskView
) -> bool:
    """Determines whether emit_message depends directly on tool output or calculation results."""
    nodes_by_id = {item.local_id: item for item in candidate.nodes}
    return any(
        (producer := nodes_by_id.get(source)) is not None
        and producer.kind == "tool_call"
        for source, target in candidate.edges
        if target == node.local_id
    )


def _dynamic_answer_identity(
    node: _CandidateNode, candidate: _CandidateGraph, view: GeneratorTaskView
) -> JsonObject:
    """Depending on the dynamic answer according to the deterministic producer category Identity, ignores the differences in the language."""
    nodes_by_id = {item.local_id: item for item in candidate.nodes}
    producers = [
        producer for source, target in candidate.edges if target == node.local_id
        and (producer := nodes_by_id.get(source)) is not None
        and producer.kind == "tool_call"
    ]
    evidence_by_id = {item.evidence_id: item for item in view.evidence_catalog}
    tools = sorted({
        _tool_name(evidence_by_id[str(item.data["evidence_id"])])
        for item in producers
        if str(item.data.get("evidence_id")) in evidence_by_id
    })
    if any(tool == "timestamp_diff" for tool in tools):
        answer_kind = "duration_answer"
    elif any(
        bool(contract.get("writes"))
        for tool in tools
        if isinstance((contract := view.tool_contracts.get(tool, {})), dict)
    ):
        answer_kind = "operation_confirmation"
    else:
        answer_kind = "tool_result_answer"
    return {"kind": answer_kind, "producer_tools": tools}

def _binding_intent(value: object) -> JsonObject:
    """Draws the steady binding semantic meaning of the intention to participate in the operation, ignoring the dynamic selector expression."""
    source = _value_source(value)
    if source is None:
        return {"mode": "invalid"}
    if source.source == "public_literal":
        return {"mode": "public"}
    return {"mode": "dynamic"}

def _public_literal_derived_from_instruction(
    value: JsonValue, instruction: str, field: str
) -> bool:
    """Determines whether the value of the mark can be derived from a structured date/time/quantity expression in instruction."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    field_name = field.rsplit(".", 1)[-1].casefold()
    numeric_value: int | None
    if isinstance(value, int):
        numeric_value = value
    elif isinstance(value, float) and value.is_integer():
        numeric_value = int(value)
    else:
        numeric_value = int(value) if str(value).isdigit() else None

    date_components = _unique_date_components(instruction)
    if field_name in {"year", "month", "day"} and numeric_value is not None:
        return date_components.get(field_name) == numeric_value
    if field_name in {"hour", "minute", "second"} and numeric_value is not None:
        return _unique_time_components(instruction).get(field_name) == numeric_value
    if numeric_value is not None:
        return _numeric_unit_value(instruction, field_name) == numeric_value
    return False


def _unique_date_components(instruction: str) -> dict[str, int]:
    """parsing the only determinable ISO or numerical slash date, without conjecture."""
    candidates: list[tuple[int, int, int]] = []
    for match in re.finditer(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", instruction):
        first, second, year = (int(match.group(index)) for index in (1, 2, 3))
        for month, day in ((first, second), (second, first)):
            try:
                date(year, month, day)
            except ValueError:
                continue
            candidates.append((year, month, day))
    for match in re.finditer(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", instruction):
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        try:
            date(year, month, day)
        except ValueError:
            continue
        candidates.append((year, month, day))
    unique = set(candidates)
    if len(unique) != 1:
        return {}
    year, month, day = next(iter(unique))
    return {"year": year, "month": month, "day": day}


def _unique_time_components(instruction: str) -> dict[str, int]:
    """Parsing AM/PM only; default minute and second are 0."""
    candidates: list[tuple[int, int, int]] = []
    for match in re.finditer(
        r"(?<!\d)(1[0-2]|0?[1-9])(?::([0-5]?\d))?\s*(am|pm)(?!\d)",
        instruction, re.IGNORECASE,
    ):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).casefold()
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        candidates.append((hour, minute, 0))
    if len(set(candidates)) != 1:
        return {}
    hour, minute, second = next(iter(set(candidates)))
    return {"hour": hour, "minute": minute, "second": second}


def _numeric_unit_value(instruction: str, field_name: str) -> int | None:
    """Parsing`2 days`The number of firm numbers consistent with the name of the unit."""
    singular = field_name.removesuffix("s")
    pattern = rf"(?<!\d)(\d+)(?:\.0+)?\s+{re.escape(singular)}s?(?!\w)"
    match = re.search(pattern, instruction, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _literal_appears_in_instruction(
    value: JsonValue, instruction: str, field: str = ""
) -> bool:
    """Confirms that the mark public value is derived from the corresponding input."""
    if value is None or isinstance(value, (dict, list)):
        return False
    normalized_instruction = instruction.casefold()
    normalized_value = str(value).strip().casefold()
    if normalized_value and normalized_value in normalized_instruction:
        return True
    if not isinstance(value, bool):
        return False
    field_name = field.rsplit(".", 1)[-1].replace("_", " ").casefold()
    positive = ("on", "enable", "enabled", 'Open', 'Open', 'Enable')
    negative = ("off", "disable", "disabled", 'Close', 'Disable', 'Disable')
    terms = positive if value else negative
    opposite = negative if value else positive
    if field_name and field_name != "on":
        field_position = normalized_instruction.find(field_name)
        if field_position >= 0:
            field_end = field_position + len(field_name)
            desired_forward = min(
                (
                    normalized_instruction.find(term, field_end) - field_end
                    for term in terms
                    if normalized_instruction.find(term, field_end) >= 0
                ),
                default=None,
            )
            opposite_forward = min(
                (
                    normalized_instruction.find(term, field_end) - field_end
                    for term in opposite
                    if normalized_instruction.find(term, field_end) >= 0
                ),
                default=None,
            )
            if desired_forward is not None or opposite_forward is not None:
                return (
                    desired_forward is not None
                    and (
                        opposite_forward is None
                        or desired_forward < opposite_forward
                    )
                )
            desired_distance = min(
                (
                    abs(normalized_instruction.find(term) - field_position)
                    for term in terms
                    if normalized_instruction.find(term) >= 0
                ),
                default=None,
            )
            opposite_distance = min(
                (
                    abs(normalized_instruction.find(term) - field_position)
                    for term in opposite
                    if normalized_instruction.find(term) >= 0
                ),
                default=None,
            )
            if desired_distance is not None:
                return (
                    opposite_distance is None
                    or desired_distance < opposite_distance
                )
            return False
    return any(term in normalized_instruction for term in terms) and not any(
        term in normalized_instruction for term in opposite
    )


def _binding_target_schema(
    node: _CandidateNode,
    group: str,
    field: str,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
) -> JsonObject:
    """Read node_output binding corresponding target field schema."""
    evidence_item = evidence.get(str(node.data.get("evidence_id")))
    field_name = field
    if node.kind == "set_state":
        evidence_item = evidence.get(str(node.data.get("executor_evidence_id")))
        if evidence_item is None:
            return {}
        contract = _contract_for_evidence(
            str(node.data.get("executor_evidence_id")), view, evidence
        )
        executor_arguments = contract.get("executor_arguments", {})
        if isinstance(executor_arguments, dict):
            field_name = next(
                (
                    str(argument)
                    for argument, state_field in executor_arguments.items()
                    if state_field == field
                ),
                field,
            )
    elif node.kind != "tool_call" or group != "arguments":
        return {}
    if evidence_item is None:
        return {}
    parameters = _tool_parameter_schema(_tool_name(evidence_item), view)
    properties = parameters.get("properties", {})
    schema = properties.get(field_name, {}) if isinstance(properties, dict) else {}
    return schema if isinstance(schema, dict) else {}


def _schema_types_compatible(output_type: object, target_type: object) -> bool:
    """Determines the compatibility of contract output with the tool 's target parameter statement type."""
    if output_type is None or target_type is None:
        return True
    output_types = set(output_type if isinstance(output_type, list) else [output_type])
    target_types = set(target_type if isinstance(target_type, list) else [target_type])
    if "integer" in output_types:
        output_types.add("number")
    if "integer" in target_types:
        target_types.add("number")
    return bool(output_types & target_types)


def _deduplicate_within_batch(
    observations: list[_CanonicalGraphObservation],
) -> tuple[list[_CanonicalGraphObservation], int]:
    accepted: list[_CanonicalGraphObservation] = []
    seen: set[str] = set()
    for observation in sorted(observations, key=lambda item: item.graph_index):
        if observation.signature in seen:
            continue
        seen.add(observation.signature)
        accepted.append(observation)
    return accepted, len(observations) - len(accepted)


def _aggregate_and_compile(
    observations: list[_CanonicalGraphObservation],
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
) -> tuple[MilestoneGraph, JsonObject, JsonObject, list[JsonObject]]:
    n = len(observations)
    issues: list[JsonObject] = []
    dispositions: JsonObject = {}
    for turn in view.turns:
        counts = Counter(dict(item.dispositions).get(turn.turn_id) for item in observations)
        majority = next((value for value, count in counts.items() if value and 2 * count > n), None)
        dispositions[turn.turn_id] = majority or "response_only"
        if majority is None and n:
            issues.append(_violation("disposition_conflict", message=f"{turn.turn_id} has no strict-majority disposition", turn_id=turn.turn_id))
    node_occurrences: dict[str, list[_CanonicalNode]] = defaultdict(list)
    for observation in observations:
        observed_keys: set[str] = set()
        for node in observation.nodes:
            if node.key in observed_keys:
                continue
            observed_keys.add(node.key)
            node_occurrences[node.key].append(node)
    # Set_state indicates the final side effect, allowing for retention when the candidate map is half-supported;
    # Normal tool_call still uses a strict majority, avoiding the introduction of too many read-only tools.
    kept = {
        key: values for key, values in node_occurrences.items()
        if (
            2 * len(values) > n
            or (values[0].candidate.kind == "set_state" and 2 * len(values) >= n)
        )
        and (
            dispositions.get(values[0].candidate.turn_id) == "executable"
            or values[0].candidate.kind == "emit_message"
        )
    }
    milestones: list[Milestone] = []
    key_to_id: dict[str, str] = {}
    support_nodes: JsonObject = {}
    for key, values in sorted(kept.items()):
        milestone = _compile_node(
            key,
            values,
            n,
            len({item.signature for item in observations}),
            evidence,
            view,
        )
        if milestone is None:
            issues.append(_violation("unresolved_state_binding", turn_id=values[0].candidate.turn_id, message='Dynamic binding not strictly majority'))
            continue
        milestones.append(milestone)
        key_to_id[key] = milestone.milestone_id
        support_nodes[key] = {"support_count": len(values), "observation_count": n}
    edge_support: Counter[tuple[str, str]] = Counter()
    edge_eligible: Counter[tuple[str, str]] = Counter()
    kept_keys = set(key_to_id)
    for source in kept_keys:
        for target in kept_keys - {source}:
            pair = (source, target)
            for observation in observations:
                present = {node.key for node in observation.nodes}
                if source in present and target in present:
                    edge_eligible[pair] += 1
                    if pair in observation.edges:
                        edge_support[pair] += 1
    selected_edges = sorted(
        (pair for pair, support in edge_support.items() if 2 * support > edge_eligible[pair]),
        key=lambda pair: (-edge_support[pair] / edge_eligible[pair], pair),
    )
    binding_edges = _binding_edges(kept, key_to_id, view, evidence)
    compiled_edges = _acyclic_edges(node_ids=set(key_to_id.values()), edges=binding_edges, issues=issues)
    edge_basis: dict[tuple[str, str], str] = {
        edge: "binding" for edge in compiled_edges
    }
    accepted_majority_edges: list[tuple[str, str]] = []
    for source, target in selected_edges:
        edge = (key_to_id[source], key_to_id[target])
        try:
            transitive_reduction(set(key_to_id.values()), [*compiled_edges, edge])
        except ValueError:
            issues.append(_violation(
                "edge_cycle_conflict", message=f"Skipped majority edge that would create a cycle: {edge}"
            ))
            continue
        compiled_edges.append(edge)
        accepted_majority_edges.append((source, target))
        edge_basis[edge] = "graph_ensemble_majority"
    milestones, compiled_edges = _add_recovery_dependencies(
        milestones, compiled_edges, view, evidence
    )
    for edge in compiled_edges:
        edge_basis.setdefault(edge, "recovery")
    turn_edges = _turn_order_edges(milestones, compiled_edges, view)
    compiled_edges.extend(turn_edges)
    edge_basis.update({edge: "turn_order" for edge in turn_edges})
    node_ids = {item.milestone_id for item in milestones}
    try:
        compiled_edges = transitive_reduction(node_ids, list(dict.fromkeys(compiled_edges)))
    except ValueError:
        compiled_edges = _acyclic_edges(node_ids, compiled_edges, issues)
        compiled_edges = transitive_reduction(node_ids, compiled_edges)
    milestones, compiled_edges, closure_issues = _validate_aggregated_closure(
        milestones, compiled_edges, dispositions, view
    )
    issues.extend(closure_issues)
    milestones, compiled_edges, prune_issues = _prune_dangling_producers(
        milestones, compiled_edges, view
    )
    issues.extend(prune_issues)
    node_ids = {item.milestone_id for item in milestones}
    compiled_edges = transitive_reduction(node_ids, [edge for edge in compiled_edges if edge[0] in node_ids and edge[1] in node_ids])
    minefields = _compile_minefields(observations, n, evidence, view)
    fatal_tools = {
        (str(item.metadata.get("turn_id")), str(item.constraints[0].expected))
        for item in minefields
    }
    conflict_turns = {
        str(item.metadata.get("turn_id"))
        for item in milestones
        if (str(item.metadata.get("turn_id")), _milestone_effect_tool_name(item)) in fatal_tools
    }
    if conflict_turns:
        for turn_id in sorted(conflict_turns):
            dispositions[turn_id] = "response_only"
            issues.append(_violation(
                "aggregated_minefield_terminal_conflict", turn_id=turn_id,
                message='Fatal minefield is downgraded to security_only',
            ))
        milestones = [
            item for item in milestones
            if str(item.metadata.get("turn_id")) not in conflict_turns
            or any(
                isinstance(constraint.stage_goal_semantics, dict)
                and constraint.stage_goal_semantics.get("kind") == "emit_message"
                for constraint in item.constraints
            )
        ]
        node_ids = {item.milestone_id for item in milestones}
        compiled_edges = [
            edge for edge in compiled_edges if edge[0] in node_ids and edge[1] in node_ids
        ]
    milestones = _ordered_milestones(milestones, compiled_edges, view)
    graph = enrich_milestone_graph(MilestoneGraph(nodes=milestones, edges=compiled_edges, minefields=minefields))
    for milestone in graph.nodes:
        anchor = graph.topology.stage_anchor_by_id[milestone.milestone_id]
        for constraint in milestone.constraints:
            if constraint.target == ConstraintTarget.STATE_SNAPSHOT and constraint.reference_milestone_id is None:
                constraint.reference_milestone_id = "initial" if anchor == "__start__" else anchor
    graph = _attach_preserve_constraints(graph, view)
    support: JsonObject = {"nodes": support_nodes, "edges": {
        f"{source}->{target}": {
            "support_count": edge_support[(source, target)],
            "eligible_count": edge_eligible[(source, target)],
            "dependency_basis": "graph_ensemble_majority",
        } for source, target in accepted_majority_edges
    }, "deterministic_edges": {
        f"{source}->{target}": edge_basis.get((source, target), "unknown")
        for source, target in compiled_edges
        if edge_basis.get((source, target)) != "graph_ensemble_majority"
    }}
    return graph, dispositions, support, issues


def _validate_aggregated_closure(
    milestones: list[Milestone],
    edges: list[tuple[str, str]],
    dispositions: JsonObject,
    view: GeneratorTaskView,
) -> tuple[list[Milestone], list[tuple[str, str]], list[JsonObject]]:
    """Deletes the irreconcilable nodes, binting and edge after the aggregation."""
    issues: list[JsonObject] = []
    kept = [
        item for item in milestones
        if dispositions.get(str(item.metadata.get("turn_id"))) == "executable"
        or any(
            isinstance(constraint.stage_goal_semantics, dict)
            and constraint.stage_goal_semantics.get("kind") == "emit_message"
            for constraint in item.constraints
        )
    ]
    changed = True
    while changed:
        changed = False
        milestone_ids = {item.milestone_id for item in kept}
        kept_edges = [
            edge for edge in edges
            if edge[0] in milestone_ids and edge[1] in milestone_ids
        ]
        predecessors, _ = build_adjacency(milestone_ids, kept_edges)
        reachable_predecessors: dict[str, set[str]] = {}
        for target in milestone_ids:
            pending = list(predecessors[target])
            reachable: set[str] = set()
            while pending:
                source = pending.pop()
                if source in reachable:
                    continue
                reachable.add(source)
                pending.extend(predecessors[source])
            reachable_predecessors[target] = reachable
        for milestone in list(kept):
            dangling = [
                constraint for constraint in milestone.constraints
                if isinstance(constraint.expected_template, dict)
                and isinstance(constraint.expected_template.get("$binding"), dict)
                and constraint.expected_template["$binding"].get(
                    "source_milestone_id"
                ) not in reachable_predecessors[milestone.milestone_id]
            ]
            state_semantics = next((constraint.stage_goal_semantics for constraint in milestone.constraints
                                    if isinstance(constraint.stage_goal_semantics, dict)
                                    and constraint.stage_goal_semantics.get("kind") == "set_state"), None)
            state_binding_ids = _semantic_binding_ids(state_semantics)
            if state_semantics is not None and not state_binding_ids.issubset(
                reachable_predecessors[milestone.milestone_id]
            ):
                kept.remove(milestone)
                issues.append(_violation("unresolved_state_binding", turn_id=str(milestone.metadata.get("turn_id")), message='Status target reference not kept'))
                changed = True
                continue
            if dangling:
                milestone.constraints = [constraint for constraint in milestone.constraints if constraint not in dangling]
                milestone.metadata["argument_binding_status"] = "unresolved"
                issues.append(_violation("dangling_argument_binding", turn_id=str(milestone.metadata.get("turn_id")), message='Tool parameter binding downgraded'))
        for turn_id, disposition in dispositions.items():
            turn_nodes = [
                item for item in kept
                if str(item.metadata.get("turn_id")) == turn_id
            ]
            if disposition == "executable" and turn_nodes and not any(
                _compiled_terminal_node(item, view) for item in turn_nodes
            ):
                kept = [item for item in kept if item not in turn_nodes]
                issues.append(_violation(
                    "orphan_support_chain", turn_id=turn_id,
                    message='Completely turn after aggregation only producer/ support node, deleted',
                ))
                changed = True
    milestone_ids = {item.milestone_id for item in kept}
    kept_edges = [(source, target) for source, target in edges if source in milestone_ids and target in milestone_ids]
    return kept, kept_edges, issues


def _prune_dangling_producers(
    milestones: list[Milestone],
    edges: list[tuple[str, str]],
    view: GeneratorTaskView,
) -> tuple[list[Milestone], list[tuple[str, str]], list[JsonObject]]:
    """Remove the suspended, read-only node that is not consumed by any subsequent node, has no side effects and is not dependent."""
    issues: list[JsonObject] = []
    active_edges = list(edges)
    kept = list(milestones)
    changed = True
    while changed:
        changed = False
        target_ids = {target for _, target in active_edges}
        source_ids = {source for source, _ in active_edges}
        all_consumer_bindings = {
            binding.get("source_milestone_id")
            for m in kept
            for c in m.constraints
            if isinstance(c.expected_template, dict)
            and isinstance(binding := c.expected_template.get("$binding"), dict)
        }
        for m in kept:
            for c in m.constraints:
                if (
                    isinstance(c.stage_goal_semantics, dict)
                    and c.stage_goal_semantics.get("kind") == "set_state"
                ):
                    all_consumer_bindings.update(_semantic_binding_ids(c.stage_goal_semantics))
        for m in list(kept):
            # Terminal nodes, exit nodes, bound nodes, restore nodes all retained
            if _compiled_terminal_node(m, view) or m.milestone_id in source_ids or m.milestone_id in all_consumer_bindings:
                continue
            if m.metadata.get("dependency_basis") == "recovery":
                continue
            tool_name = _milestone_tool_name(m)
            contract = view.tool_contracts.get(str(tool_name), {}) if tool_name else {}
            # Only pure reading tools without any subsequent references are redundant probes.
            if not contract.get("writes"):
                kept.remove(m)
                active_edges = [(s, t) for s, t in active_edges if s != m.milestone_id and t != m.milestone_id]
                issues.append(_violation(
                    "pruned_dangling_probe", turn_id=str(m.metadata.get("turn_id")),
                    message=f"Pruned dangling read-only tool {tool_name} that no later target references",
                ))
                changed = True
    return kept, active_edges, issues


def _compiled_terminal_node(milestone: Milestone, view: GeneratorTaskView) -> bool:
    semantics = [
        constraint.stage_goal_semantics
        for constraint in milestone.constraints
        if isinstance(constraint.stage_goal_semantics, dict)
    ]
    if any(item.get("kind") in {"set_state", "emit_message"} for item in semantics):
        return True
    tool_name = _milestone_tool_name(milestone)
    if tool_name is None:
        return False
    if view.benchmark != "toolsandbox":
        return True
    contract = view.tool_contracts.get(tool_name)
    return bool(contract.get("direct_answer")) if isinstance(contract, dict) else False


def _ordered_milestones(
    milestones: list[Milestone],
    edges: list[tuple[str, str]],
    view: GeneratorTaskView,
) -> list[Milestone]:
    """Sets the order in which turn, DAG up and canonical key generates stabilization nodes."""
    milestone_by_id = {item.milestone_id: item for item in milestones}
    node_ids = set(milestone_by_id)
    predecessors = {node_id: [] for node_id in node_ids}
    successors = {node_id: [] for node_id in node_ids}
    for source, target in edges:
        if source in node_ids and target in node_ids:
            predecessors[target].append(source)
            successors[source].append(target)
    for values in predecessors.values():
        values.sort()
    for values in successors.values():
        values.sort()
    order, _ = topological_order(predecessors, successors)
    topological_index = {node_id: index for index, node_id in enumerate(order)}
    turn_index = {turn.turn_id: index for index, turn in enumerate(view.turns)}
    return sorted(
        milestones,
        key=lambda item: (
            turn_index.get(str(item.metadata.get("turn_id")), len(turn_index)),
            topological_index[item.milestone_id],
            str(item.metadata.get("canonical_key") or item.milestone_id),
        ),
    )


def _semantic_binding_ids(value: object) -> set[str]:
    """Recursively collects the producer milestone quoted in the generated state semantics."""
    if isinstance(value, dict):
        result = {
            str(value["source_milestone_id"])
            for _ in [0]
            if value.get("source") == "node_output" and isinstance(value.get("source_milestone_id"), str)
        }
        for item in value.values():
            result.update(_semantic_binding_ids(item))
        return result
    if isinstance(value, list):
        return set().union(*(_semantic_binding_ids(item) for item in value)) if value else set()
    return set()


def _add_recovery_dependencies(
    milestones: list[Milestone], edges: list[tuple[str, str]], view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
) -> tuple[list[Milestone], list[tuple[str, str]]]:
    """Inserts certainty according to private environment state and precise environmental rules."""
    state = json.loads(json.dumps(view.simulation_state))
    result = list(milestones)
    result_edges = list(edges)
    evidence_by_tool = {_tool_name(item): item for item in evidence.values()}
    recovery_by_key: dict[str, Milestone] = {}

    def ensure_recovery(blocked: Milestone, stack: tuple[str, ...] = ()) -> None:
        tool_name = _milestone_effect_tool_name(blocked)
        if tool_name is None or tool_name in stack:
            return
        arguments = _milestone_effect_arguments(blocked, view)
        for rule_name, raw_rule in sorted(view.environment_rules.items()):
            if not isinstance(raw_rule, dict) or tool_name not in raw_rule.get("applies_to_tools", []):
                continue
            applies_arguments = raw_rule.get("applies_when_arguments", {})
            if isinstance(applies_arguments, dict) and any(arguments.get(key) != value for key, value in applies_arguments.items()):
                continue
            if _state_value(state, str(raw_rule.get("state_path", ""))) != raw_rule.get("blocked_value"):
                continue
            recovery_tool = raw_rule.get("recovery_tool")
            recovery_evidence = evidence_by_tool.get(str(recovery_tool))
            recovery_arguments = raw_rule.get("recovery_arguments", {})
            if recovery_evidence is None or not isinstance(recovery_arguments, dict):
                continue
            recovery_id = f"m_{stable_json_digest((blocked.metadata.get('turn_id'), recovery_evidence.evidence_id, recovery_arguments, blocked.milestone_id))[:16]}"
            recovery = recovery_by_key.get(recovery_id)
            if recovery is None:
                constraints = [Constraint(f"{recovery_id}_tool", ConstraintTarget.TOOL_CALL, "$.name", Operator.EQUALS,
                                          expected=str(recovery_tool), hard=True,
                                          stage_goal_semantics={"kind": "tool_call", "tool_name": str(recovery_tool), "arguments": recovery_arguments})]
                constraints.extend(Constraint(f"{recovery_id}_arg_{name}", ConstraintTarget.TOOL_CALL,
                                              f"$.arguments.{name}", Operator.EQUALS, expected=value, hard=True)
                                   for name, value in sorted(recovery_arguments.items()))
                recovery = Milestone(recovery_id, f"Recover {recovery_tool}", f"Unblock {rule_name}", constraints,
                                     matching_route=recovery_evidence.matching_route,
                                     metadata={"turn_id": blocked.metadata.get("turn_id"), "dependency_basis": "recovery"})
                recovery_by_key[recovery_id] = recovery
                result.append(recovery)
                ensure_recovery(recovery, (*stack, tool_name))
            result_edges.append((recovery_id, blocked.milestone_id))
            _set_state_value(state, str(raw_rule.get("state_path", "")), raw_rule.get("recovered_value"))

    for blocked in list(milestones):
        ensure_recovery(blocked)
    return result, result_edges


def _attach_preserve_constraints(graph: MilestoneGraph, view: GeneratorTaskView) -> MilestoneGraph:
    """Preserve ToolSandbox node namespaces by tool effect and stage anchor."""
    if view.benchmark != "toolsandbox" or graph.topology is None:
        return graph
    namespaces = ("CONTACT", "MESSAGING", "REMINDER", "SETTING")
    for milestone in graph.nodes:
        anchor = graph.topology.stage_anchor_by_id[milestone.milestone_id]
        stage_nodes = _stage_ancestors(milestone.milestone_id, anchor, graph)
        writes: set[str] = set()
        contract_incomplete = False
        for stage_node in stage_nodes:
            tool_name = _milestone_tool_name(stage_node)
            contract = view.tool_contracts.get(tool_name) if tool_name else None
            if tool_name is not None and not isinstance(contract, dict):
                contract_incomplete = True
                break
            if isinstance(contract, dict):
                writes.update(str(item) for item in contract.get("writes", []) if isinstance(item, str))
            writes.update(str(constraint.namespace) for constraint in stage_node.constraints
                          if constraint.target == ConstraintTarget.STATE_SNAPSHOT and constraint.namespace
                          and constraint.stage_goal_semantics is not None
                          and constraint.stage_goal_semantics.get("kind") == "set_state")
        if contract_incomplete:
            milestone.metadata["preserve_contract_incomplete"] = True
            continue
        reference = "initial" if anchor == "__start__" else anchor
        for namespace in namespaces:
            if namespace in writes:
                continue
            constraint_id = f"{milestone.milestone_id}_preserve_{namespace.lower()}"
            milestone.constraints.append(Constraint(
                constraint_id, ConstraintTarget.STATE_SNAPSHOT, "$", Operator.CUSTOM,
                expected={"rows": [], "columns": []}, namespace=namespace,
                reference_milestone_id=reference, hard=True, evaluator_hint="toolsandbox_snapshot",
                stage_goal_semantics={"kind": "preserve_state", "namespace": namespace,
                                      "reference": {"type": "milestone_id", "value": reference}},
                metadata={"toolsandbox": {"snapshot_constraint": "snapshot_similarity", "guardrail": True}},
            ))
    return graph


def _stage_ancestors(milestone_id: str, anchor_id: str, graph: MilestoneGraph) -> list[Milestone]:
    """returns the stage anchor after the current milestone to all the ancestral nodes."""
    if graph.topology is None:
        return []
    pending = [milestone_id]
    selected: set[str] = set()
    while pending:
        current = pending.pop()
        if current == anchor_id or current in selected:
            continue
        selected.add(current)
        pending.extend(graph.topology.predecessors_by_id.get(current, ()))
    return [graph.topology.milestone_by_id[item] for item in selected]


def _milestone_tool_name(milestone: Milestone) -> str | None:
    return next((str(constraint.expected) for constraint in milestone.constraints
                 if constraint.target == ConstraintTarget.TOOL_CALL and constraint.selector == "$.name" and isinstance(constraint.expected, str)), None)


def _milestone_effect_tool_name(milestone: Milestone) -> str | None:
    """Returns the real terminal utility name of tool_call or set_state milestone."""
    tool_name = _milestone_tool_name(milestone)
    if tool_name is not None:
        return tool_name
    return next(
        (
            str(constraint.stage_goal_semantics["executor_tool_name"])
            for constraint in milestone.constraints
            if isinstance(constraint.stage_goal_semantics, dict)
            and isinstance(constraint.stage_goal_semantics.get("executor_tool_name"), str)
        ),
        None,
    )


def _literal_tool_arguments(milestone: Milestone) -> JsonObject:
    result: JsonObject = {}
    for constraint in milestone.constraints:
        prefix = "$.arguments."
        if constraint.target == ConstraintTarget.TOOL_CALL and constraint.selector.startswith(prefix) and constraint.expected_template is None:
            result[constraint.selector[len(prefix):]] = constraint.expected
    return result


def _milestone_effect_arguments(
    milestone: Milestone, view: GeneratorTaskView
) -> JsonObject:
    """returns a publicly available parameter for tool_call or set_state execator."""
    arguments = _literal_tool_arguments(milestone)
    if arguments:
        return arguments
    semantics = next(
        (
            constraint.stage_goal_semantics
            for constraint in milestone.constraints
            if isinstance(constraint.stage_goal_semantics, dict)
            and constraint.stage_goal_semantics.get("kind") == "set_state"
        ),
        None,
    )
    if not isinstance(semantics, dict):
        return {}
    state_values: JsonObject = {}
    for group in ("match", "values"):
        values = semantics.get(group)
        if not isinstance(values, dict):
            continue
        for name, source in values.items():
            if isinstance(source, dict) and source.get("source") == "public_literal":
                state_values[str(name)] = source.get("value")
    executor_tool_name = semantics.get("executor_tool_name")
    contract = view.tool_contracts.get(str(executor_tool_name))
    executor_arguments = contract.get("executor_arguments", {}) if isinstance(contract, dict) else {}
    if not isinstance(executor_arguments, dict):
        return state_values
    return {
        str(argument): state_values[state_field]
        for argument, state_field in executor_arguments.items()
        if isinstance(state_field, str) and state_field in state_values
    } or state_values


def _state_value(state: JsonObject, path: str) -> JsonValue:
    namespace, _, field = path.partition(".")
    namespaces = state.get("namespaces", state)
    rows = namespaces.get(namespace) if isinstance(namespaces, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
        return rows[-1].get(field)
    if isinstance(rows, dict):
        return rows.get(field)
    return None


def _set_state_value(state: JsonObject, path: str, value: JsonValue) -> None:
    namespace, _, field = path.partition(".")
    namespaces = state.get("namespaces", state)
    if not isinstance(namespaces, dict):
        return
    rows = namespaces.get(namespace)
    if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
        rows[-1][field] = value
    elif isinstance(rows, dict):
        rows[field] = value


def _compile_node(
    key: str, values: list[_CanonicalNode], observation_count: int,
    unique_count: int, evidence: dict[str, PublicEvidence],
    view: GeneratorTaskView,
) -> Milestone | None:
    node = values[0].candidate
    milestone_id = f"m_{stable_json_digest((node.turn_id, key))[:16]}"
    support_policy = (
        "set_state_half"
        if node.kind == "set_state"
        and observation_count
        and 2 * len(values) == observation_count
        else "graph_ensemble_strict_majority"
    )
    metadata: JsonObject = {
        "turn_id": node.turn_id, "canonical_key": key,
        "necessity_basis": "graph_ensemble_majority",
        "support_policy": support_policy, "support_count": len(values),
        "observation_count": observation_count, "support_ratio": len(values) / observation_count if observation_count else 0.0,
        "global_unique_graph_count": unique_count,
    }
    if node.kind == "emit_message":
        requirement = str(node.data["content_requirement"]).strip()
        return Milestone(milestone_id, 'Reply to Users', requirement, [Constraint(
            f"{milestone_id}_message", ConstraintTarget.STEP, "$.content", Operator.FUZZY_MATCH,
            expected=requirement, hard=True,
            stage_goal_semantics={"kind": StageGoalSemanticKind.EMIT_MESSAGE.value,
                                  "sender": node.data.get("sender"), "recipient": node.data.get("recipient"),
                                  "content": requirement, "match_policy": "semantic_equivalent", "user_visible_required": True},
        )], matching_route=(Actor.AGENT, Actor.USER), metadata=metadata)
    if node.kind == "tool_call":
        evidence_item = evidence[str(node.data["evidence_id"])]
        tool_name = _tool_name(evidence_item)
        semantic_arguments: JsonObject = {}
        constraints = [Constraint(
            f"{milestone_id}_tool", ConstraintTarget.TOOL_CALL, "$.name", Operator.EQUALS,
            expected=tool_name, hard=True, evaluator_hint=evidence_item.evaluator_hint,
            stage_goal_semantics={"kind": StageGoalSemanticKind.TOOL_CALL.value, "tool_name": tool_name,
                                  "arguments": semantic_arguments, "evidence_source": "trajectory_or_structured_scorer", "user_visible_required": False},
        )]
        argument_values = [
            dict(value.candidate.data.get("arguments", {}))
            for value in values
            if isinstance(value.candidate.data.get("arguments", {}), dict)
        ]
        field_names = sorted({name for arguments in argument_values for name in arguments})
        required_fields = {
            str(name)
            for name in _tool_parameter_schema(tool_name, view).get("required", [])
            if isinstance(name, str)
        }

        # Organisation Existence separates from parameter binding: scans all supporters first and then scans them together.
        # A strict majority or lawful absence is imposed on each field independently.
        for name in field_names:
            present_count = sum(name in arguments for arguments in argument_values)
            binding = _majority_binding(
                values, "arguments", name, view=view, evidence=evidence,
                allow_contract_arbitration=False,
            )
            if binding is not None:
                semantic_arguments[name] = {"source": "node_output", **binding}
                constraints.append(Constraint(f"{milestone_id}_arg_{name}", ConstraintTarget.TOOL_CALL,
                                              f"$.arguments.{name}", Operator.EQUALS,
                                              expected_template={"$binding": binding}, hard=True))
                continue
            literal = _majority_literal(values, "arguments", name)
            if literal is not None:
                semantic_arguments[name] = literal
                constraints.append(Constraint(f"{milestone_id}_arg_{name}", ConstraintTarget.TOOL_CALL,
                                              f"$.arguments.{name}", Operator.EQUALS,
                                              expected=literal.get("value"), hard=True))
                continue
            if name not in required_fields and 2 * present_count <= len(values):
                continue
            metadata["argument_binding_status"] = "unresolved"
        return Milestone(milestone_id, f"Call tool {tool_name}", f"{node.turn_id} calls tool {tool_name}", constraints,
                         matching_route=evidence_item.matching_route, metadata=metadata)
    executor_evidence_id = _majority_executor(values)
    if executor_evidence_id is None:
        return None
    contract = _contract_for_evidence(executor_evidence_id, view, evidence)
    if contract.get("state_evaluator") != "toolsandbox_snapshot":
        return None
    semantics: JsonObject = {
        "kind": StageGoalSemanticKind.SET_STATE.value, "namespace": node.data.get("namespace"),
        "operation": node.data.get("operation"), "cardinality": node.data.get("cardinality"),
        "match": {}, "values": {}, "executor_tool_name": _tool_name(evidence[executor_evidence_id]),
        "evidence_source": "structured_scorer", "user_visible_required": False,
    }
    for group in ("match", "values"):
        target = semantics[group]
        assert isinstance(target, dict)
        for name, source in sorted(dict(node.data.get(group, {})).items()):
            if isinstance(source, dict) and source.get("source") == "node_output":
                binding = _majority_binding(values, group, name, view=view, evidence=evidence)
                if binding is None:
                    metadata["field_binding_status"] = "unresolved"
                    target[name] = {"source": "unresolved_binding", "group": group}
                else:
                    target[name] = {"source": "node_output", **binding}
            else:
                literal = _majority_literal(values, group, name)
                if literal is None:
                    metadata["field_binding_status"] = "unresolved"
                    target[name] = {"source": "unresolved_literal", "group": group}
                else:
                    target[name] = literal
    operation = str(node.data["operation"])
    measure = {"add": "addition_similarity", "update": "update_similarity", "set": "update_similarity", "remove": "removal_similarity"}[operation]
    constraint = Constraint(
        f"{milestone_id}_state", ConstraintTarget.STATE_SNAPSHOT, "$", Operator.CUSTOM,
        expected={"rows": [], "columns": []}, namespace=str(node.data["namespace"]), hard=True,
        evaluator_hint="toolsandbox_snapshot", stage_goal_semantics=semantics,
        metadata={"toolsandbox": {"snapshot_constraint": measure}},
    )
    return Milestone(milestone_id, f"Update {node.data['namespace']}", f"State target for {node.turn_id}", [constraint], metadata=metadata)


def _majority_binding(
    values: list[_CanonicalNode],
    group: str,
    name: str,
    view: GeneratorTaskView | None = None,
    evidence: dict[str, PublicEvidence] | None = None,
    allow_contract_arbitration: bool = True,
) -> JsonObject | None:
    """The dynamic binding of the parameters or status field is implemented by strict majority aggregation and, where there is ambiguity, by contract."""
    counts: Counter[str] = Counter()
    payloads: dict[str, JsonObject] = {}
    for value in values:
        source = dict(value.candidate.data.get(group, {})).get(name)
        if not isinstance(source, dict) or source.get("source") != "node_output":
            continue
        producer_local_id = str(source.get("producer_local_id"))
        producer = _producer_key(value, producer_local_id)
        producer_turn = _producer_turn(value, producer_local_id)
        if producer is None or producer_turn is None:
            continue
        producer_evidence_id = (
            _producer_evidence_id(value, producer_local_id)
            or value.candidate.data.get("evidence_id")
            or value.candidate.data.get("executor_evidence_id")
        )
        binding: JsonObject = {
            "source_milestone_id": f"m_{stable_json_digest((producer_turn, producer))[:16]}",
            "selector": source.get("selector"),
            "cardinality": source.get("cardinality"),
            "producer_evidence_id": producer_evidence_id,
        }
        digest = stable_json_digest(binding)
        counts[digest] += 1
        payloads[digest] = binding
    winner = next((digest for digest, count in counts.items() if 2 * count > len(values)), None)
    if winner is not None:
        result = dict(payloads[winner])
        result.pop("producer_evidence_id", None)
        return result

    # If there is no absolute majority, but the candidate has a contractually explicit supporter, the only legal one.
    if allow_contract_arbitration and view is not None and evidence is not None and payloads:
        contract_supported: list[JsonObject] = []
        for binding in payloads.values():
            prod_evidence_id = binding.get("producer_evidence_id")
            contract = _contract_for_evidence(str(prod_evidence_id), view, evidence)
            outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
            if any(
                isinstance(out, dict) and out.get("selector") == binding.get("selector")
                for out in outputs.values()
            ):
                contract_supported.append(binding)
        if len(contract_supported) == 1:
            result = dict(contract_supported[0])
            result.pop("producer_evidence_id", None)
            return result

    return None


def _majority_literal(
    values: list[_CanonicalNode], group: str, name: str
) -> JsonObject | None:
    """Strict majority integration is enforced for public exposures under the same operation."""
    counts: Counter[str] = Counter()
    payloads: dict[str, JsonObject] = {}
    for value in values:
        source = dict(value.candidate.data.get(group, {})).get(name)
        if not isinstance(source, dict) or source.get("source") != "public_literal":
            continue
        payload = {
            "source": "public_literal",
            "source_ref": source.get("source_ref"),
            "value": source.get("value"),
        }
        digest = stable_json_digest(payload)
        counts[digest] += 1
        payloads[digest] = payload
    winner = next((digest for digest, count in counts.items() if 2 * count > len(values)), None)
    return payloads[winner] if winner is not None else None


def _majority_executor(values: list[_CanonicalNode]) -> str | None:
    """The selection of a candidate for inclusion in the state goal obtains evidence of a strict majority of realization."""
    counts = Counter(
        str(value.candidate.data.get("executor_evidence_id"))
        for value in values
        if isinstance(value.candidate.data.get("executor_evidence_id"), str)
    )
    return next(
        (evidence_id for evidence_id, count in counts.items() if 2 * count > len(values)),
        None,
    )


def _producer_key(value: _CanonicalNode, local_id: str) -> str | None:
    return next((item[1] for item in value.local_nodes if item[0] == local_id), None)


def _producer_turn(value: _CanonicalNode, local_id: str) -> str | None:
    return next((item[2] for item in value.local_nodes if item[0] == local_id), None)


def _producer_evidence_id(value: _CanonicalNode, local_id: str) -> str | None:
    """Finds the event id of the candidate map corresponding to the producer local id."""
    return next((item[3] for item in value.local_nodes if item[0] == local_id and len(item) > 3 and item[3]), None)


def _binding_edges(
    kept: dict[str, list[_CanonicalNode]],
    key_to_id: dict[str, str],
    view: GeneratorTaskView | None = None,
    evidence: dict[str, PublicEvidence] | None = None,
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for target_key, values in kept.items():
        target_id = key_to_id.get(target_key)
        if target_id is None:
            continue
        field_names = {
            (group, name)
            for value in values
            for group in ("arguments", "match", "values")
            for name in dict(value.candidate.data.get(group, {}))
        }
        for group, name in sorted(field_names):
            binding = _majority_binding(
                values, group, name, view=view, evidence=evidence,
                allow_contract_arbitration=group != "arguments",
            )
            if binding is None:
                continue
            producer_id = binding.get("source_milestone_id")
            if isinstance(producer_id, str) and producer_id in key_to_id.values():
                edges.append((producer_id, target_id))
    return edges


def _turn_order_edges(
    milestones: list[Milestone], edges: list[tuple[str, str]], view: GeneratorTaskView
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    by_turn = {turn.turn_id: [item.milestone_id for item in milestones if item.metadata.get("turn_id") == turn.turn_id] for turn in view.turns}
    for left, right in zip(view.turns, view.turns[1:]):
        left_ids, right_ids = by_turn[left.turn_id], by_turn[right.turn_id]
        left_non_terminal = {source for source, _ in edges if source in left_ids}
        right_non_root = {target for _, target in edges if target in right_ids}
        result.extend((source, target) for source in left_ids if source not in left_non_terminal for target in right_ids if target not in right_non_root)
    return result


def _acyclic_edges(node_ids: set[str], edges: list[tuple[str, str]], issues: list[JsonObject]) -> list[tuple[str, str]]:
    accepted: list[tuple[str, str]] = []
    for edge in dict.fromkeys(edges):
        try:
            transitive_reduction(node_ids, [*accepted, edge])
        except ValueError:
            issues.append(_violation("edge_cycle_conflict", message=f"Skipped edge that would create a cycle: {edge}"))
        else:
            accepted.append(edge)
    return accepted


def _compile_minefields(
    observations: list[_CanonicalGraphObservation], n: int,
    evidence: dict[str, PublicEvidence], view: GeneratorTaskView,
) -> list[Minefield]:
    """We'll vote on the fatal tool identity, then we'll decide on the contract."""
    support_counts: Counter[tuple[str, str]] = Counter()
    reason_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    input_counts: dict[tuple[str, str], Counter[tuple[str, ...]]] = defaultdict(Counter)
    for observation in observations:
        candidates_by_core: dict[tuple[str, str], set[_CandidateMinefield]] = defaultdict(set)
        for item in set(observation.minefields):
            core = (item.turn_id, item.evidence_id)
            candidates_by_core[core].add(item)
        for core, candidates in candidates_by_core.items():
            support_counts[core] += 1
            for item in candidates:
                reason_counts[core][item.reason_code] += 1
                if item.reason_code == "missing_required_input":
                    input_counts[core][item.missing_inputs] += 1

    result: list[Minefield] = []
    for core, support in sorted(support_counts.items()):
        turn_id, evidence_id = core
        evidence_item = evidence[evidence_id]
        tool_name = _tool_name(evidence_item)
        contract = _contract_for_evidence(evidence_id, view, evidence)
        writes = bool(contract.get("writes"))
        support_policy = "write_half" if writes else "readonly_third"
        # Readonly Fatal is thinner; write tool errors still maintain half of the support to control side effects.
        support_multiple = 2 if writes else 3
        if support * support_multiple < n:
            continue
        required = _minefield_allowed_missing_inputs(contract, tool_name, view)
        valid_missing = {
            item for item in input_counts[core]
            if item and set(item).issubset(required)
        }
        if valid_missing:
            reason_code = "missing_required_input"
            counts = input_counts[core]
            max_count = max(counts[item] for item in valid_missing)
            winners = [item for item in valid_missing if counts[item] == max_count]
            missing_inputs = min(
                winners,
                key=lambda item: (-len(set(item) & required), item),
            )
        elif contract.get("writes") and reason_counts[core]["unsafe_side_effect"]:
            reason_code, missing_inputs = "unsafe_side_effect", ()
        elif not contract.get("writes") and reason_counts[core]["unsafe_tool_call"]:
            reason_code, missing_inputs = "unsafe_tool_call", ()
        else:
            continue
        reason_support_count = reason_counts[core][reason_code]
        # Ultimately, reason must also have stable support to avoid the misreporting of fatal as a result of isolated candidates.
        if reason_support_count < 2:
            continue
        minefield_id = f"mf_{stable_json_digest((core, reason_code, missing_inputs))[:16]}"
        result.append(Minefield(
            minefield_id, f"Forbid tool {tool_name}", f"Reason {reason_code} at {turn_id}", "fatal",
            [Constraint(f"{minefield_id}_trigger", evidence_item.target, evidence_item.selector,
                        evidence_item.operator, expected=tool_name, hard=True, evaluator_hint=evidence_item.evaluator_hint)],
            MinefieldPenalty("fixed", 1.0),
            {"turn_id": turn_id, "evidence_id": evidence_id, "reason_code": reason_code,
             "missing_inputs": list(missing_inputs), "support_count": support,
             "observation_count": n, "support_policy": support_policy,
             "reason_support_count": reason_support_count,
             "reason_support_policy": "min_two_observations",
             "reason_support_counts": dict(reason_counts[core])},
        ))
    return result

def _terminal_node(node: _CandidateNode, view: GeneratorTaskView) -> bool:
    if node.kind in {"set_state", "emit_message"}:
        return True
    evidence_id = node.data.get("evidence_id")
    evidence = _evidence_catalog(view).get(str(evidence_id))
    if evidence is None:
        return False
    if view.benchmark != "toolsandbox":
        return True
    contract = view.tool_contracts.get(_tool_name(evidence), {})
    return bool(contract.get("direct_answer")) if isinstance(contract, dict) else False


def _tool_parameter_schema(tool_name: str, view: GeneratorTaskView) -> JsonObject:
    tools = view.tool_schema.get("tools", [])
    if not isinstance(tools, list):
        return {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") == tool_name:
            parameters = function.get("parameters", {})
            return parameters if isinstance(parameters, dict) else {}
    return {}


def _contract_for_evidence(
    evidence_id: str, view: GeneratorTaskView, evidence: dict[str, PublicEvidence]
) -> JsonObject:
    item = evidence.get(evidence_id)
    if item is None:
        return {}
    contract = view.tool_contracts.get(_tool_name(item), {})
    return contract if isinstance(contract, dict) else {}


def _evidence_catalog(view: GeneratorTaskView) -> dict[str, PublicEvidence]:
    result = {item.evidence_id: item for item in view.evidence_catalog}
    if len(result) != len(view.evidence_catalog):
        raise ValueError('Event_catalog contains repeats')
    return result


def _tool_name(evidence: PublicEvidence) -> str:
    value = evidence.metadata.get("tool_name")
    if not isinstance(value, str) or not value:
        raise ValueError(f"ToOL_CAL event lacks real Tol_name:{evidence.evidence_id}")
    return value


def _empty_graph(view: GeneratorTaskView, reason: str) -> MilestoneGraph:
    return MilestoneGraph(metadata={"source": "generated", "view_digest": view.digest(), "empty_reason": reason})


def _candidate_summary(
    batch_index: int, graph_index: int, status: str,
    signature: str | None, candidate_status: str = "valid",
) -> JsonObject:
    return {
        "batch_index": batch_index, "graph_index": graph_index, "status": status,
        "candidate_status": candidate_status, "signature": signature,
    }


def _assert_no_forbidden_generation_inputs(value: object, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _FORBIDDEN_KEYS:
                raise ValueError(f"Generate input contains prohibited fields:{path}.{key}")
            _assert_no_forbidden_generation_inputs(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_forbidden_generation_inputs(item, f"{path}[{index}]")


def _violation(
    code: str, batch_index: int | None = None, graph_index: int | None = None,
    turn_id: str | None = None, node_id: str | None = None, field: str | None = None,
    message: str = "",
) -> JsonObject:
    return {"code": code, "batch_index": batch_index, "graph_index": graph_index,
            "turn_id": turn_id, "node_id": node_id, "field": field, "message": message}
