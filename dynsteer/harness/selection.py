from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import TaskCase

def select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness, run_all: bool) -> list[str]:
    """根据配置选择要运行的 benchmark case ID。"""
    if config is None or harness is None:
        raise ValueError('config 和 harness 不能为空')
    if config.case_ids is not None:
        return list(config.case_ids)
    cases = harness.list_cases(config)
    if not cases:
        raise ValueError('benchmark 没有可运行场景')
    if run_all:
        return [case.case_id for case in cases]
    return [cases[0].case_id]

def validate_loaded_task_cases(case_ids: list[str], task_cases: list[TaskCase]) -> None:
    """校验 loader 返回的 TaskCase 与当前 config case 顺序一致。"""
    if case_ids is None or task_cases is None:
        raise ValueError('case_ids 和 task_cases 不能为空')
    loaded_case_ids = [task_case.case_id for task_case in task_cases]
    if loaded_case_ids != case_ids:
        raise ValueError(f'加载的 TaskCase 顺序与配置不一致: {loaded_case_ids}')
