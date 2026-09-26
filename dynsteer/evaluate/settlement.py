from dynsteer.evaluate.diagnostics import (
    build_finish_matching_detail,
    build_milestone_matching_detail,
    build_stage_trace,
)
from dynsteer.evaluate.final import build_finish_verification
from dynsteer.evaluate.matching.frontier import advance_milestone_frontier
from dynsteer.evaluate.matching.milestone import stage_start_for_ready_milestone
from dynsteer.evaluate.policy import update_evaluation_policy
from dynsteer.evaluate.runtime import JudgeConfigurationError, state_scoring_context
from dynsteer.evaluate.scoring import GeneralScorer, stage_score_from_dimensions
from dynsteer.evaluate.weights import update_weights
from dynsteer.experiment.model import EvaluationStrategyConfig
from dynsteer.graph import FINISH_NODE_ID, START_NODE_ID
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.judges import CheapJudge, StandardJudge, ExpensiveJudge
from dynsteer.model import (
    Dimension,
    DynamicWeightConfig,
    EvaluationLevel,
    EvaluationPolicyState,
    EvaluationTerminationState,
    JsonObject,
    Milestone,
    MilestoneScore,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    StageEvaluationResult,
    StageInterval,
    StageStatus,
    TaskCase,
    ThresholdConfig,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import resolve_stage_evaluation_spec, stage_goal_key
from dynsteer.utils import as_number, clean_evidence_items


def evaluate_checkpoint(
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    milestone: Milestone,
    scoring_step: TrajectoryStep,
    milestone_score: MilestoneScore,
    cheap_judge: CheapJudge,
    standard_judge: StandardJudge | None,
    expensive_judge: ExpensiveJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig,
    strategy: EvaluationStrategyConfig | None = None,
) -> RuntimeEvaluationDecision:
    """Clears the individual milestone checkpoint and returns the runtime decision. Args: task_case: current benchmark case. trajectory: current runtime trajectory. state: current runtime assessment status. milestone_score: milestone structural scoring. cheap_judge: cheap layer evaluation. standard_judge: standard layer evaluator. expensive_judge: expensive layer evaluator. thresholds: stage threshold configuration. Returns: The final result of this checkpoint and possible strategy to terminate the decision."""
    anchor_id, boundary_index = stage_start_for_ready_milestone(milestone, state.milestone_frontier.topology, state.matched_settlements, trajectory)
    start_step_index = trajectory.first_step_after(boundary_index, scoring_step.index)
    ready_milestone_ids_before_match = list(state.milestone_frontier.ready_ids)
    interval = StageInterval(
        stage_id=stage_goal_key(anchor_id, milestone.milestone_id),
        milestone_id=milestone.milestone_id,
        stage_anchor_milestone_id=anchor_id,
        start_boundary_step_index=boundary_index,
        start_step_index=start_step_index,
        end_step_index=scoring_step.index,
        status=milestone_score.status,
        milestone_score=milestone_score,
        evidence=list(milestone_score.evidence),
    )
    matching_detail = build_milestone_matching_detail(
        matched=state.matched_settlements,
        milestone=milestone,
        topology=state.milestone_frontier.topology,
        scoring_step=scoring_step,
        milestone_score=milestone_score,
        ready_milestone_ids_before_match=ready_milestone_ids_before_match,
    )
    stage_result, next_weights, next_policy = _evaluate_stage(
        interval,
        task_case,
        trajectory,
        state,
        cheap_judge,
        standard_judge,
        expensive_judge,
        thresholds,
        weight_config,
        strategy=strategy,
    )
    settlement = HarnessStageSettlement(
        settlement_id=f"st{len(state.settlements)}",
        stage_id=interval.stage_id,
        kind="milestone",
        milestone_id=interval.milestone_id,
        start_step_index=interval.start_step_index,
        end_step_index=interval.end_step_index,
        boundary_id=f"runtime:b{scoring_step.index}",
        boundary_step_index=scoring_step.index,
        score=stage_result.stage_score,
        status=stage_result.status.value,
        evidence=list(stage_result.evidence),
        metadata={
            "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
            "stage_start_boundary_step_index": interval.start_boundary_step_index,
            "stage_trace": build_stage_trace(trajectory=trajectory, interval=interval),
            "milestone_matching": matching_detail,
        },
    )
    if milestone_score.status != StageStatus.PASS and (
        stage_result.status != StageStatus.PASS or stage_result.stage_score < thresholds.pass_threshold
    ):
        return RuntimeEvaluationDecision(stage_result=stage_result)

    record_settlement(state, settlement)
    matched_snapshot = trajectory.snapshot_at_or_before(scoring_step.index)
    if matched_snapshot is not None:
        state.reference_anchor_snapshots[milestone.milestone_id] = matched_snapshot
    state.ready_frontier_progress_watch = None
    advance_milestone_frontier(state.milestone_frontier, milestone.milestone_id)
    state.stage_reports.append(stage_result)
    state.weights = next_weights
    state.evaluation_policy = next_policy

    active_strategy = strategy or EvaluationStrategyConfig()
    stop_termination = termination_after_stage(stage_result, thresholds) if active_strategy.policy_stop else EvaluationTerminationState()
    if not stop_termination.should_stop:
        return RuntimeEvaluationDecision(settlement, stage_result)
    stage_result.metadata["evaluation_termination"] = stop_termination.to_dict()
    return RuntimeEvaluationDecision(settlement, stage_result, termination=stop_termination)


def record_settlement(state: RuntimeEvaluationState, settlement: HarnessStageSettlement) -> None:
    """Record settings and update the runtime index."""
    state.scoring_context_cache = None
    state.settlements.append(settlement)
    if settlement.kind == "milestone" and settlement.milestone_id is not None:
        state.matched_settlements[settlement.milestone_id] = settlement


def refresh_reference_anchors(
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    scoring_step: TrajectoryStep,
    scorer: GeneralScorer,
) -> None:
    """Moves forward the ToolSandbox reference anchor that still meets the original constraint."""
    graph = task_case.milestone_graph
    snapshot = trajectory.snapshot_at_or_before(scoring_step.index)
    if graph is None or snapshot is None:
        return

    for milestone_id in sorted(state.referenced_milestone_ids & state.matched_settlements.keys()):
        current = state.reference_anchor_snapshots.get(milestone_id)
        if current is not None and current.after_step_index >= snapshot.after_step_index:
            continue
        milestone = state.milestone_frontier.topology.milestone_by_id.get(milestone_id)
        if milestone is None:
            continue
        context = state_scoring_context(task_case, trajectory, state)
        score = scorer.score_milestone(
            milestone,
            scoring_step,
            trajectory,
            context,
        )
        if score.status == StageStatus.PASS:
            state.reference_anchor_snapshots[milestone_id] = snapshot
            state.scoring_context_cache = None


def referenced_milestone_ids(task_case: TaskCase) -> frozenset[str]:
    """The milestone ID is expected to be active during the run-off period."""
    return frozenset(
        f"m{index}"
        for milestone in task_case.milestone_graph.nodes
        for constraint in milestone.constraints
        if isinstance((metadata := constraint.metadata.get("toolsandbox")), dict)
        and isinstance((index := metadata.get("reference_milestone_node_index")), int)
        and index >= 0
    )


def finish_settlement(
    task_case: TaskCase,
    trajectory: Trajectory,
    scorer: GeneralScorer,
    state: RuntimeEvaluationState,
    standard_judge: StandardJudge | None = None,
    thresholds: ThresholdConfig | None = None,
) -> tuple[HarnessStageSettlement, StageEvaluationResult]:
    """Generates the finish stage settlement at the end of nature. Args: task_case: current benchmark case. trajectory: current track of the runtime. Scorer: current benchmark binding scoring. state: current runtime assessment status. thresholds: stage threshold used for complete trajectories terminal assessments. Returns: finish settlement and stage report."""
    matched = state.matched_settlements

    graph = task_case.milestone_graph
    last_step_index = trajectory.steps[-1].index if trajectory.steps else 0
    finish_anchor_id = graph.finish_anchor_id
    finish_node_id = FINISH_NODE_ID

    if finish_anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif finish_anchor_id in matched:
        boundary_index = matched[finish_anchor_id].end_step_index
    else:
        boundary_index = max(
            (settlement.end_step_index for settlement in matched.values()), default=trajectory.first_step_index - 1
        )
    end_step_index = max(boundary_index, last_step_index)
    start_step_index = trajectory.first_step_after(boundary_index, end_step_index)
    interval = StageInterval(
        stage_id=stage_goal_key(finish_anchor_id, finish_node_id),
        milestone_id=finish_node_id,
        stage_anchor_milestone_id=finish_anchor_id,
        start_boundary_step_index=boundary_index,
        start_step_index=start_step_index,
        end_step_index=end_step_index,
        status=StageStatus.PASS,
        evidence=['finish Settlement Node'],
    )

    verification = build_finish_verification(task_case, trajectory, state, scorer, interval)
    if verification.get("coverage_basis") == "whole_trajectory" and not verification.get("fatal_minefield"):
        stage_result = _whole_trajectory_finish_stage_result(
            interval=interval,
            task_case=task_case,
            trajectory=trajectory,
            verification=verification,
            state=state,
            standard_judge=standard_judge,
            thresholds=thresholds or ThresholdConfig(),
        )
    else:
        stage_result = _deterministic_finish_stage_result(
            interval=interval,
            verification=verification,
            state=state,
        )

    settlement = HarnessStageSettlement(
        settlement_id=f"st{len(state.settlements)}",
        stage_id=interval.stage_id,
        kind="finish",
        milestone_id=interval.milestone_id,
        start_step_index=interval.start_step_index,
        end_step_index=interval.end_step_index,
        score=stage_result.stage_score,
        status=stage_result.status.value,
        evidence=list(stage_result.evidence),
        metadata={
            "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
            "stage_start_boundary_step_index": interval.start_boundary_step_index,
            "stage_trace": build_stage_trace(trajectory=trajectory, interval=interval),
            "milestone_matching": build_finish_matching_detail(graph=graph, matched=matched),
            "finish_stage_evaluation": stage_result.metadata.get("finish_stage_evaluation", verification),
        },
    )
    return settlement, stage_result


def _deterministic_finish_stage_result(
    interval: StageInterval,
    verification: JsonObject,
    state: RuntimeEvaluationState,
) -> StageEvaluationResult:
    """Convert certainty into final stage report."""
    status = StageStatus(str(verification.get("status") or StageStatus.FAIL.value))
    score = float(as_number(verification.get("score"), 0.0) or 0.0)
    terminal_checks = verification.get("terminal_state_checks")
    has_terminal_checks = isinstance(terminal_checks, list) and len(terminal_checks) > 0
    dimension_scores = {Dimension.PROGRESS: score}
    if has_terminal_checks:
        dimension_scores[Dimension.STATE_CONSISTENCY] = min(
            (
                float(as_number(item.get("score"), score) or 0.0)
                for item in terminal_checks
                if isinstance(item, dict)
            ),
            default=score,
        )
    dimension_confidence = {dimension: 0.95 for dimension in dimension_scores}
    evidence = _verification_string_list(verification, "evidence", default=['finish Settlement Node'])
    diagnosis = _verification_string_list(verification, "diagnosis")
    return _finish_result(
        interval=interval,
        state=state,
        status=status,
        score=score,
        dimension_scores=dimension_scores,
        dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in dimension_scores},
        dimension_confidence=dimension_confidence,
        evidence=evidence,
        diagnosis=diagnosis,
        fatal=bool(verification.get("fatal_minefield")),
        hard_pass=status in {StageStatus.PASS, StageStatus.WARN},
        required_fields_missing_ratio=1.0 if verification.get("unmatched_milestone_ids") else 0.0,
        metadata=_finish_stage_metadata(verification),
    )


def _whole_trajectory_finish_stage_result(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    verification: JsonObject,
    state: RuntimeEvaluationState,
    standard_judge: StandardJudge | None,
    thresholds: ThresholdConfig,
) -> StageEvaluationResult:
    """Generates a complete track terminal assessment stage report for empty milestone graph."""
    spec = resolve_stage_evaluation_spec(interval, task_case)
    focus_dimensions = list(dict.fromkeys(spec.focus_dimensions))
    precheck_evidence = _verification_string_list(verification, "evidence", default=['finish Settlement Node'])
    precheck_diagnosis = _verification_string_list(verification, "diagnosis")

    if standard_judge is None:
        finish_evaluation = dict(verification)
        finish_evaluation.update(
            {
                "status": StageStatus.INVALID.value,
                "score": 0.0,
                "evaluation_mode": "unavailable",
                "judge_level": None,
                "focus_dimensions": [dimension.value for dimension in focus_dimensions],
            }
        )
        evidence = clean_evidence_items(
            [*precheck_evidence, 'The final assessment cannot run without the DynSTEER whole-trajectory evaluator.']
        )
        diagnosis = [
            *precheck_diagnosis,
            'finish whole-trade evaluation invalid: either Standard Judge or Equivalent terminal evaluator not configured.',
        ]
        finish_evaluation["evidence"] = evidence
        finish_evaluation["diagnosis"] = diagnosis
        return _finish_result(
            interval=interval,
            state=state,
            status=StageStatus.INVALID,
            score=0.0,
            dimension_scores={Dimension.PROGRESS: 0.0},
            dimension_levels={Dimension.PROGRESS: EvaluationLevel.CHEAP},
            dimension_confidence={Dimension.PROGRESS: 0.95},
            evidence=evidence,
            diagnosis=diagnosis,
            fatal=False,
            hard_pass=False,
            required_fields_missing_ratio=1.0,
            metadata=_finish_stage_metadata(
                finish_evaluation,
                extra={
                    "focus_dimensions": [dimension.value for dimension in focus_dimensions],
                    "dimension_rationale": _dimension_rationale_json(spec.dimension_rationale, focus_dimensions),
                    "structural_failure": True,
                },
            ),
        )

    judge_result = standard_judge.evaluate_stage(interval, task_case, trajectory, focus_dimensions)
    score = stage_score_from_dimensions(judge_result.dimension_scores, state.weights)
    status = _whole_trajectory_status(judge_result.status, score, thresholds)
    evidence = clean_evidence_items([*precheck_evidence, *judge_result.evidence])
    diagnosis = [*precheck_diagnosis, *judge_result.diagnosis]
    hard_pass = status in {StageStatus.PASS, StageStatus.WARN}
    finish_evaluation = dict(verification)
    finish_evaluation.update(
        {
            "status": status.value,
            "score": score,
            "evaluation_mode": "standard_judge",
            "coverage_basis": "whole_trajectory",
            "judge_level": EvaluationLevel.STANDARD.value,
            "judge_status": judge_result.status.value,
            "judge_stage_score": score,
            "focus_dimensions": [dimension.value for dimension in focus_dimensions],
            "dimension_scores": {
                dimension.value: value for dimension, value in judge_result.dimension_scores.items()
            },
            "evidence": evidence,
            "diagnosis": diagnosis,
        }
    )
    return _finish_result(
        interval=interval,
        state=state,
        status=status,
        score=score,
        dimension_scores=dict(judge_result.dimension_scores),
        dimension_levels=dict(judge_result.dimension_levels),
        dimension_confidence=dict(judge_result.dimension_confidence),
        dimension_uncertainty=dict(judge_result.dimension_uncertainty),
        evidence=evidence,
        diagnosis=diagnosis,
        fatal=False,
        hard_pass=hard_pass,
        required_fields_missing_ratio=0.0 if hard_pass else 1.0,
        metadata=_finish_stage_metadata(
            finish_evaluation,
            extra={
                "dimension_judge_results": [_judge_result_metadata(judge_result, focus_dimensions, state.weights)],
                "focus_dimensions": [dimension.value for dimension in focus_dimensions],
                "dimension_rationale": _dimension_rationale_json(spec.dimension_rationale, focus_dimensions),
                "structural_failure": not hard_pass,
            },
        ),
    )


def _finish_result(
    *,
    interval: StageInterval,
    state: RuntimeEvaluationState,
    status: StageStatus,
    score: float,
    dimension_scores: dict[Dimension, float],
    dimension_levels: dict[Dimension, EvaluationLevel],
    dimension_confidence: dict[Dimension, float],
    evidence: list[str],
    diagnosis: list[str],
    fatal: bool,
    hard_pass: bool,
    required_fields_missing_ratio: float,
    metadata: JsonObject,
    dimension_uncertainty: dict[Dimension, float] | None = None,
) -> StageEvaluationResult:
    return StageEvaluationResult(
        stage_id=interval.stage_id,
        milestone_id=interval.milestone_id,
        status=status,
        stage_score=score,
        dimension_scores=dimension_scores,
        dimension_levels=dimension_levels,
        dimension_confidence=dimension_confidence,
        dimension_uncertainty=dimension_uncertainty or {
            dimension: 1.0 - value for dimension, value in dimension_confidence.items()
        },
        evidence=evidence,
        diagnosis=diagnosis,
        next_weights=dict(state.weights),
        fatal=fatal,
        hard_constraints_all_pass=hard_pass,
        required_fields_missing_ratio=required_fields_missing_ratio,
        minefield_score=state.max_minefield_score,
        fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
        metadata=metadata,
    )


def _finish_stage_metadata(
    finish_evaluation: JsonObject,
    extra: JsonObject | None = None,
) -> JsonObject:
    """Construct the finish stage report common metadata."""
    metadata: JsonObject = {"finish_stage_evaluation": finish_evaluation}
    if extra is not None:
        metadata.update(extra)
    return metadata


def _verification_string_list(
    verification: JsonObject, key: str, default: list[str] | None = None
) -> list[str]:
    """Reads the string list from the veritation and cleans the event."""
    value = verification.get(key)
    result = [str(item) for item in value] if isinstance(value, list) else list(default or [])
    return clean_evidence_items(result) if key == "evidence" else result


def _whole_trajectory_status(
    judge_status: StageStatus,
    score: float,
    thresholds: ThresholdConfig,
) -> StageStatus:
    """Generate a whole-trajectory finish state from judge output and aggregate scores."""
    if judge_status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
        return judge_status
    if score < thresholds.fail_threshold:
        return StageStatus.FAIL
    if score < thresholds.pass_threshold:
        return StageStatus.WARN
    if judge_status in {StageStatus.WARN, StageStatus.AMBIGUOUS}:
        return judge_status
    return StageStatus.PASS


def _dimension_rationale_json(
    rationale: dict[Dimension, str], dimensions: list[Dimension]
) -> JsonObject:
    """Serializes the current finish focus dimensions."""
    return {dimension.value: rationale.get(dimension, "") for dimension in dimensions}


def _evaluate_stage(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    cheap_judge: CheapJudge,
    standard_judge: StandardJudge | None,
    expensive_judge: ExpensiveJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig,
    strategy: EvaluationStrategyConfig | None = None,
) -> tuple[StageEvaluationResult, dict[Dimension, float], EvaluationPolicyState]:
    """Assessment of a stage according to the current strategy for assessing granularity. Args: interval: current stage.task_case: current benchmark case. trajectory: current runtime trajectory. state: current runtime assessment status. Expensive_judge: expensive layer evaluator. thresholds: stage threshold configuration. weight: dynamic weight configuration. Returns: stage reports, new weights, and strategy updates."""
    active_strategy = strategy or EvaluationStrategyConfig()
    weights = state.weights
    evaluation_policy = state.evaluation_policy
    spec = resolve_stage_evaluation_spec(interval, task_case)
    focus_dimensions = list(dict.fromkeys(spec.focus_dimensions))
    if active_strategy.dynamic_routing:
        dimension_levels = {
            dimension: evaluation_policy.dimension_levels.get(dimension, evaluation_policy.base_level)
            for dimension in focus_dimensions
        }
    else:
        dimension_levels = {dimension: active_strategy.fixed_judge_level for dimension in focus_dimensions}
    stage_result = cheap_judge.evaluate_stage(interval, task_case, trajectory, focus_dimensions)
    judge_results = [_judge_result_metadata(stage_result, focus_dimensions, weights)]

    for level, judge, label in (
        (EvaluationLevel.STANDARD, standard_judge, "standard"),
        (EvaluationLevel.EXPENSIVE, expensive_judge, "expensive"),
    ):
        dimensions = [dimension for dimension, current_level in dimension_levels.items() if current_level == level]
        if not dimensions:
            continue
        if judge is None:
            raise JudgeConfigurationError(f"{label} evaluation requires a configured LLMJudge")
        result = judge.evaluate_stage(interval, task_case, trajectory, dimensions)
        _merge_dimension_result(stage_result, result, dimensions)
        judge_results.append(_judge_result_metadata(result, dimensions, weights))

    stage_result.stage_score = stage_score_from_dimensions(stage_result.dimension_scores, weights)
    structural_failure = interval.status in {StageStatus.MISSING, StageStatus.INVALID} or (
        stage_result.hard_constraints_all_pass is False
    )
    if structural_failure:
        if stage_result.status not in {StageStatus.INVALID, StageStatus.MISSING, StageStatus.FAIL}:
            stage_result.status = StageStatus.FAIL
    elif stage_result.stage_score < thresholds.fail_threshold:
        stage_result.status = StageStatus.FAIL
    elif stage_result.stage_score < thresholds.pass_threshold:
        stage_result.status = StageStatus.WARN
    else:
        stage_result.status = StageStatus.PASS
    stage_result.minefield_score = state.max_minefield_score
    stage_result.fatal_minefield_score = state.max_minefield_score if state.fatal_minefield else 0.0
    if active_strategy.dynamic_weighting:
        next_weights = update_weights(
            weights, stage_result.dimension_scores, stage_result.dimension_uncertainty, weight_config
        )
    else:
        next_weights = dict(weights)
    stage_result.next_weights = next_weights
    low_score_dimensions = [
        {
            "dimension": dimension.value,
            "score": score,
            "severity": "fail" if score < thresholds.fail_threshold else "warn",
        }
        for dimension, score in sorted(stage_result.dimension_scores.items(), key=lambda item: item[0].value)
        if score < thresholds.warn_threshold
    ]
    stage_result.metadata["dimension_judge_results"] = judge_results
    stage_result.metadata["focus_dimensions"] = [dimension.value for dimension in focus_dimensions]
    stage_result.metadata["dimension_rationale"] = {
        dimension.value: reason
        for dimension, reason in spec.dimension_rationale.items()
        if dimension in focus_dimensions
    }
    stage_result.metadata["evaluation_strategy"] = active_strategy.to_dict()
    stage_result.metadata["structural_failure"] = structural_failure
    stage_result.metadata["failure_basis"] = (
        "structural_hard_constraint" if structural_failure else
        "fatal_minefield" if stage_result.fatal_minefield_score >= thresholds.fatal_minefield_threshold else
        "quality_score" if stage_result.stage_score < thresholds.fail_threshold else None
    )
    if low_score_dimensions:
        stage_result.metadata["low_score_dimensions"] = low_score_dimensions
    stage_result.metadata["weight_update_diagnostics"] = {
        "formula": "w_next = normalize(w * exp(alpha * (1 - score) + beta * uncertainty))",
        "alpha": weight_config.alpha,
        "beta": weight_config.beta,
        "dimensions": {
            dimension.value: {
                "current_weight": weights.get(dimension, 0.0),
                "score": stage_result.dimension_scores.get(dimension, 0.0),
                "dimension_uncertainty": stage_result.dimension_uncertainty.get(dimension, 0.0),
                "next_weight": next_weights.get(dimension, 0.0),
            }
            for dimension in Dimension
        },
    }
    if active_strategy.dynamic_routing:
        next_policy = update_evaluation_policy(evaluation_policy, stage_result, thresholds)
    else:
        next_policy = EvaluationPolicyState(
            base_level=active_strategy.fixed_judge_level,
            dimension_levels={dimension: active_strategy.fixed_judge_level for dimension in Dimension},
            reason="static_routing",
        )
    stage_result.metadata["next_evaluation_policy"] = next_policy.to_dict()
    return stage_result, next_weights, next_policy


def _merge_dimension_result(
    base: StageEvaluationResult, update: StageEvaluationResult, dimensions: list[Dimension]
) -> None:
    for attr in ("dimension_scores", "dimension_confidence", "dimension_uncertainty", "dimension_levels"):
        base_attr, update_attr = getattr(base, attr), getattr(update, attr)
        for dimension in dimensions:
            if dimension in update_attr:
                base_attr[dimension] = update_attr[dimension]
    base.evidence = clean_evidence_items([*base.evidence, *update.evidence])
    base.diagnosis.extend(item for item in update.diagnosis if item not in base.diagnosis)


def _judge_result_metadata(
    result: StageEvaluationResult, dimensions: list[Dimension], weights: dict[Dimension, float]
) -> JsonObject:
    dimension_scores = {dimension: result.dimension_scores.get(dimension) for dimension in dimensions}
    scored_dimensions = {
        dimension: score for dimension, score in dimension_scores.items() if isinstance(score, int | float)
    }
    confidence_values = [
        result.dimension_confidence[dimension]
        for dimension in dimensions
        if dimension in result.dimension_confidence
    ]
    return {
        "status": result.status.value,
        "dimension_stage_score": stage_score_from_dimensions(scored_dimensions, weights),
        "dimensions": [dimension.value for dimension in dimensions],
        "dimension_scores": {
            dimension.value: score for dimension, score in dimension_scores.items() if score is not None
        },
        "dimension_levels": {
            dimension.value: result.dimension_levels.get(dimension, EvaluationLevel.CHEAP).value
            for dimension in dimensions
        },
        "dimension_confidence": {
            dimension.value: result.dimension_confidence.get(dimension) for dimension in dimensions
        },
        "dimension_confidence_avg": sum(confidence_values) / len(confidence_values) if confidence_values else None,
        "first_evidence": result.evidence[0] if result.evidence else None,
        "first_diagnosis": result.diagnosis[0] if result.diagnosis else None,
    }


def termination_after_stage(
    stage_result: StageEvaluationResult,
    thresholds: ThresholdConfig,
) -> EvaluationTerminationState:
    """Decide whether evaluation should stop after a stage.

    Args:
        stage_result: Evaluation result for the current stage.
        thresholds: Stage threshold configuration.

    Returns:
        A termination state; ``should_stop`` is false when execution should continue.
    """
    milestone_id = stage_result.milestone_id or "unknown"
    detail = {"stage_score": stage_result.stage_score, "stage_status": stage_result.status.value}
    if stage_result.metadata.get("structural_failure") is True:
        return EvaluationTerminationState(termination_code=f"stage_failure:{milestone_id}", termination_reason=f"Structural failure occurred at stage {milestone_id}; stopping early.", termination_detail={**detail, "structural_failure": True})
    if stage_result.fatal_minefield_score >= thresholds.fatal_minefield_threshold:
        return EvaluationTerminationState(termination_code=f"minefield_score:{stage_result.fatal_minefield_score:.3f}", termination_reason=f"Minefield score {stage_result.fatal_minefield_score:.3f} reached the stop threshold; stopping early.", termination_detail=detail)
    if stage_result.stage_score < thresholds.fail_threshold:
        return EvaluationTerminationState(termination_code=f"stage_score:{milestone_id}", termination_reason=f"Stage score {stage_result.stage_score:.3f} fell below the failure threshold at {milestone_id}; stopping early.", termination_detail=detail)
    return EvaluationTerminationState()
