from __future__ import annotations

from pathlib import Path

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
