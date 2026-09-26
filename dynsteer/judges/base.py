import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dynsteer.prompt.judge import build_judge_system_prompt
from dynsteer.judges.confidence import complete_dimension_confidence, uncertainty_from_confidence
from dynsteer.language import TaskLanguage
from dynsteer.llm.base import BaseLLM, LLMResponseError
from dynsteer.model import Dimension, EvaluationLevel, JsonObject, LLMMessage, StageEvaluationResult, StageInterval, StageStatus, TaskCase, Trajectory, ValidatedJudgePayload

class LLMJudgeConfigurationError(ValueError):
    """LLMJudge 配置缺失或不合法时抛出。"""

class LLMJudgeResponseError(ValueError):
    """LLMJudge 返回内容无法解析时抛出。"""

class BaseJudge(ABC):
    """所有轨迹阶段评估器的抽象基类。"""

    @abstractmethod
    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """评估单个阶段。"""

class LLMJudge(BaseJudge):
    """基于 BaseLLM 的 LLM-as-a-Judge 抽象基类。"""

    def __init__(self, llm: BaseLLM, passes: int=3) -> None:
        if llm is None:
            raise LLMJudgeConfigurationError("LLMJudge 需要提供 BaseLLM 实例")
        if passes < 1:
            raise LLMJudgeConfigurationError("passes 必须大于 0")
        self._llm = llm
        self._passes = passes

    def _call_json(self, prompt: str, language: TaskLanguage=TaskLanguage.ENGLISH) -> JsonObject:
        """调用 LLM 并解析为 JSON 对象。"""
        if prompt is None or not prompt.strip():
            raise LLMJudgeConfigurationError("prompt 不能为空")
        messages = [LLMMessage(role="system", content=build_judge_system_prompt(language)), LLMMessage(role="user", content=prompt)]
        try:
            text = self._llm.chat(messages, response_format="json_object")
        except LLMResponseError as exc:
            raise LLMJudgeResponseError(f"LLMJudge 调用 LLM 失败: {exc}") from exc
        return self._parse_json_text(text)

    def _parse_json_text(self, text: str) -> JsonObject:
        """解析完整 JSON 或单个 fenced JSON block。"""
        if text is None or not isinstance(text, str) or (not text.strip()):
            raise LLMJudgeResponseError("LLMJudge 返回内容为空")
        raw = text.strip()
        fenced = re.fullmatch("```(?:json)?\\s*(\\{.*\\})\\s*```", raw, flags=re.DOTALL)
        if fenced is not None:
            raw = fenced.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMJudgeResponseError("LLMJudge 返回内容不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise LLMJudgeResponseError("LLMJudge 返回内容必须是 JSON 对象")
        return data

    def _result_from_payload(self, interval: StageInterval, level: EvaluationLevel, validated: ValidatedJudgePayload, metadata: JsonObject, dimension_confidence: dict[Dimension, float] | None=None) -> StageEvaluationResult:
        result_metadata = dict(metadata)
        result_metadata.update(validated.metadata)
        confidence = complete_dimension_confidence(list(validated.dimension_scores), dimension_confidence)
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            status=validated.status,
            stage_score=0.0,
            dimension_scores=validated.dimension_scores,
            dimension_levels={
                dimension: level for dimension in validated.dimension_scores
            },
            dimension_confidence=confidence,
            dimension_uncertainty=uncertainty_from_confidence(confidence),
            evidence=validated.evidence,
            diagnosis=validated.diagnosis,
            metadata=result_metadata,
        )

    def _validate_payload(self, payload: JsonObject, dimensions: Iterable[Dimension]) -> ValidatedJudgePayload:
        """校验 Judge JSON payload 的必需字段和可选元数据。"""
        try:
            status = StageStatus(str(payload["status"]))
        except (KeyError, ValueError) as exc:
            raw_status = payload.get("status", "<missing>")
            allowed = ", ".join((status.value for status in StageStatus))
            raise LLMJudgeResponseError(f"LLMJudge 返回 status 非法: actual={raw_status!r}, allowed=[{allowed}], payload={self._payload_excerpt(payload)}") from exc
        return ValidatedJudgePayload(status=status, dimension_scores=self._dimension_scores(payload.get("dimension_scores"), list(dimensions)), evidence=self._string_list(payload.get("evidence"), "evidence"), diagnosis=self._string_list(payload.get("diagnosis"), "diagnosis"), metadata=self._metadata_object(payload.get("metadata")))

    def _dimension_scores(self, value: object, dimensions: list[Dimension]) -> dict[Dimension, float]:
        if not isinstance(value, dict):
            raise LLMJudgeResponseError("dimension_scores 必须是对象")
        scores: dict[Dimension, float] = {}
        for dimension in dimensions:
            scores[dimension] = self._float_in_unit(value.get(dimension.value), f"dimension_scores.{dimension.value}")
        return scores

    def _float_in_unit(self, value: object, label: str) -> float:
        if not isinstance(value, int | float):
            raise LLMJudgeResponseError(f"{label} 必须是数字")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise LLMJudgeResponseError(f"{label} 必须在 [0,1] 区间")
        return number

    def _string_list(self, value: list, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise LLMJudgeResponseError(f"{label} 必须是数组")
        return [str(item) for item in value]

    def _metadata_object(self, value: dict) -> JsonObject:
        """校验可选 metadata 字段。"""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise LLMJudgeResponseError("metadata 必须是对象")
        return {str(key): item for key, item in value.items()}

    def _payload_excerpt(self, payload: object, max_length: int=1200) -> str:
        """生成用于异常信息的 payload 摘要，避免超长输出刷屏。"""
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(payload)
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}...(truncated, total={len(text)})"
