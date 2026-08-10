from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib
import importlib.util
import itertools
import json
import logging
import math
import shutil
import statistics
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional

import networkx as nx
from tqdm import tqdm

from dynsteer.adapter.loader import safe_case_file_name
from dynsteer.adapter.registry import get_adapter
from dynsteer.experiment.config import (
    build_harness_config,
    expand_experiment_matrix,
    load_experiment_config,
)
from dynsteer.experiment.model import ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.llm import build_llm_from_config, build_llm_from_env
from dynsteer.milestone import compile_task_case
from dynsteer.milestone.model import (
    MilestoneGenerationConfig,
    MilestoneGenerationError,
)
from dynsteer.model import JsonObject, MilestoneGraph, TaskCase


LOGGER = logging.getLogger("milestone_reliability")
DEFAULT_EDIT_COST_PROFILE: JsonObject = {
    "profile_id": "strict_v1",
    "node_insert": 1.0,
    "node_delete": 1.0,
    "edge_insert": 1.0,
    "edge_delete": 1.0,
    "node_label_distance": "canonical_json",
    "edge_label_distance": "exact",
}


class _InfrastructureError(RuntimeError):
    """表示 source、依赖或结果目录不可用。"""


@dataclass(frozen=True)
class _ReliabilitySpecGroup:
    benchmark: str
    data_root: Path
    case_ids: tuple[str, ...]
    harness_config: HarnessRunConfig
    milestone_generation: MilestoneGenerationConfig
    reliability_metadata: JsonObject
    results_dir: Path
    source_root: Path | None = None


@dataclass(frozen=True)
class _CaseResult:
    benchmark: str
    case_id: str
    status: str
    metrics: JsonObject
    output_path: Path
    generation_report: JsonObject
    reference_count: int | None
    prediction_count: int | None
    reference_summary: JsonObject
    prediction_summary: JsonObject
    diagnostics: tuple[str, ...] = ()
    elapsed_ms: int = 0


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """解析 milestone reliability 实验参数。"""
    parser = argparse.ArgumentParser(
        description="评估自动 milestone 生成图相对原生图的可靠性",
        epilog=(
            "示例: python milestone_reliability.py --exp "
            "data/experiments/toolsandbox_milestone_reliability_partial_main.json"
        ),
    )
    parser.add_argument(
        "--exp", "--experiment-config", dest="experiment_config", required=True
    )
    parser.add_argument("--workers", type=int)
    parser.add_argument("--ged-solver", choices=("exact", "approximate"))
    parser.add_argument("--ged-timeout-seconds", type=float)
    parser.add_argument("--fgw", action="store_true", default=None)
    parser.add_argument("--random-seed", type=int, default=202608)
    parser.add_argument(
        "--force",
        action="store_true",
        help="删除并重建已存在的同名实验结果目录",
    )
    args = parser.parse_args(argv)
    if args.workers is not None and args.workers < 1:
        parser.error("--workers 必须大于等于 1")
    if args.ged_timeout_seconds is not None and args.ged_timeout_seconds <= 0:
        parser.error("--ged-timeout-seconds 必须大于 0")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    """运行可靠性实验；配置错误返回 1，基础设施错误返回 2。"""
    try:
        _configure_logging()
        options = _parse_args(argv)
        config_path = Path(options.experiment_config).resolve()
        config = load_experiment_config(config_path)
        specs = expand_experiment_matrix(config)
        groups = _group_reliability_specs(specs)
        _run_reliability_experiment(config, config_path, groups, options)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except _InfrastructureError as exc:
        LOGGER.error("可靠性实验基础设施失败: %s", _safe_error(exc))
        return 2
    except (OSError, ImportError) as exc:
        LOGGER.error("可靠性实验基础设施失败: %s", _safe_error(exc))
        return 2
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.error("可靠性实验配置失败: %s", _safe_error(exc))
        return 1


def _run_reliability_experiment(
    config: Mapping[str, Any],
    config_path: Path,
    groups: list[_ReliabilitySpecGroup],
    options: argparse.Namespace,
) -> Path:
    """执行全部 benchmark/case，并增量写出 case 与汇总报告。"""
    if not groups:
        raise ValueError("可靠性实验没有可执行的 benchmark")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    # 可靠性结果使用稳定、可人工定位的路径；默认拒绝覆盖同名实验。
    project_root = config_path.parents[2] if len(config_path.parents) > 2 else Path.cwd()
    run_dir = _prepare_run_dir(
        project_root,
        str(config["experiment_id"]),
        options.force,
    )

    started_at = _utc_now()
    ordered_results: list[_CaseResult] = []
    case_files: list[str] = []
    total_cases = sum(len(group.case_ids) for group in groups)
    with tqdm(
        total=total_cases,
        desc="Milestone reliability",
        unit="case",
        dynamic_ncols=True,
    ) as progress:
        for group in groups:
            for case_id in group.case_ids:
                case_path = _case_output_path(run_dir, group.benchmark, case_id)
                response_output_dir = _response_output_dir(
                    run_dir, run_id, group.benchmark, case_id
                )
                result = _run_case(group, case_id, options, response_output_dir)
                result = replace(result, output_path=case_path)
                _write_case_json(
                    case_path,
                    result,
                    str(config["experiment_id"]),
                    run_id,
                )
                ordered_results.append(result)
                case_files.append(case_path.relative_to(run_dir).as_posix())
                progress.update(1)
                progress.set_postfix(
                    status=result.status,
                    refresh=False,
                )

    _write_report(
        run_dir,
        config,
        config_path,
        groups,
        ordered_results,
        case_files,
        options,
        started_at,
        _utc_now(),
    )
    return run_dir


def _prepare_run_dir(
    project_root: Path,
    experiment_id: str,
    force: bool,
) -> Path:
    """校验并准备实验结果目录；仅在显式 force 时删除旧结果。"""
    if project_root is None or not experiment_id.strip():
        raise ValueError("project_root 和 experiment_id 不能为空")
    safe_experiment_id = Path(safe_case_file_name(experiment_id)).stem
    if safe_experiment_id != experiment_id:
        raise ValueError(
            "experiment_id 只能包含字母、数字、点、下划线和连字符，"
            "且不能包含路径分隔符"
        )

    # 删除前确认目标严格位于 results/milestone 的直接子级。
    results_root = (project_root / "results" / "milestone").resolve()
    run_dir = (results_root / safe_experiment_id).resolve()
    if run_dir == results_root or run_dir.parent != results_root:
        raise ValueError(f"实验结果目录越界: {run_dir}")

    try:
        results_root.mkdir(parents=True, exist_ok=True)
        if run_dir.exists():
            if not force:
                raise ValueError(
                    f"实验结果目录已存在，默认拒绝覆盖: {run_dir}；"
                    "请更换 experiment_id，或显式传入 --force"
                )
            if not run_dir.is_dir():
                raise _InfrastructureError(f"实验结果路径不是目录: {run_dir}")
            shutil.rmtree(run_dir)
        run_dir.mkdir()
    except OSError as exc:
        raise _InfrastructureError(f"结果目录不可写: {run_dir}") from exc
    return run_dir


def _configure_logging() -> None:
    """配置低噪声终端日志，运行进度由 tqdm 统一展示。"""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("dynsteer.milestone.compiler").setLevel(logging.CRITICAL)


def _case_output_path(run_dir: Path, benchmark: str, case_id: str) -> Path:
    """生成稳定的 benchmark/case 输出路径，文件名保留完整 case ID。"""
    return run_dir / benchmark / safe_case_file_name(case_id)


def _response_output_dir(
    run_dir: Path,
    run_id: str,
    benchmark: str,
    case_id: str,
) -> Path:
    """返回单个 case 的 milestone LLM 原始响应目录。"""
    benchmark_dir = Path(safe_case_file_name(benchmark)).stem
    case_dir = Path(safe_case_file_name(case_id)).stem
    return run_dir / "llm_outputs" / run_id / benchmark_dir / case_dir


def _group_reliability_specs(
    specs: list[ExperimentRunSpec],
) -> list[_ReliabilitySpecGroup]:
    """按 benchmark/data_root 有序分组并去重 case。"""
    if specs is None:
        raise ValueError("specs 不能为空")
    mutable: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in specs:
        if not spec.case_ids:
            raise ValueError(f"{spec.benchmark}.case_ids 不能为空")
        if not spec.data_root.exists():
            raise ValueError(f"data_root 不存在: {spec.data_root}")
        key = (spec.benchmark, str(spec.data_root.resolve()))
        reliability = _validate_reliability_metadata(
            spec.metadata.get("milestone_reliability", {})
        )
        generation_digest = _digest(spec.milestone_generation)
        reliability_digest = _digest(reliability)
        current = mutable.get(key)
        if current is None:
            mutable[key] = {
                "spec": spec,
                "case_ids": list(dict.fromkeys(spec.case_ids)),
                "generation_digest": generation_digest,
                "reliability_digest": reliability_digest,
                "reliability": reliability,
            }
            continue
        if current["generation_digest"] != generation_digest:
            raise ValueError(f"milestone_generation 配置冲突: {key}")
        if current["reliability_digest"] != reliability_digest:
            raise ValueError(f"milestone_reliability 配置冲突: {key}")
        known = set(current["case_ids"])
        for case_id in spec.case_ids:
            if case_id not in known:
                current["case_ids"].append(case_id)
                known.add(case_id)

    groups: list[_ReliabilitySpecGroup] = []
    for item in mutable.values():
        spec = item["spec"]
        harness_config = build_harness_config(spec)
        harness_config = replace(
            harness_config,
            case_ids=tuple(item["case_ids"]),
            metadata={**harness_config.metadata, "method": "milestone_reliability"},
        )
        source_root = _source_root(spec.data_root)
        groups.append(
            _ReliabilitySpecGroup(
                benchmark=spec.benchmark,
                data_root=spec.data_root,
                case_ids=tuple(item["case_ids"]),
                harness_config=harness_config,
                milestone_generation=spec.milestone_generation,
                reliability_metadata=item["reliability"],
                results_dir=spec.results_dir,
                source_root=source_root,
            )
        )
    return groups


def _run_case(
    group: _ReliabilitySpecGroup,
    case_id: str,
    options: argparse.Namespace,
    response_output_dir: Path,
) -> _CaseResult:
    """不写 adapted cache，分别构造原生 reference 与生成 prediction。"""
    started = time.perf_counter()
    reference_count: int | None = None
    prediction_count: int | None = None
    report: JsonObject = {}
    try:
        adapter = get_adapter(group.benchmark)
        case_config = replace(
            group.harness_config,
            case_ids=(case_id,),
            metadata={**group.harness_config.metadata, "method": "milestone_reliability"},
        )
        original = adapter.adapt_task_case(case_config, case_id)
        if not isinstance(original, TaskCase):
            raise TypeError("adapter.adapt_task_case() 必须返回 TaskCase")
        reference = copy.deepcopy(original.milestone_graph)
        if reference is None:
            raise ValueError("原生 milestone graph 缺失")
        reference_count = len(reference.nodes)
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        return _failed_case(
            group, case_id, "adapt_failed", exc, started, reference_count, None
        )

    try:
        view = adapter.generator_task_view(case_config, copy.deepcopy(original), case_id)
        llm = build_llm_from_config(group.milestone_generation.generator)
        if llm is None:
            llm = build_llm_from_env()
        if llm is None:
            raise ValueError("milestone generator LLM 未配置")
        prediction, generation_report = compile_task_case(
            view,
            group.milestone_generation,
            llm,
            response_output_dir=response_output_dir,
        )
        report = generation_report.to_dict()
        prediction_count = len(prediction.nodes)
    except MilestoneGenerationError as exc:
        return _failed_case(
            group,
            case_id,
            "generation_rejected",
            exc,
            started,
            reference_count,
            None,
            exc.report.to_dict(),
            reference_summary=_descriptor_summary(reference),
        )
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        return _failed_case(
            group,
            case_id,
            "generation_failed",
            exc,
            started,
            reference_count,
            None,
            reference_summary=_descriptor_summary(reference),
        )

    try:
        strict_prediction = _graph_descriptor(prediction, structural=False)
        strict_reference = _graph_descriptor(reference, structural=False)
        structural_prediction = _graph_descriptor(prediction, structural=True)
        structural_reference = _graph_descriptor(reference, structural=True)
        profile = group.reliability_metadata["edit_cost_profile"]
        solver_mode = options.ged_solver or group.reliability_metadata["ged_solver"]
        timeout = (
            options.ged_timeout_seconds
            or group.reliability_metadata["ged_timeout_seconds"]
        )
        metrics = {
            "strict": _graph_metric_bundle(
                strict_prediction, strict_reference, profile, solver_mode, timeout
            ),
            "structural": _graph_metric_bundle(
                structural_prediction,
                structural_reference,
                profile,
                solver_mode,
                timeout,
            ),
            "node_count_delta": len(prediction.nodes) - len(reference.nodes),
            "edge_count_delta": len(prediction.edges) - len(reference.edges),
        }
        fgw_enabled = (
            options.fgw
            if options.fgw is not None
            else group.reliability_metadata["fgw"]
        )
        metrics["fgw"] = (
            _compute_fgw_optional(strict_prediction, strict_reference, alpha=0.5)
            if fgw_enabled
            else _disabled_fgw()
        )
    except (nx.NetworkXException, TimeoutError, RuntimeError, ValueError) as exc:
        return _failed_case(
            group,
            case_id,
            "ged_failed",
            exc,
            started,
            reference_count,
            prediction_count,
            report,
            reference_summary=_descriptor_summary(reference),
            prediction_summary=_descriptor_summary(prediction),
        )

    return _CaseResult(
        benchmark=group.benchmark,
        case_id=case_id,
        status="completed",
        metrics=metrics,
        output_path=Path(),
        generation_report=report,
        reference_count=reference_count,
        prediction_count=prediction_count,
        reference_summary=_descriptor_summary(reference),
        prediction_summary=_descriptor_summary(prediction),
        elapsed_ms=_elapsed_ms(started),
    )


def _graph_descriptor(
    graph: MilestoneGraph | nx.DiGraph, structural: bool = False
) -> nx.DiGraph:
    """将 milestone graph 转为包含稳定节点/边标签的 DiGraph。"""
    if isinstance(graph, (nx.DiGraph, nx.Graph)):
        if not graph.is_directed():
            directed = nx.DiGraph()
            directed.add_nodes_from(graph.nodes(data=True))
            directed.add_edges_from(graph.edges(data=True))
            return directed
        return graph.copy()
    if not isinstance(graph, MilestoneGraph):
        raise TypeError("graph 必须为 MilestoneGraph 或 nx.DiGraph")
    descriptor = nx.DiGraph()
    terminal_ids = {
        node.milestone_id
        for node in graph.nodes
        if not any(source == node.milestone_id for source, _ in graph.edges)
    }
    for node in graph.nodes:
        constraint_shapes = [
            {
                "target": getattr(item.target, "value", item.target),
                "selector": item.selector,
                "operator": getattr(item.operator, "value", item.operator),
                "namespace": item.namespace,
                "evaluator_hint": item.evaluator_hint,
            }
            for item in node.constraints
        ]
        constraint_shapes.sort(key=_canonical_json)
        label = (
            {
                "terminal": node.milestone_id in terminal_ids,
                "constraint_shapes": constraint_shapes,
            }
            if structural
            else {
                "name": node.name,
                "description": node.description,
                "route": [
                    getattr(value, "value", value) for value in node.matching_route
                ]
                if node.matching_route
                else None,
                "terminal": node.milestone_id in terminal_ids,
                "constraints": [
                    {
                        **shape,
                        "expected": item.expected,
                        "stage_goal_semantics": item.stage_goal_semantics,
                    }
                    for shape, item in zip(constraint_shapes, sorted(
                        node.constraints,
                        key=lambda value: _canonical_json({
                            "target": getattr(value.target, "value", value.target),
                            "selector": value.selector,
                            "operator": getattr(value.operator, "value", value.operator),
                            "namespace": value.namespace,
                            "evaluator_hint": value.evaluator_hint,
                        }),
                    ))
                ],
            }
        )
        descriptor.add_node(node.milestone_id, label=_canonical_json(label))
    for source, target in graph.edges:
        descriptor.add_edge(source, target, label="directed_precedence")
    return descriptor


def _descriptor_summary(graph: MilestoneGraph) -> JsonObject:
    """返回不含任务文本和期望值的图结构摘要。"""
    terminal_ids = {
        node.milestone_id
        for node in graph.nodes
        if not any(source == node.milestone_id for source, _ in graph.edges)
    }
    shape_counts: Counter[str] = Counter()
    for node in graph.nodes:
        for constraint in node.constraints:
            target = getattr(constraint.target, "value", constraint.target)
            operator = getattr(constraint.operator, "value", constraint.operator)
            shape_counts[f"{target}:{operator}"] += 1
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "terminal_count": len(terminal_ids),
        "constraint_shape_counts": dict(sorted(shape_counts.items())),
    }


def _graph_metric_bundle(
    predicted_graph: nx.DiGraph,
    reference_graph: nx.DiGraph,
    profile: Mapping[str, Any],
    solver_mode: str,
    timeout_seconds: float,
) -> JsonObject:
    """一次 GED 求解同时产出 GED 审计字段与节点集合 F1。"""
    result = _compute_ged(
        predicted_graph,
        reference_graph,
        profile,
        solver_mode,
        timeout_seconds,
    )
    vertex_path = result.pop("vertex_path")
    result["node_set_f1"] = _node_set_f1(
        predicted_graph, reference_graph, vertex_path
    )
    return result


def _compute_ged(
    predicted_graph: MilestoneGraph | nx.DiGraph,
    reference_graph: MilestoneGraph | nx.DiGraph,
    edit_cost_profile: Mapping[str, object],
    solver_mode: str = "exact",
    timeout_seconds: float = 60,
) -> JsonObject:
    """计算 Graph Edit Distance 及可审计编辑路径。

    $$ GED(G_p,G_r)=min_{P in Paths(G_p,G_r)} sum_{o in P} c(o) $$
    来源：Bunke & Shearer, Pattern Recognition Letters, 1998，
    https://doi.org/10.1016/S0167-8655(97)00164-7
    """
    profile = _validated_profile(edit_cost_profile)
    if solver_mode not in {"exact", "approximate"}:
        raise ValueError("solver_mode 必须为 exact 或 approximate")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    predicted = _graph_descriptor(predicted_graph)
    reference = _graph_descriptor(reference_graph)

    def node_substitution(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
        return 0.0 if left.get("label") == right.get("label") else 1.0

    def edge_substitution(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
        return 0.0 if left.get("label") == right.get("label") else 1.0

    kwargs: dict[str, Any] = {
        "node_subst_cost": node_substitution,
        "node_del_cost": lambda _: profile["node_delete"],
        "node_ins_cost": lambda _: profile["node_insert"],
        "edge_subst_cost": edge_substitution,
        "edge_del_cost": lambda _: profile["edge_delete"],
        "edge_ins_cost": lambda _: profile["edge_insert"],
    }
    if solver_mode == "approximate":
        kwargs["timeout"] = timeout_seconds

    best: tuple[list[tuple[Any, Any]], list[tuple[Any, Any]], float] | None = None
    try:
        for vertex_path, edge_path, cost in nx.optimize_edit_paths(
            predicted, reference, **kwargs
        ):
            candidate = (vertex_path, edge_path, float(cost))
            if best is None or candidate[2] < best[2]:
                best = candidate
    except ModuleNotFoundError as exc:
        if exc.name != "numpy":
            raise
        best = _fallback_ged(predicted, reference, profile)
    if best is None:
        raise RuntimeError("GED 求解器未返回可行编辑路径")

    vertex_path, edge_path, distance = best
    base = (
        predicted.number_of_nodes() * profile["node_delete"]
        + reference.number_of_nodes() * profile["node_insert"]
        + predicted.number_of_edges() * profile["edge_delete"]
        + reference.number_of_edges() * profile["edge_insert"]
    )
    similarity = 1.0 if base == 0 and distance == 0 else (
        max(0.0, 1.0 - distance / base) if base > 0 else 0.0
    )
    operations = _edit_operations(
        predicted,
        reference,
        vertex_path,
        edge_path,
        node_substitution,
        edge_substitution,
    )
    return {
        "ged_distance": distance,
        "ged_base": base,
        "ged_similarity": similarity,
        "solver_mode": solver_mode,
        "optimal": solver_mode == "exact",
        "edit_path": operations["edit_path"],
        "vertex_path": vertex_path,
        "node_insertions": operations["node_insertions"],
        "node_deletions": operations["node_deletions"],
        "node_substitutions": operations["node_substitutions"],
        "edge_insertions": operations["edge_insertions"],
        "edge_deletions": operations["edge_deletions"],
        "edge_substitutions": operations["edge_substitutions"],
        "edit_cost_profile": dict(profile),
    }


def _fallback_ged(
    predicted: nx.DiGraph, reference: nx.DiGraph, profile: Mapping[str, float]
) -> tuple[list[tuple[Any, Any]], list[tuple[Any, Any]], float]:
    """在 NetworkX 的 numpy 可选依赖缺失时，为小图提供确定性回退。"""
    left = list(predicted)
    right = list(reference)
    best: tuple[list[tuple[Any, Any]], list[tuple[Any, Any]], float] | None = None
    matched_count = min(len(left), len(right))
    for selected in itertools.combinations(right, matched_count):
        for permutation in itertools.permutations(selected):
            mapping = list(zip(left[:matched_count], permutation))
            vertex_path = mapping + [(node, None) for node in left[matched_count:]]
            vertex_path += [(None, node) for node in right if node not in permutation]
            cost = sum(
                0.0
                if predicted.nodes[source].get("label") == reference.nodes[target].get("label")
                else 1.0
                for source, target in mapping
            )
            cost += (len(left) - matched_count) * profile["node_delete"]
            cost += (len(right) - matched_count) * profile["node_insert"]
            matched_edges = set()
            edge_path: list[tuple[Any, Any]] = []
            for source, target in mapping:
                if source is None or target is None:
                    continue
            for source, target, data in predicted.edges(data=True):
                target_source = next((r for l, r in mapping if l == source), None)
                target_target = next((r for l, r in mapping if l == target), None)
                if target_source is not None and target_target is not None and reference.has_edge(target_source, target_target):
                    matched_edges.add((source, target, target_source, target_target))
                    edge_path.append(((source, target), (target_source, target_target)))
                    if data.get("label") != reference.edges[target_source, target_target].get("label"):
                        cost += 1.0
                else:
                    cost += profile["edge_delete"]
                    edge_path.append(((source, target), None))
            cost += (reference.number_of_edges() - len(matched_edges)) * profile["edge_insert"]
            matched_reference = {(rs, rt) for _, _, rs, rt in matched_edges}
            edge_path.extend((None, edge) for edge in reference.edges if edge not in matched_reference)
            candidate = (vertex_path, edge_path, cost)
            if best is None or cost < best[2]:
                best = candidate
    if best is None:
        raise RuntimeError("GED 无可行编辑路径")
    return best


def _node_set_f1(
    predicted_graph: nx.DiGraph,
    reference_graph: nx.DiGraph,
    vertex_path: list[tuple[Any, Any]] | None = None,
) -> JsonObject:
    """按 GED 零代价节点替换计算节点集合 F1 基线。"""
    if vertex_path is None:
        result = _compute_ged(
            predicted_graph,
            reference_graph,
            DEFAULT_EDIT_COST_PROFILE,
        )
        vertex_path = result["vertex_path"]
    tp = sum(
        left is not None
        and right is not None
        and predicted_graph.nodes[left].get("label")
        == reference_graph.nodes[right].get("label")
        for left, right in vertex_path
    )
    fp = predicted_graph.number_of_nodes() - tp
    fn = reference_graph.number_of_nodes() - tp
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def _compute_fgw_optional(
    predicted_graph: nx.DiGraph,
    reference_graph: nx.DiGraph,
    alpha: float,
) -> JsonObject:
    """按无向最短路投影计算可选 FGW；依赖不可用时明确返回 unavailable。"""
    if importlib.util.find_spec("ot") is None:
        return {
            "status": "unavailable",
            "distance": None,
            "alpha": alpha,
            "relation_projection": "undirected_shortest_path",
        }
    if predicted_graph.number_of_nodes() == 0 or reference_graph.number_of_nodes() == 0:
        return {
            "status": "unavailable",
            "distance": None,
            "alpha": alpha,
            "relation_projection": "undirected_shortest_path",
        }
    try:
        import numpy as np

        ot = importlib.import_module("ot")
        left_nodes = list(predicted_graph)
        right_nodes = list(reference_graph)
        features = np.array(
            [
                [
                    0.0
                    if predicted_graph.nodes[left].get("label")
                    == reference_graph.nodes[right].get("label")
                    else 1.0
                    for right in right_nodes
                ]
                for left in left_nodes
            ]
        )
        left_relations = _relation_matrix(predicted_graph, left_nodes, np)
        right_relations = _relation_matrix(reference_graph, right_nodes, np)
        left_weights = np.full(len(left_nodes), 1.0 / len(left_nodes))
        right_weights = np.full(len(right_nodes), 1.0 / len(right_nodes))
        distance = ot.gromov.fused_gromov_wasserstein2(
            features,
            left_relations,
            right_relations,
            left_weights,
            right_weights,
            alpha=alpha,
        )
        return {
            "status": "completed",
            "distance": float(distance),
            "alpha": alpha,
            "relation_projection": "undirected_shortest_path",
        }
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        return {
            "status": "unavailable",
            "distance": None,
            "alpha": alpha,
            "relation_projection": "undirected_shortest_path",
            "diagnostic": _safe_error(exc),
        }


def _write_report(
    run_dir: Path,
    config: Mapping[str, Any],
    config_path: Path,
    groups: list[_ReliabilitySpecGroup],
    results: list[_CaseResult],
    case_files: list[str],
    options: argparse.Namespace,
    started_at: str,
    finished_at: str,
) -> None:
    """写出 index.json 与 summary.json。"""
    statuses = (
        "completed",
        "generation_rejected",
        "generation_failed",
        "adapt_failed",
        "ged_failed",
    )
    status_counts = {
        status: sum(result.status == status for result in results)
        for status in statuses
    }
    completed = [result for result in results if result.status == "completed"]
    strict_similarities = [
        float(result.metrics["strict"]["ged_similarity"]) for result in completed
    ]
    structural_similarities = [
        float(result.metrics["structural"]["ged_similarity"])
        for result in completed
    ]
    strict_f1 = [
        float(result.metrics["strict"]["node_set_f1"]["f1"])
        for result in completed
    ]
    structural_f1 = [
        float(result.metrics["structural"]["node_set_f1"]["f1"])
        for result in completed
    ]
    generation_fields = (
        "requested_path_count",
        "minimum_valid_path_count",
        "valid_path_count",
        "distinct_path_count",
    )
    summary = {
        "schema_version": "milestone_reliability.summary.v1",
        "status_counts": status_counts,
        "failed_case_count": len(results) - status_counts["completed"],
        "strict": {
            "ged_similarity": _statistics(strict_similarities),
            "node_set_f1": _statistics(strict_f1),
        },
        "structural": {
            "ged_similarity": _statistics(structural_similarities),
            "node_set_f1": _statistics(structural_f1),
        },
        "absolute_node_count_delta": _statistics(
            [abs(float(result.metrics["node_count_delta"])) for result in completed]
        ),
        "absolute_edge_count_delta": _statistics(
            [abs(float(result.metrics["edge_count_delta"])) for result in completed]
        ),
        "generation": {
            field: _statistics(
                [
                    float(result.generation_report[field])
                    for result in results
                    if isinstance(result.generation_report.get(field), (int, float))
                    and not isinstance(result.generation_report.get(field), bool)
                ]
            )
            for field in generation_fields
        },
        "exact_count": sum(
            result.metrics["strict"].get("solver_mode") == "exact"
            for result in completed
        ),
        "approximate_count": sum(
            result.metrics["strict"].get("solver_mode") == "approximate"
            for result in completed
        ),
        "case_files": case_files,
        "metric_definition": {
            "primary": "strict.ged_similarity",
            "strict.ged_similarity": "max(0, 1 - ged_distance / ged_base)",
            "structural.ged_similarity": "同公式，仅比较 terminal 与 constraint shape",
            "strict.node_set_f1.f1": "strict GED 的零代价节点替换",
            "structural.node_set_f1.f1": "structural GED 的零代价节点替换",
            "node_count_delta": "prediction node count - reference node count",
            "edge_count_delta": "prediction edge count - reference edge count",
        },
    }
    index = {
        "experiment_id": config["experiment_id"],
        "experiment_config": str(config_path),
        "random_seed": options.random_seed,
        "started_at": started_at,
        "finished_at": finished_at,
        "groups": [
            {
                "benchmark": group.benchmark,
                "data_root": str(group.data_root.resolve()),
                "source_root": str(group.source_root) if group.source_root else None,
                "case_count": len(group.case_ids),
                "generator": _generator_summary(group.milestone_generation.generator),
                "milestone_reliability": group.reliability_metadata,
            }
            for group in groups
        ],
        "case_files": case_files,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "index.json", index)


def _write_case_json(
    path: Path,
    result: _CaseResult,
    experiment_id: str,
    run_id: str,
) -> None:
    """写出单个 case 的稳定 v1 schema。"""
    finished_at = _utc_now()
    payload = {
        "schema_version": "milestone_reliability.case.v1",
        "experiment_id": experiment_id,
        "run_id": run_id,
        "benchmark": result.benchmark,
        "case_id": result.case_id,
        "status": result.status,
        "finished_at": finished_at,
        "elapsed_ms": result.elapsed_ms,
        "reference": result.reference_summary,
        "prediction": result.prediction_summary,
        "metrics": result.metrics,
        "generation_report": result.generation_report,
        "diagnostics": list(result.diagnostics),
    }
    _write_json(path, payload)


def _validate_reliability_metadata(metadata: object) -> JsonObject:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata.milestone_reliability 必须为 JSON 对象")
    result = dict(metadata)
    result.setdefault("ged_solver", "exact")
    result.setdefault("ged_timeout_seconds", 60)
    result.setdefault("edit_cost_profile", dict(DEFAULT_EDIT_COST_PROFILE))
    result.setdefault("fgw", False)
    if result["ged_solver"] not in {"exact", "approximate"}:
        raise ValueError("metadata.milestone_reliability.ged_solver 非法")
    timeout = result["ged_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(
            "metadata.milestone_reliability.ged_timeout_seconds 必须为正数"
        )
    result["edit_cost_profile"] = _validated_profile(
        result["edit_cost_profile"]
    )
    if not isinstance(result["fgw"], bool):
        raise ValueError("metadata.milestone_reliability.fgw 必须为 bool")
    return result


def _validated_profile(profile: object) -> JsonObject:
    if not isinstance(profile, Mapping):
        raise ValueError("edit_cost_profile 必须为 JSON 对象")
    result = dict(profile)
    required = (
        "profile_id",
        "node_insert",
        "node_delete",
        "edge_insert",
        "edge_delete",
        "node_label_distance",
        "edge_label_distance",
    )
    missing = [key for key in required if key not in result]
    if missing:
        raise ValueError(f"edit_cost_profile 缺少字段: {missing}")
    if not str(result["profile_id"]).strip():
        raise ValueError("edit_cost_profile.profile_id 不能为空")
    for key in ("node_insert", "node_delete", "edge_insert", "edge_delete"):
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"edit_cost_profile.{key} 必须为有限非负数")
        result[key] = float(value)
    if result["node_label_distance"] != "canonical_json":
        raise ValueError("edit_cost_profile.node_label_distance 必须为 canonical_json")
    if result["edge_label_distance"] != "exact":
        raise ValueError("edit_cost_profile.edge_label_distance 必须为 exact")
    return result


def _edit_operations(
    predicted: nx.DiGraph,
    reference: nx.DiGraph,
    vertex_path: list[tuple[Any, Any]],
    edge_path: list[tuple[Any, Any]],
    node_substitution: Any,
    edge_substitution: Any,
) -> JsonObject:
    counts = {
        "node_insertions": 0,
        "node_deletions": 0,
        "node_substitutions": 0,
        "edge_insertions": 0,
        "edge_deletions": 0,
        "edge_substitutions": 0,
    }
    edit_path: list[JsonObject] = []
    for left, right in vertex_path:
        if left is None:
            counts["node_insertions"] += 1
            edit_path.append({"operation": "node_insert", "target": right})
        elif right is None:
            counts["node_deletions"] += 1
            edit_path.append({"operation": "node_delete", "source": left})
        elif node_substitution(predicted.nodes[left], reference.nodes[right]) > 0:
            counts["node_substitutions"] += 1
            edit_path.append(
                {"operation": "node_substitute", "source": left, "target": right}
            )
    for left, right in edge_path:
        if left is None:
            counts["edge_insertions"] += 1
            edit_path.append({"operation": "edge_insert", "target": list(right)})
        elif right is None:
            counts["edge_deletions"] += 1
            edit_path.append({"operation": "edge_delete", "source": list(left)})
        elif edge_substitution(predicted.edges[left], reference.edges[right]) > 0:
            counts["edge_substitutions"] += 1
            edit_path.append(
                {
                    "operation": "edge_substitute",
                    "source": list(left),
                    "target": list(right),
                }
            )
    return {**counts, "edit_path": edit_path}


def _failed_case(
    group: _ReliabilitySpecGroup,
    case_id: str,
    status: str,
    exc: Exception,
    started: float,
    reference_count: int | None,
    prediction_count: int | None,
    report: JsonObject | None = None,
    reference_summary: JsonObject | None = None,
    prediction_summary: JsonObject | None = None,
) -> _CaseResult:
    return _CaseResult(
        benchmark=group.benchmark,
        case_id=case_id,
        status=status,
        metrics={},
        output_path=Path(),
        generation_report=report or {},
        reference_count=reference_count,
        prediction_count=prediction_count,
        reference_summary=reference_summary or {},
        prediction_summary=prediction_summary or {},
        diagnostics=(_safe_error(exc),),
        elapsed_ms=_elapsed_ms(started),
    )


def _relation_matrix(graph: nx.DiGraph, nodes: list[Any], np: Any) -> Any:
    undirected = graph.to_undirected()
    maximum = max(len(nodes), 1)
    matrix = np.full((len(nodes), len(nodes)), float(maximum))
    for index, source in enumerate(nodes):
        matrix[index, index] = 0.0
        lengths = nx.single_source_shortest_path_length(undirected, source)
        for target_index, target in enumerate(nodes):
            if target in lengths:
                matrix[index, target_index] = float(lengths[target])
    return matrix


def _disabled_fgw() -> JsonObject:
    return {
        "status": "disabled",
        "distance": None,
        "alpha": None,
        "relation_projection": None,
    }


def _source_root(data_root: Path) -> Path | None:
    manifest = data_root / "benchmark.json"
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    value = data.get("source_root")
    if not value:
        return None
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (data_root / path).resolve()


def _generator_summary(config: Mapping[str, Any]) -> JsonObject:
    sensitive = {"api_key", "authorization", "password", "token"}
    return {
        str(key): value
        for key, value in config.items()
        if str(key).lower() not in sensitive
    }


def _statistics(values: list[float]) -> JsonObject:
    return {
        "count": len(values),
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).splitlines())[:300]
    lowered = message.lower()
    for key in ("api_key", "authorization", "password", "token"):
        if key in lowered:
            message = f"包含敏感字段 {key} 的错误消息已隐藏"
            break
    return f"{type(exc).__name__}: {message}"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        raise _InfrastructureError(f"JSON 写入失败: {path}") from exc


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
