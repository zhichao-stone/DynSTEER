from __future__ import annotations

import hashlib
import re

from dynsteer.milestone.model import PublicEvidence, PublicInvariant
from dynsteer.model import ConstraintTarget, JsonObject, Operator
from dynsteer.utils import json_safe


_INVARIANT_PATTERN = re.compile(
    r"\b(?:must not|do not|never)\b|禁止|不得|严禁", re.IGNORECASE
)


def stable_contract_slug(value: str) -> str:
    """根据公开契约值生成稳定且抗冲突的标识。"""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
    return f"{normalized or 'item'}_{digest}"


def base_public_evidence() -> list[PublicEvidence]:
    """返回所有 benchmark 共用的基础公开 evidence。"""
    return [
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


def normalize_tool_contract(
    tool_schema: JsonObject,
) -> tuple[JsonObject, list[PublicEvidence]]:
    """复制公开工具 schema，并返回稳定的工具 evidence。"""
    normalized = json_safe(tool_schema)
    if not isinstance(normalized, dict):
        raise TypeError("tool_schema 必须是对象")
    evidence: list[PublicEvidence] = []
    seen_names: set[str] = set()
    tools = normalized.get("tools")
    if not isinstance(tools, list):
        return normalized, evidence
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        raw_name = function.get("name") if isinstance(function, dict) else tool.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = raw_name.strip()
        if name in seen_names:
            raise ValueError(f"工具名重复: {name}")
        seen_names.add(name)
        slug = stable_contract_slug(name)
        source_ref = f"tool:{slug}:name"
        tools[index] = {**tool, "source_ref": source_ref, "value": name}
        evidence.append(
            PublicEvidence(
                evidence_id=f"tool_call_{slug}",
                target=ConstraintTarget.TOOL_CALL,
                selector="$.name",
                operator=Operator.EQUALS,
                source_ref=source_ref,
            )
        )
    return normalized, evidence


def build_public_invariants(
    contents: list[str],
) -> tuple[list[JsonObject], list[PublicEvidence], list[PublicInvariant]]:
    """从公开文本提取去重的不变量及其可执行 evidence。"""
    assets: list[JsonObject] = []
    evidence: list[PublicEvidence] = []
    invariants: list[PublicInvariant] = []
    seen: set[str] = set()
    for content in contents:
        for line in content.splitlines():
            rule = line.strip(" -\t")
            if not rule or rule in seen or not _INVARIANT_PATTERN.search(rule):
                continue
            seen.add(rule)
            slug = stable_contract_slug(rule)
            source_ref = f"invariant:{slug}"
            evidence_id = f"invariant_message_{slug}"
            invariant_id = f"invariant_{slug}"
            assets.append(
                {"kind": "invariant_source", "source_ref": source_ref, "value": rule}
            )
            evidence.append(
                PublicEvidence(
                    evidence_id=evidence_id,
                    target=ConstraintTarget.STEP,
                    selector="$.content",
                    operator=Operator.CONTAINS,
                    source_ref=source_ref,
                )
            )
            invariants.append(
                PublicInvariant(
                    invariant_id=invariant_id,
                    description=rule,
                    source_ref=source_ref,
                    evidence_id=evidence_id,
                )
            )
    return assets, evidence, invariants
