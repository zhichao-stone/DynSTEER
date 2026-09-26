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
    """Construct the provider-specific BaseLLM instance."""
    if config is None:
        raise LLMConfigurationError('LLMConfig cannot be empty.')
    provider = config.provider.strip().lower()
    if provider in _OPENAI_PROVIDERS:
        return OpenaiLLM(config)
    if provider in _ANTHROPIC_PROVIDERS:
        return AnthropicLLM(config)
    raise LLMConfigurationError(f"unsupported judge provider: {config.provider}")

def build_llm_from_env(env: Mapping[str, str] | None=None) -> BaseLLM | None:
    """Construct a judge LLM from environment variables."""
    source = env if env is not None else os.environ
    provider_raw = normalize_str_from_source(source, "DYNSTEER_JUDGE_PROVIDER")
    if provider_raw is None:
        return None
    provider = provider_raw.strip().lower()
    model = normalize_str_from_source(source, "DYNSTEER_JUDGE_MODEL")
    if model is None:
        raise LLMConfigurationError('DYNSTEER_JUDGE_MODEL cannot be empty')
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
    """Build a BaseLLM from a structured configuration.

    Args:
        config: Judge profile or ``LLMConfig``; an empty profile returns ``None``.
        env: Optional environment mapping used to resolve ``*_env`` references.

    Returns:
        The configured LLM, or ``None`` when no profile is configured.
    """
    source = os.environ if env is None else env
    if config is None:
        return None
    if isinstance(config, LLMConfig):
        return build_llm(config)
    if not isinstance(config, Mapping):
        raise LLMConfigurationError('LLM configuration must be a JSON object')
    provider_raw = config.get("provider")
    if provider_raw is None or not str(provider_raw).strip():
        return None
    provider = str(provider_raw).strip().lower()
    model = config.get("model")
    if model is None or not str(model).strip():
        raise LLMConfigurationError('Judge profile model must not be empty')
    llm_config = LLMConfig(
        provider=provider,
        model=str(model).strip(),
        api_key=_environment_setting(source, config, "api_key"),
        base_url=_environment_setting(source, config, "base_url"),
        timeout_seconds=parse_float_value(config.get("timeout_seconds"), "timeout_seconds", default=60.0, error_type=LLMConfigurationError),
        temperature=parse_float_value(config.get("temperature"), "temperature", default=DEFAULT_JUDGE_TEMPERATURE, error_type=LLMConfigurationError),
        max_tokens=parse_int_value(config.get("max_tokens"), "max_tokens", default=None, min_value=1, error_type=LLMConfigurationError),
        max_retries=parse_int_value(config.get("max_retries"), "max_retries", default=3, min_value=1, error_type=LLMConfigurationError),
        retry_base_seconds=parse_float_value(config.get("retry_base_seconds"), "retry_base_seconds", default=1.0, min_value=0.0, error_type=LLMConfigurationError),
        retry_max_seconds=parse_float_value(config.get("retry_max_seconds"), "retry_max_seconds", default=8.0, min_value=0.0, error_type=LLMConfigurationError),
        seed=parse_int_value(config.get("seed"), "seed", default=None, min_value=0, error_type=LLMConfigurationError),
    )
    if llm_config.api_key is None or llm_config.base_url is None:
        raise LLMConfigurationError('LLM configuration must provide api_key/base_url values or environment references')
    return build_llm(llm_config)

def _provider_setting(source: Mapping[str, str], provider: str, setting: str) -> str | None:
    prefix = "ANTHROPIC" if provider in _ANTHROPIC_PROVIDERS else "OPENAI"
    return normalize_str_from_source(source, f"DYNSTEER_JUDGE_{setting}") or normalize_str_from_source(source, f"{prefix}_{setting}")

def _environment_setting(source: Mapping[str, str], config: Mapping[str, Any], key: str) -> str | None:
    literal = optional_str(config.get(key))
    environment_name = optional_str(config.get(f"{key}_env"))
    if literal is not None and environment_name is not None:
        raise LLMConfigurationError(f"LLM config must not provide both {key} and {key}_env")
    if environment_name is None:
        return literal
    value = normalize_str_from_source(source, environment_name)
    if value is None:
        raise LLMConfigurationError(f"LLM environment variable is not set: {environment_name}")
    return value
