from __future__ import annotations

import argparse
import copy
import datetime as dt
import itertools
import json
import re
import logging
import math
import shutil
import statistics
import time
import traceback
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

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
from dynsteer.llm import build_llm_from_config
from dynsteer.llm.base import BaseLLM
from dynsteer.milestone import compile_task_case
from dynsteer.milestone.model import (
    MilestoneGenerationConfig,
)
from dynsteer.milestone.semantics import (
    _canonical_state_goal,
    _tool_name_from_node,
    canonical_graph_semantics,
    compare_input_coverage,
)
from dynsteer.metrics import (
    activate_runtime_metrics_recorder,
    reset_runtime_metrics_recorder,
    summarize_llm_calls,
)
from dynsteer.model import JsonObject, MilestoneGraph, RuntimeMetricsRecorder, TaskCase
from dynsteer.utils import canonical_json, stable_json_digest


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
_MODEL_CLIENT_OVERRIDE_FIELDS = (
    "api_key",
    "api_key_env",
    "base_url",
    "base_url_env",
    "timeout_seconds",
    "max_retries",
    "retry_base_seconds",
    "retry_max_seconds",
)
_FEW_SHOT_CONTAMINATED_PREFIXES = (
    "search_relationship_with_phone_number",
    "remove_reminder_with_recency_latest",
)


class _InfrastructureError(RuntimeError):
    """occurs when a source, dependency, or result directory is unavailable."""


@dataclass(frozen=True)
class _ReliabilitySpecGroup:
    benchmark: str
    repeat_index: int
    data_root: Path
    case_ids: tuple[str, ...]
    harness_config: HarnessRunConfig
    milestone_generation: MilestoneGenerationConfig
    reliability_metadata: JsonObject
    results_dir: Path
    source_root: Path | None = None
    llm: BaseLLM | None = None


@dataclass(frozen=True)
class _CaseResult:
    benchmark: str
    case_id: str
    status: str
    metrics: JsonObject
    output_path: Path
    generation_report: JsonObject
    generation_usage: JsonObject
    reference_count: int | None
    prediction_count: int | None
    reference_summary: JsonObject
    prediction_summary: JsonObject
    diagnostics: tuple[str, ...] = ()
    elapsed_ms: int = 0


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parsing the milestone reliability experimental parameters."""
    parser = argparse.ArgumentParser(
        description='Assess the reliability of automatic milestone-generated maps vis-à-vis original maps',
        epilog=(
            'Example: python milestone_reliability.py --exp '
            "data/experiments/toolsandbox_milestone_reliability_main.example.json"
        ),
    )
    parser.add_argument(
        "--exp", "--experiment-config", dest="experiment_config", required=True
    )
    parser.add_argument("--workers", type=int)
    parser.add_argument("--ged-solver", choices=("exact", "approximate"))
    parser.add_argument("--ged-timeout-seconds", type=float)
    parser.add_argument(
        "--fgw",
        action="store_true",
        default=None,
        help='Abandoned (Deprecated): Recommended no more FGW graphic distance',
    )
    parser.add_argument("--random-seed", type=int, default=202608)
    parser.add_argument(
        "--force",
        action="store_true",
        help='Delete and recreate an existing directory of experimental results of the same name',
    )
    args = parser.parse_args(argv)
    if args.workers is not None and args.workers < 1:
        parser.error('--workers must be greater than 1')
    if args.ged_timeout_seconds is not None and args.ged_timeout_seconds <= 0:
        parser.error('--ged-timeout-seconds must be greater than 0')
    return args


def main(argv: Optional[list[str]] = None) -> int:
    """Run reliability experiments; configuration error returned 1 and infrastructure error returned 2."""
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
        LOGGER.error('Reliability experimental infrastructure failure:%s', _safe_error(exc))
        return 2
    except (OSError, ImportError) as exc:
        LOGGER.error('Reliability experimental infrastructure failure:%s', _safe_error(exc))
        return 2
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.error('Reliability experimental configuration failed:%s', _safe_error(exc))
        return 1


def _run_reliability_experiment(
    config: Mapping[str, Any],
    config_path: Path,
    groups: list[_ReliabilitySpecGroup],
    options: argparse.Namespace,
) -> Path:
    """Implements all benchmark/cases and adds case and summary reports."""
    if not groups:
        raise ValueError('Reliability experiment not available for benchmark')
    groups = [
        replace(group, llm=_build_milestone_llm(group, options))
        for group in groups
    ]
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    # Reliability results use stable, manually identifiable paths; default refusal to cover the same name experiment.

    project_root = config_path.parents[2] if len(config_path.parents) > 2 else Path.cwd()
    run_dir = _prepare_run_dir(
        project_root,
        str(config["experiment_id"]),
        options.force,
    )

    started_at = _utc_now()
    jobs = [
        (group, case_id)
        for group in groups
        for case_id in group.case_ids
    ]
    completed: dict[int, tuple[_CaseResult, str]] = {}
    worker_count = options.workers or 1
    with tqdm(
        total=len(jobs),
        desc=f"Milestone reliability ({worker_count} workers)",
        unit="case",
        dynamic_ncols=True,
    ) as progress:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="milestone-reliability",
        ) as executor:
            futures: dict[Future[_CaseResult], tuple[int, Path]] = {}
            for index, (group, case_id) in enumerate(jobs):
                case_path = _case_output_path(
                    run_dir, group.benchmark, case_id, group.repeat_index
                )
                response_output_file = _response_output_file(
                    run_dir, group.benchmark, case_id, group.repeat_index
                )
                future = executor.submit(
                    _run_case, group, case_id, options, response_output_file
                )
                futures[future] = (index, case_path)
            for future in as_completed(futures):
                index, case_path = futures[future]
                result = replace(future.result(), output_path=case_path)
                _write_case_json(
                    case_path, result, str(config["experiment_id"]), run_id
                )
                completed[index] = (
                    result,
                    case_path.relative_to(run_dir).as_posix(),
                )
                progress.update(1)
                progress.set_postfix(
                    status=result.status,
                    refresh=False,
                )

    ordered_results = [completed[index][0] for index in range(len(jobs))]
    case_files = [completed[index][1] for index in range(len(jobs))]

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
    """Verify and prepare a directory of test results; delete old results only in visible form."""
    if project_root is None or not experiment_id.strip():
        raise ValueError('Project_root and execution_id cannot be empty')
    safe_experiment_id = Path(safe_case_file_name(experiment_id)).stem
    if safe_experiment_id != experiment_id:
        raise ValueError(
            'experiment_id may contain only letters, digits, dots, underscores, and hyphens, '
            'and must not contain a path separator'
        )

    # The pre-delete confirmed target is strictly at the direct sub-level of the results/milestone.
    results_root = (project_root / "results" / "milestone").resolve()
    run_dir = (results_root / safe_experiment_id).resolve()
    if run_dir == results_root or run_dir.parent != results_root:
        raise ValueError(f"Transfrontier list of test results:{run_dir}")

    try:
        results_root.mkdir(parents=True, exist_ok=True)
        if run_dir.exists():
            if not force:
                raise ValueError(
                    f"The directory of experimental results already exists, default refusal to overwrite:{run_dir} ; "
                    'Use a different experiment_id, or explicitly pass --force'
                )
            if not run_dir.is_dir():
                raise _InfrastructureError(f"The experimental result path is not a directory:{run_dir}")
            shutil.rmtree(run_dir)
        run_dir.mkdir()
    except OSError as exc:
        raise _InfrastructureError(f"The results list cannot be written:{run_dir}") from exc
    return run_dir


def _configure_logging() -> None:
    """Configure low-noise terminal logging and display progress uniformly through tqdm."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("dynsteer.milestone.compiler").setLevel(logging.CRITICAL)


def _case_output_path(
    run_dir: Path, benchmark: str, case_id: str, repeat_index: int = 0
) -> Path:
    """Generates a stable benchmark/case output path, and the filename retains the full case ID."""
    return run_dir / benchmark / f"repeat_{repeat_index:02d}" / safe_case_file_name(case_id)


def _response_output_file(
    run_dir: Path,
    benchmark: str,
    case_id: str,
    repeat_index: int = 0,
) -> Path:
    """returns a single case LLM response to the JSON file under the benchmark directory."""
    benchmark_dir = Path(safe_case_file_name(benchmark)).stem
    return run_dir / benchmark_dir / f"repeat_{repeat_index:02d}" / "llm_outputs" / safe_case_file_name(case_id)


def _group_reliability_specs(
    specs: list[ExperimentRunSpec],
) -> list[_ReliabilitySpecGroup]:
    """Press benchmark/data_root to group in an orderly manner and to weigh case."""
    if specs is None:
        raise ValueError('Specs cannot be empty.')
    mutable: dict[tuple[str, str, int], dict[str, Any]] = {}
    for spec in specs:
        if not spec.case_ids:
            raise ValueError(f"{spec.benchmark}.case_ids cannot be empty.")
        if not spec.data_root.exists():
            raise ValueError(f"Data_root does not exist:{spec.data_root}")
        key = (spec.benchmark, str(spec.data_root.resolve()), spec.repeat_index)
        reliability = _validate_reliability_metadata(
            spec.metadata.get("milestone_reliability", {})
        )
        generation_digest = stable_json_digest(spec.milestone_generation)
        reliability_digest = stable_json_digest(reliability)
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
            raise ValueError(f"milestone_generation configuration conflict: {key}")
        if current["reliability_digest"] != reliability_digest:
            raise ValueError(f"milestone_reliability configuration conflict:{key}")
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
                repeat_index=spec.repeat_index,
                data_root=spec.data_root,
                case_ids=tuple(item["case_ids"]),
                harness_config=harness_config,
                milestone_generation=replace(
                    spec.milestone_generation,
                    generator=_effective_generator_config(harness_config),
                ),
                reliability_metadata=item["reliability"],
                results_dir=spec.results_dir,
                source_root=source_root,
            )
        )
    return groups


def _effective_generator_config(harness_config: HarnessRunConfig) -> JsonObject:
    """Overwrite the benchmark default generator configuration with an experimental model client."""
    generator = dict(harness_config.milestone_generation.generator)
    client = harness_config.metadata.get("agent_client")
    if not isinstance(client, dict):
        client = harness_config.metadata.get("client")
    if not isinstance(client, dict):
        return generator
    for field in _MODEL_CLIENT_OVERRIDE_FIELDS:
        value = client.get(field)
        if value is not None:
            generator[field] = value
    return generator


def _build_milestone_llm(group: _ReliabilitySpecGroup, options: argparse.Namespace) -> BaseLLM:
    """Builds and pre-screens each group of milestone generator LLM."""
    llm = build_llm_from_config({
        **group.milestone_generation.generator,
        "seed": options.random_seed + group.repeat_index,
    })
    if llm is None:
        raise ValueError('Milestone generator LLM not configured')
    return llm


def _run_case(
    group: _ReliabilitySpecGroup,
    case_id: str,
    options: argparse.Namespace,
    response_output_file: Path,
) -> _CaseResult:
    """Construct the original reference without using the captured cache."""
    started = time.perf_counter()
    reference_count: int | None = None
    prediction_count: int | None = None
    report: JsonObject = {}
    generation_usage = summarize_llm_calls([])
    try:
        adapter = get_adapter(group.benchmark)
        case_config = replace(
            group.harness_config,
            case_ids=(case_id,),
            metadata={**group.harness_config.metadata, "method": "milestone_reliability"},
        )
        original = adapter.adapt_task_case(case_config, case_id)
        if not isinstance(original, TaskCase):
            raise TypeError('Aadapt_task_case() must return to TaskCase')
        reference = adapter.reference_milestone_graph(case_config, case_id)
        if reference is None:
            raise ValueError('Original milestone variance missing')
        reference = copy.deepcopy(reference)
        reference_count = len(reference.nodes)
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        return _failed_case(
            group, case_id, "adapt_failed", exc, started, reference_count, None
        )

    try:
        view = adapter.generator_task_view(case_config, copy.deepcopy(original), case_id)
        llm = group.llm
        if llm is None:
            raise ValueError('Milestone generator LLM not configured')
        recorder = RuntimeMetricsRecorder()
        token = activate_runtime_metrics_recorder(recorder)
        try:
            prediction, generation_report = compile_task_case(
                view,
                group.milestone_generation,
                llm,
                response_output_file=response_output_file,
            )
        finally:
            reset_runtime_metrics_recorder(token)
            generation_usage = summarize_llm_calls(recorder.llm_calls)
        report = generation_report.to_dict()
        prediction_count = len(prediction.nodes)
        if generation_report.generation_status == "generation_failed":
            raise RuntimeError('all candidate batch calls or top-level parsing failed')
    except (TypeError, ValueError, KeyError, RuntimeError) as exc:
        report = {
            **report,
            "exception_stage": "compile_task_case",
            "exception_traceback": traceback.format_exc(),
        }
        return _failed_case(
            group,
            case_id,
            "generation_failed",
            exc,
            started,
            reference_count,
            None,
            report=report,
            reference_summary=_descriptor_summary(reference),
            generation_usage=generation_usage,
        )

    try:
        tool_aliases = adapter.reliability_tool_aliases(case_config, case_id)
        reference_semantics = canonical_graph_semantics(
            reference, tool_aliases, view
        )
        prediction_semantics = canonical_graph_semantics(
            prediction, view=view
        )
        coverage = compare_input_coverage(reference, view, tool_aliases)
        semantic_metrics = _semantic_metric_bundle(
            reference_semantics, prediction_semantics, coverage
        )
        semantic_prediction = _graph_descriptor(
            prediction, mode="semantic", tool_aliases=tool_aliases, view=view
        )
        semantic_reference = _graph_descriptor(
            reference, mode="semantic", tool_aliases=tool_aliases, view=view
        )
        strict_prediction = _graph_descriptor(prediction, mode="strict")
        strict_reference = _graph_descriptor(reference, mode="strict")
        structural_prediction = _graph_descriptor(prediction, mode="structural")
        structural_reference = _graph_descriptor(reference, mode="structural")
        topology_prediction = _graph_descriptor(prediction, mode="topology")
        topology_reference = _graph_descriptor(reference, mode="topology")
        profile = group.reliability_metadata["edit_cost_profile"]
        solver_mode = options.ged_solver or group.reliability_metadata["ged_solver"]
        timeout = (
            options.ged_timeout_seconds
            or group.reliability_metadata["ged_timeout_seconds"]
        )
        diagnostics = {
            "semantic": _graph_metric_bundle(
                semantic_prediction,
                semantic_reference,
                profile,
                solver_mode,
                timeout,
            ),
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
            "topology": _graph_metric_bundle(
                topology_prediction, topology_reference, profile, solver_mode, timeout
            ),
            "node_count_delta": len(prediction.nodes) - len(reference.nodes),
            "edge_count_delta": len(prediction.edges) - len(reference.edges),
        }
        diagnostics["fgw"] = _disabled_fgw()
        metrics = {
            "completed": True,
            "graph_returned": True,
            "graph_empty": _semantic_graph_empty(prediction_semantics),
            "reference_graph_empty": _semantic_graph_empty(reference_semantics),
            "generated_operation_count": len(prediction_semantics["operations"]),
            "reference_operation_count": len(reference_semantics["operations"]),
            "generation_status": report.get("generation_status"),
            "few_shot_contaminated": _few_shot_contaminated(case_id),
            "semantic_metrics": semantic_metrics,
            "input_coverage": coverage,
            "diagnostics": diagnostics,
        }
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
            generation_usage=generation_usage,
        )

    return _CaseResult(
        benchmark=group.benchmark,
        case_id=case_id,
        status="completed",
        metrics=metrics,
        output_path=Path(),
        generation_report=report,
        generation_usage=generation_usage,
        reference_count=reference_count,
        prediction_count=prediction_count,
        reference_summary=_descriptor_summary(reference),
        prediction_summary=_descriptor_summary(prediction),
        elapsed_ms=_elapsed_ms(started),
    )


def _semantic_metric_bundle(
    reference: JsonObject, generated: JsonObject, coverage: JsonObject
) -> JsonObject:
    """Calculates the Goal, Operations, Topology, Preserve and Fatal Minefield indicators."""
    dimensions = ("dispositions", "goals", "operations", "minefields", "topology", "preserves")
    metrics = {
        dimension: _multiset_metric(
            Counter(str(item) for item in reference.get(dimension, [])),
            Counter(str(item) for item in generated.get(dimension, [])),
        )
        for dimension in dimensions
    }
    reference_fatal = {
        item for item in reference.get("minefields", []) if '"severity":"fatal"' in str(item)
    }
    generated_minefields = {str(item) for item in generated.get("minefields", [])}
    fatal_miss_count = len(reference_fatal - generated_minefields)
    spurious_fatal_count = len(generated_minefields - reference_fatal)
    operation_complete = all(
        bool(metrics[dimension]["exact"])
        for dimension in ("operations", "minefields", "topology")
    )
    goal_complete = all(
        bool(metrics[dimension]["exact"])
        for dimension in ("goals", "operations", "minefields", "topology")
    )
    positive_fatal_recall = (
        metrics["minefields"]["recall"] if reference_fatal else None
    )
    fatal_tools = sorted({_fatal_tool_name(item) for item in reference_fatal} - {None})
    per_tool_recall = {
        tool: float(any(_fatal_tool_name(item) == tool for item in generated_minefields))
        for tool in fatal_tools
    }
    return {
        **metrics,
        "fatal_minefield_miss_count": fatal_miss_count,
        "spurious_fatal_minefield_count": spurious_fatal_count,
        "fatal_positive_recall": positive_fatal_recall,
        "fatal_per_tool_recall": per_tool_recall,
        "operation_topology_exact": operation_complete,
        "tool_multiset_exact": bool(metrics["operations"]["exact"]),
        "preserve_exact": bool(metrics["preserves"]["exact"]),
        "goal_effect_exact": goal_complete,
    }


def _semantic_graph_empty(semantics: JsonObject) -> bool:
    """Use Goal, Operation and Minefield to jointly judge synonyms."""
    return not any(
        semantics.get(key) for key in ("goals", "operations", "minefields")
    )


def _few_shot_contaminated(case_id: str) -> bool:
    """Marks the ToolSandbox case, which is the same source as the two configuration models."""
    return any(case_id.startswith(prefix) for prefix in _FEW_SHOT_CONTAMINATED_PREFIXES)


def _fatal_tool_name(value: object) -> str | None:
    """Reads the toolname from the canonical Fatal minefield identity."""
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
    return str(tool_name) if isinstance(tool_name, str) and tool_name else None


def _multiset_metric(
    reference_items: Counter[str], generated_items: Counter[str]
) -> JsonObject:
    """Calculates multiple sets using occurrence count. precision, recall, F1 and exact."""
    reference_count = sum(reference_items.values())
    generated_count = sum(generated_items.values())
    true_positive = sum((reference_items & generated_items).values())
    precision = true_positive / generated_count if generated_count else float(not reference_count)
    recall = true_positive / reference_count if reference_count else float(not generated_count)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "reference_count": reference_count,
        "generated_count": generated_count,
        "true_positive": true_positive,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact": reference_items == generated_items,
    }


def _graph_descriptor(
    graph: MilestoneGraph | nx.DiGraph,
    mode: Literal["strict", "structural", "topology", "semantic"] = "semantic",
    tool_aliases: dict[str, str] | None = None,
    view: GeneratorTaskView | None = None,
) -> nx.DiGraph:
    """Converts the milestone graph to a DiGraph with a stabilization node/ border label. Adds a semantic mode."""
    if isinstance(graph, (nx.DiGraph, nx.Graph)):
        if not graph.is_directed():
            directed = nx.DiGraph()
            directed.add_nodes_from(graph.nodes(data=True))
            directed.add_edges_from(graph.edges(data=True))
            return directed
        return graph.copy()
    if not isinstance(graph, MilestoneGraph):
        raise TypeError('graph must be a MilestoneGraph or nx.DiGraph')
    descriptor = nx.DiGraph()
    terminal_ids = {
        node.milestone_id
        for node in graph.nodes
        if not any(source == node.milestone_id for source, _ in graph.edges)
    }
    if mode == "semantic":
        for node in graph.nodes:
            is_terminal = node.milestone_id in terminal_ids
            semantic_constraints = [
                (constraint, constraint.stage_goal_semantics)
                for constraint in node.constraints
                if isinstance(constraint.stage_goal_semantics, dict)
            ]
            primary = next(
                (
                    (c, s) for c, s in semantic_constraints
                    if s.get("kind") != "preserve_state"
                ),
                None,
            )
            primary_constraint, primary_semantic = primary if primary is not None else (None, None)
            kind = primary_semantic.get("kind") if isinstance(primary_semantic, dict) else None

            if kind == "set_state":
                state_goal = _canonical_state_goal(primary_semantic, primary_constraint, view)
                label = {
                    "kind": "set_state",
                    "namespace": str(state_goal.get("namespace")),
                    "operation": str(state_goal.get("operation")),
                    "terminal": is_terminal,
                }
            elif kind == "emit_message":
                label = {"kind": "emit_message", "terminal": is_terminal}
            else:
                tool_name = _tool_name_from_node(node)
                tool_name = (tool_aliases or {}).get(tool_name, tool_name) if tool_name else "unknown"
                label = {"kind": "tool_call", "tool_name": tool_name, "terminal": is_terminal}

            descriptor.add_node(node.milestone_id, label=canonical_json(label))
        for source, target in graph.edges:
            descriptor.add_edge(source, target, label="directed_precedence")
        return descriptor

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
        constraint_shapes.sort(key=canonical_json)
        label = (
            {"terminal": node.milestone_id in terminal_ids}
            if mode == "topology"
            else
            {
                "terminal": node.milestone_id in terminal_ids,
                "constraint_shapes": constraint_shapes,
            }
            if mode == "structural"
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
                        key=lambda value: canonical_json({
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
        descriptor.add_node(node.milestone_id, label=canonical_json(label))
    for source, target in graph.edges:
        descriptor.add_edge(source, target, label="directed_precedence")
    return descriptor


def _descriptor_summary(graph: MilestoneGraph) -> JsonObject:
    """Returns the schematic summary that does not contain task text and expectations."""
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
    """A GED solver results simultaneously with GED audit fields and nodes."""
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
    """Compute Graph Edit and Auditable Edit Path. $ GED(G_p, G_r)=min_{P in Paths(G_p,G_r)} sum_{o in P}c(o) $ Source: Bunke & Shearer, Pattern Recognition Letters, 1998,https://doi.org/10.1016/S0167-8655(97)00164-7
    """
    profile = _validated_profile(edit_cost_profile)
    if solver_mode not in {"exact", "approximate"}:
        raise ValueError('Solver_mode must be exact or approximate')
    if timeout_seconds <= 0:
        raise ValueError('Timeout_seconds must be greater than 0')
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
        raise RuntimeError('GED solver did not return a viable edit path')

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
    """Provide a deterministic fallback when the optional NumPy/SciPy dependency required by NetworkX is unavailable."""
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
        raise RuntimeError('GED no viable editing path')
    return best


def _node_set_f1(
    predicted_graph: nx.DiGraph,
    reference_graph: nx.DiGraph,
    vertex_path: list[tuple[Any, Any]] | None = None,
) -> JsonObject:
    """Calculates the F1 baseline for the node pool using the GED zero-cost node replacement."""
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
    """Deprecated: safely return the disabled result to avoid the heavy external POT dependency and numerical degradation."""
    return {
        "status": "deprecated",
        "distance": None,
        "alpha": alpha,
        "relation_projection": "undirected_shortest_path",
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
    """Write index. json with submary.json."""
    statuses = (
        "completed",
        "generation_failed",
        "adapt_failed",
        "ged_failed",
    )
    status_counts = {
        status: sum(result.status == status for result in results)
        for status in statuses
    }
    completed = [result for result in results if result.status == "completed"]
    primary_completed = [
        result for result in completed
        if not bool(result.metrics.get("few_shot_contaminated"))
    ]
    topology_similarities = [
        float(result.metrics["diagnostics"]["topology"]["ged_similarity"])
        for result in completed
    ]
    graph_return_count = sum(bool(result.metrics.get("graph_returned")) for result in results)
    summary = {
        "status_counts": status_counts,
        "graph_return_count": graph_return_count,
        "valid_dag_compilation_rate": (
            graph_return_count / len(results) if results else None
        ),
        "evaluation": {
            "tool_operation_micro": _semantic_micro_metric(
                primary_completed, "operations"
            ),
            "tool_operation_macro": _semantic_macro_metric(
                primary_completed, "operations"
            ),
            "fatal_minefield": _semantic_micro_metric(
                primary_completed, "minefields"
            ),
        },
        "topology": {
            "ged_similarity": {
                "mean": (
                    statistics.fmean(topology_similarities)
                    if topology_similarities else None
                )
            }
        },
    }
    index = {
        "experiment_id": config["experiment_id"],
        "experiment_config": str(config_path),
        "random_seed": options.random_seed,
        "workers": options.workers or 1,
        "started_at": started_at,
        "finished_at": finished_at,
        "groups": [
            {
                "benchmark": group.benchmark,
                "repeat_index": group.repeat_index,
                "random_seed": options.random_seed + group.repeat_index,
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




def _semantic_micro_metric(
    results: list[_CaseResult], dimension: str
) -> JsonObject:
    """microPrecision/Recall/F1 for the aggregation of semantic numbers per case."""
    reference_count = sum(
        int(result.metrics["semantic_metrics"][dimension]["reference_count"])
        for result in results
    )
    generated_count = sum(
        int(result.metrics["semantic_metrics"][dimension]["generated_count"])
        for result in results
    )
    true_positive = sum(
        int(result.metrics["semantic_metrics"][dimension]["true_positive"])
        for result in results
    )
    precision = true_positive / generated_count if generated_count else None
    recall = true_positive / reference_count if reference_count else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def _semantic_macro_metric(
    results: list[_CaseResult], dimension: str
) -> JsonObject:
    """Macro Precision/Recall/F1 for average aggregation by case fraction."""
    values = [
        result.metrics["semantic_metrics"][dimension]
        for result in results
    ]
    return {
        metric: (
            statistics.fmean([float(item[metric]) for item in values])
            if values else None
        )
        for metric in ("precision", "recall", "f1")
    }


def _write_case_json(
    path: Path,
    result: _CaseResult,
    experiment_id: str,
    run_id: str,
) -> None:
    """Writes a stable structure for individual case."""
    finished_at = _utc_now()
    payload = {
        "experiment_id": experiment_id,
        "run_id": run_id,
        "benchmark": result.benchmark,
        "case_id": result.case_id,
        "status": result.status,
        "finished_at": finished_at,
        "elapsed_ms": result.elapsed_ms,
        "graph_state": {
            "graph_returned": bool(result.metrics.get("graph_returned")),
            "graph_empty": bool(result.metrics.get("graph_empty")),
            "reference_graph_empty": bool(result.metrics.get("reference_graph_empty")),
            "completed": result.status == "completed",
            "generation_status": result.metrics.get("generation_status"),
        },
        "reference": result.reference_summary,
        "prediction": result.prediction_summary,
        "semantic_metrics": result.metrics.get("semantic_metrics", {}),
        "input_coverage": result.metrics.get("input_coverage", {}),
        "diagnostics": result.metrics.get("diagnostics", {}),
        "generation_report": result.generation_report,
        "llm_usage": result.generation_usage,
        "raw_response_records": [],
        "errors": list(result.diagnostics),
    }
    _write_json(path, payload)


def _validate_reliability_metadata(metadata: object) -> JsonObject:
    if not isinstance(metadata, Mapping):
        raise ValueError('Metadata. milestone_reliability must be a JSON object')
    result = dict(metadata)
    result.setdefault("primary_metric", "goal_effect_exact")
    result.setdefault("ged_solver", "exact")
    result.setdefault("ged_timeout_seconds", 60)
    result.setdefault("edit_cost_profile", dict(DEFAULT_EDIT_COST_PROFILE))
    result.setdefault("fgw", False)
    if result["primary_metric"] != "goal_effect_exact":
        raise ValueError("I don't know what you're talking about.")
    if result["ged_solver"] not in {"exact", "approximate"}:
        raise ValueError("I don't know, metadata. milestone_reliability.ged_solver")
    timeout = result["ged_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(
            'You have to be positive, metadata.milestone_reliability.ged_timeout_seconds'
        )
    result["edit_cost_profile"] = _validated_profile(
        result["edit_cost_profile"]
    )
    if not isinstance(result["fgw"], bool):
        raise ValueError("If you want to do this, you're gonna have to do it.")
    return result


def _validated_profile(profile: object) -> JsonObject:
    if not isinstance(profile, Mapping):
        raise ValueError('Edit_cost_profile must be a JSON object')
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
        raise ValueError(f"Missing field for edit_cost_profile:{missing}")
    if not str(result["profile_id"]).strip():
        raise ValueError('edit_cost_profile.profile_id cannot be empty')
    for key in ("node_insert", "node_delete", "edge_insert", "edge_delete"):
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"edit_cost_profile.{key} must be finite and non-negative")
        result[key] = float(value)
    if result["node_label_distance"] != "canonical_json":
        raise ValueError('edit_cost_profile.node_label_distance must be canonical_json')
    if result["edge_label_distance"] != "exact":
        raise ValueError('If you have to do something about it, you will have to do something about it.')
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
    generation_usage: JsonObject | None = None,
) -> _CaseResult:
    diagnostics: tuple[str, ...] = (_safe_error(exc),)
    if status == "generation_failed":
        traceback_text = str((report or {}).get("exception_traceback") or traceback.format_exc())
        report = {
            **(report or {}),
            "exception_stage": str((report or {}).get("exception_stage") or "compile_task_case"),
            "exception_traceback": traceback_text,
        }
        diagnostics = (*diagnostics, traceback_text)
    return _CaseResult(
        benchmark=group.benchmark,
        case_id=case_id,
        status=status,
        metrics={},
        output_path=Path(),
        generation_report=report or {},
        generation_usage=generation_usage or summarize_llm_calls([]),
        reference_count=reference_count,
        prediction_count=prediction_count,
        reference_summary=reference_summary or {},
        prediction_summary=prediction_summary or {},
        diagnostics=diagnostics,
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


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).splitlines())[:300]
    message = _redact_sensitive_values(message)
    return f"{type(exc).__name__}: {message}"


def _redact_sensitive_values(message: str) -> str:
    """Hides only suspect evidence values and retains the field name for the locationable configuration problem."""
    patterns = (
        r"(?i)(api_key\s*[:=]\s*)\S+",
        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+",
        r"(?i)(password\s*[:=]\s*)\S+",
        r"(?i)(token\s*[:=]\s*)\S+",
    )
    for pattern in patterns:
        message = re.sub(pattern, r"\1[REDACTED]", message)
    return message


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        raise _InfrastructureError(f"Synchronising \"%s\"{path}") from exc


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
