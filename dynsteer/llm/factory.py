import os
from typing import Any, Mapping
from dynsteer.llm.base import BaseLLM, LLMConfigurationError
from dynsteer.llm.anthropic import AnthropicLLM
from dynsteer.llm.openai import OpenaiLLM
from dynsteer.model import DEFAULT_JUDGE_TEMPERATURE, LLMConfig
from dynsteer.utils import normalize_str_from_source, optional_str, parse_float_value, parse_int_value


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

def build_llm_from_env(env: Mapping[str, str] | None=None) -> BaseLLM | None:
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
        api_key=_provider_setting(source, provider, "API_KEY"),
        base_url=_provider_setting(source, provider, "BASE_URL"),
        timeout_seconds=parse_float_value(source.get("DYNSTEER_JUDGE_TIMEOUT_SECONDS"), "DYNSTEER_JUDGE_TIMEOUT_SECONDS", default=60.0, error_type=LLMConfigurationError),
        temperature=parse_float_value(source.get("DYNSTEER_JUDGE_TEMPERATURE"), "DYNSTEER_JUDGE_TEMPERATURE", default=DEFAULT_JUDGE_TEMPERATURE, error_type=LLMConfigurationError),
        max_tokens=parse_int_value(source.get("DYNSTEER_JUDGE_MAX_TOKENS"), "DYNSTEER_JUDGE_MAX_TOKENS", default=None, min_value=1, error_type=LLMConfigurationError),
        max_retries=parse_int_value(source.get("DYNSTEER_JUDGE_MAX_RETRIES"), "DYNSTEER_JUDGE_MAX_RETRIES", default=3, min_value=1, error_type=LLMConfigurationError),
        retry_base_seconds=parse_float_value(source.get("DYNSTEER_JUDGE_RETRY_BASE_SECONDS"), "DYNSTEER_JUDGE_RETRY_BASE_SECONDS", default=1.0, min_value=0.0, error_type=LLMConfigurationError),
        retry_max_seconds=parse_float_value(source.get("DYNSTEER_JUDGE_RETRY_MAX_SECONDS"), "DYNSTEER_JUDGE_RETRY_MAX_SECONDS", default=8.0, min_value=0.0, error_type=LLMConfigurationError),
        seed=parse_int_value(source.get("DYNSTEER_JUDGE_SEED"), "DYNSTEER_JUDGE_SEED", default=None, min_value=0, error_type=LLMConfigurationError),
    )
    return build_llm(config)

def build_llm_from_config(config: Mapping[str, Any] | LLMConfig | None, env: Mapping[str, str] | None=None) -> BaseLLM | None:
    """从结构化配置构建 BaseLLM。

    入参：
        config: Judge profile 或 LLMConfig；为空时返回 None。
        env: 环境变量来源，API key 只从这里读取。
    输出：
        BaseLLM 实例；没有配置 provider 时返回 None。
    """
    if config is None:
        return None
    if isinstance(config, LLMConfig):
        return build_llm(config)
    if not isinstance(config, Mapping):
        raise LLMConfigurationError("LLM 配置必须是 JSON 对象")
    provider_raw = config.get("provider")
    if provider_raw is None or not str(provider_raw).strip():
        return None
    provider = str(provider_raw).strip().lower()
    model = config.get("model")
    if model is None or not str(model).strip():
        raise LLMConfigurationError("Judge profile 中 model 不能为空")
    source = env if env is not None else os.environ
    llm_config = LLMConfig(
        provider=provider,
        model=str(model).strip(),
        api_key=_provider_setting(source, provider, "API_KEY"),
        base_url=optional_str(config.get("base_url")) or _provider_setting(source, provider, "BASE_URL"),
        timeout_seconds=parse_float_value(config.get("timeout_seconds"), "timeout_seconds", default=60.0, error_type=LLMConfigurationError),
        temperature=parse_float_value(config.get("temperature"), "temperature", default=DEFAULT_JUDGE_TEMPERATURE, error_type=LLMConfigurationError),
        max_tokens=parse_int_value(config.get("max_tokens"), "max_tokens", default=None, min_value=1, error_type=LLMConfigurationError),
        max_retries=parse_int_value(config.get("max_retries"), "max_retries", default=3, min_value=1, error_type=LLMConfigurationError),
        retry_base_seconds=parse_float_value(config.get("retry_base_seconds"), "retry_base_seconds", default=1.0, min_value=0.0, error_type=LLMConfigurationError),
        retry_max_seconds=parse_float_value(config.get("retry_max_seconds"), "retry_max_seconds", default=8.0, min_value=0.0, error_type=LLMConfigurationError),
        seed=parse_int_value(config.get("seed"), "seed", default=None, min_value=0, error_type=LLMConfigurationError),
    )
    return build_llm(llm_config)

def _provider_setting(source: Mapping[str, str], provider: str, setting: str) -> str | None:
    prefix = "ANTHROPIC" if provider in _ANTHROPIC_PROVIDERS else "OPENAI"
    return normalize_str_from_source(source, f"DYNSTEER_JUDGE_{setting}") or normalize_str_from_source(source, f"{prefix}_{setting}")
