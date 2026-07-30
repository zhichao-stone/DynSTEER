from dataclasses import asdict, is_dataclass
from difflib import SequenceMatcher
from typing import Any
from dynsteer.evaluate.matching.boundary import boundary_snapshot, boundary_step
from dynsteer.graph import START_NODE_ID
from dynsteer.model import Boundary, Constraint, ConstraintScore, ConstraintTarget, Dimension, EventType, JsonObject, JsonValue, MISSING, Milestone, MilestoneScore, Operator, ScoringContext, StageEvaluationResult, StageStatus, StateSnapshot, Trajectory, TrajectoryStep
from dynsteer.utils import clamp, json_subsumes, read_token

class GeneralScorer:

    def select_value(self, source: JsonValue, selector: str) -> JsonValue:
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
            return 1.0 if actual is not None and expected is not None and (actual != expected) else 0.0
        if operator == Operator.REMOVED:
            return 1.0 if actual is None and expected is not None else 0.0
        if operator == Operator.UNCHANGED_SINCE:
            return 1.0 if actual == expected else 0.0
        return 0.0

    def score_custom_constraint(self, constraint: Constraint, source: object, reference_source: object | None, actual: JsonValue, reference_value: JsonValue, context: ScoringContext | None=None) -> ConstraintScore:
        evidence = f"约束 {constraint.constraint_id} 使用 Operator.CUSTOM，但当前评分器 {self.__class__.__name__} 不支持 evaluator_hint={constraint.evaluator_hint}"
        return ConstraintScore(constraint_id=constraint.constraint_id, score=0.0, missing=actual is None, evidence=[evidence], actual=actual)

    def score_constraint(self, constraint: Constraint, source: object, reference_source: object | None=None, context: ScoringContext | None=None) -> ConstraintScore:
        current_source = self._resolve_source(constraint, source)
        actual = self.select_value(current_source, constraint.selector)
        missing = actual is None
        reference_value = constraint.expected
        if constraint.operator in {Operator.ADDED, Operator.UPDATED, Operator.REMOVED, Operator.UNCHANGED_SINCE}:
            if constraint.reference_milestone_id is not None and reference_source is None:
                return ConstraintScore(constraint_id=constraint.constraint_id, score=0.0, missing=True, evidence=[f"reference milestone 未命中: {constraint.reference_milestone_id}"], actual=actual)
            reference_data = self._resolve_source(constraint, reference_source)
            reference_value = self.select_value(reference_data, constraint.selector)
        if constraint.operator == Operator.CUSTOM:
            return self.score_custom_constraint(constraint, source, reference_source, actual, reference_value, context=context)
        score = 0.0 if missing and constraint.operator != Operator.REMOVED else self.score_operator(actual, constraint.operator, reference_value)
        evidence = [f"约束 {constraint.constraint_id} 得分 {score:.3f}"]
        if missing:
            evidence.append(f"selector 未命中: {constraint.selector}")
        return ConstraintScore(constraint_id=constraint.constraint_id, score=score, missing=missing, evidence=evidence, actual=actual)

    def score_milestone(self, milestone: Milestone, boundary: Boundary, trajectory: Trajectory, reference_snapshots: list[StateSnapshot], context: ScoringContext | None=None) -> MilestoneScore:
        if len(milestone.constraints) == 0:
            return MilestoneScore(milestone_id=milestone.milestone_id, boundary_id=boundary.boundary_id, score=0.0, status=StageStatus.INVALID, evidence=["milestone 缺少 constraints"], missing_ratio=1.0, hard_constraints_all_pass=False)
        constraint_scores: list[ConstraintScore] = []
        weighted_sum = 0.0
        weight_sum = 0.0
        hard_pass = True
        for constraint in milestone.constraints:
            source, reference = self.constraint_sources(constraint, boundary, trajectory, reference_snapshots, context=context)
            result = self.score_constraint(constraint, source, reference, context=context)
            constraint_scores.append(result)
            weight = max(float(constraint.weight), 0.0)
            weighted_sum += result.score * weight
            weight_sum += weight
            if constraint.hard and result.score < constraint.threshold:
                hard_pass = False
        missing_count = sum((1 for item in constraint_scores if item.missing))
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
        return MilestoneScore(milestone_id=milestone.milestone_id, boundary_id=boundary.boundary_id, score=clamp(score), status=status, evidence=evidence, missing_ratio=missing_ratio, hard_constraints_all_pass=hard_pass, constraint_scores=constraint_scores)

    def _resolve_source(self, constraint: Constraint, source: object | None) -> JsonValue:
        if source is None:
            return None
        if isinstance(source, StateSnapshot):
            selected_namespace = constraint.namespace or "default"
            return source.namespaces.get(selected_namespace, source.namespaces)
        if isinstance(source, TrajectoryStep):
            data: dict[str, JsonValue] = {
                "step_id": source.step_id,
                "index": source.index,
                "actor": source.actor.value,
                "recipient": source.recipient.value if source.recipient is not None else None,
                "event_type": source.event_type.value,
                "timestamp": source.timestamp,
                "content": source.content,
                "state_delta_refs": list(source.state_delta_refs),
            }
            if source.tool_call is not None:
                data["tool_call"] = asdict(source.tool_call)
            if source.tool_result is not None:
                data["tool_result"] = asdict(source.tool_result)
            data.update(source.raw)
            if constraint.target == ConstraintTarget.TOOL_CALL:
                return data.get("tool_call")
            if constraint.target == ConstraintTarget.TOOL_RESULT:
                return data.get("tool_result")
            return data
        if is_dataclass(source):
            return asdict(source)
        if isinstance(source, dict):
            return source
        return None

    def constraint_sources(self, constraint: Constraint, boundary: Boundary, trajectory: Trajectory, snapshots: list[StateSnapshot], context: ScoringContext | None=None) -> tuple[object, StateSnapshot | None]:
        """按约束目标解析 boundary 上的评分 source 与 reference。"""
        if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
            source: object = boundary_snapshot(boundary, snapshots)
        elif constraint.target == ConstraintTarget.METRIC:
            source = trajectory.metrics
        elif constraint.target in {ConstraintTarget.TOOL_CALL, ConstraintTarget.TOOL_RESULT}:
            source = self._interval_step_source(constraint, boundary, trajectory, context)
        else:
            source = boundary_step(trajectory, boundary)
        if constraint.reference_milestone_id is None:
            return (source, None)
        if context is not None:
            snapshot = context.matched_snapshots.get(constraint.reference_milestone_id)
            if snapshot is not None:
                return (source, snapshot)
        for snapshot in snapshots:
            if snapshot.snapshot_id == constraint.reference_milestone_id:
                return (source, snapshot)
        return (source, None)

    def _interval_step_source(self, constraint: Constraint, boundary: Boundary, trajectory: Trajectory, context: ScoringContext | None) -> TrajectoryStep | None:
        """在当前 milestone 阶段区间中寻找最近的目标 step。"""
        start_index = self._stage_start_step_index(constraint, trajectory, context)
        if boundary.step_index <= start_index:
            return boundary_step(trajectory, boundary)
        for step in reversed(trajectory.get_interval(start_index, boundary.step_index)):
            if constraint.target == ConstraintTarget.TOOL_CALL and (step.tool_call is not None or step.event_type == EventType.TOOL_CALL):
                return step
            if constraint.target == ConstraintTarget.TOOL_RESULT and (step.tool_result is not None or step.event_type == EventType.TOOL_RESULT):
                return step
        return boundary_step(trajectory, boundary)

    def _stage_start_step_index(self, constraint: Constraint, trajectory: Trajectory, context: ScoringContext | None) -> int:
        """根据 constraint 所属 milestone 找到当前阶段左边界。"""
        if trajectory.latest_step_index is None:
            return -1
        if context is None or context.task_case is None or context.task_case.milestone_graph is None:
            return trajectory.first_step_index - 1
        for milestone in context.task_case.milestone_graph.nodes:
            if not any((item.constraint_id == constraint.constraint_id for item in milestone.constraints)):
                continue
            anchor_id = milestone.stage_anchor_predecessor_id
            if anchor_id == START_NODE_ID:
                return trajectory.first_step_index - 1
            if isinstance(anchor_id, str) and anchor_id in context.matched_boundaries:
                return context.matched_boundaries[anchor_id].step_index
            return trajectory.first_step_index - 1
        return trajectory.first_step_index - 1

def get_effective_scorer(scorer: GeneralScorer | None) -> GeneralScorer:
    return scorer if scorer is not None else GeneralScorer()

def stage_score_from_dimensions(dimension_scores: dict[Dimension, float], weights: dict[Dimension, float]) -> float:
    """根据维度分数和动态权重计算阶段综合分数。"""
    if not dimension_scores:
        return 0.0
    weighted_score = 0.0
    total_weight = 0.0
    present_dimensions = [dimension for dimension in Dimension if dimension in dimension_scores]
    for dimension in present_dimensions:
        score = dimension_scores.get(dimension)
        weight = float(weights.get(dimension, 0.0))
        if weight > 0.0:
            weighted_score += clamp(float(score)) * weight
            total_weight += weight
    if total_weight == 0.0:
        return sum((clamp(float(dimension_scores[dimension])) for dimension in present_dimensions)) / len(present_dimensions)
    return weighted_score / total_weight

def overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 0.0
    raw = sum((stage.stage_score for stage in stage_reports)) / len(stage_reports)
    return clamp(raw * (1.0 - clamp(minefield_score)))

def minefield_penalty_score(matches: list[JsonObject]) -> float:
    """根据 minefield 命中记录计算最终总分扣罚比例。"""
    max_penalty = 0.0
    for match in matches:
        if not isinstance(match, dict):
            continue
        raw_score = match.get("score", 0.0)
        score = float(raw_score) if isinstance(raw_score, int | float) else 0.0
        penalty = match.get("penalty")
        if isinstance(penalty, dict) and penalty.get("mode") == "fixed":
            raw_value = penalty.get("value", 0.0)
            value = float(raw_value) if isinstance(raw_value, int | float) else 0.0
            max_penalty = max(max_penalty, score * value)
        else:
            max_penalty = max(max_penalty, score)
    return clamp(max_penalty)
