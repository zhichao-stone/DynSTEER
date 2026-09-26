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
    """LLMJudge is dropped when the configuration is missing or invalid."""

class LLMJudgeResponseError(ValueError):
    """LLMJudge drops when it returns content that cannot be parsed."""

class BaseJudge(ABC):
    """Abstract base class for trajectory-stage evaluators."""

    @abstractmethod
    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """Assessment of individual stages."""

class LLMJudge(BaseJudge):
    """LLM-as-a-Jude based on BaseLLLM."""

    def __init__(self, llm: BaseLLM, passes: int=3) -> None:
        if llm is None:
            raise LLMJudgeConfigurationError('LLMJudge needs to provide BaseLLLLM examples')
        if passes < 1:
            raise LLMJudgeConfigurationError('Passes must be greater than 0')
        self._llm = llm
        self._passes = passes

    def _call_json(self, prompt: str, language: TaskLanguage=TaskLanguage.ENGLISH) -> JsonObject:
        """Call LLM and parse to JSON objects."""
        if prompt is None or not prompt.strip():
            raise LLMJudgeConfigurationError('Prompt cannot be empty')
        messages = [LLMMessage(role="system", content=build_judge_system_prompt(language)), LLMMessage(role="user", content=prompt)]
        try:
            text = self._llm.chat(messages, response_format="json_object")
        except LLMResponseError as exc:
            raise LLMJudgeResponseError(f"LLMJudge failed to call LLM:{exc}") from exc
        return self._parse_json_text(text)

    def _parse_json_text(self, text: str) -> JsonObject:
        """Parsing the full JSON or individual Fenced JSON black."""
        if text is None or not isinstance(text, str) or (not text.strip()):
            raise LLMJudgeResponseError('LLMJudge returns empty content')
        raw = text.strip()
        fenced = re.fullmatch("```(?:json)?\\s*(\\{.*\\})\\s*```", raw, flags=re.DOTALL)
        if fenced is not None:
            raw = fenced.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMJudgeResponseError('LLMJudge returns content is not legal') from exc
        if not isinstance(data, dict):
            raise LLMJudgeResponseError('LLMJudge returns content must be a JSON object')
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
        """Validate required fields and optional metadata in judge JSON payloads."""
        try:
            status = StageStatus(str(payload["status"]))
        except (KeyError, ValueError) as exc:
            raw_status = payload.get("status", "<missing>")
            allowed = ", ".join((status.value for status in StageStatus))
            raise LLMJudgeResponseError(f"LLMJudge returns status illegal: actual={raw_status!r}, allowed=[{allowed}], payload={self._payload_excerpt(payload)}") from exc
        return ValidatedJudgePayload(status=status, dimension_scores=self._dimension_scores(payload.get("dimension_scores"), list(dimensions)), evidence=self._string_list(payload.get("evidence"), "evidence"), diagnosis=self._string_list(payload.get("diagnosis"), "diagnosis"), metadata=self._metadata_object(payload.get("metadata")))

    def _dimension_scores(self, value: object, dimensions: list[Dimension]) -> dict[Dimension, float]:
        if not isinstance(value, dict):
            raise LLMJudgeResponseError('dimension_scores must be a JSON object')
        scores: dict[Dimension, float] = {}
        for dimension in dimensions:
            scores[dimension] = self._float_in_unit(value.get(dimension.value), f"dimension_scores.{dimension.value}")
        return scores

    def _float_in_unit(self, value: object, label: str) -> float:
        if not isinstance(value, int | float):
            raise LLMJudgeResponseError(f"{label} must be a number.")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise LLMJudgeResponseError(f"{label} must be in the [0, 1] interval")
        return number

    def _string_list(self, value: list, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise LLMJudgeResponseError(f"{label} must be an array.")
        return [str(item) for item in value]

    def _metadata_object(self, value: dict) -> JsonObject:
        """Verify the selected metadata field."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise LLMJudgeResponseError('Metadata must be an object')
        return {str(key): item for key, item in value.items()}

    def _payload_excerpt(self, payload: object, max_length: int=1200) -> str:
        """Generates a payload summary for unusual information, avoiding a super-long output brush."""
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(payload)
        if len(text) <= max_length:
            return text
        return f"{text[:max_length]}...(truncated, total={len(text)})"
