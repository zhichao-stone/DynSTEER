# DynSTEER 分范围时间与 Token 成本统计代码修改方案

## 1. 当前结论

当前代码只支持部分底层计量，**不支持**按 `docs/constraints/result_analysis.md` 的完整口径直接统计不同范围的时间和 token 消耗。

| 能力 | 当前支持情况 | 现有实现与缺口 |
|---|---|---|
| Agent 执行时间 | 部分支持 | `dynsteer/metrics.py` 已记录 `advance_case()` batch 耗时，replay 也能计算 DEFAULT 前缀时间；但实验汇总只有平均值，没有逐配对差值、逐 case 和提前终止子集统计。 |
| Agent 执行 token | 依 benchmark 而定 | `TrajectoryStep.cost.tokens` 可聚合；AgentCompass 轨迹能够写入 token，ToolSandbox 当前轨迹没有 token 来源。现有 `trajectory_cost_available` 只表示至少出现过一个值，不能表达覆盖率。 |
| DynSTEER 评估时间 | 部分支持 | replay 的 `elapsed_seconds` 是评估器墙钟；在线 evaluate 的 `elapsed_seconds` 同时覆盖 Agent 执行和评估，尚未拆分。 |
| DynSTEER 评估 token | 支持原始计量 | `BaseLLM.chat()` 和 `RuntimeMetricsRecorder` 能记录 Judge 的 prompt/completion/total token，但聚合层只输出一个 `llm_total_tokens`，未按范围计算占比。 |
| case 适配时间/token | 不支持 | milestone、minefield、stage_goal 生成发生在 `load_task_case()` 阶段，此时没有激活 recorder；缓存命中与实际生成也没有成本账本。 |
| 相对 DEFAULT 的全流程成本差值 | 不支持 | 当前只有 `score_delta`，没有配对后的时间/token delta。 |
| 因 Agent 执行不力提前终止的子集 | 不支持 | 只有 termination code 计数，没有稳定的终止类别、`stop_step/default_total_step` 配对进度和子集成本聚合。 |
| 全量、逐 case、提前终止子集三种范围 | 不支持 | `aggregate_efficiency()` 和 `aggregate_cost()` 对全部结果做一次整体汇总，未按 method、case 或配对子集组织。 |
| 适配/评估 token 占全流程比例 | 不支持 | 尚无 adaptation token，且没有 micro 口径的三种 token share。 |

因此不能只修改结果分析脚本；必须先补齐适配期计量、统一成本数据模型和实验级配对聚合。

### 1.1 当前代码复核后的方案调整

2026-08-06 再次核查当前工作树后，以上能力判断没有变化，但实施设计需要按最新代码收敛：

1. 复用现有 `RuntimeMetricsRecorder` 记录一次 case 适配期间的全部 LLM 调用，不再给 `LLMCallMetrics` 增加 scope，也不修改 `dynsteer/llm/base.py`。
2. 保持 `load_task_case() -> list[TaskCase]` 接口；适配 artifact 成本持久化在 `TaskCase.metadata`，本次是否生成使用一个仅在内存中传播的 usage 字段，不新增 `TaskCaseLoadResult` 或适配账本中转类。
3. case 原始产物继续保存现有 `runtime_metrics` 原子字段；三段成本只在实验聚合层统一派生并写入 `costs.json`，避免 summary/report/raw_summary 重复保存同一成本对象。
4. termination category/mode 由实验聚合层根据现有 `code`、`detail.failure_basis` 和 method 做唯一映射，不修改 policy/step/settlement 中所有终止构造点。
5. replay 前缀 token 必须在已有 `build_replay_timing_metrics()` 中一并计算，因为 `replay_continue_after_virtual_stop=true` 时现有 `trajectory_total_tokens` 会包含虚拟停点之后的 token。
6. 利用现有 `BenchmarkDefaultResult.metrics` 接收 benchmark 原生评估 token；DEFAULT 原生 scorer 的墙钟时间在 `write_default_case_outputs()` 调用边界直接测量。
7. 调整实验准备顺序：同一 benchmark/data_root/case/generation 配置只执行一次静态适配，优先选择 DynSTEER spec；每个 spec 仍基于静态模板的深拷贝刷新动态 target。该调整同时避免 `force_adapt` 在 model/method/repeat 矩阵中重复付费。

## 2. 修改目标与边界

### 2.1 目标

1. 对每个实验结果拆分 `agent`、`adaptation`、`evaluation` 三类时间/token。
2. 对同一 `(benchmark, model_id, repeat_index, case_id)` 的 DEFAULT 与各 DynSTEER 方法做严格配对。
3. 输出实验全量、因 Agent 执行不力提前终止的子集、逐 case 三种统计范围。
4. 同时输出 DynSTEER 专属额外开销和包含 Agent 执行的全流程差值。
5. Agent token 可用时，输出适配、评估、适配+评估三种 token micro 占比及覆盖率。
6. 适配产物被缓存或跨 method/model/repeat 复用时，不重复计算实际调用成本，并明确区分实际成本与分摊成本。
7. 对不可用数据保留 `null`、原因和覆盖率，不把缺失当作 0。

### 2.2 不修改的行为

- 不修改 milestone、minefield、Judge、scorer 和 stop policy 的判定语义。
- 不修改 Agent、User Simulator 或 benchmark 的执行策略。
- 不根据成本结果重写原始 `results/`、`runs/` 或 adapted case。
- 不为 ToolSandbox 估算不存在的 Agent token；没有可靠来源时保持不可用。
- 不主动执行 git commit。

## 3. 统一统计口径

### 3.1 单个结果的成本组件

```text
agent_time       = Agent/环境推进 batch 的实际执行时间
adaptation_time  = case 适配、milestone/minefield/stage_goal 生成时间
evaluation_time  = 除 Agent batch 外的评估、Judge、stop policy、原生 scorer 等时间
pipeline_time    = agent_time + adaptation_time + evaluation_time

agent_tokens      = Agent 轨迹中可审计的 token
adaptation_tokens = milestone、minefield、stage_goal 等适配 LLM token
evaluation_tokens = DynSTEER Judge/语义复判等评估 LLM token
pipeline_tokens   = agent_tokens + adaptation_tokens + evaluation_tokens
```

时间边界按 method 区分：

- DEFAULT：`agent_time = execution_total_seconds`；`evaluation_time = case_elapsed_seconds - agent_time`，包含必要的 session 初始化、原生 scorer 和结果整理，另保留 Judge/native scorer 的细分调用耗时用于审计。
- `DYNSTEER_EVALUATE`：`agent_time = execution_total_seconds`；`evaluation_time = case_elapsed_seconds - agent_time`，覆盖在线结构评估、Judge、停止决策和必要的运行编排。
- `DYNSTEER_REPLAY*`：`agent_time = default_prefix_execution_seconds`；`evaluation_time = replay elapsed_seconds`。虚拟早停后继续扫描时，Agent 时间仍截止虚拟停点。

若相减后因计时精度出现极小负数，只允许在预先声明的毫秒误差范围内归零；超出误差必须标为计时异常。

### 3.2 两类相对 DEFAULT 指标

```text
dynsteer_overhead
  = adaptation_cost + dynsteer_evaluation_cost - default_evaluation_cost

pipeline_delta
  = dynsteer_pipeline_cost - default_pipeline_cost
```

时间和 token 分别计算。`pipeline_delta` 允许为负，表示提前终止节省的 Agent 成本大于新增适配/评估成本；不能把负值改写成“额外开销”。

### 3.3 提前终止与进度

为 `EvaluationTerminationState` 增加稳定终止类别，至少包含：

- `agent_underperformance`：结构性失败、关键前置 milestone 缺失、阶段质量分过低、ready frontier 持续无进展；
- `minefield`：fatal minefield 或 minefield score 触发；
- `other`：原生 benchmark 停止、显式外部停止等；
- `none`：未停止。

同时保留停止模式：

- `live`：在线 DynSTEER 确实调用 `harness.stop_case()`；
- `virtual`：replay 只模拟停止边界；
- `none`：没有停止。

不得混合报告 live 与 virtual。分别输出 `early_stop_live` 与 `early_stop_virtual` 子集；需要总览时可以并列展示，但不能合并成一个“实际提前终止率”。

进度使用闭合 Agent step 口径：

```text
stop_step = DynSTEER runtime_metrics.step_count
default_total_step = 配对 DEFAULT runtime_metrics.step_count
progress = stop_step / default_total_step
```

`virtual_stop_step_index` 继续作为 raw trajectory 边界证据，但不能直接与闭合 Agent step 数相除。

### 3.4 适配成本的实际值与分摊值

每个 adapted case 保存一个稳定 `adaptation_artifact_id` 和生成时的原始成本。聚合时提供三种明确口径：

1. `actual_run_cost`：当前命令真实发生的适配调用；缓存命中为 0，并标记 `cache_hit=true`。
2. `attributed_artifact_cost`：实验使用的唯一 adapted artifact 的一次生成成本，按 `adaptation_artifact_id` 去重，适合计算完整复现成本。
3. `allocated_cost`：用于逐配对结果计算的分摊值：
   - `standalone_method`：一个方法独立运行时承担该 case 的一次适配成本，再在该方法的 model×repeat 消费者之间均分；
   - `experiment_amortized`：按当前实验中所有 DynSTEER 消费者均分，所有行相加严格等于去重后的 artifact 成本。

分摊在划分提前终止子集之前完成；不能把整份 case 适配成本重新分摊给少数提前终止行，否则会夸大该子集成本。

## 4. 目标产物结构

### 4.1 case 级原子计量与适配引用

不在 case summary/report/raw_summary 中新增重复的三段成本对象。继续复用现有 `runtime_metrics`：

- Agent：`execution_total_seconds`、`trajectory_total_tokens`、`trajectory_cost_available`；
- 评估：`elapsed_seconds`、`llm_prompt_tokens`、`llm_completion_tokens`、`llm_total_tokens`；
- replay：`default_prefix_execution_seconds`、新增的 `default_prefix_trajectory_tokens` 及各自可用性；
- DEFAULT 原生评估：新增 `native_evaluation_seconds`、`native_evaluation_*_tokens` 及来源/可用性。

adapted case 的 `TaskCase.metadata.adaptation_cost` 保存 artifact 原始成本：

```json
{
  "schema_version": 1,
  "artifact_id": "...",
  "created_at": "...",
  "elapsed_seconds": 1.8,
  "llm_call_count": 2,
  "llm_failed_call_count": 0,
  "prompt_tokens": 700,
  "completion_tokens": 150,
  "total_tokens": 850,
  "token_available": true
}
```

`TaskCase.metadata.adaptation_usage` 仅存在于当前进程内，记录 `generated_now`、`cache_hit` 和成本不可用原因；`save_task_case()` 不把 usage 写回 artifact。`ExperimentCaseResult` 读取这两个字段并写入 index/`costs.json`。三段成本、分摊和 delta 只在实验聚合层计算一次。

### 4.2 实验级 `costs.json`

新增 `results/exp/<experiment_id>/costs.json`，保存可审计但可能较大的逐配对明细：

```text
schema_version
pairing_key
adaptation_ledger[]
paired_cases[]
  identity
  stop: category/mode/code/stop_step/default_total_step/progress
  default_cost
  dynsteer_cost
  standalone_method_adaptation_allocation
  experiment_amortized_adaptation_allocation
  overhead_delta
  pipeline_delta
  availability/reasons/evidence_paths
```

`metrics.json.cost_analysis` 只保存聚合摘要和 `detail_path="costs.json"`，避免把逐 case 明细重复嵌入多个文件。

### 4.3 实验级聚合范围

`metrics.json.cost_analysis` 至少包含：

```text
full.by_method
early_stop_live.by_method
early_stop_virtual.by_method
by_case.<benchmark>.<case_id>.<method>
adaptation_actual_run
adaptation_attributed_artifacts
data_quality
```

每个范围输出：

- 配对数、可用数、缺失数和缺失原因计数；
- DEFAULT、DynSTEER 的 agent/adaptation/evaluation/pipeline 时间和 token 总量；
- overhead 与 pipeline delta 的总量、mean、median、P90、P95；
- 提前终止范围额外输出 progress 的 mean、median、P10、P90；
- Agent token 可用行上的三种 micro token share：

```text
adaptation_token_share
  = sum(adaptation_tokens) / sum(agent_tokens + adaptation_tokens + evaluation_tokens)

evaluation_token_share
  = sum(evaluation_tokens) / sum(agent_tokens + adaptation_tokens + evaluation_tokens)

combined_overhead_token_share
  = sum(adaptation_tokens + evaluation_tokens)
    / sum(agent_tokens + adaptation_tokens + evaluation_tokens)
```

同时报告 `token_share_eligible_pair_count / pair_count`。逐样本 macro 均值可以附加，但不能替代 micro 占比。

## 5. 详细代码修改

### 5.1 `dynsteer/model.py`

当前 `LLMCallMetrics`、`RuntimeMetricsRecorder` 和 `EvaluationTerminationState` 已包含本任务需要的原始调用与终止证据，不修改这些公共数据类。适配计量直接创建独立 recorder，终止分类放在实验聚合层，避免为了统计新增运行期状态。

### 5.2 `dynsteer/metrics.py`

只增加三个共享 helper：

1. `summarize_llm_calls(llm_calls)`：复用现有 optional token 求和规则，输出调用/失败调用、prompt/completion/total token、LLM 调用耗时和 token 可用性，供运行期与适配期共同调用。
2. `prefix_trajectory_cost(trajectory, stop_step_index)`：与 `prefix_execution_timing()` 使用相同 raw step 边界，返回前缀时间、token、token 值数量和可用性。时间仍以 execution timing record 为真值；token 只聚合真实 `StepCost.tokens`。
3. `build_replay_timing_metrics()` 合并上述前缀结果，新增 `default_prefix_trajectory_tokens`、`prefix_token_available` 和不可用原因；不改变现有 timing 字段。

同时修正 `build_runtime_metrics()` 的可用性表达：增加 token 值数量与来源说明；保留现有字段供原始审计，但实验聚合不得再把“至少一个 step 有 token”解释为完整覆盖。

不在该文件构造 method-specific pipeline/delta，避免 `dynsteer.metrics` 反向依赖 experiment model。

### 5.3 `dynsteer/adapter/loader.py`

1. 对确实执行 `_adapt_task_case()` 的 case 创建一个独立 `RuntimeMetricsRecorder`，在整个适配过程外层激活现有 context recorder，并使用 `perf_counter()` 测量总适配时间。这样 milestone、minefield 和可能的 LLM stage_goal 生成全部进入同一 adaptation 总成本，不要求修改生成器接口。
2. 调用 `summarize_llm_calls()`，把 schema version、artifact id、created_at、总适配耗时、调用数、失败数、prompt/completion/total token 和可用性写入 `TaskCase.metadata.adaptation_cost` 后再保存。
3. `artifact_id` 使用不含 `adaptation_cost/adaptation_usage` 的 canonical TaskCase 内容摘要，避免路径移动或成本字段本身改变 artifact 身份。
4. 当读取缓存时，保留 artifact 中的 `adaptation_cost`，只在内存中的 `metadata.adaptation_usage` 写 `cache_hit=true/generated_now=false`；本次生成则写 `generated_now=true`。`save_task_case()` 必须剔除 transient usage。
5. 历史 artifact 缺少成本 schema 时写 `available=false` 和原因，不补 0。
6. 保持 `load_task_case() -> list[TaskCase]`，不新增返回类型。

`dynsteer/stage/goal.py`、`dynsteer/milestone/compiler.py` 和 `dynsteer/llm/base.py` 不需要修改；现有 LLM 调用会自动写入 loader 激活的 recorder。

### 5.4 `dynsteer/harness/runner.py`、`dynsteer/experiment/runner.py` 与 `main.py`

1. 将“静态 adapted case 加载”和“按 spec 刷新动态 target”分开：静态模板按 `(benchmark, resolved data_root, case_ids, milestone_generation, stage_goal_generation)` 缓存一次；每个 spec 使用深拷贝后再调用 `refresh_task_cases_for_experiment()`。
2. `run_experiment()` 和 `--only_adapt` 选择代表配置时优先使用同组 DynSTEER spec，避免首个 DEFAULT spec 触发 `_adapt_task_case()` 的 DEFAULT 早返回而漏掉 DynSTEER milestone/stage_goal 生成。
3. `force_adapt` 对每个静态适配组只生效一次，不能随 model/method/repeat 重复重建；后续 spec 直接复用本次内存模板。
4. DEFAULT-only 组继续允许只生成 benchmark 基础 TaskCase，不强迫配置 DynSTEER generator。
5. 单 benchmark 模式在完成日志中输出实际生成数、缓存命中数、适配时间/token 总量。

这一步是成本正确性的前置条件，也修正当前 `run_experiment()` 每个 spec 都调用 `prepare_task_cases()` 所导致的 `force_adapt` 重复付费问题。

### 5.5 `dynsteer/evaluate/evaluator.py`

保持当前 stop policy 和 termination 结构不变。replay 仅通过调整后的 `build_replay_timing_metrics()` 写入早停前缀 token 字段；在线 evaluate 已有 `elapsed_seconds` 与 `execution_total_seconds`，无需增加新的计时器。

### 5.6 `dynsteer/harness/outputs.py`

1. 在 `write_default_case_outputs()` 调用 `harness.default_result_from_session()` 的边界测量 `native_evaluation_seconds`。
2. 从已有 `BenchmarkDefaultResult.metrics` 读取 benchmark 明确提供的 native evaluation prompt/completion/total token 和可用性；字段缺失时标记不可用，不补 0。确定为纯本地规则 scorer 的 adapter 应显式返回 token 0 与 `available=true`。
3. 将上述原子字段并入 DEFAULT `runtime_metrics`；不在这里计算相对 DynSTEER 的 delta。
4. 提升 `RESULT_SCHEMA_VERSION`。历史产物仍可读取分数，但不得进入新成本统计；完整统计要求 `--force_adapt --force_eval`。

### 5.7 `dynsteer/experiment/model.py` 与 `runner.py`

1. `ExperimentCaseResult` 增加：
   - termination detail；
   - adaptation cost 与 transient usage；
   - 现有 output paths 继续作为证据路径。
2. category/mode 不写入运行产物，由聚合函数一次性派生：replay termination 为 virtual，在线 evaluate termination 为 live；minefield code/failure basis 与 Agent underperformance 使用固定映射表。
3. `_case_result_from_output()` 继续从 summary 读取 runtime/termination，并从传入的 `task_case.metadata` 读取 adaptation 字段，不重新打开 adapted case。
4. `run_experiment()` 将同一 artifact 的 generated/cache usage 随结果传入聚合；不建立第二套独立账本对象。
5. `write_metric_tables()` 同时写出 `costs.json`。

### 5.8 `dynsteer/experiment/metrics.py`

1. 用新的配对成本聚合替换当前过于粗粒度的 `aggregate_cost()`；不保留只调用新函数的兼容 wrapper。
2. 新增集中式函数：
   - 构造 DEFAULT map，键固定为 `(benchmark, model_id, repeat_index, case_id)`；
   - 从现有 runtime 原子字段派生 DEFAULT、在线 evaluate、replay 的 agent/evaluation/pipeline 时间/token；
   - 根据 code、failure basis 和 method 派生 termination category/mode；
   - 为每个 DynSTEER 结果生成 paired cost row；
   - 根据派生的 category/mode 生成 full/live/virtual 范围；
   - 先确定适配分摊，再过滤子集；
   - 生成逐 case、逐 method、逐 benchmark 聚合；
   - 计算总量、分位数、progress 和三种 micro token share。
3. 配对 DEFAULT 缺失、DEFAULT 未完整运行、`default_total_step=0`、`stop_step>default_total_step` 均写入 `data_quality`，不跨 repeat/case 补配。
4. 所有 optional 成本使用“有效样本求和 + 覆盖率”模式；不可用值不参与分子或分母，也不按 0 累加。
5. `aggregate_efficiency()` 调整为按 method 输出，保留 elapsed/prefix/effective 原始诊断字段，但最终效率结论以 `cost_analysis` 的全流程口径为准。
6. `costs.json.adaptation_ledger` 直接从 paired rows 中按 artifact id 去重：
   - 任一 usage 的 `generated_now=true` 才进入 `actual_run_cost`；
   - 所有被实验使用的唯一 artifact 进入 `attributed_artifact_cost`；
   - 缓存命中次数只作审计，不计为生成成本。

### 5.9 API 文档

同步更新：

- `docs/apis/milestone.md`：adaptation artifact 成本、缓存来源和 transient usage；
- `docs/apis/harness.md`：DEFAULT native evaluation 时间/token 字段与可用性；
- `docs/apis/evaluate.md`：replay prefix token/time；
- `docs/apis/experiment.md`：`costs.json`、聚合范围、分摊口径和 token share。

## 6. 测试方案

新增或扩展以下测试，测试调用项目现有公共接口，不为测试创建业务接口：

### 6.1 Recorder 与适配计量

- mock provider 返回 usage 时，一次适配中的 milestone 与 stage_goal LLM 调用都进入 adaptation recorder 总量。
- retry 的成功/失败 attempt 均被计量，失败 attempt token 缺失不会补 0。
- 纯 origin/semantic/stored 适配且没有 LLM 调用时记录已知 0；provider 调用存在但 usage 缺失时 token 标为不可用。
- force adapt 产生新 artifact；cache hit 不产生新的实际 token，但保留原 artifact 成本引用。
- 同一实验矩阵的 `force_adapt` 每个静态适配组只执行一次；每个 spec 的动态 target 刷新仍彼此隔离。
- `--only_adapt` 在同时包含 DEFAULT/DynSTEER 时优先使用 DynSTEER 代表配置。

### 6.2 case 成本拆分

- DEFAULT：agent + evaluation 等于 pipeline runtime，原生 scorer 时间不混入 Agent batch。
- 在线 DynSTEER：提前停止后只统计已执行 batch 的 Agent 时间/token。
- replay：虚拟停点前 prefix time/token 正确；继续扫描不扩大 Agent prefix 成本。
- ToolSandbox 没有 Agent token 时，pipeline token 和 token share 为不可计算，不输出假 0。
- AgentCompass token 可用时，agent/evaluation/pipeline token 加法一致。

### 6.3 配对与分范围聚合

- DEFAULT 只能与相同 benchmark/model/repeat/case 配对。
- live 与 virtual 提前终止分别进入不同范围，minefield 不进入 Agent 执行不力子集。
- `progress=stop_step/default_total_step`，分母缺失/为 0 及 stop 大于 total 均进入质量问题。
- full、early_stop_live、early_stop_virtual、by_case 的计数和总量一致。
- pipeline delta 可为负且不被截断。
- micro token share 使用总分子/总分母计算，不能误用逐行比例均值。
- 提前终止子集沿用全量阶段确定的适配分摊，不重新分摊。
- 多 method/model/repeat 使用同一 adaptation artifact 时，actual/attributed 总量只计一次，standalone 与 experiment-amortized 分摊分别满足各自守恒关系。

### 6.4 产物与缓存

- summary/raw_summary 的原子 runtime 字段与 index、costs.json、metrics.json 的派生值一致。
- 老 result schema 和老 adapted case 明确标为成本不可用。
- `--force_adapt --force_eval` 能生成完整新 schema。
- 新增核心函数单元测试覆盖率不低于 80%，并执行现有全量测试。

建议验收命令：

```powershell
uv run pytest tests -q
uv run pytest --cov=dynsteer --cov-report=term-missing tests -q
```

再选择至少一个 Agent token 可用的 AgentCompass case 和一个 Agent token 不可用的 ToolSandbox case 做产物核对。

## 7. 实施顺序

1. 在 loader 接入现有 recorder，持久化 adaptation artifact 成本与 transient usage。
2. 重构实验静态适配缓存：优先 DynSTEER 代表配置、force adapt 每组一次、动态 target 每 spec 深拷贝刷新。
3. 在 DEFAULT writer 增加 native evaluation 原子计量，在 replay timing 增加 prefix token。
4. 扩展 `ExperimentCaseResult`，集中实现严格配对、终止分类、提前终止进度和三段成本派生。
5. 写出 `costs.json` 与 `metrics.json.cost_analysis`，再调整 method-level efficiency 摘要。
6. 更新 API 文档和结果 schema 说明。
7. 运行定向测试、全量测试和两个代表性 case；核对加法守恒、配对数量、覆盖率与分摊守恒。
8. 检查未使用 import、重复 helper、编码和测试中间文件；不提交 git commit。

## 8. 验收标准

- 每个 DynSTEER 配对结果都有 DEFAULT 引用、三类成本、delta、可用性和证据路径。
- 全量、live 提前终止、virtual 提前终止、逐 case 范围均可直接从标准产物读取，无需临时脚本重新发明口径。
- 提前终止进度使用闭合 Agent step，并与同 case/model/repeat 的 DEFAULT 完整轨迹配对。
- 实际适配成本按 artifact 去重；两类分摊值均满足总量守恒。
- Agent token 可用时三种 token share 正确；不可用时明确报告覆盖率和原因。
- 时间与 token 的 component 总量、pipeline 总量和相对 DEFAULT 差值可审计。
- 新旧 schema 不混算，历史缺失不补 0。
- 新增核心逻辑测试覆盖率不低于 80%，全量测试通过。

## 9. 风险与取舍

- `advance_case()` 是目前跨 benchmark 可统一取得的最小可靠时间边界；batch 内 raw step 的耗时仍是已有均匀分摊估算，不能表述为单条消息真实耗时。
- ToolSandbox 当前没有可靠 Agent token 元数据，方案只能报告不可用；若外部 SDK 后续提供 usage，应在 adapter 规范化为 `StepCost.tokens`，而不是在实验聚合层估算。
- DEFAULT 和在线 evaluate 的非 Agent 时间包含必要 session/scorer 编排；方案保留 LLM 调用耗时明细，但不强行把无法可靠拆分的本地微小开销伪装成精确 Judge 时间。
- 适配通常早于正式实验并被缓存，因此“当前命令实际成本”和“完整复现归属成本”可能不同；报告必须并列呈现，不能只选更有利的一种。
- schema 升级后，历史产物无法恢复已经发生但未记录的 token/时间，只能标记不可用或重新运行。

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox Agent token 的真实来源**：当前转换链路没有 provider usage，无法从消息文本可靠反推 token。是否能补齐取决于外部 ToolSandbox role/client 是否暴露每次模型调用 usage；在确认 SDK 能力前只能保持不可用。
2. **外部 AgentCompass 指标的一致性**：当前 ACTF `metric` 中能读取 token 和 latency，但不同 Agent 或 provider 是否都使用相同口径需要结合真实产物抽样验证。方案会保留 source/coverage 字段，不假设所有 case 均完整。
3. **历史适配 artifact 的成本回溯**：旧 adapted case 没有生成调用 usage，无法无损补算 milestone/stage_goal token；只能通过 `--force_adapt` 重建。
4. **并发下的绝对 wall-clock 可加性**：单 case 内组件可按统一边界计量，但多个 worker 并行时，各 case 墙钟总和不等于实验实际历时。方案把“资源消耗总和”和“实验端到端 wall-clock”分开；若论文需要后者，还需在 `run_experiment()` 外层增加独立实验级起止计时。
