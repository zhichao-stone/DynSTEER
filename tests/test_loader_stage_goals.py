from __future__ import annotations

import json

from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.loader import adapterd_case_path, load_task_case
from dynsteer.harness.model import BenchmarkCase, HarnessAdvanceResult, HarnessRunConfig
from dynsteer.model import TaskCase


class FakeAdapter(BaseBenchmarkAdapter):
    benchmark = "toolsandbox"

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        raise AssertionError("existing adapted case must not call adapt_task_case")

    def create_harness(self) -> BaseBenchmarkHarness:
        raise AssertionError("loader must not create harness")


class UnusedHarness(BaseBenchmarkHarness):
    benchmark = "toolsandbox"

    def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]:
        return []

    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir) -> object:  # type: ignore[no-untyped-def]
        return object()

    def advance_case(self, session: object) -> HarnessAdvanceResult:
        return HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False)

    def case_finished(self, session: object) -> bool:
        return True


def test_load_task_case_existing_file_does_not_reenrich_or_require_stage_goals(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        case_ids=("case",),
        metadata={"stage_goal_generation": "stored"},
    )
    path = adapterd_case_path(tmp_path, "case")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": "task",
                "task_description": "task",
                "case_id": "case",
                "environment_schema": {},
                "tool_schema": {},
                "policy_constraints": [],
                "initial_state": None,
                "milestone_graph": {
                    "nodes": [
                        {
                            "milestone_id": "m0",
                            "name": "m0",
                            "description": "root",
                            "constraints": [],
                            "dependency_predecessor_ids": [],
                            "stage_anchor_predecessor_id": None,
                        }
                    ],
                    "edges": [],
                    "minefields": [],
                    "default_thresholds": {},
                    "metadata": {},
                },
                "stage_goals": {},
                "task_types": [],
                "metadata": {},
            },
            ensure_ascii=False,
            indent=4,
        ),
        encoding="utf-8",
    )

    task_cases = load_task_case(config, FakeAdapter())

    assert task_cases[0].stage_goals == {}
    assert task_cases[0].milestone_graph is not None
    assert task_cases[0].milestone_graph.nodes[0].stage_anchor_predecessor_id is None
