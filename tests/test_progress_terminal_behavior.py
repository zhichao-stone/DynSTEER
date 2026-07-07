from __future__ import annotations

import logging
import sys

from dynsteer.progress import TqdmCaseProgressManager, progress_logging_redirect
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.runner import _HarnessCaseTask, _progress_total_from_tasks
from dynsteer.model import TaskCase


class FakeBar:
    def __init__(self, **kwargs: object) -> None:
        self.total = kwargs.get("total")
        self.n = int(kwargs.get("initial", 0))
        self.closed = False
        self.refresh_count = 0
        self.updates: list[int] = []

    def update(self, step_count: int) -> None:
        self.n += step_count
        self.updates.append(step_count)

    def set_postfix(self, data: dict[str, object]) -> None:
        pass

    def refresh(self) -> None:
        self.refresh_count += 1

    def close(self) -> None:
        self.closed = True


def test_progress_suppresses_terminal_logs() -> None:
    logger = logging.getLogger("test_progress_suppresses_terminal_logs")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setLevel(logging.NOTSET)
    logger.addHandler(handler)

    with progress_logging_redirect(logger):
        assert handler.level > logging.CRITICAL

    assert handler.level == logging.NOTSET


def test_progress_suppresses_direct_terminal_writes(capsys) -> None:  # type: ignore[no-untyped-def]
    with progress_logging_redirect(logging.getLogger("test_progress_suppresses_direct_terminal_writes")):
        print("stdout noise")
        print("stderr noise", file=sys.stderr)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_progress_uses_estimated_total_and_closes_at_actual_steps() -> None:
    bars: list[FakeBar] = []
    manager = TqdmCaseProgressManager(
        max_workers=1,
        bar_factory=lambda **kwargs: bars.append(FakeBar(**kwargs)) or bars[-1],
        line_writer=lambda message: None,
        estimated_total=100,
    )

    manager.case_started("case_1")
    manager.case_advanced("case_1", 3)
    manager.case_finished("case_1")

    assert bars[0].total == 3
    assert bars[0].updates == [3, 0]
    assert bars[0].refresh_count == 1
    assert bars[0].closed is True


def test_progress_total_uses_task_config_max_messages(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        metadata={"max_messages": 30},
    )
    task = _HarnessCaseTask(
        order=0,
        config=config,
        case_id="case",
        task_case=TaskCase(task_id="task", task_description="task", case_id="case"),
    )

    assert _progress_total_from_tasks([task]) == 30
