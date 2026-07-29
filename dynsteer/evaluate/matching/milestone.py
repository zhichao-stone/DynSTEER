from dynsteer.utils import json_safe
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import blocked_candidate_milestones, ready_milestones
from dynsteer.evaluate.semantic import is_semantic_emit_message_constraint
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.scoring import GeneralScorer, get_effective_scorer
from dynsteer.model import Boundary, Constraint, ConstraintScore, JsonObject, Milestone, MilestoneScore, MilestoneFrontierState, MilestoneStepAnalysis, ScoringContext, StageStatus, Trajectory, TrajectoryStep
from dynsteer.graph import START_NODE_ID
SEMANTIC_REVIEW_MIN_SCORE = 0.3

def stage_start_for_ready_milestone(milestone: Milestone, matched: dict[str, HarnessStageSettlement], trajectory: Trajectory) -> tuple[str, int]:
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
        raise ValueError(f'milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}')
    if anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif anchor_id in matched:
        boundary_index = matched[anchor_id].end_step_index
    else:
        raise ValueError(f'stage anchor 尚未结算: milestone={milestone.milestone_id}, anchor={anchor_id}')
    return (anchor_id, boundary_index)

def analyze_milestone_step(trajectory: Trajectory, step: TrajectoryStep, boundary: Boundary, matched: dict[str, HarnessStageSettlement], frontier: MilestoneFrontierState, closure_steps: list[TrajectoryStep] | None=None, scorer: GeneralScorer | None=None, context: ScoringContext | None=None) -> MilestoneStepAnalysis:
    """分析单个新增 step 是否命中 ready milestone 或 blocked 诊断候选。

    入参：
        trajectory: 已追加当前 step 的运行期轨迹。
        step: 当前新增 step。
        boundary: 当前 step 对应的唯一候选边界。
        matched: 当前已匹配 milestone 结算表。
        frontier: 当前 case 的 ready frontier 增量状态。
        closure_steps: 当前已闭合 agent step 包含的完整 raw steps。
        scorer: 可选 milestone 评分器。
        context: 可选评分上下文。
    输出：
        milestone 命中、attempt 详情或 blocked 诊断详情。
    """
    if not frontier.milestone_by_id:
        return MilestoneStepAnalysis()
    matched_ids = set(matched)
    effective_scorer = get_effective_scorer(scorer)
    candidate_closure_steps = closure_steps or [step]
    ready = list(ready_milestones(frontier))
    ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit = _analyze_ready_candidates(ready=ready, boundary=boundary, closure_steps=candidate_closure_steps, trajectory=trajectory, matched=matched, matched_ids=matched_ids, scorer=effective_scorer, context=context)
    blocked_candidate_details, blocked_best = _analyze_blocked_candidates(frontier=frontier, boundary=boundary, closure_steps=candidate_closure_steps, trajectory=trajectory, matched_ids=matched_ids, scorer=effective_scorer, context=context, candidate_by_milestone=candidate_by_milestone)
    _mark_selected_candidate_details(ready_candidate_details, ready_hit)
    attempt_detail = {'step_index': step.index, 'step_id': step.step_id, 'matched_before': sorted(matched_ids), 'ready_before': [milestone.milestone_id for milestone in ready], 'candidate_scores': ready_candidate_details}
    if ready_hit is not None:
        return MilestoneStepAnalysis(hit=ready_hit, attempt_detail=attempt_detail)
    if ready_llm_review_hit is not None:
        _mark_selected_candidate_details(ready_candidate_details, ready_llm_review_hit)
        attempt_detail['llm_semantic_review'] = {'status': 'candidate'}
        return MilestoneStepAnalysis(hit=ready_llm_review_hit, attempt_detail=attempt_detail, requires_semantic_review=True)
    if blocked_best is None:
        return MilestoneStepAnalysis(attempt_detail=attempt_detail)
    milestone, blocked_boundary, score, missing_predecessors = blocked_best
    _mark_selected_candidate_details(blocked_candidate_details, (milestone, blocked_boundary, score))
    return MilestoneStepAnalysis(attempt_detail=attempt_detail, blocked_detail={'diagnostic_type': 'blocked_milestone_hit', 'step_index': step.index, 'step_id': step.step_id, 'matched_before': sorted(matched_ids), 'ready_before': [item.milestone_id for item in ready], 'missing_predecessors': list(missing_predecessors), 'predecessor_diagnostics': _build_predecessor_diagnostics(frontier, missing_predecessors, candidate_by_milestone), 'candidate_scores': blocked_candidate_details})

def _analyze_ready_candidates(ready: list[Milestone], boundary: Boundary, closure_steps: list[TrajectoryStep], trajectory: Trajectory, matched: dict[str, HarnessStageSettlement], matched_ids: set[str], scorer: GeneralScorer, context: ScoringContext | None) -> tuple[list[JsonObject], dict[str, JsonObject], tuple[Milestone, Boundary, MilestoneScore] | None, tuple[Milestone, Boundary, MilestoneScore] | None]:
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
            detail = {'milestone_id': milestone.milestone_id, 'boundary': json_safe(boundary), 'score': None, 'selected': False, 'reject_reason': 'boundary_not_after_predecessor'}
            ready_candidate_details.append(detail)
            candidate_by_milestone[milestone_id] = detail
            continue
        scoring_step = milestone_scoring_step(milestone, closure_steps, _default_step_from_closure(closure_steps, boundary))
        scoring_boundary = candidate_boundary_for_current_step(trajectory, scoring_step)
        score = scorer.score_milestone(milestone, scoring_boundary, trajectory, trajectory.snapshots, context=context)
        needs_llm_review = _is_llm_semantic_review_candidate(milestone, score)
        detail = {'milestone_id': milestone.milestone_id, 'boundary': json_safe(scoring_boundary), 'score': json_safe(score), 'selected': False, 'reject_reason': None if score.status == StageStatus.PASS else 'needs_llm_semantic_review' if needs_llm_review else 'status_not_pass'}
        ready_candidate_details.append(detail)
        candidate_by_milestone[milestone_id] = detail
        if score.status == StageStatus.PASS and (ready_hit is None or score.score > ready_hit[2].score):
            ready_hit = (milestone, scoring_boundary, score)
        elif needs_llm_review and (ready_llm_review_hit is None or score.score > ready_llm_review_hit[2].score):
            ready_llm_review_hit = (milestone, scoring_boundary, score)
    return (ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit)

def _analyze_blocked_candidates(frontier: MilestoneFrontierState, boundary: Boundary, closure_steps: list[TrajectoryStep], trajectory: Trajectory, matched_ids: set[str], scorer: GeneralScorer, context: ScoringContext | None, candidate_by_milestone: dict[str, JsonObject]) -> tuple[list[JsonObject], tuple[Milestone, Boundary, MilestoneScore, list[str]] | None]:
    blocked_candidate_details: list[JsonObject] = []
    blocked_best: tuple[Milestone, Boundary, MilestoneScore, list[str]] | None = None
    for milestone in blocked_candidate_milestones(frontier):
        if milestone.milestone_id in matched_ids:
            continue
        missing_predecessors = [predecessor_id for predecessor_id in milestone.dependency_predecessor_ids if predecessor_id not in matched_ids]
        if not missing_predecessors:
            continue
        scoring_step = milestone_scoring_step(milestone, closure_steps, _default_step_from_closure(closure_steps, boundary))
        scoring_boundary = candidate_boundary_for_current_step(trajectory, scoring_step)
        score = scorer.score_milestone(milestone, scoring_boundary, trajectory, trajectory.snapshots, context=context)
        detail = {'milestone_id': milestone.milestone_id, 'boundary': json_safe(scoring_boundary), 'score': json_safe(score), 'selected': False, 'reject_reason': None if score.status == StageStatus.PASS else 'status_not_pass'}
        blocked_candidate_details.append(detail)
        candidate_by_milestone[milestone.milestone_id] = detail
        if score.status == StageStatus.PASS and (blocked_best is None or score.score > blocked_best[2].score):
            blocked_best = (milestone, scoring_boundary, score, missing_predecessors)
    return (blocked_candidate_details, blocked_best)

def milestone_scoring_step(milestone: Milestone, closure_steps: list[TrajectoryStep], default_step: TrajectoryStep) -> TrajectoryStep:
    """基于预计算 route metadata 从闭包中选择 milestone 评分 step。

    入参：
        milestone: 当前待匹配 milestone。
        closure_steps: 当前已闭合 agent step 的完整 raw steps。
        default_step: 无 route 或找不到匹配 route 时使用的闭包终点 step。
    输出：
        用于构造评分 boundary 的 raw step。
    """
    if milestone is None or default_step is None:
        raise ValueError('milestone 和 default_step 不能为空')
    route_groups = _milestone_route_groups(milestone)
    if not route_groups:
        return default_step
    if len(route_groups) > 1:
        raise ValueError(f'当前实现只支持单个 milestone 至多一个显式 route；milestone={milestone.milestone_id}, route_group_count={len(route_groups)}')
    route = route_groups[0].get('route')
    if not isinstance(route, dict):
        return default_step
    sender = str(route.get('sender') or '').strip().upper()
    recipient = str(route.get('recipient') or '').strip().upper()
    if not sender or not recipient:
        return default_step
    for step in reversed(closure_steps):
        if step is not None and step.actor.name == sender and (step.recipient is not None) and (step.recipient.name == recipient):
            return step
    return default_step

def _default_step_from_closure(closure_steps: list[TrajectoryStep], boundary: Boundary) -> TrajectoryStep:
    """从 closure steps 中读取默认闭包终点 step。"""
    if closure_steps:
        return closure_steps[-1]
    raise ValueError(f'闭包 steps 不能为空: boundary={boundary.boundary_id}')

def _milestone_route_groups(milestone: Milestone) -> list[JsonObject]:
    """读取 milestone 级预计算 route groups。"""
    matching = milestone.metadata.get('milestone_matching')
    if not isinstance(matching, dict):
        return []
    count = matching.get('route_group_count')
    if isinstance(count, int) and count > 1:
        raise ValueError(f'当前实现只支持单个 milestone 至多一个显式 route；milestone={milestone.milestone_id}, route_group_count={count}')
    route_groups = matching.get('route_groups')
    if not isinstance(route_groups, list):
        return []
    groups = [dict(item) for item in route_groups if isinstance(item, dict)]
    if len(groups) > 1:
        raise ValueError(f'当前实现只支持单个 milestone 至多一个显式 route；milestone={milestone.milestone_id}, route_group_count={len(groups)}')
    return groups

def _build_predecessor_diagnostics(frontier: MilestoneFrontierState, missing_predecessors: list[str], candidate_by_milestone: dict[str, JsonObject]) -> list[JsonObject]:
    predecessor_diagnostics: list[JsonObject] = []
    for predecessor_id in missing_predecessors:
        predecessor = frontier.milestone_by_id.get(predecessor_id)
        if predecessor is None:
            predecessor_diagnostics.append({'milestone_id': predecessor_id, 'missing_node': True, 'best_candidate': None})
            continue
        best_predecessor = candidate_by_milestone.get(predecessor_id)
        predecessor_diagnostics.append({'milestone_id': predecessor_id, 'missing_node': False, 'best_candidate': best_predecessor})
    return predecessor_diagnostics

def _mark_selected_candidate_details(candidate_details: list[JsonObject], selected: tuple[Milestone, Boundary, MilestoneScore] | None) -> None:
    selected_milestone_id = selected[0].milestone_id if selected is not None else None
    selected_boundary_id = selected[1].boundary_id if selected is not None else None
    for candidate in candidate_details:
        boundary = candidate.get('boundary')
        is_selected = candidate.get('milestone_id') == selected_milestone_id and isinstance(boundary, dict) and (boundary.get('boundary_id') == selected_boundary_id)
        if is_selected:
            candidate['selected'] = True
            candidate['reject_reason'] = None
        elif candidate.get('reject_reason') is None:
            candidate['reject_reason'] = 'lower_score_than_selected'

def _is_llm_semantic_review_candidate(milestone: Milestone, score: MilestoneScore) -> bool:
    if score.status == StageStatus.PASS:
        return False
    if not any((is_semantic_emit_message_constraint(constraint) for constraint in milestone.constraints)):
        return False
    if score.missing_ratio > 0.0 or not score.hard_constraints_all_pass:
        if not _hard_failures_are_semantic_emit_messages(milestone, score):
            return False
    score_by_id = {item.constraint_id: item for item in score.constraint_scores}
    has_reviewable_semantic_message = False
    for constraint in milestone.constraints:
        if is_semantic_emit_message_constraint(constraint):
            constraint_score = score_by_id.get(constraint.constraint_id)
            if constraint_score is not None and _has_reviewable_semantic_message(constraint, constraint_score):
                has_reviewable_semantic_message = True
            continue
        if not constraint.hard:
            continue
        constraint_score = score_by_id.get(constraint.constraint_id)
        if constraint_score is None or constraint_score.missing or constraint_score.score < constraint.threshold:
            return False
    return has_reviewable_semantic_message

def _hard_failures_are_semantic_emit_messages(milestone: Milestone, score: MilestoneScore) -> bool:
    score_by_id = {item.constraint_id: item for item in score.constraint_scores}
    semantic_failure_found = False
    for constraint in milestone.constraints:
        if not constraint.hard:
            continue
        constraint_score = score_by_id.get(constraint.constraint_id)
        failed = constraint_score is None or constraint_score.missing or constraint_score.score < constraint.threshold
        if not failed:
            continue
        if not is_semantic_emit_message_constraint(constraint):
            return False
        semantic_failure_found = True
    return semantic_failure_found

def _has_reviewable_semantic_message(constraint: Constraint, score: ConstraintScore) -> bool:
    if score.missing:
        return False
    if _actual_contains_expected_message_route(score.actual, constraint.stage_goal_semantics):
        return True
    if isinstance(score.actual, list | dict):
        return False
    return score.score >= SEMANTIC_REVIEW_MIN_SCORE

def _actual_contains_expected_message_route(actual: object, semantics: object) -> bool:
    if not isinstance(semantics, dict):
        return False
    expected_sender = str(semantics.get('sender') or '').strip()
    expected_recipient = str(semantics.get('recipient') or '').strip()
    rows = actual if isinstance(actual, list) else [actual]
    for row in rows:
        if not isinstance(row, dict):
            continue
        sender = str(row.get('sender') or '').strip()
        recipient = str(row.get('recipient') or '').strip()
        content = str(row.get('content') or '').strip()
        if content and (not expected_sender or sender == expected_sender) and (not expected_recipient or recipient == expected_recipient):
            return True
    return False
