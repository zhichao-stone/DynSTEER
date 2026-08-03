from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from tqdm import tqdm

from dynsteer.adapter import BaseBenchmarkHarness, get_harness
from dynsteer.adapter.loader import load_trajectory
from dynsteer.adapter.toolsandbox.utils.runtime import clear_named_scenarios_cache
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.state_summary import apply_runtime_initial_state
from dynsteer.experiment.config import build_harness_config, expand_experiment_matrix, load_experiment_config
from dynsteer.experiment.metrics import write_metric_tables
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import write_case_outputs, write_default_case_outputs, write_replay_case_outputs
from dynsteer.harness.runner import effective_max_workers, prepare_task_cases
from dynsteer.model import HarnessEvaluationOutput, JsonObject, TaskCase
from dynsteer.utils import as_number, json_safe, read_json_file


TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")
DefaultOutputKey = tuple[str, str, str, int, str]
DefaultOutputCache = dict[DefaultOutputKey, tuple[HarnessEvaluationOutput, JsonObject]]
DefaultCaseItem = tuple[DefaultOutputKey, TaskCase]
DefaultCaseEntry = tuple[DefaultOutputKey, TaskCase, HarnessEvaluationOutput, JsonObject]

_METHODS_NEED_DEFAULT = {
    ExperimentMethod.DEFAULT,
    ExperimentMethod.DYNSTEER_REPLAY,
    ExperimentMethod.DYNSTEER_REPLAY_STATIC,
    ExperimentMethod.DYNSTEER_REPLAY_STATIC_WEIGHTING,
    ExperimentMethod.DYNSTEER_REPLAY_STATIC_ROUTING,
}


class ExperimentCaseExecutionError(RuntimeError):
    """单个 experiment case 运行失败时抛出。"""

    def __init__(self, spec: ExperimentRunSpec, case_id: str, cause: Exception) -> None:
        self.experiment_id = spec.experiment_id
        self.benchmark = spec.benchmark
        self.method = spec.method
        self.model_id = spec.model_id
        self.repeat_index = spec.repeat_index
        self.threshold_profile = spec.threshold_profile
        self.case_id = case_id
        self.cause = cause
        super().__init__(
            "experiment case 执行失败: "
            f"experiment_id={spec.experiment_id}, benchmark={spec.benchmark}, method={spec.method.value}, "
            f"model_id={spec.model_id}, repeat_index={spec.repeat_index}, case_id={case_id}, error={cause}"
        )


def run_experiment(
    config_path: Path | str,
    workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
    no_sum: bool = False,
) -> list[ExperimentCaseResult]:
    """执行统一实验矩阵并写出结果。"""
    config = load_experiment_config(config_path)
    clear_named_scenarios_cache()
    specs = expand_experiment_matrix(config)
    effective_force_eval = bool(force_eval or force_adapt)

    harness_configs = [build_harness_config(spec) for spec in specs]

    results: list[ExperimentCaseResult] = []
    default_outputs: DefaultOutputCache = {}
    for spec_index, (spec, harness_config) in enumerate(zip(specs, harness_configs, strict=True)):
        print("=" * 20 + f" 第 {spec_index + 1:02d} 组实验 " + "=" * 20)
        print(
            f"Info: benchmark={spec.benchmark}, model={spec.model_id}, method={spec.method.name}, repeat_index={spec.repeat_index}"
        )
        prepared_task_cases, harness_config = prepare_task_cases(
            harness_config,
            force_adapt,
            refresh_dynamic_targets=True,
        )
        task_cases = tuple(prepared_task_cases)
        spec_workers = effective_max_workers(workers, harness_config)

        if spec.method in _METHODS_NEED_DEFAULT:
            default_spec = spec if spec.method == ExperimentMethod.DEFAULT else replace(spec, method=ExperimentMethod.DEFAULT)
            default_entries = _run_worker_group(
                _pending_default_case_items(spec, task_cases, default_outputs),
                max_workers=spec_workers,
                runner=partial(_run_default_case_entry, spec=default_spec, force_eval=effective_force_eval),
                progress_desc="执行 DEFAULT case",
            )
            for key, task_case, output, reference in default_entries:
                default_outputs[key] = (output, reference)
                results.append(_case_result_from_output(default_spec, task_case, output, reference))

            if spec.method != ExperimentMethod.DEFAULT:
                results.extend(
                    _run_worker_group(
                        task_cases,
                        max_workers=spec_workers,
                        runner=partial(
                            _run_replay_case_entry, 
                            spec=spec, default_outputs=default_outputs, force_eval=effective_force_eval
                        ),
                        progress_desc=f"执行 {spec.method.name} case",
                    )
                )

        elif spec.method == ExperimentMethod.DYNSTEER_EVALUATE:
            results.extend(
                _run_worker_group(
                    task_cases,
                    max_workers=spec_workers,
                    runner=partial(_run_evaluate_case_entry, spec=spec, force_eval=effective_force_eval),
                    progress_desc=f"执行 {spec.method.name} case",
                )
            )
        else:
            raise ValueError(f"不支持的 experiment method: {spec.method}")

    if results and not no_sum:
        output_dir = specs[0].results_dir
        write_experiment_index(results, output_dir)
        write_metric_tables(results, output_dir)
    return results


def _default_output_key(spec: ExperimentRunSpec, task_case: TaskCase) -> DefaultOutputKey:
    """生成 default_outputs 的缓存 key。"""
    return spec.experiment_id, spec.benchmark, spec.model_id, spec.repeat_index, task_case.case_id


def _pending_default_case_items(
    spec: ExperimentRunSpec,
    task_cases: tuple[TaskCase, ...],
    default_outputs: DefaultOutputCache,
) -> list[DefaultCaseItem]:
    """筛出尚未准备好的 default case。"""
    pending: list[DefaultCaseItem] = []
    for task_case in task_cases:
        key = _default_output_key(spec, task_case)
        if key not in default_outputs:
            pending.append((key, task_case))
    return pending


def _run_default_case_entry(item: DefaultCaseItem, *, spec: ExperimentRunSpec, force_eval: bool) -> DefaultCaseEntry:
    """执行单个 default case 并返回可缓存结果。"""
    key, task_case = item
    try:
        output, reference = run_default_case(spec, task_case, force_eval=force_eval)
    except Exception as exc:
        raise ExperimentCaseExecutionError(spec, task_case.case_id, exc) from exc
    return key, task_case, output, reference


def _run_replay_case_entry(
    task_case: TaskCase,
    *,
    spec: ExperimentRunSpec,
    default_outputs: DefaultOutputCache,
    force_eval: bool,
) -> ExperimentCaseResult:
    """执行单个 replay case。"""
    key = _default_output_key(spec, task_case)
    cached = default_outputs.get(key)
    if cached is None:
        raise ExperimentCaseExecutionError(spec, task_case.case_id, KeyError(f"缺少 default output: {key}"))
    default_output, default_reference = cached
    try:
        output = run_replay_case(
            spec,
            task_case,
            default_output,
            default_reference,
            force_eval=force_eval,
        )
    except Exception as exc:
        raise ExperimentCaseExecutionError(spec, task_case.case_id, exc) from exc
    return _case_result_from_output(spec, task_case, output, default_reference)


def _run_evaluate_case_entry(task_case: TaskCase, *, spec: ExperimentRunSpec, force_eval: bool) -> ExperimentCaseResult:
    """执行单个在线 evaluate case。"""
    try:
        output = run_evaluate_case(spec, task_case, force_eval=force_eval)
    except Exception as exc:
        raise ExperimentCaseExecutionError(spec, task_case.case_id, exc) from exc
    return _case_result_from_output(spec, task_case, output)


def _run_worker_group(
    items: Sequence[TInput],
    *,
    max_workers: int,
    runner: Callable[[TInput], TOutput],
    progress_desc: str,
) -> list[TOutput]:
    """按给定 worker 数执行一组独立任务，并保持原始顺序。"""
    if not items:
        return []
    if max_workers == 1:
        with tqdm(items, total=len(items), unit="case", desc=progress_desc) as progress:
            return [runner(item) for item in progress]

    outputs_by_index: dict[int, TOutput] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(runner, item): index for index, item in enumerate(items)}
        try:
            with tqdm(total=len(items), unit="case", desc=progress_desc) as progress:
                for future in as_completed(futures):
                    outputs_by_index[futures[future]] = future.result()
                    progress.update(1)
        except Exception:
            for future in futures:
                future.cancel()
            raise
    return [outputs_by_index[index] for index in range(len(items))]


def _prepare_for_run_case(spec: ExperimentRunSpec, task_case_template: TaskCase) -> tuple[TaskCase, BaseBenchmarkHarness, HarnessRunConfig]:
    task_case = copy.deepcopy(task_case_template)
    harness = get_harness(spec.benchmark)
    config = build_harness_config(spec)
    return task_case, harness, config


def run_default_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    force_eval: bool = False,
) -> tuple[HarnessEvaluationOutput, JsonObject]:
    """执行原生 Default case。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    output = write_default_case_outputs(
        config=config,
        harness=harness,
        task_case=task_case,
        force_eval=force_eval,
    )
    raw_summary = read_json_file(output.raw_summary_path, str(output.raw_summary_path), dict)
    default_result = raw_summary.get("default_result")
    if not isinstance(default_result, dict):
        raise ValueError(f"Default raw_summary 缺少 default_result: {output.raw_summary_path}")
    return output, dict(default_result)


def run_replay_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    default_output: HarnessEvaluationOutput,
    default_reference: JsonObject,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """读取 Default 轨迹并执行 replay。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    trajectory_data = read_json_file(default_output.trajectory_path, str(default_output.trajectory_path), dict)
    trajectory = load_trajectory(trajectory_data)
    runtime_initial_state = trajectory.raw.get("runtime_initial_state")
    if isinstance(runtime_initial_state, dict):
        apply_runtime_initial_state(task_case, runtime_initial_state, "default_trajectory")
    evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
    return write_replay_case_outputs(
        config=config,
        evaluator=evaluator,
        task_case=task_case,
        trajectory=trajectory,
        harness=harness,
        default_reference=default_reference,
        force_eval=force_eval,
    )


def run_evaluate_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """直接调用 DynSTEEREvaluator.evaluate() 执行在线评估。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
    return write_case_outputs(
        config=config,
        harness=harness,
        evaluator=evaluator,
        task_case=task_case,
        force_eval=force_eval,
    )


def write_experiment_index(results: list[ExperimentCaseResult], output_dir: Path) -> None:
    """写出分层 experiment index。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _build_experiment_index_payload(results)
    (output_dir / "index.json").write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=4), encoding="utf-8")


def _build_experiment_index_payload(results: list[ExperimentCaseResult]) -> JsonObject:
    """合并实验结果，构建 index.json 的分层结构。"""
    if not results:
        return {"experiment_id": None, "case_count": 0, "results": {}}
    experiment_id = results[0].experiment_id
    grouped: JsonObject = {}
    for result in sorted(results, key=lambda item: (item.benchmark, item.method.value, item.model_id, item.repeat_index, item.case_id)):
        if result.experiment_id != experiment_id:
            raise ValueError("results 不能同时包含不同 experiment_id")
        cases = _result_bucket(grouped, result)
        if result.case_id in cases:
            raise ValueError("同一个 repeat 和 case_id 下的结果不能重复写入")
        cases[result.case_id] = result.to_index_dict()
    return {"experiment_id": experiment_id, "case_count": len(results), "results": grouped}


def _result_bucket(root: JsonObject, result: ExperimentCaseResult) -> JsonObject:
    benchmark = root.setdefault(result.benchmark, {})
    method = benchmark.setdefault(result.method.value, {})
    model = method.setdefault(result.model_id, {"repeats": {}})
    repeat = model["repeats"].setdefault(str(result.repeat_index), {"cases": {}})
    return repeat["cases"]


def _case_result_from_output(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    output: HarnessEvaluationOutput,
    default_reference: JsonObject | None = None,
) -> ExperimentCaseResult:
    """从输出文件中抽取 ExperimentCaseResult。"""
    summary: dict = read_json_file(output.summary_path, str(output.summary_path), dict)
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
    runtime_metrics = summary.get("runtime_metrics") if isinstance(summary.get("runtime_metrics"), dict) else {}

    default_score = as_number(summary.get("default_score"))
    if default_score is None and default_reference is not None:
        default_score = as_number(default_reference.get("score"))
    dynsteer_score = None if spec.method == ExperimentMethod.DEFAULT else as_number(summary.get("overall_score"))
    resolved = summary.get("resolved")

    return ExperimentCaseResult(
        experiment_id=spec.experiment_id,
        benchmark=spec.benchmark,
        case_id=task_case.case_id,
        model_id=spec.model_id,
        repeat_index=spec.repeat_index,
        method=spec.method,
        default_score=default_score,
        dynsteer_score=dynsteer_score,
        resolved=resolved if isinstance(resolved, bool) else None,
        runtime_metrics=dict(runtime_metrics),
        output_paths={
            k: str(v)
            for k, v in asdict(output).items()
            if k in ["result_dir", "raw_run_dir", "summary_path", "report_path", "trajectory_path"]
        },
        raw={"summary_metadata": dict(metadata), "default_reference": dict(default_reference or {})},
    )
