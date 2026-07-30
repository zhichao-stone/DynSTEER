import importlib
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any
import anthropic
from openai import OpenAI
from dynsteer.model import Actor
from dynsteer.utils import enum_name, normalize_client_config

@dataclass(frozen=True)
class RoleFactorySpec:
    module_name: str
    parent_class_name: str
    mode: str
    model_name: str | None = None
    client_attr: str = "openai_client"
    needs_model_name: bool = False
_OPENAI_AGENT_SPECS = {
    "GPT_3_5_0125": "GPT_3_5_0125_Agent",
    "GPT_4_0125": "GPT_4_0125_Agent",
    "GPT_4_o_2024_05_13": "GPT_4_o_2024_05_13_Agent"
}
_ANTHROPIC_AGENT_SPECS = {
    "Claude_3_Opus": "ClaudeOpusAgent",
    "Claude_3_Sonnet": "ClaudeSonnetAgent",
    "Claude_3_Haiku": "ClaudeHaikuAgent"
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
_AGENT_FACTORY_SPECS = {
    **{
        role_name: RoleFactorySpec(
            "tool_sandbox.roles.openai_api_agent",
            parent_class,
            "openai",
        )
        for role_name, parent_class in _OPENAI_AGENT_SPECS.items()
    },
    **{
        role_name: RoleFactorySpec(
            "tool_sandbox.roles.anthropic_api_agent",
            parent_class,
            "anthropic",
        )
        for role_name, parent_class in _ANTHROPIC_AGENT_SPECS.items()
    },
    **{
        role_name: RoleFactorySpec(
            module_name,
            parent_class,
            "openai_server",
            model_name,
            client_attr,
        )
        for role_name, (
            module_name,
            parent_class,
            model_name,
            client_attr,
        ) in _OPENAI_SERVER_AGENT_CONFIGS.items()
    },
    **{
        role_name: RoleFactorySpec(
            "tool_sandbox.roles.gemini_agent",
            "GeminiAgent",
            "pass",
            model_name,
        )
        for role_name, model_name in _GEMINI_AGENT_CONFIGS.items()
    },
}
_USER_FACTORY_SPECS = {
    role_name: RoleFactorySpec(
        "tool_sandbox.roles.openai_api_user",
        parent_class,
        "openai",
    )
    for role_name, parent_class in _OPENAI_USER_CLASSES.items()
}
_GENERIC_AGENT_SPEC = RoleFactorySpec("tool_sandbox.roles.openai_api_agent", "OpenAIAPIAgent", "openai", needs_model_name=True)
_GENERIC_USER_SPEC = RoleFactorySpec("tool_sandbox.roles.openai_api_user", "OpenAIAPIUser", "openai", needs_model_name=True)
_TOOL_SANDBOX_AGENT_FALLBACK_NAMES = {"Cli", "Unhelpful"}
_TOOL_SANDBOX_USER_FALLBACK_NAMES = {"Cli"}
_DYNSTEER_CLIENT_CONFIG_KWARG = "_dynsteer_client_config"

def get_agent_factory(role_impl_type: object, client_config: Mapping[str, Any] | None=None) -> Callable[[], object] | None:
    """按 ToolSandbox agent 角色类型创建可选独立 client 配置的工厂。"""
    return _role_factory(role_impl_type, _AGENT_FACTORY_SPECS, _TOOL_SANDBOX_AGENT_FALLBACK_NAMES, _GENERIC_AGENT_SPEC, client_config)

def get_user_factory(role_impl_type: object, client_config: Mapping[str, Any] | None=None) -> Callable[[], object] | None:
    """按 ToolSandbox user 角色类型创建可选独立 client 配置的工厂。"""
    return _role_factory(role_impl_type, _USER_FACTORY_SPECS, _TOOL_SANDBOX_USER_FALLBACK_NAMES, _GENERIC_USER_SPEC, client_config)

def _role_factory(role_impl_type: object, specs: dict[str, RoleFactorySpec], fallback_names: set[str], generic_spec: RoleFactorySpec, client_config: Mapping[str, Any] | None) -> Callable[[], object] | None:
    if role_impl_type is None:
        raise ValueError("role_impl_type 不能为空")
    role_name = _role_impl_name(role_impl_type)
    spec = specs.get(role_name)
    if spec is not None:
        return _build_role_factory(spec, client_config)
    if role_name in fallback_names:
        return None
    return _build_role_factory(replace(generic_spec, model_name=role_name), client_config)

def role_to_actor(sender: object, recipient: object) -> str:
    """将 ToolSandbox sender/recipient 映射为 DynSTEER actor。"""
    sender_name, recipient_name = enum_name(sender), enum_name(recipient)
    actor = actor_value_from_role_name(sender_name)
    if actor is not None:
        return actor
    return Actor.ENVIRONMENT.value if recipient_name == "AGENT" else Actor.AGENT.value

def role_to_recipient(recipient: object) -> str | None:
    """将 ToolSandbox recipient 映射为 DynSTEER recipient。"""
    return actor_value_from_role_name(enum_name(recipient))

def actor_value_from_role_name(role_name: str) -> str | None:
    mapping = {
        "SYSTEM": Actor.SYSTEM.value,
        "USER": Actor.USER.value,
        "AGENT": Actor.AGENT.value,
        "EXECUTION_ENVIRONMENT": Actor.ENVIRONMENT.value,
        "ENVIRONMENT": Actor.ENVIRONMENT.value,
        "EVALUATOR": Actor.EVALUATOR.value,
    }
    return mapping.get(role_name)

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
    return raw_name.rsplit(".", 1)[-1] if "." in raw_name else raw_name

def _env_value(name: str) -> str | None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("环境变量名称不能为空")
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None

def _client_kwargs(client_config: Mapping[str, Any] | None, api_key_env: str, base_url_env: str, default_api_key: str | None=None) -> dict[str, object]:
    config = normalize_client_config(client_config, "client_config")
    api_key = config.get("api_key") or _env_value(config.get("api_key_env") or api_key_env) or default_api_key
    if api_key is None:
        raise ValueError(f"环境变量 {api_key_env} 未配置")
    kwargs: dict[str, object] = {"api_key": api_key}
    base_url = config.get("base_url") or _env_value(config.get("base_url_env") or base_url_env)
    if base_url is not None:
        kwargs["base_url"] = base_url
    timeout_seconds = config.get("timeout_seconds")
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return kwargs

def _openai_client_from_config(client_config: Mapping[str, Any] | None, default_api_key: str | None=None) -> object:
    return OpenAI(**_client_kwargs(client_config, "OPENAI_API_KEY", "OPENAI_BASE_URL", default_api_key))

def _anthropic_client_from_config(client_config: Mapping[str, Any] | None) -> object:
    return anthropic.Anthropic(**_client_kwargs(client_config, "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"))

def _build_role_factory(spec: RoleFactorySpec, client_config: Mapping[str, Any] | None) -> Callable[[], object]:
    role_type = _environment_role_type(spec.module_name, spec.parent_class_name, spec.mode, needs_model_name=spec.needs_model_name, client_attr=spec.client_attr)
    kwargs: dict[str, object] = {_DYNSTEER_CLIENT_CONFIG_KWARG: client_config}
    if spec.model_name is not None:
        kwargs["model_name"] = spec.model_name
    return lambda: role_type(**kwargs)

@lru_cache(maxsize=None)
def _environment_role_type(module_name: str, parent_class_name: str, mode: str, needs_model_name: bool=False, client_attr: str="openai_client") -> type:
    parent_type = getattr(importlib.import_module(module_name), parent_class_name)

    class DynsteerEnvironmentRole(parent_type):

        def __init__(self, *args: object, **kwargs: object) -> None:
            client_config = kwargs.pop(_DYNSTEER_CLIENT_CONFIG_KWARG, None)
            if mode == "openai_server":
                super().__init__(*args, **kwargs)
                setattr(self, client_attr, _openai_client_from_config(client_config, default_api_key="EMPTY"))
                return
            if mode == "pass":
                super().__init__(*args, **kwargs)
                return
            if needs_model_name:
                self.model_name = str(kwargs.get("model_name") if "model_name" in kwargs else args[0])
            if mode == "anthropic":
                self.client = _anthropic_client_from_config(client_config)
                logging.getLogger("httpx").setLevel(logging.WARNING)
            else:
                self.openai_client = _openai_client_from_config(client_config)
    DynsteerEnvironmentRole.__name__ = f"DynSTEER{parent_type.__name__}"
    DynsteerEnvironmentRole.__qualname__ = DynsteerEnvironmentRole.__name__
    return DynsteerEnvironmentRole
