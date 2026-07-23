from collections.abc import Iterable

from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.evaluate.semantic import (
    SemanticMessageReview,
    SemanticMessageReviewTarget,
)
from dynsteer.prompt.judge import build_judge_prompt, build_semantic_message_equivalence_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory


class StandardJudge(LLMJudge):
    """standard 粒度的单轮 LLM-as-a-Judge。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """在单轮 LLM 调用中完成 standard 阶段评估。"""
        language = language_from_task(task_case)
        target_dimensions = self._target_dimensions(dimensions)
        prompt = build_judge_prompt(
            "standard", interval, task_case, trajectory, language=language, target_dimensions=target_dimensions
        )
        input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt)
        input_metadata["target_dimensions"] = [dimension.value for dimension in target_dimensions]
        payloads = [self._call_json(prompt, language=language) for _ in range(self._passes)]
        for payload in payloads:
            self._validate_payload(payload, target_dimensions)
        payload = aggregate_judge_payload(payloads, target_dimensions)
        result = self._result_from_payload(
            interval,
            EvaluationLevel.STANDARD,
            payload,
            metadata=input_metadata,
            dimensions=target_dimensions,
            dimension_confidence=agreement_confidence(payloads, target_dimensions),
        )
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def review_message_equivalence(
        self,
        target: SemanticMessageReviewTarget,
        *,
        task_case: TaskCase,
    ) -> SemanticMessageReview:
        """专用判断单条用户可见消息是否语义等价。

        入参：
            target: 单条消息语义复判目标。
            task_case: 当前任务，用于提供任务语义背景与语言选择。
        输出：
            `equivalent/confidence/reason` 形式的窄域复判结果。
        """
        if target is None or task_case is None:
            raise ValueError("target 和 task_case 不能为空")
        language = language_from_task(task_case)
        prompt = build_semantic_message_equivalence_prompt(
            task_case,
            target.to_dict(),
            language=language,
        )
        payload = self._semantic_payload(self._call_json(prompt, language=language))
        return SemanticMessageReview(
            constraint_id=target.constraint_id,
            equivalent=bool(payload["equivalent"]),
            confidence=float(payload["confidence"]),
            reason=str(payload.get("reason") or ""),
            raw_payload=payload,
        )

    def _semantic_payload(self, payload: JsonObject) -> JsonObject:
        """校验消息语义等价 judge payload。"""
        if not isinstance(payload.get("equivalent"), bool):
            raise ValueError("semantic message judge 返回 equivalent 必须是 bool")
        confidence = self._float_in_unit(payload.get("confidence"), "confidence")
        return {
            "equivalent": bool(payload["equivalent"]),
            "confidence": confidence,
            "reason": str(payload.get("reason") or ""),
        }
