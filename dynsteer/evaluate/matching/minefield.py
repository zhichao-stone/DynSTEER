from dataclasses import asdict
from dynsteer.evaluate.scoring import GeneralScorer
from dynsteer.model import JsonObject, MilestoneGraph, ScoringContext, StateSnapshot, Trajectory, TrajectoryStep
from dynsteer.utils import clamp

def evaluate_minefields_at_boundary(graph: MilestoneGraph, trajectory: Trajectory, scoring_step: TrajectoryStep, scorer: GeneralScorer, context: ScoringContext, evaluated_sources: set[str]) -> tuple[list[JsonObject], float, bool]:
    """Scanning minefield hits on a single runtime."""
    matches: list[JsonObject] = []
    max_score = 0.0
    fatal = False
    for minefield in graph.minefields:
        if minefield is None or not minefield.constraints:
            continue
        sources = [scorer.constraint_sources(constraint, scoring_step, trajectory, context) for constraint in minefield.constraints]
        source_identity = "|".join(f"{_source_identity(source, scoring_step)}@{_source_identity(reference, scoring_step)}" for source, reference in sources)
        fingerprint = f"{minefield.minefield_id}:{source_identity}"
        if fingerprint in evaluated_sources:
            continue
        evaluated_sources.add(fingerprint)
        weighted_sum = weight_sum = 0.0
        evidence: list[str] = []
        for constraint, (source, reference) in zip(minefield.constraints, sources, strict=True):
            score = scorer.score_constraint(constraint, source, reference, context=context)
            weight = max(float(constraint.weight), 0.0)
            weighted_sum += score.score * weight
            weight_sum += weight
            evidence.extend(score.evidence)
        minefield_score = clamp(weighted_sum / weight_sum if weight_sum > 0 else 0.0)
        if minefield_score <= 0.0:
            continue
        matches.append(
            {
                "minefield_id": minefield.minefield_id,
                "boundary_id": f"runtime:b{scoring_step.index}",
                "boundary_step_index": scoring_step.index,
                "source_identity": source_identity,
                "score": minefield_score,
                "severity": minefield.severity,
                "evidence": evidence,
                "penalty": asdict(minefield.penalty),
            }
        )
        max_score = max(max_score, minefield_score)
        if minefield.severity == "fatal" and minefield_score >= 1.0:
            fatal = True
    return (matches, max_score, fatal)


def _source_identity(source: object, fallback: TrajectoryStep) -> str:
    if source is None:
        return "none"
    if isinstance(source, TrajectoryStep):
        return f"step:{source.index}"
    if isinstance(source, StateSnapshot):
        return f"snapshot:{source.snapshot_id}"
    return f"step:{fallback.index}"
