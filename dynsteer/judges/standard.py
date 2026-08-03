from collections.abc import Iterable
from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.evaluate.semantic import SemanticMessageReview, SemanticMessageReviewTarget
from dynsteer.prompt.judge import build_judge_prompt, build_semantic_message_equivalence_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory

class StandardJudge(LLMJudge):
    """standard 粒度的单轮 LLM-as-a-Judge。"""

    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """在单轮 LLM 调用中完成 standard 阶段评估。"""
        language = language_from_task(task_case)
        target_dimensions = self._target_dimensions(dimensions)
        prompt = build_judge_prompt("standard", interval, task_case, trajectory, language=language, target_dimensions=target_dimensions)
        input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt)
        input_metadata["target_dimensions"] = [dimension.value for dimension in target_dimensions]
        cache_context = {
            "case_id": task_case.case_id,
            "model_id": self._model_id(),
            "prompt_type": "standard",
            "stage_id": interval.stage_id,
            "milestone_id": interval.milestone_id,
            "trajectory_prefix": [interval.start_step_index, interval.end_step_index],
            "boundary": interval.end_step_index,
            "focus_dimensions": [dimension.value for dimension in target_dimensions],
            "language": language.value,
        }
        payloads = [
            self._cached_call_json(prompt, cache_context, language=language)
            for _ in range(self._passes)
        ]
        for payload in payloads:
            self._validate_payload(payload, target_dimensions)
        payload = aggregate_judge_payload(payloads, target_dimensions)
        result = self._result_from_payload(interval, EvaluationLevel.STANDARD, payload, metadata=input_metadata, dimensions=target_dimensions, dimension_confidence=agreement_confidence(payloads, target_dimensions))
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def review_message_equivalence(self, target: SemanticMessageReviewTarget, *, task_case: TaskCase) -> SemanticMessageReview:
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
        target_data = target.to_dict()
        prompt = build_semantic_message_equivalence_prompt(task_case, target_data, language=language)
        recent_steps = target.supporting_context.get("recent_steps")
        boundary = (
            recent_steps[-1].get("index")
            if isinstance(recent_steps, list) and recent_steps and isinstance(recent_steps[-1], dict)
            else None
        )
        cache_context = {
            "case_id": task_case.case_id,
            "model_id": self._model_id(),
            "prompt_type": "semantic_message_equivalence",
            "stage_id": None,
            "milestone_id": target.constraint_id,
            "trajectory_prefix": target_data,
            "boundary": boundary,
            "focus_dimensions": ["message_semantics"],
            "language": language.value,
        }
        payload = self._semantic_payload(
            self._cached_call_json(
                prompt,
                cache_context,
                language=language,
                semantic_review=True,
            )
        )
        return SemanticMessageReview(constraint_id=target.constraint_id, equivalent=bool(payload["equivalent"]), confidence=float(payload["confidence"]), reason=str(payload.get("reason") or ""), raw_payload=payload)

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
