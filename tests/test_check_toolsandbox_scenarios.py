from __future__ import annotations

import ast
import importlib.util
import random
from pathlib import Path


def _load_checker_module() -> object:
    script_path = Path(__file__).resolve().parents[2] / "check_toolsandbox_scenarios.py"
    spec = importlib.util.spec_from_file_location("check_toolsandbox_scenarios", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_augmented_scenario_names_match_toolsandbox_suffixes() -> None:
    checker = _load_checker_module()

    names = checker._augmented_scenario_names("sample")

    assert names == [
        "sample",
        "sample_3_distraction_tools",
        "sample_10_distraction_tools",
        "sample_all_tools",
        "sample_3_distraction_tools_tool_description_scrambled",
        "sample_3_distraction_tools_arg_type_scrambled",
        "sample_3_distraction_tools_arg_description_scrambled",
        "sample_3_distraction_tools_tool_name_scrambled",
    ]


def test_default_edge_list_is_linked_path() -> None:
    checker = _load_checker_module()

    assert checker._edge_list(None, 4) == [[0, 1], [1, 2], [2, 3]]
    assert checker._path_structure(4, [[0, 1], [1, 2], [2, 3]])["kind"] == "linked_list"


def test_static_extension_summary_counts_milestones_and_minefields() -> None:
    checker = _load_checker_module()
    call = ast.parse(
        """
ScenarioExtension(
    name="sample",
    milestones=[Milestone(snapshot_constraints=[SnapshotConstraint(), SnapshotConstraint()])],
    milestone_edge_list=[(0, 1)],
    minefields=[Minefield(snapshot_constraints=[SnapshotConstraint()])],
)
"""
    ).body[0].value

    summary = checker._summarize_static_extension(call, "sample_file.py")

    assert summary["name"] == "sample"
    assert summary["milestone_count"] == 1
    assert summary["minefield_count"] == 1
    assert summary["milestones"][0]["constraint_count"] == 2
    assert summary["minefields"][0]["constraint_count"] == 1


def test_aggregate_report_includes_one_sample_name_per_milestone_count() -> None:
    checker = _load_checker_module()
    scenarios = [
        {"name": "zero-a", "milestone_count": 0},
        {"name": "zero-b", "milestone_count": 0},
        {"name": "two-a", "milestone_count": 2},
        {"name": "two-b", "milestone_count": 2},
        {"name": "three-a", "milestone_count": 3},
    ]

    report = checker._aggregate_report(scenarios, "static", [], rng=random.Random(7))

    samples = report["samples"]["milestone_count"]
    assert set(samples) == {"0", "2", "3"}
    assert samples["0"]["scenario_name"] in {"zero-a", "zero-b"}
    assert samples["2"]["scenario_name"] in {"two-a", "two-b"}
    assert samples["3"]["scenario_name"] == "three-a"
