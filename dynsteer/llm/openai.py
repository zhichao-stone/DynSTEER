from __future__ import annotations

from openai import OpenAI

from dynsteer.llm.base import BaseLLM, LLMConfig, LLMConfigurationError, LLMMessage, LLMResponseError


class OpenaiLLM(BaseLLM):
    """基于 OpenAI-compatible Chat Completions 的 LLM 实现。"""

    def __init__(self, config: LLMConfig) -> None:
        """初始化 OpenAI-compatible LLM。

        Args:
            config: LLM provider 运行配置。
        """
        super().__init__(config)

    def _get_response_from_client(
        self,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        """使用 OpenAI client 调用 Chat Completions。

        Args:
            messages: 已校验的消息列表。
            request_params: 已转换的 OpenAI 请求参数。

        Returns:
            OpenAI Chat Completions 响应对象。
        """
        request: dict[str, object] = {
            "model": self._config.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
        }
        request.update(request_params)

        client: OpenAI = self.client
        if client is None:
            raise LLMResponseError("OpenAI client 未初始化")
        return client.chat.completions.create(**request)

    def _create_client(self) -> OpenAI:
        """根据配置构造 OpenAI SDK client。

        Returns:
            OpenAI SDK client。
        """
        kwargs: dict[str, object] = {"timeout": self._config.timeout_seconds}
        if self._config.api_key is not None:
            kwargs["api_key"] = self._config.api_key
        if self._config.base_url is not None:
            kwargs["base_url"] = self._config.base_url
        return OpenAI(**kwargs)

    def _normalize_infer_params(self, infer_params: dict[str, object]) -> dict[str, object]:
        """转换 OpenAI-compatible Chat Completions 推理参数。"""
        params = super()._normalize_infer_params(infer_params)
        params.setdefault("temperature", self._config.temperature)
        if self._config.max_tokens is not None:
            params.setdefault("max_tokens", self._config.max_tokens)
        
        response_format = params.get("response_format")
        if isinstance(response_format, str):
            normalized = response_format.strip().lower()
            if normalized in {"json", "json_object"}:
                params["response_format"] = {"type": "json_object"}
            elif normalized in {"text"}:
                params["response_format"] = {"type": "text"}
            else:
                raise LLMConfigurationError(f"不支持的 OpenAI response_format: {response_format}")
            
        return params

    def _response_text(self, response: object) -> str:
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
        raise LLMResponseError("OpenAI 返回内容缺少 message.content")
