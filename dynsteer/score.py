from __future__ import annotations

from dataclasses import asdict, is_dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

from dynsteer.boundary import boundary_snapshot, boundary_step
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    JsonValue,
    Milestone,
    MilestoneScore,
    Operator,
    StageStatus,
    StateSnapshot,
    Trajectory,
    TrajectoryStep,
    MISSING,
)
from dynsteer.utils import clamp, read_token, json_subsumes


def select_value(source: JsonValue, selector: str) -> JsonValue:
    """根据轻量 JSON selector 读取值。

    selector 只支持当前评分模块需要的轻量路径语法，不尝试实现完整 JSONPath。
    未命中时统一返回 None，便于评分层把缺失值计入 missing。

    Args:
        source: JSON 值，通常为 dict 或 list。
        selector: 支持 `$`、`$.a.b`、`$.items[0]` 的 selector。

    Returns:
        命中的 JSON 值；未命中时返回 None。

    Example:
        >>> select_value({"items": [{"name": "done"}]}, "$.items[0].name")
        'done'
    """
    if selector is None or selector == "" or source is None:
        return None
    if selector == "$":
        return source
    if not selector.startswith("$."):
        return None
    current: Any = source
    for token in selector[2:].split("."):
        if token == "":
            return None
        current = read_token(current, token)
        if current is MISSING:
            return None
    return current


def score_operator(actual: JsonValue, operator: Operator, expected: JsonValue) -> float:
    """计算单个 operator 的规则分数。

    该函数只处理无上下文的值级比较；涉及 selector、reference snapshot 或
    milestone 聚合的逻辑由上层评分函数负责。

    Args:
        actual: 实际值。
        operator: 约束算子。
        expected: 期望值或参考值。

    Returns:
        `[0, 1]` 区间内的规则分数。

    Example:
        >>> score_operator("任务完成", Operator.CONTAINS, "完成")
        1.0
    """
    if operator == Operator.EQUALS:
        return 1.0 if actual == expected else 0.0
    if operator == Operator.CONTAINS:
        if actual is None or expected is None:
            return 0.0
        if isinstance(actual, str):
            return 1.0 if str(expected) in actual else 0.0
        if isinstance(actual, list):
            return 1.0 if expected in actual else 0.0
        if isinstance(actual, dict) and isinstance(expected, str):
            return 1.0 if expected in actual else 0.0
        return 0.0
    if operator == Operator.ONE_OF:
        return 1.0 if isinstance(expected, list) and actual in expected else 0.0
    if operator == Operator.JSON_SUBSUMES:
        return 1.0 if json_subsumes(actual, expected) else 0.0
    if operator == Operator.FUZZY_MATCH:
        if actual is None or expected is None:
            return 0.0
        return clamp(SequenceMatcher(None, str(actual), str(expected)).ratio())
    if operator == Operator.ADDED:
        return 1.0 if actual is not None and expected is None else 0.0
    if operator == Operator.UPDATED:
        return 1.0 if actual is not None and expected is not None and actual != expected else 0.0
    if operator == Operator.REMOVED:
        return 1.0 if actual is None and expected is not None else 0.0
    if operator == Operator.UNCHANGED_SINCE:
        return 1.0 if actual == expected else 0.0
    return 0.0


def _snapshot_namespace(snapshot: StateSnapshot, namespace: Optional[str]) -> JsonValue:
    """从状态快照中读取指定命名空间的数据。

    Args:
        snapshot: 状态快照。
        namespace: 目标命名空间；为空时使用 default。

    Returns:
        命中命名空间的数据；未命中时返回完整 namespaces 字典。
    """
    selected_namespace = namespace or "default"
    if selected_namespace in snapshot.namespaces:
        return snapshot.namespaces[selected_namespace]
    return snapshot.namespaces


def _step_to_source(step: TrajectoryStep) -> JsonValue:
    """将轨迹步骤转换为评分 selector 可读取的 JSON 对象。

    Args:
        step: 轨迹步骤。

    Returns:
        包含基础字段、工具调用结果和 raw 扩展字段的 JSON 对象。
    """
    data: dict[str, JsonValue] = {
        "step_id": step.step_id,
        "index": step.index,
        "actor": step.actor.value,
        "event_type": step.event_type.value,
        "timestamp": step.timestamp,
        "content": step.content,
        "state_delta_refs": list(step.state_delta_refs),
    }
    if step.tool_call is not None:
        data["tool_call"] = asdict(step.tool_call)
    if step.tool_result is not None:
        data["tool_result"] = asdict(step.tool_result)
    data.update(step.raw)
    return data


def _resolve_source(constraint: Constraint, source: object) -> JsonValue:
    """根据约束目标把输入来源转换为 JSON 评分对象。

    Args:
        constraint: 当前评分约束，用于判断工具调用、工具结果或命名空间来源。
        source: 原始来源，可为 StateSnapshot、TrajectoryStep、dataclass 或 dict。

    Returns:
        可供 selector 读取的 JSON 值；不支持的来源返回 None。
    """
    if source is None:
        return None
    if isinstance(source, StateSnapshot):
        return _snapshot_namespace(source, constraint.namespace)
    if isinstance(source, TrajectoryStep):
        data = _step_to_source(source)
        if constraint.target == ConstraintTarget.TOOL_CALL:
            return data.get("tool_call")
        if constraint.target == ConstraintTarget.TOOL_RESULT:
            return data.get("tool_result")
        return data
    if is_dataclass(source):
        return asdict(source)  # type: ignore[arg-type]
    if isinstance(source, dict):
        return source
    return None


def score_constraint(
    constraint: Constraint,
    source: object,
    reference_source: Optional[object],
) -> ConstraintScore:
    """计算单条约束在给定来源上的得分。

    该函数负责解析 selector、选择状态变更类算子的参考值，并生成单条约束的
    evidence。它不会决定 milestone 是否通过，聚合逻辑由 score_milestone 完成。

    Args:
        constraint: 待评分约束。
        source: 当前值来源，可为快照、步骤或字典。
        reference_source: 参考值来源，用于状态变更类算子。

    Returns:
        单条约束评分。

    Example:
        >>> constraint = Constraint("c1", ConstraintTarget.STEP, "$.content", Operator.CONTAINS, "完成")
        >>> step = TrajectoryStep("s1", 1, Actor.AGENT, EventType.MESSAGE, content="任务完成")
        >>> score_constraint(constraint, step, None).score
        1.0
    """
    if constraint is None:
        raise ValueError("constraint 不能为空")
    current_source = _resolve_source(constraint, source)
    actual = select_value(current_source, constraint.selector)
    missing = actual is None
    reference_value = constraint.expected
    if constraint.operator in {
        Operator.ADDED,
        Operator.UPDATED,
        Operator.REMOVED,
        Operator.UNCHANGED_SINCE,
    }:
        reference_data = _resolve_source(constraint, reference_source)
        reference_value = select_value(reference_data, constraint.selector)
    score = 0.0 if missing and constraint.operator != Operator.REMOVED else score_operator(
        actual,
        constraint.operator,
        reference_value,
    )
    evidence = [
        f"约束 {constraint.constraint_id} 得分 {score:.3f}",
    ]
    if missing:
        evidence.append(f"selector 未命中: {constraint.selector}")
    return ConstraintScore(
        constraint_id=constraint.constraint_id,
        score=score,
        missing=missing,
        evidence=evidence,
        actual=actual,
    )


def _source_for_constraint(
    constraint: Constraint,
    boundary: Boundary,
    trajectory: Trajectory,
    snapshots: list[StateSnapshot],
) -> object:
    """查找 milestone 约束在候选边界上的当前值来源。

    Args:
        constraint: 待评分约束。
        boundary: 候选阶段边界。
        trajectory: Agent 执行轨迹。
        snapshots: 可用状态快照列表。

    Returns:
        与约束 target 匹配的当前值来源。
    """
    if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
        return boundary_snapshot(boundary, snapshots)
    if constraint.target == ConstraintTarget.METRIC:
        return trajectory.metrics
    return boundary_step(trajectory, boundary)


def _reference_for_constraint(
    constraint: Constraint,
    snapshots: list[StateSnapshot],
) -> Optional[StateSnapshot]:
    """查找状态变更类约束使用的参考快照。

    Args:
        constraint: 待评分约束。
        snapshots: 可用状态快照列表。

    Returns:
        reference_milestone_id 对应的快照；未配置或未命中时返回 None。
    """
    if constraint.reference_milestone_id is None:
        return None
    for snapshot in snapshots:
        if snapshot.snapshot_id == constraint.reference_milestone_id:
            return snapshot
    return None


def score_milestone(
    milestone: Milestone,
    boundary: Boundary,
    trajectory: Trajectory,
    reference_snapshots: list[StateSnapshot],
) -> MilestoneScore:
    """计算 milestone 在候选边界上的完成度。

    函数会在候选边界上逐条计算约束分数，按权重聚合为 milestone 得分，并根据硬约束、
    pass_threshold 和缺失比例生成最终状态。

    Args:
        milestone: 待评估 milestone。
        boundary: 候选阶段边界。
        trajectory: Agent 轨迹。
        reference_snapshots: 可用于状态约束的快照列表。

    Returns:
        milestone 匹配分数和状态。

    Example:
        >>> boundary = Boundary("b0", step_index=1, snapshot_id=None, reason="agent_message")
        >>> score_milestone(milestone, boundary, trajectory, trajectory.snapshots).status
        <StageStatus.PASS: 'pass'>
    """
    if milestone is None or boundary is None or trajectory is None:
        raise ValueError("milestone、boundary、trajectory 均不能为空")
    if len(milestone.constraints) == 0:
        return MilestoneScore(
            milestone_id=milestone.milestone_id,
            boundary_id=boundary.boundary_id,
            score=0.0,
            status=StageStatus.INVALID,
            evidence=["milestone 缺少 constraints"],
            missing_ratio=1.0,
            hard_constraints_all_pass=False,
        )

    constraint_scores: list[ConstraintScore] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    hard_pass = True
    for constraint in milestone.constraints:
        source = _source_for_constraint(constraint, boundary, trajectory, reference_snapshots)
        reference = _reference_for_constraint(constraint, reference_snapshots)
        result = score_constraint(constraint, source, reference)
        constraint_scores.append(result)
        weight = max(float(constraint.weight), 0.0)
        weighted_sum += result.score * weight
        weight_sum += weight
        if constraint.hard and result.score < constraint.threshold:
            hard_pass = False

    missing_count = sum(1 for item in constraint_scores if item.missing)
    missing_ratio = missing_count / len(constraint_scores)
    score = 0.0 if not hard_pass else weighted_sum / weight_sum if weight_sum > 0 else 0.0
    threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8
    if not hard_pass:
        status = StageStatus.FAIL
    elif score >= threshold:
        status = StageStatus.PASS
    elif score >= 0.6:
        status = StageStatus.WARN
    else:
        status = StageStatus.FAIL
    evidence = [line for item in constraint_scores for line in item.evidence]
    return MilestoneScore(
        milestone_id=milestone.milestone_id,
        boundary_id=boundary.boundary_id,
        score=clamp(score),
        status=status,
        evidence=evidence,
        missing_ratio=missing_ratio,
        hard_constraints_all_pass=hard_pass,
        constraint_scores=constraint_scores,
    )
