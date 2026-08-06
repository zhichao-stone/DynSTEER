import json

import pytest

from dynsteer.milestone import (
    GeneratorTaskView,
    MilestoneGenerationConfig,
    MilestoneGenerationError,
    PublicEvidence,
    PublicInvariant,
    compile_task_case,
)
from dynsteer.model import ConstraintTarget, Operator


class FakeLLM:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def chat(self, messages: list[object], **params: object) -> str:
        self.calls += 1
        assert len(messages) == 1
        return json.dumps(self.payload)


def _view(*, invariant: bool = False) -> GeneratorTaskView:
    evidence = [
        PublicEvidence(
            evidence_id="message",
            target=ConstraintTarget.STEP,
            selector="$.content",
            operator=Operator.EQUALS,
            source_ref="instruction",
        )
    ]
    invariants = []
    assets = []
    if invariant:
        assets.append({"source_ref": "rule", "value": "never delete data"})
        evidence.append(
            PublicEvidence(
                evidence_id="rule_message",
                target=ConstraintTarget.STEP,
                selector="$.content",
                operator=Operator.CONTAINS,
                source_ref="rule",
            )
        )
        invariants.append(
            PublicInvariant(
                invariant_id="no_delete",
                description="never delete data",
                source_ref="rule",
                evidence_id="rule_message",
                severity="error",
            )
        )
    return GeneratorTaskView(
        benchmark="bench",
        task_id="task",
        case_id="case",
        language="en",
        instruction="done",
        public_assets=assets,
        tool_schema={},
        environment_schema={},
        output_contract={},
        evidence_catalog=tuple(evidence),
        invariant_catalog=tuple(invariants),
    )


def _atom(atom_id: str, *, terminal: bool = False) -> dict[str, object]:
    return {
        "atom_id": atom_id,
        "name": atom_id,
        "description": atom_id,
        "source_refs": ["instruction"],
        "evidence_id": "message",
        "expected": "done",
        "terminal": terminal,
    }


def _successful_payload(path_count: int = 3) -> dict[str, object]:
    atoms = [_atom("common"), _atom("terminal", terminal=True)]
    paths = []
    for index in range(path_count):
        variant = f"variant_{index}"
        atoms.append(_atom(variant))
        paths.append({"strategy": variant, "atom_ids": ["common", variant, "terminal"]})
    return {"atoms": atoms, "paths": paths, "minefields": []}


def test_compile_uses_one_call_and_keeps_consensus_atoms() -> None:
    llm = FakeLLM(_successful_payload())

    graph, report = compile_task_case(_view(), MilestoneGenerationConfig(), llm)

    assert llm.calls == 1
    assert [node.milestone_id for node in graph.nodes] == ["common", "terminal"]
    assert graph.edges == [("common", "terminal")]
    assert graph.metadata["source"] == "generated"
    assert graph.metadata["necessity_basis"] == "synthetic_consensus"
    assert report.generation_status == "generated"
    assert report.distinct_path_count == 3


def test_two_thirds_boundary_uses_ceil() -> None:
    payload = _successful_payload(6)
    payload["atoms"].append(_atom("four_of_six"))
    payload["atoms"].append(_atom("three_of_six"))
    for index, path in enumerate(payload["paths"]):
        if index < 4:
            path["atom_ids"].insert(-1, "four_of_six")
        if index < 3:
            path["atom_ids"].insert(-1, "three_of_six")

    graph, _ = compile_task_case(_view(), MilestoneGenerationConfig(), FakeLLM(payload))

    ids = {node.milestone_id for node in graph.nodes}
    assert "four_of_six" in ids
    assert "three_of_six" not in ids


def test_duplicate_paths_auto_rejected() -> None:
    payload = _successful_payload()
    payload["paths"] = [payload["paths"][0]] * 3

    with pytest.raises(MilestoneGenerationError) as caught:
        compile_task_case(_view(), MilestoneGenerationConfig(), FakeLLM(payload))

    assert caught.value.report.generation_status == "auto_rejected"
    assert caught.value.report.distinct_path_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_refs", ["ground_truth"]),
        ("evidence_id", "unknown"),
        ("expected", "invented"),
    ],
)
def test_invalid_contract_atom_is_rejected(field: str, value: object) -> None:
    payload = _successful_payload()
    for atom in payload["atoms"]:
        atom[field] = value

    with pytest.raises(MilestoneGenerationError):
        compile_task_case(_view(), MilestoneGenerationConfig(), FakeLLM(payload))


def test_hidden_key_rejects_whole_response() -> None:
    payload = _successful_payload()
    payload["atoms"][0]["ground_truth"] = "hidden"

    with pytest.raises(MilestoneGenerationError) as caught:
        compile_task_case(_view(), MilestoneGenerationConfig(), FakeLLM(payload))

    assert caught.value.report.leakage_count == 1


def test_conflicting_order_does_not_create_edge() -> None:
    payload = _successful_payload()
    payload["paths"][1]["atom_ids"] = ["terminal", "common", "variant_1"]
    payload["atoms"][-2]["terminal"] = True

    graph, _ = compile_task_case(_view(), MilestoneGenerationConfig(), FakeLLM(payload))

    assert ("common", "terminal") not in graph.edges


def test_minefield_uses_catalog_severity() -> None:
    payload = _successful_payload()
    payload["minefields"] = [
        {
            "minefield_id": "mf",
            "invariant_id": "no_delete",
            "expected": "never delete data",
        }
    ]

    graph, _ = compile_task_case(
        _view(invariant=True), MilestoneGenerationConfig(), FakeLLM(payload)
    )

    assert graph.minefields[0].severity == "error"
    assert graph.minefields[0].penalty.value == 0.5


def test_digest_is_stable() -> None:
    assert _view().digest() == _view().digest()
