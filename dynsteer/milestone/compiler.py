from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
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
_MINEFIELD_REASONS = {"missing_required_input", "tool_unavailable", "unsafe_side_effect"}
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
    """生成独立候选图，经确定性校验和严格多数聚合后编译 milestone graph。"""
    if view is None or config is None or llm is None:
        raise ValueError("view、config 和 llm 不能为空")
    evidence = _evidence_catalog(view)
    prompts = MilestonePromptBuilder(view, config)
    _assert_no_forbidden_generation_inputs(prompts.payload)
    observations: list[_CanonicalGraphObservation] = []
    candidates: list[JsonObject] = []
    issues: list[JsonObject] = []
    response_digests: list[str] = []
    raw_records: dict[str, JsonObject] = {}
    counters: Counter[str] = Counter()

    # 每批都是独立请求；达到目标后立即停止。
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
            valid, validation_issues = _validate_candidate_graph(
                candidate, view, evidence, batch_index, graph_index
            )
            issues.extend(validation_issues)
            if valid is None:
                counters["rejected_graph_count"] += 1
                candidates.append(_candidate_summary(batch_index, graph_index, "rejected", None))
                continue
            counters["valid_graph_count"] += 1
            observation = _canonicalize_candidate_graph(valid, view, batch_index, graph_index)
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
                batch_index, observation.graph_index, status, observation.signature
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
    empty_reason = (
        "generation_failed" if generation_failed
        else "no_majority_goal" if not graph.nodes and not graph.minefields
        else None
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
        cross_request_signature_counts=dict(sorted(signature_counts.items())),
        candidate_summaries=tuple(candidates),
        aggregation_support=support,
        validation_issues=tuple(issues),
        response_digests=tuple(response_digests),
        reasons=tuple(str(item.get("message", item.get("code", ""))) for item in issues),
    )
    logger.info(
        "milestone graph 候选聚合完成",
        extra={"事件": "milestone候选聚合完成", "case_id": view.case_id,
               "观测数": len(observations), "节点数": len(graph.nodes), "边数": len(graph.edges)},
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
class _CanonicalGraphObservation:
    batch_index: int
    graph_index: int
    dispositions: tuple[tuple[str, TurnDisposition], ...]
    nodes: tuple[_CanonicalNode, ...]
    edges: tuple[tuple[str, str], ...]
    minefields: tuple[_CandidateMinefield, ...]
    signature: str


def _request_candidate_batch(prompt: str, llm: BaseLLM) -> tuple[str, bool]:
    try:
        return llm.chat([LLMMessage(role="user", content=prompt)], response_format="json_object"), True
    except Exception as exc:  # LLM provider 边界：失败转为可审计批次。
        logger.warning("候选图批次请求失败", extra={"事件": "候选图请求失败", "错误": str(exc)})
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
        return [], [_violation("batch_json_error", batch_index, message=f"批次 JSON 无效: {exc}")], False, 0
    if not isinstance(payload, dict) or set(payload) != {"graphs"} or not isinstance(payload.get("graphs"), list):
        return [], [_violation("batch_schema_error", batch_index, message="顶层 graphs 必须是数组")], False, 0
    raw_graphs = payload["graphs"]
    if len(raw_graphs) > 2:
        return [], [_violation("batch_schema_error", batch_index, message="单批候选图不能超过两张")], False, len(raw_graphs)
    if len(raw_graphs) < 2:
        issues.append(_violation("batch_incomplete", batch_index, message="单批返回候选图不足两张"))
    parsed: list[tuple[int, _CandidateGraph]] = []
    for graph_index, value in enumerate(raw_graphs):
        try:
            parsed.append((graph_index, _parse_candidate_graph(value)))
        except (TypeError, ValueError) as exc:
            issues.append(_violation(
                "graph_schema_error", batch_index, graph_index, message=f"候选图结构无效: {exc}"
            ))
    return parsed, issues, True, len(raw_graphs)


def _parse_candidate_graph(value: object) -> _CandidateGraph:
    if not isinstance(value, dict):
        raise TypeError("graph 必须是对象")
    if set(value) != _GRAPH_FIELDS:
        raise ValueError("graph 必须且只能包含 dispositions/nodes/edges/minefields")
    dispositions = value.get("dispositions")
    nodes = value.get("nodes")
    edges = value.get("edges")
    minefields = value.get("minefields")
    if not isinstance(dispositions, dict) or not isinstance(nodes, list) or not isinstance(edges, list) or not isinstance(minefields, list):
        raise TypeError("dispositions/nodes/edges/minefields 类型错误")
    parsed_nodes: list[_CandidateNode] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise TypeError("node 必须是对象")
        local_id, turn_id, kind = node.get("local_id"), node.get("turn_id"), node.get("kind")
        if not all(isinstance(item, str) for item in (local_id, turn_id, kind)):
            raise TypeError("node 标识字段必须是字符串")
        if kind not in _NODE_FIELDS or set(node) != _NODE_FIELDS[kind]:
            raise ValueError(f"{kind} node 字段不符合精确 schema")
        parsed_nodes.append(_CandidateNode(local_id, turn_id, kind, json_safe(node)))
    parsed_edges: list[tuple[str, str]] = []
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            raise TypeError("edge 必须是 [source_local_id, target_local_id] 二元数组")
        source, target = edge
        if not isinstance(source, str) or not isinstance(target, str):
            raise TypeError("edge 端点必须是字符串")
        parsed_edges.append((source, target))
    parsed_minefields: list[_CandidateMinefield] = []
    for item in minefields:
        if not isinstance(item, dict):
            raise TypeError("minefield 必须是对象")
        if set(item) != _MINEFIELD_FIELDS:
            raise ValueError("minefield 字段不符合精确 schema")
        missing = item["missing_inputs"]
        if not isinstance(missing, list) or not all(isinstance(field, str) for field in missing):
            raise TypeError("missing_inputs 必须是字符串数组")
        if not all(isinstance(item.get(key), str) for key in ("turn_id", "evidence_id", "severity", "reason_code")):
            raise TypeError("minefield 标识字段必须是字符串")
        parsed_minefields.append(_CandidateMinefield(
            item["turn_id"], item["evidence_id"], item["severity"], item["reason_code"],
            tuple(sorted(missing)),
        ))
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in dispositions.items()):
        raise TypeError("dispositions 必须是字符串到字符串的对象")
    return _CandidateGraph(
        tuple(sorted((str(key), str(item)) for key, item in dispositions.items())),
        tuple(parsed_nodes), tuple(parsed_edges), tuple(parsed_minefields),
    )


def _validate_candidate_graph(
    candidate: _CandidateGraph,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
    batch_index: int,
    graph_index: int,
) -> tuple[_CandidateGraph | None, list[JsonObject]]:
    issues: list[JsonObject] = []
    turn_ids = {turn.turn_id for turn in view.turns}
    turn_order = {turn.turn_id: index for index, turn in enumerate(view.turns)}
    dispositions = dict(candidate.dispositions)
    if set(dispositions) != turn_ids:
        issues.append(_violation("disposition_turn_mismatch", batch_index, graph_index, message="disposition 必须覆盖全部 turn"))
    if any(value not in _DISPOSITIONS for value in dispositions.values()):
        issues.append(_violation("invalid_disposition", batch_index, graph_index, message="disposition 值无效"))
    node_by_id: dict[str, _CandidateNode] = {}
    for node in candidate.nodes:
        if not node.local_id or node.local_id in node_by_id:
            issues.append(_violation("duplicate_node_id", batch_index, graph_index, node_id=node.local_id, message="local_id 为空或重复"))
            continue
        node_by_id[node.local_id] = node
        if node.turn_id not in turn_ids or node.kind not in _NODE_KINDS:
            issues.append(_violation("invalid_node", batch_index, graph_index, node.turn_id, node.local_id, message="node 的 turn 或 kind 无效"))
            continue
        if node.kind in {"tool_call", "set_state"} and dispositions.get(node.turn_id) != "executable":
            issues.append(_violation("node_on_non_executable_turn", batch_index, graph_index, node.turn_id, node.local_id, message="非 executable turn 不能包含工具或状态目标"))
        issues.extend(_validate_node(node, view, evidence, batch_index, graph_index))
    if len(set(candidate.edges)) != len(candidate.edges):
        issues.append(_violation("duplicate_edge", batch_index, graph_index, message="edge 重复"))
    edge_set = set(candidate.edges)
    # binding 本身是确定性依赖；校验 producer contract 并补齐缺失 edge。
    for node in candidate.nodes:
        for group in ("arguments", "match", "values"):
            values = node.data.get(group, {})
            if not isinstance(values, dict):
                continue
            for field, source in values.items():
                if not isinstance(source, dict) or source.get("source") != "node_output":
                    continue
                producer_id = source.get("producer_local_id")
                producer = node_by_id.get(str(producer_id))
                if producer is None or producer.kind != "tool_call":
                    issues.append(_violation("invalid_binding_producer", batch_index, graph_index, node.turn_id, node.local_id, f"{group}.{field}", "binding producer 不存在或不是 tool_call"))
                    continue
                if turn_order[producer.turn_id] > turn_order[node.turn_id]:
                    issues.append(_violation(
                        "future_turn_binding", batch_index, graph_index,
                        node.turn_id, node.local_id, f"{group}.{field}",
                        "binding producer 不能来自后续 turn",
                    ))
                    continue
                producer_evidence = evidence.get(str(producer.data.get("evidence_id")))
                contract = _contract_for_evidence(str(producer.data.get("evidence_id")), view, evidence) if producer_evidence else {}
                outputs = contract.get("outputs", {}) if isinstance(contract, dict) else {}
                output = next((item for item in outputs.values() if isinstance(item, dict) and item.get("selector") == source.get("selector")), None) if isinstance(outputs, dict) else None
                if not isinstance(output, dict) or source.get("cardinality") not in output.get("cardinality", []):
                    issues.append(_violation("contract_incomplete", batch_index, graph_index, node.turn_id, node.local_id, f"{group}.{field}", "producer output contract 不支持该 selector/cardinality"))
                    continue
                target_schema = _binding_target_schema(node, group, str(field), view, evidence)
                if not _schema_types_compatible(output.get("type"), target_schema.get("type")):
                    issues.append(_violation("binding_type_mismatch", batch_index, graph_index, node.turn_id, node.local_id, f"{group}.{field}", "producer output 类型与目标字段不兼容"))
                    continue
                edge_set.add((producer.local_id, node.local_id))
    for source, target in candidate.edges:
        if source not in node_by_id or target not in node_by_id or source == target:
            issues.append(_violation("invalid_edge", batch_index, graph_index, node_id=source, message="edge 端点不存在或自环"))
    if not issues:
        try:
            predecessors = {node_id: [] for node_id in node_by_id}
            successors = {node_id: [] for node_id in node_by_id}
            for source, target in edge_set:
                predecessors[target].append(source)
                successors[source].append(target)
            topological_order(predecessors, successors)
        except ValueError:
            issues.append(_violation("graph_cycle", batch_index, graph_index, message="候选图存在环"))
    for turn_id, disposition in dispositions.items():
        if disposition == "executable" and not any(
            node.turn_id == turn_id and _terminal_node(node, view) for node in candidate.nodes
        ):
            issues.append(_violation("missing_terminal_goal", batch_index, graph_index, turn_id, message="executable turn 缺少终端目标"))
    for minefield in candidate.minefields:
        contract = _contract_for_evidence(minefield.evidence_id, view, evidence)
        if minefield.turn_id not in turn_ids or minefield.severity != "fatal" or minefield.reason_code not in _MINEFIELD_REASONS:
            issues.append(_violation("invalid_minefield", batch_index, graph_index, minefield.turn_id, message="minefield 字段无效"))
        elif not contract or not contract.get("writes"):
            issues.append(_violation("minefield_contract_unverified", batch_index, graph_index, minefield.turn_id, message="minefield 无法由副作用契约核实"))
        elif minefield.reason_code in {"tool_unavailable", "unsafe_side_effect"} and minefield.reason_code not in contract.get("fatal_reasons", []):
            issues.append(_violation("minefield_reason_unverified", batch_index, graph_index, minefield.turn_id, message="minefield reason 未获得私有工具契约支持"))
        elif minefield.reason_code == "missing_required_input" and not set(minefield.missing_inputs).issubset(set(contract.get("required_dynamic_inputs", []))):
            issues.append(_violation("minefield_input_unverified", batch_index, graph_index, minefield.turn_id, message="缺失输入不属于契约必需动态输入"))
        elif minefield.reason_code == "missing_required_input" and not minefield.missing_inputs:
            issues.append(_violation("minefield_input_unverified", batch_index, graph_index, minefield.turn_id, message="missing_required_input 必须列出缺失输入"))
        elif minefield.reason_code == "missing_required_input" and _minefield_inputs_available(minefield, candidate):
            issues.append(_violation("minefield_input_available", batch_index, graph_index, minefield.turn_id, message="minefield 声称缺失的输入已存在"))
        if any(
            node.turn_id == minefield.turn_id
            and (
                node.data.get("executor_evidence_id") == minefield.evidence_id
                or (
                    node.data.get("evidence_id") == minefield.evidence_id
                    and bool(contract.get("writes"))
                )
            )
            for node in candidate.nodes
        ):
            issues.append(_violation("minefield_terminal_conflict", batch_index, graph_index, minefield.turn_id, message="同一 terminal side effect 不能同时是 executable goal 和 fatal minefield"))
    validated = _CandidateGraph(candidate.dispositions, candidate.nodes, tuple(sorted(edge_set)), candidate.minefields)
    return (None if issues else validated), issues


def _validate_node(
    node: _CandidateNode, view: GeneratorTaskView, evidence: dict[str, PublicEvidence],
    batch_index: int, graph_index: int,
) -> list[JsonObject]:
    issues: list[JsonObject] = []
    data = node.data
    if node.kind == "emit_message":
        if data.get("sender") != "AGENT" or data.get("recipient") != "USER":
            issues.append(_violation("invalid_message_route", batch_index, graph_index, node.turn_id, node.local_id, message="emit_message 必须是 AGENT→USER"))
        if not isinstance(data.get("content_requirement"), str) or not str(data["content_requirement"]).strip():
            issues.append(_violation("empty_content_requirement", batch_index, graph_index, node.turn_id, node.local_id, message="消息要求不能为空"))
        return issues
    evidence_id = data.get("evidence_id") if node.kind == "tool_call" else data.get("executor_evidence_id")
    if not isinstance(evidence_id, str) or evidence_id not in evidence:
        return [_violation("unknown_evidence", batch_index, graph_index, node.turn_id, node.local_id, message="evidence_id 不存在")]
    if node.kind == "tool_call":
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            return [_violation("invalid_arguments", batch_index, graph_index, node.turn_id, node.local_id, message="arguments 必须是对象")]
        schema = _tool_parameter_schema(_tool_name(evidence[evidence_id]), view)
        issues.extend(_validate_tool_arguments(arguments, schema, node, view, batch_index, graph_index))
    else:
        operation, cardinality = data.get("operation"), data.get("cardinality")
        match, values = data.get("match", {}), data.get("values", {})
        contract = _contract_for_evidence(evidence_id, view, evidence)
        effect = contract.get("effect") if isinstance(contract, dict) else None
        if operation not in _STATE_OPERATIONS or cardinality not in _CARDINALITIES or not isinstance(match, dict) or not isinstance(values, dict):
            issues.append(_violation("invalid_state_goal", batch_index, graph_index, node.turn_id, node.local_id, message="状态目标结构无效"))
        elif not isinstance(effect, dict) or effect.get("namespace") != data.get("namespace") or effect.get("operation") != operation:
            issues.append(_violation("state_contract_mismatch", batch_index, graph_index, node.turn_id, node.local_id, message="状态目标与工具 effect 契约不一致"))
        elif contract.get("state_evaluator") != "toolsandbox_snapshot":
            issues.append(_violation(
                "state_contract_unscorable", batch_index, graph_index,
                node.turn_id, node.local_id,
                message="adapter 未声明 compiler 支持的状态评分契约",
            ))
        elif (operation == "add" and not values) or (operation in {"update", "set"} and not values) or (operation == "remove" and not match):
            issues.append(_violation("incomplete_state_goal", batch_index, graph_index, node.turn_id, node.local_id, message="状态目标缺少 match 或 values"))
        elif not set(contract.get("required_dynamic_inputs", [])).issubset(set(match) | set(values)):
            issues.append(_violation("state_required_input_missing", batch_index, graph_index, node.turn_id, node.local_id, message="状态目标缺少 executor 契约要求的动态输入"))
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


def _minefield_inputs_available(
    minefield: _CandidateMinefield, candidate: _CandidateGraph
) -> bool:
    """判断同 turn 是否已经为 minefield 声称缺失的输入提供来源。"""
    missing = set(minefield.missing_inputs)
    if not missing:
        return False
    for node in candidate.nodes:
        if node.turn_id != minefield.turn_id:
            continue
        for group in ("arguments", "match", "values"):
            values = node.data.get(group, {})
            if isinstance(values, dict):
                missing.difference_update(values)
    return not missing


def _validate_tool_arguments(
    arguments: JsonObject, schema: JsonObject, node: _CandidateNode, view: GeneratorTaskView,
    batch_index: int, graph_index: int,
) -> list[JsonObject]:
    issues: list[JsonObject] = []
    properties = schema.get("properties", {}) if isinstance(schema.get("properties", {}), dict) else {}
    required = schema.get("required", []) if isinstance(schema.get("required", []), list) else []
    for name in required:
        if name not in arguments:
            issues.append(_violation("missing_tool_argument", batch_index, graph_index, node.turn_id, node.local_id, f"arguments.{name}", "缺少必需工具参数"))
    if schema.get("additionalProperties") is False:
        for name in set(arguments) - set(properties):
            issues.append(_violation("unknown_tool_argument", batch_index, graph_index, node.turn_id, node.local_id, f"arguments.{name}", "包含未知工具参数"))
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
        return [_violation("invalid_value_source", batch_index, graph_index, node.turn_id, node.local_id, field, "值来源无效")]
    assert isinstance(value, dict)
    if parsed.source == "public_literal":
        if set(value) != {"source", "source_ref", "value"}:
            return [_violation("invalid_value_source", batch_index, graph_index, node.turn_id, node.local_id, field, "public_literal 字段不符合精确 schema")]
        turn_sources = {turn.source_ref: turn.instruction for turn in view.turns}
        asset_sources = {str(item.get("source_ref")): item.get("value") for item in view.public_assets if item.get("visibility", "agent") == "agent"}
        public_state_sources = _public_state_sources(view.public_state)
        source_ref = parsed.source_ref
        if source_ref not in turn_sources and source_ref not in asset_sources and source_ref not in public_state_sources:
            return [_violation("unknown_public_source", batch_index, graph_index, node.turn_id, node.local_id, field, "public literal 来源不可见")]
        literal = parsed.value
        if source_ref in asset_sources and asset_sources[str(source_ref)] != literal:
            return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, "public literal 与来源值不一致")]
        if source_ref in turn_sources and not _literal_appears_in_instruction(
            literal, turn_sources[str(source_ref)], field
        ):
            return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, "public literal 未在 instruction 中出现")]
        if source_ref in public_state_sources and public_state_sources[str(source_ref)] != literal:
            return [_violation("public_literal_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, "public literal 与 public_state 来源值不一致")]
    else:
        if set(value) != {"source", "producer_local_id", "selector", "cardinality"}:
            return [_violation("invalid_output_binding", batch_index, graph_index, node.turn_id, node.local_id, field, "node_output 字段不符合精确 schema")]
        if not isinstance(value.get("producer_local_id"), str) or not isinstance(value.get("selector"), str) or value.get("cardinality") not in _CARDINALITIES:
            return [_violation("invalid_output_binding", batch_index, graph_index, node.turn_id, node.local_id, field, "node_output binding 无效")]
    return []


def _value_source(value: object) -> _ValueSource | None:
    """把精确来源对象转为 compiler 内部不可变表示。"""
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
    """将公开状态叶节点展开为稳定的 `public_state:<path>` 来源。"""
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
        return [_violation("argument_enum_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, "参数不属于 enum")]
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    checks = {"object": lambda item: isinstance(item, dict), "array": lambda item: isinstance(item, list),
              "string": lambda item: isinstance(item, str), "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
              "integer": lambda item: isinstance(item, int) and not isinstance(item, bool), "boolean": lambda item: isinstance(item, bool),
              "null": lambda item: item is None}
    if expected is not None and not any(checks.get(str(item), lambda _: True)(value) for item in types):
        return [_violation("argument_type_mismatch", batch_index, graph_index, node.turn_id, node.local_id, field, "参数类型不符合 schema")]
    issues: list[JsonObject] = []
    if isinstance(value, dict):
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required", []) if isinstance(schema.get("required"), list) else []
        for name in required:
            if name not in value:
                issues.append(_violation("argument_required_property_missing", batch_index, graph_index, node.turn_id, node.local_id, f"{field}.{name}", "对象缺少必需字段"))
        if schema.get("additionalProperties") is False:
            for name in set(value) - set(properties):
                issues.append(_violation("argument_unknown_property", batch_index, graph_index, node.turn_id, node.local_id, f"{field}.{name}", "对象包含未知字段"))
        for name, item in value.items():
            issues.extend(_validate_schema_value(item, properties.get(name, {}), batch_index, graph_index, node, f"{field}.{name}"))
    elif isinstance(value, list):
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            issues.extend(_validate_schema_value(item, item_schema, batch_index, graph_index, node, f"{field}[{index}]"))
    return issues


def _canonicalize_candidate_graph(
    candidate: _CandidateGraph, view: GeneratorTaskView, batch_index: int, graph_index: int
) -> _CanonicalGraphObservation:
    base_by_id = {node.local_id: _node_identity(node, view) for node in candidate.nodes}
    predecessors = {node.local_id: [] for node in candidate.nodes}
    successors = {node.local_id: [] for node in candidate.nodes}
    for source, target in candidate.edges:
        predecessors[target].append(source)
        successors[source].append(target)
    order, _ = topological_order(predecessors, successors)
    order_index = {local_id: index for index, local_id in enumerate(order)}
    occurrence: Counter[str] = Counter()
    canonical_nodes: list[_CanonicalNode] = []
    local_to_key: dict[str, str] = {}
    ordered_nodes: list[_CandidateNode] = []
    grouped: dict[str, list[_CandidateNode]] = defaultdict(list)
    for node in candidate.nodes:
        grouped[stable_json_digest(base_by_id[node.local_id])].append(node)
    for base_digest in sorted(grouped):
        ordered_nodes.extend(sorted(grouped[base_digest], key=lambda item: (
            order_index[item.local_id],
            tuple(sorted(stable_json_digest(base_by_id[source]) for source in predecessors[item.local_id])),
            tuple(sorted(stable_json_digest(base_by_id[target]) for target in successors[item.local_id])),
            item.local_id,
        )))
    for node in ordered_nodes:
        base_digest = stable_json_digest(base_by_id[node.local_id])
        index = occurrence[base_digest]
        occurrence[base_digest] += 1
        key = stable_json_digest((base_by_id[node.local_id], index))
        local_to_key[node.local_id] = key
    local_nodes = tuple(sorted((
        node.local_id,
        local_to_key[node.local_id],
        node.turn_id,
        str(node.data.get("evidence_id") or node.data.get("executor_evidence_id") or ""),
    ) for node in candidate.nodes))
    for node in ordered_nodes:
        canonical_nodes.append(_CanonicalNode(
            local_to_key[node.local_id], base_by_id[node.local_id], node,
            local_nodes,
        ))
    edges = tuple(sorted((local_to_key[source], local_to_key[target]) for source, target in candidate.edges))
    minefields = tuple(sorted(candidate.minefields, key=lambda item: (item.turn_id, item.evidence_id, item.reason_code, item.missing_inputs)))
    signature = stable_json_digest({
        "dispositions": candidate.dispositions,
        "nodes": sorted(item.key for item in canonical_nodes),
        "edges": edges,
        "minefields": json_safe(minefields),
    })
    return _CanonicalGraphObservation(batch_index, graph_index, candidate.dispositions, tuple(canonical_nodes), edges, minefields, signature)


def _node_identity(node: _CandidateNode, view: GeneratorTaskView) -> JsonObject:
    data = node.data
    if node.kind == "emit_message":
        return {"turn_id": node.turn_id, "kind": node.kind, "sender": str(data.get("sender", "")).upper(),
                "recipient": str(data.get("recipient", "")).upper(),
                "content_requirement": " ".join(str(data.get("content_requirement", "")).split()).casefold()}
    if node.kind == "tool_call":
        identity: JsonObject = {"turn_id": node.turn_id, "kind": node.kind, "evidence_id": str(data.get("evidence_id")), "arguments": {}}
        arguments = identity["arguments"]
        assert isinstance(arguments, dict)
        for name, value in sorted(dict(data.get("arguments", {})).items()):
            arguments[name] = _source_identity(value)
        return identity
    identity = {"turn_id": node.turn_id, "kind": node.kind, "namespace": str(data.get("namespace")),
                "operation": str(data.get("operation")), "cardinality": str(data.get("cardinality")), "match": {}, "values": {}}
    for group in ("match", "values"):
        target = identity[group]
        assert isinstance(target, dict)
        for name, value in sorted(dict(data.get(group, {})).items()):
            target[name] = _source_identity(value)
    return identity


def _source_identity(value: object) -> JsonValue:
    source = _value_source(value)
    if source is None:
        return None
    if source.source == "public_literal":
        return {
            "source": "public_literal",
            "source_ref": source.source_ref,
            "value": source.value,
        }
    return {
        "source": "node_output",
        "selector": source.selector,
        "cardinality": source.cardinality,
    }


def _literal_appears_in_instruction(
    value: JsonValue, instruction: str, field: str = ""
) -> bool:
    """确认标量公开值确实来自对应 instruction。"""
    if value is None or isinstance(value, (dict, list)):
        return False
    normalized_instruction = instruction.casefold()
    normalized_value = str(value).strip().casefold()
    if normalized_value and normalized_value in normalized_instruction:
        return True
    if not isinstance(value, bool):
        return False
    field_name = field.rsplit(".", 1)[-1].replace("_", " ").casefold()
    positive = ("on", "enable", "enabled", "开启", "打开", "启用")
    negative = ("off", "disable", "disabled", "关闭", "停用", "禁用")
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
    """读取 node_output binding 对应的目标字段 schema。"""
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
    """判断 contract output 与工具目标参数声明类型是否兼容。"""
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
            issues.append(_violation("disposition_conflict", message=f"{turn.turn_id} 无 disposition 严格多数", turn_id=turn.turn_id))
    node_occurrences: dict[str, list[_CanonicalNode]] = defaultdict(list)
    for observation in observations:
        for node in observation.nodes:
            node_occurrences[node.key].append(node)
    kept = {
        key: values for key, values in node_occurrences.items()
        if 2 * len(values) > n
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
            issues.append(_violation("unresolved_state_binding", turn_id=values[0].candidate.turn_id, message="动态状态 binding 未获严格多数"))
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
                "edge_cycle_conflict", message=f"跳过成环 majority edge {edge}"
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
    minefields = _compile_minefields(observations, n, evidence)
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
                message="fatal minefield 与聚合终态冲突，按安全规则降级为 response_only",
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
    """删除聚合后不可闭合的节点、binding 和 edge。"""
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
                issues.append(_violation("unresolved_state_binding", turn_id=str(milestone.metadata.get("turn_id")), message="状态目标引用未保留 producer"))
                changed = True
                continue
            if dangling:
                milestone.constraints = [constraint for constraint in milestone.constraints if constraint not in dangling]
                milestone.metadata["argument_binding_status"] = "unresolved"
                issues.append(_violation("dangling_argument_binding", turn_id=str(milestone.metadata.get("turn_id")), message="工具参数 binding 已降级"))
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
                    message="聚合后 executable turn 只剩 producer/support 节点，已删除",
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
    """剔除未被任何后续节点消费、无副作用且无依赖关系的悬空只读查询节点。"""
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
            # 终端节点、有出度节点、有被绑定的节点、恢复节点均保留
            if _compiled_terminal_node(m, view) or m.milestone_id in source_ids or m.milestone_id in all_consumer_bindings:
                continue
            if m.metadata.get("dependency_basis") == "recovery":
                continue
            tool_name = _milestone_tool_name(m)
            contract = view.tool_contracts.get(str(tool_name), {}) if tool_name else {}
            # 只有纯读工具且无任何后继引用的才属于冗余探针
            if not contract.get("writes"):
                kept.remove(m)
                active_edges = [(s, t) for s, t in active_edges if s != m.milestone_id and t != m.milestone_id]
                issues.append(_violation(
                    "pruned_dangling_probe", turn_id=str(m.metadata.get("turn_id")),
                    message=f"已修剪未被任何后续目标引用的悬空只读工具 {tool_name}",
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
    """按 turn、DAG 拓扑和 canonical key 生成稳定节点顺序。"""
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
    """递归收集 generated state semantics 中引用的 producer milestone。"""
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
    """根据私有 simulation state 和确切环境规则插入确定性 recovery。"""
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
                recovery = Milestone(recovery_id, f"恢复 {recovery_tool}", f"解除 {rule_name} 阻塞", constraints,
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
    """按 tool effect 和 stage anchor 为 ToolSandbox 节点派生 namespace preserve。"""
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
    """返回 stage anchor 之后到当前 milestone 的全部祖先节点。"""
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
    """返回 tool_call 或 set_state milestone 的真实终态工具名。"""
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
    """返回 tool_call 或 set_state executor 的可确定公开参数。"""
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
    metadata: JsonObject = {
        "turn_id": node.turn_id, "canonical_key": key,
        "necessity_basis": "graph_ensemble_majority", "support_count": len(values),
        "observation_count": observation_count, "support_ratio": len(values) / observation_count if observation_count else 0.0,
        "global_unique_graph_count": unique_count,
    }
    if node.kind == "emit_message":
        requirement = str(node.data["content_requirement"]).strip()
        return Milestone(milestone_id, "回复用户", requirement, [Constraint(
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
        arguments = dict(node.data.get("arguments", {}))
        for name, source in sorted(arguments.items()):
            if not isinstance(source, dict):
                continue
            if source.get("source") == "public_literal":
                semantic_arguments[name] = source
                constraints.append(Constraint(f"{milestone_id}_arg_{name}", ConstraintTarget.TOOL_CALL,
                                              f"$.arguments.{name}", Operator.EQUALS, expected=source.get("value"), hard=True))
            else:
                binding = _majority_binding(values, "arguments", name, view=view, evidence=evidence)
                if binding is not None:
                    semantic_arguments[name] = {"source": "node_output", **binding}
                    constraints.append(Constraint(f"{milestone_id}_arg_{name}", ConstraintTarget.TOOL_CALL,
                                                  f"$.arguments.{name}", Operator.EQUALS,
                                                  expected_template={"$binding": binding}, hard=True))
                else:
                    metadata["argument_binding_status"] = "unresolved"
        return Milestone(milestone_id, f"执行 {tool_name}", f"{node.turn_id} 执行 {tool_name}", constraints,
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
                    return None
                target[name] = {"source": "node_output", **binding}
            else:
                target[name] = source
    operation = str(node.data["operation"])
    measure = {"add": "addition_similarity", "update": "update_similarity", "set": "update_similarity", "remove": "removal_similarity"}[operation]
    constraint = Constraint(
        f"{milestone_id}_state", ConstraintTarget.STATE_SNAPSHOT, "$", Operator.CUSTOM,
        expected={"rows": [], "columns": []}, namespace=str(node.data["namespace"]), hard=True,
        evaluator_hint="toolsandbox_snapshot", stage_goal_semantics=semantics,
        metadata={"toolsandbox": {"snapshot_constraint": measure}},
    )
    return Milestone(milestone_id, f"更新 {node.data['namespace']}", f"{node.turn_id} 的状态目标", [constraint], metadata=metadata)


def _majority_binding(
    values: list[_CanonicalNode],
    group: str,
    name: str,
    view: GeneratorTaskView | None = None,
    evidence: dict[str, PublicEvidence] | None = None,
) -> JsonObject | None:
    """针对参数或状态字段的动态绑定执行严格多数聚合，并在存在歧义时通过契约进行仲裁。"""
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

    # 契约辅助裁决兜底: 如果无绝对多数，但候选中有契约显式支持的 selector，且唯一合法
    if view is not None and evidence is not None and payloads:
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


def _majority_executor(values: list[_CanonicalNode]) -> str | None:
    """选择包含该状态 goal 的候选中获得严格多数的实现证据。"""
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
    """查找候选图中对应 producer local id 的 evidence id。"""
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
            binding = _majority_binding(values, group, name, view=view, evidence=evidence)
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
            issues.append(_violation("edge_cycle_conflict", message=f"跳过成环 edge {edge}"))
        else:
            accepted.append(edge)
    return accepted


def _compile_minefields(
    observations: list[_CanonicalGraphObservation], n: int, evidence: dict[str, PublicEvidence]
) -> list[Minefield]:
    counts: Counter[_CandidateMinefield] = Counter(item for observation in observations for item in set(observation.minefields))
    result: list[Minefield] = []
    for item, support in sorted(counts.items(), key=lambda pair: (pair[0].turn_id, pair[0].evidence_id)):
        if 2 * support <= n:
            continue
        evidence_item = evidence[item.evidence_id]
        tool_name = _tool_name(evidence_item)
        minefield_id = f"mf_{stable_json_digest(item)[:16]}"
        result.append(Minefield(
            minefield_id, f"禁止 {tool_name}", f"{item.turn_id} 中 {item.reason_code}", "fatal",
            [Constraint(f"{minefield_id}_trigger", evidence_item.target, evidence_item.selector,
                        evidence_item.operator, expected=tool_name, hard=True, evaluator_hint=evidence_item.evaluator_hint)],
            MinefieldPenalty("fixed", 1.0),
            {"turn_id": item.turn_id, "evidence_id": item.evidence_id, "reason_code": item.reason_code,
             "missing_inputs": list(item.missing_inputs), "support_count": support, "observation_count": n},
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
        raise ValueError("evidence_catalog 包含重复 evidence_id")
    return result


def _tool_name(evidence: PublicEvidence) -> str:
    value = evidence.metadata.get("tool_name")
    if not isinstance(value, str) or not value:
        raise ValueError(f"TOOL_CALL evidence 缺少真实 tool_name: {evidence.evidence_id}")
    return value


def _empty_graph(view: GeneratorTaskView, reason: str) -> MilestoneGraph:
    return MilestoneGraph(metadata={"source": "generated", "view_digest": view.digest(), "empty_reason": reason})


def _candidate_summary(batch_index: int, graph_index: int, status: str, signature: str | None) -> JsonObject:
    return {"batch_index": batch_index, "graph_index": graph_index, "status": status, "signature": signature}


def _assert_no_forbidden_generation_inputs(value: object, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _FORBIDDEN_KEYS:
                raise ValueError(f"生成输入包含禁止字段: {path}.{key}")
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
