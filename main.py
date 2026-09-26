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
from dynsteer.runtime.profile import require_native_execution_profile
from dynsteer.log import configure_logger

DEFAULT_RANDOM_SEED = 202608


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    """Parse benchmark-only and unified-experiment command-line arguments."""
    parser = argparse.ArgumentParser(description='Run DynSTEER benchmark stage dynamic evaluation experiment')
    parser.add_argument("--benchmark", default=None, help='benchmark name, for example toolsandbox or swebench_pro')
    parser.add_argument("--exp", "--experiment-config", dest="experiment_config", default=None, help='path to the unified experiment-matrix JSON configuration')
    parser.add_argument("--data-root", default=None, help='benchmark static configuration and manifest directory')
    parser.add_argument("--runs-dir", default="runs", help='benchmark intermediate and native output directory')
    parser.add_argument("--results-dir", default="results", help='Directory of the final DynSTEER assessment results')
    parser.add_argument("--log-dir", default="logs", help='DynSTEER log directory')
    parser.add_argument("--workers", type=int, default=3, help='maximum number of parallel benchmark-case workers (default: 3)')
    parser.add_argument("--random_seed", "--random-seed", type=int, default=DEFAULT_RANDOM_SEED,
        help=f"Python random seed (default: {DEFAULT_RANDOM_SEED})",
    )
    parser.add_argument("--only_adapt", "--only-adapt", action="store_true", help='adapt benchmark cases without running evaluation')
    parser.add_argument("--force_adapt", "--force-adapt", action="store_true", help='recreate adapted cases and rerun the evaluation process')
    parser.add_argument("--force_eval", "--force-eval", action="store_true", help='rerun execution and overwrite existing case outputs')
    parser.add_argument("--no_sum", "--no-sum", action="store_true", help='write only case-level results, without index, scores, or metrics summaries')
    return parser.parse_args(argv)


def _adapted_case_files_exist(config: HarnessRunConfig) -> bool:
    """Return whether all selected adapted case files already exist."""
    if config is None:
        raise ValueError("configs must not be empty.")
    if config.case_ids is None:
        return False
    return all(adapted_case_path(config, case_id).exists() for case_id in config.case_ids)


def _adapt_only_configs(configs: list[HarnessRunConfig], force_adapt: bool = False) -> list[Path]:
    """Adapt benchmark TaskCases and return their selected case paths."""
    if configs is None:
        raise ValueError("configs must not be empty.")
    adapted_paths: list[Path] = []
    for config in configs:
        if config is None:
            raise ValueError('configs cannot contain null entries')
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
    """Adapt every benchmark configured by the unified experiment and return the adapted case paths."""
    config = load_experiment_config(config_path)
    if config["benchmarks"][0]["benchmark"] == "swebench_pro":
        require_native_execution_profile()
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
    """Load benchmark or unified experiment configurations and run the evaluation."""
    args = _parse_args(argv)
    random.seed(args.random_seed)
    logger = configure_logger(args.log_dir)
    logger.info('Main experiment entry: loading benchmark configurations and starting evaluation.', extra={"random_seed": args.random_seed})
    runs_dir = Path(args.runs_dir)
    results_dir = Path(args.results_dir)

    try:
        if int(args.workers) < 1:
            raise ValueError('--workers must be greater than zero.')

        if args.experiment_config is not None:
            if args.only_adapt:
                adapted_paths = _adapt_only_experiment(args.experiment_config, force_adapt=bool(args.force_adapt))
                logger.info('Benchmark adaptation completed; output case count: %s', extra={"case_count": len(adapted_paths)})
                return 0
            results = run_experiment(
                Path(args.experiment_config),
                workers=int(args.workers),
                force_adapt=bool(args.force_adapt),
                force_eval=bool(args.force_eval),
                no_sum=bool(args.no_sum),
            )
            logger.info('Unified experiment completed; case result count: %s', extra={"case_count": len(results)})
            return 0

        if args.benchmark is None:
            raise ValueError('Provide either --benchmark for a benchmark run or --exp for a unified experiment')

        data_root = Path(args.data_root) if args.data_root is not None else Path(__file__).parent / "data" / args.benchmark
        if not data_root.exists():
            raise ValueError('A reliable data-root is required to run benchmark harms, provided through --data-root or using data/{benchmark}')

        configs = load_harness_run_configs(benchmark=str(args.benchmark), data_root=data_root, runs_dir=runs_dir, results_dir=results_dir)
        if args.only_adapt:
            adapted_paths = _adapt_only_configs(configs, force_adapt=bool(args.force_adapt))
            logger.info('Benchmark adaptation completed; output case count: %s', extra={"case_count": len(adapted_paths)})
            return 0

        outputs: list[HarnessEvaluationOutput] = run_harness_configs(
            configs=configs,
            max_workers=int(args.workers),
            force_adapt=bool(args.force_adapt),
            force_eval=bool(args.force_eval),
        )
        logger.info('Evaluation completed; output report count: %s', extra={"report_count": len(outputs)})
        return 0
    except ValueError as exc:
        logger.exception('Failed to parse benchmark input', extra={"error": str(exc)})
        return 1
    except Exception as exc:
        logger.exception('Benchmark execution failed', extra={"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
