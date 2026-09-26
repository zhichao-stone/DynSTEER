from collections.abc import Mapping
from dynsteer.model import Actor, Constraint, JsonObject, Milestone, MilestoneGraph
from dynsteer.utils import normalize_actor

def enrich_milestone_routes(graph: MilestoneGraph) -> MilestoneGraph:
    """Expected to match milestone with milestone metadata. Args: gram: generic milestone graph after appropriate. Returns: entered constraint with milestone level`metadata.milestone_matching`The graph."""
    for milestone in graph.nodes:
        milestone.matching_route = _milestone_route(milestone)
    return graph

def _milestone_route(milestone: Milestone) -> tuple[Actor, Actor] | None:
    """Aggregation of the same milestone under all constraint features."""
    routes: list[tuple[Actor, Actor]] = []
    for constraint in milestone.constraints:
        route = _constraint_route(constraint)
        if route is not None and route not in routes:
            routes.append(route)
    if len(routes) > 1:
        raise ValueError(f"Current realization supports only single milestone up to one visible route:{milestone.milestone_id}")
    return routes[0] if routes else None

def _constraint_route(constraint: Constraint) -> tuple[Actor, Actor] | None:
    """parsing the only root from the fixed statement of constraint."""
    if isinstance(constraint.stage_goal_semantics, dict):
        route = _route_from_mapping(constraint.stage_goal_semantics)
        if route is not None:
            return route
    rows = _expected_rows(constraint.expected)
    if not rows:
        return None
    routes: list[tuple[Actor, Actor]] = []
    for row in rows:
        route = _route_from_mapping(row)
        if route is not None and route not in routes:
            routes.append(route)
    if len(routes) > 1:
        raise ValueError(f"Constraint has multiple visible roots, which are not supported at this time:{constraint.constraint_id}")
    if not routes:
        return None
    return routes[0]

def _expected_rows(expected: dict) -> list[JsonObject]:
    """Reads line data from expected.rows that can be used for root resolution."""
    if not isinstance(expected, dict):
        return []
    rows = expected.get("rows")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]

def _route_from_mapping(data: Mapping[str, object]) -> tuple[Actor, Actor] | None:
    """Reads the Sender/ index root from a semantic object or expected root."""
    sender = _actor_from_aliases(data, ("sender", "source", "initiator"))
    recipient = _actor_from_aliases(data, ("recipient", "target", "receiver"))
    if sender is None or recipient is None:
        return None
    return sender, recipient

def _actor_from_aliases(data: Mapping[str, object], aliases: tuple[str, ...]) -> Actor | None:
    """Read and normalize an Actor from field aliases."""
    for alias in aliases:
        actor = normalize_actor(data.get(alias))
        if actor is not None:
            return actor
    return None
