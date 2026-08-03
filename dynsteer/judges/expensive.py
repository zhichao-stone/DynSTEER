from collections.abc import Iterable
from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.prompt.judge import build_judge_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_payload_output_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, StageEvaluationResult, StageInterval, TaskCase, Trajectory

class ExpensiveJudge(LLMJudge):
    """expensive 粒度的逐维专项 LLM-as-a-Judge。"""

    def evaluate_stage(self, interval: StageInterval, task_case: TaskCase, trajectory: Trajectory, dimensions: Iterable[Dimension] | None=None) -> StageEvaluationResult:
        """对目标维度逐一执行多轮专项评估并合并结果。"""
        language = language_from_task(task_case)
        target_dimensions = self._target_dimensions(dimensions)
        all_payloads = []
        pass_metadata = []
        for dimension in target_dimensions:
            prompt = build_judge_prompt(f"expensive/{dimension.value}", interval, task_case, trajectory, language=language, target_dimensions=[dimension])
            input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt, prompt_type=f"expensive:{dimension.value}")
            input_metadata["target_dimensions"] = [dimension.value]
            cache_context = {
                "case_id": task_case.case_id,
                "model_id": self._model_id(),
                "prompt_type": f"expensive:{dimension.value}",
                "stage_id": interval.stage_id,
                "milestone_id": interval.milestone_id,
                "trajectory_prefix": [interval.start_step_index, interval.end_step_index],
                "boundary": interval.end_step_index,
                "focus_dimensions": [dimension.value],
                "language": language.value,
            }
            payloads = [
                self._cached_call_json(prompt, cache_context, language=language)
                for _ in range(self._passes)
            ]
            for payload in payloads:
                self._validate_payload(payload, [dimension])
                payload["prompt_type"] = f"expensive:{dimension.value}"
                payload["target_dimensions"] = [dimension.value]
                all_payloads.append(payload)
                pass_metadata.append(self._pass_metadata(payload, input_metadata, [dimension]))
        payload = aggregate_judge_payload(all_payloads, target_dimensions)
        metadata = {"judge_passes": pass_metadata, "target_dimensions": [dimension.value for dimension in target_dimensions]}
        result = self._result_from_payload(interval, EvaluationLevel.EXPENSIVE, payload, metadata=metadata, dimensions=target_dimensions, dimension_confidence=agreement_confidence(all_payloads, target_dimensions))
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def _pass_metadata(self, payload: dict[str, object], input_metadata: dict[str, object], dimensions: list[Dimension]) -> dict[str, object]:
        """生成中间复核轮次的结构化 metadata。"""
        validated = self._validate_payload(payload, dimensions)
        return {
            **input_metadata,
            "prompt_type": payload.get("prompt_type"),
            "status": validated.status.value,
            "diagnosis": list(validated.diagnosis),
            **judge_payload_output_metadata(payload, input_metadata),
        }
