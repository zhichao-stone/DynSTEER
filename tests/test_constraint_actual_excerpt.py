from dynsteer.evaluate.semantic import constraint_actual_excerpt
from dynsteer.model import Constraint, ConstraintScore, ConstraintTarget, Operator


def _contact_constraint(expected_rows: list[dict[str, object]], constraint_id: str = "m1_c0") -> Constraint:
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


def test_constraint_actual_excerpt_prioritizes_target_rows_at_the_end() -> None:
    expected_rows = [
        {
            "person_id": "9e137f06-916a-5310-8174-cf0b7e9f7054",
            "name": "Fredrik Thordendal",
            "phone_number": "+12453344098",
            "relationship": "enemy",
            "is_self": False,
        },
        {
            "person_id": "a22e1984-6c6c-530c-8831-c3ea3b5138e7",
            "name": "John Petrucci",
            "phone_number": "+1234560987",
            "relationship": "enemy",
            "is_self": False,
        },
    ]
    actual_rows = [
        {
            "person_id": "3815cac9-3cab-5d75-89e2-fe12fe19779e",
            "name": "Tomas Haake",
            "phone_number": "+11233344455",
            "relationship": "self",
            "is_self": True,
        },
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10000000000",
            "relationship": "boss",
            "is_self": False,
        },
        *expected_rows,
    ]
    excerpt = constraint_actual_excerpt(
        _contact_constraint(expected_rows),
        ConstraintScore(constraint_id="m1_c0", score=1.0, missing=False, actual=actual_rows),
    )

    assert excerpt is not None
    assert "actual_rows=4" in excerpt
    assert "expected_rows=2" in excerpt
    assert "matched_expected_rows=2/2" in excerpt
    assert "Fredrik Thordendal" in excerpt
    assert "John Petrucci" in excerpt
    assert "Tomas Haake" not in excerpt
    assert "omitted_actual_rows=2" in excerpt


def test_constraint_actual_excerpt_shows_field_differences_for_a_matched_row() -> None:
    expected_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293846310",
            "relationship": "enemy",
            "is_self": False,
        }
    ]
    actual_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "name": "Homer S",
            "phone_number": "+10293846310",
            "relationship": "boss",
            "is_self": False,
        },
        {
            "person_id": "3815cac9-3cab-5d75-89e2-fe12fe19779e",
            "name": "Tomas Haake",
            "phone_number": "+11233344455",
            "relationship": "self",
            "is_self": True,
        },
    ]
    excerpt = constraint_actual_excerpt(
        _contact_constraint(expected_rows, "m3_c0"),
        ConstraintScore(constraint_id="m3_c0", score=0.0, missing=False, actual=actual_rows),
    )

    assert excerpt is not None
    assert "matched_expected_rows=1/1" in excerpt
    assert "mismatched_fields" in excerpt
    assert "relationship" in excerpt
    assert "enemy" in excerpt
    assert "boss" in excerpt


def test_constraint_actual_excerpt_falls_back_to_closest_rows_when_no_identifier_matches() -> None:
    expected_rows = [
        {
            "person_id": "e3570ab6-0819-5032-be1e-2b366390c8ef",
            "phone_number": "+10293847563",
        }
    ]
    actual_rows = [
        {
            "person_id": "3815cac9-3cab-5d75-89e2-fe12fe19779e",
            "name": "Tomas Haake",
            "phone_number": "+10293847563",
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
        {
            "person_id": "9e137f06-916a-5310-8174-cf0b7e9f7054",
            "name": "Fredrik Thordendal",
            "phone_number": "+12453344098",
            "relationship": "friend",
            "is_self": False,
        },
    ]
    excerpt = constraint_actual_excerpt(
        _contact_constraint(expected_rows, "m3_c1"),
        ConstraintScore(constraint_id="m3_c1", score=0.0, missing=False, actual=actual_rows),
    )

    assert excerpt is not None
    assert "matched_expected_rows=0/1" in excerpt
    assert "Tomas Haake" in excerpt
    assert "Bart" in excerpt
    assert "Fredrik Thordendal" not in excerpt
    assert "omitted_actual_rows=1" in excerpt
