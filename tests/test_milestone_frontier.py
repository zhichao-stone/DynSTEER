from __future__ import annotations

from dynsteer.evaluate.matching.frontier import (
    advance_milestone_frontier,
    blocked_candidate_milestones,
    initialize_milestone_frontier,
    ready_milestones,
    required_ready_milestone_ids,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import Milestone, MilestoneGraph


def test_frontier_advance_matches_full_scan_logic_after_first_match() -> None:
    graph = MilestoneGraph(
        nodes=[
            _milestone("m0"),
            _milestone("m1", predecessors=["m0"]),
            _milestone("m2", predecessors=["m0"]),
            _milestone("m3", predecessors=["m1", "m2"]),
        ]
    )
    matched = {}

    frontier = initialize_milestone_frontier(graph)
    matched["m0"] = _settlement("m0")
    advance_milestone_frontier(frontier, "m0", matched)

    assert [item.milestone_id for item in ready_milestones(frontier)] == ["m1", "m2"]
    assert required_ready_milestone_ids(frontier, matched) == ("m1", "m2")


def test_frontier_advance_updates_only_direct_successors() -> None:
    graph = MilestoneGraph(
        nodes=[
            _milestone("m0"),
            *[_milestone(f"m{index}", predecessors=["m0"]) for index in range(1, 10)],
            _milestone("m10", predecessors=[f"m{index}" for index in range(1, 10)]),
        ]
    )
    matched = {}
    frontier = initialize_milestone_frontier(graph)
    graph.nodes = []

    matched["m0"] = _settlement("m0")
    advance_milestone_frontier(frontier, "m0", matched)
    matched["m1"] = _settlement("m1")
    advance_milestone_frontier(frontier, "m1", matched)

    assert [item.milestone_id for item in ready_milestones(frontier)] == [
        "m2",
        "m3",
        "m4",
        "m5",
        "m6",
        "m7",
        "m8",
        "m9",
    ]
    assert [item.milestone_id for item in blocked_candidate_milestones(frontier)] == ["m10"]
    assert frontier.remaining_predecessor_count["m10"] == 8


def test_frontier_advance_promotes_successor_when_all_predecessors_matched() -> None:
    graph = MilestoneGraph(
        nodes=[
            _milestone("m0"),
            _milestone("m1"),
            _milestone("m2", predecessors=["m0", "m1"]),
        ]
    )
    matched = {}
    frontier = initialize_milestone_frontier(graph)

    matched["m0"] = _settlement("m0")
    advance_milestone_frontier(frontier, "m0", matched)
    matched["m1"] = _settlement("m1")
    advance_milestone_frontier(frontier, "m1", matched)

    assert [item.milestone_id for item in ready_milestones(frontier)] == ["m2"]
    assert blocked_candidate_milestones(frontier) == ()


def test_frontier_maintains_topological_order_during_insert() -> None:
    graph = MilestoneGraph(
        nodes=[
            _milestone("m0"),
            _milestone("m1", predecessors=["m0"]),
            _milestone("m2"),
            _milestone("m3", predecessors=["m0"]),
        ]
    )
    matched = {}
    frontier = initialize_milestone_frontier(graph)

    matched["m0"] = _settlement("m0")
    advance_milestone_frontier(frontier, "m0", matched)

    assert frontier.ready_ids == ["m1", "m2", "m3"]
    assert [item.milestone_id for item in ready_milestones(frontier)] == ["m1", "m2", "m3"]


def _milestone(
    milestone_id: str,
    predecessors: list[str] | None = None,
    required: bool = True,
) -> Milestone:
    return Milestone(
        milestone_id=milestone_id,
        name=milestone_id,
        description=milestone_id,
        constraints=[],
        required=required,
        dependency_predecessor_ids=list(predecessors or []),
        stage_anchor_predecessor_id="__start__",
    )


def _settlement(milestone_id: str) -> HarnessStageSettlement:
    return HarnessStageSettlement(
        settlement_id=f"st-{milestone_id}",
        kind="milestone",
        milestone_id=milestone_id,
        start_step_index=1,
        end_step_index=1,
    )
