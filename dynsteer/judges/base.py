from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from dynsteer.prompt.judge import build_judge_system_prompt
from dynsteer.evaluate.scoring import stage_score_from_dimensions
from dynsteer.language import TaskLanguage
from dynsteer.llm.base import BaseLLM, LLMMessage, LLMResponseError
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    JsonObject,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


class LLMJudgeConfigurationError(ValueError):
    """LLMJudge 配置缺失或不合法时抛出。"""


class LLMJudgeResponseError(ValueError):
    """LLMJudge 返回内容无法解析时抛出。"""


@dataclass(frozen=True)
class LLMJudgeConfig:
    """LLMJudge 评估行为配置。"""

    expensive_passes: int = 3


@dataclass(frozen=True)
class _ValidatedJudgePayload:
    """已通过 schema 校验的 Judge payload。"""

    status: StageStatus
    dimension_scores: dict[Dimension, float]
    judge_confidence: float
    evidence: list[str]
    diagnosis: list[str]
    metadata: JsonObject


class BaseJudge(ABC):
    """所有轨迹阶段评估器的抽象基类。"""

    @abstractmethod
    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """评估单个阶段。"""


class LLMJudge(BaseJudge):
    """基于 BaseLLM 的 LLM-as-a-Judge 抽象基类。"""

    def __init__(self, llm: BaseLLM, expensive_passes: int = 3) -> None:
        if llm is None:
            raise LLMJudgeConfigurationError("LLMJudge 需要提供 BaseLLM 实例")
        if expensive_passes < 1:
            raise LLMJudgeConfigurationError("expensive_passes 必须大于 0")
        self._llm = llm
        self._config = LLMJudgeConfig(expensive_passes=int(expensive_passes))

    def _call_json(self, prompt: str, language: TaskLanguage = TaskLanguage.ENGLISH) -> JsonObject:
        """调用 LLM 并解析为 JSON 对象。"""
        if prompt is None or not prompt.strip():
            raise LLMJudgeConfigurationError("prompt 不能为空")
        messages = [
            LLMMessage(role="system", content=build_judge_system_prompt(language)),
            LLMMessage(role="user", content=prompt),
        ]
        try:
            text = self._llm.chat(messages, response_format="json_object")
        except LLMResponseError as exc:
            raise LLMJudgeResponseError("LLMJudge 调用 LLM 失败") from exc
        data = self._parse_json_text(text)
        if not isinstance(data, dict):
            raise LLMJudgeResponseError("LLMJudge 返回内容必须是 JSON 对象")
        self._validate_payload(data)
        return data

    def _parse_json_text(self, text: str) -> JsonObject:
        """解析完整 JSON 或单个 fenced JSON block。"""
        if text is None or not isinstance(text, str) or not text.strip():
            raise LLMJudgeResponseError("LLMJudge 返回内容为空")
        raw = text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
        if fenced is not None:
            raw = fenced.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMJudgeResponseError("LLMJudge 返回内容不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise LLMJudgeResponseError("LLMJudge 返回内容必须是 JSON 对象")
        return data

    def _result_from_payload(
        self,
        interval: StageInterval,
        level: EvaluationLevel,
        payload: JsonObject,
        weights: dict[Dimension, float],
        metadata: JsonObject,
    ) -> StageEvaluationResult:
        validated = self._validate_payload(payload)
        result_metadata = dict(metadata)
        result_metadata.update(validated.metadata)
        stage_score = self._stage_score_from_dimensions(validated.dimension_scores, weights)
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            evaluator_level=level,
            status=validated.status,
            stage_score=stage_score,
            uncertainty=max(0.0, min(1.0, 1.0 - validated.judge_confidence)),
            dimension_scores=validated.dimension_scores,
            evidence=validated.evidence,
            diagnosis=validated.diagnosis,
            judge_confidence=validated.judge_confidence,
            metadata=result_metadata,
        )

    def _validate_payload(self, payload: JsonObject) -> _ValidatedJudgePayload:
        """校验 Judge JSON payload 的必需字段和可选元数据。"""
        if not isinstance(payload, dict):
            raise LLMJudgeResponseError("LLMJudge 返回内容必须是 JSON 对象")
        try:
            status = StageStatus(str(payload["status"]))
        except (KeyError, ValueError) as exc:
            raise LLMJudgeResponseError("LLMJudge 返回 status 非法") from exc
        return _ValidatedJudgePayload(
            status=status,
            dimension_scores=self._dimension_scores(payload.get("dimension_scores")),
            judge_confidence=self._float_in_unit(payload.get("judge_confidence"), "judge_confidence"),
            evidence=self._string_list(payload.get("evidence"), "evidence"),
            diagnosis=self._string_list(payload.get("diagnosis"), "diagnosis"),
            metadata=self._metadata_object(payload.get("metadata")),
        )

    def _stage_score_from_dimensions(
        self,
        dimension_scores: dict[Dimension, float],
        weights: dict[Dimension, float],
    ) -> float:
        """根据维度分数和内部权重计算阶段总分。"""
        if not isinstance(dimension_scores, dict) or not isinstance(weights, dict):
            raise LLMJudgeConfigurationError("stage_score 计算参数必须是字典")
        try:
            return stage_score_from_dimensions(dimension_scores, weights)
        except ValueError as exc:
            raise LLMJudgeConfigurationError("stage_score 计算参数不合法") from exc

    def _dimension_scores(self, value: object) -> dict[Dimension, float]:
        if not isinstance(value, dict):
            raise LLMJudgeResponseError("dimension_scores 必须是对象")
        scores: dict[Dimension, float] = {}
        for dimension in Dimension:
            scores[dimension] = self._float_in_unit(value.get(dimension.value), f"dimension_scores.{dimension.value}")
        return scores

    def _float_in_unit(self, value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise LLMJudgeResponseError(f"{label} 必须是数字")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise LLMJudgeResponseError(f"{label} 必须在 [0,1] 区间")
        return number

    def _string_list(self, value: object, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise LLMJudgeResponseError(f"{label} 必须是数组")
        return [str(item) for item in value]

    def _metadata_object(self, value: object) -> JsonObject:
        """校验可选 metadata 字段。"""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise LLMJudgeResponseError("metadata 必须是对象")
        return {str(key): item for key, item in value.items()}
