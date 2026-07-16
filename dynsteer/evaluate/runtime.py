from __future__ import annotations

from dynsteer.evaluate.matching.boundary import boundary_snapshot
from dynsteer.evaluate.diagnostics import build_final_milestone_diagnostics, build_milestone_graph_summary
from dynsteer.evaluate.quality import build_runtime_quality_diagnostics
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import (
    Actor,
    Boundary,
    Dimension,
    EvaluationLevel,
    JsonObject,
    ReadyFrontierProgressWatch,
    ReadyMilestoneProgress,
    RuntimeEvaluationState,
    ScoringContext,
    StageEvaluationResult,
    StageStatus,
    StateSnapshot,
    TaskCase,
    ThresholdConfig,
    Trajectory,
)
from dynsteer.stage import stage_goal_key
from dynsteer.utils import compact_text


class JudgeConfigurationError(RuntimeError):
    """LLM judge 配置缺失或不合法时抛出。"""


class HarnessTeardownError(RuntimeError):
    """benchmark session 资源释放失败时抛出。"""


def update_ready_frontier_progress_watch(
    state: RuntimeEvaluationState,
    ready_ids: tuple[str, ...],
    attempt_detail: JsonObject,
    thresholds: ThresholdConfig,
    stop_enabled: bool,
    patience: int,
    min_delta: float,
) -> JsonObject | None:
    """更新 ready frontier 无进展追踪状态，必要时返回策略终止详情。

    入参：
        state: 当前运行期评估状态，函数会原地更新 watch。
        ready_ids: 当前 ready frontier 的 milestone id。
        attempt_detail: `analyze_milestone_step(...)` 生成的候选评分详情。
        thresholds: 阶段阈值配置，用于判断 frontier 是否已有 PASS 水平候选。
        stop_enabled: 是否启用 ready frontier 无进展终止策略。
        patience: 同一 frontier 连续无有效提升的观察次数阈值。
        min_delta: 判定有效提升的最小分数增量。
    输出：
        达到终止条件时返回 JSON 详情，否则返回 None。
    """
    if state is None or ready_ids is None or attempt_detail is None or thresholds is None:
        raise ValueError("ready frontier watch 参数不能为空")
    if patience < 1:
        raise ValueError("ready frontier patience 必须大于 0")
    if min_delta < 0:
        raise ValueError("ready frontier min_delta 不能为负数")
    if not stop_enabled:
        return None

    normalized_ready_ids = tuple(str(milestone_id) for milestone_id in ready_ids if str(milestone_id).strip())
    if not normalized_ready_ids:
        state.ready_frontier_progress_watch = None
        return None

    candidate_by_id = _candidate_scores_by_milestone(attempt_detail)
    observed_candidates = {
        milestone_id: candidate_by_id[milestone_id]
        for milestone_id in normalized_ready_ids
        if milestone_id in candidate_by_id and isinstance(candidate_by_id[milestone_id].get("score"), dict)
    }
    if not observed_candidates:
        return None

    step_index = _attempt_step_index(attempt_detail)
    watch = state.ready_frontier_progress_watch
    if watch is None or watch.frontier_key != normalized_ready_ids:
        state.ready_frontier_progress_watch = _build_ready_frontier_progress_watch(
            ready_ids=normalized_ready_ids,
            observed_candidates=observed_candidates,
            step_index=step_index,
        )
        return None

    watch.frontier_observation_count += 1
    watch.last_observed_step_index = step_index
    frontier_improved = False

    # 更新当前 frontier 内各 milestone 的历史最高分。
    for milestone_id, candidate in observed_candidates.items():
        current = _ready_milestone_progress_from_candidate(milestone_id, candidate, step_index)
        progress = watch.milestone_progress.get(milestone_id)
        if progress is None:
            watch.milestone_progress[milestone_id] = current
            frontier_improved = True
            continue
        if current.best_score >= progress.best_score + min_delta:
            progress.best_score = current.best_score
            progress.best_status = current.best_status
            progress.best_boundary_step_index = current.best_boundary_step_index
            progress.last_improved_step_index = step_index
            frontier_improved = True

    if frontier_improved:
        watch.last_frontier_improved_step_index = step_index
        watch.stale_frontier_observation_count = 0
        return None

    watch.stale_frontier_observation_count += 1
    if watch.stale_frontier_observation_count < patience:
        return None
    if any(progress.best_score >= thresholds.pass_threshold for progress in watch.milestone_progress.values()):
        return None
    return _ready_frontier_no_progress_detail(watch, patience, min_delta)


def ready_frontier_no_progress_termination_reason(detail: JsonObject) -> str:
    """根据 ready frontier 无进展详情生成中文终止原因。"""
    if detail is None:
        raise ValueError("ready frontier 无进展详情不能为空")
    code = str(detail.get("code") or "ready_frontier_no_progress")
    milestone_id = str(detail.get("most_promising_milestone_id") or "unknown")
    stale_count = int(detail.get("stale_frontier_observation_count") or 0)
    patience = int(detail.get("patience") or 0)
    ready_ids = detail.get("ready_milestone_ids")
    ready_text = ",".join(str(item) for item in ready_ids) if isinstance(ready_ids, list) else "unknown"
    return (
        f"ready frontier 连续 {stale_count}/{patience} 次评分观察无有效提升，"
        f"提前终止执行：code={code}, most_promising_milestone={milestone_id}, ready={ready_text}"
    )


def runtime_diagnostics_summary(
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
) -> JsonObject:
    """构造运行期 raw_summary 的 milestone 与质量诊断信息。"""
    if task_case is None or trajectory is None or state is None:
        raise ValueError("运行期诊断参数不能为空")
    graph = task_case.milestone_graph
    return {
        "milestone_graph_summary": build_milestone_graph_summary(graph),
        "milestone_match_attempts": list(state.match_attempts),
        "milestone_final_diagnostics": build_final_milestone_diagnostics(
            graph=graph,
            matched=state.matched_settlements,
            match_attempts=state.match_attempts,
        ),
        "runtime_quality_diagnostics": build_runtime_quality_diagnostics(task_case, trajectory),
    }


def blocked_milestone_termination_reason(detail: JsonObject) -> str:
    """根据前驱断裂诊断生成中文终止原因。"""
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


def pending_milestone_stage_results(task_case: TaskCase, state: RuntimeEvaluationState) -> list[StageEvaluationResult]:
    """为自然结束时仍未完成的 milestone 生成失败阶段报告。"""
    if task_case is None or state is None:
        raise ValueError("pending milestone stage 参数不能为空")
    graph = task_case.milestone_graph
    diagnostics = build_final_milestone_diagnostics(
        graph=graph,
        matched=state.matched_settlements,
        match_attempts=state.match_attempts,
    )
    milestones_by_id = {node.milestone_id: node for node in graph.nodes}
    results: list[StageEvaluationResult] = []
    for item in diagnostics:
        if item.get("final_state") == "matched":
            continue
        milestone_id = str(item.get("milestone_id") or "unknown")
        milestone = milestones_by_id.get(milestone_id)
        if milestone is None:
            raise ValueError(f"pending milestone 不存在: {milestone_id}")
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone_id}")
        blocker = str(item.get("blocker") or "unknown")
        ready_ever = bool(item.get("ready_ever"))
        attempt_count = int(item.get("attempt_count") or 0)
        status = StageStatus.FAIL if ready_ever or attempt_count > 0 else StageStatus.MISSING
        failure_kind = status.value
        fallback_summary = (
            f"milestone 未完成: milestone={milestone_id}, blocker={blocker}, "
            f"best_score={item.get('best_score')}, "
            f"best_boundary_step_index={item.get('best_boundary_step_index')}, "
            f"pending_predecessor_ids={item.get('pending_predecessor_ids')}"
        )
        failure_summary = str(item.get("failure_summary") or fallback_summary)
        raw_reasons = item.get("failure_reasons")
        failure_reasons = [str(reason) for reason in raw_reasons] if isinstance(raw_reasons, list) else []
        evidence = [failure_summary, *failure_reasons[1:3]]
        results.append(
            StageEvaluationResult(
                stage_id=stage_goal_key(anchor_id, milestone_id),
                milestone_id=milestone_id,
                status=status,
                stage_score=0.0,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
                dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in Dimension},
                dimension_confidence={dimension: 0.9 for dimension in Dimension},
                dimension_uncertainty={dimension: 0.1 for dimension in Dimension},
                evidence=evidence,
                diagnosis=[failure_summary],
                hard_constraints_all_pass=False,
                required_fields_missing_ratio=1.0,
                metadata={
                    **dict(item),
                    "synthetic_pending_milestone": True,
                    "failure_kind": failure_kind,
                    "stage_anchor_milestone_id": anchor_id,
                },
            )
        )
    return results


def scoring_context(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
) -> ScoringContext:
    """构造运行期评分上下文。"""
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
    """构造可审计的任务快照摘要。"""
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


def _candidate_scores_by_milestone(attempt_detail: JsonObject) -> dict[str, JsonObject]:
    raw_candidates = attempt_detail.get("candidate_scores")
    if not isinstance(raw_candidates, list):
        return {}
    candidates: dict[str, JsonObject] = {}
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        milestone_id = candidate.get("milestone_id")
        if isinstance(milestone_id, str) and milestone_id.strip():
            candidates[milestone_id] = candidate
    return candidates


def _build_ready_frontier_progress_watch(
    ready_ids: tuple[str, ...],
    observed_candidates: dict[str, JsonObject],
    step_index: int,
) -> ReadyFrontierProgressWatch:
    milestone_progress = {
        milestone_id: _ready_milestone_progress_from_candidate(milestone_id, candidate, step_index)
        for milestone_id, candidate in observed_candidates.items()
    }
    return ReadyFrontierProgressWatch(
        frontier_key=ready_ids,
        ready_since_step_index=step_index,
        last_observed_step_index=step_index,
        last_frontier_improved_step_index=step_index,
        frontier_observation_count=1,
        milestone_progress=milestone_progress,
    )


def _ready_milestone_progress_from_candidate(
    milestone_id: str,
    candidate: JsonObject,
    step_index: int,
) -> ReadyMilestoneProgress:
    score_payload = _candidate_score_payload(candidate)
    return ReadyMilestoneProgress(
        milestone_id=milestone_id,
        best_score=_clamped_score(score_payload.get("score")),
        best_status=str(score_payload.get("status") or "unknown"),
        best_boundary_step_index=_candidate_boundary_step_index(candidate),
        last_improved_step_index=step_index,
    )


def _ready_frontier_no_progress_detail(
    watch: ReadyFrontierProgressWatch,
    patience: int,
    min_delta: float,
) -> JsonObject:
    if not watch.milestone_progress:
        raise ValueError("ready frontier progress 不能为空")
    progress_items = sorted(
        watch.milestone_progress.values(),
        key=lambda item: (-item.best_score, item.milestone_id),
    )
    most_promising = progress_items[0]
    code = (
        f"milestone_no_progress:{most_promising.milestone_id}"
        if len(watch.frontier_key) == 1
        else f"ready_frontier_no_progress:{most_promising.milestone_id}"
    )
    return {
        "code": code,
        "ready_milestone_ids": list(watch.frontier_key),
        "most_promising_milestone_id": most_promising.milestone_id,
        "ready_since_step_index": watch.ready_since_step_index,
        "last_observed_step_index": watch.last_observed_step_index,
        "last_frontier_improved_step_index": watch.last_frontier_improved_step_index,
        "stale_frontier_observation_count": watch.stale_frontier_observation_count,
        "frontier_observation_count": watch.frontier_observation_count,
        "patience": patience,
        "min_delta": min_delta,
        "milestone_progress": {
            milestone_id: {
                "best_score": progress.best_score,
                "best_status": progress.best_status,
                "best_boundary_step_index": progress.best_boundary_step_index,
                "last_improved_step_index": progress.last_improved_step_index,
            }
            for milestone_id, progress in sorted(watch.milestone_progress.items())
        },
    }


def _candidate_score_payload(candidate: JsonObject) -> JsonObject:
    score = candidate.get("score")
    if not isinstance(score, dict):
        raise ValueError("candidate score 必须是 JSON 对象")
    return score


def _candidate_boundary_step_index(candidate: JsonObject) -> int | None:
    boundary = candidate.get("boundary")
    if not isinstance(boundary, dict):
        return None
    step_index = boundary.get("step_index")
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        return None
    return step_index


def _attempt_step_index(attempt_detail: JsonObject) -> int:
    step_index = attempt_detail.get("step_index")
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        raise ValueError("attempt_detail.step_index 必须是整数")
    return step_index


def _clamped_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _initial_user_message_excerpt(trajectory: Trajectory) -> str | None:
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    for step in trajectory.steps:
        if step.actor == Actor.USER and isinstance(step.content, str) and step.content.strip():
            return compact_text(step.content, 240)
    return None
