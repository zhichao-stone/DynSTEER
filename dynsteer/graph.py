from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from dynsteer.model import (
    Constraint,
    Milestone,
    MilestoneGraph,
    MilestoneTopology,
    Minefield,
    MinefieldPenalty,
    StageGoalSemanticKind,
)
from dynsteer.utils import stable_json_digest


if TYPE_CHECKING:
    from dynsteer.milestone.model import PublicEvidence


START_NODE_ID = "__start__"
FINISH_NODE_ID = "__finish__"


def milestones_from_occurrences(
    occurrences: list[tuple[str, str, int]],
    evidence: dict[str, PublicEvidence],
) -> list[Milestone]:
    """将已排序的共同工具 occurrence 编译为 operation milestones。"""
    result: list[Milestone] = []
    for turn_id, evidence_id, occurrence_index in occurrences:
        item = evidence[evidence_id]
        tool_name = _evidence_tool_name(item)
        milestone_id = f"m_{stable_json_digest((turn_id, evidence_id, occurrence_index))[:16]}"
        result.append(Milestone(
            milestone_id=milestone_id,
            name=f"执行 {tool_name}",
            description=f"{turn_id} 中第 {occurrence_index + 1} 次执行 {tool_name}",
            constraints=[Constraint(
                constraint_id=f"{milestone_id}_tool",
                target=item.target,
                selector=item.selector,
                operator=item.operator,
                expected=tool_name,
                namespace=item.namespace,
                hard=True,
                evaluator_hint=item.evaluator_hint,
                stage_goal_semantics={
                    "kind": StageGoalSemanticKind.TOOL_CALL.value,
                    "tool_name": tool_name,
                    "evidence_source": "trajectory_or_structured_scorer",
                    "user_visible_required": False,
                },
                metadata={"evidence_id": evidence_id},
            )],
            matching_route=item.matching_route,
            metadata={
                "turn_id": turn_id,
                "evidence_id": evidence_id,
                "occurrence_index": occurrence_index,
                "necessity_basis": "path_intersection",
            },
        ))
    return result


def minefields_from_forbidden(
    forbidden_by_turn: dict[str, set[str]],
    evidence: dict[str, PublicEvidence],
) -> list[Minefield]:
    """将逐 turn 的共同 forbidden evidence 编译为 fatal minefields。"""
    result: list[Minefield] = []
    for turn_id, forbidden in forbidden_by_turn.items():
        for evidence_id in sorted(forbidden):
            item = evidence[evidence_id]
            tool_name = _evidence_tool_name(item)
            minefield_id = f"mf_{stable_json_digest((turn_id, evidence_id))[:16]}"
            result.append(Minefield(
                minefield_id=minefield_id,
                name=f"禁止 {tool_name}",
                description=f"{turn_id} 的全部可模拟路径均禁止调用 {tool_name}",
                severity="fatal",
                constraints=[Constraint(
                    constraint_id=f"{minefield_id}_trigger",
                    target=item.target,
                    selector=item.selector,
                    operator=item.operator,
                    expected=tool_name,
                    hard=True,
                    evaluator_hint=item.evaluator_hint,
                    stage_goal_semantics={
                        "kind": StageGoalSemanticKind.TOOL_CALL.value,
                        "tool_name": tool_name,
                    },
                )],
                penalty=MinefieldPenalty(mode="fixed", value=1.0),
                metadata={"turn_id": turn_id, "evidence_id": evidence_id},
            ))
    return result


def _evidence_tool_name(evidence: PublicEvidence) -> str:
    value = evidence.metadata.get("tool_name")
    if not isinstance(value, str) or not value:
        raise ValueError(f"TOOL_CALL evidence 缺少真实 tool_name: {evidence.evidence_id}")
    return value

def augmented_edges(graph: MilestoneGraph) -> list[tuple[str, str]]:
    ids = {node.milestone_id for node in graph.nodes}
    if not ids:
        return [(START_NODE_ID, FINISH_NODE_ID)]
    indegree: dict[str, int] = {node_id: 0 for node_id in ids}
    outdegree: dict[str, int] = {node_id: 0 for node_id in ids}
    edges = [(source, target) for source, target in graph.edges if source in ids and target in ids]
    for source, target in edges:
        outdegree[source] += 1
        indegree[target] += 1
    augmented: list[tuple[str, str]] = []
    augmented.extend(((START_NODE_ID, node_id) for node_id in sorted(ids) if indegree[node_id] == 0))
    augmented.extend(edges)
    augmented.extend(((node_id, FINISH_NODE_ID) for node_id in sorted(ids) if outdegree[node_id] == 0))
    return augmented

def build_adjacency(node_ids: set[str], edges: list[tuple[str, str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    predecessors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    successors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for source, target in edges:
        predecessors[target].append(source)
        successors[source].append(target)
    return ({node_id: sorted(values) for node_id, values in predecessors.items()}, {node_id: sorted(values) for node_id, values in successors.items()})

def topological_order(predecessors: dict[str, list[str]], successors: dict[str, list[str]]) -> tuple[list[str], dict[str, int]]:
    indegree: dict[str, int] = {node_id: len(values) for node_id, values in predecessors.items()}
    root_node: list[str] = sorted((node_id for node_id, degree in indegree.items() if degree == 0))
    queue: deque[str] = deque(root_node)
    depths: dict[str, int] = {n: 0 for n in root_node}
    order: list[str] = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for target in successors[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                depths[target] = depths[node_id] + 1
                queue.append(target)
    if len(order) != len(predecessors):
        raise ValueError("milestone graph 存在环")
    return (order, depths)


def transitive_reduction(
    node_ids: set[str], edges: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """校验 DAG 并删除可由其他路径推导出的传递边。"""
    predecessors, successors = build_adjacency(node_ids, edges)
    topological_order(predecessors, successors)
    reduced: list[tuple[str, str]] = []
    for source, target in sorted(set(edges)):
        if not _reachable_without_edge(source, target, successors, (source, target)):
            reduced.append((source, target))
    return reduced


def _reachable_without_edge(
    source: str,
    target: str,
    successors: dict[str, list[str]],
    excluded: tuple[str, str],
) -> bool:
    pending = [source]
    visited = {source}
    while pending:
        current = pending.pop()
        for successor in successors[current]:
            if (current, successor) == excluded:
                continue
            if successor == target:
                return True
            if successor not in visited:
                visited.add(successor)
                pending.append(successor)
    return False

def _lca(left: str, right: str, idom: dict[str, str], depths: dict[str, int]) -> str:
    """基于直接支配关系和深度信息，获取两个节点的最近公共祖先（LCA）。"""
    u, v = (left, right)
    while u != v:
        if depths[u] < depths[v]:
            v = idom[v]
        else:
            u = idom[u]
    return u

def _lca_all(nodes: list[str], idom: dict[str, str], depths: dict[str, int]) -> str:
    """基于直接支配关系和深度信息，获取多个节点的最近公共祖先（LCA）。"""
    if not nodes:
        raise ValueError("lca 节点列表不能为空")
    current_lca = nodes[0]
    for node_id in nodes[1:]:
        current_lca = _lca(current_lca, node_id, idom, depths)
    return current_lca

def _immediate_dominators(order: list[str], predecessors: dict[str, list[str]], depths: dict[str, int]) -> dict[str, str]:
    """基于拓扑顺序、前驱关系和深度信息，计算每个节点的直接支配节点。"""
    idom: dict[str, str] = {START_NODE_ID: START_NODE_ID}
    for node_id in order[1:]:
        idom[node_id] = _lca_all(predecessors[node_id], idom, depths)
    return idom

def enrich_milestone_graph(graph: MilestoneGraph) -> MilestoneGraph:
    """为 milestone graph 写入直接前驱、阶段锚点和增强图分析元数据。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    actualnode_ids = {node.milestone_id for node in graph.nodes}
    augmented = augmented_edges(graph)
    node_ids = actualnode_ids | {START_NODE_ID, FINISH_NODE_ID}
    predecessors, successors = build_adjacency(node_ids, augmented)
    order, depths = topological_order(predecessors, successors)
    idom = _immediate_dominators(order, predecessors, depths)
    finish_anchor = idom.get(FINISH_NODE_ID)
    actual_predecessors = {node_id: tuple(item for item in predecessors[node_id] if item in actualnode_ids) for node_id in actualnode_ids}
    actual_successors = {node_id: tuple(item for item in successors[node_id] if item in actualnode_ids) for node_id in actualnode_ids}
    graph.topology = MilestoneTopology(
        milestone_by_id={node.milestone_id: node for node in graph.nodes},
        predecessors_by_id=actual_predecessors,
        successors_by_id=actual_successors,
        stage_anchor_by_id={node_id: idom.get(node_id, START_NODE_ID) for node_id in actualnode_ids},
        order_by_id={node.milestone_id: index for index, node in enumerate(graph.nodes)},
        terminal_ids=tuple(node_id for node_id in order if node_id in actualnode_ids and not actual_successors[node_id]),
        finish_anchor_id=finish_anchor if isinstance(finish_anchor, str) else START_NODE_ID,
    )
    return graph
