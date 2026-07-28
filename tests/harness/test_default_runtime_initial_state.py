import json
from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.harness.outputs import write_default_case_outputs
from dynsteer.model import MilestoneGraph, TaskCase


class FakeDefaultHarness(BaseBenchmarkHarness):
    benchmark = "fake"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case_a")]

    def prepare_config(self, config: HarnessRunConfig) -> None:
        return None

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> dict[str, object]:
        return {"case_id": case_id}

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)

    def case_finished(self, session: object) -> bool:
        return True

    def initial_state_from_session(self, session: object) -> dict[str, object]:
        return {"namespaces": {"REMINDER": [{"reminder_id": "r1", "content": "old"}]}}

    def final_state_from_session(self, session: object) -> dict[str, object]:
        return {"namespaces": {"REMINDER": [{"reminder_id": "r1", "content": "old"}]}}

    def raw_summary_from_session(self, session: object) -> dict[str, object]:
        return {"native": True}

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        return BenchmarkDefaultResult(score=1.0, resolved=True)


def test_default_output_records_runtime_initial_state(tmp_path: Path) -> None:
    """Default 轨迹写入本次 session 的真实初始状态。"""
    config = HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path / "data",
        case_ids=("case_a",),
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        metadata={"method": "default", "run_id": "run_0"},
    )
    task_case = TaskCase(
        task_id="task",
        task_description="do task",
        case_id="case_a",
        milestone_graph=MilestoneGraph(),
    )

    output = write_default_case_outputs(config, FakeDefaultHarness(), task_case)

    trajectory = json.loads(output.trajectory_path.read_text(encoding="utf-8"))
    raw_summary = json.loads(output.raw_summary_path.read_text(encoding="utf-8"))
    expected_state = {"namespaces": {"REMINDER": [{"reminder_id": "r1", "content": "old"}]}}
    assert trajectory["runtime_initial_state"] == expected_state
    assert raw_summary["runtime_initial_state_source"] == "harness_session"
    assert raw_summary["runtime_initial_state_summary"] == {
        "namespace_count": 1,
        "row_counts": {"REMINDER": 1},
    }
    assert task_case.initial_state == expected_state
    assert task_case.metadata["runtime_initial_state_source"] == "harness_session"
