from dynsteer.llm.base import BaseLLM, LLMConfigurationError, LLMResponseError
from dynsteer.llm.factory import build_llm, build_llm_from_env
from dynsteer.model import LLMConfig, LLMMessage


def __getattr__(name: str) -> object:
    """按需加载具体 provider，避免未使用的 SDK 缺失影响本地评估。"""
    if name == "OpenaiLLM":
        from dynsteer.llm.openai import OpenaiLLM

        return OpenaiLLM
    if name == "AnthropicLLM":
        from dynsteer.llm.anthropic import AnthropicLLM

        return AnthropicLLM
    raise AttributeError(name)


__all__ = [
    "BaseLLM",
    "LLMConfig",
    "LLMConfigurationError",
    "LLMMessage",
    "LLMResponseError",
    "OpenaiLLM",
    "AnthropicLLM",
    "build_llm",
    "build_llm_from_env",
]
