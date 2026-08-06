from __future__ import annotations

import re
from collections.abc import Callable

from dynsteer.adapter.toolsandbox.utils.state import sandbox_rows_from_context
from dynsteer.adapter.toolsandbox.utils.trace import sandbox_rows_to_step_dicts
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, PublicEvidence, PublicInvariant
from dynsteer.model import Actor, ConstraintTarget, JsonObject, Operator, TaskCase
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

    tool_schema = agent_facing_tool_schema(context, module_loader)
    evidence: list[PublicEvidence] = [
        PublicEvidence(
            evidence_id="agent_message_instruction",
            target=ConstraintTarget.STEP,
            selector="$.content",
            operator=Operator.FUZZY_MATCH,
            source_ref="instruction",
        ),
        PublicEvidence(
            evidence_id="tool_result_present",
            target=ConstraintTarget.TOOL_RESULT,
            selector="$",
            operator=Operator.ADDED,
            source_ref="instruction",
            expected_policy="none",
        ),
    ]
    tools = tool_schema.get("tools")
    if isinstance(tools, list):
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            source_ref = f"tool:{index}:name"
            tool["source_ref"] = source_ref
            tool["value"] = name
            evidence.append(
                PublicEvidence(
                    evidence_id=f"tool_call_{index}",
                    target=ConstraintTarget.TOOL_CALL,
                    selector="$.name",
                    operator=Operator.EQUALS,
                    source_ref=source_ref,
                )
            )
    invariants = _public_invariants(public_assets, evidence)
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


def _public_invariants(
    assets: list[JsonObject], evidence: list[PublicEvidence]
) -> list[PublicInvariant]:
    result: list[PublicInvariant] = []
    for asset in tuple(assets):
        content = asset.get("value")
        if not isinstance(content, str):
            continue
        rules = [
            line.strip(" -\t")
            for line in content.splitlines()
            if re.search(
                r"\b(?:must not|do not|never)\b|禁止|不得|严禁", line, re.IGNORECASE
            )
        ]
        for rule in rules:
            index = len(result)
            source_ref = f"invariant:{index}"
            assets.append({"source_ref": source_ref, "value": rule, "actor": "system"})
            evidence_id = f"invariant_message_{index}"
            evidence.append(
                PublicEvidence(
                    evidence_id=evidence_id,
                    target=ConstraintTarget.STEP,
                    selector="$.content",
                    operator=Operator.CONTAINS,
                    source_ref=source_ref,
                )
            )
            result.append(
                PublicInvariant(
                    invariant_id=f"invariant_{index}",
                    description=rule,
                    source_ref=source_ref,
                    evidence_id=evidence_id,
                )
            )
    return result
