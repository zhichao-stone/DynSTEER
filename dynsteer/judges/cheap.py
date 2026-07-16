from __future__ import annotations

from dynsteer.evaluate.quality import build_stage_quality_diagnostics
from collections.abc import Iterable

from dynsteer.evaluate.scoring import stage_score_from_dimensions
from dynsteer.judges.base import BaseJudge
from dynsteer.judges.confidence import cheap_dimension_confidence, uncertainty_from_confidence
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
from dynsteer.utils import clamp


class CheapJudge(BaseJudge):
    """仅用于 cheap 层的本地结构化评估器。"""

    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        dimensions: Iterable[Dimension] | None = None,
    ) -> StageEvaluationResult:
        """使用结构化分数生成确定性阶段评估结果。"""
        if interval is None or task_case is None or trajectory is None or weights is None:
            raise ValueError("CheapJudge 入参不能为空")
        
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

        diagnostics = build_stage_quality_diagnostics(interval, trajectory)
        target_dimensions = list(dict.fromkeys(dimensions or list(Dimension)))
        dimension_scores = {
            dimension: value
            for dimension, value in self._dimension_scores(score, interval.status, diagnostics).items()
            if dimension in target_dimensions
        }
        stage_score = stage_score_from_dimensions(dimension_scores, weights)
        dimension_confidence = {
            dimension: value
            for dimension, value in cheap_dimension_confidence(interval.status, missing_ratio, diagnostics).items()
            if dimension in target_dimensions
        }
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            status=interval.status,
            stage_score=stage_score,
            dimension_scores=dimension_scores,
            dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in dimension_scores},
            dimension_confidence=dimension_confidence,
            dimension_uncertainty=uncertainty_from_confidence(dimension_confidence),
            evidence=evidence,
            diagnosis=self._diagnosis(interval.status, score, missing_ratio),
            hard_constraints_all_pass=hard_pass,
            required_fields_missing_ratio=missing_ratio,
            metadata={
                "stage_quality_diagnostics": diagnostics,
            },
        )

    def _dimension_scores(
        self,
        progress_score: float,
        status: StageStatus,
        diagnostics: JsonObject,
    ) -> dict[Dimension, float]:
        progress = clamp(progress_score)
        base = self._base_quality_score(status)
        failed_count = len(diagnostics.get("failed_tool_results", []))
        empty_warning_count = sum(
            1
            for item in diagnostics.get("empty_tool_results", [])
            if isinstance(item, dict) and item.get("severity") == "warning"
        )
        argument_warning_count = len(diagnostics.get("tool_argument_warnings", []))
        grounding_warning_count = len(diagnostics.get("grounding_warnings", []))
        efficiency = diagnostics.get("efficiency", {})
        extra_user_turns = (
            int(efficiency.get("extra_user_turns_before_first_tool_call") or 0)
            if isinstance(efficiency, dict)
            else 0
        )
        step_count = int(diagnostics.get("step_count") or 0)
        result = {
            Dimension.PROGRESS: progress,
            Dimension.STATE_CONSISTENCY: clamp(base - 0.10 * failed_count),
            Dimension.TOOL_QUALITY: clamp(
                base - 0.18 * failed_count - 0.12 * empty_warning_count - 0.08 * argument_warning_count
            ),
            Dimension.EFFICIENCY: clamp(base - 0.10 * extra_user_turns - 0.02 * max(step_count - 12, 0)),
            Dimension.SAFETY: clamp(base - 0.15 * failed_count),
            Dimension.INTERACTION_QUALITY: clamp(base - 0.08 * grounding_warning_count),
            Dimension.RECOVERY: clamp(base - 0.20 * failed_count),
        }
        if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
            result[Dimension.PROGRESS] = min(progress, 0.2)
            result[Dimension.RECOVERY] = min(result[Dimension.RECOVERY], 0.3)
        return result

    def _diagnosis(self, status: StageStatus, score: float, missing_ratio: float) -> list[str]:
        diagnosis: list[str] = []
        if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
            diagnosis.append("阶段未达成预期 milestone")
        if missing_ratio > 0:
            diagnosis.append("存在未命中的必要字段或状态")
        if score < 0.6:
            diagnosis.append("阶段完成度偏低")
        return diagnosis

    def _base_quality_score(self, status: StageStatus) -> float:
        """根据阶段结构状态给非 progress 维度提供低成本基础分。"""
        if status == StageStatus.PASS:
            return 0.85
        if status == StageStatus.WARN:
            return 0.65
        if status == StageStatus.AMBIGUOUS:
            return 0.55
        return 0.35
