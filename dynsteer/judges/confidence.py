import math
from collections import Counter
from collections.abc import Iterable
from dynsteer.model import Dimension, JsonObject, StageStatus
from dynsteer.utils import clamp, clean_evidence_items

def complete_dimension_confidence(dimensions: Iterable[Dimension], confidence: dict[Dimension, float] | None=None, default: float=0.65) -> dict[Dimension, float]:
    """补齐指定维度的置信度字典。

    入参：
        dimensions: 需要补齐置信度的维度集合。
        confidence: 已有置信度，可为空。
        default: 缺省置信度。
    输出：
        覆盖指定维度的置信度字典，所有值都在 [0,1] 内。
    """
    source = confidence or {}
    return {dimension: clamp(float(source.get(dimension, default))) for dimension in dimensions}

def uncertainty_from_confidence(confidence: dict[Dimension, float]) -> dict[Dimension, float]:
    """由逐维置信度计算逐维不确定度。"""
    return {dimension: clamp(1.0 - value) for dimension, value in confidence.items()}

def cheap_dimension_confidence(status: StageStatus, missing_ratio: float, diagnostics: JsonObject) -> dict[Dimension, float]:
    """根据结构化 cheap 诊断生成逐维置信度。

    hard fail / missing 是确定性失败信号，不会被解释为高不确定度。
    """
    warning_count = _warning_count(diagnostics)
    weak_rule_penalty = min(warning_count, 5) * 0.03
    missing_penalty = 0.0 if status in {StageStatus.FAIL, StageStatus.MISSING, StageStatus.INVALID} else clamp(missing_ratio) * 0.12
    base = {Dimension.PROGRESS: 0.92, Dimension.STATE_CONSISTENCY: 0.82, Dimension.TOOL_QUALITY: 0.78, Dimension.EFFICIENCY: 0.62, Dimension.SAFETY: 0.84, Dimension.INTERACTION_QUALITY: 0.48, Dimension.RECOVERY: 0.55}
    if status in {StageStatus.MISSING, StageStatus.INVALID}:
        base[Dimension.PROGRESS] = 0.9
        base[Dimension.STATE_CONSISTENCY] = 0.78
    return {dimension: clamp(value - weak_rule_penalty - missing_penalty) for dimension, value in base.items()}

def agreement_confidence(payloads: list[JsonObject], dimensions: Iterable[Dimension]) -> dict[Dimension, float]:
    """用多次 judge 的分数锚点一致性估计逐维置信度。

    入参：
        payloads: 已解析的 judge JSON payload。
        dimensions: 本轮需要估计置信度的维度。
    输出：
        每个目标维度的 agreement confidence。该值只表示多 pass 一致性，不等同于真实正确率。
    """
    result: dict[Dimension, float] = {}
    dimension_scores = _dimension_scores(payloads, dimensions)
    for dimension in dimensions:
        anchors = [f'{round(clamp(score) * 4) / 4:.2f}' for score in dimension_scores.get(dimension, []) if score is not None]
        if len(anchors) <= 1:
            result[dimension] = 0.62
            continue
        result[dimension] = clamp(1.0 - _normalized_entropy(anchors))
    return result

def aggregate_judge_payload(payloads: list[JsonObject], dimensions: Iterable[Dimension]) -> JsonObject:
    target_dimensions = list(dimensions)
    return {'status': aggregate_status(payloads).value, 'dimension_scores': {dimension.value: score for dimension, score in aggregate_dimension_scores(payloads, target_dimensions).items()}, 'evidence': merge_text_items(payloads, 'evidence'), 'diagnosis': merge_text_items(payloads, 'diagnosis'), 'metadata': {'judge_pass_count': len(payloads)}}

def aggregate_dimension_scores(payloads: list[JsonObject], dimensions: Iterable[Dimension]) -> dict[Dimension, float]:
    """按维度聚合多次 judge 的分数，使用均值作为首版稳定规则。"""
    if payloads is None or dimensions is None:
        raise ValueError('judge payloads 和 dimensions 不能为空')
    result: dict[Dimension, float] = {}
    dimension_scores = _dimension_scores(payloads, dimensions)
    for dimension in dimensions:
        values = [score for score in dimension_scores.get(dimension, []) if score is not None]
        if not values:
            raise ValueError(f'缺少 {dimension.value} 维度分数')
        result[dimension] = clamp(sum(values) / len(values))
    return result

def aggregate_status(payloads: list[JsonObject]) -> StageStatus:
    """聚合多次 judge 的阶段状态，优先返回更严重的状态。"""
    if payloads is None or not payloads:
        raise ValueError('judge payloads 不能为空')
    statuses: list[StageStatus] = []
    for payload in payloads:
        try:
            statuses.append(StageStatus(str(payload.get('status'))))
        except ValueError:
            statuses.append(StageStatus.INVALID)
    severity = {StageStatus.INVALID: 5, StageStatus.MISSING: 4, StageStatus.FAIL: 3, StageStatus.AMBIGUOUS: 2, StageStatus.WARN: 1, StageStatus.PASS: 0}
    return max(statuses, key=lambda status: severity[status])

def merge_text_items(payloads: list[JsonObject], key: str, limit: int=6) -> list[str]:
    """从多个 judge payload 中合并 evidence/diagnosis 文本。"""
    if payloads is None or key is None:
        raise ValueError('payloads 和 key 不能为空')
    items: list[str] = []
    for payload in payloads:
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item)
            if text and text not in items:
                items.append(text)
            if len(items) >= limit:
                return clean_evidence_items(items, limit) if key == 'evidence' else items
    return clean_evidence_items(items, limit) if key == 'evidence' else items

def _dimension_scores(payloads: list[JsonObject], dimensions: list[Dimension]) -> dict[Dimension, list[float]] | None:
    dimension_scores: dict[Dimension, list[float]] = {dimension: [] for dimension in dimensions}
    for payload in payloads:
        scores = payload.get('dimension_scores')
        if not isinstance(scores, dict):
            for dimension in dimensions:
                dimension_scores[dimension].append(None)
        else:
            for dimension in dimensions:
                value = scores.get(dimension.value)
                dimension_scores[dimension].append(clamp(float(value)) if isinstance(value, (int, float)) else None)
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
    value = diagnostics.get('warning_count')
    if not isinstance(value, int):
        return 0
    return max(value, 0)
