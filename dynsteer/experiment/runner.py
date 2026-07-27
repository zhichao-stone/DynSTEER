from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from dynsteer.adapter.loader import load_task_case, load_trajectory
from dynsteer.adapter.registry import get_adapter, get_harness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.experiment.config import build_harness_config, config_for_case, expand_experiment_matrix, load_experiment_config
from dynsteer.experiment.metrics import write_metric_tables
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import write_case_outputs, write_default_case_outputs, write_replay_case_outputs
from dynsteer.harness.selection import select_case_ids, validate_loaded_task_cases
from dynsteer.model import HarnessEvaluationOutput, JsonObject, TaskCase
from dynsteer.utils import as_number, json_safe, read_json_file


def run_experiment(config_path: Path | str, workers: int = 1) -> list[ExperimentCaseResult]:
    """执行统一实验矩阵并写出汇总。

    入参：
        config_path: 实验 JSON 配置路径。
        workers: 预留并发参数；当前实验层按规格串行执行以保证复现。
    输出：
        case 级结构化实验结果列表。
    """
    if workers < 1:
        raise ValueError("workers 必须大于 0")
    config = load_experiment_config(config_path)
    specs = expand_experiment_matrix(config)
    results: list[ExperimentCaseResult] = []
    default_outputs: dict[tuple[str, str, int, str], tuple[HarnessEvaluationOutput, JsonObject]] = {}
    for spec in specs:
        harness_config = build_harness_config(spec)
        task_cases = _load_task_cases(harness_config)
        for task_case in task_cases:
            key = (spec.benchmark, spec.model_id, spec.repeat_index, task_case.case_id)
            if spec.method == ExperimentMethod.DEFAULT:
                output, default_reference = run_default_case(spec, task_case)
                default_outputs[key] = (output, default_reference)
                results.append(_case_result_from_output(spec, task_case, output, default_reference=default_reference))
            elif spec.method in {ExperimentMethod.DYNSTEER_REPLAY, ExperimentMethod.DYNSTEER_REPLAY_STATIC}:
                cached_default = default_outputs.get(key)
                if cached_default is None:
                    default_spec = _default_spec_for_replay(spec)
                    default_output, default_reference = run_default_case(default_spec, task_case)
                    default_outputs[key] = (default_output, default_reference)
                    results.append(_case_result_from_output(default_spec, task_case, default_output, default_reference=default_reference))
                else:
                    default_output, default_reference = cached_default
                output = run_replay_case(spec, task_case, default_output, default_reference)
                results.append(_case_result_from_output(spec, task_case, output, default_reference=default_reference))
            elif spec.method == ExperimentMethod.DYNSTEER_EVALUATE:
                output = run_evaluate_case(spec, task_case)
                results.append(_case_result_from_output(spec, task_case, output))
            elif spec.method == ExperimentMethod.DYNSTEER_GUIDANCE:
                output = run_guidance_case(spec, task_case)
                results.append(_case_result_from_output(spec, task_case, output))
    output_dir = _experiment_output_dir(specs)
    write_experiment_index(results, output_dir)
    write_metric_tables(results, output_dir)
    return results


def run_default_case(spec: ExperimentRunSpec, task_case: TaskCase) -> tuple[HarnessEvaluationOutput, JsonObject]:
    """完整执行 benchmark，不进行 DynSTEER 介入。"""
    if spec is None or task_case is None:
        raise ValueError("spec 和 task_case 不能为空")
    harness = get_harness(spec.benchmark)
    config = _config_for_method(spec, task_case.case_id, ExperimentMethod.DEFAULT)
    output = write_default_case_outputs(config=config, harness=harness, task_case=task_case)
    raw_summary = read_json_file(output.raw_summary_path, str(output.raw_summary_path), dict)
    default_result = raw_summary.get("default_result")
    if not isinstance(default_result, dict):
        raise ValueError(f"Default raw_summary 缺少 default_result: {output.raw_summary_path}")
    return output, dict(default_result)


def run_replay_case(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    default_output: HarnessEvaluationOutput,
    default_reference: JsonObject,
) -> HarnessEvaluationOutput:
    """读取同一条 Default 轨迹，执行 DynSTEER-Replay。"""
    if spec is None or task_case is None or default_output is None:
        raise ValueError("spec、task_case 和 default_output 不能为空")
    harness = get_harness(spec.benchmark)
    trajectory_data = read_json_file(default_output.trajectory_path, str(default_output.trajectory_path), dict)
    trajectory = load_trajectory(trajectory_data)
    evaluator = DynSTEEREvaluator.from_config(build_harness_config(spec), strategy=spec.strategy)
    return write_replay_case_outputs(
        config=config_for_case(build_harness_config(spec), task_case.case_id),
        evaluator=evaluator,
        task_case=task_case,
        trajectory=trajectory,
        harness=harness,
        default_reference=default_reference,
    )


def run_evaluate_case(spec: ExperimentRunSpec, task_case: TaskCase) -> HarnessEvaluationOutput:
    """调用 DynSTEEREvaluator.evaluate() 主入口执行在线动态评估。"""
    if spec is None or task_case is None:
        raise ValueError("spec 和 task_case 不能为空")
    harness = get_harness(spec.benchmark)
    config = config_for_case(build_harness_config(spec), task_case.case_id)
    evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
    return write_case_outputs(config=config, harness=harness, evaluator=evaluator, task_case=task_case)


def run_guidance_case(spec: ExperimentRunSpec, task_case: TaskCase) -> HarnessEvaluationOutput:
    """动态 guidance 分实验入口。"""
    raise NotImplementedError("DynSTEER guidance 注入 hook 尚未实现，请等待分实验阶段补齐")


def write_experiment_index(results: list[ExperimentCaseResult], output_dir: Path) -> None:
    """写出所有 case 的结构化索引。"""
    if results is None or output_dir is None:
        raise ValueError("results 和 output_dir 不能为空")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _build_experiment_index_payload(results)
    (output_dir / "index.json").write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=4), encoding="utf-8")


def _build_experiment_index_payload(results: list[ExperimentCaseResult]) -> JsonObject:
    """合并实验结果，构建写入 index.json 的分层结构。"""
    if not results:
        return {"schema_version": 2, "experiment_id": None, "case_count": 0, "results": {}}
    experiment_id = results[0].experiment_id
    grouped: dict[str, dict[str, dict[str, JsonObject]]] = {}
    for result in sorted(results, key=_experiment_index_sort_key):
        if result.experiment_id != experiment_id:
            raise ValueError("results 不能同时包含不同 experiment_id")
        benchmark_bucket = grouped.setdefault(result.benchmark, {})
        method_bucket = benchmark_bucket.setdefault(result.method.value, {})
        model_bucket = method_bucket.setdefault(result.model_id, {"repeats": {}})
        repeats = model_bucket.setdefault("repeats", {})
        if not isinstance(repeats, dict):
            raise ValueError("repeats 类型异常")
        repeat_key = str(result.repeat_index)
        repeat_node = repeats.setdefault(repeat_key, {"run_id": result.run_id, "cases": {}})
        if repeat_node.get("run_id") != result.run_id:
            raise ValueError("同一 repeat 下的 run_id 必须一致")
        cases = repeat_node.setdefault("cases", {})
        if not isinstance(cases, dict):
            raise ValueError("cases 类型异常")
        if result.case_id in cases:
            raise ValueError("同一 repeat 和 case_id 下的结果不能重复写入")
        cases[result.case_id] = result.to_index_dict()
    return {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "case_count": len(results),
        "results": _sort_experiment_index_results(grouped),
    }


def _sort_experiment_index_results(grouped: dict[str, dict[str, dict[str, JsonObject]]]) -> JsonObject:
    """按字典序和 repeat 序号稳定排序实验索引结果。"""
    ordered_results: JsonObject = {}
    for benchmark, benchmark_bucket in sorted(grouped.items()):
        ordered_methods: JsonObject = {}
        for method, method_bucket in sorted(benchmark_bucket.items()):
            ordered_models: JsonObject = {}
            for model_id, model_bucket in sorted(method_bucket.items()):
                repeats = model_bucket.get("repeats") if isinstance(model_bucket, dict) else {}
                ordered_repeats: JsonObject = {}
                if isinstance(repeats, dict):
                    for repeat_index, repeat_node in sorted(repeats.items(), key=lambda item: int(item[0])):
                        cases = repeat_node.get("cases") if isinstance(repeat_node, dict) else {}
                        ordered_cases: JsonObject = {}
                        if isinstance(cases, dict):
                            for case_id, case_payload in sorted(cases.items()):
                                ordered_cases[case_id] = case_payload
                        ordered_repeats[repeat_index] = {
                            "run_id": repeat_node.get("run_id") if isinstance(repeat_node, dict) else None,
                            "cases": ordered_cases,
                        }
                ordered_models[model_id] = {"repeats": ordered_repeats}
            ordered_methods[method] = ordered_models
        ordered_results[benchmark] = ordered_methods
    return ordered_results


def _experiment_index_sort_key(result: ExperimentCaseResult) -> tuple[str, str, str, int, str]:
    """为实验索引按字典序稳定排序。"""
    return (result.benchmark, result.method.value, result.model_id, result.repeat_index, result.case_id)


def _load_task_cases(config: HarnessRunConfig) -> list[TaskCase]:
    """加载当前规格的 adapted task cases。"""
    adapter = get_adapter(config.benchmark)
    harness = get_harness(config.benchmark)
    case_ids = select_case_ids(config, harness, run_all=True)
    run_config = replace(config, case_ids=tuple(case_ids))
    harness.prepare_config(run_config)
    task_cases = load_task_case(run_config, adapter)
    validate_loaded_task_cases(case_ids, task_cases)
    return task_cases


def _config_for_method(spec: ExperimentRunSpec, case_id: str, method: ExperimentMethod) -> HarnessRunConfig:
    """构造指定 method 和 case 的 HarnessRunConfig。"""
    metadata = spec.to_metadata()
    metadata["method"] = method.value
    config = build_harness_config(replace(spec, method=method, metadata={**spec.metadata, "method": method.value}))
    return config_for_case(replace(config, metadata={**config.metadata, **metadata}), case_id)


def _default_spec_for_replay(spec: ExperimentRunSpec) -> ExperimentRunSpec:
    """为 replay 自动补跑同条件 Default 规格。"""
    return replace(
        spec,
        method=ExperimentMethod.DEFAULT,
        run_id=spec.run_id.replace(spec.method.value, ExperimentMethod.DEFAULT.value),
    )


def _case_result_from_output(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    output: HarnessEvaluationOutput,
    default_reference: JsonObject | None = None,
) -> ExperimentCaseResult:
    """从输出摘要构造 ExperimentCaseResult。"""
    summary = read_json_file(output.summary_path, str(output.summary_path), dict)
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
    runtime_metrics = summary.get("runtime_metrics") if isinstance(summary.get("runtime_metrics"), dict) else {}
    default_score = as_number(summary.get("default_score"))
    if default_score is None and default_reference is not None:
        default_score = as_number(default_reference.get("score"))
    dynsteer_score = None if spec.method == ExperimentMethod.DEFAULT else as_number(summary.get("overall_score"))
    resolved = summary.get("resolved")
    return ExperimentCaseResult(
        experiment_id=spec.experiment_id,
        run_id=spec.run_id,
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
            "result_dir": str(output.result_dir),
            "raw_run_dir": str(output.raw_run_dir),
            "summary_path": str(output.summary_path),
            "report_path": str(output.report_path),
            "trajectory_path": str(output.trajectory_path),
        },
        raw={"summary_metadata": dict(metadata), "default_reference": dict(default_reference or {})},
    )


def _experiment_output_dir(specs: list[ExperimentRunSpec]) -> Path:
    """读取实验输出目录。"""
    if not specs:
        return Path("results") / "experiments" / "empty"
    return specs[0].results_dir
