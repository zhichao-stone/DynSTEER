from abc import ABC, abstractmethod
import logging
import time

from dynsteer.metrics import current_runtime_metrics_recorder
from dynsteer.model import LLMCallMetrics, LLMConfig, LLMMessage


logger = logging.getLogger(__name__)


class LLMConfigurationError(ValueError):
    """LLM is thrown out when the configuration is missing or invalid."""


class LLMResponseError(ValueError):
    """LLM returns if the content cannot be parsed."""


class BaseLLM(ABC):
    """Only the external LLM abstract base for interactive response interfaces."""

    def __init__(self, config: LLMConfig) -> None:
        """Initializes the LLM base class configuration."""
        if config is None:
            raise LLMConfigurationError('LLMConfig cannot be empty.')
        if not isinstance(config.provider, str) or not config.provider.strip():
            raise LLMConfigurationError("LLM provider must not be empty")
        if not isinstance(config.model, str) or not config.model.strip():
            raise LLMConfigurationError('LLM model cannot be empty')
        if config.max_retries < 1:
            raise LLMConfigurationError('max_retries must be greater than 0')
        if config.retry_base_seconds < 0 or config.retry_max_seconds < 0:
            raise LLMConfigurationError('retry waits cannot be negative')
        self._config = config

    def chat(self, messages: list[LLMMessage], **infer_params: object) -> str:
        """Interacts with LLM and returns the text of the reply."""
        self._validate_messages(messages)
        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            started = time.perf_counter()
            try:
                client = self._create_client()
                request_params = self._normalize_infer_params(infer_params, client)
                response = self._get_response_from_client(client, messages, request_params)
                elapsed_seconds = time.perf_counter() - started
                text = self._response_text(response)
                if isinstance(text, str) and text.strip():
                    self._record_llm_success(response, elapsed_seconds)
                    return text.strip()
                raise LLMResponseError('LLM returned an empty response')
            except LLMConfigurationError:
                raise
            except Exception as exc:
                elapsed_seconds = time.perf_counter() - started
                self._record_llm_failure(elapsed_seconds, exc)
                last_error = exc
                if self._is_permanent_client_error(exc):
                    break
                if attempt >= self._config.max_retries:
                    break
                delay = self._retry_delay(attempt)
                logger.warning(
                    'LLM call failed; preparing to retry',
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
        error_text = str(last_error) if last_error is not None else 'Unknown error'
        raise LLMResponseError(
            f"LLM call failed: provider={self._config.provider}, model={self._config.model}, attempts={self._config.max_retries}, error={error_text}"
        ) from last_error

    def _record_llm_success(self, response: object, elapsed_seconds: float) -> None:
        """Recording a successful provider call for statistics."""
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
        """Recording a failed provider call for statistics."""
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
        """Validate chat messages."""
        if messages is None or len(messages) == 0:
            raise LLMConfigurationError("messages must not be empty")
        for index, message in enumerate(messages):
            if message is None:
                raise LLMConfigurationError(f"messages[{index}].content must not be empty")
            if not isinstance(message.role, str) or not message.role.strip():
                raise LLMConfigurationError(f"messages[{index}].content must not be empty")
            if not isinstance(message.content, str) or not message.content.strip():
                raise LLMConfigurationError(f"messages[{index}].content must not be empty")

    def _retry_delay(self, attempt: int) -> float:
        """Calculate the exponential backoff delay before the next retry."""
        delay = self._config.retry_base_seconds * 2 ** max(attempt - 1, 0)
        return min(delay, self._config.retry_max_seconds)

    def _is_permanent_client_error(self, exc: Exception) -> bool:
        """Return whether a deterministic 4xx client error should not be retried."""
        status_code = getattr(exc, "status_code", None)
        if not isinstance(status_code, int):
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if not isinstance(status_code, int):
            return False
        return 400 <= status_code < 500 and status_code not in {408, 409, 429}

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]:
        """Normalizes the reasoning parameters."""
        if client is None:
            raise LLMConfigurationError('Provider client cannot be empty.')
        if infer_params is None:
            return {}
        return {key: value for key, value in infer_params.items() if value is not None}

    def _client_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {"timeout": self._config.timeout_seconds}
        if self._config.api_key is not None:
            kwargs["api_key"] = self._config.api_key
        if self._config.base_url is not None:
            kwargs["base_url"] = self._config.base_url
        return kwargs

    def _response_usage(self, response: object) -> dict[str, int | None]:
        """Extract token usage; the default profile provides none."""
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

    def _optional_usage_int(self, value: object) -> int | None:
        """Converts the provider user field to an optional integer."""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        return None

    @abstractmethod
    def _create_client(self) -> object:
        """Creates a provider SDK client."""

    @abstractmethod
    def _get_response_from_client(self, client: object, messages: list[LLMMessage], request_params: dict[str, object]) -> object:
        """Use the provider SDK client to get a response."""

    @abstractmethod
    def _response_text(self, response: object) -> str:
        """extracts the text from the provider response."""
