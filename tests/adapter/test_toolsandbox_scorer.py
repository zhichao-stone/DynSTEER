from __future__ import annotations

import importlib.util
from functools import partial
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness
from dynsteer.adapter.toolsandbox.scorer import ToolSandboxConstraintScorer
from dynsteer.evaluate.score import ScoringContext
from dynsteer.model import Boundary, Constraint, ConstraintTarget, Milestone, Operator, StateSnapshot, StageStatus, Trajectory


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


def fake_column_similarity(value: object, other: object, atol_dict: dict[str, int] | None = None) -> float:
    return 1.0


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


def test_toolsandbox_constraint_metadata_preserves_partial_column_similarity() -> None:
    constraint = SimpleNamespace(
        database_namespace=SimpleNamespace(name="SANDBOX"),
        target_dataframe=[{"tool_trace": "{}"}],
        snapshot_constraint=fake_similarity,
        column_similarity_measure={
            "tool_trace": partial(fake_column_similarity, atol_dict={"timestamp_0": 1})
        },
        reference_milestone_node_index=None,
    )

    data = ToolSandboxHarness()._constraint_from_snapshot_constraint("m2_c0", constraint)
    metadata = data["metadata"]["toolsandbox"]

    assert metadata["column_similarity_measure"]["tool_trace"] == {
        "callable": "fake_column_similarity",
        "partial_keywords": {"atol_dict": {"timestamp_0": 1}},
    }


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


def test_toolsandbox_scorer_restores_partial_column_similarity() -> None:
    class PartialEvaluationModule:
        _default_dbs_column_similarities = {}

        @staticmethod
        def column_tool_trace_exact_match_similarity(
            value: object,
            other: object,
            atol_dict: dict[str, int] | None = None,
        ) -> float:
            return 1.0

        @staticmethod
        def snapshot_similarity(
            snapshot: object,
            target_dataframe: object,
            column_similarities: dict[str, object],
            reference_snapshot: object | None = None,
        ) -> float:
            measure = column_similarities["tool_trace"]
            assert isinstance(measure, partial)
            assert measure.func is PartialEvaluationModule.column_tool_trace_exact_match_similarity
            assert measure.keywords == {"atol_dict": {"timestamp_0": 1}}
            return 1.0

    scorer = ToolSandboxConstraintScorer(module_loader=lambda _: PartialEvaluationModule)
    constraint = Constraint(
        constraint_id="m2_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"tool_trace": ['{"tool_name": "timestamp_diff"}']}], "columns": ["tool_trace"]},
        namespace="SANDBOX",
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "SANDBOX",
                "snapshot_constraint": "snapshot_similarity",
                "column_similarity_measure": {
                    "tool_trace": {
                        "callable": "column_tool_trace_exact_match_similarity",
                        "partial_keywords": {"atol_dict": {"timestamp_0": 1}},
                    }
                },
            }
        },
    )

    result = scorer.score_constraint(
        constraint,
        StateSnapshot(
            snapshot_id="sandbox:1",
            after_step_id="s1",
            after_step_index=1,
            namespaces={"SANDBOX": [{"tool_trace": ['{"tool_name": "timestamp_diff"}']}]},
        ),
    )

    assert result.score == 1.0


def test_toolsandbox_scorer_uses_native_milestone_aggregation_for_soft_constraints() -> None:
    class NativeAggregationEvaluationModule:
        _default_dbs_column_similarities = {}

        @staticmethod
        def soft_similarity(**kwargs: object) -> float:
            return 0.899

        @staticmethod
        def guardrail_similarity(**kwargs: object) -> float:
            return 1.0

    scorer = ToolSandboxConstraintScorer(module_loader=lambda _: NativeAggregationEvaluationModule)
    milestone = Milestone(
        milestone_id="m1",
        name="soft",
        description="soft ToolSandbox milestone",
        pass_threshold=0.8,
        constraints=[
            Constraint(
                constraint_id="m1_c0",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                expected={"rows": [{"value": "ok"}]},
                namespace="SETTING",
                threshold=1.0,
                hard=True,
                evaluator_hint="toolsandbox",
                metadata={
                    "toolsandbox": {
                        "database_namespace": "SETTING",
                        "snapshot_constraint": "soft_similarity",
                        "guardrail": False,
                    }
                },
            ),
            Constraint(
                constraint_id="m1_c1",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                expected={"rows": [{"value": "ok"}]},
                namespace="SETTING",
                threshold=1.0,
                hard=True,
                evaluator_hint="toolsandbox",
                metadata={
                    "toolsandbox": {
                        "database_namespace": "SETTING",
                        "snapshot_constraint": "guardrail_similarity",
                        "guardrail": True,
                    }
                },
            ),
        ],
    )
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[],
        snapshots=[
            StateSnapshot(
                snapshot_id="setting:1",
                after_step_id="s1",
                after_step_index=1,
                namespaces={"SETTING": [{"value": "ok"}]},
            )
        ],
    )

    result = scorer.score_milestone(
        milestone,
        Boundary("b0", 1, "setting:1", "state_update"),
        trajectory,
        trajectory.snapshots,
    )

    assert result.status == StageStatus.PASS
    assert result.score == pytest.approx(0.899)


def test_toolsandbox_scorer_keeps_guardrail_zero_as_hard_failure() -> None:
    class NativeAggregationEvaluationModule:
        _default_dbs_column_similarities = {}

        @staticmethod
        def soft_similarity(**kwargs: object) -> float:
            return 1.0

        @staticmethod
        def guardrail_similarity(**kwargs: object) -> float:
            return 0.0

    scorer = ToolSandboxConstraintScorer(module_loader=lambda _: NativeAggregationEvaluationModule)
    milestone = Milestone(
        milestone_id="m1",
        name="guardrail",
        description="guardrail ToolSandbox milestone",
        constraints=[
            Constraint(
                constraint_id="m1_c0",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                expected={"rows": [{"value": "ok"}]},
                namespace="SETTING",
                evaluator_hint="toolsandbox",
                metadata={
                    "toolsandbox": {
                        "database_namespace": "SETTING",
                        "snapshot_constraint": "soft_similarity",
                        "guardrail": False,
                    }
                },
            ),
            Constraint(
                constraint_id="m1_c1",
                target=ConstraintTarget.STATE_SNAPSHOT,
                selector="$",
                operator=Operator.CUSTOM,
                expected={"rows": [{"value": "ok"}]},
                namespace="SETTING",
                evaluator_hint="toolsandbox",
                metadata={
                    "toolsandbox": {
                        "database_namespace": "SETTING",
                        "snapshot_constraint": "guardrail_similarity",
                        "guardrail": True,
                    }
                },
            ),
        ],
    )
    trajectory = Trajectory(
        run_id="run",
        task_id="task",
        steps=[],
        snapshots=[
            StateSnapshot(
                snapshot_id="setting:1",
                after_step_id="s1",
                after_step_index=1,
                namespaces={"SETTING": [{"value": "ok"}]},
            )
        ],
    )

    result = scorer.score_milestone(
        milestone,
        Boundary("b0", 1, "setting:1", "state_update"),
        trajectory,
        trajectory.snapshots,
    )

    assert result.status == StageStatus.FAIL
    assert result.score == 0.0


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


def test_toolsandbox_scorer_restores_sandbox_schema_before_native_similarity() -> None:
    sandbox_schema = {
        "sender": pl.Enum(["USER", "AGENT", "EXECUTION_ENVIRONMENT"]),
        "recipient": pl.Enum(["USER", "AGENT", "EXECUTION_ENVIRONMENT"]),
        "visible_to": pl.List(pl.Enum(["USER", "AGENT", "EXECUTION_ENVIRONMENT"])),
        "tool_trace": pl.List(pl.String),
    }

    class FakeExecutionContextModule:
        class ExecutionContext:
            dbs_schemas = {"SANDBOX": sandbox_schema}

    def module_loader(module_name: str) -> object:
        if module_name == "tool_sandbox.common.execution_context":
            return FakeExecutionContextModule
        raise ModuleNotFoundError(module_name)

    scorer = ToolSandboxConstraintScorer(module_loader=module_loader)

    dataframe = scorer._rows_to_dataframe(
        {
            "rows": [
                {
                    "sender": "AGENT",
                    "recipient": "EXECUTION_ENVIRONMENT",
                    "visible_to": ["AGENT", "EXECUTION_ENVIRONMENT"],
                    "tool_trace": ['{"tool_name": "search_lat_lon", "arguments": {"query": "city"}}'],
                }
            ]
        },
        namespace="SANDBOX",
    )

    assert dataframe.schema["sender"] == sandbox_schema["sender"]
    assert dataframe.schema["recipient"] == sandbox_schema["recipient"]
    assert dataframe.schema["visible_to"] == sandbox_schema["visible_to"]
    assert dataframe.schema["tool_trace"] == sandbox_schema["tool_trace"]


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


def test_toolsandbox_scorer_uses_initial_reference_snapshot() -> None:
    def snapshot_similarity(
        snapshot: Any,
        target_dataframe: Any,
        column_similarities: dict[str, object],
        reference_snapshot: Any | None,
    ) -> float:
        assert reference_snapshot is not None
        assert reference_snapshot.to_dicts() == [{"device_id": "phone", "cellular": True}]
        return 1.0

    evaluation = SimpleNamespace(
        snapshot_similarity=snapshot_similarity,
        _default_dbs_column_similarities={},
    )
    scorer = _FakeToolSandboxScorer(evaluation)
    constraint = Constraint(
        constraint_id="m1_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"device_id": "phone", "cellular": False}]},
        namespace="SETTING",
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "SETTING",
                "snapshot_constraint": "snapshot_similarity",
                "reference_milestone_node_index": -1,
                "column_similarity_measure": {},
            }
        },
    )
    current_snapshot = StateSnapshot(
        snapshot_id="setting:current",
        after_step_id="s1",
        after_step_index=1,
        namespaces={"SETTING": [{"device_id": "phone", "cellular": False}]},
    )
    initial_snapshot = StateSnapshot(
        snapshot_id="initial",
        after_step_id="initial",
        after_step_index=0,
        namespaces={"SETTING": [{"device_id": "phone", "cellular": True}]},
    )

    result = scorer.score_constraint(
        constraint,
        current_snapshot,
        context=ScoringContext(matched_snapshots={"initial": initial_snapshot}),
    )

    assert result.score == 1.0


def test_toolsandbox_scorer_reports_missing_reference_snapshot() -> None:
    evaluation = SimpleNamespace(
        snapshot_similarity=lambda **kwargs: 1.0,
        _default_dbs_column_similarities={},
    )
    scorer = _FakeToolSandboxScorer(evaluation)
    constraint = Constraint(
        constraint_id="m2_c0",
        target=ConstraintTarget.STATE_SNAPSHOT,
        selector="$",
        operator=Operator.CUSTOM,
        expected={"rows": [{"device_id": "phone", "cellular": False}]},
        namespace="SETTING",
        evaluator_hint="toolsandbox",
        metadata={
            "toolsandbox": {
                "database_namespace": "SETTING",
                "snapshot_constraint": "snapshot_similarity",
                "reference_milestone_node_index": 0,
                "column_similarity_measure": {},
            }
        },
    )

    result = scorer.score_constraint(
        constraint,
        StateSnapshot(
            snapshot_id="setting:current",
            after_step_id="s1",
            after_step_index=1,
            namespaces={"SETTING": [{"device_id": "phone", "cellular": False}]},
        ),
        context=ScoringContext(matched_snapshots={}),
    )

    assert result.score == 0.0
    assert any("ToolSandbox reference snapshot 缺失" in item and "m2_c0" in item for item in result.evidence)


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
