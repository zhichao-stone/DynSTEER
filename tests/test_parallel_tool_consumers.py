import json
from pathlib import Path

import dynsteer.evaluate.evaluator as evaluator_module
import pytest
from dynsteer.adapter.base import BenchmarkDefaultResult
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.harness.outputs import write_default_case_outputs
from dynsteer.model import (
    Actor,
    AgentStepClosure,
    EventType,
    MilestoneGraph,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def _parallel_steps() -> list[TrajectoryStep]:
    routes = [
        (Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, "A"),
        (Actor.AGENT, Actor.ENVIRONMENT, EventType.TOOL_CALL, "B"),
        (Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, "A"),
        (Actor.ENVIRONMENT, Actor.AGENT, EventType.TOOL_RESULT, "B"),
    ]
    return [
        TrajectoryStep(
            step_id=f"s{index}",
            index=index,
            actor=actor,
            recipient=recipient,
            event_type=event_type,
            raw={"openai_tool_call_id": call_id},
        )
        for index, (actor, recipient, event_type, call_id) in enumerate(routes, start=1)
    ]


class _FakeParallelHarness:
    benchmark = "fake"

    def prepare_config(self, config: HarnessRunConfig) -> None:
        if config is None:
            raise ValueError("config 不能为空")

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return [BenchmarkCase(benchmark=self.benchmark, case_id="case")]

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> dict[str, int]:
        return {"batch": 0}

    def timed_advance_case(self, session: dict[str, int]) -> HarnessAdvanceResult:
        steps = _parallel_steps()
        batch = session["batch"]
        session["batch"] += 1
        if batch == 0:
            return HarnessAdvanceResult(steps=steps[:2], snapshots=[], continue_running=True, execution_latency_ms=2)
        return HarnessAdvanceResult(steps=steps[2:], snapshots=[], continue_running=False, execution_latency_ms=2)

    def case_finished(self, session: dict[str, int]) -> bool:
        return session["batch"] >= 2

    def constraint_scorer(self) -> GeneralScorer:
        return GeneralScorer()

    def metrics_from_session(self, session: dict[str, int]) -> dict[str, object]:
        return {}

    def initial_state_from_session(self, session: dict[str, int]) -> None:
        return None

    def final_state_from_session(self, session: dict[str, int]) -> None:
        return None

    def raw_summary_from_session(self, session: dict[str, int]) -> dict[str, object]:
        return {}

    def default_result_from_session(self, session: dict[str, int]) -> BenchmarkDefaultResult:
        return BenchmarkDefaultResult(score=1.0, resolved=True)

    def teardown_case(self, session: dict[str, int]) -> None:
        return None


class _ProgressRecorder:
    def __init__(self) -> None:
        self.count = 0

    def case_advanced(self, case_id: str, step_count: int) -> None:
        self.count += step_count


class _RecordingEvaluator(DynSTEEREvaluator):
    def __init__(self) -> None:
        super().__init__()
        self.closed_step_ids: list[tuple[str, str]] = []

    def _evaluate_closed_agent_step(self, **kwargs: object) -> object:
        closure = kwargs["closure"]
        if not isinstance(closure, AgentStepClosure):
            raise TypeError("closure 必须是 AgentStepClosure")
        self.closed_step_ids.append((closure.steps[0].step_id, closure.steps[-1].step_id))
        return super()._evaluate_closed_agent_step(**kwargs)


def _task_case() -> TaskCase:
    return TaskCase(
        task_id="task",
        task_description="parallel tool contract",
        case_id="case",
        milestone_graph=MilestoneGraph(metadata={"empty_graph_completion_basis": "minefield_only"}),
    )


def _config(tmp_path: Path, method: str) -> HarnessRunConfig:
    return HarnessRunConfig(
        benchmark="fake",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
        stop_on_ready_frontier_no_progress=False,
        metadata={"method": method},
    )


def test_default_counts_both_parallel_tool_closures(tmp_path: Path) -> None:
    progress = _ProgressRecorder()

    output = write_default_case_outputs(
        _config(tmp_path, "default"),
        _FakeParallelHarness(),
        _task_case(),
        progress_reporter=progress,
        force_eval=True,
    )

    raw_summary = json.loads(output.raw_summary_path.read_text(encoding="utf-8"))
    assert raw_summary["runtime_metrics"]["step_count"] == 2
    assert raw_summary["runtime_metrics"]["raw_step_count"] == 4
    assert progress.count == 2


def test_online_evaluation_scores_each_closure_and_checks_every_raw_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _RecordingEvaluator()
    raw_step_ids: list[str] = []
    original = evaluator_module.evaluate_step_minefields

    def recording_minefield_check(**kwargs: object) -> object:
        step = kwargs["step"]
        if not isinstance(step, TrajectoryStep):
            raise TypeError("step 必须是 TrajectoryStep")
        raw_step_ids.append(step.step_id)
        return original(**kwargs)

    monkeypatch.setattr(evaluator_module, "evaluate_step_minefields", recording_minefield_check)

    result = evaluator.evaluate(_FakeParallelHarness(), _config(tmp_path, "dynsteer_evaluate"), _task_case())

    assert evaluator.closed_step_ids == [("s1", "s3"), ("s2", "s4")]
    assert raw_step_ids == ["s1", "s2", "s3", "s4"]
    assert result.evaluation_report is not None
    assert result.evaluation_report.runtime_metrics["step_count"] == 2


def test_replay_counts_parallel_outbounds_as_independent_steps(tmp_path: Path) -> None:
    evaluator = _RecordingEvaluator()
    trajectory = Trajectory(task_id="task", steps=_parallel_steps())

    result = evaluator.evaluate_replay(
        _task_case(),
        trajectory,
        GeneralScorer(),
        _config(tmp_path, "dynsteer_replay"),
    )

    assert evaluator.closed_step_ids == [("s1", "s3"), ("s2", "s4")]
    assert result.evaluation_report is not None
    assert result.evaluation_report.runtime_metrics["step_count"] == 2
