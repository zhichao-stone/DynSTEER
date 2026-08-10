from __future__ import annotations

from collections.abc import Callable

from dynsteer.adapter.contract import (
    base_public_evidence,
    build_public_invariants,
    normalize_tool_contract,
)
from dynsteer.adapter.toolsandbox.utils.state import sandbox_rows_from_context
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import Actor, JsonObject, TaskCase
from dynsteer.utils import enum_name, json_safe


def agent_facing_tool_schema(
    context: object,
    module_loader: Callable[[str], object],
) -> JsonObject:
    """提取 allow/deny/扰动后且 Agent 可见的最终工具 schema。"""
    if context is None or module_loader is None:
        raise ValueError("context 和 module_loader 不能为空")
    available = context.get_available_tools(scrambling_allowed=True)
    if not isinstance(available, dict):
        raise TypeError("context.get_available_tools 必须返回字典")
    visible = {
        name: tool
        for name, tool in available.items()
        if _visible_to_agent(getattr(tool, "visible_to", None))
    }
    conversion = module_loader("tool_sandbox.common.tool_conversion")
    convert = getattr(conversion, "convert_to_openai_tools", None)
    if not callable(convert):
        raise TypeError("ToolSandbox 缺少 convert_to_openai_tools")
    tools = json_safe(convert(visible))
    if not isinstance(tools, list):
        raise TypeError("convert_to_openai_tools 必须返回数组")
    return {"tools": tools}


def build_toolsandbox_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    context: object,
    module_loader: Callable[[str], object],
) -> GeneratorTaskView:
    """从 Agent 可见消息和工具构造 ToolSandbox 公开生成视图。"""
    if config is None or task_case is None or context is None or module_loader is None:
        raise ValueError("config、task_case、context 和 module_loader 不能为空")
    rows = sandbox_rows_from_context(context, module_loader)
    steps = sandbox_rows_to_step_dicts(rows)
    public_assets: list[JsonObject] = []
    for step in steps:
        if step.get("actor") not in {Actor.SYSTEM.value, Actor.USER.value}:
            continue
        if not _message_visible_to_agent(step.get("visible_to")):
            continue
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        index = len(public_assets)
        public_assets.append(
            {
                "source_ref": f"message:{index}:content",
                "value": content,
                "actor": str(step["actor"]),
            }
        )

    raw_tool_schema = agent_facing_tool_schema(context, module_loader)
    tool_schema, tool_evidence = normalize_tool_contract(raw_tool_schema)
    public_texts = [
        str(asset["value"])
        for asset in public_assets
        if isinstance(asset.get("value"), str)
    ]
    invariant_assets, invariant_evidence, invariants = build_public_invariants(
        [task_case.task_description, *public_texts]
    )
    public_assets.extend(invariant_assets)
    evidence = [
        *base_public_evidence(),
        *tool_evidence,
        *invariant_evidence,
    ]
    return GeneratorTaskView(
        benchmark="toolsandbox",
        task_id=task_case.task_id,
        case_id=task_case.case_id,
        language=str(config.metadata.get("language") or "en"),
        instruction=task_case.task_description,
        public_assets=public_assets,
        tool_schema=tool_schema,
        environment_schema={
            "source": {"source_ref": "environment:source", "value": "toolsandbox"},
            "stateful": {"source_ref": "environment:stateful", "value": True},
        },
        output_contract={},
        evidence_catalog=tuple(evidence),
        invariant_catalog=tuple(invariants),
    )


def _visible_to_agent(value: object) -> bool:
    if value is None:
        return True
    return any(enum_name(item) == "AGENT" for item in value)


def _message_visible_to_agent(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, list) and any(
        str(item).upper().endswith("AGENT") for item in value
    )
