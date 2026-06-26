from __future__ import annotations

from dynsteer.judges.base import LLMJudge
from dynsteer.judges.prompt import build_standard_prompt
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
        """在单轮 LLM 调用中完成 standard 阶段评估。

        Args:
            interval: 阶段区间。
            task_case: 当前任务。
            trajectory: Agent 轨迹。
            weights: 当前维度权重。

        Returns:
            固定 evaluator_level 为 standard 的阶段评估结果。
        """
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("StandardJudge 入参不能为空")
        
        language = language_from_task(task_case)
        prompt = build_standard_prompt(
            interval=interval,
            task_case=task_case,
            trajectory=trajectory,
            weights=weights,
            language=language,
        )
        payload = self._call_json(prompt, language=language)
        return self._result_from_payload(interval, EvaluationLevel.STANDARD, payload, metadata={})
