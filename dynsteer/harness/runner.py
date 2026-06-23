from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dynsteer.evaluate import evaluate_trajectory
from dynsteer.harness.model import BenchmarkHarness, HarnessRunConfig
from dynsteer.log import configure_logger


@dataclass(frozen=True)
class HarnessEvaluationOutput:
    """harness 运行与 DynSTEER 评估输出路径。

    Args:
        run_dir: 本次 DynSTEER 报告目录。
        report_path: 完整评估报告路径。
        summary_path: 摘要报告路径。
        raw_summary_path: benchmark 原生摘要路径。
    """

    run_dir: Path
    report_path: Path
    summary_path: Path
    raw_summary_path: Path


def run_harness_case(config: HarnessRunConfig, harness: BenchmarkHarness) -> HarnessEvaluationOutput:
    """运行 benchmark harness 并执行 DynSTEER 评估。

    Args:
        config: harness 运行配置。
        harness: benchmark harness 适配器。

    Returns:
        输出文件路径集合。
    """
    if config is None:
        raise ValueError("config 不能为空")
    if harness is None:
        raise ValueError("harness 不能为空")
    logger = configure_logger(config.output_dir / "logs")
    cases = harness.list_cases(config)
    if not cases:
        raise ValueError("benchmark 没有可运行场景")
    case_id = config.scenario or cases[0].case_id
    if case_id not in {case.case_id for case in cases}:
        raise KeyError(f"benchmark 场景不存在: {case_id}")

    logger.info("开始运行 benchmark harness", extra={"benchmark": config.benchmark, "case_id": case_id})
    harness_result = harness.run_case(config, case_id)
    report = evaluate_trajectory(harness_result.task_case, harness_result.trajectory)
    run_dir = config.output_dir / config.benchmark / harness_result.run_id / case_id / "dynsteer"
    run_dir.mkdir(parents=True, exist_ok=True)
    indent = 2 if config.pretty else None
    report_path = run_dir / "report.json"
    summary_path = run_dir / "summary.json"
    raw_summary_path = run_dir / "raw_summary.json"
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=indent), encoding="utf-8")
    summary_path.write_text(json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=indent), encoding="utf-8")
    raw_summary_path.write_text(json.dumps(harness_result.raw_summary, ensure_ascii=False, indent=indent), encoding="utf-8")
    logger.info("benchmark harness 评估完成", extra={"report_path": str(report_path)})
    return HarnessEvaluationOutput(
        run_dir=run_dir,
        report_path=report_path,
        summary_path=summary_path,
        raw_summary_path=raw_summary_path,
    )
