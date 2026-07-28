from dynsteer.evaluate.diagnostics import _constraint_failure_detail
from dynsteer.evaluate.semantic import constraint_actual_excerpt, constraint_expected_excerpt
from dynsteer.model import (
    Constraint,
    ConstraintScore,
    ConstraintTarget,
    Operator,
    StageGoalSemanticKind,
)


def test_preserve_state_expected_excerpt_uses_reference_label() -> None:
    """preserve_state expected 摘要展示参考态，不展示空 rows 目标。"""
    constraint = _preserve_constraint()

    excerpt = constraint_expected_excerpt(constraint)

    assert excerpt is not None
    assert "preserve_state" in excerpt
    assert "reference=initial_state" in excerpt
    assert '"rows"' not in excerpt


def test_preserve_state_actual_excerpt_uses_reference_prefix() -> None:
    """preserve_state actual 摘要不输出 expected_rows=0。"""
    constraint = _preserve_constraint()
    score = ConstraintScore(
        constraint_id="m0_c1",
        score=0.0,
        actual={"rows": [{"reminder_id": "r1", "content": "old"}]},
    )

    excerpt = constraint_actual_excerpt(constraint, score)

    assert excerpt is not None
    assert "reference=initial_state" in excerpt
    assert "target_source=reference_snapshot" in excerpt
    assert "actual_rows=1" in excerpt
    assert "expected_rows=0" not in excerpt


def test_preserve_state_failure_detail_marks_empty_expected_as_placeholder() -> None:
    """失败诊断把空 expected 标成序列化占位。"""
    constraint = _preserve_constraint()

    detail = _constraint_failure_detail(
        constraint,
        {
            "constraint_id": "m0_c1",
            "score": 0.0,
            "missing": False,
            "evidence": ["ToolSandbox reference snapshot 诊断: target_source=reference_snapshot"],
            "actual": {"rows": [{"reminder_id": "r1", "content": "old"}]},
        },
    )

    assert "serialized placeholder" in str(detail["line"])
    assert detail["expected_excerpt"] == "preserve_state(reference=initial_state, namespace=REMINDER)"


def _preserve_constraint() -> Constraint:
    return Constraint(
        constraint_id="m0_c1",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [], "columns": []},
        namespace="REMINDER",
        stage_goal_semantics={
            "kind": StageGoalSemanticKind.PRESERVE_STATE.value,
            "namespace": "REMINDER",
            "reference": {"type": "milestone_index", "value": -1},
        },
        metadata={
            "toolsandbox": {
                "database_namespace": "REMINDER",
                "snapshot_constraint": "guardrail_similarity",
                "reference_milestone_node_index": -1,
            }
        },
    )
