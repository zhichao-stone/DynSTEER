import importlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any
import anthropic
from openai import DefaultHttpxClient as OpenAIHttpxClient
from openai import OpenAI
from anthropic import DefaultHttpxClient as AnthropicHttpxClient

from dynsteer.adapter.toolsandbox.utils.usage import ProviderUsageRecorder
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

def get_agent_factory(
    role_impl_type: object,
    client_config: Mapping[str, Any] | None=None,
    usage_recorder: ProviderUsageRecorder | None=None,
) -> Callable[[], object] | None:
    """Creates an optional independently configured factory based on the ToolSandbox role type."""
    return _role_factory(role_impl_type, _AGENT_FACTORY_SPECS, _TOOL_SANDBOX_AGENT_FALLBACK_NAMES, _GENERIC_AGENT_SPEC, client_config, usage_recorder)

def get_user_factory(role_impl_type: object, client_config: Mapping[str, Any] | None=None) -> Callable[[], object] | None:
    """Creates an optional independently configured factory based on the ToolSandbox user role type."""
    return _role_factory(role_impl_type, _USER_FACTORY_SPECS, _TOOL_SANDBOX_USER_FALLBACK_NAMES, _GENERIC_USER_SPEC, client_config)

def role_client_config(role: object) -> dict[str, object]:
    """Read the role bound client_config."""
    if role is None:
        raise ValueError("Role can't be empty.")
    client_config = getattr(role, _DYNSTEER_CLIENT_CONFIG_KWARG, None)
    if client_config is None:
        return {}
    if not isinstance(client_config, Mapping):
        raise ValueError('role client_config must be a JSON object')
    return normalize_client_config(client_config, "client_config")

def _role_factory(
    role_impl_type: object,
    specs: dict[str, RoleFactorySpec],
    fallback_names: set[str],
    generic_spec: RoleFactorySpec,
    client_config: Mapping[str, Any] | None,
    usage_recorder: ProviderUsageRecorder | None=None,
) -> Callable[[], object] | None:
    if role_impl_type is None:
        raise ValueError('role_impl_type cannot be empty')
    role_name = _role_impl_name(role_impl_type)
    spec = specs.get(role_name)
    if spec is not None:
        return _build_role_factory(spec, client_config, usage_recorder)
    if role_name in fallback_names:
        return None
    return _build_role_factory(replace(generic_spec, model_name=role_name), client_config, usage_recorder)

def role_to_actor(sender: object, recipient: object) -> str:
    """Map ToolSandbox sender/recipient as DynSTEER actor."""
    sender_name, recipient_name = enum_name(sender), enum_name(recipient)
    actor = actor_value_from_role_name(sender_name)
    if actor is not None:
        return actor
    return Actor.ENVIRONMENT.value if recipient_name == "AGENT" else Actor.AGENT.value

def role_to_recipient(recipient: object) -> str | None:
    """Map the ToolSandbox map to DynSTEER map."""
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
            raise ValueError('Role_impl_type name cannot be empty')
        return raw_name
    name = getattr(role_impl_type, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    raw_name = str(role_impl_type).strip()
    if not raw_name:
        raise ValueError('Role_impl_type name cannot be empty')
    return raw_name.rsplit(".", 1)[-1] if "." in raw_name else raw_name

def _client_kwargs(config: Mapping[str, Any]) -> dict[str, object]:
    api_key = config.get("api_key")
    if api_key is None:
        raise ValueError('ToolSandbox client_config.api_key')
    kwargs: dict[str, object] = {"api_key": api_key}
    base_url = config.get("base_url")
    if base_url is None:
        raise ValueError('ToolSandbox client_config.base_url cannot be empty')
    kwargs["base_url"] = base_url
    timeout_seconds = config.get("timeout_seconds")
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return kwargs

def _openai_client_from_config(
    client_config: Mapping[str, Any],
    usage_recorder: ProviderUsageRecorder | None=None,
) -> object:
    kwargs = _client_kwargs(client_config)
    if usage_recorder is not None:
        return OpenAI(http_client=OpenAIHttpxClient(event_hooks={"response": [usage_recorder.record_openai_response]}), **kwargs)
    return OpenAI(**kwargs)


def _anthropic_client_from_config(client_config: Mapping[str, Any], usage_recorder: ProviderUsageRecorder | None=None) -> object:
    kwargs = _client_kwargs(client_config)
    if usage_recorder is not None:
        return anthropic.Anthropic(http_client=AnthropicHttpxClient(event_hooks={"response": [usage_recorder.record_anthropic_response]}), **kwargs)
    return anthropic.Anthropic(**kwargs)

def _build_role_factory(
    spec: RoleFactorySpec,
    client_config: Mapping[str, Any] | None,
    usage_recorder: ProviderUsageRecorder | None=None,
) -> Callable[[], object]:
    role_type = _environment_role_type(spec.module_name, spec.parent_class_name, spec.mode, needs_model_name=spec.needs_model_name, client_attr=spec.client_attr, usage_recorder=usage_recorder)
    kwargs: dict[str, object] = {_DYNSTEER_CLIENT_CONFIG_KWARG: client_config}
    if spec.model_name is not None:
        kwargs["model_name"] = spec.model_name
    return lambda: role_type(**kwargs)

@lru_cache(maxsize=None)
def _environment_role_type(
    module_name: str,
    parent_class_name: str,
    mode: str,
    needs_model_name: bool=False,
    client_attr: str="openai_client",
    usage_recorder: ProviderUsageRecorder | None=None,
) -> type:
    parent_type = getattr(importlib.import_module(module_name), parent_class_name)

    class DynsteerEnvironmentRole(parent_type):

        def __init__(self, *args: object, **kwargs: object) -> None:
            client_config = kwargs.pop(_DYNSTEER_CLIENT_CONFIG_KWARG, None)
            normalized_client_config = normalize_client_config(client_config, _DYNSTEER_CLIENT_CONFIG_KWARG)
            setattr(self, _DYNSTEER_CLIENT_CONFIG_KWARG, normalized_client_config)
            if mode == "openai_server":
                super().__init__(*args, **kwargs)
                setattr(self, client_attr, _openai_client_from_config(normalized_client_config, usage_recorder=usage_recorder))
                return
            if mode == "pass":
                super().__init__(*args, **kwargs)
                return
            if needs_model_name:
                self.model_name = str(kwargs.get("model_name") if "model_name" in kwargs else args[0])
            if mode == "anthropic":
                self.client = _anthropic_client_from_config(normalized_client_config, usage_recorder=usage_recorder)
                logging.getLogger("httpx").setLevel(logging.WARNING)
            else:
                self.openai_client = _openai_client_from_config(normalized_client_config, usage_recorder=usage_recorder)
    DynsteerEnvironmentRole.__name__ = f"DynSTEER{parent_type.__name__}"
    DynsteerEnvironmentRole.__qualname__ = DynsteerEnvironmentRole.__name__
    return DynsteerEnvironmentRole
