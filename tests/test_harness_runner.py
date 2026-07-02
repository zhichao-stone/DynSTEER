from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import dynsteer.harness.runner as runner_module
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.harness.runner import run_harness_cases
from dynsteer.model import TaskCase, Trajectory, TrajectoryEvaluationReport


class FakeHarness:
    benchmark = "fake"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark="fake", case_id="case-1")]

    def prepare_config(self, config: HarnessRunConfig) -> None:
        if config is None:
            raise ValueError("config 不能为空")

    def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
        return str(config.metadata.get("run_id", "run-1"))


class FakeAdapter:
    benchmark = "fake"

    def __init__(self, harness_factory: type[FakeHarness] = FakeHarness) -> None:
        self.harness_factory = harness_factory

    def create_harness(self) -> FakeHarness:
        return self.harness_factory()

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        return TaskCase(
            task_id=f"task-{case_id}",
            task_description="测试任务",
            case_id=case_id,
        )


class FakeEvaluator:
    def __init__(self) -> None:
        self.case_ids: list[str] = []

    def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
        case_id = task_case.case_id
        self.case_ids.append(case_id)
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


class MultiCaseHarness(FakeHarness):
    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [
            BenchmarkCase(benchmark="fake", case_id="case-1"),
            BenchmarkCase(benchmark="fake", case_id="case-2"),
        ]


class ScoredEvaluator(FakeEvaluator):
    def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
        case_id = task_case.case_id
        result = super().evaluate(harness, config, task_case)
        score = 1.0 if case_id == "case-1" else 0.5
        coverage = "full" if case_id == "case-1" else "partial"
        return HarnessRunResult(
            benchmark=result.benchmark,
            case_id=result.case_id,
            run_id=result.run_id,
            task_case=result.task_case,
            trajectory=result.trajectory,
            raw_output_dir=result.raw_output_dir,
            raw_summary=result.raw_summary,
            stage_settlements=result.stage_settlements,
            evaluation_report=TrajectoryEvaluationReport(
                run_id=result.run_id,
                task_id=f"task-{case_id}",
                milestone_coverage=coverage,
                overall_score=score,
                first_failure_stage_id=None if case_id == "case-1" else "stage-x",
            ),
        )


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.extras: list[dict[str, object] | None] = []

    def info(self, message: str, *args: object, extra: dict[str, object] | None = None) -> None:
        if args:
            message = message % args
        self.messages.append(message)
        self.extras.append(extra)


@pytest.fixture(autouse=True)
def install_default_fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(), raising=False)


def test_runner_calls_dynsteer_evaluator(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )
    evaluator = FakeEvaluator()

    outputs = run_harness_cases(config=config, evaluator=evaluator)

    assert evaluator.case_ids == ["case-1"]
    assert len(outputs) == 1
    assert outputs[0].report_path.read_text(encoding="utf-8")


def test_run_harness_cases_writes_run_level_summary(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    try:
        run_harness_cases(config=config, evaluator=ScoredEvaluator())
    finally:
        monkeypatch.undo()

    summary_path = tmp_path / "results" / "fake" / "run-1" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["benchmark"] == "fake"
    assert summary["run_id"] == "run-1"
    assert summary["case_count"] == 2
    assert summary["average_overall_score"] == pytest.approx(0.75)
    assert summary["milestone_coverage_counts"] == {"full": 1, "partial": 1}
    assert [case["case_id"] for case in summary["cases"]] == ["case-1", "case-2"]
    assert summary["cases"][0]["summary_path"] == "case-1/summary.json"
    assert summary["cases"][0]["report_path"] == "case-1/report.json"
    assert summary["cases"][1]["first_failure_stage_id"] == "stage-x"


def test_run_harness_case_writes_run_level_summary(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    try:
        runner_module.run_harness_case(config=config, evaluator=ScoredEvaluator())
    finally:
        monkeypatch.undo()

    summary_path = tmp_path / "results" / "fake" / "run-1" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["benchmark"] == "fake"
    assert summary["run_id"] == "run-1"
    assert summary["case_count"] == 1
    assert summary["average_overall_score"] == pytest.approx(1.0)
    assert summary["milestone_coverage_counts"] == {"full": 1}
    assert summary["cases"][0]["case_id"] == "case-1"


def test_run_harness_configs_logs_case_overview_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logger = RecordingLogger()
    dynsteer_logger = logging.getLogger("dynsteer")

    class MultiCaseHarness(FakeHarness):
        def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
            return [
                BenchmarkCase(benchmark="fake", case_id="case-1"),
                BenchmarkCase(benchmark="fake", case_id="case-2"),
            ]

    monkeypatch.setattr(dynsteer_logger, "handlers", [], raising=False)
    monkeypatch.setattr(runner_module, "configure_logger", lambda log_dir: logger, raising=False)
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: FakeEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    runner_module.run_harness_configs(configs=[config], max_workers=1)

    assert logger.messages.count("benchmark harness 将运行 2 个场景: case-1, case-2") == 1
    assert logger.messages.count("开始运行 benchmark harness") == 1
    assert logger.messages.count("benchmark harness 评估完成") == 1


def test_run_harness_configs_reuses_existing_logger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    dynsteer_logger = logging.getLogger("dynsteer")

    class MessageHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    class MultiCaseHarness(FakeHarness):
        def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
            return [
                BenchmarkCase(benchmark="fake", case_id="case-1"),
                BenchmarkCase(benchmark="fake", case_id="case-2"),
            ]

    handler = MessageHandler()
    monkeypatch.setattr(dynsteer_logger, "handlers", [handler], raising=False)
    monkeypatch.setattr(dynsteer_logger, "level", logging.INFO, raising=False)
    monkeypatch.setattr(dynsteer_logger, "propagate", False, raising=False)
    monkeypatch.setattr(runner_module, "configure_logger", lambda log_dir: pytest.fail("不应重复初始化 logger"), raising=False)
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: FakeEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    runner_module.run_harness_configs(configs=[config], max_workers=1)

    assert messages.count("benchmark harness 将运行 2 个场景: case-1, case-2") == 1
    assert messages.count("开始运行 benchmark harness") == 1
    assert messages.count("benchmark harness 评估完成") == 1


def test_runner_serializes_all_outputs_before_writing_files(tmp_path: Path) -> None:
    class NonSerializableEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            result = super().evaluate(harness, config, task_case)
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

    with pytest.raises(runner_module.HarnessCaseExecutionError) as exc_info:
        run_harness_cases(config=config, evaluator=NonSerializableEvaluator())

    assert isinstance(exc_info.value.cause, TypeError)
    result_dir = tmp_path / "results" / "fake" / "run-1" / "case-1"
    assert not (result_dir / "report.json").exists()
    assert not (result_dir / "summary.json").exists()
    assert not (tmp_path / "runs" / "fake" / "run-1" / "case-1" / "raw_summary.json").exists()


def test_runner_preserves_stage_diagnostics_in_raw_summary(tmp_path: Path) -> None:
    class DiagnosticsEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            result = super().evaluate(harness, config, task_case)
            settlement = HarnessStageSettlement(
                settlement_id="st0",
                kind="finish",
                milestone_id=None,
                start_step_index=0,
                end_step_index=0,
                metadata={
                    "stage_trace": {"step_count": 1, "steps": [{"index": 0}]},
                    "milestone_matching": {"mode": "runtime_finish", "matched": False},
                },
            )
            return HarnessRunResult(
                benchmark=result.benchmark,
                case_id=result.case_id,
                run_id=result.run_id,
                task_case=result.task_case,
                trajectory=result.trajectory,
                raw_output_dir=result.raw_output_dir,
                raw_summary=result.raw_summary,
                stage_settlements=[settlement],
                evaluation_report=result.evaluation_report,
            )

    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    outputs = run_harness_cases(config=config, evaluator=DiagnosticsEvaluator())

    raw_summary = json.loads(outputs[0].raw_summary_path.read_text(encoding="utf-8"))
    metadata = raw_summary["stage_settlements"][0]["metadata"]
    assert metadata["stage_trace"]["step_count"] == 1
    assert metadata["milestone_matching"]["mode"] == "runtime_finish"


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

        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            calls.append((self.instance_id, harness.instance_id, task_case.case_id))  # type: ignore[attr-defined]
            return super().evaluate(harness, config, task_case)

    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(FactoryHarness), raising=False)
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

        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            calls.append((self.instance_id, harness.instance_id, task_case.case_id))  # type: ignore[attr-defined]
            return super().evaluate(harness, config, task_case)

    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(FactoryHarness), raising=False)
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


def test_run_harness_configs_parallel_writes_run_level_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: ScoredEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    runner_module.run_harness_configs(configs=[config], max_workers=2)

    summary_path = tmp_path / "results" / "fake" / "run-1" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["case_count"] == 2
    assert summary["average_overall_score"] == pytest.approx(0.75)
    assert sorted(case["case_id"] for case in summary["cases"]) == ["case-1", "case-2"]


def test_run_harness_configs_serial_writes_run_level_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(MultiCaseHarness), raising=False)
    monkeypatch.setattr(
        runner_module,
        "DynSTEEREvaluator",
        type("EvaluatorFactory", (), {"from_env": staticmethod(lambda: ScoredEvaluator())}),
    )
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    runner_module.run_harness_configs(configs=[config], max_workers=1)

    summary_path = tmp_path / "results" / "fake" / "run-1" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["case_count"] == 2
    assert summary["average_overall_score"] == pytest.approx(0.75)


def test_run_harness_configs_wraps_case_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            raise RuntimeError("boom")

    monkeypatch.setattr(runner_module, "get_adapter", lambda benchmark: FakeAdapter(), raising=False)
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


def test_run_harness_case_wraps_case_errors(tmp_path: Path) -> None:
    class FailingEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            raise RuntimeError("boom")

    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    with pytest.raises(runner_module.HarnessCaseExecutionError, match="case-1") as exc_info:
        runner_module.run_harness_case(config=config, evaluator=FailingEvaluator())

    assert exc_info.value.benchmark == "fake"
    assert exc_info.value.run_id == "run-1"
    assert exc_info.value.case_id == "case-1"


def test_run_harness_cases_wraps_case_errors(tmp_path: Path) -> None:
    class FailingEvaluator(FakeEvaluator):
        def evaluate(self, harness: object, config: HarnessRunConfig, task_case: TaskCase) -> HarnessRunResult:
            raise RuntimeError("boom")

    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )

    with pytest.raises(runner_module.HarnessCaseExecutionError, match="case-1") as exc_info:
        runner_module.run_harness_cases(config=config, evaluator=FailingEvaluator())

    assert exc_info.value.benchmark == "fake"
    assert exc_info.value.run_id == "run-1"
    assert exc_info.value.case_id == "case-1"
