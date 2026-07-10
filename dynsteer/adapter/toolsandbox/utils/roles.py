from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from functools import lru_cache

_OPENAI_AGENT_CLASSES = {
    "GPT_3_5_0125": "GPT_3_5_0125_Agent",
    "GPT_4_0125": "GPT_4_0125_Agent",
    "GPT_4_o_2024_05_13": "GPT_4_o_2024_05_13_Agent",
}
_ANTHROPIC_AGENT_CLASSES = {
    "Claude_3_Opus": "ClaudeOpusAgent",
    "Claude_3_Sonnet": "ClaudeSonnetAgent",
    "Claude_3_Haiku": "ClaudeHaikuAgent",
}
_OPENAI_SERVER_AGENT_CONFIGS = {
    "Hermes": (
        "tool_sandbox.roles.hermes_api_agent",
        "HermesAPIAgent",
        "NousResearch/Hermes-2-Pro-Mistral-7B",
        "client",
    ),
    "Gorilla": (
        "tool_sandbox.roles.gorilla_api_agent",
        "GorillaAPIAgent",
        "gorilla-llm/gorilla-openfunctions-v2",
        "client",
    ),
    "MistralOpenAIServer": (
        "tool_sandbox.roles.mistral_api_agent",
        "MistralOpenAIServerAgent",
        "mistralai/Mistral-7B-Instruct-v0.3",
        "openai_client",
    ),
    "Cohere_Command_R": (
        "tool_sandbox.roles.cohere_agent",
        "CohereAgent",
        "CohereForAI/c4ai-command-r-v01",
        "client",
    ),
    "Cohere_Command_R_Plus": (
        "tool_sandbox.roles.cohere_agent",
        "CohereAgent",
        "CohereForAI/c4ai-command-r-plus",
        "client",
    ),
}
_GEMINI_AGENT_CONFIGS = {
    "Gemini_1_0": "gemini-1.0-pro",
    "Gemini_1_5": "gemini-1.5-pro-001",
    "Gemini_1_5_Flash": "gemini-1.5-flash-001",
}
_OPENAI_USER_CLASSES = {
    "GPT_3_5_0125": "GPT_3_5_0125_User",
    "GPT_4_0125": "GPT_4_0125_User",
    "GPT_4_o_2024_05_13": "GPT_4_o_2024_05_13_User",
}
_TOOL_SANDBOX_AGENT_FALLBACK_NAMES = {"Cli", "Unhelpful"}
_TOOL_SANDBOX_USER_FALLBACK_NAMES = {"Cli"}


def get_agent_factory(role_impl_type: object) -> Callable[[], object] | None:
    if role_impl_type is None:
        raise ValueError("role_impl_type 不能为空")
    role_name = _role_impl_name(role_impl_type)
    if role_name in _OPENAI_AGENT_CLASSES:
        return _openai_agent_factory(_OPENAI_AGENT_CLASSES[role_name])
    if role_name in _ANTHROPIC_AGENT_CLASSES:
        return _anthropic_agent_factory(_ANTHROPIC_AGENT_CLASSES[role_name])
    if role_name in _OPENAI_SERVER_AGENT_CONFIGS:
        return _openai_server_agent_factory(_OPENAI_SERVER_AGENT_CONFIGS[role_name])
    if role_name in _GEMINI_AGENT_CONFIGS:
        return _gemini_agent_factory(_GEMINI_AGENT_CONFIGS[role_name])
    if role_name in _TOOL_SANDBOX_AGENT_FALLBACK_NAMES:
        return None
    return _generic_openai_agent_factory(role_name)


def get_user_factory(role_impl_type: object) -> Callable[[], object] | None:
    if role_impl_type is None:
        raise ValueError("role_impl_type 不能为空")
    role_name = _role_impl_name(role_impl_type)
    if role_name in _OPENAI_USER_CLASSES:
        return _environment_openai_user_type(_OPENAI_USER_CLASSES[role_name])
    if role_name in _TOOL_SANDBOX_USER_FALLBACK_NAMES:
        return None
    return _generic_openai_user_factory(role_name)


def _role_impl_name(role_impl_type: object) -> str:
    if isinstance(role_impl_type, str):
        raw_name = role_impl_type.strip()
        if not raw_name:
            raise ValueError("role_impl_type 名称不能为空")
        return raw_name
    name = getattr(role_impl_type, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    raw_name = str(role_impl_type).strip()
    if not raw_name:
        raise ValueError("role_impl_type 名称不能为空")
    if "." in raw_name:
        raw_name = raw_name.rsplit(".", 1)[-1]
    return raw_name


def _env_value(name: str) -> str | None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("环境变量名称不能为空")
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required_env_value(name: str) -> str:
    value = _env_value(name)
    if value is None:
        raise ValueError(f"环境变量 {name} 未配置")
    return value


def _openai_client_from_env(default_api_key: str | None = None) -> object:
    from openai import OpenAI

    api_key = _env_value("OPENAI_API_KEY") or default_api_key
    if api_key is None:
        raise ValueError("环境变量 OPENAI_API_KEY 未配置")
    kwargs: dict[str, str] = {"api_key": api_key}
    base_url = _env_value("OPENAI_BASE_URL")
    if base_url is not None:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _anthropic_client_from_env() -> object:
    import anthropic

    kwargs: dict[str, str] = {"api_key": _required_env_value("ANTHROPIC_API_KEY")}
    base_url = _env_value("ANTHROPIC_BASE_URL")
    if base_url is not None:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


def _openai_agent_factory(parent_class_name: str) -> Callable[[], object]:
    return _environment_role_type("tool_sandbox.roles.openai_api_agent", parent_class_name, "openai")


def _generic_openai_agent_factory(model_name: str) -> Callable[[], object]:
    return _model_factory(
        _environment_role_type("tool_sandbox.roles.openai_api_agent", "OpenAIAPIAgent", "openai", needs_model_name=True),
        model_name,
    )


def _generic_openai_user_factory(model_name: str) -> Callable[[], object]:
    return _model_factory(
        _environment_role_type("tool_sandbox.roles.openai_api_user", "OpenAIAPIUser", "openai", needs_model_name=True),
        model_name,
    )


def _anthropic_agent_factory(parent_class_name: str) -> Callable[[], object]:
    return _environment_role_type("tool_sandbox.roles.anthropic_api_agent", parent_class_name, "anthropic")


def _openai_server_agent_factory(
    config: tuple[str, str, str, str],
) -> Callable[[], object]:
    module_name, parent_class_name, model_name, client_attr = config
    return _model_factory(
        _environment_role_type(module_name, parent_class_name, "openai_server", client_attr=client_attr),
        model_name,
    )


def _gemini_agent_factory(model_name: str) -> Callable[[], object]:
    return _model_factory(_environment_role_type("tool_sandbox.roles.gemini_agent", "GeminiAgent", "pass"), model_name)


@lru_cache(maxsize=None)
def _environment_openai_user_type(parent_class_name: str) -> type:
    return _environment_role_type("tool_sandbox.roles.openai_api_user", parent_class_name, "openai")


@lru_cache(maxsize=None)
def _model_factory(role_type: type, model_name: str) -> Callable[[], object]:
    def factory() -> object:
        return role_type(model_name=model_name)

    return factory


@lru_cache(maxsize=None)
def _environment_role_type(
    module_name: str,
    parent_class_name: str,
    mode: str,
    needs_model_name: bool = False,
    client_attr: str = "openai_client",
) -> type:
    parent_type = getattr(importlib.import_module(module_name), parent_class_name)

    class DynsteerEnvironmentRole(parent_type):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            if mode == "openai_server":
                super().__init__(*args, **kwargs)
                setattr(self, client_attr, _openai_client_from_env(default_api_key="EMPTY"))
                return
            if mode == "pass":
                super().__init__(*args, **kwargs)
                return
            if needs_model_name:
                self.model_name = str(kwargs.get("model_name") if "model_name" in kwargs else args[0])
            if mode == "anthropic":
                self.client = _anthropic_client_from_env()
                logging.getLogger("httpx").setLevel(logging.WARNING)
            else:
                self.openai_client = _openai_client_from_env()

    DynsteerEnvironmentRole.__name__ = f"DynSTEER{parent_type.__name__}"
    DynsteerEnvironmentRole.__qualname__ = DynsteerEnvironmentRole.__name__
    return DynsteerEnvironmentRole
