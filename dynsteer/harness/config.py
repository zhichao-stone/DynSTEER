import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from dynsteer.experiment.model import EvaluationStrategyConfig
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import MilestoneGenerationConfig
from dynsteer.model import EvaluationLevel, JsonObject, ThresholdConfig
from dynsteer.utils import (
    assert_execution_neutral,
    normalize_client_config,
    optional_str,
    parse_int_value,
    read_json_file,
    required_str,
)

DEFAULT_READY_FRONTIER_PATIENCE = 8
_CLIENT_CONFIG_KEYS = ("agent_client", "user_client", "client")
RUN_CONFIG_CONTROL_FIELDS = {
    "scenarios",
    "ready_frontier_patience",
    "stop_on_ready_frontier_no_progress",
    "ready_frontier_min_delta",
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
    "base_url_env",
    "api_key",
    "api_key_env",
    "timeout_seconds",
    "temperature",
    "max_tokens",
    "max_retries",
    "retry_base_seconds",
    "retry_max_seconds",
    "seed",
}


def load_benchmark_manifest_metadata(benchmark: str, data_root: Path) -> JsonObject:
    """Read manifest metadata shared by harness and experiment runs."""
    if benchmark is None or not benchmark.strip():
        raise ValueError('Benchmark cannot be empty.')
    if data_root is None:
        raise ValueError('data_root cannot be empty')
    normalized_benchmark = benchmark.strip().lower()
    manifest: dict[str, Any] = read_json_file(data_root / "benchmark.json", "benchmark.json", dict)
    assert_execution_neutral(manifest, "benchmark.json")
    manifest_benchmark = required_str(manifest, "benchmark", "benchmark.json").lower()
    if manifest_benchmark != normalized_benchmark:
        raise ValueError(f"benchmark.json benchmark must be {normalized_benchmark}")
    source_root = required_str(manifest, "source_root", "benchmark.json")
    tool_backend = required_str(manifest, "tool_backend", "benchmark.json")
    language = manifest.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        raise ValueError('benchmark.json language must be a non-empty string')
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
    if normalized_benchmark == "swebench_pro":
        metadata["dataset_archive"] = required_str(manifest, "dataset_archive", "benchmark.json")
        metadata["dockerhub_username"] = required_str(manifest, "dockerhub_username", "benchmark.json")
    return metadata


def threshold_config_from_mapping(data: Mapping[str, Any] | None = None) -> ThresholdConfig:
    """Build a ThresholdConfig from a JSON mapping."""
    if data is None:
        return ThresholdConfig()
    if not isinstance(data, Mapping):
        raise ValueError("threshold config must be a JSON object")
    defaults = ThresholdConfig()
    values: dict[str, float] = {}
    valid_fields = {field.name for field in fields(ThresholdConfig)}
    for key, value in data.items():
        if key not in valid_fields:
            raise ValueError(f"Unsupported threshold field: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Threshold field must be a number: {key}")
        values[str(key)] = float(value)
    config = ThresholdConfig(**{field.name: values.get(field.name, getattr(defaults, field.name)) for field in fields(ThresholdConfig)})
    if config.fail_threshold > config.warn_threshold or config.warn_threshold > config.pass_threshold:
        raise ValueError("threshold values must satisfy fail_threshold <= warn_threshold <= pass_threshold")
    return config


def evaluation_strategy_from_mapping(data: Mapping[str, Any] | None = None) -> EvaluationStrategyConfig:
    """Build an EvaluationStrategyConfig from a JSON mapping."""
    if data is None:
        return EvaluationStrategyConfig()
    if not isinstance(data, Mapping):
        raise ValueError("evaluation strategy must be a JSON object")
    fixed_level = _evaluation_level(data.get("fixed_judge_level"), default=EvaluationLevel.CHEAP)
    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('strategy.metadata must be a JSON object')
    return EvaluationStrategyConfig(
        dynamic_routing=data.get("dynamic_routing", True),
        dynamic_weighting=data.get("dynamic_weighting", True),
        policy_stop=data.get("policy_stop", True),
        use_milestone_graph=data.get("use_milestone_graph", True),
        use_minefields=data.get("use_minefields", True),
        max_interventions=data.get("max_interventions", 2),
        fixed_judge_level=fixed_level,
        replay_continue_after_virtual_stop=data.get("replay_continue_after_virtual_stop", False),
        metadata={str(key): value for key, value in dict(metadata or {}).items()},
    )


def milestone_generation_from_mapping(
    data: Mapping[str, Any] | None,
) -> MilestoneGenerationConfig:
    """Build the sole milestone-generation configuration from a JSON mapping."""
    if data is None:
        return MilestoneGenerationConfig()
    if not isinstance(data, Mapping):
        raise TypeError('milestone_generation must be a JSON object')
    unknown = set(data) - {
        "use_origin_milestone",
        "target_candidate_graph_count",
        "max_candidate_batch_count",
        "generator",
    }
    if unknown:
        raise ValueError(f"Unsupported milestone_generation field: {sorted(unknown)}")
    generator = data.get("generator", {})
    if not isinstance(generator, Mapping):
        raise TypeError('milestone_generation.generator must be a JSON object')
    unknown_generator = set(generator) - _GENERATOR_CONFIG_FIELDS
    if unknown_generator:
        raise ValueError(f"Unsupported milestone generator field: {sorted(unknown_generator)}")
    return MilestoneGenerationConfig(
        use_origin_milestone=data.get("use_origin_milestone", True),
        target_candidate_graph_count=data.get("target_candidate_graph_count", 6),
        max_candidate_batch_count=data.get("max_candidate_batch_count", 4),
        generator={str(key): value for key, value in generator.items()},
    )


def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    """Read the ready-frontier no-progress patience from an environment mapping."""
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
        raise ValueError(f"run_configs.json item {index} scenarios must be a string array")
    if not scenarios:
        return None
    case_ids: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"run_configs.json item {index} scenarios must not contain empty strings")
        case_ids.append(scenario.strip())
    return tuple(case_ids)


def load_harness_run_configs(benchmark: str, data_root: Path, runs_dir: Path, results_dir: Path) -> list[HarnessRunConfig]:
    """Load all harness run configurations from a benchmark data root."""
    if benchmark is None or not benchmark.strip():
        raise ValueError('Benchmark cannot be empty.')
    if data_root is None:
        raise ValueError('data_root cannot be empty')
    manifest_metadata = load_benchmark_manifest_metadata(benchmark, data_root)
    tool_backend = required_str(manifest_metadata, "tool_backend", "benchmark metadata")
    language = required_str(manifest_metadata, "language", "benchmark metadata")
    manifest_max_workers = manifest_metadata.get("benchmark_max_workers")
    raw_specs = read_json_file(data_root / "run_configs.json", "run_configs.json", list)
    if not raw_specs:
        raise ValueError('run_configs.json must contain at least one run configuration')
    configs: list[HarnessRunConfig] = []
    config_benchmark = benchmark.strip().lower()
    ready_frontier_patience = load_ready_frontier_patience_from_env()
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"run_configs.json item {index} must be a JSON object")
        assert_execution_neutral(raw_spec, f"run_configs.json item {index}")
        thresholds = threshold_config_from_mapping(raw_spec.get("thresholds") if isinstance(raw_spec.get("thresholds"), dict) else None)
        strategy = evaluation_strategy_from_mapping(raw_spec.get("strategy") if isinstance(raw_spec.get("strategy"), dict) else None)
        milestone_generation = milestone_generation_from_mapping(
            raw_spec.get("milestone_generation")
        )
        metadata: JsonObject = {str(key): value for key, value in raw_spec.items() if key not in RUN_CONFIG_CONTROL_FIELDS}
        metadata.update({key: normalize_client_config(raw_spec.get(key), f"run_configs.json item {index} {key}") for key in _CLIENT_CONFIG_KEYS if key in raw_spec})
        metadata.update(manifest_metadata)
        metadata["language"] = language
        if manifest_max_workers is not None:
            metadata["benchmark_max_workers"] = manifest_max_workers
        if config_benchmark == "toolsandbox":
            metadata["agent"] = required_str(raw_spec, "agent", f"run_configs.json item {index}")
            metadata["user"] = required_str(raw_spec, "user", f"run_configs.json item {index}")
        elif config_benchmark == "swebench_pro":
            _validate_source_direct_runtime(raw_spec, f"run_configs.json item {index}")
            metadata["max_tool_calls"] = int(raw_spec["max_tool_calls"])
            metadata["command_timeout_seconds"] = int(raw_spec["command_timeout_seconds"])
            metadata["model_temperature"] = float(raw_spec["model_temperature"])
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
                benchmark=config_benchmark,
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


def _validate_source_direct_runtime(raw_spec: dict[str, Any], label: str) -> None:
    try:
        max_tool_calls = int(raw_spec["max_tool_calls"])
        timeout = int(raw_spec["command_timeout_seconds"])
        temperature = float(raw_spec["model_temperature"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} has invalid source-direct runtime parameters") from exc
    if max_tool_calls <= 0 or timeout <= 0 or not 0 <= temperature <= 2:
        raise ValueError(f"{label} max_tool_calls, command_timeout_seconds, or model_temperature is out of range")


def _evaluation_level(value: object, default: EvaluationLevel) -> EvaluationLevel:
    """Read an EvaluationLevel value."""
    if value is None:
        return default
    try:
        return EvaluationLevel(str(value))
    except ValueError as exc:
        raise ValueError(f"Unsupported fixed_judge_level: {value}") from exc
