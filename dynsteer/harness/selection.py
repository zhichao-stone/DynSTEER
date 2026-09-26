from dynsteer.adapter.base import BaseBenchmarkHarness
from dynsteer.harness.model import HarnessRunConfig

def select_case_ids(config: HarnessRunConfig, harness: BaseBenchmarkHarness) -> list[str]:
    """The benchmark case ID that you select to run according to configuration."""
    if config is None or harness is None:
        raise ValueError('Config and harness cannot be empty')
    if config.case_ids is not None:
        available_case_ids = harness.list_case_ids(config)
        available = set(available_case_ids)
        missing = [case_id for case_id in config.case_ids if case_id not in available]
        if missing:
            preview = ", ".join(available_case_ids[:20])
            raise ValueError(f"Case ID does not exist:{missing}; with case ID examples:{preview}")
        return list(config.case_ids)

    case_ids = harness.list_case_ids(config)
    if not case_ids:
        raise ValueError("There's no running scene for benchmark.")
    return case_ids
