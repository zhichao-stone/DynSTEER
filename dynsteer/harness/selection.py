from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import HarnessRunConfig

def select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness) -> list[str]:
    """根据配置选择要运行的 benchmark case ID。"""
    if config is None or harness is None:
        raise ValueError("config 和 harness 不能为空")
    if config.case_ids is not None:
        available_case_ids = harness.list_case_ids(config)
        available = set(available_case_ids)
        missing = [case_id for case_id in config.case_ids if case_id not in available]
        if missing:
            preview = ", ".join(available_case_ids[:20])
            raise ValueError(f"case ID 不存在: {missing}; 可用 case ID 示例: {preview}")
        return list(config.case_ids)

    case_ids = harness.list_case_ids(config)
    if not case_ids:
        raise ValueError("benchmark 没有可运行场景")
    return case_ids
