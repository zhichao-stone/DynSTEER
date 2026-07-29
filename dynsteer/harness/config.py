import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from dynsteer.experiment.model import EvaluationStrategyConfig
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import EvaluationLevel, JsonObject, ThresholdConfig
from dynsteer.utils import optional_str, read_json_file, required_str

DEFAULT_READY_FRONTIER_PATIENCE = 8


def load_judge_config_from_env(env: Mapping[str, str] | None = None) -> JsonObject:
    """从环境变量读取 LLMJudge 配置。"""
    source = env or os.environ
    provider = source.get("DYNSTEER_JUDGE_PROVIDER")
    if provider is None or not provider.strip():
        return {}
    model = source.get("DYNSTEER_JUDGE_MODEL")
    if model is None or not model.strip():
        raise ValueError("DYNSTEER_JUDGE_MODEL 不能为空")
    return {
        "provider": provider.strip(),
        "model": model.strip(),
        "base_url": source.get("DYNSTEER_JUDGE_BASE_URL"),
        "timeout_seconds": float(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS", "60")),
        "temperature": float(source.get("DYNSTEER_JUDGE_TEMPERATURE", "0")),
        "max_tokens": _optional_positive_int(source.get("DYNSTEER_JUDGE_MAX_TOKENS"), "DYNSTEER_JUDGE_MAX_TOKENS"),
        "max_retries": int(source.get("DYNSTEER_JUDGE_MAX_RETRIES", "3")),
        "retry_base_seconds": float(source.get("DYNSTEER_JUDGE_RETRY_BASE_SECONDS", "1.0")),
        "retry_max_seconds": float(source.get("DYNSTEER_JUDGE_RETRY_MAX_SECONDS", "8.0")),
        "standard_passes": int(source.get("DYNSTEER_STANDARD_JUDGE_PASSES", "3")),
        "expensive_passes": int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3")),
        "api_key_configured": bool(source.get("DYNSTEER_JUDGE_API_KEY")),
    }


def threshold_config_from_mapping(data: Mapping[str, Any] | None = None) -> ThresholdConfig:
    """从 JSON 映射生成 ThresholdConfig。

    入参：
        data: 阈值字段映射；缺失字段使用默认值。
    输出：
        可传入 evaluator 的 ThresholdConfig。
    """
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
        dynamic_routing=_bool_from_mapping(data, "dynamic_routing", True),
        dynamic_weighting=_bool_from_mapping(data, "dynamic_weighting", True),
        policy_stop=_bool_from_mapping(data, "policy_stop", True),
        guidance_enabled=_bool_from_mapping(data, "guidance_enabled", False),
        fixed_judge_level=fixed_level,
        replay_continue_after_virtual_stop=_bool_from_mapping(data, "replay_continue_after_virtual_stop", False),
        metadata={str(key): value for key, value in dict(metadata or {}).items()},
    )


def load_ready_frontier_patience_from_env(env: Mapping[str, str] | None = None) -> int:
    """从环境变量读取 ready frontier 无进展 patience。"""
    source = env if env is not None else os.environ
    raw_value = source.get("DYNSTEER_READY_FRONTIER_PATIENCE")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_READY_FRONTIER_PATIENCE
    try:
        patience = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError("DYNSTEER_READY_FRONTIER_PATIENCE 必须是整数") from exc
    if patience < 1:
        raise ValueError("DYNSTEER_READY_FRONTIER_PATIENCE 必须大于 0")
    return patience


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


def load_harness_run_configs(
    benchmark: str, data_root: Path, runs_dir: Path, results_dir: Path
) -> list[HarnessRunConfig]:
    """从 benchmark data-root 加载多组 harness 运行配置。"""
    if benchmark is None or not benchmark.strip():
        raise ValueError("benchmark 不能为空")
    if data_root is None:
        raise ValueError("data_root 不能为空")
    normalized_benchmark = benchmark.strip().lower()
    manifest: dict = read_json_file(data_root / "benchmark.json", "benchmark.json", dict)
    manifest_benchmark = required_str(manifest, "benchmark", "benchmark.json").lower()
    if manifest_benchmark != normalized_benchmark:
        raise ValueError(f"benchmark.json 中的 benchmark 必须是 {normalized_benchmark}")
    required_str(manifest, "source_root", "benchmark.json")
    tool_backend = required_str(manifest, "tool_backend", "benchmark.json")
    language = manifest.get("language", "en")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("benchmark.json language 必须是非空字符串")
    language = language.strip()
    manifest_max_workers = manifest.get("max_workers")
    if manifest_max_workers is None or not isinstance(manifest_max_workers, int):
        raise ValueError("benchmark.json max_workers 必须是整数")
    manifest_max_workers = max(manifest_max_workers, 1)

    raw_specs = read_json_file(data_root / "run_configs.json", "run_configs.json", list)
    if not raw_specs:
        raise ValueError("run_configs.json 至少需要包含一组运行配置")

    configs: list[HarnessRunConfig] = []
    seen_run_ids: set[str] = set()
    judge_config = load_judge_config_from_env()
    ready_frontier_patience = load_ready_frontier_patience_from_env()
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"run_configs.json 第 {index} 项必须是 JSON 对象")
        thresholds = threshold_config_from_mapping(raw_spec.get("thresholds") if isinstance(raw_spec.get("thresholds"), dict) else None)
        strategy = evaluation_strategy_from_mapping(raw_spec.get("strategy") if isinstance(raw_spec.get("strategy"), dict) else None)
        metadata: JsonObject = {
            str(key): value
            for key, value in raw_spec.items()
            if key not in {"scenarios", "ready_frontier_patience", "thresholds", "strategy"}
        }
        metadata["language"] = language
        metadata["benchmark_max_workers"] = manifest_max_workers
        if normalized_benchmark == "toolsandbox":
            required_str(raw_spec, "agent", f"run_configs.json 第 {index} 项")
            required_str(raw_spec, "user", f"run_configs.json 第 {index} 项")
        metadata.setdefault("tool_backend", tool_backend)
        run_id = optional_str(raw_spec.get("run_id")) or optional_str(raw_spec.get("name"))
        if run_id is None:
            agent = optional_str(raw_spec.get("agent"))
            user = optional_str(raw_spec.get("user"))
            if agent is not None and user is not None:
                run_id = f"run_{index}_{agent}_user_{user}"
            else:
                run_id = f"run_{index}"
        if run_id in seen_run_ids:
            raise ValueError(f"run_configs.json 中 run_id 重复: {run_id}")
        seen_run_ids.add(run_id)
        metadata["run_config_index"] = index
        metadata["run_id"] = run_id
        name = optional_str(raw_spec.get("name"))
        if name is not None:
            metadata["run_config_name"] = name
        if judge_config:
            metadata["judge"] = judge_config
        metadata["thresholds"] = {
            field.name: getattr(thresholds, field.name) for field in fields(ThresholdConfig)
        }
        metadata["strategy"] = strategy.to_dict()
        stop_on_ready = raw_spec.get("stop_on_ready_frontier_no_progress", True)
        if not isinstance(stop_on_ready, bool):
            raise ValueError("run_configs.json 字段 stop_on_ready_frontier_no_progress 必须是布尔值")
        ready_min_delta = raw_spec.get("ready_frontier_min_delta", 0.02)
        if not isinstance(ready_min_delta, (int, float)):
            raise ValueError("run_configs.json 字段 ready_frontier_min_delta 必须是数字")
        ready_min_delta = float(ready_min_delta)
        if ready_min_delta < 0:
            raise ValueError("run_configs.json 字段 ready_frontier_min_delta 不能为负数")
        configs.append(
            HarnessRunConfig(
                benchmark=normalized_benchmark,
                data_root=data_root,
                case_ids=_case_ids_from_spec(raw_spec, index),
                runs_dir=runs_dir,
                results_dir=results_dir,
                stop_on_ready_frontier_no_progress=stop_on_ready,
                ready_frontier_patience=ready_frontier_patience,
                ready_frontier_min_delta=ready_min_delta,
                metadata=metadata,
            )
        )
    return configs


def _optional_positive_int(value: str | None, label: str) -> int | None:
    """读取可选正整数。"""
    if value is None or not value.strip():
        return None
    parsed = int(value.strip())
    if parsed <= 0:
        raise ValueError(f"{label} 必须是正整数")
    return parsed


def _bool_from_mapping(data: Mapping[str, Any], key: str, default: bool) -> bool:
    """从 JSON 映射读取 bool 字段。"""
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"strategy.{key} 必须是 bool")
    return value


def _evaluation_level(value: object, default: EvaluationLevel) -> EvaluationLevel:
    """读取 EvaluationLevel。"""
    if value is None:
        return default
    try:
        return EvaluationLevel(str(value))
    except ValueError as exc:
        raise ValueError(f"不支持的 fixed_judge_level: {value}") from exc
