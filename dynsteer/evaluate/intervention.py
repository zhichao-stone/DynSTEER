from __future__ import annotations

import json

from dynsteer.model import (
    JsonObject,
    RuntimeEvaluationDecision,
    RuntimeEvaluationState,
    TaskCase,
)

INTERVENTION_TEXT_LIMIT = 1200


def build_intervention_message(
    task_case: TaskCase,
    decision: RuntimeEvaluationDecision,
    state: RuntimeEvaluationState,
) -> str:
    """Guides the message with a visible public diagnostic construction process. Args: task_case: currently suitable for case. Deposition: triggering the termination of the runtime decision. state: assessment of the current runtime. Returns: a plain text intervention hint starting with a fixed prefix."""
    if task_case is None or decision is None or state is None:
        raise ValueError('The task_case, decision and state cannot be empty.')
    termination = decision.termination
    detail = termination.termination_detail if isinstance(termination.termination_detail, dict) else {}
    stage_id = trigger_stage_id(decision)
    lines = [
        "[DynSTEER intervention]",
        'This is a process diagnosis reminder that does not change the original mission objective.',
        f"Mission description:{task_case.task_description}",
    ]
    stage_goal = task_case.stage_goals.get(stage_id)
    if stage_goal:
        lines.append(f"Objectives for the current stage:{stage_goal}")
    if decision.stage_result is not None:
        stage = decision.stage_result
        lines.append(f"stage status:{stage.status.value}, stage score:{stage.stage_score:.3f}")
    diagnosis = _whitelisted_diagnosis(detail)
    if diagnosis:
        lines.append(f"Structured diagnosis:{diagnosis}")
    ready = _ready_frontier(task_case, state)
    if ready:
        lines.append(f"Candidature front:{ready}")
    return "\n".join(lines)[:INTERVENTION_TEXT_LIMIT]


# # Internal Functions

def trigger_stage_id(decision: RuntimeEvaluationDecision) -> str:
    """Reads publicly releasable trigger stage identifiers; returns to white list termination details when missing."""
    if decision.stage_result is not None and decision.stage_result.stage_id.strip():
        return decision.stage_result.stage_id
    detail = decision.termination.termination_detail if isinstance(decision.termination.termination_detail, dict) else {}
    for key in ("milestone_id", "most_promising_milestone_id", "failure_basis"):
        value = detail.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"


def _whitelisted_diagnosis(detail: JsonObject) -> str:
    """Draws candidate scores, status, evidence and termination codes from the white list."""
    allowed = {"code", "failure_basis", "candidate_scores", "best_score", "best_status", "evidence"}
    payload = {str(key): detail[key] for key in allowed if key in detail}
    candidate = next(
        (
            item
            for item in reversed(payload.get("candidate_scores", []))
            if isinstance(item, dict) and item.get("selected") is True
        ),
        None,
    ) if isinstance(payload.get("candidate_scores"), list) else None
    if candidate is not None:
        score = candidate.get("score")
        if isinstance(score, dict):
            payload["selected_score"] = {
                key: score[key] for key in ("score", "status", "evidence") if key in score
            }
        payload.pop("candidate_scores")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _ready_frontier(task_case: TaskCase, state: RuntimeEvaluationState) -> str:
    """Generates the id/name white list summary for ready frontier."""
    names = {
        node.milestone_id: node.name
        for node in task_case.milestone_graph.nodes
    } if task_case.milestone_graph is not None else {}
    return ", ".join(
        f"{milestone_id}({names.get(milestone_id, 'unknown')})"
        for milestone_id in state.milestone_frontier.ready_ids[:6]
    )
