from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Queue

from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.adapter.loader import load_task_case, trajectory_to_json
from dynsteer.adapter.registry import get_adapter
from dynsteer.evaluate import DynSTEEREvaluator
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.log import configure_logger
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


@dataclass(frozen=True)
class HarnessEvaluationOutput:
    """harness 运行与 DynSTEER 评估输出路径。"""

    run_dir: Path
    raw_run_dir: Path
    result_dir: Path
    report_path: Path
    summary_path: Path
    raw_summary_path: Path
    trajectory_path: Path


class HarnessCaseExecutionError(RuntimeError):
    """单个 benchmark case 执行失败时抛出。"""

    def __init__(self, benchmark: str, run_id: str | None, case_id: str, cause: Exception) -> None:
        """初始化 case 执行异常。"""
        self.benchmark = benchmark
        self.run_id = run_id
        self.case_id = case_id
        self.cause = cause
        super().__init__(
            f"benchmark case 执行失败: benchmark={benchmark}, run_id={run_id}, "
            f"case_id={case_id}, error={cause}"
        )


@dataclass(frozen=True)
class _HarnessCaseTask:
    """已加载 TaskCase 后的单 case 执行任务。"""

    order: int
    config: HarnessRunConfig
    case_id: str
    task_case: TaskCase


def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
) -> list[HarnessEvaluationOutput]:
    """运行多组 harness 配置，并按 config 顺序执行其中的 case。

    Args:
        configs: harness 运行配置列表。
        max_workers: 单个 config 内 case 最大并行 worker 数；1 表示串行。

    Returns:
        按配置与 case 顺序排列的输出路径集合。
    """
    if configs is None:
        raise ValueError("configs 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")

    logger = _get_or_configure_harness_logger(_log_dir_from_configs(configs))
    outputs: list[HarnessEvaluationOutput] = []
    for config in configs:
        if config is None:
            raise ValueError("configs 不能包含空配置")
        config_outputs = _run_config(config, max_workers=_effective_max_workers(max_workers, config), logger=logger)
        outputs.extend(config_outputs)
    return outputs


def _effective_max_workers(max_workers: int, config: HarnessRunConfig) -> int:
    """计算当前配置实际可使用的 worker 数。"""
    if config is None:
        raise ValueError("config 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    benchmark_max_workers = config.metadata.get("benchmark_max_workers")
    if benchmark_max_workers is None:
        return max_workers
    if isinstance(benchmark_max_workers, bool) or not isinstance(benchmark_max_workers, int):
        raise ValueError("benchmark_max_workers 必须是整数")
    return max(1, min(max_workers, max(benchmark_max_workers, 1)))


def _run_config(
    config: HarnessRunConfig,
    *,
    max_workers: int,
    logger: logging.Logger,
) -> list[HarnessEvaluationOutput]:
    """按单个 config 加载 TaskCase 列表并执行。"""
    if config is None or logger is None:
        raise ValueError("config 和 logger 不能为空")
    adapter = get_adapter(config.benchmark)
    harness = adapter.create_harness()
    case_ids = _select_case_ids(config, harness, run_all=True)
    run_config = _config_with_case_ids(config, case_ids)
    harness.prepare_config(run_config)
    task_cases = load_task_case(run_config, adapter)
    _validate_loaded_task_cases(case_ids, task_cases)
    label = _config_label(run_config)
    logger.info(
        "基于配置%s，开始基于 %s 展开评估，Cases数量: %s",
        label,
        run_config.benchmark,
        len(task_cases),
        extra={
            "benchmark": run_config.benchmark,
            "case_count": len(task_cases),
            "run_config": label,
        },
    )

    tasks = [
        _HarnessCaseTask(order=index, config=run_config, case_id=task_case.case_id, task_case=task_case)
        for index, task_case in enumerate(task_cases)
    ]
    outputs = _run_tasks(tasks, max_workers=max_workers, logger=logger)
    _write_run_level_summaries(outputs)
    return outputs


class _DirectProgressReporter:
    """串行执行时直接更新主线程 progress manager。"""

    def __init__(self, manager: TqdmCaseProgressManager) -> None:
        if manager is None:
            raise ValueError("manager 不能为空")
        self._manager = manager

    def case_advanced(self, case_id: str, step_count: int) -> None:
        self._manager.case_advanced(case_id, step_count)


def _progress_total_from_tasks(tasks: list[_HarnessCaseTask]) -> int:
    """读取当前任务组的进度条估算总步数。"""
    if tasks is None or not tasks:
        return DEFAULT_PROGRESS_TOTAL
    
    ## 读取当前运行配置的进度条估算总步数。
    config = tasks[0].config
    if config is None:
        raise ValueError("config 不能为空")
    value = config.metadata.get("max_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_PROGRESS_TOTAL
    
    return max(value, 1)


def _progress_visible_bars(max_workers: int) -> int:
    """根据并发数计算终端可见进度条窗口大小。"""
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    return max(DEFAULT_VISIBLE_PROGRESS_BARS, max_workers)


def _run_tasks(
    tasks: list[_HarnessCaseTask],
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


def _run_tasks_serial(
    tasks: list[_HarnessCaseTask],
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
                manager.case_started(task.case_id)
                try:
                    outputs.append(_run_case(task, progress_reporter=_DirectProgressReporter(manager)))
                finally:
                    manager.case_finished(task.case_id)
        finally:
            manager.close_all()
    return outputs


def _run_tasks_parallel(
    tasks: list[_HarnessCaseTask],
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
        futures: dict[object, _HarnessCaseTask] = {}

        def submit_next() -> None:
            nonlocal next_index
            if next_index >= len(tasks):
                return
            task = tasks[next_index]
            next_index += 1
            manager.case_started(task.case_id)
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


def _validate_loaded_task_cases(case_ids: list[str], task_cases: list[TaskCase]) -> None:
    """校验 loader 返回的 TaskCase 与当前 config case 顺序一致。"""
    if case_ids is None or task_cases is None:
        raise ValueError("case_ids 和 task_cases 不能为空")
    loaded_case_ids = [task_case.case_id for task_case in task_cases]
    if loaded_case_ids != case_ids:
        raise ValueError(f"加载的 TaskCase 顺序与配置不一致: {loaded_case_ids}")


def _config_label(config: HarnessRunConfig) -> str:
    """构造日志中使用的运行配置标签。"""
    if config is None:
        raise ValueError("config 不能为空")
    name = config.metadata.get("run_config_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    run_id = config.metadata.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    index = config.metadata.get("run_config_index")
    return f"第{index}项配置" if index is not None else "未命名配置"


def _log_dir_from_configs(configs: list[HarnessRunConfig]) -> Path:
    """获取本批 harness 日志目录。"""
    if configs is None:
        raise ValueError("configs 不能为空")
    if configs:
        return configs[0].runs_dir / "logs"
    return Path("runs") / "logs"


def _get_or_configure_harness_logger(log_dir: Path) -> logging.Logger:
    """获取已配置 logger；未配置时初始化一次。"""
    if log_dir is None:
        raise ValueError("log_dir 不能为空")
    logger = logging.getLogger("dynsteer")
    if logger.handlers:
        return logger
    return configure_logger(log_dir)


def _select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness, run_all: bool) -> list[str]:
    """根据配置选择要运行的 benchmark case ID。"""
    if config is None or harness is None:
        raise ValueError("config 和 harness 不能为空")
    cases = harness.list_cases(config)
    if not cases:
        raise ValueError("benchmark 没有可运行场景")
    known_case_ids = {case.case_id for case in cases}
    if config.case_ids is not None:
        missing = [case_id for case_id in config.case_ids if case_id not in known_case_ids]
        if missing:
            raise KeyError(f"benchmark 场景不存在: {missing[0]}")
        return list(config.case_ids)
    if run_all:
        return [case.case_id for case in cases]
    return [cases[0].case_id]


def _config_with_case_ids(config: HarnessRunConfig, case_ids: list[str]) -> HarnessRunConfig:
    """返回写入本次展开 case_ids 的运行配置。"""
    if config is None or case_ids is None:
        raise ValueError("config 和 case_ids 不能为空")
    if not case_ids:
        raise ValueError("case_ids 不能为空")
    return replace(config, case_ids=tuple(case_ids))


def _safe_run_id(config: HarnessRunConfig, harness: BaseBenchmarkHarness, case_id: str) -> str | None:
    """尽量构造 run_id，用于异常上下文。"""
    try:
        return harness.build_run_id(config, case_id)
    except Exception:
        raw_run_id = config.metadata.get("run_id")
        return str(raw_run_id) if raw_run_id is not None else None


def _run_case(
    task: _HarnessCaseTask,
    progress_reporter: CaseProgressReporter | None = None,
) -> HarnessEvaluationOutput:
    """构造独立 harness/evaluator 并执行单个 case。"""
    if task is None:
        raise ValueError("task 不能为空")
    harness: BaseBenchmarkHarness | None = None
    try:
        adapter = get_adapter(task.config.benchmark)
        harness = adapter.create_harness()
        evaluator = DynSTEEREvaluator.from_env()
        case_config = _config_with_case_ids(task.config, [task.case_id])
        return _write_case_outputs(case_config, harness, evaluator, task.task_case, progress_reporter)
    except HarnessCaseExecutionError:
        raise
    except Exception as exc:
        run_id = _safe_run_id(task.config, harness, task.case_id) if harness is not None else None
        raise HarnessCaseExecutionError(task.config.benchmark, run_id, task.case_id, exc) from exc


def _write_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
) -> HarnessEvaluationOutput:
    """执行单个 case 并分别写入中间产物和最终结果。"""
    if config is None or harness is None or evaluator is None or task_case is None or not task_case.case_id:
        raise ValueError("config、harness、evaluator 和 task_case 不能为空")
    harness_result = evaluator.evaluate(harness, config, task_case, progress_reporter=progress_reporter)
    case_id = task_case.case_id
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluator.evaluate 必须返回 evaluation_report")

    raw_run_dir = config.runs_dir / config.benchmark / harness_result.run_id / case_id
    result_dir = config.results_dir / config.benchmark / harness_result.run_id / case_id
    report_path = result_dir / "report.json"
    summary_path = result_dir / "summary.json"
    raw_summary_path = raw_run_dir / "raw_summary.json"
    trajectory_path = raw_run_dir / "trajectory.json"
    raw_summary = dict(harness_result.raw_summary)
    runtime_metrics = dict(report.runtime_metrics)
    if runtime_metrics and not isinstance(raw_summary.get("runtime_metrics"), dict):
        raw_summary["runtime_metrics"] = runtime_metrics
    trajectory = harness_result.trajectory
    raw_summary["trajectory_output"] = {
        "path": "trajectory.json",
        "step_count": len(trajectory.steps),
        "snapshot_count": len(trajectory.snapshots),
        "final_state_present": trajectory.final_state is not None,
    }
    raw_summary.update(
        {
            "terminated_by_policy": harness_result.terminated_by_policy,
            "termination_code": harness_result.termination_code,
            "termination_reason": harness_result.termination_reason,
            "stage_settlements": [settlement.to_dict() for settlement in harness_result.stage_settlements],
        }
    )
    report_text = json.dumps(report.to_dict(), ensure_ascii=False, indent=4)
    summary_text = json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=4)
    trajectory_text = json.dumps(trajectory_to_json(trajectory), ensure_ascii=False, indent=4)
    raw_summary_text = json.dumps(raw_summary, ensure_ascii=False, indent=4)
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    summary_path.write_text(summary_text, encoding="utf-8")
    trajectory_path.write_text(trajectory_text, encoding="utf-8")
    raw_summary_path.write_text(raw_summary_text, encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
        trajectory_path=trajectory_path,
    )


def _write_run_level_summaries(outputs: list[HarnessEvaluationOutput]) -> None:
    """按 run 目录写出所有场景的汇总摘要。"""
    if outputs is None:
        raise ValueError("outputs 不能为空")
    outputs_by_run_dir: dict[Path, list[HarnessEvaluationOutput]] = {}
    for output in outputs:
        if output is None:
            raise ValueError("outputs 不能包含空输出")
        outputs_by_run_dir.setdefault(output.result_dir.parent, []).append(output)
    for run_dir, run_outputs in outputs_by_run_dir.items():
        summary = _build_run_level_summary(run_dir, run_outputs)
        summary_text = json.dumps(summary, ensure_ascii=False, indent=4)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(summary_text, encoding="utf-8")


def _build_run_level_summary(run_dir: Path, outputs: list[HarnessEvaluationOutput]) -> dict[str, object]:
    """构造单个 run_id 下所有场景的汇总摘要。"""
    if run_dir is None or outputs is None:
        raise ValueError("run_dir 和 outputs 不能为空")
    cases: list[dict[str, object]] = []
    coverage_counts: dict[str, int] = {}
    score_sum = 0.0
    total_step_count = 0
    total_llm_tokens = 0
    total_trajectory_tokens = 0
    elapsed_values: list[float] = []
    for output in outputs:
        if output is None:
            raise ValueError("outputs 不能包含空输出")
        summary_data = json.loads(output.summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary_data, dict):
            raise ValueError(f"场景摘要必须是 JSON 对象: {output.summary_path}")
        coverage = str(summary_data.get("milestone_coverage", "unknown"))
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        score = float(summary_data.get("overall_score", 0.0))
        score_sum += score
        metrics = summary_data.get("runtime_metrics")
        if isinstance(metrics, dict):
            total_step_count += int(metrics.get("step_count", 0) or 0)
            total_llm_tokens += int(metrics.get("llm_total_tokens", 0) or 0)
            total_trajectory_tokens += int(metrics.get("trajectory_total_tokens", 0) or 0)
            elapsed_values.append(float(metrics.get("elapsed_seconds", 0.0) or 0.0))
        case_summary: dict[str, object] = dict(summary_data)
        case_summary.update(
            {
                "case_id": output.result_dir.name,
                "summary_path": output.summary_path.relative_to(run_dir).as_posix(),
                "report_path": output.report_path.relative_to(run_dir).as_posix(),
            }
        )
        cases.append(case_summary)
    case_count = len(cases)
    return {
        "benchmark": run_dir.parent.name,
        "run_id": run_dir.name,
        "case_count": case_count,
        "average_overall_score": score_sum / case_count if case_count else 0.0,
        "milestone_coverage_counts": coverage_counts,
        "total_step_count": total_step_count,
        "total_llm_tokens": total_llm_tokens,
        "total_trajectory_tokens": total_trajectory_tokens,
        "average_elapsed_seconds": sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0,
        "cases": cases,
    }
