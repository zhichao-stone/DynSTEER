from dynsteer.evaluate.runtime import scoring_context
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.evaluate.semantic import constraint_actual_excerpt, constraint_expected_excerpt
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import Constraint, ConstraintTarget, JsonObject, Milestone, MilestoneGraph, RuntimeEvaluationState, StageGoalSemanticKind, StageInterval, StageStatus, StateSnapshot, TaskCase, Trajectory
from dynsteer.utils import clean_evidence_items, compact_text, json_safe

def build_finish_verification(task_case: TaskCase, trajectory: Trajectory, state: RuntimeEvaluationState, scorer: GeneralScorer, interval: StageInterval) -> JsonObject:
    """构造 `__finish__` 的任务级最终核查 payload。

    入参：
        task_case: 当前 benchmark case。
        trajectory: 完整运行期轨迹。
        matched: 已匹配真实 milestone 结算表。
        state: 当前运行期评估状态。
        scorer: 当前 benchmark 约束评分器。
    输出：
        JSON payload，供 finish stage report 组装使用。
    """
    graph = task_case.milestone_graph
    matched = state.matched_settlements
    if not graph.nodes:
        return _empty_graph_finish_verification(graph, state)
    milestone_ids = [node.milestone_id for node in graph.nodes]
    matched_ids = set(matched)
    unmatched_ids = [milestone_id for milestone_id in milestone_ids if milestone_id not in matched_ids]
    terminal_ids = list(graph.topology.terminal_ids) if graph.topology is not None else []
    terminal_state_checks = _terminal_state_checks(
        task_case,
        trajectory,
        matched,
        state.reference_anchor_snapshots,
        terminal_ids,
        scorer,
    )
    terminal_message_checks = _terminal_message_checks(task_case, matched, terminal_ids)
    failed_terminal_checks = [item for item in terminal_state_checks if item.get("status") in {StageStatus.FAIL.value, StageStatus.MISSING.value, StageStatus.INVALID.value}]
    warn_terminal_checks = [item for item in [*terminal_state_checks, *terminal_message_checks] if item.get("status") == StageStatus.WARN.value]
    fatal_minefield = bool(state.fatal_minefield)
    if unmatched_ids or failed_terminal_checks or fatal_minefield:
        status = StageStatus.FAIL
        score = 0.0
    elif warn_terminal_checks:
        status = StageStatus.WARN
        score = 0.7
    else:
        status = StageStatus.PASS
        score = 1.0
    unmatched_text = ", ".join(unmatched_ids)
    fatal_text = "触发" if fatal_minefield else "未触发"
    evidence = clean_evidence_items(
        [
            "finish 结算节点",
            f"真实 milestone 覆盖：{len(milestone_ids) - len(unmatched_ids)}/{len(milestone_ids)}",
            *(
                [f"未完成 milestone：{unmatched_text}"]
                if unmatched_ids
                else ["所有真实 milestone 已匹配"]
            ),
            *_terminal_check_evidence(terminal_state_checks, state_check=True),
            *_terminal_check_evidence(terminal_message_checks, state_check=False),
            f"fatal minefield：{fatal_text}",
            *_terminal_step_evidence(trajectory, interval),
        ]
    )
    diagnosis: list[str] = []
    if status == StageStatus.PASS:
        diagnosis = ["finish final verification passed: full milestone coverage."]
    else:
        if unmatched_ids:
            diagnosis.append(
                f"finish final verification failed: unfinished milestones {', '.join(unmatched_ids)}."
            )
        if failed_terminal_checks:
            failed = ", ".join((str(item.get("milestone_id")) for item in failed_terminal_checks))
            diagnosis.append(f"finish final verification failed: terminal checks not passed {failed}.")
        if fatal_minefield:
            diagnosis.append("finish final verification failed: fatal minefield during runtime.")
        if warn_terminal_checks:
            warned = ", ".join((str(item.get("milestone_id")) for item in warn_terminal_checks))
            diagnosis.append(f"finish final verification warning: terminal milestones have soft issues {warned}.")
        if not diagnosis:
            diagnosis = ["finish final verification warning: non-fatal quality issue."]
    return {
        "unmatched_milestone_ids": unmatched_ids,
        "terminal_milestone_ids": terminal_ids,
        "terminal_state_checks": terminal_state_checks,
        "terminal_message_checks": terminal_message_checks,
        "fatal_minefield": fatal_minefield,
        "coverage_basis": "milestone_graph",
        "evaluation_mode": "deterministic",
        "status": status.value,
        "score": score,
        "evidence": evidence,
        "diagnosis": diagnosis,
    }


def _empty_graph_finish_verification(graph: MilestoneGraph, state: RuntimeEvaluationState) -> JsonObject:
    fatal_minefield = bool(state.fatal_minefield)
    metadata = graph.metadata if isinstance(graph.metadata, dict) else {}
    coverage_basis = str(metadata.get("empty_graph_completion_basis") or "whole_trajectory")
    minefield_only = coverage_basis == "minefield_only"
    if minefield_only:
        status = StageStatus.FAIL if fatal_minefield else StageStatus.PASS
        score = 0.0 if fatal_minefield else 1.0
        evidence_message = (
            "minefield-only graph triggered fatal minefield"
            if fatal_minefield
            else "minefield-only graph completed without fatal minefield"
        )
        diagnosis_message = (
            "finish final verification failed: fatal minefield."
            if fatal_minefield
            else "finish final verification passed: no fatal minefield."
        )
    else:
        status = StageStatus.FAIL if fatal_minefield else StageStatus.AMBIGUOUS
        score = 0.0
        evidence_message = (
            "empty graph triggered fatal minefield"
            if fatal_minefield
            else "empty graph requires whole-trajectory evaluation"
        )
        diagnosis_message = (
            "finish final verification failed: fatal minefield."
            if fatal_minefield
            else "finish final verification precheck passed: whole-trajectory judge still required."
        )
    return {
        "unmatched_milestone_ids": [],
        "terminal_milestone_ids": [],
        "terminal_state_checks": [],
        "terminal_message_checks": [],
        "fatal_minefield": fatal_minefield,
        "coverage_basis": coverage_basis,
        "evaluation_mode": "deterministic" if minefield_only or fatal_minefield else "standard_judge",
        "minefield_count": len(graph.minefields) if graph is not None else 0,
        "minefield_match_count": len(state.minefield_matches),
        "status": status.value,
        "score": score,
        "evidence": [
            "finish 结算节点",
            evidence_message,
        ],
        "diagnosis": [
            diagnosis_message,
        ],
    }

def _terminal_state_checks(task_case: TaskCase, trajectory: Trajectory, matched: dict[str, HarnessStageSettlement], reference_anchor_snapshots: dict[str, StateSnapshot], terminal_ids: list[str], scorer: GeneralScorer) -> list[JsonObject]:
    graph = task_case.milestone_graph
    milestone_by_id = graph.topology.milestone_by_id if graph.topology is not None else {}
    if not trajectory.steps:
        return []
    final_step = trajectory.steps[-1]
    snapshot = trajectory.snapshot_at_or_before(final_step.index)
    context = scoring_context(task_case, trajectory, matched, reference_anchor_snapshots)
    checks: list[JsonObject] = []
    for milestone_id in terminal_ids:
        milestone = milestone_by_id.get(milestone_id)
        if milestone is None or milestone_id not in matched:
            continue
        constraints = [constraint for constraint in milestone.constraints if _is_terminal_state_constraint(constraint)]
        if not constraints:
            continue
        recheck = Milestone(milestone_id=milestone.milestone_id, name=milestone.name, description=milestone.description, constraints=constraints, pass_threshold=milestone.pass_threshold, metadata=dict(milestone.metadata))
        score = scorer.score_milestone(recheck, final_step, trajectory, trajectory.snapshots, context=context)
        constraint_by_id = {constraint.constraint_id: constraint for constraint in constraints}
        constraint_evidence = []
        for constraint_score in score.constraint_scores:
            constraint = constraint_by_id.get(constraint_score.constraint_id)
            constraint_evidence.append(
                {
                    "constraint_id": constraint_score.constraint_id,
                    "score": constraint_score.score,
                    "actual_excerpt": constraint_actual_excerpt(constraint, constraint_score),
                    "expected_excerpt": constraint_expected_excerpt(constraint),
                    "target_source": "Constraint.expected",
                    "evidence": list(constraint_score.evidence[:3]),
                }
            )
        checks.append(
            {
                "milestone_id": milestone_id,
                "status": score.status.value,
                "score": score.score,
                "constraint_count": len(constraints),
                "boundary_step_index": final_step.index,
                "snapshot_id": snapshot.snapshot_id if snapshot is not None else None,
                "evidence": list(score.evidence[:6]),
                "constraint_scores": json_safe(score.constraint_scores),
                "constraint_evidence": constraint_evidence,
            }
        )
    return checks

def _terminal_message_checks(task_case: TaskCase, matched: dict[str, HarnessStageSettlement], terminal_ids: list[str]) -> list[JsonObject]:
    graph = task_case.milestone_graph
    milestone_by_id = graph.topology.milestone_by_id if graph.topology is not None else {}
    checks: list[JsonObject] = []
    for milestone_id in terminal_ids:
        milestone = milestone_by_id.get(milestone_id)
        settlement = matched.get(milestone_id)
        if milestone is None or settlement is None:
            continue
        constraints = [constraint for constraint in milestone.constraints if _is_terminal_message_constraint(constraint)]
        if not constraints:
            continue
        checks.append(
            {
                "milestone_id": milestone_id,
                "status": str(settlement.status or StageStatus.PASS.value),
                "score": settlement.score,
                "constraint_count": len(constraints),
                "constraint_ids": [
                    constraint.constraint_id for constraint in constraints
                ],
                "boundary_step_index": settlement.boundary_step_index
                or settlement.end_step_index,
                "evidence": list(settlement.evidence[:6]),
                "recheck_skipped": True,
                "reason": "emit_message 已在原 terminal milestone 匹配阶段确认，finish 不在 end_conversation 后重复重评。",
            }
        )
    return checks

def _is_terminal_state_constraint(constraint: Constraint) -> bool:
    if _is_terminal_message_constraint(constraint):
        return False
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    if semantics.get("kind") in {StageGoalSemanticKind.SET_STATE.value, StageGoalSemanticKind.PRESERVE_STATE.value}:
        return True
    if constraint.target in {ConstraintTarget.STATE_SNAPSHOT, ConstraintTarget.STATE_DELTA}:
        return True
    metadata = constraint.metadata.get("toolsandbox") if isinstance(constraint.metadata, dict) else None
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("guardrail") or metadata.get("snapshot_constraint"))

def _is_terminal_message_constraint(constraint: Constraint) -> bool:
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    if semantics.get("kind") == StageGoalSemanticKind.EMIT_MESSAGE.value:
        return True
    if bool(semantics.get("user_visible_required")):
        return True
    metadata = constraint.metadata.get("toolsandbox") if isinstance(constraint.metadata, dict) else None
    namespace = constraint.namespace or ""
    if isinstance(metadata, dict):
        namespace = namespace or str(metadata.get("database_namespace") or "")
    return namespace.upper() == "SANDBOX"

def _terminal_check_evidence(checks: list[JsonObject], state_check: bool) -> list[str]:
    if not checks:
        return ["terminal 状态约束：无需要最终重检的状态约束"] if state_check else ["terminal 消息约束：无用户可见消息约束需要引用"]
    return [
        (
            f"terminal {'状态重检' if state_check else '消息约束'} "
            f"{item.get('milestone_id')}：status={item.get('status')}, "
            f"score={float(item.get('score') or 0.0):.3f}"
        )
        if state_check
        else (
            f"terminal 消息约束 {item.get('milestone_id')}："
            f"status={item.get('status')}，沿用原 milestone 匹配结果"
        )
        for item in checks
    ]

def _terminal_step_evidence(trajectory: Trajectory, interval: StageInterval) -> list[str]:
    if interval.start_boundary_step_index >= interval.end_step_index:
        return []
    steps = trajectory.get_interval(interval.start_boundary_step_index, interval.end_step_index)
    evidence: list[str] = []
    for step in steps[-4:]:
        actor = step.actor.value
        recipient = step.recipient.value if step.recipient is not None else "unknown"
        if step.tool_call is not None:
            action = f"calls {step.tool_call.name}"
        elif step.tool_result is not None:
            result_content = (
                compact_text(step.tool_result.content, 80)
                if step.tool_result.content is not None
                else "None"
            )
            action = f"returns {result_content}"
        elif isinstance(step.content, str) and step.content.strip():
            content = compact_text(step.content, 80)
            action = f"sends {content}"
            if content == "end_conversation":
                action = "calls end_conversation"
        else:
            action = step.event_type.value
        evidence.append(f"step {step.index}: {actor} -> {recipient} {action}")
    return evidence
