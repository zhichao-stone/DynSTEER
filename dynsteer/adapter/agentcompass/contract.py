from __future__ import annotations

from dynsteer.adapter.contract import normalize_tool_contract
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView, GeneratorTurn
from dynsteer.model import JsonObject, TaskCase
from dynsteer.utils import json_safe


def build_agentcompass_generator_view(
    config: HarnessRunConfig,
    task_case: TaskCase,
    *,
    environment_schema: JsonObject,
    output_contract: JsonObject,
) -> GeneratorTaskView:
    """构造 SWE-bench Pro 与 SkillsBench 共用的最小公开生成视图。"""
    if config is None or task_case is None:
        raise ValueError("config 和 task_case 不能为空")
    tool_schema, tool_evidence = normalize_tool_contract(task_case.tool_schema)
    public_assets = [
        {"source_ref": f"environment:{key}", "value": json_safe(value)}
        for key, value in environment_schema.items()
    ]
    public_assets.extend(
        {"source_ref": f"output:{key}", "value": json_safe(value)}
        for key, value in output_contract.items()
    )
    return GeneratorTaskView(
        benchmark=config.benchmark,
        task_id=task_case.task_id,
        case_id=task_case.case_id,
        language=str(config.metadata.get("language") or "en"),
        turns=(GeneratorTurn("turn_0", task_case.task_description, "instruction"),),
        public_assets=public_assets,
        public_state=task_case.initial_state or {},
        simulation_state={},
        tool_schema=tool_schema,
        tool_contracts={},
        environment_rules={},
        evidence_catalog=tuple(tool_evidence),
    )
