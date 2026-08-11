import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from dynsteer.experiment.model import EvaluationStrategyConfig
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import MilestoneGenerationConfig
from dynsteer.model import EvaluationLevel, JsonObject, ThresholdConfig
from dynsteer.utils import (
    normalize_client_config,
    optional_str,
    parse_int_value,
    read_json_file,
    required_str,
)

DEFAULT_READY_FRONTIER_PATIENCE = 8
_CLIENT_CONFIG_KEYS = ("agent_client", "user_client")
_RUN_CONFIG_CONTROL_FIELDS = {
    "scenarios",
    "ready_frontier_patience",
    "thresholds",
    "strategy",
    "name",
    "milestone_generation",
    *_CLIENT_CONFIG_KEYS,
}

_GENERATOR_CONFIG_FIELDS = {
    "provider",
    "model",
    "base_url",
    "timeout_seconds",
    "temperature",
    "max_tokens",
    "max_retries",
    "retry_base_seconds",
    "retry_max_seconds",
}


def load_benchmark_manifest_metadata(benchmark: str, data_root: Path) -> JsonObject:
    """读取 benchmark.json 中 harness/experiment 通用的 manifest 元数据。"""
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    if data_root is None:
        raise ValueError("data_root 不能为空")
    normalized_benchmark = benchmark.strip().lower()
    manifest: dict[str, Any] = read_json_file(data_root / "benchmark.json", "benchmark.json", dict)
    manifest_benchmark = required_str(manifest, "benchmark", "benchmark.json").lower()
    if manifest_benchmark != normalized_benchmark:
        raise ValueError(f"benchmark.json 中的 benchmark 必须是 {normalized_benchmark}")
    source_root = required_str(manifest, "source_root", "benchmark.json")
    tool_backend = required_str(manifest, "tool_backend", "benchmark.json")
    language = manifest.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("benchmark.json language 必须是非空字符串")
    metadata: JsonObject = {
        "benchmark": manifest_benchmark,
        "source_root": source_root,
        "tool_backend": tool_backend,
        "language": language.strip(),
    }
    manifest_max_workers = parse_int_value(
        manifest.get("max_workers"),
        "benchmark.json max_workers",
        default=None,
        min_value=1,
    )
    if manifest_max_workers is not None:
        metadata["benchmark_max_workers"] = manifest_max_workers
    return metadata


def threshold_config_from_mapping(data: Mapping[str, Any] | None = None) -> ThresholdConfig:
    """从 JSON 映射生成 ThresholdConfig。"""
    if data is None:
        return ThresholdConfig()
    if not isinstance(data, Mapping):
        raise ValueError("threshold config 必须是 JSON 对象")
    defaults = ThresholdConfig()
    values: dict[str, float] = {}
    valid_fields = {field.name for field in fields(ThresholdConfig)}
    for key, value in data.items():
        if key not in valid_fields:
            raise ValueError(f"不支持的 threshold 字段: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"threshold 字段必须是数字: {key}")
        values[str(key)] = float(value)
    config = ThresholdConfig(**{field.name: values.get(field.name, getattr(defaults, field.name)) for field in fields(ThresholdConfig)})
    if config.fail_threshold > config.warn_threshold or config.warn_threshold > config.pass_threshold:
        raise ValueError("threshold 必须满足 fail <= warn <= pass")
    return config


def evaluation_strategy_from_mapping(data: Mapping[str, Any] | None = None) -> EvaluationStrategyConfig:
    """从 JSON 映射生成 EvaluationStrategyConfig。"""
    if data is None:
        return EvaluationStrategyConfig()
    if not isinstance(data, Mapping):
        raise ValueError("evaluation strategy 必须是 JSON 对象")
    fixed_level = _evaluation_level(data.get("fixed_judge_level"), default=EvaluationLevel.CHEAP)
    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("strategy.metadata 必须是 JSON 对象")
    return EvaluationStrategyConfig(
        dynamic_routing=data.get("dynamic_routing", True),
        dynamic_weighting=data.get("dynamic_weighting", True),
        policy_stop=data.get("policy_stop", True),
        fixed_judge_level=fixed_level,
        replay_continue_after_virtual_stop=data.get("replay_continue_after_virtual_stop", False),
        metadata={str(key): value for key, value in dict(metadata or {}).items()},
    )


def milestone_generation_from_mapping(
    data: Mapping[str, Any] | None,
) -> MilestoneGenerationConfig:
    """从 JSON 映射生成唯一的 milestone 生成配置。"""
    if data is None:
        return MilestoneGenerationConfig()
    if not isinstance(data, Mapping):
        raise TypeError("milestone_generation 必须是 JSON 对象")
    unknown = set(data) - {
        "use_origin_milestone",
        "max_candidate_path_count",
        "enable_repair",
        "generator",
    }
    if unknown:
        raise ValueError(f"不支持的 milestone_generation 字段: {sorted(unknown)}")
    generator = data.get("generator", {})
    if not isinstance(generator, Mapping):
        raise TypeError("milestone_generation.generator 必须是 JSON 对象")
    unknown_generator = set(generator) - _GENERATOR_CONFIG_FIELDS
    if unknown_generator:
        raise ValueError(f"不支持的 milestone generator 字段: {sorted(unknown_generator)}")
    return MilestoneGenerationConfig(
        use_origin_milestone=data.get("use_origin_milestone", True),
        max_candidate_path_count=data.get("max_candidate_path_count", 6),
        enable_repair=data.get("enable_repair", True),
        generator={str(key): value for key, value in generator.items()},
    )


def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    """从环境变量读取 ready frontier 无进展 patience。"""
    source = env if env is not None else os.environ
    return parse_int_value(
        source.get("DYNSTEER_READY_FRONTIER_PATIENCE"),
        "DYNSTEER_READY_FRONTIER_PATIENCE",
        default=DEFAULT_READY_FRONTIER_PATIENCE,
        min_value=1,
    )


def _case_ids_from_spec(spec: dict[str, Any], index: int) -> tuple[str, ...] | None:
    scenarios = spec.get("scenarios", [])
    if scenarios is None:
        return None
    if not isinstance(scenarios, list):
        raise ValueError(f"run_configs.json 第 {index} 项的 scenarios 必须是字符串数组")
    if not scenarios:
        return None
    case_ids: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"run_configs.json 第 {index} 项的 scenarios 不能包含空字符串")
        case_ids.append(scenario.strip())
    return tuple(case_ids)


def load_harness_run_configs(benchmark: str, data_root: Path, runs_dir: Path, results_dir: Path) -> list[HarnessRunConfig]:
    """从 benchmark data-root 加载多组 harness 运行配置。"""
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    if data_root is None:
        raise ValueError("data_root 不能为空")
    manifest_metadata = load_benchmark_manifest_metadata(benchmark, data_root)
    tool_backend = required_str(manifest_metadata, "tool_backend", "benchmark metadata")
    language = required_str(manifest_metadata, "language", "benchmark metadata")
    manifest_max_workers = manifest_metadata.get("benchmark_max_workers")
    raw_specs = read_json_file(data_root / "run_configs.json", "run_configs.json", list)
    if not raw_specs:
        raise ValueError("run_configs.json 至少需要包含一组运行配置")
    configs: list[HarnessRunConfig] = []
    ready_frontier_patience = load_ready_frontier_patience_from_env()
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"run_configs.json 第 {index} 项必须是 JSON 对象")
        thresholds = threshold_config_from_mapping(raw_spec.get("thresholds") if isinstance(raw_spec.get("thresholds"), dict) else None)
        strategy = evaluation_strategy_from_mapping(raw_spec.get("strategy") if isinstance(raw_spec.get("strategy"), dict) else None)
        milestone_generation = milestone_generation_from_mapping(
            raw_spec.get("milestone_generation")
        )
        metadata: JsonObject = {str(key): value for key, value in raw_spec.items() if key not in _RUN_CONFIG_CONTROL_FIELDS}
        metadata.update({key: normalize_client_config(raw_spec.get(key), f"run_configs.json 第 {index} 项的 {key}") for key in _CLIENT_CONFIG_KEYS if key in raw_spec})
        metadata.update(manifest_metadata)
        metadata["language"] = language
        if manifest_max_workers is not None:
            metadata["benchmark_max_workers"] = manifest_max_workers
        if required_str(manifest_metadata, "benchmark", "benchmark metadata") == "toolsandbox":
            metadata["agent"] = required_str(raw_spec, "agent", f"run_configs.json 第 {index} 项")
            metadata["user"] = required_str(raw_spec, "user", f"run_configs.json 第 {index} 项")
        metadata.setdefault("tool_backend", tool_backend)
        metadata["run_config_index"] = index
        name = optional_str(raw_spec.get("name"))
        if name is not None:
            metadata["run_config_name"] = name
        metadata["thresholds"] = {field.name: getattr(thresholds, field.name) for field in fields(ThresholdConfig)}
        metadata["strategy"] = strategy.to_dict()
        stop_on_ready = raw_spec.get("stop_on_ready_frontier_no_progress", True)
        configs.append(
            HarnessRunConfig(
                benchmark=benchmark.strip().lower(),
                data_root=data_root,
                case_ids=_case_ids_from_spec(raw_spec, index),
                runs_dir=runs_dir,
                results_dir=results_dir,
                stop_on_ready_frontier_no_progress=stop_on_ready,
                ready_frontier_patience=ready_frontier_patience,
                ready_frontier_min_delta=raw_spec.get("ready_frontier_min_delta", 0.02),
                milestone_generation=milestone_generation,
                metadata=metadata,
            )
        )
    return configs


def _evaluation_level(value: object, default: EvaluationLevel) -> EvaluationLevel:
    """读取 EvaluationLevel。"""
    if value is None:
        return default
    try:
        return EvaluationLevel(str(value))
    except ValueError as exc:
        raise ValueError(f"不支持的 fixed_judge_level: {value}") from exc
