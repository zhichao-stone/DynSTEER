from dynsteer.evaluate.matching.boundary import boundary_snapshot
from dynsteer.evaluate.diagnostics import build_final_milestone_diagnostics, build_milestone_graph_summary, build_quality_diagnostics
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.model import Boundary, Dimension, EvaluationLevel, JsonObject, ReadyFrontierProgressWatch, ReadyMilestoneProgress, RuntimeEvaluationState, ScoringContext, StageEvaluationResult, StageStatus, StateSnapshot, TaskCase, ThresholdConfig, Trajectory
from dynsteer.stage import stage_goal_key
from dynsteer.utils import clamped_number

class JudgeConfigurationError(RuntimeError):
    """LLM judge 配置缺失或不合法时抛出。"""

class HarnessTeardownError(RuntimeError):
    """benchmark session 资源释放失败时抛出。"""

def update_ready_frontier_progress_watch(state: RuntimeEvaluationState, ready_ids: tuple[str, ...], attempt_detail: JsonObject, thresholds: ThresholdConfig, patience: int, min_delta: float) -> JsonObject | None:
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
    normalized_ready_ids = tuple((str(milestone_id) for milestone_id in ready_ids if str(milestone_id).strip()))
    if not normalized_ready_ids:
        state.ready_frontier_progress_watch = None
        return None
    raw_candidates = attempt_detail.get("candidate_scores")
    candidate_by_id = (
        {
            str(candidate["milestone_id"]): candidate
            for candidate in raw_candidates
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("milestone_id"), str)
                and candidate.get("milestone_id").strip()
            )
        }
        if isinstance(raw_candidates, list)
        else {}
    )
    observed_candidates = {
        milestone_id: candidate_by_id[milestone_id]
        for milestone_id in normalized_ready_ids
        if milestone_id in candidate_by_id
        and isinstance(candidate_by_id[milestone_id].get("score"), dict)
    }
    if not observed_candidates:
        return None
    step_index = attempt_detail.get("step_index")
    if not isinstance(step_index, int):
        raise ValueError("attempt_detail.step_index 必须是整数")
    watch = state.ready_frontier_progress_watch
    if watch is None or watch.frontier_key != normalized_ready_ids:
        state.ready_frontier_progress_watch = ReadyFrontierProgressWatch(
            frontier_key=normalized_ready_ids,
            ready_since_step_index=step_index,
            last_observed_step_index=step_index,
            last_frontier_improved_step_index=step_index,
            frontier_observation_count=1,
            milestone_progress={
                milestone_id: _ready_milestone_progress_from_candidate(
                    milestone_id,
                    candidate,
                    step_index,
                )
                for milestone_id, candidate in observed_candidates.items()
            },
        )
        return None
    watch.frontier_observation_count += 1
    watch.last_observed_step_index = step_index
    frontier_improved = False
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
    if any((progress.best_score >= thresholds.pass_threshold for progress in watch.milestone_progress.values())):
        return None
    progress_items = sorted(watch.milestone_progress.values(), key=lambda item: (-item.best_score, item.milestone_id))
    most_promising = progress_items[0]
    code_prefix = "milestone_no_progress" if len(watch.frontier_key) == 1 else "ready_frontier_no_progress"
    progress_payload = {
        milestone_id: {
            "best_score": progress.best_score,
            "best_status": progress.best_status,
            "best_boundary_step_index": progress.best_boundary_step_index,
            "last_improved_step_index": progress.last_improved_step_index,
        }
        for milestone_id, progress in sorted(watch.milestone_progress.items())
    }
    return {
        "code": f"{code_prefix}:{most_promising.milestone_id}",
        "ready_milestone_ids": list(watch.frontier_key),
        "most_promising_milestone_id": most_promising.milestone_id,
        "ready_since_step_index": watch.ready_since_step_index,
        "last_observed_step_index": watch.last_observed_step_index,
        "last_frontier_improved_step_index": watch.last_frontier_improved_step_index,
        "stale_frontier_observation_count": watch.stale_frontier_observation_count,
        "frontier_observation_count": watch.frontier_observation_count,
        "patience": patience,
        "min_delta": min_delta,
        "milestone_progress": progress_payload,
    }

def ready_frontier_no_progress_termination_reason(detail: JsonObject) -> str:
    """根据 ready frontier 无进展详情生成终止原因。"""
    code = str(detail.get("code") or "ready_frontier_no_progress")
    milestone_id = str(detail.get("most_promising_milestone_id") or "unknown")
    stale_count = int(detail.get("stale_frontier_observation_count") or 0)
    patience = int(detail.get("patience") or 0)
    ready_ids = detail.get("ready_milestone_ids")
    ready_text = ",".join((str(item) for item in ready_ids)) if isinstance(ready_ids, list) else "unknown"
    return f"ready frontier 连续 {stale_count}/{patience} 次评分观察无有效提升，提前终止执行：code={code}, most_promising_milestone={milestone_id}, ready={ready_text}"

def runtime_diagnostics_summary(task_case: TaskCase, trajectory: Trajectory, state: RuntimeEvaluationState) -> JsonObject:
    """构造运行期 raw_summary 的 milestone 与质量诊断信息。"""
    graph = task_case.milestone_graph
    return {
        "milestone_graph_summary": build_milestone_graph_summary(graph),
        "milestone_match_attempts": list(state.match_attempts),
        "milestone_final_diagnostics": build_final_milestone_diagnostics(
            graph=graph,
            matched=state.matched_settlements,
            match_attempts=state.match_attempts,
            termination=state.evaluation_termination,
        ),
        "runtime_quality_diagnostics": build_quality_diagnostics(list(trajectory.steps)),
    }

def selected_candidate_from_attempt(attempt: JsonObject) -> JsonObject | None:
    """从运行期匹配记录中读取被选中的候选项。"""
    raw_candidates = attempt.get("candidate_scores")
    if not isinstance(raw_candidates, list):
        return None
    return next((candidate for candidate in raw_candidates if isinstance(candidate, dict) and candidate.get("selected") is True), None)

def blocked_milestone_termination_reason(detail: JsonObject) -> str:
    """根据前驱断裂诊断生成终止原因。"""
    selected_candidate = selected_candidate_from_attempt(detail)
    step_id = detail.get("step_id")
    milestone_id = str(selected_candidate.get("milestone_id") if selected_candidate is not None else "unknown")
    missing = detail.get("missing_predecessors")
    missing_text = ",".join((str(item) for item in missing)) if isinstance(missing, list) else "unknown"
    score = selected_candidate.get("score") if selected_candidate is not None else None
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
    return f"当前 step={step_id} 命中 milestone={milestone_id}，但前驱 milestone={missing_text} 未匹配；当前 milestone 证据={evidence_text}；前驱诊断={predecessor_text}"

def pending_milestone_stage_results(task_case: TaskCase, state: RuntimeEvaluationState) -> list[StageEvaluationResult]:
    """为自然结束时仍未完成的 milestone 生成失败阶段报告。"""
    graph = task_case.milestone_graph
    diagnostics = build_final_milestone_diagnostics(graph=graph, matched=state.matched_settlements, match_attempts=state.match_attempts, termination=state.evaluation_termination)
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
                dimension_levels={
                    dimension: EvaluationLevel.CHEAP for dimension in Dimension
                },
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

def scoring_context(task_case: TaskCase, trajectory: Trajectory, matched: dict[str, HarnessStageSettlement]) -> ScoringContext:
    """构造运行期评分上下文。"""
    matched_boundaries: dict[str, Boundary] = {}
    matched_snapshots: dict[str, StateSnapshot] = {}
    initial_state = task_case.initial_state
    if isinstance(initial_state, dict):
        namespaces = initial_state.get("namespaces")
        if isinstance(namespaces, dict):
            matched_snapshots["initial"] = StateSnapshot(snapshot_id="initial", after_step_id="initial", after_step_index=0, namespaces={str(key): value for key, value in namespaces.items()})
    for milestone_id, settlement in matched.items():
        if settlement.boundary_step_index is None:
            continue
        boundary = Boundary(boundary_id=settlement.boundary_id or f"matched:{milestone_id}", step_index=settlement.boundary_step_index, snapshot_id=None, reason="matched_milestone")
        matched_boundaries[milestone_id] = boundary
        snapshot = boundary_snapshot(boundary, trajectory.snapshots)
        if snapshot is not None:
            matched_snapshots[milestone_id] = snapshot
    return ScoringContext(task_case=task_case, matched_boundaries=matched_boundaries, matched_snapshots=matched_snapshots)

def task_case_snapshot(task_case: TaskCase) -> JsonObject:
    """构造可审计的任务快照摘要。"""
    return {
        "case_id": str(task_case.case_id),
        "task_id": task_case.task_id,
        "task_description": task_case.task_description,
        "task_types": [item.value for item in task_case.task_types],
        "scenario_name": task_case.metadata.get("scenario_name"),
        "categories": list(task_case.metadata.get("categories", [])),
        "runtime_initial_state_source": task_case.metadata.get(
            "runtime_initial_state_source"
        ),
        "runtime_initial_state_summary": task_case.metadata.get(
            "runtime_initial_state_summary"
        ),
    }

def _ready_milestone_progress_from_candidate(milestone_id: str, candidate: JsonObject, step_index: int) -> ReadyMilestoneProgress:
    score_payload = candidate.get("score")
    if not isinstance(score_payload, dict):
        raise ValueError("candidate score 必须是 JSON 对象")
    boundary = candidate.get("boundary")
    boundary_step_index = None
    if isinstance(boundary, dict):
        raw_boundary_step_index = boundary.get("step_index")
        if isinstance(raw_boundary_step_index, int):
            boundary_step_index = raw_boundary_step_index
    return ReadyMilestoneProgress(milestone_id=milestone_id, best_score=clamped_number(score_payload.get("score")), best_status=str(score_payload.get("status") or "unknown"), best_boundary_step_index=boundary_step_index, last_improved_step_index=step_index)
