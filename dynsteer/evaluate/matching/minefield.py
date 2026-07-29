from dataclasses import asdict
from dynsteer.evaluate.scoring import GeneralScorer, get_effective_scorer
from dynsteer.model import Boundary, JsonObject, MilestoneGraph, ScoringContext, Trajectory
from dynsteer.utils import clamp

def evaluate_minefields_at_boundary(graph: MilestoneGraph, trajectory: Trajectory, boundary: Boundary, scorer: GeneralScorer, context: ScoringContext | None) -> tuple[list[JsonObject], float, bool]:
    """在单个运行期 boundary 上扫描 minefield 命中情况。"""
    effective_scorer = get_effective_scorer(scorer)
    matches: list[JsonObject] = []
    max_score = 0.0
    fatal = False
    for minefield in graph.minefields:
        if minefield is None or not minefield.constraints:
            continue
        weighted_sum = 0.0
        weight_sum = 0.0
        evidence: list[str] = []
        for constraint in minefield.constraints:
            source, reference = effective_scorer.constraint_sources(constraint, boundary, trajectory, trajectory.snapshots)
            score = effective_scorer.score_constraint(constraint, source, reference, context=context)
            weight = max(float(constraint.weight), 0.0)
            weighted_sum += score.score * weight
            weight_sum += weight
            evidence.extend(score.evidence)
        minefield_score = clamp(weighted_sum / weight_sum if weight_sum > 0 else 0.0)
        if minefield_score <= 0.0:
            continue
        matches.append({'minefield_id': minefield.minefield_id, 'boundary_id': boundary.boundary_id, 'boundary_step_index': boundary.step_index, 'score': minefield_score, 'severity': minefield.severity, 'evidence': evidence, 'penalty': asdict(minefield.penalty)})
        max_score = max(max_score, minefield_score)
        if minefield.severity == 'fatal' and minefield_score >= 1.0:
            fatal = True
    return (matches, max_score, fatal)
