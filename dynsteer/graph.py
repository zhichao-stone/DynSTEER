from collections import deque
from dynsteer.model import MilestoneGraph


START_NODE_ID = "__start__"
FINISH_NODE_ID = "__finish__"

def _augmented_edges(graph: MilestoneGraph) -> list[tuple[str, str]]:
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
    augmented = _augmented_edges(graph)
    node_ids = actualnode_ids | {START_NODE_ID, FINISH_NODE_ID}
    predecessors, successors = build_adjacency(node_ids, augmented)
    order, depths = topological_order(predecessors, successors)
    idom = _immediate_dominators(order, predecessors, depths)
    for node in graph.nodes:
        node.dependency_predecessor_ids = [predecessor for predecessor in predecessors.get(node.milestone_id, []) if predecessor in actualnode_ids]
        anchor_id = idom.get(node.milestone_id)
        node.stage_anchor_predecessor_id = anchor_id if isinstance(anchor_id, str) else START_NODE_ID
    finish_anchor = idom.get(FINISH_NODE_ID)
    graph.metadata["graph_analysis"] = {
        "start_node_id": START_NODE_ID,
        "finish_node_id": FINISH_NODE_ID,
        "augmented_edges": [[source, target] for source, target in augmented],
        "finish_stage_anchor_predecessor_id": finish_anchor
        if isinstance(finish_anchor, str)
        else START_NODE_ID,
    }
    return graph
