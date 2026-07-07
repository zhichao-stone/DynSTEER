from __future__ import annotations

from dynsteer.graph import START_NODE_ID, enrich_milestone_graph
from dynsteer.model import Milestone, MilestoneGraph


def test_enrich_milestone_graph_preserves_start_anchor() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("m0", "m0", "root", []),
            Milestone("m1", "m1", "next", []),
        ],
        edges=[("m0", "m1")],
    )

    enriched = enrich_milestone_graph(graph)

    by_id = {node.milestone_id: node for node in enriched.nodes}
    assert by_id["m0"].stage_anchor_predecessor_id == START_NODE_ID
    assert by_id["m1"].stage_anchor_predecessor_id == "m0"


def test_join_milestone_uses_common_anchor_not_direct_predecessor_max() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("m0", "m0", "root", []),
            Milestone("m1", "m1", "branch root", []),
            Milestone("m3", "m3", "left branch", []),
            Milestone("m4", "m4", "right branch", []),
            Milestone("m5", "m5", "join", []),
        ],
        edges=[
            ("m0", "m1"),
            ("m1", "m3"),
            ("m1", "m4"),
            ("m3", "m5"),
            ("m4", "m5"),
        ],
    )

    enriched = enrich_milestone_graph(graph)

    by_id = {node.milestone_id: node for node in enriched.nodes}
    assert by_id["m5"].dependency_predecessor_ids == ["m3", "m4"]
    assert by_id["m5"].stage_anchor_predecessor_id == "m1"
