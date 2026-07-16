from __future__ import annotations

import io
from typing import Any, Callable

from dynsteer.progress import TqdmCaseProgressManager
from tqdm import tqdm


def test_finished_progress_bar_keeps_elapsed_in_tqdm_suffix() -> None:
    current_time = [0.0]
    manager = TqdmCaseProgressManager(
        max_workers=1,
        bar_factory=_captured_tqdm_factory(io.StringIO()),
        estimated_total=1000,
        time_fn=lambda: current_time[0],
    )
    try:
        manager.case_started("finished_case", case_index=1)
        current_time[0] = 343.0
        manager.case_advanced("finished_case", 4)
        manager.case_finished("finished_case")

        progress_bar = manager.bars["finished_case"].progress_bar
        rendered = str(progress_bar)

        assert "05:43<00:00" in rendered
        assert "00:00<?" not in rendered
    finally:
        manager.close_all()


def test_rebuilt_progress_bar_keeps_elapsed_after_visible_window_moves_up() -> None:
    current_time = [0.0]
    manager = TqdmCaseProgressManager(
        max_workers=2,
        bar_factory=_captured_tqdm_factory(io.StringIO()),
        estimated_total=1000,
        time_fn=lambda: current_time[0],
        max_visible_bars=2,
    )
    try:
        manager.case_started("finished_case", case_index=1)
        manager.case_started("active_case", case_index=2)
        current_time[0] = 10.0
        manager.case_advanced("finished_case", 1)
        manager.case_finished("finished_case")
        current_time[0] = 120.0
        manager.case_advanced("active_case", 4)
        old_active_bar = manager.bars["active_case"].progress_bar

        manager.case_started("new_case", case_index=3)
        rebuilt_active_bar = manager.bars["active_case"].progress_bar
        rendered = str(rebuilt_active_bar)

        assert rebuilt_active_bar is not old_active_bar
        assert "02:00<" in rendered
        assert "00:00<?" not in rendered
    finally:
        manager.close_all()


def _captured_tqdm_factory(output: io.StringIO) -> Callable[..., tqdm]:
    def factory(**kwargs: Any) -> tqdm:
        kwargs["file"] = output
        kwargs["ascii"] = True
        return tqdm(**kwargs)

    return factory
