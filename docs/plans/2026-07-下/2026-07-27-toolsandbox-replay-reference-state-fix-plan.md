# 2026-07-27 ToolSandbox replay 参考状态修复方案

## 1. 问题结论

结合 [default vs replay 核查报告](./analysis/2026-07-27-toolsandbox-partial-main-default-vs-replay-audit-report.md)，当前 replay 和 default 的大幅差异，最大共性不是语义 judge 宽松，而是 **structured scorer / adapted case 依赖了不可信的参考状态**。

典型表现有两类：

1. `add_contact_with_name_and_phone_number_3_distraction_tools`：`reference_milestone_node_index=-1` 的 `preserve_state` 约束被 replay 解释成空表期望，`expected_rows=0`，因此 `REMINDER/MESSAGING` 直接被打成 `0.0`。
2. `modify_reminder_with_recency_latest`：adapted case 里冻结了过期的绝对时间戳，replay 用 `1784883600.0` 去比对运行时正确结果 `1785229200.0`，差 4 天。

所以本次修复要同时解决两件事：

- `preserve_state` 必须按 runtime reference snapshot 评分，不能拿空 `expected.rows` 当真实目标。
- ToolSandbox 的 adapted case 不能长期复用过期的动态值，尤其是 reminder recency 这类时间敏感目标。

## 2. 修复目标

1. replay 不再把 `preserve_state` 误判成“目标为空表”。
2. `--force_adapt` 仍作为唯一的 adapted case 重建开关；同一次实验中 default、dynsteer-replay 等方法必须使用同一批 TaskCase 模板。
3. 诊断文本不再把 `expected_rows=0` 伪装成真实的状态目标。
4. 不改 semantic message review 的宽松策略，只修 reference state 绑定和 replay 评分输入。
5. 不在 `settlement.py` 做兜底，不在结算层覆盖 structured scorer 的结果。
6. 实验矩阵中的 TaskCase 准备只按 benchmark 名称发生一次，不随 method、model、judge、threshold、repeat 反复加载或重新适配。
7. `HarnessRunConfig.case_ids` 只用于加载阶段的 case 选择；进入单 case 执行后，case 标识统一来自 `task_case.case_id`，不再为了通过校验反复构造单 case config。
8. 移除 `dynsteer.evaluate` 包内 `__getattr__` 懒加载式 re-export，改成显式顶层 import，保证 IDE 跳转、静态分析和 DEBUG 正常。
9. 同步清理当前代码里同类复杂化写法：单行中转函数、重复 config 构造、包级懒加载、以及为了校验而制造的数据改写。

## 3. 不改动范围

以下部分不作为本轮修复重点：

- `dynsteer/evaluate/settlement.py`：不新增结构化评分兜底逻辑。
- `dynsteer/stage/goal.py`：stage goal 的 reference 文本已经正确表达 `initial_state` / `milestone_index`，不需要重写。
- `dynsteer/evaluate/runtime.py`：`ScoringContext` 已经保存了 `initial` snapshot，不需要再改运行期上下文模型。
- `dynsteer/evaluate/semantic.py` 里的 message semantic review 逻辑：本轮不动消息语义宽松策略，只调 state 证据呈现方式。

## 4. 详细代码修改方案

### 4.1 `dynsteer/adapter/base.py`

上一版方案建议新增 `BaseBenchmarkAdapter.should_force_readapt()`，这个建议取消。

原因：

1. 当前项目已经通过 `--force_adapt` 表达“本次实验是否重建 adapted_cases”。
2. adapted_cases 是同一次实验的共享输入，不应该由某个 adapter 在 default、dynsteer-replay 等方法之间隐式改变。
3. 如果 ToolSandbox 每次加载都自动重新适配，反而会破坏同一实验矩阵里不同方法使用同一份 TaskCase 的公平性。

因此 `dynsteer/adapter/base.py` 不需要新增 freshness hook。

### 4.2 `dynsteer/adapter/loader.py`

`load_task_case(config, adapter, force_adapt=False)` 的语义保持不变：

- `force_adapt=False`：优先读取 `data/{benchmark}/adapted_cases/<case_id>.json`；文件缺失或结构不完整时才适配并落盘。
- `force_adapt=True`：忽略已有 adapted case，重新调用 adapter 适配并覆盖落盘文件。

需要补充的不是 adapter 自主重适配，而是把这个语义在实验流程中用对：**所有方法执行前，先统一加载或生成 TaskCase 模板；后续 default、dynsteer-replay、dynsteer-evaluate 等方法都从这批模板深拷贝。**

建议只做轻量代码整理：

1. 保持 `load_task_case()` 的现有入参和行为。
2. 在函数注释和 API 文档里强调 `force_adapt` 是“本次加载阶段是否重建 adapted_cases”的统一开关。
3. 不新增 benchmark 专属自动重建逻辑。

### 4.3 `dynsteer/experiment/runner.py`

统一实验流程必须明确拆成两步，并且第一步不能继续挂在每个 `ExperimentRunSpec` 上执行。
当前代码先 `expand_experiment_matrix(config)` 得到 benchmark × model × method × repeat × threshold 的三维/多维矩阵，再在每个 spec 上调用 `_load_task_cases_cached(...)`。
这个结构不合理；修复时直接删掉缓存封装，不再用另一个 key 体系补救：

- TaskCase/adapted_cases 是对比实验的输入变量，应该只由 benchmark 决定。
- method、model、judge、threshold、repeat 是评估策略变量，不应该触发加载或重建 adapted_cases。
- `--force_adapt=True` 时，如果在 spec 循环中加载，会把“重建 adapted_cases”的副作用放到模型/方法循环里，直接破坏控制变量。
- 这里不需要 `_prepare_task_case_templates_by_benchmark()`，也不需要 `_task_case_prepare_key()`；一个 `dict[str, tuple[TaskCase, ...]]` 就够了。

因此应删除 `_load_task_cases_cached(...)`、`_task_case_cache_key(...)` 和 `_TASK_CASE_CACHE_METADATA_EXCLUDE_KEYS`，在 `run_experiment()` 里直接用 benchmark 名称加载一次。

第一步：基于 benchmark 名称准备 TaskCase 模板。

```python
task_cases_by_benchmark: dict[str, tuple[TaskCase, ...]] = {}
for spec in specs:
    if spec.benchmark in task_cases_by_benchmark:
        continue
    harness_config = build_harness_config(spec)
    task_cases_by_benchmark[spec.benchmark] = tuple(
        _load_task_cases(harness_config, force_adapt=force_adapt)
    )
```

这里的约束很简单：

- 字典键就是 `spec.benchmark`。
- 字典值就是该 benchmark 对应的 `tuple[TaskCase, ...]` 模板。
- `force_adapt=True` 时，某个 benchmark 的重建动作最多发生一次。

第二步：对同一批 TaskCase 模板按方法执行评估。

```python
total_case_runs = sum(len(task_cases_by_benchmark[spec.benchmark]) for spec in specs)

for spec in specs:
    for task_case_template in task_cases_by_benchmark[spec.benchmark]:
        task_case = copy.deepcopy(task_case_template)
        if spec.method == ExperimentMethod.DEFAULT:
            run_default_case(spec, task_case)
        elif spec.method == ExperimentMethod.DYNSTEER_REPLAY:
            run_replay_case(spec, task_case, default_output, default_reference)
```

这里必须保留对 `task_case_template` 的深拷贝，因为 default/evaluate/replay 都可能在运行时写入 `runtime_initial_state`、metadata 或评估中间态；深拷贝是为了隔离运行副作用，不是为了重新加载 adapted_cases。

修改后效果：

- default 和 replay 使用同一批 adapted_cases。
- `--force_adapt` 的影响范围是整个本次实验的 TaskCase 输入，而不是单个 method。
- 同一 benchmark 在同一次实验中只加载或适配一次，避免反复读写 adapted case 文件。
- replay 评分差异只来自 evaluator 策略和 scorer，不来自两份不同的适配数据。

#### 4.3.1 `dynsteer/experiment/config.py` 与单 case config 冗余

当前 `config_for_case(config, case_id)` 的做法是把 `config.case_ids` 改写成 `(case_id,)`。这一步在单 case 执行阶段没有实际价值：

- `DynSTEEREvaluator.evaluate()` 和 `evaluate_replay()` 已经通过 `task_case.case_id` 获取当前 case。
- `write_default_case_outputs()`、`write_case_outputs()`、`write_replay_case_outputs()` 也都可以直接通过 `task_case.case_id` 写路径和摘要。
- 如果 evaluator 内部还需要依赖 `config.case_ids == (task_case.case_id,)` 才能通过校验，说明校验本身就是冗余的，应删除校验，而不是反过来构造冗余 config。

建议修改：

1. `dynsteer/experiment/runner.py`
   - 移除 `config_for_case` import。
   - `run_default_case()`、`run_evaluate_case()`、`run_replay_case()` 中不再调用 `config_for_case(...)`。
   - 删除 `_config_for_method()`，在 `run_default_case()` 中直接构造 default config。
   - 单 case 执行函数统一把完整 `HarnessRunConfig` 传给 outputs/evaluator，case_id 只从 `task_case.case_id` 读取。

2. `dynsteer/experiment/config.py`
   - 如果 `rg "config_for_case"` 确认没有其他有效调用，删除 `config_for_case()`。
   - 如果为了过渡需要保留，也应在方案落地时明确不再由 experiment runner 调用；后续再清理。

3. `dynsteer/evaluate/evaluator.py`
   - 不新增任何基于 `config.case_ids` 的单 case 校验。
   - `evaluate()` / `evaluate_replay()` 的第一行继续使用 `case_id = task_case.case_id`。
   - `_replay_run_id(config, trajectory, case_id)` 保持接收显式 `case_id`，不要从 config 反查。

4. `dynsteer/harness/outputs.py`
   - 保持输出路径、summary、report 全部以 `task_case.case_id` 为准。
   - `write_replay_case_outputs()` 中只允许通过 `replace(config, metadata=metadata)` 合并 default reference，不要改写 `case_ids`。

加载阶段仍然可以保留 `_load_task_cases()` 中的 `replace(config, case_ids=tuple(case_ids))`，因为 `load_task_case()` 当前入口就是通过 `config.case_ids` 接收“本次要加载的一批 case”。这属于 TaskCase 准备阶段，不属于单 case 运行阶段的冗余。

### 4.4 `dynsteer/harness/runner.py` 与 `main.py`

单 benchmark 运行也应遵循同样的两阶段语义：

1. `main.py` 解析 `--force_adapt`。
2. `run_harness_configs(..., force_adapt=args.force_adapt)` 在执行任一 case 前调用 `load_task_case(...)`。
3. `load_task_case(...)` 根据 `force_adapt` 决定是否重建 adapted_cases。
4. 后续 evaluator 只消费已经加载好的 `TaskCase`。

当前代码总体已经按这个方向实现，因此不建议大改。只需要在文档和必要注释里澄清：`--force_adapt` 控制的是加载阶段的 adapted_cases 产物，不是 replay 评分阶段的动态行为。

### 4.5 `dynsteer/harness/outputs.py`

为避免 replay 的 `reference_milestone_node_index=-1` 绑定到过期 adapted initial state，Default 运行产物必须记录该次 session 的真实 runtime initial state。

建议在 `write_default_case_outputs()` 的 `harness.start_case(...)` 之后新增：

```python
runtime_initial_state = harness.initial_state_from_session(session)
if runtime_initial_state is not None:
    task_case.initial_state = runtime_initial_state
    task_case.metadata["runtime_initial_state_source"] = "harness_session"
    trajectory.raw["runtime_initial_state"] = runtime_initial_state
```

并在 `raw_summary` 中补充轻量摘要。这里不要直接从 `harness/outputs.py` 调用
`dynsteer/evaluate/evaluator.py` 里的私有 `_state_namespace_summary()`；更合适的做法是把该逻辑抽成共享 helper
（例如 `dynsteer/evaluate/state_summary.py::state_namespace_summary()`），再由 evaluator 和 harness outputs 共同调用。
如果希望进一步压缩改动，也可以先在 `harness/outputs.py` 内实现一个同等规则的小型本地 helper。

```python
raw_summary["runtime_initial_state_source"] = "harness_session"
raw_summary["runtime_initial_state_summary"] = state_namespace_summary(runtime_initial_state)
```

修改后效果：

- replay 不需要重新打开 ToolSandbox session，也能拿到 default 轨迹对应的真实初始状态。
- `add_contact...` 这类 preserve-state reference 不会因为 adapted JSON 中的旧初始值而漂移。
- 轨迹文件仍是 default/replay 共享的事实输入。

### 4.6 `dynsteer/experiment/runner.py::run_replay_case`

replay 读取 default trajectory 后，应先把 default runtime initial state 注入本次 replay 使用的 `task_case`。

建议在 `load_trajectory(...)` 后新增：

```python
runtime_initial_state = trajectory.raw.get("runtime_initial_state")
if isinstance(runtime_initial_state, dict):
    task_case.initial_state = runtime_initial_state
    task_case.metadata["runtime_initial_state_source"] = "default_trajectory"
```

如果后续希望接口更显式，也可以把 `evaluate_replay()` 的入参扩展为：

```python
def evaluate_replay(
    self,
    task_case: TaskCase,
    trajectory: Trajectory,
    scorer: GeneralScorer,
    config: HarnessRunConfig,
    runtime_initial_state: JsonObject | None = None,
) -> HarnessRunResult:
    ...
```

但最小改法是不改 `evaluate_replay()` 签名，只在 `run_replay_case()` 调用前修正 `task_case.initial_state`。这样改动范围更小，也符合当前代码结构。

### 4.7 `dynsteer/adapter/toolsandbox/scorer.py`

这是本次最核心的评分修复点。现在的问题不是 `reference_snapshot` 不存在，而是 **preserve_state 仍然拿 serialized empty expected 去打分**。

建议改法：

1. 在 `_score_toolsandbox_snapshot_constraint()` 里新增一个“目标数据源解析”步骤。
2. 当 `constraint.stage_goal_semantics.kind == "preserve_state"` 时，`target_dataframe` 不再来自 `constraint.expected`，而是来自 `context.matched_snapshots` 解析出来的 reference snapshot。
3. 当 `reference_milestone_node_index == -1` 时，继续沿用 `ScoringContext["initial"]` 作为初始参考快照。
4. 其他 `set_state` / `emit_message` / `tool_call` 约束仍然走现有 `constraint.expected`。

建议的结构形态：

```python
reference_snapshot, reference_summary = self._reference_dataframe(constraint, context)
target_dataframe = self._resolved_target_dataframe(constraint, namespace, reference_snapshot)
score = float(
    measure(
        snapshot=snapshot,
        target_dataframe=target_dataframe,
        column_similarities=column_similarities,
        reference_snapshot=reference_snapshot,
        **kwargs,
    )
)
```

`_resolved_target_dataframe()` 的规则应是：

- `preserve_state`：优先使用 reference snapshot 的 namespace rows。
- 其他语义：继续使用 `constraint.expected`。

同时建议把 `_reference_evidence()` 补成更明确的证据行，至少要体现：

- `reference_snapshot_id`
- `reference_milestone_id`
- `namespace`
- `row_count`
- `target_source=reference_snapshot` 或 `target_source=constraint.expected`

修改后效果：

- `add_contact...` 不会再把 `m0_c1/m0_c3` 误判成空表失败。
- `reference_milestone_node_index=-1` 会明确落到 runtime initial snapshot。
- preserve_state 的 replay 评分和 default 的成功/失败判断会重新对齐。

### 4.8 `dynsteer/evaluate/semantic.py`

这里不改 message review，只改 state 类约束的 excerpt 呈现，让诊断文本不再把空 `expected.rows` 误读成“真实目标为空”。

建议修改：

1. `constraint_expected_excerpt()`：
   - 当 `stage_goal_semantics.kind == "preserve_state"` 时，不再直接 compact `{"rows": [], "columns": []}`。
   - 改为返回短语义摘要，例如 `preserve_state(reference=initial_state)` 或 `preserve_state(reference=milestone_index:0)`。
2. `constraint_actual_excerpt()` / `_focused_state_excerpt()`：
   - 对 `preserve_state` 约束，前缀中必须带 reference 标签。
   - 不要让 `expected_rows=0` 看起来像真实目标，而应让它成为“参考态由 runtime 解析”的提示。

建议输出形态：

```text
reference=initial_state; actual_rows=3; relevant_actual_rows=[...]
```

或者：

```text
reference=milestone_index:0; actual_rows=5; relevant_actual_rows=[...]
```

修改后效果：

- `expected_excerpt` 不会再像空表。
- `actual_excerpt` 会明确告诉人：这是参考态比较，不是空表比较。
- 诊断和 prompt 会更接近真实评估语义。

### 4.9 `dynsteer/evaluate/diagnostics.py`

`diagnostics.py` 负责把失败写成能看懂的文字。这里要跟着 `semantic.py` 的新 excerpt 走，但不要新增大字段。

建议修改：

1. `_constraint_goal_hint()`：
   - `preserve_state` 时补上 reference 信息。
   - 例如 `Need to preserve REMINDER state relative to reference milestone index -1.`
2. `_constraint_failure_detail()`：
   - 当 `semantic_kind == "preserve_state"` 且 `expected_rows=0` 时，失败线里要明确提示“这是 reference baseline，不是空表目标”。
   - 继续保留现有 `actual_excerpt` / `expected_excerpt` 字段，不扩大 JSON 体积。

修改后效果：

- `add_contact...` 的失败线会直接暴露“参考态没绑对”，而不是只看到空 expected。
- 人工核查时不会再把占位空表误认为真实期望。

### 4.10 `docs/apis/harness.md`、`docs/apis/experiment.md`、`docs/apis/evaluate.md` 与 `docs/apis/llm.md`

API 文档需要同步澄清新的流程语义。

建议改动：

- `docs/apis/experiment.md`：明确 experiment runner 应先按 benchmark 名称加载或生成 TaskCase 模板，再分 method/model/judge/repeat 执行。
- `docs/apis/harness.md`：明确 `--force_adapt` 是 adapted_cases 是否重建的唯一显式开关；`HarnessRunConfig.case_ids` 只用于加载阶段选择 case，不是 evaluator 单 case 身份来源。
- `docs/apis/evaluate.md`：明确 `DynSTEEREvaluator.evaluate()` / `evaluate_replay()` 的当前 case 来自 `task_case.case_id`，不要求 `config.case_ids` 被改写成单元素元组。
- `docs/apis/llm.md`：明确 `dynsteer.llm` 包级导出使用显式 import，不通过 `__getattr__` 懒加载隐藏 provider/factory 依赖。
- 明确 Default 轨迹应携带 runtime initial state，Replay 评分应优先使用 default trajectory 的 runtime initial state。
- 将 `docs/apis/harness.md` 中“ToolSandbox adapter 通过懒加载导入 `tool_sandbox`”这类说法改成“ToolSandbox 外部可选依赖由专门依赖边界工具函数加载”，避免和项目自身包级懒加载混淆。

### 4.11 `data/toolsandbox/adapted_cases/*.json`

这里不写成由本方案执行的操作，只作为用户侧验收前置说明。

需要注意：

1. 旧的 `data/toolsandbox/adapted_cases/*.json` 已经包含过期 timestamp 和空 expected 占位，不适合作为修复后实验输入。
2. 用户在验收前应自行决定是否通过 `--force_adapt` 重建这些 adapted case。
3. 代码修复的职责是保证：一旦用户选择 `--force_adapt`，default、replay 等方法会使用同一批新生成 TaskCase；如果用户不选择 `--force_adapt`，系统仍按现有缓存语义复用旧 adapted case。

### 4.12 `dynsteer/evaluate/__init__.py` 与 `dynsteer/evaluate/matching/__init__.py`

当前 `dynsteer.evaluate.__init__` 和 `dynsteer.evaluate.matching.__init__` 使用 `__getattr__ + importlib.import_module` 做懒加载式 re-export。
这会导致 IDE 无法稳定跳转、静态分析无法识别真实依赖，也违反 `docs/constraints/code.md` 中“禁止懒加载”的约束。

修复原则：

1. 包级 `__init__.py` 必须改成显式顶层 import。
2. 包内部模块不要再通过 `from dynsteer.evaluate import ...` 反向导入自己的包级 re-export，应全部改成从具体文件导入。
3. 只保留 `__all__`，删除 `_EXPORTS`、`__getattr__()`、`__dir__()` 和 `import_module`。

循环导入核查结论：

1. 如果只把 `dynsteer/evaluate/__init__.py` 改成显式 re-export，而不改包内导入，会出现循环：
   - `dynsteer.evaluate -> dynsteer.evaluate.evaluator -> dynsteer.evaluate`
   - `dynsteer.evaluate -> dynsteer.evaluate.settlement -> dynsteer.evaluate`
2. 如果只把 `dynsteer/evaluate/matching/__init__.py` 改成显式 re-export，而不改包内导入，会出现循环：
   - `dynsteer.evaluate.scoring -> dynsteer.evaluate.matching -> dynsteer.evaluate.matching.milestone -> dynsteer.evaluate.scoring`
   - `dynsteer.evaluate.scoring -> dynsteer.evaluate.matching -> dynsteer.evaluate.matching.minefield -> dynsteer.evaluate.scoring`
3. `dynsteer.llm` 当前没有发现项目内部循环导入；它的主要风险是显式导入 `AnthropicLLM` / `OpenaiLLM` 后会立即暴露实验环境里第三方 SDK 是否安装完整。这个问题应通过 `uv sync --locked` 或修正依赖环境解决，不允许再用 `__getattr__` 懒加载遮住。

因此落地顺序必须固定为：**先改包内模块的具体 import，再改包级 `__init__.py` 的显式 re-export，最后跑 import 测试**。

第一步，先把包内反向导入改成具体模块导入：

```python
# dynsteer/evaluate/evaluator.py
from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    pending_milestone_stage_results,
    runtime_diagnostics_summary,
    task_case_snapshot,
)
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_step_minefields
from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import initialize_milestone_frontier
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
```

```python
# dynsteer/evaluate/settlement.py
from dynsteer.evaluate.runtime import JudgeConfigurationError
from dynsteer.evaluate.matching.frontier import advance_milestone_frontier, ready_milestones
from dynsteer.evaluate.matching.milestone import stage_start_for_ready_milestone
```

```python
# dynsteer/evaluate/runtime.py
from dynsteer.evaluate.matching.boundary import boundary_snapshot
```

```python
# dynsteer/evaluate/scoring.py
from dynsteer.evaluate.matching.boundary import boundary_snapshot, boundary_step
```

```python
# dynsteer/evaluate/final.py
from dynsteer.evaluate.matching.boundary import boundary_snapshot
```

```python
# dynsteer/evaluate/step.py
from dynsteer.evaluate.matching.boundary import candidate_boundary_for_current_step
from dynsteer.evaluate.matching.frontier import ready_milestone_ids
from dynsteer.evaluate.matching.milestone import analyze_milestone_step
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
```

第二步，再把 `dynsteer/evaluate/matching/__init__.py` 改为显式 re-export：

```python
from dynsteer.evaluate.matching.boundary import (
    boundary_snapshot,
    boundary_step,
    candidate_boundary_for_current_step,
)
from dynsteer.evaluate.matching.frontier import (
    advance_milestone_frontier,
    blocked_candidate_milestones,
    initialize_milestone_frontier,
    ready_milestone_ids,
    ready_milestones,
)
from dynsteer.evaluate.matching.milestone import (
    analyze_milestone_step,
    milestone_scoring_step,
    stage_start_for_ready_milestone,
)
from dynsteer.evaluate.matching.minefield import evaluate_minefields_at_boundary
```

第三步，再把 `dynsteer/evaluate/__init__.py` 改为显式 re-export：

```python
from dynsteer.evaluate.runtime import (
    HarnessTeardownError,
    JudgeConfigurationError,
    blocked_milestone_termination_reason,
    pending_milestone_stage_results,
    ready_frontier_no_progress_termination_reason,
    runtime_diagnostics_summary,
    scoring_context,
    selected_candidate_from_attempt,
    task_case_snapshot,
)
from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement
from dynsteer.evaluate.step import evaluate_agent_step, evaluate_step_minefields
from dynsteer.evaluate.evaluator import DynSTEEREvaluator
```

第四步，修改 `dynsteer/llm/__init__.py`：

```python
from dynsteer.llm.base import BaseLLM, LLMConfigurationError, LLMResponseError
from dynsteer.llm.openai import OpenaiLLM
from dynsteer.llm.anthropic import AnthropicLLM
from dynsteer.llm.factory import build_llm, build_llm_from_config, build_llm_from_env
from dynsteer.model import LLMConfig, LLMMessage
```

如果这里因为 `anthropic` 或 `openai` SDK 缺失失败，应修复 uv 环境或依赖声明，不要把 provider 类重新藏回懒加载。
本轮修复范围至少覆盖 `dynsteer.evaluate`、`dynsteer.evaluate.matching` 和 `dynsteer.llm.__init__`；包级 `__init__.py` 不允许再使用懒加载式 re-export。

### 4.13 当前代码复杂化问题核查与精简清单

本次补充核查了 `dynsteer/experiment`、`dynsteer/harness`、`dynsteer/evaluate`、`dynsteer/llm` 中的流程胶水层。
需要负责清理的不是算法本身，而是那些把简单数据流写成多层中转、缓存键或懒加载的代码。

#### 4.13.1 `dynsteer/experiment/runner.py`

必须精简：

1. 删除 `_TASK_CASE_CACHE_METADATA_EXCLUDE_KEYS`、`_load_task_cases_cached()`、`_task_case_cache_key()`。
   - 直接在 `run_experiment()` 里维护 `task_cases_by_benchmark: dict[str, tuple[TaskCase, ...]]`。
   - 每个 benchmark 第一次出现时调用 `_load_task_cases(...)`；后续 spec 直接按 `spec.benchmark` 取模板并 `copy.deepcopy(...)`。
2. 删除 `prepared_specs`。
   - `prepared_specs` 只是为了缓存方案服务；改成 benchmark 字典后没有存在价值。
   - `total_case_runs` 直接用 `sum(len(task_cases_by_benchmark[spec.benchmark]) for spec in specs)`。
3. 删除 `_config_for_method(...)`。
   - `run_default_case()` 中直接写：
     ```python
     default_spec = spec if spec.method == ExperimentMethod.DEFAULT else replace(spec, method=ExperimentMethod.DEFAULT)
     config = build_harness_config(default_spec)
     ```
   - 不再把 metadata 拿出来合并一遍，也不再通过 `config_for_case()` 改写 `case_ids`。
4. `run_replay_case()` 只构造一次 config。
   - 当前代码同时调用两次 `build_harness_config(spec)`，应改成：
     ```python
     config = build_harness_config(spec)
     evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
     return write_replay_case_outputs(config=config, ...)
     ```
5. `run_evaluate_case()` 只构造一次 config，并直接传给 evaluator/output。
   - 不再调用 `config_for_case(...)`。

这些修改的共同原则是：runner 只做两件事，先按 benchmark 准备 TaskCase，再按 spec 执行方法；不要在中间制造额外缓存层、单 case config 层或 metadata 重组层。

#### 4.13.2 `dynsteer/experiment/config.py`

必须精简：

1. 删除 `config_for_case(config, case_id)`。
   - 它只是 `replace(config, case_ids=(case_id,))` 的单行中转。
   - 单 case 执行阶段不应再修改 `config.case_ids`。
2. 删除后同步移除 `from dataclasses import replace` 中仅为 `config_for_case()` 服务的依赖；如果文件里其他函数仍使用 `replace`，则保留。

#### 4.13.3 `dynsteer/harness/selection.py`、`runner.py`、`scheduler.py`

必须精简：

1. 删除 `config_with_case_ids(config, case_ids)`。
   - 它也是 `replace(config, case_ids=tuple(case_ids))` 的单行中转。
2. `dynsteer/harness/runner.py`
   - 加载阶段确实需要把 `select_case_ids(...)` 的结果写入 config 传给 `load_task_case(...)`，这里直接使用 `replace(config, case_ids=tuple(case_ids))`。
   - 这是加载阶段的批量 case 选择，不是单 case 执行阶段的 config 改写。
3. `dynsteer/harness/scheduler.py`
   - `_run_case()` 中删除：
     ```python
     case_config = config_with_case_ids(task.config, [task.case_id])
     return write_case_outputs(case_config, harness, evaluator, task.task_case, progress_reporter)
     ```
   - 改为：
     ```python
     return write_case_outputs(task.config, harness, evaluator, task.task_case, progress_reporter)
     ```
   - 当前 case 身份已经在 `task.task_case.case_id` 中，不需要再写入 `config.case_ids`。
4. `validate_loaded_task_cases(...)` 暂时保留。
   - 它不是单行中转，而是加载阶段确认顺序的直接校验。
   - 后续如果 `load_task_case()` 改为显式接收 `case_ids`，再考虑把该校验合并进 loader。

#### 4.13.4 `dynsteer/evaluate/__init__.py`、`matching/__init__.py`、`dynsteer/llm/__init__.py`

必须精简：

1. `dynsteer.evaluate.__init__` 和 `dynsteer.evaluate.matching.__init__` 按 `4.12` 改成显式顶层 import。
   - 已核查：如果只改 `__init__.py`，会暴露 `evaluate -> evaluator/settlement -> evaluate` 以及 `scoring -> matching -> milestone/minefield -> scoring` 两类循环导入。
   - 因此这不是“显式 import 写法稍微调整”的问题，而是必须先把包内 `from dynsteer.evaluate import ...` 和 `from dynsteer.evaluate.matching import ...` 全部改成具体子模块 import。
2. `dynsteer.llm.__init__` 也要删除 `_EXPORTS`、`__getattr__()`、`__dir__()`、`import_module`。
   - 直接导入 `BaseLLM`、`LLMConfigurationError`、`LLMResponseError`、`OpenaiLLM`、`AnthropicLLM`、`build_llm`、`build_llm_from_config`、`build_llm_from_env`。
   - 当前没有发现 `dynsteer.llm` 内部循环；若显式导入后失败，优先修复实验环境的 SDK 安装，不要恢复懒加载。
3. `dynsteer.llm.factory.build_llm()` 里当前存在 provider 类的函数内 import。
   - 如果当前项目依赖中已经固定安装 `openai` 与 `anthropic`，则把 `OpenaiLLM`、`AnthropicLLM` 移到文件顶层导入。
   - 如果后续决定保留第三方 SDK 可选依赖边界，必须在注释中明确说明这是外部 provider 依赖边界，不是项目自身模块懒加载；但包级 `__init__.py` 仍不允许懒加载。

#### 4.13.5 暂不作为复杂化清理对象

以下代码虽然也使用动态导入或缓存，但不是 `4.3` 这类简单问题复杂化：

- `dynsteer/adapter/registry.py`：按 benchmark 名称加载 adapter/harness，属于插件注册边界。
- `dynsteer/adapter/toolsandbox/utils/runtime.py`：加载外部 ToolSandbox 包，属于外部 benchmark 依赖边界。
- `dynsteer/adapter/toolsandbox/harness.py` 的 `_NAMED_SCENARIOS_CACHE`：缓存外部 scenario 构造结果，避免重复加载外部数据；这不是实验矩阵 TaskCase 控制变量问题。
- `dynsteer/harness/scheduler.py` 的并发与进度队列：虽然代码较长，但承担真实并发调度和进度汇聚职责，不应在本轮 replay reference 修复中混入重写。

## 5. 建议测试

建议新增以下窄测试，优先覆盖 TaskCase 准备、runtime initial state 传递、scorer、diagnostics/excerpt、包级 import 几层：

1. `tests/experiment/test_task_case_prepare.py`
   - 构造同一 benchmark 下多 method、多 model、多 repeat 的实验矩阵，mock `_load_task_cases()`，断言只按 benchmark 名称调用一次。
   - 断言 `force_adapt=True` 只影响统一准备阶段，不会因 method/model/judge/threshold/repeat 变化二次适配。
   - 断言 `run_experiment()` 内只维护 `task_cases_by_benchmark[benchmark]`，不再存在 `_load_task_cases_cached()`、`_task_case_cache_key()` 或 `_task_case_prepare_key()`。
   - 断言传给不同 method 的 `TaskCase` 是同一模板的深拷贝，运行期改写 metadata 不会污染模板或其他 method。
2. `tests/experiment/test_single_case_config.py`
   - 断言 `run_default_case()`、`run_evaluate_case()`、`run_replay_case()` 不再调用 `config_for_case()`。
   - 断言 `run_replay_case()` 对每个 spec 只构造一次 `HarnessRunConfig`。
   - 断言 `_config_for_method()` 已删除，default method config 由 `run_default_case()` 直接构造。
   - 断言 evaluator/outputs 使用 `task_case.case_id` 生成路径和摘要，不依赖 `config.case_ids == (case_id,)`。
3. `tests/harness/test_case_config_simplification.py`
   - 断言 `config_with_case_ids()` 已删除。
   - 断言 `_run_case()` 直接把 `task.config` 传给 `write_case_outputs()`。
   - 断言 `_run_config()` 只在加载阶段直接使用 `replace(config, case_ids=tuple(case_ids))`。
4. `tests/harness/test_default_runtime_initial_state.py`
   - 断言 default trajectory/raw_summary 写入 runtime initial state。
5. `tests/experiment/test_replay_runtime_initial_state.py`
   - 断言 replay 会优先把 default trajectory 中的 runtime initial state 注入 `task_case.initial_state`。
6. `tests/adapter/test_toolsandbox_reference_scoring.py`
   - 断言 `preserve_state` 约束不会再拿空 `expected.rows` 作为真实目标。
   - 断言 `reference_milestone_node_index=-1` 会走 initial snapshot。
7. `tests/evaluate/test_toolsandbox_reference_excerpts.py`
   - 断言 `constraint_expected_excerpt()` 对 `preserve_state` 返回 reference 标签，而不是空表 JSON。
   - 断言失败线会出现 reference milestone / initial state 提示。
8. `tests/evaluate/test_explicit_package_exports.py`
   - 断言 `dynsteer.evaluate`、`dynsteer.evaluate.matching` 和 `dynsteer.llm` 不存在 `__getattr__`。
   - 断言从 `dynsteer.evaluate` / `dynsteer.evaluate.matching` / `dynsteer.llm` 导入公开函数和类后，`inspect.getmodule()` 能定位到真实代码模块。
   - 断言项目内不再出现 `from dynsteer.evaluate import ...` 这种包内反向导入。

如果要再加一层回归，可以补一个 experiment 级测试，确认当前两条典型 case：

- `add_contact_with_name_and_phone_number_3_distraction_tools`
- `modify_reminder_with_recency_latest`

在重新适配后不再出现 default/replay 的明显错位。

## 6. 验证顺序

1. 先整理 `experiment/runner.py` 的 TaskCase 准备阶段，明确只按 benchmark 名称加载/适配 TaskCase 模板。
2. 清理 `config_for_case()` 在单 case 执行路径中的调用，确保 evaluator/outputs 全部以 `task_case.case_id` 为准。
3. 清理 `config_with_case_ids()` 和 `_config_for_method()` 等单行中转或重复 config 构造。
4. 修改 `dynsteer.evaluate`、`dynsteer.evaluate.matching` 和 `dynsteer.llm` 的包级导入，移除 `__getattr__` 懒加载。
5. 修改 `harness/outputs.py`，让 default trajectory 记录 runtime initial state。
6. 修改 `experiment/runner.py::run_replay_case()`，让 replay 注入 default trajectory 的 runtime initial state。
7. 修改 `toolsandbox/scorer.py`，把 `preserve_state` 的 target 解析到 runtime reference snapshot。
8. 修改 `semantic.py` 和 `diagnostics.py`，把空 expected 的展示修正掉。
9. 更新 `docs/apis/harness.md`、`docs/apis/experiment.md`、`docs/apis/evaluate.md` 和 `docs/apis/llm.md`。
10. 跑窄测试；是否重建 `data/toolsandbox/adapted_cases/*.json` 与是否重跑 `toolsandbox_partial_main` 由用户侧执行。

## 7. 预期修复结果

修复完成后，至少应达到下面的结果：

- `add_contact...` 不再因为 `expected_rows=0` 被 replay 误杀。
- `modify_reminder...` 不再因为旧 timestamp 被 replay 压成 partial。
- replay 的失败/成功判断会更接近 default 的真实运行语义。
- `expected_rows=0` 只会出现在“reference placeholder”语境里，不会再被误读成“空表目标”。
- 同一 benchmark 在多 method、多 model、多 judge profile 的实验中只准备一批 adapted TaskCase，符合控制变量原则。
- 单 case 运行路径不再为了 `config.case_ids` 做无意义的单元素替换。
- `dynsteer.evaluate`、`dynsteer.evaluate.matching`、`dynsteer.llm` 的公开函数和类可以被 IDE 直接跳转，包级导出不再依赖 `__getattr__` 懒加载。

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox 外部 `guardrail_similarity` / `update_similarity` 的完整内部实现。**
   - 没有把握原因：这部分在外部 `tool_sandbox` 包里，不在当前仓库内。我们能确定的是 replay 现在把 preserve_state / reference state 处理错了，但外部 measure 的所有边界条件仍需要结合实际 ToolSandbox 版本再验证。
2. **所有时间敏感字段是否都能仅靠用户在加载阶段使用 `--force_adapt` 重建 adapted_cases 解决。**
   - 没有把握原因：目前已确认 `modify_reminder...` 属于过期绝对 timestamp，按现有流程应由用户在验收或新实验加载阶段显式选择 `--force_adapt` 重建 adapted_cases；但未来若出现新的相对时间语义，是否还需要更细的 runtime 归一规则，要看后续 case。
3. **`reference_snapshot` 证据行的最优文本长度。**
   - 没有把握原因：diagnostics 需要兼顾可读性和终端长度，具体应保留几条证据行，最好在修复后结合真实 report 再微调。
4. **移除 `dynsteer.evaluate` / `dynsteer.llm` 懒加载后的导入风险。**
   - 核查结论：存在被懒加载遮住的循环导入风险。若只把 `dynsteer.evaluate.__init__` 改成显式 re-export，会形成 `dynsteer.evaluate -> dynsteer.evaluate.evaluator -> dynsteer.evaluate` 和 `dynsteer.evaluate -> dynsteer.evaluate.settlement -> dynsteer.evaluate`；若只把 `dynsteer.evaluate.matching.__init__` 改成显式 re-export，会形成 `dynsteer.evaluate.scoring -> dynsteer.evaluate.matching -> dynsteer.evaluate.matching.milestone -> dynsteer.evaluate.scoring` 以及 `dynsteer.evaluate.scoring -> dynsteer.evaluate.matching -> dynsteer.evaluate.matching.minefield -> dynsteer.evaluate.scoring`。
   - 解决方案：先把包内模块全部改为从具体文件导入，再改包级 `__init__.py`。具体包括：`evaluator.py` 从 `runtime.py`、`step.py`、`settlement.py` 和 `matching` 的具体子模块导入；`settlement.py` 从 `runtime.py`、`matching/frontier.py`、`matching/milestone.py` 导入；`runtime.py`、`scoring.py`、`final.py`、`step.py` 不再从 `dynsteer.evaluate.matching` 包级导入，而是从 `matching/boundary.py`、`matching/frontier.py`、`matching/milestone.py`、`matching/minefield.py` 导入。完成这些内部导入调整后，再把 `dynsteer/evaluate/__init__.py` 与 `dynsteer/evaluate/matching/__init__.py` 改成显式顶层 import。
   - `dynsteer.llm` 当前没有发现项目内部循环导入；主要风险是显式导入 provider 类后暴露 `openai` / `anthropic` SDK 缺失。该问题应通过同步 uv 环境或修正依赖声明解决，不允许恢复 `__getattr__` 懒加载。
