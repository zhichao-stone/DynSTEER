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


def trajectory_step_to_dict(step: TrajectoryStep) -> JsonObject:
    """将单个轨迹步骤转换为 raw_summary 可写入的诊断字典。

    Args:
        step: Agent 轨迹中的单步事件。

    Returns:
        JSON 可序列化的轨迹步骤诊断信息。
    """
    if step is None:
        raise ValueError("step 不能为空")
    tool_call = None
    if step.tool_call is not None:
        tool_call = {
            "name": step.tool_call.name,
            "arguments": dict(step.tool_call.arguments),
        }
    tool_result = None
    if step.tool_result is not None:
        tool_result = {
            "success": step.tool_result.success,
            "content": step.tool_result.content,
            "exception": step.tool_result.exception,
        }
    return {
        "step_id": step.step_id,
        "index": step.index,
        "actor": step.actor.value,
        "event_type": step.event_type.value,
        "timestamp": step.timestamp,
        "content": step.content,
        "tool_call": tool_call,
        "tool_result": tool_result,
        "state_delta_refs": list(step.state_delta_refs),
        "cost": {
            "tokens": step.cost.tokens,
            "latency_ms": step.cost.latency_ms,
        },
        "raw": dict(step.raw),
    }


def build_stage_trace(
    trajectory: Trajectory,
    interval: StageInterval,
) -> JsonObject:
    """序列化当前阶段轨迹诊断信息。

    Args:
        trajectory: 当前运行期已采集的 Agent 轨迹。
        interval: 当前阶段区间。

    Returns:
        当前阶段左开右闭区间内的轨迹步骤和范围摘要。
    """
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
    """将单条约束评分转换为诊断字典。

    Args:
        score: 单条约束评分结果。

    Returns:
        JSON 可序列化的约束评分详情。
    """
    if score is None:
        raise ValueError("score 不能为空")
    return {
        "constraint_id": score.constraint_id,
        "score": score.score,
        "missing": score.missing,
        "evidence": list(score.evidence),
        "actual": score.actual,
    }


def milestone_score_to_dict(score: MilestoneScore) -> JsonObject:
    """将 milestone 评分转换为诊断字典。

    Args:
        score: milestone 评分结果。

    Returns:
        JSON 可序列化的 milestone 匹配评分详情。
    """
    if score is None:
        raise ValueError("score 不能为空")
    return {
        "milestone_id": score.milestone_id,
        "boundary_id": score.boundary_id,
        "score": score.score,
        "status": score.status.value,
        "evidence": list(score.evidence),
        "missing_ratio": score.missing_ratio,
        "hard_constraints_all_pass": score.hard_constraints_all_pass,
        "constraint_scores": [constraint_score_to_dict(item) for item in score.constraint_scores],
    }


def milestone_summary_to_dict(milestone: Milestone) -> JsonObject:
    """将 milestone 定义转换为简要诊断字典。

    Args:
        milestone: 当前命中的 milestone。

    Returns:
        milestone 的基础定义摘要。
    """
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
    """将约束定义转换为轻量诊断摘要。

    Args:
        constraint: milestone 或 minefield 中的单条约束。

    Returns:
        可写入 raw_summary 的约束摘要，避免输出完整大字段。
    """
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
    """构造运行期 milestone 图摘要。

    Args:
        graph: 当前任务的 milestone DAG。

    Returns:
        包含节点、边、required/optional 计数的诊断摘要。
    """
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
    """将候选边界转换为诊断字典。

    Args:
        boundary: 触发 milestone 的候选边界。

    Returns:
        JSON 可序列化的边界详情。
    """
    if boundary is None:
        raise ValueError("boundary 不能为空")
    return {
        "boundary_id": boundary.boundary_id,
        "step_index": boundary.step_index,
        "step_id": boundary.step_id,
        "snapshot_id": boundary.snapshot_id,
        "reason": boundary.reason,
    }


def build_milestone_candidate_detail(
    milestone: Milestone,
    boundary: Boundary | None,
    score: MilestoneScore | None,
    selected: bool,
    reject_reason: str | None,
) -> JsonObject:
    """构造单个 ready milestone 在候选边界上的评分诊断。

    Args:
        milestone: 当前尝试匹配的 milestone。
        boundary: 当前候选边界；被起点过滤时可为空。
        score: 当前候选评分；被起点过滤时可为空。
        selected: 该候选是否最终成为 checkpoint。
        reject_reason: 未选中原因。

    Returns:
        单个候选评分详情。
    """
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
    """构造运行期 milestone checkpoint 的匹配诊断详情。

    Args:
        graph: 当前任务的 milestone DAG。
        matched: 命中前已经结算的 milestone 映射。
        milestone: 当前命中的 milestone。
        boundary: 当前命中的候选边界。
        milestone_score: 当前边界上的 milestone 评分。
        ready_milestone_ids_before_match: 命中前可被匹配的 milestone ID 列表。

    Returns:
        可写入 settlement metadata 的 milestone 匹配诊断详情。
    """
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
    """汇总每个 milestone 在运行结束时的匹配状态。

    Args:
        graph: 当前任务 milestone DAG。
        matched: 已经结算的 milestone 映射。
        match_attempts: 运行期逐 step 匹配尝试诊断。

    Returns:
        每个 milestone 的最终诊断列表。
    """
    if graph is None or matched is None or match_attempts is None:
        raise ValueError("最终 milestone 诊断参数不能为空")
    diagnostics: list[JsonObject] = []
    for node in graph.nodes:
        candidate_entries: list[JsonObject] = []
        ready_ever = False
        for attempt in match_attempts:
            ready_ids = attempt.get("ready_before")
            if isinstance(ready_ids, list) and node.milestone_id in ready_ids:
                ready_ever = True
            raw_candidates = attempt.get("candidate_scores")
            if not isinstance(raw_candidates, list):
                continue
            for candidate in raw_candidates:
                if isinstance(candidate, dict) and candidate.get("milestone_id") == node.milestone_id:
                    candidate_entries.append(candidate)

        scored_entries = [
            entry
            for entry in candidate_entries
            if isinstance(entry.get("score"), dict)
        ]
        best_entry = None
        if scored_entries:
            best_entry = max(scored_entries, key=lambda item: float(item["score"].get("score", 0.0)))
        last_entry = candidate_entries[-1] if candidate_entries else None
        pending_predecessors = [item for item in node.dependency_predecessor_ids if item not in matched]

        if node.milestone_id in matched:
            settlement = matched[node.milestone_id]
            diagnostics.append(
                {
                    "milestone_id": node.milestone_id,
                    "required": node.required,
                    "dependency_predecessor_ids": list(node.dependency_predecessor_ids),
                    "stage_anchor_milestone_id": node.stage_anchor_predecessor_id,
                    "final_state": "matched",
                    "ready_ever": True,
                    "attempt_count": len(candidate_entries),
                    "blocker": None,
                    "settlement_id": settlement.settlement_id,
                    "boundary_step_index": settlement.boundary_step_index,
                    "stage_start_boundary_step_index": settlement.metadata.get("stage_start_boundary_step_index"),
                    "stage_start_step_index": settlement.start_step_index,
                    "stage_end_step_index": settlement.end_step_index,
                    "best_score": best_entry["score"]["score"] if best_entry is not None else settlement.score,
                    "best_status": best_entry["score"]["status"] if best_entry is not None else settlement.status,
                    "best_boundary_step_index": (
                        best_entry["boundary"]["step_index"]
                        if best_entry is not None and isinstance(best_entry.get("boundary"), dict)
                        else settlement.boundary_step_index
                    ),
                    "last_reject_reason": None,
                    "pending_predecessor_ids": [],
                }
            )
            continue

        if candidate_entries:
            blocker = "attempted_but_not_pass"
        elif pending_predecessors:
            blocker = "predecessor_not_matched"
        elif ready_ever:
            blocker = "ready_without_candidate"
        else:
            blocker = "not_ready"
        diagnostics.append(
            {
                "milestone_id": node.milestone_id,
                "required": node.required,
                "dependency_predecessor_ids": list(node.dependency_predecessor_ids),
                "stage_anchor_milestone_id": node.stage_anchor_predecessor_id,
                "final_state": "pending",
                "ready_ever": ready_ever,
                "attempt_count": len(candidate_entries),
                "blocker": blocker,
                "settlement_id": None,
                "boundary_step_index": None,
                "stage_start_boundary_step_index": None,
                "stage_start_step_index": None,
                "stage_end_step_index": None,
                "best_score": best_entry["score"]["score"] if best_entry is not None else None,
                "best_status": best_entry["score"]["status"] if best_entry is not None else None,
                "best_boundary_step_index": (
                    best_entry["boundary"]["step_index"]
                    if best_entry is not None and isinstance(best_entry.get("boundary"), dict)
                    else None
                ),
                "last_reject_reason": last_entry.get("reject_reason") if isinstance(last_entry, dict) else None,
                "pending_predecessor_ids": pending_predecessors,
            }
        )
    return diagnostics


def build_finish_matching_detail(
    graph: MilestoneGraph,
    matched: dict[str, HarnessStageSettlement],
) -> JsonObject:
    """构造 finish 阶段的 milestone 匹配收尾诊断详情。

    Args:
        graph: 当前任务的 milestone DAG。
        matched: 已经结算的 milestone 映射。

    Returns:
        finish 阶段的已命中和未命中 milestone 摘要。
    """
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
