from __future__ import annotations

import logging
from pathlib import Path

from dynsteer.harness import scheduler
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import HarnessCaseTask, TaskCase


def _task(tmp_path: Path) -> HarnessCaseTask:
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"method": "dynsteer_evaluate"},
    )
    task_case = TaskCase(
        task_id="task-1",
        task_description="Task",
        case_id="case_a",
    )
    return HarnessCaseTask(order=0, config=config, case_id="case_a", task_case=task_case)


def test_run_case_tasks_passes_force_eval_to_serial_runner(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_tasks_serial(tasks, *, logger, force_eval: bool = False):
        captured["force_eval"] = force_eval
        captured["task_count"] = len(tasks)
        return []

    monkeypatch.setattr(scheduler, "_run_tasks_serial", fake_run_tasks_serial)

    result = scheduler.run_case_tasks(
        [_task(tmp_path)],
        max_workers=1,
        logger=logging.getLogger("dynsteer-test"),
        force_eval=True,
    )

    assert result == []
    assert captured == {"force_eval": True, "task_count": 1}


def test_harness_case_execution_error_message_has_no_run_id() -> None:
    error = scheduler.HarnessCaseExecutionError("toolsandbox", "case_a", RuntimeError("boom"))

    assert error.benchmark == "toolsandbox"
    assert error.case_id == "case_a"
    assert "run_id" not in str(error)
