from dynsteer.evaluate.final import build_finish_verification
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.model import StageStatus

from tests.support import make_empty_task_case, make_minefield, make_nonempty_task_case, make_runtime_state, make_trajectory


def test_empty_graph_with_fatal_minefield_fails_without_whole_trajectory_judge() -> None:
    task_case = make_empty_task_case(minefields=[make_minefield()])
    state = make_runtime_state(
        fatal_minefield=True,
        minefield_matches=[{"minefield_id": "mf0", "score": 1.0}],
    )

    verification = build_finish_verification(task_case, make_trajectory(), state, GeneralScorer())

    assert verification["status"] == StageStatus.FAIL.value
    assert verification["score"] == 0.0
    assert verification["whole_trajectory_evaluation_required"] is False
    assert verification["default_reference_used"] is False


def test_empty_graph_without_fatal_minefield_requires_whole_trajectory_judge() -> None:
    task_case = make_empty_task_case(minefields=[make_minefield()])
    state = make_runtime_state()

    verification = build_finish_verification(task_case, make_trajectory(), state, GeneralScorer())

    assert verification["status"] == StageStatus.AMBIGUOUS.value
    assert verification["score"] == 0.0
    assert verification["empty_milestone_graph"] is True
    assert verification["fixed_milestones_applicable"] is False
    assert verification["whole_trajectory_evaluation_required"] is True
    assert verification["default_reference_used"] is False
    assert verification["minefield_count"] == 1


def test_nonempty_graph_still_uses_milestone_finish_verification() -> None:
    task_case = make_nonempty_task_case()
    state = make_runtime_state()

    verification = build_finish_verification(task_case, make_trajectory(), state, GeneralScorer())

    assert verification["status"] == StageStatus.FAIL.value
    assert verification["unmatched_milestone_ids"] == ["m1"]
    assert "whole_trajectory_evaluation_required" not in verification
