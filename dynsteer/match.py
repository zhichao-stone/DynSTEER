from __future__ import annotations

from collections import defaultdict, deque

from dynsteer.config import MatchConfig
from dynsteer.model import (
    Boundary,
    MilestoneGraph,
    MilestoneMapping,
    MilestoneMappingItem,
    MilestoneScore,
)


def _node_ids(graph: MilestoneGraph) -> set[str]:
    ids = {node.milestone_id for node in graph.nodes}
    for source, target in graph.edges:
        ids.add(source)
        ids.add(target)
    return ids


def _topological_order(graph: MilestoneGraph) -> list[str]:
    ids = _node_ids(graph)
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    indegree: dict[str, int] = {node_id: 0 for node_id in ids}
    for source, target in graph.edges:
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
    if len(order) != len(ids):
        raise ValueError("milestone graph 存在环")
    return order


def validate_milestone_graph(graph: MilestoneGraph) -> None:
    """校验 milestone 图节点唯一、边引用有效且无环。

    Args:
        graph: 待校验的 milestone DAG。

    Raises:
        ValueError: 图结构非法时抛出。
    """
    if graph is None:
        raise ValueError("graph 不能为空")
    explicit_ids = [node.milestone_id for node in graph.nodes]
    if len(explicit_ids) != len(set(explicit_ids)):
        raise ValueError("milestone_id 不能重复")
    explicit_id_set = set(explicit_ids)
    if explicit_id_set:
        for source, target in graph.edges:
            if source not in explicit_id_set or target not in explicit_id_set:
                raise ValueError(f"边引用了不存在的 milestone: {source}->{target}")
    _topological_order(graph)


def _predecessors(graph: MilestoneGraph) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for source, target in graph.edges:
        result[target].append(source)
    return result


def _best_candidate(
    milestone_id: str,
    boundaries: list[Boundary],
    score_matrix: dict[tuple[str, str], MilestoneScore],
    min_step_index: int,
    config: MatchConfig,
) -> tuple[Boundary, MilestoneScore] | None:
    candidates: list[tuple[Boundary, MilestoneScore]] = []
    for boundary in boundaries:
        if boundary.step_index < min_step_index:
            continue
        score = score_matrix.get((milestone_id, boundary.boundary_id))
        if score is None or score.score < config.candidate_min_score:
            continue
        candidates.append((boundary, score))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[1].score, -item[0].step_index))


def match_milestones(
    graph: MilestoneGraph,
    boundaries: list[Boundary],
    score_matrix: dict[tuple[str, str], MilestoneScore],
    config: MatchConfig,
) -> MilestoneMapping:
    """将 milestone DAG 贪心匹配到候选边界。

    Args:
        graph: milestone DAG。
        boundaries: 候选边界。
        score_matrix: `(milestone_id, boundary_id)` 到评分的矩阵。
        config: 匹配阈值配置。

    Returns:
        milestone 到边界的匹配结果。
    """
    if graph is None or boundaries is None or score_matrix is None or config is None:
        raise ValueError("match_milestones 入参不能为空")
    validate_milestone_graph(graph)
    node_by_id = {node.milestone_id: node for node in graph.nodes}
    predecessors = _predecessors(graph)
    assignments: dict[str, MilestoneMappingItem] = {}
    missing_required: list[str] = []
    evidence: list[str] = []

    for milestone_id in _topological_order(graph):
        milestone = node_by_id.get(milestone_id)
        if milestone is None:
            continue
        pred_steps = [
            assignments[pred].boundary_step_index
            for pred in predecessors.get(milestone_id, [])
            if pred in assignments
        ]
        min_step = max(pred_steps) if pred_steps else min((boundary.step_index for boundary in boundaries), default=0)
        candidate = _best_candidate(milestone_id, boundaries, score_matrix, min_step, config)
        if candidate is None:
            if milestone.required:
                missing_required.append(milestone_id)
            evidence.append(f"milestone {milestone_id} 未找到满足阈值的候选边界")
            continue
        boundary, score = candidate
        assignments[milestone_id] = MilestoneMappingItem(
            milestone_id=milestone_id,
            boundary_id=boundary.boundary_id,
            boundary_step_index=boundary.step_index,
            score=score,
        )
        evidence.append(f"milestone {milestone_id} 匹配到边界 {boundary.boundary_id}")

    if assignments:
        objective = sum(item.score.score for item in assignments.values()) / len(assignments)
    else:
        objective = 0.0
    objective -= config.missing_required_penalty * len(missing_required)
    return MilestoneMapping(
        assignments=assignments,
        missing_required=missing_required,
        objective=objective,
        evidence=evidence,
    )
