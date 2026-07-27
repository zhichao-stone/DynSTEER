from __future__ import annotations

from importlib import import_module

__all__ = [
    "advance_milestone_frontier",
    "analyze_milestone_step",
    "boundary_snapshot",
    "boundary_step",
    "blocked_candidate_milestones",
    "candidate_boundary_for_current_step",
    "evaluate_minefields_at_boundary",
    "initialize_milestone_frontier",
    "milestone_scoring_step",
    "ready_milestones",
    "ready_milestone_ids",
    "stage_start_for_ready_milestone",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "advance_milestone_frontier": (".frontier", "advance_milestone_frontier"),
    "analyze_milestone_step": (".milestone", "analyze_milestone_step"),
    "boundary_snapshot": (".boundary", "boundary_snapshot"),
    "boundary_step": (".boundary", "boundary_step"),
    "blocked_candidate_milestones": (".frontier", "blocked_candidate_milestones"),
    "candidate_boundary_for_current_step": (".boundary", "candidate_boundary_for_current_step"),
    "evaluate_minefields_at_boundary": (".minefield", "evaluate_minefields_at_boundary"),
    "initialize_milestone_frontier": (".frontier", "initialize_milestone_frontier"),
    "milestone_scoring_step": (".milestone", "milestone_scoring_step"),
    "ready_milestones": (".frontier", "ready_milestones"),
    "ready_milestone_ids": (".frontier", "ready_milestone_ids"),
    "stage_start_for_ready_milestone": (".milestone", "stage_start_for_ready_milestone"),
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
