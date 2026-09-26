from collections.abc import Sequence

from dynsteer.utils import json_safe
from dynsteer.evaluate.matching.frontier import blocked_candidate_milestones, ready_milestones
from dynsteer.evaluate.semantic import hard_failure_is_semantic_message_only, is_semantic_emit_message_constraint
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.model import Actor, AgentStepClosure, Constraint, ConstraintScore, JsonObject, Milestone, MilestoneScore, MilestoneFrontierState, MilestoneStepAnalysis, MilestoneTopology, ScoringContext, StageStatus, Trajectory, TrajectoryStep
from dynsteer.graph import START_NODE_ID
SEMANTIC_REVIEW_MIN_SCORE = 0.3

def stage_start_for_ready_milestone(milestone: Milestone, topology: MilestoneTopology, matched: dict[str, HarnessStageSettlement], trajectory: Trajectory) -> tuple[str, int]:
    """Based on the starting point of the calculation stage of the holding milestone object. Args: milestone: currently ready or hit. Matched: currently matching milestone settlement. trajectory: current runtime trajectory. Returns: stage anchor id and anchor boundary step index."""
    anchor_id = topology.stage_anchor_by_id[milestone.milestone_id]
    if anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif anchor_id in matched:
        boundary_index = matched[anchor_id].end_step_index
    else:
        raise ValueError(f"stage anchor not settled: milestone={milestone.milestone_id}, anchor={anchor_id}")
    return (anchor_id, boundary_index)

def analyze_milestone_step(trajectory: Trajectory, closure: AgentStepClosure, matched: dict[str, HarnessStageSettlement], frontier: MilestoneFrontierState, scorer: GeneralScorer, context: ScoringContext) -> MilestoneStepAnalysis:
    """Analyses whether a single new step has been hit by a ready mine or blacked diagnostic candidate. Args: trajectory: The current track of the period has been added to the current step. close: currently closed agent step. Matched: currently matching the table. Frontier: Current case ready frontier incremental state. Scorer: milestone scoring. Context: optional rating context. Returns: milestone hits, attachment details or blocked diagnostic details."""
    if not frontier.topology.milestone_by_id:
        return MilestoneStepAnalysis()
    step = closure.end_step
    matched_ids = set(matched)
    ready = list(ready_milestones(frontier))
    steps = closure.steps
    route_steps = {(item.actor, item.recipient): item for item in closure.steps}
    ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit = _analyze_ready_candidates(ready=ready, topology=frontier.topology, scoring_step=step, steps=steps, route_steps=route_steps, trajectory=trajectory, matched=matched, matched_ids=matched_ids, scorer=scorer, context=context)
    blocked_candidate_details, blocked_best = _analyze_blocked_candidates(frontier=frontier, steps=steps, route_steps=route_steps, trajectory=trajectory, matched_ids=matched_ids, scorer=scorer, context=context, candidate_by_milestone=candidate_by_milestone)
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
        return MilestoneStepAnalysis(hit=ready_llm_review_hit, attempt_detail=attempt_detail, requires_semantic_review=True)
    if blocked_best is None:
        return MilestoneStepAnalysis(attempt_detail=attempt_detail)
    milestone, blocked_step, score, missing_predecessors = blocked_best
    _mark_selected_candidate_details(blocked_candidate_details, (milestone, blocked_step, score))
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
                frontier,
                missing_predecessors,
                candidate_by_milestone,
            ),
            "candidate_scores": blocked_candidate_details,
        },
    )

def _analyze_ready_candidates(ready: list[Milestone], topology: MilestoneTopology, scoring_step: TrajectoryStep, steps: Sequence[TrajectoryStep], route_steps: dict[tuple[Actor, Actor | None], TrajectoryStep], trajectory: Trajectory, matched: dict[str, HarnessStageSettlement], matched_ids: set[str], scorer: GeneralScorer, context: ScoringContext) -> tuple[list[JsonObject], dict[str, JsonObject], tuple[Milestone, TrajectoryStep, MilestoneScore] | None, tuple[Milestone, TrajectoryStep, MilestoneScore] | None]:
    ready_candidate_details: list[JsonObject] = []
    candidate_by_milestone: dict[str, JsonObject] = {}
    ready_hit: tuple[Milestone, TrajectoryStep, MilestoneScore] | None = None
    ready_llm_review_hit: tuple[Milestone, TrajectoryStep, MilestoneScore] | None = None
    for milestone in ready:
        milestone_id = milestone.milestone_id
        _, predecessor_start = stage_start_for_ready_milestone(milestone, topology, matched, trajectory)
        if scoring_step.index <= predecessor_start and predecessor_start > 0:
            detail = {
                "milestone_id": milestone.milestone_id,
                "boundary": _step_boundary(scoring_step),
                "score": None,
                "selected": False,
                "reject_reason": "boundary_not_after_predecessor",
            }
            ready_candidate_details.append(detail)
            candidate_by_milestone[milestone_id] = detail
            continue
        candidate_step, score, detail = _score_candidate(
            milestone, steps, route_steps, trajectory, scorer, context
        )
        needs_llm_review = _is_llm_semantic_review_candidate(milestone, score)
        detail["reject_reason"] = None if score.status == StageStatus.PASS else (
            "needs_llm_semantic_review" if needs_llm_review else "status_not_pass"
        )
        ready_candidate_details.append(detail)
        candidate_by_milestone[milestone_id] = detail
        if score.status == StageStatus.PASS and (ready_hit is None or score.score > ready_hit[2].score):
            ready_hit = (milestone, candidate_step, score)
        elif needs_llm_review and (ready_llm_review_hit is None or score.score > ready_llm_review_hit[2].score):
            ready_llm_review_hit = (milestone, candidate_step, score)
    return (ready_candidate_details, candidate_by_milestone, ready_hit, ready_llm_review_hit)

def _analyze_blocked_candidates(frontier: MilestoneFrontierState, steps: Sequence[TrajectoryStep], route_steps: dict[tuple[Actor, Actor | None], TrajectoryStep], trajectory: Trajectory, matched_ids: set[str], scorer: GeneralScorer, context: ScoringContext, candidate_by_milestone: dict[str, JsonObject]) -> tuple[list[JsonObject], tuple[Milestone, TrajectoryStep, MilestoneScore, list[str]] | None]:
    blocked_candidate_details: list[JsonObject] = []
    blocked_best: tuple[Milestone, TrajectoryStep, MilestoneScore, list[str]] | None = None
    for milestone in blocked_candidate_milestones(frontier):
        missing_predecessors = [predecessor_id for predecessor_id in frontier.topology.predecessors_by_id[milestone.milestone_id] if predecessor_id not in matched_ids]
        if not missing_predecessors:
            continue
        candidate_step, score, detail = _score_candidate(
            milestone, steps, route_steps, trajectory, scorer, context
        )
        detail["reject_reason"] = None if score.status == StageStatus.PASS else "status_not_pass"
        blocked_candidate_details.append(detail)
        candidate_by_milestone[milestone.milestone_id] = detail
        if score.status == StageStatus.PASS and (blocked_best is None or score.score > blocked_best[2].score):
            blocked_best = (milestone, candidate_step, score, missing_predecessors)
    return (blocked_candidate_details, blocked_best)

def _score_candidate(
    milestone: Milestone,
    steps: Sequence[TrajectoryStep],
    route_steps: dict[tuple[Actor, Actor | None], TrajectoryStep],
    trajectory: Trajectory,
    scorer: GeneralScorer,
    context: ScoringContext,
) -> tuple[TrajectoryStep, MilestoneScore, JsonObject]:
    scoring_step = milestone_scoring_step(milestone, steps, route_steps)
    score = scorer.score_milestone(milestone, scoring_step, trajectory, context)
    return (
        scoring_step,
        score,
        {
            "milestone_id": milestone.milestone_id,
            "boundary": _step_boundary(scoring_step),
            "score": json_safe(score),
            "selected": False,
            "reject_reason": None,
        },
    )

def milestone_scoring_step(milestone: Milestone, steps: Sequence[TrajectoryStep], route_steps: dict[tuple[Actor, Actor | None], TrajectoryStep]) -> TrajectoryStep:
    """Select the milestone rating step from the closed batch based on the expected root metadata. Args: milestone: currently to match milestone.steps: The complete raw step that currently closes the agent step. Returns: the raw step used to construct the binary rating."""
    default_step = steps[-1]
    route = milestone.matching_route
    return route_steps.get(route, default_step) if route is not None else default_step

def _build_predecessor_diagnostics(frontier: MilestoneFrontierState, missing_predecessors: list[str], candidate_by_milestone: dict[str, JsonObject]) -> list[JsonObject]:
    predecessor_diagnostics: list[JsonObject] = []
    for predecessor_id in missing_predecessors:
        frontier.topology.milestone_by_id[predecessor_id]
        best_predecessor = candidate_by_milestone.get(predecessor_id)
        predecessor_diagnostics.append(
            {"milestone_id": predecessor_id, "best_candidate": best_predecessor}
        )
    return predecessor_diagnostics

def _mark_selected_candidate_details(candidate_details: list[JsonObject], selected: tuple[Milestone, TrajectoryStep, MilestoneScore] | None) -> None:
    selected_milestone_id = selected[0].milestone_id if selected is not None else None
    selected_boundary_id = f"runtime:b{selected[1].index}" if selected is not None else None
    for candidate in candidate_details:
        boundary = candidate.get("boundary")
        is_selected = candidate.get("milestone_id") == selected_milestone_id and isinstance(boundary, dict) and (boundary.get("boundary_id") == selected_boundary_id)
        if is_selected:
            candidate["selected"] = True
            candidate["reject_reason"] = None
        elif candidate.get("reject_reason") is None:
            candidate["reject_reason"] = "lower_score_than_selected"

def _step_boundary(step: TrajectoryStep) -> JsonObject:
    return {"boundary_id": f"runtime:b{step.index}", "step_index": step.index, "step_id": step.step_id}

def _is_llm_semantic_review_candidate(milestone: Milestone, score: MilestoneScore) -> bool:
    if score.status == StageStatus.PASS:
        return False
    if not any((is_semantic_emit_message_constraint(constraint) for constraint in milestone.constraints)):
        return False
    if score.missing_ratio > 0.0 or not score.hard_constraints_all_pass:
        if not hard_failure_is_semantic_message_only(milestone, score.constraint_scores):
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
    expected_sender = str(semantics.get("sender") or "").strip()
    expected_recipient = str(semantics.get("recipient") or "").strip()
    rows = actual if isinstance(actual, list) else [actual]
    for row in rows:
        if not isinstance(row, dict):
            continue
        sender = str(row.get("sender") or "").strip()
        recipient = str(row.get("recipient") or "").strip()
        content = str(row.get("content") or "").strip()
        if content and (not expected_sender or sender == expected_sender) and (not expected_recipient or recipient == expected_recipient):
            return True
    return False
