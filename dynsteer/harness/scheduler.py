from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty, Queue

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.adapter.registry import get_harness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import HarnessEvaluationOutput, write_case_outputs
from dynsteer.harness.selection import config_with_case_ids
from dynsteer.model import TaskCase
from dynsteer.progress import (
    CaseProgressEvent,
    CaseProgressReporter,
    DEFAULT_PROGRESS_TOTAL,
    DEFAULT_VISIBLE_PROGRESS_BARS,
    QueueProgressReporter,
    TqdmCaseProgressManager,
    progress_logging_redirect,
)
import logging


@dataclass(frozen=True)
class HarnessCaseTask:
    """已加载 TaskCase 后的单 case 执行任务。"""

    order: int
    config: HarnessRunConfig
    case_id: str
    task_case: TaskCase


class HarnessCaseExecutionError(RuntimeError):
    """单个 benchmark case 执行失败时抛出。"""

    def __init__(self, benchmark: str, run_id: str | None, case_id: str, cause: Exception) -> None:
        self.benchmark = benchmark
        self.run_id = run_id
        self.case_id = case_id
        self.cause = cause
        super().__init__(
            f"benchmark case 执行失败: benchmark={benchmark}, run_id={run_id}, "
            f"case_id={case_id}, error={cause}"
        )


class _DirectProgressReporter:
    """串行执行时直接更新主线程 progress manager。"""

    def __init__(self, manager: TqdmCaseProgressManager) -> None:
        if manager is None:
            raise ValueError("manager 不能为空")
        self._manager = manager

    def case_advanced(self, case_id: str, step_count: int) -> None:
        self._manager.case_advanced(case_id, step_count)


def run_case_tasks(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
) -> list[HarnessEvaluationOutput]:
    """按最大并发数执行已加载的 case 任务，并维护进度条。"""
    if tasks is None or logger is None:
        raise ValueError("tasks 和 logger 不能为空")
    if not tasks:
        return []
    if max_workers == 1:
        return _run_tasks_serial(tasks, logger=logger)
    return _run_tasks_parallel(tasks, max_workers=max_workers, logger=logger)


def _progress_visible_bars(max_workers: int) -> int:
    """根据并发数计算终端可见进度条窗口大小。"""
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    return max(DEFAULT_VISIBLE_PROGRESS_BARS, max_workers)


def _run_case(
    task: HarnessCaseTask,
    progress_reporter: CaseProgressReporter | None = None,
) -> HarnessEvaluationOutput:
    """构造独立 harness/evaluator 并执行单个 case。"""
    if task is None:
        raise ValueError("task 不能为空")
    harness: BaseBenchmarkHarness | None = None
    try:
        harness = get_harness(task.config.benchmark)
        evaluator = DynSTEEREvaluator.from_env()
        case_config = config_with_case_ids(task.config, [task.case_id])
        return write_case_outputs(case_config, harness, evaluator, task.task_case, progress_reporter)
    except HarnessCaseExecutionError:
        raise
    except Exception as exc:
        run_id = _safe_run_id(task.config, harness, task.case_id) if harness is not None else None
        raise HarnessCaseExecutionError(task.config.benchmark, run_id, task.case_id, exc) from exc


def _progress_total_from_tasks(tasks: list[HarnessCaseTask]) -> int:
    """读取当前任务组的进度条估算总步数。"""
    if tasks is None or not tasks:
        return DEFAULT_PROGRESS_TOTAL
    value = tasks[0].config.metadata.get("max_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_PROGRESS_TOTAL
    return max(value, 1)


def _run_tasks_serial(
    tasks: list[HarnessCaseTask],
    *,
    logger: logging.Logger,
) -> list[HarnessEvaluationOutput]:
    """串行执行 case，并复用同一套进度管理器。"""
    manager = TqdmCaseProgressManager(
        max_workers=1,
        estimated_total=_progress_total_from_tasks(tasks),
        max_visible_bars=_progress_visible_bars(1),
    )
    outputs: list[HarnessEvaluationOutput] = []
    with progress_logging_redirect(logger):
        try:
            for task in tasks:
                manager.case_started(task.case_id, case_index=task.order + 1)
                try:
                    outputs.append(_run_case(task, progress_reporter=_DirectProgressReporter(manager)))
                finally:
                    manager.case_finished(task.case_id)
        finally:
            manager.close_all()
    return outputs


def _run_tasks_parallel(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
) -> list[HarnessEvaluationOutput]:
    """并行执行 case，主线程通过 queue 维护进度条。"""
    manager = TqdmCaseProgressManager(
        max_workers=max_workers,
        estimated_total=_progress_total_from_tasks(tasks),
        max_visible_bars=_progress_visible_bars(max_workers),
    )
    events: Queue[CaseProgressEvent] = Queue()
    outputs_by_order: dict[int, HarnessEvaluationOutput] = {}
    next_index = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor, progress_logging_redirect(logger):
        futures: dict[object, HarnessCaseTask] = {}

        def submit_next() -> None:
            nonlocal next_index
            if next_index >= len(tasks):
                return
            task = tasks[next_index]
            next_index += 1
            manager.case_started(task.case_id, case_index=task.order + 1)
            futures[executor.submit(_run_case, task, QueueProgressReporter(events))] = task

        for _ in range(min(max_workers, len(tasks))):
            submit_next()

        try:
            while futures:
                _drain_progress_events(events, manager)
                done_futures = [future for future in list(futures) if future.done()]
                if not done_futures:
                    try:
                        event = events.get(timeout=0.05)
                    except Empty:
                        continue
                    _apply_progress_event(event, manager)
                    continue
                for future in done_futures:
                    task = futures.pop(future)
                    _drain_progress_events(events, manager)
                    try:
                        outputs_by_order[task.order] = future.result()
                    finally:
                        manager.case_finished(task.case_id)
                    submit_next()
        finally:
            _drain_progress_events(events, manager)
            manager.close_all()
    return [outputs_by_order[task.order] for task in tasks]


def _drain_progress_events(
    events: Queue[CaseProgressEvent],
    manager: TqdmCaseProgressManager,
) -> None:
    """处理 worker 已上报的进度事件。"""
    if events is None or manager is None:
        raise ValueError("events 和 manager 不能为空")
    while True:
        try:
            event = events.get_nowait()
        except Empty:
            return
        _apply_progress_event(event, manager)


def _apply_progress_event(
    event: CaseProgressEvent,
    manager: TqdmCaseProgressManager,
) -> None:
    """把单个进度事件应用到 progress manager。"""
    if event is None or manager is None:
        raise ValueError("event 和 manager 不能为空")
    if event.kind == "case_advanced":
        manager.case_advanced(event.case_id, event.step_count)
    elif event.kind == "case_started":
        manager.case_started(event.case_id)
    elif event.kind == "case_finished":
        manager.case_finished(event.case_id)


def _safe_run_id(config: HarnessRunConfig, harness: BaseBenchmarkHarness, case_id: str) -> str | None:
    """尽量构造 run_id，用于异常上下文。"""
    try:
        return harness.build_run_id(config, case_id)
    except Exception:
        raw_run_id = config.metadata.get("run_id")
        return str(raw_run_id) if raw_run_id is not None else None
