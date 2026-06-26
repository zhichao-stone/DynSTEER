from __future__ import annotations

import json
from pathlib import Path

import pytest

from dynsteer.harness.config import load_harness_run_configs


def _write_config_root(path: Path, manifest: dict[str, object]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "benchmark.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (path / "run_config.json").write_text(json.dumps([{"name": "run-a"}], ensure_ascii=False), encoding="utf-8")


def test_load_harness_run_configs_reads_manifest_language(tmp_path: Path) -> None:
    _write_config_root(
        tmp_path,
        {
            "benchmark": "generic",
            "source_root": ".",
            "tool_backend": "DEFAULT",
            "language": "zh",
        },
    )

    configs = load_harness_run_configs(
        benchmark="generic",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )

    assert configs[0].metadata["language"] == "zh"


def test_load_harness_run_configs_defaults_language_to_english(tmp_path: Path) -> None:
    _write_config_root(
        tmp_path,
        {
            "benchmark": "generic",
            "source_root": ".",
            "tool_backend": "DEFAULT",
        },
    )

    configs = load_harness_run_configs(
        benchmark="generic",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )

    assert configs[0].metadata["language"] == "en"


def test_load_harness_run_configs_rejects_invalid_language(tmp_path: Path) -> None:
    _write_config_root(
        tmp_path,
        {
            "benchmark": "generic",
            "source_root": ".",
            "tool_backend": "DEFAULT",
            "language": "",
        },
    )

    with pytest.raises(ValueError, match="language"):
        load_harness_run_configs(
            benchmark="generic",
            data_root=tmp_path,
            runs_dir=tmp_path / "runs",
            results_dir=tmp_path / "results",
        )
