import json
from dynsteer.graph import augmented_edges
from dynsteer.language import TaskLanguage
from dynsteer.model import Constraint, JsonObject, Milestone, MilestoneGraph, TaskCase
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
    rules = (
        "凡 stage goal 需要引用某个 constraint 的 expected，必须原样保留 "
        "[[<constraint_id>.expected]]；不得把当前 expected 的具体值直接写死到模板中。"
    )
    return load_prompt_template("stage", "goal_generation").render(
        language=language,
        context_json=json.dumps(payload, ensure_ascii=False, indent=2),
    ) + f"\n\n{rules}"

def _graph_prompt_json(graph: MilestoneGraph) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone graph 摘要。"""
    if graph is None:
        raise ValueError("graph 不能为空")
    edges = [[source, target] for source, target in augmented_edges(graph)]
    topology = graph.topology
    if topology is None:
        raise ValueError("milestone graph 尚未 enrich")
    return {"nodes": [_milestone_prompt_json(node, topology.stage_anchor_by_id[node.milestone_id]) for node in graph.nodes], "edges": edges}

def _milestone_prompt_json(milestone: Milestone, anchor_id: str) -> JsonObject:
    """构造 LLM fallback prompt 使用的 milestone 摘要。"""
    if milestone is None:
        raise ValueError("milestone 不能为空")
    return {
        "milestone_id": milestone.milestone_id,
        "name": milestone.name,
        "description": milestone.description,
        "anchor": anchor_id,
        "constraints": [
            _constraint_prompt_json(constraint) for constraint in milestone.constraints
        ],
    }

def _constraint_prompt_json(constraint: Constraint) -> JsonObject:
    """构造 LLM fallback prompt 可消费的通用 constraint JSON。"""
    semantics = dict(constraint.stage_goal_semantics) if isinstance(constraint.stage_goal_semantics, dict) else None
    placeholder = f"[[{constraint.constraint_id}.expected]]"
    generated_state = isinstance(semantics, dict) and semantics.get("kind") == "set_state" and "operation" in semantics
    if isinstance(semantics, dict) and semantics.get("kind") == "set_state" and not generated_state:
        semantics["expected"] = placeholder
    if isinstance(semantics, dict) and semantics.get("kind") == "tool_call":
        arguments = semantics.get("arguments")
        if isinstance(arguments, dict):
            semantics["arguments"] = {
                name: (
                    {"source_milestone_id": value.get("source_milestone_id"), "argument": name, "label": "derived from predecessor"}
                    if isinstance(value, dict) and value.get("source") == "node_output"
                    else value
                )
                for name, value in arguments.items()
            }
    return {
        "constraint_id": constraint.constraint_id,
        "target": constraint.target.value,
        "selector": constraint.selector,
        "operator": constraint.operator.value,
        "namespace": constraint.namespace,
        "reference_milestone_id": constraint.reference_milestone_id,
        "hard": constraint.hard,
        "evaluator_hint": constraint.evaluator_hint,
        "expected_summary": "generated_state_goal" if generated_state else placeholder,
        "stage_goal_semantics": semantics,
    }
