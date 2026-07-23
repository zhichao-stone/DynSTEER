from __future__ import annotations

from dataclasses import dataclass, field, replace

from dynsteer.model import (
    Boundary,
    Constraint,
    ConstraintScore,
    JsonObject,
    JsonValue,
    Milestone,
    MilestoneScore,
    StageGoalSemanticKind,
    StageStatus,
    Trajectory,
    TrajectoryStep,
)
from dynsteer.utils import clamp, compact_text, json_safe

SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD = 0.7
SEMANTIC_MESSAGE_CONTEXT_STEP_LIMIT = 12
SEMANTIC_MESSAGE_CONTEXT_SNAPSHOT_LIMIT = 4


@dataclass(frozen=True)
class SemanticMessageReviewTarget:
    """单条消息语义等价复判目标。"""

    constraint_id: str
    expected_sender: str
    expected_recipient: str
    actual_sender: str
    actual_recipient: str
    expected_content: str
    actual_content: str
    base_score: float
    supporting_context: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """转换为 JSON 诊断对象。"""
        payload: JsonObject = {
            "constraint_id": self.constraint_id,
            "route": {
                "expected_sender": self.expected_sender,
                "expected_recipient": self.expected_recipient,
                "actual_sender": self.actual_sender,
                "actual_recipient": self.actual_recipient,
            },
            "expected_content": self.expected_content,
            "actual_content": self.actual_content,
            "base_score": self.base_score,
        }
        if self.supporting_context:
            payload["supporting_context"] = dict(self.supporting_context)
        return payload


@dataclass(frozen=True)
class SemanticMessageReview:
    """专用消息语义等价 judge 的单次复判结果。"""

    constraint_id: str
    equivalent: bool
    confidence: float
    reason: str
    judge_status: str = "called"
    raw_payload: JsonObject = field(default_factory=dict)

    def accepted(self, confidence_threshold: float = SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD) -> bool:
        """判断该复判结果是否足以放行对应消息约束。"""
        return self.equivalent and self.confidence >= confidence_threshold

    def to_dict(self) -> JsonObject:
        """转换为 JSON 诊断对象。"""
        return {
            "constraint_id": self.constraint_id,
            "equivalent": self.equivalent,
            "confidence": self.confidence,
            "reason": self.reason,
            "judge_status": self.judge_status,
            "raw_payload": dict(self.raw_payload),
        }


def semantic_message_review_targets(
    milestone: Milestone,
    score: MilestoneScore,
    trajectory: Trajectory | None = None,
    boundary: Boundary | None = None,
) -> list[SemanticMessageReviewTarget]:
    """从 milestone 评分中提取需要专用语义复判的消息约束。

    入参：
        milestone: 当前候选 milestone。
        score: 原结构化评分结果。
        trajectory: 可选运行期轨迹，用于提供量词和指代消解上下文。
        boundary: 当前候选边界，用于截取复判前上下文。
    输出：
        需要进行 expected-vs-actual 消息等价判断的目标列表。
    """
    if milestone is None or score is None:
        raise ValueError("milestone 和 score 不能为空")
    score_by_id = {item.constraint_id: item for item in score.constraint_scores}
    supporting_context = _supporting_context(trajectory, boundary)
    targets: list[SemanticMessageReviewTarget] = []
    for constraint in milestone.constraints:
        constraint_score = score_by_id.get(constraint.constraint_id)
        if (
            not is_semantic_emit_message_constraint(constraint)
            or constraint_score is None
            or constraint_score.missing
            or constraint_score.score >= constraint.threshold
        ):
            continue
        target = _review_target(constraint, constraint_score, supporting_context)
        if target is not None:
            targets.append(target)
    return targets


def apply_semantic_message_reviews(
    milestone: Milestone,
    score: MilestoneScore,
    reviews: list[SemanticMessageReview],
    confidence_threshold: float = SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD,
) -> MilestoneScore:
    """把已接受的消息语义复判结果覆写回 MilestoneScore。

    入参：
        milestone: 当前候选 milestone。
        score: 原结构化评分结果。
        reviews: 专用消息语义 judge 的复判结果。
        confidence_threshold: 等价结论最低置信度。
    输出：
        若有消息约束被接受，返回重新聚合后的 MilestoneScore；否则返回原 score。
    """
    if milestone is None or score is None:
        raise ValueError("milestone 和 score 不能为空")
    accepted_reviews = {
        review.constraint_id: review
        for review in reviews
        if review is not None and review.accepted(confidence_threshold)
    }
    if not accepted_reviews:
        return score

    constraint_by_id = {constraint.constraint_id: constraint for constraint in milestone.constraints}
    updated_scores: list[ConstraintScore] = []
    for constraint_score in score.constraint_scores:
        constraint = constraint_by_id.get(constraint_score.constraint_id)
        review = accepted_reviews.get(constraint_score.constraint_id)
        if constraint is None or review is None:
            updated_scores.append(constraint_score)
            continue
        updated_scores.append(
            replace(
                constraint_score,
                score=clamp(max(float(constraint.threshold), float(constraint_score.score))),
                missing=False,
                evidence=[
                    *constraint_score.evidence,
                    (
                        "semantic message review accepted: "
                        f"constraint={constraint.constraint_id}, confidence={review.confidence:.3f}, "
                        f"reason={compact_text(review.reason or 'semantic equivalent', 180)}"
                    ),
                ],
            )
        )
    return _recompute_milestone_score(milestone, score, updated_scores)


def semantic_review_attempt_detail(
    targets: list[SemanticMessageReviewTarget],
    reviews: list[SemanticMessageReview],
    reviewed_score: MilestoneScore,
    confidence_threshold: float = SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD,
) -> JsonObject:
    """构造写入 match_attempts 的消息语义复判诊断。"""
    if targets is None or reviews is None or reviewed_score is None:
        raise ValueError("targets、reviews 和 reviewed_score 不能为空")
    accepted = [review for review in reviews if review.accepted(confidence_threshold)]
    rejected = [review for review in reviews if not review.accepted(confidence_threshold)]
    return {
        "status": "accepted" if len(accepted) == len(targets) and targets else "rejected",
        "review_type": "semantic_message_equivalence",
        "confidence_threshold": confidence_threshold,
        "target_constraint_ids": [target.constraint_id for target in targets],
        "targets": [target.to_dict() for target in targets],
        "reviews": [review.to_dict() for review in reviews],
        "accepted_constraint_ids": [review.constraint_id for review in accepted],
        "rejected_constraint_ids": [review.constraint_id for review in rejected],
        "reviewed_milestone_score": reviewed_score.score,
        "reviewed_milestone_status": reviewed_score.status.value,
        "reason": " | ".join(compact_text(review.reason, 180) for review in reviews if review.reason),
    }


def skipped_semantic_review_detail(status: str, targets: list[SemanticMessageReviewTarget]) -> JsonObject:
    """构造未执行专用语义复判时的诊断对象。"""
    if status is None or not str(status).strip():
        raise ValueError("status 不能为空")
    return {
        "status": str(status),
        "review_type": "semantic_message_equivalence",
        "target_constraint_ids": [target.constraint_id for target in targets],
        "targets": [target.to_dict() for target in targets],
    }


def is_semantic_emit_message_constraint(constraint: Constraint) -> bool:
    """判断约束是否声明为 emit_message + semantic_equivalent。"""
    if constraint is None or not isinstance(constraint.stage_goal_semantics, dict):
        return False
    semantics = constraint.stage_goal_semantics
    return (
        semantics.get("kind") == StageGoalSemanticKind.EMIT_MESSAGE.value
        and str(semantics.get("match_policy") or "semantic_equivalent") == "semantic_equivalent"
    )


def constraint_expected_excerpt(constraint: Constraint | None, limit: int = 420) -> str | None:
    """从约束中提取用于诊断和 prompt 的 expected 文本摘要。"""
    if constraint is None:
        return None
    content = _expected_content(constraint)
    if content:
        return compact_text(content, limit)
    safe_expected = json_safe(constraint.expected)
    return compact_text(safe_expected, limit) if safe_expected is not None else None


def constraint_actual_excerpt(
    constraint: Constraint | None,
    score: ConstraintScore | JsonObject | None,
    limit: int = 420,
) -> str | None:
    """从约束评分中提取用于诊断和 prompt 的 actual 文本摘要。"""
    if score is None:
        return None
    actual = score.actual if isinstance(score, ConstraintScore) else score.get("actual")
    semantics = constraint.stage_goal_semantics if constraint is not None else {}
    message = _matching_message(actual, semantics if isinstance(semantics, dict) else {})
    if message is not None:
        return compact_text(message[2], limit)
    return compact_text(json_safe(actual), limit) if actual is not None else None


def _review_target(
    constraint: Constraint,
    score: ConstraintScore,
    supporting_context: JsonObject,
) -> SemanticMessageReviewTarget | None:
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    expected_content = _expected_content(constraint)
    actual_message = _matching_message(score.actual, semantics)
    if not expected_content or actual_message is None:
        return None
    actual_sender, actual_recipient, actual_content = actual_message
    return SemanticMessageReviewTarget(
        constraint_id=constraint.constraint_id,
        expected_sender=str(semantics.get("sender") or "").strip(),
        expected_recipient=str(semantics.get("recipient") or "").strip(),
        actual_sender=actual_sender,
        actual_recipient=actual_recipient,
        expected_content=expected_content,
        actual_content=actual_content,
        base_score=clamp(float(score.score)),
        supporting_context=dict(supporting_context),
    )


def _supporting_context(trajectory: Trajectory | None, boundary: Boundary | None) -> JsonObject:
    if trajectory is None or boundary is None:
        return {}
    context: JsonObject = {
        "usage": (
            "Use this context only to resolve references, quantifiers, and scope in the expected/actual messages. "
            "Do not evaluate tool choice or whole-stage quality."
        )
    }
    steps = _recent_supporting_steps(trajectory, boundary)
    snapshots = _recent_state_summaries(trajectory, boundary)
    if steps:
        context["recent_steps"] = steps
    if snapshots:
        context["recent_state_summaries"] = snapshots
    return context if len(context) > 1 else {}


def _recent_supporting_steps(trajectory: Trajectory, boundary: Boundary) -> list[JsonObject]:
    steps = [step for step in trajectory.steps if step.index <= boundary.step_index]
    return [_step_summary(step) for step in steps[-SEMANTIC_MESSAGE_CONTEXT_STEP_LIMIT:]]


def _step_summary(step: TrajectoryStep) -> JsonObject:
    item: JsonObject = {
        "index": step.index,
        "actor": step.actor.value,
        "recipient": step.recipient.value if step.recipient is not None else None,
        "event_type": step.event_type.value,
    }
    if isinstance(step.content, str) and step.content.strip():
        item["content"] = compact_text(step.content, 700)
    if step.tool_call is not None:
        item["tool_call"] = {
            "name": step.tool_call.name,
            "arguments": json_safe(step.tool_call.arguments),
        }
    if step.tool_result is not None:
        result_summary: JsonObject = {
            "success": step.tool_result.success,
            "exception": step.tool_result.exception,
        }
        if step.tool_result.content is not None:
            result_summary["content"] = compact_text(json_safe(step.tool_result.content), 500)
        item["tool_result"] = result_summary
    return item


def _recent_state_summaries(trajectory: Trajectory, boundary: Boundary) -> list[JsonObject]:
    snapshots = [snapshot for snapshot in trajectory.snapshots if snapshot.after_step_index <= boundary.step_index]
    result: list[JsonObject] = []
    for snapshot in snapshots[-SEMANTIC_MESSAGE_CONTEXT_SNAPSHOT_LIMIT:]:
        namespaces: JsonObject = {}
        for namespace, rows in snapshot.namespaces.items():
            summary = _relationship_summary(rows)
            if summary:
                namespaces[str(namespace)] = summary
        if namespaces:
            result.append(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "after_step_index": snapshot.after_step_index,
                    "namespaces": namespaces,
                }
            )
    return result


def _relationship_summary(rows: JsonValue) -> JsonObject:
    if not isinstance(rows, list):
        return {}
    groups: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        relationship = row.get("relationship")
        if not isinstance(relationship, str) or not relationship.strip():
            continue
        label = _row_label(row)
        if label:
            groups.setdefault(relationship.strip(), []).append(label)
    if not groups:
        return {}
    return {
        "row_count": len(rows),
        "relationship_groups": {
            relationship: sorted(names)[:12]
            for relationship, names in sorted(groups.items())
        },
    }


def _row_label(row: JsonObject) -> str:
    for key in ("name", "person_id", "phone_number", "contact_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _expected_content(constraint: Constraint) -> str:
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    for value in (semantics.get("content"), constraint.expected):
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(constraint.expected, dict) and isinstance(constraint.expected.get("rows"), list):
        for row in constraint.expected["rows"]:
            content = row.get("content") if isinstance(row, dict) else None
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _matching_message(actual: JsonValue, semantics: JsonObject) -> tuple[str, str, str] | None:
    expected_sender = _actor(str(semantics.get("sender") or ""))
    expected_recipient = _actor(str(semantics.get("recipient") or ""))
    rows = actual.get("rows") if isinstance(actual, dict) and isinstance(actual.get("rows"), list) else actual
    values = rows if isinstance(rows, list) else [rows]
    for value in reversed(values):
        row = {"sender": "", "recipient": "", "content": value} if isinstance(value, str) else value
        if not isinstance(row, dict):
            continue
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        sender = str(row.get("sender") or "").strip()
        recipient = str(row.get("recipient") or "").strip()
        if _route_matches(sender, recipient, expected_sender, expected_recipient):
            return sender, recipient, content.strip()
    return None


def _route_matches(sender: str, recipient: str, expected_sender: str, expected_recipient: str) -> bool:
    actual_sender = _actor(sender)
    actual_recipient = _actor(recipient)
    return (
        (not expected_sender or not actual_sender or actual_sender == expected_sender)
        and (not expected_recipient or not actual_recipient or actual_recipient == expected_recipient)
    )


def _actor(value: str) -> str:
    normalized = str(value or "").strip().upper()
    return "ENVIRONMENT" if normalized == "EXECUTION_ENVIRONMENT" else normalized


def _recompute_milestone_score(
    milestone: Milestone,
    original: MilestoneScore,
    constraint_scores: list[ConstraintScore],
) -> MilestoneScore:
    score_by_id = {item.constraint_id: item for item in constraint_scores}
    toolsandbox_product = any(isinstance(constraint.metadata.get("toolsandbox"), dict) for constraint in milestone.constraints)
    score_value = 1.0 if toolsandbox_product else 0.0
    weight_sum = 0.0
    non_guardrail_count = 0
    hard_pass = True

    for constraint in milestone.constraints:
        result = score_by_id.get(constraint.constraint_id)
        constraint_score = clamp(float(result.score)) if result is not None else 0.0
        if toolsandbox_product:
            score_value *= constraint_score
            metadata = constraint.metadata.get("toolsandbox")
            if isinstance(metadata, dict) and bool(metadata.get("guardrail")):
                hard_pass = hard_pass and constraint_score > 0.0
                continue
            non_guardrail_count += 1
        else:
            weight = max(float(constraint.weight), 0.0)
            score_value += constraint_score * weight
            weight_sum += weight
        if constraint.hard and (result is None or result.missing or constraint_score < constraint.threshold):
            hard_pass = False

    if toolsandbox_product:
        score_value = score_value ** (1.0 / non_guardrail_count) if non_guardrail_count > 0 else score_value
    else:
        score_value = score_value / weight_sum if weight_sum > 0 else 0.0
    score_value = clamp(score_value) if hard_pass else 0.0
    threshold = milestone.pass_threshold if milestone.pass_threshold is not None else 0.8
    if not hard_pass:
        status = StageStatus.FAIL
    elif score_value >= threshold:
        status = StageStatus.PASS
    elif score_value >= 0.6:
        status = StageStatus.WARN
    else:
        status = StageStatus.FAIL
    return replace(
        original,
        score=score_value,
        status=status,
        evidence=[line for item in constraint_scores for line in item.evidence],
        missing_ratio=sum(1 for item in constraint_scores if item.missing) / len(constraint_scores),
        hard_constraints_all_pass=hard_pass,
        constraint_scores=constraint_scores,
    )
