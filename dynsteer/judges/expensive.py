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
from dynsteer.judges.telemetry import (
    judge_input_metadata,
    judge_payload_output_metadata,
    judge_result_output_metadata,
)
from dynsteer.language import language_from_task
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    StageEvaluationResult,
    StageInterval,
    TaskCase,
    Trajectory,
)


class ExpensiveJudge(LLMJudge):
    """expensive 粒度的逐维专项 LLM-as-a-Judge。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """对目标维度逐一执行多轮专项评估并合并结果。"""
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("ExpensiveJudge 入参不能为空")
        
        language = language_from_task(task_case)
        target_dimensions = list(dict.fromkeys(dimensions or list(Dimension)))
        all_payloads = []
        pass_metadata = []
        for dimension in target_dimensions:
            prompt = build_judge_prompt(
                f"expensive/{dimension.value}",
                interval,
                task_case,
                trajectory,
                weights,
                language=language,
                target_dimensions=[dimension],
            )
            input_metadata = judge_input_metadata(
                interval,
                task_case,
                trajectory,
                prompt,
                prompt_type=f"expensive:{dimension.value}",
            )
            input_metadata["target_dimensions"] = [dimension.value]
            payloads = [self._call_json(prompt, language=language) for _ in range(self._config.expensive_passes)]
            for payload in payloads:
                self._validate_payload(payload, [dimension])
                payload["prompt_type"] = f"expensive:{dimension.value}"
                payload["target_dimensions"] = [dimension.value]
                all_payloads.append(payload)
                pass_metadata.append(self._pass_metadata(payload, weights, input_metadata, [dimension]))

        payload = {
            "status": aggregate_status(all_payloads).value,
            "dimension_scores": {
                dimension.value: score
                for dimension, score in aggregate_dimension_scores(all_payloads, target_dimensions).items()
            },
            "evidence": merge_text_items(all_payloads, "evidence"),
            "diagnosis": merge_text_items(all_payloads, "diagnosis"),
            "metadata": {"judge_pass_count": len(all_payloads)},
        }
        metadata = {
            "judge_passes": pass_metadata,
            "target_dimensions": [dimension.value for dimension in target_dimensions],
        }
        result = self._result_from_payload(
            interval,
            EvaluationLevel.EXPENSIVE,
            payload,
            weights=weights,
            metadata=metadata,
            dimensions=target_dimensions,
            dimension_confidence=agreement_confidence(all_payloads, target_dimensions),
        )
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result

    def _pass_metadata(
        self,
        payload: dict[str, object],
        weights: dict[Dimension, float],
        input_metadata: dict[str, object],
        dimensions: list[Dimension],
    ) -> dict[str, object]:
        """生成中间复核轮次的结构化 metadata。"""
        validated = self._validate_payload(payload, dimensions)
        stage_score = self._stage_score_from_dimensions(validated.dimension_scores, weights)
        metadata: dict[str, object] = {
            **input_metadata,
            "prompt_type": payload.get("prompt_type"),
            "stage_score": stage_score,
            "status": validated.status.value,
            "diagnosis": list(validated.diagnosis),
            **judge_payload_output_metadata(payload, stage_score, input_metadata),
        }
        return metadata
