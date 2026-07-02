from __future__ import annotations

from dataclasses import dataclass

from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import Dimension, JsonObject, StageEvaluationResult


@dataclass
class RuntimeEvaluationState:
    """保存单个 case 运行期间的评估状态。"""

    weights: dict[Dimension, float]
    settlements: list[HarnessStageSettlement]
    matched_settlements: dict[str, HarnessStageSettlement]
    stage_reports: list[StageEvaluationResult]
    match_attempts: list[JsonObject]


@dataclass
class RuntimeEvaluationDecision:
    """单步运行期阶段评估决策。"""

    checkpoint: HarnessStageSettlement | None
    stage_result: StageEvaluationResult | None
    next_state: RuntimeEvaluationState
    should_stop: bool = False
    termination_code: str | None = None
    termination_reason: str | None = None
    termination_detail: JsonObject | None = None


class JudgeConfigurationError(RuntimeError):
    """LLM judge 配置缺失或不合法时抛出。"""


class HarnessTeardownError(RuntimeError):
    """benchmark session 资源释放失败时抛出。"""
