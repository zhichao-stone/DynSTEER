import json
from dynsteer.graph import augmented_edges
from dynsteer.language import TaskLanguage
from dynsteer.model import Constraint, JsonObject, Milestone, MilestoneGraph, TaskCase
from dynsteer.prompt.template import load_prompt_template

def build_stage_goal_system_prompt(language: TaskLanguage=TaskLanguage.ENGLISH) -> str:
    """Constructive stage target generator systemprompt."""
    return load_prompt_template("stage", "goal_system").render(language=language)

def build_stage_goal_generation_prompt(task_case: TaskCase, required_keys: list[str], language: TaskLanguage=TaskLanguage.ENGLISH, *, graph: MilestoneGraph | None=None) -> str:
    """Constructs a LLM project that generates all stage_goal."""
    if task_case is None or required_keys is None:
        raise ValueError('The task_case and required_keys cannot be empty')
    effective_graph = graph or task_case.milestone_graph
    if effective_graph is None:
        raise ValueError('TaskCase Missing Milestone_graph')
    payload = {
        "task_description": task_case.task_description,
        "milestone_graph": _graph_prompt_json(effective_graph),
        "required_stage_goal_keys": list(required_keys),
        "output_schema": {"stage_goals": {"milestone_id_1->milestone_id_2": 'Natural language objectives at the current stage'}},
    }
    rules_by_language = {
        TaskLanguage.ENGLISH: (
            "When a stage goal references a constraint expected value, preserve "
            "[[<constraint_id>.expected]] verbatim; do not inline the concrete value."
        ),
        TaskLanguage.CHINESE: (
            "凡 stage goal 需要引用某个 constraint 的 expected，必须原样保留 "
            "[[<constraint_id>.expected]]；不得把当前 expected 的具体值直接写死到模板中。"
        ),
    }
    rules = rules_by_language[language]
    return load_prompt_template("stage", "goal_generation").render(
        language=language,
        context_json=json.dumps(payload, ensure_ascii=False, indent=2),
    ) + f"\n\n{rules}"

def _graph_prompt_json(graph: MilestoneGraph) -> JsonObject:
    """Constructs the summary of the milestone drag for the LLM fallback program."""
    if graph is None:
        raise ValueError("You can't be empty.")
    edges = [[source, target] for source, target in augmented_edges(graph)]
    topology = graph.topology
    if topology is None:
        raise ValueError('Milestone drag not yet enrich')
    return {"nodes": [_milestone_prompt_json(node, topology.stage_anchor_by_id[node.milestone_id]) for node in graph.nodes], "edges": edges}

def _milestone_prompt_json(milestone: Milestone, anchor_id: str) -> JsonObject:
    """Constructs the milestone summary for the LLM fallback program."""
    if milestone is None:
        raise ValueError("Milestone can't be empty.")
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
    """Build generic constraint JSON consumable by the LLM fallback prompt."""
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
