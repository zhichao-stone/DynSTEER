from dynsteer.harness.model import (
    BenchmarkCase,
    HarnessAdvanceResult,
    HarnessRunConfig,
    HarnessRunResult,
    HarnessStageSettlement,
)


def __getattr__(name: str) -> object:
    if name == "BaseBenchmarkHarness":
        from dynsteer.adapter.base import BaseBenchmarkHarness

        return BaseBenchmarkHarness
    if name == "load_harness_run_configs":
        from dynsteer.harness.config import load_harness_run_configs

        return load_harness_run_configs
    if name in {
        "HarnessCaseExecutionError",
        "HarnessEvaluationOutput",
        "run_harness_case",
        "run_harness_cases",
        "run_harness_configs",
    }:
        from dynsteer.harness.runner import (
            HarnessCaseExecutionError,
            HarnessEvaluationOutput,
            run_harness_case,
            run_harness_cases,
            run_harness_configs,
        )

        return {
            "HarnessCaseExecutionError": HarnessCaseExecutionError,
            "HarnessEvaluationOutput": HarnessEvaluationOutput,
            "run_harness_case": run_harness_case,
            "run_harness_cases": run_harness_cases,
            "run_harness_configs": run_harness_configs,
        }[name]
    raise AttributeError(name)


__all__ = [
    "BenchmarkCase",
    "BaseBenchmarkHarness",
    "HarnessCaseExecutionError",
    "HarnessEvaluationOutput",
    "HarnessAdvanceResult",
    "HarnessRunConfig",
    "HarnessRunResult",
    "HarnessStageSettlement",
    "load_harness_run_configs",
    "run_harness_case",
    "run_harness_cases",
    "run_harness_configs",
]
