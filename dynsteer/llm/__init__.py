from __future__ import annotations

from importlib import import_module

from dynsteer.llm.base import BaseLLM, LLMConfigurationError, LLMResponseError
from dynsteer.model import LLMConfig, LLMMessage

__all__ = [
    "BaseLLM",
    "LLMConfig",
    "LLMConfigurationError",
    "LLMMessage",
    "LLMResponseError",
    "OpenaiLLM",
    "AnthropicLLM",
    "build_llm",
    "build_llm_from_config",
    "build_llm_from_env",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "OpenaiLLM": (".openai", "OpenaiLLM"),
    "AnthropicLLM": (".anthropic", "AnthropicLLM"),
    "build_llm": (".factory", "build_llm"),
    "build_llm_from_config": (".factory", "build_llm_from_config"),
    "build_llm_from_env": (".factory", "build_llm_from_env"),
}


def __getattr__(name: str) -> object:
    exported = _EXPORTS.get(name)
    if exported is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(exported[0], __name__)
    value = getattr(module, exported[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
