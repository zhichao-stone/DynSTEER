import math
from dynsteer.config import TASK_TYPE_WEIGHTS
from dynsteer.model import Dimension, DynamicWeightConfig, TaskCase
from dynsteer.utils import clamp

def normalize_weights(weights: dict[Dimension, float]) -> dict[Dimension, float]:
    """归一化动态维度权重。

    入参：
        weights: 原始维度权重。
    输出：
        覆盖全部维度且总和为 1 的权重字典。
    """
    normalized_source = {dimension: max(float(weights.get(dimension, 0.0)), 0.0) for dimension in Dimension}
    total = sum(normalized_source.values())
    if total <= 0:
        return {dimension: 1 / len(Dimension) for dimension in Dimension}
    return {dimension: value / total for dimension, value in normalized_source.items()}

def select_initial_weights(task_case: TaskCase) -> dict[Dimension, float]:
    """根据任务类型选择初始动态权重。

    入参：
        task_case: 当前 benchmark case。
    输出：
        当前 case 的初始维度权重。
    """
    task_types = task_case.task_types or [next(iter(TASK_TYPE_WEIGHTS))]
    merged = {dimension: 0.0 for dimension in Dimension}
    for task_type in task_types:
        weights = TASK_TYPE_WEIGHTS[task_type]
        for dimension in Dimension:
            merged[dimension] += weights.get(dimension, 0.0)
    return normalize_weights(merged)

def update_weights(current: dict[Dimension, float], scores: dict[Dimension, float], dimension_uncertainty: dict[Dimension, float], config: DynamicWeightConfig) -> dict[Dimension, float]:
    """根据阶段维度分数和不确定性更新动态权重。

    入参：
        current: 当前维度权重。
        scores: 当前阶段维度分数。
        dimension_uncertainty: 当前阶段维度不确定性。
        config: 动态权重更新配置。
    输出：
        更新并归一化后的下一阶段权重。
    """
    next_weights: dict[Dimension, float] = {}
    for dimension in Dimension:
        base = max(float(current.get(dimension, 0.0)), 1e-09)
        if dimension not in scores:
            next_weights[dimension] = base
            continue
        score = float(scores.get(dimension, 0.0))
        uncertainty = clamp(float(dimension_uncertainty.get(dimension, 0.0)))
        next_weights[dimension] = base * math.exp(config.alpha * (1.0 - clamp(score)) + config.beta * uncertainty)
    return normalize_weights(next_weights)
