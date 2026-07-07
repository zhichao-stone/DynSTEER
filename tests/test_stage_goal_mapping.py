from __future__ import annotations

import json

import pytest

from dynsteer.graph import START_NODE_ID
from dynsteer.model import Milestone, MilestoneGraph, StageInterval, StageStatus, TaskCase
from dynsteer.stage_goal import (
    build_stage_goal_generation_prompt,
    required_stage_goal_keys,
    resolve_stage_goal,
    stage_goal_key,
)


def _task_case(stage_goals: dict[str, str]) -> TaskCase:
    return TaskCase(
        task_id="task",
        task_description="complete task",
        case_id="case",
        milestone_graph=MilestoneGraph(
            nodes=[
                Milestone("m0", "m0", "root", [], stage_anchor_predecessor_id=START_NODE_ID),
                Milestone("m1", "m1", "next", [], stage_anchor_predecessor_id="m0"),
            ],
            edges=[("m0", "m1")],
        ),
        stage_goals=stage_goals,
    )


def _prompt_payload(prompt: str) -> dict[str, object]:
    marker = "输入 JSON:\n"
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1])


def test_required_stage_goal_keys_uses_anchor_key() -> None:
    keys = required_stage_goal_keys(_task_case({}).milestone_graph)

    assert keys == ["__start__->m0", "m0->m1"]


def test_resolve_stage_goal_fails_fast_when_cached_mapping_is_missing() -> None:
    interval = StageInterval(
        stage_id="stage:m1",
        milestone_id="m1",
        stage_anchor_milestone_id="m0",
        start_boundary_step_index=1,
        start_step_index=2,
        end_step_index=3,
        status=StageStatus.PASS,
    )

    with pytest.raises(ValueError, match="缺少预生成 stage_goal"):
        resolve_stage_goal(interval, _task_case({}))


def test_resolve_stage_goal_returns_cached_text_exactly() -> None:
    interval = StageInterval(
        stage_id="stage:m1",
        milestone_id="m1",
        stage_anchor_milestone_id="m0",
        start_boundary_step_index=1,
        start_step_index=2,
        end_step_index=3,
        status=StageStatus.PASS,
    )
    stage_goal = "finish next milestone"

    assert resolve_stage_goal(interval, _task_case({stage_goal_key("m0", "m1"): stage_goal})) == stage_goal


def test_stage_goal_prompt_uses_augmented_edges_and_anchor_field() -> None:
    graph = MilestoneGraph(
        nodes=[
            Milestone("m0", "m0", "root", [], stage_anchor_predecessor_id=START_NODE_ID),
            Milestone("m1", "m1", "next", [], stage_anchor_predecessor_id="m0"),
        ],
        edges=[("m0", "m1")],
        metadata={
            "graph_analysis": {
                "augmented_edges": [
                    [START_NODE_ID, "m0"],
                    ["m0", "m1"],
                    ["m1", "__finish__"],
                ],
            }
        },
    )
    task_case = TaskCase(
        task_id="task",
        task_description="complete task",
        case_id="case",
        milestone_graph=graph,
        stage_goals={},
    )

    payload = _prompt_payload(build_stage_goal_generation_prompt(task_case))
    milestone_graph = payload["milestone_graph"]

    assert milestone_graph["edges"] == [
        [START_NODE_ID, "m0"],
        ["m0", "m1"],
        ["m1", "__finish__"],
    ]
    assert "graph_analysis" not in milestone_graph
    first_node = milestone_graph["nodes"][0]
    assert first_node["anchor"] == START_NODE_ID
    assert "stage_anchor_predecessor_id" not in first_node
    assert "dependency_predecessor_ids" not in first_node
