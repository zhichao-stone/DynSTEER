# 2026-08-03 ToolSandbox Replay 语义对齐与多阈值 DS 最终代码修改方案

## 1. 修改范围

本方案仅包含以下代码修改：

1. 修复 ToolSandbox SANDBOX constraint 的消息/工具调用语义。
2. 修复 DEFAULT 轨迹遗漏同一 advance 内较早 SANDBOX rows 的问题。
3. 修复 ToolSandbox minefield-only 空 milestone graph 的完成判定。
4. 修正 replay coverage/recovered-after-stop 输出语义。
5. 删除 PSEP，新增多阈值 Discriminability Score。
6. 增加 DEFAULT/replay 成功结果的一致性清单。
7. 补充对应单元测试和 API 文档。





## 2. 代码修改方案

### 2.1 修复 ToolSandbox SANDBOX constraint 的 route-first 语义

修改文件：

- `dynsteer/adapter/toolsandbox/utils/scenario.py`
- `dynsteer/adapter/toolsandbox/utils/trace.py`
- `docs/apis/stage_goal.md`

#### 2.1.1 `constraint_from_snapshot_constraint()`

当 `namespace == "SANDBOX"` 时按以下顺序生成 `stage_goal_semantics`：

1. 读取首个 expected row；expected rows 为空时抛出包含 `constraint_id` 的 `ValueError`。
2. 规范化 `sender`、`recipient`。
3. 若 route 为 `AGENT/ENVIRONMENT/SYSTEM -> USER`，直接生成 `emit_message`：
   - `kind="emit_message"`；
   - 保留 sender、recipient、content；
   - `match_policy="semantic_equivalent"`；
   - `user_visible_required=True`；
   - 不调用任何 tool-name 解析函数。
4. 仅对以下 route 尝试工具调用解析：
   - `AGENT -> EXECUTION_ENVIRONMENT/ENVIRONMENT`；
   - `EXECUTION_ENVIRONMENT/ENVIRONMENT -> AGENT` 且存在结构化 `tool_trace`。
5. 工具调用必须具有下列至少一种证据：
   - `tool_trace[*].tool_name`；
   - 非空 `openai_function_name`；
   - content 中可完整匹配的 `name(...)` 调用形式。
6. 工具证据存在时生成 `tool_call`，写入明确的 `tool_name` 和已解析 arguments。
7. 既不是用户可见消息、又没有合法工具证据时抛出 `ValueError`，错误中包含 constraint、sender、recipient，不生成 `tool_name="unknown"`。

#### 2.1.2 统一工具调用解析

在 `dynsteer/adapter/toolsandbox/utils/trace.py` 保留唯一的工具调用解析实现：

- `tool_trace_from_row()` 解析结构化 trace；
- `tool_arguments_from_agent_content()` 解析 `*_parameters = {...}`；
- `tool_call_from_agent_row()` 解析 trace、`openai_function_name` 或完整函数调用。

`scenario.py` 复用这些函数；删除 `_sandbox_tool_call_semantics()` 中“从普通 content 提取第一个英文单词”的正则逻辑。若 `scenario.py` 不再使用 `re`，同时删除该 import。

#### 2.1.3 ToolSandbox graph metadata

`milestone_graph_from_scenario()` 先分别构造 `nodes` 和 `minefields`，再写入：

```python
metadata["empty_graph_completion_basis"] = (
    "minefield_only"
    if not nodes and minefields
    else "whole_trajectory"
)
```

该 metadata 只由 ToolSandbox adapter 写入，通用 evaluator 不根据“是否存在 minefield”自行推断 benchmark 语义。

#### 2.1.4 重建 adapted cases

修改完成后使用现有 `force_adapt` 流程重建 `data/toolsandbox/adapted_cases/*.json`，禁止手工编辑生成文件。

### 2.2 修复 DEFAULT trajectory 的 SANDBOX history 完整性

修改文件：

- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/adapter/toolsandbox/utils/state.py`
- `dynsteer/experiment/runner.py`
- `docs/apis/harness.md`

#### 2.2.1 `ToolSandboxHarness.advance_case()`

将新增 row 的读取改为：

```python
all_rows = rows_from_dataframe(
    self._sandbox_database(
        session.context,
        get_all_history_snapshots=True,
    )
)
rows = [
    row
    for row in all_rows
    if sandbox_message_index(row) > session.last_sandbox_message_index
]
rows.sort(key=sandbox_message_index)
```

后续处理顺序固定为：

1. 调用一次 native role `respond()`。
2. 读取全量 SANDBOX history。
3. 按 `last_sandbox_message_index` 过滤本次新增 rows。
4. 按 index 升序转换为 steps。
5. 为每个新增 index 构造历史 snapshot。
6. 所有 steps/snapshots 构造成功后，再把 session 的 last index 更新为本批最大值。

不得继续使用 `get_all_history_snapshots=False` 的最近 snapshot 结果。若本批 rows 包含重复 `sandbox_message_index`，在转换前抛出结构化 `ValueError`，避免生成重复 step id 或覆盖 snapshot。

#### 2.2.2 `snapshots_from_context()`

调整为：

- 输入 steps 必须按 `raw_sandbox_message_index` 升序；
- 每个 step index 调用 `state_from_context(..., sandbox_message_index=index)`；
- 每个 step 生成一个 `snapshot_id="toolsandbox:{index}"` 的 snapshot；
- 删除当前 `step_by_sandbox_index` 静默覆盖重复 index 的行为；
- steps 为空时仍返回空列表；
- 缺少有效 `raw_sandbox_message_index` 时抛出明确错误，不生成不完整 snapshot。

#### 2.2.3 完整性检查

在 `dynsteer/experiment/runner.py::run_default_case()` 读取 `default_result` 后增加内部完整性检查：

- native `milestone_mapping[*].snapshot_index` 必须全部存在于 trajectory snapshot 的 `raw.sandbox_message_index`；
- 缺失时错误包含 benchmark、model、case、milestone 和 snapshot index；
- 检查不修改 native mapping，也不使用最近 snapshot 代替缺失 snapshot。

实现为 runner 文件内的内部函数，直接读取 `output.trajectory_path` 和 `default_result["raw"]["milestone_mapping"]`；非 ToolSandbox benchmark 或没有 milestone mapping 时直接跳过。

### 2.3 修复 ToolSandbox minefield-only 空图完成语义

修改文件：

- `dynsteer/evaluate/final.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/evaluator.py`
- `docs/apis/evaluate.md`

#### 2.3.1 `_empty_graph_finish_verification()`

读取 `graph.metadata["empty_graph_completion_basis"]`：

- 当值为 `minefield_only`：
  - `state.fatal_minefield=True` 时返回 fail、score 0、coverage basis `minefield_only`；
  - 未触发 fatal minefield 时返回 pass、score 1、coverage basis `minefield_only`；
  - 两种情况都设置 `whole_trajectory_evaluation_required=False`。
- 其他值继续使用现有 `whole_trajectory` 预检查和 StandardJudge fallback。

返回 payload 中始终写入 `coverage_basis`，并保留 minefield count/match count。

#### 2.3.2 `finish_settlement()`

保持单一分支条件：

```python
if verification["whole_trajectory_evaluation_required"]:
    ...
else:
    ...
```

minefield-only 必须进入 `_deterministic_finish_stage_result()`，不得调用 `_whole_trajectory_finish_stage_result()`；其他 benchmark 的空图行为保持不变。

#### 2.3.3 `DynSTEEREvaluator._runtime_report()`

空 graph 时按 finish stage 的 `finish_stage_evaluation.coverage_basis` 计算 coverage：

- `minefield_only + pass/warn -> full`；
- `minefield_only + fail/invalid -> none`；
- `whole_trajectory` 继续使用现有 pass/warn/fail 映射。

把当前 `_empty_graph_whole_trajectory_coverage()` 改为可处理上述两个 basis 的通用内部函数，函数仍位于 `evaluator.py` 文件末尾。

#### 2.3.4 `build_replay_execution_summary()`

增加显式入参：

```python
coverage_basis: str
```

删除硬编码的 `"coverage_basis": "milestone_graph"`，直接写入调用方传入值。调用方从 finish stage metadata 读取；有真实 milestone graph 时传 `milestone_graph`。

同时增加：

```python
"recovered_after_virtual_stop": (
    termination.should_stop and coverage == "full"
)
```

顶层 `replay_execution`、report metadata 和 finish settlement metadata 复用同一个 summary 对象，禁止各自重新推导字段。


### 2.4 以多阈值 Discriminability Score 替换 PSEP

修改文件：

- `dynsteer/experiment/model.py`
- `dynsteer/experiment/runner.py`
- `dynsteer/experiment/metrics.py`
- `docs/apis/experiment.md`

#### 2.4.1 `ExperimentCaseResult`

增加：

```python
milestone_coverage: str | None = None
```

增加只读属性：

```python
@property
def successful(self) -> bool | None:
    if self.method == ExperimentMethod.DEFAULT:
        return self.resolved
    if self.milestone_coverage is None:
        return None
    return self.milestone_coverage == "full"
```

`_case_result_from_output()` 从 replay `summary.json` 读取 `milestone_coverage`。不持久化重复的 success 字段。

#### 2.4.2 删除 PSEP

从 `dynsteer/experiment/metrics.py` 删除：

- `psep()`；
- `_psep_table()`；
- `metrics.json["psep"]`。

不保留旧字段兼容层。

#### 2.4.3 DS 常量和单阈值函数

在 `dynsteer/experiment/metrics.py` 定义：

```python
DISCRIMINABILITY_THRESHOLDS = (0.01, 0.02, 0.03, 0.04, 0.05)
```

新增：

```python
def discriminability_score(
    scores: Mapping[str, float],
    epsilon: float,
) -> JsonObject:
    ...
```

实现公式：

```text
DS = (population_stddev / mean_score)
     * sqrt(significant_pair_count / pair_count)

significant(i, j) = abs(score_i - score_j) > epsilon
```

实现要求：

1. 所有模型得分先通过 `case_score()` 归一到 `[0, 1]`。
2. `population_stddev` 使用总体标准差：
   `sqrt(sum((x - mean_score) ** 2) / model_count)`。
3. 模型对只枚举一次，pair count 为 `m * (m - 1) / 2`。
4. 阈值判断使用严格大于 `epsilon`；恰好等于阈值不计入。
5. `mean_score == 0` 时所有非负归一化得分必为 0，DS 返回 `0.0`。
6. 模型数不足 2 时 DS 返回 `null`，同时保留 model count 和 pair count。
7. `epsilon < 0` 时抛出 `ValueError`。
8. 返回组件字段，不只返回最终分数：

```json
{
  "epsilon": 0.02,
  "model_count": 4,
  "pair_count": 6,
  "mean_score": 0.8448157778,
  "population_stddev": 0.0249993035,
  "significant_pair_count": 3,
  "significant_pair_ratio": 0.5,
  "score": 0.0209242980
}
```

#### 2.4.4 DS 聚合表

删除 `_psep_table()`，新增 `_discriminability_table(scores)`：

- 输入复用 `model_scores(results)` 的 `method -> benchmark -> model -> mean score`；
- 对每个 method/benchmark 只提取一次模型得分；
- 依次计算 0.01、0.02、0.03、0.04、0.05；
- 阈值 key 固定格式为两位小数：`"0.01"` 至 `"0.05"`；
- 输出结构：

```json
{
  "discriminability_score": {
    "default": {
      "toolsandbox": {
        "0.01": {},
        "0.02": {},
        "0.03": {},
        "0.04": {},
        "0.05": {}
      }
    },
    "dynsteer_replay": {
      "toolsandbox": {
        "0.01": {},
        "0.02": {},
        "0.03": {},
        "0.04": {},
        "0.05": {}
      }
    }
  }
}
```

#### 2.4.5 简单 success consistency

新增 `success_consistency(results)`，按相同 `benchmark/model/case/repeat` 配对 DEFAULT 与每个 replay method，输出：

- pair count；
- consistent/inconsistent count；
- agreement rate；
- DEFAULT success + replay failure count；
- DEFAULT failure + replay success count；
- inconsistent case 列表，包含 model、repeat、case、两侧 success 和 score。

不增加 precision、recall、F1、kappa、Separability、Pearson、Spearman 或其他指标。

#### 2.4.6 `write_metric_tables()`

修改为：

```python
metrics = {
    "efficiency": aggregate_efficiency(results),
    "cost": aggregate_cost(results),
    "discriminability_score": _discriminability_table(scores),
    "rank_tau": _rank_tau_table(scores),
    "success_consistency": success_consistency(results),
}
```

`model_scores()`、`rank_tau`、`aggregate_efficiency()` 和 `aggregate_cost()` 的现有算法保持不变。



### 2.5 统一 replay 输出字段

修改文件：

- `dynsteer/evaluate/evaluator.py`
- `docs/apis/evaluate.md`

保持字段职责：

- `milestone_coverage/final_completion`：最终完成结论；
- `first_failure_stage_id`：首个 fail/missing/invalid stage；
- `virtual_stop_code`：虚拟停止原因；
- `recovered_after_virtual_stop`：发生 virtual stop，但 finish 复核得到 full。

finish 复核结果优先于中间失败诊断；不得使用 `first_failure_stage_id` 或 `virtual_stop_code` 覆盖最终 `milestone_coverage`。

## 3. 文件级修改清单

| 文件 | 修改内容 |
|---|---|
| `dynsteer/adapter/toolsandbox/utils/scenario.py` | route-first SANDBOX 语义；删除首词工具名推导；未知 route fail fast；标记 ToolSandbox minefield-only 空图 |
| `dynsteer/adapter/toolsandbox/utils/trace.py` | 提供唯一的结构化/代码式工具调用解析逻辑供 scenario 和 trajectory 共用 |
| `dynsteer/adapter/toolsandbox/harness.py` | advance 读取全部 SANDBOX history，再按 last index 过滤 |
| `dynsteer/adapter/toolsandbox/utils/state.py` | 按新增 step index 有序构造 snapshot；禁止重复 index 静默覆盖 |
| `dynsteer/evaluate/final.py` | 区分 minefield-only 与真正 empty graph finish |
| `dynsteer/evaluate/settlement.py` | minefield-only 走 deterministic finish，不调用 whole-trajectory Judge |
| `dynsteer/evaluate/evaluator.py` | 正确 coverage basis；增加 recovered-after-stop 展示字段 |
| `dynsteer/experiment/model.py` | 增加 replay milestone coverage 和统一成功属性 |
| `dynsteer/experiment/runner.py` | 校验 native milestone snapshot 完整性；从 summary 装载 milestone coverage |
| `dynsteer/experiment/metrics.py` | 删除 PSEP；增加 0.01–0.05 多阈值 DS 和简单 success consistency；保留现有 scores/rank_tau/efficiency/cost |
| `data/toolsandbox/adapted_cases/*.json` | 通过 force adapt 自动重建，不手工编辑 |
| `docs/apis/stage_goal.md` | route 与 tool-call 推导规则 |
| `docs/apis/harness.md` | 全量 history row 读取和 replay source 完整性 |
| `docs/apis/evaluate.md` | minefield-only coverage、最终字段优先级 |
| `docs/apis/experiment.md` | PSEP 删除、多阈值 DS 公式/schema、success consistency schema |

## 4. 测试方案

### 4.1 新增 `tests/test_toolsandbox_scenario_semantics.py`

覆盖：

1. `AGENT -> USER + content` 必须为 `emit_message`。
2. `SYSTEM/ENVIRONMENT -> USER + content` 必须为 `emit_message`。
3. `AGENT -> EXECUTION_ENVIRONMENT + openai_function_name` 为 `tool_call`。
4. `AGENT -> EXECUTION_ENVIRONMENT + name(...) content` 为 `tool_call`。
5. `EXECUTION_ENVIRONMENT -> AGENT + tool_trace` 为 `tool_call`。
6. 用户消息即使以 `Location`、`Stephen`、`I` 开头，也不得推导 tool name。
7. 非用户可见且无工具证据的 SANDBOX constraint 明确报错。

### 4.2 新增 `tests/test_toolsandbox_harness_history.py`

使用 fake context/session 构造一次 advance 产生多个 SANDBOX rows：

- 两个并行 tool calls；
- 两个 tool results；
- 最后一个 agent message。

断言：

- 所有新增 index 均进入 steps；
- 顺序与 sandbox index 一致；
- 每个 index 都有 snapshot；
- 第二次 advance 不重复第一次 rows。
- 重复 sandbox index 明确报错，不生成重复 step/snapshot id。

### 4.3 新增 `tests/test_toolsandbox_replay_source_completeness.py`

构造 native result milestone mapping，断言所有 mapped snapshot index 均存在于 trajectory。再构造缺失 index，确认测试能明确失败并指出 case/milestone/index。

如 ToolSandbox 本地依赖可用，增加 `modify_reminder_with_recency_latest` 的轻量集成 fixture，验证 `get_current_timestamp` row 不被丢失；测试不调用外部 LLM API。

### 4.4 新增 `tests/test_empty_graph_completion.py`

覆盖：

1. ToolSandbox metadata 标记 minefield-only + no match -> full/minefield_only；
2. ToolSandbox metadata 标记 minefield-only + fatal match -> none/minefield_only；
3. 无 metadata 的 no milestone + minefield -> 仍为 whole-trajectory；
4. no milestone + no minefield -> whole-trajectory；
5. 顶层 replay execution 与 finish metadata coverage basis 一致；
6. minefield-only 非 fatal 路径不调用 StandardJudge。

### 4.5 新增 `tests/test_experiment_metrics.py`

覆盖：

1. DEFAULT/replay 成功一致、DEFAULT 成功而 replay 失败、DEFAULT 失败而 replay 成功三类配对；
2. 缺失配对不进入分母；
3. inconsistent case 清单包含完整 identity、两侧 success 和 score；
4. `metrics.json` 不再包含 `psep`；
5. 总体标准差使用 `ddof=0` 等价公式；
6. 差值恰好等于 epsilon 时不计入 significant pair；
7. 0.01、0.02、0.03、0.04、0.05 五个 key 完整输出；
8. epsilon 增大时 significant pair count 和 DS 不得增加；
9. 全模型零分返回 DS 0；模型不足 2 个返回 DS `null`；负 epsilon 报错；
10. 使用当前四模型均分构造固定 fixture，验证：
    - DEFAULT：0.01→0.027013153、0.02→0.020924298、0.03→0.020924298、0.04→0.020924298、0.05→0.017084618；
    - replay：0.01→0.044234310、0.02→0.036117163、0.03→0.031278381、0.04→0.025538691、0.05→0.018058581；
11. `scores.json`、现有 `rank_tau`、efficiency 和 cost 输出保持；
12. 不新增 Separability、precision、recall、F1、kappa、Pearson/Spearman 或 pairwise model-order 指标。

### 4.6 覆盖率

新增和修改的核心函数 pytest 行覆盖率不低于 80%。测试调用真实公共接口或现有内部逻辑，不为测试新建无业务用途的中转接口。

## 5. 实施顺序

### Phase 1：修复输入语义和轨迹完整性

1. 修改 SANDBOX route-first constraint semantics。
2. 修改 harness 全 history row 读取。
3. 完成 adapter/harness 单元测试。
4. force adapt 重建 25 个 adapted cases。
5. 验证 22 条错误 tool semantics 清零。
6. 验证 native mapped snapshot 缺失清零。

### Phase 2：修复 minefield-only finish 和输出语义

1. 实现 `minefield_only` deterministic finish。
2. 修复 coverage basis。
3. 增加 recovered-after-stop 展示字段。
4. 完成 finish/replay 测试。

### Phase 3：最小化调整实验指标

1. 扩展 `ExperimentCaseResult` 以统一读取 DEFAULT/replay success。
2. 删除 PSEP。
3. 实现 0.01、0.02、0.03、0.04、0.05 五个阈值的 DS。
4. 实现简单 success consistency 和不一致 case 清单。
5. 保持现有 scores、rank_tau、efficiency 和 cost 聚合不变。
6. 更新实验 API 文档。

### Phase 4：全量验收

1. 运行全部 pytest。
2. 使用当前实验配置执行 `force_adapt + force_eval`，保证 DEFAULT 和 replay 均基于新轨迹产物。
3. 重新生成 `index.json`、`scores.json`、`metrics.json`。
4. 验证五个 DS 阈值全部输出并与单元测试公式一致。
5. 重做 100 对成功一致性和轨迹证据核查。

## 6. 验收标准

### 6.1 ToolSandbox 语义与轨迹

- 22 条用户消息 constraint 不再被识别为工具调用。
- 普通消息首词 `Location`、`Stephen`、`I` 不再成为 tool name。
- DEFAULT 已命中的 native snapshot 在 replay source 中缺失数为 0。
- 同一 advance 内所有新增 SANDBOX rows 均生成 step 和 snapshot。
- genuine hard milestones、threshold 和 graph edge 未被删除或降级。

### 6.2 Finish 与 replay 输出

- ToolSandbox minefield-only case 不调用 whole-trajectory Judge。
- minefield-only 无命中输出 full；fatal match 输出 none。
- `coverage_basis` 在 finish metadata、report 和顶层 replay summary 中一致。
- `recovered_after_virtual_stop` 仅在 virtual stop 后最终 full 时为 true。
- DEFAULT/replay 继续使用同一 source trajectory。

### 6.3 实验指标

- `metrics.json` 不包含 `psep`。
- `metrics.json.discriminability_score` 对每个 method/benchmark 包含 `0.01`、`0.02`、`0.03`、`0.04`、`0.05`。
- 每个阈值项包含 model count、pair count、mean score、population stddev、significant pair count/ratio 和 DS score。
- 当前未修复结果 fixture 在 epsilon=0.02 时得到 DEFAULT≈0.020924298、DYNSTEER_REPLAY≈0.036117163。
- success consistency 输出配对数、一致数、不一致数、方向计数和不一致 case 清单。
- `scores.json`、`rank_tau`、efficiency 和 cost 保持可用。
- 全部测试通过，新增/修改核心函数行覆盖率不低于 80%。

## 附录A. 项目中没有把握实现的模块部分

### A.1 ToolSandbox 重复 sandbox index

当前代码和现有结果以 `sandbox_message_index` 同时作为 step index 和 snapshot identity。外部 ToolSandbox 在并行工具调用时是否可能为多 row 分配同一 index，需要先通过真实 fixture 确认。

实施顺序：

1. 先增加重复 index 检测和真实 ToolSandbox 集成测试。
2. 若实际 index 唯一，保留当前 `s{index}` 与 `toolsandbox:{index}` 标识。
3. 若实际允许重复 index，不得仅删除重复 row；需要把 trajectory step identity 改为稳定的 `sandbox index + row ordinal`，并同步调整 snapshot boundary、successor mapping 和相关 API 文档。
4. 未获得 fixture 证据前，不提前修改公共 trajectory index 模型。

除该外部 index 语义外，其余修改均可直接基于当前项目结构实现。
