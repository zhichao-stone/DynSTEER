from __future__ import annotations

import importlib.util
from functools import partial
from types import SimpleNamespace
from typing import Any

from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness
from dynsteer.adapter.toolsandbox.scorer import ToolSandboxConstraintScorer
from dynsteer.evaluate.score import ScoringContext
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


def fake_similarity(**kwargs: object) -> float:
    return 1.0


def fake_extractor(tool_trace: dict[str, object]) -> list[dict[str, object]]:
    return [{"latitude": 1.0, "longitude": 2.0}]


class FakeEvaluationModule:
    _default_dbs_column_similarities = {}

    @staticmethod
    def fake_similarity(
        snapshot: object,
        target_dataframe: object,
        column_similarities: dict[str, object],
        reference_snapshot: object | None = None,
        **kwargs: object,
    ) -> float:
        assert kwargs["fill_to"] == "tool_trace"
        assert callable(kwargs["extractor"])
        return 1.0


class FakeExtractorModule:
    @staticmethod
    def fake_extractor(tool_trace: dict[str, object]) -> list[dict[str, object]]:
        return [{"value": 1}]


class FakePyo3PanicException(BaseException):
    pass


FakePyo3PanicException.__module__ = "pyo3_runtime"


def test_toolsandbox_constraint_metadata_preserves_partial_kwargs() -> None:
    constraint = SimpleNamespace(
        database_namespace=SimpleNamespace(name="SANDBOX"),
        target_dataframe=[{"tool_trace": "{}"}],
        snapshot_constraint=partial(fake_similarity, fill_to="tool_trace", extractor=fake_extractor),
        column_similarity_measure={},
        reference_milestone_node_index=3,
    )

    data = ToolSandboxHarness()._constraint_from_snapshot_constraint("m4_c0", constraint)
    metadata = data["metadata"]["toolsandbox"]

    assert metadata["snapshot_constraint"] == "fake_similarity"
    assert metadata["snapshot_constraint_kwargs"] == {
        "fill_to": "tool_trace",
        "extractor": "fake_extractor",
    }
    assert metadata["reference_milestone_node_index"] == 3


def test_toolsandbox_scorer_restores_snapshot_constraint_kwargs() -> None:
    def module_loader(module_name: str) -> object:
        if module_name == "tool_sandbox.common.evaluation":
            return FakeEvaluationModule
        if module_name == "tool_sandbox.common.tool_trace_extractors":
            return FakeExtractorModule
        raise ModuleNotFoundError(module_name)

    scorer = ToolSandboxConstraintScorer(module_loader=module_loader)
    constraint = Constraint(
        constraint_id="m4_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"tool_trace": "{}"}], "columns": ["tool_trace"]},
        namespace="SANDBOX",
        hard=True,
        metadata={
            "toolsandbox": {
                "snapshot_constraint": "fake_similarity",
                "snapshot_constraint_kwargs": {
                    "fill_to": "tool_trace",
                    "extractor": "fake_extractor",
                },
            }
        },
    )

    result = scorer.score_constraint(
        constraint,
        source=[{"tool_trace": "{}"}],
        reference_source=None,
    )

    assert result.score == 1.0


def test_toolsandbox_scorer_scores_snapshot_similarity_with_exact_rows() -> None:
    def snapshot_similarity(
        snapshot: Any,
        target_dataframe: Any,
        column_similarities: dict[str, object],
        reference_snapshot: Any | None,
    ) -> float:
        return 1.0 if snapshot.to_dicts() == target_dataframe.to_dicts() else 0.0

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


def test_toolsandbox_scorer_converts_pyo3_panic_to_constraint_failure() -> None:
    class PanicEvaluationModule:
        _default_dbs_column_similarities = {}

        @staticmethod
        def removal_similarity(**kwargs: object) -> float:
            raise FakePyo3PanicException("not yet implemented")

    scorer = ToolSandboxConstraintScorer(module_loader=lambda _: PanicEvaluationModule)
    constraint = Constraint(
        constraint_id="m0-c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": []},
        namespace="CONTACT",
        hard=True,
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "CONTACT",
                "snapshot_constraint": "removal_similarity",
                "reference_milestone_node_index": None,
                "column_similarity_measure": {},
            }
        },
    )

    result = scorer.score_constraint(constraint, {"rows": []})

    assert result.score == 0.0
    assert result.missing is False
    assert any("评分失败" in item and "not yet implemented" in item for item in result.evidence)


def test_toolsandbox_scorer_restores_reminder_null_column_schema_before_native_similarity() -> None:
    def removal_similarity(
        snapshot: Any,
        target_dataframe: Any,
        column_similarities: dict[str, object],
        reference_snapshot: Any | None,
    ) -> float:
        assert reference_snapshot is not None
        reference_snapshot.drop("sandbox_message_index").fill_null(strategy="zero")
        snapshot.drop("sandbox_message_index").fill_null(strategy="zero")
        target_dataframe.fill_null(strategy="zero")
        return 1.0

    evaluation = SimpleNamespace(
        removal_similarity=removal_similarity,
        _default_dbs_column_similarities={},
    )
    scorer = _FakeToolSandboxScorer(evaluation)
    constraint = Constraint(
        constraint_id="m2-c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"reminder_id": "reminder-2"}]},
        namespace="REMINDER",
        hard=True,
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "REMINDER",
                "snapshot_constraint": "removal_similarity",
                "reference_milestone_node_index": 0,
                "column_similarity_measure": {},
            }
        },
    )
    reminder_rows = [
        {
            "sandbox_message_index": 17,
            "reminder_id": "reminder-2",
            "content": "Buy tickets",
            "creation_timestamp": 1782698492.0,
            "reminder_timestamp": 1782784832.0,
            "latitude": None,
            "longitude": None,
        }
    ]
    current_snapshot = StateSnapshot(
        snapshot_id="reminder:current",
        after_step_id="s17",
        after_step_index=17,
        namespaces={"REMINDER": reminder_rows},
    )
    reference_snapshot = StateSnapshot(
        snapshot_id="reminder:reference",
        after_step_id="s15",
        after_step_index=15,
        namespaces={"REMINDER": reminder_rows},
    )
    context = ScoringContext(matched_snapshots={"m0": reference_snapshot})

    result = scorer.score_constraint(constraint, current_snapshot, context=context)

    assert result.score == 1.0
