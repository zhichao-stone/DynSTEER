# DynSTEER 第一性原理代码精简执行方案

生成日期：2026-07-30  
方案状态：仅制定执行方案，不直接修改主功能代码  
约束来源：`docs/constraints/code.md`  
统计范围：仅统计 `dynsteer/**/*.py`，排除测试文件、文档文件、空白行、仅注释行、docstring 行、日志输出行。

## 0. 当前审计结论

当前 `dynsteer` 主包有效行数为 **10669 行**。本次核查没有把“多行格式压缩成一行”当作精简；所有建议都只针对真实冗余：死功能入口、未被主链路调用的类/函数、重复配置解析、重复 Actor 归一化、重复 candidate payload 组装、重复 trajectory/snapshot 输出维护、调用链下游重复参数校验。

最明确的问题有三类：

1. **死代码和半成品入口**：`build_milestone_candidate_detail()`、`LLMResponse/LLMTokenLogprob`、`record_runtime_metrics()`、`all_rubrics()`、`DynSTEEREvaluator.evaluate_minefields()`、`dynsteer_guidance`、`swebench_pro` scaffold。
2. **重复原语**：`evaluate/evaluator.py::_int_from_mapping` 与配置解析层重复；Actor/role 归一化散落在 loader、route、ToolSandbox roles、ToolSandbox trajectory；`ToolCall.from_dict()`、`ToolResult.from_dict()`、`MinefieldPenalty.from_dict()` 是仅被单点调用的一次性中转。
3. **重复流程**：live evaluate、replay、default writer 都各自维护 snapshot 合并、runtime initial state 摘要、trajectory output 摘要；matching ready/blocked candidate 也分别手写同构 payload。
4. **重复校验**：`harness/runner.py`、`harness/scheduler.py`、`adapter/toolsandbox/harness.py`、`evaluate/matching/milestone.py`、`evaluate/runtime.py` 的私有下游函数仍在重复校验上游刚构造或刚规范化过的对象。

本方案落地后，按当前统计口径预计可减少 **约 400 行有效代码**，保守区间为 **360-440 行**。有效行数预计从 **10669 行降至约 10269 行**。其中可直接确认删除的死代码和 stub 约 **110-130 行**，其余来自重复流程、重复工具函数、重复校验收口。

## 1. 精简原则

1. **保留真实入口，删除假入口**  
   能跑通主流程的入口保留；只注册、只枚举、调用后立刻 `NotImplementedError` 的入口删除，等真实实现到位后再新增。

2. **配置解析只留在配置边界**  
   `DynSTEEREvaluator` 负责评估编排，不应维护 `_int_from_mapping` 这种配置读取工具。环境变量、JSON profile、run config 的数值读取应在 `harness/config.py`、`llm/factory.py` 或共享工具中完成。

3. **同一种外部枚举归一只实现一次**  
   Actor、ToolSandbox role name、`EXECUTION_ENVIRONMENT -> environment` 这类别名映射只保留一个权威实现。

4. **单点便利构造器直接内联**  
   如果一个 `from_dict()` 只被一个调用点使用，且只是把 dict 字段映射到 dataclass 构造参数，应删除该类方法，把解析逻辑放到唯一调用点。

5. **不删除项目约束明确要求的支撑能力**  
   `get_log_buffer()` / `clear_log_buffer()` 当前静态未被仓库内部调用，但 `docs/constraints/code.md` 要求日志支持缓冲区以供前端轮询，因此本方案不删除它们。

## 2. 修改总览

| 优先级 | 修改区域 | 核心动作 | 预计有效行缩减 |
| --- | --- | --- | ---: |
| P0 | 死代码 | 删除无主代码调用的函数/类和半成品入口 | 110-130 |
| P0 | `evaluate/evaluator.py` 配置解析 | 删除 `_int_from_mapping`，改用配置边界函数 | 4-8 |
| P0 | 标量解析工具 | 合并 int/float/bool/env/config 小工具，删除局部重复 helper | 25-40 |
| P0 | Actor/role 归一化 | 收到 `dynsteer.utils.normalize_actor()` | 25-35 |
| P0 | 一次性 `from_dict()` | 删除 3 个 dataclass 便利构造器并内联 | 8-12 |
| P1 | snapshot / trajectory 输出 | 收口重复的 snapshot merge、trajectory output summary、runtime state summary | 30-45 |
| P1 | matching candidate 组装 | 合并 ready/blocked candidate detail 构造 | 25-35 |
| P1 | 调用链重复校验 | 入口/配置边界校验一次，删除私有下游重复判空和范围检查 | 35-55 |
| P1 | finish stage result | 抽出 finish result 公共构造，减少确定性/whole trajectory 分支重复 | 45-65 |
| P2 | outputs / experiment index | 减少 summary/raw_summary/index payload 重复拼装 | 35-50 |

## 3. P0：删除明确死代码和半成品入口

### 3.1 删除 `build_milestone_candidate_detail()`

现状：

- 文件：`dynsteer/evaluate/diagnostics.py`
- 函数：`build_milestone_candidate_detail()`，约 14 有效行
- 静态复核：`dynsteer`、`display`、`main.py`、`tests` 均无调用。
- 同等 payload 已在 `evaluate/matching/milestone.py` 的 ready/blocked 分析里手写构造。

修改：

```python
# 删除整个函数：
def build_milestone_candidate_detail(...):
    ...
```

后续 candidate detail 只由 matching 层生成，不再在 diagnostics 层保留未使用 builder。

测试：

- 不需要删除测试；当前没有测试引用该函数。
- 运行 `uv run pytest tests -q` 验证无引用残留。

### 3.2 删除 `LLMTokenLogprob` 和 `LLMResponse`

现状：

- 文件：`dynsteer/model.py`
- 类：`LLMTokenLogprob`、`LLMResponse`，共约 6 有效行。
- 当前 `BaseLLM.chat()` 返回 `str`，主链路没有任何代码创建或读取 `LLMResponse`。
- `dynsteer/llm/__init__.py` 没有导出这两个类，`docs/apis/llm.md` 也没有把它们列为当前 API。

修改后 `model.py` 中 LLM 相关 schema 保留为：

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
```

测试：

- `uv run python -m compileall dynsteer`
- LLM 相关测试如果后续新增，只断言 `BaseLLM.chat()` 返回 `str`。

### 3.3 删除 `record_runtime_metrics()`

现状：

- 文件：`dynsteer/metrics.py`
- 函数：`record_runtime_metrics()`，约 7 有效行。
- 主链路已经直接使用 `activate_runtime_metrics_recorder()` / `reset_runtime_metrics_recorder()`，没有调用 context manager。
- `docs/apis/llm.md` 仍提到该 context manager，属于文档滞后。

修改：

1. 删除 `record_runtime_metrics()`。
2. 删除 `contextlib.contextmanager` 和 `typing.Iterator` 导入。
3. 修改 `docs/apis/llm.md`，把：

```text
当调用发生在 dynsteer.metrics.record_runtime_metrics() 上下文内时...
```

改为：

```text
当评估器或 harness writer 通过 activate_runtime_metrics_recorder(...) 激活 recorder 后，
BaseLLM.chat(...) 会记录每次 provider attempt 的耗时、成功/失败状态和 usage tokens。
```

保留：

```python
def activate_runtime_metrics_recorder(recorder: RuntimeMetricsRecorder) -> Token[RuntimeMetricsRecorder | None]: ...
def reset_runtime_metrics_recorder(token: Token[RuntimeMetricsRecorder | None]) -> None: ...
def current_runtime_metrics_recorder() -> RuntimeMetricsRecorder | None: ...
def build_runtime_metrics(...) -> JsonObject: ...
```

### 3.4 删除 `all_rubrics()`

现状：

- 文件：`dynsteer/prompt/rubrics.py`
- 函数：`all_rubrics()`，约 2 有效行。
- 主代码只使用 `rubrics_for_dimensions(...)`；`all_rubrics()` 没有导出，也没有测试引用。

修改：

```python
# 删除
def all_rubrics() -> JsonObject:
    return rubrics_for_dimensions(list(Dimension))
```

保留唯一真实入口：

```python
def rubrics_for_dimensions(dimensions: list[Dimension] | tuple[Dimension, ...]) -> JsonObject:
    if dimensions is None:
        raise ValueError("dimensions 不能为空")
    return {dimension.value: _RUBRICS[dimension] for dimension in dimensions}
```

### 3.5 删除 `DynSTEEREvaluator.evaluate_minefields()`

现状：

- 文件：`dynsteer/evaluate/evaluator.py`
- 方法：`DynSTEEREvaluator.evaluate_minefields()`，约 25 有效行。
- 仓库主链路无调用；运行时 minefield 检查由 `evaluate_step_minefields()` 调用 `evaluate_minefields_at_boundary()` 完成。
- 该方法重复扫描 trajectory 并自行 dedupe，和运行期 state 累计逻辑重复。

修改：

1. 删除 `DynSTEEREvaluator.evaluate_minefields()`。
2. 删除 `evaluator.py` 中仅为该方法服务的导入：

```python
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
```

注意：如果 `candidate_boundary_for_current_step` 仍被其他 evaluator 代码使用则保留；当前 evaluator 只在该方法中直接使用它。

保留的真实 minefield 入口：

```python
def evaluate_step_minefields(...) -> RuntimeEvaluationDecision | None:
    ...

def evaluate_minefields_at_boundary(...) -> tuple[list[JsonObject], float, bool]:
    ...
```

文档：

- `docs/apis/evaluate.md` 当前未宣传 `evaluate_minefields()`，无需修改。
- 历史 `docs/plans/report/*` 提到它，按约束不删除历史计划/报告。

### 3.6 删除 `dynsteer_guidance` 半成品方法

现状：

- `ExperimentMethod.DYNSTEER_GUIDANCE` 是可配置方法。
- `run_guidance_case()` 只抛 `NotImplementedError`。
- `EvaluationStrategyConfig.guidance_enabled` 除校验和 metadata 输出外没有实际行为。

第一性原理判断：不能把“配置可选项 + NotImplementedError”当成功能入口。删除后，等 guidance 注入真实实现时再以完整行为新增。

修改文件：

1. `dynsteer/experiment/model.py`

修改前：

```python
class ExperimentMethod(str, Enum):
    DEFAULT = "default"
    DYNSTEER_EVALUATE = "dynsteer_evaluate"
    DYNSTEER_REPLAY = "dynsteer_replay"
    DYNSTEER_REPLAY_STATIC = "dynsteer_replay_static"
    DYNSTEER_GUIDANCE = "dynsteer_guidance"
```

修改后：

```python
class ExperimentMethod(str, Enum):
    DEFAULT = "default"
    DYNSTEER_EVALUATE = "dynsteer_evaluate"
    DYNSTEER_REPLAY = "dynsteer_replay"
    DYNSTEER_REPLAY_STATIC = "dynsteer_replay_static"
```

并删除：

```python
guidance_enabled: bool = False
...
if not isinstance(self.guidance_enabled, bool):
    raise TypeError("guidance_enabled 必须是 bool")
```

2. `dynsteer/harness/config.py`

从 `evaluation_strategy_from_mapping()` 删除：

```python
guidance_enabled=_bool_from_mapping(data, "guidance_enabled", False),
```

3. `dynsteer/experiment/config.py`

删除：

```python
if spec.method == ExperimentMethod.DYNSTEER_GUIDANCE and (not spec.strategy.guidance_enabled):
    raise ValueError(...)

if method == ExperimentMethod.DYNSTEER_GUIDANCE:
    return replace(strategy, guidance_enabled=True)
```

4. `dynsteer/experiment/runner.py`

删除：

```python
elif spec.method == ExperimentMethod.DYNSTEER_GUIDANCE:
    output = run_guidance_case(spec, task_case)
    results.append(_case_result_from_output(spec, task_case, output))

def run_guidance_case(...):
    raise NotImplementedError(...)
```

5. `docs/apis/experiment.md`

把支持方法列表从：

```text
default、dynsteer_evaluate、dynsteer_replay、dynsteer_replay_static、dynsteer_guidance
```

改为：

```text
default、dynsteer_evaluate、dynsteer_replay、dynsteer_replay_static
```

测试：

- 删除或修改任何期望 `dynsteer_guidance` 可配置的测试。当前仓库内没有这类测试。

### 3.7 删除 `swebench_pro` scaffold

现状：

- `dynsteer/adapter/registry.py` 注册了 `swebench_pro`。
- `dynsteer/adapter/swebench/adapter.py` 和 `harness.py` 所有主方法都抛 `NotImplementedError`。
- 这不是未使用 helper，而是公开了一个必然失败的 benchmark。

修改：

1. 删除文件：

```text
dynsteer/adapter/swebench/__init__.py
dynsteer/adapter/swebench/adapter.py
dynsteer/adapter/swebench/harness.py
```

2. 修改 `dynsteer/adapter/registry.py`

修改前：

```python
_ADAPTERS = {
    "toolsandbox": "dynsteer.adapter.toolsandbox.adapter:ToolSandboxAdapter",
    "swebench_pro": "dynsteer.adapter.swebench.adapter:SwebenchProAdapter",
}
_HARNESSES = {
    "toolsandbox": "dynsteer.adapter.toolsandbox.harness:ToolSandboxHarness",
    "swebench_pro": "dynsteer.adapter.swebench.harness:SwebenchProHarness",
}
```

修改后：

```python
_ADAPTERS = {
    "toolsandbox": "dynsteer.adapter.toolsandbox.adapter:ToolSandboxAdapter",
}
_HARNESSES = {
    "toolsandbox": "dynsteer.adapter.toolsandbox.harness:ToolSandboxHarness",
}
```

3. 修改 `docs/apis/swebench.md`

不删除该文档，改为明确说明：

```text
当前代码包不再暴露 swebench_pro registry scaffold。SWE-bench 接入需要在真实 dataset schema、
runner、adapter 和 harness 均可运行后重新新增。
```

测试：

- 当前无 swebench 测试。
- 运行 `python -c "from dynsteer.adapter.registry import get_adapter; get_adapter('toolsandbox')"` 验证 registry 正常。
- 可额外断言 `get_adapter('swebench_pro')` 抛 `KeyError`，但不必为删除的假入口新增持久测试。

## 4. P0：配置数值解析收口

补充核查后，`_int_from_mapping()` 不是孤例。当前同类重复工具函数如下：

| 文件 | 函数 | 重复点 |
| --- | --- | --- |
| `evaluate/evaluator.py` | `_int_from_mapping()` | 从 mapping 读取 int |
| `harness/config.py` | `_optional_positive_int()`、`load_ready_frontier_patience_from_env()` | 从 env string 读取正整数 |
| `llm/factory.py` | `_read_positive_int()`、`_config_positive_int()` | env/config 两套正整数读取 |
| `llm/factory.py` | `_read_non_negative_float()`、`_config_non_negative_float()` | env/config 两套非负浮点读取 |
| `harness/config.py` | `_bool_from_mapping()` | mapping bool 读取 |
| `adapter/loader.py` | `_optional_int()` | JSON dict 可选 int 读取 |
| `evaluate/telemetry.py` | `_optional_float()` | 与 `utils.as_number()` 同义 |
| `evaluate/diagnostics.py` | `_compact_json()` | 与 `utils.compact_json_text()` 高度重叠 |

第一性原理结论：

- env string、JSON mapping、profile dict 都只是“外部输入边界”；标量解析应该统一在 `utils.py` 或 `harness/config.py` 边界完成。
- 评估器、telemetry、diagnostics 不应保留自己的配置读取或 JSON 压缩小工具。
- LLM 配置需要保留 `LLMConfigurationError` 语义，但不需要保留四个同构 helper；共享 parser 可通过 `error_type` 抛出原异常类型。

建议先在 `dynsteer/utils.py` 增加两个通用标量解析原语：

```python
def parse_int_value(
    value: object,
    label: str,
    *,
    default: int | None = None,
    min_value: int | None = None,
    error_type: type[Exception] = ValueError,
) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = int(value.strip()) if isinstance(value, str) else value
    except ValueError as exc:
        raise error_type(f"{label} 必须是整数") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, int):
        raise error_type(f"{label} 必须是整数")
    if min_value is not None and parsed < min_value:
        raise error_type(f"{label} 必须大于等于 {min_value}")
    return parsed


def parse_float_value(
    value: object,
    label: str,
    *,
    default: float | None = None,
    min_value: float | None = None,
    error_type: type[Exception] = ValueError,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = float(value.strip()) if isinstance(value, str) else value
    except ValueError as exc:
        raise error_type(f"{label} 必须是数字") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise error_type(f"{label} 必须是数字")
    if min_value is not None and parsed < min_value:
        raise error_type(f"{label} 必须大于等于 {min_value}")
    return parsed
```

注意：这不是新增抽象层，而是把当前分散在 5 个文件里的同构解析分支收成两个原语。

### 4.1 删除 `evaluate/evaluator.py::_int_from_mapping`

现状：

- `DynSTEEREvaluator.from_config()` 里解析 `standard_passes` / `expensive_passes`。
- `harness/config.py` 已经负责 judge env config、threshold config、strategy config。
- 用户指出的 `_int_from_mapping` 确实放错层：评估器不应维护配置解析工具。

修改目标：

1. 在配置边界新增 judge pass 读取函数。
2. evaluator 只消费已经读出的整型 pass。
3. 删除 `_int_from_mapping()`。

建议在 `dynsteer/harness/config.py` 增加配置边界函数：

```python
def judge_pass_count(
    data: Mapping[str, Any],
    key: str,
    env: Mapping[str, str],
    env_key: str,
    default: int = 3,
) -> int:
    raw_value = data.get(key)
    if raw_value is None:
        raw_value = env.get(env_key)
    value = parse_int_value(raw_value, key, default=default, min_value=1)
    return value if value is not None else default
```

`DynSTEEREvaluator.from_config()` 修改为：

```python
standard_passes = judge_pass_count(
    judge_mapping,
    "standard_passes",
    source,
    "DYNSTEER_STANDARD_JUDGE_PASSES",
)
expensive_passes = judge_pass_count(
    judge_mapping,
    "expensive_passes",
    source,
    "DYNSTEER_EXPENSIVE_JUDGE_PASSES",
)
```

同时删除：

```python
def _int_from_mapping(...):
    ...
```

### 4.2 合并 LLM factory 数值 helper

现状：

`dynsteer/llm/factory.py` 有四个同构 helper：

```python
_read_positive_int(...)
_config_positive_int(...)
_read_non_negative_float(...)
_config_non_negative_float(...)
```

它们的差异只是输入来自 env string 还是 config object。

修改目标：

删除四个 local helper，直接复用 `utils.parse_int_value()` / `utils.parse_float_value()`，并通过 `error_type=LLMConfigurationError` 保持原异常类型。

调用改为：

```python
max_tokens=parse_int_value(
    source.get("DYNSTEER_JUDGE_MAX_TOKENS"),
    "DYNSTEER_JUDGE_MAX_TOKENS",
    min_value=1,
    error_type=LLMConfigurationError,
)
max_retries=parse_int_value(
    source.get("DYNSTEER_JUDGE_MAX_RETRIES"),
    "DYNSTEER_JUDGE_MAX_RETRIES",
    default=3,
    min_value=1,
    error_type=LLMConfigurationError,
)
retry_base_seconds=parse_float_value(
    source.get("DYNSTEER_JUDGE_RETRY_BASE_SECONDS"),
    "DYNSTEER_JUDGE_RETRY_BASE_SECONDS",
    default=1.0,
    min_value=0.0,
    error_type=LLMConfigurationError,
)
retry_max_seconds=parse_float_value(
    source.get("DYNSTEER_JUDGE_RETRY_MAX_SECONDS"),
    "DYNSTEER_JUDGE_RETRY_MAX_SECONDS",
    default=8.0,
    min_value=0.0,
    error_type=LLMConfigurationError,
)
```

以及：

```python
max_tokens=parse_int_value(config.get("max_tokens"), "max_tokens", min_value=1, error_type=LLMConfigurationError)
max_retries=parse_int_value(config.get("max_retries"), "max_retries", default=3, min_value=1, error_type=LLMConfigurationError)
retry_base_seconds=parse_float_value(config.get("retry_base_seconds"), "retry_base_seconds", default=1.0, min_value=0.0, error_type=LLMConfigurationError)
retry_max_seconds=parse_float_value(config.get("retry_max_seconds"), "retry_max_seconds", default=8.0, min_value=0.0, error_type=LLMConfigurationError)
```

### 4.3 合并零散非配置小工具

现状：

- `evaluate/telemetry.py::_optional_float()` 与 `utils.as_number()` 等价。
- `evaluate/diagnostics.py::_compact_json()` 只是在 `json_safe()` 后调用 `json.dumps()` 和 `compact_text()`，与 `utils.compact_json_text()` 重叠。
- `adapter/loader.py::_optional_int()` 只为 `StepCost.tokens/latency_ms` 服务，行为与新增的 `parse_int_value()` 可选模式一致。

修改：

1. `evaluate/telemetry.py`

```python
from dynsteer.utils import as_number, compact_text, first_text, optional_str

...
return (as_number(score.get("score")), optional_str(score.get("status")))
```

删除 `_optional_float()`。

2. `evaluate/diagnostics.py`

```python
from dynsteer.utils import as_number, compact_json_text, compact_text, json_safe

...
actual_excerpt = compact_json_text(score.get("actual"), 420) if score.get("actual") is not None else None
```

删除 `_compact_json()`；如果 `diagnostics.py` 不再直接使用 `json`，同步删除 `import json`。

3. `adapter/loader.py`

```python
cost = StepCost(
    tokens=parse_int_value(cost_data.get("tokens"), "cost.tokens"),
    latency_ms=parse_int_value(cost_data.get("latency_ms"), "cost.latency_ms"),
)
```

删除 `_optional_int()`。

## 5. P0：Actor / role 归一化收口

现状中存在五套近似实现：

| 文件 | 函数 | 问题 |
| --- | --- | --- |
| `adapter/loader.py` | `_load_actor()` | 自维护 alias map |
| `adapter/route.py` | `_normalize_actor()` | 自维护小写 alias map |
| `adapter/toolsandbox/utils/roles.py` | `actor_value_from_role_name()` | 自维护大写 alias map |
| `adapter/toolsandbox/utils/trajectory.py` | `_actor_from_step()` | 再包一层 ToolSandbox role -> Actor |
| `evaluate/semantic.py` | `_matching_message()` | 手写 `EXECUTION_ENVIRONMENT -> ENVIRONMENT` 字符串归一 |

修改目标：保留一个权威实现。

建议在 `dynsteer/utils.py` 增加：

```python
_ACTOR_ALIASES: dict[str, Actor] = {
    "system": Actor.SYSTEM,
    "user": Actor.USER,
    "agent": Actor.AGENT,
    "environment": Actor.ENVIRONMENT,
    "execution_environment": Actor.ENVIRONMENT,
    "evaluator": Actor.EVALUATOR,
}


def normalize_actor(value: object, field_name: str = "actor", required: bool = False) -> Actor | None:
    if value is None:
        if required:
            raise ValueError(f"缺少枚举字段: {field_name}")
        return None
    key = str(value).strip().lower()
    if not key:
        if required:
            raise ValueError(f"{field_name} 不能为空")
        return None
    actor = _ACTOR_ALIASES.get(key)
    if actor is not None:
        return actor
    try:
        return Actor(key)
    except ValueError as exc:
        raise ValueError(f"{field_name} 角色非法: {value}") from exc
```

各文件改法：

1. `adapter/loader.py`

```python
actor=normalize_actor(data.get("actor"), "actor", required=True) or Actor.EVALUATOR
recipient=normalize_actor(data.get("recipient"), "recipient", required=False)
```

删除 `_load_actor()`。

2. `adapter/route.py`

```python
def _actor_from_aliases(data: Mapping[str, object], aliases: tuple[str, ...]) -> Actor | None:
    for alias in aliases:
        actor = normalize_actor(data.get(alias), alias, required=False)
        if actor is not None:
            return actor
    return None
```

删除 `_normalize_actor()`。

3. `adapter/toolsandbox/utils/roles.py`

```python
def role_to_recipient(recipient: object) -> str | None:
    actor = normalize_actor(enum_name(recipient), "recipient", required=False)
    return actor.value if actor is not None else None
```

`role_to_actor()` 同理使用 `normalize_actor()`，删除 `actor_value_from_role_name()`。

4. `adapter/toolsandbox/utils/trajectory.py`

```python
actor=normalize_actor(step_dict.pop("actor"), "actor", required=True)
recipient=normalize_actor(step_dict.pop("recipient", None), "recipient", required=False)
```

删除 `_actor_from_step()`。

5. `evaluate/semantic.py`

新增一个局部小函数只负责把消息 route 转成可比较的 Actor 名称，不再手写字符串替换：

```python
def _route_actor_name(value: object) -> str:
    actor = normalize_actor(value, required=False)
    return actor.name if actor is not None else str(value or "").strip().upper()
```

`_matching_message()` 和 `_actual_contains_expected_message_route()` 中的 sender/recipient 比较都改为 `_route_actor_name(...)`，删除散落的：

```python
if expected_sender == "EXECUTION_ENVIRONMENT":
    expected_sender = "ENVIRONMENT"
```

收益：

- 删除三份 alias map 和一个中转函数。
- 后续新增 actor 别名只需改一处。

## 6. P0：删除一次性 dataclass `from_dict()`

现状：

| 类 | 文件 | 调用点 |
| --- | --- | --- |
| `ToolCall.from_dict()` | `model.py` | `toolsandbox/utils/trajectory.py` 一处 |
| `ToolResult.from_dict()` | `model.py` | `toolsandbox/utils/trajectory.py` 一处 |
| `MinefieldPenalty.from_dict()` | `model.py` | `adapter/loader.py` 一处 |

这些方法扩大了 schema 类 API，却没有复用价值。

修改：

1. 删除 `model.py` 中三个 `from_dict()`。

2. `adapter/toolsandbox/utils/trajectory.py` 内联：

```python
tool_call_payload = tool_call if isinstance(tool_call, dict) else None
tool_result_payload = tool_result if isinstance(tool_result, dict) else None

return TrajectoryStep(
    ...
    tool_call=(
        ToolCall(
            name=str(tool_call_payload["name"]),
            arguments=dict(tool_call_payload.get("arguments", {})),
        )
        if tool_call_payload is not None
        else None
    ),
    tool_result=(
        ToolResult(
            success=bool(tool_result_payload.get("success")),
            content=tool_result_payload.get("content"),
            exception=tool_result_payload.get("exception"),
        )
        if tool_result_payload is not None
        else None
    ),
    ...
)
```

3. `adapter/loader.py` 内联：

```python
penalty=MinefieldPenalty(
    mode=str(penalty_data.get("mode", "fixed")),
    value=float(penalty_data.get("value", 0.0)),
)
```

## 7. P1：snapshot / trajectory 输出流程收口

### 7.1 合并 snapshot 去重追加

现状重复：

- `DynSTEEREvaluator.evaluate()` 内维护 `snapshot_by_id`。
- `write_default_case_outputs()` 内维护同样的 `snapshot_by_id`。
- `evaluate_replay()` 另有 `_append_replay_snapshots()`。

修改目标：把“按 snapshot_id 去重并按 `(after_step_index, snapshot_id)` 排序”变成一个小原语。

建议新增到 `dynsteer/evaluate/runtime.py` 或 `dynsteer/harness/outputs.py` 中更通用的位置。因为 evaluator 和 outputs 都使用，建议放到 `dynsteer/model.py` 的 `Trajectory` 方法中：

```python
def extend_snapshots(self, snapshots: list[StateSnapshot]) -> None:
    if not snapshots:
        return
    snapshot_by_id = {snapshot.snapshot_id: snapshot for snapshot in self.snapshots}
    for snapshot in snapshots:
        snapshot_by_id[snapshot.snapshot_id] = snapshot
    self.snapshots = sorted(snapshot_by_id.values(), key=lambda item: (item.after_step_index, item.snapshot_id))
```

然后调用点改为：

```python
trajectory.extend_snapshots(advance.snapshots)
```

`_append_replay_snapshots()` 可改为只负责“按 replay step index 取可见 snapshot”，实际追加也调用 `extend_snapshots()`。

### 7.2 合并 trajectory output summary

现状重复：

- `write_case_outputs()`
- `write_default_case_outputs()`
- `write_replay_case_outputs()`

都手写：

```python
"trajectory_output": {
    "path": "trajectory.json",
    "step_count": ...,
    "raw_step_count": len(trajectory.steps),
    "snapshot_count": len(trajectory.snapshots),
    "final_state_present": trajectory.final_state is not None,
}
```

修改目标：新增一个真实共享 builder。

```python
def trajectory_output_summary(trajectory: Trajectory, runtime_metrics: Mapping[str, object]) -> JsonObject:
    return {
        "path": "trajectory.json",
        "step_count": runtime_metrics.get("step_count"),
        "raw_step_count": len(trajectory.steps),
        "snapshot_count": len(trajectory.snapshots),
        "final_state_present": trajectory.final_state is not None,
    }
```

放置位置：`dynsteer/harness/outputs.py`，因为它只服务落盘 payload。

调用改为：

```python
raw_summary["trajectory_output"] = trajectory_output_summary(trajectory, runtime_metrics)
```

### 7.3 合并 runtime initial state 写入

现状：

- evaluator live 流程写 `task_case.initial_state`、`metadata["runtime_initial_state_source"]`、`runtime_initial_state_summary`。
- default writer 写同样字段。
- replay case 从 default trajectory 取 `runtime_initial_state` 后也写同样字段。

修改目标：

新增：

```python
def apply_runtime_initial_state(
    task_case: TaskCase,
    state: JsonObject | None,
    source: str,
) -> JsonObject | None:
    if state is None:
        task_case.metadata.setdefault("runtime_initial_state_source", "adapted_case")
        return None
    task_case.initial_state = state
    task_case.metadata["runtime_initial_state_source"] = source
    summary = state_namespace_summary(state)
    task_case.metadata["runtime_initial_state_summary"] = summary
    return summary
```

放置位置：`dynsteer/evaluate/state_summary.py` 或 `dynsteer/evaluate/runtime.py`。建议放 `state_summary.py`，因为核心职责是状态摘要。

## 8. P1：matching candidate 分析收口

现状：

`evaluate/matching/milestone.py` 中 ready 和 blocked 两条路径都在做：

- 选择 scoring step。
- 构造 scoring boundary。
- 调用 `scorer.score_milestone(...)`。
- 构造 `{"milestone_id", "boundary", "score", "selected", "reject_reason"}`。
- 根据 PASS 或 score 选最优。

修改目标：保留 ready/blocked 的差异，合并同构 candidate scoring。

建议新增：

```python
def _score_candidate(
    milestone: Milestone,
    boundary: Boundary,
    closure_steps: list[TrajectoryStep],
    trajectory: Trajectory,
    scorer: GeneralScorer,
    context: ScoringContext | None,
    reject_reason: str | None = None,
) -> tuple[Boundary, MilestoneScore, JsonObject]:
    scoring_step = milestone_scoring_step(
        milestone,
        closure_steps,
        _default_step_from_closure(closure_steps, boundary),
    )
    scoring_boundary = candidate_boundary_for_current_step(trajectory, scoring_step)
    score = scorer.score_milestone(milestone, scoring_boundary, trajectory, trajectory.snapshots, context=context)
    detail = {
        "milestone_id": milestone.milestone_id,
        "boundary": json_safe(scoring_boundary),
        "score": json_safe(score),
        "selected": False,
        "reject_reason": reject_reason,
    }
    return scoring_boundary, score, detail
```

ready 分支只负责计算 `needs_llm_review` 并覆盖 `reject_reason`：

```python
reject_reason = None if score.status == StageStatus.PASS else (
    "needs_llm_semantic_review" if needs_llm_review else "status_not_pass"
)
detail["reject_reason"] = reject_reason
```

blocked 分支只负责 `missing_predecessors` 和 predecessor diagnostics。

收益：

- 删除 `build_milestone_candidate_detail()` 后，candidate payload 只有 matching 层一处真实构造。
- ready/blocked 两段保留业务差异，不再复制 scoring/payload 细节。

## 9. P1：finish stage result 构造瘦身

现状：

`evaluate/settlement.py` 中 `_deterministic_finish_stage_result()` 与 `_whole_trajectory_finish_stage_result()` 的两个分支重复构造大量 `StageEvaluationResult` 字段：

- `stage_id`
- `milestone_id`
- `next_weights`
- `minefield_score`
- `fatal_minefield_score`
- `metadata=_finish_stage_metadata(...)`
- `evidence/diagnosis`
- `dimension_confidence/uncertainty`

修改目标：抽一个真实构造器，分支只提供不同的评分信息。

建议新增：

```python
def _finish_result(
    interval: StageInterval,
    state: RuntimeEvaluationState,
    status: StageStatus,
    score: float,
    dimension_scores: dict[Dimension, float],
    dimension_levels: dict[Dimension, EvaluationLevel],
    dimension_confidence: dict[Dimension, float],
    evidence: list[str],
    diagnosis: list[str],
    hard_pass: bool,
    required_fields_missing_ratio: float,
    metadata: JsonObject,
) -> StageEvaluationResult:
    return StageEvaluationResult(
        stage_id=interval.stage_id,
        milestone_id=interval.milestone_id,
        status=status,
        stage_score=score,
        dimension_scores=dimension_scores,
        dimension_levels=dimension_levels,
        dimension_confidence=dimension_confidence,
        dimension_uncertainty={dimension: 1.0 - value for dimension, value in dimension_confidence.items()},
        evidence=evidence,
        diagnosis=diagnosis,
        next_weights=dict(state.weights),
        fatal=False,
        hard_constraints_all_pass=hard_pass,
        required_fields_missing_ratio=required_fields_missing_ratio,
        minefield_score=state.max_minefield_score,
        fatal_minefield_score=state.max_minefield_score if state.fatal_minefield else 0.0,
        metadata=metadata,
    )
```

分支调用示例：

```python
return _finish_result(
    interval=interval,
    state=state,
    status=status,
    score=score,
    dimension_scores=dimension_scores,
    dimension_levels={dimension: EvaluationLevel.CHEAP for dimension in dimension_scores},
    dimension_confidence={dimension: 0.95 for dimension in dimension_scores},
    evidence=evidence,
    diagnosis=diagnosis,
    hard_pass=status in {StageStatus.PASS, StageStatus.WARN},
    required_fields_missing_ratio=0.0 if bool(verification.get("all_milestones_matched")) else 1.0,
    metadata=_finish_stage_metadata(verification, evaluation_policy, replay_metadata),
)
```

同时 `_judge_result_metadata()` 与 finish whole trajectory 分支中的 dimension metadata 应继续复用，不要再复制字典字段。

## 10. P2：outputs / experiment index 小幅收口

### 10.1 `write_default_case_outputs()` 只保留 default 差异

`write_default_case_outputs()` 当前和 evaluator live 流程重复：

- session start
- runtime initial state
- step append
- snapshot merge
- progress report
- runtime metrics
- trajectory output

本轮不建议抽一个巨大的 “run_session_loop” 框架，否则会把不同方法的细节塞进回调。建议只抽以下小原语：

1. `Trajectory.extend_snapshots(...)`
2. `apply_runtime_initial_state(...)`
3. `trajectory_output_summary(...)`
4. `_build_runtime_metrics(...)` 目前 evaluator 内有成员方法，default writer 仍直接调用 `build_runtime_metrics(...)`。建议把 evaluator 私有 `_build_runtime_metrics()` 下沉为模块函数或直接在二者都用 `build_runtime_metrics(...)`，不要保留 evaluator 私有中转。

### 10.2 `experiment/runner.py` index payload 简化

现状：

`_build_experiment_index_payload()` 手写三层嵌套，同时 `ExperimentCaseResult.to_index_dict()` 已经是 case 级唯一事实来源。

修改目标：

保留 `to_index_dict()` 为 case payload 来源；index builder 只负责分桶：

```python
def _result_bucket(root: JsonObject, result: ExperimentCaseResult) -> JsonObject:
    benchmark = root.setdefault(result.benchmark, {})
    method = benchmark.setdefault(result.method.value, {})
    model = method.setdefault(result.model_id, {"repeats": {}})
    repeat = model["repeats"].setdefault(str(result.repeat_index), {"cases": {}})
    return repeat["cases"]
```

`_build_experiment_index_payload()` 主体变成：

```python
grouped: JsonObject = {}
for result in sorted(results, key=_experiment_result_key):
    cases = _result_bucket(grouped, result)
    if result.case_id in cases:
        raise ValueError(...)
    cases[result.case_id] = result.to_index_dict()
return {"experiment_id": experiment_id, "case_count": len(results), "results": grouped}
```

注意：这里新增 helper 不是为了“套娃”，而是把重复 `setdefault` 层级收成一个分桶原语。

## 11. P1：调用链重复参数校验清理

本节专门回应“无意义参数校验”问题。判定标准：

- **保留**：外部输入边界、dataclass `__post_init__` 领域不变量、第三方对象能力检查、文件/JSON/schema 读取边界。
- **删除**：私有下游函数中，对上游刚构造、刚校验、刚 normalize 的对象继续做 `None` / 类型 / 范围重复检查。
- **不移动成中转函数**：能在入口函数一次性校验的，就在入口函数校验；下游私有 helper 默认接收可信数据。

### 11.1 `harness/runner.py`：入口校验一次，私有 helper 不重复校验

调用链：

```text
main.py -> run_harness_configs(...)
         -> _log_dir_from_configs(configs)
         -> _get_or_configure_harness_logger(log_dir)
         -> _effective_max_workers(max_workers, config)
         -> _run_config(...)
         -> _config_label(run_config)
```

上游保证：

- `run_harness_configs()` 已检查 `configs is not None`、`max_workers >= 1`、`configs` 不含 `None`。
- `HarnessRunConfig.__post_init__()` 已检查 `benchmark/data_root/runs_dir/results_dir`。
- `_log_dir_from_configs()` 只由 `run_harness_configs()` 调用。
- `_effective_max_workers()` 只由 `run_harness_configs()` 对每个已校验 config 调用。
- `_config_label()` 只接收 `prepare_task_cases()` 返回的 `run_config`。

修改：

```python
def _effective_max_workers(max_workers: int, config: HarnessRunConfig) -> int:
    benchmark_max_workers = config.metadata.get("benchmark_max_workers")
    if benchmark_max_workers is None:
        return max_workers
    if isinstance(benchmark_max_workers, bool) or not isinstance(benchmark_max_workers, int):
        raise ValueError("benchmark_max_workers 必须是整数")
    return min(max_workers, max(benchmark_max_workers, 1))


def _config_label(config: HarnessRunConfig) -> str:
    name = config.metadata.get("run_config_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    index = config.metadata.get("run_config_index")
    return f"第{index}项配置" if index is not None else "未命名配置"


def _log_dir_from_configs(configs: list[HarnessRunConfig]) -> Path:
    return configs[0].runs_dir / "logs" if configs else Path("runs") / "logs"


def _get_or_configure_harness_logger(log_dir: Path) -> logging.Logger:
    logger = logging.getLogger("dynsteer")
    if logger.handlers:
        return logger
    return configure_logger(log_dir)
```

同时在 `_run_config()` 中只计算一次配置标签，避免重复调用：

```python
config_label = _config_label(run_config)
logger.info(
    "基于配置%s，开始基于%s展开评估，Cases数量: %s",
    config_label,
    run_config.benchmark,
    len(task_cases),
    extra={
        "benchmark": run_config.benchmark,
        "case_count": len(task_cases),
        "run_config": config_label,
    },
)
```

预计缩减：约 8-12 行。

### 11.2 `harness/scheduler.py`：调度入口承担 task/logger/max_workers 校验

调用链：

```text
_run_config(...) -> run_case_tasks(...)
               -> _run_tasks_serial(...) / _run_tasks_parallel(...)
               -> _progress_total_from_tasks(...)
               -> _progress_visible_bars(...)
               -> _run_case(...)
               -> _drain_progress_events(...)
               -> _apply_progress_event(...)
```

上游保证：

- `_run_config()` 构造的 `tasks` 是 `HarnessCaseTask` 列表。
- `run_case_tasks()` 是调度边界，适合集中校验 `tasks/logger/max_workers`。
- `_drain_progress_events()` 和 `_apply_progress_event()` 只处理本函数内创建的 `Queue`、`TqdmCaseProgressManager` 和 `CaseProgressEvent`。

修改：

```python
def run_case_tasks(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
    if tasks is None or logger is None:
        raise ValueError("tasks 和 logger 不能为空")
    if max_workers < 1:
        raise ValueError("max_workers 必须大于 0")
    if any((task is None for task in tasks)):
        raise ValueError("tasks 不能包含空任务")
    if not tasks:
        return []
    if max_workers == 1:
        return _run_tasks_serial(tasks, logger=logger, force_eval=force_eval)
    return _run_tasks_parallel(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)
```

删除下游重复校验：

```python
def _progress_visible_bars(max_workers: int) -> int:
    return max(DEFAULT_VISIBLE_PROGRESS_BARS, max_workers)


def _run_case(...):
    # 删除 task is None 校验；run_case_tasks 已保证。
    ...


def _progress_total_from_tasks(tasks: list[HarnessCaseTask]) -> int:
    value = tasks[0].config.metadata.get("max_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_PROGRESS_TOTAL
    return max(value, 1)


def _drain_progress_events(events: Queue[CaseProgressEvent], manager: TqdmCaseProgressManager) -> None:
    while True:
        ...


def _apply_progress_event(event: CaseProgressEvent, manager: TqdmCaseProgressManager) -> None:
    if event.kind == "case_advanced":
        ...
```

预计缩减：约 12-18 行。

### 11.3 `adapter/toolsandbox/harness.py`：session/config 校验只在接口边界做

调用链：

```text
start_case(...)
  -> _named_scenarios(config)
  -> _toolsandbox_roles(config)
  -> _starting_context_from_scenario(scenario)
  -> _prepare_system_environment_messages(session)

advance_case(session)
  -> _require_session(session)
  -> _advance_native_session(session)
```

上游保证：

- `start_case()` 是 harness 外部接口，保留 `config/case_id/raw_output_dir/max_messages` 校验。
- `advance_case()` 已通过 `_require_session()` 把 `object` 收窄为 `ToolSandboxSession`。
- `_prepare_system_environment_messages()` 只接收 `start_case()` 刚构造出的 session。
- `_advance_native_session()` 只接收 `_require_session()` 返回的 session。
- `_named_scenarios()` 只由 `list_cases()` / `start_case()` 调用；两者都会先走 `prepare_config()` 或入口校验。

修改：

1. `load_harness_run_configs()` 在配置边界写入清洗后的 agent/user：

```python
if normalized_benchmark == "toolsandbox":
    metadata["agent"] = required_str(raw_spec, "agent", f"run_configs.json 第 {index} 项")
    metadata["user"] = required_str(raw_spec, "user", f"run_configs.json 第 {index} 项")
```

2. `_role_impl_type()` 不再重复判断空字符串：

```python
def _role_impl_type(self, role_name: object, role_label: str) -> object:
    cli_utils = load_toolsandbox_module("tool_sandbox.cli.utils")
    role_impl_type = getattr(cli_utils, "RoleImplType")
    effective_name = str(role_name).strip()
    try:
        return role_impl_type[effective_name]
    except KeyError:
        try:
            return role_impl_type(effective_name)
        except (TypeError, ValueError):
            return effective_name
```

3. 删除 `_named_scenarios()` 的 `config is None` 校验。

4. 删除 `_prepare_system_environment_messages()` 的 `session is None` 校验。

5. 删除 `_advance_native_session()` 的 `session is None` 校验。

预计缩减：约 8-14 行。

### 11.4 `evaluate/matching/milestone.py`：route metadata 下游不重复校验

调用链：

```text
adapter/loader._postprocess_task_case(...)
  -> enrich_milestone_routes(graph)
     -> _enrich_milestone_route_groups(milestone)
        # 已保证同一 milestone 至多一个 route_group

evaluate_agent_step(...)
  -> analyze_milestone_step(...)
     -> milestone_scoring_step(...)
        -> _milestone_route_groups(...)
```

上游保证：

- `enrich_milestone_routes()` 在 adapted case 加载和重建阶段都会执行。
- `_enrich_milestone_route_groups()` 已在发现 `len(route_groups) > 1` 时抛错。
- `analyze_milestone_step()` 传入的 `closure_steps` 由 `closure_steps or [step]` 保证非空。
- `milestone_scoring_step()` 只从 ready/blocked milestone 循环中调用，`milestone/default_step` 均为已构造对象。

修改：

```python
def milestone_scoring_step(
    milestone: Milestone,
    closure_steps: list[TrajectoryStep],
    default_step: TrajectoryStep,
) -> TrajectoryStep:
    route_group = _milestone_route_group(milestone)
    if route_group is None:
        return default_step
    route = route_group.get("route")
    if not isinstance(route, dict):
        return default_step
    sender = str(route.get("sender") or "").strip().upper()
    recipient = str(route.get("recipient") or "").strip().upper()
    if not sender or not recipient:
        return default_step
    for step in reversed(closure_steps):
        if step.actor.name == sender and step.recipient is not None and step.recipient.name == recipient:
            return step
    return default_step


def _milestone_route_group(milestone: Milestone) -> JsonObject | None:
    matching = milestone.metadata.get("milestone_matching")
    if not isinstance(matching, dict):
        return None
    route_groups = matching.get("route_groups")
    if not isinstance(route_groups, list) or not route_groups:
        return None
    first_group = route_groups[0]
    return dict(first_group) if isinstance(first_group, dict) else None
```

同时把 `_default_step_from_closure(closure_steps, boundary)` 改为 `closure_steps[-1]`，删除 `_default_step_from_closure()`。

预计缩减：约 12-18 行。

### 11.5 `evaluate/runtime.py`：candidate progress 只消费同源生成 payload

调用链：

```text
evaluate_agent_step(...)
  -> analyze_milestone_step(...)
     -> attempt_detail = {"step_index": step.index, "candidate_scores": [...]}
  -> _ready_frontier_no_progress_decision(...)
     -> update_ready_frontier_progress_watch(...)
        -> _ready_milestone_progress_from_candidate(...)
```

上游保证：

- `attempt_detail.step_index` 由 `TrajectoryStep.index` 写入，主链路中 `TrajectoryStep` 来自 loader 或 ToolSandbox trajectory parser，均已转为 int。
- `observed_candidates` 已筛选 `candidate["score"]` 为 dict。
- `_ready_milestone_progress_from_candidate()` 是私有函数，唯一调用点就在筛选之后。

修改：

```python
step_index = attempt_detail["step_index"]
```

删除：

```python
if not isinstance(step_index, int):
    raise ValueError("attempt_detail.step_index 必须是整数")
```

`_ready_milestone_progress_from_candidate()` 改为：

```python
def _ready_milestone_progress_from_candidate(
    milestone_id: str,
    candidate: JsonObject,
    step_index: int,
) -> ReadyMilestoneProgress:
    score_payload = candidate["score"]
    boundary = candidate.get("boundary")
    ...
```

删除 `candidate score 必须是 JSON 对象` 的重复校验。

预计缩减：约 4-8 行。

### 11.6 `evaluate/settlement.py`：`milestone_frontier` 校验只保留一处

调用链：

```text
DynSTEEREvaluator._initial_runtime_state(...)
  -> RuntimeEvaluationState(milestone_frontier=initialize_milestone_frontier(...))

DynSTEEREvaluator._evaluate_closed_agent_step(...)
  -> evaluate_agent_step(...)
     -> analyze_milestone_step(...)
     -> evaluate_checkpoint(...)
```

当前 `evaluate_agent_step()` 和 `evaluate_checkpoint()` 都检查：

```python
if state.milestone_frontier is None:
    raise ValueError("RuntimeEvaluationState 缺少 milestone_frontier")
```

仓库主功能代码中，`RuntimeEvaluationState` 只由 `_initial_runtime_state()` 创建，且 `evaluate_checkpoint()` 只通过 `evaluate_agent_step()` 进入。因此保留上游 `evaluate_agent_step()` 的检查即可，删除 `evaluate_checkpoint()` 中的重复检查。

如果后续希望把 `evaluate_checkpoint()` 重新定义为公开 API，则应反过来删除 `evaluate_agent_step()` 的检查并在 `evaluate_checkpoint()` 保留校验；不要两处同时保留。

预计缩减：约 2-3 行。

### 11.7 不建议作为本轮删除的校验

以下校验虽然静态看也有上游保护，但它们处在更明确的 API 或领域边界，本轮不删：

| 位置 | 保留原因 |
| --- | --- |
| dataclass `__post_init__()` | 领域对象不变量，允许外部直接构造时立即失败 |
| `BaseLLM.__init__()` / `BaseLLM.chat()` / `LLMJudge` | 对外 provider 和 judge 边界 |
| `read_json_file()` / `ensure_json_object()` / `enum_value()` | 外部文件和 schema 边界 |
| `write_replay_case_outputs()` 与 `DynSTEEREvaluator.evaluate_replay()` 的双入口校验 | 两者都是可独立调用的功能入口，除非先明确其中一个降级为内部函数 |
| `BaseBenchmarkHarness` 默认 hook 的 `session is None` | benchmark harness contract，不同子类可能直接继承默认实现 |
| `ToolSandboxHarness.start_case()` / `advance_case()` / `stop_case()` | benchmark harness 对外接口，接收外部调度器传入对象 |
| `ToolSandboxConstraintScorer` 的 metadata/schema/measure 校验 | 第三方 ToolSandbox schema 和动态函数恢复边界 |

## 12. 不删除项

以下对象静态看起来低引用或无引用，但本方案不删除：

1. `get_log_buffer()` / `clear_log_buffer()`  
   原因：项目约束要求日志支持缓冲区，未来前端轮询接口需要这个边界能力。

2. `BaseBenchmarkHarness` 中返回 `{}` / `None` 的默认 hook  
   原因：这些是子类可覆盖接口，不是死代码。`metrics_from_session()`、`initial_state_from_session()`、`final_state_from_session()`、`raw_summary_from_session()` 是 harness contract。

3. 包级 `__init__.py` 显式 re-export  
   原因：项目约束禁止懒加载，但允许显式顶层 import。虽然 AST 本文件内看似未使用，它们是包 API。

4. `load_trajectory()`  
   原因：`experiment/runner.py` replay 主链路使用，且测试覆盖。

5. `ToolSandboxHarness` 中检查第三方能力缺失的 `NotImplementedError`  
   原因：这不是假 registry，而是运行真实 ToolSandbox scenario 时外部对象缺少必需方法的边界错误。

## 13. 测试与验收

建议按阶段验证，不要等所有改完再跑。

1. P0 删除项后：

```powershell
uv run python -m compileall dynsteer main.py
uv run pytest tests -q
```

2. 配置解析收口后：

```powershell
uv run pytest tests/harness/test_paths_and_cache.py tests/experiment/test_runner_flags.py -q
```

3. 标量解析与零散工具函数收口后：

```powershell
uv run pytest tests/harness/test_paths_and_cache.py tests/experiment/test_runner_flags.py tests/adapter/test_trajectory_schema_preserves_run_id.py -q
```

4. Actor 归一化收口后：

```powershell
uv run pytest tests/adapter/test_trajectory_schema_preserves_run_id.py -q
```

5. 调用链重复校验清理后：

```powershell
uv run pytest tests/harness/test_scheduler_force_eval.py tests/harness/test_paths_and_cache.py -q
```

6. snapshot / output 收口后：

```powershell
uv run pytest tests/harness/test_paths_and_cache.py tests/experiment/test_index_without_run_id.py -q
```

7. 全量验收：

```powershell
uv run python -m compileall dynsteer main.py
uv run pytest tests -q --cov=dynsteer --cov-report=term-missing
git diff --check
```

## 附录A. 项目中没有把握实现的模块部分

1. **仓库外部 API 使用情况无法静态确认**  
   `DynSTEEREvaluator.evaluate_minefields()`、`record_runtime_metrics()`、`all_rubrics()`、`dynsteer_guidance`、`swebench_pro` 在仓库内没有真实主链路调用，但不能证明用户是否有仓库外脚本直接调用。按本任务“主功能代码未使用则删除”的规则，本方案默认删除；若用户确认存在外部调用，应先迁移外部脚本。

2. **历史 `docs/plans/report/*` 会继续提到旧入口**  
   项目约束禁止删除 `docs/plans` 既有文档，本方案不会清理历史报告中的旧 API 描述。因此代码落地后，历史报告可能仍保留 `evaluate_minefields()` 或 `swebench_pro` 的历史文字，视为历史记录，不作为当前 API。

3. **`swebench_pro` 是否近期必须实现无法确认**  
   当前代码只是 scaffold，并无 dataset schema、runner 和适配逻辑。若研究计划近期确实要求 SWE-bench，应另开“真实 SWE-bench 接入方案”，而不是保留一个注册后必失败的假入口。

4. **大函数瘦身的最终行数存在实现浮动**  
   `finish_settlement()`、`_whole_trajectory_finish_stage_result()`、`write_default_case_outputs()` 这类函数的收口必须以测试行为不变为前提；具体缩减可能因保留中文注释、异常消息和 API 文档同步而上下浮动。

## 附录B. 建议落地顺序

1. 删除 P0 死代码和半成品入口。
2. 新增 `parse_int_value()` / `parse_float_value()`，收口 `_int_from_mapping`、LLM factory 数值 helper、loader/telemetry/diagnostics 小工具。
3. 收口 Actor/role 归一化。
4. 删除一次性 dataclass `from_dict()`。
5. 收口 runner/scheduler/ToolSandbox harness 的私有下游重复校验。
6. 收口 route metadata、runtime candidate progress、checkpoint frontier 的重复校验。
7. 收口 snapshot / trajectory output / runtime initial state。
8. 收口 matching candidate detail。
9. 收口 finish stage result 构造。
10. 收口 outputs / experiment index。
11. 更新 `docs/apis/llm.md`、`docs/apis/experiment.md`、`docs/apis/swebench.md`。
12. 全量 compile、pytest、coverage、`git diff --check`。

## 14. 有效行数缩减统计

当前基线：

| 范围 | 当前有效行数 |
| --- | ---: |
| `dynsteer/**/*.py` | 10669 |

当前最大热点文件：

| 文件 | 当前有效行数 |
| --- | ---: |
| `dynsteer/evaluate/settlement.py` | 706 |
| `dynsteer/evaluate/evaluator.py` | 697 |
| `dynsteer/model.py` | 611 |
| `dynsteer/evaluate/diagnostics.py` | 588 |
| `dynsteer/evaluate/semantic.py` | 562 |
| `dynsteer/harness/outputs.py` | 424 |
| `dynsteer/adapter/toolsandbox/scorer.py` | 326 |
| `dynsteer/adapter/toolsandbox/harness.py` | 319 |

预计缩减：

| 项目 | 预计缩减 |
| --- | ---: |
| 明确死代码、半成品入口、未使用 schema | 110-130 |
| 配置解析、标量工具与 `_int_from_mapping` 收口 | 40-65 |
| Actor/role 归一化收口 | 25-35 |
| 一次性 `from_dict()` 删除 | 8-12 |
| snapshot / trajectory / runtime initial state 收口 | 30-45 |
| matching candidate 组装收口 | 25-35 |
| 调用链重复校验删除 | 35-55 |
| finish result 构造收口 | 45-65 |
| outputs / experiment index 小幅收口 | 35-50 |
| **合计** | **360-440** |

保守取中位后，本方案落地预计：

```text
10669 - 约400 = 约10269 有效行
```

统计口径对应用户要求：非测试、非文档的 `.py` 文件中，排除空白行、日志输出行、仅注释行、docstring 行。
