from dynsteer.model import (
    Actor,
    Dimension,
    EvaluationLevel,
    MilestoneGraph,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
)


def test_empty_milestone_graph_is_allowed() -> None:
    task = TaskCase(task_id="task-1", task_description="demo")

    assert task.milestone_graph is None


def test_mutable_defaults_are_isolated() -> None:
    first = MilestoneGraph()
    second = MilestoneGraph()

    first.edges.append(("a", "b"))

    assert second.edges == []


def test_enum_values_are_stable() -> None:
    assert Actor.AGENT.value == "agent"
    assert Dimension.SAFETY.value == "safety"


def test_stage_result_serializes_enum_keys() -> None:
    result = StageEvaluationResult(
        stage_id="stage-1",
        milestone_id="m1",
        evaluator_level=EvaluationLevel.CHEAP,
        status=StageStatus.PASS,
        stage_score=0.9,
        uncertainty=0.1,
        dimension_scores={Dimension.PROGRESS: 0.9},
    )

    data = result.to_dict()

    assert data["dimension_scores"] == {"progress": 0.9}
    assert data["evaluator_level"] == "cheap"

