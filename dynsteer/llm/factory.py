from __future__ import annotations

import os
from typing import Mapping

from dynsteer.llm.anthropic import AnthropicLLM
from dynsteer.llm.base import BaseLLM, LLMConfigurationError
from dynsteer.llm.openai import OpenaiLLM
from dynsteer.model import LLMConfig

from dynsteer.utils import normalize_str_from_source

_OPENAI_PROVIDERS = {"openai_compatible", "openai", "qwen"}
_ANTHROPIC_PROVIDERS = {"anthropic", "claude"}


def build_llm(config: LLMConfig) -> BaseLLM:
    """根据 provider 构建对应的 BaseLLM 实例。"""
    if config is None:
        raise LLMConfigurationError("LLMConfig 不能为空")
    provider = config.provider.strip().lower()
    if provider in _OPENAI_PROVIDERS:
        return OpenaiLLM(config)
    if provider in _ANTHROPIC_PROVIDERS:
        return AnthropicLLM(config)
    raise LLMConfigurationError(f"不支持的 judge provider: {config.provider}")


def build_llm_from_env(env: Mapping[str, str] | None = None) -> BaseLLM | None:
    """从环境变量构建 BaseLLM。"""
    source = env if env is not None else os.environ

    provider_raw = normalize_str_from_source(source, "DYNSTEER_JUDGE_PROVIDER")
    if provider_raw is None:
        return None
    provider = provider_raw.strip().lower()

    model = normalize_str_from_source(source, "DYNSTEER_JUDGE_MODEL")
    if model is None:
        raise LLMConfigurationError("DYNSTEER_JUDGE_MODEL 不能为空")

    config = LLMConfig(
        provider=provider,
        model=model.strip(),
        api_key=_read_api_key(source, provider),
        base_url=_read_base_url(source, provider),
        timeout_seconds=float(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS", "60")),
        temperature=float(source.get("DYNSTEER_JUDGE_TEMPERATURE", "0")),
        max_tokens=_read_max_tokens(source.get("DYNSTEER_JUDGE_MAX_TOKENS")),
        max_retries=_read_positive_int(source.get("DYNSTEER_JUDGE_MAX_RETRIES"), "DYNSTEER_JUDGE_MAX_RETRIES", 3),
        retry_base_seconds=_read_non_negative_float(
            source.get("DYNSTEER_JUDGE_RETRY_BASE_SECONDS"),
            "DYNSTEER_JUDGE_RETRY_BASE_SECONDS",
            1.0,
        ),
        retry_max_seconds=_read_non_negative_float(
            source.get("DYNSTEER_JUDGE_RETRY_MAX_SECONDS"),
            "DYNSTEER_JUDGE_RETRY_MAX_SECONDS",
            8.0,
        ),
    )
    return build_llm(config)


def _read_max_tokens(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed <= 0:
        raise LLMConfigurationError("DYNSTEER_JUDGE_MAX_TOKENS 必须是正整数")
    return parsed


def _read_positive_int(value: str | None, label: str, default: int) -> int:
    """读取正整数环境变量。"""
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed <= 0:
        raise LLMConfigurationError(f"{label} 必须是正整数")
    return parsed


def _read_non_negative_float(value: str | None, label: str, default: float) -> float:
    """读取非负浮点环境变量。"""
    if value is None or not value.strip():
        return default
    parsed = float(value)
    if parsed < 0:
        raise LLMConfigurationError(f"{label} 不能为负数")
    return parsed


def _read_api_key(source: Mapping[str, str], provider: str) -> str | None:
    api_key = normalize_str_from_source(source, "DYNSTEER_JUDGE_API_KEY")
    if api_key is None:
        fallback_key = "ANTHROPIC_API_KEY" if provider in _ANTHROPIC_PROVIDERS else "OPENAI_API_KEY"
        api_key = normalize_str_from_source(source, fallback_key)
    return api_key

def _read_base_url(source: Mapping[str, str], provider: str) -> str | None:    
    base_url = normalize_str_from_source(source, "DYNSTEER_JUDGE_BASE_URL")
    if base_url is None:
        fallback_key = "ANTHROPIC_BASE_URL" if provider in _ANTHROPIC_PROVIDERS else "OPENAI_BASE_URL"
        base_url = normalize_str_from_source(source, fallback_key)
    return base_url
