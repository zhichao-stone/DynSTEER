from collections.abc import Mapping
from dynsteer.model import Actor, Constraint, JsonObject, Milestone, MilestoneGraph
from dynsteer.utils import normalize_actor

def enrich_milestone_routes(graph: MilestoneGraph) -> MilestoneGraph:
    """为 milestone graph 预计算 milestone 匹配 route metadata。

    入参：
        graph: 适配后的通用 milestone graph。
    输出：
        已写入 constraint 与 milestone 级 `metadata.milestone_matching` 的 graph。
    """
    for milestone in graph.nodes:
        milestone.matching_route = _milestone_route(milestone)
    return graph

def _milestone_route(milestone: Milestone) -> tuple[Actor, Actor] | None:
    """聚合同一 milestone 下所有 constraint 的显式 route。"""
    routes: list[tuple[Actor, Actor]] = []
    for constraint in milestone.constraints:
        route = _constraint_route(constraint)
        if route is not None and route not in routes:
            routes.append(route)
    if len(routes) > 1:
        raise ValueError(f"当前实现只支持单个 milestone 至多一个显式 route：{milestone.milestone_id}")
    return routes[0] if routes else None

def _constraint_route(constraint: Constraint) -> tuple[Actor, Actor] | None:
    """从 constraint 的固定声明中解析唯一 route。"""
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
        raise ValueError(f"constraint 存在多个显式 route，当前暂不支持: {constraint.constraint_id}")
    if not routes:
        return None
    return routes[0]

def _expected_rows(expected: dict) -> list[JsonObject]:
    """读取 expected.rows 中可用于 route 解析的行数据。"""
    if not isinstance(expected, dict):
        return []
    rows = expected.get("rows")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]

def _route_from_mapping(data: Mapping[str, object]) -> tuple[Actor, Actor] | None:
    """从语义对象或 expected row 中读取 sender/recipient route。"""
    sender = _actor_from_aliases(data, ("sender", "source", "initiator"))
    recipient = _actor_from_aliases(data, ("recipient", "target", "receiver"))
    if sender is None or recipient is None:
        return None
    return sender, recipient

def _actor_from_aliases(data: Mapping[str, object], aliases: tuple[str, ...]) -> Actor | None:
    """按字段别名读取并规范化 Actor。"""
    for alias in aliases:
        actor = normalize_actor(data.get(alias))
        if actor is not None:
            return actor
    return None
