import logging
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.adapter.registry import get_harness
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.harness.outputs import write_case_outputs
from dynsteer.model import HarnessCaseTask, HarnessEvaluationOutput
from dynsteer.progress import (
    CaseProgressEvent,
    CaseProgressReporter,
    DEFAULT_PROGRESS_TOTAL,
    QueueProgressReporter,
    TqdmCaseProgressManager,
    progress_logging_redirect,
)


class HarnessCaseExecutionError(RuntimeError):
    """单个 benchmark case 执行失败时抛出。"""

    def __init__(self, benchmark: str, case_id: str, cause: Exception) -> None:
        self.benchmark = benchmark
        self.case_id = case_id
        self.cause = cause
        super().__init__(f"benchmark case 执行失败: benchmark={benchmark}, case_id={case_id}, error={cause}")


def run_case_tasks(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """按最大并发数执行已加载的 case 任务。"""
    if tasks is None or logger is None:
        raise ValueError("tasks 和 logger 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    if any((task is None for task in tasks)):
        raise ValueError("tasks 不能包含空任务")
    if not tasks:
        return []
    if max_workers == 1:
        return _run_tasks_serial(tasks, logger=logger, force_eval=force_eval)
    return _run_tasks_parallel(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)


def _run_case(
    task: HarnessCaseTask,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """构造单个 case 的 harness / evaluator 并执行。"""
    harness: BaseBenchmarkHarness | None = None
    try:
        harness = get_harness(task.config.benchmark)
        evaluator = DynSTEEREvaluator.from_config(task.config)
        return write_case_outputs(
            task.config,
            harness,
            evaluator,
            task.task_case,
            progress_reporter,
            force_eval=force_eval,
        )
    except HarnessCaseExecutionError:
        raise
    except Exception as exc:
        raise HarnessCaseExecutionError(task.config.benchmark, task.task_case.case_id, exc) from exc


def _progress_total_from_tasks(tasks: list[HarnessCaseTask]) -> int:
    """估算本批任务的进度条总步数。"""
    value = tasks[0].config.metadata.get("max_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_PROGRESS_TOTAL
    return max(value, 1)


def _run_tasks_serial(
    tasks: list[HarnessCaseTask],
    *,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """串行执行 case，并复用同一套进度管理器。"""
    manager = TqdmCaseProgressManager(
        max_workers=1,
        estimated_total=_progress_total_from_tasks(tasks),
    )
    outputs: list[HarnessEvaluationOutput] = []
    with progress_logging_redirect(logger):
        try:
            for task in tasks:
                manager.case_started(task.task_case.case_id, case_index=task.order + 1)
                try:
                    outputs.append(_run_case(task, progress_reporter=manager, force_eval=force_eval))
                finally:
                    manager.case_finished(task.task_case.case_id)
        finally:
            manager.close_all()
    return outputs


def _run_tasks_parallel(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """并行执行 case，并由主线程维护进度条。"""
    manager = TqdmCaseProgressManager(
        max_workers=max_workers,
        estimated_total=_progress_total_from_tasks(tasks),
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
            manager.case_started(task.task_case.case_id, case_index=task.order + 1)
            futures[executor.submit(_run_case, task, QueueProgressReporter(events), force_eval)] = task

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
                    manager.case_advanced(event.case_id, event.step_count)
                    continue
                for future in done_futures:
                    task = futures.pop(future)
                    _drain_progress_events(events, manager)
                    try:
                        outputs_by_order[task.order] = future.result()
                    finally:
                        manager.case_finished(task.task_case.case_id)
                    submit_next()
        finally:
            _drain_progress_events(events, manager)
            manager.close_all()
    return [outputs_by_order[task.order] for task in tasks]


def _drain_progress_events(events: Queue[CaseProgressEvent], manager: TqdmCaseProgressManager) -> None:
    """处理 worker 上报的进度事件。"""
    while True:
        try:
            event = events.get_nowait()
        except Empty:
            return
        manager.case_advanced(event.case_id, event.step_count)
