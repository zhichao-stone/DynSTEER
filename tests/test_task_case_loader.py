from __future__ import annotations

import json
from pathlib import Path

import pytest

from dynsteer.adapter.base import BaseBenchmarkAdapter
from dynsteer.adapter.loader import load_task_case, load_task_case_file
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.model import MilestoneGraph, TaskCase


def test_load_task_case_file_requires_milestone_graph(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "task-case-1",
                "task_description": "test",
                "case_id": "case-1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="缺少 milestone_graph"):
        load_task_case_file(path, expected_case_id="case-1")


def test_load_task_case_rebuilds_adapted_case_without_milestone_graph(tmp_path: Path) -> None:
    adapted_dir = tmp_path / "adapted_cases"
    adapted_dir.mkdir()
    adapted_path = adapted_dir / "case-1.json"
    adapted_path.write_text(
        json.dumps(
            {
                "task_id": "stale-case-1",
                "task_description": "stale",
                "case_id": "case-1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    adapter = _GraphAdapter()
    config = HarnessRunConfig(benchmark="testbench", data_root=tmp_path, case_ids=("case-1",))

    cases = load_task_case(config, adapter)

    assert adapter.call_count == 1
    assert len(cases) == 1
    assert cases[0].milestone_graph is not None
    assert "graph_analysis" in cases[0].milestone_graph.metadata
    saved = json.loads(adapted_path.read_text(encoding="utf-8"))
    assert saved["milestone_graph"] is not None


class _GraphAdapter(BaseBenchmarkAdapter):
    benchmark = "testbench"

    def __init__(self) -> None:
        self.call_count = 0

    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase:
        self.call_count += 1
        return TaskCase(
            task_id=f"task-{case_id}",
            task_description="test",
            case_id=case_id,
            milestone_graph=MilestoneGraph(),
        )
