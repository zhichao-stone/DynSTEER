from __future__ import annotations

from dynsteer.graph import enrich_milestone_graph
from dynsteer.model import (
    Constraint,
    ConstraintTarget,
    Milestone,
    MilestoneGraph,
    Operator,
    StageInterval,
    StageStatus,
    TaskCase,
)
from dynsteer.stage import build_stage_goal


def _state_update_constraint() -> Constraint:
    return Constraint(
        constraint_id="m1_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={
            "rows": [{"person_id": "person-1", "phone_number": "+10293847563"}],
            "columns": ["person_id", "phone_number"],
        },
        namespace="CONTACT",
        hard=True,
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "CONTACT",
                "snapshot_constraint": "update_similarity",
                "reference_milestone_node_index": 0,
            }
        },
    )


def test_build_stage_goal_describes_constraint_with_generic_fields_only() -> None:
    graph = enrich_milestone_graph(MilestoneGraph(
        nodes=[
            Milestone("m0", "search contact", "找到目标联系人", []),
            Milestone("m1", "update contact", "更新目标联系人电话", [_state_update_constraint()]),
        ],
        edges=[("m0", "m1")],
        metadata={"benchmark": "toolsandbox"},
    ))
    task_case = TaskCase("task-1", "I want to send a message to someone.", "case-1", milestone_graph=graph)
    interval = StageInterval("runtime:st2", "m1", 5, 9, StageStatus.PASS)

    goal = build_stage_goal(interval, task_case)

    assert goal["stage_kind"] == "milestone"
    assert "current_milestone_id" not in goal
    assert "stage_anchor_predecessor_id" not in goal
    assert "predecessor_milestone_ids" not in goal
    assert "constraint_targets" not in goal
    assert goal["objective"] == (
        "在已完成“找到目标联系人”后，完成当前阶段目标：更新目标联系人电话；"
        "关键要求：使 CONTACT 中 person_id=person-1, phone_number=+10293847563。"
    )
    assert goal["success_condition"] == (
        "仅判断给定阶段区间内的行为、工具结果和状态变化是否已经达成上述目标；"
        "不要求完成后续阶段或整个任务的额外目标。"
    )
    assert goal["primary_dimensions"] == ["progress", "state_consistency", "tool_quality", "safety"]
    assert "toolsandbox" not in str(goal).lower()
    assert "snapshot_constraint" not in str(goal)


def test_build_stage_goal_describes_finish_stage() -> None:
    graph = enrich_milestone_graph(MilestoneGraph(
        nodes=[Milestone("m0", "done", "完成状态变更", [])],
        edges=[],
    ))
    task_case = TaskCase("task-1", "任务背景", "case-1", milestone_graph=graph)
    interval = StageInterval("runtime:st3", None, 10, 12, StageStatus.PASS, evidence=["finish 结算节点"])

    goal = build_stage_goal(interval, task_case)

    assert goal["stage_kind"] == "finish"
    assert "current_milestone_id" not in goal
    assert "predecessor_milestone_ids" not in goal
    assert goal["objective"] == "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"
    assert goal["primary_dimensions"] == ["progress", "interaction_quality", "efficiency"]
    assert "constraint_targets" not in goal
