from dynsteer.adapter.agentcompass.runtime import (
    AGENTCOMPASS_COMMIT,
    AgentCompassTaskRecord,
)
from dynsteer.adapter.agentcompass.result import task_case_from_record as build_task_case
from dynsteer.model import TaskCase


def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase:
    """将 AgentCompass 可见任务记录投影为 SWE-bench Pro TaskCase。"""
    sections = [record.question]
    requirements = record.metadata.get("requirements")
    interface = record.metadata.get("interface")
    if requirements:
        sections.append(f"Requirements:\n{requirements}")
    if interface:
        sections.append(f"New interfaces introduced:\n{interface}")
    return build_task_case(
        record,
        benchmark="swebench_pro",
        description="\n\n".join(sections),
        metadata={
            "repo": record.metadata.get("repo"),
            "base_commit": record.metadata.get("base_commit"),
            "agentcompass_commit": AGENTCOMPASS_COMMIT,
        },
    )
