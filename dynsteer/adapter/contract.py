from __future__ import annotations

import hashlib
import re

from dynsteer.milestone.model import PublicEvidence
from dynsteer.model import Actor, ConstraintTarget, JsonObject, Operator
from dynsteer.utils import json_safe


def stable_contract_slug(value: str) -> str:
    """Create a stable and conflict-resistant logo based on open contract values."""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
    return f"{normalized or 'item'}_{digest}"


def normalize_tool_contract(
    tool_schema: JsonObject,
) -> tuple[JsonObject, list[PublicEvidence]]:
    """Scema, a single walk-through tool to supplement the source and generate the exact tool to call the event."""
    normalized = json_safe(tool_schema)
    if not isinstance(normalized, dict):
        raise TypeError('Tol_schema must be the object')
    evidence: list[PublicEvidence] = []
    seen_names: set[str] = set()
    tools = normalized.get("tools")
    if not isinstance(tools, list):
        return normalized, evidence
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise TypeError(f"tools[{index}]] Must be the object")
        function = tool.get("function")
        raw_name = function.get("name") if isinstance(function, dict) else tool.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError(f"tools[{index}] is missing a tool name")
        name = raw_name.strip()
        if name in seen_names:
            raise ValueError(f"Tool name repeats:{name}")
        seen_names.add(name)
        slug = stable_contract_slug(name)
        source_ref = f"tool:{slug}:name"
        tools[index] = {**tool, "source_ref": source_ref, "value": name}
        evidence.append(PublicEvidence(
            evidence_id=f"tool_call_{slug}",
            target=ConstraintTarget.TOOL_CALL,
            selector="$.name",
            operator=Operator.EQUALS,
            source_ref=source_ref,
            matching_route=(Actor.AGENT, Actor.ENVIRONMENT),
            metadata={"tool_name": name},
        ))
    return normalized, evidence
