from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from dynsteer.adapter.toolsandbox_harness import ToolSandboxHarness
from dynsteer.harness.model import HarnessRunConfig


class FakeDataFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def to_dicts(self) -> list[dict[str, object]]:
        return list(self._rows)


class FakeRole:
    def __init__(self) -> None:
        self.torn_down = False

    def teardown(self) -> None:
        self.torn_down = True


class FakeRoleType(Enum):
    USER = "USER"
    AGENT = "AGENT"
    EXECUTION_ENVIRONMENT = "EXECUTION_ENVIRONMENT"


class FakeDatabaseNamespace(Enum):
    SANDBOX = "SANDBOX"
    SETTING = "SETTING"


class FakeToolBackend(Enum):
    DEFAULT = "DEFAULT"


class FakeContext:
    first_user_sandbox_message_index = 2

    def __init__(self) -> None:
        self.sandbox_rows = [
            {"sandbox_message_index": 1, "sender": "SYSTEM", "recipient": "AGENT", "content": "Rules"},
            {"sandbox_message_index": 2, "sender": "USER", "recipient": "AGENT", "content": "Turn off cellular"},
            {
                "sandbox_message_index": 3,
                "sender": "AGENT",
                "recipient": "EXECUTION_ENVIRONMENT",
                "content": "set_cellular_service_status(on=False)",
            },
            {
                "sandbox_message_index": 4,
                "sender": "EXECUTION_ENVIRONMENT",
                "recipient": "AGENT",
                "content": "None",
                "tool_trace": [
                    json.dumps(
                        {
                            "tool_name": "set_cellular_service_status",
                            "arguments": {"on": False},
                            "result": None,
                        }
                    )
                ],
            },
        ]
        self.setting_rows = [
            {"sandbox_message_index": 0, "cellular": True},
            {"sandbox_message_index": 4, "cellular": False},
        ]

    def get_database(self, namespace: object, **kwargs: object) -> FakeDataFrame:
        if namespace == FakeDatabaseNamespace.SANDBOX:
            return FakeDataFrame(self.sandbox_rows)
        return FakeDataFrame(self.setting_rows)


class FakeScenario:
    categories = ["SINGLE_TOOL_CALL"]
    evaluation = SimpleNamespace(milestone_matcher=SimpleNamespace(milestones=[], edge_list=[]))

    def __init__(self) -> None:
        self.received_output_directory: Path | None = None

    def play_and_evaluate(self, roles: dict[object, object], output_directory: Path, scenario_name: str) -> object:
        self.received_output_directory = output_directory
        return SimpleNamespace(
            ending_context=FakeContext(),
            evaluation_result=SimpleNamespace(
                similarity=1.0,
                milestone_similarity=1.0,
                minefield_similarity=0.0,
                turn_count=2,
                milestone_mapping={},
                minefield_mapping={},
            ),
        )


def install_fake_toolsandbox(monkeypatch: pytest.MonkeyPatch, scenarios: dict[str, object] | None = None) -> None:
    scenario_map = scenarios or {"cellular_off": FakeScenario()}
    root_module = ModuleType("tool_sandbox")
    common_module = ModuleType("tool_sandbox.common")
    scenarios_module = ModuleType("tool_sandbox.scenarios")
    discovery_module = ModuleType("tool_sandbox.common.tool_discovery")
    execution_context_module = ModuleType("tool_sandbox.common.execution_context")
    roles_module = ModuleType("tool_sandbox.roles")
    environment_module = ModuleType("tool_sandbox.roles.execution_environment")
    cli_module = ModuleType("tool_sandbox.cli")
    cli_utils_module = ModuleType("tool_sandbox.cli.utils")

    def named_scenarios(preferred_tool_backend: object) -> dict[str, object]:
        assert preferred_tool_backend == FakeToolBackend.DEFAULT
        return scenario_map

    scenarios_module.named_scenarios = named_scenarios  # type: ignore[attr-defined]
    discovery_module.ToolBackend = FakeToolBackend  # type: ignore[attr-defined]
    execution_context_module.RoleType = FakeRoleType  # type: ignore[attr-defined]
    execution_context_module.DatabaseNamespace = FakeDatabaseNamespace  # type: ignore[attr-defined]
    environment_module.ExecutionEnvironment = FakeRole  # type: ignore[attr-defined]
    cli_utils_module.RoleImplType = Enum(
        "RoleImplType",
        {"GPT_4_o_2024_05_13": "GPT_4_o_2024_05_13", "Cli": "Cli"},
    )
    cli_utils_module.AGENT_TYPE_TO_FACTORY = {cli_utils_module.RoleImplType.GPT_4_o_2024_05_13: FakeRole}  # type: ignore[attr-defined]
    cli_utils_module.USER_TYPE_TO_FACTORY = {cli_utils_module.RoleImplType.GPT_4_o_2024_05_13: FakeRole}  # type: ignore[attr-defined]

    for name, module in {
        "tool_sandbox": root_module,
        "tool_sandbox.common": common_module,
        "tool_sandbox.scenarios": scenarios_module,
        "tool_sandbox.common.tool_discovery": discovery_module,
        "tool_sandbox.common.execution_context": execution_context_module,
        "tool_sandbox.roles": roles_module,
        "tool_sandbox.roles.execution_environment": environment_module,
        "tool_sandbox.cli": cli_module,
        "tool_sandbox.cli.utils": cli_utils_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_list_cases_from_toolsandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_toolsandbox(monkeypatch)
    harness = ToolSandboxHarness()
    config = HarnessRunConfig(benchmark="toolsandbox", data_root=Path("data/toolsandbox"))

    cases = harness.list_cases(config)

    assert [case.case_id for case in cases] == ["cellular_off"]
    assert cases[0].categories == ["SINGLE_TOOL_CALL"]


def test_list_cases_requires_matching_benchmark() -> None:
    harness = ToolSandboxHarness()
    config = HarnessRunConfig(benchmark="other", data_root=Path("data/toolsandbox"))

    with pytest.raises(ValueError, match="benchmark 必须是 toolsandbox"):
        harness.list_cases(config)


def test_convert_sandbox_rows_to_trajectory_steps() -> None:
    harness = ToolSandboxHarness()
    rows = FakeContext().sandbox_rows

    steps = harness.convert_sandbox_rows_to_steps(rows)

    assert steps[0]["actor"] == "system"
    assert steps[2]["event_type"] == "tool_call"
    assert steps[3]["tool_result"] == {"success": True, "content": None, "exception": None}
    assert steps[3]["event_type"] == "tool_result"
    assert steps[3]["raw_sandbox_message_index"] == 4


def test_run_case_uses_fake_toolsandbox_and_writes_raw_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario = FakeScenario()
    install_fake_toolsandbox(monkeypatch, {"cellular_off": scenario})
    harness = ToolSandboxHarness()
    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=tmp_path / "data",
        scenario="cellular_off",
        output_dir=tmp_path / "runs",
    )

    result = harness.run_case(config, "cellular_off")

    assert result.case_id == "cellular_off"
    assert result.task_case.task_id == "toolsandbox::cellular_off"
    assert result.trajectory.run_id == "toolsandbox_cellular_off_run"
    assert result.raw_summary["similarity"] == 1.0
    assert scenario.received_output_directory == result.raw_output_dir
    assert result.raw_output_dir == tmp_path / "runs" / "toolsandbox" / "toolsandbox_cellular_off_run" / "cellular_off" / "raw"


def test_milestone_graph_preserves_toolsandbox_constraint_metadata() -> None:
    harness = ToolSandboxHarness()

    def guardrail_similarity() -> None:
        return None

    constraint = SimpleNamespace(
        database_namespace="SETTING",
        target_dataframe=[{"cellular": False}],
        snapshot_constraint=guardrail_similarity,
        reference_milestone_node_index=-1,
        column_similarity_measure={"cellular": object()},
    )
    scenario = SimpleNamespace(
        evaluation=SimpleNamespace(
            milestone_matcher=SimpleNamespace(
                milestones=[SimpleNamespace(snapshot_constraints=[constraint])],
                edge_list=[],
            ),
            minefield_matcher=SimpleNamespace(milestones=[], edge_list=[]),
        )
    )

    graph = harness._milestone_graph_from_scenario(scenario)

    assert graph["metadata"]["benchmark"] == "toolsandbox"
    assert graph["nodes"][0]["constraints"][0]["operator"] == "custom"
    assert graph["nodes"][0]["constraints"][0]["metadata"]["toolsandbox"]["guardrail"] is True


def test_run_case_rejects_missing_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fake_toolsandbox(monkeypatch)
    harness = ToolSandboxHarness()
    config = HarnessRunConfig(benchmark="toolsandbox", data_root=tmp_path / "data")

    with pytest.raises(KeyError, match="ToolSandbox 场景不存在"):
        harness.run_case(config, "missing")
