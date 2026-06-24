from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

from dynsteer.boundary import generate_candidate_boundaries
from dynsteer.config import (
    DEFAULT_FOCUS,
    DEFAULT_TARGETS,
    DIMENSION_ORDER,
    TASK_TYPE_WEIGHTS,
    DynamicWeightConfig,
    MatchConfig,
    ThresholdConfig,
    default_dynamic_weight_config,
)
from dynsteer.judge import Judge, LocalJudge
from dynsteer.match import match_milestones, validate_milestone_graph
from dynsteer.model import (
    Boundary,
    ConstraintTarget,
    Dimension,
    EvaluationDecision,
    EvaluationLevel,
    JsonObject,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryEvaluationReport,
)
from dynsteer.score import score_constraint, score_milestone
from dynsteer.stage import build_stage_intervals


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def normalize_weights(weights: dict[Dimension, float]) -> dict[Dimension, float]:
    """归一化维度权重。

    Args:
        weights: 原始维度权重。

    Returns:
        覆盖全部维度且和为 1 的权重。
    """
    if weights is None:
        raise ValueError("weights 不能为空")
    normalized_source = {dimension: max(float(weights.get(dimension, 0.0)), 0.0) for dimension in Dimension}
    total = sum(normalized_source.values())
    if total <= 0:
        return {dimension: 1 / len(Dimension) for dimension in Dimension}
    return {dimension: value / total for dimension, value in normalized_source.items()}


def select_initial_weights(task_case: TaskCase) -> dict[Dimension, float]:
    """根据任务类型选择初始维度权重。

    Args:
        task_case: 任务定义。

    Returns:
        初始维度权重。
    """
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
    """根据低分维度与不确定性更新下一阶段权重。

    Args:
        current: 当前阶段权重。
        scores: 当前阶段各维度分数。
        uncertainty: 当前阶段不确定性。
        config: 动态权重超参数；为空时使用默认配置。

    Returns:
        下一阶段归一化权重。
    """
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
            effective_config.alpha * deficit + effective_config.beta * _clamp(uncertainty) * focus
        )
    return normalize_weights(next_weights)


def compute_uncertainty(
    top1_score: float,
    top2_score: float,
    missing_ratio: float,
    stage_score: float,
    evidence_conflict: bool,
    judge_uncertainty: float,
    thresholds: Optional[ThresholdConfig] = None,
) -> float:
    """计算阶段评估不确定性。

    Args:
        top1_score: 最优候选分数。
        top2_score: 次优候选分数。
        missing_ratio: 必要字段缺失比例。
        stage_score: 阶段总分。
        evidence_conflict: 证据是否冲突。
        judge_uncertainty: judge 自身不确定性。
        thresholds: 阈值配置。

    Returns:
        `[0, 1]` 区间的不确定性。
    """
    effective_thresholds = thresholds or ThresholdConfig()
    margin = max(top1_score - top2_score, 0.0)
    u_margin = 1.0 - _clamp(margin / 0.3)
    u_missing = _clamp(missing_ratio)
    threshold_distance = min(
        abs(stage_score - effective_thresholds.pass_threshold),
        abs(stage_score - effective_thresholds.warn_threshold),
        abs(stage_score - effective_thresholds.fail_threshold),
    )
    u_threshold = 1.0 - _clamp(threshold_distance / effective_thresholds.threshold_margin)
    u_conflict = 1.0 if evidence_conflict else 0.0
    u_judge = _clamp(judge_uncertainty)
    return _clamp(
        0.30 * u_margin
        + 0.25 * u_missing
        + 0.20 * u_threshold
        + 0.15 * u_conflict
        + 0.10 * u_judge
    )


def select_evaluation_level(
    result: StageEvaluationResult,
    thresholds: Optional[ThresholdConfig] = None,
) -> EvaluationDecision:
    """根据阶段风险选择评估粒度。

    Args:
        result: 当前阶段已有评估结果。
        thresholds: 阈值配置。

    Returns:
        评估粒度决策。
    """
    if result is None:
        raise ValueError("result 不能为空")
    effective_thresholds = thresholds or ThresholdConfig()
    if result.fatal or result.fatal_minefield_score >= effective_thresholds.fatal_minefield_threshold:
        return EvaluationDecision(EvaluationLevel.EXPENSIVE, "触发致命风险，需要最高粒度复核")
    if result.evaluator_level == EvaluationLevel.EXPENSIVE:
        return EvaluationDecision(EvaluationLevel.EXPENSIVE, "已处于最高评估粒度")
    if result.evaluator_level == EvaluationLevel.STANDARD:
        if result.judge_confidence < 0.45:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "standard judge 置信度过低")
        if result.uncertainty >= effective_thresholds.high_uncertainty:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "standard 阶段不确定性过高")
        if result.required_fields_missing_ratio > 0.3:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "必要字段缺失比例过高")
        if result.minefield_score >= effective_thresholds.risky_minefield_threshold:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "风险约束接近 minefield")
        return EvaluationDecision(EvaluationLevel.STANDARD, "standard 评估已足够")

    cheap_pass = (
        result.status == StageStatus.PASS
        and result.stage_score >= effective_thresholds.pass_threshold + effective_thresholds.threshold_margin
        and result.uncertainty <= effective_thresholds.low_uncertainty
        and result.hard_constraints_all_pass
        and result.required_fields_missing_ratio == 0
        and result.minefield_score <= effective_thresholds.safe_minefield_threshold
        and result.judge_confidence >= 0.7
    )
    if cheap_pass:
        return EvaluationDecision(EvaluationLevel.CHEAP, "结构化证据充分，cheap 评估足够")
    return EvaluationDecision(EvaluationLevel.STANDARD, "cheap 证据不足，升级到 standard")


def _milestone_score_matrix(
    graph: MilestoneGraph,
    boundaries: list[Boundary],
    trajectory: Trajectory,
) -> dict[tuple[str, str], MilestoneScore]:
    matrix: dict[tuple[str, str], MilestoneScore] = {}
    for milestone in graph.nodes:
        for boundary in boundaries:
            matrix[(milestone.milestone_id, boundary.boundary_id)] = score_milestone(
                milestone,
                boundary,
                trajectory,
                trajectory.snapshots,
            )
    return matrix


def _top_two_scores(
    milestone_id: str,
    matrix: dict[tuple[str, str], MilestoneScore],
) -> tuple[float, float]:
    scores = sorted(
        [score.score for (candidate_id, _), score in matrix.items() if candidate_id == milestone_id],
        reverse=True,
    )
    if not scores:
        return 0.0, 0.0
    if len(scores) == 1:
        return scores[0], 0.0
    return scores[0], scores[1]


def evaluate_minefields(graph: MilestoneGraph, trajectory: Trajectory) -> tuple[list[JsonObject], float, bool]:
    """评估轨迹是否触发 minefield。

    Args:
        graph: milestone 图，包含 minefield 定义。
        trajectory: 待检查的轨迹。

    Returns:
        三元组：命中的 minefield 列表、最高 minefield 分数、是否触发 fatal minefield。
    """
    if graph is None or trajectory is None:
        raise ValueError("graph 和 trajectory 不能为空")
    matches: list[JsonObject] = []
    max_score = 0.0
    fatal = False
    for minefield in graph.minefields:
        if not minefield.constraints:
            continue
        scores = []
        evidence: list[str] = []
        for constraint in minefield.constraints:
            source: object = trajectory.metrics if constraint.target == ConstraintTarget.METRIC else trajectory.final_state or {}
            score = score_constraint(constraint, source, None)
            scores.append(score.score)
            evidence.extend(score.evidence)
        minefield_score = sum(scores) / len(scores) if scores else 0.0
        if minefield_score > 0:
            matches.append(
                {
                    "minefield_id": minefield.minefield_id,
                    "score": minefield_score,
                    "severity": minefield.severity,
                    "evidence": evidence,
                }
            )
        max_score = max(max_score, minefield_score)
        if minefield.severity == "fatal" and minefield_score >= 1.0:
            fatal = True
    return matches, max_score, fatal


def _overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 1.0 if minefield_score == 0 else 0.0
    raw = sum(stage.stage_score for stage in stage_reports) / len(stage_reports)
    return _clamp(raw * (1.0 - _clamp(minefield_score)))


def evaluate_trajectory(
    task_case: TaskCase,
    trajectory: Trajectory,
    judge: Optional[Judge] = None,
    thresholds: Optional[ThresholdConfig] = None,
    match_config: Optional[MatchConfig] = None,
    weight_config: Optional[DynamicWeightConfig] = None,
) -> TrajectoryEvaluationReport:
    """执行阶段式动态轨迹评估。

    Args:
        task_case: 任务定义。
        trajectory: 待评估 Agent 轨迹。
        judge: 阶段评估器；为空时使用 LocalJudge。
        thresholds: 阈值配置。
        match_config: milestone 匹配配置。
        weight_config: 动态权重配置。

    Returns:
        轨迹评估报告。
    """
    if task_case is None or trajectory is None:
        raise ValueError("task_case 和 trajectory 不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        graph = MilestoneGraph()
    effective_judge = judge or LocalJudge()
    effective_thresholds = thresholds or ThresholdConfig()
    minefield_matches, minefield_score, fatal_minefield = evaluate_minefields(graph, trajectory)
    if fatal_minefield:
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage="none" if not graph.nodes else "partial",
            overall_score=0.0,
            stage_reports=[],
            minefield_matches=minefield_matches,
            first_failure_stage_id=f"minefield:{minefield_matches[0]['minefield_id']}" if minefield_matches else "minefield",
        )
    if len(graph.nodes) == 0:
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage="none",
            overall_score=_overall_score([], minefield_score),
            stage_reports=[],
            minefield_matches=minefield_matches,
        )

    validate_milestone_graph(graph)
    boundaries = generate_candidate_boundaries(trajectory)
    matrix = _milestone_score_matrix(graph, boundaries, trajectory)
    mapping = match_milestones(graph, boundaries, matrix, match_config or MatchConfig())
    intervals = build_stage_intervals(graph, mapping, trajectory)
    weights = select_initial_weights(task_case)
    stage_reports: list[StageEvaluationResult] = []
    for interval in intervals:
        result = effective_judge.evaluate_stage(interval, task_case, trajectory, EvaluationLevel.CHEAP, weights)
        top1, top2 = _top_two_scores(interval.milestone_id or "", matrix)
        uncertainty = compute_uncertainty(
            top1_score=top1 if top1 > 0 else result.stage_score,
            top2_score=top2,
            missing_ratio=result.required_fields_missing_ratio,
            stage_score=result.stage_score,
            evidence_conflict=False,
            judge_uncertainty=1.0 - result.judge_confidence,
            thresholds=effective_thresholds,
        )
        result = replace(
            result,
            uncertainty=uncertainty,
            minefield_score=minefield_score,
            fatal_minefield_score=minefield_score if fatal_minefield else 0.0,
        )
        decision = select_evaluation_level(result, effective_thresholds)
        if decision.level != EvaluationLevel.CHEAP:
            result = effective_judge.evaluate_stage(interval, task_case, trajectory, decision.level, weights)
            result = replace(
                result,
                uncertainty=uncertainty,
                minefield_score=minefield_score,
                fatal_minefield_score=minefield_score if fatal_minefield else 0.0,
            )
        next_weights = update_weights(weights, result.dimension_scores, result.uncertainty, weight_config)
        result = replace(result, next_weights=next_weights)
        stage_reports.append(result)
        weights = next_weights

    failed = next(
        (
            stage.stage_id
            for stage in stage_reports
            if stage.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
        ),
        None,
    )
    coverage = "full" if not mapping.missing_required else "partial"
    return TrajectoryEvaluationReport(
        run_id=trajectory.run_id,
        task_id=trajectory.task_id,
        milestone_coverage=coverage,
        overall_score=_overall_score(stage_reports, minefield_score),
        stage_reports=stage_reports,
        minefield_matches=minefield_matches,
        first_failure_stage_id=failed,
    )
