from dynsteer.model import Dimension, EvaluationLevel, EvaluationPolicyState, EvaluationTerminationState, StageEvaluationResult, ThresholdConfig

def update_evaluation_policy(policy: EvaluationPolicyState, result: StageEvaluationResult, thresholds: ThresholdConfig, allow_stop: bool=True) -> tuple[EvaluationPolicyState, EvaluationTerminationState]:
    """根据当前阶段结果生成下一阶段评估策略。"""
    stop_reason = None
    failure_basis = result.metadata.get("failure_basis")
    if allow_stop:
        if result.metadata.get("structural_failure") is True:
            failure_basis = "structural_hard_constraint"
            stop_reason = "阶段存在结构性失败，触发策略终止"
        elif result.metadata.get("missing_required_milestone") is True:
            stop_reason = "阶段缺少必要 milestone，触发策略终止"
        elif result.fatal_minefield_score >= thresholds.fatal_minefield_threshold:
            failure_basis = "fatal_minefield"
            stop_reason = f"fatal minefield score={result.fatal_minefield_score:.3f}，触发策略终止"
        elif result.stage_score < thresholds.fail_threshold:
            failure_basis = "quality_score"
            stop_reason = f"阶段分数 {result.stage_score:.3f} 低于失败阈值 {thresholds.fail_threshold:.3f}，触发策略终止"
    if stop_reason:
        return (EvaluationPolicyState(base_level=policy.base_level, dimension_levels=dict(policy.dimension_levels), reason=stop_reason), EvaluationTerminationState(termination_code="evaluation_policy_stop", termination_reason=stop_reason, termination_detail={"failure_basis": failure_basis, "stage_score": result.stage_score, "fail_threshold": thresholds.fail_threshold, "triggered": True}))
    max_uncertainty = max(result.dimension_uncertainty.values(), default=0.0)
    base_level = EvaluationLevel.CHEAP if result.stage_score >= thresholds.pass_threshold + thresholds.threshold_margin and max_uncertainty <= thresholds.low_dimension_uncertainty and (result.fatal_minefield_score == 0) else EvaluationLevel.STANDARD
    reason = "高分低不确定，下一阶段使用 cheap" if base_level == EvaluationLevel.CHEAP else "阶段结果处于合理区间，下一阶段使用 standard"
    reason = f"stage_score={result.stage_score:.3f}, max_dimension_uncertainty={max_uncertainty:.3f}, reason={reason}"
    next_policy = EvaluationPolicyState(base_level=base_level, dimension_levels=_next_dimension_levels(base_level, policy.base_level, policy.dimension_levels, result, thresholds), reason=reason)
    return (next_policy, EvaluationTerminationState())

def _next_dimension_levels(next_base_level: EvaluationLevel, current_base_level: EvaluationLevel, current_levels: dict[Dimension, EvaluationLevel], result: StageEvaluationResult, thresholds: ThresholdConfig) -> dict[Dimension, EvaluationLevel]:
    """根据逐维分数计算下一阶段的逐维粒度。"""
    levels: dict[Dimension, EvaluationLevel] = {}
    for dimension in Dimension:
        if dimension not in result.dimension_scores:
            levels[dimension] = current_levels.get(dimension, current_base_level)
            continue
        score = float(result.dimension_scores.get(dimension, 0.0))
        uncertainty = float(result.dimension_uncertainty.get(dimension, 0.0))
        current_level = current_levels.get(dimension, current_base_level)
        if score < thresholds.fail_threshold:
            levels[dimension] = EvaluationLevel.EXPENSIVE
        elif uncertainty >= thresholds.high_dimension_uncertainty or score < thresholds.warn_threshold:
            levels[dimension] = EvaluationLevel.STANDARD if current_level == EvaluationLevel.CHEAP else EvaluationLevel.EXPENSIVE
        else:
            levels[dimension] = next_base_level
    return levels
