from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import HarnessRunConfig

def select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness) -> list[str]:
    """根据配置选择要运行的 benchmark case ID。"""
    if config is None or harness is None:
        raise ValueError("config 和 harness 不能为空")
    if config.case_ids is not None:
        return list(config.case_ids)
    case_ids = harness.list_case_ids(config)
    if not case_ids:
        raise ValueError("benchmark 没有可运行场景")
    return case_ids
