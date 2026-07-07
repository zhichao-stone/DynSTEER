from __future__ import annotations

from collections.abc import Callable
import json

from dynsteer.llm import BaseLLM, LLMMessage
from dynsteer.model import (
    Constraint,
    JsonObject,
    JsonValue,
    Milestone,
    MilestoneGraph,
    StageGoalSemanticKind,
    StageInterval,
    TaskCase,
)
from dynsteer.utils import enum_value


StageGoalLLMProvider = Callable[[], BaseLLM | None]


def stage_goal_key(anchor_milestone_id: str, milestone_id: str) -> str:
    """生成阶段目标映射 key。"""
    if not anchor_milestone_id or not milestone_id:
        raise ValueError("anchor_milestone_id 和 milestone_id 不能为空")
    return f"{anchor_milestone_id}->{milestone_id}"


def required_stage_goal_keys(graph: MilestoneGraph) -> list[str]:
    """返回当前 graph 需要生成 stage_goal 的稳定 key 列表。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    keys: list[str] = []
    for milestone in graph.nodes:
        if milestone is None or not milestone.milestone_id:
            raise ValueError("milestone 不能为空")
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
        keys.append(stage_goal_key(anchor_id, milestone.milestone_id))
    return keys


def generate_stage_goals(
    task_case: TaskCase,
    mode: str = "auto",
    llm_provider: StageGoalLLMProvider | None = None,
) -> dict[str, str]:
    """集中生成 TaskCase 的 stage_goals。

    Args:
        task_case: 已完成 milestone graph enrichment 的任务定义。
        mode: 生成模式，支持 auto、semantic、llm、stored。
        llm_provider: 仅在需要 LLM fallback 时调用的 provider。

    Returns:
        完整覆盖 milestone graph 的 stage_goals。
    """
    # 规范化生成模式；None 或空字符串沿用 auto。
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode == "stored":
        validate_stage_goals(task_case.milestone_graph, task_case.stage_goals)
        return dict(task_case.stage_goals)
    if normalized_mode not in {"auto", "semantic", "llm"}:
        raise ValueError(f"未知 stage_goal_generation 模式: {mode}")

    # auto/semantic 优先使用公共语义 IR 生成，不读取任何 benchmark 私有 metadata。
    if normalized_mode in {"auto", "semantic"}:
        semantic_goals: dict[str, str] = {}
        graph = task_case.milestone_graph
        if graph is None:
            raise ValueError("TaskCase 缺少 milestone_graph")
        semantic_complete = True
        for milestone in graph.nodes:
            anchor_id = milestone.stage_anchor_predecessor_id
            if not isinstance(anchor_id, str) or not anchor_id:
                raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
            key = stage_goal_key(anchor_id, milestone.milestone_id)
            if not milestone.constraints:
                semantic_complete = False
                break
            pieces: list[str] = []
            for constraint in milestone.constraints:
                # 单个 constraint 缺失公共 IR 时，auto 回退 LLM，semantic 直接报错。
                piece = _constraint_goal_text_from_semantics(constraint)
                if piece is None:
                    semantic_complete = False
                    break
                pieces.append(piece)
            if not semantic_complete:
                break
            objective = f"Complete milestone {milestone.milestone_id}: {milestone.description or milestone.name}."
            goal_text = " ".join([objective, *pieces])
            semantic_goals[key] = goal_text
        if semantic_complete:
            validate_stage_goals(task_case.milestone_graph, semantic_goals)
            return semantic_goals
        if normalized_mode == "semantic":
            raise ValueError("stage_goal_generation=semantic 需要所有约束提供 stage_goal_semantics")

    # 无法用语义 IR 完整覆盖时，按配置回退到 LLM 生成。
    if llm_provider is None:
        raise ValueError("stage_goal_generation=llm 需要配置 LLM provider")
    llm = llm_provider()
    if llm is None:
        raise ValueError("stage_goal_generation=llm 需要配置 DYNSTEER_JUDGE_PROVIDER")
    return generate_stage_goals_with_llm(task_case, llm)


def build_stage_goal_generation_prompt(task_case: TaskCase) -> str:
    """构造一次性生成全部 stage_goal 的 LLM prompt。"""
    if task_case is None:
        raise ValueError("task_case 不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    payload = {
        "task_description": task_case.task_description,
        "milestone_graph": _graph_prompt_json(graph),
        "required_stage_goal_keys": required_stage_goal_keys(graph),
        "output_schema": {
            "stage_goals": {
                "milestone_id_1->milestone_id_2": "当前阶段自然语言目标",
            }
        },
    }
    return (
        "你是 DynSTEER 阶段目标生成器。请只返回一个 JSON 对象，不要输出 Markdown 或解释。\n"
        "stage_goals 必须是对象，key 必须与 required_stage_goal_keys 完全一致，value 必须是非空字符串。\n"
        "milestone_graph.edges 已包含 __start__ 与 __finish__ 增强边；nodes[].anchor 表示当前阶段目标的起点锚点。\n"
        "每个 value 只描述当前 `(anchor, milestone)` 阶段目标，不输出整任务目标；"
        "不要把 anchor 自身的完成动作当作当前阶段要求；可以引用 anchor 作为阶段上下文。\n"
        "对依赖上下文解析的阶段，应把解析所需证据纳入当前 stage goal，"
        "不要要求固定字面消息由特定角色发出。\n"
        f"输入 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def generate_stage_goals_with_llm(task_case: TaskCase, llm: BaseLLM) -> dict[str, str]:
    """用 LLM 一次性生成并校验全部 stage_goal 字典。"""
    if task_case is None or llm is None:
        raise ValueError("task_case 和 llm 不能为空")
    raw_text = llm.chat(
        [
            LLMMessage(role="system", content="你是 DynSTEER 阶段目标生成器，只返回 JSON 对象。"),
            LLMMessage(role="user", content=build_stage_goal_generation_prompt(task_case)),
        ],
        temperature=0,
    )
    stage_goals = _parse_stage_goal_from_resp(raw_text)
    validate_stage_goals(task_case.milestone_graph, stage_goals)
    return stage_goals


def validate_stage_goals(graph: MilestoneGraph | None, stage_goals: dict[str, str]) -> None:
    """校验 stage_goals 是否完整覆盖当前 graph。"""
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    if stage_goals is None:
        raise ValueError("stage_goals 不能为空")
    required = set(required_stage_goal_keys(graph))
    actual = set(stage_goals)
    if actual != required:
        missing = sorted(required - actual)
        extra = sorted(actual - required)
        if missing:
            raise ValueError(f"TaskCase 缺少预生成 stage_goal: {missing}")
        raise ValueError(f"TaskCase 包含多余 stage_goal: {extra}")
    for key, value in stage_goals.items():
        if not isinstance(key, str) or not key:
            raise ValueError("stage_goals key 必须是非空字符串")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"stage_goal 不能为空: {key}")


def resolve_stage_goal(interval: StageInterval, task_case: TaskCase) -> str:
    """从 TaskCase.stage_goals 读取当前阶段目标。"""
    if interval is None or task_case is None:
        raise ValueError("阶段目标参数不能为空")
    if interval.milestone_id is None:
        return "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"
    if not isinstance(interval.stage_anchor_milestone_id, str) or not interval.stage_anchor_milestone_id:
        raise ValueError(f"milestone 阶段缺少 stage_anchor_milestone_id: {interval.milestone_id}")
    key = stage_goal_key(interval.stage_anchor_milestone_id, interval.milestone_id)
    stage_goal = task_case.stage_goals.get(key)
    if isinstance(stage_goal, str) and stage_goal.strip():
        return stage_goal
    raise ValueError(f"TaskCase 缺少预生成 stage_goal: {key}")


def _parse_stage_goal_from_resp(raw: str) -> dict[str, str]:
    """从 LLM 返回的 JSON 字符串中解析 stage_goals 字典。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM stage_goal 返回不能为空")
    try:
        payload = json.loads(raw).get("stage_goals")
    except json.JSONDecodeError as exc:
        raise ValueError("LLM stage_goal 返回必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM stage_goal 返回必须是 JSON 对象")
    
    result: dict[str, str] = {}
    for key, stage_goal in payload.items():
        if not isinstance(key, str) or not key:
            continue  # 忽略无效 key
        if not isinstance(stage_goal, str) or not stage_goal.strip():
            continue  # 忽略无效 stage_goal
        result[key] = stage_goal
    return result


def _constraint_goal_text_from_semantics(constraint: Constraint) -> str | None:
    """根据单个约束的公共语义 IR 生成 stage_goal 文本片段。"""
    # 缺失或格式错误表示该约束无法走 deterministic 语义生成。
    semantics = constraint.stage_goal_semantics
    if semantics is None:
        return None
    if not isinstance(semantics, dict):
        return None
    try:
        kind = enum_value(StageGoalSemanticKind, semantics.get("kind"), "stage_goal_semantics.kind")
    except ValueError:
        return None
    # 状态设置类目标依赖结构化 scorer evidence，不要求额外自然语言复述。
    if kind == StageGoalSemanticKind.SET_STATE:
        namespace = _semantic_text(semantics.get("namespace"), "state")
        expected = json.dumps(semantics.get("expected"), ensure_ascii=False, sort_keys=True)
        return (
            f"Make or verify {namespace} state satisfies {expected}. "
            "Use structured scorer evidence for this state requirement; no separate user-facing restatement is required unless another message requirement says so."
        )
    # 状态保持类目标表达“相对参考点不变”，不能被误写为数据库为空。
    if kind == StageGoalSemanticKind.PRESERVE_STATE:
        namespace = _semantic_text(semantics.get("namespace"), "state")
        reference = semantics.get("reference")
        reference_text = "the referenced state"
        if isinstance(reference, dict) and reference.get("type") == "milestone_index":
            reference_text = f"reference milestone index {reference.get('value')}"
        elif isinstance(reference, dict) and reference.get("type") == "initial_state":
            reference_text = "initial state"
        return (
            f"Preserve {namespace} state relative to {reference_text}. "
            "This means the relevant state should remain unchanged or equivalent, not that the namespace must be empty."
        )
    # 消息类目标允许语义等价，避免把自然语言回复锁死为固定字面文本。
    if kind == StageGoalSemanticKind.EMIT_MESSAGE:
        sender = _semantic_text(semantics.get("sender"), "sender")
        recipient = _semantic_text(semantics.get("recipient"), "recipient")
        content = _semantic_text(semantics.get("content"), "")
        match_policy = _semantic_text(semantics.get("match_policy"), "semantic_equivalent")
        exact_note = "Exact wording is required." if match_policy == "exact" else "Exact wording is not required."
        quoted_content = json.dumps(content, ensure_ascii=False) if content else "the required content"
        return f"Emit a message from {sender} to {recipient} conveying {quoted_content}. {exact_note}"
    # 工具调用类目标只描述工具名与参数兼容性，不承担 scorer 私有逻辑。
    if kind == StageGoalSemanticKind.TOOL_CALL:
        tool_name = _semantic_text(semantics.get("tool_name"), "the required tool")
        arguments = semantics.get("arguments")
        if isinstance(arguments, dict) and arguments:
            expected_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            return f"Call tool {tool_name} with arguments compatible with {expected_arguments}."
        return f"Call tool {tool_name} with appropriate arguments."
    return None


def _semantic_text(value: object, default: str) -> str:
    """把可选语义字段转换为非空文本。"""
    return value if isinstance(value, str) and value.strip() else default


def _graph_prompt_json(graph: MilestoneGraph) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone graph 摘要。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    analysis = graph.metadata.get("graph_analysis") if isinstance(graph.metadata, dict) else None
    augmented_edges = analysis.get("augmented_edges") if isinstance(analysis, dict) else None
    edges = augmented_edges if isinstance(augmented_edges, list) else [
        [source, target]
        for source, target in graph.edges
    ]
    return {
        "nodes": [_milestone_prompt_json(node) for node in graph.nodes],
        "edges": edges,
    }


def _milestone_prompt_json(milestone: Milestone) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone 摘要。"""
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "required": milestone.required,
        "anchor": milestone.stage_anchor_predecessor_id,
        "constraints": [_constraint_prompt_json(constraint) for constraint in milestone.constraints],
    }


def _constraint_prompt_json(constraint: Constraint) -> JsonObject:
    """构造 LLM fallback prompt 可消费的通用 constraint JSON。"""
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "selector": constraint.selector,
        "operator": constraint.operator.value,
        "namespace": constraint.namespace,
        "reference_milestone_id": constraint.reference_milestone_id,
        "hard": constraint.hard,
        "evaluator_hint": constraint.evaluator_hint,
        "expected_summary": _expected_summary(constraint.expected),
        "stage_goal_semantics": constraint.stage_goal_semantics,
    }


def _expected_summary(value: JsonValue) -> object:
    """把 expected 值压缩为 LLM fallback prompt 摘要。"""
    if isinstance(value, dict):
        rows = value.get("rows")
        columns = value.get("columns")
        return {
            "type": "dict",
            "row_count": len(rows) if isinstance(rows, list) else None,
            "columns": list(columns) if isinstance(columns, list) else None,
            "sample": rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None,
        }
    return value
