from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator

import pytest


class _FakeOpenAI:
    instances: list["_FakeOpenAI"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        _FakeOpenAI.instances.append(self)


class _FakeAnthropic:
    instances: list["_FakeAnthropic"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        _FakeAnthropic.instances.append(self)


class _NamedRole:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture()
def fake_toolsandbox_modules(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _FakeOpenAI.instances.clear()
    _FakeAnthropic.instances.clear()

    def role_class(name: str) -> type:
        return type(name, (), {"model_name": name})

    modules: dict[str, types.ModuleType] = {}

    openai_module = types.ModuleType("openai")
    openai_module.OpenAI = _FakeOpenAI
    modules["openai"] = openai_module

    anthropic_module = types.ModuleType("anthropic")
    anthropic_module.Anthropic = _FakeAnthropic
    modules["anthropic"] = anthropic_module

    openai_agent_module = types.ModuleType("tool_sandbox.roles.openai_api_agent")
    openai_agent_module.OpenAIAPIAgent = role_class("OpenAIAPIAgent")
    openai_agent_module.GPT_3_5_0125_Agent = role_class("GPT_3_5_0125_Agent")
    openai_agent_module.GPT_4_0125_Agent = role_class("GPT_4_0125_Agent")
    openai_agent_module.GPT_4_o_2024_05_13_Agent = role_class("GPT_4_o_2024_05_13_Agent")
    modules[openai_agent_module.__name__] = openai_agent_module

    openai_user_module = types.ModuleType("tool_sandbox.roles.openai_api_user")
    openai_user_module.OpenAIAPIUser = role_class("OpenAIAPIUser")
    openai_user_module.GPT_3_5_0125_User = role_class("GPT_3_5_0125_User")
    openai_user_module.GPT_4_0125_User = role_class("GPT_4_0125_User")
    openai_user_module.GPT_4_o_2024_05_13_User = role_class("GPT_4_o_2024_05_13_User")
    modules[openai_user_module.__name__] = openai_user_module

    anthropic_agent_module = types.ModuleType("tool_sandbox.roles.anthropic_api_agent")
    anthropic_agent_module.ClaudeOpusAgent = role_class("ClaudeOpusAgent")
    anthropic_agent_module.ClaudeSonnetAgent = role_class("ClaudeSonnetAgent")
    anthropic_agent_module.ClaudeHaikuAgent = role_class("ClaudeHaikuAgent")
    modules[anthropic_agent_module.__name__] = anthropic_agent_module

    for module_name, class_names in {
        "tool_sandbox.roles.hermes_api_agent": ("HermesAPIAgent",),
        "tool_sandbox.roles.gorilla_api_agent": ("GorillaAPIAgent",),
        "tool_sandbox.roles.mistral_api_agent": ("MistralOpenAIServerAgent",),
        "tool_sandbox.roles.gemini_agent": ("GeminiAgent",),
        "tool_sandbox.roles.cohere_agent": ("CohereAgent",),
        "tool_sandbox.roles.cli_role": ("CliAgent", "CliUser"),
        "tool_sandbox.roles.unhelpful_agent": ("UnhelpfulAgent",),
    }.items():
        module = types.ModuleType(module_name)
        for class_name in class_names:
            setattr(module, class_name, role_class(class_name))
        modules[module_name] = module

    for module_name, module in modules.items():
        monkeypatch.setitem(sys.modules, module_name, module)

    for module_name in (
        "dynsteer.adapter.toolsandbox.agents",
        "dynsteer.adapter.toolsandbox.users",
    ):
        sys.modules.pop(module_name, None)

    yield

    for module_name in (
        "dynsteer.adapter.toolsandbox.agents",
        "dynsteer.adapter.toolsandbox.users",
    ):
        sys.modules.pop(module_name, None)


def test_openai_agent_factory_uses_environment_client_configuration(
    fake_toolsandbox_modules: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")

    agents = importlib.import_module("dynsteer.adapter.toolsandbox.agents")
    factory = agents.get_agent_factory(_NamedRole("GPT_4_o_2024_05_13"))

    assert factory is not None
    agent = factory()

    assert agent.__class__.__mro__[1].__name__ == "GPT_4_o_2024_05_13_Agent"
    assert agent.openai_client.kwargs == {
        "api_key": "sk-test",
        "base_url": "https://proxy.example/v1",
    }


def test_anthropic_agent_factory_uses_environment_client_configuration(
    fake_toolsandbox_modules: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic-proxy.example")

    agents = importlib.import_module("dynsteer.adapter.toolsandbox.agents")
    factory = agents.get_agent_factory(_NamedRole("Claude_3_Haiku"))

    assert factory is not None
    agent = factory()

    assert agent.__class__.__mro__[1].__name__ == "ClaudeHaikuAgent"
    assert agent.client.kwargs == {
        "api_key": "anthropic-key",
        "base_url": "https://anthropic-proxy.example",
    }


def test_openai_user_factory_uses_environment_client_configuration(
    fake_toolsandbox_modules: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://user-proxy.example/v1")

    users = importlib.import_module("dynsteer.adapter.toolsandbox.users")
    factory = users.get_user_factory(_NamedRole("GPT_4_0125"))

    assert factory is not None
    user = factory()

    assert user.__class__.__mro__[1].__name__ == "GPT_4_0125_User"
    assert user.openai_client.kwargs == {
        "api_key": "sk-user",
        "base_url": "https://user-proxy.example/v1",
    }


def test_unknown_agent_factory_defaults_to_openai_agent_with_model_name(
    fake_toolsandbox_modules: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom-agent")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom-agent.example/v1")

    agents = importlib.import_module("dynsteer.adapter.toolsandbox.agents")
    factory = agents.get_agent_factory("my-custom-agent.model")

    assert factory is not None
    agent = factory()

    assert agent.__class__.__mro__[1].__name__ == "OpenAIAPIAgent"
    assert agent.model_name == "my-custom-agent.model"
    assert agent.openai_client.kwargs == {
        "api_key": "sk-custom-agent",
        "base_url": "https://custom-agent.example/v1",
    }


def test_unknown_user_factory_defaults_to_openai_user_with_model_name(
    fake_toolsandbox_modules: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom-user")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom-user.example/v1")

    users = importlib.import_module("dynsteer.adapter.toolsandbox.users")
    factory = users.get_user_factory("my-custom-user.model")

    assert factory is not None
    user = factory()

    assert user.__class__.__mro__[1].__name__ == "OpenAIAPIUser"
    assert user.model_name == "my-custom-user.model"
    assert user.openai_client.kwargs == {
        "api_key": "sk-custom-user",
        "base_url": "https://custom-user.example/v1",
    }
