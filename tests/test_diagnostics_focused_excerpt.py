from dynsteer.evaluate.diagnostics import _constraint_failure_detail, _constraint_failure_line
from dynsteer.model import Constraint, ConstraintTarget, Operator


def _contact_constraint(expected_rows: list[dict[str, object]], constraint_id: str) -> Constraint:
    return Constraint(
        constraint_id=constraint_id,
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": expected_rows, "columns": list(expected_rows[0].keys()) if expected_rows else []},
        namespace="CONTACT",
        threshold=1.0,
        hard=True,
        stage_goal_semantics={"kind": "set_state", "namespace": "CONTACT", "expected": expected_rows},
        metadata={"toolsandbox": {"database_namespace": "CONTACT"}},
    )


def test_constraint_failure_line_prefers_extra_state_drift_over_target_absence() -> None:
    expected_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293847563",
            "relationship": "enemy",
            "is_self": False,
        }
    ]
    actual_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293847563",
            "relationship": "enemy",
            "is_self": False,
        },
        {
            "person_id": "3815cac9-3cab-5d75-89e2-fe12fe19779e",
            "name": "Tomas Haake",
            "phone_number": "+11233344455",
            "relationship": "self",
            "is_self": True,
        },
        {
            "person_id": "f3e3e3e3-0000-0000-0000-000000000000",
            "name": "Bart",
            "phone_number": "+10293847563",
            "relationship": "friend",
            "is_self": False,
        },
    ]
    detail = _constraint_failure_detail(
        _contact_constraint(expected_rows, "m3_c0"),
        {
            "constraint_id": "m3_c0",
            "score": 0.0,
            "missing": False,
            "evidence": ["ToolSandbox custom constraint m3_c0 score 0.000 (update_similarity)"],
            "actual": actual_rows,
        },
    )
    line = _constraint_failure_line(detail)

    assert "matched_expected_rows=1/1" in str(detail["actual_excerpt"])
    assert "target-related rows already appeared" in line
    assert "reference drift" in line
    assert "target row absent" not in line
