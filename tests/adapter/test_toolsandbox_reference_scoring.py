from typing import Any

from dynsteer.adapter.toolsandbox.scorer import ToolSandboxConstraintScorer
from dynsteer.model import (
    Constraint,
    ConstraintTarget,
    Operator,
    ScoringContext,
    StageGoalSemanticKind,
    StateSnapshot,
)


def test_preserve_state_uses_reference_snapshot_as_target() -> None:
    """preserve_state 目标 dataframe 来自 runtime reference snapshot。"""
    reference_rows = [{"reminder_id": "r1", "content": "old"}]
    captured: dict[str, object] = {}

    class FakeEvaluation:
        _default_dbs_column_similarities: dict[str, object] = {}

        @staticmethod
        def guardrail_similarity(
            snapshot: object,
            target_dataframe: object,
            column_similarities: dict[str, object],
            reference_snapshot: object,
            **kwargs: Any,
        ) -> float:
            captured["target_rows"] = target_dataframe.to_dicts()
            captured["reference_rows"] = reference_snapshot.to_dicts()
            return 1.0

    def fake_loader(module_name: str) -> object:
        if module_name == "tool_sandbox.common.evaluation":
            return FakeEvaluation
        raise ModuleNotFoundError(module_name)

    constraint = Constraint(
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
    context = ScoringContext(
        matched_snapshots={
            "initial": StateSnapshot(
                snapshot_id="initial",
                after_step_id="initial",
                after_step_index=0,
                namespaces={"REMINDER": reference_rows},
            )
        }
    )
    scorer = ToolSandboxConstraintScorer(module_loader=fake_loader)

    score = scorer.score_custom_constraint(
        constraint=constraint,
        source=None,
        reference_source=None,
        actual={"rows": reference_rows},
        reference_value=None,
        context=context,
    )

    assert score.score == 1.0
    assert captured["target_rows"] == reference_rows
    assert captured["reference_rows"] == reference_rows
    assert any("target_source=reference_snapshot" in line for line in score.evidence)
