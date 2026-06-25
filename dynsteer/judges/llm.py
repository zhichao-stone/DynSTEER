from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping
from openai import OpenAI

from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    JsonObject,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


class LLMJudgeConfigurationError(ValueError):
    """LLMJudge 配置缺失或不合法时抛出。"""


class LLMJudgeResponseError(ValueError):
    """LLMJudge 返回内容无法解析时抛出。"""


@dataclass(frozen=True)
class LLMJudgeConfig:
    """LLMJudge 运行配置。"""

    model: str
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    expensive_passes: int = 3


class LLMJudge:
    """基于 OpenAI-compatible API 的 LLM-as-a-Judge。"""

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
        expensive_passes: int = 3,
        client: object | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise LLMJudgeConfigurationError("LLMJudge model 不能为空")
        if expensive_passes < 1:
            raise LLMJudgeConfigurationError("expensive_passes 必须大于 0")
        self._config = LLMJudgeConfig(
            model=model.strip(),
            base_url=base_url.strip() if isinstance(base_url, str) and base_url.strip() else None,
            api_key=api_key.strip() if isinstance(api_key, str) and api_key.strip() else None,
            timeout_seconds=float(timeout_seconds),
            temperature=float(temperature),
            expensive_passes=int(expensive_passes),
        )
        self._client = client or self._create_client()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMJudge | None":
        """从环境变量创建 LLMJudge。

        Args:
            env: 环境变量映射；测试时可传入 fake env。

        Returns:
            未配置 provider 时返回 None；配置 provider 时返回 LLMJudge。
        """
        source = env or os.environ
        provider = source.get("DYNSTEER_JUDGE_PROVIDER")
        if provider is None or not provider.strip():
            return None
        if provider.strip().lower() != "openai_compatible":
            raise LLMJudgeConfigurationError(f"不支持的 judge provider: {provider}")
        model = source.get("DYNSTEER_JUDGE_MODEL")
        if model is None or not model.strip():
            raise LLMJudgeConfigurationError("DYNSTEER_JUDGE_MODEL 不能为空")
        timeout = float(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS", "60"))
        temperature = float(source.get("DYNSTEER_JUDGE_TEMPERATURE", "0"))
        expensive_passes = int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3"))
        return cls(
            model=model,
            base_url=source.get("DYNSTEER_JUDGE_BASE_URL"),
            api_key=source.get("DYNSTEER_JUDGE_API_KEY"),
            timeout_seconds=timeout,
            temperature=temperature,
            expensive_passes=expensive_passes,
        )

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """使用 LLM 评估单个阶段。

        Args:
            interval: 阶段区间。
            task_case: 当前任务。
            trajectory: Agent 轨迹。
            level: standard 或 expensive 评估粒度。
            weights: 当前维度权重。

        Returns:
            阶段评估结果。
        """
        if interval is None or task_case is None or trajectory is None or level is None or weights is None:
            raise ValueError("LLMJudge 入参不能为空")
        if level == EvaluationLevel.EXPENSIVE:
            return self._evaluate_expensive(interval, task_case, trajectory, weights)
        return self._evaluate_standard(interval, task_case, trajectory, level, weights)

    def _create_client(self) -> object:
        kwargs: dict[str, object] = {"timeout": self._config.timeout_seconds}
        if self._config.api_key is not None:
            kwargs["api_key"] = self._config.api_key
        if self._config.base_url is not None:
            kwargs["base_url"] = self._config.base_url
        return OpenAI(**kwargs)

    def _evaluate_standard(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        prompt = self._build_prompt(interval, task_case, trajectory, level, weights)
        payload = self._call_json(prompt)
        return self._result_from_payload(interval, level, payload, metadata={})

    def _evaluate_expensive(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        passes: list[JsonObject] = []
        groups = [
            "progress,state_consistency",
            "tool_quality,efficiency,recovery",
            "safety,interaction_quality",
        ]
        for index in range(self._config.expensive_passes):
            group = groups[index % len(groups)]
            prompt = self._build_prompt(
                interval,
                task_case,
                trajectory,
                EvaluationLevel.EXPENSIVE,
                weights,
                focus=f"请重点评估维度: {group}",
            )
            passes.append(self._call_json(prompt))
        adjudicator_prompt = self._build_prompt(
            interval,
            task_case,
            trajectory,
            EvaluationLevel.EXPENSIVE,
            weights,
            focus=f"请汇总以下多轮评估并输出最终 JSON: {json.dumps(passes, ensure_ascii=False)}",
        )
        payload = self._call_json(adjudicator_prompt)
        metadata: JsonObject = {
            "judge_passes": [
                {
                    "stage_score": item.get("stage_score"),
                    "status": item.get("status"),
                    "judge_confidence": item.get("judge_confidence"),
                    "diagnosis": item.get("diagnosis", []),
                }
                for item in passes
            ]
        }
        return self._result_from_payload(interval, EvaluationLevel.EXPENSIVE, payload, metadata=metadata)

    def _call_json(self, prompt: str) -> JsonObject:
        response = self._client.chat.completions.create(
            model=self._config.model,
            temperature=self._config.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "你是 DynSTEER 的 LLM-as-a-Judge，只能输出 JSON 对象。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = self._response_text(response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMJudgeResponseError("LLMJudge 返回内容不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise LLMJudgeResponseError("LLMJudge 返回内容必须是 JSON 对象")
        return data

    def _response_text(self, response: object) -> str:
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
        raise LLMJudgeResponseError("LLMJudge 返回内容缺少 message.content")

    def _build_prompt(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
        focus: str | None = None,
    ) -> str:
        stage_steps = [
            step
            for step in trajectory.steps
            if interval.start_step_index <= step.index <= interval.end_step_index
            or interval.start_step_index < 0
        ]
        data = {
            "evaluation_level": level.value,
            "task": {
                "task_id": task_case.task_id,
                "task_description": task_case.task_description,
                "task_types": [item.value for item in task_case.task_types],
            },
            "interval": {
                "stage_id": interval.stage_id,
                "milestone_id": interval.milestone_id,
                "start_step_index": interval.start_step_index,
                "end_step_index": interval.end_step_index,
                "status": interval.status.value,
                "evidence": list(interval.evidence),
            },
            "steps": [
                {
                    "index": step.index,
                    "actor": step.actor.value,
                    "event_type": step.event_type.value,
                    "content": step.content,
                }
                for step in stage_steps
            ],
            "weights": {dimension.value: value for dimension, value in weights.items()},
            "rubric_dimensions": [dimension.value for dimension in Dimension],
            "required_output": {
                "dimension_scores": "dict[str,float] covering all rubric_dimensions",
                "stage_score": "float in [0,1]",
                "status": "pass|warn|fail|missing|ambiguous|invalid",
                "judge_confidence": "float in [0,1]",
                "evidence": "list[str]",
                "diagnosis": "list[str]",
                "needs_expensive": "bool",
                "first_error_location_required": "bool",
            },
        }
        if focus is not None:
            data["focus"] = focus
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _result_from_payload(
        self,
        interval: StageInterval,
        level: EvaluationLevel,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> StageEvaluationResult:
        try:
            status = StageStatus(str(payload["status"]))
        except (KeyError, ValueError) as exc:
            raise LLMJudgeResponseError("LLMJudge 返回 status 非法") from exc
        dimension_scores = self._dimension_scores(payload.get("dimension_scores"))
        stage_score = self._float_in_unit(payload.get("stage_score"), "stage_score")
        judge_confidence = self._float_in_unit(payload.get("judge_confidence"), "judge_confidence")
        evidence = self._string_list(payload.get("evidence"), "evidence")
        diagnosis = self._string_list(payload.get("diagnosis"), "diagnosis")
        result_metadata = dict(metadata)
        result_metadata["needs_expensive"] = bool(payload.get("needs_expensive", False))
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            evaluator_level=level,
            status=status,
            stage_score=stage_score,
            uncertainty=0.0,
            dimension_scores=dimension_scores,
            evidence=evidence,
            diagnosis=diagnosis,
            judge_confidence=judge_confidence,
            first_error_location_required=bool(payload.get("first_error_location_required", False)),
            metadata=result_metadata,
        )

    def _dimension_scores(self, value: object) -> dict[Dimension, float]:
        if not isinstance(value, dict):
            raise LLMJudgeResponseError("dimension_scores 必须是对象")
        scores: dict[Dimension, float] = {}
        for dimension in Dimension:
            scores[dimension] = self._float_in_unit(value.get(dimension.value), f"dimension_scores.{dimension.value}")
        return scores

    def _float_in_unit(self, value: object, label: str) -> float:
        if not isinstance(value, int | float):
            raise LLMJudgeResponseError(f"{label} 必须是数字")
        number = float(value)
        if number < 0.0 or number > 1.0:
            raise LLMJudgeResponseError(f"{label} 必须在 [0,1] 区间")
        return number

    def _string_list(self, value: object, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise LLMJudgeResponseError(f"{label} 必须是数组")
        return [str(item) for item in value]
