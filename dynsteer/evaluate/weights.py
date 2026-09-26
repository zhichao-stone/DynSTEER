import math
from dynsteer.config import TASK_TYPE_WEIGHTS
from dynsteer.model import Dimension, DynamicWeightConfig, TaskCase
from dynsteer.utils import clamp

def normalize_weights(weights: dict[Dimension, float]) -> dict[Dimension, float]:
    """Normalizes the weight of dynamic dimensions. Args: weights: weights: dimensions: original dimensions. Returns: weights dictionary over which all dimensions are covered and which sum to 1."""
    normalized_source = {dimension: max(float(weights.get(dimension, 0.0)), 0.0) for dimension in Dimension}
    total = sum(normalized_source.values())
    if total <= 0:
        return {dimension: 1 / len(Dimension) for dimension in Dimension}
    return {dimension: value / total for dimension, value in normalized_source.items()}

def select_initial_weights(task_case: TaskCase) -> dict[Dimension, float]:
    """Select the initial dynamic weight according to the task type. Args: task_case: the current benchmark case. Returns: the initial dimension weight of the current case."""
    task_types = task_case.task_types or [next(iter(TASK_TYPE_WEIGHTS))]
    merged = {dimension: 0.0 for dimension in Dimension}
    for task_type in task_types:
        weights = TASK_TYPE_WEIGHTS[task_type]
        for dimension in Dimension:
            merged[dimension] += weights.get(dimension, 0.0)
    return normalize_weights(merged)

def update_weights(current: dict[Dimension, float], scores: dict[Dimension, float], dimension_uncertainty: dict[Dimension, float], config: DynamicWeightConfig) -> dict[Dimension, float]:
    """Update dynamic weights from stage dimension scores and uncertainties."""
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
