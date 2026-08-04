from __future__ import annotations
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod
from dynsteer.model import JsonObject
from dynsteer.utils import clamp, json_safe

DISCRIMINABILITY_THRESHOLDS = (0.01, 0.02, 0.03, 0.04, 0.05)

def case_score(value: object) -> float:
    """将 benchmark 原生结果归一到 [0, 1]。"""
    if isinstance(value, int | float | bool):
        return clamp(float(value))
    if isinstance(value, Mapping):
        for key in ("score", "similarity", "milestone_similarity", "resolved"):
            if key in value:
                return case_score(value[key])
    raise ValueError("无法从输入中读取 case score")

def model_scores(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """计算 S_{m,e,b} 模型级平均分。

    入参：
        results: case 级实验结果列表。
    输出：
        method -> benchmark -> model_id -> average_score 的嵌套 JSON。
    """
    if results is None:
        raise ValueError("results 不能为空")
    buckets: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for result in results:
        if result is None:
            raise ValueError("results 不能包含空结果")
        score = result.score
        if score is not None:
            buckets[result.method.value, result.benchmark, result.model_id].append(case_score(score))
    scores: JsonObject = {}
    for (method, benchmark, model_id), values in sorted(buckets.items()):
        method_scores = scores.setdefault(method, {})
        if not isinstance(method_scores, dict):
            raise ValueError("method scores 类型异常")
        benchmark_scores = method_scores.setdefault(benchmark, {})
        if not isinstance(benchmark_scores, dict):
            raise ValueError("benchmark scores 类型异常")
        benchmark_scores[model_id] = sum(values) / len(values)
    return scores

def discriminability_score(scores: Mapping[str, float], epsilon: float) -> JsonObject:
    """按模型总体离散度和显著模型对比例计算 Discriminability Score。"""
    if scores is None:
        raise ValueError("scores 不能为空")
    if epsilon < 0:
        raise ValueError("epsilon 不能为负数")
    values = [case_score(value) for value in scores.values()]
    model_count = len(values)
    pair_count = model_count * (model_count - 1) // 2
    mean_score = sum(values) / model_count if model_count else 0.0
    population_stddev = (
        math.sqrt(sum((value - mean_score) ** 2 for value in values) / model_count)
        if model_count else 0.0
    )
    significant_pair_count = sum(
        1
        for left_index, left_value in enumerate(values)
        for right_value in values[left_index + 1:]
        if abs(left_value - right_value) > epsilon
    )
    significant_pair_ratio = significant_pair_count / pair_count if pair_count else 0.0
    score = None
    if model_count >= 2:
        score = 0.0 if mean_score == 0 else (
            population_stddev / mean_score
        ) * math.sqrt(significant_pair_ratio)
    model_pairs = [
        {"left_model": left, "right_model": right, "absolute_difference": abs(case_score(scores[left]) - case_score(scores[right])), "significant": abs(case_score(scores[left]) - case_score(scores[right])) > epsilon}
        for index, left in enumerate(sorted(scores))
        for right in sorted(scores)[index + 1:]
    ]
    return {
        "epsilon": epsilon,
        "model_count": model_count,
        "pair_count": pair_count,
        "mean_score": mean_score,
        "population_stddev": population_stddev,
        "significant_pair_count": significant_pair_count,
        "significant_pair_ratio": significant_pair_ratio,
        "score": score,
        "model_pairs": model_pairs,
    }

def rank_tau_by_repeat(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """仅在相同 repeat 内配对 DEFAULT 与 replay 的模型排名。"""
    grouped: dict[tuple[str, str, int, str], dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for result in results:
        if result.score is not None:
            grouped[(result.method.value, result.benchmark, result.repeat_index, result.model_id)][result.method.value][result.case_id] = case_score(result.score)
    output: JsonObject = {}
    for (method, benchmark, repeat, model), _ in grouped.items():
        if method == ExperimentMethod.DEFAULT.value:
            continue
        left = {r.case_id: case_score(r.score) for r in results if r.method == ExperimentMethod.DEFAULT and r.benchmark == benchmark and r.model_id == model and r.repeat_index == repeat and r.score is not None}
        right = {r.case_id: case_score(r.score) for r in results if r.method.value == method and r.benchmark == benchmark and r.model_id == model and r.repeat_index == repeat and r.score is not None}
        output.setdefault(method, {}).setdefault(benchmark, {})[str(repeat)] = kendall_tau(left, right)
    return output


def success_consistency(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """按 case identity 配对 DEFAULT 与各 replay method 的成功结论。"""
    if results is None:
        raise ValueError("results 不能为空")
    default_results = {
        (result.benchmark, result.model_id, result.case_id, result.repeat_index): result
        for result in results
        if result.method == ExperimentMethod.DEFAULT
    }
    buckets: dict[tuple[str, str], list[tuple[ExperimentCaseResult, ExperimentCaseResult]]] = defaultdict(list)
    for result in results:
        if not result.method.value.startswith("dynsteer_replay"):
            continue
        identity = (result.benchmark, result.model_id, result.case_id, result.repeat_index)
        default_result = default_results.get(identity)
        if default_result is None or default_result.successful is None or result.successful is None:
            continue
        buckets[result.method.value, result.benchmark].append((default_result, result))
    table: JsonObject = {}
    for (method, benchmark), pairs in sorted(buckets.items()):
        inconsistent = [(default, replay) for default, replay in pairs if default.successful != replay.successful]
        default_success_replay_failure = sum(
            1 for default, replay in inconsistent if default.successful is True and replay.successful is False
        )
        default_failure_replay_success = sum(
            1 for default, replay in inconsistent if default.successful is False and replay.successful is True
        )
        method_table = table.setdefault(method, {})
        method_table[benchmark] = {
            "pair_count": len(pairs),
            "consistent_count": len(pairs) - len(inconsistent),
            "inconsistent_count": len(inconsistent),
            "agreement_rate": (len(pairs) - len(inconsistent)) / len(pairs),
            "default_success_replay_failure_count": default_success_replay_failure,
            "default_failure_replay_success_count": default_failure_replay_success,
            "inconsistent_cases": [
                {
                    "model": default.model_id,
                    "repeat": default.repeat_index,
                    "case": default.case_id,
                    "default_success": default.successful,
                    "replay_success": replay.successful,
                    "default_score": default.score,
                    "replay_score": replay.score,
                }
                for default, replay in inconsistent
            ],
        }
    return table

def kendall_tau(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """计算带并列处理的 Kendall tau-b。

    入参：
        left: 第一组模型得分。
        right: 第二组模型得分。
    输出：
        [-1, 1] 区间内的排序相关系数；有效模型不足两个时返回 0。
    """
    if left is None or right is None:
        raise ValueError("left 和 right 不能为空")
    keys = sorted(set(left) & set(right))
    if len(keys) < 2:
        return 0.0
    concordant, discordant = 0, 0
    left_ties, right_ties = 0, 0
    for left_index, first_key in enumerate(keys):
        for second_key in keys[left_index + 1:]:
            left_delta = _compare_score(left[first_key], left[second_key])
            right_delta = _compare_score(right[first_key], right[second_key])
            if left_delta == 0 or right_delta == 0:
                if left_delta != 0:
                    right_ties += 1
                elif right_delta != 0:
                    left_ties += 1
            else:
                if left_delta == right_delta:
                    concordant += 1
                else:
                    discordant += 1
    denominator = math.sqrt((concordant + discordant + left_ties) * (concordant + discordant + right_ties))
    if denominator == 0:
        return 0.0
    return (concordant - discordant) / denominator

def repeat_statistics(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """按 repeat/case 聚合分数与成功率，并计算总体标准差。"""
    groups: dict[tuple[str, str, str], dict[int, list[ExperimentCaseResult]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        groups[(result.method.value, result.benchmark, result.model_id)][result.repeat_index].append(result)
    output: JsonObject = {}
    for (method, benchmark, model), repeats in sorted(groups.items()):
        repeat_scores, repeat_rates = {}, {}
        for repeat, items in sorted(repeats.items()):
            scores = [case_score(item.score) for item in items if item.score is not None]
            successes = [item.successful for item in items if item.successful is not None]
            repeat_scores[str(repeat)] = _average(scores)
            repeat_rates[str(repeat)] = _average([1.0 if value else 0.0 for value in successes])
        output.setdefault(method, {}).setdefault(benchmark, {})[model] = {
            "repeat_count": len(repeats), "repeat_scores": repeat_scores,
            "mean_score": _average(list(repeat_scores.values())),
            "population_stddev": _population_stddev(list(repeat_scores.values())),
            "repeat_success_rates": repeat_rates,
            "mean_success_rate": _average(list(repeat_rates.values())),
            "success_rate_stddev": _population_stddev(list(repeat_rates.values())),
        }
    return output

def _population_stddev(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

def aggregate_efficiency(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总评估耗时与 Agent 步骤数。"""
    if results is None:
        raise ValueError("results 不能为空")
    elapsed_values: list[float] = []
    step_values: list[int] = []
    raw_step_values: list[int] = []
    default_prefix_values: list[float] = []
    effective_elapsed_values: list[float] = []
    timing_available_case_count = 0
    for result in results:
        metrics = result.runtime_metrics
        elapsed = metrics.get("elapsed_seconds")
        if isinstance(elapsed, int | float):
            elapsed_values.append(float(elapsed))
        step_count = metrics.get("step_count")
        if isinstance(step_count, int):
            step_values.append(step_count)
        raw_step_count = metrics.get("raw_step_count")
        if isinstance(raw_step_count, int):
            raw_step_values.append(raw_step_count)
        if result.method.value.startswith("dynsteer_replay") and metrics.get("timing_available") is True:
            timing_available_case_count += 1
            prefix = metrics.get("default_prefix_execution_seconds")
            effective = metrics.get("effective_elapsed_seconds")
            if isinstance(prefix, (int, float)):
                default_prefix_values.append(float(prefix))
            if isinstance(effective, (int, float)):
                effective_elapsed_values.append(float(effective))
    return {
        "case_count": len(results), 
        "average_elapsed_seconds": _average(elapsed_values), 
        "average_agent_step_count": _average(step_values), 
        "average_raw_step_count": _average(raw_step_values),
        "average_default_prefix_execution_seconds": _average(default_prefix_values),
        "average_effective_elapsed_seconds": _average(effective_elapsed_values),
        "effective_timing_available_case_count": timing_available_case_count,
    }

def aggregate_cost(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总 Agent token 与 Judge token。"""
    if results is None:
        raise ValueError("results 不能为空")
    trajectory_tokens, llm_tokens = 0, 0
    trajectory_available, llm_available = False, False
    for result in results:
        metrics = result.runtime_metrics
        raw_trajectory_tokens = metrics.get("trajectory_total_tokens")
        if isinstance(raw_trajectory_tokens, int):
            trajectory_tokens += raw_trajectory_tokens
            trajectory_available = trajectory_available or bool(metrics.get("trajectory_cost_available"))
        raw_llm_tokens = metrics.get("llm_total_tokens")
        if isinstance(raw_llm_tokens, int):
            llm_tokens += raw_llm_tokens
            llm_available = True
    return {
        "trajectory_total_tokens": trajectory_tokens, "trajectory_cost_available": trajectory_available, 
        "llm_total_tokens": llm_tokens, "llm_cost_available": llm_available
    }

def write_metric_tables(results: Sequence[ExperimentCaseResult], output_dir: Path) -> JsonObject:
    """写出 scores.json 与 metrics.json。"""
    if results is None or output_dir is None:
        raise ValueError("results 和 output_dir 不能为空")
    output_dir.mkdir(parents=True, exist_ok=True)
    scores = model_scores(results)
    metrics: JsonObject = {
        "efficiency": aggregate_efficiency(results),
        "cost": aggregate_cost(results),
        "discriminability_score": _discriminability_table(scores),
        "rank_tau": _rank_tau_table(scores),
        "success_consistency": success_consistency(results),
        "repeat_statistics": repeat_statistics(results),
        "rank_tau_by_repeat": rank_tau_by_repeat(results),
    }
    (output_dir / "scores.json").write_text(json.dumps(json_safe(scores), ensure_ascii=False, indent=4), encoding="utf-8")
    (output_dir / "metrics.json").write_text(json.dumps(json_safe(metrics), ensure_ascii=False, indent=4), encoding="utf-8")
    return {"scores": scores, "metrics": metrics}

def _compare_score(left: float, right: float) -> int:
    left_score, right_score = case_score(left), case_score(right)
    if abs(left_score - right_score) < 1e-12:
        return 0
    return 1 if left_score > right_score else -1

def _average(values: Sequence[int | float]) -> float:
    return float(sum(values)) / len(values) if values else 0.0

def _discriminability_table(scores: JsonObject) -> JsonObject:
    result: JsonObject = {}
    for method, method_scores in scores.items():
        if not isinstance(method_scores, dict):
            continue
        result[method] = {}
        for benchmark, benchmark_scores in method_scores.items():
            if isinstance(benchmark_scores, dict):
                model_values = {str(key): float(value) for key, value in benchmark_scores.items()}
                result[method][benchmark] = {
                    f"{epsilon:.2f}": discriminability_score(model_values, epsilon)
                    for epsilon in DISCRIMINABILITY_THRESHOLDS
                }
    return result

def _rank_tau_table(scores: JsonObject) -> JsonObject:
    default_by_benchmark = scores.get(ExperimentMethod.DEFAULT.value)
    if not isinstance(default_by_benchmark, dict):
        return {}
    result: JsonObject = {}
    for method, method_scores in scores.items():
        if method == ExperimentMethod.DEFAULT.value or not isinstance(method_scores, dict):
            continue
        result[method] = {}
        for benchmark, benchmark_scores in method_scores.items():
            default_scores = default_by_benchmark.get(benchmark)
            if isinstance(default_scores, dict) and isinstance(benchmark_scores, dict):
                result[method][benchmark] = kendall_tau(
                    {str(key): float(value) for key, value in default_scores.items()}, 
                    {str(key): float(value) for key, value in benchmark_scores.items()}
                )
    return result
