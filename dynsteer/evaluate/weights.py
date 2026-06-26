from __future__ import annotations

import math
from typing import Optional

from dynsteer.config import (
    DEFAULT_FOCUS,
    DEFAULT_TARGETS,
    TASK_TYPE_WEIGHTS,
    DynamicWeightConfig,
    default_dynamic_weight_config,
)
from dynsteer.model import Dimension, TaskCase
from dynsteer.utils import clamp


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
            effective_config.alpha * deficit + effective_config.beta * clamp(uncertainty) * focus
        )
    return normalize_weights(next_weights)