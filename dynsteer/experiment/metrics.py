from __future__ import annotations
import json
import math
from collections import defaultdict
from itertools import combinations
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
        for key in ("score", "similarity", "milestone_similarity"):
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

def repeat_model_scores(
    results: Sequence[ExperimentCaseResult],
) -> dict[tuple[str, str, int], dict[str, float]]:
    """按方法、benchmark 和 repeat 汇总模型 case 均分。

    入参：
        results: case 级实验结果列表。
    输出：
        (method, benchmark, repeat_index) -> model_id -> average_score。
        缺失、无法解析或非有限的分数不进入均值，由一致性汇总标记对应 pair 无效。
    """
    if results is None:
        raise ValueError("results 不能为空")
    buckets: dict[tuple[str, str, int, str], list[float]] = defaultdict(list)
    for result in results:
        if result is None:
            raise ValueError("results 不能包含空结果")
        score = _finite_case_score(result.score)
        if score is not None:
            buckets[result.method.value, result.benchmark, result.repeat_index, result.model_id].append(score)

    output: dict[tuple[str, str, int], dict[str, float]] = defaultdict(dict)
    for (method, benchmark, repeat, model), values in sorted(buckets.items()):
        output[method, benchmark, repeat][model] = sum(values) / len(values)
    return dict(output)

def rank_models(model_scores: Mapping[str, float]) -> JsonObject:
    """按模型均分降序生成稳定展示顺序和并列平均名次。

    入参：
        model_scores: model_id -> average_score。
    输出：
        包含模型均分、展示顺序和 mid-rank 的 JSON 对象。
    """
    if model_scores is None:
        raise ValueError("model_scores 不能为空")
    scores = {str(model): float(score) for model, score in model_scores.items()}
    if any(not math.isfinite(score) for score in scores.values()):
        raise ValueError("model_scores 不能包含非有限分数")

    # 模型名只用于让 JSON 展示稳定；相同分数共享同一个 mid-rank。
    order = sorted(scores, key=lambda model: (-scores[model], model))
    ranks: dict[str, float] = {}
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and abs(scores[order[end]] - scores[order[index]]) < 1e-12:
            end += 1
        mid_rank = ((index + 1) + end) / 2
        for model in order[index:end]:
            ranks[model] = mid_rank
        index = end
    return {"model_scores": scores, "order": order, "ranks": ranks}

def repeat_rank_consistency(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """计算同一方法和 benchmark 内不同 repeat 的模型排名一致性。

    入参：
        results: case 级实验结果列表。
    输出：
        method -> benchmark 的 repeat 排名、pairwise 原子指标和汇总指标。
    """
    if results is None:
        raise ValueError("results 不能为空")
    scores = repeat_model_scores(results)
    repeats: dict[tuple[str, str], set[int]] = defaultdict(set)
    expected_models: dict[tuple[str, str], set[str]] = defaultdict(set)
    invalid_score_models: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for result in results:
        if result is None:
            raise ValueError("results 不能包含空结果")
        group = result.method.value, result.benchmark
        repeat = (*group, result.repeat_index)
        repeats[group].add(result.repeat_index)
        expected_models[group].add(result.model_id)
        if _finite_case_score(result.score) is None:
            invalid_score_models[repeat].add(result.model_id)

    output: JsonObject = {}
    for (method, benchmark), repeat_indices in sorted(repeats.items()):
        models = expected_models[method, benchmark]
        rankings = {
            str(repeat): rank_models(scores.get((method, benchmark, repeat), {}))
            for repeat in sorted(repeat_indices)
        }
        pairwise: list[JsonObject] = []
        invalid_pairs: list[JsonObject] = []
        for left_repeat, right_repeat in combinations(sorted(repeat_indices), 2):
            left_ranking = rankings[str(left_repeat)]
            right_ranking = rankings[str(right_repeat)]
            left_scores = left_ranking["model_scores"]
            right_scores = right_ranking["model_scores"]
            invalid_reason = _invalid_repeat_pair_reason(
                models,
                left_repeat,
                right_repeat,
                left_scores,
                right_scores,
                invalid_score_models.get((method, benchmark, left_repeat), set()),
                invalid_score_models.get((method, benchmark, right_repeat), set()),
            )
            tau = None if invalid_reason else _kendall_tau_b(left_scores, right_scores)
            if invalid_reason is None and tau is None:
                invalid_reason = "没有可用于 Kendall tau-b 的有效模型对"
            if invalid_reason is not None:
                invalid_pairs.append({
                    "left_repeat": left_repeat,
                    "right_repeat": right_repeat,
                    "invalid_reason": invalid_reason,
                })
                continue

            left_ranks = left_ranking["ranks"]
            right_ranks = right_ranking["ranks"]
            left_top = {model for model in models if left_ranks[model] == min(left_ranks.values())}
            right_top = {model for model in models if right_ranks[model] == min(right_ranks.values())}
            pairwise.append({
                "left_repeat": left_repeat,
                "right_repeat": right_repeat,
                "kendall_tau_b": tau,
                "exact_order_agreement": left_ranks == right_ranks,
                "top1_agreement": left_top == right_top,
                "mean_absolute_rank_displacement": _average([
                    abs(left_ranks[model] - right_ranks[model]) for model in models
                ]),
            })

        output.setdefault(method, {})[benchmark] = {
            "repeat_count": len(repeat_indices),
            "repeat_pair_count": len(repeat_indices) * (len(repeat_indices) - 1) // 2,
            "valid_pair_count": len(pairwise),
            "repeat_rankings": rankings,
            "pairwise": pairwise,
            "mean_pairwise_kendall_tau": _optional_average([item["kendall_tau_b"] for item in pairwise]),
            "exact_order_agreement_rate": _optional_average([
                int(item["exact_order_agreement"]) for item in pairwise
            ]),
            "top1_agreement_rate": _optional_average([int(item["top1_agreement"]) for item in pairwise]),
            "mean_pairwise_absolute_rank_displacement": _optional_average([
                item["mean_absolute_rank_displacement"] for item in pairwise
            ]),
            "invalid_pairs": invalid_pairs,
        }
    return output

def discriminability_score(scores: Mapping[str, float], epsilon: float) -> JsonObject:
    """按模型总体离散度和显著模型对比例计算 Discriminability Score。"""
    if scores is None:
        raise ValueError("scores 不能为空")
    if epsilon < 0:
        raise ValueError("epsilon 不能为负数")
    normalized = {model: case_score(value) for model, value in scores.items()}
    values = list(normalized.values())
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
        {"left_model": left, "right_model": right, "absolute_difference": abs(normalized[left] - normalized[right]), "significant": abs(normalized[left] - normalized[right]) > epsilon}
        for index, left in enumerate(sorted(normalized))
        for right in sorted(normalized)[index + 1:]
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
    grouped: dict[tuple[str, str, int], dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for result in results:
        if result.score is not None:
            grouped[(result.benchmark, result.model_id, result.repeat_index)][result.method.value][result.case_id] = case_score(result.score)
    output: JsonObject = {}
    for (benchmark, _model, repeat), methods in grouped.items():
        default_scores = methods.get(ExperimentMethod.DEFAULT.value, {})
        for method, scores in methods.items():
            if method != ExperimentMethod.DEFAULT.value:
                output.setdefault(method, {}).setdefault(benchmark, {})[str(repeat)] = kendall_tau(default_scores, scores)
    return output


def score_delta(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """按 case identity 计算 DEFAULT 与 replay 的连续分差。"""
    defaults = {
        (item.benchmark, item.model_id, item.case_id, item.repeat_index): item
        for item in results
        if item.method == ExperimentMethod.DEFAULT and item.score is not None
    }
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for item in results:
        if not item.method.value.startswith("dynsteer_replay") or item.score is None:
            continue
        default = defaults.get((item.benchmark, item.model_id, item.case_id, item.repeat_index))
        if default is not None and default.score is not None:
            buckets[item.method.value, item.benchmark].append(case_score(item.score) - case_score(default.score))
    return {
        method: {
            benchmark: {"pair_count": len(values), "mean_delta": _average(values)}
            for (current_method, benchmark), values in sorted(buckets.items())
            if current_method == method
        }
        for method in sorted({key[0] for key in buckets})
    }


def categorical_counts(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总 replay coverage、minefield 和结构化终止原因。"""
    coverage: dict[str, int] = defaultdict(int)
    minefield = {"hit": 0, "not_hit": 0, "unknown": 0}
    termination: dict[str, int] = defaultdict(int)
    for item in results:
        if item.milestone_coverage is not None:
            coverage[item.milestone_coverage] += 1
        if item.minefield_match_count is None:
            minefield["unknown"] += 1
        elif item.minefield_match_count > 0:
            minefield["hit"] += 1
        else:
            minefield["not_hit"] += 1
        termination[item.termination_code or "unknown"] += 1
    return {
        "coverage_counts": dict(sorted(coverage.items())),
        "minefield_counts": minefield,
        "termination_counts": dict(sorted(termination.items())),
    }

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
    return _kendall_tau_b(left, right) or 0.0

def repeat_statistics(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """按 repeat/case 聚合连续分，并计算总体标准差。"""
    groups: dict[tuple[str, str, str], dict[int, list[ExperimentCaseResult]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        groups[(result.method.value, result.benchmark, result.model_id)][result.repeat_index].append(result)
    output: JsonObject = {}
    for (method, benchmark, model), repeats in sorted(groups.items()):
        repeat_scores = {}
        for repeat, items in sorted(repeats.items()):
            scores = [case_score(item.score) for item in items if item.score is not None]
            repeat_scores[str(repeat)] = _average(scores)
        output.setdefault(method, {}).setdefault(benchmark, {})[model] = {
            "repeat_count": len(repeats), "repeat_scores": repeat_scores,
            "mean_score": _average(list(repeat_scores.values())),
            "population_stddev": _population_stddev(list(repeat_scores.values())),
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
    counts = categorical_counts(results)
    metrics: JsonObject = {
        "efficiency": aggregate_efficiency(results),
        "cost": aggregate_cost(results),
        "discriminability_score": _discriminability_table(scores),
        "score_rank_tau": _rank_tau_table(scores),
        "score_delta": score_delta(results),
        **counts,
        "repeat_statistics": repeat_statistics(results),
        "rank_tau_by_repeat": rank_tau_by_repeat(results),
        "repeat_rank_consistency": repeat_rank_consistency(results),
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

def _finite_case_score(value: object) -> float | None:
    """读取有限 case score，缺失、异常或非有限值返回空值。"""
    if value is None or (
        isinstance(value, int | float | bool) and not math.isfinite(float(value))
    ):
        return None
    try:
        score = case_score(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) else None

def _kendall_tau_b(left: Mapping[str, float], right: Mapping[str, float]) -> float | None:
    """计算 Kendall tau-b；没有有效模型对时返回空值。"""
    keys = sorted(set(left) & set(right))
    if len(keys) < 2:
        return None
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
            elif left_delta == right_delta:
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + left_ties)
        * (concordant + discordant + right_ties)
    )
    if denominator == 0:
        return None
    return (concordant - discordant) / denominator

def _optional_average(values: Sequence[int | float]) -> float | None:
    return float(sum(values)) / len(values) if values else None

def _invalid_repeat_pair_reason(
    expected_models: set[str],
    left_repeat: int,
    right_repeat: int,
    left_scores: Mapping[str, float],
    right_scores: Mapping[str, float],
    left_invalid_models: set[str],
    right_invalid_models: set[str],
) -> str | None:
    """返回 repeat pair 无效原因；有效时返回空值。"""
    reasons: list[str] = []
    if len(expected_models) < 2:
        reasons.append("有效模型不足两个")
    for repeat, repeat_scores, invalid_models in (
        (left_repeat, left_scores, left_invalid_models),
        (right_repeat, right_scores, right_invalid_models),
    ):
        missing = sorted(expected_models - set(repeat_scores))
        if missing:
            reasons.append(f"repeat {repeat} 缺少有效模型分数: {', '.join(missing)}")
        if invalid_models:
            reasons.append(f"repeat {repeat} 包含缺失或非有限 score: {', '.join(sorted(invalid_models))}")
    return "；".join(reasons) or None

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
