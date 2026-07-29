from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Mapping, TYPE_CHECKING

from dynsteer.config import default_dynamic_weight_config
from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    pending_milestone_stage_results,
    runtime_diagnostics_summary,
    task_case_snapshot,
)
from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_step_minefields
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
from dynsteer.evaluate.telemetry import policy_stop_log_extra
from dynsteer.evaluate.scoring import (
    GeneralScorer,
    minefield_penalty_score,
    overall_score,
)
from dynsteer.evaluate.state_summary import state_namespace_summary
from dynsteer.evaluate.weights import select_initial_weights
from dynsteer.experiment.model import EvaluationStrategyConfig
from dynsteer.graph import FINISH_NODE_ID
from dynsteer.harness.config import (
    evaluation_strategy_from_mapping,
    load_judge_config_from_env,
    threshold_config_from_mapping,
)
from dynsteer.harness import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement, case_output_dir
from dynsteer.judges import CheapJudge, StandardJudge, ExpensiveJudge
from dynsteer.llm import build_llm_from_config, build_llm_from_env
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
        strategy: EvaluationStrategyConfig | None = None,
    ) -> None:
        self._cheap_judge = cheap_judge or CheapJudge()
        self._standard_judge = standard_judge
        self._expensive_judge = expensive_judge
        self._thresholds = thresholds or ThresholdConfig()
        self._weight_config = weight_config or default_dynamic_weight_config()
        self._strategy = strategy or EvaluationStrategyConfig()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DynSTEEREvaluator":
        """从环境变量构建评估器。"""
        source = env if env is not None else os.environ
        return cls.from_config({}, judge_config=load_judge_config_from_env(source), env=source)

    @classmethod
    def from_config(
        cls,
        config: HarnessRunConfig | Mapping[str, Any] | None = None,
        judge_config: Mapping[str, Any] | None = None,
        strategy: EvaluationStrategyConfig | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "DynSTEEREvaluator":
        """从 run / experiment 配置构建评估器。

        入参：
            config: HarnessRunConfig 或 metadata 映射。
            judge_config: 覆盖使用的 Judge profile。
            strategy: 覆盖使用的评估策略。
            env: 环境变量来源；API key 仍从这里读取。
        输出：
            已配置阈值、Judge 和策略的 DynSTEEREvaluator。
        """
        source = env if env is not None else os.environ
        metadata = _metadata_from_config(config)
        raw_judge = judge_config if judge_config is not None else metadata.get("judge")
        llm = (
            build_llm_from_config(raw_judge, source)
            if isinstance(raw_judge, Mapping) and bool(raw_judge)
            else build_llm_from_env(source)
        )
        raw_thresholds = metadata.get("thresholds")
        thresholds = threshold_config_from_mapping(raw_thresholds if isinstance(raw_thresholds, Mapping) else None)
        raw_strategy = metadata.get("strategy")
        active_strategy = strategy or evaluation_strategy_from_mapping(raw_strategy if isinstance(raw_strategy, Mapping) else None)
        if llm is None:
            return cls(cheap_judge=CheapJudge(), thresholds=thresholds, strategy=active_strategy)
        judge_mapping = raw_judge if isinstance(raw_judge, Mapping) else {}
        standard_passes = _int_from_mapping(
            judge_mapping, "standard_passes", int(source.get("DYNSTEER_STANDARD_JUDGE_PASSES", "3"))
        )
        expensive_passes = _int_from_mapping(
            judge_mapping, "expensive_passes", int(source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES", "3"))
        )
        return cls(
            cheap_judge=CheapJudge(),
            standard_judge=StandardJudge(llm=llm, passes=standard_passes),
            expensive_judge=ExpensiveJudge(llm=llm, passes=expensive_passes),
            thresholds=thresholds,
            strategy=active_strategy,
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
        raw_output_dir = case_output_dir(config.runs_dir, config, run_id, case_id, "dynsteer_evaluate") / "raw"
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
                task_case.metadata["runtime_initial_state_summary"] = state_namespace_summary(runtime_initial_state)
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
            state = self._initial_runtime_state(task_case)

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
                        eval_function=lambda step=step: evaluate_step_minefields(
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
                    decision = self._evaluate_step(
                        harness=harness,
                        session=session,
                        task_case=task_case,
                        eval_function=lambda closure=closure: self._evaluate_closed_agent_step(
                            config=config,
                            task_case=task_case,
                            trajectory=trajectory,
                            state=state,
                            closure=closure,
                            scorer=scorer,
                        ),
                    )
                    if decision is not None:
                        state = decision.next_state
                        termination = decision.termination
                        break

                if not termination.should_stop and not advance.continue_running:
                    closure = state.agent_step_tracker.finalize()
                    if closure is not None:
                        completed_agent_steps += 1
                        decision = self._evaluate_step(
                            harness=harness,
                            session=session,
                            task_case=task_case,
                            eval_function=lambda closure=closure: self._evaluate_closed_agent_step(
                                config=config,
                                task_case=task_case,
                                trajectory=trajectory,
                                state=state,
                                closure=closure,
                                scorer=scorer,
                            ),
                        )
                        if decision is not None:
                            state = decision.next_state
                            termination = decision.termination

                if progress_reporter is not None and completed_agent_steps > 0:
                    progress_reporter.case_advanced(case_id, completed_agent_steps)

                if termination.should_stop or not advance.continue_running:
                    break

            self._finalize_state_reports(
                task_case,
                trajectory,
                scorer,
                state,
                termination,
                finish_on_termination=False,
            )
            runtime_metrics = self._build_runtime_metrics(metrics_recorder, trajectory, state.agent_step_tracker.completed_count)
            return self._build_runtime_result(
                benchmark=config.benchmark,
                case_id=case_id,
                run_id=run_id,
                task_case=task_case,
                trajectory=trajectory,
                raw_output_dir=raw_output_dir,
                raw_summary=harness.raw_summary_from_session(session),
                state=state,
                termination=termination,
                runtime_metrics=runtime_metrics,
                metadata=self._report_metadata(config, method_fallback="dynsteer_evaluate"),
            )
        finally:
            try:
                self._teardown_session_safely(harness, session, config.benchmark, run_id, case_id)
            finally:
                reset_runtime_metrics_recorder(metrics_token)

    def evaluate_replay(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        scorer: GeneralScorer,
        config: HarnessRunConfig,
    ) -> HarnessRunResult:
        """对完整轨迹执行离线阶段式回放评估。

        入参：
            task_case: 已适配的 DynSTEER case。
            trajectory: Default 或外部运行生成的完整轨迹。
            scorer: benchmark 约束评分器。
            config: 当前 replay 运行配置。
        输出：
            不调用 harness.stop_case() 的 replay 评估结果。
        """
        if task_case is None or trajectory is None or scorer is None or config is None:
            raise ValueError("task_case、trajectory、scorer 和 config 不能为空")
        case_id = task_case.case_id
        run_id = self._replay_run_id(config, trajectory, case_id)
        raw_output_dir = case_output_dir(config.runs_dir, config, run_id, case_id, "dynsteer_replay") / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)
        replay_trajectory = Trajectory(
            run_id=run_id,
            task_id=trajectory.task_id,
            steps=[],
            snapshots=[],
            final_state=trajectory.final_state,
            metrics=dict(trajectory.metrics),
            raw=dict(trajectory.raw),
        )
        state = self._initial_runtime_state(task_case)
        virtual_termination = EvaluationTerminationState()
        metrics_recorder = RuntimeMetricsRecorder()
        metrics_token = activate_runtime_metrics_recorder(metrics_recorder)
        snapshots = sorted(trajectory.snapshots, key=lambda item: (item.after_step_index, item.snapshot_id))
        snapshot_index = 0

        try:
            for step in trajectory.steps:
                replay_trajectory.append_step(step)
                snapshot_index = _append_replay_snapshots(replay_trajectory, snapshots, snapshot_index, step.index)
                decision = evaluate_step_minefields(
                    config=config,
                    task_case=task_case,
                    trajectory=replay_trajectory,
                    state=state,
                    step=step,
                    scorer=scorer,
                )
                virtual_termination = self._record_replay_decision(
                    decision, state, virtual_termination, step_index=step.index
                )
                if virtual_termination.should_stop and not self._strategy.replay_continue_after_virtual_stop:
                    break

                closure = state.agent_step_tracker.ingest(step)
                if closure is None:
                    continue
                decision = self._evaluate_closed_agent_step(
                    config=config,
                    task_case=task_case,
                    trajectory=replay_trajectory,
                    state=state,
                    closure=closure,
                    scorer=scorer,
                )
                virtual_termination = self._record_replay_decision(
                    decision, state, virtual_termination, step_index=closure.end_step.index
                )
                if virtual_termination.should_stop and not self._strategy.replay_continue_after_virtual_stop:
                    break

            if not virtual_termination.should_stop or self._strategy.replay_continue_after_virtual_stop:
                closure = state.agent_step_tracker.finalize()
                if closure is not None:
                    decision = self._evaluate_closed_agent_step(
                        config=config,
                        task_case=task_case,
                        trajectory=replay_trajectory,
                        state=state,
                        closure=closure,
                        scorer=scorer,
                    )
                    virtual_termination = self._record_replay_decision(
                        decision, state, virtual_termination, step_index=closure.end_step.index
                    )

            self._finalize_state_reports(
                task_case,
                replay_trajectory,
                scorer,
                state,
                virtual_termination,
                finish_on_termination=True,
                replay_termination=virtual_termination if virtual_termination.should_stop else None,
            )
            runtime_metrics = self._build_runtime_metrics(
                metrics_recorder,
                replay_trajectory,
                state.agent_step_tracker.completed_count,
            )
            return self._build_runtime_result(
                benchmark=config.benchmark,
                case_id=case_id,
                run_id=run_id,
                task_case=task_case,
                trajectory=replay_trajectory,
                raw_output_dir=raw_output_dir,
                raw_summary={
                    "case_id": case_id,
                    "method": str(config.metadata.get("method") or "dynsteer_replay"),
                    "source_run_id": trajectory.run_id,
                },
                state=state,
                termination=virtual_termination,
                runtime_metrics=runtime_metrics,
                metadata=self._report_metadata(config, method_fallback="dynsteer_replay"),
            )
        finally:
            reset_runtime_metrics_recorder(metrics_token)

    def _initial_runtime_state(self, task_case: TaskCase) -> RuntimeEvaluationState:
        """构造运行期初始状态。

        入参：
            task_case: 已适配的 benchmark case。
        输出：
            带初始权重、start settlement 和 ready frontier 的运行期状态。
        """
        return RuntimeEvaluationState(
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

    def _evaluate_closed_agent_step(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        state: RuntimeEvaluationState,
        closure: AgentStepClosure,
        scorer: GeneralScorer,
    ) -> RuntimeEvaluationDecision | None:
        """评估一个已闭合的 agent step。"""
        return evaluate_agent_step(
            config=config,
            task_case=task_case,
            trajectory=trajectory,
            state=state,
            step=closure.end_step,
            closure_steps=list(closure.steps),
            scorer=scorer,
            standard_judge=self._standard_judge,
            thresholds=self._thresholds,
            evaluate_checkpoint=self._evaluate_checkpoint_for_strategy,
        )

    def _evaluate_checkpoint_for_strategy(self, **kwargs: object) -> RuntimeEvaluationDecision:
        """按当前 judge、阈值、权重和策略执行 checkpoint 结算。"""
        return evaluate_checkpoint(
            **kwargs,
            cheap_judge=self._cheap_judge,
            standard_judge=self._standard_judge,
            expensive_judge=self._expensive_judge,
            thresholds=self._thresholds,
            weight_config=self._weight_config,
            strategy=self._strategy,
        )

    def _finalize_state_reports(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        scorer: GeneralScorer,
        state: RuntimeEvaluationState,
        termination: EvaluationTerminationState,
        finish_on_termination: bool,
        replay_termination: EvaluationTerminationState | None = None,
    ) -> None:
        """写入 pending 或 finish 阶段报告。"""
        state.evaluation_termination = termination
        pending_stage_reports = self._new_pending_stage_reports(task_case, state)
        if pending_stage_reports:
            state.stage_reports.extend(pending_stage_reports)
            return
        if termination.should_stop and not finish_on_termination:
            return
        settlement, stage_result, next_policy = finish_settlement(
            state.settlements,
            task_case,
            trajectory,
            scorer,
            state,
            replay_termination=replay_termination,
            standard_judge=self._standard_judge,
            thresholds=self._thresholds,
        )
        state.settlements.append(settlement)
        state.stage_reports.append(stage_result)
        state.evaluation_policy = next_policy

    def _build_runtime_metrics(
        self,
        metrics_recorder: RuntimeMetricsRecorder,
        trajectory: Trajectory,
        agent_step_count: int,
    ) -> JsonObject:
        """构造统一运行指标。"""
        return build_runtime_metrics(
            started_monotonic=metrics_recorder.started_monotonic,
            finished_monotonic=time.perf_counter(),
            started_at=metrics_recorder.started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            trajectory=trajectory,
            llm_calls=metrics_recorder.llm_calls,
            agent_step_count=agent_step_count,
        )

    def _build_runtime_result(
        self,
        benchmark: str,
        case_id: str,
        run_id: str,
        task_case: TaskCase,
        trajectory: Trajectory,
        raw_output_dir: Path,
        raw_summary: JsonObject,
        state: RuntimeEvaluationState,
        termination: EvaluationTerminationState,
        runtime_metrics: JsonObject,
        metadata: JsonObject,
    ) -> HarnessRunResult:
        """构造统一 HarnessRunResult 与 raw summary。"""
        report = self._runtime_report(task_case, trajectory, state, metadata=metadata)
        report.runtime_metrics = runtime_metrics
        summary = dict(raw_summary)
        summary.update(runtime_diagnostics_summary(task_case=task_case, trajectory=trajectory, state=state))
        summary.update(
            {
                "runtime_metrics": runtime_metrics,
                "terminated_by_policy": termination.should_stop,
                "termination_code": termination.termination_code,
                "termination_reason": termination.termination_reason,
                "stage_settlements": [settlement.to_dict() for settlement in state.settlements],
                "task_case_snapshot": task_case_snapshot(task_case),
            }
        )
        if termination.termination_detail is not None:
            summary["termination_detail"] = termination.termination_detail
        return HarnessRunResult(
            benchmark=benchmark,
            case_id=case_id,
            run_id=run_id,
            task_case=task_case,
            trajectory=trajectory,
            raw_output_dir=raw_output_dir,
            raw_summary=summary,
            stage_settlements=state.settlements,
            evaluation_report=report,
            termination=termination,
        )

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
        if not self._strategy.policy_stop:
            decision.termination = EvaluationTerminationState()
            decision.next_state.evaluation_termination = decision.termination
            if decision.stage_result is not None:
                decision.stage_result.metadata["policy_stop_suppressed"] = True
            return None
        termination_reason = decision.termination.termination_reason or DEFAULT_POLICY_STOP_REASON
        decision.termination.termination_reason = termination_reason
        decision.next_state.evaluation_termination = decision.termination
        harness.stop_case(session, termination_reason)
        logger.warning("evaluator_policy_stop", extra={"事件": "策略提前终止", **policy_stop_log_extra(task_case, decision)})
        return decision

    def _record_replay_decision(
        self,
        decision: RuntimeEvaluationDecision | None,
        state: RuntimeEvaluationState,
        current_termination: EvaluationTerminationState,
        step_index: int,
    ) -> EvaluationTerminationState:
        """记录 replay 虚拟早停，不触发外部 session stop。"""
        if decision is None or not decision.termination.should_stop:
            return current_termination
        if not self._strategy.policy_stop:
            if decision.stage_result is not None:
                decision.stage_result.metadata["policy_stop_suppressed"] = True
            return current_termination
        if current_termination.should_stop:
            return current_termination
        termination = decision.termination
        termination.termination_reason = termination.termination_reason or DEFAULT_POLICY_STOP_REASON
        detail = dict(termination.termination_detail or {})
        detail["virtual_stop_step_index"] = step_index
        detail["replay_continue_after_virtual_stop"] = self._strategy.replay_continue_after_virtual_stop
        termination.termination_detail = detail
        state.evaluation_termination = termination
        if decision.stage_result is not None:
            decision.stage_result.metadata["replay_virtual_stop"] = termination.to_dict()
        return termination

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
        metadata: JsonObject | None = None,
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
            coverage = _empty_graph_whole_trajectory_coverage(stage_reports)
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
            metadata=metadata or {},
        )

    def _replay_run_id(self, config: HarnessRunConfig, trajectory: Trajectory, case_id: str) -> str:
        """构造 replay run_id。"""
        raw_run_id = config.metadata.get("run_id")
        if isinstance(raw_run_id, str) and raw_run_id.strip():
            return raw_run_id.strip()
        return f"{trajectory.run_id}_replay_{case_id}"

    def _report_metadata(self, config: HarnessRunConfig, method_fallback: str) -> JsonObject:
        """构造评估报告实验元数据。"""
        metadata = config.metadata
        default_reference = metadata.get("default_reference")
        default_score = metadata.get("default_score")
        return {
            "method": str(metadata.get("method") or method_fallback),
            "strategy": self._strategy.to_dict(),
            "judge_profile": metadata.get("judge_profile"),
            "threshold_profile": metadata.get("threshold_profile"),
            "model_id": metadata.get("model_id"),
            "repeat_index": metadata.get("repeat_index"),
            "default_score": default_score if isinstance(default_score, (int, float)) else None,
            "default_reference": dict(default_reference) if isinstance(default_reference, dict) else None,
        }

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


def _append_replay_snapshots(
    trajectory: Trajectory,
    snapshots: list[object],
    start_index: int,
    step_index: int,
) -> int:
    """追加当前 step 可见的 replay snapshot。"""
    seen = {snapshot.snapshot_id for snapshot in trajectory.snapshots}
    index = start_index
    while index < len(snapshots):
        snapshot = snapshots[index]
        after_step_index = getattr(snapshot, "after_step_index", None)
        if not isinstance(after_step_index, int) or after_step_index > step_index:
            break
        snapshot_id = getattr(snapshot, "snapshot_id", None)
        if isinstance(snapshot_id, str) and snapshot_id not in seen:
            trajectory.snapshots.append(snapshot)
            seen.add(snapshot_id)
        index += 1
    return index


def _metadata_from_config(config: HarnessRunConfig | Mapping[str, Any] | None) -> Mapping[str, Any]:
    """从 HarnessRunConfig 或映射读取 metadata。"""
    if config is None:
        return {}
    if isinstance(config, HarnessRunConfig):
        return config.metadata
    if isinstance(config, Mapping):
        return config
    raise ValueError("config 必须是 HarnessRunConfig 或 JSON 对象")


def _int_from_mapping(data: Mapping[str, Any], key: str, default: int) -> int:
    """从映射读取整数配置。"""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} 必须是整数")
    return value


def _empty_graph_whole_trajectory_coverage(stage_reports: list[StageEvaluationResult]) -> str:
    """根据空图 whole-trajectory finish 阶段状态生成 coverage。"""
    finish_stage = next(
        (stage for stage in reversed(stage_reports) if stage.milestone_id == FINISH_NODE_ID),
        None,
    )
    if finish_stage is None:
        return "none"
    evaluation = finish_stage.metadata.get("finish_stage_evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("coverage_basis") != "whole_trajectory":
        return "none"
    if finish_stage.status == StageStatus.PASS:
        return "full"
    if finish_stage.status in {StageStatus.WARN, StageStatus.AMBIGUOUS}:
        return "partial"
    return "none"
