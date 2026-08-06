from dynsteer.adapter.agentcompass.runtime import (
    AGENTCOMPASS_COMMIT,
    AgentCompassTaskRecord,
)
from dynsteer.adapter.agentcompass.result import task_case_from_record as build_task_case
from dynsteer.model import TaskCase


def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase:
    """将 AgentCompass 可见任务记录投影为 SkillsBench TaskCase。"""
    return build_task_case(
        record,
        benchmark="skillsbench",
        description=record.question,
        metadata={"agentcompass_commit": AGENTCOMPASS_COMMIT},
    )
