from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Optional

from dynsteer.adapter.loader import adapterd_case_path, load_task_case
from dynsteer.adapter.registry import get_adapter
from dynsteer.harness.config import load_harness_run_configs
from dynsteer.harness.model import HarnessRunConfig
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
    parser.add_argument("--workers", type=int, default=3, help="benchmark case 最大并行 worker 数，默认 3")
    parser.add_argument("--only_adapt", action="store_true", help="仅适配 benchmark 数据并写入 data-root，不执行评估")
    return parser.parse_args(argv)


def _adapt_only_configs(configs: list[HarnessRunConfig]) -> list[Path]:
    """仅执行 benchmark TaskCase 适配，并返回 adapted case 文件路径。

    Args:
        configs: 已从 data-root 加载的 harness 运行配置列表。

    Returns:
        本次确认可用的 adapted case JSON 文件路径列表。
    """
    if configs is None:
        raise ValueError("configs 不能为空")
    adapted_paths: list[Path] = []
    for config in configs:
        if config is None:
            raise ValueError("configs 不能包含空配置")
        adapter = get_adapter(config.benchmark)
        harness = adapter.create_harness()
        cases = harness.list_cases(config)
        if not cases:
            raise ValueError("benchmark 没有可适配场景")
        known_case_ids = {case.case_id for case in cases}
        if config.case_ids is not None:
            missing = [case_id for case_id in config.case_ids if case_id not in known_case_ids]
            if missing:
                raise KeyError(f"benchmark 场景不存在: {missing[0]}")
            case_ids = list(config.case_ids)
        else:
            case_ids = [case.case_id for case in cases]
        run_config = replace(config, case_ids=tuple(case_ids))
        harness.prepare_config(run_config)
        task_cases = load_task_case(run_config, adapter)
        loaded_case_ids = [task_case.case_id for task_case in task_cases]
        if loaded_case_ids != case_ids:
            raise ValueError(f"加载的 TaskCase 顺序与配置不一致: {loaded_case_ids}")
        adapted_paths.extend(adapterd_case_path(run_config.data_root, case_id) for case_id in case_ids)
    return adapted_paths


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
        if int(args.workers) < 1:
            raise ValueError("--workers 必须大于 0")

        configs = load_harness_run_configs(
            benchmark=str(args.benchmark),
            data_root=data_root,
            runs_dir=runs_dir,
            results_dir=results_dir,
        )
        if args.only_adapt:
            adapted_paths = _adapt_only_configs(configs)
            logger.info("数据适配完成，输出 case 数量: %s", len(adapted_paths), extra={"case_count": len(adapted_paths)})
            for path in adapted_paths:
                print(str(path))
            return 0

        outputs: list[HarnessEvaluationOutput] = run_harness_configs(
            configs=configs,
            max_workers=int(args.workers),
        )
        logger.info("评估完成，输出报告数量: %s", len(outputs), extra={"report_count": len(outputs)})
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
