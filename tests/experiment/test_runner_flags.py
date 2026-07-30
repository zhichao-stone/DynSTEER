from __future__ import annotations

import json
from pathlib import Path

from dynsteer.experiment import runner
from dynsteer.experiment.model import ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import HarnessEvaluationOutput, TaskCase


def _spec(tmp_path: Path) -> ExperimentRunSpec:
    return ExperimentRunSpec(
        experiment_id="exp-1",
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        case_ids=None,
        model_id="model-a",
        repeat_index=0,
        method=ExperimentMethod.DYNSTEER_EVALUATE,
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"method": "dynsteer_evaluate"},
    )


def _task_case() -> TaskCase:
    return TaskCase(
        task_id="task-1",
        task_description="Task",
        case_id="case_a",
    )


def _output(tmp_path: Path, score: float = 0.8) -> HarnessEvaluationOutput:
    raw_run_dir = tmp_path / "runs" / "toolsandbox" / "dynsteer_evaluate" / "case_a"
    result_dir = tmp_path / "results" / "toolsandbox" / "dynsteer_evaluate" / "case_a"
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    (raw_run_dir / "raw_summary.json").write_text("{}", encoding="utf-8")
    (raw_run_dir / "trajectory.json").write_text(
        json.dumps({"task_id": "task-1", "steps": [], "snapshots": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = {
        "task_id": "task-1",
        "method": "dynsteer_evaluate",
        "overall_score": score,
        "resolved": True,
        "runtime_metrics": {
            "step_count": 2,
            "llm_total_tokens": 4,
            "trajectory_total_tokens": 6,
            "elapsed_seconds": 1.25,
        },
        "metadata": {"method": "dynsteer_evaluate", "model_id": "model-a", "repeat_index": 0},
    }
    (result_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    (result_dir / "report.json").write_text("{}", encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=result_dir / "report.json",
        summary_path=result_dir / "summary.json",
        raw_summary_path=raw_run_dir / "raw_summary.json",
        trajectory_path=raw_run_dir / "trajectory.json",
    )


def _patch_matrix(monkeypatch, spec: ExperimentRunSpec, config: HarnessRunConfig, task_case: TaskCase) -> None:
    monkeypatch.setattr(runner, "load_experiment_config", lambda _path: object())
    monkeypatch.setattr(runner, "expand_experiment_matrix", lambda _config: [spec])
    monkeypatch.setattr(runner, "build_harness_config", lambda _spec: config)
    monkeypatch.setattr(runner, "prepare_task_cases", lambda _config, force_adapt=False: ([task_case], config))
    monkeypatch.setattr(runner, "tqdm", lambda iterable, **kwargs: iterable)


def test_run_experiment_force_adapt_forces_eval(monkeypatch, tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    config = _config(tmp_path)
    task_case = _task_case()
    output = _output(tmp_path)
    captured: dict[str, object] = {}

    _patch_matrix(monkeypatch, spec, config, task_case)

    def fake_run_evaluate_case(spec_obj, task_case_obj, force_eval: bool = False):
        captured["force_eval"] = force_eval
        return output

    monkeypatch.setattr(runner, "run_evaluate_case", fake_run_evaluate_case)
    monkeypatch.setattr(runner, "write_experiment_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_metric_tables", lambda *args, **kwargs: None)

    results = runner.run_experiment(tmp_path / "experiment.json", force_adapt=True)

    assert captured["force_eval"] is True
    assert len(results) == 1
    assert results[0].case_id == "case_a"
    assert results[0].score == 0.8


def test_run_experiment_no_sum_skips_summary_writers(monkeypatch, tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    config = _config(tmp_path)
    task_case = _task_case()
    output = _output(tmp_path)
    index_calls: list[tuple[object, object]] = []
    metric_calls: list[tuple[object, object]] = []

    _patch_matrix(monkeypatch, spec, config, task_case)
    monkeypatch.setattr(runner, "run_evaluate_case", lambda spec_obj, task_case_obj, force_eval=False: output)
    monkeypatch.setattr(runner, "write_experiment_index", lambda results, output_dir: index_calls.append((results, output_dir)))
    monkeypatch.setattr(runner, "write_metric_tables", lambda results, output_dir: metric_calls.append((results, output_dir)))

    results = runner.run_experiment(tmp_path / "experiment.json", no_sum=True)

    assert len(results) == 1
    assert index_calls == []
    assert metric_calls == []
