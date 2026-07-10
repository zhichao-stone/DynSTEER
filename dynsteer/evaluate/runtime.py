from __future__ import annotations

from dataclasses import dataclass, field

from dynsteer.boundary import boundary_snapshot
from dynsteer.evaluate.diagnostics import build_final_milestone_diagnostics, build_milestone_graph_summary
from dynsteer.evaluate.policy import EvaluationPolicyState, initial_evaluation_policy
from dynsteer.evaluate.quality import build_runtime_quality_diagnostics
from dynsteer.evaluate.scoring import ScoringContext
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
    TrajectoryStep,
)
from dynsteer.stage import stage_goal_key
from dynsteer.utils import compact_text

@dataclass
class RuntimeEvaluationState:
    """保存单个 case 运行期间的评估状态。"""

    weights: dict[Dimension, float]
    settlements: list[HarnessStageSettlement]
    matched_settlements: dict[str, HarnessStageSettlement]
    stage_reports: list[StageEvaluationResult]
    match_attempts: list[JsonObject]
    evaluation_policy: EvaluationPolicyState = field(default_factory=initial_evaluation_policy)
    minefield_matches: list[JsonObject] = field(default_factory=list)
    max_minefield_score: float = 0.0
    fatal_minefield: bool = False


@dataclass
class RuntimeEvaluationDecision:
    """单步运行期阶段评估决策。"""

    checkpoint: HarnessStageSettlement | None
    stage_result: StageEvaluationResult | None
    next_state: RuntimeEvaluationState
    should_stop: bool = False
    termination_code: str | None = None
    termination_reason: str | None = None
    termination_detail: JsonObject | None = None


class JudgeConfigurationError(RuntimeError):
    """LLM judge 配置缺失或不合法时抛出。"""


class HarnessTeardownError(RuntimeError):
    """benchmark session 资源释放失败时抛出。"""


def runtime_diagnostics_summary(
    task_case: TaskCase,
    trajectory: Trajectory,
    state: RuntimeEvaluationState,
) -> JsonObject:
    """构造运行期 raw_summary 的 milestone 与质量诊断信息。"""
    if task_case is None or trajectory is None or state is None:
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


def pending_required_stage_results(task_case: TaskCase, state: RuntimeEvaluationState) -> list[StageEvaluationResult]:
    """为自然结束时仍未完成的 required milestone 生成失败阶段报告。"""
    if task_case is None or state is None:
        raise ValueError("pending required stage 参数不能为空")
    graph = task_case.milestone_graph or MilestoneGraph()
    diagnostics = build_final_milestone_diagnostics(
        graph=graph,
        matched=state.matched_settlements,
        match_attempts=state.match_attempts,
    )
    milestones_by_id = {node.milestone_id: node for node in graph.nodes}
    results: list[StageEvaluationResult] = []
    for item in diagnostics:
        if item.get("required") is not True or item.get("final_state") == "matched":
            continue
        milestone_id = str(item.get("milestone_id") or "unknown")
        milestone = milestones_by_id.get(milestone_id)
        if milestone is None:
            raise ValueError(f"pending required milestone 不存在: {milestone_id}")
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone_id}")
        blocker = str(item.get("blocker") or "unknown")
        ready_ever = bool(item.get("ready_ever"))
        attempt_count = int(item.get("attempt_count") or 0)
        status = StageStatus.FAIL if ready_ever or attempt_count > 0 else StageStatus.MISSING
        failure_kind = status.value
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
                stage_id=stage_goal_key(anchor_id, milestone_id),
                milestone_id=milestone_id,
                evaluator_level=EvaluationLevel.CHEAP,
                status=status,
                stage_score=0.0,
                uncertainty=1.0,
                dimension_scores={dimension: 0.0 for dimension in Dimension},
                evidence=evidence,
                diagnosis=[f"required milestone {milestone_id} 未完成，结果高风险"],
                hard_constraints_all_pass=False,
                required_fields_missing_ratio=1.0,
                metadata={
                    **dict(item),
                    "synthetic_pending_required": True,
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


def _initial_user_message_excerpt(trajectory: Trajectory) -> str | None:
    if trajectory is None:
        raise ValueError("trajectory 不能为空")
    for step in trajectory.steps:
        if step.actor == Actor.USER and isinstance(step.content, str) and step.content.strip():
            return compact_text(step.content, 240)
    return None

