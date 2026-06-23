from pathlib import Path

import pytest

from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult


def test_harness_run_config_requires_benchmark() -> None:
    with pytest.raises(ValueError, match="benchmark 不能为空"):
        HarnessRunConfig(benchmark="", data_root=Path("data/toolsandbox"))


def test_harness_run_config_requires_output_dir() -> None:
    with pytest.raises(ValueError, match="output_dir 不能为空"):
        HarnessRunConfig(benchmark="toolsandbox", data_root=Path("data/toolsandbox"), output_dir=None)  # type: ignore[arg-type]


def test_benchmark_case_to_dict() -> None:
    case = BenchmarkCase(
        benchmark="toolsandbox",
        case_id="cellular_off",
        categories=["SINGLE_TOOL_CALL"],
        metadata={"source": "unit"},
    )

    assert case.to_dict() == {
        "benchmark": "toolsandbox",
        "case_id": "cellular_off",
        "categories": ["SINGLE_TOOL_CALL"],
        "metadata": {"source": "unit"},
    }


def test_harness_run_result_requires_task_and_trajectory() -> None:
    with pytest.raises(ValueError, match="task_case 和 trajectory 不能为空"):
        HarnessRunResult(
            benchmark="toolsandbox",
            case_id="cellular_off",
            run_id="run-1",
            task_case=None,
            trajectory=None,
            raw_output_dir=Path("raw"),
            raw_summary={},
        )
