from __future__ import annotations

from dynsteer.boundary import boundary_snapshot
from dynsteer.evaluate.diagnostics import build_final_milestone_diagnostics, build_milestone_graph_summary
from dynsteer.evaluate.models import RuntimeEvaluationState
from dynsteer.evaluate.score import ScoringContext
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Boundary,
    Dimension,
    EvaluationLevel,
    JsonObject,
    MilestoneGraph,
    StageEvaluationResult,
    StageStatus,
    StateSnapshot,
    TaskCase,
    Trajectory,
)


def runtime_diagnostics_summary(task_case: TaskCase, state: RuntimeEvaluationState) -> JsonObject:
    """构造运行期 raw_summary 的 milestone 诊断信息。

    Args:
        task_case: 当前任务定义。
        state: 运行结束时的评估状态。

    Returns:
        可合入 raw_summary 的诊断字段。
    """
    if task_case is None or state is None:
        raise ValueError("运行期诊断参数不能为空")
    graph = task_case.milestone_graph or MilestoneGraph()
    return {
        "milestone_graph_summary": build_milestone_graph_summary(graph),
        "milestone_match_attempts": list(state.match_attempts),
        "milestone_final_diagnostics": build_final_milestone_diagnostics(
            graph=graph,
            matched=state.matched_settlements,
            match_attempts=state.match_attempts,
        ),
    }


def blocked_milestone_termination_reason(detail: JsonObject) -> str:
    """根据前驱断裂诊断生成中文终止原因。

    Args:
        detail: `find_blocked_milestone_hit_with_diagnostics()` 返回的诊断对象。

    Returns:
        面向日志和 stop_case 的中文终止原因。
    """
    if detail is None:
        raise ValueError("路径断裂诊断不能为空")
    current_step = detail.get("current_step")
    step_id = None
    if isinstance(current_step, dict):
        step_id = current_step.get("step_id")
    if step_id is None:
        step_id = detail.get("step_id")
    milestone_id = str(detail.get("milestone_id") or "unknown")
    missing = detail.get("missing_predecessors")
    missing_text = ",".join(str(item) for item in missing) if isinstance(missing, list) else "unknown"
    score = detail.get("score")
    evidence_text = ""
    if isinstance(score, dict):
        evidence = score.get("evidence")
        if isinstance(evidence, list) and evidence:
            evidence_text = str(evidence[0])
        else:
            evidence_text = f"score={score.get('score')}, status={score.get('status')}"
    predecessor_diagnostics = detail.get("predecessor_diagnostics")
    predecessor_text = ""
    if isinstance(predecessor_diagnostics, list) and predecessor_diagnostics:
        predecessor_text = str(predecessor_diagnostics[0].get("best_candidate"))
    return (
        f"当前 step={step_id} 命中 milestone={milestone_id}，"
        f"但前驱 milestone={missing_text} 未匹配；"
        f"当前 milestone 证据={evidence_text}；前驱诊断={predecessor_text}"
    )


def pending_required_stage_results(task_case: TaskCase, state: RuntimeEvaluationState) -> list[StageEvaluationResult]:
    """为自然结束时仍未完成的 required milestone 生成失败阶段报告。

    Args:
        task_case: 当前任务定义。
        state: 运行结束时的评估状态。

    Returns:
        与缺失 required milestone 对应的运行期失败阶段结果。
    """
    if task_case is None or state is None:
        raise ValueError("pending required stage 参数不能为空")
    graph = task_case.milestone_graph or MilestoneGraph()
    diagnostics = build_final_milestone_diagnostics(
        graph=graph,
        matched=state.matched_settlements,
        match_attempts=state.match_attempts,
    )
    results: list[StageEvaluationResult] = []
    for item in diagnostics:
        if item.get("required") is not True or item.get("final_state") == "matched":
            continue
        milestone_id = str(item.get("milestone_id") or "unknown")
        blocker = str(item.get("blocker") or "unknown")
        ready_ever = bool(item.get("ready_ever"))
        attempt_count = int(item.get("attempt_count") or 0)
        status = StageStatus.FAIL if ready_ever or attempt_count > 0 else StageStatus.MISSING
        evidence = [
            (
                f"required milestone 未完成: milestone={milestone_id}, blocker={blocker}, "
                f"best_score={item.get('best_score')}, "
                f"best_boundary_step_index={item.get('best_boundary_step_index')}, "
                f"pending_predecessor_ids={item.get('pending_predecessor_ids')}"
            )
        ]
        results.append(
            StageEvaluationResult(
                stage_id=f"runtime:missing:{milestone_id}",
                milestone_id=milestone_id,
                evaluator_level=EvaluationLevel.CHEAP,
                status=status,
                stage_score=0.0,
                uncertainty=0.0,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
                evidence=evidence,
                diagnosis=[f"required milestone {milestone_id} 未完成"],
                hard_constraints_all_pass=False,
                required_fields_missing_ratio=1.0,
                metadata=dict(item),
            )
        )
    return results


def scoring_context(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
) -> ScoringContext:
    """构造运行期评分上下文。

    Args:
        task_case: 当前任务定义。
        trajectory: 当前已观测轨迹。
        matched: 已结算 milestone 映射。

    Returns:
        包含初始快照、已命中边界和已命中快照的评分上下文。
    """
    if task_case is None or trajectory is None or matched is None:
        raise ValueError("评分上下文参数不能为空")
    matched_boundaries: dict[str, Boundary] = {}
    matched_snapshots: dict[str, StateSnapshot] = {}
    initial_state = task_case.initial_state
    if isinstance(initial_state, dict):
        namespaces = initial_state.get("namespaces")
        if isinstance(namespaces, dict):
            matched_snapshots["initial"] = StateSnapshot(
                snapshot_id="initial",
                after_step_id="initial",
                after_step_index=0,
                namespaces={str(key): value for key, value in namespaces.items()},
            )
    for milestone_id, settlement in matched.items():
        if settlement.boundary_step_index is None:
            continue
        boundary = Boundary(
            boundary_id=settlement.boundary_id or f"matched:{milestone_id}",
            step_index=settlement.boundary_step_index,
            snapshot_id=None,
            reason="matched_milestone",
        )
        matched_boundaries[milestone_id] = boundary
        snapshot = boundary_snapshot(boundary, trajectory.snapshots)
        if snapshot is not None:
            matched_snapshots[milestone_id] = snapshot
    return ScoringContext(
        task_case=task_case,
        matched_boundaries=matched_boundaries,
        matched_snapshots=matched_snapshots,
    )


def task_case_snapshot(case_id: str, task_case: TaskCase, trajectory: Trajectory) -> JsonObject:
    """构造可审计的任务快照摘要。

    Args:
        case_id: benchmark case ID。
        task_case: 当前任务定义。
        trajectory: 当前完整轨迹。

    Returns:
        包含 task_description 与首条用户消息摘要的轻量快照。
    """
    if case_id is None or not str(case_id).strip() or task_case is None or trajectory is None:
        raise ValueError("task_case 快照参数不能为空")
    metadata = dict(task_case.metadata)
    return {
        "case_id": str(case_id),
        "task_id": task_case.task_id,
        "task_description": task_case.task_description,
        "task_types": [item.value for item in task_case.task_types],
        "scenario_name": metadata.get("scenario_name"),
        "categories": list(metadata.get("categories", [])) if isinstance(metadata.get("categories"), list) else [],
        "initial_user_message_excerpt": _initial_user_message_excerpt(trajectory),
    }


def task_description_mismatched(snapshot: JsonObject) -> bool:
    """判断任务描述与首条用户消息摘要是否明显不一致。"""
    if snapshot is None:
        raise ValueError("task_case 快照不能为空")
    description = snapshot.get("task_description")
    initial_message = snapshot.get("initial_user_message_excerpt")
    if not isinstance(description, str) or not isinstance(initial_message, str):
        return False
    return bool(description.strip() and initial_message.strip() and description.strip() != initial_message.strip())


def _initial_user_message_excerpt(trajectory: Trajectory) -> str | None:
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    for step in trajectory.steps:
        if step.actor == Actor.USER and isinstance(step.content, str) and step.content.strip():
            return _excerpt(step.content)
    return None


def _excerpt(value: str, limit: int = 240) -> str:
    if value is None:
        raise ValueError("摘要文本不能为空")
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
