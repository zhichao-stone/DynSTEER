from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
import time
from typing import Mapping, TYPE_CHECKING

from dynsteer.config import default_dynamic_weight_config
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_step_minefields
from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    pending_milestone_stage_results,
    runtime_diagnostics_summary,
    task_case_snapshot,
)
from dynsteer.evaluate.telemetry import policy_stop_log_extra
from dynsteer.evaluate.scoring import (
    GeneralScorer,
    minefield_penalty_score,
    overall_score,
)
from dynsteer.evaluate.weights import select_initial_weights
from dynsteer.harness.model import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement
from dynsteer.judges import CheapJudge, StandardJudge, ExpensiveJudge
from dynsteer.llm import build_llm_from_env
from dynsteer.metrics import activate_runtime_metrics_recorder, build_runtime_metrics, reset_runtime_metrics_recorder
from dynsteer.model import (
    AgentStepClosure,
    DynamicWeightConfig,
    JsonObject,
    MilestoneGraph,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    RuntimeMetricsRecorder,
    ScoringContext,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    EvaluationTerminationState,
    ThresholdConfig,
    Trajectory,
    TrajectoryEvaluationReport,
)
from dynsteer.progress import CaseProgressReporter

if TYPE_CHECKING:
    from dynsteer.adapter.base import BaseBenchmarkHarness

logger = logging.getLogger(__name__)
DEFAULT_POLICY_STOP_REASON = "阶段式动态评估触发提前终止"


class DynSTEEREvaluator:
    """DynSTEER 执行编排与阶段式动态评估入口。"""

    def __init__(
        self,
        cheap_judge: CheapJudge | None = None,
        standard_judge: StandardJudge | None = None,
        expensive_judge: ExpensiveJudge | None = None,
        thresholds: ThresholdConfig | None = None,
        weight_config: DynamicWeightConfig | None = None,
    ) -> None:
        self._cheap_judge = cheap_judge or CheapJudge()
        self._standard_judge = standard_judge
        self._expensive_judge = expensive_judge
        self._thresholds = thresholds or ThresholdConfig()
        self._weight_config = weight_config or default_dynamic_weight_config()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DynSTEEREvaluator":
        """从环境变量构建评估器。"""
        source = env if env is not None else os.environ
        llm = build_llm_from_env(source)
        if llm is None:
            return cls(cheap_judge=CheapJudge())
        standard_passes = int(source.get("DYNSTEER_STANDARD_JUDGE_PASSES", "3"))
        expensive_passes = int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3"))
        return cls(
            cheap_judge=CheapJudge(),
            standard_judge=StandardJudge(llm=llm, passes=standard_passes),
            expensive_judge=ExpensiveJudge(llm=llm, passes=expensive_passes),
        )

    def evaluate_minefields(
        self,
        graph: MilestoneGraph,
        trajectory: Trajectory,
        scorer: GeneralScorer | None = None,
        context: ScoringContext | None = None,
    ) -> tuple[list[JsonObject], float, bool]:
        """评估轨迹是否触发 minefield。"""
        matches: list[JsonObject] = []
        seen: set[tuple[str, str]] = set()
        max_score = 0.0
        fatal = False
        for step in trajectory.steps:
            boundary = candidate_boundary_for_current_step(trajectory, step)
            (boundary_matches, boundary_score, boundary_fatal) = evaluate_minefields_at_boundary(
                graph, trajectory, boundary, scorer, context
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
        harness: BaseBenchmarkHarness,
        config: HarnessRunConfig,
        task_case: TaskCase,
        progress_reporter: CaseProgressReporter | None = None,
    ) -> HarnessRunResult:
        """执行 benchmark case，并进行阶段式动态评估。"""
        case_id = task_case.case_id
        harness.prepare_config(config)
        run_id = harness.build_run_id(config, case_id)
        raw_output_dir = config.runs_dir / config.benchmark / run_id / case_id / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)

        session: object | None = None
        termination = EvaluationTerminationState()
        metrics_recorder = RuntimeMetricsRecorder()
        metrics_token = activate_runtime_metrics_recorder(metrics_recorder)

        try:
            session = harness.start_case(config, case_id, raw_output_dir)
            runtime_initial_state = harness.initial_state_from_session(session)
            if runtime_initial_state is not None:
                task_case.initial_state = runtime_initial_state
                task_case.metadata["runtime_initial_state_source"] = "harness_session"
                task_case.metadata["runtime_initial_state_summary"] = _state_namespace_summary(runtime_initial_state)
            else:
                task_case.metadata.setdefault("runtime_initial_state_source", "adapted_case")
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
                milestone_frontier=initialize_milestone_frontier(task_case.milestone_graph),
            )

            def evaluate_closed_agent_step(closure: AgentStepClosure) -> RuntimeEvaluationDecision | None:
                def checkpoint_evaluator(**kwargs: object) -> RuntimeEvaluationDecision:
                    return evaluate_checkpoint(
                        **kwargs,
                        cheap_judge=self._cheap_judge,
                        standard_judge=self._standard_judge,
                        expensive_judge=self._expensive_judge,
                        thresholds=self._thresholds,
                        weight_config=self._weight_config,
                    )

                return self._evaluate_step(
                    harness=harness,
                    session=session,
                    task_case=task_case,
                    eval_function=lambda: evaluate_agent_step(
                        config=config,
                        task_case=task_case,
                        trajectory=trajectory,
                        state=state,
                        step=closure.end_step,
                        closure_steps=list(closure.steps),
                        scorer=scorer,
                        standard_judge=self._standard_judge,
                        thresholds=self._thresholds,
                        evaluate_checkpoint=checkpoint_evaluator,
                    )
                )

            while True:
                advance = harness.advance_case(session)
                if advance.snapshots:
                    snapshot_by_id = {snapshot.snapshot_id: snapshot for snapshot in trajectory.snapshots}
                    for snapshot in advance.snapshots:
                        snapshot_by_id[snapshot.snapshot_id] = snapshot
                    trajectory.snapshots = sorted(snapshot_by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))
                trajectory.final_state = harness.final_state_from_session(session)
                trajectory.metrics = harness.metrics_from_session(session)

                completed_agent_steps = 0
                for step in advance.steps:
                    trajectory.append_step(step)
                    decision = self._evaluate_step(
                        harness=harness,
                        session=session,
                        task_case=task_case,
                        eval_function=lambda: evaluate_step_minefields(
                            config=config,
                            task_case=task_case,
                            trajectory=trajectory,
                            state=state,
                            step=step,
                            scorer=scorer,
                        )
                    )
                    if decision is not None:
                        state = decision.next_state
                        termination = decision.termination
                        break

                    closure = state.agent_step_tracker.ingest(step)
                    if closure is None:
                        continue
                    completed_agent_steps += 1
                    decision = evaluate_closed_agent_step(closure)
                    if decision is not None:
                        state = decision.next_state
                        termination = decision.termination
                        break

                if not termination.should_stop and not advance.continue_running:
                    closure = state.agent_step_tracker.finalize()
                    if closure is not None:
                        completed_agent_steps += 1
                        decision = evaluate_closed_agent_step(closure)
                        if decision is not None:
                            state = decision.next_state
                            termination = decision.termination

                if progress_reporter is not None and completed_agent_steps > 0:
                    progress_reporter.case_advanced(case_id, completed_agent_steps)

                if termination.should_stop or not advance.continue_running:
                    break

            pending_stage_reports = self._new_pending_stage_reports(task_case, state)
            if pending_stage_reports:
                state.stage_reports.extend(pending_stage_reports)
            elif not termination.should_stop:
                settlement, stage_result, next_policy = finish_settlement(
                    state.settlements,
                    task_case,
                    trajectory,
                    scorer,
                    state,
                )
                state.settlements.append(settlement)
                state.stage_reports.append(stage_result)
                state.evaluation_policy = next_policy
            report = self._runtime_report(task_case, trajectory, state)
            runtime_metrics = build_runtime_metrics(
                started_monotonic=metrics_recorder.started_monotonic,
                finished_monotonic=time.perf_counter(),
                started_at=metrics_recorder.started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                trajectory=trajectory,
                llm_calls=metrics_recorder.llm_calls,
                agent_step_count=state.agent_step_tracker.completed_count,
            )
            report.runtime_metrics = runtime_metrics
            raw_summary = harness.raw_summary_from_session(session)
            task_snapshot = task_case_snapshot(task_case)
            raw_summary.update(runtime_diagnostics_summary(task_case=task_case, trajectory=trajectory, state=state))
            raw_summary["runtime_metrics"] = runtime_metrics
            raw_summary["task_case_snapshot"] = task_snapshot
            if termination.termination_detail is not None:
                raw_summary["termination_detail"] = termination.termination_detail
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
                termination=termination,
            )
        finally:
            try:
                self._teardown_session_safely(harness, session, config.benchmark, run_id, case_id)
            finally:
                reset_runtime_metrics_recorder(metrics_token)

    def _evaluate_step(
        self,
        harness: BaseBenchmarkHarness,
        session: object,
        task_case: TaskCase,
        eval_function: Callable[[], RuntimeEvaluationDecision | None],
    ) -> RuntimeEvaluationDecision | None:
        """执行单步评估函数，并统一处理策略提前终止副作用。"""
        decision = eval_function()
        if decision is None or not decision.termination.should_stop:
            return None
        termination_reason = decision.termination.termination_reason or DEFAULT_POLICY_STOP_REASON
        decision.termination.termination_reason = termination_reason
        decision.next_state.evaluation_termination = decision.termination
        harness.stop_case(session, termination_reason)
        logger.warning("evaluator_policy_stop", extra={"事件": "策略提前终止", **policy_stop_log_extra(task_case, decision)})
        return decision

    def _new_pending_stage_reports(
        self, task_case: TaskCase, state: RuntimeEvaluationState
    ) -> list[StageEvaluationResult]:
        """生成尚未写入过的 pending milestone synthetic stage。"""
        existing_pending_ids = {
            stage.milestone_id
            for stage in state.stage_reports
            if stage.metadata.get("synthetic_pending_milestone") is True and stage.milestone_id is not None
        }
        return [
            stage
            for stage in pending_milestone_stage_results(task_case, state)
            if stage.milestone_id not in existing_pending_ids
        ]

    def _runtime_report(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        state: RuntimeEvaluationState,
    ) -> TrajectoryEvaluationReport:
        graph = task_case.milestone_graph
        minefield_matches = list(state.minefield_matches)
        matched_ids = state.matched_settlements
        stage_reports = state.stage_reports

        if not matched_ids:
            matched_ids = {
                stage.milestone_id
                for stage in stage_reports
                if stage.milestone_id is not None
                and stage.status != StageStatus.MISSING
                and stage.metadata.get("synthetic_pending_milestone") is not True
            }
        milestone_ids = {node.milestone_id for node in graph.nodes}
        if not graph.nodes:
            coverage = "none"
        elif milestone_ids.issubset(matched_ids):
            coverage = "full"
        elif matched_ids:
            coverage = "partial"
        else:
            coverage = "none"
        return TrajectoryEvaluationReport(
            run_id=trajectory.run_id,
            task_id=trajectory.task_id,
            milestone_coverage=coverage,
            overall_score=overall_score(stage_reports, minefield_penalty_score(minefield_matches)),
            stage_reports=stage_reports,
            minefield_matches=minefield_matches,
            first_failure_stage_id=next(
                (
                    stage.stage_id
                    for stage in stage_reports
                    if stage.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}
                ),
                None,
            ),
        )

    def _teardown_session_safely(
        self, harness: BaseBenchmarkHarness, session: object | None, benchmark: str, run_id: str, case_id: str
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


def _state_namespace_summary(state: JsonObject) -> JsonObject:
    """生成状态 namespace 行数摘要，避免 raw summary 嵌入完整初始状态。"""
    namespaces = state.get("namespaces")
    if not isinstance(namespaces, dict):
        return {"namespace_count": 0, "row_counts": {}}
    row_counts = {
        str(namespace): len(rows)
        for namespace, rows in namespaces.items()
        if isinstance(rows, list)
    }
    return {"namespace_count": len(namespaces), "row_counts": row_counts}
