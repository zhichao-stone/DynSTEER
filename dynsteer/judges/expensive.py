from __future__ import annotations

import logging
from dataclasses import replace

from dynsteer.judges.base import LLMJudge
from dynsteer.judges.prompt import (
    build_expensive_adjudication_prompt,
    build_expensive_focus_prompt,
    build_expensive_risk_prompt,
)
from dynsteer.judges.telemetry import (
    judge_input_metadata,
    judge_payload_output_metadata,
    judge_result_output_metadata,
)
from dynsteer.language import language_from_task
from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    JsonObject,
    StageEvaluationResult,
    StageInterval,
    TaskCase,
    Trajectory,
)

logger = logging.getLogger(__name__)

_FOCUS_GROUPS = (
    "progress,state_consistency",
    "tool_quality,efficiency,recovery",
    "safety,interaction_quality",
)


class ExpensiveJudge(LLMJudge):
    """expensive 粒度的多轮聚焦 LLM-as-a-Judge。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """通过多轮聚焦评估与一次汇总裁决完成 expensive 阶段评估。

        Args:
            interval: 阶段区间。
            task_case: 当前任务。
            trajectory: Agent 轨迹。
            weights: 当前维度权重。

        Returns:
            固定 evaluator_level 为 expensive 且记录多轮 metadata 的阶段评估结果。
        """
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("ExpensiveJudge 入参不能为空")
        
        language = language_from_task(task_case)
        passes: list[JsonObject] = []
        pass_metadata: list[JsonObject] = []
        for index in range(self._config.expensive_passes):
            group = _FOCUS_GROUPS[index % len(_FOCUS_GROUPS)]
            prompt = build_expensive_focus_prompt(
                interval=interval,
                task_case=task_case,
                trajectory=trajectory,
                weights=weights,
                focus_dimensions=group,
                language=language,
            )
            input_metadata = judge_input_metadata(interval, task_case, trajectory, prompt, prompt_type="focus")
            input_metadata["focus_dimensions"] = group
            logger.info("expensive_judge_input_snapshot", extra=input_metadata)
            payload = self._call_json(prompt, language=language)
            payload["prompt_type"] = "focus"
            payload["focus_dimensions"] = group
            passes.append(payload)
            metadata = self._pass_metadata(payload, weights, input_metadata)
            pass_metadata.append(metadata)
            logger.info("expensive_judge_output_snapshot", extra={**input_metadata, **metadata})

        risk_prompt = build_expensive_risk_prompt(
            interval=interval,
            task_case=task_case,
            trajectory=trajectory,
            weights=weights,
            language=language,
        )
        risk_input_metadata = judge_input_metadata(interval, task_case, trajectory, risk_prompt, prompt_type="risk")
        logger.info("expensive_judge_input_snapshot", extra=risk_input_metadata)
        risk_payload = self._call_json(risk_prompt, language=language)
        risk_payload["prompt_type"] = "risk"
        passes.append(risk_payload)
        risk_metadata = self._pass_metadata(risk_payload, weights, risk_input_metadata)
        pass_metadata.append(risk_metadata)
        logger.info("expensive_judge_output_snapshot", extra={**risk_input_metadata, **risk_metadata})

        adjudicator_prompt = build_expensive_adjudication_prompt(
            interval=interval,
            task_case=task_case,
            trajectory=trajectory,
            weights=weights,
            previous_passes=passes,
            language=language,
        )
        input_metadata = judge_input_metadata(
            interval,
            task_case,
            trajectory,
            adjudicator_prompt,
            prompt_type="adjudication",
        )
        logger.info("expensive_judge_input_snapshot", extra=input_metadata)
        payload = self._call_json(adjudicator_prompt, language=language)
        metadata: JsonObject = {
            "judge_passes": pass_metadata,
            **input_metadata,
        }
        result = self._result_from_payload(interval, EvaluationLevel.EXPENSIVE, payload, weights=weights, metadata=metadata)
        output_metadata = judge_result_output_metadata(result, input_metadata)
        logger.info("expensive_judge_output_snapshot", extra={**input_metadata, **output_metadata})
        return replace(result, metadata={**result.metadata, **output_metadata})

    def _pass_metadata(
        self,
        payload: JsonObject,
        weights: dict[Dimension, float],
        input_metadata: JsonObject,
    ) -> JsonObject:
        """生成中间复核轮次的结构化 metadata。

        Args:
            payload: 已通过 LLMJudge 基础校验的单轮复核 payload。
            weights: 当前维度权重，用于本地计算该轮总分。
            input_metadata: 当前轮次调用前的输入快照。

        Returns:
            可写入最终结果 metadata 的 JSON 对象。
        """
        validated = self._validate_payload(payload)
        stage_score = self._stage_score_from_dimensions(validated.dimension_scores, weights)
        metadata: JsonObject = {
            **input_metadata,
            "prompt_type": payload.get("prompt_type"),
            "stage_score": stage_score,
            "status": validated.status.value,
            "judge_confidence": validated.judge_confidence,
            "diagnosis": list(validated.diagnosis),
            **judge_payload_output_metadata(payload, stage_score, input_metadata),
        }
        focus_dimensions = payload.get("focus_dimensions")
        if focus_dimensions is not None:
            metadata["focus_dimensions"] = focus_dimensions
        return metadata
