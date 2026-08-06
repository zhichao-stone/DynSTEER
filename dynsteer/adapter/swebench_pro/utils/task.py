from dynsteer.adapter.agentcompass.runtime import (
    AGENTCOMPASS_COMMIT,
    AgentCompassTaskRecord,
)
from dynsteer.model import TaskCase


def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase:
    """将 AgentCompass 可见任务记录投影为 SWE-bench Pro TaskCase。"""
    if record is None:
        raise ValueError("record 不能为空")
    sections = [record.question]
    requirements = record.metadata.get("requirements")
    interface = record.metadata.get("interface")
    if requirements:
        sections.append(f"Requirements:\n{requirements}")
    if interface:
        sections.append(f"New interfaces introduced:\n{interface}")
    return TaskCase(
        task_id=f"swebench_pro::{record.task_id}",
        case_id=record.task_id,
        task_description="\n\n".join(sections),
        metadata={
            "benchmark": "swebench_pro",
            "category": record.category,
            "repo": record.metadata.get("repo"),
            "base_commit": record.metadata.get("base_commit"),
            "agentcompass_commit": AGENTCOMPASS_COMMIT,
        },
    )
