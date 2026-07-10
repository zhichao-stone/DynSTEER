from __future__ import annotations

import json

from dynsteer.language import TaskLanguage
from dynsteer.model import Constraint, Dimension, JsonObject, JsonValue, Milestone, StageInterval, TaskCase, Trajectory
from dynsteer.prompt.template import PromptTemplate, load_prompt_text
from dynsteer.stage import stage_trajectory_steps


def build_judge_system_prompt(language: TaskLanguage = TaskLanguage.ENGLISH) -> str:
    """构造 LLMJudge 系统 prompt。"""
    return _render_template("system", language)


def build_judge_prompt(
    template_name: str,
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    weights: dict[Dimension, float],
    *,
    language: TaskLanguage = TaskLanguage.ENGLISH,
    extra: JsonObject | None = None,
    render_kwargs: dict[str, object] | None = None,
) -> str:
    """按模板名称构造 judge prompt。"""
    context_json = _context_json(interval, task_case, trajectory, weights, language=language, extra=extra)
    return _render_template(template_name, language, context_json=context_json, **dict(render_kwargs or {}))


def _context_json(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    weights: dict[Dimension, float],
    language: TaskLanguage = TaskLanguage.ENGLISH,
    extra: JsonObject | None = None,
) -> str:
    """把阶段评估上下文序列化为 JSON 文本。"""
    if interval is None or task_case is None or trajectory is None or weights is None:
        raise ValueError("prompt 上下文参数不能为空")
    from dynsteer.stage import resolve_stage_goal

    data: JsonObject = {
        "task": {
            "task_description": task_case.task_description,
        },
        "stage_goal": resolve_stage_goal(interval, task_case),
        "rubric_dimension_focus": [dimension.value for dimension in Dimension],
        "interval": {
            "status": interval.status.value,
            "evidence": list(interval.evidence),
            "milestone_score": interval.milestone_score.score if interval.milestone_score is not None else None,
        },
        "structured_milestone_evidence": _structured_milestone_evidence(interval, task_case),
        "steps": [
            {
                "index": step.index,
                "actor": step.actor.value,
                "event_type": step.event_type.value,
                "content": step.content,
                "tool_call": _json_safe_dataclass(step.tool_call),
                "tool_result": _json_safe_dataclass(step.tool_result),
                "raw": dict(step.raw),
            }
            for step in stage_trajectory_steps(interval, trajectory)
        ],
        "rubric_dimensions": [dimension.value for dimension in Dimension],
        "required_output": _output_schema(language),
    }
    if extra is not None:
        data.update(extra)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _render_template(name: str, language: TaskLanguage, **kwargs: object) -> str:
    template_text = load_prompt_text("judge", name, language)
    return PromptTemplate(**{language.value: template_text}).render(language=language, **kwargs)


def _json_safe_dataclass(value: object) -> object:
    """将简单 dataclass 转换为 JSON 友好对象。"""
    if value is None:
        return None
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return dict(raw)
    return value


def _structured_milestone_evidence(interval: StageInterval, task_case: TaskCase) -> list[JsonObject]:
    """构造给 LLM judge 使用的轻量结构化 milestone evidence。"""
    if interval.milestone_score is None or interval.milestone_id is None:
        return []
    graph = task_case.milestone_graph
    milestone: Milestone | None = None
    if graph is not None:
        for candidate in graph.nodes:
            if candidate.milestone_id == interval.milestone_id:
                milestone = candidate
                break
    constraints: dict[str, Constraint] = (
        {constraint.constraint_id: constraint for constraint in milestone.constraints}
        if milestone is not None
        else {}
    )
    evidence: list[JsonObject] = []
    for score in interval.milestone_score.constraint_scores:
        constraint = constraints.get(score.constraint_id)
        constraint_context: JsonObject = {}
        if constraint is not None:
            constraint_context = {
                "target": constraint.target.value,
                "operator": constraint.operator.value,
                "namespace": constraint.namespace,
                "hard": constraint.hard,
                "stage_goal_semantics": dict(constraint.stage_goal_semantics)
                if isinstance(constraint.stage_goal_semantics, dict)
                else None,
            }
        evidence.append(
            {
                "constraint_id": score.constraint_id,
                "score": score.score,
                "missing": score.missing,
                "evidence": list(score.evidence),
                "actual_summary": _actual_summary(score.actual),
                "constraint": constraint_context,
            }
        )
    return evidence


def _actual_summary(actual: JsonValue) -> JsonObject:
    """把 constraint actual 值压缩成适合放入 judge prompt 的摘要。"""
    if isinstance(actual, list):
        sample = actual[0] if actual and isinstance(actual[0], dict) else None
        return {"type": "list", "row_count": len(actual), "sample": sample}
    if isinstance(actual, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in actual)}
    if isinstance(actual, (str, int, float, bool)) or actual is None:
        return {"type": type(actual).__name__, "value": actual}
    return {"type": type(actual).__name__, "value": str(actual)}


def _output_schema(language: TaskLanguage) -> JsonObject:
    """按任务语言返回 required_output 字段说明。"""
    if not isinstance(language, TaskLanguage):
        raise ValueError("language 必须是 TaskLanguage 枚举类")
    return dict(_OUTPUT_SCHEMA.get(language, _OUTPUT_SCHEMA[TaskLanguage.ENGLISH]))


_OUTPUT_SCHEMA: dict[TaskLanguage, JsonObject] = {
    TaskLanguage.ENGLISH: {
        "dimension_scores": "dict[str,float] covering progress,state_consistency,tool_quality,efficiency,safety,interaction_quality,recovery",
        "status": "pass|warn|fail|missing|ambiguous|invalid",
        "judge_confidence": "float in [0,1]",
        "evidence": "list[str] with step index or milestone evidence references",
        "diagnosis": "list[str], each item is one independent diagnostic conclusion; prefix with overall or a rubric dimension when useful",
    },
    TaskLanguage.CHINESE: {
        "dimension_scores": "dict[str,float]，覆盖 progress,state_consistency,tool_quality,efficiency,safety,interaction_quality,recovery",
        "status": "pass|warn|fail|missing|ambiguous|invalid",
        "judge_confidence": "float, 0 到 1 之间的浮点数",
        "evidence": "list[str]，包含 step index 或 milestone evidence 引用",
        "diagnosis": "list[str]，每一项是一条独立诊断结论，建议用 overall 或 rubric 维度名前缀标明归属",
    },
}
