import json
from collections.abc import Iterable

from dynsteer.graph import FINISH_NODE_ID
from dynsteer.language import TaskLanguage

from dynsteer.evaluate.semantic import constraint_actual_excerpt, constraint_expected_excerpt
from dynsteer.model import Constraint, Dimension, JsonObject, Milestone, MilestoneGraph, StageInterval, TaskCase, Trajectory
from dynsteer.prompt.rubrics import rubrics_for_dimensions
from dynsteer.prompt.template import load_prompt_template
from dynsteer.stage.resolve import resolve_stage_goal
from dynsteer.stage.trajectory import stage_trajectory_steps
from dynsteer.utils import json_safe, validated_target_dimensions


def build_judge_system_prompt(language: TaskLanguage = TaskLanguage.ENGLISH) -> str:
    """Construct the LLMJudge system prompt."""
    return _render_template("system", language)


def build_judge_prompt(
    template_name: str,
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    *,
    language: TaskLanguage = TaskLanguage.ENGLISH,
    extra: JsonObject | None = None,
    render_kwargs: dict[str, object] | None = None,
    target_dimensions: Iterable[Dimension] | None = None,
) -> str:
    """Construct judge profile by template name."""
    context_json = _context_json(
        interval, task_case, trajectory, language=language, extra=extra, target_dimensions=target_dimensions
    )
    return _render_template(template_name, language, context_json=context_json, **dict(render_kwargs or {}))


def build_semantic_message_equivalence_prompt(
    task_case: TaskCase,
    check: JsonObject,
    *,
    language: TaskLanguage = TaskLanguage.ENGLISH,
) -> str:
    """Construct the ad hoc message semantic equivalence correction."""
    if task_case is None or check is None:
        raise ValueError('task_case and check cannot be empty')
    context = {
        "task": {"task_description": task_case.task_description},
        "semantic_message_check": check,
        "required_output": {
            "equivalent": "bool",
            "confidence": "float in [0,1]",
            "reason": "short string explaining the semantic equivalence or mismatch",
        },
    }
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return _render_template("semantic_message_equivalence", language, context_json=context_json)


def _context_json(
    interval: StageInterval,
    task_case: TaskCase,
    trajectory: Trajectory,
    language: TaskLanguage = TaskLanguage.ENGLISH,
    extra: JsonObject | None = None,
    target_dimensions: Iterable[Dimension] | None = None,
) -> str:
    """Sequence the context of the stage assessment into a JSON text."""
    dimensions = validated_target_dimensions(target_dimensions)

    data: JsonObject = {
        "task": {"task_description": task_case.task_description},
        "stage_goal": resolve_stage_goal(interval, task_case),
        "rubrics": rubrics_for_dimensions(dimensions),
        "interval": {
            "status": interval.status.value,
            "evidence": list(interval.evidence),
            "milestone_score": interval.milestone_score.score if interval.milestone_score is not None else None,
        },
        "constraint_checks": _constraint_checks(interval, task_case),
        "steps": [
            {
                "index": step.index,
                "actor": step.actor.value,
                "event_type": step.event_type.value,
                "content": step.content,
                "tool_call": json_safe(step.tool_call),
                "tool_result": json_safe(step.tool_result),
                "raw": dict(step.raw),
            }
            for step in stage_trajectory_steps(interval, trajectory)
        ],
        "required_output": _output_schema(language, dimensions),
    }
    if _is_whole_trajectory_finish(interval, task_case):
        data["whole_trajectory_context"] = {
            "coverage_basis": "whole_trajectory",
            "initial_state_summary": task_case.metadata.get("runtime_initial_state_summary"),
            "final_state": json_safe(trajectory.final_state),
            "minefields": _minefield_prompt_json(task_case.milestone_graph),
        }
    if extra is not None:
        data.update(extra)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _render_template(name: str, language: TaskLanguage, **kwargs: object) -> str:
    return load_prompt_template("judge", name).render(language=language, **kwargs)


def _constraint_checks(interval: StageInterval, task_case: TaskCase) -> list[JsonObject]:
    """Constructs the lightweight summary for LLM judge."""
    if interval.milestone_score is None or interval.milestone_id is None:
        return []
    graph = task_case.milestone_graph
    milestone: Milestone | None = None
    if graph is not None:
        for candidate in graph.nodes:
            if candidate.milestone_id == interval.milestone_id:
                milestone = candidate
                break
    constraints: dict[str, Constraint] = {constraint.constraint_id: constraint for constraint in milestone.constraints} if milestone is not None else {}
    checks: list[JsonObject] = []
    for score in interval.milestone_score.constraint_scores:
        constraint = constraints.get(score.constraint_id)
        threshold = constraint.threshold if constraint is not None else 1.0
        checks.append(
            {
                "constraint_id": score.constraint_id,
                "constraint_goal": _constraint_goal(constraint),
                "satisfied": score.score >= threshold and not score.missing,
                "score": score.score,
                "missing": score.missing,
                "hard": constraint.hard if constraint is not None else None,
                "target_source": "Constraint.expected",
                "expected_excerpt": constraint_expected_excerpt(constraint),
                "actual_excerpt": constraint_actual_excerpt(constraint, score),
                "short_evidence": [str(item) for item in score.evidence[:3]],
            }
        )
    return checks


def _is_whole_trajectory_finish(interval: StageInterval, task_case: TaskCase) -> bool:
    """Determines whether the current judge prompt is a complete trajectories."""
    graph = task_case.milestone_graph
    return interval.milestone_id == FINISH_NODE_ID and graph is not None and not graph.nodes


def _minefield_prompt_json(graph: MilestoneGraph | None) -> list[JsonObject]:
    """Generate a prompt-safe minefield summary without leaking DEFAULT conclusions."""
    if graph is None:
        return []
    return [
        {
            "minefield_id": minefield.minefield_id,
            "name": minefield.name,
            "description": minefield.description,
            "severity": minefield.severity,
            "penalty": json_safe(minefield.penalty),
            "constraints": [
                {
                    "constraint_id": constraint.constraint_id,
                    "target": constraint.target.value,
                    "operator": constraint.operator.value,
                    "namespace": constraint.namespace,
                    "hard": constraint.hard,
                    "evaluator_hint": constraint.evaluator_hint,
                    "metadata": json_safe(constraint.metadata),
                }
                for constraint in minefield.constraints
            ],
        }
        for minefield in graph.minefields
        if minefield is not None
    ]


def _output_schema(language: TaskLanguage, dimensions: list[Dimension]) -> JsonObject:
    """Returns the description of the required_output field by the language of the task."""
    schema = dict(_OUTPUT_SCHEMA.get(language, _OUTPUT_SCHEMA[TaskLanguage.ENGLISH]))
    dimension_text = ", ".join(dimension.value for dimension in dimensions)
    schema["dimension_scores"] = schema["dimension_scores"].format(dimensions=dimension_text)
    return schema


def _constraint_goal(constraint: Constraint | None) -> str:
    if constraint is None:
        return 'Satisfying the structured constraint'
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    kind = str(semantics.get("kind") or "")
    if kind == "emit_message":
        return 'Send user-visible messages that meet the semantic requirements of the stage'
    if kind == "set_state":
        namespace = constraint.namespace or "state"
        return f"Set the {namespace} state to the target value"
    if kind == "preserve_state":
        namespace = constraint.namespace or "state"
        return f"Preserve the {namespace} state."
    if kind == "tool_call":
        tool_name = semantics.get("tool_name")
        return f"Call tool {tool_name}" if isinstance(tool_name, str) and tool_name else 'Call the matching tool'
    return str(constraint.metadata.get("description") or constraint.constraint_id)


_OUTPUT_SCHEMA: dict[TaskLanguage, JsonObject] = {
    TaskLanguage.ENGLISH: {
        "dimension_scores": (
            "dict[str,float], covering only these dimensions: {dimensions}"
        ),
        "status": "pass|warn|fail|missing|ambiguous|invalid",
        "evidence": "list[str] with step index, interval.evidence, or constraint_checks references",
        "diagnosis": "list[str], each item is one independent diagnostic conclusion; prefix with overall or a rubric dimension when useful",
    },
    TaskLanguage.CHINESE: {
        "dimension_scores": 'Subject [str, float], only covers these dimensions:{dimensions}',
        "status": "pass|warn|fail|missing|ambiguous|invalid",
        "evidence": 'List [str], including step index, interval.event or reference to transaction_checks',
        "diagnosis": 'List [str], each of which is an independent diagnosis suggesting an overall or rubric dimension prefix for attribution',
    },
}
