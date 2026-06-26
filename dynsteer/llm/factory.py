from __future__ import annotations

import os
from typing import Mapping

from dynsteer.llm.anthropic import AnthropicLLM
from dynsteer.llm.base import BaseLLM, LLMConfig, LLMConfigurationError
from dynsteer.llm.openai import OpenaiLLM

_OPENAI_PROVIDERS = {"openai_compatible", "openai", "qwen"}
_ANTHROPIC_PROVIDERS = {"anthropic", "claude"}


def build_llm(config: LLMConfig) -> BaseLLM:
    """根据 provider 构建对应的 BaseLLM 实例。

    Args:
        config: LLM provider 运行配置。

    Returns:
        与 provider 匹配的 BaseLLM 实现。

    Raises:
        LLMConfigurationError: provider 不受支持时抛出。
    """
    if config is None:
        raise LLMConfigurationError("LLMConfig 不能为空")
    provider = config.provider.strip().lower()
    if provider in _OPENAI_PROVIDERS:
        return OpenaiLLM(config)
    if provider in _ANTHROPIC_PROVIDERS:
        return AnthropicLLM(config)
    raise LLMConfigurationError(f"不支持的 judge provider: {config.provider}")


def build_llm_from_env(env: Mapping[str, str] | None = None) -> BaseLLM | None:
    """从环境变量构建 BaseLLM。

    Args:
        env: 环境变量映射；测试时可传入 fake env。

    Returns:
        未配置 provider 时返回 None；配置 provider 时返回对应 BaseLLM。

    Raises:
        LLMConfigurationError: provider 已配置但其余必填项缺失或不合法时抛出。
    """
    source = env if env is not None else os.environ
    provider_raw = source.get("DYNSTEER_JUDGE_PROVIDER")
    if provider_raw is None or not provider_raw.strip():
        return None
    provider = provider_raw.strip().lower()
    model = source.get("DYNSTEER_JUDGE_MODEL")
    if model is None or not model.strip():
        raise LLMConfigurationError("DYNSTEER_JUDGE_MODEL 不能为空")
    base_url = source.get("DYNSTEER_JUDGE_BASE_URL")
    config = LLMConfig(
        provider=provider,
        model=model.strip(),
        api_key=_read_api_key(source, provider),
        base_url=base_url.strip() if isinstance(base_url, str) and base_url.strip() else None,
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
    """读取正整数环境变量。

    Args:
        value: 环境变量原始值。
        label: 环境变量名。
        default: 缺失时默认值。

    Returns:
        正整数。
    """
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed <= 0:
        raise LLMConfigurationError(f"{label} 必须是正整数")
    return parsed


def _read_non_negative_float(value: str | None, label: str, default: float) -> float:
    """读取非负浮点环境变量。

    Args:
        value: 环境变量原始值。
        label: 环境变量名。
        default: 缺失时默认值。

    Returns:
        非负浮点数。
    """
    if value is None or not value.strip():
        return default
    parsed = float(value)
    if parsed < 0:
        raise LLMConfigurationError(f"{label} 不能为负数")
    return parsed


def _read_api_key(source: Mapping[str, str], provider: str) -> str | None:
    direct = source.get("DYNSTEER_JUDGE_API_KEY")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    fallback_key = "ANTHROPIC_API_KEY" if provider in _ANTHROPIC_PROVIDERS else "OPENAI_API_KEY"
    fallback = source.get(fallback_key)
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None
