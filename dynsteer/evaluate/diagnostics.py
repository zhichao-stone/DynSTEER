import re

from dynsteer.evaluate.semantic import (
    constraint_actual_excerpt,
    constraint_expected_excerpt,
    preserve_state_reference_label,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Constraint,
    EventType,
    EvaluationTerminationState,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneScore,
    MilestoneTopology,
    StageInterval,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.stage import stage_trajectory_steps
from dynsteer.utils import as_number, compact_json_text, compact_text, json_safe

STATE_MUTATION_TOOL_PREFIXES = ("set_", "modify_", "remove_", "add_", "create_", "delete_", "send_")
QUERY_TOOL_PREFIXES = ("search_", "get_", "find_", "list_")


def build_stage_trace(trajectory: Trajectory, interval: StageInterval) -> JsonObject:
    empty_stage_interval = interval.start_boundary_step_index == interval.end_step_index
    steps = [] if empty_stage_interval else [json_safe(step) for step in stage_trajectory_steps(interval, trajectory)]
    return {
        "stage_anchor_milestone_id": interval.stage_anchor_milestone_id,
        "start_boundary_step_index": interval.start_boundary_step_index,
        "start_step_index": interval.start_step_index,
        "end_step_index": interval.end_step_index,
        "interval_semantics": "(start_boundary_step_index, end_step_index]",
        "empty_stage_interval": empty_stage_interval,
        "empty_stage_interval_reason": "no_step_after_start_boundary" if empty_stage_interval else None,
        "step_count": len(steps),
        "steps": steps,
        "state_snapshot_delta_summary": _state_snapshot_delta_summary(trajectory, interval),
    }


def milestone_summary_to_dict(milestone: Milestone) -> JsonObject:
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "pass_threshold": milestone.pass_threshold,
        "constraint_count": len(milestone.constraints),
        "metadata": dict(milestone.metadata),
    }


def constraint_summary_to_dict(constraint: Constraint) -> JsonObject:
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
    stage_goal_semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    payload = {
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
    if stage_goal_semantics:
        payload["stage_goal_semantics"] = json_safe(stage_goal_semantics)
        if stage_goal_semantics.get("kind") == "tool_call":
            payload["tool_name"] = stage_goal_semantics.get("tool_name")
            payload["argument_match_policy"] = stage_goal_semantics.get("argument_match_policy", "exact")
            if stage_goal_semantics.get("argument_match_policy") == "exact":
                payload["arguments"] = json_safe(stage_goal_semantics.get("arguments", {}))
    return payload


def _format_number(value: object) -> str:
    number = as_number(value)
    return "unknown" if number is None else f"{number:.3f}"


def _state_snapshot_delta_summary(trajectory: Trajectory, interval: StageInterval) -> JsonObject:
    start_snapshot = trajectory.snapshot_at_or_before(interval.start_boundary_step_index)
    end_snapshot = trajectory.snapshot_at_or_before(interval.end_step_index)
    if start_snapshot is None and end_snapshot is None:
        return {"available": False, "namespaces": {}}
    start_namespaces = start_snapshot.namespaces if start_snapshot is not None else {}
    end_namespaces = end_snapshot.namespaces if end_snapshot is not None else {}
    namespace_names = sorted({*start_namespaces.keys(), *end_namespaces.keys()})
    summaries: JsonObject = {}
    for namespace in namespace_names:
        before = start_namespaces.get(namespace)
        after = end_namespaces.get(namespace)
        summaries[namespace] = {
            "before_row_count": len(before) if isinstance(before, list) else None,
            "after_row_count": len(after) if isinstance(after, list) else None,
            "changed": json_safe(before) != json_safe(after),
        }
    return {
        "available": True,
        "start_snapshot_id": start_snapshot.snapshot_id if start_snapshot is not None else None,
        "end_snapshot_id": end_snapshot.snapshot_id if end_snapshot is not None else None,
        "changed_namespaces": [namespace for namespace, item in summaries.items() if isinstance(item, dict) and item.get("changed")],
        "namespaces": summaries,
    }


def _constraint_goal_hint(constraint: Constraint | None) -> str:
    if constraint is None:
        return ""
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    kind = str(semantics.get("kind") or "")
    if kind == "emit_message":
        return "Need to emit the required user-visible message."
    if kind == "set_state":
        namespace = constraint.namespace or "state"
        return f"Need to bring {namespace} state to the target value."
    if kind == "preserve_state":
        namespace = constraint.namespace or "state"
        return f"Need to preserve {namespace} state relative to {preserve_state_reference_label(constraint)}."
    if kind == "tool_call":
        tool_name = str(semantics.get("tool_name") or "unknown")
        if semantics.get("argument_match_policy") == "reference_derived":
            return (
                f"Required tool call not matched: tool={tool_name}, "
                f"argument_match_policy=reference_derived, "
                f"reference_milestone_node_index={semantics.get('reference_milestone_node_index')}, "
                f"extractor={semantics.get('extractor')}"
            )
        arguments = compact_json_text(semantics.get("arguments", {}), 240)
        return f"Required tool call not matched: tool={tool_name}, expected_arguments={arguments}"

    metadata = constraint.metadata.get("toolsandbox")
    if isinstance(metadata, dict):
        namespace = constraint.namespace or str(metadata.get("database_namespace") or "")
        measure = str(metadata.get("snapshot_constraint") or "")
        if metadata.get("guardrail"):
            return f"Need to preserve {namespace or 'state'} guardrail."
        if namespace:
            return f"Need to satisfy the {namespace} {measure or 'snapshot'} constraint."
    return f"target={constraint.target.value}, operator={constraint.operator.value}"


def _constraint_failure_detail(constraint: Constraint | None, score: JsonObject) -> JsonObject:
    threshold = constraint.threshold if constraint is not None else 1.0
    evidence = score.get("evidence")
    metadata = constraint.metadata.get("toolsandbox") if constraint is not None else None
    toolsandbox_measure = str(metadata.get("snapshot_constraint") or "") if isinstance(metadata, dict) else ""
    actual_excerpt = constraint_actual_excerpt(constraint, score) if "actual" in score else None
    if actual_excerpt is None and "actual" in score:
        actual_excerpt = compact_json_text(score.get("actual"), 420)
    detail: JsonObject = {
        "constraint_id": str(score.get("constraint_id") or (constraint.constraint_id if constraint else "constraint")),
        "score": score.get("score"),
        "threshold": threshold,
        "missing": bool(score.get("missing")),
        "hard": constraint.hard if constraint is not None else None,
        "target": constraint.target.value if constraint is not None else None,
        "namespace": constraint.namespace if constraint is not None else None,
        "operator": constraint.operator.value if constraint is not None else None,
        "semantic_kind": (
            str(constraint.stage_goal_semantics.get("kind") or "")
            if constraint is not None and isinstance(constraint.stage_goal_semantics, dict)
            else ""
        ),
        "goal_hint": _constraint_goal_hint(constraint),
        "toolsandbox_measure": toolsandbox_measure,
        "evidence": [compact_text(item, 220) for item in evidence[:2]] if isinstance(evidence, list) else [],
        "actual_excerpt": actual_excerpt,
    }
    if detail["semantic_kind"] == "tool_call":
        semantics = constraint.stage_goal_semantics if constraint is not None and isinstance(constraint.stage_goal_semantics, dict) else {}
        detail["expected_tool_name"] = semantics.get("tool_name")
        detail["argument_match_policy"] = semantics.get("argument_match_policy", "exact")
        if detail["argument_match_policy"] == "exact":
            detail["expected_tool_arguments"] = json_safe(semantics.get("arguments", {}))
    if constraint is not None:
        detail["expected_summary"] = constraint_summary_to_dict(constraint)["expected_summary"]
        detail["expected_excerpt"] = constraint_expected_excerpt(constraint)
    score_text = _format_number(detail.get("score"))
    threshold_text = _format_number(detail.get("threshold"))
    evidence_text = str(detail["evidence"][0]) if isinstance(detail.get("evidence"), list) and detail["evidence"] else ""
    actual_text = str(detail.get("actual_excerpt") or "")
    parts = [
        f"{detail['constraint_id']} score {score_text}, below threshold {threshold_text}"
    ]
    if detail["semantic_kind"] == "preserve_state" and detail.get("expected_summary", {}).get("row_count") == 0:
        parts.append("expected_rows=0 is a serialized placeholder; preserve_state target is resolved from runtime reference baseline")
    if _excerpt_shows_matched_expected_rows(actual_text) and _score_value(detail.get("score")) < _score_value(detail.get("threshold")):
        parts.append(
            "target-related rows already appeared, but structured scoring still failed; check extra state changes or reference drift"
        )
    if detail["goal_hint"]:
        parts.append(str(detail["goal_hint"]))
    if evidence_text:
        parts.append(f"evidence: {evidence_text}")
    detail["line"] = " | ".join(parts)
    return detail


def _failed_constraint_details(milestone: Milestone, score_payload: JsonObject | None) -> list[JsonObject]:
    if not isinstance(score_payload, dict):
        return []
    raw_scores = score_payload.get("constraint_scores")
    if not isinstance(raw_scores, list):
        return []
    constraints = {constraint.constraint_id: constraint for constraint in milestone.constraints}
    details: list[tuple[float, JsonObject]] = []
    fallbacks: list[tuple[float, JsonObject]] = []
    for raw_score in raw_scores:
        if not isinstance(raw_score, dict):
            continue
        constraint_id = str(raw_score.get("constraint_id") or "")
        constraint = constraints.get(constraint_id)
        threshold = constraint.threshold if constraint is not None else 1.0
        score_value = as_number(raw_score.get("score"))
        missing = bool(raw_score.get("missing"))
        detail = _constraint_failure_detail(constraint, raw_score)
        sort_key = score_value if score_value is not None else -1.0
        fallbacks.append((sort_key, detail))
        if missing or score_value is None or score_value < threshold:
            details.append((sort_key, detail))
    selected = details or sorted(fallbacks, key=lambda item: item[0])[:1]
    return [detail for _, detail in sorted(selected, key=lambda item: item[0])[:5]]


def _excerpt_shows_matched_expected_rows(actual_excerpt: str) -> bool:
    match = re.search(r"matched_expected_rows=(\d+)/(\d+)", actual_excerpt)
    if match is None:
        return False
    return int(match.group(1)) > 0 and int(match.group(2)) > 0


def _score_value(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _pending_failure_diagnostics(
    milestone: Milestone,
    topology: MilestoneTopology,
    blocker: str,
    common: JsonObject,
    best_entry: JsonObject | None,
    last_entry: JsonObject | None,
    pending_predecessors: list[str],
) -> JsonObject:
    attempt_count = int(common.get("attempt_count") or 0)
    score_payload = best_entry.get("score") if isinstance(best_entry, dict) else None
    boundary = best_entry.get("boundary") if isinstance(best_entry, dict) else None
    failed_constraints = _failed_constraint_details(milestone, score_payload if isinstance(score_payload, dict) else None)
    threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8

    if blocker == "attempted_but_not_pass":
        step_text = ""
        if isinstance(boundary, dict) and boundary.get("step_index") is not None:
            step_text = f"; best candidate step={boundary.get('step_index')}"
        status_text = (
            str(score_payload.get("status") or common.get("best_status") or "unknown")
            if isinstance(score_payload, dict)
            else str(common.get("best_status") or "unknown")
        )
        summary = (
            f"milestone {milestone.milestone_id} tried {attempt_count} times but never reached threshold {threshold:.3f}{step_text}; "
            f"best score {_format_number(common.get('best_score'))} (status={status_text})."
        )
        reasons = [summary]
        if failed_constraints:
            lead = str(failed_constraints[0].get("line") or "")
            summary = f"{summary} Main unmet constraint: {lead}."
            reasons[0] = summary
            reasons.extend(str(item.get("line") or "") for item in failed_constraints[1:])
        elif isinstance(last_entry, dict) and last_entry.get("reject_reason"):
            reasons.append(f"Last reject reason: {last_entry.get('reject_reason')}")
        semantic_review, review_line = _semantic_review_failure(best_entry, last_entry)
        if semantic_review is not None and review_line is not None:
            summary = f"{summary} {review_line}"
            reasons.insert(1, review_line)
        result: JsonObject = {
            "failure_summary": summary,
            "failure_reasons": reasons,
            "failed_constraints": failed_constraints,
        }
        if semantic_review is not None:
            result["semantic_review"] = semantic_review
        return result

    if blocker == "predecessor_not_matched":
        missing_text = ", ".join(pending_predecessors) if pending_predecessors else "unknown"
        summary = f"milestone {milestone.milestone_id} is not yet evaluable because predecessor milestones are incomplete: {missing_text}."
        return {
            "failure_summary": summary,
            "failure_reasons": [summary],
            "failed_constraints": failed_constraints,
        }

    if blocker == "ready_without_candidate":
        summary = f"milestone {milestone.milestone_id} was ready, but no candidate boundary appeared before the run ended."
    else:
        summary = f"milestone {milestone.milestone_id} has not become ready yet; the run may still lack the prerequisite evidence."
    return {
        "failure_summary": summary,
        "failure_reasons": [summary],
        "failed_constraints": failed_constraints,
    }


def _semantic_review_failure(*candidates: JsonObject | None) -> tuple[JsonObject | None, str | None]:
    """从候选详情中读取并格式化最后一次 rejected semantic review。"""
    for candidate in reversed([item for item in candidates if isinstance(item, dict)]):
        semantic_review = candidate.get("llm_semantic_review")
        if isinstance(semantic_review, dict) and semantic_review.get("status") == "rejected":
            review = dict(semantic_review)
            reason = str(review.get("reason") or "").strip()
            rejected_ids = review.get("rejected_constraint_ids")
            id_text = ",".join(str(item) for item in rejected_ids) if isinstance(rejected_ids, list) else ""
            suffix = f"：{compact_text(reason, 220)}" if reason else ""
            id_suffix = f"（{id_text}）" if id_text else ""
            return review, f"消息语义复判 rejected{id_suffix}{suffix}。"
    return None, None


def build_milestone_graph_summary(graph: MilestoneGraph) -> JsonObject:
    return {
        "total_milestone_count": len(graph.nodes),
        "mandatory_milestone_ids": [node.milestone_id for node in graph.nodes],
        "edges": [[source, target] for source, target in graph.edges],
        "nodes": [
            {
                **milestone_summary_to_dict(node),
                "constraints": [constraint_summary_to_dict(constraint) for constraint in node.constraints],
            }
            for node in graph.nodes
        ],
    }


def build_milestone_matching_detail(
    matched: dict[str, HarnessStageSettlement],
    milestone: Milestone,
    scoring_step: TrajectoryStep,
    milestone_score: MilestoneScore,
    ready_milestone_ids_before_match: list[str],
) -> JsonObject:
    return {
        "mode": "runtime_checkpoint",
        "matched": True,
        "milestone": milestone_summary_to_dict(milestone),
        "boundary": {"boundary_id": f"runtime:b{scoring_step.index}", "step_index": scoring_step.index, "step_id": scoring_step.step_id},
        "score": json_safe(milestone_score),
        "ready_milestone_ids_before_match": list(ready_milestone_ids_before_match),
        "matched_milestone_ids_before_match": sorted(matched),
        "predecessor_milestone_ids": list(topology.predecessors_by_id[milestone.milestone_id]),
    }


def build_final_milestone_diagnostics(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
    match_attempts: list[JsonObject],
    termination: EvaluationTerminationState | None = None,
) -> list[JsonObject]:
    milestone_ids = {node.milestone_id for node in graph.nodes}
    topology = graph.topology
    if topology is None:
        raise ValueError("milestone graph 尚未 enrich")
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
                    candidate_detail = dict(candidate)
                    semantic_review = attempt.get("llm_semantic_review")
                    if isinstance(semantic_review, dict):
                        candidate_detail["llm_semantic_review"] = semantic_review
                    attempts_by_milestone[str(candidate["milestone_id"])].append(candidate_detail)

    diagnostics: list[JsonObject] = []
    for node in graph.nodes:
        candidate_entries = attempts_by_milestone[node.milestone_id]
        scored_entries = [entry for entry in candidate_entries if isinstance(entry.get("score"), dict)]
        best_entry = max(scored_entries, key=lambda item: float(item["score"].get("score", 0.0)), default=None)
        last_entry = candidate_entries[-1] if candidate_entries else None
        pending_predecessors = [item for item in topology.predecessors_by_id[node.milestone_id] if item not in matched]
        finally_ready = len(pending_predecessors) == 0
        best_score = best_entry.get("score") if isinstance(best_entry, dict) else None
        best_boundary = best_entry.get("boundary") if isinstance(best_entry, dict) else None
        common: JsonObject = {
            "milestone_id": node.milestone_id,
            "mandatory": True,
            "dependency_predecessor_ids": list(topology.predecessors_by_id[node.milestone_id]),
            "stage_anchor_milestone_id": topology.stage_anchor_by_id[node.milestone_id],
            "ready_ever": node.milestone_id in ready_seen or finally_ready,
            "finally_ready": finally_ready,
            "attempt_count": len(candidate_entries),
            "best_score": best_score.get("score") if isinstance(best_score, dict) else None,
            "best_status": best_score.get("status") if isinstance(best_score, dict) else None,
            "best_boundary_step_index": (best_boundary.get("step_index") if isinstance(best_boundary, dict) else None),
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

        if scored_entries:
            blocker = "attempted_but_not_pass"
        elif pending_predecessors:
            blocker = "predecessor_not_matched"
        elif finally_ready:
            blocker = "ready_without_candidate"
        else:
            blocker = "not_ready"
        failure_diagnostics = _pending_failure_diagnostics(
            milestone=node,
            blocker=blocker,
            common=common,
            best_entry=best_entry,
            last_entry=last_entry,
            pending_predecessors=pending_predecessors,
        )
        pending_detail: JsonObject = {
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
            **failure_diagnostics,
        }
        if termination is not None and termination.should_stop:
            pending_detail["blocked_by_policy_stop"] = True
            pending_detail["policy_stop_detail"] = termination.to_dict()
        diagnostics.append(pending_detail)
    return diagnostics


def build_finish_matching_detail(graph: MilestoneGraph, matched: dict[str, HarnessStageSettlement]) -> JsonObject:
    matched_ids = set(matched)
    milestone_ids = {node.milestone_id for node in graph.nodes}
    pending_ids = milestone_ids - matched_ids
    return {
        "mode": "runtime_finish",
        "matched": not pending_ids,
        "matched_milestone_ids": sorted(matched_ids),
        "pending_milestone_ids": sorted(pending_ids),
        "total_milestone_count": len(graph.nodes),
    }


def build_quality_diagnostics(steps: list[TrajectoryStep]) -> JsonObject:
    tool_argument_warnings: list[JsonObject] = []
    empty_tool_results: list[JsonObject] = []
    informational_tool_results: list[JsonObject] = []
    query_no_match_results: list[JsonObject] = []
    failed_tool_results: list[JsonObject] = []
    grounding_warnings: list[JsonObject] = []
    next_agent_message_by_index = _next_agent_message_by_index(steps)
    first_user_index: int | None = None
    first_tool_index: int | None = None
    tool_call_count = 0
    latest_tool_call: TrajectoryStep | None = None
    pending_calls: dict[str, TrajectoryStep] = {}
    unmatched_calls: list[TrajectoryStep] = []
    for candidate in steps:
        if candidate.tool_call is not None or candidate.event_type == EventType.TOOL_CALL:
            call_id = str(candidate.raw.get("openai_tool_call_id") or candidate.raw.get("tool_call_id") or "")
            if call_id:
                pending_calls[call_id] = candidate
            else:
                unmatched_calls.append(candidate)

    for step in steps:
        if step.actor == Actor.USER and first_user_index is None:
            first_user_index = step.index
        if step.tool_call is not None or step.event_type == EventType.TOOL_CALL:
            tool_call_count += 1
            first_tool_index = step.index if first_tool_index is None else first_tool_index
            latest_tool_call = step
            tool_argument_warnings.extend(_tool_argument_warnings(step))
            continue
        if step.tool_result is None and step.event_type != EventType.TOOL_RESULT:
            continue
        result = step.tool_result
        raw_name = step.raw.get("openai_function_name")
        call_id = str(step.raw.get("openai_tool_call_id") or step.raw.get("tool_call_id") or "")
        matched_call = pending_calls.get(call_id) if call_id else None
        if isinstance(raw_name, str) and raw_name.strip():
            tool_name = raw_name.strip()
            attribution_source = "result.openai_function_name"
        elif matched_call is not None and matched_call.tool_call is not None:
            tool_name = matched_call.tool_call.name
            attribution_source = "result.openai_tool_call_id"
        elif len(unmatched_calls) == 1 and latest_tool_call is not None and latest_tool_call.tool_call is not None:
            tool_name = latest_tool_call.tool_call.name
            attribution_source = "single_unmatched_call_fallback"
        else:
            tool_name = None
            attribution_source = "ambiguous"
        success = bool(result.success) if result is not None else False
        exception = result.exception if result is not None else None
        content = result.content if result is not None else None
        if not success or exception:
            failed_tool_results.append(
                {"step_index": step.index, "step_id": step.step_id, "tool_name": tool_name, "tool_call_id": call_id or None, "attribution_source": attribution_source, "attribution_status": "ambiguous" if tool_name is None else "matched", "exception": exception}
            )
        if success and _empty_tool_content(content):
            empty = {
                "step_index": step.index,
                "step_id": step.step_id,
                "tool_name": tool_name,
                "tool_call_id": call_id or None,
                "attribution_source": attribution_source,
                "attribution_status": "ambiguous" if tool_name is None else "matched",
                "content": content,
                **_classify_empty_tool_result(tool_name, content),
            }
            category = empty["result_category"]
            if category == "state_mutation_no_payload":
                informational_tool_results.append(empty)
            elif category == "query_no_match":
                query_no_match_results.append(empty)
            else:
                empty_tool_results.append(empty)
            answer = next_agent_message_by_index.get(step.index)
            if category == "query_no_match" and answer is not None and _answer_claims_query_match(answer.content):
                grounding_warnings.append(
                    {
                        "warning": "agent_answer_after_empty_tool_result",
                        "tool_name": tool_name,
                        "tool_result_step_index": step.index,
                        "answer_step_index": answer.index,
                        "answer_excerpt": compact_text(answer.content or ""),
                    }
                )

    first_user_index = -1 if first_user_index is None else first_user_index
    first_tool_index = max((step.index for step in steps), default=first_user_index) + 1 if first_tool_index is None else first_tool_index
    extra_user_turns = [
        step.index for step in steps if step.actor == Actor.USER and first_user_index < step.index < first_tool_index
    ]
    agent_messages = [
        step.index
        for step in steps
        if step.actor == Actor.AGENT
        and step.event_type == EventType.MESSAGE
        and first_user_index < step.index < first_tool_index
    ]
    empty_tool_warning_count = sum(1 for item in empty_tool_results if item.get("severity") == "warning")
    warning_count = (
        len(tool_argument_warnings)
        + empty_tool_warning_count
        + len(failed_tool_results)
        + len(grounding_warnings)
        + (1 if extra_user_turns else 0)
    )
    efficiency = {
        "extra_user_turns_before_first_tool_call": len(extra_user_turns),
        "extra_user_turn_step_indices": extra_user_turns,
        "agent_messages_before_first_tool_call": len(agent_messages),
        "agent_message_step_indices_before_first_tool_call": agent_messages,
        "tool_call_count": tool_call_count,
        "step_count": len(steps),
    }
    return {
        "step_count": len(steps),
        "warning_count": warning_count,
        "tool_argument_warnings": tool_argument_warnings,
        "empty_tool_results": empty_tool_results,
        "failed_tool_results": failed_tool_results,
        "grounding_warnings": grounding_warnings,
        "informational_tool_results": informational_tool_results,
        "query_no_match_results": query_no_match_results,
        "efficiency": efficiency,
    }


def _classify_empty_tool_result(tool_name: str | None, content: object) -> JsonObject:
    """按工具语义和空值形态区分无 payload mutation、无匹配 query 与异常空值。"""
    if tool_name is None or not str(tool_name).strip():
        return {"severity": "warning", "result_category": "unknown_empty_payload"}
    normalized = str(tool_name).strip().lower()
    if normalized.startswith(STATE_MUTATION_TOOL_PREFIXES):
        return {"severity": "info", "result_category": "state_mutation_no_payload"}
    if normalized.startswith(QUERY_TOOL_PREFIXES) and _empty_tool_content(content, treat_null_as_empty=False):
        return {"severity": "info", "result_category": "query_no_match"}
    return {"severity": "warning", "result_category": "unknown_empty_payload"}


def _answer_claims_query_match(content: str | None) -> bool:
    """判断空查询后的回答是否避开“未找到”语义并声称存在结果。"""
    text = str(content or "").strip().lower()
    if not text:
        return False
    no_match_markers = (
        "not found",
        "no match",
        "no result",
        "nothing found",
        "couldn't find",
        "could not find",
        "unable to find",
        "didn't find",
        "未找到",
        "没有找到",
        "无法找到",
        "查无",
        "请补充",
    )
    return not any(marker in text for marker in no_match_markers)


def _tool_argument_warnings(step: TrajectoryStep) -> list[JsonObject]:
    tool_call = step.tool_call
    if tool_call is None:
        return []
    warnings: list[JsonObject] = []
    for key, value in tool_call.arguments.items():
        argument_name = str(key)
        if (
            (argument_name.endswith("_id") or argument_name.endswith("_person_id"))
            and isinstance(value, str)
            and value.strip().lower() in {"self", "me", "user", "agent"}
        ):
            warnings.append(
                {
                    "warning": "literal_alias_for_id_argument",
                    "step_index": step.index,
                    "step_id": step.step_id,
                    "tool_name": tool_call.name,
                    "argument_name": argument_name,
                    "argument_value": value,
                }
            )
    return warnings


def _next_agent_message_by_index(steps: list[TrajectoryStep]) -> dict[int, TrajectoryStep | None]:
    result: dict[int, TrajectoryStep | None] = {}
    next_message: TrajectoryStep | None = None
    for step in reversed(steps):
        result[step.index] = next_message
        if (
            step.actor == Actor.AGENT
            and step.event_type in {EventType.MESSAGE, EventType.FINAL}
            and isinstance(step.content, str)
            and step.content.strip()
        ):
            next_message = step
    return result


def _empty_tool_content(value: object, *, treat_null_as_empty: bool = True) -> bool:
    if value is None:
        return treat_null_as_empty
    if isinstance(value, str):
        empty_values = {"", "[]", "{}"}
        if treat_null_as_empty:
            empty_values |= {"none", "null"}
        return value.strip().lower() in empty_values
    return isinstance(value, list | dict) and len(value) == 0
