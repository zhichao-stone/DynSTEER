from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from dynsteer.harness.model import HarnessRunConfig


class _RoleImpl:
    def __init__(self, name: str) -> None:
        self.name = name


class _RoleImplType:
    GPT_4_o_2024_05_13 = _RoleImpl("GPT_4_o_2024_05_13")
    Cli = _RoleImpl("Cli")

    @classmethod
    def __class_getitem__(cls, name: str) -> _RoleImpl:
        if name == "GPT_4_o_2024_05_13":
            return cls.GPT_4_o_2024_05_13
        if name == "Cli":
            return cls.Cli
        raise KeyError(name)


class _RoleType:
    USER = "user"
    EXECUTION_ENVIRONMENT = "environment"
    AGENT = "agent"


class _OriginalRole:
    pass


class _ExecutionEnvironment:
    pass


class _FakeOpenAI:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture()
def fake_toolsandbox_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def role_class(name: str) -> type:
        return type(name, (), {"model_name": name})

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))

    execution_context = types.ModuleType("tool_sandbox.common.execution_context")
    execution_context.RoleType = _RoleType
    monkeypatch.setitem(sys.modules, execution_context.__name__, execution_context)

    execution_environment = types.ModuleType("tool_sandbox.roles.execution_environment")
    execution_environment.ExecutionEnvironment = _ExecutionEnvironment
    monkeypatch.setitem(sys.modules, execution_environment.__name__, execution_environment)

    cli_utils = types.ModuleType("tool_sandbox.cli.utils")
    cli_utils.RoleImplType = _RoleImplType
    cli_utils.AGENT_TYPE_TO_FACTORY = {_RoleImplType.GPT_4_o_2024_05_13: _OriginalRole}
    cli_utils.USER_TYPE_TO_FACTORY = {_RoleImplType.GPT_4_o_2024_05_13: _OriginalRole}
    monkeypatch.setitem(sys.modules, cli_utils.__name__, cli_utils)

    openai_agent_module = types.ModuleType("tool_sandbox.roles.openai_api_agent")
    openai_agent_module.OpenAIAPIAgent = role_class("OpenAIAPIAgent")
    openai_agent_module.GPT_3_5_0125_Agent = role_class("GPT_3_5_0125_Agent")
    openai_agent_module.GPT_4_0125_Agent = role_class("GPT_4_0125_Agent")
    openai_agent_module.GPT_4_o_2024_05_13_Agent = role_class("GPT_4_o_2024_05_13_Agent")
    monkeypatch.setitem(sys.modules, openai_agent_module.__name__, openai_agent_module)

    openai_user_module = types.ModuleType("tool_sandbox.roles.openai_api_user")
    openai_user_module.OpenAIAPIUser = role_class("OpenAIAPIUser")
    openai_user_module.GPT_3_5_0125_User = role_class("GPT_3_5_0125_User")
    openai_user_module.GPT_4_0125_User = role_class("GPT_4_0125_User")
    openai_user_module.GPT_4_o_2024_05_13_User = role_class("GPT_4_o_2024_05_13_User")
    monkeypatch.setitem(sys.modules, openai_user_module.__name__, openai_user_module)

    for module_name in (
        "dynsteer.adapter.toolsandbox.agents",
        "dynsteer.adapter.toolsandbox.users",
    ):
        sys.modules.pop(module_name, None)


def test_toolsandbox_roles_prefer_dynsteer_environment_factories(
    fake_toolsandbox_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-harness")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://harness-proxy.example/v1")

    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=Path("data/toolsandbox"),
        metadata={"agent": "GPT_4_o_2024_05_13", "user": "GPT_4_o_2024_05_13"},
    )

    roles = ToolSandboxHarness()._toolsandbox_roles(config)

    assert isinstance(roles[_RoleType.EXECUTION_ENVIRONMENT], _ExecutionEnvironment)
    assert not isinstance(roles[_RoleType.AGENT], _OriginalRole)
    assert not isinstance(roles[_RoleType.USER], _OriginalRole)
    assert roles[_RoleType.AGENT].openai_client.kwargs["api_key"] == "sk-harness"
    assert roles[_RoleType.USER].openai_client.kwargs["base_url"] == "https://harness-proxy.example/v1"


def test_toolsandbox_roles_accept_unknown_role_names_as_openai_models(
    fake_toolsandbox_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom-proxy.example/v1")

    from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

    config = HarnessRunConfig(
        benchmark="toolsandbox",
        data_root=Path("data/toolsandbox"),
        metadata={"agent": "agent-model.x", "user": "user-model.y"},
    )

    roles = ToolSandboxHarness()._toolsandbox_roles(config)

    assert roles[_RoleType.AGENT].model_name == "agent-model.x"
    assert roles[_RoleType.USER].model_name == "user-model.y"
    assert roles[_RoleType.AGENT].openai_client.kwargs["base_url"] == "https://custom-proxy.example/v1"
