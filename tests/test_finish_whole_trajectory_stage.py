from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.evaluate.settlement import finish_settlement
from dynsteer.model import Dimension, StageStatus, ThresholdConfig

from tests.support import FakeStandardJudge, make_empty_task_case, make_runtime_state, make_trajectory


def test_empty_graph_finish_uses_fake_standard_judge_pass() -> None:
    task_case = make_empty_task_case()
    trajectory = make_trajectory(task_id=task_case.task_id)
    state = make_runtime_state()
    judge = FakeStandardJudge(StageStatus.PASS, 0.9)

    settlement, stage, _ = finish_settlement(
        state.settlements,
        task_case,
        trajectory,
        GeneralScorer(),
        state,
        standard_judge=judge,
        thresholds=ThresholdConfig(),
    )

    evaluation = stage.metadata["finish_stage_evaluation"]
    assert stage.status == StageStatus.PASS
    assert settlement.status == StageStatus.PASS.value
    assert evaluation["coverage_basis"] == "whole_trajectory"
    assert evaluation["default_reference_used"] is False
    assert evaluation["judge_level"] == "standard"
    assert set(evaluation["focus_dimensions"]) == {dimension.value for dimension in Dimension}
    assert len(judge.calls) == 1


def test_empty_graph_finish_uses_fake_standard_judge_fail() -> None:
    task_case = make_empty_task_case()
    state = make_runtime_state()
    judge = FakeStandardJudge(StageStatus.FAIL, 0.95)

    _, stage, _ = finish_settlement(
        state.settlements,
        task_case,
        make_trajectory(task_id=task_case.task_id),
        GeneralScorer(),
        state,
        standard_judge=judge,
        thresholds=ThresholdConfig(),
    )

    evaluation = stage.metadata["finish_stage_evaluation"]
    assert stage.status == StageStatus.FAIL
    assert stage.stage_score > 0.8
    assert evaluation["judge_status"] == StageStatus.FAIL.value
    assert evaluation["default_reference_used"] is False


def test_empty_graph_finish_without_judge_is_invalid() -> None:
    task_case = make_empty_task_case()
    state = make_runtime_state()

    _, stage, _ = finish_settlement(
        state.settlements,
        task_case,
        make_trajectory(task_id=task_case.task_id),
        GeneralScorer(),
        state,
        standard_judge=None,
        thresholds=ThresholdConfig(),
    )

    evaluation = stage.metadata["finish_stage_evaluation"]
    assert stage.status == StageStatus.INVALID
    assert stage.stage_score == 0.0
    assert evaluation["whole_trajectory_evaluator_unavailable"] is True
    assert stage.hard_constraints_all_pass is False


def test_empty_graph_fatal_minefield_overrides_fake_standard_judge_pass() -> None:
    task_case = make_empty_task_case()
    state = make_runtime_state(
        fatal_minefield=True,
        minefield_matches=[{"minefield_id": "mf0", "score": 1.0}],
    )
    judge = FakeStandardJudge(StageStatus.PASS, 1.0)

    _, stage, _ = finish_settlement(
        state.settlements,
        task_case,
        make_trajectory(task_id=task_case.task_id),
        GeneralScorer(),
        state,
        standard_judge=judge,
        thresholds=ThresholdConfig(),
    )

    assert stage.status == StageStatus.FAIL
    assert stage.stage_score == 0.0
    assert len(judge.calls) == 0
