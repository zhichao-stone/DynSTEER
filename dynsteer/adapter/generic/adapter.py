from __future__ import annotations

from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.loader import load_trajectory, parse_milestone_graph, parse_task_case
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import JsonObject, TaskCase, Trajectory, ensure_json_object

load_milestone_graph = parse_milestone_graph
load_task_case = parse_task_case


class GenericAdapter(BaseBenchmarkAdapter):
    """通用 adapted JSON 解析 adapter。

    generic 包只保留离线 JSON parser 能力，不再注册为主运行期 benchmark。
    """

    benchmark = "generic"

    # override 基类的函数实现

    def load_experiment(self, data: JsonObject) -> tuple[TaskCase, Trajectory]:
        """载入通用 JSON 实验数据。

        Args:
            data: 包含 task 与 trajectory 的实验字典。

        Returns:
            DynSTEER 任务与轨迹。
        """
        experiment = ensure_json_object(data)
        if experiment.get("task") is None or experiment.get("trajectory") is None:
            raise ValueError("通用实验数据必须包含 task 和 trajectory")
        return load_task_case(ensure_json_object(experiment["task"])), load_trajectory(
            ensure_json_object(experiment["trajectory"])
        )

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """generic 不负责从原生 benchmark 动态适配 TaskCase。"""
        if config is None or not case_id:
            raise ValueError("config 和 case_id 不能为空")
        task_case = config.metadata.get("task_case")
        if not isinstance(task_case, TaskCase):
            raise ValueError("GenericAdapter 需要在 metadata 中提供 task_case")
        if task_case.case_id != case_id:
            raise ValueError(f"TaskCase.case_id 与 case_id 不一致: {case_id}")
        return task_case

    def create_harness(self) -> BaseBenchmarkHarness:
        """创建通用逐步回放 harness。

        Returns:
            GenericHarness 实例。
        """
        from dynsteer.adapter.generic.harness import GenericHarness

        return GenericHarness()

    # GenericAdapter 独有函数实现
