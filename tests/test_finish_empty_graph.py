from dynsteer.evaluate.final import build_finish_verification
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.model import Dimension, MilestoneGraph, RuntimeEvaluationState, StageStatus, TaskCase, Trajectory


def test_empty_graph_without_minefield_is_invalid() -> None:
    """覆盖空 milestone graph 不能自然得到 finish pass。"""
    verification = build_finish_verification(_task_case(), Trajectory("run", "task", []), _state(), GeneralScorer())

    assert verification["status"] == StageStatus.INVALID.value
    assert verification["score"] == 0.0
    assert verification["empty_milestone_graph"] is True


def test_empty_graph_with_fatal_minefield_is_fail() -> None:
    """覆盖空 milestone graph 触发 fatal minefield 时仍判失败。"""
    state = _state()
    state.fatal_minefield = True
    state.max_minefield_score = 1.0

    verification = build_finish_verification(_task_case(), Trajectory("run", "task", []), state, GeneralScorer())

    assert verification["status"] == StageStatus.FAIL.value
    assert verification["score"] == 0.0
    assert verification["fatal_minefield"] is True


def _task_case() -> TaskCase:
    return TaskCase(
        task_id="task",
        task_description="desc",
        case_id="case",
        milestone_graph=MilestoneGraph(nodes=[], edges=[]),
    )


def _state() -> RuntimeEvaluationState:
    return RuntimeEvaluationState(weights={dimension: 1.0 for dimension in Dimension}, settlements=[])
