from __future__ import annotations

from dynsteer.adapter.contract import (
    base_public_evidence,
    build_public_invariants,
    normalize_tool_contract,
    stable_contract_slug,
)
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, PublicEvidence
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
    tool_schema, tool_evidence = normalize_tool_contract(task_case.tool_schema)
    normalized_output = _with_source_refs(output_contract, "output")
    invariant_assets, invariant_evidence, invariants = build_public_invariants(
        [task_case.task_description]
    )
    evidence = [
        *base_public_evidence(),
        *tool_evidence,
        *_output_evidence(normalized_output),
        *invariant_evidence,
    ]
    return GeneratorTaskView(
        benchmark=config.benchmark,
        task_id=task_case.task_id,
        case_id=task_case.case_id,
        language=language,
        instruction=task_case.task_description,
        public_assets=invariant_assets,
        tool_schema=tool_schema,
        environment_schema=_with_source_refs(environment_schema, "environment"),
        output_contract=normalized_output,
        evidence_catalog=tuple(evidence),
        invariant_catalog=tuple(invariants),
    )


def _output_evidence(output_contract: JsonObject) -> list[PublicEvidence]:
    result: list[PublicEvidence] = []
    for key, value in output_contract.items():
        if not isinstance(value, dict) or "source_ref" not in value:
            continue
        slug = stable_contract_slug(str(key))
        result.append(
            PublicEvidence(
                evidence_id=f"output_{slug}",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                source_ref=str(value["source_ref"]),
            )
        )
    return result


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
