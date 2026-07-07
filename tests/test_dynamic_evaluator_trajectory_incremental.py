from __future__ import annotations

from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import Actor, EventType, StateSnapshot, TaskCase, TrajectoryStep


def _step(index: int) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"s{index}",
        index=index,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=f"step {index}",
    )


class IncrementalHarness(BaseBenchmarkHarness):
    benchmark = "fake"

    def __init__(self) -> None:
        self.advance_calls = 0

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case")]

    def prepare_config(self, config: HarnessRunConfig) -> None:
        return None

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> dict[str, object]:
        return {"state": {"phase": "started"}, "metrics": {"advance_calls": 0}}

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        assert isinstance(session, dict)
        self.advance_calls += 1
        session["state"] = {"phase": f"advance:{self.advance_calls}"}
        session["metrics"] = {"advance_calls": self.advance_calls}
        if self.advance_calls == 1:
            return HarnessAdvanceResult(
                steps=[_step(10)],
                snapshots=[StateSnapshot("snap10", "s10", 10)],
                continue_running=True,
            )
        return HarnessAdvanceResult(
            steps=[_step(12), _step(20)],
            snapshots=[
                StateSnapshot("snap12", "s12", 12),
                StateSnapshot("snap20", "s20", 20),
            ],
            continue_running=False,
        )

    def case_finished(self, session: object) -> bool:
        return self.advance_calls >= 2

    def final_state_from_session(self, session: object) -> dict[str, object]:
        assert isinstance(session, dict)
        return dict(session["state"])  # type: ignore[arg-type]

    def metrics_from_session(self, session: object) -> dict[str, object]:
        assert isinstance(session, dict)
        return dict(session["metrics"])  # type: ignore[arg-type]

    def raw_summary_from_session(self, session: object) -> dict[str, object]:
        return {"raw": "summary"}


def test_evaluate_maintains_one_runtime_trajectory_without_rebuilding_per_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("evaluate 不应在每个 step 后重建 trajectory")

    monkeypatch.setattr("dynsteer.evaluate.evaluator.build_trajectory", fail_if_called, raising=False)
    harness = IncrementalHarness()
    evaluator = DynSTEEREvaluator()
    task_case = TaskCase(task_id="task", task_description="complete task", case_id="case")
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        stop_on_stage_failure=False,
        stop_on_minefield=False,
    )

    result = evaluator.evaluate(harness, config, task_case)

    assert [step.index for step in result.trajectory.steps] == [10, 12, 20]
    assert [snapshot.snapshot_id for snapshot in result.trajectory.snapshots] == ["snap10", "snap12", "snap20"]
    assert result.trajectory.first_step_index == 10
    assert result.trajectory.successor_by_boundary == {9: 10, 10: 12, 12: 20}
    assert result.trajectory.final_state == {"phase": "advance:2"}
    assert result.trajectory.metrics == {"advance_calls": 2}
