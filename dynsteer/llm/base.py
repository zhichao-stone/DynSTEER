from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import time

from dynsteer.metrics import LLMCallMetrics, current_runtime_metrics_recorder

logger = logging.getLogger(__name__)


class LLMConfigurationError(ValueError):
    """LLM 配置缺失或不合法时抛出。"""


class LLMResponseError(ValueError):
    """LLM 返回内容无法解析时抛出。"""


@dataclass(frozen=True)
class LLMMessage:
    """单条对话消息。"""

    role: str
    content: str


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider 运行配置。"""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_tokens: int | None = None
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0


class BaseLLM(ABC):
    """对外只暴露交互响应接口的 LLM 抽象基类。"""

    def __init__(self, config: LLMConfig) -> None:
        """初始化 LLM 基类配置。

        Args:
            config: LLM provider 运行配置。
        """
        if config is None:
            raise LLMConfigurationError("LLMConfig 不能为空")
        if not isinstance(config.provider, str) or not config.provider.strip():
            raise LLMConfigurationError("LLM provider 不能为空")
        if not isinstance(config.model, str) or not config.model.strip():
            raise LLMConfigurationError("LLM model 不能为空")
        if config.max_retries < 1:
            raise LLMConfigurationError("max_retries 必须大于 0")
        if config.retry_base_seconds < 0 or config.retry_max_seconds < 0:
            raise LLMConfigurationError("重试等待时间不能为负数")
        self._config = config

    def chat(self, messages: list[LLMMessage], **infer_params: object) -> str:
        """与 LLM 交互并返回回复文本。

        Args:
            messages: 角色与内容组成的消息列表。
            infer_params: 本次推理请求的可选参数。

        Returns:
            LLM 回复的纯文本内容。
        """
        self._validate_messages(messages)
        client = self._create_client()
        request_params = self._normalize_infer_params(infer_params, client)
        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._get_response_from_client(client, messages, request_params)
                elapsed_seconds = time.perf_counter() - started
                text = self._response_text(response)
                if isinstance(text, str) and text.strip():
                    self._record_llm_success(response, elapsed_seconds)
                    return text.strip()
                raise LLMResponseError("LLM 返回空响应")
            except LLMConfigurationError:
                raise
            except Exception as exc:
                elapsed_seconds = time.perf_counter() - started
                self._record_llm_failure(elapsed_seconds, exc)
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                delay = self._retry_delay(attempt)
                logger.warning(
                    "LLM 调用失败，准备重试",
                    extra={
                        "provider": self._config.provider,
                        "model": self._config.model,
                        "attempt": attempt,
                        "max_retries": self._config.max_retries,
                        "delay_seconds": delay,
                        "error": str(exc),
                    },
                )
                if delay > 0:
                    time.sleep(delay)
        error_text = str(last_error) if last_error is not None else "未知错误"
        raise LLMResponseError(
            f"LLM 调用失败: provider={self._config.provider}, model={self._config.model}, "
            f"attempts={self._config.max_retries}, error={error_text}"
        ) from last_error

    def _record_llm_success(self, response: object, elapsed_seconds: float) -> None:
        """记录一次成功的 provider 调用统计。"""
        recorder = current_runtime_metrics_recorder()
        if recorder is None:
            return
        usage = self._response_usage(response)
        recorder.record_llm_call(
            LLMCallMetrics(
                provider=self._config.provider,
                model=self._config.model,
                elapsed_seconds=elapsed_seconds,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                success=True,
            )
        )

    def _record_llm_failure(self, elapsed_seconds: float, exc: Exception) -> None:
        """记录一次失败的 provider 调用统计。"""
        recorder = current_runtime_metrics_recorder()
        if recorder is None:
            return
        recorder.record_llm_call(
            LLMCallMetrics(
                provider=self._config.provider,
                model=self._config.model,
                elapsed_seconds=elapsed_seconds,
                success=False,
                error=str(exc),
            )
        )

    def _validate_messages(self, messages: list[LLMMessage]) -> None:
        """校验对话消息列表。

        Args:
            messages: 待发送给 provider 的消息列表。
        """
        if messages is None or len(messages) == 0:
            raise LLMConfigurationError("messages 不能为空")
        for index, message in enumerate(messages):
            if message is None:
                raise LLMConfigurationError(f"messages[{index}] 不能为空")
            if not isinstance(message.role, str) or not message.role.strip():
                raise LLMConfigurationError(f"messages[{index}].role 不能为空")
            if not isinstance(message.content, str) or not message.content.strip():
                raise LLMConfigurationError(f"messages[{index}].content 不能为空")

    def _retry_delay(self, attempt: int) -> float:
        """计算指数退避等待时间。

        Args:
            attempt: 当前失败尝试次数，从 1 开始。

        Returns:
            本次重试前等待秒数。
        """
        delay = self._config.retry_base_seconds * (2 ** max(attempt - 1, 0))
        return min(delay, self._config.retry_max_seconds)

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]:
        """归一化推理参数。

        Args:
            infer_params: chat 调用传入的推理参数。
            client: 本次 chat 调用创建的 provider SDK client。

        Returns:
            去除 None 值后的参数字典。
        """
        if client is None:
            raise LLMConfigurationError("provider client 不能为空")
        if infer_params is None:
            return {}
        return {key: value for key, value in infer_params.items() if value is not None}

    def _response_usage(self, response: object) -> dict[str, int | None]:
        """从 provider 响应中提取 token usage；默认 provider 不提供。"""
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

    def _optional_usage_int(self, value: object) -> int | None:
        """把 provider usage 字段转换为可选整数。"""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        return None

    @abstractmethod
    def _create_client(self) -> object:
        """创建 provider SDK client。

        Returns:
            provider SDK client。
        """

    @abstractmethod
    def _get_response_from_client(
        self,
        client: object,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        """使用 provider SDK client 获取响应。

        Args:
            client: 本次 chat 调用创建的 provider SDK client。
            messages: 已校验的消息列表。
            request_params: 已归一化的 provider 请求参数。

        Returns:
            provider 原始响应对象。
        """

    @abstractmethod
    def _response_text(self, response: object) -> str:
        """从 provider 响应中提取文本。

        Args:
            response: provider 原始响应对象。

        Returns:
            响应文本。
        """
