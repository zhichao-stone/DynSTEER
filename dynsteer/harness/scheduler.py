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
    """Throws when individual benchmark case execution fails."""

    def __init__(self, benchmark: str, case_id: str, cause: Exception) -> None:
        self.benchmark = benchmark
        self.case_id = case_id
        self.cause = cause
        super().__init__(f"Benchmark case execution failed: benchmark={benchmark}, case_id={case_id}, error={cause}")


def run_case_tasks(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """Performs the loaded case task by maximum simultaneous distribution."""
    if tasks is None or logger is None:
        raise ValueError('tabs and logger cannot be empty')
    if max_workers < 1:
        raise ValueError('max_workers must be greater than 0')
    if any((task is None for task in tasks)):
        raise ValueError('tasks cannot contain empty tasks')
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
    """Construct and execute the harness and evaluator for one case."""
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
    """Estimate the total progress-bar step count for the tasks in this run."""
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
    """serially execute case and reuse the same set of progress managers."""
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
    """Execute case in parallel and maintain progress bars in the main thread."""
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
    """Handle progress events reported by worker."""
    while True:
        try:
            event = events.get_nowait()
        except Empty:
            return
        manager.case_advanced(event.case_id, event.step_count)
