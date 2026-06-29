from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from dynsteer.adapter.registry import get_harness
from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.evaluate import DynSTEEREvaluator
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.log import configure_logger


@dataclass(frozen=True)
class HarnessEvaluationOutput:
    """harness 运行与 DynSTEER 评估输出路径。

    Args:
        run_dir: 本次 DynSTEER 报告目录。
        raw_run_dir: 本次中间产物目录。
        result_dir: 本次最终评估报告目录。
        report_path: 完整评估报告路径。
        summary_path: 摘要报告路径。
        raw_summary_path: benchmark 原生摘要路径。
    """

    run_dir: Path
    raw_run_dir: Path
    result_dir: Path
    report_path: Path
    summary_path: Path
    raw_summary_path: Path


class HarnessCaseExecutionError(RuntimeError):
    """单个 benchmark case 执行失败时抛出。"""

    def __init__(self, benchmark: str, run_id: str | None, case_id: str, cause: Exception) -> None:
        """初始化 case 执行异常。

        Args:
            benchmark: benchmark 名称。
            run_id: 运行 ID。
            case_id: case ID。
            cause: 原始异常。
        """
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
    """并行执行前展开的单个 case 任务。"""

    order: int
    config: HarnessRunConfig
    case_id: str


def run_harness_cases(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
) -> list[HarnessEvaluationOutput]:
    """运行一个或多个 benchmark case，并执行 DynSTEER 评估。

    Args:
        config: harness 运行配置；未指定 case_ids 时运行全部场景。
        harness: benchmark harness 适配器。
        evaluator: DynSTEER 主实验 evaluator。

    Returns:
        每个 case 的输出文件路径集合。
    """
    if config is None:
        raise ValueError("config 不能为空")
    if harness is None:
        raise ValueError("harness 不能为空")
    if evaluator is None:
        raise ValueError("evaluator 不能为空")
    case_ids = _select_case_ids(config, harness, run_all=True)
    logger = _get_or_configure_harness_logger(config.runs_dir / "logs")
    _log_harness_case_overview(logger, case_ids)
    logger.info("开始运行 benchmark harness", extra={"case_count": len(case_ids)})
    outputs: list[HarnessEvaluationOutput] = []
    for case_id in case_ids:
        outputs.append(_run_single_harness_case(config, harness, evaluator, case_id))
    _write_run_level_summaries(outputs)
    logger.info("benchmark harness 评估完成", extra={"case_count": len(case_ids)})
    return outputs


def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
) -> list[HarnessEvaluationOutput]:
    """运行多组 harness 配置，并可按 case 并行执行。

    Args:
        configs: harness 运行配置列表。
        max_workers: 最大并行 worker 数；1 表示串行。

    Returns:
        按配置与 case 顺序排列的输出路径集合。
    """
    if configs is None:
        raise ValueError("configs 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    tasks = _expand_harness_case_tasks(configs)
    logger = _get_or_configure_harness_logger(_log_dir_from_harness_tasks(configs, tasks))
    _log_harness_case_overview(logger, [task.case_id for task in tasks])
    logger.info("开始运行 benchmark harness", extra={"case_count": len(tasks)})
    if max_workers == 1:
        outputs = [_run_isolated_harness_case(task) for task in tasks]
        _write_run_level_summaries(outputs)
        logger.info("benchmark harness 评估完成", extra={"case_count": len(tasks)})
        return outputs

    outputs_by_order: dict[int, HarnessEvaluationOutput] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_isolated_harness_case, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            outputs_by_order[task.order] = future.result()
    outputs = [outputs_by_order[task.order] for task in tasks]
    _write_run_level_summaries(outputs)
    logger.info("benchmark harness 评估完成", extra={"case_count": len(tasks)})
    return outputs


def _log_dir_from_harness_tasks(configs: list[HarnessRunConfig], tasks: list[_HarnessCaseTask]) -> Path:
    """获取本批 harness 日志目录。

    Args:
        configs: harness 运行配置列表。
        tasks: 已展开的 case 任务。

    Returns:
        日志目录路径。
    """
    if configs is None or tasks is None:
        raise ValueError("configs 和 tasks 不能为空")
    if tasks:
        return tasks[0].config.runs_dir / "logs"
    if configs:
        return configs[0].runs_dir / "logs"
    return Path("runs") / "logs"


def _get_or_configure_harness_logger(log_dir: Path) -> logging.Logger:
    """获取已配置 logger；未配置时初始化一次。

    Args:
        log_dir: 日志目录。

    Returns:
        DynSTEER logger。
    """
    if log_dir is None:
        raise ValueError("log_dir 不能为空")
    logger = logging.getLogger("dynsteer")
    if logger.handlers:
        return logger
    return configure_logger(log_dir)


def _log_harness_case_overview(logger: logging.Logger, case_ids: list[str]) -> None:
    """输出本批 benchmark 场景总览。

    Args:
        logger: 已配置的 DynSTEER logger。
        case_ids: 本批运行的 case ID 列表。

    Returns:
        None。
    """
    if logger is None:
        raise ValueError("logger 不能为空")
    if case_ids is None:
        raise ValueError("case_ids 不能为空")
    scenario_text = ", ".join(case_ids)
    logger.info(
        "benchmark harness 将运行 %s 个场景: %s",
        len(case_ids),
        scenario_text,
        extra={"case_count": len(case_ids), "case_ids": scenario_text},
    )


def _expand_harness_case_tasks(configs: list[HarnessRunConfig]) -> list[_HarnessCaseTask]:
    """将多组运行配置展开为按顺序排列的 case 任务。

    Args:
        configs: harness 运行配置列表。

    Returns:
        case 任务列表。
    """
    tasks: list[_HarnessCaseTask] = []
    for config in configs:
        if config is None:
            raise ValueError("configs 不能包含空配置")
        harness = get_harness(config.benchmark)
        case_ids = _select_case_ids(config, harness, run_all=True)
        for case_id in case_ids:
            tasks.append(_HarnessCaseTask(order=len(tasks), config=config, case_id=case_id))
    return tasks


def _run_isolated_harness_case(task: _HarnessCaseTask) -> HarnessEvaluationOutput:
    """在独立 harness/evaluator 实例中执行单个 case。

    Args:
        task: 已展开的 case 任务。

    Returns:
        单个 case 输出路径集合。
    """
    if task is None:
        raise ValueError("task 不能为空")
    try:
        harness = get_harness(task.config.benchmark)
        evaluator = DynSTEEREvaluator.from_env()
        return _run_single_harness_case(task.config, harness, evaluator, task.case_id)
    except HarnessCaseExecutionError:
        raise
    except Exception as exc:
        run_id = task.config.metadata.get("run_id")
        raise HarnessCaseExecutionError(
            task.config.benchmark,
            str(run_id) if run_id is not None else None,
            task.case_id,
            exc,
        ) from exc


def run_harness_case(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
) -> HarnessEvaluationOutput:
    """运行单个 benchmark case，并执行 DynSTEER 评估。

    Args:
        config: harness 运行配置；若 case_ids 为空则选择第一个 case。
        harness: benchmark harness 适配器。
        evaluator: DynSTEER 主实验 evaluator。

    Returns:
        单个 case 的输出文件路径集合。
    """
    if config is None:
        raise ValueError("config 不能为空")
    if harness is None:
        raise ValueError("harness 不能为空")
    if evaluator is None:
        raise ValueError("evaluator 不能为空")
    case_id = _select_case_ids(config, harness, run_all=False)[0]
    logger = _get_or_configure_harness_logger(config.runs_dir / "logs")
    _log_harness_case_overview(logger, [case_id])
    logger.info("开始运行 benchmark harness", extra={"case_count": 1})
    output = _run_single_harness_case(config, harness, evaluator, case_id)
    _write_run_level_summaries([output])
    logger.info("benchmark harness 评估完成", extra={"case_count": 1})
    return output


def _select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness, run_all: bool) -> list[str]:
    """根据配置选择要运行的 benchmark case ID。

    Args:
        config: harness 运行配置。
        harness: benchmark harness 适配器。
        run_all: 未指定 case_ids 时是否运行全部场景。

    Returns:
        待运行 case ID 列表。
    """
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


def _safe_run_id(config: HarnessRunConfig, harness: BaseBenchmarkHarness, case_id: str) -> str | None:
    """尽量构造 run_id，用于异常上下文。"""
    try:
        return harness.build_run_id(config, case_id)
    except Exception:
        raw_run_id = config.metadata.get("run_id")
        return str(raw_run_id) if raw_run_id is not None else None


def _run_single_harness_case(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    case_id: str,
) -> HarnessEvaluationOutput:
    """执行单个 case，并把失败包装为 HarnessCaseExecutionError。

    Args:
        config: 已指定 scenario 的 harness 运行配置。
        harness: benchmark harness 适配器。
        evaluator: DynSTEER 主实验 evaluator。
        case_id: benchmark 场景 ID。

    Returns:
        输出文件路径集合。
    """
    if config is None or harness is None or evaluator is None or not case_id:
        raise ValueError("config、harness、evaluator 和 case_id 不能为空")
    try:
        return _run_single_harness_case_impl(config, harness, evaluator, case_id)
    except HarnessCaseExecutionError:
        raise
    except Exception as exc:
        run_id = _safe_run_id(config, harness, case_id)
        raise HarnessCaseExecutionError(config.benchmark, run_id, case_id, exc) from exc


def _run_single_harness_case_impl(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    case_id: str,
) -> HarnessEvaluationOutput:
    """执行单个 case 并分别写入中间产物和最终结果。"""
    harness_result = evaluator.evaluate(harness, case_id, config)
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluator.evaluate 必须返回 evaluation_report")
    
    raw_run_dir = config.runs_dir / config.benchmark / harness_result.run_id / case_id
    result_dir = config.results_dir / config.benchmark / harness_result.run_id / case_id
    report_path = result_dir / "report.json"
    summary_path = result_dir / "summary.json"
    raw_summary_path = raw_run_dir / "raw_summary.json"
    raw_summary = dict(harness_result.raw_summary)
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
    raw_summary_text = json.dumps(raw_summary, ensure_ascii=False, indent=4)
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    summary_path.write_text(summary_text, encoding="utf-8")
    raw_summary_path.write_text(raw_summary_text, encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
    )


def _write_run_level_summaries(outputs: list[HarnessEvaluationOutput]) -> None:
    """按 run 目录写出所有场景的汇总摘要。

    Args:
        outputs: 已成功写出的单场景输出路径集合。
    """
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
    """构造单个 run_id 下所有场景的汇总摘要。

    Args:
        run_dir: `results/<benchmark>/<run_id>` 目录。
        outputs: 属于该 run 目录的单场景输出路径集合。

    Returns:
        可序列化为 JSON 的 run 级汇总摘要。
    """
    if run_dir is None or outputs is None:
        raise ValueError("run_dir 和 outputs 不能为空")
    cases: list[dict[str, object]] = []
    coverage_counts: dict[str, int] = {}
    score_sum = 0.0
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
        "cases": cases,
    }
