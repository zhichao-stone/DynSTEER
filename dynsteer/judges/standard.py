from __future__ import annotations

from collections.abc import Iterable

from dynsteer.judges.base import LLMJudge
from dynsteer.judges.confidence import (
    aggregate_dimension_scores,
    aggregate_status,
    agreement_confidence,
    merge_text_items,
)
from dynsteer.prompt.judge import build_judge_prompt
from dynsteer.judges.telemetry import judge_input_metadata, judge_result_output_metadata
from dynsteer.language import language_from_task
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    StageEvaluationResult,
    StageInterval,
    TaskCase,
    Trajectory,
)

class StandardJudge(LLMJudge):
    """standard 粒度的单轮 LLM-as-a-Judge。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """在单轮 LLM 调用中完成 standard 阶段评估。"""
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("StandardJudge 入参不能为空")

        language = language_from_task(task_case)
        target_dimensions = list(dict.fromkeys(dimensions or list(Dimension)))
        prompt = build_judge_prompt(
            "standard",
            interval,
            task_case,
            trajectory,
            weights,
            language=language,
            target_dimensions=target_dimensions,
        )
        input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt)
        input_metadata["target_dimensions"] = [dimension.value for dimension in target_dimensions]
        payloads = [self._call_json(prompt, language=language) for _ in range(self._config.standard_passes)]
        for payload in payloads:
            self._validate_payload(payload, target_dimensions)
        payload = {
            "status": aggregate_status(payloads).value,
            "dimension_scores": {
                dimension.value: score
                for dimension, score in aggregate_dimension_scores(payloads, target_dimensions).items()
            },
            "evidence": merge_text_items(payloads, "evidence"),
            "diagnosis": merge_text_items(payloads, "diagnosis"),
            "metadata": {"judge_pass_count": len(payloads)},
        }
        result = self._result_from_payload(
            interval,
            EvaluationLevel.STANDARD,
            payload,
            weights=weights,
            metadata=input_metadata,
            dimensions=target_dimensions,
            dimension_confidence=agreement_confidence(payloads, target_dimensions),
        )
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result
