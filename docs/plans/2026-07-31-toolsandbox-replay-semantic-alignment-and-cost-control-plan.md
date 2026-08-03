# 2026-07-31 ToolSandbox 运行期 expected 与 stage goal 模板化修改方案

## 1. 修改目标与边界

### 1.1 修改目标

每次实验开始时，按本次实验实际使用的 ToolSandbox scenario 更新 `Constraint.expected`，然后用更新后的 expected 渲染 stage goal。适配文件以 `stage_goal_templates` 为 canonical source；`stage_goals` 可以保留最近一次实例化结果用于兼容和诊断，但不得把其中的动态数值当成长期固定文本。

### 1.2 必须保持的语义

1. `Constraint.expected` 仍是 structured scorer 的唯一状态目标来源。
2. `Constraint.stage_goal_semantics["expected"]` 必须与当前运行期的 `Constraint.expected` 同步。
3. `TaskCase.stage_goal_templates` 保存可重复渲染的模板；`TaskCase.stage_goals` 保存当前实验已经实例化的文本。
4. `TaskCase.initial_state` 只表示运行期初始状态，不参与 expected 或 stage goal 的计算。
5. default 与 replay 必须使用同一份已刷新 expected 的 `TaskCase`；replay 不得在 default 之后再次刷新 expected。
6. 不改变 milestone matching、constraint scorer、threshold、weight、passes、policy stop 或 LLM judge 的既有语义。

本方案不新增通用数据格式校验、模板完整性校验、字段类型校验或异常恢复分支；沿用当前严格定义的数据结构。只有 LLM 生成 stage goal 时，检查其是否保留要求的 expected 占位符。

### 1.3 占位符格式

统一使用：

~~~text
[[<constraint_id>.expected]]
~~~

其中尖括号只是文档中的说明符号，实际文本使用具体 constraint ID，例如 `[[m1_c0.expected]]`。占位符替换为该 constraint 当前完整 `Constraint.expected` 的 canonical JSON，适用于 timestamp、日期、金额、字符串、数组以及嵌套对象等所有 JSON 类型，不针对某一种字段设计专用逻辑。

示例：

~~~text
模板：设置或核验 REMINDER 状态满足 [[m1_c0.expected]]。
实例化：设置或核验 REMINDER 状态满足 {"rows": [{"content": "Buy milk", "reminder_timestamp": 1785574800.0}], "columns": ["content", "reminder_timestamp"]}。
~~~

## 2. 数据结构修改

### 2.1 `TaskCase` 增加模板字段

修改 `dynsteer/model.py::TaskCase`，增加：

~~~python
stage_goal_templates: dict[str, str] = field(default_factory=dict)
~~~

字段职责：

| 字段 | 持久化内容 | 运行期内容 |
|---|---|---|
| `milestone_graph.*.constraints[*].expected` | 适配时的兼容值 | 当前实验从 scenario 刷新的 target |
| `Constraint.stage_goal_semantics["expected"]` | 适配时的兼容值 | 与当前 `Constraint.expected` 相同 |
| `stage_goal_templates` | 含 `[[<constraint_id>.expected]]` 的模板 | 不修改 |
| `stage_goals` | 旧文件中的旧渲染值，仅兼容读取 | 当前 expected 实例化后的文本 |
| `initial_state` | 新适配文件写 `null` | default/replay 注入的 runtime state |

`stage_goal_templates` 是新的 canonical source；加载旧 JSON 时不得把旧 `stage_goals` 当成当前实验文本继续使用。

### 2.2 ToolSandbox constraint expected

修改 `dynsteer/adapter/toolsandbox/utils/scenario.py::constraint_from_snapshot_constraint`，保留现有 `expected` 生成逻辑：

~~~text
SnapshotConstraint.target_dataframe
    -> rows_from_dataframe()
    -> Constraint.expected = {"rows": rows, "columns": ...}
~~~

不得从 `initial_state` 反推或覆盖该值。是否需要在 stage goal 中引用 expected，由模板中是否出现 `[[<constraint_id>.expected]]` 决定。

### 2.3 adapted metadata

不新增 expected 专用 metadata。default 与 replay 的一致性由 experiment runner 的调用顺序保证：同一 experiment spec 只刷新一次 TaskCase，随后 default 与 replay 复用该 TaskCase。

## 3. stage goal 模板与实例化代码

### 3.1 修改 `dynsteer/stage/goal.py`

新增常量和接口：

~~~python
EXPECTED_PLACEHOLDER_PATTERN = "[[{constraint_id}.expected]]"

def expected_placeholder(constraint_id: str) -> str:
    return f"[[{constraint_id}.expected]]"

def generate_stage_goal_templates(
    task_case: TaskCase,
    mode: str = "auto",
    llm_provider: Callable[[], BaseLLM] | None = None,
) -> dict[str, str]:
    """生成不包含本次实验具体 expected 值的 stage goal 模板。"""

def materialize_stage_goals(
    task_case: TaskCase,
    templates: dict[str, str] | None = None,
) -> dict[str, str]:
    """用当前 milestone graph 中的 Constraint.expected 实例化 stage goal。"""

~~~

修改现有 `generate_stage_goals()`：保留原有公开接口，但改为：

~~~python
templates = generate_stage_goal_templates(task_case, mode, llm_provider)
task_case.stage_goal_templates = templates
goals = materialize_stage_goals(task_case, templates)
task_case.stage_goals = goals
return goals
~~~

修改 `dynsteer/stage/__init__.py`，显式导出 `generate_stage_goal_templates`、`materialize_stage_goals` 和 `expected_placeholder`，不使用包级懒加载或动态导入。

### 3.2 semantic 模式的具体修改

修改 `dynsteer/stage/goal.py::_constraint_goal_text_from_semantics`：

- `SET_STATE` 语义的 expected 文本统一使用 `expected_placeholder(constraint.constraint_id)`；
- 其他语义只有在其 stage goal 模板需要直接引用 `Constraint.expected` 时才使用同一占位符；
- 不根据字段名、数据类型或是否为 timestamp 增加分支。

`set_state.zh.md` 和 `set_state.en.md` 的 `{expected}` 不需要改名；它们接收的参数从当前 concrete JSON 改为 `[[<constraint_id>.expected]]`。

### 3.3 `materialize_stage_goals()` 的替换规则

1. 遍历 `stage_goal_templates` 中的每个 stage goal。
2. 按 `constraint_id` 查找当前 graph 中的 constraint。
3. 对 `constraint.expected` 执行 `json.dumps(..., ensure_ascii=False, sort_keys=True)`。
4. 替换 `[[<constraint_id>.expected]]`，返回 `stage_goals`。

该函数只读取当前 graph，不访问 `TaskCase.initial_state`、trajectory 或 wall clock；不新增模板结构校验器。

### 3.4 LLM stage goal 模式

修改 `dynsteer/prompt/stage.py::build_stage_goal_generation_prompt`，向 LLM 输入中增加明确规则：

~~~text
凡 stage goal 需要引用某个 constraint 的 expected，必须原样保留 [[<constraint_id>.expected]]；不得把当前 expected 的具体值直接写死到模板中。
~~~

修改 `dynsteer/stage/goal.py::_parse_stage_goal_from_resp`：

- 只在 LLM 返回结果时检查占位符是否仍然存在；若 LLM 把占位符替换成具体值，按现有 LLM 输出错误处理。

## 4. 每次实验刷新 expected 的代码方案

### 4.1 增加 adapter 刷新接口

修改 `dynsteer/adapter/base.py::BaseBenchmarkAdapter`，增加默认实现：

~~~python
def refresh_task_case_for_experiment(
    self,
    config: HarnessRunConfig,
    task_case: TaskCase,
    case_id: str,
) -> TaskCase:
    """按本次实验 source 更新动态 target 和 stage goal。"""
    return task_case
~~~

该接口只修改内存中的 `TaskCase`，不自动写 runtime `initial_state`。

### 4.2 ToolSandbox 实现

修改 `dynsteer/adapter/toolsandbox/adapter.py::ToolSandboxAdapter`，实现 `refresh_task_case_for_experiment()`：

1. 通过共享 `load_named_scenarios(config, load_toolsandbox_module)` 获取 scenario；不得重新直接调用 `named_scenarios()`。
2. 取 `scenarios[case_id]`，调用现有 `milestone_graph_from_scenario(scenario)` 取得当前 graph。
3. 按 constraint ID 对齐当前 scenario graph 与 task case graph，并更新 expected：

~~~python
for current_constraint, source_constraint in matching_constraints:
    current_constraint.expected = deepcopy(source_constraint.expected)
    if _is_set_state(current_constraint):
        current_constraint.stage_goal_semantics["expected"] = deepcopy(current_constraint.expected)
~~~

4. 调用 `materialize_stage_goals(task_case)`，覆盖 `task_case.stage_goals`。
5. 调用现有 `generate_stage_evaluation_specs(task_case)`，确保 specs 与当前 stage goal key 对齐；不改变聚焦维度规则。
6. 强制将 `task_case.initial_state` 置为 `None`；runtime initial state 仍由 harness session 或 default trajectory 注入。

### 4.3 共享 scenario loader

修改 `dynsteer/adapter/toolsandbox/utils/runtime.py`：

~~~python
def load_named_scenarios(
    config: HarnessRunConfig,
    module_loader: Callable[[str], object] = load_toolsandbox_module,
) -> dict[str, object]:
    """返回当前进程、当前 backend/data root 共享的 scenario dictionary。"""

def clear_named_scenarios_cache() -> None:
    """清理测试或新实验边界的 scenario cache。"""
~~~

cache key 至少包含 `data_root.resolve()`、ToolSandbox backend、source root。adapter refresh 和 `ToolSandboxHarness._named_scenarios()` 必须返回同一份 dictionary，保证刷新 expected 使用的 scenario 与 default starting context 来自同一次生成。

### 4.4 loader 与 harness runner 调用顺序

修改 `dynsteer/adapter/loader.py`，新增：

~~~python
def refresh_task_cases_for_experiment(
    config: HarnessRunConfig,
    adapter: BaseBenchmarkAdapter,
    task_cases: list[TaskCase],
) -> list[TaskCase]:
    """在一次实验执行前刷新所有当前 expected 并实例化 stage goals。"""
~~~

每个 case 调用 `adapter.refresh_task_case_for_experiment()`，然后执行：

~~~python
task_case.stage_goals = materialize_stage_goals(task_case)
task_case.stage_evaluation_specs = generate_stage_evaluation_specs(task_case)
~~~

修改 `dynsteer/harness/runner.py::prepare_task_cases`，增加 `refresh_dynamic_targets: bool = True` 参数：

- 先按现有逻辑 `load_task_case()`；
- `refresh_dynamic_targets=True` 时调用 `refresh_task_cases_for_experiment()`；
- 返回的 task cases 已经是本次实验的最终内存版本。

修改 `dynsteer/experiment/runner.py`：

1. 不再在所有 experiment spec 之间复用同一份未刷新的 `benchmark_state`。
2. 每个 experiment spec 开始时调用一次 `prepare_task_cases(..., refresh_dynamic_targets=True)`。
3. 该 spec 的 default 与 replay 共同使用这批刷新后的 task cases。
4. `_prepare_for_run_case()` 只做 `deepcopy`，不得再次刷新 expected。
5. replay 读取 default trajectory 后直接使用同一批 `Constraint.expected`；不得从 replay 当前时刻重新调用 adapter。
6. `DefaultOutputKey` 增加 `experiment_id`，避免不同 experiment spec 误用同一个 default output；不增加 expected digest 校验。

## 5. replay 结果语义与 judge 成本控制

### 5.1 replay 执行摘要

修改 `dynsteer/evaluate/evaluator.py::evaluate_replay`，新增纯报告函数：

```python
def build_replay_execution_summary(
    source_trajectory: Trajectory,
    replay_trajectory: Trajectory,
    termination: EvaluationTerminationState,
    coverage: str,
) -> JsonObject:
    """汇总 replay 虚拟停点、完成度和轨迹变化。"""
```

输出字段固定为：

```json
{
  "virtual_stop_triggered": true,
  "virtual_stop_code": "milestone_no_progress:m2",
  "virtual_stop_step_index": 41,
  "finish_after_virtual_stop": true,
  "final_completion": "none",
  "coverage_basis": "milestone_graph",
  "trajectory_changed": true,
  "source_step_count": 48,
  "replay_step_count": 42,
  "source_snapshot_count": 48,
  "replay_snapshot_count": 42,
  "step_ratio": 0.875,
  "snapshot_ratio": 0.875
}
```

修改 `dynsteer/harness/outputs.py`、`dynsteer/evaluate/settlement.py` 和现有 summary/raw 输出，使 `replay_execution` 同时写入 report、summary、raw summary 和 finish metadata。该摘要只描述执行结果，不改变 virtual stop、milestone matching 或最终评分规则。

### 5.2 final-state 与 whole-trajectory evidence 对齐

修改：

- `dynsteer/evaluate/final.py::_terminal_state_checks`
- `dynsteer/evaluate/settlement.py::finish_settlement`
- `dynsteer/prompt/judge.py`

实现要求：

1. terminal constraint 继续调用现有 structured scorer，不复制 target 解析逻辑。
2. finish metadata 写入 constraint ID、score、actual excerpt、expected excerpt 和 target source。
3. whole-trajectory judge 只引用结构化 evidence；不得把 `default_reference.score/resolved` 当作 replay 判定输入。
4. state constraint 失败时，LLM 不能仅凭自然语言确认把结果改判为 PASS。
5. 不改变既有 threshold、weight、passes 和 routing。

### 5.3 judge 精确去重

新增 `dynsteer/evaluate/judge_cache.py`，提供 case 内、单次 evaluator 生命周期内的缓存：

```python
class JudgeCache:
    def get(self, key: str) -> JsonObject | None: ...
    def put(self, key: str, result: JsonObject) -> None: ...

def judge_context_digest(context: JsonObject) -> str: ...
```

缓存 key 必须包含：`case_id`、`model_id`、judge/prompt 类型、stage/milestone ID、trajectory prefix/boundary、focus dimensions、passes、prompt context digest。以下任一项变化都必须 miss：case、trajectory boundary、dimension、passes、model、prompt context。

修改：

- `dynsteer/judges/standard.py`
- `dynsteer/judges/expensive.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/metrics.py`

在 judge 调用前查 cache，命中时返回深拷贝结果；未命中时调用现有 LLM 并写入 cache。增加 `llm_unique_prompt_count`、`llm_cache_hit_count`、`llm_duplicate_prompt_avoided_count`、`semantic_review_cache_hit_count` telemetry。不得跨 case、跨 trajectory prefix 或跨 experiment spec 复用结果。

## 6. adapted case 读写与兼容处理

### 6.1 `dynsteer/adapter/loader.py`

修改 `parse_task_case()`：读取可选 `stage_goal_templates`；缺失时置为空字典。

修改 `_postprocess_task_case()`：

1. 有 `stage_goal_templates` 时直接使用，不从旧 `stage_goals` 反推模板。
2. 缺少模板时按当前 graph 调用 `generate_stage_goal_templates()`，并标记 `stage_goal_template_schema_version=1`。
3. 每次刷新后都重新执行 `materialize_stage_goals()`。
4. 旧 `stage_goals` 中含旧 expected 时，覆盖为当前 expected 实例化结果。
5. 旧 `initial_state` 允许解析，但不得作为 default/replay fallback；新生成文件写入 `null`。

### 6.2 `save_task_case()` 写入规则

保存 adapted case 时必须包含：

~~~json
{
  "stage_goal_templates": {"...": "...[[<constraint_id>.expected]]..."},
  "stage_goals": {"...": "...当前实验 expected..."},
  "initial_state": null,
  "metadata": {
    "stage_goal_template_schema_version": 1
  }
}
~~~

`stage_goals` 可以保存最近一次实例化结果用于诊断，但加载后必须重新 materialize，不能视为永久值。

## 7. 文件修改清单

### 7.1 必须修改

- `dynsteer/model.py`
- `dynsteer/stage/goal.py`
- `dynsteer/stage/__init__.py`
- `dynsteer/prompt/stage.py`
- `dynsteer/adapter/base.py`
- `dynsteer/adapter/loader.py`
- `dynsteer/adapter/toolsandbox/adapter.py`
- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/adapter/toolsandbox/utils/runtime.py`
- `dynsteer/adapter/toolsandbox/utils/scenario.py`
- `dynsteer/harness/runner.py`
- `dynsteer/experiment/runner.py`
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/evaluate/final.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/judge_cache.py`（新增）
- `dynsteer/judges/standard.py`
- `dynsteer/judges/expensive.py`
- `dynsteer/metrics.py`
- `dynsteer/harness/outputs.py`

### 7.2 API 文档

- `docs/apis/harness.md`：说明运行期 expected 刷新发生在 experiment preparation，initial state 的运行期边界。
- `docs/apis/evaluation.md`：说明 scorer target 来自当前 `Constraint.expected`，stage goal 来自模板实例化。
- `docs/apis/experiment.md`：说明同一 experiment spec 内 default/replay 共用同一份刷新后的 TaskCase。

### 7.3 不修改

- ToolSandbox native evaluator、联系人/reminder selector、timestamp 计算函数。
- `dynsteer/evaluate/scoring.py` 的 operator 语义。
- milestone matching、threshold、weight、passes、policy stop 和 judge rubric。
- `initial_state` 到 `Constraint.expected` 的任何推导逻辑（不得新增）。

## 8. 测试方案

### 8.1 模板渲染

新增 `tests/test_stage_goal_templates.py`：

1. SET_STATE constraint 生成 `[[<constraint_id>.expected]]`。
2. `materialize_stage_goals()` 能替换对象、数组、字符串和数值 expected。
3. stage goal 文本中的 expected 内容来自当前 `Constraint.expected`，不限定字段名称。
4. `initial_state` 改变时，渲染结果不改变。

### 8.2 ToolSandbox target refresh

新增 `tests/test_toolsandbox_dynamic_target_refresh.py`：

1. fake T1/T2 scenarios 具有不同的 expected JSON 内容。
2. refresh 后 `Constraint.expected`、`stage_goal_semantics["expected"]` 和 materialized `stage_goals` 三者同步为 T2。
3. refresh 不读取或修改 runtime state 来计算 expected。
4. adapter refresh 与 harness 使用同一 shared scenario dictionary。

### 8.3 experiment/replay 边界

新增 `tests/test_experiment_target_generation.py`：

1. 每个 experiment spec 只刷新一次 task cases。
2. 同一 spec 的 default/replay 使用同一批刷新后的 task cases。
3. 不同 experiment spec 不会因为 default output cache key 相同而互相复用。
4. replay 不会在 default 完成后再次调用 `named_scenarios()`。

### 8.4 replay/judge 与兼容性回归

1. 旧 adapted JSON 缺少 `stage_goal_templates` 时可加载，并在刷新时生成模板。
2. 旧 adapted JSON 含旧 expected 的 `stage_goals` 时会被当前 expected 覆盖。
3. 旧 adapted JSON 含 `initial_state` 时可解析，但不作为 runtime fallback。
4. 现有 stage goal/spec、scorer、matching、judge 测试全部通过。
5. replay summary 覆盖虚拟停点但未缩短、虚拟停点且缩短、完整回放三种情形。
6. 相同 judge context 只调用一次 LLM；prefix、boundary、dimension、passes 变化时必须 miss。
7. 执行：

~~~text
uv run pytest -q
uv run python -m compileall -q dynsteer tests
~~~

## 9. 实施顺序

1. 增加 `TaskCase.stage_goal_templates` 与 loader 兼容解析。
2. 实现 placeholder、template generation 和 materialization。
3. 让 ToolSandbox adapter 在每次 experiment preparation 更新当前 expected。
4. 实现 shared scenario loader 和 adapter refresh 接口。
5. 修改 experiment preparation，使每个 spec 刷新一次，并让 default/replay 复用同一 task case。
6. 更新 API 文档和测试。
7. 执行全量测试、compileall 和静态检查。

## 10. 验收标准

1. 新实验开始后，`Constraint.expected` 取自本次 shared ToolSandbox scenario 的 `target_dataframe`。
2. `stage_goal_templates` 中不保存某次实验的具体 expected 值；`stage_goals` 在刷新 expected 后才实例化。
3. `Constraint.expected`、`stage_goal_semantics["expected"]`、stage goal 中的占位符实例化结果保持一致。
4. `initial_state` 不参与 expected 或 stage goal 生成。
5. 同一 experiment spec 的 default/replay 使用同一批刷新后的 TaskCase；不同 experiment spec 不复用 default output。
6. 旧 adapted case 可以迁移，且不会继续使用旧 stage goal 或旧 initial state 作为运行依据。
7. 除动态 target 与 stage goal 实例化外，既有评分和策略语义不变。




