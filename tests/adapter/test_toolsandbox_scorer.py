from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from typing import Any

from dynsteer.adapter.toolsandbox.scorer import ToolSandboxConstraintScorer
from dynsteer.model import Constraint, ConstraintTarget, Operator, StateSnapshot


class _FakeDataFrame:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows


class _FakePolars:
    def DataFrame(self, rows: list[object]) -> _FakeDataFrame:
        return _FakeDataFrame(rows)


class _FakeToolSandboxScorer(ToolSandboxConstraintScorer):
    def __init__(self, evaluation: object) -> None:
        super().__init__(module_loader=lambda _: evaluation)

    def _load_polars(self) -> Any:
        return _FakePolars()


def test_toolsandbox_scorer_scores_snapshot_similarity_with_exact_rows() -> None:
    def snapshot_similarity(
        snapshot: _FakeDataFrame,
        target_dataframe: _FakeDataFrame,
        column_similarities: dict[str, object],
        reference_snapshot: _FakeDataFrame | None,
    ) -> float:
        return 1.0 if snapshot.rows == target_dataframe.rows else 0.0

    evaluation = SimpleNamespace(
        snapshot_similarity=snapshot_similarity,
        _default_dbs_column_similarities={},
    )
    scorer = _FakeToolSandboxScorer(evaluation)
    constraint = Constraint(
        constraint_id="m0-c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        namespace="CONTACT",
        selector="$",
        operator=Operator.CUSTOM,
        expected={
            "rows": [
                {
                    "sandbox_message_index": 3,
                    "person_id": "p1",
                    "name": "Alice",
                    "phone_number": "123",
                    "relationship": "friend",
                    "is_self": False,
                }
            ],
            "columns": ["sandbox_message_index", "person_id", "name", "phone_number", "relationship", "is_self"],
        },
        hard=True,
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "CONTACT",
                "snapshot_constraint": "snapshot_similarity",
                "reference_milestone_node_index": None,
                "column_similarity_measure": {},
                "guardrail": False,
            }
        },
    )
    snapshot = StateSnapshot(
        snapshot_id="contact:3",
        after_step_id="s0",
        after_step_index=0,
        namespaces={
            "CONTACT": [
                {
                    "sandbox_message_index": 3,
                    "person_id": "p1",
                    "name": "Alice",
                    "phone_number": "123",
                    "relationship": "friend",
                    "is_self": False,
                }
            ]
        },
    )

    result = scorer.score_constraint(constraint, snapshot)

    assert result.score == 1.0
    assert result.missing is False
    assert any("ToolSandbox" in item for item in result.evidence)


def test_toolsandbox_scorer_falls_back_to_explicit_unsupported_for_unknown_metadata() -> None:
    scorer = _FakeToolSandboxScorer(SimpleNamespace(_default_dbs_column_similarities={}))
    constraint = Constraint(
        constraint_id="m0-c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": []},
        evaluator_hint="toolsandbox",
        metadata={"toolsandbox": {"snapshot_constraint": "unknown_similarity"}},
    )

    result = scorer.score_constraint(constraint, {"rows": []})

    assert result.score == 0.0
    assert any("unknown_similarity" in item for item in result.evidence)


def test_toolsandbox_scorer_reports_missing_dependency_when_unavailable() -> None:
    if importlib.util.find_spec("tool_sandbox") is not None:
        return
    scorer = ToolSandboxConstraintScorer()
    constraint = Constraint(
        constraint_id="m0-c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": []},
        evaluator_hint="toolsandbox",
        metadata={"toolsandbox": {"snapshot_constraint": "snapshot_similarity"}},
    )

    result = scorer.score_constraint(constraint, {"rows": []})

    assert result.score == 0.0
    assert any("tool_sandbox" in item for item in result.evidence)
