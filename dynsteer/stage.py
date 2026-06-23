from __future__ import annotations

from collections import defaultdict, deque

from dynsteer.model import Actor, MilestoneGraph, MilestoneMapping, StageInterval, StageStatus, Trajectory


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
