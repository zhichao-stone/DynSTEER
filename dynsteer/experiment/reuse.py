from __future__ import annotations

import json
import shutil
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dynsteer.experiment.config import (
    build_harness_config,
    expand_experiment_matrix,
    load_experiment_config,
)
from dynsteer.experiment.model import ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.outputs import existing_case_output
from dynsteer.harness.paths import case_output_dir
from dynsteer.model import HarnessEvaluationOutput, JsonObject
from dynsteer.utils import optional_str, read_json_file


logger = logging.getLogger(__name__)


ReuseKey = tuple[str, str, int, str, str | None, str]


class ExperimentReuseError(RuntimeError):
    """Toss out when the configuration or source of the multi-experiment reuse is not legal."""


@dataclass(frozen=True)
class SourceExperiment:
    config_path: Path
    methods: tuple[ExperimentMethod, ...]


@dataclass
class ReuseStats:
    imported: int = 0
    existing: int = 0
    missing: int = 0

    def summary(self) -> str:
        return f"imported={self.imported}, existing={self.existing}, missing={self.missing}"


def reuse_experiment_outputs(
    config: Mapping[str, object],
    target_specs: list[ExperimentRunSpec],
) -> ReuseStats:
    """Imports the full reusable case output from the source experiment into the current experiment."""
    if config is None or target_specs is None:
        raise ValueError('Config and target_specs cannot be empty')
    stats = ReuseStats()
    definitions = _source_experiments(config)
    if not definitions:
        return stats

    target_experiment_id = str(config["experiment_id"])
    for definition in definitions:
        source_config = load_experiment_config(definition.config_path)
        source_experiment_id = str(source_config["experiment_id"])
        source_specs = expand_experiment_matrix(source_config)
        stats = _import_source_experiment(
            source_experiment_id=source_experiment_id,
            source_specs=source_specs,
            methods=definition.methods,
            target_specs=target_specs,
            previous_stats=stats,
        )
        logger.info(
            'Cross-experiment reuse completed.',
            extra={
                "source_experiment_id": source_experiment_id,
                "imported": stats.imported,
                "existing": stats.existing,
                "missing": stats.missing,
            },
        )
    return stats


def _source_experiments(config: Mapping[str, object]) -> list[SourceExperiment]:
    raw_definitions = config.get("source_experiments")
    if raw_definitions is None:
        return []
    if not isinstance(raw_definitions, list) or not raw_definitions:
        raise ValueError('source_experiments must be a non-empty array')
    config_path = Path(str(config.get("_config_path", "experiment.json"))).resolve()

    definitions: list[SourceExperiment] = []
    for index, raw_definition in enumerate(raw_definitions):
        if not isinstance(raw_definition, dict):
            raise ValueError(f"source_experiments[{index}] Must be a JSON object")
        source_path = optional_str(raw_definition.get("config"))
        if source_path is None:
            raise ValueError(f"source_experiments[{index}].config must be non-empty.")
        raw_methods = raw_definition.get("methods")
        if not isinstance(raw_methods, list) or not raw_methods:
            raise ValueError(f"source_experiments[{index}].methods must be non-empty arrays")
        methods = tuple(ExperimentMethod(str(method)) for method in raw_methods)
        definitions.append(SourceExperiment((config_path.parent / source_path).resolve(), methods))
    return definitions


def _import_source_experiment(
    *,
    source_experiment_id: str,
    source_specs: list[ExperimentRunSpec],
    methods: tuple[ExperimentMethod, ...],
    target_specs: list[ExperimentRunSpec],
    previous_stats: ReuseStats,
) -> ReuseStats:
    source_by_key: dict[ReuseKey, ExperimentRunSpec] = {}
    for spec in source_specs:
        if spec.method not in methods:
            continue
        for case_id in spec.case_ids or ():
            key = _reuse_key(spec, case_id)
            if key in source_by_key:
                raise ExperimentReuseError(f"The source experiment has duplicated the key:{key}")
            source_by_key[key] = spec

    stats = previous_stats
    for target_spec in target_specs:
        if target_spec.method not in methods:
            continue
        for case_id in target_spec.case_ids or ():
            key = _reuse_key(target_spec, case_id)
            source_spec = source_by_key.get(key)
            if source_spec is None:
                stats.missing += 1
                continue
            _assert_compatible(source_spec, target_spec, case_id)
            source_output = existing_case_output(
                build_harness_config(source_spec), case_id, source_spec.method.value
            )
            if source_output is None:
                stats.missing += 1
                continue
            target_output = existing_case_output(
                build_harness_config(target_spec), case_id, target_spec.method.value
            )
            if target_output is not None:
                stats.existing += 1
                continue
            _copy_case_output(source_spec, target_spec, case_id, source_output)
            stats.imported += 1
    return stats


def _reuse_key(spec: ExperimentRunSpec, case_id: str) -> ReuseKey:
    return (
        spec.benchmark,
        spec.model_id,
        spec.repeat_index,
        spec.method.value,
        spec.threshold_profile,
        case_id,
    )


def _assert_compatible(source: ExperimentRunSpec, target: ExperimentRunSpec, case_id: str) -> None:
    fields = (
        "benchmark",
        "model_id",
        "repeat_index",
        "repeat_count",
        "judge_profile",
        "judge_config",
        "threshold_profile",
        "thresholds",
        "strategy",
        "milestone_generation",
        "metadata",
    )
    different = [field for field in fields if getattr(source, field) != getattr(target, field)]
    if source.data_root.resolve() != target.data_root.resolve():
        different.append("data_root")
    if different:
        identity = f"{target.benchmark}/{target.model_id}/r{target.repeat_index}/{target.method.value}/{case_id}"
        raise ExperimentReuseError(f"Inconsistent cross-experiment re-entry:{identity}, fields={sorted(set(different))}")


def _copy_case_output(
    source: ExperimentRunSpec,
    target: ExperimentRunSpec,
    case_id: str,
    source_output: HarnessEvaluationOutput,
) -> None:
    _assert_source_identity(source, case_id, source_output)
    target_config = build_harness_config(target)
    target_raw_dir = case_output_dir(target_config.runs_dir, target_config, case_id, target.method.value)
    target_result_dir = case_output_dir(target_config.results_dir, target_config, case_id, target.method.value)
    shutil.copytree(source_output.raw_run_dir, target_raw_dir, dirs_exist_ok=True)
    shutil.copytree(source_output.result_dir, target_result_dir, dirs_exist_ok=True)
    reused_from = {
        "experiment_id": source.experiment_id,
        "raw_run_dir": str(source_output.raw_run_dir.resolve()),
        "result_dir": str(source_output.result_dir.resolve()),
        "trajectory_unchanged": True,
    }
    _rewrite_raw_summary(target_raw_dir / "raw_summary.json", target, reused_from)
    _rewrite_summary(target_result_dir / "summary.json", target, reused_from)
    _rewrite_summary(target_result_dir / "report.json", target, reused_from)


def _assert_source_identity(
    source: ExperimentRunSpec,
    case_id: str,
    source_output: HarnessEvaluationOutput,
) -> None:
    summary_path = source_output.result_dir / "summary.json"
    summary = read_json_file(summary_path, f"Source Case Summary:{summary_path}", dict)
    metadata = summary.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    expected = {
        "experiment_id": source.experiment_id,
        "method": source.method.value,
        "model_id": source.model_id,
        "repeat_index": source.repeat_index,
    }
    actual = {key: metadata.get(key) for key in expected}
    if actual != expected:
        raise ExperimentReuseError(f"Source Case Identity Inconsistent:{case_id}, expected={expected}, actual={actual}")


def _rewrite_raw_summary(path: Path, target: ExperimentRunSpec, reused_from: JsonObject) -> None:
    payload = read_json_file(path, f"Reuse rawsummary:{path}", dict)
    payload["experiment_id"] = target.experiment_id
    payload["method"] = target.method.value
    payload["reused_from"] = dict(reused_from)
    _write_json(path, payload)


def _rewrite_summary(path: Path, target: ExperimentRunSpec, reused_from: JsonObject) -> None:
    payload = read_json_file(path, f"Reuse assessment results:{path}", dict)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    metadata.update(
        {
            "experiment_id": target.experiment_id,
            "method": target.method.value,
            "model_id": target.model_id,
            "repeat_index": target.repeat_index,
        }
    )
    payload["reused_from"] = dict(reused_from)
    _write_json(path, payload)


def _write_json(path: Path, payload: JsonObject) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")