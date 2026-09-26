from collections.abc import Iterable
from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.evaluate.semantic import SemanticMessageReview, SemanticMessageReviewTarget
from dynsteer.prompt.judge import build_judge_prompt, build_semantic_message_equivalence_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, JsonObject, StageEvaluationResult, StageInterval, TaskCase, Trajectory, ValidatedJudgePayload
from dynsteer.utils import validated_target_dimensions

class StandardJudge(LLMJudge):
    """Multiple rounds of independent LLM-as-a-Judge at the size of a grain."""

    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """Complete the evaluation of the standard stage through multiple rounds of independent LLM calls."""
        language = language_from_task(task_case)
        target_dimensions = validated_target_dimensions(dimensions)
        prompt = build_judge_prompt("standard", interval, task_case, trajectory, language=language, target_dimensions=target_dimensions)
        input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt)
        input_metadata["target_dimensions"] = [dimension.value for dimension in target_dimensions]
        payloads = []
        for pass_index in range(1, self._passes + 1):
            validated = self._validate_payload(self._call_json(prompt, language=language), target_dimensions)
            payloads.append(
                ValidatedJudgePayload(
                    status=validated.status,
                    dimension_scores=validated.dimension_scores,
                    evidence=validated.evidence,
                    diagnosis=validated.diagnosis,
                    metadata={**validated.metadata, "judge_pass_index": pass_index},
                )
            )
        payload = aggregate_judge_payload(payloads, target_dimensions)
        result = self._result_from_payload(interval, EvaluationLevel.STANDARD, payload, metadata=input_metadata, dimension_confidence=agreement_confidence(payloads, target_dimensions))
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def review_message_equivalence(self, target: SemanticMessageReviewTarget, *, task_case: TaskCase) -> SemanticMessageReview:
        """Select whether the semantic equivalent value of a single user's visible message is available. Args: target: Single message semantic correction of target. task_case: Current task to provide semantic background and language selection for the task. Returns:`equivalent/confidence/reason`The form of narrow area review results."""
        if target is None or task_case is None:
            raise ValueError('Target and task_case cannot be empty')
        language = language_from_task(task_case)
        target_data = target.to_dict()
        prompt = build_semantic_message_equivalence_prompt(task_case, target_data, language=language)
        payload = self._semantic_payload(self._call_json(prompt, language=language))
        return SemanticMessageReview(constraint_id=target.constraint_id, equivalent=bool(payload["equivalent"]), confidence=float(payload["confidence"]), reason=str(payload.get("reason") or ""), raw_payload=payload)

    def _semantic_payload(self, payload: JsonObject) -> JsonObject:
        """Check the semantic equivalent of the message."""
        if not isinstance(payload.get("equivalent"), bool):
            raise ValueError('Second message judge returns equivalent must be bool')
        confidence = self._float_in_unit(payload.get("confidence"), "confidence")
        return {
            "equivalent": bool(payload["equivalent"]),
            "confidence": confidence,
            "reason": str(payload.get("reason") or ""),
        }
