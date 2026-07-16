from __future__ import annotations

from collections.abc import Callable
import json

from dynsteer.graph import FINISH_NODE_ID
from dynsteer.llm.base import BaseLLM
from dynsteer.model import (
    Constraint,
    LLMMessage,
    MilestoneGraph,
    StageGoalSemanticKind,
    StageInterval,
    TaskCase,
)
from dynsteer.utils import enum_value


DEFAULT_FINISH_STAGE_GOAL = "完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。"


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
    llm_provider: Callable[[], BaseLLM | None] | None = None,
) -> dict[str, str]:
    """集中生成 TaskCase 的 stage_goals。"""
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode == "stored":
        validate_stage_goals(task_case.milestone_graph, task_case.stage_goals)
        return dict(task_case.stage_goals)
    if normalized_mode not in {"auto", "semantic", "llm"}:
        raise ValueError(f"未知 stage_goal_generation 模式: {mode}")

    if normalized_mode in {"auto", "semantic"}:
        semantic_goals = _generate_semantic_stage_goals(task_case)
        if semantic_goals is not None:
            return semantic_goals
        if normalized_mode == "semantic":
            raise ValueError("stage_goal_generation=semantic 需要所有约束提供 stage_goal_semantics")

    if llm_provider is None:
        raise ValueError("stage_goal_generation=llm 需要配置 LLM provider")
    llm = llm_provider()
    if llm is None:
        raise ValueError("stage_goal_generation=llm 需要配置 DYNSTEER_JUDGE_PROVIDER")
    return generate_stage_goals_with_llm(task_case, llm)


def generate_stage_goals_with_llm(task_case: TaskCase, llm: BaseLLM) -> dict[str, str]:
    """用 LLM 一次性生成并校验全部 stage_goal 字典。"""
    if task_case is None or llm is None:
        raise ValueError("task_case 和 llm 不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    from dynsteer.prompt.stage import build_stage_goal_generation_prompt

    raw_text = llm.chat(
        [
            LLMMessage(role="system", content="你是 DynSTEER 阶段目标生成器，只返回 JSON 对象。"),
            LLMMessage(
                role="user",
                content=build_stage_goal_generation_prompt(
                    task_case,
                    required_keys=required_stage_goal_keys(graph),
                    graph=graph,
                ),
            ),
        ],
        temperature=0,
    )
    stage_goals = _parse_stage_goal_from_resp(raw_text)
    validate_stage_goals(graph, stage_goals)
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
        return DEFAULT_FINISH_STAGE_GOAL
    if interval.milestone_id == FINISH_NODE_ID:
        if isinstance(interval.stage_anchor_milestone_id, str) and interval.stage_anchor_milestone_id:
            key = stage_goal_key(interval.stage_anchor_milestone_id, interval.milestone_id)
            stage_goal = task_case.stage_goals.get(key)
            if isinstance(stage_goal, str) and stage_goal.strip():
                return stage_goal
        return DEFAULT_FINISH_STAGE_GOAL
    if not isinstance(interval.stage_anchor_milestone_id, str) or not interval.stage_anchor_milestone_id:
        raise ValueError(f"milestone 阶段缺少 stage_anchor_milestone_id: {interval.milestone_id}")
    key = stage_goal_key(interval.stage_anchor_milestone_id, interval.milestone_id)
    stage_goal = task_case.stage_goals.get(key)
    if isinstance(stage_goal, str) and stage_goal.strip():
        return stage_goal
    raise ValueError(f"TaskCase 缺少预生成 stage_goal: {key}")


def _generate_semantic_stage_goals(task_case: TaskCase) -> dict[str, str] | None:
    if task_case is None:
        raise ValueError("task_case 不能为空")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    semantic_goals: dict[str, str] = {}
    for milestone in graph.nodes:
        anchor_id = milestone.stage_anchor_predecessor_id
        if not isinstance(anchor_id, str) or not anchor_id:
            raise ValueError(f"milestone 缺少 stage_anchor_predecessor_id: {milestone.milestone_id}")
        if not milestone.constraints:
            return None
        pieces: list[str] = []
        for constraint in milestone.constraints:
            piece = _constraint_goal_text_from_semantics(constraint)
            if piece is None:
                return None
            pieces.append(piece)
        objective = f"Complete milestone {milestone.milestone_id}: {milestone.description or milestone.name}."
        semantic_goals[stage_goal_key(anchor_id, milestone.milestone_id)] = " ".join([objective, *pieces])
    validate_stage_goals(graph, semantic_goals)
    return semantic_goals


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
    return {
        key: stage_goal
        for key, stage_goal in payload.items()
        if isinstance(key, str) and key and isinstance(stage_goal, str) and stage_goal.strip()
    }


def _constraint_goal_text_from_semantics(constraint: Constraint) -> str | None:
    """根据单个约束的公共语义 IR 生成 stage_goal 文本片段。"""
    semantics = constraint.stage_goal_semantics
    if semantics is None or not isinstance(semantics, dict):
        return None
    try:
        kind = enum_value(StageGoalSemanticKind, semantics.get("kind"), "stage_goal_semantics.kind")
    except ValueError:
        return None
    if kind == StageGoalSemanticKind.SET_STATE:
        namespace = _semantic_text(semantics.get("namespace"), "state")
        expected = json.dumps(semantics.get("expected"), ensure_ascii=False, sort_keys=True)
        return (
            f"Make or verify {namespace} state satisfies {expected}. "
            "Use structured scorer evidence for this state requirement; no separate user-facing restatement is required unless another message requirement says so."
        )
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
    if kind == StageGoalSemanticKind.EMIT_MESSAGE:
        sender = _semantic_text(semantics.get("sender"), "sender")
        recipient = _semantic_text(semantics.get("recipient"), "recipient")
        content = _semantic_text(semantics.get("content"), "")
        match_policy = _semantic_text(semantics.get("match_policy"), "semantic_equivalent")
        exact_note = "Exact wording is required." if match_policy == "exact" else "Exact wording is not required."
        quoted_content = json.dumps(content, ensure_ascii=False) if content else "the required content"
        return f"Emit a message from {sender} to {recipient} conveying {quoted_content}. {exact_note}"
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
