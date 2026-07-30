from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

from tqdm import tqdm

from dynsteer.adapter import BaseBenchmarkHarness, get_harness
from dynsteer.adapter.loader import load_trajectory
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.state_summary import state_namespace_summary
from dynsteer.experiment.config import build_harness_config, expand_experiment_matrix, load_experiment_config
from dynsteer.experiment.metrics import write_metric_tables
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import write_case_outputs, write_default_case_outputs, write_replay_case_outputs
from dynsteer.harness.runner import prepare_task_cases
from dynsteer.model import HarnessEvaluationOutput, JsonObject, TaskCase
from dynsteer.utils import as_number, json_safe, read_json_file


def run_experiment(
    config_path: Path | str,
    force_adapt: bool = False,
    force_eval: bool = False,
    no_sum: bool = False,
) -> list[ExperimentCaseResult]:
    """执行统一实验矩阵并写出结果。"""
    config = load_experiment_config(config_path)
    specs = expand_experiment_matrix(config)
    effective_force_eval = bool(force_eval or force_adapt)
    task_cases_by_benchmark: dict[str, tuple[TaskCase, ...]] = {}
    for spec in specs:
        if spec.benchmark not in task_cases_by_benchmark:
            harness_config = build_harness_config(spec)
            task_cases, _ = prepare_task_cases(harness_config, force_adapt)
            task_cases_by_benchmark[spec.benchmark] = task_cases
    results: list[ExperimentCaseResult] = []
    default_outputs: dict[tuple[str, str, int, str], tuple[HarnessEvaluationOutput, JsonObject]] = {}
    for i, spec in enumerate(specs):
        print("=" * 20 + f"第{i:02d}组实验" + "=" * 20)
        print(f"Info: benchmark={spec.benchmark}, model={spec.model_id}, method={spec.method.name}, repeat_index={spec.repeat_index}")
        for task_case in tqdm(task_cases_by_benchmark[spec.benchmark], desc="实验进度", unit="case"):
            if spec.method in {
                ExperimentMethod.DEFAULT, 
                ExperimentMethod.DYNSTEER_REPLAY, ExperimentMethod.DYNSTEER_REPLAY_STATIC
            }:
                default_output, default_reference = _get_default_case_outputs(
                    spec, task_case,
                    results, default_outputs,
                    force_eval=effective_force_eval,
                )
                if spec.method != ExperimentMethod.DEFAULT:
                    output = run_replay_case(
                        spec, task_case,
                        default_output, default_reference,
                        force_eval=effective_force_eval,
                    )
                    results.append(_case_result_from_output(spec, task_case, output, default_reference))
            elif spec.method == ExperimentMethod.DYNSTEER_EVALUATE:
                output = run_evaluate_case(spec, task_case, force_eval=effective_force_eval)
                results.append(_case_result_from_output(spec, task_case, output))
            elif spec.method == ExperimentMethod.DYNSTEER_GUIDANCE:
                output = run_guidance_case(spec, task_case)
                results.append(_case_result_from_output(spec, task_case, output))
    if results and not no_sum:
        output_dir = specs[0].results_dir
        write_experiment_index(results, output_dir)
        write_metric_tables(results, output_dir)
    return results


def _get_default_case_outputs(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    results: list[ExperimentCaseResult],
    outputs: dict[tuple[str, str, int, str], tuple[HarnessEvaluationOutput, JsonObject]],
    force_eval: bool = False,
) -> tuple[HarnessEvaluationOutput, JsonObject]:
    key = (spec.benchmark, spec.model_id, spec.repeat_index, task_case.case_id)
    cached = outputs.get(key)
    if cached is None:
        run_spec = spec if spec.method == ExperimentMethod.DEFAULT else replace(spec, method=ExperimentMethod.DEFAULT)
        output, reference = run_default_case(run_spec, task_case, force_eval=force_eval)
        outputs[key] = (output, reference)
        results.append(_case_result_from_output(run_spec, task_case, output, reference))
    else:
        output, reference = cached
    return output, reference


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
        task_case.initial_state = runtime_initial_state
        task_case.metadata["runtime_initial_state_source"] = "default_trajectory"
        task_case.metadata["runtime_initial_state_summary"] = state_namespace_summary(runtime_initial_state)
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
    """调用 DynSTEEREvaluator.evaluate() 执行在线动态评估。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
    return write_case_outputs(
        config=config,
        harness=harness,
        evaluator=evaluator,
        task_case=task_case,
        force_eval=force_eval,
    )


def run_guidance_case(spec: ExperimentRunSpec, task_case: TaskCase) -> HarnessEvaluationOutput:
    """动态 guidance 分支实验入口。"""
    raise NotImplementedError("DynSTEER guidance 注入 hook 尚未实现，请等待分实验阶段补齐")


def write_experiment_index(results: list[ExperimentCaseResult], output_dir: Path) -> None:
    """写出所有 case 的结构化索引。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _build_experiment_index_payload(results)
    (output_dir / "index.json").write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=4), encoding="utf-8")


def _build_experiment_index_payload(results: list[ExperimentCaseResult]) -> JsonObject:
    """合并实验结果，构建 index.json 的分层结构。"""
    if not results:
        return {"experiment_id": None, "case_count": 0, "results": {}}
    experiment_id = results[0].experiment_id
    grouped: dict[str, dict[str, dict[str, JsonObject]]] = {}
    for result in sorted(results, key=lambda item: (item.benchmark, item.method.value, item.model_id, item.repeat_index, item.case_id)):
        if result.experiment_id != experiment_id:
            raise ValueError("results 不能同时包含不同 experiment_id")
        benchmark_bucket = grouped.setdefault(result.benchmark, {})
        method_bucket = benchmark_bucket.setdefault(result.method.value, {})
        model_bucket = method_bucket.setdefault(result.model_id, {"repeats": {}})
        repeats = model_bucket.setdefault("repeats", {})
        if not isinstance(repeats, dict):
            raise ValueError("repeats 类型异常")

        repeat_key = str(result.repeat_index)
        repeat_node = repeats.setdefault(repeat_key, {"cases": {}})
        cases = repeat_node.setdefault("cases", {})
        if not isinstance(cases, dict):
            raise ValueError("cases 类型异常")
        if result.case_id in cases:
            raise ValueError("同一 repeat 和 case_id 下的结果不能重复写入")
        cases[result.case_id] = result.to_index_dict()

    ordered_results: JsonObject = {}
    for benchmark, benchmark_bucket in grouped.items():
        ordered_methods: JsonObject = {}
        for method, method_bucket in benchmark_bucket.items():
            ordered_models: JsonObject = {}
            for model_id, model_bucket in method_bucket.items():
                repeats = model_bucket.get("repeats") if isinstance(model_bucket, dict) else {}
                ordered_repeats: JsonObject = {}
                if isinstance(repeats, dict):
                    for repeat_index, repeat_node in sorted(repeats.items(), key=lambda item: int(item[0])):
                        cases = repeat_node.get("cases") if isinstance(repeat_node, dict) else {}
                        ordered_repeats[repeat_index] = {
                            "cases": dict(cases.items()) if isinstance(cases, dict) else {}
                        }
                ordered_models[model_id] = {"repeats": ordered_repeats}
            ordered_methods[method] = ordered_models
        ordered_results[benchmark] = ordered_methods

    return {"experiment_id": experiment_id, "case_count": len(results), "results": ordered_results}


def _case_result_from_output(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    output: HarnessEvaluationOutput,
    default_reference: JsonObject | None = None,
) -> ExperimentCaseResult:
    """从输出摘要构造 ExperimentCaseResult。"""
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
        benchmark=spec.benchmark, case_id=task_case.case_id,
        model_id=spec.model_id, repeat_index=spec.repeat_index, method=spec.method,
        default_score=default_score, dynsteer_score=dynsteer_score,
        resolved=resolved if isinstance(resolved, bool) else None,
        runtime_metrics=dict(runtime_metrics),
        output_paths={
            k: str(v) for k, v in asdict(output).items()
            if k in ["result_dir", "raw_run_dir", "summary_path", "report_path", "trajectory_path"]
        },
        raw={"summary_metadata": dict(metadata), "default_reference": dict(default_reference or {})},
    )
