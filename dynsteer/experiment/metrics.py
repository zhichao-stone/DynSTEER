from __future__ import annotations
import json
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Callable, Mapping, Sequence
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
    scores: JsonObject = {}
    averages = _group_average(results, lambda result: (result.method.value, result.benchmark, result.model_id), lambda result: case_score(result.score) if result.score is not None else None)
    for (method, benchmark, model_id), average in sorted(averages.items()):
        method_scores = scores.setdefault(method, {})
        if not isinstance(method_scores, dict):
            raise ValueError("method scores 类型异常")
        benchmark_scores = method_scores.setdefault(benchmark, {})
        if not isinstance(benchmark_scores, dict):
            raise ValueError("benchmark scores 类型异常")
        benchmark_scores[model_id] = average
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
    output: dict[tuple[str, str, int], dict[str, float]] = defaultdict(dict)
    averages = _group_average(results, lambda result: (result.method.value, result.benchmark, result.repeat_index, result.model_id), lambda result: _finite_case_score(result.score))
    for (method, benchmark, repeat, model), average in sorted(averages.items()):
        output[method, benchmark, repeat][model] = average
    return dict(output)


def _group_average(results: Sequence[ExperimentCaseResult], key: Callable[[ExperimentCaseResult], tuple[object, ...]], score_getter: Callable[[ExperimentCaseResult], float | None]) -> dict[tuple[object, ...], float]:
    if results is None:
        raise ValueError("results 不能为空")
    buckets: dict[tuple[object, ...], list[float]] = defaultdict(list)
    for result in results:
        if result is None:
            raise ValueError("results 不能包含空结果")
        score = score_getter(result)
        if score is not None:
            buckets[key(result)].append(score)
    return {group: sum(values) / len(values) for group, values in buckets.items()}

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
    ordered_models = sorted(normalized)
    model_pairs = [
        {"left_model": left, "right_model": right, "absolute_difference": abs(normalized[left] - normalized[right]), "significant": abs(normalized[left] - normalized[right]) > epsilon}
        for index, left in enumerate(ordered_models)
        for right in ordered_models[index + 1:]
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
    """按 method 汇总评估耗时与 Agent 步骤数。"""
    if results is None:
        raise ValueError("results 不能为空")
    buckets: dict[str, list[ExperimentCaseResult]] = defaultdict(list)
    for result in results:
        buckets[result.method.value].append(result)
    return {"by_method": {method: _aggregate_efficiency_rows(items) for method, items in sorted(buckets.items())}}


def _aggregate_efficiency_rows(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """汇总单个 method 的原始效率诊断字段。"""
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

def aggregate_cost(results: Sequence[ExperimentCaseResult]) -> tuple[JsonObject, JsonObject]:
    """严格配对 DEFAULT 与 DynSTEER，生成成本摘要和逐配对明细。"""
    if results is None:
        raise ValueError("results 不能为空")
    defaults = {
        _pair_key(item): item for item in results if item.method == ExperimentMethod.DEFAULT
    }
    rows: list[JsonObject] = []
    quality: dict[str, int] = defaultdict(int)
    for item in results:
        if item.method == ExperimentMethod.DEFAULT:
            continue
        default = defaults.get(_pair_key(item))
        if default is None:
            quality["missing_default"] += 1
            continue
        row = _paired_cost_row(default, item, quality)
        if not isinstance(row["adaptation_cost"].get("artifact_id"), str):
            quality["adaptation_cost_unavailable"] += 1
            row["dynsteer_cost"]["pipeline_time_seconds"] = None
            row["dynsteer_cost"]["pipeline_tokens"] = None
            row["standalone_method_adaptation_allocation"] = {"time_seconds": None, "tokens": None}
            row["experiment_amortized_adaptation_allocation"] = {"time_seconds": None, "tokens": None}
            row["overhead_delta"] = {"time_seconds": None, "tokens": None}
            row["pipeline_delta"] = {"time_seconds": None, "tokens": None}
        rows.append(row)
    ledger = _allocate_adaptation(rows)
    scopes = {
        "full": rows,
        "early_stop_live": [row for row in rows if row["stop"]["category"] == "agent_underperformance" and row["stop"]["mode"] == "live"],
        "early_stop_virtual": [row for row in rows if row["stop"]["category"] == "agent_underperformance" and row["stop"]["mode"] == "virtual"],
    }
    summary: JsonObject = {
        name: {"by_method": _aggregate_rows_by_method(scope_rows)}
        for name, scope_rows in scopes.items()
    }
    by_case: JsonObject = {}
    for row in rows:
        identity = row["identity"]
        case_bucket = by_case.setdefault(identity["benchmark"], {}).setdefault(identity["case_id"], {})
        case_bucket.setdefault(identity["method"], []).append(row)
    summary["by_case"] = {
        benchmark: {
            case_id: {method: _aggregate_rows(items) for method, items in methods.items()}
            for case_id, methods in cases.items()
        }
        for benchmark, cases in by_case.items()
    }
    summary["adaptation_actual_run"] = _sum_ledger(ledger, generated_only=True)
    summary["adaptation_attributed_artifacts"] = _sum_ledger(ledger, generated_only=False)
    summary["data_quality"] = dict(sorted(quality.items()))
    detail = {
        "pairing_key": ["benchmark", "model_id", "repeat_index", "case_id"],
        "adaptation_ledger": ledger,
        "paired_cases": rows,
        "data_quality": dict(sorted(quality.items())),
    }
    return summary, detail

def write_metric_tables(results: Sequence[ExperimentCaseResult], output_dir: Path) -> JsonObject:
    """写出 scores.json 与 metrics.json。"""
    if results is None or output_dir is None:
        raise ValueError("results 和 output_dir 不能为空")
    output_dir.mkdir(parents=True, exist_ok=True)
    scores = model_scores(results)
    counts = categorical_counts(results)
    cost_analysis, cost_detail = aggregate_cost(results)
    metrics: JsonObject = {
        "efficiency": aggregate_efficiency(results),
        "cost_analysis": {**cost_analysis, "detail_path": "costs.json"},
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
    (output_dir / "costs.json").write_text(json.dumps(json_safe(cost_detail), ensure_ascii=False, indent=4), encoding="utf-8")
    return {"scores": scores, "metrics": metrics}


def _pair_key(result: ExperimentCaseResult) -> tuple[str, str, int, str]:
    return result.benchmark, result.model_id, result.repeat_index, result.case_id


def _paired_cost_row(default: ExperimentCaseResult, item: ExperimentCaseResult, quality: dict[str, int]) -> JsonObject:
    default_cost = _result_cost(default, quality)
    dynsteer_cost = _result_cost(item, quality)
    stop = _stop_detail(default, item, quality)
    row: JsonObject = {
        "identity": {
            "benchmark": item.benchmark, "model_id": item.model_id,
            "repeat_index": item.repeat_index, "case_id": item.case_id,
            "method": item.method.value,
        },
        "stop": stop,
        "default_cost": default_cost,
        "dynsteer_cost": dynsteer_cost,
        "adaptation_cost": dict(item.adaptation_cost),
        "adaptation_usage": dict(item.adaptation_usage),
        "evidence_paths": {"default": default.output_paths, "dynsteer": item.output_paths},
    }
    row["overhead_delta"] = _cost_delta(default_cost, dynsteer_cost, overhead=True)
    row["pipeline_delta"] = _cost_delta(default_cost, dynsteer_cost, overhead=False)
    return row


def _result_cost(result: ExperimentCaseResult, quality: dict[str, int]) -> JsonObject:
    metrics = result.runtime_metrics
    replay = result.method.value.startswith("dynsteer_replay")
    agent_time = _number(metrics.get("default_prefix_execution_seconds" if replay else "execution_total_seconds"))
    elapsed = _number(metrics.get("elapsed_seconds"))
    if replay:
        evaluation_time = elapsed
    elif elapsed is not None and agent_time is not None:
        difference = elapsed - agent_time
        if difference < -0.001:
            quality["negative_evaluation_time"] += 1
            evaluation_time = None
        else:
            evaluation_time = max(difference, 0.0)
    else:
        evaluation_time = None
    agent_tokens_key = "default_prefix_trajectory_tokens" if replay else "trajectory_total_tokens"
    token_available_key = "prefix_token_available" if replay else "trajectory_cost_available"
    agent_tokens = metrics.get(agent_tokens_key) if metrics.get(token_available_key) is True else None
    if result.method == ExperimentMethod.DEFAULT:
        evaluation_tokens = metrics.get("native_evaluation_total_tokens") if metrics.get("native_evaluation_token_available") is True else None
    else:
        calls = metrics.get("llm_call_count")
        evaluation_tokens = metrics.get("llm_total_tokens")
        if calls == 0:
            evaluation_tokens = 0
        elif not isinstance(evaluation_tokens, int):
            evaluation_tokens = None
    pipeline_time = agent_time + evaluation_time if agent_time is not None and evaluation_time is not None else None
    pipeline_tokens = agent_tokens + evaluation_tokens if isinstance(agent_tokens, int) and isinstance(evaluation_tokens, int) else None
    return {
        "agent_time_seconds": agent_time, "adaptation_time_seconds": 0.0 if result.method == ExperimentMethod.DEFAULT else None,
        "evaluation_time_seconds": evaluation_time,
        "pipeline_time_seconds": pipeline_time, "agent_tokens": agent_tokens,
        "adaptation_tokens": 0 if result.method == ExperimentMethod.DEFAULT else None,
        "evaluation_tokens": evaluation_tokens, "pipeline_tokens": pipeline_tokens,
    }


def _stop_detail(default: ExperimentCaseResult, item: ExperimentCaseResult, quality: dict[str, int]) -> JsonObject:
    code = item.termination_code
    detail = item.termination_detail
    basis = str(detail.get("failure_basis") or "")
    if not code:
        category = mode = "none"
    elif code.startswith("minefield"):
        category, mode = "minefield", _stop_mode(item)
    elif code.startswith(("stage_failure", "stage_score", "milestone_predecessor_gap", "ready_frontier")) or basis in {
        "milestone_predecessor_gap", "ready_frontier_no_progress", "structural_failure"
    }:
        category, mode = "agent_underperformance", _stop_mode(item)
    else:
        category, mode = "other", _stop_mode(item)
    stop_step = item.runtime_metrics.get("step_count")
    default_total = default.runtime_metrics.get("step_count")
    progress = None
    if isinstance(stop_step, int) and isinstance(default_total, int) and default_total > 0 and stop_step <= default_total:
        progress = stop_step / default_total
    elif code:
        quality["invalid_stop_progress"] += 1
    return {"category": category, "mode": mode, "code": code, "stop_step": stop_step,
            "default_total_step": default_total, "progress": progress}


def _stop_mode(item: ExperimentCaseResult) -> str:
    return "virtual" if item.method.value.startswith("dynsteer_replay") else "live"


def _allocate_adaptation(rows: list[JsonObject]) -> list[JsonObject]:
    artifacts: dict[str, list[JsonObject]] = defaultdict(list)
    for row in rows:
        cost = row["adaptation_cost"]
        artifact_id = cost.get("artifact_id") if isinstance(cost, dict) else None
        if isinstance(artifact_id, str) and artifact_id:
            artifacts[artifact_id].append(row)
    ledger: list[JsonObject] = []
    for artifact_id, consumers in sorted(artifacts.items()):
        cost = consumers[0]["adaptation_cost"]
        method_counts: dict[str, int] = defaultdict(int)
        for row in consumers:
            method_counts[row["identity"]["method"]] += 1
        for row in consumers:
            method = row["identity"]["method"]
            row["standalone_method_adaptation_allocation"] = _allocated(cost, method_counts[method])
            row["experiment_amortized_adaptation_allocation"] = _allocated(cost, len(consumers))
            _apply_adaptation_to_deltas(row)
        ledger.append({
            "artifact_id": artifact_id, "cost": cost, "consumer_count": len(consumers),
            "generated_now": any(row["adaptation_usage"].get("generated_now") is True for row in consumers),
            "cache_hit_count": sum(row["adaptation_usage"].get("cache_hit") is True for row in consumers),
        })
    return ledger


def _allocated(cost: JsonObject, denominator: int) -> JsonObject:
    seconds = _number(cost.get("elapsed_seconds"))
    tokens = cost.get("total_tokens") if cost.get("token_available") is True else None
    return {"time_seconds": seconds / denominator if seconds is not None else None,
            "tokens": tokens / denominator if isinstance(tokens, int) else None}


def _apply_adaptation_to_deltas(row: JsonObject) -> None:
    allocation = row["experiment_amortized_adaptation_allocation"]
    dyn = row["dynsteer_cost"]
    default = row["default_cost"]
    dyn["adaptation_time_seconds"] = allocation.get("time_seconds")
    dyn["adaptation_tokens"] = allocation.get("tokens")
    for unit, cost_key, allocation_key in (("time_seconds", "evaluation_time_seconds", "time_seconds"), ("tokens", "evaluation_tokens", "tokens")):
        adaptation = allocation.get(allocation_key)
        dyn_evaluation = dyn.get(cost_key)
        default_evaluation = default.get(cost_key)
        if all(isinstance(value, (int, float)) for value in (adaptation, dyn_evaluation, default_evaluation)):
            row["overhead_delta"][unit] = adaptation + dyn_evaluation - default_evaluation
        dyn_pipeline = dyn.get("pipeline_time_seconds" if unit == "time_seconds" else "pipeline_tokens")
        default_pipeline = default.get("pipeline_time_seconds" if unit == "time_seconds" else "pipeline_tokens")
        if all(isinstance(value, (int, float)) for value in (adaptation, dyn_pipeline, default_pipeline)):
            pipeline_key = "pipeline_time_seconds" if unit == "time_seconds" else "pipeline_tokens"
            dyn[pipeline_key] = adaptation + dyn_pipeline
            row["pipeline_delta"][unit] = dyn[pipeline_key] - default_pipeline


def _cost_delta(default: JsonObject, dynsteer: JsonObject, *, overhead: bool) -> JsonObject:
    if overhead:
        left_time, right_time = default.get("evaluation_time_seconds"), dynsteer.get("evaluation_time_seconds")
        left_tokens, right_tokens = default.get("evaluation_tokens"), dynsteer.get("evaluation_tokens")
    else:
        left_time, right_time = default.get("pipeline_time_seconds"), dynsteer.get("pipeline_time_seconds")
        left_tokens, right_tokens = default.get("pipeline_tokens"), dynsteer.get("pipeline_tokens")
    return {"time_seconds": right_time - left_time if _both_numbers(left_time, right_time) else None,
            "tokens": right_tokens - left_tokens if _both_numbers(left_tokens, right_tokens) else None}


def _aggregate_rows_by_method(rows: list[JsonObject]) -> JsonObject:
    buckets: dict[str, list[JsonObject]] = defaultdict(list)
    for row in rows:
        buckets[row["identity"]["method"]].append(row)
    return {method: _aggregate_rows(items) for method, items in sorted(buckets.items())}


def _aggregate_rows(rows: list[JsonObject]) -> JsonObject:
    time_deltas = [row["pipeline_delta"]["time_seconds"] for row in rows if _number(row["pipeline_delta"]["time_seconds"]) is not None]
    token_deltas = [row["pipeline_delta"]["tokens"] for row in rows if _number(row["pipeline_delta"]["tokens"]) is not None]
    overhead_time = [row["overhead_delta"]["time_seconds"] for row in rows if _number(row["overhead_delta"]["time_seconds"]) is not None]
    overhead_tokens = [row["overhead_delta"]["tokens"] for row in rows if _number(row["overhead_delta"]["tokens"]) is not None]
    progresses = [row["stop"]["progress"] for row in rows if _number(row["stop"]["progress"]) is not None]
    eligible = [row for row in rows if isinstance(row["dynsteer_cost"].get("agent_tokens"), int) and isinstance(row["dynsteer_cost"].get("evaluation_tokens"), int) and _number(row.get("experiment_amortized_adaptation_allocation", {}).get("tokens")) is not None]
    agent = sum(row["dynsteer_cost"]["agent_tokens"] for row in eligible)
    evaluation = sum(row["dynsteer_cost"]["evaluation_tokens"] for row in eligible)
    adaptation = sum(row["experiment_amortized_adaptation_allocation"]["tokens"] for row in eligible)
    denominator = agent + evaluation + adaptation
    return {
        "pair_count": len(rows), "time_available_pair_count": len(time_deltas),
        "token_available_pair_count": len(token_deltas),
        "pipeline_time_delta": _distribution(time_deltas),
        "pipeline_token_delta": _distribution(token_deltas),
        "overhead_time_delta": _distribution(overhead_time),
        "overhead_token_delta": _distribution(overhead_tokens),
        "default_totals": _component_totals(rows, "default_cost"),
        "dynsteer_totals": _component_totals(rows, "dynsteer_cost"),
        "progress": _distribution(progresses),
        "token_share_eligible_pair_count": len(eligible),
        "adaptation_token_share": adaptation / denominator if denominator else None,
        "evaluation_token_share": evaluation / denominator if denominator else None,
        "combined_overhead_token_share": (adaptation + evaluation) / denominator if denominator else None,
    }


def _component_totals(rows: list[JsonObject], key: str) -> JsonObject:
    fields = (
        "agent_time_seconds", "adaptation_time_seconds", "evaluation_time_seconds", "pipeline_time_seconds",
        "agent_tokens", "adaptation_tokens", "evaluation_tokens", "pipeline_tokens",
    )
    output: JsonObject = {}
    for field in fields:
        values = [row[key].get(field) for row in rows]
        available = [value for value in values if _number(value) is not None]
        output[field] = sum(available) if available else None
        output[f"{field}_available_count"] = len(available)
    return output


def _distribution(values: list[float]) -> JsonObject:
    ordered = sorted(float(value) for value in values)
    return {"count": len(ordered), "total": sum(ordered), "mean": _optional_average(ordered),
            "median": _percentile(ordered, 0.5), "p10": _percentile(ordered, 0.1),
            "p90": _percentile(ordered, 0.9), "p95": _percentile(ordered, 0.95)}


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * quantile
    lower, upper = math.floor(index), math.ceil(index)
    return values[lower] if lower == upper else values[lower] + (values[upper] - values[lower]) * (index - lower)


def _sum_ledger(ledger: list[JsonObject], *, generated_only: bool) -> JsonObject:
    selected = [item for item in ledger if not generated_only or item["generated_now"]]
    seconds = [_number(item["cost"].get("elapsed_seconds")) for item in selected]
    tokens = [item["cost"].get("total_tokens") for item in selected if item["cost"].get("token_available") is True]
    return {"artifact_count": len(selected), "elapsed_seconds": sum(value for value in seconds if value is not None),
            "total_tokens": sum(tokens) if len(tokens) == len(selected) else None}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _both_numbers(left: object, right: object) -> bool:
    return _number(left) is not None and _number(right) is not None

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
