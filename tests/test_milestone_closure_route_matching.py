import json
from pathlib import Path

import pytest

from dynsteer.adapter.loader import parse_task_case
from dynsteer.adapter.route import enrich_milestone_routes
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.matching.milestone import analyze_milestone_step, milestone_scoring_step
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.graph import enrich_milestone_graph
from dynsteer.model import (
    Actor,
    Constraint,
    ConstraintTarget,
    EventType,
    Milestone,
    MilestoneGraph,
    Operator,
    StageStatus,
    Trajectory,
    TrajectoryStep,
    ensure_json_object,
)


def test_route_milestone_scores_matching_closure_step() -> None:
    """覆盖有 route 的 milestone 使用闭包内同向 step 评分。"""
    graph, milestone = _graph_with_message_constraint(route=True, expected="target refusal")
    agent_step = _step("s1", 1, Actor.AGENT, Actor.USER, "target refusal")
    user_step = _step("s2", 2, Actor.USER, Actor.AGENT, "please try again")
    trajectory = Trajectory("run", "task", [agent_step, user_step])
    boundary = candidate_boundary_for_current_step(trajectory, user_step)

    analysis = analyze_milestone_step(
        trajectory=trajectory,
        step=user_step,
        boundary=boundary,
        matched={},
        frontier=initialize_milestone_frontier(graph),
        closure_steps=[agent_step, user_step],
        scorer=GeneralScorer(),
    )

    assert analysis.hit is not None
    _, scoring_boundary, score = analysis.hit
    assert scoring_boundary.step_index == agent_step.index
    assert score.status == StageStatus.PASS
    assert score.constraint_scores[0].actual == agent_step.content
    assert milestone_scoring_step(milestone, [agent_step, user_step], user_step) == agent_step


def test_route_milestone_does_not_scan_all_closure_steps() -> None:
    """覆盖 route 只选同向 step，不因闭包终点文本满足目标而误判通过。"""
    graph, _ = _graph_with_message_constraint(route=True, expected="target refusal")
    agent_step = _step("s1", 1, Actor.AGENT, Actor.USER, "unrelated answer")
    user_step = _step("s2", 2, Actor.USER, Actor.AGENT, "target refusal")
    trajectory = Trajectory("run", "task", [agent_step, user_step])
    boundary = candidate_boundary_for_current_step(trajectory, user_step)

    analysis = analyze_milestone_step(
        trajectory=trajectory,
        step=user_step,
        boundary=boundary,
        matched={},
        frontier=initialize_milestone_frontier(graph),
        closure_steps=[agent_step, user_step],
        scorer=GeneralScorer(),
    )

    assert analysis.hit is None
    assert analysis.attempt_detail is not None
    candidate = analysis.attempt_detail["candidate_scores"][0]
    assert candidate["boundary"]["step_index"] == agent_step.index
    assert candidate["score"]["constraint_scores"][0]["actual"] == agent_step.content


def test_milestone_without_route_keeps_default_closure_endpoint() -> None:
    """覆盖无 route milestone 继续使用闭包终点 boundary。"""
    graph, _ = _graph_with_message_constraint(route=False, expected="target refusal")
    agent_step = _step("s1", 1, Actor.AGENT, Actor.USER, "target refusal")
    user_step = _step("s2", 2, Actor.USER, Actor.AGENT, "unrelated reply")
    trajectory = Trajectory("run", "task", [agent_step, user_step])
    boundary = candidate_boundary_for_current_step(trajectory, user_step)

    analysis = analyze_milestone_step(
        trajectory=trajectory,
        step=user_step,
        boundary=boundary,
        matched={},
        frontier=initialize_milestone_frontier(graph),
        closure_steps=[agent_step, user_step],
        scorer=GeneralScorer(),
    )

    assert analysis.hit is None
    assert analysis.attempt_detail is not None
    candidate = analysis.attempt_detail["candidate_scores"][0]
    assert candidate["boundary"]["step_index"] == user_step.index
    assert candidate["score"]["constraint_scores"][0]["actual"] == user_step.content


def test_enrich_milestone_routes_writes_canonical_metadata() -> None:
    """覆盖通用适配后处理写入 constraint 与 milestone 级 route metadata。"""
    constraint = _message_constraint(route=True)
    no_route_constraint = _message_constraint(route=False, constraint_id="c1")
    milestone = Milestone(
        milestone_id="m0",
        name="m0",
        description="message",
        constraints=[constraint, no_route_constraint],
    )

    enrich_milestone_routes(MilestoneGraph(nodes=[milestone]))

    constraint_matching = constraint.metadata["milestone_matching"]
    no_route_matching = no_route_constraint.metadata["milestone_matching"]
    milestone_matching = milestone.metadata["milestone_matching"]
    assert constraint_matching["route"] == {"sender": "AGENT", "recipient": "USER"}
    assert constraint_matching["route_source"] == "stage_goal_semantics"
    assert no_route_matching["route"] is None
    assert no_route_matching["route_source"] is None
    assert milestone_matching["route_group_count"] == 1
    assert milestone_matching["route_groups"][0]["constraint_ids"] == ["c0"]


def test_enrich_milestone_routes_rejects_multi_route_milestone() -> None:
    """覆盖多 route milestone 不静默回退。"""
    first = _message_constraint(route=True, constraint_id="c0")
    second = _message_constraint(route=True, constraint_id="c1")
    second.stage_goal_semantics = {"sender": "USER", "recipient": "AGENT"}
    milestone = Milestone(
        milestone_id="m0",
        name="m0",
        description="message",
        constraints=[first, second],
    )

    with pytest.raises(ValueError, match="route_group_count"):
        enrich_milestone_routes(MilestoneGraph(nodes=[milestone]))


def test_toolsandbox_adapted_cases_satisfy_single_route_invariant() -> None:
    """覆盖当前正式 ToolSandbox adapted case 均满足单 route invariant。"""
    data_dir = Path("data/toolsandbox/adapted_cases")
    paths = sorted(data_dir.glob("*.json"))
    assert paths
    for path in paths:
        task_case = parse_task_case(ensure_json_object(json.loads(path.read_text(encoding="utf-8"))))
        assert task_case.milestone_graph is not None
        enrich_milestone_routes(task_case.milestone_graph)
        for milestone in task_case.milestone_graph.nodes:
            matching = milestone.metadata.get("milestone_matching")
            assert isinstance(matching, dict)
            assert matching.get("route_group_count") in {0, 1}


def _graph_with_message_constraint(route: bool, expected: str) -> tuple[MilestoneGraph, Milestone]:
    milestone = Milestone(
        milestone_id="m0",
        name="m0",
        description="message",
        constraints=[_message_constraint(route=route, expected=expected)],
        pass_threshold=1.0,
    )
    graph = enrich_milestone_graph(MilestoneGraph(nodes=[milestone]))
    return enrich_milestone_routes(graph), milestone


def _message_constraint(
    route: bool,
    expected: str = "target refusal",
    constraint_id: str = "c0",
) -> Constraint:
    semantics = {"sender": "AGENT", "recipient": "USER"} if route else None
    return Constraint(
        constraint_id=constraint_id,
        target=ConstraintTarget.STEP,
        selector="$.content",
        operator=Operator.CONTAINS,
        expected=expected,
        hard=True,
        stage_goal_semantics=semantics,
    )


def _step(step_id: str, index: int, actor: Actor, recipient: Actor, content: str) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=step_id,
        index=index,
        actor=actor,
        event_type=EventType.MESSAGE,
        recipient=recipient,
        content=content,
    )
