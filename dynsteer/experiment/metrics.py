from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod
from dynsteer.model import JsonObject
from dynsteer.utils import clamp, json_safe


def case_score(value: object) -> float:
    """将 benchmark 原生结果归一到 [0, 1]。

    入参：
        value: bool、数字或包含 score/resolved/similarity 字段的 JSON 对象。
    输出：
        归一化分数。
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return clamp(float(value))
    if isinstance(value, Mapping):
        for key in ("score", "similarity", "milestone_similarity"):
            if key in value:
                return case_score(value[key])
        if "resolved" in value:
            return case_score(value["resolved"])
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
        if score is None:
            continue
        buckets[(result.method.value, result.benchmark, result.model_id)].append(case_score(score))
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


def psep(scores: Mapping[str, float]) -> float:
    """计算模型对平均得分间距 PSEP。"""
    if scores is None or not scores:
        raise ValueError("scores 不能为空")
    values = [case_score(value) for value in scores.values()]
    if len(values) < 2:
        return 0.0
    pair_count = 0
    total = 0.0
    for left_index, left_value in enumerate(values):
        for right_value in values[left_index + 1:]:
            total += abs(left_value - right_value)
            pair_count += 1
    return total / pair_count


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
    concordant = 0
    discordant = 0
    left_ties = 0
    right_ties = 0
    for left_index, first_key in enumerate(keys):
        for second_key in keys[left_index + 1:]:
            left_delta = _compare_score(left[first_key], left[second_key])
            right_delta = _compare_score(right[first_key], right[second_key])
            if left_delta == 0 and right_delta == 0:
                continue
            if left_delta == 0:
                left_ties += 1
                continue
            if right_delta == 0:
                right_ties += 1
                continue
            if left_delta == right_delta:
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt((concordant + discordant + left_ties) * (concordant + discordant + right_ties))
    if denominator == 0:
        return 0.0
    return (concordant - discordant) / denominator


def aggregate_efficiency(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总评估耗时与 Agent 步骤数。"""
    if results is None:
        raise ValueError("results 不能为空")
    elapsed_values: list[float] = []
    step_values: list[int] = []
    raw_step_values: list[int] = []
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
    return {
        "case_count": len(results),
        "average_elapsed_seconds": _average(elapsed_values),
        "average_agent_step_count": _average(step_values),
        "average_raw_step_count": _average(raw_step_values),
    }


def aggregate_cost(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总 Agent token 与 Judge token。"""
    if results is None:
        raise ValueError("results 不能为空")
    trajectory_tokens = 0
    llm_tokens = 0
    trajectory_available = False
    llm_available = False
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
        "trajectory_total_tokens": trajectory_tokens,
        "trajectory_cost_available": trajectory_available,
        "llm_total_tokens": llm_tokens,
        "llm_cost_available": llm_available,
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
        "psep": _psep_table(scores),
        "rank_tau": _rank_tau_table(scores),
    }
    (output_dir / "scores.json").write_text(json.dumps(json_safe(scores), ensure_ascii=False, indent=4), encoding="utf-8")
    (output_dir / "metrics.json").write_text(json.dumps(json_safe(metrics), ensure_ascii=False, indent=4), encoding="utf-8")
    return {"scores": scores, "metrics": metrics}


def _compare_score(left: float, right: float) -> int:
    left_score = case_score(left)
    right_score = case_score(right)
    if abs(left_score - right_score) < 1e-12:
        return 0
    return 1 if left_score > right_score else -1


def _average(values: Sequence[int | float]) -> float:
    if not values:
        return 0.0
    return float(sum(values)) / len(values)


def _psep_table(scores: JsonObject) -> JsonObject:
    result: JsonObject = {}
    for method, method_scores in scores.items():
        if not isinstance(method_scores, dict):
            continue
        result[method] = {}
        for benchmark, benchmark_scores in method_scores.items():
            if isinstance(benchmark_scores, dict) and benchmark_scores:
                result[method][benchmark] = psep({str(key): float(value) for key, value in benchmark_scores.items()})
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
                    {str(key): float(value) for key, value in benchmark_scores.items()},
                )
    return result
