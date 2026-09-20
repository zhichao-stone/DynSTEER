from __future__ import annotations

import argparse
import random
from dataclasses import replace
from pathlib import Path
from typing import Optional

from dynsteer.adapter.loader import adapted_case_path, load_task_case
from dynsteer.adapter.registry import get_adapter, get_harness
from dynsteer.experiment.config import build_harness_config, expand_experiment_matrix, load_experiment_config
from dynsteer.experiment.model import ExperimentMethod, ExperimentRunSpec
from dynsteer.experiment.runner import run_experiment
from dynsteer.harness.config import load_harness_run_configs
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.runner import run_harness_configs
from dynsteer.harness.selection import select_case_ids
from dynsteer.model import HarnessEvaluationOutput
from dynsteer.log import configure_logger

DEFAULT_RANDOM_SEED = 202608


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """解析 benchmark-only 和统一实验命令行参数。"""
    parser = argparse.ArgumentParser(description="运行 DynSTEER benchmark 阶段式动态评估实验")
    parser.add_argument("--benchmark", default=None, help="benchmark harness 名称，例如 toolsandbox")
    parser.add_argument("--exp", "--experiment-config", dest="experiment_config", default=None, help="统一实验矩阵 JSON 配置路径")
    parser.add_argument("--data-root", default=None, help="benchmark 静态配置与 manifest 目录")
    parser.add_argument("--runs-dir", default="runs", help="benchmark 中间产物与原生输出目录")
    parser.add_argument("--results-dir", default="results", help="最终 DynSTEER 评估结果目录")
    parser.add_argument("--log-dir", default="logs", help="DynSTEER 日志目录")
    parser.add_argument("--workers", type=int, default=3, help="benchmark case 最大并行 worker 数，默认 3")
    parser.add_argument("--random_seed", "--random-seed", type=int, default=DEFAULT_RANDOM_SEED,
        help=f"Python random 随机种子，默认 {DEFAULT_RANDOM_SEED}",
    )
    parser.add_argument("--only_adapt", "--only-adapt", action="store_true", help="仅适配 benchmark 数据并写入 data-root，不执行评估")
    parser.add_argument("--force_adapt", "--force-adapt", action="store_true", help="强制重建已有 adapted case，并自动重新执行评估流程")
    parser.add_argument("--force_eval", "--force-eval", action="store_true", help="强制重新执行评估流程并覆盖已有 case 产物")
    parser.add_argument("--no_sum", "--no-sum", action="store_true", help="统一实验入口下只写 case 级结果，不写 index/scores/metrics 汇总文件")
    return parser.parse_args(argv)


def _adapted_case_files_exist(config: HarnessRunConfig) -> bool:
    """判断当前配置指定的 adapted case 文件是否都已存在。"""
    if config is None:
        raise ValueError("config 不能为空")
    if config.case_ids is None:
        return False
    return all(adapted_case_path(config, case_id).exists() for case_id in config.case_ids)


def _adapt_only_configs(configs: list[HarnessRunConfig], force_adapt: bool = False) -> list[Path]:
    """仅执行 benchmark TaskCase 适配，并返回 adapted case 文件路径。"""
    if configs is None:
        raise ValueError("configs 不能为空")
    adapted_paths: list[Path] = []
    for config in configs:
        if config is None:
            raise ValueError("configs 不能包含空配置")
        adapter = get_adapter(config.benchmark)
        harness = get_harness(config.benchmark)
        if not force_adapt and _adapted_case_files_exist(config):
            case_ids = list(config.case_ids)
        else:
            if config.case_ids is not None:
                case_ids = list(config.case_ids)
            else:
                case_ids = select_case_ids(config, harness)
        run_config = replace(config, case_ids=tuple(case_ids))
        harness.prepare_config(run_config)
        load_task_case(run_config, adapter, force_adapt=force_adapt)
        adapted_paths.extend(adapted_case_path(run_config, case_id) for case_id in case_ids)
    return adapted_paths


def _adapt_only_experiment(config_path: Path | str, force_adapt: bool = False) -> list[Path]:
    """仅适配统一实验配置中涉及的 benchmark 数据，并返回 adapted case 路径。"""
    config = load_experiment_config(config_path)
    specs = expand_experiment_matrix(config)
    grouped: dict[tuple[str, str], list[ExperimentRunSpec]] = {}
    for spec in specs:
        key = (spec.benchmark, str(spec.data_root.resolve()))
        grouped.setdefault(key, []).append(spec)

    prepared: list[HarnessRunConfig] = []
    for group_specs in grouped.values():
        representative = next(
            (spec for spec in group_specs if spec.method != ExperimentMethod.DEFAULT),
            group_specs[0],
        )
        harness_config = build_harness_config(representative)
        case_ids: list[str] = []
        for spec in group_specs:
            for case_id in spec.case_ids or ():
                if case_id not in case_ids:
                    case_ids.append(case_id)
        prepared.append(replace(harness_config, case_ids=tuple(case_ids) if case_ids else None))

    adapted_paths: list[Path] = []
    for harness_config in prepared:
        adapted_paths.extend(_adapt_only_configs([harness_config], force_adapt=force_adapt))
    return adapted_paths


def main(argv: Optional[list[str]] = None) -> int:
    """主实验入口：加载 benchmark 配置列表并执行评估。"""
    args = _parse_args(argv)
    random.seed(args.random_seed)
    logger = configure_logger(args.log_dir)
    logger.info("实验随机种子已设置", extra={"random_seed": args.random_seed})
    runs_dir = Path(args.runs_dir)
    results_dir = Path(args.results_dir)

    try:
        if int(args.workers) < 1:
            raise ValueError("--workers 必须大于 0")

        if args.experiment_config is not None:
            if args.only_adapt:
                adapted_paths = _adapt_only_experiment(args.experiment_config, force_adapt=bool(args.force_adapt))
                logger.info("数据适配完成，输出 case 数量: %s", len(adapted_paths), extra={"case_count": len(adapted_paths)})
                return 0
            results = run_experiment(
                Path(args.experiment_config),
                workers=int(args.workers),
                force_adapt=bool(args.force_adapt),
                force_eval=bool(args.force_eval),
                no_sum=bool(args.no_sum),
            )
            logger.info("统一实验完成，case结果数量: %s", len(results), extra={"case_count": len(results)})
            return 0

        if args.benchmark is None:
            raise ValueError("运行单 benchmark harness 时必须提供 --benchmark；统一实验请提供 --exp")

        data_root = Path(args.data_root) if args.data_root is not None else Path(__file__).parent / "data" / args.benchmark
        if not data_root.exists():
            raise ValueError("运行 benchmark harness 时需要提供可靠的 data-root，通过 --data-root 提供或者使用 data/{benchmark}")

        configs = load_harness_run_configs(benchmark=str(args.benchmark), data_root=data_root, runs_dir=runs_dir, results_dir=results_dir)
        if args.only_adapt:
            adapted_paths = _adapt_only_configs(configs, force_adapt=bool(args.force_adapt))
            logger.info("数据适配完成，输出 case 数量: %s", len(adapted_paths), extra={"case_count": len(adapted_paths)})
            return 0

        outputs: list[HarnessEvaluationOutput] = run_harness_configs(
            configs=configs,
            max_workers=int(args.workers),
            force_adapt=bool(args.force_adapt),
            force_eval=bool(args.force_eval),
        )
        logger.info("评估完成，输出报告数量: %s", len(outputs), extra={"report_count": len(outputs)})
        return 0
    except ValueError as exc:
        logger.exception("benchmark 输入解析失败", extra={"error": str(exc)})
        return 1
    except Exception as exc:
        logger.exception("benchmark 执行失败", extra={"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
