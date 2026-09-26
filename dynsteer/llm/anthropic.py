from anthropic import Anthropic
from dynsteer.llm.base import BaseLLM, LLMConfigurationError, LLMResponseError
from dynsteer.model import LLMMessage

class AnthropicLLM(BaseLLM):
    """LLM realized based on the official Anthropic SDK."""

    def _get_response_from_client(self, client: Anthropic, messages: list[LLMMessage], request_params: dict[str, object]) -> object:
        """Call Messages API using Anthropic client."""
        system_text = "\n".join((message.content for message in messages if message.role == "system"))
        request: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role != "system"
            ],
        }
        if system_text.strip():
            request["system"] = system_text
        request.update(request_params)
        return client.messages.create(**request)

    def _create_client(self) -> Anthropic:
        """Construct the official Anthropic filter based on LLMConfig."""
        return Anthropic(**self._client_kwargs())

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]:
        """Converts Anthropic Messages API reasoning parameters."""
        params = super()._normalize_infer_params(infer_params, client)
        params.setdefault("temperature", self._config.temperature)
        params["max_tokens"] = self._max_tokens_from_params(params, client)
        json_schema = params.pop("json_schema", None)
        response_format = params.pop("response_format", None)
        if json_schema is not None:
            params["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
            return params
        if response_format is None:
            return params
        if isinstance(response_format, dict):
            params["output_config"] = {"format": response_format}
            return params
        if not isinstance(response_format, str):
            raise LLMConfigurationError('Anthropic response_format must be a string or object')
        normalized = response_format.strip().lower()
        if normalized in {"json", "json_object"}:
            params["output_config"] = {"format": {"type": "json_object"}}
            return params
        raise LLMConfigurationError(f"Unsupported Anthropic response_format:{response_format}")

    def _max_tokens_from_params(self, params: dict[str, object], client: object) -> int:
        """Read or extrapolate Anthropic max_tokens."""
        configured = params.get("max_tokens", self._config.max_tokens)
        if isinstance(configured, int) and (not isinstance(configured, bool)) and (configured > 0):
            return configured
        if configured is not None:
            raise LLMConfigurationError('max_tokens must be positive integer')
        models = getattr(client, "models", None)
        retrieve = getattr(models, "retrieve", None)
        if not callable(retrieve):
            raise LLMConfigurationError('Anthropic Messages API needs to configure max_tokens')
        model_info = retrieve(model_id=self._config.model)
        model_max_tokens = getattr(model_info, "max_tokens", None)
        if not isinstance(model_max_tokens, int) or model_max_tokens <= 0:
            raise LLMConfigurationError('Anthropic Messages API needs to configure max_tokens')
        return model_max_tokens

    def _response_text(self, response: object) -> str:
        """Extracts the first non-empty text block from the Anthropic SDK response."""
        content = getattr(response, "content", None)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") if block.get("type") == "text" else None
                else:
                    text = getattr(block, "text", None) if getattr(block, "type", None) == "text" else None
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise LLMResponseError('Anthropic returns content missing text block')

    def _response_usage(self, response: object) -> dict[str, int | None]:
        """Token user-page extracts from Anthropic Messages."""
        usage = getattr(response, "usage", None)
        prompt_tokens = self._optional_usage_int(getattr(usage, "input_tokens", None))
        completion_tokens = self._optional_usage_int(getattr(usage, "output_tokens", None))
        total_tokens = prompt_tokens + completion_tokens if prompt_tokens is not None and completion_tokens is not None else None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
