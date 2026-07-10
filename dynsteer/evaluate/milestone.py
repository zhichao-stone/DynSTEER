from __future__ import annotations

from dataclasses import dataclass

from dynsteer.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.diagnostics import (
    build_milestone_candidate_detail,
    boundary_to_dict,
    milestone_score_to_dict,
    milestone_summary_to_dict,
    trajectory_step_to_dict,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.scoring import GeneralScorer, ScoringContext, get_effective_scorer
from dynsteer.model import (
    Boundary,
    Constraint,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageGoalSemanticKind,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.graph import START_NODE_ID


@dataclass(frozen=True)
class MilestoneStepAnalysis:

    hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    attempt_detail: JsonObject | None = None
    blocked_detail: JsonObject | None = None


def ready_milestones(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> list[Milestone]:
    if graph is None or matched is None:
        raise ValueError("graph 和 matched 不能为空")
    matched_ids = set(matched)
    ready = []
    for node in graph.nodes:
        if node.milestone_id in matched_ids:
            continue
        if all(source in matched_ids for source in node.dependency_predecessor_ids):
            ready.append(node)
    return ready


def stage_start_for_milestone(
    graph: MilestoneGraph,
    milestone_id: str,
    matched: dict[str, HarnessStageSettlement],
    trajectory: Trajectory,
) -> tuple[str, int]:
    if graph is None or not milestone_id or matched is None or trajectory is None:
        raise ValueError("阶段起点参数不能为空")
    milestone = next((node for node in graph.nodes if node.milestone_id == milestone_id), None)
    if milestone is None:
        raise KeyError(f"milestone 不存在: {milestone_id}")
    anchor_id = milestone.stage_anchor_predecessor_id
    if not isinstance(anchor_id, str) or not anchor_id:
        raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone_id}")
    if anchor_id == START_NODE_ID:
        boundary_index = trajectory.first_step_index - 1
    elif anchor_id in matched:
        boundary_index = matched[anchor_id].end_step_index
    else:
        raise ValueError(f"stage anchor 尚未结算: milestone={milestone_id}, anchor={anchor_id}")
    return anchor_id, boundary_index


def analyze_milestone_step(
    task_case: TaskCase,
    trajectory: Trajectory,
    step: TrajectoryStep,
    matched: dict[str, HarnessStageSettlement],
    scorer: GeneralScorer | None = None,
    context: ScoringContext | None = None,
) -> MilestoneStepAnalysis:
    if task_case is None or trajectory is None or step is None or matched is None:
        raise ValueError("milestone step 分析参数不能为空")
    graph = task_case.milestone_graph
    if graph is None or not graph.nodes:
        return MilestoneStepAnalysis()

    boundary = candidate_boundary_for_current_step(trajectory, step)
    node_by_id = {node.milestone_id: node for node in graph.nodes}
    matched_ids = set(matched)
    effective_scorer = get_effective_scorer(scorer)

    ready: list[Milestone] = []
    ready_candidate_details: list[JsonObject] = []
    blocked_candidate_details: list[JsonObject] = []
    candidate_by_milestone: dict[str, JsonObject] = {}
    ready_hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    ready_llm_review_hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    blocked_best: tuple[Milestone, Boundary, MilestoneScore, list[str]] | None = None

    for milestone in graph.nodes:
        milestone_id = milestone.milestone_id
        if milestone_id in matched_ids:
            continue
        missing_predecessors = [
            predecessor_id
            for predecessor_id in milestone.dependency_predecessor_ids
            if predecessor_id not in matched_ids
        ]
        if not missing_predecessors:
            ready.append(milestone)
            _, predecessor_start = stage_start_for_milestone(graph, milestone_id, matched, trajectory)
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
        score = effective_scorer.score_milestone(
            milestone,
            boundary,
            trajectory,
            trajectory.snapshots,
            context=context,
        )
        if not missing_predecessors:
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
            if score.status == StageStatus.PASS:
                if ready_hit is None or score.score > ready_hit[2].score:
                    ready_hit = (milestone, boundary, score)
            elif needs_llm_review and (ready_llm_review_hit is None or score.score > ready_llm_review_hit[2].score):
                ready_llm_review_hit = (milestone, boundary, score)
            continue

        detail = build_milestone_candidate_detail(
            milestone=milestone,
            boundary=boundary,
            score=score,
            selected=False,
            reject_reason=None if score.status == StageStatus.PASS else "status_not_pass",
        )
        blocked_candidate_details.append(detail)
        candidate_by_milestone[milestone_id] = detail
        if score.status == StageStatus.PASS and (blocked_best is None or score.score > blocked_best[2].score):
            blocked_best = (milestone, boundary, score, missing_predecessors)

    _mark_selected_candidate_details(ready_candidate_details, ready_hit)
    attempt_detail = _build_step_attempt_detail(
        step=step,
        boundary=boundary,
        matched_ids=matched_ids,
        ready=ready,
        candidate_details=ready_candidate_details,
        selected=ready_hit,
    )
    if ready_hit is not None:
        return MilestoneStepAnalysis(hit=ready_hit, attempt_detail=attempt_detail)
    if ready_llm_review_hit is not None:
        _mark_selected_candidate_details(ready_candidate_details, ready_llm_review_hit)
        attempt_detail["selected_milestone_id"] = ready_llm_review_hit[0].milestone_id
        attempt_detail["llm_semantic_review"] = {
            "status": "candidate",
            "milestone_id": ready_llm_review_hit[0].milestone_id,
            "boundary_id": ready_llm_review_hit[1].boundary_id,
            "structural_score": ready_llm_review_hit[2].score,
            "structural_status": ready_llm_review_hit[2].status.value,
        }
        return MilestoneStepAnalysis(hit=ready_llm_review_hit, attempt_detail=attempt_detail)

    if blocked_best is None:
        return MilestoneStepAnalysis(attempt_detail=attempt_detail)

    milestone, boundary, score, missing_predecessors = blocked_best
    _mark_selected_candidate_details(blocked_candidate_details, (milestone, boundary, score))
    predecessor_diagnostics: list[JsonObject] = []
    for predecessor_id in missing_predecessors:
        predecessor = node_by_id.get(predecessor_id)
        if predecessor is None:
            predecessor_diagnostics.append(
                {
                    "milestone_id": predecessor_id,
                    "missing_node": True,
                    "candidate_scores": [],
                }
            )
            continue
        best_predecessor = candidate_by_milestone.get(predecessor_id)
        predecessor_diagnostics.append(
            {
                "milestone_id": predecessor_id,
                "missing_node": False,
                "candidate_scores": [best_predecessor] if best_predecessor is not None else [],
                "best_candidate": best_predecessor,
            }
        )

    blocked_detail: JsonObject = {
        "diagnostic_type": "blocked_milestone_hit",
        "step_index": step.index,
        "step_id": step.step_id,
        "current_step": trajectory_step_to_dict(step),
        "matched_before": sorted(matched_ids),
        "ready_before": [item.milestone_id for item in ready],
        "milestone_id": milestone.milestone_id,
        "matched_milestone": milestone_summary_to_dict(milestone),
        "boundary": boundary_to_dict(boundary),
        "score": milestone_score_to_dict(score),
        "missing_predecessors": list(missing_predecessors),
        "missing_predecessor_ids": list(missing_predecessors),
        "predecessor_diagnostics": predecessor_diagnostics,
        "candidate_scores": blocked_candidate_details,
    }
    return MilestoneStepAnalysis(attempt_detail=attempt_detail, blocked_detail=blocked_detail)


def _build_step_attempt_detail(
    step: TrajectoryStep,
    boundary: Boundary,
    matched_ids: set[str],
    ready: list[Milestone],
    candidate_details: list[JsonObject],
    selected: tuple[Milestone, Boundary, MilestoneScore] | None,
) -> JsonObject:
    return {
        "step_index": step.index,
        "step_id": step.step_id,
        "boundaries": [boundary_to_dict(boundary)],
        "boundary": boundary_to_dict(boundary),
        "matched_before": sorted(matched_ids),
        "ready_before": [milestone.milestone_id for milestone in ready],
        "candidate_scores": candidate_details,
        "selected_milestone_id": selected[0].milestone_id if selected is not None else None,
    }


def _mark_selected_candidate_details(
    candidate_details: list[JsonObject],
    selected: tuple[Milestone, Boundary, MilestoneScore] | None,
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
    if milestone is None or score is None:
        raise ValueError("milestone 和 score 不能为空")
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
    if constraint is None:
        raise ValueError("constraint 不能为空")
    semantics = constraint.stage_goal_semantics
    if not isinstance(semantics, dict):
        return False
    if semantics.get("kind") != StageGoalSemanticKind.EMIT_MESSAGE.value:
        return False
    return str(semantics.get("match_policy") or "semantic_equivalent") == "semantic_equivalent"
