from __future__ import annotations
import json
import math
from collections import defaultdict
from itertools import combinations
import numpy as np
from pathlib import Path
from typing import Callable, Mapping, Sequence
from scipy import stats
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod
from dynsteer.model import JsonObject
from dynsteer.utils import clamp, json_safe

DISCRIMINABILITY_THRESHOLDS = (0.01, 0.02, 0.03, 0.04, 0.05)
INTERVENTION_METHODS = {
    ExperimentMethod.DEFAULT,
    ExperimentMethod.DYNSTEER_EVALUATE,
    ExperimentMethod.DYNSTEER_EVALUATE_GUIDED,
}
STRATUM_MIN_PAIRS = 3

def case_score(value: object) -> float:
    """To combine the native results of benchmark with [0,1]."""
    if isinstance(value, int | float | bool):
        return clamp(float(value))
    if isinstance(value, Mapping):
        for key in ("score", "similarity", "milestone_similarity"):
            if key in value:
                return case_score(value[key])
    raise ValueError('Could not read case score from input')

def model_scores(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Calculate model-level averages for ``S_{m,e,b}``.

    Args:
        results: Result-level experiment outputs.

    Returns:
        A mapping from ``(method, benchmark, model_id)`` to average score.
    """
    scores: JsonObject = {}
    averages = _group_average(results, lambda result: (result.method.value, result.benchmark, result.model_id), lambda result: case_score(result.score) if result.score is not None else None)
    for (method, benchmark, model_id), average in sorted(averages.items()):
        method_scores = scores.setdefault(method, {})
        if not isinstance(method_scores, dict):
            raise ValueError('method scores must not be empty')
        benchmark_scores = method_scores.setdefault(benchmark, {})
        if not isinstance(benchmark_scores, dict):
            raise ValueError('Benchmark scores type abnormal')
        benchmark_scores[model_id] = average
    return scores

def repeat_model_scores(
    results: Sequence[ExperimentCaseResult],
) -> dict[tuple[str, str, int], dict[str, float]]:
    """Average model case scores by method, benchmark, and repeat. Missing, unparseable, and non-finite scores are excluded from the means and pairwise consistency summaries."""
    output: dict[tuple[str, str, int], dict[str, float]] = defaultdict(dict)
    averages = _group_average(results, lambda result: (result.method.value, result.benchmark, result.repeat_index, result.model_id), lambda result: _finite_case_score(result.score))
    for (method, benchmark, repeat, model), average in sorted(averages.items()):
        output[method, benchmark, repeat][model] = average
    return dict(output)


def _group_average(results: Sequence[ExperimentCaseResult], key: Callable[[ExperimentCaseResult], tuple[object, ...]], score_getter: Callable[[ExperimentCaseResult], float | None]) -> dict[tuple[object, ...], float]:
    if results is None:
        raise ValueError('results must not be empty')
    buckets: dict[tuple[object, ...], list[float]] = defaultdict(list)
    for result in results:
        if result is None:
            raise ValueError('Results cannot contain empty results')
        score = score_getter(result)
        if score is not None:
            buckets[key(result)].append(score)
    return {group: sum(values) / len(values) for group, values in buckets.items()}

def rank_models(model_scores: Mapping[str, float]) -> JsonObject:
    """Generate a stable display order and average tied ranks for models."""
    if model_scores is None:
        raise ValueError("model_scores must not be null.")
    scores = {str(model): float(score) for model, score in model_scores.items()}
    if any(not math.isfinite(score) for score in scores.values()):
        raise ValueError('model scores must be finite')

    # The name of the model is used only to stabilize the JSON display; the same score is shared with the same Mid-rank.
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
    """Calculate ranking consistency across repeats within each method and benchmark."""
    if results is None:
        raise ValueError('results must not be empty')
    scores = repeat_model_scores(results)
    repeats: dict[tuple[str, str], set[int]] = defaultdict(set)
    expected_models: dict[tuple[str, str], set[str]] = defaultdict(set)
    invalid_score_models: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for result in results:
        if result is None:
            raise ValueError('Results cannot contain empty results')
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
                invalid_reason = 'No valid model for Kendall tau-b'
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
    """Discriminability Scorre is calculated by the overall dispersion of the model and the ratio of the significant model to the ratio."""
    if scores is None:
        raise ValueError('Scores cannot be empty.')
    if epsilon < 0:
        raise ValueError("You can't count negative.")
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
    """Only matches the model ranking of DEFAULT with replay within the same repeat."""
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
    """Calculates the continuous difference between DEFAULT and replay on case awareness."""
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
    """Summary replay coverage, minefield and structural causes of termination."""
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

def task_completion_rates(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Aggregate native-score completion and partial reward by method, benchmark, and model."""
    buckets: dict[tuple[str, str, str], list[ExperimentCaseResult]] = defaultdict(list)
    for result in results:
        buckets[(result.method.value, result.benchmark, result.model_id)].append(result)
    output: JsonObject = {}
    for (method, benchmark, model_id), items in sorted(buckets.items()):
        scores = [value for item in items if (value := _finite_number(item.native_score)) is not None]
        completed_count = sum(value >= 1.0 for value in scores)
        output[method, benchmark, model_id] = {
            "case_count": len(items),
            "valid_native_score_count": len(scores),
            "completed_count": completed_count,
            "completion_rate": completed_count / len(scores) if scores else None,
            "full_score_count": completed_count,
            "full_score_rate": completed_count / len(scores) if scores else None,
            "average_partial_score": _average(scores),
        }
    return {f"{method}:{benchmark}:{model_id}": value for (method, benchmark, model_id), value in output.items()}

def saved_progress(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Calculates the saved progress only for the case where the replay stop and the default tracks are matched."""
    defaults = {_pair_key(item): item for item in results if item.method == ExperimentMethod.DEFAULT}
    replay_items = [item for item in results if item.method.value.startswith("dynsteer_replay")]
    eligible: dict[tuple[str, str], list[ExperimentCaseResult]] = defaultdict(list)
    stopped: dict[tuple[str, str], list[ExperimentCaseResult]] = defaultdict(list)
    progress_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    invalid_counts: dict[tuple[str, str], int] = defaultdict(int)
    for item in replay_items:
        default = defaults.get(_pair_key(item))
        if default is None:
            continue
        group = item.benchmark, item.model_id
        eligible[group].append(item)
        if not item.termination_code:
            continue
        stopped[group].append(item)
        value = _saved_progress(default, item)
        if value is None:
            invalid_counts[group] += 1
        else:
            progress_values[group].append(value)
    output: JsonObject = {}
    for group in sorted(eligible):
        values = progress_values[group]
        stopped_items = stopped[group]
        output[f"{group[0]}:{group[1]}"] = {
            "paired_replay_count": len(eligible[group]),
            "stopped_count": len(stopped_items),
            "stop_rate": len(stopped_items) / len(eligible[group]) if eligible[group] else None,
            "valid_progress_count": len(values),
            "invalid_progress_count": invalid_counts[group],
            "mean": _average(values),
            "median": _percentile(values, 0.5),
            "p10": _percentile(values, 0.1),
            "p90": _percentile(values, 0.9),
            "p95": _percentile(values, 0.95),
        }
    return output

def paired_model_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Outputs an intermodel matching audit of Wilcoxon in addition to DynSTEER scores."""
    buckets: dict[tuple[str, str], dict[tuple[object, ...], ExperimentCaseResult]] = defaultdict(dict)
    for result in results:
        buckets[(result.method.value, result.benchmark)][_pair_key(result)] = result
    output: JsonObject = {}
    for (method, benchmark), items in buckets.items():
        group_key = f"{method}:{benchmark}"
        methods = output.setdefault(group_key, {})
        model_ids = sorted({result.model_id for result in items.values()})
        for left_model, right_model in combinations(model_ids, 2):
            left = [result for result in items.values() if result.model_id == left_model]
            right = [result for result in items.values() if result.model_id == right_model]
            differences, missing = _paired_differences(left, right, lambda result: _finite_number(result.score))
            methods[f"{left_model}:{right_model}"] = {
                "paired_case_count": len(differences),
                "missing_pair_count": missing,
                **_wilcoxon_payload(differences),
                "left_better_count": sum(value > 0 for value in differences),
                "right_better_count": sum(value < 0 for value in differences),
            }
    return output

def paired_group_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Strictly pair the replay/online blending arm with the full DynSTEER arm."""
    grouped: dict[tuple[str, str], dict[tuple[object, ...], ExperimentCaseResult]] = defaultdict(dict)
    for result in results:
        grouped[(result.benchmark, result.method.value)][_pair_key(result)] = result
    defaults = {_pair_key(result): result for result in results if result.method == ExperimentMethod.DEFAULT}
    output: JsonObject = {}
    full_methods = {
        ExperimentMethod.DYNSTEER_REPLAY,
        ExperimentMethod.DYNSTEER_EVALUATE,
    }
    for (benchmark, method), items in grouped.items():
        method_enum = ExperimentMethod(method)
        if method_enum in full_methods:
            continue
        full_method = ExperimentMethod.DYNSTEER_REPLAY if method_enum.value.startswith("dynsteer_replay") else ExperimentMethod.DYNSTEER_EVALUATE
        full_items = grouped.get((benchmark, full_method.value), {})
        if not full_items:
            continue
        model_ids = sorted({result.model_id for result in items.values()})
        for model_id in model_ids:
            model_items = [item for item in items.values() if item.model_id == model_id]
            model_full = [item for item in full_items.values() if item.model_id == model_id]
            score_differences, missing = _paired_differences(model_items, model_full, lambda result: _finite_number(result.score))
            common_saved = _common_saved_progress(model_items, model_full, defaults)
            output[f"{benchmark}:{model_id}:{method}"] = {
                "full_method": full_method.value,
                "paired_case_count": len(score_differences),
                "missing_pair_count": missing,
                "mean_score_delta": _average(score_differences),
                **_wilcoxon_payload(score_differences),
                "saved_progress_applicable": method_enum != ExperimentMethod.DYNSTEER_REPLAY_NO_POLICY_STOP,
                "saved_progress_delta": _bootstrap_paired_delta_payload(common_saved),
            }
    return output


def intervention_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Summarizes the quality, fatal detonation and additional costs of the guided intervention triggering."""
    if results is None:
        raise ValueError('results must not be empty')
    grouped: dict[tuple[str, str, int], list[ExperimentCaseResult]] = defaultdict(list)
    defaults: dict[tuple[str, str, int, str], ExperimentCaseResult] = {}
    stops: dict[tuple[str, str, int, str], ExperimentCaseResult] = {}
    for result in results:
        if result.method == ExperimentMethod.DYNSTEER_EVALUATE_GUIDED:
            grouped[(result.benchmark, result.model_id, result.repeat_index)].append(result)
        elif result.method == ExperimentMethod.DEFAULT:
            defaults[_pair_key(result)] = result
        elif result.method == ExperimentMethod.DYNSTEER_EVALUATE:
            stops[_pair_key(result)] = result
    return {
        f"{benchmark}:{model_id}:{repeat_index}": _intervention_audit_payload(items, defaults, stops)
        for (benchmark, model_id, repeat_index), items in sorted(grouped.items())
    }


def intervention_tests(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Performs a fixed pair-up feature test for the three arms of default, stop and guided."""
    if results is None:
        raise ValueError('results must not be empty')
    grouped: dict[tuple[str, str, int], dict[ExperimentMethod, list[ExperimentCaseResult]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for result in results:
        if result.method in INTERVENTION_METHODS:
            grouped[(result.benchmark, result.model_id, result.repeat_index)][result.method].append(result)
    comparisons = (
        ("guided_vs_default", ExperimentMethod.DYNSTEER_EVALUATE_GUIDED, ExperimentMethod.DEFAULT),
        ("guided_vs_stop", ExperimentMethod.DYNSTEER_EVALUATE_GUIDED, ExperimentMethod.DYNSTEER_EVALUATE),
        ("stop_vs_default", ExperimentMethod.DYNSTEER_EVALUATE, ExperimentMethod.DEFAULT),
    )
    output: JsonObject = {}
    for group_key, methods in sorted(grouped.items()):
        if not all(method in methods for method in INTERVENTION_METHODS):
            continue
        benchmark, model_id, repeat_index = group_key
        output[f"{benchmark}:{model_id}:{repeat_index}"] = {
            name: _intervention_comparison_payload(methods[left], methods[right], left, right)
            for name, left, right in comparisons
        }
    return output


def stratified_intervention_audit(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """The stratification sensitivity of interventions by mission type and type of security."""
    if results is None:
        raise ValueError('results must not be empty')
    eligible = [result for result in results if result.method in INTERVENTION_METHODS]
    return {
        dimension: _stratified_dimension_audit(eligible, dimension)
        for dimension in ("task_type", "safety_category")
    }


# # Internal Functions

def _intervention_audit_payload(
    items: Sequence[ExperimentCaseResult],
    defaults: Mapping[tuple[str, str, int, str], ExperimentCaseResult],
    stops: Mapping[tuple[str, str, int, str], ExperimentCaseResult],
) -> JsonObject:
    """Construct the intervention quality audit of the results of the single group guided."""
    interventions = [record for item in items for record in item.interventions]
    sent_records = [record for record in interventions if record.get("outcome") == "sent"]
    pass_count = sum(record.get("post_guidance_stage_status") == "pass" for record in sent_records)
    fatal_count = sum(
        bool(item.termination_code and item.termination_code.startswith("minefield"))
        for item in items
    )
    return {
        "case_count": len(items),
        "guidance_fired_case_count": sum(bool(item.interventions) for item in items),
        "guidance_count": len(sent_records),
        "mean_guidance_count": len(sent_records) / len(items) if items else None,
        "guidance_attempt_count": len(interventions),
        "same_stage_repeat_trigger_count": sum(
            record.get("outcome") == "repeat_stage_suppressed" for record in interventions
        ),
        "post_guidance_stage_pass_count": pass_count,
        "post_guidance_stage_pass_rate": pass_count / len(sent_records) if sent_records else None,
        "fatal_minefield_stop_count": fatal_count,
        "fatal_minefield_stop_rate": fatal_count / len(items) if items else None,
        "extra_cost_vs_default": _intervention_cost_delta_payload(items, defaults),
        "extra_cost_vs_stop": _intervention_cost_delta_payload(items, stops),
    }


def _intervention_cost_delta_payload(
    guided_items: Sequence[ExperimentCaseResult],
    references: Mapping[tuple[str, str, int, str], ExperimentCaseResult],
) -> JsonObject:
    """Calculates the three continuous cost distributions for the relative reference arm of the guided."""
    output: JsonObject = {}
    for metric_name in ("agent_steps", "wall_time_seconds", "agent_tokens"):
        values = [
            guided_value - reference_value
            for guided in guided_items
            if (reference := references.get(_pair_key(guided))) is not None
            and (guided_value := _intervention_metric(guided, metric_name)) is not None
            and (reference_value := _intervention_metric(reference, metric_name)) is not None
        ]
        output[metric_name] = _distribution(values)
    return output


def _intervention_metric(result: ExperimentCaseResult, metric_name: str) -> float | None:
    """Read the intervention cost indicator;token is valid only when the trajectory cost is available."""
    metrics = result.runtime_metrics
    if metric_name == "agent_steps":
        return _finite_number(metrics.get("step_count"))
    if metric_name == "wall_time_seconds":
        return _finite_number(metrics.get("elapsed_seconds"))
    if metrics.get("trajectory_cost_available") is True:
        return _finite_number(metrics.get("trajectory_total_tokens"))
    return None


def _intervention_comparison_payload(
    left_items: Sequence[ExperimentCaseResult],
    right_items: Sequence[ExperimentCaseResult],
    left_method: ExperimentMethod,
    right_method: ExperimentMethod,
) -> JsonObject:
    """Output a group of co-composition and continuous indicator tests for about a set of methods."""
    completion = exact_mcnemar(
        [*left_items, *right_items],
        left_method=left_method,
        right_method=right_method,
    )
    output: JsonObject = {"completion": completion}
    for metric_name in ("agent_steps", "wall_time_seconds", "agent_tokens"):
        differences, missing_count = _paired_differences(
            left_items,
            right_items,
            lambda result: _intervention_metric(result, metric_name),
        )
        output[metric_name] = {
            "paired_case_count": len(differences),
            "missing_pair_count": missing_count,
            **_wilcoxon_payload(differences),
            **_bootstrap_paired_delta_payload(differences),
        }
    return output


def _stratified_dimension_audit(
    results: Sequence[ExperimentCaseResult],
    dimension: str,
) -> JsonObject:
    """Expands the individual layer dimensions and tailors the results to sample sufficiency."""
    layers: dict[str, list[ExperimentCaseResult]] = defaultdict(list)
    field_name = "task_types" if dimension == "task_type" else "safety_categories"
    for result in results:
        for value in result.strata.get(field_name, []):
            layers[str(value)].append(result)
    output: JsonObject = {}
    for value, items in sorted(layers.items()):
        tests = intervention_tests(items)
        insufficient = not tests or any(
            len(group) != 3 for group in tests.values()
        ) or any(
            comparison["completion"]["paired_valid_case_count"] < STRATUM_MIN_PAIRS
            for group in tests.values()
            for comparison in group.values()
        )
        output[value] = {
            "case_count_by_method": dict(sorted(
                (method.value, sum(item.method == method for item in items))
                for method in INTERVENTION_METHODS
            )),
            "insufficient_sample": insufficient,
            "tests": None if insufficient else tests,
        }
    return output


def _paired_differences(
    left: Sequence[ExperimentCaseResult],
    right: Sequence[ExperimentCaseResult],
    value_getter: Callable[[ExperimentCaseResult], float | None],
) -> tuple[list[float], int]:
    """Use the experimental pairing key to take the difference between the values and the values; returns the difference and the missing pairs."""
    left_values = {_pair_key(item): value_getter(item) for item in left}
    right_values = {_pair_key(item): value_getter(item) for item in right}
    common = sorted(set(left_values) & set(right_values))
    differences = [
        float(left_values[key]) - float(right_values[key])
        for key in common
        if left_values[key] is not None and right_values[key] is not None
    ]
    return differences, len(left_values | right_values) - len(differences)

def _wilcoxon_payload(differences: Sequence[float]) -> JsonObject:
    """Calculates Wilcoxon signed-rank on both sides, returns the zero-sum steady p=1."""
    if not differences:
        return {"statistic": None, "p_value": None, "all_zero_differences": not differences}
    if all(abs(value) < 1e-12 for value in differences):
        return {"statistic": 0.0, "p_value": 1.0, "all_zero_differences": True}
    result = stats.wilcoxon(differences, zero_method="wilcox", alternative="two-sided")
    return {
        "statistic": float(result.statistic),
        "p_value": float(result.pvalue),
        "all_zero_differences": False,
    }

def _stop_progress(default: ExperimentCaseResult, replay: ExperimentCaseResult) -> float | None:
    """Reads the stop step as a proportion of the total default step. Invalid input returns None."""
    stop_step = _finite_number(replay.runtime_metrics.get("step_count"))
    total_step = _finite_number(default.runtime_metrics.get("step_count"))
    if stop_step is None or total_step is None or total_step <= 0 or not 0 <= stop_step <= total_step:
        return None
    return stop_step / total_step


def _saved_progress(default: ExperimentCaseResult, replay: ExperimentCaseResult) -> float | None:
    """Reads a valid stamped-run Saved profile, returns an invalid pair to None."""
    progress = _stop_progress(default, replay)
    return None if progress is None else 1.0 - progress

def _finite_saved_progress(default: ExperimentCaseResult, replay: ExperimentCaseResult) -> float | None:
    """Include only replay latency observed at the stopping point in the saved latency distribution."""
    return _saved_progress(default, replay) if replay.termination_code else None

def _common_saved_progress(
    left: Sequence[ExperimentCaseResult],
    right: Sequence[ExperimentCaseResult],
    defaults: Mapping[tuple[object, ...], ExperimentCaseResult],
) -> list[float]:
    """Constructs a logarithm range sequence on both sides of the Saved Process."""
    values: list[float] = []
    for item in left:
        key = _pair_key(item)
        default = defaults.get(key)
        other = next((candidate for candidate in right if _pair_key(candidate) == key), None)
        if default is None or other is None:
            continue
        left_value, right_value = _finite_saved_progress(default, item), _finite_saved_progress(default, other)
        if left_value is not None and right_value is not None:
            values.append(left_value - right_value)
    return values

def _bootstrap_paired_delta_payload(
    differences: Sequence[float],
    *,
    seed: int = 202608,
    samples: int = 10000,
) -> JsonObject:
    """Output as point estimate for delta and 95% per cent percentile bootstrap."""
    values = [float(value) for value in differences]
    if not values:
        return {"pair_count": 0, "mean": None, "ci_lower": None, "ci_upper": None}
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(values), size=(samples, len(values)))
    bootstrap_means = np.asarray(values)[indexes].mean(axis=1)
    return {
        "pair_count": len(values),
        "mean": _average(values),
        "ci_lower": float(np.percentile(bootstrap_means, 2.5)),
        "ci_upper": float(np.percentile(bootstrap_means, 97.5)),
    }

def _finite_number(value: object) -> float | None:
    """Reads a limited value, boolean value and missing returns None."""
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        else None
    )

def exact_mcnemar(
    results: Sequence[ExperimentCaseResult],
    *,
    left_method: ExperimentMethod,
    right_method: ExperimentMethod,
    threshold: float = 1.0,
) -> JsonObject:
    """Construct the pair completion/failure matrix and calculate the exact McNemar using the native score threshold."""
    left_items = {
        _pair_key(item): item for item in results
        if item.method == left_method and _finite_number(item.native_score) is not None
    }
    right_items = {
        _pair_key(item): item for item in results
        if item.method == right_method and _finite_number(item.native_score) is not None
    }
    common = sorted(set(left_items) & set(right_items))
    both_completed = both_failed = left_only = right_only = 0
    for key in common:
        left_completed = _finite_number(left_items[key].native_score) >= threshold
        right_completed = _finite_number(right_items[key].native_score) >= threshold
        if left_completed and right_completed:
            both_completed += 1
        elif not left_completed and not right_completed:
            both_failed += 1
        elif left_completed:
            left_only += 1
        else:
            right_only += 1
    discordant = left_only + right_only
    p_value = stats.binomtest(left_only, discordant, 0.5).pvalue if discordant else 1.0
    return {
        "left_method": left_method.value,
        "right_method": right_method.value,
        "paired_valid_case_count": len(common),
        "both_completed": both_completed,
        "both_failed": both_failed,
        "left_only_completed": left_only,
        "right_only_completed": right_only,
        "discordant_pair_count": discordant,
        "p_value": float(p_value),
    }


def kendall_tau(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """Calculates the Kendall tau-b, which is treated in parallel. Args: left: first group model score. right: second group model score. Returns: [1,1] serial correlation factor; returns 0 when the effective model is less than two."""
    if left is None or right is None:
        raise ValueError('Left and right cannot be empty')
    return _kendall_tau_b(left, right) or 0.0

def repeat_statistics(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Aggregate continuous scores by repeat and case, then compute the overall standard deviation."""
    groups: dict[tuple[str, str, str], dict[int, list[ExperimentCaseResult]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        groups[(result.method.value, result.benchmark, result.model_id)][result.repeat_index].append(result)
    output: JsonObject = {}
    for (method, benchmark, model), repeats in sorted(groups.items()):
        repeat_scores = {}
        for repeat, items in sorted(repeats.items()):
            scores = [case_score(item.score) for item in items if item.score is not None]
            repeat_scores[str(repeat)] = _average(scores)
        valid_repeat_scores = [score for score in repeat_scores.values() if score is not None]
        output.setdefault(method, {}).setdefault(benchmark, {})[model] = {
            "repeat_count": len(repeats), "repeat_scores": repeat_scores,
            "mean_score": _average(valid_repeat_scores),
            "population_stddev": _population_stddev(valid_repeat_scores),
        }
    return output

def _population_stddev(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

def aggregate_efficiency(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Summarizes the time-consuming assessment with the number of Agent steps by method."""
    if results is None:
        raise ValueError('results must not be empty')
    buckets: dict[str, list[ExperimentCaseResult]] = defaultdict(list)
    for result in results:
        buckets[result.method.value].append(result)
    return {"by_method": {method: _aggregate_efficiency_rows(items) for method, items in sorted(buckets.items())}}


def _aggregate_efficiency_rows(results: Sequence[ExperimentCaseResult]) -> JsonObject:
    """Summarize a primary efficiency diagnostic field for one method."""
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
    """Strictly paired DEFAULT and DynSTEER to generate cost summaries and pairing details."""
    if results is None:
        raise ValueError('results must not be empty')
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
    """Write scores.json with metrics.json."""
    if results is None or output_dir is None:
        raise ValueError('Results and output_dir cannot be empty')
    output_dir.mkdir(parents=True, exist_ok=True)
    methods = {result.method for result in results}
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
        "task_completion": task_completion_rates(results),
        "saved_progress": saved_progress(results),
        "paired_model_audit": paired_model_audit(results),
        "paired_group_audit": paired_group_audit(results),
        "repeat_statistics": repeat_statistics(results),
        "rank_tau_by_repeat": rank_tau_by_repeat(results),
        "repeat_rank_consistency": repeat_rank_consistency(results),
    }
    if INTERVENTION_METHODS.issubset(methods):
        metrics.update({
            "intervention_audit": intervention_audit(results),
            "intervention_tests": intervention_tests(results),
            "stratified_intervention_audit": stratified_intervention_audit(results),
        })
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
    agent_time = _finite_number(metrics.get("default_prefix_execution_seconds" if replay else "execution_total_seconds"))
    elapsed = _finite_number(metrics.get("elapsed_seconds"))
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
    progress = _stop_progress(default, item)
    if progress is None and code:
        quality["invalid_stop_progress"] = quality.get("invalid_stop_progress", 0) + 1
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
    seconds = _finite_number(cost.get("elapsed_seconds"))
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
    time_deltas = [row["pipeline_delta"]["time_seconds"] for row in rows if _finite_number(row["pipeline_delta"]["time_seconds"]) is not None]
    token_deltas = [row["pipeline_delta"]["tokens"] for row in rows if _finite_number(row["pipeline_delta"]["tokens"]) is not None]
    overhead_time = [row["overhead_delta"]["time_seconds"] for row in rows if _finite_number(row["overhead_delta"]["time_seconds"]) is not None]
    overhead_tokens = [row["overhead_delta"]["tokens"] for row in rows if _finite_number(row["overhead_delta"]["tokens"]) is not None]
    progresses = [row["stop"]["progress"] for row in rows if _finite_number(row["stop"]["progress"]) is not None]
    eligible = [row for row in rows if isinstance(row["dynsteer_cost"].get("agent_tokens"), int) and isinstance(row["dynsteer_cost"].get("evaluation_tokens"), int) and _finite_number(row.get("experiment_amortized_adaptation_allocation", {}).get("tokens")) is not None]
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
        available = [value for value in values if _finite_number(value) is not None]
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
    seconds = [_finite_number(item["cost"].get("elapsed_seconds")) for item in selected]
    tokens = [item["cost"].get("total_tokens") for item in selected if item["cost"].get("token_available") is True]
    return {"artifact_count": len(selected), "elapsed_seconds": sum(value for value in seconds if value is not None),
            "total_tokens": sum(tokens) if len(tokens) == len(selected) else None}


def _both_numbers(left: object, right: object) -> bool:
    return _finite_number(left) is not None and _finite_number(right) is not None

def _compare_score(left: float, right: float) -> int:
    left_score, right_score = case_score(left), case_score(right)
    if abs(left_score - right_score) < 1e-12:
        return 0
    return 1 if left_score > right_score else -1

def _average(values: Sequence[int | float]) -> float | None:
    return float(sum(values)) / len(values) if values else None

def _finite_case_score(value: object) -> float | None:
    """Reads a limited case score, returns an empty value with missing, abnormal or non-limited values."""
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
    """Calculates Kendall tau-b; there is no valid model to return empty values."""
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
    """returns repeat pair invalid reason; returns empty value when valid."""
    reasons: list[str] = []
    if len(expected_models) < 2:
        reasons.append('Less than two effective models.')
    for repeat, repeat_scores, invalid_models in (
        (left_repeat, left_scores, left_invalid_models),
        (right_repeat, right_scores, right_invalid_models),
    ):
        missing = sorted(expected_models - set(repeat_scores))
        if missing:
            reasons.append(f"repeat {repeat} is missing valid model scores: {', '.join(missing)}")
        if invalid_models:
            reasons.append(f"repeat {repeat} contains missing or non-finite scores: {', '.join(sorted(invalid_models))}")
    return " ; ".join(reasons) or None

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
