import json
import math
from pathlib import Path

import pytest

from dynsteer.experiment.metrics import rank_models, repeat_rank_consistency, write_metric_tables
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod


def test_repeat_rank_consistency_identical_and_reversed_orders() -> None:
    results = _results_for_orders(
        ExperimentMethod.DEFAULT,
        [["a", "b", "c", "d"], ["a", "b", "c", "d"], ["d", "c", "b", "a"]],
    )

    summary = repeat_rank_consistency(results)["default"]["toolsandbox"]

    assert summary["repeat_count"] == 3
    assert summary["repeat_pair_count"] == 3
    assert summary["valid_pair_count"] == 3
    assert [pair["kendall_tau_b"] for pair in summary["pairwise"]] == [1.0, -1.0, -1.0]
    assert summary["exact_order_agreement_rate"] == pytest.approx(1 / 3)
    assert summary["mean_pairwise_absolute_rank_displacement"] == pytest.approx(4 / 3)


def test_rank_models_and_tau_b_preserve_ties() -> None:
    ranking = rank_models({"b": 1.0, "a": 1.0, "c": 0.0})
    results = _results_for_score_maps(
        ExperimentMethod.DEFAULT,
        [{"a": 1.0, "b": 1.0, "c": 0.0}, {"a": 1.0, "b": 0.0, "c": 0.0}],
    )

    summary = repeat_rank_consistency(results)["default"]["toolsandbox"]

    assert ranking["order"] == ["a", "b", "c"]
    assert ranking["ranks"] == {"a": 1.5, "b": 1.5, "c": 3.0}
    assert summary["pairwise"][0]["kendall_tau_b"] == pytest.approx(0.5)
    assert summary["pairwise"][0]["mean_absolute_rank_displacement"] == pytest.approx(2 / 3)


def test_missing_model_and_invalid_scores_make_pairs_invalid() -> None:
    results = [
        _result(ExperimentMethod.DEFAULT, "a", 0, 1.0),
        _result(ExperimentMethod.DEFAULT, "b", 0, 0.5),
        _result(ExperimentMethod.DEFAULT, "a", 1, 1.0),
        _result(ExperimentMethod.DEFAULT, "b", 1, None),
        _result(ExperimentMethod.DEFAULT, "a", 2, math.inf),
        _result(ExperimentMethod.DEFAULT, "b", 2, 0.5),
    ]

    summary = repeat_rank_consistency(results)["default"]["toolsandbox"]

    assert summary["repeat_pair_count"] == 3
    assert summary["valid_pair_count"] == 0
    assert summary["mean_pairwise_kendall_tau"] is None
    assert len(summary["invalid_pairs"]) == 3
    assert all(pair["invalid_reason"] for pair in summary["invalid_pairs"])


@pytest.mark.parametrize(
    ("orders", "repeat_count", "pair_count"),
    [
        ([["a", "b"]], 1, 0),
        ([["a"], ["a"]], 2, 1),
    ],
)
def test_insufficient_repeats_or_models_return_null(
    orders: list[list[str]], repeat_count: int, pair_count: int
) -> None:
    results = _results_for_orders(ExperimentMethod.DEFAULT, orders)
    summary = repeat_rank_consistency(results)["default"]["toolsandbox"]

    assert summary["repeat_count"] == repeat_count
    assert summary["repeat_pair_count"] == pair_count
    assert summary["valid_pair_count"] == 0
    assert summary["mean_pairwise_kendall_tau"] is None
    assert summary["exact_order_agreement_rate"] is None
    assert summary["top1_agreement_rate"] is None
    assert summary["mean_pairwise_absolute_rank_displacement"] is None


def test_methods_are_aggregated_independently() -> None:
    results = [
        *_results_for_orders(ExperimentMethod.DEFAULT, [["a", "b"], ["b", "a"]]),
        *_results_for_orders(ExperimentMethod.DYNSTEER_REPLAY, [["a", "b"], ["a", "b"]]),
    ]

    metrics = repeat_rank_consistency(results)

    assert metrics["default"]["toolsandbox"]["mean_pairwise_kendall_tau"] == -1.0
    assert metrics["dynsteer_replay"]["toolsandbox"]["mean_pairwise_kendall_tau"] == 1.0


def test_write_metric_tables_adds_serializable_metric_without_changing_existing_fields(tmp_path: Path) -> None:
    results = [
        *_results_for_orders(ExperimentMethod.DEFAULT, [["a", "b"], ["a", "b"]]),
        *_results_for_orders(ExperimentMethod.DYNSTEER_REPLAY, [["a", "b"], ["a", "b"]]),
    ]

    output = write_metric_tables(results, tmp_path)
    on_disk = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))

    assert "score_rank_tau" in output["metrics"]
    assert "rank_tau_by_repeat" in output["metrics"]
    assert "repeat_rank_consistency" in output["metrics"]
    assert on_disk["repeat_rank_consistency"]["default"]["toolsandbox"]["valid_pair_count"] == 1
    json.dumps(output, ensure_ascii=False)


def _results_for_orders(
    method: ExperimentMethod, orders: list[list[str]]
) -> list[ExperimentCaseResult]:
    score_maps = [
        {model: (len(order) - rank) / len(order) for rank, model in enumerate(order)}
        for order in orders
    ]
    return _results_for_score_maps(method, score_maps)


def _results_for_score_maps(
    method: ExperimentMethod, score_maps: list[dict[str, float]]
) -> list[ExperimentCaseResult]:
    return [
        _result(method, model, repeat, score)
        for repeat, scores in enumerate(score_maps)
        for model, score in scores.items()
    ]


def _result(
    method: ExperimentMethod, model: str, repeat: int, score: float | None
) -> ExperimentCaseResult:
    return ExperimentCaseResult(
        experiment_id="test",
        benchmark="toolsandbox",
        case_id="case-1",
        model_id=model,
        repeat_index=repeat,
        method=method,
        score=score,
    )
