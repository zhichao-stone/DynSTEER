from __future__ import annotations

from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    EvaluationPolicyState,
    EvaluationTerminationState,
    StageEvaluationResult,
    StageStatus,
    ThresholdConfig,
)


def update_evaluation_policy(
    policy: EvaluationPolicyState,
    result: StageEvaluationResult,
    thresholds: ThresholdConfig | None = None,
) -> tuple[EvaluationPolicyState, EvaluationTerminationState]:
    """根据当前阶段结果生成下一阶段评估策略。"""
    if policy is None or result is None:
        raise ValueError("策略更新参数不能为空")
    effective_thresholds = thresholds or ThresholdConfig()
    if _should_stop(result, effective_thresholds):
        reason = _termination_reason(result, effective_thresholds)
        return (
            EvaluationPolicyState(
                base_level=policy.base_level,
                dimension_levels=dict(policy.dimension_levels),
                reason=reason,
            ),
            EvaluationTerminationState(
                should_stop=True,
                termination_code="evaluation_policy_stop",
                termination_reason=reason,
            ),
        )

    base_level = (
        EvaluationLevel.CHEAP
        if _is_high_confidence_pass(result, effective_thresholds)
        else EvaluationLevel.STANDARD
    )
    next_policy = EvaluationPolicyState(
        base_level=base_level,
        dimension_levels=_next_dimension_levels(
            base_level,
            policy.base_level,
            result,
            effective_thresholds,
        ),
        reason=_policy_reason(base_level, result),
    )
    return next_policy, EvaluationTerminationState()


def _should_stop(result: StageEvaluationResult, thresholds: ThresholdConfig) -> bool:
    """判断阶段结果是否触发策略终止。"""
    return (
        result.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
        or result.stage_score < thresholds.warn_threshold
        or result.fatal_minefield_score >= thresholds.fatal_minefield_threshold
    )


def _is_high_confidence_pass(result: StageEvaluationResult, thresholds: ThresholdConfig) -> bool:
    """判断阶段是否足以让下一阶段回到 cheap。"""
    return (
        result.stage_score >= thresholds.pass_threshold + thresholds.threshold_margin
        and max(result.dimension_uncertainty.values(), default=0.0) <= thresholds.low_dimension_uncertainty
        and result.fatal_minefield_score == 0
    )


def _next_dimension_levels(
    next_base_level: EvaluationLevel,
    current_base_level: EvaluationLevel,
    result: StageEvaluationResult,
    thresholds: ThresholdConfig,
) -> dict[Dimension, EvaluationLevel]:
    """根据逐维分数计算下一阶段的逐维粒度。"""
    levels: dict[Dimension, EvaluationLevel] = {}
    for dimension in Dimension:
        score = float(result.dimension_scores.get(dimension, 0.0))
        uncertainty = float(result.dimension_uncertainty.get(dimension, 0.0))
        if score < thresholds.fail_threshold:
            levels[dimension] = EvaluationLevel.EXPENSIVE
        elif uncertainty >= thresholds.high_dimension_uncertainty:
            levels[dimension] = _upgrade_level(current_base_level)
        elif score < thresholds.warn_threshold:
            levels[dimension] = _upgrade_level(current_base_level)
        else:
            levels[dimension] = next_base_level
    return levels


def _upgrade_level(level: EvaluationLevel) -> EvaluationLevel:
    """把低分维度提升到下一档评估粒度。"""
    if level == EvaluationLevel.CHEAP:
        return EvaluationLevel.STANDARD
    return EvaluationLevel.EXPENSIVE


def _policy_reason(base_level: EvaluationLevel, result: StageEvaluationResult) -> str:
    """生成下一阶段策略原因。"""
    if base_level == EvaluationLevel.CHEAP:
        return (
            f"stage_score={result.stage_score:.3f}, max_dimension_uncertainty="
            f"{max(result.dimension_uncertainty.values(), default=0.0):.3f}，"
            "高分低不确定，下一阶段使用 cheap"
        )
    return (
        f"stage_score={result.stage_score:.3f}, max_dimension_uncertainty="
        f"{max(result.dimension_uncertainty.values(), default=0.0):.3f}，"
        "阶段结果处于合理区间，下一阶段使用 standard"
    )


def _termination_reason(result: StageEvaluationResult, thresholds: ThresholdConfig) -> str:
    """生成策略终止原因。"""
    if result.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
        return f"阶段状态为 {result.status.value}，触发策略终止"
    if result.fatal_minefield_score >= thresholds.fatal_minefield_threshold:
        return f"fatal minefield score={result.fatal_minefield_score:.3f}，触发策略终止"
    return f"阶段分数 {result.stage_score:.3f} 低于警戒阈值 {thresholds.warn_threshold:.3f}，触发策略终止"
