from __future__ import annotations

from importlib import import_module

__all__ = [
    "DynSTEEREvaluator",
    "JudgeConfigurationError",
    "HarnessTeardownError",
    "blocked_milestone_termination_reason",
    "evaluate_agent_step",
    "evaluate_checkpoint",
    "evaluate_step_minefields",
    "finish_settlement",
    "pending_milestone_stage_results",
    "ready_frontier_no_progress_termination_reason",
    "runtime_diagnostics_summary",
    "scoring_context",
    "selected_candidate_from_attempt",
    "task_case_snapshot",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "DynSTEEREvaluator": (".evaluator", "DynSTEEREvaluator"),
    "JudgeConfigurationError": (".runtime", "JudgeConfigurationError"),
    "HarnessTeardownError": (".runtime", "HarnessTeardownError"),
    "blocked_milestone_termination_reason": (".runtime", "blocked_milestone_termination_reason"),
    "evaluate_agent_step": (".step", "evaluate_agent_step"),
    "evaluate_checkpoint": (".settlement", "evaluate_checkpoint"),
    "evaluate_step_minefields": (".step", "evaluate_step_minefields"),
    "finish_settlement": (".settlement", "finish_settlement"),
    "pending_milestone_stage_results": (".runtime", "pending_milestone_stage_results"),
    "ready_frontier_no_progress_termination_reason": (".runtime", "ready_frontier_no_progress_termination_reason"),
    "runtime_diagnostics_summary": (".runtime", "runtime_diagnostics_summary"),
    "scoring_context": (".runtime", "scoring_context"),
    "selected_candidate_from_attempt": (".runtime", "selected_candidate_from_attempt"),
    "task_case_snapshot": (".runtime", "task_case_snapshot"),
}


def __getattr__(name: str) -> object:
    exported = _EXPORTS.get(name)
    if exported is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(exported[0], __name__)
    value = getattr(module, exported[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
