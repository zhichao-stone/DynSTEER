import json
from dynsteer.language import TaskLanguage
from dynsteer.model import Constraint, JsonObject, JsonValue, Milestone, MilestoneGraph, TaskCase
from dynsteer.prompt.template import load_prompt_template

def build_stage_goal_system_prompt(language: TaskLanguage=TaskLanguage.ENGLISH) -> str:
    """构造阶段目标生成器 system prompt。"""
    return load_prompt_template("stage", "goal_system").render(language=language)

def build_stage_goal_generation_prompt(task_case: TaskCase, required_keys: list[str], language: TaskLanguage=TaskLanguage.ENGLISH, *, graph: MilestoneGraph | None=None) -> str:
    """构造一次性生成全部 stage_goal 的 LLM prompt。"""
    if task_case is None or required_keys is None:
        raise ValueError("task_case 和 required_keys 不能为空")
    effective_graph = graph or task_case.milestone_graph
    if effective_graph is None:
        raise ValueError("TaskCase 缺少 milestone_graph")
    payload = {
        "task_description": task_case.task_description,
        "milestone_graph": _graph_prompt_json(effective_graph),
        "required_stage_goal_keys": list(required_keys),
        "output_schema": {"stage_goals": {"milestone_id_1->milestone_id_2": "当前阶段自然语言目标"}},
    }
    return load_prompt_template("stage", "goal_generation").render(language=language, context_json=json.dumps(payload, ensure_ascii=False, indent=2))

def _graph_prompt_json(graph: MilestoneGraph) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone graph 摘要。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    analysis = graph.metadata.get("graph_analysis") if isinstance(graph.metadata, dict) else None
    augmented_edges = analysis.get("augmented_edges") if isinstance(analysis, dict) else None
    edges = augmented_edges if isinstance(augmented_edges, list) else [[source, target] for source, target in graph.edges]
    return {"nodes": [_milestone_prompt_json(node) for node in graph.nodes], "edges": edges}

def _milestone_prompt_json(milestone: Milestone) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone 摘要。"""
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "anchor": milestone.stage_anchor_predecessor_id,
        "constraints": [
            _constraint_prompt_json(constraint) for constraint in milestone.constraints
        ],
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
