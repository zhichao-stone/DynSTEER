import logging
from dataclasses import replace
from pathlib import Path

from dynsteer.adapter.loader import load_task_case
from dynsteer.adapter.registry import get_adapter, get_harness
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import write_method_level_summaries
from dynsteer.harness.scheduler import run_case_tasks
from dynsteer.harness.selection import select_case_ids, validate_loaded_task_cases
from dynsteer.log import configure_logger
from dynsteer.model import HarnessCaseTask, HarnessEvaluationOutput, TaskCase


def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """运行一组 harness 配置。"""
    if configs is None:
        raise ValueError("configs 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    logger = _get_or_configure_harness_logger(_log_dir_from_configs(configs))
    outputs: list[HarnessEvaluationOutput] = []
    effective_force_eval = bool(force_eval or force_adapt)
    for config in configs:
        if config is None:
            raise ValueError("configs 不能包含空配置")
        outputs.extend(
            _run_config(
                config,
                max_workers=_effective_max_workers(max_workers, config),
                logger=logger,
                force_adapt=force_adapt,
                force_eval=effective_force_eval,
            )
        )
    return outputs


def prepare_task_cases(config: HarnessRunConfig, force_adapt: bool = False) -> tuple[list[TaskCase], HarnessRunConfig]:
    adapter = get_adapter(config.benchmark)
    harness = get_harness(config.benchmark)
    case_ids = select_case_ids(config, harness, run_all=True)
    run_config = replace(config, case_ids=tuple(case_ids))
    harness.prepare_config(run_config)
    task_cases = load_task_case(run_config, adapter, force_adapt=force_adapt)
    validate_loaded_task_cases(case_ids, task_cases)
    return task_cases, run_config


def _run_config(
    config: HarnessRunConfig,
    *,
    max_workers: int,
    logger: logging.Logger,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    """按单个配置加载 TaskCase 列表并执行。"""
    task_cases, run_config = prepare_task_cases(config, force_adapt)
    logger.info(
        "基于配置%s，开始基于%s展开评估，Cases数量: %s",
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
        HarnessCaseTask(order=index, config=run_config, case_id=task_case.case_id, task_case=task_case)
        for index, task_case in enumerate(task_cases)
    ]
    outputs = run_case_tasks(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)
    write_method_level_summaries(outputs)
    return outputs


def _effective_max_workers(max_workers: int, config: HarnessRunConfig) -> int:
    """计算当前配置允许的最大 worker 数。"""
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


def _config_label(config: HarnessRunConfig) -> str:
    """日志中使用的配置标签。"""
    if config is None:
        raise ValueError("config 不能为空")
    name = config.metadata.get("run_config_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
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


__all__ = ["HarnessEvaluationOutput", "run_harness_configs"]
