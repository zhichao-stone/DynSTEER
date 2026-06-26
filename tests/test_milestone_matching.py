from __future__ import annotations

import pytest

from dynsteer.config import MatchConfig
from dynsteer.evaluate.milestone import (
    find_hit_milestone,
    match_milestones,
    milestone_score_matrix,
    ready_milestones,
    stage_start_for_milestone,
    validate_milestone_graph,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Boundary,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    MilestoneGraph,
    Operator,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)


def _milestone(milestone_id: str, expected: str = "完成", required: bool = True) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description="测试 milestone",
        constraints=[
            Constraint(
                constraint_id=f"{milestone_id}-c1",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                expected=expected,
            )
        ],
        required=required,
    )


def _trajectory(content: str = "任务完成") -> Trajectory:
    step = TrajectoryStep(
        step_id="s0",
        index=0,
        actor=Actor.AGENT,
        event_type=EventType.MESSAGE,
        content=content,
    )
    return Trajectory(run_id="run-1", task_id="task-1", steps=[step])


def _settlement(milestone_id: str, end_step_index: int) -> HarnessStageSettlement:
    return HarnessStageSettlement(
        settlement_id=f"st-{milestone_id}",
        kind="milestone",
        milestone_id=milestone_id,
        start_step_index=0,
        end_step_index=end_step_index,
    )


def test_validate_milestone_graph_accepts_dag() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1"), _milestone("m2")], edges=[("m1", "m2")])

    validate_milestone_graph(graph)


def test_validate_milestone_graph_rejects_cycle() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1"), _milestone("m2")], edges=[("m1", "m2"), ("m2", "m1")])

    with pytest.raises(ValueError, match="环"):
        validate_milestone_graph(graph)


def test_validate_milestone_graph_rejects_duplicate_id() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1"), _milestone("m1")])

    with pytest.raises(ValueError, match="不能重复"):
        validate_milestone_graph(graph)


def test_validate_milestone_graph_rejects_unknown_edge() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1")], edges=[("m1", "ghost")])

    with pytest.raises(ValueError, match="不存在"):
        validate_milestone_graph(graph)


def test_match_milestones_assigns_matched_boundary() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1")])
    trajectory = _trajectory("任务完成")
    boundaries = [Boundary(boundary_id="b0", step_index=0, snapshot_id=None, reason="agent_message")]
    matrix = milestone_score_matrix(graph, boundaries, trajectory)

    mapping = match_milestones(graph, boundaries, matrix, MatchConfig())

    assert "m1" in mapping.assignments
    assert mapping.assignments["m1"].boundary_id == "b0"
    assert mapping.missing_required == []


def test_match_milestones_marks_missing_required() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1", expected="未出现的内容")])
    trajectory = _trajectory("任务完成")
    boundaries = [Boundary(boundary_id="b0", step_index=0, snapshot_id=None, reason="agent_message")]
    matrix = milestone_score_matrix(graph, boundaries, trajectory)

    mapping = match_milestones(graph, boundaries, matrix, MatchConfig())

    assert mapping.assignments == {}
    assert mapping.missing_required == ["m1"]


def test_ready_milestones_respects_predecessors() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1"), _milestone("m2")], edges=[("m1", "m2")])

    assert [node.milestone_id for node in ready_milestones(graph, {})] == ["m1"]
    matched = {"m1": _settlement("m1", end_step_index=2)}
    assert [node.milestone_id for node in ready_milestones(graph, matched)] == ["m2"]


def test_stage_start_for_milestone_uses_predecessor_end() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1"), _milestone("m2")], edges=[("m1", "m2")])
    matched = {"m1": _settlement("m1", end_step_index=4)}

    assert stage_start_for_milestone(graph, "m2", matched, None) == 4
    assert stage_start_for_milestone(graph, "m1", {}, None) == 0


def test_find_hit_milestone_returns_passing_milestone() -> None:
    graph = MilestoneGraph(nodes=[_milestone("m1")])
    task_case = TaskCase(task_id="task-1", task_description="测试任务", milestone_graph=graph)
    trajectory = _trajectory("任务完成")

    hit = find_hit_milestone(task_case, trajectory, trajectory.steps[0], {})

    assert hit is not None
    milestone, boundary, score = hit
    assert milestone.milestone_id == "m1"
    assert score.status == StageStatus.PASS


def test_find_hit_milestone_returns_none_without_graph() -> None:
    task_case = TaskCase(task_id="task-1", task_description="测试任务")
    trajectory = _trajectory("任务完成")

    assert find_hit_milestone(task_case, trajectory, trajectory.steps[0], {}) is None