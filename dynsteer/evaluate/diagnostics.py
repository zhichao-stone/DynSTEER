from __future__ import annotations

from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    StageInterval,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import stage_trajectory_steps
from dynsteer.utils import json_safe


def trajectory_step_to_dict(step: TrajectoryStep) -> JsonObject:
    return json_safe(step)  # type: ignore[return-value]


def build_stage_trace(
    trajectory: Trajectory,
    interval: StageInterval,
) -> JsonObject:
    if trajectory is None or interval is None:
        raise ValueError("trajectory 和 interval 不能为空")
    steps = [
        trajectory_step_to_dict(step)
        for step in stage_trajectory_steps(interval, trajectory)
    ]
    return {
        "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
        "start_boundary_step_index": interval.start_boundary_step_index,
        "start_step_index": interval.start_step_index,
        "end_step_index": interval.end_step_index,
        "interval_semantics": "(start_boundary_step_index, end_step_index]",
        "step_count": len(steps),
        "steps": steps,
    }


def constraint_score_to_dict(score: ConstraintScore) -> JsonObject:
    return json_safe(score)  # type: ignore[return-value]


def milestone_score_to_dict(score: MilestoneScore) -> JsonObject:
    return json_safe(score)  # type: ignore[return-value]


def milestone_summary_to_dict(milestone: Milestone) -> JsonObject:
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "required": milestone.required,
        "pass_threshold": milestone.pass_threshold,
        "constraint_count": len(milestone.constraints),
        "metadata": dict(milestone.metadata),
    }


def constraint_summary_to_dict(constraint: Constraint) -> JsonObject:
    if constraint is None:
        raise ValueError("constraint 不能为空")
    expected = constraint.expected
    expected_summary: JsonObject = {"type": type(expected).__name__}
    if isinstance(expected, dict):
        rows = expected.get("rows")
        columns = expected.get("columns")
        expected_summary = {
            "type": "dict",
            "row_count": len(rows) if isinstance(rows, list) else None,
            "columns": list(columns) if isinstance(columns, list) else None,
        }
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "namespace": constraint.namespace,
        "selector": constraint.selector,
        "operator": constraint.operator.value,
        "weight": constraint.weight,
        "threshold": constraint.threshold,
        "hard": constraint.hard,
        "evaluator_hint": constraint.evaluator_hint,
        "reference_milestone_id": constraint.reference_milestone_id,
        "expected_summary": expected_summary,
        "metadata": dict(constraint.metadata),
    }


def build_milestone_graph_summary(graph: MilestoneGraph) -> JsonObject:
    if graph is None:
        raise ValueError("graph 不能为空")
    return {
        "total_milestone_count": len(graph.nodes),
        "required_milestone_ids": [node.milestone_id for node in graph.nodes if node.required],
        "optional_milestone_ids": [node.milestone_id for node in graph.nodes if not node.required],
        "edges": [[source, target] for source, target in graph.edges],
        "nodes": [
            {
                **milestone_summary_to_dict(node),
                "constraints": [constraint_summary_to_dict(constraint) for constraint in node.constraints],
            }
            for node in graph.nodes
        ],
    }


def boundary_to_dict(boundary: Boundary) -> JsonObject:
    return json_safe(boundary)  # type: ignore[return-value]


def build_milestone_candidate_detail(
    milestone: Milestone,
    boundary: Boundary | None,
    score: MilestoneScore | None,
    selected: bool,
    reject_reason: str | None,
) -> JsonObject:
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "boundary": boundary_to_dict(boundary) if boundary is not None else None,
        "score": milestone_score_to_dict(score) if score is not None else None,
        "selected": selected,
        "reject_reason": reject_reason,
    }


def build_milestone_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    milestone: Milestone,
    boundary: Boundary,
    milestone_score: MilestoneScore,
    ready_milestone_ids_before_match: list[str],
) -> JsonObject:
    if (
        graph is None
        or matched is None
        or milestone is None
        or boundary is None
        or milestone_score is None
        or ready_milestone_ids_before_match is None
    ):
        raise ValueError("milestone 匹配诊断参数不能为空")
    return {
        "mode": "runtime_checkpoint",
        "matched": True,
        "milestone": milestone_summary_to_dict(milestone),
        "boundary": boundary_to_dict(boundary),
        "score": milestone_score_to_dict(milestone_score),
        "ready_milestone_ids_before_match": list(ready_milestone_ids_before_match),
        "matched_milestone_ids_before_match": sorted(matched),
        "predecessor_milestone_ids": list(milestone.dependency_predecessor_ids),
    }


def build_final_milestone_diagnostics(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    match_attempts: list[JsonObject],
) -> list[JsonObject]:
    milestone_ids = {node.milestone_id for node in graph.nodes}
    attempts_by_milestone: dict[str, list[JsonObject]] = {milestone_id: [] for milestone_id in milestone_ids}
    ready_seen: set[str] = set()
    for attempt in match_attempts:
        ready_ids = attempt.get("ready_before")
        if isinstance(ready_ids, list):
            ready_seen.update(str(item) for item in ready_ids if str(item) in milestone_ids)
        raw_candidates = attempt.get("candidate_scores")
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if isinstance(candidate, dict) and str(candidate.get("milestone_id")) in milestone_ids:
                    attempts_by_milestone[str(candidate["milestone_id"])].append(candidate)

    diagnostics: list[JsonObject] = []
    for node in graph.nodes:
        candidate_entries = attempts_by_milestone[node.milestone_id]
        scored_entries = [entry for entry in candidate_entries if isinstance(entry.get("score"), dict)]
        best_entry = max(scored_entries, key=lambda item: float(item["score"].get("score", 0.0)), default=None)
        last_entry = candidate_entries[-1] if candidate_entries else None
        pending_predecessors = [item for item in node.dependency_predecessor_ids if item not in matched]
        best_score = best_entry.get("score") if isinstance(best_entry, dict) else None
        best_boundary = best_entry.get("boundary") if isinstance(best_entry, dict) else None
        common: JsonObject = {
            "milestone_id": node.milestone_id,
            "required": node.required,
            "dependency_predecessor_ids": list(node.dependency_predecessor_ids),
            "stage_anchor_milestone_id": node.stage_anchor_predecessor_id,
            "ready_ever": node.milestone_id in ready_seen,
            "attempt_count": len(candidate_entries),
            "best_score": best_score.get("score") if isinstance(best_score, dict) else None,
            "best_status": best_score.get("status") if isinstance(best_score, dict) else None,
            "best_boundary_step_index": (
                best_boundary.get("step_index") if isinstance(best_boundary, dict) else None
            ),
        }

        if node.milestone_id in matched:
            settlement = matched[node.milestone_id]
            diagnostics.append(
                {
                    **common,
                    "final_state": "matched",
                    "blocker": None,
                    "settlement_id": settlement.settlement_id,
                    "boundary_step_index": settlement.boundary_step_index,
                    "stage_start_boundary_step_index": settlement.metadata.get("stage_start_boundary_step_index"),
                    "stage_start_step_index": settlement.start_step_index,
                    "stage_end_step_index": settlement.end_step_index,
                    "best_score": common["best_score"] if common["best_score"] is not None else settlement.score,
                    "best_status": common["best_status"] if common["best_status"] is not None else settlement.status,
                    "best_boundary_step_index": common["best_boundary_step_index"]
                    if common["best_boundary_step_index"] is not None
                    else settlement.boundary_step_index,
                    "last_reject_reason": None,
                    "pending_predecessor_ids": [],
                }
            )
            continue

        if candidate_entries:
            blocker = "attempted_but_not_pass"
        elif pending_predecessors:
            blocker = "predecessor_not_matched"
        elif common["ready_ever"]:
            blocker = "ready_without_candidate"
        else:
            blocker = "not_ready"
        diagnostics.append(
            {
                **common,
                "final_state": "pending",
                "blocker": blocker,
                "settlement_id": None,
                "boundary_step_index": None,
                "stage_start_boundary_step_index": None,
                "stage_start_step_index": None,
                "stage_end_step_index": None,
                "last_reject_reason": last_entry.get("reject_reason") if isinstance(last_entry, dict) else None,
                "pending_predecessor_ids": pending_predecessors,
            }
        )
    return diagnostics


def build_finish_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> JsonObject:
    if graph is None or matched is None:
        raise ValueError("finish 匹配诊断参数不能为空")
    matched_ids = set(matched)
    required_ids = {node.milestone_id for node in graph.nodes if node.required}
    optional_ids = {node.milestone_id for node in graph.nodes if not node.required}
    return {
        "mode": "runtime_finish",
        "matched": False,
        "matched_milestone_ids": sorted(matched_ids),
        "pending_required_milestone_ids": sorted(required_ids - matched_ids),
        "pending_optional_milestone_ids": sorted(optional_ids - matched_ids),
        "total_milestone_count": len(graph.nodes),
    }
