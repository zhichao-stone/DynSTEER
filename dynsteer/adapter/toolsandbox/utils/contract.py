from __future__ import annotations

import logging
import re
from collections.abc import Callable

from dynsteer.adapter.contract import (
    normalize_tool_contract,
)
from dynsteer.adapter.toolsandbox.utils.effects import (
    toolsandbox_environment_rules,
    toolsandbox_tool_contracts,
)
from dynsteer.adapter.toolsandbox.utils.state import (
    initial_state_from_context,
    sandbox_rows_from_context,
)
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import (
    GeneratorTurn,
    GeneratorTaskView,
)
from dynsteer.model import Actor, JsonObject, TaskCase
from dynsteer.utils import enum_name, json_safe


logger = logging.getLogger(__name__)


def agent_facing_tool_schema(
    context: object,
    module_loader: Callable[[str], object],
) -> JsonObject:
    """Extracts allow/deny/disturbing final tool schema."""
    if context is None or module_loader is None:
        raise ValueError('Context and module_loader cannot be empty')
    available = context.get_available_tools(scrambling_allowed=True)
    if not isinstance(available, dict):
        raise TypeError('You have to return the dictionary.get_aviable_tools')
    visible = {
        name: tool
        for name, tool in available.items()
        if _visible_to_agent(getattr(tool, "visible_to", None))
    }
    conversion = module_loader("tool_sandbox.common.tool_conversion")
    convert = getattr(conversion, "convert_to_openai_tools", None)
    if not callable(convert):
        raise TypeError('ToolSandbox Missing Convert_to_openai_tools')
    tools = json_safe(convert(visible))
    if not isinstance(tools, list):
        raise TypeError('You have to return the array.')
    return {"tools": tools}


def build_toolsandbox_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    context: object,
    module_loader: Callable[[str], object],
) -> GeneratorTaskView:
    """Project the first-user boundary state and the generation view of all predefined cycles."""
    if config is None or task_case is None or context is None or module_loader is None:
        raise ValueError('Config, task_case, context and module_loader cannot be empty')
    rows = sandbox_rows_from_context(context, module_loader)
    steps = sandbox_rows_to_step_dicts(rows)
    boundary = getattr(context, "first_user_sandbox_message_index", None)
    public_assets, turn_sources = _interaction_plan_from_steps(
        steps, boundary if isinstance(boundary, int) else None
    )
    if turn_sources and not turn_sources[0][1]:
        turn_sources[0] = ("instruction", task_case.task_description)
    raw_tool_schema = agent_facing_tool_schema(context, module_loader)
    tool_schema, tool_evidence = normalize_tool_contract(raw_tool_schema)
    tool_name_mapping = context.get_agent_to_execution_facing_tool_name()
    if not isinstance(tool_name_mapping, dict):
        raise TypeError('ToolSandbox tool-name mapping must be a dictionary')
    initial_state = initial_state_from_context(context, module_loader)
    turns = tuple(
        GeneratorTurn(f"turn_{order}", instruction, source_ref)
        for order, (source_ref, instruction) in enumerate(turn_sources)
    )
    logger.info(
        'ToolSandbox generator view construction complete',
        extra={
            "event": "generator_view finished",
            "case_id": task_case.case_id,
            "turn_count": len(turns),
            "initial_namespace_count": len(initial_state.get("namespaces", {})),
            "visible_tool_count": len(tool_evidence),
        },
    )
    return GeneratorTaskView(
        benchmark="toolsandbox",
        task_id=task_case.task_id,
        case_id=task_case.case_id,
        language=str(config.metadata.get("language") or "en"),
        turns=turns,
        public_assets=public_assets,
        public_state={},
        simulation_state=initial_state,
        tool_schema=tool_schema,
        tool_contracts=toolsandbox_tool_contracts({str(key): str(value) for key, value in tool_name_mapping.items()}),
        environment_rules=toolsandbox_environment_rules({str(key): str(value) for key, value in tool_name_mapping.items()}),
        evidence_catalog=tuple(tool_evidence),
    )


def _interaction_plan_from_steps(
    steps: list[dict[str, object]], boundary: int | None
) -> tuple[list[JsonObject], list[tuple[str, str]]]:
    assets: list[JsonObject] = []
    turns: list[tuple[str, str]] = [("instruction", "")]
    first_user_found = False
    simulator_steps = [
        step for step in steps
        if step.get("actor") == Actor.SYSTEM.value
        and step.get("recipient") == Actor.USER.value
        and isinstance(step.get("content"), str)
        and (
            boundary is None
            or not isinstance(step.get("raw_sandbox_message_index"), int)
            or int(step["raw_sandbox_message_index"]) <= boundary
        )
    ]
    selected_simulator = max(
        simulator_steps,
        key=lambda item: int(item.get("raw_sandbox_message_index", -1)),
        default=None,
    )
    for step in steps:
        actor = step.get("actor")
        recipient = step.get("recipient")
        content = step.get("content")
        sandbox_index = step.get("raw_sandbox_message_index")
        if not isinstance(content, str) or not content.strip():
            continue
        if step is not selected_simulator and boundary is not None and isinstance(sandbox_index, int) and sandbox_index < boundary:
            continue
        if recipient == Actor.AGENT.value and actor in {Actor.SYSTEM.value, Actor.USER.value}:
            index = int(step.get("index", len(assets)))
            source_ref = f"message:{index}:content"
            assets.append({
                "source_ref": source_ref,
                "value": content,
                "actor": str(actor),
                "recipient": Actor.AGENT.value,
                "visibility": "agent",
                "turn_index": index,
            })
            if actor == Actor.USER.value and not first_user_found:
                turns[0] = ("instruction", content)
                first_user_found = True
        elif actor == Actor.SYSTEM.value and recipient == Actor.USER.value:
            index = int(step.get("index", len(assets)))
            source_ref = f"simulator:{index}:content"
            assets.append({
                "source_ref": source_ref,
                "value": content,
                "actor": Actor.SYSTEM.value,
                "recipient": Actor.USER.value,
                "visibility": "evaluator_only",
                "turn_index": index,
            })
            for turn_instruction in _simulator_turn_instructions(content):
                turns.append((source_ref, turn_instruction))
    return assets, turns


def _simulator_turn_instructions(content: str) -> list[str]:
    if re.search(r"\bAfter User B did so,", content, flags=re.IGNORECASE) is None:
        return []
    match = re.search(r"following task .*? complete:\s*(.+)$", content, flags=re.IGNORECASE | re.DOTALL)
    task = match.group(1).strip() if match is not None else content.strip()
    parts = re.split(r"\bAfter User B did so,\s*", task, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _visible_to_agent(value: object) -> bool:
    if value is None:
        return True
    return any(enum_name(item) == "AGENT" for item in value)
