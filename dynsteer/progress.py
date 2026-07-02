from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import sys
import time
from queue import Queue
from typing import Any, Callable, Iterator, Literal, Protocol

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


ProgressEventKind = Literal["case_started", "case_advanced", "case_finished", "case_warning"]


@dataclass(frozen=True)
class CaseProgressEvent:
    """跨线程传递的 case 进度事件。"""

    kind: ProgressEventKind
    case_id: str
    step_count: int = 0
    message: str | None = None


@dataclass
class CaseProgressState:
    """单个 case 的进度条状态。"""

    case_id: str
    started_at: float
    step_count: int = 0
    elapsed_seconds: float = 0.0
    avg_step_seconds: float | None = None


class CaseProgressReporter(Protocol):
    """接收 evaluator 推进事件的协议。"""

    def case_advanced(self, case_id: str, step_count: int) -> None:
        """记录当前 case 新增的轨迹 step 数。"""


class QueueProgressReporter:
    """把 evaluator 进度事件写入线程安全队列。"""

    def __init__(self, events: Queue[CaseProgressEvent]) -> None:
        if events is None:
            raise ValueError("events 不能为空")
        self._events = events

    def case_advanced(self, case_id: str, step_count: int) -> None:
        """写入单个 case 的新增 step 数。"""
        if case_id is None or not case_id.strip():
            raise ValueError("case_id 不能为空")
        if step_count <= 0:
            raise ValueError("step_count 必须大于 0")
        self._events.put(CaseProgressEvent("case_advanced", case_id, step_count=step_count))


class TqdmWarningWriter:
    """用 tqdm.write 风格输出 warning 文本。"""

    def __init__(self, writer: Callable[[str], object] | None = None) -> None:
        self._writer = writer or tqdm.write

    def write_warning(self, message: str) -> None:
        """在进度条下方输出 warning。"""
        if message is None:
            raise ValueError("message 不能为空")
        self._writer(str(message))


class TqdmCaseProgressManager:
    """管理多个未知总步数的 case 进度条。"""

    def __init__(
        self,
        max_workers: int,
        bar_factory: Callable[..., Any] | None = None,
        line_writer: Callable[[str], object] | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        self.max_workers = max_workers
        self.active_order: list[str] = []
        self.case_states: dict[str, CaseProgressState] = {}
        self.bars: dict[str, Any] = {}
        self._bar_factory = bar_factory or tqdm
        self._line_writer = line_writer or (lambda message: tqdm.write(message, file=sys.stderr))
        self._time_fn = time_fn or time.monotonic
        self._had_active_bars = False
        self._line_after_close_written = False

    @property
    def active_count(self) -> int:
        """返回当前活动进度条数量。"""
        return len(self.active_order)

    def case_started(self, case_id: str) -> None:
        """创建指定 case 的进度条。"""
        self._validate_case_id(case_id)
        if case_id in self.active_order:
            return
        if self.active_count >= self.max_workers:
            raise ValueError("活动进度条数量不能超过 max_workers")
        state = self.case_states.get(case_id)
        if state is None:
            state = CaseProgressState(case_id=case_id, started_at=self._time_fn())
            self.case_states[case_id] = state
        self.active_order.append(case_id)
        self._had_active_bars = True
        self.bars[case_id] = self._create_bar(case_id, len(self.active_order) - 1)
        self._refresh_bar(case_id)

    def case_advanced(self, case_id: str, step_count: int) -> None:
        """更新指定 case 的累计 step 数。"""
        self._validate_case_id(case_id)
        if step_count <= 0:
            raise ValueError("step_count 必须大于 0")
        if case_id not in self.case_states:
            self.case_started(case_id)
        state = self.case_states[case_id]
        state.step_count += step_count
        self._refresh_state(state)
        bar = self.bars.get(case_id)
        if bar is not None:
            bar.update(step_count)
            self._set_bar_postfix(bar, state)

    def case_finished(self, case_id: str) -> None:
        """关闭指定 case 进度条，并重建剩余进度条位置。"""
        self._validate_case_id(case_id)
        if case_id not in self.case_states:
            return
        state = self.case_states[case_id]
        self._refresh_state(state)
        bar = self.bars.pop(case_id, None)
        if bar is not None:
            bar.close()
        if case_id in self.active_order:
            self.active_order.remove(case_id)
        self._rebuild_active_bars()

    def close_all(self) -> None:
        """关闭所有仍活动的进度条。"""
        for case_id in list(self.active_order):
            bar = self.bars.pop(case_id, None)
            if bar is not None:
                bar.close()
        self.active_order.clear()
        self._write_line_after_close()

    def _rebuild_active_bars(self) -> None:
        """按当前活动顺序重建进度条位置。"""
        old_bars = dict(self.bars)
        self.bars.clear()
        for bar in old_bars.values():
            bar.close()
        for position, case_id in enumerate(self.active_order):
            self.bars[case_id] = self._create_bar(case_id, position)
            self._refresh_bar(case_id)

    def _create_bar(self, case_id: str, position: int) -> Any:
        state = self.case_states[case_id]
        return self._bar_factory(
            desc=case_id,
            total=None,
            unit="step",
            position=position,
            leave=False,
            initial=state.step_count,
        )

    def _refresh_bar(self, case_id: str) -> None:
        state = self.case_states[case_id]
        self._refresh_state(state)
        bar = self.bars.get(case_id)
        if bar is not None:
            self._set_bar_postfix(bar, state)

    def _refresh_state(self, state: CaseProgressState) -> None:
        state.elapsed_seconds = max(self._time_fn() - state.started_at, 0.0)
        state.avg_step_seconds = state.elapsed_seconds / state.step_count if state.step_count else None

    def _set_bar_postfix(self, bar: Any, state: CaseProgressState) -> None:
        avg_step = f"{state.avg_step_seconds:.2f}s/step" if state.avg_step_seconds is not None else "-"
        bar.set_postfix({"steps": state.step_count, "avg_step": avg_step})

    def _validate_case_id(self, case_id: str) -> None:
        if case_id is None or not str(case_id).strip():
            raise ValueError("case_id 不能为空")

    def _write_line_after_close(self) -> None:
        if not self._had_active_bars or self._line_after_close_written:
            return
        self._line_writer("")
        self._line_after_close_written = True


@contextmanager
def progress_logging_redirect(logger: logging.Logger | None = None) -> Iterator[None]:
    """进度条运行期间将终端 INFO 降噪，并把 warning 输出交给 tqdm。"""
    target_logger = logger or logging.getLogger("dynsteer")
    if not isinstance(target_logger, logging.Logger):
        yield
        return
    handlers = [
        handler
        for handler in target_logger.handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    ]
    original_levels = [handler.level for handler in handlers]
    try:
        for handler in handlers:
            handler.setLevel(logging.WARNING)
        with logging_redirect_tqdm(loggers=[target_logger]):
            yield
    finally:
        for handler, level in zip(handlers, original_levels, strict=False):
            handler.setLevel(level)
