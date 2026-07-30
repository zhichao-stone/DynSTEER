from collections.abc import Mapping
from dynsteer.model import Actor, Constraint, JsonObject, Milestone, MilestoneGraph
from dynsteer.utils import get_object
MILESTONE_MATCHING_METADATA_KEY = "milestone_matching"

def enrich_milestone_routes(graph: MilestoneGraph) -> MilestoneGraph:
    """为 milestone graph 预计算 milestone 匹配 route metadata。

    入参：
        graph: 适配后的通用 milestone graph。
    输出：
        已写入 constraint 与 milestone 级 `metadata.milestone_matching` 的 graph。
    """
    for milestone in graph.nodes:
        _enrich_milestone_route_groups(milestone)
    return graph

def _enrich_milestone_route_groups(milestone: Milestone) -> None:
    """聚合同一 milestone 下所有 constraint 的显式 route。"""
    route_groups: dict[tuple[str, str], list[str]] = {}
    for constraint in milestone.constraints:
        route, route_source = _constraint_route(constraint)
        _set_constraint_route_metadata(constraint, route, route_source)
        if route is None:
            continue
        route_groups.setdefault(route, []).append(constraint.constraint_id)
    if len(route_groups) > 1:
        raise ValueError(f"当前实现只支持单个 milestone 至多一个显式 route；milestone={milestone.milestone_id}, route_group_count={len(route_groups)}")
    matching = get_object(milestone.metadata, MILESTONE_MATCHING_METADATA_KEY, dict, {}, False)
    matching["route_groups"] = [
        {"route": {"sender": sender, "recipient": recipient}, "constraint_ids": list(constraint_ids)}
        for (sender, recipient), constraint_ids in route_groups.items()
    ]
    matching["route_group_count"] = len(route_groups)
    milestone.metadata[MILESTONE_MATCHING_METADATA_KEY] = matching

def _constraint_route(constraint: Constraint) -> tuple[tuple[str, str] | None, str | None]:
    """从 constraint 的固定声明中解析唯一 route。"""
    if isinstance(constraint.stage_goal_semantics, dict):
        route = _route_from_mapping(constraint.stage_goal_semantics)
        if route is not None:
            return (route, "stage_goal_semantics")
    rows = _expected_rows(constraint.expected)
    if not rows:
        return (None, None)
    routes: list[tuple[str, str]] = []
    for row in rows:
        route = _route_from_mapping(row)
        if route is not None and route not in routes:
            routes.append(route)
    if len(routes) > 1:
        raise ValueError(f"constraint 存在多个显式 route，当前暂不支持: {constraint.constraint_id}")
    if not routes:
        return (None, None)
    return (routes[0], "expected.rows")

def _set_constraint_route_metadata(constraint: Constraint, route: tuple[str, str] | None, route_source: str | None) -> None:
    """写入 constraint 级 milestone_matching route metadata。"""
    matching = get_object(constraint.metadata, MILESTONE_MATCHING_METADATA_KEY, dict, {}, False)
    matching["route"] = {"sender": route[0], "recipient": route[1]} if route is not None else None
    matching["route_source"] = route_source
    constraint.metadata[MILESTONE_MATCHING_METADATA_KEY] = matching

def _expected_rows(expected: dict) -> list[JsonObject]:
    """读取 expected.rows 中可用于 route 解析的行数据。"""
    if not isinstance(expected, dict):
        return []
    rows = expected.get("rows")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]

def _route_from_mapping(data: Mapping[str, object]) -> tuple[str, str] | None:
    """从语义对象或 expected row 中读取 sender/recipient route。"""
    sender = _actor_from_aliases(data, ("sender", "source", "initiator"))
    recipient = _actor_from_aliases(data, ("recipient", "target", "receiver"))
    if sender is None or recipient is None:
        return None
    return (sender.name, recipient.name)

def _actor_from_aliases(data: Mapping[str, object], aliases: tuple[str, ...]) -> Actor | None:
    """按字段别名读取并规范化 Actor。"""
    for alias in aliases:
        actor = _normalize_actor(data.get(alias))
        if actor is not None:
            return actor
    return None

def _normalize_actor(value: object) -> Actor | None:
    """将外部 actor 名称统一为内部 Actor 枚举。"""
    if value is None:
        return None
    aliases = {
        "system": Actor.SYSTEM,
        "user": Actor.USER,
        "agent": Actor.AGENT,
        "environment": Actor.ENVIRONMENT,
        "execution_environment": Actor.ENVIRONMENT,
        "evaluator": Actor.EVALUATOR,
    }
    return aliases.get(str(value).strip().lower())
