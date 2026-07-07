from __future__ import annotations

from dataclasses import dataclass

from dynsteer.boundary import candidate_boundary_for_current_step
from dynsteer.config import MatchConfig
from dynsteer.evaluate.diagnostics import (
    build_milestone_candidate_detail,
    boundary_to_dict,
    milestone_score_to_dict,
    milestone_summary_to_dict,
    trajectory_step_to_dict,
)
from dynsteer.harness.model import HarnessStageSettlement
from dynsteer.evaluate.score import GeneralScorer, ScoringContext, get_effective_scorer
from dynsteer.model import (
    Boundary,
    JsonObject,
    Milestone,
    MilestoneGraph,
    MilestoneMapping,
    MilestoneMappingItem,
    MilestoneScore,
    StageStatus,
    TaskCase,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.graph import START_NODE_ID, build_adjacency, topological_order


@dataclass(frozen=True)
class MilestoneStepAnalysis:
    """当前运行期 step 的 milestone 命中分析结果。"""

    hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    attempt_detail: JsonObject | None = None
    blocked_detail: JsonObject | None = None


def _best_candidate(
    milestone_id: str,
    boundaries: list[Boundary],
    score_matrix: dict[tuple[str, str], MilestoneScore],
    min_step_index: int,
    config: MatchConfig,
) -> tuple[Boundary, MilestoneScore] | None:
    candidates: list[tuple[Boundary, MilestoneScore]] = []
    for boundary in boundaries:
        if boundary.step_index < min_step_index:
            continue
        score = score_matrix.get((milestone_id, boundary.boundary_id))
        if score is None or score.score < config.candidate_min_score:
            continue
        candidates.append((boundary, score))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[1].score, -item[0].step_index))


def match_milestones(
    graph: MilestoneGraph,
    boundaries: list[Boundary],
    score_matrix: dict[tuple[str, str], MilestoneScore],
    config: MatchConfig,
) -> MilestoneMapping:
    """将 milestone DAG 贪心匹配到候选边界。

    Args:
        graph: milestone DAG。
        boundaries: 候选边界。
        score_matrix: `(milestone_id, boundary_id)` 到评分的矩阵。
        config: 匹配阈值配置。

    Returns:
        milestone 到边界的匹配结果。
    """
    if graph is None or boundaries is None or score_matrix is None or config is None:
        raise ValueError("match_milestones 入参不能为空")
    node_by_id = {node.milestone_id: node for node in graph.nodes}
    assignments: dict[str, MilestoneMappingItem] = {}
    missing_required: list[str] = []
    evidence: list[str] = []

    predecessors, successors = build_adjacency({node.milestone_id for node in graph.nodes}, graph.edges)
    order, _ = topological_order(predecessors, successors)

    for milestone_id in order:
        milestone = node_by_id.get(milestone_id)
        if milestone is None:
            continue
        pred_steps = [
            assignments[pred].boundary_step_index
            for pred in predecessors.get(milestone_id, [])
            if pred in assignments
        ]
        min_step = max(pred_steps) if pred_steps else min((boundary.step_index for boundary in boundaries), default=0)
        candidate = _best_candidate(milestone_id, boundaries, score_matrix, min_step, config)
        if candidate is None:
            if milestone.required:
                missing_required.append(milestone_id)
            evidence.append(f"milestone {milestone_id} 未找到满足阈值的候选边界")
            continue
        boundary, score = candidate
        assignments[milestone_id] = MilestoneMappingItem(
            milestone_id=milestone_id,
            boundary_id=boundary.boundary_id,
            boundary_step_index=boundary.step_index,
            score=score,
        )
        evidence.append(f"milestone {milestone_id} 匹配到边界 {boundary.boundary_id}")

    if assignments:
        objective = sum(item.score.score for item in assignments.values()) / len(assignments)
    else:
        objective = 0.0
    objective -= config.missing_required_penalty * len(missing_required)
    return MilestoneMapping(
        assignments=assignments,
        missing_required=missing_required,
        objective=objective,
        evidence=evidence,
    )


def milestone_score_matrix(
    graph: MilestoneGraph,
    boundaries: list[Boundary],
    trajectory: Trajectory,
    scorer: GeneralScorer | None = None,
    context: ScoringContext | None = None,
) -> dict[tuple[str, str], MilestoneScore]:
    """构造 milestone 与候选边界的评分矩阵。

    Args:
        graph: milestone DAG。
        boundaries: 候选边界。
        trajectory: Agent 轨迹。
        scorer: 可选评分器；为空时使用通用评分器。
        context: 可选评分上下文。

    Returns:
        `(milestone_id, boundary_id)` 到 milestone 评分的矩阵。
    """
    if graph is None or boundaries is None or trajectory is None:
        raise ValueError("milestone_score_matrix 入参不能为空")
    matrix: dict[tuple[str, str], MilestoneScore] = {}
    for milestone in graph.nodes:
        for boundary in boundaries:
            matrix[(milestone.milestone_id, boundary.boundary_id)] = get_effective_scorer(scorer).score_milestone(
                milestone,
                boundary,
                trajectory,
                trajectory.snapshots,
                context=context,
            )
    return matrix


def ready_milestones(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> list[Milestone]:
    """返回前驱已命中、尚未结算的可命中 milestone。

    Args:
        graph: milestone DAG。
        matched: 已结算 milestone 到结算节点的映射。

    Returns:
        当前可命中的 milestone 列表。
    """
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
    """返回 milestone 阶段的 anchor ID 与 exclusive boundary。

    Args:
        graph: milestone DAG。
        milestone_id: 目标 milestone ID。
        matched: 已结算 milestone 到结算节点的映射。
        trajectory: 当前轨迹。

    Returns:
        `(anchor_id, start_boundary_step_index)`。
    """
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
    """分析当前 step 是否命中 ready milestone 或触发 blocked milestone 诊断。

    Args:
        task_case: 当前任务定义。
        trajectory: 已包含当前 step 的运行期轨迹。
        step: 当前新增 step。
        matched: 已结算 milestone 到结算节点的映射。
        scorer: 可选评分器；为空时使用通用评分器。
        context: 可选评分上下文。

    Returns:
        当前 step 的 ready hit、匹配尝试诊断和 blocked 诊断。
    """
    if task_case is None or trajectory is None or step is None or matched is None:
        raise ValueError("milestone step 分析参数不能为空")
    graph = task_case.milestone_graph
    if graph is None or not graph.nodes:
        return MilestoneStepAnalysis()

    boundary = candidate_boundary_for_current_step(trajectory, step)
    node_by_id = {node.milestone_id: node for node in graph.nodes}
    predecessor_map = {node.milestone_id: list(node.dependency_predecessor_ids) for node in graph.nodes}
    ready = ready_milestones(graph, matched)
    ready_ids = {milestone.milestone_id for milestone in ready}
    matched_ids = set(matched)
    effective_scorer = get_effective_scorer(scorer)

    ready_candidate_details: list[JsonObject] = []
    ready_hit: tuple[Milestone, Boundary, MilestoneScore] | None = None
    for milestone in ready:
        _, predecessor_start = stage_start_for_milestone(graph, milestone.milestone_id, matched, trajectory)
        if boundary.step_index <= predecessor_start and predecessor_start > 0:
            ready_candidate_details.append(
                build_milestone_candidate_detail(
                    milestone=milestone,
                    boundary=boundary,
                    score=None,
                    selected=False,
                    reject_reason="boundary_not_after_predecessor",
                )
            )
            continue
        score = effective_scorer.score_milestone(
            milestone,
            boundary,
            trajectory,
            trajectory.snapshots,
            context=context,
        )
        ready_candidate_details.append(
            build_milestone_candidate_detail(
                milestone=milestone,
                boundary=boundary,
                score=score,
                selected=False,
                reject_reason=None if score.status == StageStatus.PASS else "status_not_pass",
            )
        )
        if score.status != StageStatus.PASS:
            continue
        if ready_hit is None or score.score > ready_hit[2].score:
            ready_hit = (milestone, boundary, score)

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

    blocked_best: tuple[Milestone, Boundary, MilestoneScore, list[str]] | None = None
    blocked_candidate_details: list[JsonObject] = []
    for milestone in graph.nodes:
        if milestone.milestone_id in matched_ids or milestone.milestone_id in ready_ids:
            continue
        missing_predecessors = [
            predecessor_id
            for predecessor_id in predecessor_map.get(milestone.milestone_id, [])
            if predecessor_id not in matched_ids
        ]
        if not missing_predecessors:
            continue
        score = effective_scorer.score_milestone(
            milestone,
            boundary,
            trajectory,
            trajectory.snapshots,
            context=context,
        )
        blocked_candidate_details.append(
            build_milestone_candidate_detail(
                milestone=milestone,
                boundary=boundary,
                score=score,
                selected=False,
                reject_reason=None if score.status == StageStatus.PASS else "status_not_pass",
            )
        )
        if score.status != StageStatus.PASS:
            continue
        if blocked_best is None or score.score > blocked_best[2].score:
            blocked_best = (milestone, boundary, score, missing_predecessors)

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
        predecessor_candidates: list[JsonObject] = []
        predecessor_score = effective_scorer.score_milestone(
            predecessor,
            boundary,
            trajectory,
            trajectory.snapshots,
            context=context,
        )
        predecessor_candidates.append(
            build_milestone_candidate_detail(
                milestone=predecessor,
                boundary=boundary,
                score=predecessor_score,
                selected=False,
                reject_reason=None if predecessor_score.status == StageStatus.PASS else "status_not_pass",
            )
        )
        best_predecessor = None
        scored_predecessors = [
            item for item in predecessor_candidates if isinstance(item.get("score"), dict)
        ]
        if scored_predecessors:
            best_predecessor = max(scored_predecessors, key=lambda item: float(item["score"].get("score", 0.0)))
        predecessor_diagnostics.append(
            {
                "milestone_id": predecessor_id,
                "missing_node": False,
                "candidate_scores": predecessor_candidates,
                "best_candidate": best_predecessor,
            }
        )

    blocked_detail: JsonObject = {
        "diagnostic_type": "blocked_milestone_hit",
        "step_index": step.index,
        "step_id": step.step_id,
        "current_step": trajectory_step_to_dict(step),
        "matched_before": sorted(matched_ids),
        "ready_before": sorted(ready_ids),
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
    """构造当前 step 的 ready milestone 尝试诊断。"""
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
    """将候选列表中最终命中的候选标记为 selected。"""
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
