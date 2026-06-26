from __future__ import annotations

from pathlib import Path

import pytest

import dynsteer.harness.runner as runner_module
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult
from dynsteer.harness.runner import run_harness_cases
from dynsteer.model import TaskCase, Trajectory, TrajectoryEvaluationReport


class FakeHarness:
    benchmark = "fake"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark="fake", case_id="case-1")]


class FakeEvaluator:
    def __init__(self) -> None:
        self.case_ids: list[str] = []

    def evaluate(self, harness: object, case_id: str, config: HarnessRunConfig) -> HarnessRunResult:
        self.case_ids.append(case_id)
        task_case = TaskCase(task_id="task-1", task_description="测试任务")
        trajectory = Trajectory(run_id="run-1", task_id="task-1", steps=[])
        return HarnessRunResult(
            benchmark="fake",
            case_id=case_id,
            run_id="run-1",
            task_case=task_case,
            trajectory=trajectory,
            raw_output_dir=config.runs_dir / "fake" / "run-1" / case_id / "raw",
            evaluation_report=TrajectoryEvaluationReport(
                run_id="run-1",
                task_id="task-1",
                milestone_coverage="none",
                overall_score=1.0,
            ),
        )


def test_runner_calls_dynsteer_evaluator(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )
    evaluator = FakeEvaluator()

    outputs = run_harness_cases(config=config, harness=FakeHarness(), evaluator=evaluator)

    assert evaluator.case_ids == ["case-1"]
    assert len(outputs) == 1
    assert outputs[0].report_path.read_text(encoding="utf-8")


def test_runner_serializes_all_outputs_before_writing_files(tmp_path: Path) -> None:
    class NonSerializableEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, case_id: str, config: HarnessRunConfig) -> HarnessRunResult:
            result = super().evaluate(harness, case_id, config)
            return HarnessRunResult(
                benchmark=result.benchmark,
                case_id=result.case_id,
                run_id=result.run_id,
                task_case=result.task_case,
                trajectory=result.trajectory,
                raw_output_dir=result.raw_output_dir,
                raw_summary={"bad": object()},
                evaluation_report=result.evaluation_report,
            )

    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    with pytest.raises(TypeError):
        run_harness_cases(config=config, harness=FakeHarness(), evaluator=NonSerializableEvaluator())

    result_dir = tmp_path / "results" / "fake" / "run-1" / "case-1"
    assert not (result_dir / "report.json").exists()
    assert not (result_dir / "summary.json").exists()
    assert not (tmp_path / "runs" / "fake" / "run-1" / "case-1" / "raw_summary.json").exists()


def test_run_harness_configs_keeps_serial_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, str]] = []

    class FactoryHarness(FakeHarness):
        counter = 0

        def __init__(self) -> None:
            FactoryHarness.counter += 1
            self.instance_id = FactoryHarness.counter

        def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
            return [
                BenchmarkCase(benchmark="fake", case_id="case-1"),
                BenchmarkCase(benchmark="fake", case_id="case-2"),
            ]

    class FactoryEvaluator(FakeEvaluator):
        counter = 0

        def __init__(self) -> None:
            super().__init__()
            FactoryEvaluator.counter += 1
            self.instance_id = FactoryEvaluator.counter

        def evaluate(self, harness: object, case_id: str, config: HarnessRunConfig) -> HarnessRunResult:
            calls.append((self.instance_id, harness.instance_id, case_id))  # type: ignore[attr-defined]
            return super().evaluate(harness, case_id, config)

    monkeypatch.setattr(runner_module, "get_harness", lambda benchmark: FactoryHarness(), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: FactoryEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    outputs = runner_module.run_harness_configs(configs=[config], max_workers=1)

    assert [output.report_path.name for output in outputs] == ["report.json", "report.json"]
    assert [item[2] for item in calls] == ["case-1", "case-2"]


def test_run_harness_configs_parallel_uses_independent_harness_and_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, str]] = []

    class FactoryHarness(FakeHarness):
        counter = 0

        def __init__(self) -> None:
            FactoryHarness.counter += 1
            self.instance_id = FactoryHarness.counter

        def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
            return [
                BenchmarkCase(benchmark="fake", case_id="case-1"),
                BenchmarkCase(benchmark="fake", case_id="case-2"),
            ]

    class FactoryEvaluator(FakeEvaluator):
        counter = 0

        def __init__(self) -> None:
            super().__init__()
            FactoryEvaluator.counter += 1
            self.instance_id = FactoryEvaluator.counter

        def evaluate(self, harness: object, case_id: str, config: HarnessRunConfig) -> HarnessRunResult:
            calls.append((self.instance_id, harness.instance_id, case_id))  # type: ignore[attr-defined]
            return super().evaluate(harness, case_id, config)

    monkeypatch.setattr(runner_module, "get_harness", lambda benchmark: FactoryHarness(), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: FactoryEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    outputs = runner_module.run_harness_configs(configs=[config], max_workers=2)

    assert len(outputs) == 2
    assert len({item[0] for item in calls}) == 2
    assert len({item[1] for item in calls}) == 2


def test_run_harness_configs_wraps_case_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, case_id: str, config: HarnessRunConfig) -> HarnessRunResult:
            raise RuntimeError("boom")

    monkeypatch.setattr(runner_module, "get_harness", lambda benchmark: FakeHarness(), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: FailingEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    with pytest.raises(runner_module.HarnessCaseExecutionError, match="case-1"):
        runner_module.run_harness_configs(configs=[config], max_workers=1)
