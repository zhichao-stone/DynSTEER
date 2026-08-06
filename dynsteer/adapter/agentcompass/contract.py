from __future__ import annotations

import re

from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, PublicEvidence, PublicInvariant
from dynsteer.model import ConstraintTarget, JsonObject, Operator, TaskCase
from dynsteer.utils import json_safe


def build_agentcompass_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    *,
    environment_schema: JsonObject,
    output_contract: JsonObject,
) -> GeneratorTaskView:
    """构造 SWE-bench Pro 与 SkillsBench 共用的公开生成视图。

    入参：运行配置、公开 TaskCase、环境 schema 与输出契约。
    输出：不含 ground truth、测试和 verifier 的 GeneratorTaskView。
    """
    if config is None or task_case is None:
        raise ValueError("config 和 task_case 不能为空")
    language = str(config.metadata.get("language") or "en")
    public_assets = _public_task_assets(task_case)
    normalized_output = _with_source_refs(output_contract, "output")
    tool_schema = json_safe(task_case.tool_schema)
    if not isinstance(tool_schema, dict):
        raise TypeError("task_case.tool_schema 必须是对象")
    evidence = _actf_evidence_catalog(tool_schema, normalized_output)
    invariants = _explicit_invariants(
        task_case.task_description, public_assets, evidence
    )
    return GeneratorTaskView(
        benchmark=config.benchmark,
        task_id=task_case.task_id,
        case_id=task_case.case_id,
        language=language,
        instruction=task_case.task_description,
        public_assets=public_assets,
        tool_schema=tool_schema,
        environment_schema=_with_source_refs(environment_schema, "environment"),
        output_contract=normalized_output,
        evidence_catalog=tuple(evidence),
        invariant_catalog=tuple(invariants),
    )


def _actf_evidence_catalog(
    tool_schema: JsonObject, output_contract: JsonObject
) -> list[PublicEvidence]:
    """返回两个 AgentCompass benchmark 共用的 ACTF 证据目录。"""
    evidence = [
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
            name = _tool_name(tool)
            if name is None:
                continue
            source_ref = f"tool:{index}:name"
            tools[index] = {**tool, "source_ref": source_ref, "value": name}
            evidence.append(
                PublicEvidence(
                    evidence_id=f"tool_call_{index}",
                    target=ConstraintTarget.TOOL_CALL,
                    selector="$.name",
                    operator=Operator.EQUALS,
                    source_ref=source_ref,
                )
            )
    for key, value in output_contract.items():
        if not isinstance(value, dict) or "source_ref" not in value:
            continue
        evidence.append(
            PublicEvidence(
                evidence_id=f"output_{key}",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                source_ref=str(value["source_ref"]),
            )
        )
    return evidence


def _public_task_assets(task_case: TaskCase) -> list[JsonObject]:
    assets: list[JsonObject] = []
    for key in ("repo", "base_commit"):
        value = task_case.metadata.get(key)
        if isinstance(value, (str, int, float, bool)) and value is not None:
            assets.append({"source_ref": f"task:{key}", "value": value})
    return assets


def _with_source_refs(value: JsonObject, prefix: str) -> JsonObject:
    result: JsonObject = {}
    for key, item in value.items():
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str):
            result[str(key)] = dict(item)
        else:
            result[str(key)] = {
                "source_ref": f"{prefix}:{key}",
                "value": json_safe(item),
            }
    return result


def _explicit_invariants(
    instruction: str,
    public_assets: list[JsonObject],
    evidence: list[PublicEvidence],
) -> list[PublicInvariant]:
    rules = [
        line.strip(" -\t")
        for line in instruction.splitlines()
        if re.search(
            r"\b(?:must not|do not|never)\b|禁止|不得|严禁", line, re.IGNORECASE
        )
    ]
    result: list[PublicInvariant] = []
    for index, rule in enumerate(rules):
        source_ref = f"invariant:{index}"
        public_assets.append({"source_ref": source_ref, "value": rule})
        evidence.append(
            PublicEvidence(
                evidence_id=f"invariant_message_{index}",
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
                evidence_id=f"invariant_message_{index}",
            )
        )
    return result


def _tool_name(tool: JsonObject) -> str | None:
    function = tool.get("function")
    value = function.get("name") if isinstance(function, dict) else tool.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None
