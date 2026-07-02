from __future__ import annotations

from dynsteer.graph import enrich_milestone_graph
from dynsteer.model import Milestone, MilestoneGraph


def _milestone(milestone_id: str) -> Milestone:
    return Milestone(milestone_id, milestone_id, f"完成 {milestone_id}", [])


def test_enrich_milestone_graph_records_stage_anchor_predecessors() -> None:
    graph = enrich_milestone_graph(
        MilestoneGraph(
            nodes=[_milestone("m0"), _milestone("m1"), _milestone("m2"), _milestone("m3")],
            edges=[("m0", "m1"), ("m0", "m2"), ("m1", "m3"), ("m2", "m3")],
        )
    )

    nodes = {node.milestone_id: node for node in graph.nodes}

    assert nodes["m0"].stage_anchor_predecessor_id is None
    assert nodes["m1"].stage_anchor_predecessor_id == "m0"
    assert nodes["m2"].stage_anchor_predecessor_id == "m0"
    assert nodes["m3"].stage_anchor_predecessor_id == "m0"
    assert graph.metadata["graph_analysis"]["finish_stage_anchor_predecessor_id"] == "m3"


def test_enrich_milestone_graph_handles_long_dag_without_path_recursion() -> None:
    count = 1100
    graph = enrich_milestone_graph(
        MilestoneGraph(
            nodes=[_milestone(f"m{index}") for index in range(count)],
            edges=[(f"m{index}", f"m{index + 1}") for index in range(count - 1)],
        )
    )

    nodes = {node.milestone_id: node for node in graph.nodes}

    assert nodes["m0"].stage_anchor_predecessor_id is None
    assert nodes["m1099"].dependency_predecessor_ids == ["m1098"]
    assert nodes["m1099"].stage_anchor_predecessor_id == "m1098"
    assert graph.metadata["graph_analysis"]["finish_stage_anchor_predecessor_id"] == "m1099"


def test_enrich_milestone_graph_accepts_empty_graph() -> None:
    graph = enrich_milestone_graph(MilestoneGraph())

    assert graph.nodes == []
    assert graph.metadata["graph_analysis"]["augmented_edges"] == [["__start__", "__finish__"]]
    assert graph.metadata["graph_analysis"]["finish_stage_anchor_predecessor_id"] is None
