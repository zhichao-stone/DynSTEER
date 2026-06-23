import pytest

from dynsteer.config import MatchConfig
from dynsteer.match import match_milestones, validate_milestone_graph
from dynsteer.model import (
    Boundary,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageStatus,
)


def test_validate_accepts_dag() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("a", "a", "a", []),
            Milestone("b", "b", "b", []),
        ],
        edges=[("a", "b")],
    )

    validate_milestone_graph(graph)


def test_validate_rejects_cycle() -> None:
    graph = MilestoneGraph(edges=[("a", "b"), ("b", "a")])

    with pytest.raises(ValueError):
        validate_milestone_graph(graph)


def test_match_linear_milestones() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("a", "a", "a", []),
            Milestone("b", "b", "b", []),
        ],
        edges=[("a", "b")],
    )
    boundaries = [
        Boundary("b0", 0, None, "agent_message"),
        Boundary("b1", 1, None, "final"),
    ]
    matrix = {
        ("a", "b0"): MilestoneScore("a", "b0", 0.9, StageStatus.PASS),
        ("a", "b1"): MilestoneScore("a", "b1", 0.2, StageStatus.FAIL),
        ("b", "b0"): MilestoneScore("b", "b0", 0.1, StageStatus.FAIL),
        ("b", "b1"): MilestoneScore("b", "b1", 0.95, StageStatus.PASS),
    }

    mapping = match_milestones(graph, boundaries, matrix, MatchConfig())

    assert mapping.assignments["a"].boundary_id == "b0"
    assert mapping.assignments["b"].boundary_id == "b1"


def test_missing_required_milestone_is_recorded() -> None:
    graph = MilestoneGraph(nodes=[Milestone("a", "a", "a", [], required=True)])
    boundaries = [Boundary("b0", 0, None, "final")]
    matrix = {("a", "b0"): MilestoneScore("a", "b0", 0.1, StageStatus.FAIL)}

    mapping = match_milestones(graph, boundaries, matrix, MatchConfig(candidate_min_score=0.5))

    assert "a" in mapping.missing_required

