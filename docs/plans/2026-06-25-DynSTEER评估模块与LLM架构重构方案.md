# DynSTEER评估模块与LLM架构重构方案

> 本方案遵循 `docs/constraints/code.md`：先产出计划文档，不主动提交 git；代码落地时使用 Python 3.10+、uv、PyTest，并保持函数入参类型声明、空值检查、结构化异常处理、中文注释和 API 文档同步。

## 目标

将当前分散在 `dynsteer/judge.py`、`dynsteer/judges/llm.py`、`dynsteer/evaluate.py`、`dynsteer/match.py` 中的评估逻辑拆分为职责清晰的包结构：

- `dynsteer/judges/` 只负责轨迹阶段评估器抽象和 cheap/standard/expensive 三档 Judge。
- `dynsteer/llm/` 只负责不同 provider 的 LLM 构建与交互。
- `dynsteer/evaluate/` 只负责动态评估编排、运行态模型、权重、milestone 匹配和评估工具函数。
- 清理重复函数、无效引用和未使用辅助函数。
- 为兼容 ToolSandbox 外源仓库，将 DynSTEER 自身依赖回退并固定为 `openai==1.17.0` 与 `httpx==0.27.2` 的稳定组合。

## 当前核查结论

1. `dynsteer/judge.py` 当前包含 `Judge` 协议和 `LocalJudge`，应迁移到 `dynsteer/judges/base.py` 与 `dynsteer/judges/cheap.py`。
2. `dynsteer/judges/llm.py` 当前同时包含 LLM provider 构建、OpenAI SDK 调用、standard/expensive 调度、响应解析，应拆分到 `dynsteer/llm/` 与 `dynsteer/judges/`。拆分后 `LLMJudge` 不再实现 `_evaluate_standard(...)` 与 `_evaluate_expensive(...)`，两档评估流程分别落在 `StandardJudge.evaluate_stage(...)` 与 `ExpensiveJudge.evaluate_stage(...)` 中。
3. `dynsteer/evaluate.py` 当前包含权重、模型、minefield、milestone、调度器、运行态执行和工具函数，文件过大，应替换为 `dynsteer/evaluate/` 包。
4. `dynsteer/match.py` 只被 `dynsteer/evaluate.py` 使用，适合并入 `dynsteer/evaluate/milestone.py` 后删除。
5. `_clamp` 在 `dynsteer/evaluate.py` 与 `dynsteer/score.py` 重复，应抽到共享数值工具。
6. `dynsteer/adapter/toolsandbox/harness.py` 导入了未使用的 `TrajectoryStep`，应删除。
7. `ToolSandboxHarness._run_id()` 与 `BaseBenchmarkHarness.build_run_id()` 逻辑重复且当前无调用，应删除。
8. `ToolSandboxHarness._result_from_toolsandbox()` 当前无调用。该函数保留了旧的整次运行转换能力，建议本轮先标记为“待确认旧入口”，若后续确认无外部调用，再单独删除，避免误删真实兼容路径。
9. 当前 `uv.lock` 已解析为 `openai==1.109.1` 与 `httpx==0.28.1`；`pyproject.toml` 仍是范围依赖 `openai>=1.30,<2`、`httpx>=0.27,<0.29`。但本地 `../ToolSandbox/pyproject.toml` 硬依赖 `openai==1.17.0`，若通过 `uv add --editable ../ToolSandbox` 接入同一环境，会与 DynSTEER 当前依赖产生解析冲突。
10. `openai==1.17.0` 的元数据允许 `httpx>=0.23.0,<1`，但该 SDK 内部仍使用 `proxies` 参数构造 `httpx.Client`；因此不能只回退 `openai`，必须同步固定 `httpx<0.28`，本方案采用 `httpx==0.27.2`。

## 目标目录结构

### judges

```text
dynsteer/judges/
- __init__.py
- base.py
- cheap.py
- standard.py
- expensive.py
```

职责：

- `base.py`
  - `BaseJudge`：所有轨迹评估器的抽象基类，对外统一接口为 `evaluate_stage(...)`。
  - `LLMJudge`：继承 `BaseJudge` 的 LLM-as-a-Judge 抽象基类，只封装入参检查、prompt 构造、JSON 调用、响应解析、结果转换和通用异常；不实现 `_evaluate_standard(...)`、`_evaluate_expensive(...)`，不直接决定评估等级。
  - `LLMJudgeConfig`、`LLMJudgeConfigurationError`、`LLMJudgeResponseError`。
- `cheap.py`
  - `CheapJudge(BaseJudge)`：迁移当前 `LocalJudge` 的本地结构化评估逻辑。
- `standard.py`
  - `StandardJudge(LLMJudge)`：实现 `evaluate_stage(...)`，在函数内完成单轮 LLM 评估流程，复用 `LLMJudge` 的 prompt、调用、解析和结果转换 helper。
- `expensive.py`
  - `ExpensiveJudge(LLMJudge)`：实现 `evaluate_stage(...)`，在函数内完成多轮聚焦评估与汇总裁决流程，复用 `LLMJudge` 的 prompt、调用、解析和结果转换 helper。
- `__init__.py`
  - 统一导出 `BaseJudge`、`LLMJudge`、`CheapJudge`、`StandardJudge`、`ExpensiveJudge` 和相关错误类型。

核心接口形态：

```python
class BaseJudge(ABC):
    @abstractmethod
    def evaluate_stage(
        self,
        interval: StageInterval,
        task_case: TaskCase,
        trajectory: Trajectory,
        level: EvaluationLevel,
        weights: dict[Dimension, float],
    ) -> StageEvaluationResult:
        """评估单个阶段。"""
```

`LLMJudge` 作为基类不再对外承担 cheap/standard/expensive 分发，也不保留 `_evaluate_standard(...)` 与 `_evaluate_expensive(...)`。分发由 `DynSTEEREvaluator` 根据 `EvaluationDecision` 调用 `StandardJudge` 或 `ExpensiveJudge`；两档 judge 在自身 `evaluate_stage(...)` 中实现真实流程，避免出现只调用基类方法的一行中转函数。

### llm

```text
dynsteer/llm/
- __init__.py
- base.py
- factory.py
- openai.py
- anthropic.py
```

职责：

- `base.py`
  - `LLMMessage`：角色与内容。
  - `LLMConfig`：provider、model、api_key、base_url、timeout_seconds、temperature、max_tokens。其中 `max_tokens` 类型为 `int | None`，默认不传给 provider。
  - `BaseLLM`：对外只暴露交互响应接口 `chat(messages: list[LLMMessage]) -> str`。
  - `LLMConfigurationError`、`LLMResponseError`。
- `openai.py`
  - `OpenaiLLM(BaseLLM)`：使用 OpenAI-compatible Chat Completions，覆盖 GPT、Qwen 等兼容 OpenAI 协议的模型。
- `anthropic.py`
  - `AnthropicLLM(BaseLLM)`：使用 `httpx` 调用 Anthropic Messages API，避免新增默认 SDK 依赖。
- `factory.py`
  - `build_llm(config: LLMConfig) -> BaseLLM`
  - `build_llm_from_env(env: Mapping[str, str] | None = None) -> BaseLLM | None`
- `__init__.py`
  - 导出基础类型和 factory。

Provider 约定：

```text
DYNSTEER_JUDGE_PROVIDER=openai_compatible | openai | qwen | anthropic | claude
DYNSTEER_JUDGE_MODEL=qwen-plus-latest
DYNSTEER_JUDGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DYNSTEER_JUDGE_API_KEY=${DYNSTEER_JUDGE_API_KEY}
DYNSTEER_JUDGE_TIMEOUT_SECONDS=60
DYNSTEER_JUDGE_TEMPERATURE=0
# 可选；未配置或为空时不向 provider 传 max_tokens
DYNSTEER_JUDGE_MAX_TOKENS=
DYNSTEER_EXPENSIVE_JUDGE_PASSES=3
```

`max_tokens` 策略：

- 默认不设置 `DYNSTEER_JUDGE_MAX_TOKENS`，`OpenaiLLM` 调用 Chat Completions 时不传 `max_tokens`，由 provider 和模型默认策略处理输出上限，避免 DynSTEER 主动截断 judge JSON。
- 只有用户显式设置正整数时才传 `max_tokens`，用于成本控制或实验复现。
- 因依赖回退到 `openai==1.17.0`，本轮只使用 Chat Completions 的 `max_tokens` 参数，不使用新版 SDK 的 `max_completion_tokens` 或 Responses API 的 `max_output_tokens`。

API key 读取优先级：

1. `DYNSTEER_JUDGE_API_KEY`
2. OpenAI-compatible provider 使用 `OPENAI_API_KEY`
3. Anthropic provider 使用 `ANTHROPIC_API_KEY`

### evaluate

```text
dynsteer/evaluate/
- __init__.py
- models.py
- evaluator.py
- weights.py
- milestone.py
- utils.py
```

职责：

- `models.py`
  - `# 状态模型`：`RuntimeEvaluationState`
  - `# 结果/决策模型`：`RuntimeEvaluationDecision`
  - `# 错误模型`：`JudgeConfigurationError`
- `evaluator.py`
  - `DynSTEEREvaluator`
  - `evaluate_minefields(...)` 成员函数
  - `select_evaluation_level(...)` 成员函数
  - 运行态 benchmark 编排、阶段调度、fail-fast 策略。
- `weights.py`
  - `normalize_weights(...)`
  - `select_initial_weights(...)`
  - `update_weights(...)`
- `milestone.py`
  - 当前 `match.py` 中的 `_node_ids`、`_topological_order`、`validate_milestone_graph`、`_predecessors`、`_best_candidate`、`match_milestones`
  - 当前 `DynSTEEREvaluator` 中与 milestone 命中相关的 `_ready_milestones`、`_find_hit_milestone`、`_stage_start_for_milestone`
  - `_milestone_score_matrix(...)`
- `utils.py`
  - `compute_uncertainty(...)`
  - `overall_score(...)`
  - `enrich_stage_result(...)`
  - `first_failure_stage_id(...)`
  - `build_trajectory(...)`
  - `merge_snapshots(...)`
- `__init__.py`
  - 导出 `DynSTEEREvaluator`、`JudgeConfigurationError`、权重工具和通用工具。

`dynsteer/evaluate.py` 文件落地时删除，由 `dynsteer/evaluate/` 包替代；`from dynsteer.evaluate import DynSTEEREvaluator` 仍保持可用。

## 公共 API 调整

### Judge 构造

旧方式：

```python
from dynsteer.judges.llm import LLMJudge

evaluator = DynSTEEREvaluator(llm_judge=LLMJudge.from_env())
```

新方式：

```python
from dynsteer.evaluate import DynSTEEREvaluator

evaluator = DynSTEEREvaluator.from_env()
```

`DynSTEEREvaluator.from_env()` 负责：

1. 从环境变量构建 `BaseLLM | None`。
2. 未配置 LLM 时只构建 `CheapJudge`。
3. 已配置 LLM 时构建共享同一个 `BaseLLM` 的 `StandardJudge` 和 `ExpensiveJudge`。

`DynSTEEREvaluator.__init__(...)` 不再接收单个 `llm_judge` 参数；standard 与 expensive 两档应分别通过 `standard_judge`、`expensive_judge` 注入，避免由一个对象在内部按 `EvaluationLevel` 分发。

显式注入方式：

```python
evaluator = DynSTEEREvaluator(
    cheap_judge=CheapJudge(),
    standard_judge=StandardJudge(llm=llm),
    expensive_judge=ExpensiveJudge(llm=llm),
)
```

### 删除旧入口

为满足“合并统一到 judges 文件夹”和“减少一行中转函数”的要求，本轮建议不保留以下兼容 shim：

- 删除 `dynsteer/judge.py`
- 删除 `dynsteer/judges/llm.py`
- 删除 `dynsteer/match.py`

内部引用、测试和文档全部迁移到新路径。若外部项目已经依赖这些旧路径，需要在实施前确认是否允许保留一个版本的弃用兼容层；保留兼容层会增加冗余。

## 依赖版本策略

当前本仓库锁文件解析结果：

```text
openai==1.109.1
httpx==0.28.1
httpcore==1.0.9
```

本地 `../ToolSandbox/pyproject.toml` 硬依赖：

```text
openai==1.17.0
```

若通过 `uv add --editable ../ToolSandbox` 将 ToolSandbox 纳入 DynSTEER 同一个 uv 环境，`openai==1.109.1` 与 `openai==1.17.0` 无法同时满足。为优先保证 ToolSandbox benchmark 可接入，本轮依赖策略改为“回退并固定兼容组合”。

截图中的错误 `Client.__init__() got an unexpected keyword argument 'proxies'` 属于旧版 OpenAI SDK 与新版 httpx 参数不兼容的典型问题。`openai==1.17.0` 的元数据允许 `httpx>=0.23.0,<1`，但该 SDK 内部仍可能向 `httpx.Client(...)` 传入 `proxies`。因此不能只固定 `openai==1.17.0`，还必须同步固定 `httpx<0.28`。本方案采用 `httpx==0.27.2`。

建议修改 `pyproject.toml`：

```toml
dependencies = [
    "openai==1.17.0",
    "httpx==0.27.2",
]
```

理由：

- 与 ToolSandbox 的 `openai==1.17.0` 硬依赖一致，避免 editable 安装外部仓库时出现依赖解析冲突。
- `httpx==0.27.2` 仍支持旧 OpenAI SDK 使用的 `proxies` 参数，避免 `httpx==0.28.1` 下的 client 初始化错误。
- DynSTEER 的 `OpenaiLLM` 只使用 OpenAI SDK v1 已支持的 Chat Completions，不使用 Responses API、`max_completion_tokens` 等新版 SDK 能力。
- `AnthropicLLM` 继续使用项目固定的 `httpx` 直接调用 Anthropic Messages API，不新增默认 SDK 依赖。

落地后执行：

```bash
uv lock
uv sync --locked
uv run python -c "import importlib.metadata as m; print(m.version('openai'), m.version('httpx'))"
```

期望输出：

```text
1.17.0 0.27.2
```

兼容性验收还应增加 OpenAI client 构造 smoke test：

```bash
uv run python -c "from openai import OpenAI; OpenAI(api_key='test', base_url='https://example.invalid/v1'); print('ok')"
```

期望输出：

```text
ok
```

## 任务分解

### Task 1：新增共享数值工具

文件：

- 新增：`dynsteer/numeric.py`
- 修改：`dynsteer/score.py`
- 修改：`dynsteer/evaluate/weights.py`
- 修改：`dynsteer/evaluate/utils.py`

实现：

- 新增 `clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float`。
- 删除 `score.py` 与旧 `evaluate.py` 中重复的 `_clamp`。
- 所有调用统一改为 `from dynsteer.numeric import clamp`。

测试：

```bash
uv run pytest tests -q
```

### Task 2：重构 judges 包

文件：

- 新增：`dynsteer/judges/base.py`
- 新增：`dynsteer/judges/cheap.py`
- 新增：`dynsteer/judges/standard.py`
- 新增：`dynsteer/judges/expensive.py`
- 修改：`dynsteer/judges/__init__.py`
- 删除：`dynsteer/judge.py`
- 删除：`dynsteer/judges/llm.py`

实现要点：

- `LocalJudge` 改名为 `CheapJudge`。
- `LLMJudge` 只保留 LLM Judge 共用 helper，例如 `_build_prompt(...)`、`_call_json(...)`、`_response_text(...)`、`_result_from_payload(...)`、`_dimension_scores(...)`、`_float_in_unit(...)`、`_string_list(...)`。
- 删除旧 `LLMJudge.evaluate_stage(...)` 中的 cheap/standard/expensive 分发逻辑。
- 不在 `LLMJudge` 中保留 `_evaluate_standard(...)` 与 `_evaluate_expensive(...)`。
- `StandardJudge.evaluate_stage(...)` 在自身函数内实现单轮 prompt 构造、LLM JSON 调用、结果转换，并固定返回 `EvaluationLevel.STANDARD` 结果。
- `ExpensiveJudge.evaluate_stage(...)` 在自身函数内实现多轮聚焦评估、汇总裁决、metadata 记录，并固定返回 `EvaluationLevel.EXPENSIVE` 结果。
- `StandardJudge` 与 `ExpensiveJudge` 可以复用 `LLMJudge` 的共用 helper，但不得新增只调用一个基类方法的一行中转函数。
- 旧测试中的 `LocalJudge` 改为 `CheapJudge`。

测试：

```bash
uv run pytest tests/test_llm_judge.py tests/test_evaluator_runtime.py -q
```

### Task 3：新增 llm 包

文件：

- 新增：`dynsteer/llm/__init__.py`
- 新增：`dynsteer/llm/base.py`
- 新增：`dynsteer/llm/factory.py`
- 新增：`dynsteer/llm/openai.py`
- 新增：`dynsteer/llm/anthropic.py`
- 修改：`dynsteer/harness/config.py`
- 修改：`main.py`

实现要点：

- `OpenaiLLM` 封装 `openai==1.17.0` 支持的 OpenAI-compatible Chat Completions 调用，支持测试注入 fake client。
- `AnthropicLLM` 使用 `httpx.Client`，支持测试注入 fake client，默认 `base_url` 为 `https://api.anthropic.com`。
- `LLMJudge` 只依赖 `BaseLLM.chat(...)`，不直接导入 `openai.OpenAI`。
- `load_judge_config_from_env(...)` 继续输出不包含 API key 明文的配置摘要，并新增可选 `max_tokens`；未配置 `DYNSTEER_JUDGE_MAX_TOKENS` 时值为 `None`。
- `OpenaiLLM.chat(...)` 默认不传 `max_tokens`；只有 `LLMConfig.max_tokens` 为正整数时才传 `max_tokens`。
- 不使用 Responses API、`max_output_tokens` 或新版 Chat Completions 的 `max_completion_tokens`。
- `main.py` 改用 `DynSTEEREvaluator.from_env()`。

测试：

```bash
uv run pytest tests/test_llm_judge.py tests/test_main_evaluator_entry.py -q
```

### Task 4：将 evaluate.py 拆为 evaluate 包

文件：

- 新增：`dynsteer/evaluate/__init__.py`
- 新增：`dynsteer/evaluate/models.py`
- 新增：`dynsteer/evaluate/evaluator.py`
- 新增：`dynsteer/evaluate/weights.py`
- 新增：`dynsteer/evaluate/milestone.py`
- 新增：`dynsteer/evaluate/utils.py`
- 删除：`dynsteer/evaluate.py`
- 删除：`dynsteer/match.py`

实现要点：

- `evaluate_minefields(...)` 从模块函数改为 `DynSTEEREvaluator.evaluate_minefields(...)`。
- `select_evaluation_level(...)` 从模块函数改为 `DynSTEEREvaluator.select_evaluation_level(...)`。
- `_evaluate_stage_with_scheduler(...)` 调用成员 `select_evaluation_level(...)`。
- `enrich_stage_result(...)` 作为 `evaluate/utils.py` 的纯函数，接收 minefield 结果与 thresholds，不直接访问 evaluator 私有状态。
- `ready_milestones(...)`、`find_hit_milestone(...)`、`stage_start_for_milestone(...)` 作为 `evaluate/milestone.py` 函数，由 evaluator 调用。

测试：

```bash
uv run pytest tests/test_evaluator_runtime.py tests/test_harness_public_execution_api.py -q
```

### Task 5：清理冗余和无效引用

文件：

- 修改：`dynsteer/adapter/toolsandbox/harness.py`
- 修改：`dynsteer/score.py`
- 修改：`dynsteer/evaluate/*.py`

清理项：

- 删除 `dynsteer/adapter/toolsandbox/harness.py` 中未使用的 `TrajectoryStep` 导入。
- 删除 `ToolSandboxHarness._run_id(...)`。
- 保留 `ToolSandboxHarness._result_from_toolsandbox(...)`，并在后续确认无外部入口后再删除。
- 统一 `_clamp` 为 `dynsteer.numeric.clamp`。

核查命令：

```bash
rg "from dynsteer\\.judge|dynsteer\\.judges\\.llm|from dynsteer\\.match|_clamp|TrajectoryStep" dynsteer tests docs main.py
```

期望：

- 不再出现 `dynsteer.judge`、`dynsteer.judges.llm`、`dynsteer.match`。
- 不再出现重复 `_clamp` 定义。
- `dynsteer/adapter/toolsandbox/harness.py` 不再导入 `TrajectoryStep`。

### Task 6：依赖固定与锁文件同步

文件：

- 修改：`pyproject.toml`
- 修改：`uv.lock`

实现：

```toml
dependencies = [
    "openai==1.17.0",
    "httpx==0.27.2",
]
```

命令：

```bash
uv lock
uv sync --locked
uv run python -c "import importlib.metadata as m; print(m.version('openai'), m.version('httpx'))"
```

期望输出：

```text
1.17.0 0.27.2
```

兼容性 smoke test：

```bash
uv run python -c "from openai import OpenAI; OpenAI(api_key='test', base_url='https://example.invalid/v1'); print('ok')"
```

期望输出：

```text
ok
```

### Task 7：测试与文档同步

文件：

- 修改：`tests/test_llm_judge.py`
- 修改：`tests/test_evaluator_runtime.py`
- 修改：`tests/test_main_evaluator_entry.py`
- 新增：`tests/test_llm_provider.py`
- 新增：`tests/test_evaluate_package.py`
- 修改：`docs/apis/evaluate.md`
- 新增：`docs/apis/judges.md`
- 新增：`docs/apis/llm.md`
- 修改：`docs/apis/harness.md`

测试覆盖：

- `CheapJudge` 对 milestone score 的本地评估。
- `StandardJudge` 解析单次 LLM JSON。
- `ExpensiveJudge` 记录多轮评估 metadata。
- `OpenaiLLM` fake client 调用不泄露 API key 到 prompt。
- `OpenaiLLM` 在 `max_tokens is None` 时不向 fake client 传 `max_tokens`，在显式配置正整数时才传 `max_tokens`。
- `OpenaiLLM` 基于 `openai==1.17.0` 的 Chat Completions 能力实现，不依赖 `max_completion_tokens` 或 Responses API。
- `AnthropicLLM` fake httpx client 调用不访问真实网络。
- `DynSTEEREvaluator.from_env()` 在未配置 LLM 时只启用 cheap，在配置 provider 时启用 standard/expensive。
- `evaluate_minefields(...)` 与 `select_evaluation_level(...)` 作为成员函数可用。
- `from dynsteer.evaluate import DynSTEEREvaluator, JudgeConfigurationError` 保持可用。
- `uv run python -c "from openai import OpenAI; OpenAI(api_key='test', base_url='https://example.invalid/v1'); print('ok')"` 输出 `ok`，确认 `openai==1.17.0` 与 `httpx==0.27.2` 可成功构造 client。

最终验收命令：

```bash
uv run pytest --cov=dynsteer --cov-report=term-missing
```

最低要求：

- 测试全部通过。
- 核心逻辑覆盖率不低于 80%。
- 无语法错误。
- `main.py --benchmark` 和 `main.py --input` 两条入口仍构造 `DynSTEEREvaluator`。

## 建议落地顺序

1. 先写新 API 的失败测试，覆盖 `CheapJudge`、`StandardJudge`、`ExpensiveJudge`、`BaseLLM`、`DynSTEEREvaluator.from_env()`。
2. 新增 `dynsteer/numeric.py`，统一 `clamp`。
3. 重构 `judges`，删除旧 `judge.py` 与 `judges/llm.py`。
4. 新增 `llm` 包，将 provider 构建与交互从 Judge 中剥离。
5. 将 `evaluate.py` 拆成 `evaluate/` 包，迁移 `match.py`。
6. 清理无效导入和未调用重复函数。
7. 回退并固定 `openai==1.17.0` 与 `httpx==0.27.2`，更新 `uv.lock`。
8. 更新 API 文档。
9. 跑 OpenAI client 构造 smoke test、完整测试和覆盖率。

## 风险与处理

- 删除 `dynsteer.judge`、`dynsteer.judges.llm`、`dynsteer.match` 会破坏外部旧导入。若需要兼容外部调用，应在实施前确认是否允许保留短期弃用 shim。
- `dynsteer/evaluate.py` 改为 `dynsteer/evaluate/` 包时，必须先删除旧文件再新增目录，避免 Windows 文件系统下同名文件和目录冲突。
- `AnthropicLLM` 使用 `httpx` 直接调用，必须通过 fake client 单测覆盖请求体、响应解析和异常路径，避免真实网络依赖。
- 回退到 `openai==1.17.0` 后，`OpenaiLLM` 只能使用该 SDK 已支持的 Chat Completions 能力；不得在本轮实现中使用 Responses API、`max_completion_tokens` 等新版接口。
- `openai==1.17.0` 必须与 `httpx<0.28` 成组固定。若只固定 `openai==1.17.0` 而允许解析到 `httpx==0.28.1`，仍可能触发 `Client.__init__() got an unexpected keyword argument 'proxies'`。
- 后续若要升级 OpenAI SDK，需要先确认 ToolSandbox 是否已放宽 `openai==1.17.0` 硬依赖；否则应将 ToolSandbox 放入隔离 venv、Docker 或子进程环境，避免一个 uv 环境内出现不可满足的依赖组合。
