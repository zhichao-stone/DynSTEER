from __future__ import annotations

from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod
from dynsteer.experiment.runner import _build_experiment_index_payload


def test_build_experiment_index_payload_omits_run_id() -> None:
    result = ExperimentCaseResult(
        experiment_id="exp-1",
        benchmark="toolsandbox",
        case_id="case_a",
        model_id="model-a",
        repeat_index=0,
        method=ExperimentMethod.DEFAULT,
        default_score=1.0,
        resolved=True,
    )

    payload = _build_experiment_index_payload([result])
    repeat_node = payload["results"]["toolsandbox"]["default"]["model-a"]["repeats"]["0"]
    case_node = repeat_node["cases"]["case_a"]

    assert "run_id" not in repeat_node
    assert "run_id" not in case_node
    assert case_node["score"] == 1.0
    assert case_node["resolved"] is True
