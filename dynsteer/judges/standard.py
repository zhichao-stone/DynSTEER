from collections.abc import Iterable

from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import aggregate_judge_payload, agreement_confidence
from dynsteer.prompt.judge import build_judge_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import Dimension, EvaluationLevel, StageEvaluationResult, StageInterval, TaskCase, Trajectory


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
