from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
import time
from typing import Mapping, TYPE_CHECKING

from dynsteer.config import DynamicWeightConfig, ThresholdConfig
from dynsteer.evaluate.diagnostics import (
    build_finish_matching_detail,
    build_milestone_matching_detail,
    build_stage_trace,
)
from dynsteer.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.minefield import evaluate_minefields_at_boundary
from dynsteer.evaluate.scoring import GeneralScorer, ScoringContext, get_effective_scorer
from dynsteer.evaluate.policy import (
    EvaluationPolicyState,
    EvaluationPolicyUpdate,
    initial_evaluation_policy,
    update_evaluation_policy,
)
from dynsteer.graph import START_NODE_ID
from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.judges import BaseJudge, CheapJudge, ExpensiveJudge, StandardJudge
from dynsteer.llm import build_llm_from_env
from dynsteer.metrics import (
    RuntimeMetricsRecorder,
    activate_runtime_metrics_recorder,
    build_runtime_metrics,
    reset_runtime_metrics_recorder,
)
from dynsteer.model import (
    Boundary,
    Dimension,
    EvaluationLevel,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryEvaluationReport,
)
from dynsteer.progress import CaseProgressReporter
from dynsteer.stage import stage_start_step_index
from dynsteer.evaluate.milestone import (
    analyze_milestone_step,
    ready_milestones,
    stage_start_for_milestone,
)
from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    JudgeConfigurationError,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
)
from dynsteer.evaluate.runtime import (
    blocked_milestone_termination_reason,
    pending_required_stage_results,
    runtime_diagnostics_summary,
    scoring_context,
    task_case_snapshot,
    task_description_mismatched,
)
from dynsteer.evaluate.telemetry import policy_stop_log_extra
from dynsteer.evaluate.scoring import (
    enrich_stage_result,
    first_failure_stage_id,
    minefield_penalty_score,
    overall_score,
)
from dynsteer.evaluate.scoring import select_initial_weights, update_weights

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
        weight_config: DynamicWeightConfig | None = None,
    ) -> None:
        self._cheap_judge = cheap_judge or CheapJudge()
        self._standard_judge = standard_judge
        self._expensive_judge = expensive_judge
        self._thresholds = thresholds or ThresholdConfig()
        self._weight_config = weight_config

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DynSTEEREvaluator":
        """从环境变量构建评估器。"""
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
        scorer: GeneralScorer | None = None,
        context: ScoringContext | None = None,
    ) -> tuple[list[JsonObject], float, bool]:
        """评估轨迹是否触发 minefield。"""
        if graph is None or trajectory is None:
            raise ValueError("graph 和 trajectory 不能为空")
        matches: list[JsonObject] = []
        seen: set[tuple[str, str]] = set()
        max_score = 0.0
        fatal = False
        for step in trajectory.steps:
            boundary = candidate_boundary_for_current_step(trajectory, step)
            boundary_matches, boundary_score, boundary_fatal = evaluate_minefields_at_boundary(
                graph,
                trajectory,
                boundary,
                get_effective_scorer(scorer),
                context,
            )
            for match in boundary_matches:
                key = (str(match.get("minefield_id")), str(match.get("boundary_id")))
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
            max_score = max(max_score, boundary_score)
            fatal = fatal or boundary_fatal
        return matches, max_score, fatal

    def evaluate(
        self,
        harness: "BaseBenchmarkHarness",
        config: HarnessRunConfig,
        task_case: TaskCase,
        progress_reporter: CaseProgressReporter | None = None,
    ) -> HarnessRunResult:
        """执行 benchmark case，并进行阶段式动态评估。"""
        if harness is None or config is None or task_case is None:
            raise ValueError("harness、config 和 task_case 不能为空")
        case_id = task_case.case_id
        if case_id is None or not str(case_id).strip():
            raise ValueError("task_case.case_id 不能为空")
        harness.prepare_config(config)
        run_id = harness.build_run_id(config, case_id)
        raw_output_dir = config.runs_dir / config.benchmark / run_id / case_id / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)

        session: object | None = None
        terminated_by_policy = False
        termination_code: str | None = None
        termination_reason: str | None = None
        termination_detail: JsonObject | None = None
        metrics_recorder = RuntimeMetricsRecorder()
        metrics_token = activate_runtime_metrics_recorder(metrics_recorder)

        try:
            session = harness.start_case(config, case_id, raw_output_dir)
            language = config.metadata.get("language")
            if isinstance(language, str) and language.strip():
                task_case.metadata["language"] = language.strip()
            scorer = harness.constraint_scorer()
            trajectory = Trajectory(
                run_id=run_id,
                task_id=task_case.task_id,
                steps=[],
                snapshots=[],
                final_state=harness.final_state_from_session(session),
                metrics=harness.metrics_from_session(session),
            )
            state = RuntimeEvaluationState(
                weights=select_initial_weights(task_case),
                settlements=[
                    HarnessStageSettlement(
                        settlement_id="st0",
                        kind="start",
                        milestone_id=None,
                        start_step_index=0,
                        end_step_index=0,
                        evidence=["start 结算节点"],
                    )
                ],
                matched_settlements={},
                stage_reports=[],
                match_attempts=[],
                evaluation_policy=initial_evaluation_policy(),
            )

            while True:
                advance = harness.advance_case(session)
                if progress_reporter is not None and advance.steps:
                    progress_reporter.case_advanced(case_id, len(advance.steps))
                if advance.snapshots:
                    snapshot_by_id = {snapshot.snapshot_id: snapshot for snapshot in trajectory.snapshots}
                    for snapshot in advance.snapshots:
                        snapshot_by_id[snapshot.snapshot_id] = snapshot
                    trajectory.snapshots = sorted(
                        snapshot_by_id.values(),
                        key=lambda item: (item.after_step_index, item.snapshot_id),
                    )
                trajectory.final_state = harness.final_state_from_session(session)
                trajectory.metrics = harness.metrics_from_session(session)

                for step in advance.steps:
                    trajectory.append_step(step)
                    context = scoring_context(task_case, trajectory, state.matched_settlements)
                    boundary = candidate_boundary_for_current_step(trajectory, step)
                    minefield_matches, minefield_score, fatal_minefield = evaluate_minefields_at_boundary(
                        task_case.milestone_graph or MilestoneGraph(),
                        trajectory,
                        boundary,
                        scorer,
                        context,
                    )
                    if minefield_matches:
                        self._record_runtime_minefields(state, minefield_matches, minefield_score, fatal_minefield)
                        if fatal_minefield and config.stop_on_minefield:
                            minefield_id = str(minefield_matches[0].get("minefield_id", "minefield"))
                            termination_code = f"minefield:{minefield_id}"
                            termination_reason = f"触发 fatal minefield，提前终止执行：{minefield_id}"
                            termination_detail = {
                                "code": termination_code,
                                "minefield_matches": minefield_matches,
                                "boundary": {
                                    "boundary_id": boundary.boundary_id,
                                    "step_index": boundary.step_index,
                                },
                            }
                            decision = RuntimeEvaluationDecision(
                                None,
                                None,
                                state,
                                should_stop=True,
                                termination_code=termination_code,
                                termination_reason=termination_reason,
                                termination_detail=termination_detail,
                            )
                            terminated_by_policy = True
                            harness.stop_case(session, termination_reason)
                            logger.warning(
                                "evaluator_policy_stop",
                                extra={
                                    "事件": "策略提前终止",
                                    **policy_stop_log_extra(
                                        case_id,
                                        task_case,
                                        decision,
                                        termination_code,
                                        termination_reason,
                                    ),
                                },
                            )
                            break
                    analysis = analyze_milestone_step(
                        task_case,
                        trajectory,
                        step,
                        state.matched_settlements,
                        scorer=scorer,
                        context=context,
                    )
                    if analysis.hit is None:
                        if analysis.attempt_detail is not None:
                            state.match_attempts.append(analysis.attempt_detail)
                        if analysis.blocked_detail is not None:
                            state.match_attempts.append(analysis.blocked_detail)
                            if config.stop_on_stage_failure:
                                milestone_id = str(analysis.blocked_detail.get("milestone_id") or "unknown")
                                termination_code = f"milestone_predecessor_gap:{milestone_id}"
                                termination_detail = dict(analysis.blocked_detail)
                                termination_detail["code"] = termination_code
                                termination_reason = blocked_milestone_termination_reason(analysis.blocked_detail)
                                decision = RuntimeEvaluationDecision(
                                    None,
                                    None,
                                    state,
                                    should_stop=True,
                                    termination_code=termination_code,
                                    termination_reason=termination_reason,
                                    termination_detail=termination_detail,
                                )
                                terminated_by_policy = True
                                harness.stop_case(session, termination_reason or "阶段式动态评估触发提前终止")
                                logger.warning(
                                    "evaluator_policy_stop",
                                    extra={
                                        "事件": "策略提前终止",
                                        **policy_stop_log_extra(
                                            case_id,
                                            task_case,
                                            decision,
                                            termination_code,
                                            termination_reason,
                                        ),
                                    },
                                )
                                break
                        continue

                    milestone, boundary, milestone_score = analysis.hit
                    requires_llm_review = milestone_score.status != StageStatus.PASS
                    if requires_llm_review and self._standard_judge is None:
                        if analysis.attempt_detail is not None:
                            review_detail = analysis.attempt_detail.get("llm_semantic_review")
                            if isinstance(review_detail, dict):
                                review_detail["status"] = "skipped_no_standard_judge"
                            state.match_attempts.append(analysis.attempt_detail)
                        continue

                    decision = self._evaluate_checkpoint(
                        config=config,
                        task_case=task_case,
                        trajectory=trajectory,
                        state=state,
                        scorer=scorer,
                        milestone=milestone,
                        boundary=boundary,
                        milestone_score=milestone_score,
                    )
                    if requires_llm_review and analysis.attempt_detail is not None:
                        review_detail = analysis.attempt_detail.get("llm_semantic_review")
                        if isinstance(review_detail, dict) and decision.stage_result is not None:
                            review_detail["status"] = "accepted" if decision.checkpoint is not None else "rejected"
                            review_detail["judge_status"] = decision.stage_result.status.value
                            review_detail["judge_stage_score"] = decision.stage_result.stage_score
                    if analysis.attempt_detail is not None:
                        state.match_attempts.append(analysis.attempt_detail)
                    state = decision.next_state
                    if decision.should_stop:
                        termination_code = decision.termination_code
                        termination_reason = decision.termination_reason
                        termination_detail = decision.termination_detail
                        terminated_by_policy = True
                        harness.stop_case(session, termination_reason or "阶段式动态评估触发提前终止")
                        logger.warning(
                            "evaluator_policy_stop",
                            extra={
                                "事件": "策略提前终止",
                                **policy_stop_log_extra(case_id, task_case, decision, termination_code, termination_reason),
                            },
                        )
                        break

                if terminated_by_policy or not advance.continue_running:
                    break

            if not terminated_by_policy:
                pending_stage_reports = pending_required_stage_results(task_case, state)
                if pending_stage_reports:
                    state.stage_reports.extend(pending_stage_reports)
                settlement, stage_result, next_weights, policy_update = self._finish_settlement(
                    state.settlements,
                    task_case,
                    trajectory,
                    state.matched_settlements,
                    state.weights,
                    state.evaluation_policy,
                    scorer,
                )
                state.settlements.append(settlement)
                state.stage_reports.append(stage_result)
                state.weights = next_weights
                state.evaluation_policy = policy_update.next_policy
            report = self._runtime_report(
                task_case,
                trajectory,
                state.stage_reports,
                state.matched_settlements,
                scorer,
            )
            runtime_metrics = build_runtime_metrics(
                started_monotonic=metrics_recorder.started_monotonic,
                finished_monotonic=time.perf_counter(),
                started_at=metrics_recorder.started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                trajectory=trajectory,
                llm_calls=metrics_recorder.llm_calls,
            )
            report.runtime_metrics = runtime_metrics
            raw_summary = harness.raw_summary_from_session(session)
            task_snapshot = task_case_snapshot(case_id, task_case, trajectory)
            raw_summary.update(
                runtime_diagnostics_summary(
                    task_case=task_case,
                    trajectory=trajectory,
                    state=state,
                )
            )
            raw_summary["runtime_metrics"] = runtime_metrics
            raw_summary["task_case_snapshot"] = task_snapshot
            if task_description_mismatched(task_snapshot):
                logger.warning(
                    "evaluator_task_description_mismatch",
                    extra={
                        "事件": "task_description与首条用户消息不一致",
                        "case_id": case_id,
                        "task_description": task_snapshot["task_description"],
                        "initial_user_message_excerpt": task_snapshot["initial_user_message_excerpt"],
                        "scenario_name": task_snapshot["scenario_name"],
                    },
                )
            if termination_detail is not None:
                raw_summary["termination_detail"] = termination_detail
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
            try:
                self._teardown_session_safely(harness, session, config.benchmark, run_id, case_id)
            finally:
                reset_runtime_metrics_recorder(metrics_token)

    def _evaluate_checkpoint(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        state: RuntimeEvaluationState,
        scorer: GeneralScorer,
        milestone: Milestone,
        boundary: Boundary,
        milestone_score: MilestoneScore,
    ) -> RuntimeEvaluationDecision:
        if milestone is None or boundary is None or milestone_score is None:
            raise ValueError("checkpoint 阶段评估参数不能为空")
        settlement, stage_result, next_weights, policy_update = self._append_milestone_settlement(
            settlements=state.settlements,
            matched=state.matched_settlements,
            task_case=task_case,
            trajectory=trajectory,
            milestone=milestone,
            boundary=boundary,
            milestone_score=milestone_score,
            weights=state.weights,
            evaluation_policy=state.evaluation_policy,
            scorer=scorer,
        )
        if milestone_score.status != StageStatus.PASS and (
            stage_result.status != StageStatus.PASS
            or stage_result.stage_score < self._thresholds.pass_threshold
        ):
            return RuntimeEvaluationDecision(None, stage_result, state)
        state.matched_settlements[milestone.milestone_id] = settlement
        state.settlements.append(settlement)
        state.stage_reports.append(stage_result)
        state.weights = next_weights
        state.evaluation_policy = policy_update.next_policy
        next_state = state
        if policy_update.should_stop:
            return RuntimeEvaluationDecision(
                settlement,
                stage_result,
                next_state,
                should_stop=True,
                termination_code=policy_update.termination_code,
                termination_reason=policy_update.termination_reason,
                termination_detail={
                    "stage_score": stage_result.stage_score,
                    "stage_status": stage_result.status.value,
                    "current_policy": policy_update.current_policy.to_dict(),
                    "next_policy": policy_update.next_policy.to_dict(),
                },
            )
        stop_decision = self._should_stop_after_stage(config, task_case, trajectory, stage_result, scorer)
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

    def _evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        scorer: GeneralScorer,
        evaluation_policy: EvaluationPolicyState,
    ) -> tuple[StageEvaluationResult, dict[Dimension, float], EvaluationPolicyUpdate]:
        if evaluation_policy is None:
            raise ValueError("evaluation_policy 不能为空")
        level = evaluation_policy.effective_level()
        if level == EvaluationLevel.CHEAP:
            stage_result = self._cheap_judge.evaluate_stage(interval, task_case, trajectory, weights)
        elif level == EvaluationLevel.STANDARD:
            if self._standard_judge is None:
                raise JudgeConfigurationError("standard 评估需要配置真实 LLMJudge")
            stage_result = self._standard_judge.evaluate_stage(interval, task_case, trajectory, weights)
        elif level == EvaluationLevel.EXPENSIVE:
            if self._expensive_judge is None:
                raise JudgeConfigurationError("expensive 评估需要配置真实 LLMJudge")
            stage_result = self._expensive_judge.evaluate_stage(interval, task_case, trajectory, weights)
        else:
            raise JudgeConfigurationError(f"未知评估粒度: {level}")
        stage_result.evaluator_level = level
        stage_result = self._enrich_stage_result(interval, task_case, trajectory, stage_result, scorer)
        next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.uncertainty, self._weight_config)
        stage_result.next_weights = next_weights
        policy_update = update_evaluation_policy(evaluation_policy, stage_result, self._thresholds)
        stage_result.metadata["active_evaluation_policy"] = policy_update.current_policy.to_dict()
        stage_result.metadata["next_evaluation_policy"] = policy_update.next_policy.to_dict()
        stage_result.metadata["evaluation_policy_update"] = {
            "should_stop": policy_update.should_stop,
            "termination_code": policy_update.termination_code,
            "termination_reason": policy_update.termination_reason,
        }
        return stage_result, next_weights, policy_update
    

    def _enrich_stage_result(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        result: StageEvaluationResult,
        scorer: GeneralScorer,
    ) -> StageEvaluationResult:
        graph = task_case.milestone_graph or MilestoneGraph()
        context = ScoringContext(task_case=task_case)
        _, minefield_score, fatal_minefield = self.evaluate_minefields(graph, trajectory, scorer=scorer, context=context)
        return enrich_stage_result(interval, result, minefield_score, fatal_minefield, self._thresholds)

    def _runtime_report(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_reports: list[StageEvaluationResult],
        matched_settlements: dict[str, HarnessStageSettlement] | None,
        scorer: GeneralScorer,
    ) -> TrajectoryEvaluationReport:
        graph = task_case.milestone_graph or MilestoneGraph()
        context = ScoringContext(task_case=task_case)
        minefield_matches, _, _ = self.evaluate_minefields(
            graph,
            trajectory,
            scorer=scorer,
            context=context,
        )
        matched_ids = set(matched_settlements or {})
        if not matched_ids:
            matched_ids = {
                stage.milestone_id
                for stage in stage_reports
                if stage.milestone_id is not None
                and stage.status != StageStatus.MISSING
                and stage.metadata.get("synthetic_pending_required") is not True
            }
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
            overall_score=overall_score(stage_reports, minefield_penalty_score(minefield_matches)),
            stage_reports=stage_reports,
            minefield_matches=minefield_matches,
            first_failure_stage_id=first_failure_stage_id(stage_reports),
        )

    def _record_runtime_minefields(
        self,
        state: RuntimeEvaluationState,
        matches: list[JsonObject],
        max_score: float,
        fatal: bool,
    ) -> None:
        """记录运行期 minefield 命中并按 minefield/boundary 去重。"""
        if state is None or matches is None:
            raise ValueError("运行期 minefield 记录参数不能为空")
        seen = {
            (str(match.get("minefield_id")), str(match.get("boundary_id")))
            for match in state.minefield_matches
            if isinstance(match, dict)
        }
        for match in matches:
            key = (str(match.get("minefield_id")), str(match.get("boundary_id")))
            if key in seen:
                continue
            seen.add(key)
            state.minefield_matches.append(match)
        state.max_minefield_score = max(state.max_minefield_score, max_score)
        state.fatal_minefield = state.fatal_minefield or fatal

    def _finish_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        task_case: TaskCase,
        trajectory: Trajectory,
        matched: dict[str, HarnessStageSettlement],
        weights: dict[Dimension, float],
        evaluation_policy: EvaluationPolicyState,
        scorer: GeneralScorer,
    ) -> tuple[HarnessStageSettlement, StageEvaluationResult, dict[Dimension, float], EvaluationPolicyUpdate]:
        if settlements is None or task_case is None or trajectory is None or matched is None or weights is None:
            raise ValueError("finish 结算参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        last_step_index = trajectory.steps[-1].index if trajectory.steps else 0
        analysis = graph.metadata.get("graph_analysis", {}) if isinstance(graph.metadata, dict) else {}
        finish_anchor_id = analysis.get("finish_stage_anchor_predecessor_id") if isinstance(analysis, dict) else None
        if finish_anchor_id == START_NODE_ID:
            boundary_index = trajectory.first_step_index - 1
        elif isinstance(finish_anchor_id, str) and finish_anchor_id in matched:
            boundary_index = matched[finish_anchor_id].end_step_index
        else:
            boundary_index = max(
                (settlement.end_step_index for settlement in matched.values()),
                default=trajectory.first_step_index - 1,
            )
        end_step_index = max(boundary_index, last_step_index)
        start_step_index = stage_start_step_index(
            trajectory.successor_by_boundary,
            boundary_index,
            end_step_index,
        )
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=None,
            stage_anchor_milestone_id=finish_anchor_id if isinstance(finish_anchor_id, str) else None,
            start_boundary_step_index=boundary_index,
            start_step_index=start_step_index,
            end_step_index=end_step_index,
            status=StageStatus.PASS,
            evidence=["finish 结算节点"],
        )
        return self._append_stage_settlement(
            settlements=settlements,
            kind="finish",
            interval=interval,
            matching_detail=build_finish_matching_detail(graph=graph, matched=matched),
            task_case=task_case,
            trajectory=trajectory,
            weights=weights,
            evaluation_policy=evaluation_policy,
            scorer=scorer,
            metadata={
                "predecessor_milestone_ids": sorted(matched),
            },
        )

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
        evaluation_policy: EvaluationPolicyState,
        scorer: GeneralScorer,
    ) -> tuple[HarnessStageSettlement, StageEvaluationResult, dict[Dimension, float], EvaluationPolicyUpdate]:
        graph = task_case.milestone_graph
        if graph is None:
            raise ValueError("TaskCase 缺少 milestone_graph")
        anchor_id, boundary_index = stage_start_for_milestone(graph, milestone.milestone_id, matched, trajectory)
        start_step_index = stage_start_step_index(
            trajectory.successor_by_boundary,
            boundary_index,
            boundary.step_index,
        )
        ready_milestone_ids_before_match = [item.milestone_id for item in ready_milestones(graph, matched)]
        interval = StageInterval(
            stage_id=f"runtime:st{len(settlements)}",
            milestone_id=milestone.milestone_id,
            stage_anchor_milestone_id=anchor_id,
            start_boundary_step_index=boundary_index,
            start_step_index=start_step_index,
            end_step_index=boundary.step_index,
            status=milestone_score.status,
            milestone_score=milestone_score,
            evidence=list(milestone_score.evidence),
        )
        predecessor_milestone_ids = list(milestone.dependency_predecessor_ids)
        return self._append_stage_settlement(
            settlements=settlements,
            kind="milestone",
            interval=interval,
            matching_detail=build_milestone_matching_detail(
                graph=graph,
                matched=matched,
                milestone=milestone,
                boundary=boundary,
                milestone_score=milestone_score,
                ready_milestone_ids_before_match=ready_milestone_ids_before_match,
            ),
            task_case=task_case,
            trajectory=trajectory,
            weights=weights,
            evaluation_policy=evaluation_policy,
            scorer=scorer,
            boundary_id=boundary.boundary_id,
            boundary_step_index=boundary.step_index,
            checkpointed=True,
            metadata={
                "predecessor_milestone_ids": predecessor_milestone_ids,
                "dependency_predecessor_milestone_ids": predecessor_milestone_ids,
            },
        )

    def _append_stage_settlement(
        self,
        settlements: list[HarnessStageSettlement],
        kind: str,
        interval: StageInterval,
        matching_detail: JsonObject,
        task_case: TaskCase,
        trajectory: Trajectory,
        weights: dict[Dimension, float],
        evaluation_policy: EvaluationPolicyState,
        scorer: GeneralScorer,
        boundary_id: str | None = None,
        boundary_step_index: int | None = None,
        checkpointed: bool = False,
        metadata: JsonObject | None = None,
    ) -> tuple[HarnessStageSettlement, StageEvaluationResult, dict[Dimension, float], EvaluationPolicyUpdate]:
        stage_result, next_weights, policy_update = self._evaluate_stage(
            interval,
            task_case,
            trajectory,
            weights,
            scorer,
            evaluation_policy,
        )
        merged_metadata: JsonObject = {
            "stage_report": stage_result.to_dict(),
            "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
            "stage_start_boundary_step_index": interval.start_boundary_step_index,
            "stage_start_step_index": interval.start_step_index,
            "stage_end_step_index": interval.end_step_index,
            **(metadata or {}),
            "stage_trace": build_stage_trace(trajectory=trajectory, interval=interval),
            "milestone_matching": matching_detail,
        }
        settlement = HarnessStageSettlement(
            settlement_id=f"st{len(settlements)}",
            kind=kind,
            milestone_id=interval.milestone_id,
            start_step_index=interval.start_step_index,
            end_step_index=interval.end_step_index,
            boundary_id=boundary_id,
            boundary_step_index=boundary_step_index,
            score=stage_result.stage_score,
            status=stage_result.status.value,
            checkpointed=checkpointed,
            evidence=list(stage_result.evidence),
            metadata=merged_metadata,
        )
        return settlement, stage_result, next_weights, policy_update

    def _should_stop_after_stage(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        stage_result: StageEvaluationResult,
        scorer: GeneralScorer,
    ) -> tuple[str, str] | None:
        if config is None or task_case is None or trajectory is None or stage_result is None:
            raise ValueError("终止策略参数不能为空")
        graph = task_case.milestone_graph or MilestoneGraph()
        if config.stop_on_minefield:
            context = ScoringContext(task_case=task_case)
            matches, max_score, fatal = self.evaluate_minefields(graph, trajectory, scorer=scorer, context=context)
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
