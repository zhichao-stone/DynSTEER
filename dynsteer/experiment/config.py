from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from dynsteer.experiment.model import (
    EvaluationStrategyConfig,
    ExperimentMethod,
    ExperimentRunSpec,
)
from dynsteer.harness.config import (
    evaluation_strategy_from_mapping,
    load_benchmark_manifest_metadata,
    milestone_generation_from_mapping,
    threshold_config_from_mapping,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject, ThresholdConfig
from dynsteer.utils import optional_str, read_json_file, required_str


def load_experiment_config(path: Path | str) -> JsonObject:
    """读取统一实验 JSON 配置。"""
    if path is None:
        raise ValueError("实验配置路径不能为空")
    config_path = Path(path)
    if not str(config_path).strip():
        raise ValueError("实验配置路径不能为空")
    data = read_json_file(config_path, "实验配置", dict)
    data["_config_path"] = str(config_path.resolve())
    return data


def expand_experiment_matrix(config: Mapping[str, Any]) -> list[ExperimentRunSpec]:
    """展开 benchmark × model × method × repeat × threshold profile 实验矩阵。"""
    if config is None:
        raise ValueError("config 不能为空")
    config_path = Path(str(config.get("_config_path", "experiment.json"))).resolve()
    experiment_id = required_str(config, "experiment_id", "实验配置")
    runs_dir = Path(str(config.get("runs_dir", Path("runs") / "exp" / experiment_id)))
    results_dir = Path(str(config.get("results_dir", Path("results") / "exp" / experiment_id)))

    benchmarks = _list_specs(config, "benchmarks")
    models = _list_specs(config, "models", default=[{"model_id": "default"}])
    methods = _list_specs(config, "methods", default=[ExperimentMethod.DEFAULT.value, ExperimentMethod.DYNSTEER_REPLAY.value])
    repeats = int(config.get("repeats", 1))
    if repeats < 1:
        raise ValueError("repeats 必须大于 0")

    judge_profiles = _profile_mapping(config.get("judge_profiles"), "judge_profiles")
    threshold_profiles = _profile_mapping(config.get("threshold_profiles"), "threshold_profiles")
    default_threshold_profile = str(config.get("default_threshold_profile", "default"))
    threshold_names = _threshold_names(config, threshold_profiles, default_threshold_profile)

    manifest_metadata_cache: dict[tuple[str, str], JsonObject] = {}
    specs: list[ExperimentRunSpec] = []
    for benchmark_spec in benchmarks:
        benchmark_data = _spec_mapping(benchmark_spec, "benchmark")
        benchmark = required_str(benchmark_data, "benchmark", "实验配置").lower()
        data_root = _resolve_input_path(benchmark_data.get("data_root", Path("data") / benchmark), config_path.parent)
        case_ids = _case_ids(benchmark_data.get("case_ids") or benchmark_data.get("scenarios"))
        if not case_ids:
            raise ValueError(f"Benchmark {benchmark} 的 case_ids/scenarios 为空")
        manifest_cache_key = (benchmark, str(data_root.resolve()))
        manifest_metadata = manifest_metadata_cache.get(manifest_cache_key)
        if manifest_metadata is None:
            manifest_metadata = load_benchmark_manifest_metadata(benchmark, data_root)
            manifest_metadata_cache[manifest_cache_key] = manifest_metadata
        benchmark_metadata = _metadata(benchmark_data)
        milestone_generation = milestone_generation_from_mapping(
            benchmark_data.get("milestone_generation")
        )

        for model_spec in models:
            model_data = _spec_mapping(model_spec, "model_id")
            model_id = required_str(model_data, "model_id", "实验配置")
            model_metadata = _metadata(model_data)

            for method_spec in methods:
                method_data = _spec_mapping(method_spec, "method")
                method = ExperimentMethod(required_str(method_data, "method", "实验配置"))
                method_metadata = _metadata(method_data)
                strategy = _strategy_for_method(method, method_data.get("strategy"))

                judge_profile = optional_str(method_data.get("judge_profile")) or optional_str(model_data.get("judge_profile"))
                judge_config = _judge_config(judge_profiles, judge_profile)

                for threshold_name in threshold_names:
                    thresholds = _threshold_config(threshold_profiles, threshold_name)

                    for repeat_index in range(repeats):
                        metadata = _merge_metadata(
                            manifest_metadata,
                            config.get("metadata"),
                            benchmark_metadata,
                            model_metadata,
                            method_metadata,
                            model_data.get("harness_metadata"),
                            method_data.get("harness_metadata"),
                        )
                        specs.append(
                            ExperimentRunSpec(
                                experiment_id=experiment_id,
                                benchmark=benchmark,
                                data_root=data_root,
                                runs_dir=runs_dir,
                                results_dir=results_dir,
                                case_ids=case_ids,
                                model_id=model_id,
                                repeat_index=repeat_index,
                                method=method,
                                repeat_count=repeats,
                                judge_profile=judge_profile,
                                judge_config=judge_config,
                                threshold_profile=threshold_name,
                                thresholds=thresholds,
                                strategy=strategy,
                                milestone_generation=milestone_generation,
                                metadata=metadata,
                            )
                        )

    return specs


def build_harness_config(spec: ExperimentRunSpec) -> HarnessRunConfig:
    """把实验 run spec 转换为当前 harness 可用的 HarnessRunConfig。"""
    if spec is None:
        raise ValueError("spec 不能为空")
    return HarnessRunConfig(
        benchmark=spec.benchmark,
        data_root=spec.data_root,
        case_ids=spec.case_ids,
        runs_dir=spec.runs_dir,
        results_dir=spec.results_dir,
        milestone_generation=spec.milestone_generation,
        metadata=spec.to_metadata(),
    )


def _list_specs(config: Mapping[str, Any], key: str, default: list[object] | None = None) -> list[object]:
    raw_value = config.get(key, default)
    if not isinstance(raw_value, list) or not raw_value:
        raise ValueError(f"实验配置字段 {key} 必须是非空数组")
    return list(raw_value)


def _spec_mapping(value: object, default_key: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {default_key: value}
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"实验矩阵项必须是字符串或 JSON 对象: {default_key}")


def _profile_mapping(value: object, label: str) -> dict[str, JsonObject]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    profiles: dict[str, JsonObject] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            raise ValueError(f"{label}.{key} 必须是 JSON 对象")
        profiles[str(key)] = dict(item)
    return profiles


def _threshold_names(config: Mapping[str, Any], profiles: dict[str, JsonObject], default_profile: str) -> list[str | None]:
    raw_names = config.get("threshold_matrix")
    if raw_names is None:
        return [default_profile if profiles else None]
    if not isinstance(raw_names, list) or not raw_names:
        raise ValueError("threshold_matrix 必须是非空数组")
    return [str(item) for item in raw_names]


def _threshold_config(profiles: dict[str, JsonObject], profile_name: str | None) -> ThresholdConfig:
    if profile_name is None:
        return ThresholdConfig()
    if profile_name not in profiles:
        raise ValueError(f"threshold profile 不存在: {profile_name}")
    return threshold_config_from_mapping(profiles[profile_name])


def _strategy_for_method(method: ExperimentMethod, raw_strategy: object) -> EvaluationStrategyConfig:
    strategy = evaluation_strategy_from_mapping(raw_strategy if isinstance(raw_strategy, dict) else {})
    if method == ExperimentMethod.DEFAULT:
        return replace(strategy, policy_stop=False)
    if method == ExperimentMethod.DYNSTEER_REPLAY_STATIC:
        return replace(strategy, dynamic_routing=False, dynamic_weighting=False)
    if method == ExperimentMethod.DYNSTEER_REPLAY_STATIC_WEIGHTING:
        return replace(strategy, dynamic_routing=True, dynamic_weighting=False)
    if method == ExperimentMethod.DYNSTEER_REPLAY_STATIC_ROUTING:
        return replace(strategy, dynamic_routing=False, dynamic_weighting=True)
    return strategy


def _judge_config(profiles: dict[str, JsonObject], profile_name: str | None) -> JsonObject:
    if profile_name is None:
        return {}
    if profile_name not in profiles:
        raise ValueError(f"judge profile 不存在: {profile_name}")
    return dict(profiles[profile_name])


def _case_ids(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("case_ids/scenarios 必须是字符串数组")
    case_ids = tuple((str(item).strip() for item in value))
    if any(not item for item in case_ids):
        raise ValueError("case_ids/scenarios 不能包含空字符串")
    return case_ids or None


def _metadata(data: Mapping[str, Any]) -> JsonObject:
    raw_metadata = data.get("metadata")
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata 必须是 JSON 对象")
    return {str(key): value for key, value in raw_metadata.items()}


def _merge_metadata(*values: object) -> JsonObject:
    merged: JsonObject = {}
    for value in values:
        if value:
            if not isinstance(value, dict):
                raise ValueError("metadata/harness_metadata 必须是 JSON 对象")
            merged.update({str(key): item for key, item in value.items()})
    return merged


def _resolve_input_path(value: object, config_dir: Path) -> Path:
    if value is None:
        raise ValueError("路径不能为空")
    path = Path(str(value))
    if path.is_absolute():
        return path
    candidate = config_dir / path
    if candidate.exists():
        return candidate
    return Path.cwd() / path
