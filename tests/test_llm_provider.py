from __future__ import annotations

import json

import pytest

from dynsteer.llm import (
    AnthropicLLM,
    BaseLLM,
    LLMConfig,
    LLMConfigurationError,
    LLMMessage,
    LLMResponseError,
    OpenaiLLM,
    build_llm,
    build_llm_from_env,
)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        return _Response(self._content)


class FakeOpenAIClient:
    def __init__(self, content: str = "ok") -> None:
        completions = _FakeCompletions(content)
        self.chat = type("Chat", (), {"completions": completions})()


class _TestOpenaiLLM(OpenaiLLM):
    def __init__(self, config: LLMConfig, client: FakeOpenAIClient) -> None:
        self.test_client = client
        super().__init__(config)

    def _create_client(self) -> FakeOpenAIClient:
        return self.test_client


def _openai_config(max_tokens: int | None = None) -> LLMConfig:
    return LLMConfig(
        provider="openai_compatible",
        model="judge-model",
        api_key="secret-token",
        base_url="https://example.invalid/v1",
        max_tokens=max_tokens,
    )


def test_openai_llm_returns_message_content() -> None:
    client = FakeOpenAIClient("回复文本")
    llm = _TestOpenaiLLM(_openai_config(), client=client)

    text = llm.chat([LLMMessage(role="user", content="hello")])

    assert text == "回复文本"


def test_openai_llm_does_not_leak_api_key_in_messages() -> None:
    client = FakeOpenAIClient()
    llm = _TestOpenaiLLM(_openai_config(), client=client)

    llm.chat([LLMMessage(role="system", content="判官"), LLMMessage(role="user", content="prompt")])

    sent = json.dumps(client.chat.completions.calls[0]["messages"], ensure_ascii=False)
    assert "secret-token" not in sent


def test_openai_llm_omits_max_tokens_when_none() -> None:
    client = FakeOpenAIClient()
    llm = _TestOpenaiLLM(_openai_config(max_tokens=None), client=client)

    llm.chat([LLMMessage(role="user", content="prompt")])

    assert "max_tokens" not in client.chat.completions.calls[0]


def test_openai_llm_sends_max_tokens_when_configured() -> None:
    client = FakeOpenAIClient()
    llm = _TestOpenaiLLM(_openai_config(max_tokens=256), client=client)

    llm.chat([LLMMessage(role="user", content="prompt")])

    assert client.chat.completions.calls[0]["max_tokens"] == 256


def test_openai_llm_normalizes_chat_infer_params() -> None:
    client = FakeOpenAIClient()
    llm = _TestOpenaiLLM(_openai_config(max_tokens=256), client=client)

    llm.chat(
        [LLMMessage(role="user", content="prompt")],
        temperature=0.2,
        top_p=0.9,
        response_format="json_object",
    )

    request = client.chat.completions.calls[0]
    assert request["temperature"] == pytest.approx(0.2)
    assert request["top_p"] == pytest.approx(0.9)
    assert request["response_format"] == {"type": "json_object"}


def test_openai_llm_rejects_empty_content() -> None:
    llm = _TestOpenaiLLM(_openai_config(), client=FakeOpenAIClient(""))

    with pytest.raises(LLMResponseError):
        llm.chat([LLMMessage(role="user", content="prompt")])


def test_openai_llm_rejects_external_client_in_constructor() -> None:
    with pytest.raises(TypeError):
        OpenaiLLM(_openai_config(), client=FakeOpenAIClient())  # type: ignore[call-arg]


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _AnthropicResponse:
    def __init__(self, content: list[object]) -> None:
        self.content = content


class _FakeAnthropicMessages:
    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _ModelInfo:
    def __init__(self, max_tokens: int | None) -> None:
        self.max_tokens = max_tokens


class _FakeAnthropicModels:
    def __init__(self, max_tokens: int | None) -> None:
        self.max_tokens = max_tokens
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs: object) -> _ModelInfo:
        self.calls.append(kwargs)
        return _ModelInfo(self.max_tokens)


class FakeAnthropicClient:
    def __init__(self, response: object | Exception) -> None:
        self.messages = _FakeAnthropicMessages(response)
        self.models = _FakeAnthropicModels(max_tokens=4096)


class _TestAnthropicLLM(AnthropicLLM):
    def __init__(self, config: LLMConfig, client: FakeAnthropicClient) -> None:
        self.test_client = client
        super().__init__(config)

    def _create_client(self) -> FakeAnthropicClient:
        return self.test_client


def _anthropic_config(max_tokens: int | None = 256) -> LLMConfig:
    return LLMConfig(
        provider="anthropic",
        model="claude-3-haiku",
        api_key="anthropic-secret",
        max_tokens=max_tokens,
    )


def test_anthropic_llm_uses_official_sdk_messages_create_without_network() -> None:
    client = FakeAnthropicClient(_AnthropicResponse([_TextBlock("回复")]))
    llm = _TestAnthropicLLM(_anthropic_config(max_tokens=256), client=client)

    text = llm.chat([LLMMessage(role="system", content="判官"), LLMMessage(role="user", content="prompt")])

    assert text == "回复"
    request = client.messages.calls[0]
    assert request["model"] == "claude-3-haiku"
    assert request["system"] == "判官"
    assert request["max_tokens"] == 256
    assert all(message["role"] != "system" for message in request["messages"])


def test_anthropic_llm_rejects_missing_config_and_model_info_max_tokens() -> None:
    client = FakeAnthropicClient(_AnthropicResponse([]))
    client.models.max_tokens = None
    llm = _TestAnthropicLLM(_anthropic_config(max_tokens=None), client=client)

    with pytest.raises(LLMConfigurationError, match="max_tokens"):
        llm.chat([LLMMessage(role="user", content="prompt")])


def test_anthropic_llm_reads_model_info_max_tokens_and_normalizes_json_schema() -> None:
    client = FakeAnthropicClient(_AnthropicResponse([_TextBlock("回复")]))
    llm = _TestAnthropicLLM(_anthropic_config(max_tokens=None), client=client)
    schema = {
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
    }

    text = llm.chat(
        [LLMMessage(role="user", content="prompt")],
        json_schema=schema,
        top_k=20,
    )

    assert text == "回复"
    assert client.models.calls == [{"model_id": "claude-3-haiku"}]
    request = client.messages.calls[0]
    assert request["max_tokens"] == 4096
    assert request["top_k"] == 20
    assert request["output_config"] == {"format": {"type": "json_schema", "schema": schema}}


def test_anthropic_llm_wraps_sdk_response_errors() -> None:
    client = FakeAnthropicClient(RuntimeError("boom"))
    llm = _TestAnthropicLLM(_anthropic_config(max_tokens=256), client=client)

    with pytest.raises(LLMResponseError):
        llm.chat([LLMMessage(role="user", content="prompt")])


def test_anthropic_llm_rejects_external_client_in_constructor() -> None:
    with pytest.raises(TypeError):
        AnthropicLLM(_anthropic_config(), client=FakeAnthropicClient(_AnthropicResponse([])))  # type: ignore[call-arg]


class RetryLLM(OpenaiLLM):
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.client_creations = 0
        super().__init__(
            LLMConfig(
                provider="openai_compatible",
                model="retry-model",
                max_retries=3,
                retry_base_seconds=0.0,
                retry_max_seconds=0.0,
            )
        )

    def _create_client(self) -> object:
        self.client_creations += 1
        return object()

    def _get_response_from_client(
        self,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _response_text(self, response: object) -> str:
        if isinstance(response, str):
            return response
        return ""


def test_base_llm_retries_exceptions_and_empty_responses() -> None:
    llm = RetryLLM([RuntimeError("temporary"), "", "ok"])

    text = llm.chat([LLMMessage(role="user", content="prompt")])

    assert text == "ok"
    assert llm.calls == 3
    assert llm.client_creations == 1


class IdentityLLM(BaseLLM):
    def __init__(self) -> None:
        self.request_params: dict[str, object] | None = None
        super().__init__(LLMConfig(provider="identity", model="identity-model"))

    def _create_client(self) -> object:
        return object()

    def _get_response_from_client(
        self,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object:
        self.request_params = request_params
        return "ok"

    def _response_text(self, response: object) -> str:
        return str(response)


def test_base_llm_default_infer_params_are_identity_mapping_without_none() -> None:
    llm = IdentityLLM()

    text = llm.chat([LLMMessage(role="user", content="prompt")], custom=True, omitted=None)

    assert text == "ok"
    assert llm.request_params == {"custom": True}


def test_base_llm_raises_after_retry_limit() -> None:
    llm = RetryLLM(["", "", ""])

    with pytest.raises(LLMResponseError, match="retry-model"):
        llm.chat([LLMMessage(role="user", content="prompt")])


def test_build_llm_selects_provider() -> None:
    assert isinstance(build_llm(_openai_config()), OpenaiLLM)
    assert isinstance(build_llm(_anthropic_config()), AnthropicLLM)


def test_build_llm_rejects_unknown_provider() -> None:
    with pytest.raises(LLMConfigurationError):
        build_llm(LLMConfig(provider="unknown", model="m"))


def test_build_llm_from_env_returns_none_without_provider() -> None:
    assert build_llm_from_env({}) is None


def test_build_llm_from_env_uses_openai_api_key_fallback() -> None:
    env = {
        "DYNSTEER_JUDGE_PROVIDER": "qwen",
        "DYNSTEER_JUDGE_MODEL": "qwen-plus-latest",
        "DYNSTEER_JUDGE_BASE_URL": "https://example.invalid/v1",
        "OPENAI_API_KEY": "openai-secret",
    }

    llm = build_llm_from_env(env)

    assert isinstance(llm, OpenaiLLM)


def test_build_llm_from_env_rejects_invalid_max_tokens() -> None:
    env = {
        "DYNSTEER_JUDGE_PROVIDER": "openai_compatible",
        "DYNSTEER_JUDGE_MODEL": "judge-model",
        "DYNSTEER_JUDGE_MAX_TOKENS": "0",
    }

    with pytest.raises(LLMConfigurationError):
        build_llm_from_env(env)


def test_build_llm_from_env_reads_retry_options() -> None:
    env = {
        "DYNSTEER_JUDGE_PROVIDER": "openai_compatible",
        "DYNSTEER_JUDGE_MODEL": "judge-model",
        "DYNSTEER_JUDGE_MAX_RETRIES": "5",
        "DYNSTEER_JUDGE_RETRY_BASE_SECONDS": "0.25",
        "DYNSTEER_JUDGE_RETRY_MAX_SECONDS": "2.0",
    }

    llm = build_llm_from_env(env)

    assert isinstance(llm, OpenaiLLM)
    assert llm._config.max_retries == 5
    assert llm._config.retry_base_seconds == pytest.approx(0.25)
    assert llm._config.retry_max_seconds == pytest.approx(2.0)
