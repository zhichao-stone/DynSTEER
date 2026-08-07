from contextlib import contextmanager
import logging
import sys
import time
from queue import Queue
from typing import Any, Callable, Iterator, Protocol

from tqdm import tqdm

from dynsteer.model import CaseProgressEvent, CaseProgressState


DEFAULT_PROGRESS_TOTAL = 1000
TERMINAL_LOG_SILENT_LEVEL = logging.CRITICAL + 1


class CaseProgressReporter(Protocol):
    """接收 evaluator 推进事件的协议。"""

    def case_advanced(self, case_id: str, step_count: int) -> None:
        """记录当前 case 新增的轨迹 step 数。"""


class QueueProgressReporter:
    """把 evaluator 进度事件写入线程安全队列。"""

    def __init__(self, events: Queue[CaseProgressEvent]) -> None:
        self._events = events

    def case_advanced(self, case_id: str, step_count: int) -> None:
        """写入单个 case 的新增 step 数。"""
        if case_id is None or not case_id.strip():
            raise ValueError("case_id 不能为空")
        if step_count <= 0:
            raise ValueError("step_count 必须大于 0")
        self._events.put(CaseProgressEvent(case_id, step_count=step_count))


class TqdmCaseProgressManager:
    """管理多个 case 的简洁 tqdm 进度条。"""

    def __init__(
        self,
        max_workers: int,
        bar_factory: Callable[..., Any] | None = None,
        estimated_total: int = DEFAULT_PROGRESS_TOTAL,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        if estimated_total < 1:
            raise ValueError("estimated_total 必须大于 0")
        self.max_workers = max_workers
        self.estimated_total = estimated_total
        self.active_order: list[str] = []
        self.case_states: dict[str, CaseProgressState] = {}
        self.bars: dict[str, Any] = {}
        self._bar_factory = bar_factory or tqdm
        self._time_fn = time_fn or time.monotonic

    def case_started(self, case_id: str, case_index: int | None = None) -> None:
        """创建指定 case 的进度条。"""
        self._validate_case_id(case_id)
        if case_index is not None and (isinstance(case_index, bool) or not isinstance(case_index, int) or case_index < 1):
            raise ValueError("case_index 必须大于 0")
        if case_id in self.active_order:
            return
        if len(self.active_order) >= self.max_workers:
            raise ValueError("活动进度条数量不能超过 max_workers")
        state = self.case_states.get(case_id)
        if state is None:
            state = CaseProgressState(case_id=case_id, started_at=self._time_fn(), case_index=case_index)
            self.case_states[case_id] = state
        elif case_index is not None:
            state.case_index = case_index
        self.active_order.append(case_id)
        self.bars[case_id] = self._create_bar(state)

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
            if state.step_count > int(getattr(bar, "total", self.estimated_total) or self.estimated_total):
                bar.total = max(state.step_count, self.estimated_total)
            bar.update(step_count)
            bar.set_postfix(self._timing_postfix(state))

    def case_finished(self, case_id: str) -> None:
        """标记指定 case 完成并关闭进度条。"""
        self._validate_case_id(case_id)
        state = self.case_states.get(case_id)
        if state is None:
            return
        self._refresh_state(state)
        if case_id in self.active_order:
            self.active_order.remove(case_id)
        bar = self.bars.pop(case_id, None)
        if bar is not None:
            bar.total = max(state.step_count, 1)
            bar.set_postfix(self._timing_postfix(state))
            bar.close()

    def close_all(self) -> None:
        """关闭所有仍然可见的进度条。"""
        for bar in list(self.bars.values()):
            bar.close()
        self.bars.clear()
        self.active_order.clear()

    def _create_bar(self, state: CaseProgressState) -> Any:
        return self._bar_factory(
            desc=self._progress_description(state),
            total=max(self.estimated_total, state.step_count),
            unit="step",
            position=len(self.bars),
            leave=False,
            initial=state.step_count,
            file=sys.__stderr__,
        )

    def _refresh_state(self, state: CaseProgressState) -> None:
        state.elapsed_seconds = max(self._time_fn() - state.started_at, 0.0)
        state.avg_step_seconds = state.elapsed_seconds / state.step_count if state.step_count else None

    def _progress_description(self, state: CaseProgressState) -> str:
        return f"Case {state.case_index}执行进度（最多{self.estimated_total}步）" if state.case_index is not None else f"执行进度（最多{self.estimated_total}步）"

    def _validate_case_id(self, case_id: str) -> None:
        if case_id is None or not str(case_id).strip():
            raise ValueError("case_id 不能为空")

    def _timing_postfix(self, state: CaseProgressState) -> dict[str, object]:
        """构造 tqdm 耗时字段。"""
        return {
            "elapsed": tqdm.format_interval(max(state.elapsed_seconds, 0.0)),
            "steps": state.step_count,
            "avg_step": f"{state.avg_step_seconds:.2f}s/step" if state.avg_step_seconds is not None else "-",
        }


@contextmanager
def progress_logging_redirect(logger: logging.Logger | None = None) -> Iterator[None]:
    """进度条运行期间静默终端日志，保留文件与缓冲区日志。"""
    target_logger = logger or logging.getLogger("dynsteer")
    if not isinstance(target_logger, logging.Logger):
        yield
        return
    handlers = [handler for handler in target_logger.handlers if isinstance(handler, logging.StreamHandler) and (not isinstance(handler, logging.FileHandler))]
    original_levels = [handler.level for handler in handlers]
    try:
        for handler in handlers:
            handler.setLevel(TERMINAL_LOG_SILENT_LEVEL)
        yield
    finally:
        for handler, level in zip(handlers, original_levels, strict=False):
            handler.setLevel(level)
