from dynsteer.graph import FINISH_NODE_ID
from dynsteer.model import (
    Constraint,
    ConstraintTarget,
    Dimension,
    Milestone,
    MilestoneGraph,
    StageEvaluationSpec,
    StageGoalSemanticKind,
    StageInterval,
    TaskCase,
)
from dynsteer.stage.goal import required_stage_goal_keys, stage_goal_key


def generate_stage_evaluation_specs(task_case: TaskCase) -> dict[str, StageEvaluationSpec]:
    """根据公共 milestone 语义生成每个真实阶段的聚焦评估维度。

    入参：
        task_case: 已包含 milestone_graph 与 stage_goals 的任务 case。
    输出：
        key 与真实 milestone stage key 对齐的 StageEvaluationSpec 字典。
    """
    graph = task_case.milestone_graph
    specs: dict[str, StageEvaluationSpec] = {}
    for milestone in graph.nodes:
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
        key = stage_goal_key(anchor_id, milestone.milestone_id)
        specs[key] = _spec_for_milestone(milestone, task_case.stage_goals.get(key, ""))
    validate_stage_evaluation_specs(graph, task_case.stage_goals, specs)
    return specs


def validate_stage_evaluation_specs(
    graph: MilestoneGraph | None, stage_goals: dict[str, str], specs: dict[str, StageEvaluationSpec]
) -> None:
    """校验阶段聚焦维度配置是否完整、合法。"""
    expected = set(required_stage_goal_keys(graph))
    actual = {key for key in specs if not key.endswith(f"->{FINISH_NODE_ID}")}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            raise ValueError(f"TaskCase 缺少 stage_evaluation_specs: {missing}")
        raise ValueError(f"TaskCase 包含多余 stage_evaluation_specs: {extra}")
    if stage_goals:
        real_stage_goals = {key for key in stage_goals if not key.endswith(f"->{FINISH_NODE_ID}")}
        if real_stage_goals != expected:
            raise ValueError("stage_evaluation_specs 需要与真实 stage_goals 使用相同 key")
    for key, spec in specs.items():
        if key.endswith(f"->{FINISH_NODE_ID}"):
            continue
        _validate_spec(key, spec)


def resolve_stage_evaluation_spec(interval: StageInterval, task_case: TaskCase) -> StageEvaluationSpec:
    """读取当前阶段的聚焦评估维度；内存 case 缺失时按公共语义即时生成。"""
    if interval.milestone_id == FINISH_NODE_ID:
        return StageEvaluationSpec(
            focus_dimensions=[Dimension.PROGRESS, Dimension.STATE_CONSISTENCY],
            dimension_rationale={
                Dimension.PROGRESS: "finish 阶段核查真实 milestone 覆盖情况",
                Dimension.STATE_CONSISTENCY: "finish 阶段核查 terminal 状态约束",
            },
        )
    key = interval.stage_id
    spec = task_case.stage_evaluation_specs.get(key)
    if spec is not None:
        _validate_spec(key, spec)
        return spec
    generated = generate_stage_evaluation_specs(task_case)
    task_case.stage_evaluation_specs = generated
    spec = generated.get(key)
    if spec is None:
        raise ValueError(f"TaskCase 缺少 stage_evaluation_specs: {key}")
    return spec


def _spec_for_milestone(milestone: Milestone, stage_goal: str) -> StageEvaluationSpec:
    dimensions: list[Dimension] = [Dimension.PROGRESS, Dimension.EFFICIENCY]
    rationale: dict[Dimension, str] = {Dimension.PROGRESS: "阶段目标完成度必须评估", Dimension.EFFICIENCY: "所有阶段都需要评估步骤成本、冗余与拖延"}
    for constraint in milestone.constraints:
        _extend_by_constraint(dimensions, rationale, constraint)
    if any(
        term in str(stage_goal or "").lower()
        for term in ("resolve", "recover", "retry", "fix issue", "failure", "exception", "修复", "恢复", "重试", "失败", "异常")
    ):
        _add_dimension(dimensions, rationale, Dimension.RECOVERY, "阶段目标包含恢复、重试或问题处理语义")
    return StageEvaluationSpec(focus_dimensions=dimensions, dimension_rationale=rationale)


def _extend_by_constraint(dimensions: list[Dimension], rationale: dict[Dimension, str], constraint: Constraint) -> None:
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    semantic_kind = str(semantics.get("kind") or "")
    metadata = constraint.metadata if isinstance(constraint.metadata, dict) else {}
    toolsandbox = metadata.get("toolsandbox") if isinstance(metadata.get("toolsandbox"), dict) else {}

    if constraint.target in {ConstraintTarget.TOOL_CALL, ConstraintTarget.TOOL_RESULT}:
        _add_dimension(dimensions, rationale, Dimension.TOOL_QUALITY, "阶段包含工具调用或工具结果约束")
    if semantic_kind == StageGoalSemanticKind.TOOL_CALL.value:
        _add_dimension(dimensions, rationale, Dimension.TOOL_QUALITY, "阶段语义要求调用指定工具")
    if bool(metadata.get("requires_tool")) or bool(toolsandbox.get("tool_trace_required")):
        _add_dimension(dimensions, rationale, Dimension.TOOL_QUALITY, "阶段 metadata 标记需要工具证据")

    if (
        constraint.target in {ConstraintTarget.STATE_SNAPSHOT, ConstraintTarget.STATE_DELTA}
        or constraint.reference_milestone_id is not None
        or semantic_kind in {StageGoalSemanticKind.SET_STATE.value, StageGoalSemanticKind.PRESERVE_STATE.value}
    ):
        _add_dimension(dimensions, rationale, Dimension.STATE_CONSISTENCY, "阶段涉及状态设置、保持或快照约束")

    if semantic_kind == StageGoalSemanticKind.EMIT_MESSAGE.value or bool(semantics.get("user_visible_required")):
        _add_dimension(dimensions, rationale, Dimension.INTERACTION_QUALITY, "阶段要求向用户解释、确认或汇报")

    safety_text = " ".join(
        str(value).lower()
        for value in [
            constraint.constraint_id,
            constraint.evaluator_hint,
            constraint.namespace,
            semantic_kind,
            metadata.get("description"),
            toolsandbox.get("snapshot_constraint"),
        ]
        if value is not None
    )
    if bool(toolsandbox.get("guardrail")) or any(
        term in safety_text for term in ("safety", "permission", "guardrail", "sensitive", "安全", "权限", "敏感")
    ):
        _add_dimension(dimensions, rationale, Dimension.SAFETY, "阶段涉及安全、权限、guardrail 或敏感状态")


def _add_dimension(
    dimensions: list[Dimension], rationale: dict[Dimension, str], dimension: Dimension, reason: str
) -> None:
    if dimension not in dimensions:
        dimensions.append(dimension)
    rationale.setdefault(dimension, reason)


def _validate_spec(key: str, spec: StageEvaluationSpec) -> None:
    if not isinstance(key, str) or not key:
        raise ValueError("stage_evaluation_specs key 必须是非空字符串")
    if spec is None or not isinstance(spec, StageEvaluationSpec):
        raise ValueError(f"stage_evaluation_specs.{key} 必须是 StageEvaluationSpec")
    dimensions = list(dict.fromkeys(spec.focus_dimensions))
    if set(dimensions) != set(spec.focus_dimensions):
        spec.focus_dimensions = dimensions
    if Dimension.PROGRESS not in dimensions or Dimension.EFFICIENCY not in dimensions:
        raise ValueError(f"stage_evaluation_specs.{key} 必须包含 progress 和 efficiency")
    for dimension in dimensions:
        if not isinstance(dimension, Dimension):
            raise ValueError(f"stage_evaluation_specs.{key} 包含非法维度: {dimension}")
