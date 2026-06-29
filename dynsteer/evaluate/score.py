from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping

from dynsteer.boundary import boundary_snapshot, boundary_step
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    JsonObject,
    JsonValue,
    MISSING,
    Milestone,
    MilestoneScore,
    Operator,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.utils import clamp, json_subsumes, read_token


@dataclass(frozen=True)
class ScoringContext:
    """评分上下文，用于传递运行期已命中节点和 benchmark 扩展信息。

    Args:
        task_case: 当前任务定义；离线评估或单元测试中可为空。
        matched_boundaries: 已命中 milestone 到 boundary 的映射。
        matched_snapshots: 已命中 milestone 到状态快照的映射。
        metadata: benchmark 扩展上下文。
    """

    task_case: TaskCase | None = None
    matched_boundaries: Mapping[str, Boundary] = field(default_factory=dict)
    matched_snapshots: Mapping[str, StateSnapshot] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


class GeneralScorer:
    """DynSTEER 通用 milestone / minefield 评分器。"""

    def select_value(self, source: JsonValue, selector: str) -> JsonValue:
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
            current = read_token(current, token)
            if current is MISSING:
                return None
        return current

    def score_operator(self, actual: JsonValue, operator: Operator, expected: JsonValue) -> float:
        """计算单个通用 operator 的规则分数。

        Args:
            actual: 实际值。
            operator: 约束算子。
            expected: 期望值或参考值。

        Returns:
            `[0, 1]` 区间内的规则分数。

        Raises:
            ValueError: 直接传入 `Operator.CUSTOM` 时抛出。
        """
        if operator == Operator.CUSTOM:
            raise ValueError("Operator.CUSTOM 必须由 score_custom_constraint() 或 benchmark scorer 处理")
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

    def score_custom_constraint(
        self,
        constraint: Constraint,
        source: object,
        reference_source: object | None,
        actual: JsonValue,
        reference_value: JsonValue,
        context: ScoringContext | None = None,
    ) -> ConstraintScore:
        """处理通用评分器不支持的 CUSTOM 约束。

        Args:
            constraint: 当前约束定义。
            source: 当前值来源。
            reference_source: 参考值来源。
            actual: 当前 selector 命中的实际值。
            reference_value: 当前约束的参考值。
            context: 可选评分上下文。

        Returns:
            带 evidence 的显式不支持评分结果。
        """
        return ConstraintScore(
            constraint_id=constraint.constraint_id,
            score=0.0,
            missing=actual is None,
            evidence=[
                (
                    f"约束 {constraint.constraint_id} 使用 Operator.CUSTOM，"
                    f"但当前评分器 {self.__class__.__name__} 不支持 evaluator_hint={constraint.evaluator_hint}"
                )
            ],
            actual=actual,
        )

    def score_constraint(
        self,
        constraint: Constraint,
        source: object,
        reference_source: object | None = None,
        context: ScoringContext | None = None,
    ) -> ConstraintScore:
        """计算单条约束在给定来源上的得分。

        Args:
            constraint: 待评分约束。
            source: 当前值来源，可为快照、步骤或字典。
            reference_source: 参考值来源，用于状态变更类算子。
            context: 可选评分上下文。

        Returns:
            单条约束评分。
        """
        if constraint is None:
            raise ValueError("constraint 不能为空")
        current_source = self._resolve_source(constraint, source)
        actual = self.select_value(current_source, constraint.selector)
        missing = actual is None
        reference_value = constraint.expected
        if constraint.operator in {
            Operator.ADDED,
            Operator.UPDATED,
            Operator.REMOVED,
            Operator.UNCHANGED_SINCE,
        }:
            reference_data = self._resolve_source(constraint, reference_source)
            reference_value = self.select_value(reference_data, constraint.selector)
        if constraint.operator == Operator.CUSTOM:
            return self.score_custom_constraint(
                constraint,
                source,
                reference_source,
                actual,
                reference_value,
                context=context,
            )
        score = 0.0 if missing and constraint.operator != Operator.REMOVED else self.score_operator(
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

    def score_milestone(
        self,
        milestone: Milestone,
        boundary: Boundary,
        trajectory: Trajectory,
        reference_snapshots: list[StateSnapshot],
        context: ScoringContext | None = None,
    ) -> MilestoneScore:
        """计算 milestone 在候选 boundary 上的完成度。

        Args:
            milestone: 待评估 milestone。
            boundary: 候选阶段边界。
            trajectory: Agent 轨迹。
            reference_snapshots: 可用于状态约束的快照列表。
            context: 可选评分上下文。

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
            source = self._source_for_constraint(constraint, boundary, trajectory, reference_snapshots)
            reference = self._reference_for_constraint(constraint, reference_snapshots)
            result = self.score_constraint(constraint, source, reference, context=context)
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

    def _snapshot_namespace(self, snapshot: StateSnapshot, namespace: str | None) -> JsonValue:
        """从状态快照中读取指定命名空间的数据。"""
        selected_namespace = namespace or "default"
        if selected_namespace in snapshot.namespaces:
            return snapshot.namespaces[selected_namespace]
        return snapshot.namespaces

    def _step_to_source(self, step: TrajectoryStep) -> JsonObject:
        """将轨迹步骤转换为 selector 可读取的 JSON 对象。"""
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

    def _resolve_source(self, constraint: Constraint, source: object | None) -> JsonValue:
        """根据约束目标把输入来源转换为 JSON 评分对象。"""
        if source is None:
            return None
        if isinstance(source, StateSnapshot):
            return self._snapshot_namespace(source, constraint.namespace)
        if isinstance(source, TrajectoryStep):
            data = self._step_to_source(source)
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

    def _source_for_constraint(
        self,
        constraint: Constraint,
        boundary: Boundary,
        trajectory: Trajectory,
        snapshots: list[StateSnapshot],
    ) -> object:
        """查找 milestone 约束在候选边界上的当前值来源。"""
        if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
            return boundary_snapshot(boundary, snapshots)
        if constraint.target == ConstraintTarget.METRIC:
            return trajectory.metrics
        return boundary_step(trajectory, boundary)

    def _reference_for_constraint(
        self,
        constraint: Constraint,
        snapshots: list[StateSnapshot],
    ) -> StateSnapshot | None:
        """查找状态变更类约束使用的参考快照。"""
        if constraint.reference_milestone_id is None:
            return None
        for snapshot in snapshots:
            if snapshot.snapshot_id == constraint.reference_milestone_id:
                return snapshot
        return None


def get_effective_scorer(scorer: GeneralScorer | None) -> GeneralScorer:
    """返回有效评分器，未传入时使用通用评分器。"""
    return scorer if scorer is not None else GeneralScorer()