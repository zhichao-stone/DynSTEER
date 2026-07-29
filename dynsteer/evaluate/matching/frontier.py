from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import Milestone, MilestoneGraph, MilestoneFrontierState

def initialize_milestone_frontier(graph: MilestoneGraph) -> MilestoneFrontierState:
    """初始化 milestone frontier 运行期状态。

    入参：
        graph: 当前 case 的 milestone DAG。
    输出：
        仅在 case 初始化阶段扫描 graph 后得到的 frontier 状态。
    """
    milestone_by_id: dict[str, Milestone] = {}
    order_by_id: dict[str, int] = {}
    dependents: dict[str, list[str]] = {}
    for index, milestone in enumerate(graph.nodes):
        milestone_by_id[milestone.milestone_id] = milestone
        order_by_id[milestone.milestone_id] = index
        dependents.setdefault(milestone.milestone_id, [])
    remaining_predecessor_count: dict[str, int] = {}
    for milestone in graph.nodes:
        remaining_count = 0
        for predecessor_id in milestone.dependency_predecessor_ids:
            dependents.setdefault(predecessor_id, []).append(milestone.milestone_id)
            remaining_count += 1
        remaining_predecessor_count[milestone.milestone_id] = remaining_count
    ready_ids: list[str] = []
    for milestone in graph.nodes:
        milestone_id = milestone.milestone_id
        if remaining_predecessor_count[milestone_id] == 0:
            ready_ids.append(milestone_id)
    return MilestoneFrontierState(milestone_by_id=milestone_by_id, dependents_by_id={milestone_id: tuple(successors) for milestone_id, successors in dependents.items()}, remaining_predecessor_count=remaining_predecessor_count, ready_ids=ready_ids, blocked_candidate_ids=[], order_by_id=order_by_id)

def advance_milestone_frontier(frontier: MilestoneFrontierState, matched_milestone_id: str, matched: dict[str, HarnessStageSettlement]) -> None:
    """在 milestone matched 后原地推进 frontier。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
        matched_milestone_id: 刚完成结算的 milestone id。
        matched: 最新已匹配 milestone 结算表。
    输出：
        无返回值，函数会原地更新 frontier。
    """
    _remove_id(frontier.ready_ids, matched_milestone_id)
    _remove_id(frontier.blocked_candidate_ids, matched_milestone_id)
    for successor_id in frontier.dependents_by_id.get(matched_milestone_id, ()):
        if successor_id in matched:
            continue
        remaining = max(frontier.remaining_predecessor_count.get(successor_id, 0) - 1, 0)
        frontier.remaining_predecessor_count[successor_id] = remaining
        if remaining == 0:
            _insert_id_by_order(frontier.ready_ids, successor_id, frontier.order_by_id)
            _remove_id(frontier.blocked_candidate_ids, successor_id)
        else:
            _insert_id_by_order(frontier.blocked_candidate_ids, successor_id, frontier.order_by_id)

def ready_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回当前 ready milestone。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
    输出：
        当前可评估 milestone 元组。
    """
    return tuple((frontier.milestone_by_id[milestone_id] for milestone_id in frontier.ready_ids if milestone_id in frontier.milestone_by_id))

def blocked_candidate_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """返回当前 blocked 诊断候选。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
    输出：
        当前低成本 predecessor gap 诊断候选元组。
    """
    return tuple((frontier.milestone_by_id[milestone_id] for milestone_id in frontier.blocked_candidate_ids if milestone_id in frontier.milestone_by_id))

def ready_milestone_ids(frontier: MilestoneFrontierState, matched: dict[str, HarnessStageSettlement]) -> tuple[str, ...]:
    """返回当前 ready frontier 的稳定 milestone id 元组。

    入参：
        frontier: 当前 case 的 frontier 增量状态。
        matched: 当前已匹配 milestone 结算表。
    输出：
        尚未匹配的 ready milestone id。
    """
    return tuple((milestone.milestone_id for milestone in ready_milestones(frontier) if milestone.milestone_id not in matched))

def _insert_id_by_order(milestone_ids: list[str], milestone_id: str, order_by_id: dict[str, int]) -> None:
    if milestone_id in milestone_ids:
        return
    order = order_by_id.get(milestone_id, len(order_by_id))
    insert_index = len(milestone_ids)
    for index, current_id in enumerate(milestone_ids):
        if order < order_by_id.get(current_id, len(order_by_id)):
            insert_index = index
            break
    milestone_ids.insert(insert_index, milestone_id)

def _remove_id(milestone_ids: list[str], milestone_id: str) -> None:
    if milestone_id in milestone_ids:
        milestone_ids.remove(milestone_id)
