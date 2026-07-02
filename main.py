from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from dynsteer.harness.config import load_harness_run_configs
from dynsteer.harness.runner import HarnessEvaluationOutput, run_harness_configs
from dynsteer.log import configure_logger


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """解析 benchmark-only 命令行参数。"""
    parser = argparse.ArgumentParser(description="运行 DynSTEER benchmark 阶段式动态评估实验")
    parser.add_argument("--benchmark", required=True, help="benchmark harness 名称，例如 toolsandbox")
    parser.add_argument("--data-root", default=None, help="benchmark 静态配置与 manifest 目录")
    parser.add_argument("--runs-dir", default="runs", help="benchmark 中间产物与原生输出目录")
    parser.add_argument("--results-dir", default="results", help="最终 DynSTEER 评估结果目录")
    parser.add_argument("--log-dir", default="logs", help="DynSTEER 日志目录")
    parser.add_argument("--max-workers", type=int, default=1, help="benchmark case 最大并行 worker 数，默认 1")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """主实验入口：加载 benchmark 配置列表并执行评估。

    Args:
        argv: 可选命令行参数，测试时可直接传入。

    Returns:
        0 表示成功，1 表示输入解析错误，2 表示评估执行错误。
    """
    args = _parse_args(argv)
    logger = configure_logger(args.log_dir)
    runs_dir = Path(args.runs_dir)
    results_dir = Path(args.results_dir)

    try:
        data_root = Path(args.data_root) if args.data_root is not None else Path(__file__).parent / "data" / args.benchmark
        if not data_root.exists():
            raise ValueError("运行 benchmark harness 时需要提供可靠的 data-root，通过 --data-root 提供或者使用 data/{benchmark}")
        if int(args.max_workers) < 1:
            raise ValueError("--max-workers 必须大于 0")

        configs = load_harness_run_configs(
            benchmark=str(args.benchmark),
            data_root=data_root,
            runs_dir=runs_dir,
            results_dir=results_dir,
        )
        outputs: list[HarnessEvaluationOutput] = run_harness_configs(
            configs=configs,
            max_workers=int(args.max_workers),
        )
        for output in outputs:
            print(str(output.report_path))
        return 0
    except ValueError as exc:
        logger.exception("benchmark 输入解析失败", extra={"error": str(exc)})
        return 1
    except Exception as exc:
        logger.exception("benchmark 执行失败", extra={"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
