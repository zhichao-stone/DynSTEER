from pathlib import Path

import pytest

from dynsteer.adapter.generic import load_task_case, load_trajectory
from dynsteer.harness.model import BenchmarkCase, HarnessRunConfig, HarnessRunResult
from dynsteer.harness.runner import run_harness_case


class FakeHarness:
    benchmark = "fake"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark="fake", case_id="case-1")]

    def run_case(self, config: HarnessRunConfig, case_id: str) -> HarnessRunResult:
        task = load_task_case({"task_id": "t", "task_description": "demo"})
        trajectory = load_trajectory(
            {
                "run_id": "r",
                "task_id": "t",
                "steps": [
                    {
                        "step_id": "s0",
                        "index": 0,
                        "actor": "user",
                        "event_type": "message",
                        "content": "start",
                    }
                ],
            }
        )
        return HarnessRunResult(
            benchmark="fake",
            case_id=case_id,
            run_id="r",
            task_case=task,
            trajectory=trajectory,
            raw_output_dir=Path("raw"),
            raw_summary={"ok": True},
        )


def test_run_harness_case_writes_run_artifacts(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path / "data",
        scenario="case-1",
        output_dir=tmp_path / "runs",
        pretty=True,
    )

    output = run_harness_case(config=config, harness=FakeHarness())

    assert output.run_dir == tmp_path / "runs" / "fake" / "r" / "case-1" / "dynsteer"
    assert output.report_path.exists()
    assert output.summary_path.exists()
    assert output.raw_summary_path.exists()


def test_run_harness_case_rejects_missing_case(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path / "data",
        scenario="missing",
        output_dir=tmp_path / "runs",
    )

    with pytest.raises(KeyError, match="benchmark 场景不存在"):
        run_harness_case(config=config, harness=FakeHarness())
