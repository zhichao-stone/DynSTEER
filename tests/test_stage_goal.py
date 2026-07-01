from __future__ import annotations

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
    graph = MilestoneGraph(
        nodes=[
            Milestone("m0", "search contact", "找到目标联系人", []),
            Milestone("m1", "update contact", "更新目标联系人电话", [_state_update_constraint()]),
        ],
        edges=[("m0", "m1")],
        metadata={"benchmark": "toolsandbox"},
    )
    task_case = TaskCase("task-1", "I want to send a message to someone.", milestone_graph=graph)
    interval = StageInterval("runtime:st2", "m1", 5, 9, StageStatus.PASS)

    goal = build_stage_goal(interval, task_case)
    target = goal["constraint_targets"][0]

    assert goal["stage_id"] == "runtime:st2"
    assert goal["stage_kind"] == "milestone"
    assert goal["current_milestone_id"] == "m1"
    assert goal["predecessor_milestone_ids"] == ["m0"]
    assert goal["objective"] == "完成 milestone m1：更新目标联系人电话"
    assert str(goal["success_condition"]).startswith("仅判断当前阶段是否满足 milestone m1")
    assert goal["primary_dimensions"] == ["progress", "state_consistency", "tool_quality", "safety"]
    assert target == {
        "constraint_id": "m1_c0",
        "target": "state_snapshot",
        "operator": "custom",
        "selector": "$",
        "namespace": "CONTACT",
        "hard": True,
        "expected_summary": {"row_count": 1, "columns": ["person_id", "phone_number"]},
    }
    assert "toolsandbox" not in str(goal).lower()
    assert "snapshot_constraint" not in target


def test_build_stage_goal_describes_finish_stage() -> None:
    graph = MilestoneGraph(
        nodes=[Milestone("m0", "done", "完成状态变更", [])],
        edges=[],
    )
    task_case = TaskCase("task-1", "任务背景", milestone_graph=graph)
    interval = StageInterval("runtime:st3", None, 10, 12, StageStatus.PASS, evidence=["finish 结算节点"])

    goal = build_stage_goal(interval, task_case)

    assert goal["stage_kind"] == "finish"
    assert goal["current_milestone_id"] is None
    assert goal["predecessor_milestone_ids"] == ["m0"]
    assert goal["objective"] == "完成运行收尾阶段：确认已匹配 milestone 后没有新的失败证据"
    assert goal["primary_dimensions"] == ["progress", "interaction_quality", "efficiency"]
    assert goal["constraint_targets"] == []
