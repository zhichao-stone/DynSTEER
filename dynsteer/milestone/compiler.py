from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

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

if TYPE_CHECKING:
    from dynsteer.llm.base import BaseLLM


CONSENSUS_RATIO = 2 / 3
PATH_STRATEGIES = (
    "direct-shortest",
    "prerequisite-first",
    "state-check-first",
    "artifact-or-result-first",
    "alternative-tool",
    "verification-first",
    "conservative",
)
logger = logging.getLogger(__name__)

_PathSignature = tuple[str, str, int]


@dataclass(frozen=True)
class _PathAtom:
    name: str
    description: str
    evidence_id: str
    expected: JsonValue
    terminal: bool


@dataclass(frozen=True)
class _PathCandidate:
    path_index: int
    strategy: str
    atoms: tuple[_PathAtom, ...]
    minefield_invariant_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AlignedPath:
    path_index: int
    strategy: str
    atoms: tuple[_PathAtom, ...]
    signatures: tuple[_PathSignature, ...]
    minefield_invariant_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ConsensusCluster:
    representative: _PathAtom
    support_count: int
    terminal_support_count: int


class _RecordedPathResponseError(ValueError):
    """单路径响应已落盘、但后续解析或校验失败。"""

    def __init__(self, message: str, response_record: JsonObject | None) -> None:
        super().__init__(message)
        self.response_record = response_record


def compile_task_case(
    view: GeneratorTaskView,
    config: MilestoneGenerationConfig,
    llm: BaseLLM,
    response_output_file: Path | None = None,
) -> tuple[MilestoneGraph, GenerationReport]:
    """基于公开任务契约独立模拟路径并编译 milestone graph。

    入参：公开任务视图、生成配置、LLM 和可选原始响应目录。
    输出：可执行 MilestoneGraph 与生成审计报告。
    """
    if view is None or config is None or llm is None:
        raise ValueError("view、config 和 llm 不能为空")

    requested = config.simulated_path_count
    minimum = _minimum_valid_path_count(requested)
    try:
        source_values = _source_values(view)
        evidence_by_id = _evidence_catalog(view, source_values)
        payload = _generator_payload(view, source_values)
    except (KeyError, TypeError, ValueError) as exc:
        _reject(
            str(exc),
            requested_path_count=requested,
            minimum_valid_path_count=minimum,
        )

    # 每条路径使用独立调用，单条失败只淘汰自身。
    valid_paths: list[_PathCandidate] = []
    path_summaries: list[JsonObject] = []
    raw_responses: dict[str, str] = {}
    reasons: list[str] = []
    logger.info(
        "开始独立生成 milestone 路径",
        extra={"事件": "milestone生成开始", "case_id": view.case_id, "路径数": requested},
    )
    for path_index in range(requested):
        strategy = PATH_STRATEGIES[path_index % len(PATH_STRATEGIES)]
        try:
            candidate, response_record = _simulate_path(
                payload,
                path_index,
                requested,
                strategy,
                llm,
                evidence_by_id,
                source_values,
                view,
                response_output_file,
                raw_responses,
            )
        except _RecordedPathResponseError as exc:
            reason = f"path[{path_index}] rejected: {exc}"
            reasons.append(reason)
            path_summaries.append(
                _rejected_path_summary(
                    path_index,
                    strategy,
                    reason,
                    exc.response_record,
                )
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            reason = f"path[{path_index}] rejected: {exc}"
            reasons.append(reason)
            path_summaries.append(_rejected_path_summary(path_index, strategy, reason))
        else:
            valid_paths.append(candidate)
            path_summaries.append(_valid_path_summary(candidate, response_record))

    aligned_paths = [_align_path(path) for path in valid_paths]
    distinct_paths = _distinct_aligned_paths(aligned_paths)
    path_summaries = _mark_duplicate_summaries(path_summaries, distinct_paths)
    candidate_atom_count = sum(len(path.atoms) for path in valid_paths)
    aligned_atom_count = len(
        {signature for path in distinct_paths for signature in path.signatures}
    )
    if len(distinct_paths) < minimum:
        _reject(
            f"有效差异路径不足: requested={requested}, minimum={minimum}, "
            f"actual={len(distinct_paths)}",
            requested_path_count=requested,
            minimum_valid_path_count=minimum,
            valid_path_count=len(valid_paths),
            distinct_path_count=len(distinct_paths),
            candidate_atom_count=candidate_atom_count,
            aligned_atom_count=aligned_atom_count,
            reasons=tuple(reasons),
            path_summaries=tuple(path_summaries),
        )

    # 所有共识图错误由这一处统一转换为带报告的自动拒绝。
    threshold = ceil(len(distinct_paths) * CONSENSUS_RATIO)
    try:
        clusters = _consensus_clusters(distinct_paths, threshold)
        if not clusters or not any(
            cluster.terminal_support_count >= threshold
            for cluster in clusters.values()
        ):
            raise ValueError("三分之二共识未产生可执行 terminal milestone")
        nodes = _milestones_from_clusters(clusters, evidence_by_id)
        edges = _consensus_edges(distinct_paths, set(clusters), threshold)
        minefields = _consensus_minefields(
            distinct_paths,
            threshold,
            view,
            evidence_by_id,
            source_values,
        )
    except ValueError as exc:
        _reject(
            str(exc),
            requested_path_count=requested,
            minimum_valid_path_count=minimum,
            valid_path_count=len(valid_paths),
            distinct_path_count=len(distinct_paths),
            candidate_atom_count=candidate_atom_count,
            aligned_atom_count=aligned_atom_count,
            consensus_node_count=len(clusters) if "clusters" in locals() else 0,
            reasons=tuple(reasons),
            path_summaries=tuple(path_summaries),
        )

    graph = MilestoneGraph(
        nodes=nodes,
        edges=edges,
        minefields=minefields,
        metadata={
            "source": "generated",
            "view_digest": view.digest(),
            "necessity_basis": "synthetic_consensus",
        },
    )
    report = GenerationReport(
        generation_status="generated",
        requested_path_count=requested,
        minimum_valid_path_count=minimum,
        valid_path_count=len(valid_paths),
        distinct_path_count=len(distinct_paths),
        candidate_atom_count=candidate_atom_count,
        aligned_atom_count=aligned_atom_count,
        consensus_node_count=len(nodes),
        graph_valid=True,
        reasons=tuple(reasons),
        path_summaries=tuple(path_summaries),
    )
    logger.info(
        "milestone graph 生成完成",
        extra={
            "事件": "milestone生成完成",
            "case_id": view.case_id,
            "有效去重路径数": len(distinct_paths),
            "共识节点数": len(nodes),
        },
    )
    return graph, report


def _minimum_valid_path_count(requested_path_count: int) -> int:
    """返回 N−2 且不低于 3 的有效去重路径下限。"""
    return max(requested_path_count - 2, 3)


def _generator_payload(
    view: GeneratorTaskView,
    source_values: dict[str, JsonValue],
) -> JsonObject:
    evidence = [
        {
            "evidence_id": item.evidence_id,
            "source_ref": item.source_ref,
            "target": item.target.value,
            "selector": item.selector,
            "operator": item.operator.value,
            "expected_policy": item.expected_policy,
            "allowed_expected_literals": _allowed_expected_literals(
                item, source_values
            ),
        }
        for item in view.evidence_catalog
    ]
    invariants = [
        {
            "invariant_id": item.invariant_id,
            "description": item.description,
            "source_ref": item.source_ref,
            "evidence_id": item.evidence_id,
            "severity": item.severity,
        }
        for item in view.invariant_catalog
    ]
    return {
        "task": _public_task_payload(view),
        "evidence": evidence,
        "invariants": invariants,
    }


def _public_task_payload(view: GeneratorTaskView) -> JsonObject:
    return {
        "instruction": view.instruction,
        "public_assets": [
            asset
            for asset in view.public_assets
            if asset.get("kind") != "invariant_source"
        ],
        "tool_schema": view.tool_schema,
        "environment_schema": view.environment_schema,
        "output_contract": view.output_contract,
    }


def _simulate_path(
    payload: JsonObject,
    path_index: int,
    path_count: int,
    strategy: str,
    llm: BaseLLM,
    evidence_by_id: dict[str, PublicEvidence],
    source_values: dict[str, JsonValue],
    view: GeneratorTaskView,
    response_output_file: Path | None,
    raw_responses: dict[str, str],
) -> tuple[_PathCandidate, JsonObject | None]:
    """独立生成并解析一条候选路径。"""
    template = load_prompt_template("milestone", "generation")
    prompt = template.render(
        normalize_task_language(view.language),
        task=json.dumps(payload["task"], ensure_ascii=False, sort_keys=True),
        evidence=json.dumps(payload["evidence"], ensure_ascii=False, sort_keys=True),
        invariants=json.dumps(payload["invariants"], ensure_ascii=False, sort_keys=True),
        path_index=path_index,
        path_count=path_count,
        strategy=strategy,
    )
    raw = llm.chat([LLMMessage(role="user", content=prompt)])
    response_record = _record_raw_response(
        raw,
        response_output_file,
        path_index,
        strategy,
        raw_responses,
    )
    try:
        candidate = _parse_path_response(
            raw,
            path_index,
            strategy,
            evidence_by_id,
            source_values,
            view,
        )
    except (TypeError, ValueError) as exc:
        raise _RecordedPathResponseError(str(exc), response_record) from exc
    return candidate, response_record


def _record_raw_response(
    raw: str,
    output_file: Path | None,
    path_index: int,
    strategy: str,
    responses: dict[str, str],
) -> JsonObject | None:
    """把单次 LLM 原始响应写入当前 case 的聚合 JSON 文件。"""
    if output_file is None:
        return None
    response_key = f"path_{path_index:02d}_{strategy}"
    responses[response_key] = raw
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_suffix(f"{output_file.suffix}.tmp")
    temporary_file.write_text(
        json.dumps(responses, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(output_file)
    stripped = raw.lstrip()
    return {
        "path": str(output_file.resolve()),
        "key": response_key,
        "character_count": len(raw),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "starts_with_json_fence": stripped.lower().startswith("```json"),
        "starts_with_object": stripped.startswith("{"),
    }


def _parse_path_response(
    raw: str,
    path_index: int,
    strategy: str,
    evidence_by_id: dict[str, PublicEvidence],
    source_values: dict[str, JsonValue],
    view: GeneratorTaskView,
) -> _PathCandidate:
    """按精确 schema 解析并完整校验单条路径响应。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "LLM milestone JSON 解析失败: "
            f"{exc.msg}; line={exc.lineno}, column={exc.colno}, position={exc.pos}"
        ) from exc
    except TypeError as exc:
        raise TypeError("LLM milestone 返回必须是 JSON 文本") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM milestone 顶层必须是 JSON 对象")
    if set(data) != {
        "atoms",
        "minefield_invariant_ids",
    }:
        raise ValueError("顶层字段必须且只能是 atoms、minefield_invariant_ids")
    values = data["atoms"]
    if not isinstance(values, list) or not values:
        raise ValueError("atoms 必须是非空数组")

    atoms: list[_PathAtom] = []
    atom_keys = {
        "name",
        "description",
        "evidence_id",
        "expected_literal_index",
        "terminal",
    }
    for atom_index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != atom_keys:
            raise ValueError(f"atom[{atom_index}] schema 不合法")
        name = _nonempty(value["name"], "name")
        description = _nonempty(value["description"], "description")
        evidence_id = _nonempty(value["evidence_id"], "evidence_id")
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise ValueError(f"atom[{atom_index}] 引用未知 evidence_id")
        literal_index = value["expected_literal_index"]
        if isinstance(literal_index, bool) or not isinstance(literal_index, int):
            raise TypeError(f"atom[{atom_index}] expected_literal_index 必须是整数")
        if evidence.expected_policy == "none":
            if literal_index != 0:
                raise ValueError("expected_policy=none 时 index 必须为 0")
            expected: JsonValue = None
        else:
            allowed = _allowed_expected_literals(evidence, source_values)
            if literal_index < 0 or literal_index >= len(allowed):
                raise ValueError(f"atom[{atom_index}] expected_literal_index 越界")
            expected = allowed[literal_index]
        terminal = value["terminal"]
        if not isinstance(terminal, bool):
            raise TypeError(f"atom[{atom_index}] terminal 必须是 bool")
        if terminal != (atom_index == len(values) - 1):
            raise ValueError("只有最后一个 atom 的 terminal 可以为 true")
        atoms.append(_PathAtom(name, description, evidence_id, expected, terminal))

    invariant_ids = data["minefield_invariant_ids"]
    if not isinstance(invariant_ids, list):
        raise TypeError("minefield_invariant_ids 必须是数组")
    normalized_ids = tuple(
        _nonempty(value, "invariant_id") for value in invariant_ids
    )
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("minefield_invariant_ids 不能重复")
    known_ids = {item.invariant_id for item in view.invariant_catalog}
    if any(invariant_id not in known_ids for invariant_id in normalized_ids):
        raise ValueError("minefield_invariant_ids 引用未知 invariant")
    return _PathCandidate(
        path_index,
        strategy,
        tuple(atoms),
        normalized_ids,
    )


def _align_path(path: _PathCandidate) -> _AlignedPath:
    occurrences: Counter[tuple[str, str]] = Counter()
    signatures: list[_PathSignature] = []
    for atom in path.atoms:
        base = (atom.evidence_id, _canonical_json(atom.expected))
        occurrence_index = occurrences[base]
        signatures.append((base[0], base[1], occurrence_index))
        occurrences[base] += 1
    return _AlignedPath(
        path.path_index,
        path.strategy,
        path.atoms,
        tuple(signatures),
        path.minefield_invariant_ids,
    )


def _distinct_aligned_paths(paths: list[_AlignedPath]) -> list[_AlignedPath]:
    result: list[_AlignedPath] = []
    seen: set[tuple[_PathSignature, ...]] = set()
    for path in sorted(paths, key=lambda item: item.path_index):
        if path.signatures not in seen:
            result.append(path)
            seen.add(path.signatures)
    return result


def _consensus_clusters(
    paths: list[_AlignedPath], threshold: int
) -> dict[_PathSignature, _ConsensusCluster]:
    support: Counter[_PathSignature] = Counter()
    terminal_support: Counter[_PathSignature] = Counter()
    representatives: dict[_PathSignature, tuple[int, int, _PathAtom]] = {}
    for path in paths:
        seen: set[_PathSignature] = set()
        for position, (signature, atom) in enumerate(zip(path.signatures, path.atoms)):
            candidate = (path.path_index, position, atom)
            if signature not in representatives or candidate[:2] < representatives[signature][:2]:
                representatives[signature] = candidate
            if signature in seen:
                continue
            seen.add(signature)
            support[signature] += 1
            terminal_support[signature] += int(atom.terminal)
    return {
        signature: _ConsensusCluster(
            representative=representatives[signature][2],
            support_count=count,
            terminal_support_count=terminal_support[signature],
        )
        for signature, count in support.items()
        if count >= threshold
    }


def _milestones_from_clusters(
    clusters: dict[_PathSignature, _ConsensusCluster],
    evidence_by_id: dict[str, PublicEvidence],
) -> list[Milestone]:
    nodes: list[Milestone] = []
    for signature, cluster in sorted(clusters.items(), key=lambda item: item[0]):
        atom = cluster.representative
        evidence = evidence_by_id[atom.evidence_id]
        milestone_id = _milestone_id(signature)
        constraint = Constraint(
            constraint_id=f"{milestone_id}_constraint",
            target=evidence.target,
            selector=evidence.selector,
            operator=evidence.operator,
            expected=atom.expected,
            namespace=evidence.namespace,
            hard=True,
            evaluator_hint=evidence.evaluator_hint,
            metadata={"evidence_id": evidence.evidence_id, **dict(evidence.metadata)},
        )
        nodes.append(
            Milestone(
                milestone_id=milestone_id,
                name=atom.name,
                description=atom.description,
                constraints=[constraint],
                metadata={
                    "evidence_id": atom.evidence_id,
                    "signature": list(signature),
                    "support_count": cluster.support_count,
                    "necessity_basis": "synthetic_consensus",
                },
            )
        )
    return nodes


def _milestone_id(signature: _PathSignature) -> str:
    digest = hashlib.sha256(
        _canonical_json(signature).encode("utf-8")
    ).hexdigest()[:12]
    return f"m_{digest}"


def _consensus_edges(
    paths: list[_AlignedPath],
    retained: set[_PathSignature],
    threshold: int,
) -> list[tuple[str, str]]:
    support: Counter[tuple[_PathSignature, _PathSignature]] = Counter()
    for path in paths:
        ordered = [signature for signature in path.signatures if signature in retained]
        for left_index, source in enumerate(ordered):
            support.update((source, target) for target in ordered[left_index + 1 :])
    signature_edges = {
        pair for pair, count in support.items() if count >= threshold
    }
    edges = sorted(
        (_milestone_id(source), _milestone_id(target))
        for source, target in signature_edges
    )
    node_ids = {_milestone_id(signature) for signature in retained}
    if not _is_dag(node_ids, edges):
        raise ValueError("共识 precedence 关系形成环")
    return sorted(_transitive_reduction(set(edges)))


def _consensus_minefields(
    paths: list[_AlignedPath],
    threshold: int,
    view: GeneratorTaskView,
    evidence_by_id: dict[str, PublicEvidence],
    source_values: dict[str, JsonValue],
) -> list[Minefield]:
    support = Counter(
        invariant_id
        for path in paths
        for invariant_id in set(path.minefield_invariant_ids)
    )
    invariant_by_id = {item.invariant_id: item for item in view.invariant_catalog}
    result: list[Minefield] = []
    for invariant_id in sorted(
        key for key, count in support.items() if count >= threshold
    ):
        invariant = invariant_by_id[invariant_id]
        evidence = evidence_by_id[invariant.evidence_id]
        if evidence.source_ref != invariant.source_ref:
            raise ValueError(f"invariant evidence 不匹配: {invariant_id}")
        if evidence.expected_policy == "none":
            expected: JsonValue = None
        else:
            literals = _allowed_expected_literals(evidence, source_values)
            if len(literals) != 1:
                raise ValueError(f"invariant 必须只关联一个公开 literal: {invariant_id}")
            expected = literals[0]
        digest = hashlib.sha256(invariant_id.encode("utf-8")).hexdigest()[:12]
        minefield_id = f"mf_{digest}"
        constraint = Constraint(
            constraint_id=f"{minefield_id}_constraint",
            target=evidence.target,
            selector=evidence.selector,
            operator=evidence.operator,
            expected=expected,
            namespace=evidence.namespace,
            hard=True,
            evaluator_hint=evidence.evaluator_hint,
            metadata={"evidence_id": evidence.evidence_id, **dict(evidence.metadata)},
        )
        penalty = {"warning": 0.25, "error": 0.5, "fatal": 1.0}[
            invariant.severity
        ]
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
                    "support_count": support[invariant_id],
                },
            )
        )
    return result


def _valid_path_summary(
    path: _PathCandidate, response_record: JsonObject | None
) -> JsonObject:
    return {
        "path_index": path.path_index,
        "strategy": path.strategy,
        "status": "valid",
        "distinct": True,
        "atom_count": len(path.atoms),
        "minefield_count": len(path.minefield_invariant_ids),
        "reason": None,
        "response_record": response_record,
    }


def _rejected_path_summary(
    path_index: int,
    strategy: str,
    reason: str,
    response_record: JsonObject | None = None,
) -> JsonObject:
    return {
        "path_index": path_index,
        "strategy": strategy,
        "status": "rejected",
        "distinct": False,
        "atom_count": 0,
        "minefield_count": 0,
        "reason": reason,
        "response_record": response_record,
    }


def _mark_duplicate_summaries(
    summaries: list[JsonObject], distinct_paths: list[_AlignedPath]
) -> list[JsonObject]:
    distinct_indexes = {path.path_index for path in distinct_paths}
    return [
        {
            **summary,
            "distinct": summary["status"] == "valid"
            and summary["path_index"] in distinct_indexes,
        }
        for summary in summaries
    ]


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
        if evidence.expected_policy == "public_literal" and not _allowed_expected_literals(
            evidence, source_values
        ):
            raise ValueError(f"evidence 缺少允许的公开值: {evidence.evidence_id}")
        result[evidence.evidence_id] = evidence
    invariant_ids: set[str] = set()
    for invariant in view.invariant_catalog:
        if invariant.invariant_id in invariant_ids:
            raise ValueError(f"invariant_id 重复: {invariant.invariant_id}")
        if invariant.source_ref not in source_values:
            raise ValueError(f"invariant source_ref 未登记: {invariant.source_ref}")
        if invariant.evidence_id not in result:
            raise ValueError(f"invariant evidence 未登记: {invariant.evidence_id}")
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


def _public_literals(value: object) -> list[JsonValue]:
    if isinstance(value, (dict, list)):
        result: list[JsonValue] = []
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            for literal in _public_literals(item):
                if literal not in result:
                    result.append(literal)
        return result
    if isinstance(value, (str, int, float, bool)) or value is None:
        return [value]
    return []


def _allowed_expected_literals(
    evidence: PublicEvidence, source_values: dict[str, JsonValue]
) -> list[JsonValue]:
    if evidence.expected_policy == "none":
        return []
    return _public_literals(source_values[evidence.source_ref])


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()


def _transitive_reduction(
    edges: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    reduced = set(edges)
    successors: dict[str, set[str]] = {}
    for left, right in edges:
        successors.setdefault(left, set()).add(right)
    for edge in sorted(edges):
        reduced.remove(edge)
        successors[edge[0]].remove(edge[1])
        if not _reachable(edge[0], edge[1], successors):
            reduced.add(edge)
            successors[edge[0]].add(edge[1])
    return reduced


def _reachable(source: str, target: str, successors: dict[str, set[str]]) -> bool:
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


def _reject(
    message: str,
    *,
    requested_path_count: int = 0,
    minimum_valid_path_count: int = 0,
    valid_path_count: int = 0,
    distinct_path_count: int = 0,
    candidate_atom_count: int = 0,
    aligned_atom_count: int = 0,
    consensus_node_count: int = 0,
    reasons: tuple[str, ...] = (),
    path_summaries: tuple[JsonObject, ...] = (),
) -> NoReturn:
    report = GenerationReport(
        generation_status="auto_rejected",
        requested_path_count=requested_path_count,
        minimum_valid_path_count=minimum_valid_path_count,
        valid_path_count=valid_path_count,
        distinct_path_count=distinct_path_count,
        candidate_atom_count=candidate_atom_count,
        aligned_atom_count=aligned_atom_count,
        consensus_node_count=consensus_node_count,
        graph_valid=False,
        reasons=(*reasons, message),
        path_summaries=path_summaries,
    )
    logger.error(
        "milestone 自动生成被拒绝",
        extra={"事件": "milestone生成拒绝", "原因": message},
    )
    raise MilestoneGenerationError(message, report)
