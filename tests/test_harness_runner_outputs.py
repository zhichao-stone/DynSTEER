from __future__ import annotations

import json
from pathlib import Path

from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult
from dynsteer.harness.runner import _write_case_outputs
from dynsteer.model import (
    Actor,
    EventType,
    Trajectory,
    TrajectoryEvaluationReport,
    TrajectoryStep,
    TaskCase,
)


class FakeEvaluator:
    def evaluate(
        self,
        harness: object,
        config: HarnessRunConfig,
        task_case: TaskCase,
        progress_reporter: object | None = None,
    ) -> HarnessRunResult:
        trajectory = Trajectory(
            run_id="run",
            task_id=task_case.task_id,
            steps=[
                TrajectoryStep(
                    step_id="s0",
                    index=0,
                    actor=Actor.USER,
                    event_type=EventType.MESSAGE,
                    content="start",
                )
            ],
        )
        report = TrajectoryEvaluationReport(
            run_id="run",
            task_id=task_case.task_id,
            milestone_coverage="none",
            overall_score=0.0,
        )
        return HarnessRunResult(
            benchmark=config.benchmark,
            case_id=task_case.case_id,
            run_id="run",
            task_case=task_case,
            trajectory=trajectory,
            raw_output_dir=Path("raw"),
            raw_summary={},
            evaluation_report=report,
        )


def test_write_case_outputs_writes_full_trajectory_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        case_ids=("case",),
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )
    task_case = TaskCase(task_id="task", task_description="task", case_id="case")

    output = _write_case_outputs(config, object(), FakeEvaluator(), task_case)

    assert output.trajectory_path.exists()
    raw_summary = json.loads(output.raw_summary_path.read_text(encoding="utf-8"))
    assert raw_summary["trajectory_output"]["path"] == "trajectory.json"
    assert raw_summary["trajectory_output"]["step_count"] == 1
    trajectory = json.loads(output.trajectory_path.read_text(encoding="utf-8"))
    assert len(trajectory["steps"]) == 1
