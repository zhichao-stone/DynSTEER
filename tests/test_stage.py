from dynsteer.model import (
    Actor,
    EventType,
    Milestone,
    MilestoneGraph,
    MilestoneMapping,
    MilestoneMappingItem,
    MilestoneScore,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import build_stage_intervals


def _trajectory() -> Trajectory:
    return Trajectory(
        run_id="r",
        task_id="t",
        steps=[
            TrajectoryStep("s0", 0, Actor.SYSTEM, EventType.MESSAGE),
            TrajectoryStep("s1", 1, Actor.USER, EventType.MESSAGE),
            TrajectoryStep("s2", 2, Actor.AGENT, EventType.MESSAGE),
            TrajectoryStep("s3", 3, Actor.AGENT, EventType.FINAL),
        ],
    )


def test_root_stage_starts_from_first_user_step() -> None:
    graph = MilestoneGraph(nodes=[Milestone("a", "a", "a", [])])
    mapping = MilestoneMapping(
        assignments={
            "a": MilestoneMappingItem(
                milestone_id="a",
                boundary_id="b2",
                boundary_step_index=2,
                score=MilestoneScore("a", "b2", 0.9, StageStatus.PASS),
            )
        }
    )

    intervals = build_stage_intervals(graph, mapping, _trajectory())

    assert intervals[0].start_step_index == 1
    assert intervals[0].end_step_index == 2


def test_join_stage_starts_after_latest_predecessor() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("a", "a", "a", []),
            Milestone("b", "b", "b", []),
            Milestone("c", "c", "c", []),
        ],
        edges=[("a", "c"), ("b", "c")],
    )
    mapping = MilestoneMapping(
        assignments={
            "a": MilestoneMappingItem("a", "ba", 1, MilestoneScore("a", "ba", 0.9, StageStatus.PASS)),
            "b": MilestoneMappingItem("b", "bb", 2, MilestoneScore("b", "bb", 0.9, StageStatus.PASS)),
            "c": MilestoneMappingItem("c", "bc", 3, MilestoneScore("c", "bc", 0.9, StageStatus.PASS)),
        }
    )

    intervals = build_stage_intervals(graph, mapping, _trajectory())
    interval = next(item for item in intervals if item.milestone_id == "c")

    assert interval.start_step_index == 2
    assert interval.end_step_index == 3


def test_missing_milestone_generates_missing_stage() -> None:
    graph = MilestoneGraph(nodes=[Milestone("a", "a", "a", [])])
    mapping = MilestoneMapping(missing_required=["a"])

    intervals = build_stage_intervals(graph, mapping, _trajectory())

    assert intervals[0].status == StageStatus.MISSING

