from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque

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
    result: dict[str, list[str]] = defaultdict(list)
    for source, target in graph.edges:
        result[target].append(source)
    return result


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
    """根据 milestone 匹配结果构造阶段区间。

    Args:
        graph: milestone DAG。
        mapping: milestone 到候选边界的匹配结果。
        trajectory: Agent 执行轨迹。

    Returns:
        以 milestone 拓扑序排列的阶段区间。
    """
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


### 阶段评估目标的构建

def build_stage_goal(interval: StageInterval, task_case: TaskCase) -> JsonObject:
    """构造当前阶段的显式评估目标。

    Args:
        interval: 当前阶段区间。
        task_case: 当前任务定义。

    Returns:
        可写入 judge prompt context 的通用阶段目标 JSON 对象。
    """
    if interval is None or task_case is None:
        raise ValueError("阶段目标参数不能为空")
    graph = task_case.milestone_graph or MilestoneGraph()
    if interval.milestone_id is None:
        matched_ids = [node.milestone_id for node in graph.nodes if node.required]
        return {
            "stage_id": interval.stage_id,
            "stage_kind": "finish",
            "current_milestone_id": None,
            "predecessor_milestone_ids": matched_ids,
            "objective": "完成运行收尾阶段：确认已匹配 milestone 后没有新的失败证据",
            "success_condition": "仅判断收尾区间内是否存在推翻已匹配 milestone 的新证据、未处理错误或不当用户回应。",
            "constraint_targets": [],
            "primary_dimensions": _dimension_values(
                [Dimension.PROGRESS, Dimension.INTERACTION_QUALITY, Dimension.EFFICIENCY]
            ),
        }

    milestone = _milestone_by_id(graph, interval.milestone_id)
    return {
        "stage_id": interval.stage_id,
        "stage_kind": "milestone",
        "current_milestone_id": interval.milestone_id,
        "objective": _milestone_objective(milestone),
        "success_condition": (
            f"仅判断当前阶段是否满足 milestone {interval.milestone_id} 的约束；"
            "完整任务描述只作为背景，不作为本阶段的额外完成条件。"
        ),
        "constraint_targets": [_constraint_target_summary(item) for item in milestone.constraints],
        "primary_dimensions": _primary_dimensions(milestone),
    }


def stage_goal_digest(stage_goal: JsonObject) -> str:
    """生成阶段目标摘要 digest，便于日志与报告关联。

    Args:
        stage_goal: 阶段目标 JSON 对象。

    Returns:
        SHA-256 十六进制 digest。
    """
    if stage_goal is None:
        raise ValueError("stage_goal 不能为空")
    payload = json.dumps(stage_goal, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _milestone_by_id(graph: MilestoneGraph, milestone_id: str) -> Milestone:
    if graph is None or milestone_id is None:
        raise ValueError("milestone 查询参数不能为空")
    for milestone in graph.nodes:
        if milestone.milestone_id == milestone_id:
            return milestone
    return Milestone(milestone_id, milestone_id, f"完成 milestone {milestone_id}", [])


def _milestone_objective(milestone: Milestone) -> str:
    if milestone is None:
        raise ValueError("milestone 不能为空")
    description = str(milestone.description or milestone.name or milestone.milestone_id).strip()
    return f"完成 milestone {milestone.milestone_id}：{description}"


def _constraint_target_summary(constraint: Constraint) -> JsonObject:
    if constraint is None:
        raise ValueError("constraint 不能为空")
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "operator": constraint.operator.value,
        "selector": constraint.selector,
        "namespace": constraint.namespace,
        "hard": constraint.hard,
        "expected_summary": _expected_summary(constraint.expected),
    }


def _expected_summary(expected: object) -> JsonObject:
    if isinstance(expected, dict):
        rows = expected.get("rows")
        columns = expected.get("columns")
        return {
            "row_count": len(rows) if isinstance(rows, list) else None,
            "columns": [str(item) for item in columns] if isinstance(columns, list) else [],
        }
    if isinstance(expected, list):
        return {"item_count": len(expected)}
    if expected is None:
        return {"kind": "none"}
    return {"kind": type(expected).__name__}


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
