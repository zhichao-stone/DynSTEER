from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import dynsteer.adapter.toolsandbox.adapter as adapter_module
from dynsteer.adapter.toolsandbox.adapter import ToolSandboxAdapter
from dynsteer.harness.model import HarnessRunConfig


class FakeContext:
    first_user_sandbox_message_index = 0

    def get_database(self, namespace: object, **kwargs: object) -> list[dict[str, object]]:
        if str(namespace).endswith("SANDBOX"):
            return [
                {
                    "sandbox_message_index": 0,
                    "sender": "USER",
                    "recipient": "AGENT",
                    "content": "Turn off cellular",
                    "conversation_active": True,
                }
            ]
        return []


class FakeScenario:
    def __init__(self) -> None:
        self.starting_context = FakeContext()
        self.categories: list[object] = []
        self.evaluation = SimpleNamespace(
            milestone_matcher=SimpleNamespace(milestones=[SimpleNamespace(snapshot_constraints=[])], edge_list=[]),
            minefield_matcher=SimpleNamespace(milestones=[]),
        )

    def play(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("adapt_task_case 不应执行 scenario.play")


class FakeScenariosModule:
    scenario = FakeScenario()

    @staticmethod
    def named_scenarios(preferred_tool_backend: object = None) -> dict[str, FakeScenario]:
        return {"case-1": FakeScenariosModule.scenario}


class FakeToolBackend:
    DEFAULT = "DEFAULT"

    @classmethod
    def __getitem__(cls, name: str) -> str:
        return name


class FakeExecutionContext:
    class DatabaseNamespace:
        SANDBOX = "DatabaseNamespace.SANDBOX"
        SETTING = "DatabaseNamespace.SETTING"

        @classmethod
        def __iter__(cls):
            return iter([cls.SANDBOX, cls.SETTING])


def test_toolsandbox_adapter_adapts_task_case_without_playing_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(module_name: str, dependency_error_message: str | None = None) -> object:
        if module_name == "tool_sandbox.scenarios":
            return FakeScenariosModule
        if module_name == "tool_sandbox.common.tool_discovery":
            return SimpleNamespace(ToolBackend={"DEFAULT": "DEFAULT"})
        if module_name == "tool_sandbox.common.execution_context":
            return FakeExecutionContext
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(adapter_module, "ensure_source_root", lambda *args: None)
    monkeypatch.setattr(adapter_module, "import_module", fake_import_module)
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path,
        runs_dir=tmp_path / "runs",
        results_dir=tmp_path / "results",
    )

    task_case = ToolSandboxAdapter().adapt_task_case(config, "case-1")

    assert task_case.case_id == "case-1"
    assert task_case.task_id == "toolsandbox::case-1"
    assert task_case.task_description == "Turn off cellular"
    assert task_case.milestone_graph is not None
    assert task_case.milestone_graph.nodes[0].dependency_predecessor_ids == []
    assert task_case.milestone_graph.metadata["graph_analysis"]["start_node_id"] == "__start__"
