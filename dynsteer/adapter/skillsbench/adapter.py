from dynsteer.adapter.agentcompass.contract import build_agentcompass_generator_view
from dynsteer.adapter.agentcompass.result import adapt_record_case
from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.skillsbench.utils.task import task_case_from_record
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import TaskCase


class SkillsBenchAdapter(BaseBenchmarkAdapter):
    """将 SkillsBench 可见任务字段适配为 TaskCase。"""

    benchmark = "skillsbench"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """按 case ID 执行 O(1) 查找并返回任务投影。"""
        return adapt_record_case(self.benchmark, config, case_id, task_case_from_record)

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        """投影 SkillsBench 的公开 workspace 和空输出契约。"""
        if task_case.case_id != case_id:
            raise ValueError("task_case.case_id 与 case_id 不一致")
        return build_agentcompass_generator_view(
            config,
            task_case,
            environment_schema={"workspace": "/root"},
            output_contract={},
        )
