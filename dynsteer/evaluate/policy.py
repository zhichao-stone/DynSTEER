from dynsteer.model import Dimension, EvaluationLevel, EvaluationPolicyState, StageEvaluationResult, ThresholdConfig

def update_evaluation_policy(policy: EvaluationPolicyState, result: StageEvaluationResult, thresholds: ThresholdConfig) -> EvaluationPolicyState:
    """根据当前阶段结果生成下一阶段评估策略。"""
    max_uncertainty = max(result.dimension_uncertainty.values(), default=0.0)
    base_level = EvaluationLevel.CHEAP if result.stage_score >= thresholds.pass_threshold + thresholds.threshold_margin and max_uncertainty <= thresholds.low_dimension_uncertainty and (result.fatal_minefield_score == 0) else EvaluationLevel.STANDARD
    reason = "高分低不确定，下一阶段使用 cheap" if base_level == EvaluationLevel.CHEAP else "阶段结果处于合理区间，下一阶段使用 standard"
    reason = f"stage_score={result.stage_score:.3f}, max_dimension_uncertainty={max_uncertainty:.3f}, reason={reason}"
    next_policy = EvaluationPolicyState(base_level=base_level, dimension_levels=_next_dimension_levels(base_level, policy.base_level, policy.dimension_levels, result, thresholds), reason=reason)
    return next_policy

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
