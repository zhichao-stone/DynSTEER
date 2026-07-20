from dynsteer.evaluate.diagnostics import build_milestone_candidate_detail
from dynsteer.evaluate.matching.frontier import blocked_candidate_milestones, ready_milestones
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.scoring import GeneralScorer, get_effective_scorer
from dynsteer.model import (
    Boundary,
    Constraint,
    JsonObject,
    Milestone,
    MilestoneScore,
    MilestoneFrontierState,
    MilestoneStepAnalysis,
    ScoringContext,
    StageGoalSemanticKind,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.graph import START_NODE_ID


def stage_start_for_ready_milestone(
    milestone: Milestone, matched: dict[str, HarnessStageSettlement], trajectory: Trajectory
) -> tuple[str, int]:
    """基于已持有的 milestone 对象计算阶段起点。

    入参：
        milestone: 当前 ready 或已命中 milestone。
        matched: 当前已匹配 milestone 结算表。
        trajectory: 当前运行期轨迹。
    输出：
        阶段 anchor id 与 anchor boundary step index。
    """
    anchor_id = milestone.stage_anchor_predecessor_id
    if not isinstance(anchor_id, str) or not anchor_id:
        raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
    if anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif anchor_id in matched:
        boundary_index = matched[anchor_id].end_step_index
    else:
        raise ValueError(f"stage anchor 尚未结算: milestone={milestone.milestone_id}, anchor={anchor_id}")
    return anchor_id, boundary_index


def analyze_milestone_step(
    trajectory: Trajectory,
    step: TrajectoryStep,
    boundary: Boundary,
    matched: dict[str, HarnessStageSettlement],
    frontier: MilestoneFrontierState,
    scorer: GeneralScorer | None = None,
    context: ScoringContext | None = None,
) -> MilestoneStepAnalysis:
    """分析单个新增 step 是否命中 ready milestone 或 blocked 诊断候选。

    入参：
        trajectory: 已追加当前 step 的运行期轨迹。
        step: 当前新增 step。
        boundary: 当前 step 对应的唯一候选边界。
        matched: 当前已匹配 milestone 结算表。
        frontier: 当前 case 的 ready frontier 增量状态。
        scorer: 可选 milestone 评分器。
        context: 可选评分上下文。
    输出：
        milestone 命中、attempt 详情或 blocked 诊断详情。
    """
    if not frontier.milestone_by_id:
        return MilestoneStepAnalysis()

    matched_ids = set(matched)
    effective_scorer = get_effective_scorer(scorer)
    ready = list(ready_milestones(frontier))
    (ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit) = _analyze_ready_candidates(
        ready=ready,
        boundary=boundary,
        trajectory=trajectory,
        matched=matched,
        matched_ids=matched_ids,
        scorer=effective_scorer,
        context=context,
    )
    blocked_candidate_details, blocked_best = _analyze_blocked_candidates(
        frontier=frontier,
        boundary=boundary,
        trajectory=trajectory,
        matched_ids=matched_ids,
        scorer=effective_scorer,
        context=context,
        candidate_by_milestone=candidate_by_milestone,
    )

    _mark_selected_candidate_details(ready_candidate_details, ready_hit)
    attempt_detail = {
        "step_index": step.index,
        "step_id": step.step_id,
        "matched_before": sorted(matched_ids),
        "ready_before": [milestone.milestone_id for milestone in ready],
        "candidate_scores": ready_candidate_details,
    }
    if ready_hit is not None:
        return MilestoneStepAnalysis(hit=ready_hit, attempt_detail=attempt_detail)
    if ready_llm_review_hit is not None:
        _mark_selected_candidate_details(ready_candidate_details, ready_llm_review_hit)
        attempt_detail["llm_semantic_review"] = {"status": "candidate"}
        return MilestoneStepAnalysis(hit=ready_llm_review_hit, attempt_detail=attempt_detail)

    if blocked_best is None:
        return MilestoneStepAnalysis(attempt_detail=attempt_detail)

    milestone, blocked_boundary, score, missing_predecessors = blocked_best
    _mark_selected_candidate_details(blocked_candidate_details, (milestone, blocked_boundary, score))
    return MilestoneStepAnalysis(
        attempt_detail=attempt_detail,
        blocked_detail={
            "diagnostic_type": "blocked_milestone_hit",
            "step_index": step.index,
            "step_id": step.step_id,
            "matched_before": sorted(matched_ids),
            "ready_before": [item.milestone_id for item in ready],
            "missing_predecessors": list(missing_predecessors),
            "predecessor_diagnostics": _build_predecessor_diagnostics(
                frontier, missing_predecessors, candidate_by_milestone
            ),
            "candidate_scores": blocked_candidate_details,
        },
    )


def _analyze_ready_candidates(
    ready: list[Milestone],
    boundary: Boundary,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
    matched_ids: set[str],
    scorer: GeneralScorer,
    context: ScoringContext | None,
) -> tuple[
    list[JsonObject],
    dict[str, JsonObject],
    tuple[Milestone, Boundary, MilestoneScore] | None,
    tuple[Milestone, Boundary, MilestoneScore] | None,
]:
    ready_candidate_details: list[JsonObject] = []
    candidate_by_milestone: dict[str, JsonObject] = {}
    ready_hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    ready_llm_review_hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    for milestone in ready:
        milestone_id = milestone.milestone_id
        if milestone_id in matched_ids:
            continue
        _, predecessor_start = stage_start_for_ready_milestone(milestone, matched, trajectory)
        if boundary.step_index <= predecessor_start and predecessor_start > 0:
            detail = build_milestone_candidate_detail(
                milestone=milestone,
                boundary=boundary,
                score=None,
                selected=False,
                reject_reason="boundary_not_after_predecessor",
            )
            ready_candidate_details.append(detail)
            candidate_by_milestone[milestone_id] = detail
            continue
        score = scorer.score_milestone(milestone, boundary, trajectory, trajectory.snapshots, context=context)
        needs_llm_review = _is_llm_semantic_review_candidate(milestone, score)
        detail = build_milestone_candidate_detail(
            milestone=milestone,
            boundary=boundary,
            score=score,
            selected=False,
            reject_reason=(
                None
                if score.status == StageStatus.PASS
                else "needs_llm_semantic_review"
                if needs_llm_review
                else "status_not_pass"
            ),
        )
        ready_candidate_details.append(detail)
        candidate_by_milestone[milestone_id] = detail
        if score.status == StageStatus.PASS and (ready_hit is None or score.score > ready_hit[2].score):
            ready_hit = (milestone, boundary, score)
        elif needs_llm_review and (ready_llm_review_hit is None or score.score > ready_llm_review_hit[2].score):
            ready_llm_review_hit = (milestone, boundary, score)
    return ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit


def _analyze_blocked_candidates(
    frontier: MilestoneFrontierState,
    boundary: Boundary,
    trajectory: Trajectory,
    matched_ids: set[str],
    scorer: GeneralScorer,
    context: ScoringContext | None,
    candidate_by_milestone: dict[str, JsonObject],
) -> tuple[list[JsonObject], tuple[Milestone, Boundary, MilestoneScore, list[str]] | None]:
    blocked_candidate_details: list[JsonObject] = []
    blocked_best: tuple[Milestone, Boundary, MilestoneScore, list[str]] | None = None
    for milestone in blocked_candidate_milestones(frontier):
        if milestone.milestone_id in matched_ids:
            continue
        missing_predecessors = [
            predecessor_id
            for predecessor_id in milestone.dependency_predecessor_ids
            if predecessor_id not in matched_ids
        ]
        if not missing_predecessors:
            continue
        score = scorer.score_milestone(milestone, boundary, trajectory, trajectory.snapshots, context=context)
        detail = build_milestone_candidate_detail(
            milestone=milestone,
            boundary=boundary,
            score=score,
            selected=False,
            reject_reason=None if score.status == StageStatus.PASS else "status_not_pass",
        )
        blocked_candidate_details.append(detail)
        candidate_by_milestone[milestone.milestone_id] = detail
        if score.status == StageStatus.PASS and (blocked_best is None or score.score > blocked_best[2].score):
            blocked_best = (milestone, boundary, score, missing_predecessors)
    return blocked_candidate_details, blocked_best


def _build_predecessor_diagnostics(
    frontier: MilestoneFrontierState, missing_predecessors: list[str], candidate_by_milestone: dict[str, JsonObject]
) -> list[JsonObject]:
    predecessor_diagnostics: list[JsonObject] = []
    for predecessor_id in missing_predecessors:
        predecessor = frontier.milestone_by_id.get(predecessor_id)
        if predecessor is None:
            predecessor_diagnostics.append({"milestone_id": predecessor_id, "missing_node": True, "best_candidate": None})
            continue
        best_predecessor = candidate_by_milestone.get(predecessor_id)
        predecessor_diagnostics.append({"milestone_id": predecessor_id, "missing_node": False, "best_candidate": best_predecessor})
    return predecessor_diagnostics


def _mark_selected_candidate_details(
    candidate_details: list[JsonObject], selected: tuple[Milestone, Boundary, MilestoneScore] | None
) -> None:
    selected_milestone_id = selected[0].milestone_id if selected is not None else None
    selected_boundary_id = selected[1].boundary_id if selected is not None else None
    for candidate in candidate_details:
        boundary = candidate.get("boundary")
        is_selected = (
            candidate.get("milestone_id") == selected_milestone_id
            and isinstance(boundary, dict)
            and boundary.get("boundary_id") == selected_boundary_id
        )
        if is_selected:
            candidate["selected"] = True
            candidate["reject_reason"] = None
        elif candidate.get("reject_reason") is None:
            candidate["reject_reason"] = "lower_score_than_selected"


def _is_llm_semantic_review_candidate(milestone: Milestone, score: MilestoneScore) -> bool:
    if score.status != StageStatus.WARN:
        return False
    if score.missing_ratio > 0.0 or not score.hard_constraints_all_pass:
        return False
    if not any(_is_semantic_emit_message_constraint(constraint) for constraint in milestone.constraints):
        return False

    score_by_id = {item.constraint_id: item for item in score.constraint_scores}
    for constraint in milestone.constraints:
        if _is_semantic_emit_message_constraint(constraint):
            continue
        if not constraint.hard:
            continue
        constraint_score = score_by_id.get(constraint.constraint_id)
        if constraint_score is None or constraint_score.missing or constraint_score.score < constraint.threshold:
            return False
    return True


def _is_semantic_emit_message_constraint(constraint: Constraint) -> bool:
    semantics = constraint.stage_goal_semantics
    if not isinstance(semantics, dict):
        return False
    if semantics.get("kind") != StageGoalSemanticKind.EMIT_MESSAGE.value:
        return False
    return str(semantics.get("match_policy") or "semantic_equivalent") == "semantic_equivalent"
