from dynsteer.evaluate.diagnostics import (
    build_finish_matching_detail,
    build_milestone_matching_detail,
    build_stage_trace,
)
from dynsteer.evaluate.final import build_finish_verification
from dynsteer.evaluate.matching.frontier import advance_milestone_frontier, ready_milestones
from dynsteer.evaluate.matching.milestone import stage_start_for_ready_milestone
from dynsteer.evaluate.policy import update_evaluation_policy
from dynsteer.evaluate.runtime import JudgeConfigurationError
from dynsteer.evaluate.scoring import GeneralScorer, stage_score_from_dimensions
from dynsteer.evaluate.weights import update_weights
from dynsteer.graph import FINISH_NODE_ID, START_NODE_ID
from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.judges import CheapJudge, StandardJudge, ExpensiveJudge
from dynsteer.model import (
    Boundary,
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
)
from dynsteer.stage import resolve_stage_evaluation_spec, stage_goal_key, stage_start_step_index
from dynsteer.utils import as_number, clean_evidence_items


def evaluate_checkpoint(
    config: HarnessRunConfig,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    cheap_judge: CheapJudge,
    standard_judge: StandardJudge | None,
    expensive_judge: ExpensiveJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig,
) -> RuntimeEvaluationDecision:
    """结算单个 milestone checkpoint 并返回运行期决策。

    入参：
        config: 当前 run 配置。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        state: 当前运行期评估状态。
        milestone: 命中的 milestone。
        boundary: 命中边界。
        milestone_score: milestone 结构化评分。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        本 checkpoint 的结算结果和可能的策略终止决策。
    """
    if state.milestone_frontier is None:
        raise ValueError("RuntimeEvaluationState 缺少 milestone_frontier")

    anchor_id, boundary_index = stage_start_for_ready_milestone(milestone, state.matched_settlements, trajectory)
    start_step_index = stage_start_step_index(trajectory.successor_by_boundary, boundary_index, boundary.step_index)
    ready_milestone_ids_before_match = [item.milestone_id for item in ready_milestones(state.milestone_frontier)]
    interval = StageInterval(
        stage_id=stage_goal_key(anchor_id, milestone.milestone_id),
        milestone_id=milestone.milestone_id,
        stage_anchor_milestone_id=anchor_id,
        start_boundary_step_index=boundary_index,
        start_step_index=start_step_index,
        end_step_index=boundary.step_index,
        status=milestone_score.status,
        milestone_score=milestone_score,
        evidence=list(milestone_score.evidence),
    )
    matching_detail = build_milestone_matching_detail(
        matched=state.matched_settlements,
        milestone=milestone,
        boundary=boundary,
        milestone_score=milestone_score,
        ready_milestone_ids_before_match=ready_milestone_ids_before_match,
    )
    stage_result, next_weights, next_policy, termination = _evaluate_stage(
        interval, task_case, trajectory, state, cheap_judge, standard_judge, expensive_judge, thresholds, weight_config
    )
    settlement = HarnessStageSettlement(
        settlement_id=f"st{len(state.settlements)}",
        kind="milestone",
        milestone_id=interval.milestone_id,
        start_step_index=interval.start_step_index,
        end_step_index=interval.end_step_index,
        boundary_id=boundary.boundary_id,
        boundary_step_index=boundary.step_index,
        score=stage_result.stage_score,
        status=stage_result.status.value,
        checkpointed=True,
        evidence=list(stage_result.evidence),
        metadata={
            "stage_report": stage_result.to_dict(),
            "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
            "stage_start_boundary_step_index": interval.start_boundary_step_index,
            "stage_trace": build_stage_trace(trajectory=trajectory, interval=interval),
            "milestone_matching": matching_detail,
        },
    )
    if milestone_score.status != StageStatus.PASS and (
        stage_result.status != StageStatus.PASS or stage_result.stage_score < thresholds.pass_threshold
    ):
        return RuntimeEvaluationDecision(next_state=state, stage_result=stage_result)

    state.matched_settlements[milestone.milestone_id] = settlement
    state.ready_frontier_progress_watch = None
    advance_milestone_frontier(state.milestone_frontier, milestone.milestone_id, state.matched_settlements)
    state.settlements.append(settlement)
    state.stage_reports.append(stage_result)
    state.weights = next_weights
    state.evaluation_policy = next_policy

    if termination.should_stop:
        termination.termination_detail = {
            "stage_score": stage_result.stage_score,
            "stage_status": stage_result.status.value,
            "next_policy": next_policy.to_dict(),
        }
        stage_result.metadata["evaluation_termination"] = termination.to_dict()
        return RuntimeEvaluationDecision(state, settlement, stage_result, termination=termination)
    stop_termination = should_stop_after_stage(config, state, stage_result, thresholds)
    if not stop_termination.should_stop:
        return RuntimeEvaluationDecision(state, settlement, stage_result)
    stage_result.metadata["evaluation_termination"] = stop_termination.to_dict()
    return RuntimeEvaluationDecision(state, settlement, stage_result, termination=stop_termination)


def finish_settlement(
    settlements: list[HarnessStageSettlement],
    task_case: TaskCase,
    trajectory: Trajectory,
    scorer: GeneralScorer,
    state: RuntimeEvaluationState,
) -> tuple[
    HarnessStageSettlement,
    StageEvaluationResult,
    EvaluationPolicyState
]:
    """生成自然结束时的 finish 阶段结算。

    入参：
        settlements: 已有结算节点。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        scorer: 当前 benchmark 约束评分器。
        state: 当前运行期评估状态。
    输出：
        finish 结算、阶段报告、新权重和策略更新。
    """
    matched = state.matched_settlements
    evaluation_policy = state.evaluation_policy

    graph = task_case.milestone_graph
    last_step_index = trajectory.steps[-1].index if trajectory.steps else 0
    analysis = graph.metadata.get("graph_analysis", {}) if isinstance(graph.metadata, dict) else {}
    finish_anchor_id = (
        analysis.get("finish_stage_anchor_predecessor_id") if isinstance(analysis, dict) else START_NODE_ID
    )
    if not isinstance(finish_anchor_id, str) or not finish_anchor_id:
        finish_anchor_id = START_NODE_ID
    finish_node_id = (
        str(analysis.get("finish_node_id") or FINISH_NODE_ID) if isinstance(analysis, dict) else FINISH_NODE_ID
    )

    if finish_anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif finish_anchor_id in matched:
        boundary_index = matched[finish_anchor_id].end_step_index
    else:
        boundary_index = max(
            (settlement.end_step_index for settlement in matched.values()), default=trajectory.first_step_index - 1
        )
    end_step_index = max(boundary_index, last_step_index)
    start_step_index = stage_start_step_index(trajectory.successor_by_boundary, boundary_index, end_step_index)
    interval = StageInterval(
        stage_id=stage_goal_key(finish_anchor_id, finish_node_id),
        milestone_id=finish_node_id,
        stage_anchor_milestone_id=finish_anchor_id,
        start_boundary_step_index=boundary_index,
        start_step_index=start_step_index,
        end_step_index=end_step_index,
        status=StageStatus.PASS,
        evidence=["finish 结算节点"],
    )

    verification = build_finish_verification(task_case, trajectory, state, scorer)
    status = StageStatus(str(verification.get("status") or StageStatus.FAIL.value))
    score = as_number(verification.get("score"), 0.0)
    terminal_checks = verification.get("terminal_state_checks")
    has_terminal_checks = isinstance(terminal_checks, list) and len(terminal_checks) > 0
    dimension_scores = {Dimension.PROGRESS: score}
    if has_terminal_checks:
        dimension_scores[Dimension.STATE_CONSISTENCY] = min(
            (as_number(item.get("score"), score) for item in terminal_checks if isinstance(item, dict)), default=score
        )
    dimension_confidence = {dimension: 0.95 for dimension in dimension_scores}

    evidence = (
        clean_evidence_items([str(item) for item in verification.get("evidence", [])])
        if isinstance(verification.get("evidence"), list)
        else ["finish 结算节点"]
    )
    diagnosis = [str(item) for item in verification.get("diagnosis", [])] if isinstance(verification.get("diagnosis"), list) else []

    stage_result = StageEvaluationResult(
        stage_id=interval.stage_id,
        milestone_id=interval.milestone_id,
        status=status,
        stage_score=score,
        dimension_scores=dimension_scores,
        dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in dimension_scores},
        dimension_confidence=dimension_confidence,
        dimension_uncertainty={dimension: 1.0 - value for dimension, value in dimension_confidence.items()},
        evidence=evidence,
        diagnosis=diagnosis,
        next_weights=dict(state.weights),
        fatal=bool(verification.get("fatal_minefield")),
        hard_constraints_all_pass=status != StageStatus.FAIL,
        required_fields_missing_ratio=0.0 if bool(verification.get("all_milestones_matched")) else 1.0,
        minefield_score=state.max_minefield_score,
        fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
        metadata={
            "finish_stage_evaluation": verification,
            "next_evaluation_policy": evaluation_policy.to_dict(),
            "evaluation_termination": EvaluationTerminationState().to_dict(),
        },
    )

    settlement = HarnessStageSettlement(
        settlement_id=f"st{len(settlements)}",
        kind="finish",
        milestone_id=interval.milestone_id,
        start_step_index=interval.start_step_index,
        end_step_index=interval.end_step_index,
        score=stage_result.stage_score,
        status=stage_result.status.value,
        evidence=list(stage_result.evidence),
        metadata={
            "stage_report": stage_result.to_dict(),
            "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
            "stage_start_boundary_step_index": interval.start_boundary_step_index,
            "stage_trace": build_stage_trace(trajectory=trajectory, interval=interval),
            "milestone_matching": build_finish_matching_detail(graph=graph, matched=matched),
            "finish_stage_evaluation": verification,
        },
    )
    return settlement, stage_result, evaluation_policy


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
) -> tuple[StageEvaluationResult, dict[Dimension, float], EvaluationPolicyState, EvaluationTerminationState]:
    """按当前评估粒度策略评估一个阶段。

    入参：
        interval: 当前阶段区间。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        state: 当前运行期评估状态。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        阶段报告、新权重和策略更新。
    """
    weights = state.weights
    evaluation_policy = state.evaluation_policy
    spec = resolve_stage_evaluation_spec(interval, task_case)
    focus_dimensions = list(dict.fromkeys(spec.focus_dimensions))
    dimension_levels = {
        dimension: evaluation_policy.dimension_levels.get(dimension, evaluation_policy.base_level)
        for dimension in focus_dimensions
    }
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
            raise JudgeConfigurationError(f"{label} 评估需要配置真实 LLMJudge")
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
    next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.dimension_uncertainty, weight_config)
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
    stage_result.metadata["structural_failure"] = structural_failure
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
    next_policy, termination = update_evaluation_policy(evaluation_policy, stage_result, thresholds)
    stage_result.metadata["next_evaluation_policy"] = next_policy.to_dict()
    stage_result.metadata["evaluation_termination"] = termination.to_dict()
    return stage_result, next_weights, next_policy, termination


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
        "dimension_confidence_avg": _average_dimension_confidence(result, dimensions),
        "first_evidence": result.evidence[0] if result.evidence else None,
        "first_diagnosis": result.diagnosis[0] if result.diagnosis else None,
    }


def _average_dimension_confidence(result: StageEvaluationResult, dimensions: list[Dimension]) -> float | None:
    values = [result.dimension_confidence[dimension] for dimension in dimensions if dimension in result.dimension_confidence]
    if not values:
        return None
    return sum(values) / len(values)


def should_stop_after_stage(
    config: HarnessRunConfig,
    state: RuntimeEvaluationState,
    stage_result: StageEvaluationResult,
    thresholds: ThresholdConfig,
) -> EvaluationTerminationState:
    """根据阶段结果和运行期状态判断是否需要策略终止。

    入参：
        config: 当前 run 配置。
        state: 当前运行期评估状态。
        stage_result: 当前阶段评估结果。
        thresholds: 阶段阈值配置。
    输出：
        统一终止状态；不需要终止时 `should_stop=False`。
    """
    if config.stop_on_minefield:
        if state.minefield_matches and state.fatal_minefield:
            minefield_id = str(state.minefield_matches[0].get("minefield_id", "minefield"))
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"minefield:{minefield_id}",
                termination_reason=f"触发 fatal minefield，提前终止执行：{minefield_id}",
            )
        if state.minefield_matches and state.max_minefield_score >= thresholds.fatal_minefield_threshold:
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"minefield_score:{state.max_minefield_score:.3f}",
                termination_reason=f"minefield 分数 {state.max_minefield_score:.3f} 达到停止阈值，提前终止执行",
            )
    if config.stop_on_stage_failure:
        milestone_id = stage_result.milestone_id or "unknown"
        if stage_result.metadata.get("structural_failure") is True:
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"stage_failure:{milestone_id}",
                termination_reason=f"阶段存在结构性失败，提前终止执行：{milestone_id}",
                termination_detail={
                    "stage_score": stage_result.stage_score,
                    "stage_status": stage_result.status.value,
                    "structural_failure": True,
                },
            )
        if stage_result.stage_score < thresholds.fail_threshold:
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"stage_score:{milestone_id}",
                termination_reason=f"阶段评估分数 {stage_result.stage_score:.3f} 低于失败阈值，提前终止执行：{milestone_id}",
                termination_detail={"stage_score": stage_result.stage_score, "stage_status": stage_result.status.value},
            )
    return EvaluationTerminationState()
