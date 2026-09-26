from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from dynsteer.adapter.loader import load_task_case, refresh_task_cases_for_experiment
from dynsteer.adapter.registry import get_adapter, get_harness
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import write_method_level_summaries
from dynsteer.harness.scheduler import run_case_tasks
from dynsteer.harness.selection import select_case_ids
from dynsteer.log import configure_logger
from dynsteer.model import HarnessCaseTask, HarnessEvaluationOutput, TaskCase


def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """Runs a set of harness configurations."""
    if configs is None:
        raise ValueError("config must not be empty.")
    if max_workers < 1:
        raise ValueError('max_workers must be greater than 0')
    logger = _get_or_configure_harness_logger(_log_dir_from_configs(configs))
    outputs: list[HarnessEvaluationOutput] = []
    effective_force_eval = bool(force_eval or force_adapt)
    for config in configs:
        if config is None:
            raise ValueError('Configs cannot contain empty configurations')
        outputs.extend(
            _run_config(
                config,
                max_workers=effective_max_workers(max_workers, config),
                logger=logger,
                force_adapt=force_adapt,
                force_eval=effective_force_eval,
            )
        )
    return outputs


def prepare_task_cases(
    config: HarnessRunConfig,
    force_adapt: bool = False,
    refresh_dynamic_targets: bool = True,
) -> tuple[list[TaskCase], HarnessRunConfig]:
    """Load TaskCase and refresh the dynamic target at the experimental boundary."""
    adapter = get_adapter(config.benchmark)
    harness = get_harness(config.benchmark)
    case_ids = select_case_ids(config, harness)
    run_config = replace(config, case_ids=tuple(case_ids))
    harness.prepare_config(run_config)
    task_cases = load_task_case(run_config, adapter, force_adapt=force_adapt)
    if refresh_dynamic_targets:
        task_cases = refresh_task_cases_for_experiment(run_config, adapter, task_cases)
    return task_cases, run_config


def _run_config(
    config: HarnessRunConfig,
    *,
    max_workers: int,
    logger: logging.Logger,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """Prepare the TaskCase list for a single run configuration."""
    task_cases, run_config = prepare_task_cases(config, force_adapt)
    logger.info(
        'Starting evaluation for config=%s, benchmark=%s, case_count=%s',
        _config_label(run_config),
        run_config.benchmark,
        len(task_cases),
        extra={
            "benchmark": run_config.benchmark,
            "case_count": len(task_cases),
            "run_config": _config_label(run_config),
        },
    )
    tasks = [
        HarnessCaseTask(order=index, config=run_config, task_case=task_case)
        for index, task_case in enumerate(task_cases)
    ]
    outputs = run_case_tasks(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)
    write_method_level_summaries(outputs)
    return outputs


def effective_max_workers(max_workers: int, config: HarnessRunConfig) -> int:
    """Calculates the maximum number of workers that the current configuration allows."""
    if config is None:
        raise ValueError("config must not be empty.")
    if max_workers < 1:
        raise ValueError('max_workers must be greater than 0')
    benchmark_max_workers = config.metadata.get("benchmark_max_workers")
    if benchmark_max_workers is None:
        return max_workers
    if isinstance(benchmark_max_workers, bool) or not isinstance(benchmark_max_workers, int):
        raise ValueError('benchmark_max_workers must be integer')
    return max(1, min(max_workers, max(benchmark_max_workers, 1)))


def _config_label(config: HarnessRunConfig) -> str:
    """Configuration label used in logs."""
    if config is None:
        raise ValueError("config must not be empty.")
    name = config.metadata.get("run_config_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    index = config.metadata.get("run_config_index")
    return f"Run configuration {index}" if index is not None else 'Unnamed configuration'


def _log_dir_from_configs(configs: list[HarnessRunConfig]) -> Path:
    """Return the log directory for this harness run."""
    if configs is None:
        raise ValueError("config must not be empty.")
    if configs:
        return configs[0].runs_dir / "logs"
    return Path("runs") / "logs"


def _get_or_configure_harness_logger(log_dir: Path) -> logging.Logger:
    """Fetching configured logger; initializing when not configured."""
    if log_dir is None:
        raise ValueError('log_dir cannot be empty')
    logger = logging.getLogger("dynsteer")
    if logger.handlers:
        return logger
    return configure_logger(log_dir)
