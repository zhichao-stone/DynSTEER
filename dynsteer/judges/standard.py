from __future__ import annotations

from dynsteer.judges.base import LLMJudge
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
    ) -> StageEvaluationResult:
        """在单轮 LLM 调用中完成 standard 阶段评估。"""
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("StandardJudge 入参不能为空")

        language = language_from_task(task_case)
        prompt = build_judge_prompt("standard", interval, task_case, trajectory, weights, language=language)
        input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt)
        payload = self._call_json(prompt, language=language)
        result = self._result_from_payload(
            interval,
            EvaluationLevel.STANDARD,
            payload,
            weights=weights,
            metadata=input_metadata,
        )
        output_metadata = judge_result_output_metadata(result, input_metadata)
        result.metadata.update(output_metadata)
        return result
