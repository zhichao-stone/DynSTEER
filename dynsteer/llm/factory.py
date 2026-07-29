import os
from typing import Any, Mapping
from dynsteer.llm.base import BaseLLM, LLMConfigurationError
from dynsteer.llm.anthropic import AnthropicLLM
from dynsteer.llm.openai import OpenaiLLM
from dynsteer.model import LLMConfig
from dynsteer.utils import normalize_str_from_source, optional_str
_OPENAI_PROVIDERS = {'openai_compatible', 'openai', 'qwen'}
_ANTHROPIC_PROVIDERS = {'anthropic', 'claude'}

def build_llm(config: LLMConfig) -> BaseLLM:
    """根据 provider 构建对应的 BaseLLM 实例。"""
    if config is None:
        raise LLMConfigurationError('LLMConfig 不能为空')
    provider = config.provider.strip().lower()
    if provider in _OPENAI_PROVIDERS:
        return OpenaiLLM(config)
    if provider in _ANTHROPIC_PROVIDERS:
        return AnthropicLLM(config)
    raise LLMConfigurationError(f'不支持的 judge provider: {config.provider}')

def build_llm_from_env(env: Mapping[str, str] | None=None) -> BaseLLM | None:
    """从环境变量构建 BaseLLM。"""
    source = env if env is not None else os.environ
    provider_raw = normalize_str_from_source(source, 'DYNSTEER_JUDGE_PROVIDER')
    if provider_raw is None:
        return None
    provider = provider_raw.strip().lower()
    model = normalize_str_from_source(source, 'DYNSTEER_JUDGE_MODEL')
    if model is None:
        raise LLMConfigurationError('DYNSTEER_JUDGE_MODEL 不能为空')
    config = LLMConfig(provider=provider, model=model.strip(), api_key=_read_api_key(source, provider), base_url=_read_base_url(source, provider), timeout_seconds=float(source.get('DYNSTEER_JUDGE_TIMEOUT_SECONDS', '60')), temperature=float(source.get('DYNSTEER_JUDGE_TEMPERATURE', '0')), max_tokens=_read_positive_int(source.get('DYNSTEER_JUDGE_MAX_TOKENS'), 'DYNSTEER_JUDGE_MAX_TOKENS', 0) or None, max_retries=_read_positive_int(source.get('DYNSTEER_JUDGE_MAX_RETRIES'), 'DYNSTEER_JUDGE_MAX_RETRIES', 3), retry_base_seconds=_read_non_negative_float(source.get('DYNSTEER_JUDGE_RETRY_BASE_SECONDS'), 'DYNSTEER_JUDGE_RETRY_BASE_SECONDS', 1.0), retry_max_seconds=_read_non_negative_float(source.get('DYNSTEER_JUDGE_RETRY_MAX_SECONDS'), 'DYNSTEER_JUDGE_RETRY_MAX_SECONDS', 8.0))
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
        raise LLMConfigurationError('LLM 配置必须是 JSON 对象')
    provider_raw = config.get('provider')
    if provider_raw is None or not str(provider_raw).strip():
        return None
    provider = str(provider_raw).strip().lower()
    model = config.get('model')
    if model is None or not str(model).strip():
        raise LLMConfigurationError('Judge profile 中 model 不能为空')
    source = env if env is not None else os.environ
    llm_config = LLMConfig(provider=provider, model=str(model).strip(), api_key=_read_api_key(source, provider), base_url=optional_str(config.get('base_url')) or _read_base_url(source, provider), timeout_seconds=float(config.get('timeout_seconds', 60.0)), temperature=float(config.get('temperature', 0.0)), max_tokens=_config_positive_int(config.get('max_tokens'), 'max_tokens'), max_retries=_config_positive_int(config.get('max_retries'), 'max_retries') or 3, retry_base_seconds=_config_non_negative_float(config.get('retry_base_seconds'), 'retry_base_seconds', 1.0), retry_max_seconds=_config_non_negative_float(config.get('retry_max_seconds'), 'retry_max_seconds', 8.0))
    return build_llm(llm_config)

def _read_positive_int(value: str | None, label: str, default: int) -> int:
    """读取正整数环境变量。"""
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed <= 0:
        raise LLMConfigurationError(f'{label} 必须是正整数')
    return parsed

def _read_non_negative_float(value: str | None, label: str, default: float) -> float:
    """读取非负浮点环境变量。"""
    if value is None or not value.strip():
        return default
    parsed = float(value)
    if parsed < 0:
        raise LLMConfigurationError(f'{label} 不能为负数')
    return parsed

def _read_api_key(source: Mapping[str, str], provider: str) -> str | None:
    api_key = normalize_str_from_source(source, 'DYNSTEER_JUDGE_API_KEY')
    if api_key is None:
        fallback_key = 'ANTHROPIC_API_KEY' if provider in _ANTHROPIC_PROVIDERS else 'OPENAI_API_KEY'
        api_key = normalize_str_from_source(source, fallback_key)
    return api_key

def _read_base_url(source: Mapping[str, str], provider: str) -> str | None:
    base_url = normalize_str_from_source(source, 'DYNSTEER_JUDGE_BASE_URL')
    if base_url is None:
        fallback_key = 'ANTHROPIC_BASE_URL' if provider in _ANTHROPIC_PROVIDERS else 'OPENAI_BASE_URL'
        base_url = normalize_str_from_source(source, fallback_key)
    return base_url

def _config_positive_int(value: object, label: str) -> int | None:
    """读取配置中的可选正整数。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LLMConfigurationError(f'{label} 必须是正整数')
    if value <= 0:
        raise LLMConfigurationError(f'{label} 必须是正整数')
    return value

def _config_non_negative_float(value: object, label: str, default: float) -> float:
    """读取配置中的非负浮点数。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMConfigurationError(f'{label} 必须是非负数字')
    if value < 0:
        raise LLMConfigurationError(f'{label} 必须是非负数字')
    return float(value)
