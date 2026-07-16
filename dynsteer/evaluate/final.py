from __future__ import annotations

from dynsteer.evaluate.matching.boundary import boundary_snapshot
from dynsteer.evaluate.runtime import scoring_context
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.graph import FINISH_NODE_ID, START_NODE_ID
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintTarget,
    JsonObject,
    Milestone,
    RuntimeEvaluationState,
    StageGoalSemanticKind,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.utils import clean_evidence_items, compact_text, json_safe


def build_finish_verification(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
    state: RuntimeEvaluationState,
    scorer: GeneralScorer,
) -> JsonObject:
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
    if task_case is None or trajectory is None or matched is None or state is None or scorer is None:
        raise ValueError("finish verification 参数不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")

    milestone_ids = [node.milestone_id for node in graph.nodes]
    matched_ids = set(matched)
    unmatched_ids = [milestone_id for milestone_id in milestone_ids if milestone_id not in matched_ids]
    terminal_ids = _terminal_milestone_ids(task_case)
    terminal_state_checks = _terminal_state_checks(task_case, trajectory, matched, terminal_ids, scorer)
    terminal_message_checks = _terminal_message_checks(task_case, matched, terminal_ids)
    failed_terminal_checks = [
        item
        for item in terminal_state_checks
        if item.get("status") in {StageStatus.FAIL.value, StageStatus.MISSING.value, StageStatus.INVALID.value}
    ]
    warn_terminal_checks = [
        item
        for item in [*terminal_state_checks, *terminal_message_checks]
        if item.get("status") == StageStatus.WARN.value
    ]
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

    evidence = clean_evidence_items(
        [
            "finish 结算节点",
            f"真实 milestone 覆盖：{len(milestone_ids) - len(unmatched_ids)}/{len(milestone_ids)}",
            *([f"未完成 milestone：{', '.join(unmatched_ids)}"] if unmatched_ids else ["所有真实 milestone 已匹配"]),
            *_terminal_check_evidence(terminal_state_checks),
            *_terminal_message_check_evidence(terminal_message_checks),
            f"fatal minefield：{'触发' if fatal_minefield else '未触发'}",
            *_terminal_step_evidence(task_case, trajectory, matched),
        ]
    )
    diagnosis = _finish_diagnosis(status, unmatched_ids, failed_terminal_checks, warn_terminal_checks, fatal_minefield)
    return {
        "all_milestones_matched": not unmatched_ids,
        "unmatched_milestone_ids": unmatched_ids,
        "terminal_milestone_ids": terminal_ids,
        "terminal_state_checks": terminal_state_checks,
        "terminal_message_checks": terminal_message_checks,
        "fatal_minefield": fatal_minefield,
        "status": status.value,
        "score": score,
        "evidence": evidence,
        "diagnosis": diagnosis,
    }


def _terminal_state_checks(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
    terminal_ids: list[str],
    scorer: GeneralScorer,
) -> list[JsonObject]:
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    milestone_by_id = {node.milestone_id: node for node in graph.nodes}
    final_step_index = trajectory.latest_step_index if trajectory.latest_step_index is not None else 0
    final_boundary = Boundary(
        boundary_id=f"finish:b{final_step_index}",
        step_index=final_step_index,
        snapshot_id=_latest_snapshot_id(final_step_index, trajectory),
        reason="finish_final_state_check",
    )
    context = scoring_context(task_case, trajectory, matched)
    checks: list[JsonObject] = []
    for milestone_id in terminal_ids:
        milestone = milestone_by_id.get(milestone_id)
        if milestone is None or milestone_id not in matched:
            continue
        constraints = [constraint for constraint in milestone.constraints if _is_terminal_state_constraint(constraint)]
        if not constraints:
            continue
        recheck = Milestone(
            milestone_id=milestone.milestone_id,
            name=milestone.name,
            description=milestone.description,
            constraints=constraints,
            pass_threshold=milestone.pass_threshold,
            metadata=dict(milestone.metadata),
            dependency_predecessor_ids=list(milestone.dependency_predecessor_ids),
            stage_anchor_predecessor_id=milestone.stage_anchor_predecessor_id,
        )
        score = scorer.score_milestone(
            recheck,
            final_boundary,
            trajectory,
            trajectory.snapshots,
            context=context,
        )
        checks.append(
            {
                "milestone_id": milestone_id,
                "status": score.status.value,
                "score": score.score,
                "constraint_count": len(constraints),
                "boundary_step_index": final_step_index,
                "snapshot_id": final_boundary.snapshot_id,
                "evidence": list(score.evidence[:6]),
                "constraint_scores": json_safe(score.constraint_scores),
            }
        )
    return checks


def _terminal_message_checks(
    task_case: TaskCase,
    matched: dict[str, HarnessStageSettlement],
    terminal_ids: list[str],
) -> list[JsonObject]:
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    milestone_by_id = {node.milestone_id: node for node in graph.nodes}
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
                "constraint_ids": [constraint.constraint_id for constraint in constraints],
                "boundary_step_index": settlement.boundary_step_index or settlement.end_step_index,
                "evidence": list(settlement.evidence[:6]),
                "recheck_skipped": True,
                "reason": "emit_message 已在原 terminal milestone 匹配阶段确认，finish 不在 end_conversation 后重复重评。",
            }
        )
    return checks


def _terminal_milestone_ids(task_case: TaskCase) -> list[str]:
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    real_ids = {node.milestone_id for node in graph.nodes}
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in real_ids}
    for source, target in graph.edges:
        if source in real_ids and target in real_ids:
            outgoing[source].add(target)
    terminals = [node.milestone_id for node in graph.nodes if not outgoing.get(node.milestone_id)]
    if terminals:
        return terminals
    analysis = graph.metadata.get("graph_analysis", {}) if isinstance(graph.metadata, dict) else {}
    augmented_edges = analysis.get("augmented_edges") if isinstance(analysis, dict) else None
    if isinstance(augmented_edges, list):
        return [
            str(edge[0])
            for edge in augmented_edges
            if isinstance(edge, list) and len(edge) >= 2 and edge[1] == FINISH_NODE_ID and edge[0] in real_ids
        ]
    return []


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


def _latest_snapshot_id(step_index: int, trajectory: Trajectory) -> str | None:
    snapshot = boundary_snapshot(
        Boundary("finish:snapshot", step_index, None, "finish_snapshot_lookup"),
        trajectory.snapshots,
    )
    return snapshot.snapshot_id if snapshot is not None else None


def _terminal_check_evidence(checks: list[JsonObject]) -> list[str]:
    if not checks:
        return ["terminal 状态约束：无需要最终重检的状态约束"]
    return [
        (
            f"terminal 状态重检 {item.get('milestone_id')}："
            f"status={item.get('status')}, score={float(item.get('score') or 0.0):.3f}"
        )
        for item in checks
    ]


def _terminal_message_check_evidence(checks: list[JsonObject]) -> list[str]:
    if not checks:
        return ["terminal 消息约束：无用户可见消息约束需要引用"]
    return [
        (
            f"terminal 消息约束 {item.get('milestone_id')}："
            f"status={item.get('status')}，沿用原 milestone 匹配结果"
        )
        for item in checks
    ]


def _terminal_step_evidence(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
) -> list[str]:
    final_step_index = trajectory.latest_step_index
    if final_step_index is None:
        return []
    boundary_index = _finish_anchor_boundary_index(task_case, trajectory, matched)
    if boundary_index >= final_step_index:
        boundary_index = final_step_index - 1
    steps = trajectory.get_interval(boundary_index, final_step_index)
    return [_step_evidence(step) for step in steps[-4:]]


def _finish_anchor_boundary_index(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
) -> int:
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    analysis = graph.metadata.get("graph_analysis", {}) if isinstance(graph.metadata, dict) else {}
    anchor_id = analysis.get("finish_stage_anchor_predecessor_id") if isinstance(analysis, dict) else START_NODE_ID
    if not isinstance(anchor_id, str) or not anchor_id or anchor_id == START_NODE_ID:
        return trajectory.first_step_index - 1
    if anchor_id in matched:
        return matched[anchor_id].end_step_index
    return max((settlement.end_step_index for settlement in matched.values()), default=trajectory.first_step_index - 1)


def _step_evidence(step: TrajectoryStep) -> str:
    actor = step.actor.value
    recipient = step.recipient.value if step.recipient is not None else "unknown"
    if step.tool_call is not None:
        action = f"calls {step.tool_call.name}"
    elif step.tool_result is not None:
        action = f"returns {compact_text(step.tool_result.content, 80) if step.tool_result.content is not None else 'None'}"
    elif isinstance(step.content, str) and step.content.strip():
        content = compact_text(step.content, 80)
        action = f"sends {content}"
        if content == "end_conversation":
            action = "calls end_conversation"
    else:
        action = step.event_type.value
    return f"step {step.index}: {actor} -> {recipient} {action}"


def _finish_diagnosis(
    status: StageStatus,
    unmatched_ids: list[str],
    failed_terminal_checks: list[JsonObject],
    warn_terminal_checks: list[JsonObject],
    fatal_minefield: bool,
) -> list[str]:
    if status == StageStatus.PASS:
        return ["finish final verification 通过：真实 milestone 已覆盖，terminal 状态未被最终边界推翻，用户可见消息沿用原 milestone 匹配结果。"]
    diagnosis: list[str] = []
    if unmatched_ids:
        diagnosis.append(f"finish final verification 失败：仍有未完成 milestone {', '.join(unmatched_ids)}。")
    if failed_terminal_checks:
        failed = ", ".join(str(item.get("milestone_id")) for item in failed_terminal_checks)
        diagnosis.append(f"finish final verification 失败：terminal 状态重检未通过 {failed}。")
    if fatal_minefield:
        diagnosis.append("finish final verification 失败：运行期触发 fatal minefield。")
    if warn_terminal_checks:
        warned = ", ".join(str(item.get("milestone_id")) for item in warn_terminal_checks)
        diagnosis.append(f"finish final verification 警告：terminal milestone 存在非致命质量问题 {warned}。")
    return diagnosis or ["finish final verification 存在非致命质量警告。"]
