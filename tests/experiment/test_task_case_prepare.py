from pathlib import Path

import dynsteer.experiment.runner as runner
from dynsteer.experiment.model import ExperimentCaseResult, ExperimentMethod, ExperimentRunSpec
from dynsteer.model import MilestoneGraph, TaskCase


def test_run_experiment_prepares_task_cases_once_per_benchmark(monkeypatch: object, tmp_path: Path) -> None:
    """同一 benchmark 的实验矩阵只准备一批 TaskCase 模板。"""
    specs = [
        _spec(tmp_path, ExperimentMethod.DEFAULT, run_id="run_0"),
        _spec(tmp_path, ExperimentMethod.DYNSTEER_REPLAY, run_id="run_1"),
        _spec(tmp_path, ExperimentMethod.DYNSTEER_REPLAY_STATIC, run_id="run_2"),
        _spec(tmp_path, ExperimentMethod.DYNSTEER_EVALUATE, run_id="run_3"),
    ]
    template = TaskCase(
        task_id="task",
        task_description="do task",
        case_id="case_a",
        milestone_graph=MilestoneGraph(),
        metadata={"template": True},
    )
    load_calls: list[tuple[str, bool]] = []
    seen_cases: list[tuple[ExperimentMethod, TaskCase]] = []

    def fake_load_task_cases(config: object, force_adapt: bool = False) -> list[TaskCase]:
        load_calls.append((getattr(config, "benchmark"), force_adapt))
        return [template]

    def fake_case_result(
        spec: ExperimentRunSpec,
        task_case: TaskCase,
        output: object,
        default_reference: dict[str, object] | None = None,
    ) -> ExperimentCaseResult:
        return ExperimentCaseResult(
            experiment_id=spec.experiment_id,
            run_id=spec.run_id,
            benchmark=spec.benchmark,
            case_id=task_case.case_id,
            model_id=spec.model_id,
            repeat_index=spec.repeat_index,
            method=spec.method,
            default_score=1.0 if default_reference is not None else None,
            dynsteer_score=None if spec.method == ExperimentMethod.DEFAULT else 1.0,
        )

    def fake_default_case(spec: ExperimentRunSpec, task_case: TaskCase) -> tuple[object, dict[str, object]]:
        task_case.metadata["mutated_by"] = "default"
        seen_cases.append((spec.method, task_case))
        return object(), {"score": 1.0, "resolved": True}

    def fake_replay_case(
        spec: ExperimentRunSpec,
        task_case: TaskCase,
        default_output: object,
        default_reference: dict[str, object],
    ) -> object:
        seen_cases.append((spec.method, task_case))
        return object()

    def fake_evaluate_case(spec: ExperimentRunSpec, task_case: TaskCase) -> object:
        seen_cases.append((spec.method, task_case))
        return object()

    monkeypatch.setattr(runner, "load_experiment_config", lambda path: {})
    monkeypatch.setattr(runner, "expand_experiment_matrix", lambda config: specs)
    monkeypatch.setattr(runner, "_load_task_cases", fake_load_task_cases)
    monkeypatch.setattr(runner, "_case_result_from_output", fake_case_result)
    monkeypatch.setattr(runner, "run_default_case", fake_default_case)
    monkeypatch.setattr(runner, "run_replay_case", fake_replay_case)
    monkeypatch.setattr(runner, "run_evaluate_case", fake_evaluate_case)
    monkeypatch.setattr(runner, "write_experiment_index", lambda results, output_dir: None)
    monkeypatch.setattr(runner, "write_metric_tables", lambda results, output_dir: None)

    results = runner.run_experiment(tmp_path / "experiment.json", force_adapt=True)

    assert load_calls == [("toolsandbox", True)]
    assert len(results) == 4
    assert len({id(task_case) for _, task_case in seen_cases}) == 4
    assert template.metadata == {"template": True}
    replay_metadata = [task_case.metadata for method, task_case in seen_cases if method != ExperimentMethod.DEFAULT]
    assert all("mutated_by" not in metadata for metadata in replay_metadata)


def _spec(tmp_path: Path, method: ExperimentMethod, run_id: str) -> ExperimentRunSpec:
    return ExperimentRunSpec(
        experiment_id="exp",
        run_id=run_id,
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        case_ids=("case_a",),
        model_id="model_a",
        repeat_index=0,
        method=method,
    )
