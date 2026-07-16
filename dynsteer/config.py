from __future__ import annotations

from dynsteer.model import Dimension, DynamicWeightConfig, TaskType


DIMENSION_ORDER: tuple[Dimension, ...] = (
    Dimension.PROGRESS,
    Dimension.STATE_CONSISTENCY,
    Dimension.TOOL_QUALITY,
    Dimension.EFFICIENCY,
    Dimension.SAFETY,
    Dimension.INTERACTION_QUALITY,
    Dimension.RECOVERY,
)

TASK_TYPE_WEIGHTS: dict[TaskType, dict[Dimension, float]] = {
    TaskType.GENERAL: {
        Dimension.PROGRESS: 0.25,
        Dimension.STATE_CONSISTENCY: 0.20,
        Dimension.TOOL_QUALITY: 0.20,
        Dimension.EFFICIENCY: 0.10,
        Dimension.SAFETY: 0.15,
        Dimension.INTERACTION_QUALITY: 0.05,
        Dimension.RECOVERY: 0.05,
    },
    TaskType.STATEFUL_TOOL: {
        Dimension.PROGRESS: 0.25,
        Dimension.STATE_CONSISTENCY: 0.25,
        Dimension.TOOL_QUALITY: 0.22,
        Dimension.EFFICIENCY: 0.08,
        Dimension.SAFETY: 0.12,
        Dimension.INTERACTION_QUALITY: 0.03,
        Dimension.RECOVERY: 0.05,
    },
    TaskType.DIALOGUE_INTERACTION: {
        Dimension.PROGRESS: 0.20,
        Dimension.STATE_CONSISTENCY: 0.15,
        Dimension.TOOL_QUALITY: 0.15,
        Dimension.EFFICIENCY: 0.08,
        Dimension.SAFETY: 0.17,
        Dimension.INTERACTION_QUALITY: 0.20,
        Dimension.RECOVERY: 0.05,
    },
    TaskType.ARTIFACT: {
        Dimension.PROGRESS: 0.28,
        Dimension.STATE_CONSISTENCY: 0.22,
        Dimension.TOOL_QUALITY: 0.12,
        Dimension.EFFICIENCY: 0.08,
        Dimension.SAFETY: 0.10,
        Dimension.INTERACTION_QUALITY: 0.05,
        Dimension.RECOVERY: 0.15,
    },
    TaskType.SAFETY_SENSITIVE: {
        Dimension.PROGRESS: 0.20,
        Dimension.STATE_CONSISTENCY: 0.18,
        Dimension.TOOL_QUALITY: 0.15,
        Dimension.EFFICIENCY: 0.05,
        Dimension.SAFETY: 0.30,
        Dimension.INTERACTION_QUALITY: 0.07,
        Dimension.RECOVERY: 0.05,
    },
}

def default_dynamic_weight_config() -> DynamicWeightConfig:
    """创建默认动态权重配置。"""
    return DynamicWeightConfig(alpha=1.0, beta=1.0)
