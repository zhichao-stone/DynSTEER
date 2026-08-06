from dynsteer.adapter.agentcompass.contract import build_agentcompass_generator_view
from dynsteer.adapter.agentcompass.runtime import load_task_records
from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.swebench_pro.utils.task import task_case_from_record
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.milestone.model import GeneratorTaskView
from dynsteer.model import TaskCase


class SWEBenchProAdapter(BaseBenchmarkAdapter):
    """将 SWE-bench Pro 可见任务字段适配为 TaskCase。"""

    benchmark = "swebench_pro"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """按 case ID 执行 O(1) 查找并返回任务投影。"""
        if config is None or not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("config 和 case_id 不能为空")
        records = load_task_records(self.benchmark, config)
        try:
            record = records[case_id]
        except KeyError as exc:
            raise KeyError(f"SWE-bench Pro case 不存在: {case_id}") from exc
        return task_case_from_record(record)

    def generator_task_view(
        self, config: HarnessRunConfig, task_case: TaskCase, case_id: str
    ) -> GeneratorTaskView:
        """投影 SWE-bench Pro 的公开 workspace 与 diff 输出契约。"""
        if task_case.case_id != case_id:
            raise ValueError("task_case.case_id 与 case_id 不一致")
        return build_agentcompass_generator_view(
            config,
            task_case,
            environment_schema={"workspace": f"/app/{case_id}/repo"},
            output_contract={
                "path": f"/app/{case_id}/patch.txt",
                "format": "Only output a unified diff that solves the task.",
            },
        )
