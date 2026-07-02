from __future__ import annotations

from anthropic import Anthropic

from dynsteer.llm.base import BaseLLM, LLMConfig, LLMConfigurationError, LLMMessage, LLMResponseError


class AnthropicLLM(BaseLLM):
    """基于官方 Anthropic SDK 的 LLM 实现。"""

    def __init__(self, config: LLMConfig) -> None:
        """初始化 Anthropic LLM 封装。

        Args:
            config: LLM provider 运行配置，必须包含非空 model。

        Raises:
            LLMConfigurationError: config 或 model 缺失时抛出。
        """
        super().__init__(config)

    def _get_response_from_client(
        self,
        client: Anthropic,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        """使用 Anthropic client 调用 Messages API。

        Args:
            messages: 已校验的消息列表。
            request_params: 已转换的 Anthropic 请求参数。

        Returns:
            Anthropic Messages API 响应对象。
        """
        system_text = "\n".join(message.content for message in messages if message.role == "system")
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
        """根据 LLMConfig 构造官方 Anthropic client。

        Returns:
            Anthropic SDK client。
        """
        kwargs: dict[str, object] = {"timeout": self._config.timeout_seconds}
        if self._config.api_key is not None:
            kwargs["api_key"] = self._config.api_key
        if self._config.base_url is not None:
            kwargs["base_url"] = self._config.base_url
        return Anthropic(**kwargs)

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]:
        """转换 Anthropic Messages API 推理参数。"""
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
            raise LLMConfigurationError("Anthropic response_format 必须是字符串或对象")
        normalized = response_format.strip().lower()
        if normalized in {"json", "json_object"}:
            params["output_config"] = {"format": {"type": "json_object"}}
            return params
        raise LLMConfigurationError(f"不支持的 Anthropic response_format: {response_format}")

    def _max_tokens_from_params(self, params: dict[str, object], client: object) -> int:
        """读取或推断 Anthropic max_tokens。"""
        configured = params.get("max_tokens", self._config.max_tokens)
        if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
            return configured
        if configured is not None:
            raise LLMConfigurationError("max_tokens 必须是正整数")
        models = getattr(client, "models", None)
        retrieve = getattr(models, "retrieve", None)
        if not callable(retrieve):
            raise LLMConfigurationError("Anthropic Messages API 需要配置 max_tokens")
        model_info = retrieve(model_id=self._config.model)
        model_max_tokens = getattr(model_info, "max_tokens", None)
        if not isinstance(model_max_tokens, int) or model_max_tokens <= 0:
            raise LLMConfigurationError("Anthropic Messages API 需要配置 max_tokens")
        return model_max_tokens

    def _response_text(self, response: object) -> str:
        """从 Anthropic SDK 响应中提取首个非空文本块。

        Args:
            response: Anthropic Messages API 返回对象。

        Returns:
            首个非空 text block 内容。

        Raises:
            LLMResponseError: 响应中没有可用文本块时抛出。
        """
        content = getattr(response, "content", None)
        if isinstance(content, list):
            for block in content:
                text = self._text_from_block(block)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise LLMResponseError("Anthropic 返回内容缺少 text block")

    def _text_from_block(self, block: object) -> str | None:
        """读取 Anthropic text block 的文本内容。

        Args:
            block: SDK 返回的内容块，可为对象或字典。

        Returns:
            text 类型内容块的文本；类型不匹配时返回 None。
        """
        if isinstance(block, dict):
            if block.get("type") == "text":
                text = block.get("text")
                return text if isinstance(text, str) else None
            return None
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            return text
        return None

    def _response_usage(self, response: object) -> dict[str, int | None]:
        """从 Anthropic Messages 响应中提取 token usage。"""
        usage = getattr(response, "usage", None)
        prompt_tokens = self._optional_usage_int(getattr(usage, "input_tokens", None))
        completion_tokens = self._optional_usage_int(getattr(usage, "output_tokens", None))
        total_tokens = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
