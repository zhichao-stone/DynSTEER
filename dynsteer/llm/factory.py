import os
from typing import Any, Mapping

from dynsteer.llm.base import BaseLLM, LLMConfigurationError
from dynsteer.model import LLMConfig

from dynsteer.utils import normalize_str_from_source

_OPENAI_PROVIDERS = {"openai_compatible", "openai", "qwen"}
_ANTHROPIC_PROVIDERS = {"anthropic", "claude"}


def build_llm(config: LLMConfig) -> BaseLLM:
    """鏍规嵁 provider 鏋勫缓瀵瑰簲鐨?BaseLLM 瀹炰緥銆?"""
    if config is None:
        raise LLMConfigurationError("LLMConfig 涓嶈兘涓虹┖")
    provider = config.provider.strip().lower()
    if provider in _OPENAI_PROVIDERS:
        from dynsteer.llm.openai import OpenaiLLM

        return OpenaiLLM(config)
    if provider in _ANTHROPIC_PROVIDERS:
        from dynsteer.llm.anthropic import AnthropicLLM

        return AnthropicLLM(config)
    raise LLMConfigurationError(f"涓嶆敮鎸佺殑 judge provider: {config.provider}")


def build_llm_from_env(env: Mapping[str, str] | None = None) -> BaseLLM | None:
    """浠庣幆澧冨彉閲忔瀯寤?BaseLLM銆?"""
    source = env if env is not None else os.environ

    provider_raw = normalize_str_from_source(source, "DYNSTEER_JUDGE_PROVIDER")
    if provider_raw is None:
        return None
    provider = provider_raw.strip().lower()

    model = normalize_str_from_source(source, "DYNSTEER_JUDGE_MODEL")
    if model is None:
        raise LLMConfigurationError("DYNSTEER_JUDGE_MODEL 涓嶈兘涓虹┖")

    config = LLMConfig(
        provider=provider,
        model=model.strip(),
        api_key=_read_api_key(source, provider),
        base_url=_read_base_url(source, provider),
        timeout_seconds=float(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS", "60")),
        temperature=float(source.get("DYNSTEER_JUDGE_TEMPERATURE", "0")),
        max_tokens=_read_positive_int(source.get("DYNSTEER_JUDGE_MAX_TOKENS"), "DYNSTEER_JUDGE_MAX_TOKENS", 0) or None,
        max_retries=_read_positive_int(source.get("DYNSTEER_JUDGE_MAX_RETRIES"), "DYNSTEER_JUDGE_MAX_RETRIES", 3),
        retry_base_seconds=_read_non_negative_float(
            source.get("DYNSTEER_JUDGE_RETRY_BASE_SECONDS"), "DYNSTEER_JUDGE_RETRY_BASE_SECONDS", 1.0
        ),
        retry_max_seconds=_read_non_negative_float(
            source.get("DYNSTEER_JUDGE_RETRY_MAX_SECONDS"), "DYNSTEER_JUDGE_RETRY_MAX_SECONDS", 8.0
        ),
    )
    return build_llm(config)


def build_llm_from_config(
    config: Mapping[str, Any] | LLMConfig | None, env: Mapping[str, str] | None = None
) -> BaseLLM | None:
    """浠庣粨鏋勫寲閰嶇疆鏋勫缓 BaseLLM銆?
    鍏ュ弬锛?
        config: Judge profile 鎴?LLMConfig锛涗负绌烘椂杩斿洖 None銆?
        env: 鐜鍙橀噺鏉ユ簮锛孉PI key 鍙粠杩欓噷璇诲彇銆?
    杈撳嚭锛?
        BaseLLM 瀹炰緥锛涙病鏈夐厤缃?provider 鏃惰繑鍥?None銆?
    """
    if config is None:
        return None
    if isinstance(config, LLMConfig):
        return build_llm(config)
    if not isinstance(config, Mapping):
        raise LLMConfigurationError("LLM 閰嶇疆蹇呴』鏄?JSON 瀵硅薄")
    provider_raw = config.get("provider")
    if provider_raw is None or not str(provider_raw).strip():
        return None
    provider = str(provider_raw).strip().lower()
    model = config.get("model")
    if model is None or not str(model).strip():
        raise LLMConfigurationError("Judge profile 涓?model 涓嶈兘涓虹┖")
    source = env if env is not None else os.environ
    llm_config = LLMConfig(
        provider=provider,
        model=str(model).strip(),
        api_key=_read_api_key(source, provider),
        base_url=_config_str(config.get("base_url")) or _read_base_url(source, provider),
        timeout_seconds=float(config.get("timeout_seconds", 60.0)),
        temperature=float(config.get("temperature", 0.0)),
        max_tokens=_config_positive_int(config.get("max_tokens"), "max_tokens"),
        max_retries=_config_positive_int(config.get("max_retries"), "max_retries") or 3,
        retry_base_seconds=_config_non_negative_float(config.get("retry_base_seconds"), "retry_base_seconds", 1.0),
        retry_max_seconds=_config_non_negative_float(config.get("retry_max_seconds"), "retry_max_seconds", 8.0),
    )
    return build_llm(llm_config)


def _read_positive_int(value: str | None, label: str, default: int) -> int:
    """璇诲彇姝ｆ暣鏁扮幆澧冨彉閲忋€?"""
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed <= 0:
        raise LLMConfigurationError(f"{label} 蹇呴』鏄鏁存暟")
    return parsed


def _read_non_negative_float(value: str | None, label: str, default: float) -> float:
    """璇诲彇闈炶礋娴偣鐜鍙橀噺銆?"""
    if value is None or not value.strip():
        return default
    parsed = float(value)
    if parsed < 0:
        raise LLMConfigurationError(f"{label} 涓嶈兘涓鸿礋鏁?")
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


def _config_str(value: object) -> str | None:
    """璇诲彇閰嶇疆瀛楃涓层€?"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _config_positive_int(value: object, label: str) -> int | None:
    """璇诲彇閰嶇疆涓殑鍙€夋鏁存暟銆?"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMConfigurationError(f"{label} 蹇呴』鏄鏁存暟")
    if value <= 0:
        raise LLMConfigurationError(f"{label} 蹇呴』鏄鏁存暟")
    return value


def _config_non_negative_float(value: object, label: str, default: float) -> float:
    """璇诲彇閰嶇疆涓殑闈炶礋娴偣鏁般€?"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMConfigurationError(f"{label} 蹇呴』鏄潪璐熸暟瀛?")
    parsed = float(value)
    if parsed < 0:
        raise LLMConfigurationError(f"{label} 涓嶈兘涓鸿礋鏁?")
    return parsed
