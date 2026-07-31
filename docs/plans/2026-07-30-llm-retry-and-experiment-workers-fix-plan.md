# 2026-07-30 LLM 重试与统一实验 workers 并行修复方案

## 1. 核查结论

本次核查覆盖了 `dynsteer.llm`、ToolSandbox agent/user 调用链、普通 harness runner、统一实验 runner、启动脚本和 API 文档。结论如下：

1. `dynsteer.llm.BaseLLM.chat()` 当前确实有重试循环，但只包住了 `_get_response_from_client(...)` 和 `_response_text(...)`。`_create_client()` 与 `_normalize_infer_params(...)` 在重试循环外执行，因此这两个阶段的异常不会 warning、不会重试。
2. OpenAI-compatible Judge 的网络异常如果发生在 `client.chat.completions.create(...)` 内，理论上会进入 BaseLLM 的 warning + retry；但进度条期间终端日志会被静默，warning 可能只在日志文件和内存缓冲区中可见。
3. Anthropic Judge 在未配置 `max_tokens` 时，`_normalize_infer_params(...)` 会通过 `_max_tokens_from_params(...)` 访问 Anthropic model metadata。该访问发生在重试循环外，网络短断会直接抛出异常。
4. ToolSandbox 的 agent/user role 调用不走 `dynsteer.llm.BaseLLM`。`ToolSandboxHarness._advance_native_session()` 直接调用原生 role 的 `respond()`，role 内部使用 `OpenAI(...)` 或 `anthropic.Anthropic(...)` client。若昨晚中断发生在 agent/user 响应阶段，当前 DynSTEER LLM 重试机制完全不会生效。
5. 统一实验入口 `run_experiment()` 当前不接收 `workers`，也没有并行实现。`main.py` 虽解析并校验 `--workers`，但 `--exp` 分支没有把它传给 `run_experiment()`，因此 `scripts/start_experiment*.sh` 推导出的 workers 也会被忽略。
6. 普通单 benchmark 路径已经有有效 worker 上限逻辑：`load_harness_run_configs()` 将 `benchmark.json.max_workers` 写入 `metadata["benchmark_max_workers"]`，`run_harness_configs()` 再取命令行 workers 与该字段的较小值。但统一实验路径没有复用这条逻辑。
7. 文档中说 `benchmark.json.max_workers` 是可选字段，但 `load_harness_run_configs()` 当前要求该字段必须存在。用户本次需求也明确提到“可选配置的 max_workers”，建议同步修正为缺失时不施加 benchmark 级上限。

## 2. 当前代码证据

### 2.1 LLM 重试链路

- `dynsteer/llm/base.py:33` 定义 `BaseLLM.chat()`。
- `dynsteer/llm/base.py:36` 在循环外创建 provider client。
- `dynsteer/llm/base.py:37` 在循环外归一化推理参数。
- `dynsteer/llm/base.py:39` 才开始进入 `for attempt in ...` 重试循环。
- `dynsteer/llm/base.py:58` 仅在循环内异常时记录 `logger.warning("LLM 调用失败，准备重试", ...)`。
- `dynsteer/llm/anthropic.py:28` 的 `_normalize_infer_params(...)` 会调用 `_max_tokens_from_params(...)`。
- `dynsteer/llm/openai.py:15` 的 `client.chat.completions.create(...)` 在 BaseLLM 循环内，因此该阶段失败才会重试。

### 2.2 ToolSandbox agent/user 调用链

- `dynsteer/adapter/toolsandbox/utils/roles.py:209` 直接构造 OpenAI client。
- `dynsteer/adapter/toolsandbox/utils/roles.py:212` 直接构造 Anthropic client。
- `dynsteer/adapter/toolsandbox/harness.py:308` 直接调用 `respond()` 推进 agent/user。
- 以上路径没有调用 `BaseLLM.chat()`，也没有外层 retry。

### 2.3 进度条期间 warning 可见性

- `dynsteer/harness/scheduler.py:102` 和 `dynsteer/harness/scheduler.py:131` 在执行 case 时使用 `progress_logging_redirect(logger)`。
- `dynsteer/progress.py:155` 的 `progress_logging_redirect(...)` 会把终端 `StreamHandler` 临时调到 `CRITICAL + 1`，保留文件日志和内存日志。
- 因此如果异常发生在 BaseLLM 循环内，warning 可能不出现在终端，但应出现在 `logs/<date>.log` 或当前 run logger 文件中。

### 2.4 统一实验 workers 缺口

- `main.py:23` 解析 `--workers`。
- `main.py:82` 调用 `run_experiment(...)` 时没有传入 `workers`。
- `dynsteer/experiment/runner.py:24` 的 `run_experiment()` 签名没有 `workers`。
- `dynsteer/experiment/runner.py:42` 按 spec 串行循环。
- `dynsteer/experiment/runner.py:45` 对 task cases 使用普通 `tqdm` 串行循环。
- `dynsteer/harness/runner.py:81` 的 `_effective_max_workers(...)` 已实现单 benchmark 的上限计算，但实验路径未调用。
- `scripts/experiment_bootstrap.sh:79` 已读取 `benchmark.json.max_workers`，启动脚本也会传 `--workers`，但最终在 `main.py --exp` 分支被忽略。

## 3. 修复目标

1. BaseLLM 的 retry 必须覆盖 client 创建、provider 参数归一化、provider 请求、响应解析和空响应。
2. Provider 配置错误仍应快速失败，不应对明显配置错误做无意义重试。
3. ToolSandbox agent/user 的原生 `respond()` 调用必须有可配置 retry，默认行为与 Judge LLM 保持一致：`max_retries=3`、`retry_base_seconds=1.0`、`retry_max_seconds=8.0`。
4. 重试 warning 必须包含 provider/role、benchmark、case_id、attempt、max_retries、delay_seconds、错误摘要，便于从日志定位。
5. `run_experiment()` 接收 `workers`，并在固定 `benchmark + model_id + method + repeat_index + threshold_profile` 的 spec 下，对多个 `case_id` 串行或并行执行。
6. 每个 benchmark 的实际并发数取 `workers` 与 `benchmark.json.max_workers` 的较小值；`max_workers` 缺失时仅使用 `workers`。
7. ToolSandbox 仍必须可通过 `benchmark.json.max_workers=1` 保持串行，避免原生 `_global_execution_context` 跨线程污染。
8. 统一实验并行执行时，返回结果和 `index.json` 顺序稳定，不因 future 完成顺序变化。

## 4. LLM 重试修复方案

### 4.1 修改 `dynsteer/llm/base.py`

把 `client = self._create_client()` 和 `request_params = self._normalize_infer_params(...)` 移入 `for attempt` 循环，并放在同一个 `try` 块内。

建议结构：

```python
for attempt in range(1, self._config.max_retries + 1):
    started = time.perf_counter()
    try:
        client = self._create_client()
        request_params = self._normalize_infer_params(infer_params, client)
        response = self._get_response_from_client(client, messages, request_params)
        ...
    except LLMConfigurationError:
        raise
    except Exception as exc:
        ...
```

需要注意：

- `LLMConfigurationError` 继续直接抛出，不重试。
- 每次 attempt 使用新的局部 client，避免网络连接池在短断后处于坏状态。
- `_record_llm_failure(...)` 仍记录每次失败 attempt。
- warning 文本保留中文，`extra` 增加 `attempt`、`max_retries`、`delay_seconds`、`provider`、`model`、`error`。
- 终态异常仍抛 `LLMResponseError`，并保留 `from last_error`。

### 4.2 修改 `dynsteer/llm/anthropic.py`

Anthropic 当前有一个额外风险点：未配置 `max_tokens` 时会访问 `client.models.retrieve(...)` 推断上限。修复 BaseLLM 后，该访问会被 retry 包住，但还建议做两点收紧：

1. 在文档和示例中明确建议 Anthropic 显式配置 `max_tokens`，避免每次 chat 多一次 model metadata 请求。
2. `_max_tokens_from_params(...)` 抛出的配置错误继续使用 `LLMConfigurationError`，网络错误保持 provider SDK 原始异常，让 BaseLLM retry 捕获。

### 4.3 新增通用 retry 边界

建议将通用 retry 工具放到 `dynsteer/adapter/utils.py`，让 ToolSandbox 适配器层统一复用；如果后续有更多跨模块重试需求，再考虑上移到独立模块。

推荐接口：

```python
def retry_call(
    operation: Callable[[], T],
    *,
    max_retries: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
    logger: logging.Logger,
    warning_message: str,
    extra: Mapping[str, object],
    non_retry_errors: tuple[type[Exception], ...] = (),
) -> T:
    ...
```

实现原则：

- `max_retries` 表示总尝试次数，不是额外重试次数。
- 每次失败后未达上限才 warning + sleep。
- `non_retry_errors` 直接抛出。
- 不吞异常；最终失败时抛最后一次异常。
- BaseLLM 可以继续保留自己的 metrics 记录逻辑；如果引入该 helper 让回调过重，则 BaseLLM 可先只复用 `_retry_delay()` 思路，ToolSandbox 单独使用 helper。不要为了抽象牺牲可读性。

### 4.4 修复 ToolSandbox agent/user `respond()` 无 retry

修改 `dynsteer/adapter/toolsandbox/harness.py`：

1. 新增内部方法 `_respond_with_retry(session, role, recipient)`。
2. `_prepare_system_environment_messages(...)` 中的 execution environment `respond(ending_index=message_index)` 保持不重试，只在异常信息中补充 case/recipient 上下文。
3. `_advance_native_session(...)` 中的 `respond()` 改为 retry 包装。
4. warning extra 至少包含：
   - `benchmark="toolsandbox"`
   - `case_id`
   - `role`
   - `recipient`
   - `attempt`
   - `max_retries`
   - `delay_seconds`
   - `error`

建议读取 retry 配置：

- 优先读取当前 role 对应的 `agent_client` / `user_client` 中的 `max_retries`、`retry_base_seconds`、`retry_max_seconds`。
- 若未配置，使用默认值 `3 / 1.0 / 8.0`。
- execution environment 本地工具调用不应使用 LLM retry；它不是 LLM 网络调用。`_prepare_system_environment_messages(...)` 中如果 recipient 是 `EXECUTION_ENVIRONMENT`，只保留原异常处理，不做多次执行，避免工具初始化重复产生副作用。

### 4.5 扩展 client config 字段

修改 `dynsteer/utils.py` 的 client config 归一化：

- `_CLIENT_CONFIG_FIELDS` 增加：
  - `max_retries`
  - `retry_base_seconds`
  - `retry_max_seconds`
- 对 `max_retries` 使用正整数校验。
- 对 `retry_base_seconds`、`retry_max_seconds` 使用非负数字校验。
- 现有 `api_key`、`api_key_env`、`base_url`、`base_url_env`、`timeout_seconds` 语义不变。

这样 benchmark-only 的 `run_configs.json` 和 experiment 的 `models[*].harness_metadata.agent_client/user_client` 都可直接配置 agent/user retry。

## 5. 统一实验 workers 并行修复方案

### 5.1 修改 `main.py`

把 `--exp` 分支改为：

```python
results = run_experiment(
    Path(args.experiment_config),
    workers=int(args.workers),
    force_adapt=bool(args.force_adapt),
    force_eval=bool(args.force_eval),
    no_sum=bool(args.no_sum),
)
```

当前已有 `--workers 必须大于 0` 校验，可保留。

### 5.2 修改 `dynsteer/experiment/runner.py` 签名

把入口改成：

```python
def run_experiment(
    config_path: Path | str,
    workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
    no_sum: bool = False,
) -> list[ExperimentCaseResult]:
```

### 5.3 读取 benchmark 可选 max_workers

建议在 `dynsteer/harness/config.py` 新增共享函数：

```python
def load_benchmark_manifest_metadata(benchmark: str, data_root: Path) -> JsonObject:
    ...
```

该函数只负责读取 `benchmark.json` 中对 harness/experiment 都通用的 manifest 元数据：

- 校验 `benchmark` 字段与入参一致。
- 读取 `language`，缺失默认 `en`。
- 读取 `tool_backend`，若 benchmark 需要则保留原逻辑。
- 读取可选 `max_workers`：
  - 缺失：不写 `benchmark_max_workers`。
  - 存在且为正整数：写入 `benchmark_max_workers`。
  - 存在但为 bool、非整数或小于 1：抛 `ValueError("benchmark.json max_workers 必须是正整数")`。

然后改造 `load_harness_run_configs(...)` 复用该函数，消除“文档说可选但代码强制必填”的不一致。

### 5.4 修改 `dynsteer/experiment/config.py`

在展开 benchmark spec 时读取 manifest metadata，并合入 `ExperimentRunSpec.metadata`。

建议位置：

- 在 `for benchmark_spec in benchmarks:` 内，解析出 `benchmark` 和 `data_root` 后读取一次。
- 用局部 dict 缓存 `(benchmark, data_root.resolve()) -> manifest_metadata`，避免同一 benchmark 多次读文件。
- 合并优先级建议为：
  1. manifest metadata
  2. experiment 顶层 metadata
  3. benchmark metadata
  4. model metadata
  5. method metadata
  6. model/method harness_metadata

这样用户在 experiment JSON 中仍可通过 metadata 覆盖 manifest 默认值。

### 5.5 新增实验 case 调度函数

在 `dynsteer/experiment/runner.py` 内新增文件内 helper：

```python
def _run_spec_cases(
    spec: ExperimentRunSpec,
    task_cases: tuple[TaskCase, ...],
    *,
    max_workers: int,
    default_outputs: dict[tuple[str, str, int, str], tuple[HarnessEvaluationOutput, JsonObject]],
    force_eval: bool,
) -> list[ExperimentCaseResult]:
    ...
```

执行规则：

- `max_workers == 1` 时保持串行，便于调试和 ToolSandbox。
- `max_workers > 1` 时使用 `ThreadPoolExecutor(max_workers=max_workers)`。
- 结果按 `task_cases` 原始顺序返回。
- 每个 worker 内部继续调用已有 `run_default_case()`、`run_replay_case()`、`run_evaluate_case()`，这些函数已经会深拷贝 `TaskCase` 并创建独立 harness/evaluator。
- worker 抛错时包装为新的 `ExperimentCaseExecutionError`，错误信息包含 `benchmark`、`method`、`model_id`、`repeat_index`、`case_id`。

### 5.6 处理 replay 对 default 输出的依赖

当前 `_get_default_case_outputs(...)` 在 replay 方法中会按 case 懒加载 default，并把 default result 追加到全局 `results`。并行后不能让多个 worker 直接共享写 `results` 和 `default_outputs`。

建议改为两阶段：

1. 对 `DYNSTEER_REPLAY` 和 `DYNSTEER_REPLAY_STATIC`：
   - 先调用 `_ensure_default_outputs_for_spec(...)`。
   - 对缺失 default 的 case 先按当前 spec 的有效 worker 并行执行 default。
   - 按 case 顺序把 default result 追加到本 spec 的返回列表。
   - 写入 `default_outputs` 后，再并行执行 replay。
2. 对 `DEFAULT`：
   - 直接并行执行 default。
   - 同步填充 `default_outputs`。
3. 对 `DYNSTEER_EVALUATE`：
   - 直接并行执行 evaluate。

这样不会出现 dict/list 竞态，也能保留当前“即使实验 methods 不显式包含 default，replay 也会产出 default 参考结果”的行为。

### 5.7 有效 worker 计算直接复用

这里不应在 experiment runner 里再写一份 `_effective_experiment_workers(...)`。当前 `dynsteer/harness/runner.py` 已有 `_effective_max_workers(max_workers, config)`，语义正好是“命令行 workers 与 `metadata["benchmark_max_workers"]` 取较小值，最小为 1”。

建议把它改成公开 helper：

```python
def effective_max_workers(max_workers: int, config: HarnessRunConfig) -> int:
    ...
```

然后：

1. `run_harness_configs(...)` 内部继续调用 `effective_max_workers(max_workers, config)`。
2. `run_experiment(...)` 对每个 `ExperimentRunSpec` 调用 `build_harness_config(spec)` 后，直接调用 `effective_max_workers(workers, harness_config)`。
3. `dynsteer/harness/runner.py` 的 `__all__` 补充导出 `effective_max_workers`，避免 experiment runner 导入私有函数。

这样普通 benchmark 和统一实验会共用同一处 worker 上限逻辑，后续 `benchmark.json.max_workers` 的可选字段语义也只需要维护一次。

## 6. 需要修改的文件清单

### 必改代码

- `dynsteer/llm/base.py`
  - 把 client 创建和参数归一化纳入 retry。
  - 保持配置错误快速失败。
- `dynsteer/adapter/toolsandbox/harness.py`
  - 给 agent/user `respond()` 增加 retry 包装。
  - 不对 execution environment 本地工具初始化做 retry。
- `dynsteer/utils.py`
  - 扩展 `normalize_client_config(...)` 支持 role retry 字段。
- `dynsteer/harness/config.py`
  - 抽出 manifest metadata 读取函数。
  - `benchmark.json.max_workers` 改为可选正整数。
- `dynsteer/experiment/config.py`
  - 把 manifest metadata 合入 `ExperimentRunSpec.metadata`。
- `dynsteer/experiment/runner.py`
  - `run_experiment(..., workers=1, ...)`。
  - 新增 spec 内 case 并行调度。
  - 串行化 replay 的 default 依赖准备。
  - 增加 `ExperimentCaseExecutionError`。
- `dynsteer/harness/runner.py`
  - 将 `_effective_max_workers(...)` 改为公开 `effective_max_workers(...)`，供普通 benchmark 与统一实验复用。
- `main.py`
  - `--exp` 分支传入 `workers=int(args.workers)`。

### 必改文档

- `docs/apis/llm.md`
  - 更新 BaseLLM retry 覆盖范围。
  - 说明 client 每次 attempt 创建。
- `docs/apis/harness.md`
  - 更新 `benchmark.json.max_workers` 为可选。
  - 说明 ToolSandbox agent/user retry 配置字段。
- `docs/apis/experiment.md`
  - 更新 `run_experiment(config_path, workers=1, ...)` 的真实行为。
  - 说明统一实验按 spec 内 case 并行，并受 benchmark max_workers 限制。

### 建议新增测试

当前仓库根目录未发现 `tests/` 目录，建议新增：

- `tests/test_llm_retry.py`
  - 模拟 `_create_client()` 前两次失败第三次成功，断言 warning + retry 生效。
  - 模拟 `_normalize_infer_params()` 网络异常，断言会 retry。
  - 模拟 `_get_response_from_client()` 异常和空响应，断言达到上限后抛 `LLMResponseError`。
  - 模拟 `LLMConfigurationError`，断言不 retry。
- `tests/test_toolsandbox_role_retry.py`
  - 测试 retry helper 的 attempt 次数、delay 计算和最终异常。
  - 测试 agent/user client config 中 retry 字段能被归一化。
  - 用 fake role 测试 `respond()` 失败后重试成功。
- `tests/test_harness_config_manifest.py`
  - `benchmark.json.max_workers` 缺失时不写 `benchmark_max_workers`。
  - 非法 max_workers 抛错。
  - 合法 max_workers 写入 metadata。
- `tests/test_experiment_workers.py`
  - `main.py --exp --workers 4` 会把 workers 传给 `run_experiment()`。
  - `run_experiment(workers=4)` 在 `benchmark_max_workers=2` 时实际并发不超过 2。
  - spec 内多个 case 返回顺序稳定。
  - replay 方法会先准备 default outputs，再并行 replay。

## 7. 验收标准

1. Judge provider 请求、client 创建、Anthropic max_tokens metadata 推断失败都会进入统一 retry。
2. ToolSandbox agent/user 因临时网络异常失败时，会先 warning、等待、重试，达到上限后再中断。
3. 终端进度条期间即使 warning 不显示，日志文件中仍能看到结构化重试日志。
4. `python main.py --exp ... --workers N` 的 `N` 真实影响统一实验执行。
5. `benchmark.json.max_workers=1` 时，统一实验和普通 benchmark 都串行执行该 benchmark 的 cases。
6. `benchmark.json` 缺失 `max_workers` 时不报错，实际 worker 使用 CLI / `run_experiment(workers=...)` 的值。
7. replay 并行不会出现 default output dict/list 写入竞态。
8. `index.json`、`scores.json`、`metrics.json` 内容稳定，不因并行完成顺序改变。
9. 新增测试可通过 `uv run pytest -q`。
10. 代码语法检查可通过 `uv run python -m compileall -q dynsteer tests`。

## 8. 实施顺序

1. 先修 `BaseLLM.chat()` retry 覆盖范围，并补 `test_llm_retry.py`。
2. 再修 ToolSandbox agent/user `respond()` retry 和 client config 字段归一化。
3. 再把 `benchmark.json.max_workers` 统一成可选字段，并补 manifest 测试。
4. 最后改 `run_experiment(workers=...)` 和 spec 内 case 并行。
5. 更新 API 文档并执行全量测试。

## 附录A. 项目中没有把握实现的模块部分

1. ToolSandbox 原生 role 的 `respond()` 是否对所有失败都幂等，没有完全把握。网络请求失败通常没有产生有效回复，重试是合理的；但若 SDK 在服务端已完成响应、本地写 context 前断开，重复 `respond()` 可能产生重复消息。实施时需要用小范围 fake role 和至少一个真实 ToolSandbox case 验证。
2. ToolSandbox 的 `_global_execution_context` 线程安全已经通过 `max_workers=1` 避免，但其他 benchmark 是否也有类似全局状态，需要依赖各 benchmark 的 `benchmark.json.max_workers` 正确配置。方案只保证尊重上限，不能自动证明第三方 benchmark 线程安全。
3. Replay 与 default 输出缓存的并行重构需要谨慎核对现有输出目录规则。当前代码中 default、replay、evaluate 的写出函数各自有缓存复用逻辑，实施时必须确认并行准备 default 不会改变已有缓存命中语义。
4. 进度条与 warning 的终端展示是否要改变，没有完全把握。当前设计是保留文件日志、静默终端以避免 tqdm 被打乱；如果用户强烈希望终端看到 retry warning，需要另行设计 tqdm-safe warning 输出，而不是在本次修复中顺手改变日志策略。
