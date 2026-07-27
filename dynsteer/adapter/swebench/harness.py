from pathlib import Path

from dynsteer.adapter.base import BaseBenchmarkHarness, BenchmarkDefaultResult
from dynsteer.adapter.swebench.adapter import _SWEBENCH_NOT_READY
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import JsonObject


class SwebenchProHarness(BaseBenchmarkHarness):
    """SWE-bench Pro 运行期 harness 占位实现。"""

    benchmark = "swebench_pro"
    dependency_error_message = _SWEBENCH_NOT_READY

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        """列出 SWE-bench Pro cases。"""
        if config is None:
            raise ValueError("config 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object:
        """初始化 SWE-bench Pro case session。"""
        if config is None or not case_id or raw_output_dir is None:
            raise ValueError("config、case_id 和 raw_output_dir 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        """推进 SWE-bench Pro agent/runner。"""
        if session is None:
            raise ValueError("session 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)

    def case_finished(self, session: object) -> bool:
        """判断 SWE-bench Pro session 是否完成。"""
        if session is None:
            raise ValueError("session 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)

    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult:
        """提取 SWE-bench Pro 原生 resolved/default 结果。"""
        if session is None:
            raise ValueError("session 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)

    def metrics_from_session(self, session: object) -> JsonObject:
        """提取 SWE-bench Pro 运行期 metrics。"""
        if session is None:
            raise ValueError("session 不能为空")
        raise NotImplementedError(_SWEBENCH_NOT_READY)
