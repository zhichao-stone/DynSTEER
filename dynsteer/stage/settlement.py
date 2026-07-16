from __future__ import annotations

from dynsteer.evaluate.diagnostics import (
    build_finish_matching_detail,
    build_milestone_matching_detail,
    build_stage_trace,
)
from dynsteer.evaluate.matching.frontier import advance_milestone_frontier, ready_milestones
from dynsteer.evaluate.matching.milestone import stage_start_for_ready_milestone
from dynsteer.evaluate.policy import update_evaluation_policy
from dynsteer.evaluate.runtime import JudgeConfigurationError
from dynsteer.evaluate.scoring import (
    enrich_stage_result,
    stage_score_from_dimensions,
    update_weights,
    weight_update_diagnostics,
)
from dynsteer.graph import FINISH_NODE_ID, START_NODE_ID
from dynsteer.harness.model import HarnessRunConfig, HarnessStageSettlement
from dynsteer.judges.base import BaseJudge
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
from dynsteer.stage import stage_goal_key, stage_start_step_index


def evaluate_checkpoint(
    config: HarnessRunConfig,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    cheap_judge: BaseJudge,
    standard_judge: BaseJudge | None,
    expensive_judge: BaseJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig | None,
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
    if config is None or state is None or milestone is None or boundary is None or milestone_score is None:
        raise ValueError("checkpoint 阶段评估参数不能为空")
    settlement, stage_result, next_weights, next_policy, termination = append_milestone_settlement(
        settlements=state.settlements,
        matched=state.matched_settlements,
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        milestone=milestone,
        boundary=boundary,
        milestone_score=milestone_score,
        weights=state.weights,
        evaluation_policy=state.evaluation_policy,
        cheap_judge=cheap_judge,
        standard_judge=standard_judge,
        expensive_judge=expensive_judge,
        thresholds=thresholds,
        weight_config=weight_config,
    )
    if milestone_score.status != StageStatus.PASS and (
        stage_result.status != StageStatus.PASS
        or stage_result.stage_score < thresholds.pass_threshold
    ):
        return RuntimeEvaluationDecision(None, stage_result, state)

    state.matched_settlements[milestone.milestone_id] = settlement
    state.ready_frontier_progress_watch = None
    if state.milestone_frontier is None:
        raise ValueError("RuntimeEvaluationState 缺少 milestone_frontier")
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
        return RuntimeEvaluationDecision(
            settlement,
            stage_result,
            state,
            termination=termination,
        )
    stop_termination = should_stop_after_stage(config, state, stage_result, thresholds)
    if not stop_termination.should_stop:
        return RuntimeEvaluationDecision(settlement, stage_result, state)
    stage_result.metadata["evaluation_termination"] = stop_termination.to_dict()
    return RuntimeEvaluationDecision(
        settlement,
        stage_result,
        state,
        termination=stop_termination,
    )


def finish_settlement(
    settlements: list[HarnessStageSettlement],
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
    weights: dict[Dimension, float],
    evaluation_policy: EvaluationPolicyState,
    state: RuntimeEvaluationState,
    cheap_judge: BaseJudge,
    standard_judge: BaseJudge | None,
    expensive_judge: BaseJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig | None,
) -> tuple[
    HarnessStageSettlement,
    StageEvaluationResult,
    dict[Dimension, float],
    EvaluationPolicyState,
    EvaluationTerminationState,
]:
    """生成自然结束时的 finish 阶段结算。

    入参：
        settlements: 已有结算节点。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        matched: 已匹配 milestone 结算表。
        weights: 当前维度权重。
        evaluation_policy: 当前评估粒度策略。
        state: 当前运行期评估状态。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        finish 结算、阶段报告、新权重和策略更新。
    """
    if settlements is None or task_case is None or trajectory is None or matched is None or weights is None or state is None:
        raise ValueError("finish 结算参数不能为空")
    graph = task_case.milestone_graph
    last_step_index = trajectory.steps[-1].index if trajectory.steps else 0
    analysis = graph.metadata.get("graph_analysis", {}) if isinstance(graph.metadata, dict) else {}
    finish_anchor_id = analysis.get("finish_stage_anchor_predecessor_id") if isinstance(analysis, dict) else START_NODE_ID
    if not isinstance(finish_anchor_id, str) or not finish_anchor_id:
        finish_anchor_id = START_NODE_ID
    finish_node_id = str(analysis.get("finish_node_id") or FINISH_NODE_ID) if isinstance(analysis, dict) else FINISH_NODE_ID
    if finish_anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif finish_anchor_id in matched:
        boundary_index = matched[finish_anchor_id].end_step_index
    else:
        boundary_index = max(
            (settlement.end_step_index for settlement in matched.values()),
            default=trajectory.first_step_index - 1,
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
    return append_stage_settlement(
        settlements=settlements,
        kind="finish",
        interval=interval,
        matching_detail=build_finish_matching_detail(graph=graph, matched=matched),
        task_case=task_case,
        trajectory=trajectory,
        state=state,
        weights=weights,
        evaluation_policy=evaluation_policy,
        cheap_judge=cheap_judge,
        standard_judge=standard_judge,
        expensive_judge=expensive_judge,
        thresholds=thresholds,
        weight_config=weight_config,
        metadata={
            "predecessor_milestone_ids": sorted(matched),
        },
    )


def append_milestone_settlement(
    settlements: list[HarnessStageSettlement],
    matched: dict[str, HarnessStageSettlement],
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    weights: dict[Dimension, float],
    evaluation_policy: EvaluationPolicyState,
    cheap_judge: BaseJudge,
    standard_judge: BaseJudge | None,
    expensive_judge: BaseJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig | None,
) -> tuple[
    HarnessStageSettlement,
    StageEvaluationResult,
    dict[Dimension, float],
    EvaluationPolicyState,
    EvaluationTerminationState,
]:
    """生成 milestone checkpoint 阶段结算。

    入参：
        settlements: 已有结算节点。
        matched: 已匹配 milestone 结算表。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        state: 当前运行期评估状态。
        milestone: 命中的 milestone。
        boundary: 命中边界。
        milestone_score: milestone 结构化评分。
        weights: 当前维度权重。
        evaluation_policy: 当前评估粒度策略。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        milestone 结算、阶段报告、新权重和策略更新。
    """
    graph = task_case.milestone_graph
    if state is None or state.milestone_frontier is None:
        raise ValueError("RuntimeEvaluationState 缺少 milestone_frontier")
    anchor_id, boundary_index = stage_start_for_ready_milestone(milestone, matched, trajectory)
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
    predecessor_milestone_ids = list(milestone.dependency_predecessor_ids)
    return append_stage_settlement(
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
        state=state,
        weights=weights,
        evaluation_policy=evaluation_policy,
        cheap_judge=cheap_judge,
        standard_judge=standard_judge,
        expensive_judge=expensive_judge,
        thresholds=thresholds,
        weight_config=weight_config,
        boundary_id=boundary.boundary_id,
        boundary_step_index=boundary.step_index,
        checkpointed=True,
        metadata={
            "predecessor_milestone_ids": predecessor_milestone_ids,
            "dependency_predecessor_milestone_ids": predecessor_milestone_ids,
        },
    )


def append_stage_settlement(
    settlements: list[HarnessStageSettlement],
    kind: str,
    interval: StageInterval,
    matching_detail: JsonObject,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    weights: dict[Dimension, float],
    evaluation_policy: EvaluationPolicyState,
    cheap_judge: BaseJudge,
    standard_judge: BaseJudge | None,
    expensive_judge: BaseJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig | None,
    boundary_id: str | None = None,
    boundary_step_index: int | None = None,
    checkpointed: bool = False,
    metadata: JsonObject | None = None,
) -> tuple[
    HarnessStageSettlement,
    StageEvaluationResult,
    dict[Dimension, float],
    EvaluationPolicyState,
    EvaluationTerminationState,
]:
    """生成通用阶段结算节点。

    入参：
        settlements: 已有结算节点。
        kind: 结算类型。
        interval: 当前阶段区间。
        matching_detail: milestone/finish 匹配诊断。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        state: 当前运行期评估状态。
        weights: 当前维度权重。
        evaluation_policy: 当前评估粒度策略。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        阶段结算、阶段报告、新权重和策略更新。
    """
    stage_result, next_weights, next_policy, termination = evaluate_stage(
        interval,
        task_case,
        trajectory,
        state,
        weights,
        evaluation_policy,
        cheap_judge,
        standard_judge,
        expensive_judge,
        thresholds,
        weight_config,
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
    return settlement, stage_result, next_weights, next_policy, termination


def evaluate_stage(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    weights: dict[Dimension, float],
    evaluation_policy: EvaluationPolicyState,
    cheap_judge: BaseJudge,
    standard_judge: BaseJudge | None,
    expensive_judge: BaseJudge | None,
    thresholds: ThresholdConfig,
    weight_config: DynamicWeightConfig | None,
) -> tuple[StageEvaluationResult, dict[Dimension, float], EvaluationPolicyState, EvaluationTerminationState]:
    """按当前评估粒度策略评估一个阶段。

    入参：
        interval: 当前阶段区间。
        task_case: 当前 benchmark case。
        trajectory: 当前运行期轨迹。
        state: 当前运行期评估状态。
        weights: 当前维度权重。
        evaluation_policy: 当前评估粒度策略。
        cheap_judge: cheap 层评估器。
        standard_judge: standard 层评估器。
        expensive_judge: expensive 层评估器。
        thresholds: 阶段阈值配置。
        weight_config: 动态权重配置。
    输出：
        阶段报告、新权重和策略更新。
    """
    if state is None or evaluation_policy is None:
        raise ValueError("evaluation_policy 不能为空")
    dimension_levels = {
        dimension: evaluation_policy.dimension_levels.get(dimension, evaluation_policy.base_level)
        for dimension in Dimension
    }
    stage_result = cheap_judge.evaluate_stage(interval, task_case, trajectory, weights, dimensions=list(Dimension))
    judge_results = [_judge_result_metadata(stage_result, list(Dimension))]

    standard_dimensions = [
        dimension
        for dimension, level in dimension_levels.items()
        if level == EvaluationLevel.STANDARD
    ]
    if standard_dimensions:
        if standard_judge is None:
            raise JudgeConfigurationError("standard 评估需要配置真实 LLMJudge")
        standard_result = standard_judge.evaluate_stage(
            interval,
            task_case,
            trajectory,
            weights,
            dimensions=standard_dimensions,
        )
        _merge_dimension_result(stage_result, standard_result, standard_dimensions)
        judge_results.append(_judge_result_metadata(standard_result, standard_dimensions))

    expensive_dimensions = [
        dimension
        for dimension, level in dimension_levels.items()
        if level == EvaluationLevel.EXPENSIVE
    ]
    if expensive_dimensions:
        if expensive_judge is None:
            raise JudgeConfigurationError("expensive 评估需要配置真实 LLMJudge")
        expensive_result = expensive_judge.evaluate_stage(
            interval,
            task_case,
            trajectory,
            weights,
            dimensions=expensive_dimensions,
        )
        _merge_dimension_result(stage_result, expensive_result, expensive_dimensions)
        judge_results.append(_judge_result_metadata(expensive_result, expensive_dimensions))

    stage_result.stage_score = stage_score_from_dimensions(stage_result.dimension_scores, weights)
    stage_result.status = _aggregate_stage_status(
        judge_results,
        stage_result.stage_score,
        thresholds,
        keep_baseline_failure=not stage_result.hard_constraints_all_pass,
    )
    stage_result = enrich_stage_result(
        interval,
        stage_result,
        state.max_minefield_score,
        state.fatal_minefield,
        thresholds,
    )
    next_weights = update_weights(weights, stage_result.dimension_scores, stage_result.dimension_uncertainty, weight_config)
    stage_result.next_weights = next_weights
    stage_result.metadata["dimension_judge_results"] = judge_results
    stage_result.metadata["weight_update_diagnostics"] = weight_update_diagnostics(
        weights,
        stage_result.dimension_scores,
        stage_result.dimension_uncertainty,
        next_weights,
        weight_config,
    )
    next_policy, termination = update_evaluation_policy(evaluation_policy, stage_result, thresholds)
    stage_result.metadata["next_evaluation_policy"] = next_policy.to_dict()
    stage_result.metadata["evaluation_termination"] = termination.to_dict()
    return stage_result, next_weights, next_policy, termination


def _merge_dimension_result(
    base: StageEvaluationResult,
    update: StageEvaluationResult,
    dimensions: list[Dimension],
) -> None:
    if base is None or update is None or dimensions is None:
        raise ValueError("维度结果合并参数不能为空")
    for dimension in dimensions:
        if dimension in update.dimension_scores:
            base.dimension_scores[dimension] = update.dimension_scores[dimension]
        if dimension in update.dimension_confidence:
            base.dimension_confidence[dimension] = update.dimension_confidence[dimension]
        if dimension in update.dimension_uncertainty:
            base.dimension_uncertainty[dimension] = update.dimension_uncertainty[dimension]
        if dimension in update.dimension_levels:
            base.dimension_levels[dimension] = update.dimension_levels[dimension]
    base.evidence.extend(item for item in update.evidence if item not in base.evidence)
    base.diagnosis.extend(item for item in update.diagnosis if item not in base.diagnosis)


def _aggregate_stage_status(
    judge_results: list[JsonObject],
    stage_score: float,
    thresholds: ThresholdConfig,
    keep_baseline_failure: bool = False,
) -> StageStatus:
    if judge_results is None or thresholds is None:
        raise ValueError("状态聚合参数不能为空")
    severity = {
        StageStatus.INVALID: 5,
        StageStatus.MISSING: 4,
        StageStatus.FAIL: 3,
        StageStatus.AMBIGUOUS: 2,
        StageStatus.WARN: 1,
        StageStatus.PASS: 0,
    }
    status_source = judge_results
    higher_cost = [
        item
        for item in judge_results
        if any(level != EvaluationLevel.CHEAP.value for level in _metadata_dimension_levels(item).values())
    ]
    if higher_cost and not keep_baseline_failure:
        status_source = higher_cost
    parsed: list[StageStatus] = []
    for item in status_source:
        try:
            parsed.append(StageStatus(str(item.get("status"))))
        except ValueError:
            parsed.append(StageStatus.INVALID)
    status = max(parsed, key=lambda item: severity[item]) if parsed else StageStatus.INVALID
    if status in {StageStatus.INVALID, StageStatus.MISSING, StageStatus.FAIL}:
        return status
    if stage_score < thresholds.fail_threshold:
        return StageStatus.FAIL
    if stage_score < thresholds.warn_threshold:
        return StageStatus.WARN
    return status


def _judge_result_metadata(result: StageEvaluationResult, dimensions: list[Dimension]) -> JsonObject:
    if result is None or dimensions is None:
        raise ValueError("judge result metadata 参数不能为空")
    return {
        "status": result.status.value,
        "stage_score": result.stage_score,
        "dimensions": [dimension.value for dimension in dimensions],
        "dimension_levels": {
            dimension.value: result.dimension_levels.get(dimension, EvaluationLevel.CHEAP).value
            for dimension in dimensions
        },
        "dimension_confidence": {
            dimension.value: result.dimension_confidence.get(dimension)
            for dimension in dimensions
        },
    }


def _metadata_dimension_levels(item: JsonObject) -> dict[str, str]:
    value = item.get("dimension_levels") if isinstance(item, dict) else None
    if not isinstance(value, dict):
        return {}
    return {str(key): str(level) for key, level in value.items()}


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
    if config is None or state is None or stage_result is None or thresholds is None:
        raise ValueError("终止策略参数不能为空")
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
        if stage_result.status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID}:
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"stage_failure:{milestone_id}",
                termination_reason=f"阶段评估状态为 {stage_result.status.value}，提前终止执行：{milestone_id}",
            )
        if stage_result.stage_score < thresholds.fail_threshold:
            return EvaluationTerminationState(
                should_stop=True,
                termination_code=f"stage_score:{milestone_id}",
                termination_reason=f"阶段评估分数 {stage_result.stage_score:.3f} 低于失败阈值，提前终止执行：{milestone_id}",
            )
    return EvaluationTerminationState()
