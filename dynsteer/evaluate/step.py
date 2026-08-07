from collections.abc import Callable
from dynsteer.evaluate.matching.milestone import analyze_milestone_step
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
from dynsteer.evaluate.runtime import blocked_milestone_termination_reason, ready_frontier_no_progress_termination_reason, selected_candidate_from_attempt, state_scoring_context, update_ready_frontier_progress_watch
from dynsteer.evaluate.semantic import apply_semantic_message_reviews, semantic_message_review_targets, semantic_review_attempt_detail, skipped_semantic_review_detail
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import AgentStepClosure, JsonObject, EvaluationTerminationState, Milestone, MilestoneScore, RuntimeEvaluationDecision, RuntimeEvaluationState, StageStatus, TaskCase, ThresholdConfig, Trajectory, TrajectoryStep
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.evaluate.settlement import refresh_reference_anchors

def evaluate_step_minefields(task_case: TaskCase, trajectory: Trajectory, state: RuntimeEvaluationState, step: TrajectoryStep, scorer: GeneralScorer, policy_stop: bool) -> RuntimeEvaluationDecision | None:
    """对单条 step 执行 minefield 即时安全检查。

    入参：
        task_case: 当前 benchmark case。
        trajectory: 已追加当前 step 的完整轨迹。
        state: 当前运行期评估状态。
        step: 当前 step。
        scorer: 当前 harness 提供的约束评分器。
    输出：
        命中 fatal minefield 且启用提前终止时返回决策，否则返回 None。
    """
    refresh_reference_anchors(task_case, trajectory, state, step, scorer)
    context = state_scoring_context(task_case, trajectory, state)
    minefield_matches, minefield_score, fatal_minefield = evaluate_minefields_at_boundary(task_case.milestone_graph, trajectory, step, scorer, context, state.evaluated_minefield_sources)
    if not minefield_matches:
        return None
    seen = {(str(match.get("minefield_id")), str(match.get("source_identity"))) for match in state.minefield_matches if isinstance(match, dict)}
    for match in minefield_matches:
        key = (str(match.get("minefield_id")), str(match.get("source_identity")))
        if key not in seen:
            seen.add(key)
            state.minefield_matches.append(match)
    state.max_minefield_score = max(state.max_minefield_score, minefield_score)
    state.fatal_minefield = state.fatal_minefield or fatal_minefield
    if not fatal_minefield or not policy_stop:
        return None
    minefield_id = str(minefield_matches[0].get("minefield_id", "minefield"))
    termination_code = f"minefield:{minefield_id}"
    return RuntimeEvaluationDecision(
        termination=EvaluationTerminationState(
            termination_code=termination_code,
            termination_reason=f"触发 fatal minefield，提前终止执行：{minefield_id}",
            termination_detail={
                "code": termination_code,
                "minefield_matches": minefield_matches,
                "boundary": {"boundary_id": f"runtime:b{step.index}", "step_index": step.index},
            },
        ),
    )

def evaluate_agent_step(config: HarnessRunConfig, task_case: TaskCase, trajectory: Trajectory, state: RuntimeEvaluationState, closure: AgentStepClosure, scorer: GeneralScorer, standard_judge: object | None, thresholds: ThresholdConfig, policy_stop: bool, evaluate_checkpoint: Callable[..., RuntimeEvaluationDecision]) -> RuntimeEvaluationDecision | None:
    """处理一个已闭合 agent step，返回可能的策略终止决策。

    入参：
        config: 当前 run 配置。
        task_case: 当前 benchmark case。
        trajectory: 已追加闭包内 raw steps 的运行期轨迹。
        state: 当前运行期评估状态。
        closure: tracker 返回的闭合 agent step。
        scorer: 当前 harness 提供的约束评分器。
        standard_judge: standard judge；未配置时语义候选会跳过复判。
        thresholds: 阶段阈值配置。
        evaluate_checkpoint: milestone checkpoint 结算回调。
    输出：
        需要提前终止时返回决策，否则返回 None。
    """
    context = state_scoring_context(task_case, trajectory, state)
    analysis = analyze_milestone_step(trajectory, closure, state.matched_settlements, state.milestone_frontier, scorer, context=context)
    if analysis.hit is None:
        if analysis.attempt_detail is not None:
            state.match_attempts.append(analysis.attempt_detail)
            no_progress_decision = _ready_frontier_no_progress_decision(config, state, analysis.attempt_detail, thresholds)
            if no_progress_decision is not None:
                return no_progress_decision
        if analysis.blocked_detail is not None:
            state.match_attempts.append(analysis.blocked_detail)
            if policy_stop:
                selected_candidate = selected_candidate_from_attempt(analysis.blocked_detail)
                milestone_id = str(selected_candidate.get("milestone_id") if selected_candidate is not None else "unknown")
                termination_code = f"milestone_predecessor_gap:{milestone_id}"
                termination_detail = dict(analysis.blocked_detail)
                termination_detail["code"] = termination_code
                termination_detail.setdefault("failure_basis", "milestone_predecessor_gap")
                return RuntimeEvaluationDecision(termination=EvaluationTerminationState(termination_code=termination_code, termination_reason=blocked_milestone_termination_reason(analysis.blocked_detail), termination_detail=termination_detail))
        return None
    milestone, scoring_step, milestone_score = analysis.hit
    semantic_review_detail: JsonObject | None = None
    if analysis.requires_semantic_review:
        milestone_score, semantic_review_detail = _semantic_message_review_score(standard_judge=standard_judge, task_case=task_case, milestone=milestone, milestone_score=milestone_score, trajectory=trajectory, scoring_step=scoring_step)
        if analysis.attempt_detail is not None:
            analysis.attempt_detail["llm_semantic_review"] = semantic_review_detail
        if milestone_score.status != StageStatus.PASS:
            if analysis.attempt_detail is None:
                return None
            state.match_attempts.append(analysis.attempt_detail)
            return _ready_frontier_no_progress_decision(config, state, analysis.attempt_detail, thresholds)
    decision = evaluate_checkpoint(task_case=task_case, trajectory=trajectory, state=state, milestone=milestone, scoring_step=scoring_step, milestone_score=milestone_score)
    if semantic_review_detail is not None:
        semantic_review_detail["settlement_accepted"] = decision.checkpoint is not None
        if decision.stage_result is not None:
            semantic_review_detail["final_stage_status"] = decision.stage_result.status.value
            semantic_review_detail["final_stage_score"] = decision.stage_result.stage_score
            decision.stage_result.metadata["semantic_message_review"] = dict(semantic_review_detail)
    if analysis.attempt_detail is not None:
        state.match_attempts.append(analysis.attempt_detail)
    if decision.checkpoint is None and analysis.attempt_detail is not None:
        return _ready_frontier_no_progress_decision(config, state, analysis.attempt_detail, thresholds)
    if decision.termination.should_stop:
        return decision
    return None

def _semantic_message_review_score(standard_judge: object | None, task_case: TaskCase, milestone: Milestone, milestone_score: MilestoneScore, trajectory: Trajectory | None=None, scoring_step: TrajectoryStep | None=None) -> tuple[MilestoneScore, JsonObject]:
    """对需要复判的 emit_message 约束执行专用语义等价判断。"""
    targets = semantic_message_review_targets(milestone, milestone_score, trajectory=trajectory, scoring_step=scoring_step)
    if not targets:
        return (milestone_score, skipped_semantic_review_detail("skipped_no_reviewable_message", targets))
    if standard_judge is None:
        return (milestone_score, skipped_semantic_review_detail("skipped_no_standard_judge", targets))
    reviewer = getattr(standard_judge, "review_message_equivalence", None)
    if not callable(reviewer):
        return (milestone_score, skipped_semantic_review_detail("skipped_no_semantic_message_judge", targets))
    reviews = [reviewer(target, task_case=task_case) for target in targets]
    reviewed_score = apply_semantic_message_reviews(milestone, milestone_score, reviews)
    return (reviewed_score, semantic_review_attempt_detail(targets, reviews, reviewed_score))

def _ready_frontier_no_progress_decision(config: HarnessRunConfig, state: RuntimeEvaluationState, attempt_detail: JsonObject, thresholds: ThresholdConfig) -> RuntimeEvaluationDecision | None:
    ready_ids = tuple(state.milestone_frontier.ready_ids)
    if config.stop_on_ready_frontier_no_progress:
        termination_detail = update_ready_frontier_progress_watch(state=state, ready_ids=ready_ids, attempt_detail=attempt_detail, thresholds=thresholds, patience=config.ready_frontier_patience, min_delta=config.ready_frontier_min_delta)
    else:
        termination_detail = None
    if termination_detail is None:
        return None
    termination_code = str(termination_detail.get("code") or "ready_frontier_no_progress")
    termination_detail.setdefault("failure_basis", "ready_frontier_no_progress")
    return RuntimeEvaluationDecision(termination=EvaluationTerminationState(termination_code=termination_code, termination_reason=ready_frontier_no_progress_termination_reason(termination_detail), termination_detail=termination_detail))
