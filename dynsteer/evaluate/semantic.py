from __future__ import annotations
from dataclasses import dataclass, field, replace
import json
from dynsteer.model import Boundary, Constraint, ConstraintScore, JsonObject, JsonValue, Milestone, MilestoneScore, StageGoalSemanticKind, StageStatus, Trajectory
from dynsteer.utils import clamp, compact_text, json_safe


SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD = 0.7
SEMANTIC_MESSAGE_CONTEXT_STEP_LIMIT = 12
SEMANTIC_MESSAGE_CONTEXT_SNAPSHOT_LIMIT = 4
FOCUSED_STATE_ROW_LIMIT = 4
_STATE_EXCERPT_KINDS = {StageGoalSemanticKind.SET_STATE.value, StageGoalSemanticKind.PRESERVE_STATE.value}
_LOW_SIGNAL_MATCH_KEYS = {"conversation_active", "creation_timestamp", "is_self", "latitude", "longitude", "reminder_timestamp", "sandbox_message_index"}
_STATE_IDENTIFIER_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "CONTACT": (("person_id",), ("name",), ("phone_number",)),
    "REMINDER": (("reminder_id",),),
    "MESSAGING": (
        ("message_id",),
        ("sender_person_id", "recipient_person_id", "content"),
        ("sender_phone_number", "recipient_phone_number", "content"),
    ),
    "SETTING": (("device_id",),),
    "SANDBOX": (
        ("openai_tool_call_id",),
        ("sender", "recipient", "tool_trace"),
        ("sender", "recipient", "content"),
    ),
}
_STATE_DISPLAY_KEYS: dict[str, tuple[str, ...]] = {
    "CONTACT": ("person_id", "name", "phone_number", "relationship", "is_self"),
    "REMINDER": (
        "reminder_id",
        "content",
        "creation_timestamp",
        "reminder_timestamp",
        "latitude",
        "longitude",
    ),
    "MESSAGING": (
        "message_id",
        "sender_person_id",
        "sender_phone_number",
        "recipient_person_id",
        "recipient_phone_number",
        "content",
        "creation_timestamp",
    ),
    "SETTING": (
        "device_id",
        "cellular",
        "wifi",
        "location_service",
        "low_battery_mode",
        "latitude",
        "longitude",
    ),
    "SANDBOX": (
        "sandbox_message_index",
        "sender",
        "recipient",
        "content",
        "openai_function_name",
        "tool_trace",
        "tool_call_exception",
    ),
}

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

    def accepted(self, confidence_threshold: float=SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD) -> bool:
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

def semantic_message_review_targets(milestone: Milestone, score: MilestoneScore, trajectory: Trajectory | None=None, boundary: Boundary | None=None) -> list[SemanticMessageReviewTarget]:
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
    supporting_context: JsonObject = {}
    if trajectory is not None and boundary is not None:
        supporting_context = {"usage": "Use this context only to resolve references, quantifiers, and scope in the expected/actual messages. Do not evaluate tool choice or whole-stage quality."}
        steps = []
        for step in [item for item in trajectory.steps if item.index <= boundary.step_index][-SEMANTIC_MESSAGE_CONTEXT_STEP_LIMIT:]:
            item: JsonObject = {
                "index": step.index,
                "actor": step.actor.value,
                "recipient": step.recipient.value if step.recipient is not None else None,
                "event_type": step.event_type.value,
            }
            if isinstance(step.content, str) and step.content.strip():
                item["content"] = compact_text(step.content, 700)
            if step.tool_call is not None:
                item["tool_call"] = {"name": step.tool_call.name, "arguments": json_safe(step.tool_call.arguments)}
            if step.tool_result is not None:
                result_summary: JsonObject = {
                    "success": step.tool_result.success,
                    "exception": step.tool_result.exception,
                }
                if step.tool_result.content is not None:
                    result_summary["content"] = compact_text(json_safe(step.tool_result.content), 500)
                item["tool_result"] = result_summary
            steps.append(item)
        snapshots = []
        for snapshot in [item for item in trajectory.snapshots if item.after_step_index <= boundary.step_index][-SEMANTIC_MESSAGE_CONTEXT_SNAPSHOT_LIMIT:]:
            namespaces: JsonObject = {}
            for namespace, rows in snapshot.namespaces.items():
                if not isinstance(rows, list):
                    continue
                groups: dict[str, list[str]] = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    relationship = row.get("relationship")
                    if not isinstance(relationship, str) or not relationship.strip():
                        continue
                    for key in ("name", "person_id", "phone_number", "contact_id"):
                        value = row.get(key)
                        if isinstance(value, str) and value.strip():
                            label = value.strip()
                            break
                    else:
                        label = ""
                    if label:
                        groups.setdefault(relationship.strip(), []).append(label)
                if groups:
                    namespaces[str(namespace)] = {"row_count": len(rows), "relationship_groups": {relationship: sorted(names)[:12] for relationship, names in sorted(groups.items())}}
            if namespaces:
                snapshots.append({"after_step_index": snapshot.after_step_index, "namespaces": namespaces})
        if steps:
            supporting_context["recent_steps"] = steps
        if snapshots:
            supporting_context["recent_snapshots"] = snapshots
    targets: list[SemanticMessageReviewTarget] = []
    for constraint in milestone.constraints:
        constraint_score = score_by_id.get(constraint.constraint_id)
        if not is_semantic_emit_message_constraint(constraint) or constraint_score is None or constraint_score.missing or (constraint_score.score >= constraint.threshold):
            continue
        semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
        expected_content = _expected_content(constraint)
        actual_message = _matching_message(constraint_score.actual, semantics)
        if not expected_content or actual_message is None:
            continue
        actual_sender, actual_recipient, actual_content = actual_message
        targets.append(SemanticMessageReviewTarget(constraint_id=constraint.constraint_id, expected_sender=str(semantics.get("sender") or "").strip(), expected_recipient=str(semantics.get("recipient") or "").strip(), actual_sender=actual_sender, actual_recipient=actual_recipient, expected_content=expected_content, actual_content=actual_content, base_score=clamp(float(constraint_score.score)), supporting_context=dict(supporting_context)))
    return targets

def apply_semantic_message_reviews(milestone: Milestone, score: MilestoneScore, reviews: list[SemanticMessageReview], confidence_threshold: float=SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD) -> MilestoneScore:
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
    accepted_reviews = {review.constraint_id: review for review in reviews if review is not None and review.accepted(confidence_threshold)}
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
                score=clamp(
                    max(float(constraint.threshold), float(constraint_score.score))
                ),
                missing=False,
                evidence=[
                    *constraint_score.evidence,
                    (
                        "semantic message review accepted: "
                        f"constraint={constraint.constraint_id}, "
                        f"confidence={review.confidence:.3f}, "
                        f"reason={compact_text(review.reason or 'semantic equivalent', 180)}"
                    ),
                ],
            )
        )
    return _recompute_milestone_score(milestone, score, updated_scores)

def semantic_review_attempt_detail(targets: list[SemanticMessageReviewTarget], reviews: list[SemanticMessageReview], reviewed_score: MilestoneScore, confidence_threshold: float=SEMANTIC_MESSAGE_CONFIDENCE_THRESHOLD) -> JsonObject:
    """构造写入 match_attempts 的消息语义复判诊断。"""
    if targets is None or reviews is None or reviewed_score is None:
        raise ValueError("targets、reviews 和 reviewed_score 不能为空")
    accepted = [review for review in reviews if review.accepted(confidence_threshold)]
    rejected = [review for review in reviews if not review.accepted(confidence_threshold)]
    return {
        "status": "accepted"
        if len(accepted) == len(targets) and targets
        else "rejected",
        "review_type": "semantic_message_equivalence",
        "confidence_threshold": confidence_threshold,
        "target_constraint_ids": [target.constraint_id for target in targets],
        "targets": [target.to_dict() for target in targets],
        "reviews": [review.to_dict() for review in reviews],
        "accepted_constraint_ids": [review.constraint_id for review in accepted],
        "rejected_constraint_ids": [review.constraint_id for review in rejected],
        "reviewed_milestone_score": reviewed_score.score,
        "reviewed_milestone_status": reviewed_score.status.value,
        "reason": " | ".join(
            (compact_text(review.reason, 180) for review in reviews if review.reason)
        ),
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
    return semantics.get("kind") == StageGoalSemanticKind.EMIT_MESSAGE.value and str(semantics.get("match_policy") or "semantic_equivalent") == "semantic_equivalent"

def constraint_expected_excerpt(constraint: Constraint | None, limit: int=420) -> str | None:
    """从约束中提取用于诊断和 prompt 的 expected 文本摘要。"""
    if constraint is None:
        return None
    if _is_preserve_state_constraint(constraint):
        return compact_text(
            (
                "preserve_state("
                f"reference={preserve_state_reference_label(constraint)}, "
                f"namespace={_constraint_namespace(constraint) or 'state'}"
                ")"
            ),
            limit,
        )
    content = _expected_content(constraint)
    if content:
        return compact_text(content, limit)
    safe_expected = json_safe(constraint.expected)
    return compact_text(safe_expected, limit) if safe_expected is not None else None

def constraint_actual_excerpt(constraint: Constraint | None, score: ConstraintScore | JsonObject | None, limit: int=420) -> str | None:
    """从约束评分中提取用于诊断和 prompt 的 actual 文本摘要。"""
    if score is None:
        return None
    actual = score.actual if isinstance(score, ConstraintScore) else score.get("actual")
    semantics = constraint.stage_goal_semantics if constraint is not None else {}
    focused = _focused_state_excerpt(constraint, actual, limit)
    if focused is not None:
        return focused
    message = _matching_message(actual, semantics if isinstance(semantics, dict) else {})
    if message is not None:
        return compact_text(message[2], limit)
    return compact_text(json_safe(actual), limit) if actual is not None else None

def _focused_state_excerpt(constraint: Constraint | None, actual: JsonValue, limit: int) -> str | None:
    if constraint is None:
        return None
    semantics = (
        constraint.stage_goal_semantics
        if isinstance(constraint.stage_goal_semantics, dict)
        else {}
    )
    kind = str(semantics.get("kind") or "")
    target = str(getattr(constraint.target, "value", constraint.target))
    if kind not in _STATE_EXCERPT_KINDS and target != "state_snapshot":
        return None
    actual_rows = _state_rows_from_value(actual)
    if actual_rows is None:
        return None
    namespace = _constraint_namespace(constraint)
    if _is_preserve_state_constraint(constraint):
        _, selected_indices, _ = _focused_state_rows(namespace, [], actual_rows)
        relevant_rows = [_state_row_excerpt_payload(namespace, actual_rows[index]) for index in selected_indices]
        parts = [f"reference={preserve_state_reference_label(constraint)}", "target_source=reference_snapshot", f"actual_rows={len(actual_rows)}"]
        if relevant_rows:
            relevant_rows_json = json.dumps(
                relevant_rows,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            parts.append(f"relevant_actual_rows={relevant_rows_json}")
        omitted_count = max(len(actual_rows) - len(selected_indices), 0)
        if omitted_count > 0:
            parts.append(f"omitted_actual_rows={omitted_count}")
        return compact_text("; ".join(parts), limit)
    expected_rows = _state_rows_from_value(constraint.expected)
    if expected_rows is None:
        return None
    matched_count, selected_indices, expected_by_actual_index = _focused_state_rows(namespace, expected_rows, actual_rows)
    relevant_rows = [_state_row_excerpt_payload(namespace, actual_rows[index], expected_by_actual_index.get(index)) for index in selected_indices]
    parts = [f"actual_rows={len(actual_rows)}", f"expected_rows={len(expected_rows)}", f"matched_expected_rows={matched_count}/{len(expected_rows)}"]
    if relevant_rows:
        relevant_rows_json = json.dumps(
            relevant_rows,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        parts.append(f"relevant_actual_rows={relevant_rows_json}")
    omitted_count = max(len(actual_rows) - len(selected_indices), 0)
    if omitted_count > 0:
        parts.append(f"omitted_actual_rows={omitted_count}")
    return compact_text("; ".join(parts), limit)

def preserve_state_reference_label(constraint: Constraint) -> str:
    """返回 preserve_state 约束的参考状态标签。"""
    if constraint is None:
        raise ValueError("constraint 不能为空")
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    reference = semantics.get("reference")
    if isinstance(reference, dict):
        reference_type = str(reference.get("type") or "")
        value = reference.get("value")
        if reference_type == "milestone_index":
            return "initial_state" if value == -1 else f"milestone_index:{value}"
        if reference_type:
            return f"{reference_type}:{value}"
    metadata = constraint.metadata.get("toolsandbox")
    if isinstance(metadata, dict):
        reference_index = metadata.get("reference_milestone_node_index")
        if reference_index is not None:
            return "initial_state" if reference_index == -1 else f"milestone_index:{reference_index}"
    if constraint.reference_milestone_id is not None:
        return f"milestone_id:{constraint.reference_milestone_id}"
    return "runtime_reference"

def _is_preserve_state_constraint(constraint: Constraint) -> bool:
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    return semantics.get("kind") == StageGoalSemanticKind.PRESERVE_STATE.value

def _constraint_namespace(constraint: Constraint) -> str:
    if constraint.namespace:
        return str(constraint.namespace).strip().upper()
    semantics = constraint.stage_goal_semantics if isinstance(constraint.stage_goal_semantics, dict) else {}
    namespace = semantics.get("namespace")
    if isinstance(namespace, str) and namespace.strip():
        return namespace.strip().upper()
    metadata = constraint.metadata.get("toolsandbox")
    if isinstance(metadata, dict):
        namespace = metadata.get("database_namespace")
        if isinstance(namespace, str) and namespace.strip():
            return namespace.strip().upper()
    return ""

def _state_rows_from_value(value: JsonValue) -> list[JsonObject] | None:
    if isinstance(value, dict):
        rows = value.get("rows")
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
        return [dict(value)] if any((key not in {"columns", "metadata", "namespace", "row_count", "rows"} and _has_value(item) for key, item in value.items())) else None
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    return None

def _focused_state_rows(namespace: str, expected_rows: list[JsonObject], actual_rows: list[JsonObject]) -> tuple[int, list[int], dict[int, JsonObject]]:
    used_actual_indices: set[int] = set()
    selected_indices: list[int] = []
    expected_by_actual_index: dict[int, JsonObject] = {}
    matched_count = 0
    for expected_row in expected_rows:
        identifier_keys = _identifier_keys(namespace, expected_row)
        if not identifier_keys:
            continue
        candidates: list[tuple[int, int, int]] = []
        for index, actual_row in enumerate(actual_rows):
            if index in used_actual_indices:
                continue
            if all((_values_equal(expected_row.get(key), actual_row.get(key)) for key in identifier_keys)):
                shared_count = _shared_value_count(namespace, expected_row, actual_row)
                candidates.append((shared_count, -index, index))
        if not candidates:
            continue
        actual_index = max(candidates)[2]
        used_actual_indices.add(actual_index)
        expected_by_actual_index[actual_index] = expected_row
        matched_count += 1
        if actual_index not in selected_indices and len(selected_indices) < FOCUSED_STATE_ROW_LIMIT:
            selected_indices.append(actual_index)
    if len(selected_indices) >= FOCUSED_STATE_ROW_LIMIT or not actual_rows:
        return (matched_count, selected_indices, expected_by_actual_index)
    if not expected_rows:
        selected_indices.extend((index for index in range(len(actual_rows)) if index not in selected_indices))
        return (matched_count, selected_indices[:FOCUSED_STATE_ROW_LIMIT], expected_by_actual_index)
    candidates: list[tuple[int, int, int]] = []
    for index, actual_row in enumerate(actual_rows):
        if index in selected_indices:
            continue
        score = max((_shared_value_count(namespace, expected_row, actual_row) for expected_row in expected_rows))
        candidates.append((score, -index, index))
    positive_candidate_found = any((score > 0 for score, _, _ in candidates))
    for score, _, index in sorted(candidates, reverse=True):
        if positive_candidate_found and score <= 0:
            continue
        if not positive_candidate_found and len(selected_indices) >= min(2, len(actual_rows)):
            break
        selected_indices.append(index)
        if len(selected_indices) >= FOCUSED_STATE_ROW_LIMIT:
            break
    return (matched_count, selected_indices, expected_by_actual_index)

def _identifier_keys(namespace: str, expected_row: JsonObject) -> tuple[str, ...]:
    for keys in _STATE_IDENTIFIER_KEYS.get(namespace, ()):
        if all((_has_value(expected_row.get(key)) for key in keys)):
            return keys
    meaningful_keys = [key for key, value in expected_row.items() if _has_value(value) and (not _is_low_signal_match_key(namespace, key))]
    return tuple(meaningful_keys)

def _shared_value_count(namespace: str, expected_row: JsonObject, actual_row: JsonObject) -> int:
    count = 0
    for key, expected_value in expected_row.items():
        if _is_low_signal_match_key(namespace, key) or not _has_value(expected_value):
            continue
        if _values_equal(expected_value, actual_row.get(key)):
            count += 1
    return count

def _state_row_excerpt_payload(namespace: str, actual_row: JsonObject, expected_row: JsonObject | None=None) -> JsonObject:
    display_keys = _state_display_keys(namespace, actual_row, expected_row)
    payload: JsonObject = {key: _compact_field_value(actual_row.get(key)) for key in display_keys if key in actual_row}
    if expected_row is None:
        return payload
    mismatched_fields: JsonObject = {}
    for key, expected_value in expected_row.items():
        if _is_low_signal_match_key(namespace, key) or not _has_value(expected_value):
            continue
        actual_value = actual_row.get(key)
        if not _values_equal(expected_value, actual_value):
            mismatched_fields[key] = {
                "expected": _compact_field_value(expected_value),
                "actual": _compact_field_value(actual_value),
            }
    if mismatched_fields:
        payload["mismatched_fields"] = mismatched_fields
    return payload

def _state_display_keys(namespace: str, actual_row: JsonObject, expected_row: JsonObject | None=None) -> list[str]:
    keys: list[str] = []
    for key in _STATE_DISPLAY_KEYS.get(namespace, ()):
        if key not in keys:
            keys.append(key)
    if expected_row is not None:
        for key in expected_row:
            if key not in keys:
                keys.append(key)
    for key in actual_row:
        if key not in keys and (not _is_low_signal_match_key(namespace, key)):
            keys.append(key)
    return keys[:8]

def _is_low_signal_match_key(namespace: str, key: str) -> bool:
    if namespace == "SETTING" and key in {"cellular", "wifi", "location_service", "low_battery_mode"}:
        return False
    return key in _LOW_SIGNAL_MATCH_KEYS

def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return bool(value)
    return True

def _values_equal(expected: object, actual: object) -> bool:
    if isinstance(expected, str) or isinstance(actual, str):
        return str(expected or "").strip() == str(actual or "").strip()
    return json_safe(expected) == json_safe(actual)

def _compact_field_value(value: object) -> JsonValue:
    safe_value = json_safe(value)
    if isinstance(safe_value, str):
        return compact_text(safe_value, 140)
    if isinstance(safe_value, list | dict):
        return compact_text(safe_value, 180)
    return safe_value

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
    expected_sender = str(semantics.get("sender") or "").strip().upper()
    if expected_sender == "EXECUTION_ENVIRONMENT":
        expected_sender = "ENVIRONMENT"
    expected_recipient = str(semantics.get("recipient") or "").strip().upper()
    if expected_recipient == "EXECUTION_ENVIRONMENT":
        expected_recipient = "ENVIRONMENT"
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
        actual_sender = sender.upper()
        if actual_sender == "EXECUTION_ENVIRONMENT":
            actual_sender = "ENVIRONMENT"
        actual_recipient = recipient.upper()
        if actual_recipient == "EXECUTION_ENVIRONMENT":
            actual_recipient = "ENVIRONMENT"
        if (not expected_sender or not actual_sender or actual_sender == expected_sender) and (not expected_recipient or not actual_recipient or actual_recipient == expected_recipient):
            return (sender, recipient, content.strip())
    return None

def _recompute_milestone_score(milestone: Milestone, original: MilestoneScore, constraint_scores: list[ConstraintScore]) -> MilestoneScore:
    score_by_id = {item.constraint_id: item for item in constraint_scores}
    toolsandbox_product = any((isinstance(constraint.metadata.get("toolsandbox"), dict) for constraint in milestone.constraints))
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
    return replace(original, score=score_value, status=status, evidence=[line for item in constraint_scores for line in item.evidence], missing_ratio=sum((1 for item in constraint_scores if item.missing)) / len(constraint_scores), hard_constraints_all_pass=hard_pass, constraint_scores=constraint_scores)
