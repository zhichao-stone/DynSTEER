from __future__ import annotations

from typing import Protocol

from dynsteer.model import (
    Dimension,
    EvaluationLevel,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
)


class Judge(Protocol):
    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """评估单个阶段。

        Args:
            interval: 阶段区间。
            task_case: 当前任务。
            trajectory: Agent 轨迹。
            level: 本轮评估粒度。
            weights: 当前维度权重。

        Returns:
            阶段评估结果。
        """
        ...


class LocalJudge:
    """仅用于 cheap 层的本地结构化评估器。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """使用结构化分数生成确定性阶段评估结果。

        Args:
            interval: 阶段区间。
            task_case: 当前任务。
            trajectory: Agent 轨迹。
            level: 评估粒度。
            weights: 当前维度权重。

        Returns:
            不访问网络的本地评估结果。
        """
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("LocalJudge 入参不能为空")
        score = 0.0
        missing_ratio = 1.0
        hard_pass = False
        evidence = list(interval.evidence)
        if interval.milestone_score is not None:
            score = interval.milestone_score.score
            missing_ratio = interval.milestone_score.missing_ratio
            hard_pass = interval.milestone_score.hard_constraints_all_pass
        elif interval.status == StageStatus.MISSING:
            evidence.append("阶段缺少 milestone 匹配")
        elif interval.status == StageStatus.PASS:
            score = 1.0
            missing_ratio = 0.0
            hard_pass = True
        else:
            score = 0.0
        dimension_scores = self._dimension_scores(score, interval.status, weights)
        confidence = self._confidence(level, interval.status, missing_ratio)
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            evaluator_level=level,
            status=interval.status,
            stage_score=score,
            uncertainty=0.0,
            dimension_scores=dimension_scores,
            evidence=evidence,
            diagnosis=self._diagnosis(interval.status, score, missing_ratio),
            hard_constraints_all_pass=hard_pass,
            required_fields_missing_ratio=missing_ratio,
            judge_confidence=confidence,
        )

    def _dimension_scores(
        self,
        score: float,
        status: StageStatus,
        weights: dict[Dimension, float],
    ) -> dict[Dimension, float]:
        base = max(0.0, min(score, 1.0))
        result = {dimension: base for dimension in Dimension}
        if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
            result[Dimension.PROGRESS] = min(base, 0.2)
            result[Dimension.RECOVERY] = min(base, 0.3)
        if Dimension.SAFETY not in weights:
            result[Dimension.SAFETY] = base
        return result

    def _confidence(self, level: EvaluationLevel, status: StageStatus, missing_ratio: float) -> float:
        base = {
            EvaluationLevel.CHEAP: 0.75,
            EvaluationLevel.STANDARD: 0.85,
            EvaluationLevel.EXPENSIVE: 0.92,
        }[level]
        if status in {StageStatus.MISSING, StageStatus.AMBIGUOUS, StageStatus.INVALID}:
            base -= 0.25
        base -= min(max(missing_ratio, 0.0), 1.0) * 0.25
        return max(0.0, min(base, 1.0))

    def _diagnosis(self, status: StageStatus, score: float, missing_ratio: float) -> list[str]:
        diagnosis: list[str] = []
        if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
            diagnosis.append("阶段未达成预期 milestone")
        if missing_ratio > 0:
            diagnosis.append("存在未命中的必要字段或状态")
        if score < 0.6:
            diagnosis.append("阶段完成度偏低")
        return diagnosis
