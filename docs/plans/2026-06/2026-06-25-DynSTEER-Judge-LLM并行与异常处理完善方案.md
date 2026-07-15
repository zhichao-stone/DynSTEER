# DynSTEER Judge LLM 并行与异常处理完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善 Standard/Expensive Judge prompt、重构 BaseLLM 通用交互与重试逻辑、为 benchmark 多配置运行增加可控并行，并补强关键异常处理。

**Architecture:** Judge 层新增独立 prompt 模块，使用 PromptTemplate 统一管理多语言模板，Standard 与 Expensive 使用不同评估模板；LLM 层由 BaseLLM 统一实现 chat 流程，子类只实现 provider client 构建、请求和响应解析；并行仅发生在独立 config/case 级别，单 case 内仍保持阶段评估顺序。

**Tech Stack:** Python 3.11、uv、pytest、OpenAI SDK、Anthropic SDK、concurrent.futures.ThreadPoolExecutor、logging。

---

## 设计结论

### 方案取舍

推荐采用“模板模块 + BaseLLM 模板方法 + case 级线程池”方案。

- 方案 A：只扩写现有 `_build_prompt` 字符串。实现最快，但 `LLMJudge` 会继续承担 prompt、调用、解析多种职责，Expensive 多轮模板也会堆在基类中，不利于维护。
- 方案 B：新增 `dynsteer/judges/prompt.py`，保留 Judge 的评估流程，prompt 生成独立成模板函数。该方案职责清晰，改动可控，适合当前代码结构。
- 方案 C：把 Judge prompt、输出 schema、解析器做成完整策略对象。扩展性最高，但当前只有 standard/expensive 两档，抽象会过早。

本次采用方案 B。并行采用 `ThreadPoolExecutor`，不使用进程池，因为 harness session、SDK client、ToolSandbox 原生对象不可稳定 pickle，且主要瓶颈是 I/O、LLM 调用和 benchmark 运行等待。

## 文件结构

- Create: `dynsteer/judges/prompt.py`
  - 负责 PromptTemplate、多语言 Standard/Expensive prompt 模板、阶段上下文裁剪和输出 schema 文本。
- Modify: `dynsteer/judges/base.py`
  - 移除通用 `_build_prompt` 的“JSON dump 即 prompt”实现，保留 `_call_json`、结果解析和 payload 校验。
- Modify: `dynsteer/judges/standard.py`
  - 使用 `build_standard_prompt(...)`。
- Modify: `dynsteer/judges/expensive.py`
  - 使用多类 prompt：维度聚焦评估、风险复核、最终裁决。
- Modify: `dynsteer/llm/base.py`
  - BaseLLM 实现 `chat(...)`，新增抽象方法 `_create_client(...)`、`_get_response_from_client(...)`、`_response_text(...)`，并实现指数退避重试。
- Modify: `dynsteer/llm/openai.py`
  - 删除外部 client 注入和本地 chat 实现，子类方法标注 `OpenAI` client 类型。
- Modify: `dynsteer/llm/anthropic.py`
  - 删除外部 client 注入和本地 chat 实现，子类方法标注 `Anthropic` client 类型。
- Modify: `dynsteer/llm/factory.py`
  - 读取可选重试配置环境变量。
- Modify: `dynsteer/harness/config.py`
  - 从 `data/{benchmark}/benchmark.json` 读取 `language` 字段，默认 `en`，并写入 `HarnessRunConfig.metadata`。
- Modify: `dynsteer/harness/runner.py`
  - 增加 config/case 级并行运行入口，确保每个 worker 使用独立 harness 和 evaluator。
- Modify: `dynsteer/evaluate/evaluator.py`
  - 将 `HarnessRunConfig.metadata["language"]` 合入 `TaskCase.metadata`，让 Judge 可通过 task_case 读取 prompt 语言。
- Modify: `main.py`
  - 增加 `--max-workers`，补充 `outputs: list[HarnessEvaluationOutput]` 类型标注。
- Modify: `dynsteer/log.py`
  - 增加日志配置锁和缓冲区锁，避免并发配置 logger 或写内存日志时竞争。
- Modify: `dynsteer/adapter/toolsandbox/harness.py`
  - 收紧 `_call_native(...)` 异常处理，避免把原生函数内部 TypeError 误判为参数签名不匹配。
- Modify: `data/toolsandbox/benchmark.json`
  - 增加 `"language": "en"` 示例配置。
- Modify: `docs/apis/llm.md`、`docs/apis/judges.md`、`docs/apis/harness.md`
  - 同步新的 LLM 重试、prompt 层级和并行执行说明。

## Prompt 设计

### 多语言 PromptTemplate

`dynsteer/judges/prompt.py` 中新增 `PromptTemplate` 封装类，所有 prompt 必须通过该类渲染。默认语言为英文 `en`，`zh` 等其他语言作为可选模板补充。

核心规则：

- `PromptTemplate` 只做简单 `{key}` 占位符替换，不执行表达式、不调用 `str.format(...)`，避免模板中 JSON 示例的 `{}` 被误解析。
- 模板中的 `{{` 和 `}}` 会被安全还原为字面量 `{` 和 `}`，便于在 prompt 中直接展示 JSON schema。
- `render(language: str = "en", **kwargs: object) -> str` 优先读取指定语言；语言不存在时回退到英文；英文也不存在时回退到第一个模板，并记录 warning。
- `supported_languages` 返回模板支持的语言列表，便于测试和后续 CLI/文档展示。
- 缺失的 `{key}` 保持原样，不隐式替换为空字符串，避免 prompt 悄悄丢失关键信息。

建议实现形态：

```python
def _safe_format(template: str, **kwargs: object) -> str:
    """只替换简单 {key} 占位符，保留 JSON 大括号和未知占位符。"""
    ...


class PromptTemplate:
    """多语言 prompt 模板。"""

    def __init__(self, **lang_templates: str) -> None:
        ...

    @property
    def supported_languages(self) -> list[str]:
        ...

    def render(self, language: str = "en", **kwargs: object) -> str:
        ...
```

### 语言配置链路

语言选择由 benchmark manifest 控制，即 `data/{benchmark}/benchmark.json` 增加 `language` 字段：

```json
{
    "benchmark": "toolsandbox",
    "source_root": "../ToolSandbox",
    "tool_backend": "DEFAULT",
    "language": "en"
}
```

实现链路：

1. `dynsteer/harness/config.py::load_harness_run_configs(...)` 读取 manifest 的 `language` 字段。
2. 若缺失则默认 `"en"`；若存在但不是非空字符串，抛出 `ValueError("benchmark.json language 必须是非空字符串")`。
3. 将标准化后的语言写入每个 `HarnessRunConfig.metadata["language"]`。
4. `DynSTEEREvaluator.evaluate(...)` 从 `config.metadata["language"]` 读取语言，并通过 `dataclasses.replace(task_case, metadata={...})` 合入 `TaskCase.metadata`。
5. `StandardJudge` 和 `ExpensiveJudge` 通过 `task_case.metadata.get("language", "en")` 调用 prompt 模板。

离线 `evaluate_trajectory(...)` 不强制增加新参数；如果输入 JSON 的 `task.metadata.language` 存在则使用该语言，否则默认英文。

### StandardJudge prompt

Standard 是单轮 LLM 复核，目标是“在已有 cheap 结构化证据基础上做一次完整但不过度发散的阶段判断”。

模板结构：

1. 角色约束：你是 DynSTEER standard judge，只能依据给定阶段区间、milestone 证据、任务说明和轨迹片段评估，不得引入外部事实。
2. 评估目标：判断该阶段是否完成对应 milestone，是否出现安全/工具/交互质量问题，并给出多维分数。
3. 证据规则：
   - evidence 必须引用 step index、actor、event_type 或 milestone evidence。
   - 不确定时降低 `judge_confidence`，不要虚构证据。
   - 如果关键状态、工具结果或用户回复缺失，必须体现在 `diagnosis` 和 `first_error_location_required`。
4. 维度 rubric：
   - progress：阶段目标完成度。
   - state_consistency：状态变化与 milestone 约束一致性。
   - tool_quality：工具调用参数、结果处理、错误恢复。
   - efficiency：是否存在明显冗余步骤或无效循环。
   - safety：是否触发 minefield、越权、危险操作。
   - interaction_quality：是否正确回应用户、澄清不足信息。
   - recovery：出错后是否识别、修复或降级处理。
5. 输出 schema：严格 JSON 对象，包含 `dimension_scores` 全量维度、`stage_score`、`status`、`judge_confidence`、`evidence`、`diagnosis`、`needs_expensive`、`first_error_location_required`。

`build_standard_prompt(...)` 应接收 `language: str`，通过 `PromptTemplate.render(language=language, ...)` 输出自然语言说明加结构化上下文，而不是只输出 JSON 序列化字符串。上下文中的任务、阶段、轨迹片段仍可用 JSON block 承载，便于模型定位证据。

### ExpensiveJudge prompt

Expensive 是多轮聚焦评估加最终裁决。每轮 prompt 必须承担不同职责，避免多轮重复同一 JSON dump。

Prompt 板式 1：`build_expensive_focus_prompt(..., language: str)`

- 用途：按维度组做深度复核。
- 维度组：
  - `progress,state_consistency`
  - `tool_quality,efficiency,recovery`
  - `safety,interaction_quality`
- 输出：同标准 schema，但 `evidence` 必须给出每个 focus 维度的关键证据；非 focus 维度仍给出保守评分。

Prompt 板式 2：`build_expensive_risk_prompt(..., language: str)`

- 用途：专门复核失败边界、minefield、安全、工具异常和首次错误位置。
- 输出：同标准 schema，并强制说明：
  - 是否存在 fatal 或近似 fatal 风险。
  - first_error_location_required 是否为 true。
  - 如果需要定位，`diagnosis` 中给出最早可疑 step index。

Prompt 板式 3：`build_expensive_adjudication_prompt(..., language: str)`

- 用途：汇总前面多轮结果并做最终裁决。
- 输入：阶段上下文、所有 pass 的 JSON 结果。
- 裁决规则：
  - 多轮分歧超过 0.2 时，优先采用证据更具体、能引用 step index 的判断。
  - safety 或 hard constraint 失败不得被平均分掩盖。
  - 最终 `stage_score` 不取简单平均，而是根据维度权重、硬约束和风险复核结果裁决。
  - 最终 JSON 必须包含 `metadata` 可用的裁决摘要字段，例如 `adjudication_reason`。

## LLM 基类重构设计

`BaseLLM.__init__(config: LLMConfig) -> None` 负责配置校验和保存，不接受外部 client。测试通过子类覆盖 `_create_client()` 或 monkeypatch provider client 构造完成，不再把错误 provider 类型注入生产类。

`BaseLLM.chat(messages: list[LLMMessage]) -> str` 流程固定为：

1. 校验 messages 非空，且每条消息 role/content 为非空字符串。
2. 每次尝试调用 `client = self._create_client()`，避免共享 client 引发并发状态问题，也便于 config 热更新场景重新读取配置对象。
3. 调用 `response = self._get_response_from_client(client, messages)`。
4. 调用 `text = self._response_text(response)`。
5. 如果 `_get_response_from_client` 抛异常、`_response_text` 抛 `LLMResponseError`、或 text 为空，则在未达到上限时记录中文 warning 并按指数退避重试。
6. 达到重试上限后抛出 `LLMResponseError`，错误信息包含 provider、model、attempts 和最后一次异常摘要。

`LLMConfig` 增加：

```python
max_retries: int = 3
retry_base_seconds: float = 1.0
retry_max_seconds: float = 8.0
```

`OpenaiLLM` 子类方法：

```python
def _create_client(self) -> OpenAI: ...
def _get_response_from_client(self, client: OpenAI, messages: list[LLMMessage]) -> object: ...
def _response_text(self, response: object) -> str: ...
```

`AnthropicLLM` 子类方法：

```python
def _create_client(self) -> Anthropic: ...
def _get_response_from_client(self, client: Anthropic, messages: list[LLMMessage]) -> object: ...
def _response_text(self, response: object) -> str: ...
```

Anthropic 继续保留 `max_tokens` 必填校验，但应放在 `_get_response_from_client` 或 `_validate_provider_config` 中，避免进入 SDK 后才失败。

## 并行执行设计

并行粒度为“一个 config + 一个 case”。单个 case 内的阶段评估必须保持顺序，因为权重更新、milestone 命中、fail-fast 和 settlement 都依赖前一阶段结果。

新增 runner 入口：

```python
def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
) -> list[HarnessEvaluationOutput]:
    ...
```

执行规则：

- `max_workers <= 1` 时保持当前串行行为。
- `max_workers > 1` 时，先为每个 config 展开 case_id，再提交线程池。
- 每个 worker 内部重新执行：
  - `harness = get_harness(config.benchmark)`
  - `evaluator = DynSTEEREvaluator.from_env()`
  - `_run_single_harness_case(config, harness, evaluator, case_id)`
- 返回结果按 config 顺序和 case 顺序排序，保证输出路径打印稳定。
- 任一 worker 抛错时，默认 fail-fast：记录 benchmark、run_id、case_id 后抛出 `HarnessCaseExecutionError`，由 main 返回 2。

需要补充的锁：

- `dynsteer/log.py`
  - `_LOGGER_LOCK: threading.RLock` 包住 `configure_logger(...)` 中 handler 替换。
  - `_LOG_BUFFER_LOCK: threading.RLock` 包住 `_LOG_BUFFER.append(...)`、`get_log_buffer()`、`clear_log_buffer()`。
- `harness/runner.py`
  - 不共享 harness session，不需要 session 锁。
  - 不共享 evaluator，不需要 evaluator 锁。
  - 不共享 SDK client，BaseLLM 每次 chat 构建 client，不需要 client 锁。

`main.py` 改动：

```python
from dynsteer.harness.runner import HarnessEvaluationOutput, run_harness_configs

outputs: list[HarnessEvaluationOutput] = run_harness_configs(
    configs=configs,
    max_workers=int(args.max_workers),
)
```

CLI 增加：

```python
parser.add_argument("--max-workers", type=int, default=1, help="benchmark config/case 并行 worker 数，默认 1")
```

## 异常处理完善

- `LLMJudge._call_json(...)`
  - 捕获 `LLMResponseError` 并转为 `LLMJudgeResponseError`，保留原始异常链。
  - 对模型返回 JSON 做严格字段校验，`needs_expensive` 和 `first_error_location_required` 若存在必须是 bool。
  - 增加对 fenced JSON 的有限解析：只接受完整 JSON 对象或单个 ```json fenced block，不接受混杂自然语言。
- `ExpensiveJudge.evaluate_stage(...)`
  - 单个 pass 失败时不静默降级。若风险复核或裁决轮失败，由 BaseLLM 重试后仍失败则整体抛出 `LLMJudgeResponseError`。
  - metadata 中记录每轮 prompt 类型、分数、状态、置信度和诊断摘要，不记录完整 prompt，避免日志过大。
- `ToolSandboxHarness._call_native(...)`
  - 优先用 `inspect.signature` 判断候选 kwargs 是否可绑定；只在签名不匹配时换下一组参数。
  - 原生函数内部抛出的 `TypeError` 不再被吞掉重试，直接带函数名和 case 上下文抛出。
- `harness/runner.py`
  - 新增 `HarnessCaseExecutionError(RuntimeError)`，封装 config.benchmark、run_id、case_id。
  - 写 JSON 文件前先构造内容，避免写入一半后才发现对象不可序列化。
- `main.py`
  - 对 `--max-workers < 1` 返回输入错误码 1。
  - benchmark 并行异常统一记录 `实验执行失败`，并返回 2。

## 任务拆分

### Task 1: Prompt 模板独立化

**Files:**
- Create: `dynsteer/judges/prompt.py`
- Modify: `dynsteer/judges/base.py`
- Modify: `dynsteer/judges/standard.py`
- Modify: `dynsteer/judges/expensive.py`
- Test: `tests/test_llm_judge.py`

- [ ] 写 failing tests，断言 `PromptTemplate.render(language="en")` 默认返回英文模板。
- [ ] 写 failing tests，断言 `PromptTemplate.render(language="zh")` 返回中文模板，未知语言回退英文。
- [ ] 写 failing tests，断言 `_safe_format(...)` 保留 JSON 大括号、只替换 `{key}`、未知 key 保持原样。
- [ ] 写 failing tests，断言 Standard prompt 包含评估目标、维度 rubric、输出 schema、证据约束。
- [ ] 写 failing tests，断言 Expensive 会生成 focus/risk/adjudication 三类 prompt，并把 pass 摘要写入 metadata。
- [ ] 创建 `prompt.py`，实现 `_safe_format`、`PromptTemplate` 和英文默认模板。
- [ ] 为 Standard/Expensive 模板补充至少英文 `en` 版本；中文 `zh` 版本可同步提供，但默认不依赖中文。
- [ ] 修改 StandardJudge 和 ExpensiveJudge 从 `task_case.metadata["language"]` 读取语言并调用模板函数。
- [ ] 运行 `uv run pytest tests/test_llm_judge.py -q`。

### Task 2: BaseLLM 模板方法与重试

**Files:**
- Modify: `dynsteer/llm/base.py`
- Modify: `dynsteer/llm/openai.py`
- Modify: `dynsteer/llm/anthropic.py`
- Modify: `dynsteer/llm/factory.py`
- Test: `tests/test_llm_provider.py`

- [ ] 写 failing tests，断言 `OpenaiLLM(config, client=...)` 和 `AnthropicLLM(config, client=...)` 不再是合法构造方式。
- [ ] 写 failing tests，使用测试子类模拟前两次异常、第三次成功，验证 BaseLLM 指数退避重试。
- [ ] 写 failing tests，模拟空响应达到重试上限后抛出 `LLMResponseError`。
- [ ] 在 BaseLLM 中实现 chat 通用流程和抽象方法。
- [ ] 修改 OpenaiLLM/AnthropicLLM 子类方法并标注 provider client 类型。
- [ ] 修改 factory 环境变量读取：`DYNSTEER_JUDGE_MAX_RETRIES`、`DYNSTEER_JUDGE_RETRY_BASE_SECONDS`、`DYNSTEER_JUDGE_RETRY_MAX_SECONDS`。
- [ ] 运行 `uv run pytest tests/test_llm_provider.py -q`。

### Task 3: benchmark 多配置并行

**Files:**
- Modify: `data/toolsandbox/benchmark.json`
- Modify: `dynsteer/harness/config.py`
- Modify: `dynsteer/evaluate/evaluator.py`
- Modify: `dynsteer/harness/runner.py`
- Modify: `main.py`
- Modify: `dynsteer/log.py`
- Test: `tests/test_harness_config.py`
- Test: `tests/test_evaluate_package.py`
- Test: `tests/test_harness_runner.py`
- Test: `tests/test_main_evaluator_entry.py`

- [ ] 写 failing tests，断言 `load_harness_run_configs(...)` 从 `benchmark.json` 读取 `language`，缺失时默认为 `en`。
- [ ] 写 failing tests，断言非法 `language` 会抛出 `ValueError`。
- [ ] 写 failing tests，断言 `DynSTEEREvaluator.evaluate(...)` 会把 `config.metadata["language"]` 合入 `task_case.metadata`。
- [ ] 写 failing tests，断言 runner 在 `max_workers=1` 时保持原顺序。
- [ ] 写 failing tests，断言 runner 在 `max_workers=2` 时为每个 case 创建独立 harness/evaluator。
- [ ] 写 failing tests，断言 `main.py` 中 `outputs` 有 `list[HarnessEvaluationOutput]` 类型标注并传递 `--max-workers`。
- [ ] 在 `data/toolsandbox/benchmark.json` 增加 `"language": "en"`。
- [ ] 修改 `load_harness_run_configs(...)` 读取并校验 manifest language。
- [ ] 修改 evaluator 将运行配置语言写入 task_case metadata。
- [ ] 给 log 配置和 buffer 加锁。
- [ ] 实现 `run_harness_configs(...)` 和 worker 内独立构造。
- [ ] 修改 main benchmark 分支。
- [ ] 运行 `uv run pytest tests/test_harness_config.py tests/test_evaluate_package.py tests/test_harness_runner.py tests/test_main_evaluator_entry.py -q`。

### Task 4: 异常边界补强

**Files:**
- Modify: `dynsteer/judges/base.py`
- Modify: `dynsteer/adapter/toolsandbox/harness.py`
- Modify: `dynsteer/harness/runner.py`
- Test: `tests/test_llm_judge.py`
- Test: `tests/test_harness_public_execution_api.py`

- [ ] 写 failing tests，断言非 bool 的 `needs_expensive` 会抛出 `LLMJudgeResponseError`。
- [ ] 写 failing tests，断言 ToolSandbox 原生函数内部 TypeError 不会被 `_call_native` 吞掉。
- [ ] 写 failing tests，断言 runner 将 case 异常包装为 `HarnessCaseExecutionError`。
- [ ] 实现 payload bool 校验、有限 fenced JSON 解析、ToolSandbox `_call_native` 签名绑定、runner 异常包装。
- [ ] 运行 `uv run pytest tests/test_llm_judge.py tests/test_harness_public_execution_api.py -q`。

### Task 5: 文档与全量验证

**Files:**
- Modify: `docs/apis/llm.md`
- Modify: `docs/apis/judges.md`
- Modify: `docs/apis/harness.md`

- [ ] 更新 LLM API 文档：BaseLLM 重试、子类抽象方法、环境变量。
- [ ] 更新 Judges 文档：PromptTemplate 多语言模板、默认英文、Standard/Expensive prompt 层级、输出 schema、metadata。
- [ ] 更新 Harness 文档：`benchmark.json.language`、`--max-workers`、并行粒度、异常包装。
- [ ] 运行 `uv run pytest -q`。
- [ ] 运行 `uv run python -m compileall -q dynsteer tests`。

## 验收标准

- StandardJudge 与 ExpensiveJudge 不再把 JSON 字典序列化字符串当作完整 prompt。
- 所有 Judge prompt 均通过 `PromptTemplate` 渲染，默认英文 `en`，并支持通过 `data/{benchmark}/benchmark.json` 的 `language` 字段切换。
- `PromptTemplate` 的占位符替换不执行表达式，能安全保留 JSON schema 中的大括号。
- ExpensiveJudge 至少包含维度聚焦、风险复核、最终裁决三类 prompt 板式。
- OpenaiLLM 和 AnthropicLLM 不接受外部 client，BaseLLM 统一实现 chat、重试和空响应处理。
- main benchmark 分支支持 `--max-workers`，`outputs` 明确标注 `list[HarnessEvaluationOutput]`。
- 并发运行不共享 harness session、evaluator 或 provider client。
- 日志配置和日志缓冲区有锁保护。
- 关键异常不被吞掉，错误信息包含 provider/model 或 benchmark/case 上下文。
- `uv run pytest -q` 和 `uv run python -m compileall -q dynsteer tests` 通过。
