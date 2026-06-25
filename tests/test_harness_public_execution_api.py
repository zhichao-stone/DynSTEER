from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, EventType, TaskCase, TrajectoryStep


def _task_case() -> TaskCase:
    return TaskCase(task_id="task-1", task_description="完成测试任务")


def _step(index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=f"step {index}",
    )


def _config(tmp_path: Path) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"run_id": "run-1"},
    )


class FakeHarness(BaseBenchmarkHarness):
    benchmark = "fake"

    def __init__(self, batches: list[HarnessAdvanceResult]) -> None:
        super().__init__()
        self.batches = list(batches)
        self.case_finished_called = False
        self.task_case = _task_case()

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        self.prepare_config(config)
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case-1")]

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        return {"case_id": case_id, "raw_output_dir": raw_output_dir}

    def task_case_from_session(self, session: object) -> TaskCase:
        return self.task_case

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        if not self.batches:
            raise RuntimeError("benchmark session 未完成但没有新增轨迹步骤")
        return self.batches.pop(0)

    def case_finished(self, session: object) -> bool:
        self.case_finished_called = True
        raise AssertionError("DynSTEEREvaluator.evaluate 不应调用 case_finished 控制主循环")


def test_harness_advance_result_rejects_none_steps() -> None:
    with pytest.raises(ValueError, match="steps"):
        HarnessAdvanceResult(steps=None, continue_running=False)  # type: ignore[arg-type]


def test_evaluator_consumes_public_harness_api(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness(
        [
            HarnessAdvanceResult(steps=[_step(0)], continue_running=True),
            HarnessAdvanceResult(steps=[_step(1)], continue_running=False, reason="benchmark 已自然完成"),
        ]
    )
    result = DynSTEEREvaluator().evaluate(harness, "case-1", _config(tmp_path))

    assert result.task_case.task_id == "task-1"
    assert [step.index for step in result.trajectory.steps] == [0, 1]
    assert result.stage_settlements[0].kind == "start"
    assert result.stage_settlements[-1].kind == "finish"
    assert harness.case_finished_called is False


def test_evaluator_exits_on_advance_continue_running_false(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness([HarnessAdvanceResult(steps=[], continue_running=False, reason="benchmark 已自然完成")])
    result = DynSTEEREvaluator().evaluate(harness, "case-1", _config(tmp_path))

    assert result.trajectory.steps == []
    assert result.stage_settlements[-1].kind == "finish"
    assert harness.case_finished_called is False


def test_harness_owns_empty_step_error(tmp_path: Path) -> None:
    from dynsteer.evaluate import DynSTEEREvaluator

    harness = FakeHarness([])

    with pytest.raises(RuntimeError, match="没有新增轨迹步骤"):
        DynSTEEREvaluator().evaluate(harness, "case-1", _config(tmp_path))
