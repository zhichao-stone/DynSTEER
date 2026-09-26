from dynsteer.model import Milestone, MilestoneGraph, MilestoneFrontierState

def initialize_milestone_frontier(graph: MilestoneGraph) -> MilestoneFrontierState:
    """Initializes the state of the milestone frontier runtime. Args: gram: the current case 's milestone DAG. Returns: the status of the frontier obtained only after scanning the graph during case initialization."""
    topology = graph.topology
    if topology is None:
        raise ValueError('Milestone drag not yet enrich')
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
    """Advances the frontier in place after the milestone match. Args: frontier: current case frontier increment. Matched_milestone_id: newly settled milestone id. Returns: No return value, function updates the frontier in place."""
    frontier.ready_ids.remove(matched_milestone_id)
    for successor_id in frontier.topology.successors_by_id[matched_milestone_id]:
        remaining = frontier.remaining_predecessor_count[successor_id] - 1
        frontier.remaining_predecessor_count[successor_id] = remaining
        if remaining == 0:
            _insert_id_by_order(frontier.ready_ids, successor_id, frontier.topology.order_by_id)

def ready_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """Returns the current ready milestone. Args: frontier: current case frontier increment. Returns: currently assessable milestone."""
    return tuple(frontier.topology.milestone_by_id[milestone_id] for milestone_id in frontier.ready_ids)

def blocked_candidate_milestones(frontier: MilestoneFrontierState) -> tuple[Milestone, ...]:
    """Returns the current blocked diagnostic candidate. Args: frontier: the current case frontier increment. Returns: the current low-cost predecessor cap diagnostic candidate."""
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
