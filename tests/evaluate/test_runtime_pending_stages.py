from dynsteer.evaluate.models import RuntimeEvaluationState
from dynsteer.evaluate.runtime import pending_required_stage_results
from dynsteer.evaluate.utils import first_failure_stage_id
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    Milestone,
    MilestoneGraph,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    TrajectoryEvaluationReport,
)


def _state(match_attempts: list[dict]) -> RuntimeEvaluationState:
    return RuntimeEvaluationState(
        weights={dimension: 1.0 for dimension in Dimension},
        settlements=[],
        matched_settlements={},
        stage_reports=[],
        match_attempts=match_attempts,
    )


def test_pending_attempted_milestone_uses_fail_stage_id() -> None:
    task_case = TaskCase(
        task_id="task",
        task_description="desc",
        case_id="case",
        milestone_graph=MilestoneGraph(nodes=[Milestone("m0", "m0", "m0", [])]),
    )
    state = _state(
        [
            {
                "ready_before": ["m0"],
                "candidate_scores": [
                    {
                        "milestone_id": "m0",
                        "boundary": {"step_index": 16},
                        "score": {"score": 0.0, "status": "fail"},
                        "reject_reason": "status_not_pass",
                    }
                ],
            }
        ]
    )

    results = pending_required_stage_results(task_case, state)

    assert results[0].status == StageStatus.FAIL
    assert results[0].stage_id == "runtime:fail:m0"
    assert results[0].metadata["synthetic_pending_required"] is True
    assert results[0].metadata["failure_kind"] == "fail"
    assert first_failure_stage_id(results) == "runtime:fail:m0"


def test_pending_predecessor_gap_stays_missing_stage_id() -> None:
    task_case = TaskCase(
        task_id="task",
        task_description="desc",
        case_id="case",
        milestone_graph=MilestoneGraph(
            nodes=[
                Milestone("m0", "m0", "m0", []),
                Milestone("m1", "m1", "m1", [], dependency_predecessor_ids=["m0"]),
            ]
        ),
    )
    state = _state([])

    results = pending_required_stage_results(task_case, state)
    by_milestone = {result.milestone_id: result for result in results}

    assert by_milestone["m1"].status == StageStatus.MISSING
    assert by_milestone["m1"].stage_id == "runtime:missing:m1"
    assert by_milestone["m1"].metadata["failure_kind"] == "missing"
    assert by_milestone["m1"].metadata["blocker"] == "predecessor_not_matched"


def test_summary_stage_count_excludes_synthetic_pending_required_stage() -> None:
    report = TrajectoryEvaluationReport(
        run_id="run",
        task_id="task",
        milestone_coverage="partial",
        overall_score=0.5,
        stage_reports=[
            StageEvaluationResult(
                stage_id="runtime:st1",
                milestone_id="m0",
                evaluator_level=EvaluationLevel.CHEAP,
                status=StageStatus.PASS,
                stage_score=1.0,
                uncertainty=0.0,
                dimension_scores={dimension: 1.0 for dimension in Dimension},
            ),
            StageEvaluationResult(
                stage_id="runtime:fail:m1",
                milestone_id="m1",
                evaluator_level=EvaluationLevel.CHEAP,
                status=StageStatus.FAIL,
                stage_score=0.0,
                uncertainty=0.0,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
                metadata={"synthetic_pending_required": True},
            ),
        ],
    )

    assert report.to_summary_dict()["stage_count"] == 1
