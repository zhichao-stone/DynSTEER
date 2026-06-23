from __future__ import annotations

from dataclasses import asdict, is_dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

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
)

_MISSING = object()


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _read_token(current: Any, token: str) -> Any:
    value = current
    rest = token
    while rest:
        if "[" in rest:
            name, tail = rest.split("[", 1)
            if name:
                if not isinstance(value, dict) or name not in value:
                    return _MISSING
                value = value[name]
            index_text, after = tail.split("]", 1)
            if not isinstance(value, list):
                return _MISSING
            try:
                index = int(index_text)
            except ValueError:
                return _MISSING
            if index < 0 or index >= len(value):
                return _MISSING
            value = value[index]
            rest = after.lstrip(".")
            if rest and "[" not in rest:
                if not isinstance(value, dict) or rest not in value:
                    return _MISSING
                value = value[rest]
                rest = ""
        else:
            if not isinstance(value, dict) or rest not in value:
                return _MISSING
            value = value[rest]
            rest = ""
    return value


def select_value(source: JsonValue, selector: str) -> JsonValue:
    """根据轻量 JSON selector 读取值。

    Args:
        source: JSON 值，通常为 dict 或 list。
        selector: 支持 `$`、`$.a.b`、`$.items[0]` 的 selector。

    Returns:
        命中的 JSON 值；未命中时返回 None。
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
        current = _read_token(current, token)
        if current is _MISSING:
            return None
    return current


def _json_subsumes(actual: JsonValue, expected: JsonValue) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual or not _json_subsumes(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(_json_subsumes(actual[index], value) for index, value in enumerate(expected))
    return actual == expected


def score_operator(actual: JsonValue, operator: Operator, expected: JsonValue) -> float:
    """计算单个 operator 的规则分数。

    Args:
        actual: 实际值。
        operator: 约束算子。
        expected: 期望值或参考值。

    Returns:
        `[0, 1]` 区间内的规则分数。
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
        return 1.0 if _json_subsumes(actual, expected) else 0.0
    if operator == Operator.FUZZY_MATCH:
        if actual is None or expected is None:
            return 0.0
        return _clamp(SequenceMatcher(None, str(actual), str(expected)).ratio())
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
    selected_namespace = namespace or "default"
    if selected_namespace in snapshot.namespaces:
        return snapshot.namespaces[selected_namespace]
    return snapshot.namespaces


def _step_to_source(step: TrajectoryStep) -> JsonValue:
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

    Args:
        constraint: 待评分约束。
        source: 当前值来源，可为快照、步骤或字典。
        reference_source: 参考值来源，用于状态变更类算子。

    Returns:
        单条约束评分。
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


def _boundary_step(trajectory: Trajectory, boundary: Boundary) -> Optional[TrajectoryStep]:
    for step in trajectory.steps:
        if step.index == boundary.step_index:
            return step
    return None


def _boundary_snapshot(boundary: Boundary, snapshots: list[StateSnapshot]) -> Optional[StateSnapshot]:
    if boundary.snapshot_id is not None:
        for snapshot in snapshots:
            if snapshot.snapshot_id == boundary.snapshot_id:
                return snapshot
    candidates = [snapshot for snapshot in snapshots if snapshot.after_step_index <= boundary.step_index]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.after_step_index)


def _source_for_constraint(
    constraint: Constraint,
    boundary: Boundary,
    trajectory: Trajectory,
    snapshots: list[StateSnapshot],
) -> object:
    if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
        return _boundary_snapshot(boundary, snapshots)
    if constraint.target == ConstraintTarget.METRIC:
        return trajectory.metrics
    return _boundary_step(trajectory, boundary)


def _reference_for_constraint(
    constraint: Constraint,
    snapshots: list[StateSnapshot],
) -> Optional[StateSnapshot]:
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

    Args:
        milestone: 待评估 milestone。
        boundary: 候选阶段边界。
        trajectory: Agent 轨迹。
        reference_snapshots: 可用于状态约束的快照列表。

    Returns:
        milestone 匹配分数和状态。
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
        score=_clamp(score),
        status=status,
        evidence=evidence,
        missing_ratio=missing_ratio,
        hard_constraints_all_pass=hard_pass,
        constraint_scores=constraint_scores,
    )
