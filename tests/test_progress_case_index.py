from __future__ import annotations

from contextlib import nullcontext
import logging
from pathlib import Path
from typing import Any

from dynsteer.harness import scheduler
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.scheduler import HarnessCaseTask
from dynsteer.model import TaskCase
from dynsteer.progress import TqdmCaseProgressManager


def test_title_bar_formats_case_index_before_case_id() -> None:
    bars: list[_FakeBar] = []

    def bar_factory(**kwargs: Any) -> _FakeBar:
        bar = _FakeBar(**kwargs)
        bars.append(bar)
        return bar

    manager = TqdmCaseProgressManager(max_workers=1, bar_factory=bar_factory, time_fn=lambda: 0.0)

    manager.case_started("alpha_{case}", case_index=3)

    assert bars[0].kwargs["bar_format"] == "# Test Case 3: alpha_{{case}}"
    assert bars[1].kwargs["desc"] == "Case 3执行进度（最多1000步）"


def test_progress_postfix_uses_state_elapsed_time() -> None:
    bars: list[_FakeBar] = []
    current_time = 100.0

    def time_fn() -> float:
        return current_time

    def bar_factory(**kwargs: Any) -> _FakeBar:
        bar = _FakeBar(**kwargs)
        bars.append(bar)
        return bar

    manager = TqdmCaseProgressManager(max_workers=1, bar_factory=bar_factory, time_fn=time_fn)
    manager.case_started("alpha", case_index=1)

    current_time = 130.0
    manager.case_advanced("alpha", 5)

    assert bars[1].postfix == {"elapsed": "00:30", "steps": 5, "avg_step": "6.00s/step"}


def test_rebuilt_bar_keeps_state_elapsed_postfix() -> None:
    bars: list[_FakeBar] = []
    current_time = 10.0

    def time_fn() -> float:
        return current_time

    def bar_factory(**kwargs: Any) -> _FakeBar:
        bar = _FakeBar(**kwargs)
        bars.append(bar)
        return bar

    manager = TqdmCaseProgressManager(
        max_workers=2,
        max_visible_bars=2,
        bar_factory=bar_factory,
        time_fn=time_fn,
    )
    manager.case_started("case-1", case_index=1)
    manager.case_started("case-2", case_index=2)

    current_time = 20.0
    manager.case_advanced("case-2", 2)
    manager.case_finished("case-1")
    manager.case_started("case-3", case_index=3)

    rebuilt_case_2_progress = [bar for bar in bars if bar.kwargs.get("desc") == "Case 2执行进度（最多1000步）"][
        -1
    ]
    assert rebuilt_case_2_progress.postfix == {"elapsed": "00:10", "steps": 2, "avg_step": "5.00s/step"}


def test_close_all_writes_stable_final_snapshot_without_tqdm_leave() -> None:
    bars: list[_FakeBar] = []
    lines: list[str] = []
    current_time = 0.0

    def time_fn() -> float:
        return current_time

    def bar_factory(**kwargs: Any) -> _FakeBar:
        bar = _FakeBar(**kwargs)
        bars.append(bar)
        return bar

    manager = TqdmCaseProgressManager(
        max_workers=1,
        bar_factory=bar_factory,
        line_writer=lines.append,
        time_fn=time_fn,
    )
    manager.case_started("alpha", case_index=1)

    current_time = 12.0
    manager.case_advanced("alpha", 3)
    manager.case_finished("alpha")
    manager.close_all()

    assert [bar.leave for bar in bars] == [False, False]
    assert lines == [
        "# Test Case 1: alpha",
        "Case 1执行进度（最多1000步）: 100%| 3/3 [elapsed=00:12, steps=3, avg_step=4.00s/step]",
    ]


def test_serial_scheduler_passes_one_based_case_index(monkeypatch, tmp_path: Path) -> None:
    started_cases: list[tuple[str, int]] = []

    class FakeProgressManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def case_started(self, case_id: str, *, case_index: int) -> None:
            started_cases.append((case_id, case_index))

        def case_finished(self, case_id: str) -> None:
            pass

        def close_all(self) -> None:
            pass

    monkeypatch.setattr(scheduler, "TqdmCaseProgressManager", FakeProgressManager)
    monkeypatch.setattr(scheduler, "progress_logging_redirect", lambda logger: nullcontext())
    monkeypatch.setattr(scheduler, "_run_case", lambda task, progress_reporter=None: object())

    tasks = [
        _task(order=0, case_id="alpha", tmp_path=tmp_path),
        _task(order=1, case_id="beta", tmp_path=tmp_path),
    ]

    scheduler._run_tasks_serial(tasks, logger=logging.getLogger("test-progress-case-index"))

    assert started_cases == [("alpha", 1), ("beta", 2)]


class _FakeBar:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.total = kwargs.get("total", 0)
        self.leave = kwargs.get("leave", False)
        self.closed = False

    def set_postfix(self, values: dict[str, object]) -> None:
        self.postfix = values

    def update(self, step_count: int) -> None:
        self.step_count = step_count

    def refresh(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _task(order: int, case_id: str, tmp_path: Path) -> HarnessCaseTask:
    config = HarnessRunConfig(benchmark="testbench", data_root=tmp_path, metadata={"max_messages": 10})
    task_case = TaskCase(task_id=f"task-{case_id}", task_description="test", case_id=case_id)
    return HarnessCaseTask(order=order, config=config, case_id=case_id, task_case=task_case)
