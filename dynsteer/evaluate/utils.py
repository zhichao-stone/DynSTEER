from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from dynsteer.config import ThresholdConfig
from dynsteer.model import (
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.utils import clamp

if TYPE_CHECKING:
    from dynsteer.adapter.base import BaseBenchmarkHarness


def compute_uncertainty(
    top1_score: float,
    top2_score: float,
    missing_ratio: float,
    stage_score: float,
    evidence_conflict: bool,
    judge_uncertainty: float,
    thresholds: Optional[ThresholdConfig] = None,
) -> float:
    """计算阶段评估不确定性。

    Args:
        top1_score: 最优候选分数。
        top2_score: 次优候选分数。
        missing_ratio: 必要字段缺失比例。
        stage_score: 阶段总分。
        evidence_conflict: 证据是否冲突。
        judge_uncertainty: judge 自身不确定性。
        thresholds: 阈值配置。

    Returns:
        `[0, 1]` 区间的不确定性。
    """
    effective_thresholds = thresholds or ThresholdConfig()
    margin = max(top1_score - top2_score, 0.0)
    u_margin = 1.0 - clamp(margin / 0.3)
    u_missing = clamp(missing_ratio)
    threshold_distance = min(
        abs(stage_score - effective_thresholds.pass_threshold),
        abs(stage_score - effective_thresholds.warn_threshold),
        abs(stage_score - effective_thresholds.fail_threshold),
    )
    u_threshold = 1.0 - clamp(threshold_distance / effective_thresholds.threshold_margin)
    u_conflict = 1.0 if evidence_conflict else 0.0
    u_judge = clamp(judge_uncertainty)
    return clamp(
        0.30 * u_margin
        + 0.25 * u_missing
        + 0.20 * u_threshold
        + 0.15 * u_conflict
        + 0.10 * u_judge
    )


def overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    """根据阶段分数与 minefield 分数计算整体得分。

    Args:
        stage_reports: 阶段评估结果列表。
        minefield_score: 最高 minefield 分数。

    Returns:
        `[0, 1]` 区间的整体得分。
    """
    if not stage_reports:
        return 1.0 if minefield_score == 0 else 0.0
    raw = sum(stage.stage_score for stage in stage_reports) / len(stage_reports)
    return clamp(raw * (1.0 - clamp(minefield_score)))


def enrich_stage_result(
    interval: StageInterval,
    result: StageEvaluationResult,
    minefield_score: float,
    fatal_minefield: bool,
    thresholds: ThresholdConfig,
) -> StageEvaluationResult:
    """根据 minefield 结果与阈值补全阶段评估的不确定性与风险分数。

    Args:
        interval: 阶段区间。
        result: 已有阶段评估结果。
        minefield_score: 当前轨迹最高 minefield 分数。
        fatal_minefield: 是否触发 fatal minefield。
        thresholds: 阈值配置。

    Returns:
        补全不确定性与 minefield 分数后的阶段评估结果。
    """
    if interval is None or result is None or thresholds is None:
        raise ValueError("enrich_stage_result 入参不能为空")
    top1 = interval.milestone_score.score if interval.milestone_score is not None else result.stage_score
    uncertainty = compute_uncertainty(
        top1_score=top1,
        top2_score=0.0,
        missing_ratio=result.required_fields_missing_ratio,
        stage_score=result.stage_score,
        evidence_conflict=False,
        judge_uncertainty=1.0 - result.judge_confidence,
        thresholds=thresholds,
    )
    result.uncertainty = uncertainty
    result.minefield_score = minefield_score
    result.fatal_minefield_score = minefield_score if fatal_minefield else 0.0
    return result


def first_failure_stage_id(stage_reports: list[StageEvaluationResult]) -> str | None:
    """返回首个失败阶段的 stage_id。

    Args:
        stage_reports: 阶段评估结果列表。

    Returns:
        首个失败阶段的 stage_id；没有失败阶段时返回 None。
    """
    if stage_reports is None:
        raise ValueError("stage_reports 不能为空")
    return next(
        (
            stage.stage_id
            for stage in stage_reports
            if stage.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
        ),
        None,
    )


def build_trajectory(
    run_id: str,
    task_case: TaskCase,
    steps: list[TrajectoryStep],
    snapshots: list[StateSnapshot],
    session: object,
    harness: "BaseBenchmarkHarness",
) -> Trajectory:
    """从运行期增量步骤与快照构造完整轨迹。

    Args:
        run_id: 本次运行 ID。
        task_case: 任务定义。
        steps: 已采集的轨迹步骤。
        snapshots: 已采集的状态快照。
        session: harness 私有 session 对象。
        harness: benchmark harness。

    Returns:
        当前已知步骤与快照组成的轨迹。
    """
    if not run_id or task_case is None or steps is None or snapshots is None or session is None or harness is None:
        raise ValueError("构造轨迹所需参数不能为空")
    return Trajectory(
        run_id=run_id,
        task_id=task_case.task_id,
        steps=list(steps),
        snapshots=list(snapshots),
        final_state=harness.final_state_from_session(session),
        metrics=harness.metrics_from_session(session),
    )


def merge_snapshots(
    current: list[StateSnapshot],
    incoming: list[StateSnapshot],
) -> list[StateSnapshot]:
    """按 snapshot_id 合并并稳定排序状态快照。

    Args:
        current: 已有快照列表。
        incoming: 新增快照列表。

    Returns:
        去重并按 (after_step_index, snapshot_id) 排序的快照列表。
    """
    if current is None or incoming is None:
        raise ValueError("快照列表不能为空")
    by_id = {snapshot.snapshot_id: snapshot for snapshot in current}
    for snapshot in incoming:
        by_id[snapshot.snapshot_id] = snapshot
    return sorted(by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))
