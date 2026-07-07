from __future__ import annotations

from dynsteer.graph import START_NODE_ID, enrich_milestone_graph
from dynsteer.model import (
    Actor,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneMapping,
    MilestoneMappingItem,
    MilestoneScore,
    StageInterval,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import build_stage_intervals, stage_start_step_index, stage_trajectory_steps


class CountingSteps(list[TrajectoryStep]):
    def __init__(self, values: list[TrajectoryStep]) -> None:
        super().__init__(values)
        self.iteration_count = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.iteration_count += 1
        return super().__iter__()


def _trajectory(indexes: list[int]) -> Trajectory:
    return Trajectory(
        run_id="run",
        task_id="task",
        steps=[
            TrajectoryStep(
                step_id=f"s{index}",
                index=index,
                actor=Actor.AGENT,
                event_type=EventType.MESSAGE,
                content=f"step {index}",
            )
            for index in indexes
        ],
    )


def _score(milestone_id: str, boundary_step_index: int) -> MilestoneScore:
    return MilestoneScore(
        milestone_id=milestone_id,
        boundary_id=f"b{boundary_step_index}",
        score=1.0,
        status=StageStatus.PASS,
        evidence=[f"matched {milestone_id}"],
    )


def test_stage_trajectory_steps_uses_left_open_right_closed_interval() -> None:
    trajectory = _trajectory([0, 1, 2, 3])
    interval = StageInterval(
        stage_id="stage:m1",
        milestone_id="m1",
        stage_anchor_milestone_id="m0",
        start_boundary_step_index=1,
        start_step_index=2,
        end_step_index=3,
        status=StageStatus.PASS,
    )

    assert [step.index for step in stage_trajectory_steps(interval, trajectory)] == [2, 3]


def test_root_stage_includes_step_zero_after_start_anchor_boundary() -> None:
    trajectory = _trajectory([0, 1, 2])
    boundary = trajectory.first_step_index - 1
    interval = StageInterval(
        stage_id="stage:m0",
        milestone_id="m0",
        stage_anchor_milestone_id=START_NODE_ID,
        start_boundary_step_index=boundary,
        start_step_index=stage_start_step_index(trajectory.successor_by_boundary, boundary, 1),
        end_step_index=1,
        status=StageStatus.PASS,
    )

    assert boundary == -1
    assert interval.start_step_index == 0
    assert [step.index for step in stage_trajectory_steps(interval, trajectory)] == [0, 1]


def test_build_stage_intervals_uses_anchor_boundary_for_join_milestone() -> None:
    graph = enrich_milestone_graph(
        MilestoneGraph(
            nodes=[
                Milestone("m1", "m1", "branch root", []),
                Milestone("m3", "m3", "left branch", []),
                Milestone("m4", "m4", "right branch", []),
                Milestone("m5", "m5", "join", []),
            ],
            edges=[
                ("m1", "m3"),
                ("m1", "m4"),
                ("m3", "m5"),
                ("m4", "m5"),
            ],
        )
    )
    mapping = MilestoneMapping(
        assignments={
            "m1": MilestoneMappingItem("m1", "b1", 1, _score("m1", 1)),
            "m3": MilestoneMappingItem("m3", "b3", 3, _score("m3", 3)),
            "m4": MilestoneMappingItem("m4", "b7", 7, _score("m4", 7)),
            "m5": MilestoneMappingItem("m5", "b8", 8, _score("m5", 8)),
        }
    )

    intervals = build_stage_intervals(graph, mapping, _trajectory(list(range(10))))

    by_milestone = {interval.milestone_id: interval for interval in intervals}
    assert by_milestone["m5"].stage_anchor_milestone_id == "m1"
    assert by_milestone["m5"].start_boundary_step_index == 1
    assert by_milestone["m5"].start_step_index == 2


def test_stage_start_step_index_uses_successor_context() -> None:
    successor_by_boundary = {9: 10, 10: 12, 12: 20}

    assert stage_start_step_index(successor_by_boundary, 10, 20) == 12
    assert stage_start_step_index(successor_by_boundary, 20, 30) == 30


def test_build_stage_intervals_uses_cached_trajectory_step_index_context() -> None:
    milestones = [
        Milestone(f"m{index}", f"m{index}", f"milestone {index}", [])
        for index in range(30)
    ]
    graph = enrich_milestone_graph(
        MilestoneGraph(
            nodes=milestones,
            edges=[(f"m{index}", f"m{index + 1}") for index in range(29)],
        )
    )
    assignments = {
        f"m{index}": MilestoneMappingItem(
            milestone_id=f"m{index}",
            boundary_id=f"b{index}",
            boundary_step_index=index,
            score=_score(f"m{index}", index),
        )
        for index in range(30)
    }
    steps = CountingSteps(
        [
            TrajectoryStep(
                step_id=f"s{index}",
                index=index,
                actor=Actor.AGENT,
                event_type=EventType.MESSAGE,
                content=f"step {index}",
            )
            for index in range(30)
        ]
    )
    trajectory = Trajectory(run_id="run", task_id="task", steps=steps)
    steps.iteration_count = 0

    intervals = build_stage_intervals(graph, MilestoneMapping(assignments=assignments), trajectory)

    assert len(intervals) == 30
    assert steps.iteration_count == 0


def test_build_stage_intervals_uses_next_real_step_for_sparse_indexes() -> None:
    graph = enrich_milestone_graph(
        MilestoneGraph(
            nodes=[
                Milestone("m0", "m0", "root", []),
                Milestone("m1", "m1", "next", []),
            ],
            edges=[("m0", "m1")],
        )
    )
    mapping = MilestoneMapping(
        assignments={
            "m0": MilestoneMappingItem("m0", "b10", 10, _score("m0", 10)),
            "m1": MilestoneMappingItem("m1", "b20", 20, _score("m1", 20)),
        }
    )
    trajectory = _trajectory([10, 12, 20])

    intervals = build_stage_intervals(graph, mapping, trajectory)

    by_milestone = {interval.milestone_id: interval for interval in intervals}
    assert by_milestone["m0"].start_boundary_step_index == 9
    assert by_milestone["m0"].start_step_index == 10
    assert by_milestone["m1"].start_boundary_step_index == 10
    assert by_milestone["m1"].start_step_index == 12
