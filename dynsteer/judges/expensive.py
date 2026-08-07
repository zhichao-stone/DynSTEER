from collections.abc import Iterable
from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.prompt.judge import build_judge_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_payload_output_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, StageEvaluationResult, StageInterval, TaskCase, Trajectory, ValidatedJudgePayload
from dynsteer.utils import validated_target_dimensions

class ExpensiveJudge(LLMJudge):
    """expensive 粒度的逐维专项 LLM-as-a-Judge。"""

    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """对目标维度逐一执行多轮专项评估并合并结果。"""
        language = language_from_task(task_case)
        target_dimensions = validated_target_dimensions(dimensions)
        all_payloads = []
        pass_metadata = []
        for dimension in target_dimensions:
            prompt = build_judge_prompt(f"expensive/{dimension.value}", interval, task_case, trajectory, language=language, target_dimensions=[dimension])
            input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt, prompt_type=f"expensive:{dimension.value}")
            input_metadata["target_dimensions"] = [dimension.value]
            for pass_index in range(1, self._passes + 1):
                raw = self._validate_payload(self._call_json(prompt, language=language), [dimension])
                validated = ValidatedJudgePayload(
                    status=raw.status,
                    dimension_scores=raw.dimension_scores,
                    evidence=raw.evidence,
                    diagnosis=raw.diagnosis,
                    metadata={
                        **raw.metadata,
                        "judge_pass_index": pass_index,
                        "judge_dimension": dimension.value,
                    },
                )
                all_payloads.append(validated)
                pass_metadata.append(self._pass_metadata(validated, input_metadata))
        payload = aggregate_judge_payload(all_payloads, target_dimensions)
        metadata = {"judge_passes": pass_metadata, "target_dimensions": [dimension.value for dimension in target_dimensions]}
        result = self._result_from_payload(interval, EvaluationLevel.EXPENSIVE, payload, metadata=metadata, dimension_confidence=agreement_confidence(all_payloads, target_dimensions))
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def _pass_metadata(self, payload: ValidatedJudgePayload, input_metadata: dict[str, object]) -> dict[str, object]:
        """生成中间复核轮次的结构化 metadata。"""
        return {
            **input_metadata,
            "prompt_type": f"expensive:{payload.metadata.get('judge_dimension')}",
            "status": payload.status.value,
            "diagnosis": list(payload.diagnosis),
            **judge_payload_output_metadata(payload, input_metadata),
        }
