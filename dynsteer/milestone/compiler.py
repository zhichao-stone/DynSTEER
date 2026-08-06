from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING

from dynsteer.language import normalize_task_language
from dynsteer.milestone.model import (
    GenerationReport,
    GeneratorTaskView,
    MilestoneGenerationConfig,
    MilestoneGenerationError,
    PublicEvidence,
)
from dynsteer.model import (
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
)
from dynsteer.prompt.template import load_prompt_template
from dynsteer.utils import json_safe

if TYPE_CHECKING:
    from dynsteer.llm.base import BaseLLM


COMPILER_VERSION = "1.0"
MIN_DISTINCT_PATHS = 3
CONSENSUS_RATIO = 2 / 3
_HIDDEN_KEYS = frozenset(
    {
        "secret",
        "api_key",
        "password",
        "token",
        "gold",
        "ground_truth",
        "verifier",
        "reward",
        "matcher",
        "milestone_matcher",
        "minefield_matcher",
        "target_dataframe",
    }
)
_TOP_LEVEL_KEYS = frozenset({"atoms", "paths", "minefields"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Atom:
    atom_id: str
    name: str
    description: str
    source_refs: tuple[str, ...]
    evidence_id: str
    expected: JsonValue
    terminal: bool


@dataclass(frozen=True)
class _Path:
    strategy: str
    atom_ids: tuple[str, ...]


def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
) -> tuple[MilestoneGraph, GenerationReport]:
    """基于公开任务契约一次生成并编译 milestone graph。

    入参：公开任务视图、生成配置和 LLM。
    输出：可执行 MilestoneGraph 与生成审计报告。
    """
    if view is None or config is None or llm is None:
        raise ValueError("view、config 和 llm 不能为空")

    # 生成器每个 case 只调用一次，同时返回共享 atom 和全部差异路径。
    payload = _generator_payload(view, config.simulated_path_count)
    template = load_prompt_template("milestone", "generation")
    prompt = template.render(
        normalize_task_language(view.language),
        payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    logger.info(
        "开始生成 milestone 候选",
        extra={
            "事件": "milestone生成开始",
            "benchmark": view.benchmark,
            "case_id": view.case_id,
        },
    )
    raw_response = llm.chat([LLMMessage(role="user", content=prompt)])

    reasons: list[str] = []
    try:
        response = _parse_response(raw_response)
    except (TypeError, ValueError) as exc:
        _reject(str(exc), reasons=(str(exc),))
    leakage_count = _count_hidden_leakage(response)
    if leakage_count:
        _reject("LLM 输出包含隐藏字段", leakage_count=leakage_count)

    try:
        source_values = _source_values(view)
        evidence_by_id = _evidence_catalog(view, source_values)
    except (TypeError, ValueError) as exc:
        _reject(str(exc), leakage_count=leakage_count)
    atoms = _parse_atoms(response["atoms"], source_values, evidence_by_id, reasons)
    atom_by_id = {atom.atom_id: atom for atom in atoms}
    paths = _parse_paths(response["paths"], atom_by_id, reasons)
    distinct_paths = _distinct_paths(paths)
    path_summaries = _path_summaries(paths, distinct_paths, atom_by_id)
    if len(distinct_paths) < MIN_DISTINCT_PATHS:
        _reject(
            "有效差异路径少于 3 条",
            valid_path_count=len(paths),
            distinct_path_count=len(distinct_paths),
            contract_atom_count=len(atoms),
            reasons=tuple(reasons),
            path_summaries=path_summaries,
        )

    # 只有达到固定三分之二路径共识的可执行契约 atom 才进入 graph。
    threshold = ceil(len(distinct_paths) * CONSENSUS_RATIO)
    occurrences = Counter(
        atom_id for path in distinct_paths for atom_id in set(path.atom_ids)
    )
    retained_ids = {
        atom.atom_id for atom in atoms if occurrences[atom.atom_id] >= threshold
    }
    retained_atoms = [atom for atom in atoms if atom.atom_id in retained_ids]
    nodes = [
        _milestone_from_atom(atom, evidence_by_id[atom.evidence_id])
        for atom in retained_atoms
    ]
    edges = _consistent_reduced_edges(distinct_paths, retained_ids)
    minefields = _compile_minefields(
        response["minefields"], view, source_values, evidence_by_id, reasons
    )
    graph_valid = bool(nodes) and edges is not None and _is_dag(retained_ids, edges)
    if not graph_valid:
        _reject(
            "生成 graph 缺少可执行 milestone 或不是 DAG",
            valid_path_count=len(paths),
            distinct_path_count=len(distinct_paths),
            contract_atom_count=len(atoms),
            consensus_node_count=len(nodes),
            reasons=tuple(reasons),
            path_summaries=path_summaries,
        )

    graph = MilestoneGraph(
        nodes=nodes,
        edges=edges,
        minefields=minefields,
        metadata={
            "source": "generated",
            "compiler_version": COMPILER_VERSION,
            "view_digest": view.digest(),
            "necessity_basis": "synthetic_consensus",
        },
    )
    report = GenerationReport(
        generation_status="generated",
        valid_path_count=len(paths),
        distinct_path_count=len(distinct_paths),
        contract_atom_count=len(atoms),
        consensus_node_count=len(nodes),
        graph_valid=True,
        leakage_count=0,
        reasons=tuple(reasons),
        path_summaries=path_summaries,
    )
    logger.info(
        "milestone graph 生成完成",
        extra={
            "事件": "milestone生成完成",
            "case_id": view.case_id,
            "有效路径数": len(distinct_paths),
            "共识节点数": len(nodes),
        },
    )
    return graph, report


def _generator_payload(view: GeneratorTaskView, path_count: int) -> JsonObject:
    return {
        "task": json_safe(view),
        "allowed_evidence_ids": [item.evidence_id for item in view.evidence_catalog],
        "allowed_invariant_ids": [item.invariant_id for item in view.invariant_catalog],
        "simulated_path_count": path_count,
    }


def _parse_response(raw: str) -> dict[str, list[object]]:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("LLM milestone 返回必须是 JSON 对象") from exc
    if not isinstance(data, dict) or set(data) != _TOP_LEVEL_KEYS:
        raise ValueError("LLM milestone 顶层字段必须且只能是 atoms、paths、minefields")
    if any(not isinstance(data[key], list) for key in _TOP_LEVEL_KEYS):
        raise ValueError("LLM milestone 顶层字段必须是数组")
    return data


def _parse_atoms(
    values: list[object],
    source_values: dict[str, JsonValue],
    evidence_by_id: dict[str, PublicEvidence],
    reasons: list[str],
) -> list[_Atom]:
    atoms: list[_Atom] = []
    seen: set[str] = set()
    required = {
        "atom_id",
        "name",
        "description",
        "source_refs",
        "evidence_id",
        "expected",
        "terminal",
    }
    for index, value in enumerate(values):
        try:
            if not isinstance(value, dict) or set(value) != required:
                raise ValueError("schema 不合法")
            atom_id = _nonempty(value["atom_id"], "atom_id")
            if atom_id in seen:
                raise ValueError("atom_id 重复")
            source_refs = value["source_refs"]
            if not isinstance(source_refs, list) or not source_refs:
                raise ValueError("source_refs 必须是非空数组")
            normalized_refs = tuple(
                _nonempty(item, "source_ref") for item in source_refs
            )
            if any(ref not in source_values for ref in normalized_refs):
                raise ValueError("引用未知 source_ref")
            evidence_id = _nonempty(value["evidence_id"], "evidence_id")
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None or evidence.source_ref not in normalized_refs:
                raise ValueError("引用未知或不匹配的 evidence_id")
            _validate_expected(value["expected"], evidence, source_values)
            if not isinstance(value["terminal"], bool):
                raise TypeError("terminal 必须是 bool")
            atoms.append(
                _Atom(
                    atom_id=atom_id,
                    name=_nonempty(value["name"], "name"),
                    description=_nonempty(value["description"], "description"),
                    source_refs=normalized_refs,
                    evidence_id=evidence_id,
                    expected=value["expected"],
                    terminal=value["terminal"],
                )
            )
            seen.add(atom_id)
        except (TypeError, ValueError) as exc:
            reasons.append(f"atom[{index}] rejected: {exc}")
    return atoms


def _parse_paths(
    values: list[object], atom_by_id: dict[str, _Atom], reasons: list[str]
) -> list[_Path]:
    paths: list[_Path] = []
    for index, value in enumerate(values):
        try:
            if not isinstance(value, dict) or set(value) != {"strategy", "atom_ids"}:
                raise ValueError("schema 不合法")
            atom_ids = value["atom_ids"]
            if not isinstance(atom_ids, list) or not atom_ids:
                raise ValueError("atom_ids 必须是非空数组")
            normalized_ids = tuple(_nonempty(item, "atom_id") for item in atom_ids)
            if len(set(normalized_ids)) != len(normalized_ids):
                raise ValueError("路径包含重复 atom")
            if any(atom_id not in atom_by_id for atom_id in normalized_ids):
                raise ValueError("路径引用未知 atom")
            if not atom_by_id[normalized_ids[-1]].terminal:
                raise ValueError("路径未以 terminal atom 结束")
            paths.append(
                _Path(_nonempty(value["strategy"], "strategy"), normalized_ids)
            )
        except ValueError as exc:
            reasons.append(f"path[{index}] rejected: {exc}")
    return paths


def _distinct_paths(paths: list[_Path]) -> list[_Path]:
    result: list[_Path] = []
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        if path.atom_ids not in seen:
            result.append(path)
            seen.add(path.atom_ids)
    return result


def _path_summaries(
    paths: list[_Path], distinct_paths: list[_Path], atom_by_id: dict[str, _Atom]
) -> tuple[JsonObject, ...]:
    distinct = {path.atom_ids for path in distinct_paths}
    return tuple(
        {
            "strategy": path.strategy,
            "atom_ids": list(path.atom_ids),
            "distinct": path.atom_ids in distinct,
            "terminal": bool(path.atom_ids and atom_by_id[path.atom_ids[-1]].terminal),
        }
        for path in paths
    )


def _milestone_from_atom(atom: _Atom, evidence: PublicEvidence) -> Milestone:
    constraint = Constraint(
        constraint_id=f"{atom.atom_id}_constraint",
        target=evidence.target,
        selector=evidence.selector,
        operator=evidence.operator,
        expected=atom.expected,
        namespace=evidence.namespace,
        hard=True,
        evaluator_hint=evidence.evaluator_hint,
        metadata={"evidence_id": evidence.evidence_id, **dict(evidence.metadata)},
    )
    return Milestone(
        milestone_id=atom.atom_id,
        name=atom.name,
        description=atom.description,
        constraints=[constraint],
        metadata={
            "source_refs": list(atom.source_refs),
            "evidence_id": atom.evidence_id,
            "necessity_basis": "synthetic_consensus",
        },
    )


def _compile_minefields(
    values: list[object],
    view: GeneratorTaskView,
    source_values: dict[str, JsonValue],
    evidence_by_id: dict[str, PublicEvidence],
    reasons: list[str],
) -> list[Minefield]:
    invariant_by_id = {item.invariant_id: item for item in view.invariant_catalog}
    result: list[Minefield] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        try:
            if not isinstance(value, dict) or set(value) != {
                "minefield_id",
                "invariant_id",
                "expected",
            }:
                raise ValueError("schema 不合法")
            minefield_id = _nonempty(value["minefield_id"], "minefield_id")
            if minefield_id in seen:
                raise ValueError("minefield_id 重复")
            invariant_id = _nonempty(value["invariant_id"], "invariant_id")
            invariant = invariant_by_id.get(invariant_id)
            if invariant is None:
                raise ValueError("引用未知 invariant")
            evidence = evidence_by_id.get(invariant.evidence_id)
            if evidence is None or evidence.source_ref != invariant.source_ref:
                raise ValueError("invariant 缺少可执行 evidence")
            _validate_expected(value["expected"], evidence, source_values)
            constraint = Constraint(
                constraint_id=f"{minefield_id}_constraint",
                target=evidence.target,
                selector=evidence.selector,
                operator=evidence.operator,
                expected=value["expected"],
                namespace=evidence.namespace,
                hard=True,
                evaluator_hint=evidence.evaluator_hint,
                metadata={
                    "evidence_id": evidence.evidence_id,
                    **dict(evidence.metadata),
                },
            )
            penalty = {"warning": 0.25, "error": 0.5, "fatal": 1.0}[invariant.severity]
            result.append(
                Minefield(
                    minefield_id=minefield_id,
                    name=invariant.description,
                    description=invariant.description,
                    severity=invariant.severity,
                    constraints=[constraint],
                    penalty=MinefieldPenalty(mode="fixed", value=penalty),
                    metadata={
                        "invariant_id": invariant_id,
                        "source_ref": invariant.source_ref,
                    },
                )
            )
            seen.add(minefield_id)
        except ValueError as exc:
            reasons.append(f"minefield[{index}] rejected: {exc}")
    return result


def _source_values(view: GeneratorTaskView) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {"instruction": view.instruction}
    for container in (
        view.public_assets,
        view.tool_schema,
        view.environment_schema,
        view.output_contract,
    ):
        _collect_source_values(container, values)
    return values


def _evidence_catalog(
    view: GeneratorTaskView, source_values: dict[str, JsonValue]
) -> dict[str, PublicEvidence]:
    result: dict[str, PublicEvidence] = {}
    for evidence in view.evidence_catalog:
        if evidence.evidence_id in result:
            raise ValueError(f"evidence_id 重复: {evidence.evidence_id}")
        if evidence.source_ref not in source_values:
            raise ValueError(f"evidence source_ref 未登记: {evidence.source_ref}")
        if not isinstance(evidence.target, ConstraintTarget):
            raise TypeError(f"evidence target 不合法: {evidence.evidence_id}")
        if not isinstance(evidence.operator, Operator):
            raise TypeError(f"evidence operator 不合法: {evidence.evidence_id}")
        if evidence.expected_policy not in {"public_literal", "none"}:
            raise ValueError(f"expected_policy 不合法: {evidence.evidence_id}")
        if evidence.selector != "$" and not evidence.selector.startswith("$."):
            raise ValueError(f"constraint selector 不合法: {evidence.selector}")
        if not evidence.evaluator_hint.strip():
            raise ValueError(f"evaluator_hint 不能为空: {evidence.evidence_id}")
        result[evidence.evidence_id] = evidence
    invariant_ids: set[str] = set()
    for invariant in view.invariant_catalog:
        if invariant.invariant_id in invariant_ids:
            raise ValueError(f"invariant_id 重复: {invariant.invariant_id}")
        if invariant.source_ref not in source_values:
            raise ValueError(f"invariant source_ref 未登记: {invariant.source_ref}")
        invariant_ids.add(invariant.invariant_id)
    return result


def _collect_source_values(value: object, result: dict[str, JsonValue]) -> None:
    if isinstance(value, dict):
        source_ref = value.get("source_ref")
        if isinstance(source_ref, str) and source_ref.strip():
            if source_ref in result:
                raise ValueError(f"source_ref 重复: {source_ref}")
            result[source_ref] = value.get(
                "value",
                {key: item for key, item in value.items() if key != "source_ref"},
            )
        for item in value.values():
            _collect_source_values(item, result)
    elif isinstance(value, list):
        for item in value:
            _collect_source_values(item, result)


def _validate_expected(
    expected: object, evidence: PublicEvidence, source_values: dict[str, JsonValue]
) -> None:
    if evidence.expected_policy == "none":
        if expected is not None:
            raise ValueError("expected_policy=none 时 expected 必须为 null")
        return
    if evidence.source_ref not in source_values:
        raise ValueError("evidence source_ref 未登记")
    if not _is_public_literal(expected, source_values[evidence.source_ref]):
        raise ValueError("expected 不是 source_ref 的公开 literal")


def _is_public_literal(expected: object, source: object) -> bool:
    if expected == source:
        return True
    if isinstance(source, dict):
        return any(_is_public_literal(expected, value) for value in source.values())
    if isinstance(source, list):
        return any(_is_public_literal(expected, value) for value in source)
    return False


def _consistent_reduced_edges(
    paths: list[_Path], retained_ids: set[str]
) -> list[tuple[str, str]] | None:
    ordered_ids = sorted(retained_ids)
    edges: set[tuple[str, str]] = set()
    for left_index, left in enumerate(ordered_ids):
        for right in ordered_ids[left_index + 1 :]:
            containing = [
                path.atom_ids
                for path in paths
                if left in path.atom_ids and right in path.atom_ids
            ]
            if not containing:
                continue
            orders = {items.index(left) < items.index(right) for items in containing}
            if len(orders) == 1:
                edges.add((left, right) if orders.pop() else (right, left))
    if not _is_dag(retained_ids, sorted(edges)):
        return None
    return sorted(_transitive_reduction(edges))


def _transitive_reduction(
    edges: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    reduced = set(edges)
    for edge in sorted(edges):
        reduced.remove(edge)
        if not _reachable(edge[0], edge[1], reduced):
            reduced.add(edge)
    return reduced


def _reachable(source: str, target: str, edges: set[tuple[str, str]]) -> bool:
    successors: dict[str, set[str]] = {}
    for left, right in edges:
        successors.setdefault(left, set()).add(right)
    pending = list(successors.get(source, set()))
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(successors.get(current, set()) - visited)
    return False


def _is_dag(node_ids: set[str], edges: list[tuple[str, str]]) -> bool:
    successors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for source, target in edges:
        if source == target or source not in node_ids or target not in node_ids:
            return False
        successors[source].append(target)
        indegree[target] += 1
    pending = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        for target in successors[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    return visited == len(node_ids)


def _count_hidden_leakage(value: object, parent_key: str = "") -> int:
    count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            count += int(normalized in _HIDDEN_KEYS)
            count += _count_hidden_leakage(item, normalized)
    elif isinstance(value, list):
        count += sum(_count_hidden_leakage(item, parent_key) for item in value)
    return count


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()


def _reject(
    message: str,
    *,
    valid_path_count: int = 0,
    distinct_path_count: int = 0,
    contract_atom_count: int = 0,
    consensus_node_count: int = 0,
    leakage_count: int = 0,
    reasons: tuple[str, ...] = (),
    path_summaries: tuple[JsonObject, ...] = (),
) -> None:
    report = GenerationReport(
        generation_status="auto_rejected",
        valid_path_count=valid_path_count,
        distinct_path_count=distinct_path_count,
        contract_atom_count=contract_atom_count,
        consensus_node_count=consensus_node_count,
        graph_valid=False,
        leakage_count=leakage_count,
        reasons=(*reasons, message),
        path_summaries=path_summaries,
    )
    logger.error(
        "milestone 自动生成被拒绝", extra={"事件": "milestone生成拒绝", "原因": message}
    )
    raise MilestoneGenerationError(message, report)
