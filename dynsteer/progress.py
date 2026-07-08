from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import sys
import time
from queue import Queue
from typing import Any, Callable, Iterator, Literal, Protocol

from tqdm import tqdm


ProgressEventKind = Literal["case_started", "case_advanced", "case_finished"]
DEFAULT_PROGRESS_TOTAL = 100
DEFAULT_VISIBLE_PROGRESS_BARS = 5
TERMINAL_LOG_SILENT_LEVEL = logging.CRITICAL + 1


class _SilentStream:
    """进度条运行期间吞掉第三方 stdout/stderr 文本。"""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


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
    finished: bool = False


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


class TqdmCaseProgressManager:
    """管理多个未知总步数的 case 进度条。"""

    def __init__(
        self,
        max_workers: int,
        bar_factory: Callable[..., Any] | None = None,
        line_writer: Callable[[str], object] | None = None,
        estimated_total: int = DEFAULT_PROGRESS_TOTAL,
        time_fn: Callable[[], float] | None = None,
        max_visible_bars: int = DEFAULT_VISIBLE_PROGRESS_BARS,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        if estimated_total < 1:
            raise ValueError("estimated_total 必须大于 0")
        if max_visible_bars < max_workers:
            raise ValueError("max_visible_bars 不能小于 max_workers")
        self.max_workers = max_workers
        self.max_visible_bars = max_visible_bars
        self.estimated_total = estimated_total
        self.active_order: list[str] = []
        self.visible_order: list[str] = []
        self.case_states: dict[str, CaseProgressState] = {}
        self.bars: dict[str, Any] = {}
        self._bar_factory = bar_factory or tqdm
        self._line_writer = line_writer or (lambda message: tqdm.write(message, file=sys.__stderr__))
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
        state.finished = False
        self.active_order.append(case_id)
        if case_id in self.visible_order:
            self.visible_order.remove(case_id)
        self.visible_order.append(case_id)
        self._trim_visible_order()
        self._had_active_bars = True
        self._rebuild_visible_bars()

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
            self._grow_bar_total(bar, state.step_count)
            bar.update(step_count)
            self._set_bar_postfix(bar, state)

    def case_finished(self, case_id: str) -> None:
        """标记指定 case 完成，并在可见窗口中保留满进度条。"""
        self._validate_case_id(case_id)
        if case_id not in self.case_states:
            return
        state = self.case_states[case_id]
        self._refresh_state(state)
        state.finished = True
        if case_id in self.active_order:
            self.active_order.remove(case_id)
        if case_id not in self.visible_order:
            self.visible_order.append(case_id)
        bar = self.bars.get(case_id)
        if bar is not None:
            self._finish_bar(bar, state)
        self._trim_visible_order()
        self._rebuild_visible_bars()

    def close_all(self) -> None:
        """关闭所有可见进度条，并保留最终窗口。"""
        for case_id in list(self.visible_order):
            bar = self.bars.pop(case_id, None)
            state = self.case_states[case_id]
            self._refresh_state(state)
            if bar is not None:
                if state.finished:
                    self._finish_bar(bar, state)
                self._close_bar(bar, leave=True)
        self.active_order.clear()
        self.visible_order.clear()
        self._write_line_after_close()

    def _rebuild_visible_bars(self) -> None:
        """按当前可见顺序重建进度条位置。"""
        old_bars = dict(self.bars)
        self.bars.clear()
        for bar in old_bars.values():
            self._close_bar(bar, leave=False)
        for position, case_id in enumerate(self.visible_order):
            self.bars[case_id] = self._create_bar(case_id, position)
            self._refresh_bar(case_id)

    def _create_bar(self, case_id: str, position: int) -> Any:
        state = self.case_states[case_id]
        return self._bar_factory(
            desc=case_id,
            total=state.step_count if state.finished else max(self.estimated_total, state.step_count),
            unit="step",
            position=position,
            leave=False,
            initial=state.step_count,
            file=sys.__stderr__,
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

    def _grow_bar_total(self, bar: Any, step_count: int) -> None:
        current_total = getattr(bar, "total", None)
        if not isinstance(current_total, int | float) or step_count <= current_total:
            return
        bar.total = max(step_count, int(current_total) * 2, self.estimated_total)

    def _finish_bar(self, bar: Any, state: CaseProgressState) -> None:
        bar.total = state.step_count
        refresh = getattr(bar, "refresh", None)
        if callable(refresh):
            refresh()
        bar.update(0)

    def _trim_visible_order(self) -> None:
        """保留固定数量的可见进度条，优先移除最早完成的 case。"""
        while len(self.visible_order) > self.max_visible_bars:
            evicted = self._oldest_finished_visible_case()
            if evicted is None:
                return
            self.visible_order.remove(evicted)
            bar = self.bars.pop(evicted, None)
            if bar is not None:
                self._close_bar(bar, leave=False)

    def _oldest_finished_visible_case(self) -> str | None:
        """返回最早进入窗口且已经完成的 case。"""
        for case_id in self.visible_order:
            state = self.case_states.get(case_id)
            if state is not None and state.finished and case_id not in self.active_order:
                return case_id
        return None

    def _close_bar(self, bar: Any, leave: bool) -> None:
        """关闭 tqdm bar，并按需保留终端行。"""
        setattr(bar, "leave", leave)
        bar.close()

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
    """进度条运行期间静默终端日志，保留文件与缓冲区日志。"""
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
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    silent_stream = _SilentStream()
    try:
        for handler in handlers:
            handler.setLevel(TERMINAL_LOG_SILENT_LEVEL)
        sys.stdout = silent_stream  # type: ignore[assignment]
        sys.stderr = silent_stream  # type: ignore[assignment]
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        for handler, level in zip(handlers, original_levels, strict=False):
            handler.setLevel(level)
