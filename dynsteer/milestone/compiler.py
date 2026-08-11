from __future__ import annotations

import copy
import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dynsteer.graph import milestones_from_occurrences, minefields_from_forbidden, transitive_reduction
from dynsteer.milestone.model import (
    GenerationReport, GeneratorTaskView, MilestoneGenerationConfig, PublicEvidence, TurnDisposition,
)
from dynsteer.model import ConstraintTarget, JsonObject, LLMMessage, MilestoneGraph
from dynsteer.prompt.template import MilestonePromptBuilder
from dynsteer.utils import json_safe, record_raw_response, stable_json_digest

if TYPE_CHECKING:
    from dynsteer.llm.base import BaseLLM


logger = logging.getLogger(__name__)
_DISPOSITIONS = {"executable", "needs_clarification", "no_action", "response_only"}
_FORBIDDEN_KEYS = {
    "milestone_matcher", "minefield_matcher", "evaluation", "verifier",
    "reference_graph", "trajectory", "final_state",
}


@dataclass(frozen=True)
class _OperationCandidate:
    evidence_id: str
    arguments: JsonObject


@dataclass(frozen=True)
class _TurnCandidate:
    turn_id: str
    disposition: TurnDisposition
    operations: tuple[_OperationCandidate, ...]
    forbidden_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class _PathCandidate:
    turns: tuple[_TurnCandidate, ...]


def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_file: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]:
    """生成多条完整路径，经环境模拟后聚合共同 milestone graph。

    入参：公开任务视图、生成配置、LLM 和可选原始响应文件。
    输出：始终合法的 graph（允许为空）及完整生成报告。
    """
    if view is None or config is None or llm is None:
        raise ValueError("view、config 和 llm 不能为空")
    evidence = _evidence_catalog(view)
    prompts = MilestonePromptBuilder(view, config)
    _assert_no_forbidden_generation_inputs(prompts.payload)
    raw_records: dict[str, JsonObject] = {}

    # 第一轮生成并逐路径解析、模拟，单条坏路径不影响同轮其他路径。
    generation_prompt = prompts.generation()
    draft = _run_round(
        [LLMMessage(role="user", content=generation_prompt)], 1, "generation",
        view, config, llm, evidence, response_output_file, raw_records,
    )
    draft_paths = draft["simulated_paths"]
    preliminary = _mandatory_occurrences(draft_paths, view)

    # 第二轮同时修复错误并主动寻找初步必经 operation 的反例路径。
    repair_triggered = config.enable_repair and bool(
        draft["violations"] or len(draft_paths) < 2 or preliminary
    )
    refined: JsonObject | None = None
    if repair_triggered:
        prompt = prompts.refinement(
            str(draft["raw_response"]),
            list(draft["violations"]),
            [_normalized_path(path) for path in draft_paths],
            [_occurrence_label(item) for item in sorted(preliminary)],
        )
        refined = _run_round(
            [
                LLMMessage(role="user", content=generation_prompt),
                LLMMessage(role="assistant", content=str(draft["raw_response"])),
                LLMMessage(role="user", content=prompt),
            ],
            2, "refinement", view, config, llm, evidence,
            response_output_file, raw_records,
        )
    selected = refined if refined is not None and refined["simulated_paths"] else draft
    paths = selected["simulated_paths"]
    mandatory = _mandatory_occurrences(paths, view)
    nodes = milestones_from_occurrences(
        _ordered_occurrences(mandatory, paths, view), evidence
    )
    edges = _common_precedence(paths, mandatory, view)
    minefields = minefields_from_forbidden(
        _forbidden_intersection(paths, view), evidence
    )
    dispositions = _turn_dispositions(paths, view)
    empty_reason = _empty_reason(paths, mandatory, draft, refined)
    graph = MilestoneGraph(
        nodes=nodes,
        edges=transitive_reduction({node.milestone_id for node in nodes}, edges),
        minefields=minefields,
        metadata={
            "source": "generated",
            "view_digest": view.digest(),
            "turn_dispositions": dispositions,
            "disposition": next(iter(dispositions.values()), "no_action"),
            "empty_reason": empty_reason,
        },
    )
    removed = tuple(sorted(_occurrence_label(item) for item in preliminary - mandatory))
    rounds = [draft, *([refined] if refined is not None else [])]
    report = GenerationReport(
        generation_status="generated",
        turn_dispositions=dispositions,
        max_candidate_path_count=config.max_candidate_path_count,
        returned_path_count=sum(int(item["returned_path_count"]) for item in rounds),
        parsed_path_count=sum(int(item["parsed_path_count"]) for item in rounds),
        simulatable_path_count=sum(int(item["simulatable_path_count"]) for item in rounds),
        final_path_count=len(paths),
        selected_round=int(selected["round_index"]) if paths else None,
        graph_returned=True,
        graph_empty=not nodes,
        empty_reason=empty_reason,
        minefield_count=len(minefields),
        repair_triggered=repair_triggered,
        counterexample_removed_operations=removed,
        round_summaries=tuple(_round_summary(item) for item in rounds),
        path_summaries=tuple(
            summary for item in rounds for summary in item["path_summaries"]
        ),
        reasons=tuple(
            str(violation.get("message") or violation.get("kind"))
            for item in rounds for violation in item["violations"]
        ),
    )
    logger.info(
        "milestone graph 生成完成",
        extra={
            "事件": "milestone生成完成", "case_id": view.case_id,
            "有效路径数": len(paths), "节点数": len(nodes),
            "边数": len(graph.edges), "minefield数": len(minefields),
            "空图原因": empty_reason,
        },
    )
    return graph, report

def _run_round(
    messages: list[LLMMessage],
    round_index: int,
    stage: str,
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    evidence: dict[str, PublicEvidence],
    response_output_file: Path | None,
    raw_records: dict[str, JsonObject],
) -> JsonObject:
    violations: list[JsonObject] = []
    planner_failed = False
    try:
        raw = llm.chat(messages, response_format="json_object")
    except Exception as exc:  # LLM provider 边界：所有失败均转为可审计空路径轮次。
        raw = ""
        planner_failed = True
        violations.append(_violation("planner_failed", str(exc)))
    record_raw_response(
        raw, response_output_file, f"round_{round_index:02d}_{stage}", raw_records
    )
    parsed, returned_count, parse_violations = _parse_response(raw, config)
    violations.extend(parse_violations)
    simulated: list[_PathCandidate] = []
    summaries: list[JsonObject] = []
    for index, path in enumerate(parsed):
        result, path_violations = _simulate_path(path, view, evidence, index)
        violations.extend(path_violations)
        summaries.append({
            "round_index": round_index,
            "path_index": index,
            "simulatable": result is not None,
            "violations": path_violations,
            "before": _normalized_path(path),
            "after": _normalized_path(result) if result is not None else None,
        })
        if result is not None:
            simulated.append(result)
    simulated = _deduplicate_paths(simulated)
    return {
        "round_index": round_index,
        "stage": stage,
        "raw_response": raw,
        "returned_path_count": returned_count,
        "parsed_path_count": len(parsed),
        "simulatable_path_count": len(simulated),
        "simulated_paths": simulated,
        "violations": violations,
        "path_summaries": summaries,
        "planner_failed": planner_failed,
    }

def _parse_response(
    raw: str, config: MilestoneGenerationConfig
) -> tuple[list[_PathCandidate], int, list[JsonObject]]:
    violations: list[JsonObject] = []
    if not raw:
        return [], 0, violations
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], 0, [_violation("invalid_response", str(exc))]
    if not isinstance(payload, dict):
        return [], 0, [_violation("invalid_response", "顶层必须是 JSON object")]
    extra = sorted(set(payload) - {"paths"})
    if extra:
        violations.append(_violation("ignored_fields", f"忽略顶层字段: {extra}"))
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list):
        return [], 0, [*violations, _violation("invalid_response", "paths 必须是 list")]
    returned_count = len(raw_paths)
    if returned_count > config.max_candidate_path_count:
        violations.append(_violation(
            "paths_truncated",
            f"路径数 {returned_count} 超过上限 {config.max_candidate_path_count}",
        ))
    parsed: list[_PathCandidate] = []
    for index, value in enumerate(raw_paths[:config.max_candidate_path_count]):
        if isinstance(value, dict):
            ignored = set(value) - {"turns"}
            for turn in value.get("turns", []) if isinstance(value.get("turns"), list) else []:
                if not isinstance(turn, dict):
                    continue
                ignored.update(set(turn) - {
                    "turn_id", "disposition", "operations", "forbidden_evidence_ids",
                })
                for operation in turn.get("operations", []) if isinstance(turn.get("operations"), list) else []:
                    if isinstance(operation, dict):
                        ignored.update(set(operation) - {"evidence_id", "arguments"})
            if ignored:
                violations.append(_violation(
                    "ignored_fields", f"path[{index}] 忽略字段: {sorted(ignored)}",
                    path_index=index,
                ))
        try:
            parsed.append(_parse_path(value))
        except (TypeError, ValueError) as exc:
            violations.append(_violation("invalid_path", str(exc), path_index=index))
    return parsed, returned_count, violations

def _parse_path(value: object) -> _PathCandidate:
    if not isinstance(value, dict) or not isinstance(value.get("turns"), list):
        raise TypeError("path.turns 必须是 list")
    turns: list[_TurnCandidate] = []
    for turn_index, raw_turn in enumerate(value["turns"]):
        if not isinstance(raw_turn, dict):
            raise TypeError(f"turns[{turn_index}] 必须是 object")
        turn_id = raw_turn.get("turn_id")
        disposition = raw_turn.get("disposition")
        raw_operations = raw_turn.get("operations")
        raw_forbidden = raw_turn.get("forbidden_evidence_ids", [])
        if not isinstance(turn_id, str) or disposition not in _DISPOSITIONS:
            raise ValueError(f"turns[{turn_index}] turn_id/disposition 非法")
        if not isinstance(raw_operations, list) or not isinstance(raw_forbidden, list):
            raise TypeError(f"turns[{turn_index}] operations/forbidden 必须是 list")
        operations: list[_OperationCandidate] = []
        for operation_index, raw_operation in enumerate(raw_operations):
            if not isinstance(raw_operation, dict):
                raise TypeError(f"operations[{operation_index}] 必须是 object")
            evidence_id = raw_operation.get("evidence_id")
            arguments = raw_operation.get("arguments", {})
            if not isinstance(evidence_id, str) or not isinstance(arguments, dict):
                raise TypeError(f"operations[{operation_index}] evidence_id/arguments 非法")
            operations.append(_OperationCandidate(evidence_id, json_safe(arguments)))
        if any(not isinstance(item, str) for item in raw_forbidden):
            raise TypeError(f"turns[{turn_index}] forbidden evidence 必须是 string")
        turns.append(_TurnCandidate(
            turn_id, disposition, tuple(operations), tuple(sorted(set(raw_forbidden)))
        ))
    return _PathCandidate(tuple(turns))

def _simulate_path(
    path: _PathCandidate,
    view: GeneratorTaskView,
    evidence: dict[str, PublicEvidence],
    path_index: int,
) -> tuple[_PathCandidate | None, list[JsonObject]]:
    """按显式环境规则离线模拟路径，并递归插入必要 recovery。"""
    expected_turns = [turn.turn_id for turn in view.turns]
    if [turn.turn_id for turn in path.turns] != expected_turns:
        return None, [_violation(
            "turn_sequence_mismatch", "路径必须完整、按顺序覆盖所有 turns",
            path_index=path_index,
        )]
    state = copy.deepcopy(view.initial_state)
    output_turns: list[_TurnCandidate] = []
    for turn in path.turns:
        if turn.disposition != "executable" and turn.operations:
            return None, [_violation(
                "non_executable_has_operations", "非 executable turn 不能包含 operation",
                path_index=path_index, turn_id=turn.turn_id,
            )]
        if turn.disposition == "executable" and turn.forbidden_evidence_ids:
            return None, [_violation(
                "executable_has_forbidden", "executable turn 的 forbidden 必须为空",
                path_index=path_index, turn_id=turn.turn_id,
            )]
        for evidence_id in (*turn.forbidden_evidence_ids, *(op.evidence_id for op in turn.operations)):
            if evidence_id not in evidence:
                return None, [_violation(
                    "unknown_evidence", f"未知 evidence: {evidence_id}",
                    path_index=path_index, turn_id=turn.turn_id,
                )]
        simulated_operations: list[_OperationCandidate] = []
        for operation in turn.operations:
            error = _simulate_operation(
                operation, state, view.environment_rules, evidence,
                simulated_operations, (),
            )
            if error is not None:
                return None, [_violation(
                    "environment_simulation_failed", error,
                    path_index=path_index, turn_id=turn.turn_id,
                )]
        output_turns.append(_TurnCandidate(
            turn.turn_id, turn.disposition, tuple(simulated_operations),
            turn.forbidden_evidence_ids,
        ))
    return _PathCandidate(tuple(output_turns)), []

def _simulate_operation(
    operation: _OperationCandidate,
    state: JsonObject,
    rules: JsonObject,
    evidence: dict[str, PublicEvidence],
    output: list[_OperationCandidate],
    recovery_stack: tuple[str, ...],
) -> str | None:
    tool_name = _tool_name(evidence[operation.evidence_id])
    for rule_id, rule in rules.items():
        if not isinstance(rule, dict) or not _rule_applies(rule, tool_name, operation.arguments):
            continue
        required = {
            "state_path", "blocked_value", "recovery_tool",
            "recovery_arguments", "recovered_value",
        }
        if not required <= set(rule):
            return f"环境规则字段不完整: {rule_id}"
        state_path = rule["state_path"]
        if not isinstance(state_path, str) or _state_value(state, state_path) != rule["blocked_value"]:
            continue
        recovery_tool = rule["recovery_tool"]
        recovery_arguments = rule["recovery_arguments"]
        if not isinstance(recovery_tool, str) or not isinstance(recovery_arguments, dict):
            return f"环境规则 recovery 非法: {rule_id}"
        if recovery_tool in recovery_stack or recovery_tool == tool_name:
            return f"recovery cycle: {' -> '.join((*recovery_stack, tool_name, recovery_tool))}"
        recovery_evidence = _evidence_for_tool(recovery_tool, evidence)
        if recovery_evidence is None:
            return f"recovery tool 不可见: {recovery_tool}"
        recovery = _OperationCandidate(recovery_evidence, json_safe(recovery_arguments))
        error = _simulate_operation(
            recovery, state, rules, evidence, output, (*recovery_stack, tool_name)
        )
        if error is not None:
            return error
        _set_state_value(state, state_path, rule["recovered_value"])
    output.append(operation)
    _apply_recovery_effects(tool_name, operation.arguments, state, rules)
    return None

def _mandatory_occurrences(
    paths: list[_PathCandidate], view: GeneratorTaskView
) -> set[tuple[str, str, int]]:
    if not paths:
        return set()
    result: set[tuple[str, str, int]] = set()
    for turn in view.turns:
        counters = [
            Counter(operation.evidence_id for candidate_turn in path.turns
                    if candidate_turn.turn_id == turn.turn_id
                    for operation in candidate_turn.operations)
            for path in paths
        ]
        evidence_ids = set.intersection(*(set(counter) for counter in counters)) if counters else set()
        for evidence_id in evidence_ids:
            for occurrence_index in range(min(counter[evidence_id] for counter in counters)):
                result.add((turn.turn_id, evidence_id, occurrence_index))
    return result

def _common_precedence(
    paths: list[_PathCandidate],
    mandatory: set[tuple[str, str, int]],
    view: GeneratorTaskView,
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    milestone_ids = {
        item: f"m_{stable_json_digest(item)[:16]}" for item in mandatory
    }
    by_turn = {
        turn.turn_id: sorted(item for item in mandatory if item[0] == turn.turn_id)
        for turn in view.turns
    }
    positions = [_occurrence_positions(path) for path in paths]
    for occurrences in by_turn.values():
        for left in occurrences:
            for right in occurrences:
                if left != right and all(item[left] < item[right] for item in positions):
                    edges.append((milestone_ids[left], milestone_ids[right]))
    for left_turn, right_turn in zip(view.turns, view.turns[1:]):
        left = by_turn[left_turn.turn_id]
        right = by_turn[right_turn.turn_id]
        if not left or not right:
            continue
        left_sinks = [item for item in left if not any(
            (milestone_ids[item], milestone_ids[other]) in edges for other in left
        )]
        right_roots = [item for item in right if not any(
            (milestone_ids[other], milestone_ids[item]) in edges for other in right
        )]
        edges.extend(
            (milestone_ids[source], milestone_ids[target])
            for source in left_sinks for target in right_roots
        )
    return edges

def _ordered_occurrences(
    mandatory: set[tuple[str, str, int]],
    paths: list[_PathCandidate],
    view: GeneratorTaskView,
) -> list[tuple[str, str, int]]:
    turn_order = {turn.turn_id: index for index, turn in enumerate(view.turns)}
    positions = [_occurrence_positions(path) for path in paths]
    return sorted(
        mandatory,
        key=lambda item: (
            turn_order[item[0]],
            sum(position[item] for position in positions) / len(positions),
            item[1],
            item[2],
        ),
    )

def _forbidden_intersection(
    paths: list[_PathCandidate], view: GeneratorTaskView
) -> dict[str, set[str]]:
    if not paths:
        return {}
    result: dict[str, set[str]] = {}
    for turn in view.turns:
        forbidden_sets = [
            set(candidate_turn.forbidden_evidence_ids)
            for path in paths for candidate_turn in path.turns
            if candidate_turn.turn_id == turn.turn_id
        ]
        result[turn.turn_id] = (
            set.intersection(*forbidden_sets) if forbidden_sets else set()
        )
    return result

def _evidence_catalog(view: GeneratorTaskView) -> dict[str, PublicEvidence]:
    result: dict[str, PublicEvidence] = {}
    for item in view.evidence_catalog:
        if item.evidence_id in result:
            raise ValueError(f"evidence ID 重复: {item.evidence_id}")
        if item.target != ConstraintTarget.TOOL_CALL:
            continue
        _tool_name(item)
        result[item.evidence_id] = item
    if not result and view.tool_schema.get("tools"):
        raise ValueError("tool schema 非空但没有 TOOL_CALL evidence")
    return result

def _tool_name(evidence: PublicEvidence) -> str:
    value = evidence.metadata.get("tool_name")
    if not isinstance(value, str) or not value:
        raise ValueError(f"TOOL_CALL evidence 缺少真实 tool_name: {evidence.evidence_id}")
    return value

def _evidence_for_tool(
    tool_name: str, evidence: dict[str, PublicEvidence]
) -> str | None:
    return next(
        (item.evidence_id for item in evidence.values() if _tool_name(item) == tool_name),
        None,
    )

def _rule_applies(rule: JsonObject, tool_name: str, arguments: JsonObject) -> bool:
    tools = rule.get("applies_to_tools")
    expected = rule.get("applies_when_arguments", {})
    return (
        isinstance(tools, list)
        and tool_name in tools
        and isinstance(expected, dict)
        and all(arguments.get(key) == value for key, value in expected.items())
    )

def _state_value(state: JsonObject, path: str) -> object:
    parts = path.split(".")
    current: object = state.get("namespaces", state)
    for part in parts:
        if isinstance(current, list):
            current = current[-1] if current else {}
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

def _set_state_value(state: JsonObject, path: str, value: object) -> None:
    parts = path.split(".")
    namespaces = state.get("namespaces")
    current: JsonObject = namespaces if isinstance(namespaces, dict) else state
    for part in parts[:-1]:
        child = current.get(part)
        if isinstance(child, list):
            if not child:
                child.append({})
            if not isinstance(child[-1], dict):
                child[-1] = {}
            current = child[-1]
        elif isinstance(child, dict):
            current = child
        else:
            current[part] = {}
            current = current[part]
    current[parts[-1]] = json_safe(value)

def _apply_recovery_effects(
    tool_name: str, arguments: JsonObject, state: JsonObject, rules: JsonObject
) -> None:
    for rule in rules.values():
        if not isinstance(rule, dict):
            continue
        if rule.get("recovery_tool") != tool_name:
            continue
        expected = rule.get("recovery_arguments")
        if not isinstance(expected, dict) or not all(
            arguments.get(key) == value for key, value in expected.items()
        ):
            continue
        state_path = rule.get("state_path")
        if isinstance(state_path, str) and "recovered_value" in rule:
            _set_state_value(state, state_path, rule["recovered_value"])

def _deduplicate_paths(paths: list[_PathCandidate]) -> list[_PathCandidate]:
    result: list[_PathCandidate] = []
    seen: set[str] = set()
    for path in paths:
        key = json.dumps(_normalized_path(path), ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result

def _normalized_path(path: _PathCandidate | None) -> object:
    if path is None:
        return None
    return [
        [
            turn.turn_id,
            turn.disposition,
            [operation.evidence_id for operation in turn.operations],
            sorted(turn.forbidden_evidence_ids),
        ]
        for turn in path.turns
    ]

def _occurrence_positions(path: _PathCandidate) -> dict[tuple[str, str, int], int]:
    result: dict[tuple[str, str, int], int] = {}
    for turn in path.turns:
        counts: Counter[str] = Counter()
        for position, operation in enumerate(turn.operations):
            occurrence = (turn.turn_id, operation.evidence_id, counts[operation.evidence_id])
            counts[operation.evidence_id] += 1
            result[occurrence] = position
    return result

def _occurrence_label(occurrence: tuple[str, str, int]) -> str:
    return f"{occurrence[0]}:{occurrence[1]}#{occurrence[2]}"

def _turn_dispositions(
    paths: list[_PathCandidate], view: GeneratorTaskView
) -> JsonObject:
    result: JsonObject = {}
    for turn in view.turns:
        values = {
            candidate_turn.disposition
            for path in paths for candidate_turn in path.turns
            if candidate_turn.turn_id == turn.turn_id
        }
        result[turn.turn_id] = next(iter(values)) if len(values) == 1 else "mixed"
    return result

def _empty_reason(
    paths: list[_PathCandidate],
    mandatory: set[tuple[str, str, int]],
    draft: JsonObject,
    refined: JsonObject | None,
) -> str | None:
    if mandatory:
        return None
    if paths:
        has_operation = any(
            turn.operations for path in paths for turn in path.turns
        )
        return "no_common_operation" if has_operation else "no_operation_required"
    rounds = [draft, *([refined] if refined is not None else [])]
    if all(bool(item["planner_failed"]) for item in rounds):
        return "planner_failed"
    if any(
        violation.get("kind") == "invalid_response"
        for item in rounds for violation in item["violations"]
    ):
        return "invalid_response"
    return "no_simulatable_path"

def _round_summary(candidate: JsonObject) -> JsonObject:
    return {
        key: candidate[key]
        for key in (
            "round_index", "stage", "returned_path_count",
            "parsed_path_count", "simulatable_path_count", "violations",
        )
    }

def _assert_no_forbidden_generation_inputs(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"generator view 泄漏禁止字段: {path}.{key}")
            _assert_no_forbidden_generation_inputs(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_forbidden_generation_inputs(item, f"{path}[{index}]")

def _violation(kind: str, message: str, **metadata: object) -> JsonObject:
    return {"kind": kind, "message": message, **json_safe(metadata)}
