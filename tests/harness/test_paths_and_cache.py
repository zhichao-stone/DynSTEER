from __future__ import annotations

import json
from pathlib import Path

from dynsteer.harness.config import load_harness_run_configs
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.harness.outputs import case_output_dir, existing_case_output, write_method_level_summaries
from dynsteer.model import HarnessEvaluationOutput


def _config(tmp_path: Path, method: str = "default") -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"method": method},
    )


def _create_case_cache(tmp_path: Path, case_id: str, method: str, report_name: str, summary: dict[str, object]) -> HarnessEvaluationOutput:
    raw_case_dir = tmp_path / "runs" / "toolsandbox" / method / case_id
    result_dir = tmp_path / "results" / "toolsandbox" / method / case_id
    raw_case_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    (raw_case_dir / "raw_summary.json").write_text("{}", encoding="utf-8")
    (raw_case_dir / "trajectory.json").write_text(
        json.dumps({"task_id": "task-1", "steps": [], "snapshots": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (result_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    (result_dir / report_name).write_text("{}", encoding="utf-8")
    return HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_case_dir,
        result_dir=result_dir,
        report_path=result_dir / report_name,
        summary_path=result_dir / "summary.json",
        raw_summary_path=raw_case_dir / "raw_summary.json",
        trajectory_path=raw_case_dir / "trajectory.json",
    )


def test_case_output_dir_and_existing_case_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert case_output_dir(tmp_path / "runs", config, "case_a", "default") == tmp_path / "runs" / "toolsandbox" / "default" / "case_a"

    summary = {
        "task_id": "task-1",
        "method": "default",
        "milestone_coverage": "pass",
        "overall_score": 1.0,
        "runtime_metrics": {"step_count": 3},
        "metadata": {"method": "default", "model_id": "model-a", "repeat_index": 0},
    }
    _create_case_cache(tmp_path, "case_a", "default", "default_report.json", summary)

    cached = existing_case_output(config, "case_a", "default", "default_report.json")
    assert cached is not None
    assert cached.raw_run_dir == tmp_path / "runs" / "toolsandbox" / "default" / "case_a"
    assert cached.result_dir == tmp_path / "results" / "toolsandbox" / "default" / "case_a"

    assert existing_case_output(config, "case_a", "default", "report.json") is None
    (tmp_path / "results" / "toolsandbox" / "default" / "case_a" / "report.json").write_text("{}", encoding="utf-8")
    assert existing_case_output(config, "case_a", "default", "report.json") is not None

    (tmp_path / "results" / "toolsandbox" / "default" / "case_a" / "summary.json").unlink()
    assert existing_case_output(config, "case_a", "default", "report.json") is None


def test_write_method_level_summaries_creates_method_summary(tmp_path: Path) -> None:
    summary_a = {
        "task_id": "task-1",
        "method": "default",
        "milestone_coverage": "pass",
        "overall_score": 0.25,
        "runtime_metrics": {
            "step_count": 2,
            "llm_total_tokens": 5,
            "trajectory_total_tokens": 7,
            "elapsed_seconds": 1.5,
        },
        "metadata": {"method": "default", "model_id": "model-a", "repeat_index": 0},
    }
    summary_b = {
        "task_id": "task-2",
        "method": "default",
        "milestone_coverage": "warn",
        "overall_score": 0.75,
        "runtime_metrics": {
            "step_count": 4,
            "llm_total_tokens": 9,
            "trajectory_total_tokens": 11,
            "elapsed_seconds": 2.5,
        },
        "metadata": {"method": "default", "model_id": "model-a", "repeat_index": 0},
    }
    output_a = _create_case_cache(tmp_path, "case_a", "default", "default_report.json", summary_a)
    output_b = _create_case_cache(tmp_path, "case_b", "default", "default_report.json", summary_b)

    write_method_level_summaries([output_a, output_b])
    method_summary_path = tmp_path / "results" / "toolsandbox" / "default" / "summary.json"
    payload = json.loads(method_summary_path.read_text(encoding="utf-8"))

    assert payload["benchmark"] == "toolsandbox"
    assert payload["method"] == "default"
    assert payload["case_count"] == 2
    assert payload["average_overall_score"] == 0.5
    assert "run_id" not in payload
    assert all("run_id" not in case for case in payload["cases"])
    assert [case["case_id"] for case in payload["cases"]] == ["case_a", "case_b"]


def test_load_harness_run_configs_preserves_run_id_in_metadata(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "benchmark.json").write_text(
        json.dumps(
            {
                "benchmark": "demo",
                "source_root": "../demo",
                "tool_backend": "DEFAULT",
                "max_workers": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_root / "run_configs.json").write_text(
        json.dumps(
            [
                {
                    "name": "legacy-name",
                    "run_id": "legacy-run",
                    "scenarios": ["case_a"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    configs = load_harness_run_configs("demo", data_root, tmp_path / "runs", tmp_path / "results")

    assert len(configs) == 1
    assert configs[0].metadata["run_id"] == "legacy-run"
    assert configs[0].metadata["run_config_name"] == "legacy-name"
