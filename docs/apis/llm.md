# LLM API

## 目标

`dynsteer.llm` 包负责不同 provider 的 LLM 构建与交互，对外只暴露统一的交互响应接口。Judge 只依赖 `BaseLLM.chat(...)`，不直接导入 provider SDK。

## 包结构

```text
dynsteer/llm/
- __init__.py    # 导出基础类型和 factory
- base.py        # LLMMessage、LLMConfig、BaseLLM、LLMConfigurationError、LLMResponseError
- factory.py     # build_llm、build_llm_from_env
- openai.py      # OpenaiLLM
- anthropic.py   # AnthropicLLM
```

## 核心类型

```python
@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMConfig:
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
    def chat(self, messages: list[LLMMessage], **infer_params: object) -> str: ...

    @abstractmethod
    def _create_client(self) -> object: ...

    def _normalize_infer_params(self, infer_params: dict[str, object], client: object) -> dict[str, object]: ...

    @abstractmethod
    def _get_response_from_client(
        self,
        client: object,
        messages: list[LLMMessage],
        request_params: dict[str, object],
    ) -> object: ...

    @abstractmethod
    def _response_text(self, response: object) -> str: ...
```

`BaseLLM.chat(...)` 由基类统一实现，流程为：校验 messages、在重试循环外调用 `_create_client()` 构建本次局部 client、调用 `_normalize_infer_params(..., client)` 转换推理参数、在重试循环内调用 `_get_response_from_client(client, ...)` 获取 provider 原始响应、调用 `_response_text(...)` 抽取文本。client 构建失败直接抛出；provider 调用异常、解析失败或空响应会按指数退避重试，达到 `max_retries` 后抛出 `LLMResponseError`。

`_normalize_infer_params(...)` 基类默认做恒等映射并过滤 `None` 值。provider 有特定参数转换时由子类覆盖。`BaseLLM` 不持有 `self.client` 成员，provider 子类必须通过 `_normalize_infer_params` 和 `_get_response_from_client` 的 `client` 入参使用本次局部 client。`chat` 支持通过 `**infer_params` 传入本次请求参数，例如 `temperature`、`top_p`、`top_k`、`max_tokens`、`response_format`、`json_schema`。

当调用发生在 `dynsteer.metrics.record_runtime_metrics()` 上下文内时，`BaseLLM.chat(...)` 会记录每次 provider attempt 的耗时、成功/失败状态和 usage tokens。OpenAI-compatible provider 读取 `prompt_tokens`、`completion_tokens`、`total_tokens`；Anthropic provider 读取 `input_tokens`、`output_tokens` 并聚合为 total。

## Client 生命周期

`BaseLLM.chat()` 每次调用通过 `_create_client()` 创建一个局部 provider client，并在本次调用的重试循环中复用。`OpenaiLLM` 在 `_get_response_from_client` 中通过入参 `client` 调用 `client.chat.completions.create`；`AnthropicLLM` 在 `_normalize_infer_params` 中通过入参 `client` 推断 `max_tokens`，并在 `_get_response_from_client` 中调用 `client.messages.create`。`chat()` 返回或抛错后，局部 client 离开作用域，避免 `BaseLLM` 实例长期保留 SDK client 或响应对象引用。

## OpenaiLLM

`OpenaiLLM(BaseLLM)` 使用 OpenAI-compatible Chat Completions，覆盖 GPT、Qwen 等兼容 OpenAI 协议的模型。它只使用 `openai==1.17.0` 已支持的 Chat Completions 能力，不使用 Responses API、`max_completion_tokens` 或 `max_output_tokens`。`max_tokens is None` 时不向 request 传 `max_tokens`。构造函数只接收 `LLMConfig`，不接收外部 client；client 类型由 `_create_client() -> OpenAI` 固定。

OpenAI 参数转换规则：

- `LLMConfig.temperature` 和 `LLMConfig.max_tokens` 作为默认请求参数；`chat(..., **infer_params)` 中同名参数优先。
- `response_format="json"` 或 `"json_object"` 转为 `{"type": "json_object"}`。
- `json_schema` 转为 `{"type": "json_schema", "json_schema": ...}`。

## AnthropicLLM

`AnthropicLLM(BaseLLM)` 使用官方 `anthropic.Anthropic` SDK 调用 Anthropic Messages API，不再维护手写 `anthropic-version` 或默认 `base_url`。`role` 为 `system` 的消息会合并为顶层 `system` 字段。构造函数只接收 `LLMConfig`，不接收外部 client；client 类型由 `_create_client() -> Anthropic` 固定。

Anthropic 参数转换规则：

- `max_tokens` 优先级为：`chat(max_tokens=...)`、`LLMConfig.max_tokens`、本次局部 `client.models.retrieve(model_id=model).max_tokens`。
- 无法获得正整数 `max_tokens` 时抛出 `LLMConfigurationError`。
- `response_format="json"` 或 `"json_object"` 转为 `output_config={"format": {"type": "json_object"}}`。
- `json_schema` 转为 `output_config={"format": {"type": "json_schema", "schema": ...}}`。
- `top_k`、`top_p`、`temperature` 等 Anthropic Messages API 参数直接透传。

## factory

```python
def build_llm(config: LLMConfig) -> BaseLLM: ...
def build_llm_from_env(env: Mapping[str, str] | None = None) -> BaseLLM | None: ...
```

provider 约定：

```text
DYNSTEER_JUDGE_PROVIDER=openai_compatible | openai | qwen | anthropic | claude
DYNSTEER_JUDGE_MODEL=qwen-plus-latest
DYNSTEER_JUDGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DYNSTEER_JUDGE_API_KEY=${DYNSTEER_JUDGE_API_KEY}
DYNSTEER_JUDGE_TIMEOUT_SECONDS=60
DYNSTEER_JUDGE_TEMPERATURE=0
# OpenAI-compatible 可选；Anthropic provider 必填
DYNSTEER_JUDGE_MAX_TOKENS=1024
DYNSTEER_JUDGE_MAX_RETRIES=3
DYNSTEER_JUDGE_RETRY_BASE_SECONDS=1
DYNSTEER_JUDGE_RETRY_MAX_SECONDS=8
DYNSTEER_EXPENSIVE_JUDGE_PASSES=3
```

`build_llm_from_env` 在未配置 `DYNSTEER_JUDGE_PROVIDER` 时返回 `None`。

API key 读取优先级：

1. `DYNSTEER_JUDGE_API_KEY`
2. OpenAI-compatible provider 使用 `OPENAI_API_KEY`
3. Anthropic provider 使用 `ANTHROPIC_API_KEY`

## 异常

- `LLMConfigurationError`: provider 配置缺失或不合法。
- `LLMResponseError`: provider 返回内容无法解析。
