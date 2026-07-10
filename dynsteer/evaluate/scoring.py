from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from difflib import SequenceMatcher
import math
from typing import Any, Mapping, Optional

from dynsteer.boundary import boundary_snapshot, boundary_step
from dynsteer.config import (
    DEFAULT_FOCUS,
    DEFAULT_TARGETS,
    TASK_TYPE_WEIGHTS,
    DynamicWeightConfig,
    ThresholdConfig,
    default_dynamic_weight_config,
)
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    Dimension,
    JsonObject,
    JsonValue,
    MISSING,
    Milestone,
    MilestoneScore,
    Operator,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.utils import clamp, json_subsumes, read_token

@dataclass(frozen=True)
class ScoringContext:

    task_case: TaskCase | None = None
    matched_boundaries: Mapping[str, Boundary] = field(default_factory=dict)
    matched_snapshots: Mapping[str, StateSnapshot] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


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
            source, reference = self._constraint_sources(constraint, boundary, trajectory, reference_snapshots)
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
        selected_namespace = namespace or "default"
        if selected_namespace in snapshot.namespaces:
            return snapshot.namespaces[selected_namespace]
        return snapshot.namespaces

    def _step_to_source(self, step: TrajectoryStep) -> JsonObject:
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

    def _constraint_sources(
        self,
        constraint: Constraint,
        boundary: Boundary,
        trajectory: Trajectory,
        snapshots: list[StateSnapshot],
    ) -> tuple[object, StateSnapshot | None]:
        if constraint.target == ConstraintTarget.STATE_SNAPSHOT:
            source: object = boundary_snapshot(boundary, snapshots)
        elif constraint.target == ConstraintTarget.METRIC:
            source = trajectory.metrics
        else:
            source = boundary_step(trajectory, boundary)
        if constraint.reference_milestone_id is None:
            return source, None
        for snapshot in snapshots:
            if snapshot.snapshot_id == constraint.reference_milestone_id:
                return source, snapshot
        return source, None


def get_effective_scorer(scorer: GeneralScorer | None) -> GeneralScorer:
    return scorer if scorer is not None else GeneralScorer()


def compute_uncertainty(
    top1_score: float,
    top2_score: float,
    missing_ratio: float,
    stage_score: float,
    evidence_conflict: bool,
    judge_uncertainty: float,
    thresholds: Optional[ThresholdConfig] = None,
) -> float:
    effective_thresholds = thresholds or ThresholdConfig()
    margin = max(top1_score - top2_score, 0.0)
    u_margin = 1.0 - clamp(margin / 0.3)
    u_missing = clamp(missing_ratio)
    threshold_distance = min(
        abs(stage_score - effective_thresholds.pass_threshold),
        abs(stage_score - effective_thresholds.warn_threshold),
        abs(stage_score - effective_thresholds.fail_threshold),
    )
    u_threshold = 1.0 - clamp(threshold_distance / effective_thresholds.threshold_margin)
    u_conflict = 1.0 if evidence_conflict else 0.0
    u_judge = clamp(judge_uncertainty)
    return clamp(
        0.30 * u_margin
        + 0.25 * u_missing
        + 0.20 * u_threshold
        + 0.15 * u_conflict
        + 0.10 * u_judge
    )


def overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 1.0 if minefield_score == 0 else 0.0
    raw = sum(stage.stage_score for stage in stage_reports) / len(stage_reports)
    return clamp(raw * (1.0 - clamp(minefield_score)))


def enrich_stage_result(
    interval: StageInterval,
    result: StageEvaluationResult,
    minefield_score: float,
    fatal_minefield: bool,
    thresholds: ThresholdConfig,
) -> StageEvaluationResult:
    if interval is None or result is None or thresholds is None:
        raise ValueError("enrich_stage_result 入参不能为空")
    top1 = interval.milestone_score.score if interval.milestone_score is not None else result.stage_score
    uncertainty = compute_uncertainty(
        top1_score=top1,
        top2_score=0.0,
        missing_ratio=result.required_fields_missing_ratio,
        stage_score=result.stage_score,
        evidence_conflict=False,
        judge_uncertainty=1.0 - result.judge_confidence,
        thresholds=thresholds,
    )
    result.uncertainty = uncertainty
    result.minefield_score = minefield_score
    result.fatal_minefield_score = minefield_score if fatal_minefield else 0.0
    return result


def first_failure_stage_id(stage_reports: list[StageEvaluationResult]) -> str | None:
    if stage_reports is None:
        raise ValueError("stage_reports 不能为空")
    return next(
        (
            stage.stage_id
            for stage in stage_reports
            if stage.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
        ),
        None,
    )


def normalize_weights(weights: dict[Dimension, float]) -> dict[Dimension, float]:
    if weights is None:
        raise ValueError("weights 不能为空")
    normalized_source = {dimension: max(float(weights.get(dimension, 0.0)), 0.0) for dimension in Dimension}
    total = sum(normalized_source.values())
    if total <= 0:
        return {dimension: 1 / len(Dimension) for dimension in Dimension}
    return {dimension: value / total for dimension, value in normalized_source.items()}


def select_initial_weights(task_case: TaskCase) -> dict[Dimension, float]:
    if task_case is None:
        raise ValueError("task_case 不能为空")
    task_types = task_case.task_types or []
    if not task_types:
        task_types = [next(iter(TASK_TYPE_WEIGHTS))]
    merged = {dimension: 0.0 for dimension in Dimension}
    valid_count = 0
    for task_type in task_types:
        weights = TASK_TYPE_WEIGHTS.get(task_type)
        if weights is None:
            continue
        valid_count += 1
        for dimension in Dimension:
            merged[dimension] += weights.get(dimension, 0.0)
    if valid_count == 0:
        return normalize_weights(TASK_TYPE_WEIGHTS[next(iter(TASK_TYPE_WEIGHTS))])
    return normalize_weights({dimension: value / valid_count for dimension, value in merged.items()})


def update_weights(
    current: dict[Dimension, float],
    scores: dict[Dimension, float],
    uncertainty: float,
    config: Optional[DynamicWeightConfig] = None,
) -> dict[Dimension, float]:
    if current is None or scores is None:
        raise ValueError("current 和 scores 不能为空")
    effective_config = config or default_dynamic_weight_config()
    next_weights: dict[Dimension, float] = {}
    for dimension in Dimension:
        base = max(float(current.get(dimension, 0.0)), 1e-9)
        score = float(scores.get(dimension, 0.0))
        target = effective_config.targets.get(dimension, DEFAULT_TARGETS[dimension])
        focus = effective_config.focus.get(dimension, DEFAULT_FOCUS[dimension])
        deficit = max(0.0, target - score)
        next_weights[dimension] = base * math.exp(
            effective_config.alpha * deficit + effective_config.beta * clamp(uncertainty) * focus
        )
    return normalize_weights(next_weights)
