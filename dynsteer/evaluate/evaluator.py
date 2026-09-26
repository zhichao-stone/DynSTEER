from __future__ import annotations

import logging
import hashlib
import os
import sys
from datetime import datetime, timezone
import time
from typing import Any, Mapping, TYPE_CHECKING

from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    pending_milestone_stage_results,
    runtime_diagnostics_summary,
    initial_reference_snapshots,
    task_case_snapshot,
)
from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement, record_settlement, referenced_milestone_ids
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_step_minefields
from dynsteer.evaluate.intervention import build_intervention_message, trigger_stage_id as read_trigger_stage_id
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.telemetry import policy_stop_log_extra
from dynsteer.evaluate.scoring import (
    GeneralScorer,
    minefield_penalty_score,
    overall_score,
)
from dynsteer.evaluate.state_summary import apply_runtime_initial_state
from dynsteer.evaluate.weights import select_initial_weights
from dynsteer.experiment.model import EvaluationStrategyConfig, ExperimentMethod
from dynsteer.graph import FINISH_NODE_ID
from dynsteer.harness.config import (
    evaluation_strategy_from_mapping,
    threshold_config_from_mapping,
)
from dynsteer.harness import HarnessRunConfig, HarnessRunResult, HarnessStageSettlement, case_output_dir
from dynsteer.harness.session import collect_session_trajectory
from dynsteer.judges import CheapJudge, StandardJudge, ExpensiveJudge
from dynsteer.llm import build_llm_from_config, build_llm_from_env
from dynsteer.metrics import (
    activate_runtime_metrics_recorder,
    build_replay_timing_metrics,
    build_runtime_metrics,
    reset_runtime_metrics_recorder,
)
from dynsteer.model import (
    AgentStepClosure,
    DynamicWeightConfig,
    JsonObject,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    RuntimeMetricsRecorder,
    StageEvaluationResult,
    StageStatus,
    TaskCase,
    EvaluationTerminationState,
    ThresholdConfig,
    Trajectory,
    TrajectoryEvaluationReport,
    TrajectoryStep,
)
from dynsteer.progress import CaseProgressReporter
from dynsteer.utils import parse_int_value

if TYPE_CHECKING:
    from dynsteer.adapter.base import BaseBenchmarkHarness

logger = logging.getLogger(__name__)
DEFAULT_POLICY_STOP_REASON = 'stage dynamic assessment triggers early termination'


class DynSTEEREvaluator:
    """DynSTEER Implementation and stage Dynamic Assessment portal."""

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
        self._weight_config = weight_config or DynamicWeightConfig()
        self._strategy = strategy or EvaluationStrategyConfig()

    @classmethod
    def from_config(
        cls,
        config: HarnessRunConfig | Mapping[str, Any] | None = None,
        judge_config: Mapping[str, Any] | None = None,
        strategy: EvaluationStrategyConfig | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "DynSTEEREvaluator":
        """From run/ configuration configuration evaluator. Args: config: HarnessRunConfig or metadata map. Judge_config: overwrite for Judge profile. Strategy: overwrite the assessment strategy used. env: source of environmental variables; API key is still read from here. Returns: DynSTEER evaluator has been configured for thresholds, Judge and strategy."""
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
        standard_passes = parse_int_value(
            judge_mapping.get("standard_passes", source.get("DYNSTEER_STANDARD_JUDGE_PASSES")),
            "standard_passes",
            default=3,
            min_value=1,
        )
        expensive_passes = parse_int_value(
            judge_mapping.get("expensive_passes", source.get("DYNSTEER_EXPENSIVE_JUDGE_PASSES")),
            "expensive_passes",
            default=3,
            min_value=1,
        )
        return cls(
            cheap_judge=CheapJudge(),
            standard_judge=StandardJudge(llm=llm, passes=standard_passes),
            expensive_judge=ExpensiveJudge(llm=llm, passes=expensive_passes),
            thresholds=thresholds,
            strategy=active_strategy,
        )

    def evaluate(
        self,
        harness: BaseBenchmarkHarness,
        config: HarnessRunConfig,
        task_case: TaskCase,
        progress_reporter: CaseProgressReporter | None = None,
    ) -> HarnessRunResult:
        """Implement the benchmark case and conduct a phased dynamic assessment."""
        case_id = task_case.case_id
        harness.prepare_config(config)
        raw_output_dir = case_output_dir(config.runs_dir, config, case_id, "dynsteer_evaluate") / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)

        session: object | None = None
        metrics_recorder = RuntimeMetricsRecorder()
        metrics_token = activate_runtime_metrics_recorder(metrics_recorder)
        native_default_result = None
        native_evaluation_seconds: float | None = None

        try:
            session = harness.start_case(config, case_id, raw_output_dir)
            apply_runtime_initial_state(task_case, harness.initial_state_from_session(session), "harness_session")
            language = config.metadata.get("language")
            if isinstance(language, str) and language.strip():
                task_case.metadata["language"] = language.strip()
            scorer = harness.constraint_scorer()
            trajectory = Trajectory(
                task_id=task_case.task_id,
                steps=[],
                snapshots=[],
                final_state=harness.final_state_from_session(session),
                metrics=harness.metrics_from_session(session),
            )
            state = self._initial_runtime_state(task_case)

            def on_step(step: TrajectoryStep) -> tuple[int, bool]:
                decision, closed = self._evaluate_appended_step(config, task_case, trajectory, state, step, scorer)
                stopped = self._apply_live_decision(decision, state, harness, session, task_case, config)
                return int(closed), stopped

            def on_finish() -> tuple[int, bool]:
                closure = state.agent_step_tracker.finalize()
                if closure is None:
                    return 0, False
                decision = self._evaluate_closed_agent_step(config, task_case, trajectory, state, closure, scorer)
                return 1, self._apply_live_decision(decision, state, harness, session, task_case, config)

            collect_session_trajectory(
                harness,
                session,
                trajectory,
                on_step,
                on_finish,
                (lambda count: progress_reporter.case_advanced(case_id, count)) if progress_reporter is not None else None,
            )

            if config.metadata.get("collect_online_native_score") is True:
                native_started = time.perf_counter()
                native_default_result = harness.default_result_from_session(session)
                native_evaluation_seconds = max(time.perf_counter() - native_started, 0.0)

            self._finalize_state_reports(
                task_case,
                trajectory,
                scorer,
                state,
                finish_on_termination=False,
            )
            self._enrich_interventions(state)
            runtime_metrics = self._build_runtime_metrics(metrics_recorder, trajectory, state.agent_step_tracker.completed_count)
            result = self._build_runtime_result(
                task_case=task_case,
                trajectory=trajectory,
                raw_summary=harness.raw_summary_from_session(session),
                state=state,
                runtime_metrics=runtime_metrics,
                metadata=self._report_metadata(config, method_fallback="dynsteer_evaluate"),
            )
            if native_default_result is not None and native_evaluation_seconds is not None:
                self._append_online_native_metrics(
                    runtime_metrics,
                    native_default_result,
                    native_evaluation_seconds,
                )
                result.raw_summary["native_default_result"] = native_default_result.to_dict()
                result.evaluation_report.metadata["native_score"] = native_default_result.score
            return result
        finally:
            try:
                self._teardown_session_safely(harness, session, config.benchmark, case_id)
            finally:
                reset_runtime_metrics_recorder(metrics_token)

    def evaluate_replay(
        self,
        task_case: TaskCase,
        trajectory: Trajectory,
        scorer: GeneralScorer,
        config: HarnessRunConfig,
    ) -> HarnessRunResult:
        """Execute the offline replay evaluation of the complete trajectory. Args: task_case: a suitable DynSTEER case. trajectory: Default or an external operation generates a complete trajectory. Scorer: benchmark binding scoring. Config: current replay running configuration. Returns: Do not call the replay results of the review. stop_case()."""
        if task_case is None or trajectory is None or scorer is None or config is None:
            raise ValueError('task_case, trajectory, scorer, and config must not be empty')
        case_id = task_case.case_id
        raw_output_dir = case_output_dir(config.runs_dir, config, case_id, "dynsteer_replay") / "raw"
        raw_output_dir.mkdir(parents=True, exist_ok=True)
        replay_trajectory = Trajectory(
            task_id=trajectory.task_id,
            steps=[],
            snapshots=[],
            final_state=trajectory.final_state,
            metrics=dict(trajectory.metrics),
            raw=dict(trajectory.raw),
        )
        state = self._initial_runtime_state(task_case)
        metrics_recorder = RuntimeMetricsRecorder()
        metrics_token = activate_runtime_metrics_recorder(metrics_recorder)
        snapshots = sorted(trajectory.snapshots, key=lambda item: (item.after_step_index, item.snapshot_id))
        snapshot_index = 0

        try:
            for step in trajectory.steps:
                replay_trajectory.append_step(step)
                snapshot_index = _append_replay_snapshots(replay_trajectory, snapshots, snapshot_index, step.index)
                decision, _ = self._evaluate_appended_step(
                    config, task_case, replay_trajectory, state, step, scorer
                )
                self._record_replay_decision(decision, state, step.index)
                if state.evaluation_termination.should_stop and not self._strategy.replay_continue_after_virtual_stop:
                    break

            if not state.evaluation_termination.should_stop or self._strategy.replay_continue_after_virtual_stop:
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
                    self._record_replay_decision(decision, state, closure.end_step.index)

            self._finalize_state_reports(
                task_case,
                replay_trajectory,
                scorer,
                state,
                finish_on_termination=True,
            )
            runtime_metrics = self._build_runtime_metrics(
                metrics_recorder,
                replay_trajectory,
                state.agent_step_tracker.completed_count,
            )
            timing = build_replay_timing_metrics(
                source_trajectory=trajectory,
                replay_trajectory=replay_trajectory,
                termination=state.evaluation_termination,
                elapsed_seconds=float(runtime_metrics.get("elapsed_seconds", 0.0) or 0.0),
            )
            runtime_metrics.update(timing)
            result = self._build_runtime_result(
                task_case=task_case,
                trajectory=replay_trajectory,
                raw_summary={
                    "case_id": case_id,
                    "method": str(config.metadata.get("method") or "dynsteer_replay"),
                },
                state=state,
                runtime_metrics=runtime_metrics,
                metadata=self._report_metadata(config, method_fallback="dynsteer_replay"),
            )
            report = result.evaluation_report
            replay_execution = build_replay_execution_summary(
                trajectory,
                replay_trajectory,
                state.evaluation_termination,
                report.milestone_coverage,
                _finish_coverage_basis(report.stage_reports),
            )
            replay_execution["timing"] = timing
            report.metadata["replay_execution"] = replay_execution
            report.runtime_metrics.update(timing)
            result.raw_summary["runtime_metrics"] = runtime_metrics
            if not timing.get("timing_available"):
                logger.warning(
                    "replay_timing_unavailable",
                    extra={
                        'event': 'Historical DEFAULT trajectory is missing execution_timing; rerun DEFAULT with force_eval=True',
                        "benchmark": config.benchmark,
                        "case_id": case_id,
                    },
                )
            return result
        finally:
            reset_runtime_metrics_recorder(metrics_token)

    def _initial_runtime_state(self, task_case: TaskCase) -> RuntimeEvaluationState:
        """Construct the initial state of the runtime. Args: task_case: the appropriate benchmark case. Returns: the active state with the initial weight, start settings and read frontier."""
        return RuntimeEvaluationState(
            weights=select_initial_weights(task_case),
            settlements=[
                HarnessStageSettlement(
                    settlement_id="st0",
                    stage_id="start",
                    kind="start",
                    milestone_id=None,
                    start_step_index=0,
                    end_step_index=0,
                    evidence=['Start Settlement Node'],
                )
            ],
            milestone_frontier=initialize_milestone_frontier(task_case.milestone_graph),
            reference_anchor_snapshots=initial_reference_snapshots(task_case),
            referenced_milestone_ids=referenced_milestone_ids(task_case),
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
        """Assess a closed agent step."""
        return evaluate_agent_step(
            config=config,
            task_case=task_case,
            trajectory=trajectory,
            state=state,
            closure=closure,
            scorer=scorer,
            standard_judge=self._standard_judge,
            thresholds=self._thresholds,
            policy_stop=self._strategy.policy_stop,
            evaluate_checkpoint=self._evaluate_checkpoint_for_strategy,
        )

    def _evaluate_appended_step(
        self,
        config: HarnessRunConfig,
        task_case: TaskCase,
        trajectory: Trajectory,
        state: RuntimeEvaluationState,
        step: TrajectoryStep,
        scorer: GeneralScorer,
    ) -> tuple[RuntimeEvaluationDecision | None, bool]:
        """Assess the additional raw step and the implementation stage when the agent step closes."""
        decision = evaluate_step_minefields(
            task_case,
            trajectory,
            state,
            step,
            scorer,
            self._strategy.policy_stop,
            self._strategy.use_minefields,
        )
        if decision is not None:
            return decision, False
        closure = state.agent_step_tracker.ingest(step)
        if closure is None:
            return None, False
        return self._evaluate_closed_agent_step(config, task_case, trajectory, state, closure, scorer), True

    def _evaluate_checkpoint_for_strategy(self, **kwargs: object) -> RuntimeEvaluationDecision:
        """Execute the checkpoint settlement using the current judge, threshold, weight and strategy."""
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
        finish_on_termination: bool,
    ) -> None:
        """Writes a stage report for sending or finish."""
        pending_stage_reports = self._new_pending_stage_reports(task_case, state)
        if pending_stage_reports:
            state.stage_reports.extend(pending_stage_reports)
            return
        if state.evaluation_termination.should_stop and not finish_on_termination:
            return
        settlement, stage_result = finish_settlement(
            task_case,
            trajectory,
            scorer,
            state,
            standard_judge=self._standard_judge,
            thresholds=self._thresholds,
        )
        record_settlement(state, settlement)
        state.stage_reports.append(stage_result)

    def _build_runtime_metrics(
        self,
        metrics_recorder: RuntimeMetricsRecorder,
        trajectory: Trajectory,
        agent_step_count: int,
    ) -> JsonObject:
        """Construct a uniform operational indicator."""
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
        task_case: TaskCase,
        trajectory: Trajectory,
        raw_summary: JsonObject,
        state: RuntimeEvaluationState,
        runtime_metrics: JsonObject,
        metadata: JsonObject,
    ) -> HarnessRunResult:
        """Construct a unified HarnessRunResult with Rawsummary."""
        termination = state.evaluation_termination
        report = self._runtime_report(task_case, trajectory, state, metadata=metadata)
        report.runtime_metrics = runtime_metrics
        summary = dict(raw_summary)
        summary.update(runtime_diagnostics_summary(task_case=task_case, trajectory=trajectory, state=state))
        summary.update(
            {
                "runtime_metrics": runtime_metrics,
                "task_case_snapshot": task_case_snapshot(task_case),
            }
        )
        return HarnessRunResult(
            trajectory=trajectory,
            raw_summary=summary,
            stage_settlements=state.settlements,
            evaluation_report=report,
            termination=termination,
        )

    def _apply_live_decision(
        self,
        decision: RuntimeEvaluationDecision | None,
        state: RuntimeEvaluationState,
        harness: BaseBenchmarkHarness,
        session: object,
        task_case: TaskCase,
        config: HarnessRunConfig,
    ) -> bool:
        """Executes the step-by-step evaluation function and harmonizes the processing strategy to pre-empt side effects."""
        if decision is None or not decision.termination.should_stop:
            return False
        if not self._strategy.policy_stop:
            if decision.stage_result is not None:
                decision.stage_result.metadata["policy_stop_suppressed"] = True
            return False
        if (
            str(config.metadata.get("method") or "") == ExperimentMethod.DYNSTEER_EVALUATE_GUIDED.value
            and not self._is_fatal_minefield(decision)
            and self._send_guidance(decision, state, harness, session, task_case)
        ):
            return False
        termination_reason = decision.termination.termination_reason or DEFAULT_POLICY_STOP_REASON
        decision.termination.termination_reason = termination_reason
        state.evaluation_termination = decision.termination
        harness.stop_case(session, termination_reason)
        logger.warning("evaluator_policy_stop", extra={'event': 'The evaluation strategy stopped early.', **policy_stop_log_extra(task_case, state, decision)})
        return True

    def _send_guidance(
        self,
        decision: RuntimeEvaluationDecision,
        state: RuntimeEvaluationState,
        harness: BaseBenchmarkHarness,
        session: object,
        task_case: TaskCase,
    ) -> bool:
        """Try to convert non-fatal stop into an open process diagnostic guide; return successfully to True."""
        stage_id = read_trigger_stage_id(decision)
        milestone_id = self._trigger_milestone_id(decision)
        if len(state.interventions) >= self._strategy.max_interventions:
            state.interventions.append(self._intervention_record(
                decision,
                state,
                stage_id,
                milestone_id,
                "limit_reached",
            ))
            return False
        if any(item.get("trigger_stage_id") == stage_id for item in state.interventions):
            state.interventions.append(self._intervention_record(
                decision,
                state,
                stage_id,
                milestone_id,
                "repeat_stage_suppressed",
            ))
            return False
        message: str | None = None
        try:
            message = build_intervention_message(task_case, decision, state)
            harness.send_guidance(session, message)
            outcome = "sent"
        except Exception as exc:
            logger.warning(
                "evaluator_guidance_failed",
                extra={
                    'event': 'Execution-time guidance failed; falling back to policy termination',
                    "case_id": task_case.case_id,
                    "trigger_stage_id": stage_id,
                    "error": str(exc),
                },
            )
            outcome = "send_failed" if message is not None else "build_failed"
        state.interventions.append(self._intervention_record(
            decision,
            state,
            stage_id,
            milestone_id,
            outcome,
            message,
        ))
        return outcome == "sent"

    def _enrich_interventions(self, state: RuntimeEvaluationState) -> None:
        """Supplements the status of the follow-on stage and the extra agent steps that have been sent."""
        for item in state.interventions:
            if item.get("outcome") != "sent":
                item.update({
                    "post_guidance_stage_id": None,
                    "post_guidance_stage_status": None,
                    "post_guidance_extra_agent_steps": None,
                })
                continue
            stage = self._post_guidance_stage(item, state)
            trigger_step = item.get("trigger_step_index")
            item.update({
                "post_guidance_stage_id": stage.stage_id if stage is not None else None,
                "post_guidance_stage_status": stage.status.value if stage is not None else None,
                "post_guidance_extra_agent_steps": (
                    state.agent_step_tracker.completed_count - int(trigger_step)
                    if isinstance(trigger_step, int) else None
                ),
            })

    def _post_guidance_stage(
        self,
        intervention: JsonObject,
        state: RuntimeEvaluationState,
    ) -> StageEvaluationResult | None:
        """To locate the first acceptable stage of milestone checkpoint after one lead."""
        trigger_stage_id = intervention.get("trigger_stage_id")
        trigger_milestone_id = intervention.get("trigger_milestone_id")
        trigger_step_index = intervention.get("trigger_step_index")
        if not isinstance(trigger_stage_id, str):
            return None
        candidates = [
            settlement for settlement in state.settlements
            if settlement.kind == "milestone"
            and settlement.end_step_index > trigger_step_index
            and (
                settlement.stage_id == trigger_stage_id
                or settlement.milestone_id == trigger_milestone_id
            )
        ] if isinstance(trigger_step_index, int) else []
        for settlement in sorted(candidates, key=lambda item: (item.end_step_index, item.settlement_id)):
            stage = next(
                (
                    stage for stage in state.stage_reports
                    if stage.stage_id == settlement.stage_id
                    and stage.metadata.get("synthetic_pending_milestone") is not True
                ),
                None,
            )
            if stage is not None:
                return stage
        return None

    def _trigger_milestone_id(self, decision: RuntimeEvaluationDecision) -> str | None:
        """Reads the milestone ID triggered by the guide; maintains a retroactive empty value when missing."""
        if decision.stage_result is not None and decision.stage_result.milestone_id:
            return decision.stage_result.milestone_id
        detail = decision.termination.termination_detail or {}
        if isinstance(detail, dict):
            for key in ("milestone_id", "most_promising_milestone_id"):
                value = detail.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    def _intervention_record(
        self,
        decision: RuntimeEvaluationDecision,
        state: RuntimeEvaluationState,
        trigger_stage_id: str,
        trigger_milestone_id: str | None,
        outcome: str,
        message: str | None = None,
    ) -> JsonObject:
        """Structured audit records for an intervention attempt."""
        return {
            "intervention_index": len(state.interventions),
            "trigger_step_index": self._trigger_step_index(decision),
            "trigger_stage_id": trigger_stage_id,
            "trigger_milestone_id": trigger_milestone_id,
            "termination_code": decision.termination.termination_code,
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message is not None else None,
            "message_preview": message[:160] if message is not None else None,
            "outcome": outcome,
        }

    def _append_online_native_metrics(
        self,
        runtime_metrics: JsonObject,
        native_default_result: object,
        native_evaluation_seconds: float,
    ) -> None:
        """Merge online native assessment statistics into rantime metrics."""
        runtime_metrics["native_evaluation_seconds"] = native_evaluation_seconds
        native_metrics = getattr(native_default_result, "metrics", {})
        native_metrics = native_metrics if isinstance(native_metrics, dict) else {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = native_metrics.get(key)
            runtime_metrics[f"native_evaluation_{key}"] = value if isinstance(value, int) else None

    def _is_fatal_minefield(self, decision: RuntimeEvaluationDecision) -> bool:
        """Determines whether the current stop is a fatal minefield hard stop."""
        code = decision.termination.termination_code
        return bool(code and code.startswith("minefield:"))

    def _trigger_step_index(self, decision: RuntimeEvaluationDecision) -> int | None:
        """Reads the current raw step index from structured decision-making."""
        detail = decision.termination.termination_detail or {}
        if isinstance(detail, dict):
            boundary = detail.get("boundary")
            if isinstance(boundary, dict) and isinstance(boundary.get("step_index"), int):
                return boundary["step_index"]
            value = detail.get("step_index")
            if isinstance(value, int):
                return value
        if decision.checkpoint is not None:
            return decision.checkpoint.end_step_index
        return None

    def _record_replay_decision(
        self,
        decision: RuntimeEvaluationDecision | None,
        state: RuntimeEvaluationState,
        step_index: int,
    ) -> None:
        """Record replay virtual early stop without triggering external session stop."""
        if decision is None or not decision.termination.should_stop:
            return
        if not self._strategy.policy_stop:
            if decision.stage_result is not None:
                decision.stage_result.metadata["policy_stop_suppressed"] = True
            return
        if state.evaluation_termination.should_stop:
            return
        termination = decision.termination
        termination.termination_reason = termination.termination_reason or DEFAULT_POLICY_STOP_REASON
        detail = dict(termination.termination_detail or {})
        detail["virtual_stop_step_index"] = step_index
        detail["replay_continue_after_virtual_stop"] = self._strategy.replay_continue_after_virtual_stop
        termination.termination_detail = detail
        state.evaluation_termination = termination

    def _new_pending_stage_reports(
        self, task_case: TaskCase, state: RuntimeEvaluationState
    ) -> list[StageEvaluationResult]:
        """Generates an unwritten version of the text."""
        existing_pending_ids = {
            stage.milestone_id
            for stage in state.stage_reports
            if stage.metadata.get("synthetic_pending_milestone") is True and stage.milestone_id is not None
        }
        return [
            stage for stage in pending_milestone_stage_results(task_case, state)
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

        milestone_ids = {node.milestone_id for node in graph.nodes}
        if not graph.nodes:
            coverage = _empty_graph_coverage(stage_reports)
        elif milestone_ids.issubset(matched_ids):
            coverage = "full"
        elif matched_ids:
            coverage = "partial"
        else:
            coverage = "none"
        return TrajectoryEvaluationReport(
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

    def _report_metadata(self, config: HarnessRunConfig, method_fallback: str) -> JsonObject:
        """Construct the experimental metadata for the assessment report."""
        metadata = config.metadata
        return {
            "benchmark": config.benchmark,
            "experiment_id": metadata.get("experiment_id"),
            "method": str(metadata.get("method") or method_fallback),
            "strategy": self._strategy.to_dict(),
            "judge_profile": metadata.get("judge_profile"),
            "threshold_profile": metadata.get("threshold_profile"),
            "model_id": metadata.get("model_id"),
            "repeat_index": metadata.get("repeat_index"),
        }

    def _teardown_session_safely(
        self, harness: BaseBenchmarkHarness, session: object | None, benchmark: str, case_id: str
    ) -> None:
        """Safely release benchmark session to avoid cleaning out an abnormally masked main process anomaly."""
        if session is None:
            return
        active_exception = sys.exc_info()[1] is not None
        try:
            harness.teardown_case(session)
        except Exception as exc:
            logger.exception(
                "harness_teardown_failed",
                extra={
                    'event': 'Benchmark resource teardown failed',
                    "benchmark": benchmark,
                    "case_id": case_id,
                    "error": str(exc),
                },
            )
            if not active_exception:
                raise HarnessTeardownError(
                    f"Benchmark session failed to release resource: benchmark={benchmark}, case_id={case_id}, error={exc}"
                ) from exc


def _append_replay_snapshots(
    trajectory: Trajectory,
    snapshots: list[object],
    start_index: int,
    step_index: int,
) -> int:
    """Appends the current step visible replay snapshot."""
    visible_snapshots = []
    index = start_index
    while index < len(snapshots):
        snapshot = snapshots[index]
        after_step_index = getattr(snapshot, "after_step_index", None)
        if not isinstance(after_step_index, int) or after_step_index > step_index:
            break
        visible_snapshots.append(snapshot)
        index += 1
    trajectory.extend_snapshots(visible_snapshots)
    return index


def build_replay_execution_summary(
    source_trajectory: Trajectory,
    replay_trajectory: Trajectory,
    termination: EvaluationTerminationState,
    coverage: str,
    coverage_basis: str,
) -> JsonObject:
    """Summarizes changes in replay virtual stop, completion and trajectory."""
    if source_trajectory is None or replay_trajectory is None or termination is None:
        raise ValueError('Source_trajectory, replay_trajectory and termination cannot be empty')
    return {
        "termination": termination.to_dict(),
        "final_completion": coverage,
        "coverage_basis": coverage_basis,
        "source_step_count": len(source_trajectory.steps),
        "replay_step_count": len(replay_trajectory.steps),
        "source_snapshot_count": len(source_trajectory.snapshots),
        "replay_snapshot_count": len(replay_trajectory.snapshots),
    }


def _metadata_from_config(config: HarnessRunConfig | Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Read metadata from HarnessRunConfig or map."""
    if config is None:
        return {}
    if isinstance(config, HarnessRunConfig):
        return config.metadata
    if isinstance(config, Mapping):
        return config
    raise ValueError('Config must be HarnessRunConfig or JSON objects')


def _empty_graph_coverage(stage_reports: list[StageEvaluationResult]) -> str:
    """Generate coverage from the empty milestone graph and stage state."""
    finish_stage = next(
        (stage for stage in reversed(stage_reports) if stage.milestone_id == FINISH_NODE_ID),
        None,
    )
    if finish_stage is None:
        return "none"
    evaluation = finish_stage.metadata.get("finish_stage_evaluation")
    if not isinstance(evaluation, dict):
        return "none"
    coverage_basis = evaluation.get("coverage_basis")
    if coverage_basis not in {"minefield_only", "whole_trajectory"}:
        return "none"
    if finish_stage.status == StageStatus.PASS:
        return "full"
    if coverage_basis == "minefield_only" and finish_stage.status == StageStatus.WARN:
        return "full"
    if coverage_basis == "whole_trajectory" and finish_stage.status in {StageStatus.WARN, StageStatus.AMBIGUOUS}:
        return "partial"
    return "none"


def _finish_coverage_basis(stage_reports: list[StageEvaluationResult]) -> str:
    """Read the coverage basis used by the finish stage."""
    finish_stage = next(
        (stage for stage in reversed(stage_reports) if stage.milestone_id == FINISH_NODE_ID),
        None,
    )
    if finish_stage is None:
        return "milestone_graph"
    evaluation = finish_stage.metadata.get("finish_stage_evaluation")
    if not isinstance(evaluation, dict):
        return "milestone_graph"
    coverage_basis = evaluation.get("coverage_basis")
    return str(coverage_basis) if coverage_basis in {"minefield_only", "whole_trajectory"} else "milestone_graph"
