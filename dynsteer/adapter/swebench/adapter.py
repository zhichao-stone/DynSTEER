from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase

_SWEBENCH_NOT_READY = "SWE-bench Pro 仓库、dataset schema 和 runner 尚未接入，等待外部仓库到位后补实现"

class SwebenchProAdapter(BaseBenchmarkAdapter):
    """SWE-bench Pro 数据适配器占位实现。"""
    benchmark = "swebench_pro"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        """将 SWE-bench Pro instance 转换为 DynSTEER TaskCase。

        入参：
            config: 当前 harness 配置。
            case_id: SWE-bench Pro instance ID。
        输出：
            后续真实实现返回 TaskCase；当前阶段抛出未实现错误。
        """
        if config is None or not case_id:
            raise ValueError("config 和 case_id 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)
