from dynsteer.evaluate.evaluator import DynSTEEREvaluator
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.model import StageStatus

from tests.support import FakeStandardJudge, make_empty_task_case, make_harness_config, make_trajectory


def test_default_reference_failure_does_not_prevent_replay_pass(tmp_path) -> None:
    task_case = make_empty_task_case()
    trajectory = make_trajectory(task_id=task_case.task_id)
    evaluator = DynSTEEREvaluator(standard_judge=FakeStandardJudge(StageStatus.PASS, 0.9))
    config = make_harness_config(tmp_path, default_reference={"resolved": False, "score": 0.0})

    result = evaluator.evaluate_replay(task_case, trajectory, GeneralScorer(), config)
    report = result.evaluation_report
    finish_stage = report.stage_reports[-1]

    assert report.milestone_coverage == "full"
    assert finish_stage.status == StageStatus.PASS
    assert report.metadata["default_reference"]["resolved"] is False
    assert finish_stage.metadata["finish_stage_evaluation"]["default_reference_used"] is False


def test_default_reference_success_does_not_mask_replay_fail(tmp_path) -> None:
    task_case = make_empty_task_case()
    trajectory = make_trajectory(task_id=task_case.task_id)
    evaluator = DynSTEEREvaluator(standard_judge=FakeStandardJudge(StageStatus.FAIL, 0.1))
    config = make_harness_config(tmp_path, default_reference={"resolved": True, "score": 1.0})

    result = evaluator.evaluate_replay(task_case, trajectory, GeneralScorer(), config)
    report = result.evaluation_report
    finish_stage = report.stage_reports[-1]

    assert report.milestone_coverage == "none"
    assert finish_stage.status == StageStatus.FAIL
    assert report.metadata["default_reference"]["resolved"] is True
    assert finish_stage.metadata["finish_stage_evaluation"]["default_reference_used"] is False
