from __future__ import annotations

from collections.abc import Callable

from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import ready_milestone_ids
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
from dynsteer.evaluate.matching.milestone import analyze_milestone_step
from dynsteer.evaluate.runtime import (
    blocked_milestone_termination_reason,
    ready_frontier_no_progress_termination_reason,
    scoring_context,
    update_ready_frontier_progress_watch,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import (
    JsonObject,
    EvaluationTerminationState,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    StageStatus,
    TaskCase,
    ThresholdConfig,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.evaluate.scoring import GeneralScorer

CheckpointEvaluator = Callable[..., RuntimeEvaluationDecision]


def evaluate_runtime_step(
    config: HarnessRunConfig,
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
    step: TrajectoryStep,
    scorer: GeneralScorer,
    standard_judge: object | None,
    thresholds: ThresholdConfig,
    evaluate_checkpoint: CheckpointEvaluator,
) -> RuntimeEvaluationDecision | None:
    """处理单个新增 step，返回可能的策略终止决策。

    入参：
        config: 当前 run 配置。
        task_case: 当前 benchmark case。
        trajectory: 运行期轨迹，函数会追加当前 step。
        state: 当前运行期评估状态。
        step: harness 新增返回的轨迹 step。
        scorer: 当前 harness 提供的约束评分器。
        standard_judge: standard judge；未配置时 WARN 语义候选会跳过复判。
        thresholds: 阶段阈值配置。
        evaluate_checkpoint: milestone checkpoint 结算回调。
    输出：
        需要提前终止时返回决策，否则返回 None。
    """
    if (
        config is None
        or task_case is None
        or trajectory is None
        or state is None
        or step is None
        or scorer is None
        or thresholds is None
        or evaluate_checkpoint is None
    ):
        raise ValueError("单 step 评估参数不能为空")
    if state.milestone_frontier is None:
        raise ValueError("RuntimeEvaluationState 缺少 milestone_frontier")

    trajectory.append_step(step)
    context = scoring_context(task_case, trajectory, state.matched_settlements)
    boundary = candidate_boundary_for_current_step(trajectory, step)
    minefield_matches, minefield_score, fatal_minefield = evaluate_minefields_at_boundary(
        task_case.milestone_graph,
        trajectory,
        boundary,
        scorer,
        context,
    )
    if minefield_matches:
        _record_runtime_minefields(state, minefield_matches, minefield_score, fatal_minefield)
        if fatal_minefield and config.stop_on_minefield:
            minefield_id = str(minefield_matches[0].get("minefield_id", "minefield"))
            termination_code = f"minefield:{minefield_id}"
            return RuntimeEvaluationDecision(
                None,
                None,
                state,
                termination=EvaluationTerminationState(
                    should_stop=True,
                    termination_code=termination_code,
                    termination_reason=f"触发 fatal minefield，提前终止执行：{minefield_id}",
                    termination_detail={
                        "code": termination_code,
                        "minefield_matches": minefield_matches,
                        "boundary": {
                            "boundary_id": boundary.boundary_id,
                            "step_index": boundary.step_index,
                        },
                    },
                ),
            )

    analysis = analyze_milestone_step(
        task_case,
        trajectory,
        step,
        boundary,
        state.matched_settlements,
        state.milestone_frontier,
        scorer=scorer,
        context=context,
    )
    if analysis.hit is None:
        no_progress_decision = _record_attempt_and_check_no_progress(config, state, analysis.attempt_detail, thresholds)
        if no_progress_decision is not None:
            return no_progress_decision
        if analysis.blocked_detail is not None:
            state.match_attempts.append(analysis.blocked_detail)
            if config.stop_on_stage_failure:
                milestone_id = str(analysis.blocked_detail.get("milestone_id") or "unknown")
                termination_code = f"milestone_predecessor_gap:{milestone_id}"
                termination_detail = dict(analysis.blocked_detail)
                termination_detail["code"] = termination_code
                return RuntimeEvaluationDecision(
                    None,
                    None,
                    state,
                    termination=EvaluationTerminationState(
                        should_stop=True,
                        termination_code=termination_code,
                        termination_reason=blocked_milestone_termination_reason(analysis.blocked_detail),
                        termination_detail=termination_detail,
                    ),
                )
        return None

    milestone, boundary, milestone_score = analysis.hit
    requires_llm_review = milestone_score.status != StageStatus.PASS
    if requires_llm_review and standard_judge is None:
        if analysis.attempt_detail is not None:
            review_detail = analysis.attempt_detail.get("llm_semantic_review")
            if isinstance(review_detail, dict):
                review_detail["status"] = "skipped_no_standard_judge"
        return _record_attempt_and_check_no_progress(config, state, analysis.attempt_detail, thresholds)

    decision = evaluate_checkpoint(
        config=config,
        task_case=task_case,
        trajectory=trajectory,
        state=state,
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
    if decision.checkpoint is None and analysis.attempt_detail is not None:
        return _ready_frontier_no_progress_decision(config, state, analysis.attempt_detail, thresholds)
    if decision.termination.should_stop:
        return decision
    return None


def _record_runtime_minefields(
    state: RuntimeEvaluationState,
    matches: list[JsonObject],
    max_score: float,
    fatal: bool,
) -> None:
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


def _record_attempt_and_check_no_progress(
    config: HarnessRunConfig,
    state: RuntimeEvaluationState,
    attempt_detail: JsonObject | None,
    thresholds: ThresholdConfig,
) -> RuntimeEvaluationDecision | None:
    if config is None or state is None or thresholds is None:
        raise ValueError("attempt 记录参数不能为空")
    if attempt_detail is None:
        return None
    state.match_attempts.append(attempt_detail)
    return _ready_frontier_no_progress_decision(config, state, attempt_detail, thresholds)


def _ready_frontier_no_progress_decision(
    config: HarnessRunConfig,
    state: RuntimeEvaluationState,
    attempt_detail: JsonObject,
    thresholds: ThresholdConfig,
) -> RuntimeEvaluationDecision | None:
    if config is None or state is None or attempt_detail is None or state.milestone_frontier is None:
        raise ValueError("ready frontier 无进展决策参数不能为空")
    ready_ids = ready_milestone_ids(state.milestone_frontier, state.matched_settlements)
    termination_detail = update_ready_frontier_progress_watch(
        state=state,
        ready_ids=ready_ids,
        attempt_detail=attempt_detail,
        thresholds=thresholds,
        stop_enabled=config.stop_on_ready_frontier_no_progress,
        patience=config.ready_frontier_patience,
        min_delta=config.ready_frontier_min_delta,
    )
    if termination_detail is None:
        return None
    termination_code = str(termination_detail.get("code") or "ready_frontier_no_progress")
    return RuntimeEvaluationDecision(
        None,
        None,
        state,
        termination=EvaluationTerminationState(
            should_stop=True,
            termination_code=termination_code,
            termination_reason=ready_frontier_no_progress_termination_reason(termination_detail),
            termination_detail=termination_detail,
        ),
    )
