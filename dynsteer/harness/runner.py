from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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
    outputs: list[HarnessEvaluationOutput] = []
    for case_id in case_ids:
        outputs.append(_run_single_harness_case(config, harness, evaluator, case_id))
    return outputs


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
    return _run_single_harness_case(config, harness, evaluator, case_id)


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


def _run_single_harness_case(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    case_id: str,
) -> HarnessEvaluationOutput:
    """执行单个 case 并分别写入中间产物和最终结果。

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
    logger = configure_logger(config.runs_dir / "logs")

    logger.info("开始运行 benchmark harness", extra={"benchmark": config.benchmark, "case_id": case_id})
    harness_result = evaluator.evaluate(harness, case_id, config)
    report = harness_result.evaluation_report
    if report is None:
        raise ValueError("evaluator.evaluate 必须返回 evaluation_report")
    
    raw_run_dir = config.runs_dir / config.benchmark / harness_result.run_id / case_id
    result_dir = config.results_dir / config.benchmark / harness_result.run_id / case_id
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
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
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=4), encoding="utf-8")
    summary_path.write_text(json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=4), encoding="utf-8")
    raw_summary_path.write_text(json.dumps(raw_summary, ensure_ascii=False, indent=4), encoding="utf-8")
    logger.info("benchmark harness 评估完成", extra={"report_path": str(report_path)})
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
    )
