from __future__ import annotations

from typing import Any

import pytest

from dynsteer.harness.runner import _progress_visible_bars
from dynsteer.progress import TqdmCaseProgressManager


class FakeBar:
    def __init__(self, **kwargs: Any) -> None:
        self.desc = kwargs["desc"]
        self.total = kwargs["total"]
        self.unit = kwargs["unit"]
        self.position = kwargs["position"]
        self.leave = kwargs["leave"]
        self.n = kwargs["initial"]
        self.closed = False
        self.refreshed = False
        self.postfix: dict[str, object] = {}

    def update(self, amount: int) -> None:
        self.n += amount

    def refresh(self) -> None:
        self.refreshed = True

    def set_postfix(self, values: dict[str, object]) -> None:
        self.postfix = dict(values)

    def close(self) -> None:
        self.closed = True


def fake_bar_factory(**kwargs: Any) -> FakeBar:
    return FakeBar(**kwargs)


def test_serial_progress_keeps_latest_five_visible_bars() -> None:
    manager = TqdmCaseProgressManager(
        max_workers=1,
        max_visible_bars=5,
        bar_factory=fake_bar_factory,
        estimated_total=100,
    )

    for index in range(1, 6):
        case_id = f"case_{index}"
        manager.case_started(case_id)
        manager.case_advanced(case_id, 100)
        manager.case_finished(case_id)

    manager.case_started("case_6")

    assert manager.visible_order == ["case_2", "case_3", "case_4", "case_5", "case_6"]
    assert list(manager.bars) == ["case_2", "case_3", "case_4", "case_5", "case_6"]
    assert [bar.position for bar in manager.bars.values()] == [0, 1, 2, 3, 4]
    assert manager.bars["case_2"].total == 100
    assert manager.bars["case_2"].n == 100

    manager.case_advanced("case_6", 100)
    manager.case_finished("case_6")
    manager.case_started("case_7")

    assert manager.visible_order == ["case_3", "case_4", "case_5", "case_6", "case_7"]
    assert list(manager.bars) == ["case_3", "case_4", "case_5", "case_6", "case_7"]
    assert [bar.position for bar in manager.bars.values()] == [0, 1, 2, 3, 4]


def test_visible_window_does_not_increase_active_worker_limit() -> None:
    manager = TqdmCaseProgressManager(
        max_workers=1,
        max_visible_bars=5,
        bar_factory=fake_bar_factory,
    )

    manager.case_started("case_1")

    with pytest.raises(ValueError, match="活动进度条数量不能超过 max_workers"):
        manager.case_started("case_2")


def test_progress_visible_bars_uses_at_least_five_and_worker_count_when_larger() -> None:
    assert _progress_visible_bars(1) == 5
    assert _progress_visible_bars(3) == 5
    assert _progress_visible_bars(5) == 5
    assert _progress_visible_bars(8) == 8


def test_finished_bar_is_not_rebuilt_when_total_converges_and_next_case_starts() -> None:
    manager = TqdmCaseProgressManager(
        max_workers=1,
        max_visible_bars=5,
        bar_factory=fake_bar_factory,
        estimated_total=100,
    )

    manager.case_started("case_1")
    first_bar = manager.bars["case_1"]
    manager.case_advanced("case_1", 5)
    manager.case_finished("case_1")

    assert manager.bars["case_1"] is first_bar
    assert first_bar.total == 5
    assert first_bar.n == 5
    assert first_bar.refreshed is True

    manager.case_started("case_2")

    assert manager.bars["case_1"] is first_bar
    assert manager.bars["case_2"].position == 1


def test_visible_window_never_evicts_active_case() -> None:
    manager = TqdmCaseProgressManager(
        max_workers=2,
        max_visible_bars=3,
        bar_factory=fake_bar_factory,
    )

    manager.case_started("case_1")
    manager.case_started("case_2")
    manager.case_advanced("case_2", 10)
    manager.case_finished("case_2")
    manager.case_started("case_3")
    manager.case_advanced("case_3", 10)
    manager.case_finished("case_3")
    manager.case_started("case_4")

    assert "case_1" in manager.bars
    assert "case_2" not in manager.bars
    assert manager.visible_order == ["case_1", "case_3", "case_4"]
    assert list(manager.bars) == ["case_1", "case_3", "case_4"]
    assert [bar.position for bar in manager.bars.values()] == [0, 1, 2]
