from pathlib import Path

import dynsteer.harness.scheduler as scheduler
import dynsteer.harness.selection as selection
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import HarnessCaseTask, MilestoneGraph, TaskCase


def test_run_case_passes_full_config_without_single_case_rewrite(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    """单 case 执行阶段不再把 config.case_ids 改写为单元素。"""
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path / "data",
        case_ids=("case_a", "case_b"),
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )
    task_case = TaskCase(
        task_id="task",
        task_description="do task",
        case_id="case_a",
        milestone_graph=MilestoneGraph(),
    )
    task = HarnessCaseTask(order=0, config=config, case_id="case_a", task_case=task_case)
    captured: dict[str, object] = {}

    monkeypatch.setattr(scheduler, "get_harness", lambda benchmark: object())
    monkeypatch.setattr(scheduler.DynSTEEREvaluator, "from_config", lambda config: object())

    def fake_write_case_outputs(
        config: HarnessRunConfig,
        harness: object,
        evaluator: object,
        task_case: TaskCase,
        progress_reporter: object | None = None,
    ) -> object:
        captured["config"] = config
        captured["task_case"] = task_case
        return object()

    monkeypatch.setattr(scheduler, "write_case_outputs", fake_write_case_outputs)

    scheduler._run_case(task)

    assert captured["config"] is config
    assert config.case_ids == ("case_a", "case_b")
    assert not hasattr(selection, "config_with_case_ids")
