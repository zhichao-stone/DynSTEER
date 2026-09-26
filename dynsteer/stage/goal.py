from collections.abc import Callable
import json
import re

from dynsteer.language import TaskLanguage, language_from_task
from dynsteer.llm.base import BaseLLM
from dynsteer.prompt.stage import build_stage_goal_generation_prompt, build_stage_goal_system_prompt
from dynsteer.prompt.template import load_prompt_template
from dynsteer.model import Constraint, JsonObject, LLMMessage, MilestoneGraph, StageGoalSemanticKind, TaskCase
from dynsteer.stage.resolve import required_stage_goal_keys, stage_goal_key
from dynsteer.utils import enum_value, optional_str

EXPECTED_PLACEHOLDER_PATTERN = "[[{constraint_id}.expected]]"


def expected_placeholder(constraint_id: str) -> str:
    """Returns the steady stop goal placeholder that binds the bound."""
    if not constraint_id:
        raise ValueError("You can't be empty.")
    return EXPECTED_PLACEHOLDER_PATTERN.format(constraint_id=constraint_id)


def generate_stage_goal_templates(
    task_case: TaskCase,
    mode: str = "auto",
    llm_provider: Callable[[], BaseLLM] | None = None,
) -> dict[str, str]:
    """Generates a stage goal template that does not contain the specific value of the experiment."""
    if task_case is None:
        raise ValueError("We can't be empty.")
    graph = task_case.milestone_graph
    if graph is None:
        raise ValueError('TaskCase Missing Milestone_graph')
    language = language_from_task(task_case)
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode == "stored":
        validate_stage_goals(graph, task_case.stage_goal_templates)
        return dict(task_case.stage_goal_templates)
    if normalized_mode not in {"auto", "semantic", "llm"}:
        raise ValueError(f"Unknown line_goal_generation mode:{mode}")

    if normalized_mode in {"auto", "semantic"}:
        semantic_goals = _generate_stage_goals_by_semantic(graph, language)
        if semantic_goals is not None:
            return semantic_goals
        if normalized_mode == "semantic":
            raise ValueError('Stage_goal_generation=semantic requires all constraints to provide')

    if llm_provider is None:
        raise ValueError('Stage_goal_generation=llm needs to configure LLM profile')
    stage_goals = _generate_stage_goals_by_llm(task_case, graph, language, llm_provider())

    return stage_goals


def materialize_stage_goals(
    task_case: TaskCase,
    templates: dict[str, str] | None = None,
) -> dict[str, str]:
    """Use the Constraint. expected example in the current milestone profile to stage Goals."""
    if task_case is None or task_case.milestone_graph is None:
        raise ValueError('TaskCase Missing Milestone_graph')
    source = templates if templates is not None else task_case.stage_goal_templates
    validate_stage_goals(task_case.milestone_graph, source)
    replacements = {
        expected_placeholder(constraint.constraint_id): json.dumps(
            constraint.expected,
            ensure_ascii=False,
            sort_keys=True,
        )
        for milestone in task_case.milestone_graph.nodes
        for constraint in milestone.constraints
    }
    goals: dict[str, str] = {}
    for key, template in source.items():
        materialized = template
        for placeholder in _placeholders_in_goal(template):
            if placeholder in replacements:
                materialized = materialized.replace(placeholder, replacements[placeholder])
        goals[key] = materialized
    return goals


def validate_stage_goals(graph: MilestoneGraph | None, stage_goals: dict[str, str]) -> None:
    """Validate whether stage_goals fully cover the current graph."""
    if graph is None:
        raise ValueError('TaskCase Missing Milestone_graph')
    if stage_goals is None:
        raise ValueError('Stop_goals cannot be empty')
    required = set(required_stage_goal_keys(graph))
    actual = set(stage_goals)
    if actual != required:
        missing = sorted(required - actual)
        extra = sorted(actual - required)
        if missing:
            raise ValueError(f"TaskCase missing pregenerated stage_goal:{missing}")
        raise ValueError(f"TaskCase contains redundant line_goal:{extra}")
    for key, value in stage_goals.items():
        if not isinstance(key, str) or not key:
            raise ValueError('Step_goals key must be a non-empty string')
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Stage_goal cannot be empty:{key}")


def _generate_stage_goals_by_semantic(
    graph: MilestoneGraph,
    language: TaskLanguage,
) -> dict[str, str] | None:
    semantic_goals: dict[str, str] = {}
    topology = graph.topology
    if topology is None:
        raise ValueError('Milestone drag not yet enrich')
    for milestone in graph.nodes:
        anchor_id = topology.stage_anchor_by_id[milestone.milestone_id]
        if not milestone.constraints:
            return None
        pieces: list[str] = []
        for constraint in milestone.constraints:
            piece = _constraint_goal_text_from_semantics(constraint, language)
            if piece is None:
                return None
            pieces.append(piece)
        milestone_text = optional_str(milestone.description or milestone.name, milestone.milestone_id)
        objective = _render_goal_template(
            "objective", language,
            milestone_id=milestone.milestone_id,
            milestone_text=milestone_text,
        )
        semantic_goals[stage_goal_key(anchor_id, milestone.milestone_id)] = " ".join([objective, *pieces])
    validate_stage_goals(graph, semantic_goals)
    return semantic_goals


def _generate_stage_goals_by_llm(
    task_case: TaskCase,
    graph: MilestoneGraph,
    language: TaskLanguage,
    llm: BaseLLM
) -> dict[str, str] | None:
    required_keys = required_stage_goal_keys(graph)
    raw_text = llm.chat(
        [
            LLMMessage(role="system", content=build_stage_goal_system_prompt(language)),
            LLMMessage(
                role="user",
                content=build_stage_goal_generation_prompt(
                    task_case,
                    required_keys=required_keys,
                    language=language,
                    graph=graph,
                ),
            ),
        ],
        temperature=0.001,
    )
    required_placeholders = {
        expected_placeholder(constraint.constraint_id)
        for milestone in graph.nodes
        for constraint in milestone.constraints
        if _semantics_kind(constraint) == StageGoalSemanticKind.SET_STATE
        and not (
            isinstance(constraint.stage_goal_semantics, dict)
            and "operation" in constraint.stage_goal_semantics
        )
    }
    stage_goals = _parse_stage_goal_from_resp(raw_text, required_placeholders)
    validate_stage_goals(graph, stage_goals)
    return stage_goals


def _parse_stage_goal_from_resp(
    raw: str,
    required_placeholders: set[str] | None = None,
) -> dict[str, str]:
    """parsing the stage_goals dictionary from the JSON string returned from LLM."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('LLM stage_goal returns must be a JSON object') from exc
    if not isinstance(data, dict):
        raise ValueError('LLM stage_goal returns must be a JSON object')
    payload = data.get("stage_goals")
    if not isinstance(payload, dict):
        raise ValueError('LLM stage_goal. stage_goals must be a JSON object')

    stage_goals: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError('LLM stage_goal key must be a non-empty string')
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"LLM stop_goal value must be a non-empty string:{key}")
        stage_goals[key] = value.strip()
    missing = sorted((required_placeholders or set()) - set().union(
        *(set(_placeholders_in_goal(goal)) for goal in stage_goals.values())
    ))
    if missing:
        raise ValueError(f"LLM stage_goal does not keep expected placeholder:{missing}")
    return stage_goals


def _constraint_goal_text_from_semantics(constraint: Constraint, language: TaskLanguage) -> str | None:
    """Generates snippets of text based on a single binding public semantic IR."""
    semantics = constraint.stage_goal_semantics
    if semantics is None or not isinstance(semantics, dict):
        return None
    try:
        kind = enum_value(StageGoalSemanticKind, semantics.get("kind"), "stage_goal_semantics.kind")
    except ValueError:
        return None
    if kind == StageGoalSemanticKind.SET_STATE:
        namespace = optional_str(semantics.get("namespace"), "state")
        if "operation" in semantics:
            return _generated_state_goal_text(semantics, namespace, language)
        expected = expected_placeholder(constraint.constraint_id)
        return _render_goal_template("set_state", language, namespace=namespace, expected=expected)
    if kind == StageGoalSemanticKind.PRESERVE_STATE:
        namespace = optional_str(semantics.get("namespace"), "state")
        return _render_goal_template(
            "preserve_state", language,
            namespace=namespace, reference_text=_reference_text(semantics.get("reference"), language),
        )
    if kind == StageGoalSemanticKind.EMIT_MESSAGE:
        sender = optional_str(semantics.get("sender"), "sender")
        recipient = optional_str(semantics.get("recipient"), "recipient")
        content = optional_str(semantics.get("content"), "")
        content_text = json.dumps(content, ensure_ascii=False) if content else _REQUIRED_CONTENT_TEXT[language]
        return _render_goal_template(
            "emit_message", language,
            sender=sender, recipient=recipient, content_text=content_text,
        )
    if kind == StageGoalSemanticKind.TOOL_CALL:
        tool_name = optional_str(semantics.get("tool_name"), "the required tool")
        arguments = semantics.get("arguments")
        arguments_clause = ""
        if isinstance(arguments, dict) and arguments:
            expected_arguments = ", ".join(
                f"{name}={_symbolic_value_text(value, language)}"
                for name, value in sorted(arguments.items())
            )
            arguments_clause = _ARGUMENTS_CLAUSE_TEXT[language].format(expected_arguments=expected_arguments)
        return _render_goal_template(
            "tool_call", language,
            tool_name=tool_name, arguments_clause=arguments_clause
        )
    return None


def _generated_state_goal_text(
    semantics: JsonObject, namespace: str, language: TaskLanguage
) -> str:
    """Renders the genderated set_state synonyms to the target text that does not expose binding JSON."""
    operation = str(semantics.get("operation"))
    cardinality = str(semantics.get("cardinality"))
    match = _symbolic_mapping_text(semantics.get("match"), language)
    values = _symbolic_mapping_text(semantics.get("values"), language)
    if language == TaskLanguage.CHINESE:
        parts = [f"在 {namespace} 中执行 {operation}（{cardinality}）"]
        if match:
            parts.append(f"匹配 {match}")
        if values:
            parts.append(f"写入 {values}")
        return "；".join(parts)
    parts = [f"Apply {operation} ({cardinality}) in {namespace}"]
    if match:
        parts.append(f"match {match}")
    if values:
        parts.append(f"write {values}")
    return "; ".join(parts)


def _symbolic_mapping_text(value: object, language: TaskLanguage) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(
        f"{name}={_symbolic_value_text(source, language)}"
        for name, source in sorted(value.items())
    )


def _symbolic_value_text(value: object, language: TaskLanguage) -> str:
    if not isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if value.get("source") == "public_literal":
        return json.dumps(value.get("value"), ensure_ascii=False)
    milestone_id = optional_str(value.get("source_milestone_id"), "predecessor")
    selector = optional_str(value.get("selector"), "result")
    if language == TaskLanguage.CHINESE:
        return f"里程碑 {milestone_id} 输出的 {selector}"
    return f"{selector} produced by milestone {milestone_id}"


def _semantics_kind(constraint: Constraint) -> StageGoalSemanticKind | None:
    """Reads the semantic type of bound line goal."""
    semantics = constraint.stage_goal_semantics
    if not isinstance(semantics, dict):
        return None
    try:
        return enum_value(StageGoalSemanticKind, semantics.get("kind"), "stage_goal_semantics.kind")
    except ValueError:
        return None


def _placeholders_in_goal(goal: str) -> list[str]:
    """extracts the expected placeholder from the stage goal."""
    return re.findall(r"\[\[[^\[\]]+\.expected\]\]", goal)


def _render_goal_template(name: str, language: TaskLanguage, **kwargs: object) -> str:
    """Rendering certainty snippet templates."""
    return load_prompt_template("stage_goal", name).render(language=language, **kwargs)


def _reference_text(reference: object, language: TaskLanguage) -> str:
    """Describes the preserve_state reference status by language."""
    reference_type = reference.get("type") if isinstance(reference, dict) else "default"
    template = _REFERENCE_TEXT[language].get(str(reference_type), _REFERENCE_TEXT[language]["default"])
    return template.format(value=reference.get("value")) if isinstance(reference, dict) else template


_REFERENCE_TEXT: dict[TaskLanguage, dict[str, str]] = {
    TaskLanguage.ENGLISH: {
        "milestone_id": "milestone {value}",
        "milestone_index": "reference milestone index {value}",
        "initial_state": "initial state",
        "default": "the referenced state",
    },
    TaskLanguage.CHINESE: {
        "milestone_id": "里程碑 {value}",
        "milestone_index": "参考里程碑索引 {value}",
        "initial_state": "初始状态",
        "default": "被引用状态",
    },
}

_REQUIRED_CONTENT_TEXT: dict[TaskLanguage, str] = {
    TaskLanguage.ENGLISH: "the required content",
    TaskLanguage.CHINESE: "必需内容",
}

_ARGUMENTS_CLAUSE_TEXT: dict[TaskLanguage, str] = {
    TaskLanguage.ENGLISH: " with arguments compatible with {expected_arguments}",
    TaskLanguage.CHINESE: "，参数需与 {expected_arguments} 兼容",
}
