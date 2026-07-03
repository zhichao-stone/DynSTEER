from __future__ import annotations

import hashlib
import json
from collections import deque

from dynsteer.model import (
    Actor,
    Constraint,
    Dimension,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneMapping,
    Operator,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


def _topological_nodes(graph: MilestoneGraph) -> list[str]:
    ids = {node.milestone_id for node in graph.nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    indegree: dict[str, int] = {node_id: 0 for node_id in ids}
    for source, target in graph.edges:
        if source in ids and target in ids:
            outgoing[source].append(target)
            indegree[target] += 1
    queue: deque[str] = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order


def _predecessors(graph: MilestoneGraph) -> dict[str, list[str]]:
    return {node.milestone_id: list(node.dependency_predecessor_ids) for node in graph.nodes}


def _first_user_or_zero(trajectory: Trajectory) -> int:
    for step in sorted(trajectory.steps, key=lambda item: item.index):
        if step.actor == Actor.USER:
            return step.index
    if trajectory.steps:
        return min(step.index for step in trajectory.steps)
    return 0


def build_stage_intervals(
    graph: MilestoneGraph,
    mapping: MilestoneMapping,
    trajectory: Trajectory,
) -> list[StageInterval]:
    """根据 milestone 匹配结果构造阶段区间。"""
    if graph is None or mapping is None or trajectory is None:
        raise ValueError("build_stage_intervals 入参不能为空")
    first_start = _first_user_or_zero(trajectory)
    predecessors = _predecessors(graph)
    intervals: list[StageInterval] = []
    end_by_milestone: dict[str, int] = {}

    for milestone_id in _topological_nodes(graph):
        item = mapping.assignments.get(milestone_id)
        if item is None:
            if milestone_id in mapping.missing_required:
                intervals.append(
                    StageInterval(
                        stage_id=f"stage:{milestone_id}",
                        milestone_id=milestone_id,
                        start_step_index=-1,
                        end_step_index=-1,
                        status=StageStatus.MISSING,
                        evidence=[f"milestone {milestone_id} 缺失"],
                    )
                )
            continue
        pred_ends = [
            end_by_milestone[pred]
            for pred in predecessors.get(milestone_id, [])
            if pred in end_by_milestone
        ]
        start_step_index = max(pred_ends) if pred_ends else first_start
        end_by_milestone[milestone_id] = item.boundary_step_index
        intervals.append(
            StageInterval(
                stage_id=f"stage:{milestone_id}",
                milestone_id=milestone_id,
                start_step_index=start_step_index,
                end_step_index=item.boundary_step_index,
                status=item.score.status,
                milestone_score=item.score,
                evidence=list(item.score.evidence),
            )
        )

    return intervals


def build_stage_goal(interval: StageInterval, task_case: TaskCase) -> JsonObject:
    """构造面向 judge prompt 的当前阶段自然语言目标。"""
    if interval is None or task_case is None:
        raise ValueError("阶段目标参数不能为空")
    graph = task_case.milestone_graph or MilestoneGraph()
    if interval.milestone_id is None:
        return {
            "stage_kind": "finish",
            "objective": "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。",
            "success_condition": (
                "仅判断收尾区间内是否出现推翻既有阶段目标的新证据、未处理错误或不当回应。"
            ),
            "primary_dimensions": _dimension_values(
                [Dimension.PROGRESS, Dimension.INTERACTION_QUALITY, Dimension.EFFICIENCY]
            ),
        }

    milestone = _milestone_by_id(graph, interval.milestone_id)
    anchor = _stage_anchor_milestone(graph, milestone)
    return {
        "stage_kind": "milestone",
        "objective": _stage_objective(milestone, anchor),
        "success_condition": (
            "仅判断给定阶段区间内的行为、工具结果和状态变化是否已经达成上述目标；"
            "不要求完成后续阶段或整个任务的额外目标。"
        ),
        "primary_dimensions": _primary_dimensions(milestone),
    }


def _milestone_by_id(graph: MilestoneGraph, milestone_id: str) -> Milestone:
    if graph is None or milestone_id is None:
        raise ValueError("milestone 查询参数不能为空")
    for milestone in graph.nodes:
        if milestone.milestone_id == milestone_id:
            return milestone
    return Milestone(milestone_id, "当前阶段", "当前阶段目标", [])


def _stage_anchor_milestone(graph: MilestoneGraph, milestone: Milestone) -> Milestone | None:
    if graph is None or milestone is None:
        raise ValueError("阶段锚点参数不能为空")
    anchor_id = milestone.stage_anchor_predecessor_id
    if anchor_id is None:
        return None
    return next((node for node in graph.nodes if node.milestone_id == anchor_id), None)


def _stage_objective(milestone: Milestone, anchor: Milestone | None) -> str:
    current_summary = _milestone_summary(milestone)
    prefix = ""
    if anchor is not None:
        prefix = f"在已完成“{_milestone_summary(anchor)}”后，"
    requirements = _constraint_requirements(milestone.constraints)
    if requirements:
        return f"{prefix}完成当前阶段目标：{current_summary}；关键要求：{'；'.join(requirements)}。"
    return f"{prefix}完成当前阶段目标：{current_summary}。"


def _milestone_summary(milestone: Milestone) -> str:
    if milestone is None:
        raise ValueError("milestone 不能为空")
    for value in (milestone.description, milestone.name):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "当前阶段目标"


def _constraint_requirements(constraints: list[Constraint]) -> list[str]:
    if constraints is None:
        raise ValueError("constraints 不能为空")
    requirements: list[str] = []
    for constraint in constraints:
        text = _constraint_requirement(constraint)
        if text:
            requirements.append(text)
    return requirements


def _constraint_requirement(constraint: Constraint) -> str:
    if constraint is None:
        raise ValueError("constraint 不能为空")
    expected = constraint.expected
    if isinstance(expected, dict):
        rows = expected.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            columns = expected.get("columns")
            row = rows[0]
            selected_columns = (
                [str(column) for column in columns if str(column) in row]
                if isinstance(columns, list)
                else [str(column) for column in row]
            )
            values = [
                f"{column}={_format_value(row[column])}"
                for column in selected_columns[:4]
            ]
            namespace = f"{constraint.namespace} 中 " if constraint.namespace else ""
            suffix = f" 等 {len(rows)} 条记录" if len(rows) > 1 else ""
            if values:
                return f"使 {namespace}{', '.join(values)}{suffix}"
    if expected is not None:
        return f"满足{_target_text(constraint)}：{_format_value(expected)}"
    return f"满足{_target_text(constraint)}要求"


def _target_text(constraint: Constraint) -> str:
    target = constraint.target.value
    namespace = f"{constraint.namespace} " if constraint.namespace else ""
    return f"{namespace}{target}"


def _format_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _primary_dimensions(milestone: Milestone) -> list[str]:
    if milestone is None:
        raise ValueError("milestone 不能为空")
    dimensions: list[Dimension] = [Dimension.PROGRESS]
    targets = {constraint.target for constraint in milestone.constraints}
    operators = {constraint.operator for constraint in milestone.constraints}
    hard_exists = any(constraint.hard for constraint in milestone.constraints)
    if any(target.value.startswith("state_") for target in targets):
        dimensions.append(Dimension.STATE_CONSISTENCY)
    if any(target.value.startswith("tool_") for target in targets) or Operator.CUSTOM in operators:
        dimensions.append(Dimension.TOOL_QUALITY)
    if hard_exists or Operator.REMOVED in operators or Operator.UPDATED in operators:
        dimensions.append(Dimension.SAFETY)
    if not milestone.constraints:
        dimensions.append(Dimension.INTERACTION_QUALITY)
    return _dimension_values(dimensions)


def _dimension_values(dimensions: list[Dimension]) -> list[str]:
    if dimensions is None:
        raise ValueError("dimension 列表不能为空")
    return [dimension.value for dimension in dimensions]
