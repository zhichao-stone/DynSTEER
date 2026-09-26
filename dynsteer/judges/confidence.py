import math
from collections import Counter
from collections.abc import Iterable
from dynsteer.model import Dimension, JsonObject, StageStatus, ValidatedJudgePayload
from dynsteer.utils import clamp, clean_evidence_items

def complete_dimension_confidence(dimensions: Iterable[Dimension], confidence: dict[Dimension, float] | None=None, default: float=0.65) -> dict[Dimension, float]:
    """Completing the confidence dictionary for the specified dimensions. Args: dimensions: The dimension pool needs to be filled with the confidence. Confidence: The confidence is already available and can be empty. default: the default confidence dictionary. Returns: The confidence dictionary for the specified dimensions is covered, with all values in [0,1]."""
    source = confidence or {}
    return {dimension: clamp(float(source.get(dimension, default))) for dimension in dimensions}

def uncertainty_from_confidence(confidence: dict[Dimension, float]) -> dict[Dimension, float]:
    """Calculates the level of uncertainty per dimension by its confidence level."""
    return {dimension: clamp(1.0 - value) for dimension, value in confidence.items()}

def cheap_dimension_confidence(status: StageStatus, missing_ratio: float, diagnostics: JsonObject) -> dict[Dimension, float]:
    """Generates confidence on a dimensional basis based on a structured cheap diagnosis. Hard fail/missing is a definitive failure sign and cannot be interpreted as a high degree of uncertainty."""
    warning_count = _warning_count(diagnostics)
    weak_rule_penalty = min(warning_count, 5) * 0.03
    missing_penalty = 0.0 if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID} else clamp(missing_ratio) * 0.12
    base = {
        Dimension.PROGRESS: 0.92,
        Dimension.STATE_CONSISTENCY: 0.82,
        Dimension.TOOL_QUALITY: 0.78,
        Dimension.EFFICIENCY: 0.62,
        Dimension.SAFETY: 0.84,
        Dimension.INTERACTION_QUALITY: 0.48,
        Dimension.RECOVERY: 0.55,
    }
    if status in {StageStatus.MISSING, StageStatus.INVALID}:
        base[Dimension.PROGRESS] = 0.9
        base[Dimension.STATE_CONSISTENCY] = 0.78
    return {dimension: clamp(value - weak_rule_penalty - missing_penalty) for dimension, value in base.items()}

def agreement_confidence(payloads: list[ValidatedJudgePayload], dimensions: Iterable[Dimension]) -> dict[Dimension, float]:
    """Use multiple judge fractional anchor consistency estimates per dimension. Inputs: payloads: deconstructed judge JSON payloads: This round needs to estimate the dimension of confidence. Returns: the dimension of each target dimension. This value represents only a multipass consistency and is not equal to the true correct rate."""
    result: dict[Dimension, float] = {}
    dimension_scores = _dimension_scores(payloads, dimensions)
    for dimension in dimensions:
        anchors = [f"{round(clamp(score) * 4) / 4:.2f}" for score in dimension_scores.get(dimension, []) if score is not None]
        if len(anchors) <= 1:
            result[dimension] = 0.62
            continue
        result[dimension] = clamp(1.0 - _normalized_entropy(anchors))
    return result

def aggregate_judge_payload(payloads: list[ValidatedJudgePayload], dimensions: Iterable[Dimension]) -> ValidatedJudgePayload:
    target_dimensions = list(dimensions)
    return ValidatedJudgePayload(
        status=aggregate_status(payloads),
        dimension_scores=aggregate_dimension_scores(payloads, target_dimensions),
        evidence=merge_text_items(payloads, "evidence"),
        diagnosis=merge_text_items(payloads, "diagnosis"),
        metadata={
            "judge_pass_count": len(payloads),
            "judge_passes": [dict(payload.metadata) for payload in payloads],
        },
    )

def aggregate_dimension_scores(payloads: list[ValidatedJudgePayload], dimensions: Iterable[Dimension]) -> dict[Dimension, float]:
    """Aggregate scores across judge passes by dimension, using the mean as the stabilization rule."""
    if payloads is None or dimensions is None:
        raise ValueError('Judge payloads and dimensions cannot be empty')
    result: dict[Dimension, float] = {}
    dimension_scores = _dimension_scores(payloads, dimensions)
    for dimension in dimensions:
        values = [score for score in dimension_scores.get(dimension, []) if score is not None]
        if not values:
            raise ValueError(f"Missing scores for dimension {dimension.value}")
        result[dimension] = clamp(sum(values) / len(values))
    return result

def aggregate_status(payloads: list[ValidatedJudgePayload]) -> StageStatus:
    """Aggregation of judge stage states, giving priority to return to more serious state."""
    if payloads is None or not payloads:
        raise ValueError('Judge payloads cannot be empty.')
    statuses = [payload.status for payload in payloads]
    severity = {
        StageStatus.INVALID: 5,
        StageStatus.MISSING: 4,
        StageStatus.FAIL: 3,
        StageStatus.AMBIGUOUS: 2,
        StageStatus.WARN: 1,
        StageStatus.PASS: 0,
    }
    return max(statuses, key=lambda status: severity[status])

def merge_text_items(payloads: list[ValidatedJudgePayload], key: str, limit: int=6) -> list[str]:
    """Merge event/diagnosis text from multiple judge payloads."""
    if payloads is None or key is None:
        raise ValueError('Payloads and key cannot be empty')
    items: list[str] = []
    for payload in payloads:
        value = getattr(payload, key)
        for item in value:
            text = str(item)
            if text and text not in items:
                items.append(text)
            if len(items) >= limit:
                return clean_evidence_items(items, limit) if key == "evidence" else items
    return clean_evidence_items(items, limit) if key == "evidence" else items

def _dimension_scores(payloads: list[ValidatedJudgePayload], dimensions: Iterable[Dimension]) -> dict[Dimension, list[float]]:
    dimension_scores: dict[Dimension, list[float]] = {dimension: [] for dimension in dimensions}
    for payload in payloads:
        for dimension in dimensions:
            if dimension in payload.dimension_scores:
                dimension_scores[dimension].append(payload.dimension_scores[dimension])
    return dimension_scores

def _normalized_entropy(values: list[str]) -> float:
    counts = Counter(values)
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        probability = count / total
        entropy -= probability * math.log(probability)
    return entropy / math.log(len(counts))

def _warning_count(diagnostics: JsonObject) -> int:
    value = diagnostics.get("warning_count")
    if not isinstance(value, int):
        return 0
    return max(value, 0)
