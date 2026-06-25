from __future__ import annotations

import math
import logging
from dataclasses import dataclass, replace
from typing import Optional, TYPE_CHECKING

from dynsteer.boundary import generate_candidate_boundaries
from dynsteer.config import (
    DEFAULT_FOCUS,
    DEFAULT_TARGETS,
    TASK_TYPE_WEIGHTS,
    DynamicWeightConfig,
    MatchConfig,
    ThresholdConfig,
    default_dynamic_weight_config,
)
from dynsteer.judge import Judge, LocalJudge
from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.match import match_milestones, validate_milestone_graph
from dynsteer.model import (
    Boundary,
    ConstraintTarget,
    Dimension,
    EvaluationDecision,
    EvaluationLevel,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
    TrajectoryStep,
    TrajectoryEvaluationReport,
)
from dynsteer.score import score_constraint, score_milestone
from dynsteer.stage import build_stage_intervals

if TYPE_CHECKING:
    from dynsteer.adapter.base import BaseBenchmarkHarness

logger = logging.getLogger(__name__)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def normalize_weights(weights: dict[Dimension, float]) -> dict[Dimension, float]:
    """归一化维度权重。

    Args:
        weights: 原始维度权重。

    Returns:
        覆盖全部维度且和为 1 的权重。
    """
    if weights is None:
        raise ValueError("weights 不能为空")
    normalized_source = {dimension: max(float(weights.get(dimension, 0.0)), 0.0) for dimension in Dimension}
    total = sum(normalized_source.values())
    if total <= 0:
        return {dimension: 1 / len(Dimension) for dimension in Dimension}
    return {dimension: value / total for dimension, value in normalized_source.items()}


def select_initial_weights(task_case: TaskCase) -> dict[Dimension, float]:
    """根据任务类型选择初始维度权重。

    Args:
        task_case: 任务定义。

    Returns:
        初始维度权重。
    """
    if task_case is None:
        raise ValueError("task_case 不能为空")
    task_types = task_case.task_types or []
    if not task_types:
        task_types = [next(iter(TASK_TYPE_WEIGHTS))]
    merged = {dimension: 0.0 for dimension in Dimension}
    valid_count = 0
    for task_type in task_types:
        weights = TASK_TYPE_WEIGHTS.get(task_type)
        if weights is None:
            continue
        valid_count += 1
        for dimension in Dimension:
            merged[dimension] += weights.get(dimension, 0.0)
    if valid_count == 0:
        return normalize_weights(TASK_TYPE_WEIGHTS[next(iter(TASK_TYPE_WEIGHTS))])
    return normalize_weights({dimension: value / valid_count for dimension, value in merged.items()})


def update_weights(
    current: dict[Dimension, float],
    scores: dict[Dimension, float],
    uncertainty: float,
    config: Optional[DynamicWeightConfig] = None,
) -> dict[Dimension, float]:
    """根据低分维度与不确定性更新下一阶段权重。

    Args:
        current: 当前阶段权重。
        scores: 当前阶段各维度分数。
        uncertainty: 当前阶段不确定性。
        config: 动态权重超参数；为空时使用默认配置。

    Returns:
        下一阶段归一化权重。
    """
    if current is None or scores is None:
        raise ValueError("current 和 scores 不能为空")
    effective_config = config or default_dynamic_weight_config()
    next_weights: dict[Dimension, float] = {}
    for dimension in Dimension:
        base = max(float(current.get(dimension, 0.0)), 1e-9)
        score = float(scores.get(dimension, 0.0))
        target = effective_config.targets.get(dimension, DEFAULT_TARGETS[dimension])
        focus = effective_config.focus.get(dimension, DEFAULT_FOCUS[dimension])
        deficit = max(0.0, target - score)
        next_weights[dimension] = base * math.exp(
            effective_config.alpha * deficit + effective_config.beta * _clamp(uncertainty) * focus
        )
    return normalize_weights(next_weights)


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
    u_margin = 1.0 - _clamp(margin / 0.3)
    u_missing = _clamp(missing_ratio)
    threshold_distance = min(
        abs(stage_score - effective_thresholds.pass_threshold),
        abs(stage_score - effective_thresholds.warn_threshold),
        abs(stage_score - effective_thresholds.fail_threshold),
    )
    u_threshold = 1.0 - _clamp(threshold_distance / effective_thresholds.threshold_margin)
    u_conflict = 1.0 if evidence_conflict else 0.0
    u_judge = _clamp(judge_uncertainty)
    return _clamp(
        0.30 * u_margin
        + 0.25 * u_missing
        + 0.20 * u_threshold
        + 0.15 * u_conflict
        + 0.10 * u_judge
    )


def select_evaluation_level(
    result: StageEvaluationResult,
    thresholds: Optional[ThresholdConfig] = None,
) -> EvaluationDecision:
    """根据阶段风险选择评估粒度。

    Args:
        result: 当前阶段已有评估结果。
        thresholds: 阈值配置。

    Returns:
        评估粒度决策。
    """
    if result is None:
        raise ValueError("result 不能为空")
    effective_thresholds = thresholds or ThresholdConfig()
    if result.fatal or result.fatal_minefield_score >= effective_thresholds.fatal_minefield_threshold:
        return EvaluationDecision(EvaluationLevel.EXPENSIVE, "触发致命风险，需要最高粒度复核")
    if result.evaluator_level == EvaluationLevel.EXPENSIVE:
        return EvaluationDecision(EvaluationLevel.EXPENSIVE, "已处于最高评估粒度")
    if result.evaluator_level == EvaluationLevel.STANDARD:
        if result.judge_confidence < 0.45:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "standard judge 置信度过低")
        if result.uncertainty >= effective_thresholds.high_uncertainty:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "standard 阶段不确定性过高")
        if result.required_fields_missing_ratio > 0.3:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "必要字段缺失比例过高")
        if result.minefield_score >= effective_thresholds.risky_minefield_threshold:
            return EvaluationDecision(EvaluationLevel.EXPENSIVE, "风险约束接近 minefield")
        return EvaluationDecision(EvaluationLevel.STANDARD, "standard 评估已足够")

    cheap_pass = (
        result.status == StageStatus.PASS
        and result.stage_score >= effective_thresholds.pass_threshold + effective_thresholds.threshold_margin
        and result.uncertainty <= effective_thresholds.low_uncertainty
        and result.hard_constraints_all_pass
        and result.required_fields_missing_ratio == 0
        and result.minefield_score <= effective_thresholds.safe_minefield_threshold
        and result.judge_confidence >= 0.7
    )
    if cheap_pass:
        return EvaluationDecision(EvaluationLevel.CHEAP, "结构化证据充分，cheap 评估足够")
    return EvaluationDecision(EvaluationLevel.STANDARD, "cheap 证据不足，升级到 standard")


def _milestone_score_matrix(
    graph: MilestoneGraph,
    boundaries: list[Boundary],
    trajectory: Trajectory,
) -> dict[tuple[str, str], MilestoneScore]:
    matrix: dict[tuple[str, str], MilestoneScore] = {}
    for milestone in graph.nodes:
        for boundary in boundaries:
            matrix[(milestone.milestone_id, boundary.boundary_id)] = score_milestone(
                milestone,
                boundary,
                trajectory,
                trajectory.snapshots,
            )
    return matrix


def evaluate_minefields(graph: MilestoneGraph, trajectory: Trajectory) -> tuple[list[JsonObject], float, bool]:
    """评估轨迹是否触发 minefield。

    Args:
        graph: milestone 图，包含 minefield 定义。
        trajectory: 待检查的轨迹。

    Returns:
        三元组：命中的 minefield 列表、最高 minefield 分数、是否触发 fatal minefield。
    """
    if graph is None or trajectory is None:
        raise ValueError("graph 和 trajectory 不能为空")
    matches: list[JsonObject] = []
    max_score = 0.0
    fatal = False
    for minefield in graph.minefields:
        if not minefield.constraints:
            continue
        scores = []
        evidence: list[str] = []
        for constraint in minefield.constraints:
            source: object = trajectory.metrics if constraint.target == ConstraintTarget.METRIC else trajectory.final_state or {}
            score = score_constraint(constraint, source, None)
            scores.append(score.score)
            evidence.extend(score.evidence)
        minefield_score = sum(scores) / len(scores) if scores else 0.0
        if minefield_score > 0:
            matches.append(
                {
                    "minefield_id": minefield.minefield_id,
                    "score": minefield_score,
                    "severity": minefield.severity,
                    "evidence": evidence,
                }
            )
        max_score = max(max_score, minefield_score)
        if minefield.severity == "fatal" and minefield_score >= 1.0:
            fatal = True
    return matches, max_score, fatal


def _overall_score(stage_reports: list[StageEvaluationResult], minefield_score: float) -> float:
    if not stage_reports:
        return 1.0 if minefield_score == 0 else 0.0
    raw = sum(stage.stage_score for stage in stage_reports) / len(stage_reports)
    return _clamp(raw * (1.0 - _clamp(minefield_score)))


@dataclass(frozen=True)
class RuntimeEvaluationState:
    """保存单个 case 运行期间的评估状态。"""

    weights: dict[Dimension, float]
    settlements: list[HarnessStageSettlement]
    matched_settlements: dict[str, HarnessStageSettlement]
    stage_reports: list[StageEvaluationResult]


@dataclass(frozen=True)
class RuntimeEvaluationDecision:
    """单步运行期阶段评估决策。"""

    checkpoint: HarnessStageSettlement | None
    stage_result: StageEvaluationResult | None
    next_state: RuntimeEvaluationState
    should_stop: bool = False
    termination_code: str | None = None
    termination_reason: str | None = None


class JudgeConfigurationError(RuntimeError):
    """LLM judge 配置缺失或不合法时抛出。"""


class DynSTEEREvaluator:
    """DynSTEER 执行编排与阶段式动态评估入口。"""

    def __init__(
        self,
        cheap_judge: Judge | None = None,
        llm_judge: Judge | None = None,
        thresholds: ThresholdConfig | None = None,
        match_config: MatchConfig | None = None,
        weight_config: DynamicWeightConfig | None = None,
    ) -> None:
        self._cheap_judge = cheap_judge or LocalJudge()
        self._llm_judge = llm_judge
        self._thresholds = thresholds or ThresholdConfig()
        self._match_config = match_config or MatchConfig()
        self._weight_config = weight_config

    def evaluate(
        self,
        harness: "BaseBenchmarkHarness",
        case_id: str,
        config: HarnessRunConfig,
    ) -> HarnessRunResult:
        """执行 benchmark case，并进行阶段式动态评估。

        Args:
            harness: 已适配为 DynSTEER 公开执行接口的 benchmark harness。
            case_id: benchmark case ID。
            config: harness 运行配置。

        Returns:
            benchmark 运行结果，包含轨迹、阶段结算和主实验评估报告。
        """
        if harness is None or config is None:
            raise ValueError("harness 和 config 不能为空")
        if case_id is None or not str(case_id).strip():
            raise ValueError("case_id 不能为空")
        harness.prepare_config(config)
        run_id = harness.build_run_id(config, case_id)
        raw_output_dir = config.runs_dir / config.benchmark / run_id / case_id / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)

        session: object | None = None
        steps: list[TrajectoryStep] = []
        snapshots: list[StateSnapshot] = []
        terminated_by_policy = False
        termination_code: str | None = None
        termination_reason: str | None = None

        try:
            logger.info(
                "evaluator_case_start",
                extra={"事件": "启动benchmark任务", "benchmark": config.benchmark, "case_id": case_id},
            )
            session = harness.start_case(config, case_id, raw_output_dir)
            task_case = harness.task_case_from_session(session)
            state = RuntimeEvaluationState(
                weights=select_initial_weights(task_case),
                settlements=[self._start_settlement([])],
                matched_settlements={},
                stage_reports=[],
            )

            while True:
                advance = harness.advance_case(session)
                snapshots = self._merge_snapshots(snapshots, harness.snapshots_from_session(session))

                for step in advance.steps:
                    steps.append(step)
                    trajectory = self._build_trajectory(run_id, task_case, steps, snapshots, session, harness)
                    decision = self._evaluate_checkpoint_if_needed(config, task_case, trajectory, step, state)
                    state = decision.next_state
                    if decision.should_stop:
                        termination_code = decision.termination_code
                        termination_reason = decision.termination_reason
                        terminated_by_policy = True
                        harness.stop_case(
                            session,
                            termination_reason or "阶段式动态评估触发提前终止",
                        )
                        logger.warning(
                            "evaluator_policy_stop",
                            extra={
                                "事件": "策略提前终止",
                                "case_id": case_id,
                                "termination_code": termination_code,
                            },
                        )
                        break

                if terminated_by_policy or not advance.continue_running:
                    break

            snapshots = self._merge_snapshots(snapshots, harness.snapshots_from_session(session))
            trajectory = self._build_trajectory(run_id, task_case, steps, snapshots, session, harness)
            if not terminated_by_policy:
                settlement, stage_result, next_weights = self._finish_settlement(
                    state.settlements,
                    task_case,
                    trajectory,
                    state.matched_settlements,
                    state.weights,
                )
                state = replace(
                    state,
                    settlements=[*state.settlements, settlement],
                    stage_reports=[*state.stage_reports, stage_result],
                    weights=next_weights,
                )
            report = self._runtime_report(task_case, trajectory, state.stage_reports)
            raw_summary = harness.raw_summary_from_session(session)
            return HarnessRunResult(
                benchmark=config.benchmark,
                case_id=case_id,
                run_id=run_id,
                task_case=task_case,
                trajectory=trajectory,
                raw_output_dir=raw_output_dir,
                raw_summary=raw_summary,
                stage_settlements=state.settlements,
                evaluation_report=report,
                terminated_by_policy=terminated_by_policy,
                termination_code=termination_code,
                termination_reason=termination_reason,
            )
        finally:
            if session is not None:
                harness.teardown_case(session)

    def evaluate_trajectory(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
    ) -> TrajectoryEvaluationReport:
        """对完整轨迹执行阶段式动态评估。

        Args:
            task_case: 任务定义。
            trajectory: 待评估 Agent 轨迹。

        Returns:
            轨迹评估报告。
        """
        if task_case is None or trajectory is None:
            raise ValueError("task_case 和 trajectory 不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        minefield_matches, minefield_score, fatal_minefield = evaluate_minefields(graph, trajectory)
        if fatal_minefield:
            return TrajectoryEvaluationReport(
                run_id=trajectory.run_id,
                task_id=trajectory.task_id,
                milestone_coverage="none" if not graph.nodes else "partial",
                overall_score=0.0,
                stage_reports=[],
                minefield_matches=minefield_matches,
                first_failure_stage_id=(
                    f"minefield:{minefield_matches[0]['minefield_id']}" if minefield_matches else "minefield"
                ),
            )
        if len(graph.nodes) == 0:
            return TrajectoryEvaluationReport(
                run_id=trajectory.run_id,
                task_id=trajectory.task_id,
                milestone_coverage="none",
                overall_score=_overall_score([], minefield_score),
                stage_reports=[],
                minefield_matches=minefield_matches,
            )

        validate_milestone_graph(graph)
        boundaries = generate_candidate_boundaries(trajectory)
        matrix = _milestone_score_matrix(graph, boundaries, trajectory)
        mapping = match_milestones(graph, boundaries, matrix, self._match_config)
        intervals = build_stage_intervals(graph, mapping, trajectory)
        weights = select_initial_weights(task_case)
        stage_reports: list[StageEvaluationResult] = []
        for interval in intervals:
            stage_result, weights = self._evaluate_stage_with_scheduler(interval, task_case, trajectory, weights)
            stage_reports.append(stage_result)

        failed = self._first_failure_stage_id(stage_reports)
        coverage = "full" if not mapping.missing_required else "partial"
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage=coverage,
            overall_score=_overall_score(stage_reports, minefield_score),
            stage_reports=stage_reports,
            minefield_matches=minefield_matches,
            first_failure_stage_id=failed,
        )

    def _evaluate_checkpoint_if_needed(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        step: TrajectoryStep,
        state: RuntimeEvaluationState,
    ) -> RuntimeEvaluationDecision:
        hit = self._find_hit_milestone(task_case, trajectory, step, state.matched_settlements)
        if hit is None:
            return RuntimeEvaluationDecision(None, None, state)
        milestone, boundary, milestone_score = hit
        settlement, stage_result, next_weights = self._append_milestone_settlement(
            settlements=state.settlements,
            matched=state.matched_settlements,
            task_case=task_case,
            trajectory=trajectory,
            milestone=milestone,
            boundary=boundary,
            milestone_score=milestone_score,
            weights=state.weights,
        )
        next_matched = dict(state.matched_settlements)
        next_matched[milestone.milestone_id] = settlement
        next_state = RuntimeEvaluationState(
            weights=next_weights,
            settlements=[*state.settlements, settlement],
            matched_settlements=next_matched,
            stage_reports=[*state.stage_reports, stage_result],
        )
        stop_decision = self._should_stop_after_stage(config, task_case, trajectory, stage_result)
        if stop_decision is None:
            return RuntimeEvaluationDecision(settlement, stage_result, next_state)
        termination_code, termination_reason = stop_decision
        return RuntimeEvaluationDecision(
            settlement,
            stage_result,
            next_state,
            should_stop=True,
            termination_code=termination_code,
            termination_reason=termination_reason,
        )

    def _evaluate_stage_with_scheduler(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> tuple[StageEvaluationResult, dict[Dimension, float]]:
        stage_result = self._cheap_judge.evaluate_stage(interval, task_case, trajectory, EvaluationLevel.CHEAP, weights)
        stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
        decision = select_evaluation_level(stage_result, self._thresholds)
        if decision.level == EvaluationLevel.STANDARD:
            stage_result = self._evaluate_with_llm(interval, task_case, trajectory, EvaluationLevel.STANDARD, weights)
            stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
            decision = select_evaluation_level(stage_result, self._thresholds)
        if decision.level == EvaluationLevel.EXPENSIVE and stage_result.evaluator_level != EvaluationLevel.EXPENSIVE:
            stage_result = self._evaluate_with_llm(interval, task_case, trajectory, EvaluationLevel.EXPENSIVE, weights)
            stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
        next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.uncertainty, self._weight_config)
        return replace(stage_result, next_weights=next_weights), next_weights

    def _evaluate_with_llm(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        if self._llm_judge is None:
            raise JudgeConfigurationError("standard/expensive 评估需要配置真实 LLMJudge")
        return self._llm_judge.evaluate_stage(interval, task_case, trajectory, level, weights)

    def _enrich_stage_result(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        result: StageEvaluationResult,
    ) -> StageEvaluationResult:
        graph = task_case.milestone_graph or MilestoneGraph()
        _, minefield_score, fatal_minefield = evaluate_minefields(graph, trajectory)
        top1 = interval.milestone_score.score if interval.milestone_score is not None else result.stage_score
        uncertainty = compute_uncertainty(
            top1_score=top1,
            top2_score=0.0,
            missing_ratio=result.required_fields_missing_ratio,
            stage_score=result.stage_score,
            evidence_conflict=False,
            judge_uncertainty=1.0 - result.judge_confidence,
            thresholds=self._thresholds,
        )
        return replace(
            result,
            uncertainty=uncertainty,
            minefield_score=minefield_score,
            fatal_minefield_score=minefield_score if fatal_minefield else 0.0,
        )

    def _runtime_report(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_reports: list[StageEvaluationResult],
    ) -> TrajectoryEvaluationReport:
        graph = task_case.milestone_graph or MilestoneGraph()
        minefield_matches, minefield_score, _ = evaluate_minefields(graph, trajectory)
        matched_ids = {stage.milestone_id for stage in stage_reports if stage.milestone_id is not None}
        required_ids = {node.milestone_id for node in graph.nodes if node.required}
        if not graph.nodes:
            coverage = "none"
        elif required_ids.issubset(matched_ids):
            coverage = "full"
        else:
            coverage = "partial"
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage=coverage,
            overall_score=_overall_score(stage_reports, minefield_score),
            stage_reports=stage_reports,
            minefield_matches=minefield_matches,
            first_failure_stage_id=self._first_failure_stage_id(stage_reports),
        )

    def _first_failure_stage_id(self, stage_reports: list[StageEvaluationResult]) -> str | None:
        return next(
            (
                stage.stage_id
                for stage in stage_reports
                if stage.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
            ),
            None,
        )

    def _build_trajectory(
        self,
        run_id: str,
        task_case: TaskCase,
        steps: list[TrajectoryStep],
        snapshots: list[StateSnapshot],
        session: object,
        harness: "BaseBenchmarkHarness",
    ) -> Trajectory:
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

    def _merge_snapshots(
        self,
        current: list[StateSnapshot],
        incoming: list[StateSnapshot],
    ) -> list[StateSnapshot]:
        if current is None or incoming is None:
            raise ValueError("快照列表不能为空")
        by_id = {snapshot.snapshot_id: snapshot for snapshot in current}
        for snapshot in incoming:
            by_id[snapshot.snapshot_id] = snapshot
        return sorted(by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))

    def _start_settlement(self, settlements: list[HarnessStageSettlement]) -> HarnessStageSettlement:
        if settlements is None:
            raise ValueError("settlements 不能为空")
        return HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="start",
            milestone_id=None,
            start_step_index=0,
            end_step_index=0,
            evidence=["start 结算节点"],
        )

    def _finish_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        task_case: TaskCase,
        trajectory: Trajectory,
        matched: dict[str, HarnessStageSettlement],
        weights: dict[Dimension, float],
    ) -> tuple[HarnessStageSettlement, StageEvaluationResult, dict[Dimension, float]]:
        if settlements is None or task_case is None or trajectory is None or matched is None or weights is None:
            raise ValueError("finish 结算参数不能为空")
        last_step_index = max((step.index for step in trajectory.steps), default=0)
        predecessor_index = max(
            (settlement.end_step_index for settlement in matched.values()),
            default=settlements[0].end_step_index if settlements else 0,
        )
        end_step_index = max(predecessor_index, last_step_index)
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=None,
            start_step_index=predecessor_index,
            end_step_index=end_step_index,
            status=StageStatus.PASS,
            evidence=["finish 结算节点"],
        )
        stage_result, next_weights = self._evaluate_stage_with_scheduler(interval, task_case, trajectory, weights)
        settlement = HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="finish",
            milestone_id=None,
            start_step_index=predecessor_index,
            end_step_index=end_step_index,
            score=stage_result.stage_score,
            status=stage_result.status.value,
            evidence=list(stage_result.evidence),
            metadata={
                "stage_report": stage_result.to_dict(),
                "predecessor_milestone_ids": sorted(matched),
                "stage_start_step_index": predecessor_index,
                "stage_end_step_index": end_step_index,
            },
        )
        return settlement, stage_result, next_weights

    def _ready_milestones(
        self,
        graph: MilestoneGraph,
        matched: dict[str, HarnessStageSettlement],
    ) -> list[Milestone]:
        if graph is None or matched is None:
            raise ValueError("graph 和 matched 不能为空")
        matched_ids = set(matched)
        predecessors: dict[str, list[str]] = {node.milestone_id: [] for node in graph.nodes}
        for source, target in graph.edges:
            if target in predecessors:
                predecessors[target].append(source)
        ready = []
        for node in graph.nodes:
            if node.milestone_id in matched_ids:
                continue
            if all(source in matched_ids for source in predecessors.get(node.milestone_id, [])):
                ready.append(node)
        return ready

    def _find_hit_milestone(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        step: TrajectoryStep,
        matched: dict[str, HarnessStageSettlement],
    ) -> tuple[Milestone, Boundary, MilestoneScore] | None:
        if task_case is None or trajectory is None or step is None or matched is None:
            raise ValueError("milestone 判定参数不能为空")
        graph = task_case.milestone_graph
        if graph is None or not graph.nodes:
            return None
        boundaries = [boundary for boundary in generate_candidate_boundaries(trajectory) if boundary.step_index == step.index]
        if not boundaries:
            return None
        best: tuple[Milestone, Boundary, MilestoneScore] | None = None
        for milestone in self._ready_milestones(graph, matched):
            predecessor_start = self._stage_start_for_milestone(graph, milestone.milestone_id, matched, None)
            for boundary in boundaries:
                if boundary.step_index <= predecessor_start and predecessor_start > 0:
                    continue
                score = score_milestone(milestone, boundary, trajectory, trajectory.snapshots)
                if score.status != StageStatus.PASS:
                    continue
                if best is None or score.score > best[2].score:
                    best = (milestone, boundary, score)
        return best

    def _stage_start_for_milestone(
        self,
        graph: MilestoneGraph,
        milestone_id: str,
        matched: dict[str, HarnessStageSettlement],
        start_settlement: HarnessStageSettlement | None,
    ) -> int:
        if graph is None or not milestone_id or matched is None:
            raise ValueError("阶段起点参数不能为空")
        predecessors = [source for source, target in graph.edges if target == milestone_id]
        matched_predecessor_indexes = [
            matched[source].end_step_index
            for source in predecessors
            if source in matched
        ]
        if matched_predecessor_indexes:
            return max(matched_predecessor_indexes)
        return start_settlement.end_step_index if start_settlement is not None else 0

    def _append_milestone_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        matched: dict[str, HarnessStageSettlement],
        task_case: TaskCase,
        trajectory: Trajectory,
        milestone: Milestone,
        boundary: Boundary,
        milestone_score: MilestoneScore,
        weights: dict[Dimension, float],
    ) -> tuple[HarnessStageSettlement, StageEvaluationResult, dict[Dimension, float]]:
        if (
            settlements is None
            or matched is None
            or task_case is None
            or trajectory is None
            or milestone is None
            or boundary is None
            or milestone_score is None
            or weights is None
        ):
            raise ValueError("milestone 结算参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        start_settlement = settlements[0] if settlements else None
        start_step_index = self._stage_start_for_milestone(graph, milestone.milestone_id, matched, start_settlement)
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=milestone.milestone_id,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
            status=milestone_score.status,
            milestone_score=milestone_score,
            evidence=list(milestone_score.evidence),
        )
        stage_result, next_weights = self._evaluate_stage_with_scheduler(interval, task_case, trajectory, weights)
        predecessor_milestone_ids = [source for source, target in graph.edges if target == milestone.milestone_id]
        settlement = HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind="milestone",
            milestone_id=milestone.milestone_id,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
            boundary_id=boundary.boundary_id,
            boundary_step_index=boundary.step_index,
            score=stage_result.stage_score,
            status=stage_result.status.value,
            checkpointed=True,
            evidence=list(stage_result.evidence),
            metadata={
                "stage_report": stage_result.to_dict(),
                "predecessor_milestone_ids": predecessor_milestone_ids,
                "stage_start_step_index": start_step_index,
                "stage_end_step_index": boundary.step_index,
            },
        )
        logger.info(
            "evaluator_milestone_checkpoint",
            extra={
                "事件": "命中milestone并执行阶段评估",
                "milestone_id": milestone.milestone_id,
                "step_index": boundary.step_index,
                "status": stage_result.status.value,
            },
        )
        return settlement, stage_result, next_weights

    def _should_stop_after_stage(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_result: StageEvaluationResult,
    ) -> tuple[str, str] | None:
        if config is None or task_case is None or trajectory is None or stage_result is None:
            raise ValueError("终止策略参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        if config.stop_on_minefield:
            matches, max_score, fatal = evaluate_minefields(graph, trajectory)
            if matches and fatal:
                minefield_id = str(matches[0].get("minefield_id", "minefield"))
                return (
                    f"minefield:{minefield_id}",
                    f"触发 fatal minefield，提前终止执行：{minefield_id}",
                )
            if matches and max_score >= self._thresholds.fatal_minefield_threshold:
                return (
                    f"minefield_score:{max_score:.3f}",
                    f"minefield 分数 {max_score:.3f} 达到停止阈值，提前终止执行",
                )
        if config.stop_on_stage_failure:
            milestone_id = stage_result.milestone_id or "unknown"
            if stage_result.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
                return (
                    f"stage_failure:{milestone_id}",
                    f"阶段评估状态为 {stage_result.status.value}，提前终止执行：{milestone_id}",
                )
            if stage_result.stage_score < self._thresholds.fail_threshold:
                return (
                    f"stage_score:{milestone_id}",
                    f"阶段评估分数 {stage_result.stage_score:.3f} 低于失败阈值，提前终止执行：{milestone_id}",
                )
        return None
