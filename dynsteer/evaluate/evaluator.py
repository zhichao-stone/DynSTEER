from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace
from typing import Mapping, TYPE_CHECKING

from dynsteer.boundary import generate_candidate_boundaries
from dynsteer.config import DynamicWeightConfig, MatchConfig, ThresholdConfig
from dynsteer.evaluate.diagnostics import (
    build_finish_matching_detail,
    build_milestone_matching_detail,
    build_stage_trace,
)
from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.judges import BaseJudge, CheapJudge, ExpensiveJudge, StandardJudge
from dynsteer.llm import build_llm_from_env
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
from dynsteer.score import score_constraint
from dynsteer.stage import build_stage_intervals
from dynsteer.evaluate.milestone import (
    find_hit_milestone,
    match_milestones,
    milestone_score_matrix,
    ready_milestones,
    stage_start_for_milestone,
    validate_milestone_graph,
)
from dynsteer.evaluate.models import (
    HarnessTeardownError,
    JudgeConfigurationError,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
)
from dynsteer.evaluate.utils import (
    build_trajectory,
    enrich_stage_result,
    first_failure_stage_id,
    merge_snapshots,
    overall_score,
)
from dynsteer.evaluate.weights import select_initial_weights, update_weights

if TYPE_CHECKING:
    from dynsteer.adapter.base import BaseBenchmarkHarness

logger = logging.getLogger(__name__)


class DynSTEEREvaluator:
    """DynSTEER 执行编排与阶段式动态评估入口。"""

    def __init__(
        self,
        cheap_judge: BaseJudge | None = None,
        standard_judge: BaseJudge | None = None,
        expensive_judge: BaseJudge | None = None,
        thresholds: ThresholdConfig | None = None,
        match_config: MatchConfig | None = None,
        weight_config: DynamicWeightConfig | None = None,
    ) -> None:
        self._cheap_judge = cheap_judge or CheapJudge()
        self._standard_judge = standard_judge
        self._expensive_judge = expensive_judge
        self._thresholds = thresholds or ThresholdConfig()
        self._match_config = match_config or MatchConfig()
        self._weight_config = weight_config

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DynSTEEREvaluator":
        """从环境变量构建评估器。

        Args:
            env: 环境变量映射；测试时可传入 fake env。

        Returns:
            未配置 LLM 时仅启用 CheapJudge；已配置 LLM 时共享同一个 BaseLLM
            启用 StandardJudge 与 ExpensiveJudge。
        """
        source = env if env is not None else os.environ
        llm = build_llm_from_env(source)
        if llm is None:
            return cls(cheap_judge=CheapJudge())
        expensive_passes = int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3"))
        return cls(
            cheap_judge=CheapJudge(),
            standard_judge=StandardJudge(llm=llm),
            expensive_judge=ExpensiveJudge(llm=llm, expensive_passes=expensive_passes),
        )

    def evaluate_minefields(
        self,
        graph: MilestoneGraph,
        trajectory: Trajectory,
    ) -> tuple[list[JsonObject], float, bool]:
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
                source: object = (
                    trajectory.metrics
                    if constraint.target == ConstraintTarget.METRIC
                    else trajectory.final_state or {}
                )
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

    def select_evaluation_level(
        self,
        result: StageEvaluationResult,
        thresholds: ThresholdConfig | None = None,
    ) -> EvaluationDecision:
        """根据阶段风险选择评估粒度。

        Args:
            result: 当前阶段已有评估结果。
            thresholds: 阈值配置；为空时使用评估器默认阈值。

        Returns:
            评估粒度决策。
        """
        if result is None:
            raise ValueError("result 不能为空")
        effective_thresholds = thresholds or self._thresholds
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
            task_case = self._task_case_with_run_metadata(task_case, config)
            state = RuntimeEvaluationState(
                weights=select_initial_weights(task_case),
                settlements=[self._start_settlement([])],
                matched_settlements={},
                stage_reports=[],
            )

            while True:
                advance = harness.advance_case(session)
                snapshots = merge_snapshots(snapshots, harness.snapshots_from_session(session))

                for step in advance.steps:
                    steps.append(step)
                    trajectory = build_trajectory(run_id, task_case, steps, snapshots, session, harness)
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

            snapshots = merge_snapshots(snapshots, harness.snapshots_from_session(session))
            trajectory = build_trajectory(run_id, task_case, steps, snapshots, session, harness)
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
            self._teardown_session_safely(harness, session, config.benchmark, run_id, case_id)

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
        minefield_matches, minefield_score, fatal_minefield = self.evaluate_minefields(graph, trajectory)
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
                overall_score=overall_score([], minefield_score),
                stage_reports=[],
                minefield_matches=minefield_matches,
            )

        validate_milestone_graph(graph)
        boundaries = generate_candidate_boundaries(trajectory)
        matrix = milestone_score_matrix(graph, boundaries, trajectory)
        mapping = match_milestones(graph, boundaries, matrix, self._match_config)
        intervals = build_stage_intervals(graph, mapping, trajectory)
        weights = select_initial_weights(task_case)
        stage_reports: list[StageEvaluationResult] = []
        for interval in intervals:
            stage_result, weights = self._evaluate_stage_with_scheduler(interval, task_case, trajectory, weights)
            stage_reports.append(stage_result)

        failed = first_failure_stage_id(stage_reports)
        coverage = "full" if not mapping.missing_required else "partial"
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage=coverage,
            overall_score=overall_score(stage_reports, minefield_score),
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
        hit = find_hit_milestone(task_case, trajectory, step, state.matched_settlements)
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
        stage_result = self._cheap_judge.evaluate_stage(interval, task_case, trajectory, weights)
        stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
        decision = self.select_evaluation_level(stage_result, self._thresholds)
        if decision.level == EvaluationLevel.STANDARD:
            stage_result = self._run_standard(interval, task_case, trajectory, weights)
            stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
            decision = self.select_evaluation_level(stage_result, self._thresholds)
        if decision.level == EvaluationLevel.EXPENSIVE and stage_result.evaluator_level != EvaluationLevel.EXPENSIVE:
            stage_result = self._run_expensive(interval, task_case, trajectory, weights)
            stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result)
        next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.uncertainty, self._weight_config)
        return replace(stage_result, next_weights=next_weights), next_weights

    def _run_standard(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        if self._standard_judge is None:
            raise JudgeConfigurationError("standard 评估需要配置真实 LLMJudge")
        result = self._standard_judge.evaluate_stage(interval, task_case, trajectory, weights)
        return replace(result, evaluator_level=EvaluationLevel.STANDARD)

    def _run_expensive(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        if self._expensive_judge is None:
            raise JudgeConfigurationError("expensive 评估需要配置真实 LLMJudge")
        result = self._expensive_judge.evaluate_stage(interval, task_case, trajectory, weights)
        return replace(result, evaluator_level=EvaluationLevel.EXPENSIVE)

    def _task_case_with_run_metadata(self, task_case: TaskCase, config: HarnessRunConfig) -> TaskCase:
        """将运行配置中的共享元数据合入任务定义。

        Args:
            task_case: harness 提取的任务定义。
            config: 当前运行配置。

        Returns:
            合入 prompt 语言等运行元数据后的任务定义。
        """
        if task_case is None or config is None:
            raise ValueError("task_case 和 config 不能为空")
        metadata = dict(task_case.metadata)
        language = config.metadata.get("language")
        if isinstance(language, str) and language.strip():
            metadata["language"] = language.strip()
        return replace(task_case, metadata=metadata)

    def _enrich_stage_result(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        result: StageEvaluationResult,
    ) -> StageEvaluationResult:
        graph = task_case.milestone_graph or MilestoneGraph()
        _, minefield_score, fatal_minefield = self.evaluate_minefields(graph, trajectory)
        return enrich_stage_result(interval, result, minefield_score, fatal_minefield, self._thresholds)

    def _runtime_report(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_reports: list[StageEvaluationResult],
    ) -> TrajectoryEvaluationReport:
        graph = task_case.milestone_graph or MilestoneGraph()
        minefield_matches, minefield_score, _ = self.evaluate_minefields(graph, trajectory)
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
            overall_score=overall_score(stage_reports, minefield_score),
            stage_reports=stage_reports,
            minefield_matches=minefield_matches,
            first_failure_stage_id=first_failure_stage_id(stage_reports),
        )

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
        graph = task_case.milestone_graph or MilestoneGraph()
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
        stage_trace = build_stage_trace(
            trajectory=trajectory,
            start_step_index=predecessor_index,
            end_step_index=end_step_index,
        )
        milestone_matching = build_finish_matching_detail(graph=graph, matched=matched)
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
                "stage_trace": stage_trace,
                "milestone_matching": milestone_matching,
            },
        )
        return settlement, stage_result, next_weights

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
        start_step_index = stage_start_for_milestone(graph, milestone.milestone_id, matched, start_settlement)
        ready_milestone_ids_before_match = [item.milestone_id for item in ready_milestones(graph, matched)]
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
        stage_trace = build_stage_trace(
            trajectory=trajectory,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
        )
        milestone_matching = build_milestone_matching_detail(
            graph=graph,
            matched=matched,
            milestone=milestone,
            boundary=boundary,
            milestone_score=milestone_score,
            ready_milestone_ids_before_match=ready_milestone_ids_before_match,
        )
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
                "stage_trace": stage_trace,
                "milestone_matching": milestone_matching,
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
            matches, max_score, fatal = self.evaluate_minefields(graph, trajectory)
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

    def _teardown_session_safely(
        self,
        harness: "BaseBenchmarkHarness",
        session: object | None,
        benchmark: str,
        run_id: str,
        case_id: str,
    ) -> None:
        """安全释放 benchmark session，避免清理异常遮蔽主流程异常。"""
        if session is None:
            return
        active_exception = sys.exc_info()[1] is not None
        try:
            harness.teardown_case(session)
        except Exception as exc:
            logger.exception(
                "harness_teardown_failed",
                extra={
                    "事件": "benchmark资源释放失败",
                    "benchmark": benchmark,
                    "run_id": run_id,
                    "case_id": case_id,
                    "error": str(exc),
                },
            )
            if not active_exception:
                raise HarnessTeardownError(
                    f"benchmark session 资源释放失败: benchmark={benchmark}, run_id={run_id}, "
                    f"case_id={case_id}, error={exc}"
                ) from exc
