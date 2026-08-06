from dynsteer.adapter.agentcompass.runtime import (
    AGENTCOMPASS_COMMIT,
    AgentCompassTaskRecord,
)
from dynsteer.model import TaskCase


def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase:
    """将 AgentCompass 可见任务记录投影为 SkillsBench TaskCase。"""
    if record is None:
        raise ValueError("record 不能为空")
    return TaskCase(
        task_id=f"skillsbench::{record.task_id}",
        case_id=record.task_id,
        task_description=record.question,
        metadata={
            "benchmark": "skillsbench",
            "category": record.category,
            "agentcompass_commit": AGENTCOMPASS_COMMIT,
        },
    )
