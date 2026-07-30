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
from dynsteer.evaluate.semantic import is_semantic_emit_message_constraint
from dynsteer.evaluate.weights import update_weights
from dynsteer.experiment.model import EvaluationStrategyConfig
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
    strategy: EvaluationStrategyConfig | None = None,
    force_standard_dimensions: list[Dimension] | None = None,
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
        force_standard_dimensions: 需要强制使用 standard judge 的维度。
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
        force_standard_dimensions=force_standard_dimensions,
    )
    semantic_review = stage_result.metadata.get("semantic_review")
    review_accepts_checkpoint = not (
        milestone_score.status != StageStatus.PASS
        and (stage_result.status != StageStatus.PASS or stage_result.stage_score < thresholds.pass_threshold)
    )
    if isinstance(semantic_review, dict):
        semantic_review["settlement_accepted"] = review_accepts_checkpoint
        semantic_review["final_stage_status"] = stage_result.status.value
        semantic_review["final_stage_score"] = stage_result.stage_score
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
    active_strategy = strategy or EvaluationStrategyConfig()
    stop_termination = (
        should_stop_after_stage(config, state, stage_result, thresholds)
        if active_strategy.policy_stop
        else EvaluationTerminationState()
    )
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
    replay_termination: EvaluationTerminationState | None = None,
    standard_judge: StandardJudge | None = None,
    thresholds: ThresholdConfig | None = None,
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
        standard_judge: 空 milestone graph 完整轨迹终态评估器。
        thresholds: 完整轨迹终态评估使用的阶段阈值。
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
    replay_metadata = replay_termination.to_dict() if replay_termination is not None else None
    if bool(verification.get("whole_trajectory_evaluation_required")):
        stage_result = _whole_trajectory_finish_stage_result(
            interval=interval,
            task_case=task_case,
            trajectory=trajectory,
            verification=verification,
            state=state,
            evaluation_policy=evaluation_policy,
            replay_metadata=replay_metadata,
            standard_judge=standard_judge,
            thresholds=thresholds or ThresholdConfig(),
        )
    else:
        stage_result = _deterministic_finish_stage_result(
            interval=interval,
            verification=verification,
            state=state,
            evaluation_policy=evaluation_policy,
            replay_metadata=replay_metadata,
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
            "finish_stage_evaluation": stage_result.metadata.get("finish_stage_evaluation", verification),
            "replay_virtual_stop": replay_metadata,
            "finish_after_virtual_stop": replay_metadata is not None,
        },
    )
    return settlement, stage_result, evaluation_policy


def _deterministic_finish_stage_result(
    interval: StageInterval,
    verification: JsonObject,
    state: RuntimeEvaluationState,
    evaluation_policy: EvaluationPolicyState,
    replay_metadata: JsonObject | None,
) -> StageEvaluationResult:
    """把确定性 final verification 转换为 finish 阶段报告。"""
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
    evidence = _verification_string_list(verification, "evidence", default=["finish 结算节点"])
    diagnosis = _verification_string_list(verification, "diagnosis")
    return StageEvaluationResult(
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
        hard_constraints_all_pass=status in {StageStatus.PASS, StageStatus.WARN},
        required_fields_missing_ratio=0.0 if bool(verification.get("all_milestones_matched")) else 1.0,
        minefield_score=state.max_minefield_score,
        fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
        metadata=_finish_stage_metadata(verification, evaluation_policy, replay_metadata),
    )


def _whole_trajectory_finish_stage_result(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    verification: JsonObject,
    state: RuntimeEvaluationState,
    evaluation_policy: EvaluationPolicyState,
    replay_metadata: JsonObject | None,
    standard_judge: StandardJudge | None,
    thresholds: ThresholdConfig,
) -> StageEvaluationResult:
    """为空 milestone graph 生成完整轨迹终态评估阶段报告。"""
    spec = resolve_stage_evaluation_spec(interval, task_case)
    focus_dimensions = list(dict.fromkeys(spec.focus_dimensions))
    precheck_evidence = _verification_string_list(verification, "evidence", default=["finish 结算节点"])
    precheck_diagnosis = _verification_string_list(verification, "diagnosis")

    if standard_judge is None:
        finish_evaluation = dict(verification)
        finish_evaluation.update(
            {
                "status": StageStatus.INVALID.value,
                "score": 0.0,
                "whole_trajectory_evaluation": False,
                "whole_trajectory_evaluator_unavailable": True,
                "judge_level": None,
                "focus_dimensions": [dimension.value for dimension in focus_dimensions],
            }
        )
        evidence = clean_evidence_items(
            [*precheck_evidence, "缺少 DynSTEER whole-trajectory evaluator，空 milestone graph 无法完成终态评估。"]
        )
        diagnosis = [
            *precheck_diagnosis,
            "finish whole-trajectory evaluation 无效：未配置 StandardJudge 或等价终态评估器。",
        ]
        finish_evaluation["evidence"] = evidence
        finish_evaluation["diagnosis"] = diagnosis
        return StageEvaluationResult(
            stage_id=interval.stage_id,
            milestone_id=interval.milestone_id,
            status=StageStatus.INVALID,
            stage_score=0.0,
            dimension_scores={Dimension.PROGRESS: 0.0},
            dimension_levels={Dimension.PROGRESS: EvaluationLevel.CHEAP},
            dimension_confidence={Dimension.PROGRESS: 0.95},
            dimension_uncertainty={Dimension.PROGRESS: 0.05},
            evidence=evidence,
            diagnosis=diagnosis,
            next_weights=dict(state.weights),
            fatal=False,
            hard_constraints_all_pass=False,
            required_fields_missing_ratio=1.0,
            minefield_score=state.max_minefield_score,
            fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
            metadata=_finish_stage_metadata(
                finish_evaluation,
                evaluation_policy,
                replay_metadata,
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
            "whole_trajectory_evaluation": True,
            "whole_trajectory_evaluation_required": True,
            "whole_trajectory_evaluator_unavailable": False,
            "coverage_basis": "whole_trajectory",
            "default_reference_used": False,
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
    return StageEvaluationResult(
        stage_id=interval.stage_id,
        milestone_id=interval.milestone_id,
        status=status,
        stage_score=score,
        dimension_scores=dict(judge_result.dimension_scores),
        dimension_levels=dict(judge_result.dimension_levels),
        dimension_confidence=dict(judge_result.dimension_confidence),
        dimension_uncertainty=dict(judge_result.dimension_uncertainty),
        evidence=evidence,
        diagnosis=diagnosis,
        next_weights=dict(state.weights),
        fatal=False,
        hard_constraints_all_pass=hard_pass,
        required_fields_missing_ratio=0.0 if hard_pass else 1.0,
        minefield_score=state.max_minefield_score,
        fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
        metadata=_finish_stage_metadata(
            finish_evaluation,
            evaluation_policy,
            replay_metadata,
            extra={
                "dimension_judge_results": [_judge_result_metadata(judge_result, focus_dimensions, state.weights)],
                "focus_dimensions": [dimension.value for dimension in focus_dimensions],
                "dimension_rationale": _dimension_rationale_json(spec.dimension_rationale, focus_dimensions),
                "structural_failure": not hard_pass,
            },
        ),
    )


def _finish_stage_metadata(
    finish_evaluation: JsonObject,
    evaluation_policy: EvaluationPolicyState,
    replay_metadata: JsonObject | None,
    extra: JsonObject | None = None,
) -> JsonObject:
    """构造 finish 阶段报告通用 metadata。"""
    metadata: JsonObject = {
        "finish_stage_evaluation": finish_evaluation,
        "next_evaluation_policy": evaluation_policy.to_dict(),
        "evaluation_termination": EvaluationTerminationState().to_dict(),
        "replay_virtual_stop": replay_metadata,
        "finish_after_virtual_stop": replay_metadata is not None,
    }
    if extra is not None:
        metadata.update(extra)
    return metadata


def _verification_string_list(
    verification: JsonObject, key: str, default: list[str] | None = None
) -> list[str]:
    """从 verification 中读取字符串列表并清洗 evidence。"""
    value = verification.get(key)
    result = [str(item) for item in value] if isinstance(value, list) else list(default or [])
    return clean_evidence_items(result) if key == "evidence" else result


def _whole_trajectory_status(
    judge_status: StageStatus,
    score: float,
    thresholds: ThresholdConfig,
) -> StageStatus:
    """结合 judge 原始状态和综合分生成完整轨迹 finish 状态。"""
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
    """序列化当前 finish 聚焦维度原因。"""
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
    force_standard_dimensions: list[Dimension] | None = None,
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
        force_standard_dimensions: 需要覆盖为 standard 粒度的维度。
    输出：
        阶段报告、新权重和策略更新。
    """
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
    requested_force_dimensions = list(dict.fromkeys(force_standard_dimensions or []))
    forced_dimensions = [dimension for dimension in requested_force_dimensions if dimension in focus_dimensions]
    for dimension in forced_dimensions:
        dimension_levels[dimension] = EvaluationLevel.STANDARD
    stage_result = cheap_judge.evaluate_stage(interval, task_case, trajectory, focus_dimensions)
    judge_results = [_judge_result_metadata(stage_result, focus_dimensions, weights)]
    semantic_review_metadata: JsonObject | None = None
    if requested_force_dimensions:
        semantic_review_metadata = {
            "status": "forced_standard" if forced_dimensions else "skipped_no_focus_dimension",
            "candidate_cheap_score": interval.milestone_score.score if interval.milestone_score is not None else None,
            "requested_dimensions": [dimension.value for dimension in requested_force_dimensions],
            "forced_dimensions": [dimension.value for dimension in forced_dimensions],
            "standard_judge_available": standard_judge is not None,
        }
        stage_result.metadata["semantic_review"] = semantic_review_metadata

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

    semantic_review_passed = bool(forced_dimensions) and all(
        as_number(stage_result.dimension_scores.get(dimension), 0.0) >= thresholds.pass_threshold
        for dimension in forced_dimensions
    )
    semantic_review_clears_structural_failure = semantic_review_passed and _semantic_only_hard_failure(
        interval, task_case
    )
    if semantic_review_clears_structural_failure:
        stage_result.hard_constraints_all_pass = True
        stage_result.required_fields_missing_ratio = 0.0
        stage_result.diagnosis = [
            item
            for item in stage_result.diagnosis
            if item not in {"阶段未达成预期 milestone", "阶段完成度偏低"}
        ]
        stage_result.evidence = clean_evidence_items(
            [*stage_result.evidence, "semantic review 确认消息语义等价，解除结构化文本相似度失败。"]
        )

    stage_result.stage_score = stage_score_from_dimensions(stage_result.dimension_scores, weights)
    if semantic_review_clears_structural_failure and stage_result.stage_score < thresholds.pass_threshold:
        stage_result.stage_score = thresholds.pass_threshold
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
    if low_score_dimensions:
        stage_result.metadata["low_score_dimensions"] = low_score_dimensions
    if semantic_review_metadata is not None:
        semantic_review_metadata["standard_judge_status"] = (
            "called" if forced_dimensions else "not_called_no_matching_focus_dimension"
        )
        semantic_review_metadata["semantic_review_passed"] = semantic_review_passed
        semantic_review_metadata["structural_failure_cleared"] = semantic_review_clears_structural_failure
        semantic_review_metadata["dimension_levels_after_review"] = {
            dimension.value: stage_result.dimension_levels.get(dimension, EvaluationLevel.CHEAP).value
            for dimension in forced_dimensions
        }

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
        next_policy, termination = update_evaluation_policy(
            evaluation_policy, stage_result, thresholds, allow_stop=active_strategy.policy_stop
        )
    else:
        next_policy = EvaluationPolicyState(
            base_level=active_strategy.fixed_judge_level,
            dimension_levels={dimension: active_strategy.fixed_judge_level for dimension in Dimension},
            reason="static_routing",
        )
        termination = EvaluationTerminationState()
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


def _semantic_only_hard_failure(interval: StageInterval, task_case: TaskCase) -> bool:
    score = interval.milestone_score
    if score is None or score.hard_constraints_all_pass:
        return False
    milestone = None
    if interval.milestone_id is not None and task_case.milestone_graph is not None:
        milestone = next(
            (node for node in task_case.milestone_graph.nodes if node.milestone_id == interval.milestone_id),
            None,
        )
    if milestone is None:
        return False
    score_by_id = {item.constraint_id: item for item in score.constraint_scores}
    semantic_failure_found = False
    for constraint in milestone.constraints:
        if not constraint.hard:
            continue
        constraint_score = score_by_id.get(constraint.constraint_id)
        failed = (
            constraint_score is None
            or constraint_score.missing
            or constraint_score.score < constraint.threshold
        )
        if not failed:
            continue
        if not is_semantic_emit_message_constraint(constraint):
            return False
        semantic_failure_found = True
    return semantic_failure_found


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
                termination_detail = (
                    {"stage_score": stage_result.stage_score, "stage_status": stage_result.status.value},
                )
            )
    return EvaluationTerminationState()
