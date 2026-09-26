from openai import OpenAI
from dynsteer.llm.base import BaseLLM, LLMConfigurationError, LLMResponseError
from dynsteer.model import LLMMessage

class OpenaiLLM(BaseLLM):
    """LLM realization based on OpenAI-compatible Chat Commissions."""

    def _get_response_from_client(self, client: OpenAI, messages: list[LLMMessage], request_params: dict[str, object]) -> object:
        """Call Chat Corporations using OpenAI client."""
        request: dict[str, object] = {
            "model": self._config.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
        }
        request.update(request_params)
        return client.chat.completions.create(**request)

    def _create_client(self) -> OpenAI:
        """Construct OpenAI SDK profile according to configuration."""
        return OpenAI(**self._client_kwargs())

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]:
        """Converts OpenAI-compatible Chat Logitions reasoning parameters."""
        params = super()._normalize_infer_params(infer_params, client)
        params.setdefault("temperature", self._config.temperature)
        if self._config.max_tokens is not None:
            params.setdefault("max_tokens", self._config.max_tokens)
        if self._config.seed is not None:
            params.setdefault("seed", self._config.seed)
        response_format = params.get("response_format")
        if isinstance(response_format, str):
            normalized = response_format.strip().lower()
            if normalized in {"json", "json_object"}:
                params["response_format"] = {"type": "json_object"}
            elif normalized in {"text"}:
                params["response_format"] = {"type": "text"}
            else:
                raise LLMConfigurationError(f"Unsupported OpenAI response_format:{response_format}")
        return params

    def _response_text(self, response: object) -> str:
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
        raise LLMResponseError('OpenAI returns missing content message. contact')

    def _response_usage(self, response: object) -> dict[str, int | None]:
        """Token user-page extracts from OpenAI-compatible responses."""
        usage = getattr(response, "usage", None)
        return {
            "prompt_tokens": self._optional_usage_int(getattr(usage, "prompt_tokens", None)),
            "completion_tokens": self._optional_usage_int(getattr(usage, "completion_tokens", None)),
            "total_tokens": self._optional_usage_int(getattr(usage, "total_tokens", None)),
        }
