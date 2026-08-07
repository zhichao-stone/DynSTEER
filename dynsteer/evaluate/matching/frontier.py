from dynsteer.model import Milestone, MilestoneGraph, MilestoneFrontierState

def initialize_milestone_frontier(graph: MilestoneGraph) -> MilestoneFrontierState:
    """初始化 milestone frontier 运行期状态。

    入参：
        graph: 当前 case 的 milestone DAG。
    输出：
        仅在 case 初始化阶段扫描 graph 后得到的 frontier 状态。
    """
    topology = graph.topology
    if topology is None:
        raise ValueError("milestone graph 尚未 enrich")

    remaining_predecessor_count = {
        milestone.milestone_id: len(topology.predecessors_by_id[milestone.milestone_id])
        for milestone in graph.nodes
    }
    ready_ids = [milestone.milestone_id for milestone in graph.nodes if remaining_predecessor_count[milestone.milestone_id] == 0]

    return MilestoneFrontierState(
        topology=topology,
        remaining_predecessor_count=remaining_predecessor_count,
        ready_ids=ready_ids,
    )

def advance_milestone_frontier(frontier: MilestoneFrontierState, matched_milestone_id: str) -> None:
    """在 milestone matched 后原地推进 frontier。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
        matched_milestone_id: 刚完成结算的 milestone id。
    输出：
        无返回值，函数会原地更新 frontier。
    """
    frontier.ready_ids.remove(matched_milestone_id)
    for successor_id in frontier.topology.successors_by_id[matched_milestone_id]:
        remaining = frontier.remaining_predecessor_count[successor_id] - 1
        frontier.remaining_predecessor_count[successor_id] = remaining
        if remaining == 0:
            _insert_id_by_order(frontier.ready_ids, successor_id, frontier.topology.order_by_id)

def ready_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回当前 ready milestone。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
    输出：
        当前可评估 milestone 元组。
    """
    return tuple(frontier.topology.milestone_by_id[milestone_id] for milestone_id in frontier.ready_ids)

def blocked_candidate_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回当前 blocked 诊断候选。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
    输出：
        当前低成本 predecessor gap 诊断候选元组。
    """
    return tuple(
        milestone
        for milestone in frontier.topology.milestone_by_id.values()
        if 0 < frontier.remaining_predecessor_count[milestone.milestone_id] < len(frontier.topology.predecessors_by_id[milestone.milestone_id])
    )

def _insert_id_by_order(milestone_ids: list[str], milestone_id: str, order_by_id: dict[str, int]) -> None:
    order = order_by_id[milestone_id]
    insert_index = len(milestone_ids)
    for index, current_id in enumerate(milestone_ids):
        if order < order_by_id[current_id]:
            insert_index = index
            break
    milestone_ids.insert(insert_index, milestone_id)
