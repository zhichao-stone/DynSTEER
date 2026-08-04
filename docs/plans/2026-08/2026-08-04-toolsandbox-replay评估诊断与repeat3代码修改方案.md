# 2026-08-04 ToolSandbox Replay 评估诊断与 Repeat=3 代码修改方案

## 1. 方案背景

本方案基于：

- `docs/plans/analysis/2026-08-04-toolsandbox-partial-main-current-results-runs-audit-report.md`
- `results/exp/toolsandbox_partial_main/`
- `runs/exp/toolsandbox_partial_main/`
- 当前 DynSTEER evaluator、ToolSandbox adapter、experiment metrics 和输出路径实现

本次目标不是让 DYNSTEER_REPLAY 复现 DEFAULT 标签，而是：

1. 保留 DYNSTEER 对阶段执行质量、工具质量、效率和安全性的独立判断；
2. 修复确定存在的诊断归因错误；
3. 让失败原因明确到具体工具、参数和失败层级；
4. 将实验重复次数从 1 提升到 3，并正确保存、聚合三次独立运行；
5. 补充 repeat 稳定性、模型对差值和按方法拆分的效率/成本指标。

本方案不修改 `../ToolSandbox` 中的任何原始 scenario、Agent、User Simulator、milestone 或评估代码。

## 2. 对报告第 11 节意见的逐项答复

### 2.1 P0.2：缺失工具诊断不对 `get_current_timestamp` 特判

意见正确，原报告举 `get_current_timestamp` 只是从问题 case 提取的例子，不应形成工具特判。

当前 ToolSandbox adapter 已经为每个工具 milestone 生成通用语义：

```json
{
  "kind": "tool_call",
  "tool_name": "<实际工具名>",
  "arguments": {"<参数名>": "<期望值>"}
}
```

数据来自 `Constraint.stage_goal_semantics`，由 `dynsteer/adapter/toolsandbox/utils/scenario.py::_sandbox_tool_call_semantics()` 统一生成。因此代码无需认识任何具体工具，只需要修复 `dynsteer/evaluate/diagnostics.py::_constraint_goal_hint()` 当前固定返回 `Need to call the required tool.` 的信息损失。

调整后诊断示例：

```text
Required tool call not matched: tool=get_current_timestamp, expected_arguments={}
Required tool call not matched: tool=search_contacts, expected_arguments={"relationship":"friend"}
Required tool call not matched: tool=modify_contact, expected_arguments={...}
```

结论：

- 不新增工具名称枚举；
- 不对 `get_current_timestamp`、`search_messages` 或其他工具写分支；
- 不因为最终回答看似正确而跳过 ToolSandbox hard tool milestone；
- 只把已有通用 constraint 语义完整输出。

### 2.2 P0.3：当前不是“只有 hard 失败才会 stage fail”

当前实现有两层判断，hard constraint 是必要条件，但不是 stage 通过的充分条件。

#### 第一层：结构化 milestone 匹配

1. `constraint_from_snapshot_constraint()` 将 ToolSandbox milestone constraint 全部适配为 `hard=True`、`threshold=1.0`。
2. `ToolSandboxConstraintScorer.score_milestone()` 计算原生 snapshot/guardrail similarity。
3. 非消息 hard constraint 未达到阈值时，`hard_constraints_all_pass=false`、milestone `status=fail`。
4. 用户可见消息允许进入语义等价复判，但复判后仍必须达到 milestone pass 条件。
5. 只有 milestone score 为 `pass`，或消息语义复判将其提升为 `pass`，才会创建 checkpoint 并推进 graph frontier。

#### 第二层：阶段执行质量评估

checkpoint 命中后，`settlement._evaluate_stage()` 继续评估：

- progress；
- state consistency；
- tool quality；
- efficiency；
- safety；
- interaction quality；
- recovery。

执行顺序是：

1. `CheapJudge` 先基于结构化 milestone score、工具失败、空结果、步骤数等生成本地维度分；
2. dynamic routing 决定哪些维度维持 cheap、哪些升级为 Standard/Expensive；
3. Standard/Expensive Judge 的结果覆盖对应维度；
4. `stage_score_from_dimensions()` 按当前动态权重计算 stage score；
5. hard/structural failure 必然 fail；hard 通过后，stage score 仍按 `<0.4 => fail`、`0.4–0.8 => warn`、`>=0.8 => pass` 分类；
6. `update_evaluation_policy()` 在 stage score `<0.4` 时返回 `evaluation_policy_stop`。

因此，当前确实允许：

```text
hard_constraints_all_pass = true
stage_status = fail
termination = evaluation_policy_stop
```

这不是逻辑矛盾。前者表示 ToolSandbox hard milestone 已出现，后者表示实际执行质量很差。

#### DF reminder 为什么是 0.2742

`deepseek-v4-flash / modify_reminder_with_recency_latest` 在该阶段的完整证据是：

1. `search_reminder({})`：失败，缺少搜索条件；
2. `search_reminder({creation_timestamp_lowerbound: 0})`：失败，参数越界；
3. 第三次搜索才成功。

ToolSandbox hard milestone 只要求出现对应工具调用证据，因此结构化 scorer 可以命中；StandardJudge 则根据真实执行质量给出：

| 维度 | 分数 | 当时权重 |
|---|---:|---:|
| progress | 0.00 | 0.1982 |
| state consistency | 0.50 | 0.2812 |
| tool quality | 0.25 | 0.2791 |
| efficiency | 0.25 | 0.0995 |

只对本阶段实际参与的权重归一化：

```text
(0×0.1982 + 0.5×0.2812 + 0.25×0.2791 + 0.25×0.0995)
------------------------------------------------------------------------ = 0.2742
             (0.1982 + 0.2812 + 0.2791 + 0.0995)
```

该阶段执行确实不好，低于 0.4 后停止符合 DYNSTEER 目标。本方案不修改阈值、不取消质量失败、不改成 hard-only stop。

本次只增加明确的失败层级字段，使报告直接区分：

- `structural_hard_constraint`
- `quality_score`
- `fatal_minefield`
- `ready_frontier_no_progress`
- `milestone_predecessor_gap`

### 2.3 P0.4：不修改 ToolSandbox 原始多轮模拟

ToolSandbox 的 Agent/User 内容和 scenario milestone 均来自外部原始代码。DynSTEER 技术上可以在 finish 阶段引入额外 LLM，对“合并用户指令是否等价于多个顺序 milestone”做重新解释，但这会改变原 benchmark 的交互语义，并使结果依赖额外推断。

例如 `update_contact_relationship_with_relationship_twice_multiple_user_turn` 的原始 User Simulator 系统指令明确要求：

1. 先要求改成 enemy；
2. Agent 完成后，再要求改回 friend。

某次 User Simulator 把两条要求合并到同一条消息，不代表 evaluator 应自动删除中间用户可见消息 milestone。否则 DynSTEER 实际评估的将不再是 ToolSandbox 定义的流程。

结论：

- 不修改 ToolSandbox 源代码；
- 不根据单次 User Simulator 输出动态改写 milestone graph；
- 不新增“合并多轮自动视为完成”的特殊规则；
- 报告保留实际用户消息和缺失 milestone，供结果解释。

### 2.4 P0.5：撤回“看到后续恢复就不应停止”的建议

`evaluate_replay()` 虽然输入是完整 DEFAULT 轨迹，但它逐 step 构造 `replay_trajectory`，在 virtual stop 点只使用当前前缀。若因为已经知道 DEFAULT 后缀会恢复而推迟停止，就发生未来信息泄漏，replay 不再模拟在线阶段决策。

DF reminder 的案例是：

- replay 在当前可见前缀内观察到两次错误搜索和低质量阶段；
- stage score 为 0.2742，触发停止；
- DEFAULT 后缀后来成功修改 reminder。

后缀成功只能用于事后说明 Agent 具有恢复能力，不能作为 stop 点当时的输入。本方案保留当前停止逻辑，不新增 look-ahead、恢复等待窗口或终局预检。

### 2.5 输出目录：不做版本分离，但 repeat 必须独立落盘

采纳“不增加 run id、配置哈希、Git SHA 或版本目录”的意见。仍使用：

```text
runs/exp/<experiment_id>/...
results/exp/<experiment_id>/...
```

新运行继续覆盖同一实验 ID 的旧结果，只保留最新实验。

但是当前 `case_output_dir()` 完全忽略 `repeat_index`。如果只把 `repeats` 改为 3：

1. repeat 0/1/2 会写入同一个 `summary.json/trajectory.json`；
2. `existing_case_output()` 会把 repeat 0 缓存误当成 repeat 1/2；
3. index 虽有三个 repeat 节点，但三个节点可能引用同一份文件；
4. repeat 统计失真。

因此实验输出必须始终包含最短的 repeat 区分层；即使 `repeats=1` 也写入 `r0`：

```text
<benchmark>/<model>/<method>/r0/<case>/   # repeats=1

<benchmark>/<model>/<method>/r0/<case>/
<benchmark>/<model>/<method>/r1/<case>/
<benchmark>/<model>/<method>/r2/<case>/
```

这样把 `repeats` 从 1 增加到 3 时，已有 `r0` 路径不变，`existing_case_output()` 可以直接复用，只需要补跑 `r1/r2`。`r0` 只增加 3 个字符，不是版本分离，也不会产生明显路径长度风险。非 experiment harness 没有 `experiment_id/repeat_index`，继续保持 `<benchmark>/<method>/<case>` 原路径。

复用前提是代码、模型、case、阈值和评估配置没有发生会改变结果的修改。本轮并行 tool result 归因修复会改变部分 CheapJudge 质量诊断，进而可能改变 stage score 和 stop，因此当前旧目录下的 repeat=1 产物不能直接作为修改后实验的 `r0`；代码落地后第一次完整实验仍需重跑 r0。此后如果只增加 repeats 数目，即可稳定复用新代码生成的 r0。

### 2.6 配置凭据

配置文件允许保存当前实验所需连接参数。本方案不修改相关配置结构、加载方式或内容，也不包含任何此类改造项。

### 2.7 P1 区分度：`repeats` 调整为 3

采纳。`data/experiments/toolsandbox_partial_main.json`：

```json
"repeats": 3
```

不提升到 5。按当前 1-repeat 运行窗口估算，完整实验时间可能接近原来的 3 倍；ToolSandbox 使用共享 execution context，`benchmark.json.max_workers=1`，本方案不通过提高并发冒险缩短时间。

三次重复需要同时输出 repeat 均分、方差和逐 repeat 排名相关，不能只把所有 case 混合求一个平均值。

### 2.8 P1 效率：当前已经 CheapJudge 优先

意见正确。当前 `_evaluate_stage()` 无条件先调用本地 `CheapJudge`，仅对 dynamic routing 指定为 Standard/Expensive 的维度调用 LLM；消息语义等价复判和空 milestone graph whole-trajectory evaluation 是另外两类必要 LLM 路径。

本方案不重新设计相同的 cheap-first 机制，不强行禁止 Standard/Expensive 调用，也不增加 Judge token budget。

### 2.9 Online 对照实验

采纳“不执行”的意见。Online DYNSTEER 会真实改变 Agent/User 后续轨迹，无法与 DEFAULT 保持相同轨迹基础，不能用于本次 replay 方法的严格逐 pair 效率对照。本方案不增加 online 实验配置或相关代码。

## 3. 修改目标与非目标

### 3.1 修改目标

1. 并行 tool result 归因到正确 tool call。
2. 缺失工具诊断显示任意具体工具名和期望参数。
3. stage/report 明确区分结构失败和质量失败，但不改变现有评分与停止行为。
4. `repeats=3` 时每次重复独立运行、缓存、落盘和聚合。
5. 增加 repeat 稳定性、DS 模型对、case 内分散度和 method 级效率/成本指标。
6. 更新相关 API 文档和测试。

### 3.2 非目标

1. 不修改 `../ToolSandbox`。
2. 不对任何具体工具特判。
3. 不放宽或移除 ToolSandbox hard constraint。
4. 不改变 pass/warn/fail 阈值和动态权重公式。
5. 不取消 `stage_score < 0.4`、fatal minefield、no-progress 或 predecessor-gap stop。
6. 不读取 virtual stop 之后的 DEFAULT 轨迹来决定是否停止。
7. 不支持合并多轮自动跳过中间 milestone。
8. 不增加 run/version/hash 目录。
9. 不改连接配置结构。
10. 不重写 Cheap/Standard/Expensive routing。
11. 不新增 online 对照实验。
12. 不主动执行 git commit。

## 4. 详细代码修改方案

### 4.1 修复并行 tool call/result 诊断归因

修改文件：`dynsteer/evaluate/diagnostics.py`

当前 `build_quality_diagnostics()` 只维护单个 `latest_tool_call`。连续出现两个并行 tool call 后，第一个 result 会被错误归到第二个 call，已导致 failed/empty 诊断各 6 条工具名错配。

修改为以下解析顺序：

1. 若 result step 的 `raw.openai_function_name` 是非空字符串，直接采用；ToolSandbox result row 已提供该字段。
2. 否则读取 `raw.openai_tool_call_id`，从同一 interval 内预先建立的 `call_id -> tool_name` 映射解析。
3. 只有在没有 correlation id 且此前只有一个未匹配调用时，才采用串行 fallback。
4. 并行且无法唯一归属时，`tool_name=null` 并增加 `attribution_status=ambiguous`，禁止错误猜测。

failed/empty 记录增加：

```json
{
  "tool_name": "search_reminder",
  "tool_call_id": "call_xxx",
  "attribution_source": "result.openai_function_name"
}
```

不调用 `AgentStepTracker` 作为中转；diagnostics 直接使用已落在 `TrajectoryStep.raw` 中的 correlation 数据，避免把运行期闭包状态复制进离线诊断。

### 4.2 通用输出缺失工具和期望参数

修改文件：`dynsteer/evaluate/diagnostics.py`

修改函数：

- `constraint_summary_to_dict()`
- `_constraint_goal_hint()`
- `_constraint_failure_detail()`

具体修改：

1. `constraint_summary_to_dict()` 增加 JSON-safe 的 `stage_goal_semantics`。
2. `kind=tool_call` 时读取：
   - `tool_name`
   - `arguments`
3. `_constraint_goal_hint()` 输出通用工具名和压缩后的参数，不再输出没有信息量的固定句子。
4. `_constraint_failure_detail()` 增加结构化字段：

```json
{
  "expected_tool_name": "search_contacts",
  "expected_tool_arguments": {"relationship": "friend"},
  "goal_hint": "Required tool call not matched: tool=search_contacts, expected_arguments={...}"
}
```

5. 非工具 constraint 保持现有 message/state/preserve-state 诊断。

该修改只改善解释，不改变 constraint score、hard flag、milestone status 或 coverage。

### 4.3 标记失败层级，不改变停止行为

修改文件：

- `dynsteer/model.py`
- `dynsteer/evaluate/policy.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/evaluator.py`

新增统一失败层级值：

```text
structural_hard_constraint
quality_score
fatal_minefield
ready_frontier_no_progress
milestone_predecessor_gap
```

实现要求：

1. 在 `StageStatus` 相邻位置增加 `EvaluationFailureBasis` enum，集中维护上述值，避免多个模块散落字符串常量。
2. `_evaluate_stage()` 保持当前 status 和 stage score 计算不变。
3. stage metadata 增加：

```json
{
  "failure_basis": "quality_score",
  "structural_failure": false,
  "hard_constraints_all_pass": true,
  "stage_score": 0.2742,
  "fail_threshold": 0.4
}
```

4. `update_evaluation_policy()` 保持原停止分支和 `evaluation_policy_stop` code，不改变外部行为，只在 `termination_detail` 增加 `failure_basis`、阈值和触发字段。
5. `should_stop_after_stage()` 使用同一 enum，不再形成另一套文案。
6. minefield、no-progress、predecessor-gap 决策分别写入对应 `failure_basis`。
7. `_runtime_report().first_failure_stage_id` 行为不变。

验收重点：DF reminder 仍必须在相同前缀得到约 0.2742、`stage_status=fail` 和 virtual stop；变化只应是报告明确写出 `failure_basis=quality_score`。

### 4.4 正确支持三次重复的输出路径

修改文件：

- `dynsteer/experiment/model.py`
- `dynsteer/experiment/config.py`
- `dynsteer/harness/paths.py`
- `dynsteer/harness/outputs.py`（只复核调用结果，原则上无需新增分支）

#### `ExperimentRunSpec`

增加：

```python
repeat_count: int = 1
```

校验：

- `repeat_count >= 1`
- `0 <= repeat_index < repeat_count`

`to_metadata()` 输出 `repeat_count` 和现有 `repeat_index`。

#### `expand_experiment_matrix()`

每个 spec 同时写入：

```python
repeat_index=repeat_index
repeat_count=repeats
```

#### `case_output_dir()`

experiment 路径规则：

```text
<benchmark>/<model>/<method>/r<repeat_index>/<case>
```

只要存在 `experiment_id` 和合法 `repeat_index` 就使用 `rN`，与 `repeat_count` 大小无关。非 experiment harness 继续使用 `<benchmark>/<method>/<case>`。禁止使用长时间戳、UUID、配置哈希或版本号。

这会同时修复：

- repeat 缓存串用；
- repeat 文件覆盖；
- index 中不同 repeat 指向相同输出文件；
- force_eval=False 时后续 repeat 被错误跳过。

### 4.5 将实验配置调整为三次重复

修改文件：`data/experiments/toolsandbox_partial_main.json`

修改：

```diff
- "repeats": 1
+ "repeats": 3
```

不修改模型、method、case、阈值或连接参数。

预期实验规模：

```text
4 models × 25 cases × 2 methods × 3 repeats = 600 results
```

DEFAULT 每个 model/case/repeat 只运行一次，对应 replay 复用同 repeat 的 DEFAULT trajectory。

### 4.6 增加 repeat 稳定性指标

修改文件：`dynsteer/experiment/metrics.py`

新增 `repeat_statistics(results)`，输出：

```json
{
  "repeat_statistics": {
    "default": {
      "toolsandbox": {
        "deepseek-v4-pro": {
          "repeat_count": 3,
          "repeat_scores": {"0": 0.81, "1": 0.83, "2": 0.82},
          "mean_score": 0.82,
          "population_stddev": 0.0082,
          "repeat_success_rates": {"0": 0.32, "1": 0.28, "2": 0.32},
          "mean_success_rate": 0.3067,
          "success_rate_stddev": 0.0189
        }
      }
    }
  }
}
```

规则：

1. 每个 repeat 先对该模型的 case 求均分和成功率；
2. 再对 repeat 均分计算总体标准差；
3. 不把 75 个 case 直接当作 75 次独立重复；
4. 缺失 repeat 应在统计中显示实际 `repeat_count`，不得补 0。

新增 `rank_tau_by_repeat`：

```json
{
  "dynsteer_replay": {
    "toolsandbox": {
      "repeats": {"0": 0.33, "1": 0.67, "2": 0.33},
      "mean": 0.4433,
      "population_stddev": 0.1571
    }
  }
}
```

每个 repeat 只比较同 repeat 的 DEFAULT/replay 模型均分。

### 4.7 DS 输出具体模型对

修改文件：`dynsteer/experiment/metrics.py`

`discriminability_score()` 当前只输出显著对数量，无法判断差异由哪些模型产生。保留公式不变，每个 epsilon 增加：

```json
{
  "model_pairs": [
    {
      "left_model": "deepseek-v4-pro",
      "right_model": "qwen-plus-2025-12-01",
      "absolute_difference": 0.0524,
      "significant": true
    }
  ]
}
```

要求：

- 模型名按稳定排序输出；
- `significant` 仍严格使用 `difference > epsilon`；
- DS 公式、epsilon 和现有组成字段不变；
- 不为任何模型建立特殊权重。

新增 `case_discriminability`：按 `method/benchmark/repeat/case` 计算四模型两两绝对分差，再汇总 mean、median、P90、P95、max 和 zero-dispersion count，用于区分“少数离群 case 拉高均值”和“普遍区分度提升”。

### 4.8 按 method 拆分效率与成本

修改文件：`dynsteer/experiment/metrics.py`

保留全局汇总，同时在 `efficiency` 和 `cost` 下增加 `by_method`，避免 DEFAULT/replay 混合平均。

每个 method 的 efficiency 输出：

- case count；
- elapsed total/mean/median/P90/P95；
- Agent step、raw step、tool call total/mean；
- replay prefix total/mean/median；
- replay effective total/mean/median/P90/P95；
- timing available count。

每个 method 的 cost 输出：

- trajectory token total 和 available count；
- Judge LLM call/failed call/token total；
- mean Judge tokens per case/call；
- cache hit 和 duplicate avoided total。

增加 replay 边际/总成本时间视图：

```json
{
  "replay_cost_views": {
    "marginal_judge_elapsed_seconds": 795.448,
    "offline_default_plus_replay_elapsed_seconds": 4046.095,
    "counterfactual_effective_elapsed_seconds": 2456.466
  }
}
```

计算时按 `(benchmark, model_id, case_id, repeat_index)` 配对 DEFAULT 与具体 replay method：

- `marginal_judge_elapsed_seconds`：只累加 replay evaluator 的 `elapsed_seconds`；
- `offline_default_plus_replay_elapsed_seconds`：累加 DEFAULT wall-clock 与 replay evaluator elapsed；
- `counterfactual_effective_elapsed_seconds`：累加 replay 的 `default_prefix_execution_seconds + evaluator elapsed_seconds`；
- 缺失任一配对或 timing 不可用的 case 不进入对应分母，并单独输出 available pair count。

不新增 online 运行，不把 counterfactual effective time 解释成真实离线总耗时。

### 4.9 文档更新

修改文件：

- `docs/apis/evaluate.md`
- `docs/apis/experiment.md`
- `docs/apis/judges.md`
- `README.md` 中实验输出路径和指标部分

文档必须明确：

1. hard constraint 与 stage quality 是两层判断；
2. hard pass 不保证 stage pass；
3. stage score `<0.4` 的 quality stop 是预期行为；
4. CheapJudge 始终先运行，Standard/Expensive 由动态路由决定；
5. 缺失工具诊断来自通用 stage goal semantics；
6. repeat=1 和 repeat>1 的路径差异；
7. repeat_statistics、rank_tau_by_repeat、model_pairs、case_discriminability 和 by_method 指标结构；
8. replay marginal、offline total 和 counterfactual effective 三种时间口径。

## 5. 测试方案

项目当前未保留测试目录。代码落地时新增以下 PyTest 文件；测试调用真实项目接口，不为测试新建生产接口。

### 5.1 `tests/evaluate/test_diagnostics.py`

1. `test_parallel_tool_results_use_result_function_name`
   - 两个并行 calls、反序或同序 results；
   - failed/empty 记录均归到正确工具。
2. `test_parallel_tool_results_fall_back_to_call_id`
   - result 不含 function name、含 correlation id；
   - 正确关联 call。
3. `test_ambiguous_parallel_result_is_not_guessed`
   - 并行 result 缺少 function name 和 call id；
   - `tool_name=null`、`attribution_status=ambiguous`。
4. `test_missing_tool_hint_uses_generic_semantics`
   - 分别使用 `get_current_timestamp`、`search_contacts` 和任意自定义工具；
   - 同一代码路径输出具体 name/arguments。

### 5.2 `tests/evaluate/test_policy.py`

1. `test_hard_failure_reports_structural_basis`
2. `test_low_quality_score_still_stops_when_hard_passes`
   - `hard_constraints_all_pass=true`、stage score 0.2742；
   - 仍为 fail 和 `evaluation_policy_stop`；
   - `failure_basis=quality_score`。
3. `test_fatal_minefield_reports_minefield_basis`
4. `test_no_progress_and_predecessor_gap_keep_existing_stop_behavior`

### 5.3 `tests/harness/test_paths.py`

1. experiment repeat_count=1 固定生成 `/r0/`；
2. experiment repeat_count=3 分别生成 `/r0/`、`/r1/`、`/r2/`；
3. 从 repeats=1 增至 3 时，repeat 0 的路径完全不变；
4. 三次 repeat 的 existing output cache 不串用；
5. 非 experiment harness 路径不增加 `r0`；
6. 路径不包含时间戳、UUID 或哈希。

### 5.4 `tests/experiment/test_config.py`

1. repeats=3 展开 3 个 repeat index；
2. `repeat_count=3` 写入所有 spec/harness metadata；
3. repeat index 越界被拒绝；
4. `toolsandbox_partial_main` 展开为 24 个 `ExperimentRunSpec`（4 models × 2 methods × 3 repeats），每个 spec 含 25 个 case，完整执行应产生 600 条 case result。

### 5.5 `tests/experiment/test_metrics.py`

1. repeat score 先按 repeat/case 求均分，再计算 repeat 标准差；
2. success rate 方差正确；
3. rank tau by repeat 只配对相同 repeat；
4. DS model pair 名称、差值和 epsilon 严格比较正确；
5. case dispersion mean/median/P90/P95 正确；
6. efficiency/cost by_method 不混合 DEFAULT/replay；
7. marginal、offline total、counterfactual effective 三种时间口径正确。

### 5.6 覆盖率

对本次新增或修改的核心逻辑运行：

```powershell
uv run pytest --cov=dynsteer.evaluate.diagnostics --cov=dynsteer.evaluate.policy --cov=dynsteer.experiment --cov=dynsteer.harness.paths --cov-report=term-missing
```

本次新增核心逻辑覆盖率不得低于 80%。

## 6. 实施顺序

1. 修复 diagnostics 的并行工具归因。
2. 增加通用缺失工具/参数诊断。
3. 增加 failure basis metadata，确认停止行为不变。
4. 增加 `repeat_count` 和短 repeat 路径。
5. 将实验配置改为 repeats=3。
6. 增加 repeat、DS、case dispersion、method efficiency/cost 指标。
7. 补充 PyTest。
8. 更新 API 文档和 README。
9. 运行单元测试、编译检查和配置展开检查。
10. 不在代码修改阶段自动重新运行 600 条远端模型实验；完整实验由用户确认运行时机后执行。

## 7. 验收标准

### 7.1 评估行为

- DF reminder 的低质量阶段仍得到 fail 和 policy stop；
- hard pass 不再被误解为 stage 必须 pass；
- 所有 stop 都有明确 `failure_basis`；
- 不读取 virtual stop 之后的轨迹参与 stop 决策；
- ToolSandbox milestone 和成功语义未被改写。

### 7.2 诊断

- 当前审计发现的 12 条并行工具名错配全部消失；
- 任意 tool-call constraint 都输出具体 tool name 和 arguments；
- 无具体工具特判。

### 7.3 Repeat

- 配置展开 3 个 repeat；
- repeat 0/1/2 文件路径不同；
- repeat 缓存不串用；
- index 共有 600 条结果时，每条 output path 指向对应 repeat；
- repeats=1 的 experiment 结果位于 `r0`，后续增加 repeats 时可直接复用；
- 非 experiment harness 路径不变。

### 7.4 指标

- `scores.json` 继续输出跨 repeat 的模型总均分；
- `metrics.json` 增加 repeat 方差和逐 repeat tau；
- DS 可定位具体显著模型对；
- case 内区分度同时有均值和稳健分位数；
- efficiency/cost 可直接按 DEFAULT/replay 比较；
- 三种 replay 时间口径不混淆。

### 7.5 工程质量

- PyTest 全部通过；
- 新增核心逻辑覆盖率不低于 80%；
- Python 编译无错误；
- 中文文档和日志 UTF-8 正常；
- 无未使用 import、函数或参数；
- 不产生 run/version/hash 长目录；
- 不修改 ToolSandbox 源码和连接配置。

## 8. 风险与控制

### 8.1 Repeat 运行时间

风险：从 1 次提升到 3 次会把 Agent/User Simulator 远端执行量近似放大到 3 倍。

控制：

- 不提升到 5；
- 不提高 ToolSandbox 并发；
- 先完成代码测试和配置展开验证，再启动完整实验；
- force_eval=False 时可复用同 repeat 已完整存在的产物。

### 8.2 指标 JSON 体积

风险：DS 每个 epsilon 输出模型对明细。

控制：当前只有 4 个模型、每阈值 6 对，体积很小；不输出逐 case 完整 trajectory 或 prompt。

### 8.3 Repeat 路径变化

风险：所有 experiment case 路径都增加 `rN`，当前无 repeat 层的旧产物不能被新路径直接发现。

控制：

- 所有 experiment 从第一次运行开始就使用稳定的 `r0/r1/r2`；
- 使用最短稳定名称，后续增加 repeats 不改变已有 repeat 路径；
- 本轮代码行为会变化，不迁移或复用旧 evaluator 产物，避免把旧逻辑 r0 与新逻辑 r1/r2 混合；
- 所有路径通过唯一 `case_output_dir()` 生成，禁止各调用方重复拼接。

## 9. 预期修改文件清单

| 文件 | 修改内容 |
|---|---|
| `dynsteer/evaluate/diagnostics.py` | 并行 tool result 归因；通用缺失工具/参数诊断 |
| `dynsteer/model.py` | `EvaluationFailureBasis` enum |
| `dynsteer/evaluate/policy.py` | termination failure basis，不改变停止阈值 |
| `dynsteer/evaluate/settlement.py` | stage failure basis，不改变 stage score/status |
| `dynsteer/evaluate/step.py` | no-progress/predecessor-gap failure basis |
| `dynsteer/evaluate/evaluator.py` | minefield/replay termination metadata 对齐 |
| `dynsteer/experiment/model.py` | `repeat_count` 字段和校验 |
| `dynsteer/experiment/config.py` | repeat_count 注入 spec/metadata |
| `dynsteer/harness/paths.py` | repeat>1 的短路径层 |
| `dynsteer/experiment/metrics.py` | repeat、DS pair、case dispersion、method metrics |
| `data/experiments/toolsandbox_partial_main.json` | repeats 1 → 3 |
| `docs/apis/evaluate.md` | 两层失败语义和 failure basis |
| `docs/apis/experiment.md` | repeat 路径、稳定性和新指标 |
| `docs/apis/judges.md` | Cheap-first 和动态路由现状 |
| `README.md` | 输出路径和指标摘要 |
| `tests/evaluate/test_diagnostics.py` | 诊断测试 |
| `tests/evaluate/test_policy.py` | 质量停止不回退测试 |
| `tests/harness/test_paths.py` | repeat 路径测试 |
| `tests/experiment/test_config.py` | repeat 展开测试 |
| `tests/experiment/test_metrics.py` | 指标测试 |

## 10. 明确不修改的文件与行为

- `../ToolSandbox/**`
- ToolSandbox scenario messages、milestones、tool allow list
- Agent/User Simulator 执行方式
- pass/warn/fail 阈值
- dynamic weighting 公式
- Cheap/Standard/Expensive routing 主流程
- `replay_continue_after_virtual_stop` 语义
- online evaluator 实验方法
- 当前实验连接配置
- experiment id 最新结果覆盖策略

## 附录A. 项目中没有把握实现的模块部分

本方案内没有无法实现的核心模块。以下两项存在实验结论不确定性，但不影响代码可实现性：

1. **Repeat=3 后的方差大小。** 远端 Agent、User Simulator 和 Judge 的实际随机性只有完整实验后才能确定；代码只能保证正确分组、落盘和统计。
2. **按 method 的效率结论。** 当前 Agent trajectory token 仍可能由上游 ToolSandbox/provider 返回能力限制而不可用；本方案会准确输出 available count，不会用 0 冒充真实成本。

以下内容因缺少可靠、通用且不改变 benchmark 语义的实现依据，明确不实施：

- 自动把合并用户指令映射为多个顺序 milestone 完成；
- 根据 DEFAULT 未来后缀推迟 virtual stop；
- 用 online DYNSTEER 轨迹替代同轨迹 replay 效率对照。
