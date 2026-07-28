import json
from pathlib import Path

import dynsteer.experiment.runner as runner
from dynsteer.experiment.model import ExperimentMethod, ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import HarnessEvaluationOutput, MilestoneGraph, TaskCase, Trajectory


def test_run_replay_case_injects_default_runtime_initial_state(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    """Replay 优先使用 default trajectory 携带的 runtime initial state。"""
    runtime_initial_state = {
        "namespaces": {
            "REMINDER": [{"reminder_id": "r1"}],
            "MESSAGING": [],
        }
    }
    trajectory_path = tmp_path / "trajectory.json"
    trajectory_path.write_text(
        json.dumps(
            {
                "run_id": "default_run",
                "task_id": "task",
                "steps": [],
                "snapshots": [],
                "final_state": {},
                "metrics": {},
                "runtime_initial_state": runtime_initial_state,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    default_output = HarnessEvaluationOutput(
        run_dir=tmp_path,
        raw_run_dir=tmp_path,
        result_dir=tmp_path,
        report_path=tmp_path / "default_report.json",
        summary_path=tmp_path / "summary.json",
        raw_summary_path=tmp_path / "raw_summary.json",
        trajectory_path=trajectory_path,
    )
    task_case = TaskCase(
        task_id="task",
        task_description="do task",
        case_id="case_a",
        milestone_graph=MilestoneGraph(),
        initial_state={"namespaces": {"REMINDER": []}},
    )
    captured: dict[str, object] = {}

    class FakeEvaluator:
        pass

    def fake_write_replay_case_outputs(
        config: HarnessRunConfig,
        evaluator: object,
        task_case: TaskCase,
        trajectory: Trajectory,
        harness: object,
        default_reference: dict[str, object] | None = None,
    ) -> HarnessEvaluationOutput:
        captured["config"] = config
        captured["task_case"] = task_case
        captured["trajectory"] = trajectory
        captured["default_reference"] = default_reference
        return default_output

    monkeypatch.setattr(runner, "get_harness", lambda benchmark: object())
    monkeypatch.setattr(runner.DynSTEEREvaluator, "from_config", lambda config, strategy=None: FakeEvaluator())
    monkeypatch.setattr(runner, "write_replay_case_outputs", fake_write_replay_case_outputs)

    output = runner.run_replay_case(_spec(tmp_path), task_case, default_output, {"score": 1.0})

    assert output == default_output
    assert captured["task_case"] is task_case
    assert task_case.initial_state == runtime_initial_state
    assert task_case.metadata["runtime_initial_state_source"] == "default_trajectory"
    assert task_case.metadata["runtime_initial_state_summary"] == {
        "namespace_count": 2,
        "row_counts": {"MESSAGING": 0, "REMINDER": 1},
    }


def _spec(tmp_path: Path) -> ExperimentRunSpec:
    return ExperimentRunSpec(
        experiment_id="exp",
        run_id="run_1",
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        case_ids=("case_a",),
        model_id="model_a",
        repeat_index=0,
        method=ExperimentMethod.DYNSTEER_REPLAY,
    )
