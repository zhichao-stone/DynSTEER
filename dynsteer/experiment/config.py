from __future__ import annotations

import os
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
    RUN_CONFIG_CONTROL_FIELDS,
    load_benchmark_manifest_metadata,
    milestone_generation_from_mapping,
    threshold_config_from_mapping,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject, ThresholdConfig
from dynsteer.utils import assert_execution_neutral, optional_str, read_json_file, required_str


def load_experiment_config(path: Path | str) -> JsonObject:
    """Read the unified experiment JSON configuration."""
    if path is None:
        raise ValueError('Experimental configuration path cannot be empty')
    config_path = Path(path)
    if not str(config_path).strip():
        raise ValueError('Experimental configuration path cannot be empty')
    data = read_json_file(config_path, 'Experiment Configuration', dict)
    assert_execution_neutral(data, 'Experiment Configuration')
    experiment_id = required_str(data, "experiment_id", 'Experiment Configuration')
    _assert_single_benchmark(data)
    data["_config_path"] = str(config_path.resolve())
    return data


def expand_experiment_matrix(config: Mapping[str, Any]) -> list[ExperimentRunSpec]:
    """Expands the benchmark x model x method x repeat x threshold experiment matrix."""
    if config is None:
        raise ValueError("config must not be empty.")
    config_path = Path(str(config.get("_config_path", "experiment.json"))).resolve()
    experiment_id = required_str(config, "experiment_id", 'Experiment Configuration')
    runs_dir = Path(str(config.get("runs_dir", Path("runs") / "exp" / experiment_id)))
    results_dir = Path(str(config.get("results_dir", Path("results") / "exp" / experiment_id)))

    benchmarks = _list_specs(config, "benchmarks")
    models = _list_specs(config, "models", default=[{"model_id": "default"}])
    methods = _list_specs(config, "methods", default=[ExperimentMethod.DEFAULT.value, ExperimentMethod.DYNSTEER_REPLAY.value])
    repeats = int(config.get("repeats", 1))
    if repeats < 1:
        raise ValueError('Repeats must be greater than 0')

    judge_profiles = _profile_mapping(config.get("judge_profiles"), "judge_profiles")
    threshold_profiles = _profile_mapping(config.get("threshold_profiles"), "threshold_profiles")
    default_threshold_profile = str(config.get("default_threshold_profile", "default"))
    threshold_names = _threshold_names(config, threshold_profiles, default_threshold_profile)

    manifest_metadata_cache: dict[tuple[str, str], JsonObject] = {}
    specs: list[ExperimentRunSpec] = []
    for benchmark_spec in benchmarks:
        benchmark_data = _spec_mapping(benchmark_spec, "benchmark")
        benchmark = required_str(benchmark_data, "benchmark", 'Experiment Configuration').lower()
        data_root = _resolve_input_path(benchmark_data.get("data_root", Path("data") / benchmark), config_path.parent)
        case_ids = _case_ids(benchmark_data.get("case_ids") or benchmark_data.get("scenarios"))
        if not case_ids:
            raise ValueError(f"benchmark {benchmark} has empty case_ids/scenarios")
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
            model_id = required_str(model_data, "model_id", 'Experiment Configuration')
            model_metadata = _metadata(model_data)
            _validate_model_client(benchmark, model_data, model_id)

            for method_spec in methods:
                method_data = _spec_mapping(method_spec, "method")
                method = ExperimentMethod(required_str(method_data, "method", 'Experiment Configuration'))
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
    """Converts the experiment run spec to the currently available HarnessRunConfig for the current harness."""
    if spec is None:
        raise ValueError('Spec cannot be empty.')
    metadata = {**_runtime_metadata(spec), **spec.to_metadata()}
    return HarnessRunConfig(
        benchmark=spec.benchmark,
        data_root=spec.data_root,
        case_ids=spec.case_ids,
        runs_dir=spec.runs_dir,
        results_dir=spec.results_dir,
        use_milestone_graph=spec.strategy.use_milestone_graph,
        milestone_generation=spec.milestone_generation,
        metadata=metadata,
    )


def _runtime_metadata(spec: ExperimentRunSpec) -> JsonObject:
    raw_specs = read_json_file(spec.data_root / "run_configs.json", "run_configs.json", list)
    if not raw_specs or not isinstance(raw_specs[0], dict):
        raise ValueError('run_configs.json requires at least one JSON object')
    metadata = {str(key): value for key, value in raw_specs[0].items() if key not in RUN_CONFIG_CONTROL_FIELDS}
    metadata.update(load_benchmark_manifest_metadata(spec.benchmark, spec.data_root))
    for key in ("agent_client", "user_client", "client"):
        if key in raw_specs[0]:
            metadata[key] = raw_specs[0][key]
    return metadata


def _list_specs(config: Mapping[str, Any], key: str, default: list[object] | None = None) -> list[object]:
    raw_value = config.get(key, default)
    if not isinstance(raw_value, list) or not raw_value:
        raise ValueError(f"experiment configuration field {key} must be a non-empty array")
    return list(raw_value)


def _spec_mapping(value: object, default_key: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {default_key: value}
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"experimental matrix item must be a string or JSON object: {default_key}")


def _profile_mapping(value: object, label: str) -> dict[str, JsonObject]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    profiles: dict[str, JsonObject] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            raise ValueError(f"{label}.{key} must be a JSON object")
        profiles[str(key)] = dict(item)
    return profiles


def _threshold_names(config: Mapping[str, Any], profiles: dict[str, JsonObject], default_profile: str) -> list[str | None]:
    raw_names = config.get("threshold_matrix")
    if raw_names is None:
        return [default_profile if profiles else None]
    if not isinstance(raw_names, list) or not raw_names:
        raise ValueError('Threshold_matrix must be a non-empty array')
    return [str(item) for item in raw_names]


def _threshold_config(profiles: dict[str, JsonObject], profile_name: str | None) -> ThresholdConfig:
    if profile_name is None:
        return ThresholdConfig()
    if profile_name not in profiles:
        raise ValueError(f"threshold profile does not exist: {profile_name}")
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
    if method == ExperimentMethod.DYNSTEER_REPLAY_NO_MINEFIELDS:
        return replace(strategy, use_minefields=False)
    if method == ExperimentMethod.DYNSTEER_REPLAY_NO_MILESTONE_GRAPH:
        return replace(strategy, use_milestone_graph=False)
    if method == ExperimentMethod.DYNSTEER_REPLAY_NO_POLICY_STOP:
        return replace(strategy, policy_stop=False)
    return strategy


def _judge_config(profiles: dict[str, JsonObject], profile_name: str | None) -> JsonObject:
    if profile_name is None:
        return {}
    if profile_name not in profiles:
        raise ValueError(f"judge profile does not exist: {profile_name}")
    profile = dict(profiles[profile_name])
    for key in ("provider", "model"):
        if optional_str(profile.get(key)) is None:
            raise ValueError(f"judge profile must provide non-empty {key}")
    base_url = _required_setting(profile, "base_url", "judge profile")
    if not base_url.startswith("https://"):
        raise ValueError('Judge profile base_url must start with https://')
    return profile


def _validate_model_client(benchmark: str, model_data: Mapping[str, Any], model_id: str) -> None:
    if benchmark == "swebench_pro":
        harness_metadata = model_data.get("harness_metadata")
        client = harness_metadata.get("client") if isinstance(harness_metadata, dict) else None
        if not isinstance(client, dict):
            raise ValueError(f"model {model_id} is missing harness_metadata.client")
        if optional_str(client.get("model")) != model_id:
            raise ValueError(f"model {model_id} harness_metadata.client.model must match model_id")
        client_label = f"model {model_id} harness_metadata.client"
        base_url = _required_setting(client, "base_url", client_label)
        _required_setting(client, "api_key", client_label)
        if not base_url.startswith("https://"):
            raise ValueError(f"model {model_id} base_url must use https")
        return
    if benchmark != "toolsandbox":
        return
    harness_metadata = model_data.get("harness_metadata")
    agent_client = harness_metadata.get("agent_client") if isinstance(harness_metadata, dict) else None
    if not isinstance(agent_client, dict):
        raise ValueError(f"ToolSandbox model {model_id} lacks harness_metadata.agent_client")
    _required_setting(agent_client, "api_key", f"ToolSandbox model {model_id} agent_client")


def _assert_single_benchmark(config: Mapping[str, Any]) -> None:
    benchmarks = config.get("benchmarks")
    if not isinstance(benchmarks, list) or len(benchmarks) != 1 or not isinstance(benchmarks[0], dict):
        raise ValueError('an experiment configuration must contain exactly one benchmark spec')
    if optional_str(benchmarks[0].get("benchmark")) is None:
        raise ValueError('benchmarks[0].benchmark must not be empty.')


def _case_ids(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError('Case_ids/scenarios must be a string array')
    case_ids = tuple((str(item).strip() for item in value))
    if any(not item for item in case_ids):
        raise ValueError('Case_ids/scenarios cannot contain empty strings')
    if len(case_ids) != len(set(case_ids)):
        raise ValueError('Case_ids/scenarios cannot contain duplicates')
    return case_ids or None


def _metadata(data: Mapping[str, Any]) -> JsonObject:
    raw_metadata = data.get("metadata")
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, dict):
        raise ValueError('Metadata must be a JSON object')
    return {str(key): value for key, value in raw_metadata.items()}


def _merge_metadata(*values: object) -> JsonObject:
    merged: JsonObject = {}
    for value in values:
        if value:
            if not isinstance(value, dict):
                raise ValueError('metadata/harness_metadata must be a JSON object')
            merged.update({str(key): item for key, item in value.items()})
    return merged


def _resolve_input_path(value: object, config_dir: Path) -> Path:
    if value is None:
        raise ValueError('Path cannot be empty')
    path = Path(str(value))
    if path.is_absolute():
        return path
    candidate = config_dir / path
    if candidate.exists():
        return candidate
    return Path.cwd() / path

def _required_setting(data: Mapping[str, Any], key: str, label: str) -> str:
    value = optional_str(data.get(key))
    if value is not None:
        return value
    environment_name = optional_str(data.get(f"{key}_env"))
    if environment_name is None:
        raise ValueError(f"{label} must provide {key} or {key}_env")
    value = os.environ.get(environment_name)
    if value is None or not value.strip():
        raise ValueError(f"{label} environment variable is not set: {environment_name}")
    return value
