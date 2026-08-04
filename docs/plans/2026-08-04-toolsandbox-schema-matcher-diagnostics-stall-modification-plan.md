# ToolSandbox 结果口径、matcher、诊断与原生会话限制对齐方案

> 日期：2026-08-04  
> 依据：`docs/plans/analysis/toolsandbox_partial_main_evaluation_audit.md`、`toolsandbox_partial_main` 的 results/runs 复核结果，以及本轮确认意见。  
> 复测配置：`data/experiments/toolsandbox_partial_retest.json`

## 1. 目标与范围

本次修改只处理以下四项：

1. 统一 DEFAULT 与 DYNSTEER_REPLAY 的结果 schema 和成功口径。
2. 修复 ToolSandbox 动态工具轨迹、引用快照和状态 preserve matcher。
3. 降低成功空返回、合法空查询等运行诊断噪声。
4. 保持现有 DynSTEER harness 会话终止机制，不新增重复话术或重复空查询 detector。

明确不在本次范围内：

- 不修改 `policy-stop` 的立即暂停语义。
- 不增加 recovery grace window。
- 不设置 `replay_continue_after_virtual_stop=true`。
- 不因完整源轨迹后续可能恢复而覆盖当前阶段的失败结论。
- 不调整模型区分度、judge 模型、成本统计和评估权重。
- 不新增 `max_repeated_user_utterances`、`max_repeated_empty_query_results` 或其他 ToolSandbox 原生不存在的 stall 规则。

## 2. 已确认事实

### 2.1 DEFAULT 二元阈值现状

ToolSandbox 原生 `Evaluation.evaluate()` 只返回连续值：

- `milestone_similarity`
- `minefield_similarity`
- `similarity`
- milestone/minefield mapping

ToolSandbox 的 `EvaluationResult` 没有 `resolved`、`success` 或二元成功阈值。当前二元判定来自 DynSTEER：

```python
BenchmarkDefaultResult(score=score, resolved=score >= 1.0, ...)
```

位置：`dynsteer/adapter/toolsandbox/harness.py::default_result_from_session()`。

因此，当前 `1.0` 是 DynSTEER adapter 额外添加的二元判定，不是 ToolSandbox 原生规定。本方案不再修改或新增这个二元阈值。

### 2.2 评分保留原则

ToolSandbox 只定义连续轨迹评分，不定义二元 success。`similarity` 同时包含状态、工具、文本和 minefield 影响；因此不应在 DEFAULT 或 DYNSTEER_REPLAY 任一侧再人为设定阈值生成二元成功结论。

- 保留 DEFAULT 原生 `similarity`、`milestone_similarity`、`minefield_similarity` 及 mapping。
- 保留 replay `overall_score`、`milestone_coverage`、stage reports 和 minefield 结果。
- `milestone_coverage=full/partial/none` 只表示 replay 的结构化覆盖状态，不转换成 success。
- 新结果 schema 完全不写入 `resolved`、`success_basis` 或其他二元 success 字段。
- 指标比较使用连续分数、覆盖状态和终止原因，不再计算 DEFAULT/replay 的二元 success consistency。

### 2.3 已确认 matcher 问题 case

复测 case：

```text
find_days_till_holiday_wifi_off_alt
```

该 case 中的原生 milestone DAG 为：

```text
m0 -> m3
m1 -> m2 -> m3 -> m4
```

关键行为：

1. m0 调用 `get_current_timestamp`。
2. m1 把 Wi-Fi 从 false 改为 true。
3. m2 调用 `search_holiday`。
4. m3 调用 `timestamp_diff`，其参数由 m0、m2 的工具结果动态派生。

当前 DynSTEER 在最早通过 m0 的边界立即、永久绑定 m0 快照。后续 m1 合法改变 Wi-Fi 后，m3 的 preserve-state 仍引用早期 m0 快照，于是把 `wifi=false -> true` 当成状态破坏。ToolSandbox 原生 matcher 是全轨迹 DAG 优化，允许把 m0 映射到仍包含原工具轨迹、但 SETTING 已更新的后续快照，因此不会产生同样冲突。

当前报告还把 `tool_trace_dependant_similarity` 的动态参数显示为 `expected_arguments={}`。这里的空对象不是“工具必须无参数”，而是参数要由 reference snapshot 的 extractor 在运行期填充。诊断文本把动态参数误写成字面空参数，造成错误定位。

实验中：

- 11/12 条记录明确因 m3 的 SETTING preserve 引用早期 m0 而失败。
- qwen-plus 的 3 条记录同时显示误导性的 `timestamp_diff expected_arguments={}`。
- 剩余 1 条 deepseek-v4-flash 记录在更早的 `search_holiday` 参数上失败；该 agent 行为应继续保留为失败，不能被 matcher 修复误放行。

其他报告中出现 `Required tool call not matched` 的 scenario，经完整轨迹复核主要属于以下情况：

- 当前阶段确实没有调用目标工具。
- 目标工具在 policy-stop 之后才调用。
- 工具名相同但业务参数不符合目标。

这些情况符合当前阶段立即结算的设计，不纳入 matcher bug case。

## 3. 代码修改总体结构

本次不新增生产模块，修改集中在现有职责文件：

```text
dynsteer/
├── adapter/
│   ├── base.py                         # DEFAULT 连续评分字段
│   └── toolsandbox/
│       ├── adapter.py                  # 刷新动态约束语义元数据
│       ├── harness.py                  # DEFAULT 连续评分，保持现有 max_messages
│       ├── scorer.py                   # 动态 reference snapshot 评分
│       └── utils/scenario.py           # 动态工具参数匹配策略标记
├── evaluate/
│   ├── diagnostics.py                  # 空结果与动态参数诊断降噪
│   ├── runtime.py                      # reference anchor 构造
│   └── settlement.py                   # 已匹配 milestone 的引用锚点刷新
├── experiment/
│   ├── model.py                        # 统一连续评分字段
│   └── runner.py                       # 统一结果字段抽取
├── harness/outputs.py                  # summary/report/raw 输出统一
└── model.py                            # report、session、runtime state 字段

tests/
├── adapter/toolsandbox/
│   ├── test_default_result.py
│   ├── test_reference_anchor.py
│   └── test_session_limits.py
├── evaluate/test_diagnostics.py
└── experiment/test_result_schema.py
```

## 4. 修改一：统一结果 schema，但不生成二元 success

### 4.1 DEFAULT 结果

修改 `dynsteer/adapter/base.py::BenchmarkDefaultResult` 和
`dynsteer/adapter/toolsandbox/harness.py::default_result_from_session()`：

- 保留 `score`、`milestone_similarity`、`minefield_similarity`、`milestone_mapping`、`minefield_mapping`。
- 不再执行 `resolved = score >= 1.0`。
- 新的 BenchmarkDefaultResult 不包含 `resolved`、`success_basis` 或 success threshold 字段。
- 不新增 `default_success_threshold`、`success_threshold` 或任何替代阈值。

### 4.2 replay 结果

修改 `dynsteer/model.py::TrajectoryEvaluationReport`：

- 保留 `overall_score`、`milestone_coverage`、stage reports、minefield matches 和终止信息。
- `milestone_coverage` 继续表达 `full/partial/none` 的结构化覆盖状态，但不派生 `resolved=true/false`。
- 新的 TrajectoryEvaluationReport 不包含 `resolved`、`success_basis` 或其他二元 success 字段。

### 4.3 实验索引与指标

修改 `dynsteer/experiment/model.py::ExperimentCaseResult`：

- 删除 `resolved` 字段。
- 删除 `successful` 属性及所有二元 success 调用路径。
- `milestone_coverage` 仅作为 replay 诊断字段保留。

修改 `dynsteer/experiment/runner.py::_case_result_from_output()`：

- 新产物不读取、不写入 `resolved` 或 `success_basis`。
- 旧产物中的 `resolved=true/false` 只允许在迁移/读取兼容层丢弃，不得回写到新结果，也不得参与 metrics。

修改 `dynsteer/harness/outputs.py`：

- DEFAULT/replay 的 summary、report、raw 和 index 统一输出连续分字段。
- 统一只写入连续 score、score components、coverage、minefield 和 termination 字段；不写入 `resolved` 或 `success_basis`。
- 输出 metadata 中记录 `result_schema_version: 2`。

### 4.4 指标替代方案

删除或停用 `success_consistency` 二元一致率。改为输出：

- `score_delta`：两种方法连续分差异。
- `coverage_counts`：replay 的 full/partial/none 计数。
- `minefield_counts`：命中和未命中计数。
- `termination_counts`：natural end、max_messages、policy stop 等终止原因。
- `score_rank_tau`：按模型连续均分计算排名一致性。

`milestone_coverage` 可以用于诊断和分层统计，但不能被命名或呈现为 benchmark success rate。

### 4.5 配置删除

不新增以下配置：

```text
default_success_threshold
success_threshold
default_milestone_threshold
```

现有 `threshold_profiles` 仅继续用于 DynSTEER 内部阶段评估策略，不用于生成 ToolSandbox benchmark 的二元成功结果。

## 5. 修改二：修复工具轨迹与状态 matcher

### 5.1 动态参数语义不能退化为字面 `{}`

修改 `dynsteer/adapter/toolsandbox/utils/scenario.py::constraint_from_snapshot_constraint()`：

- 当 `snapshot_constraint_name == "tool_trace_dependant_similarity"` 时，在 `stage_goal_semantics` 中写入：

```json
{
  "kind": "tool_call",
  "tool_name": "timestamp_diff",
  "argument_match_policy": "reference_derived",
  "reference_milestone_node_index": 0,
  "extractor": "result_to_timestamp0_extractor"
}
```

- 不再把缺少静态实参表示成 `arguments: {}`。
- 对真正零参数的 `get_current_timestamp` 保持 `argument_match_policy: "exact"` 和 `arguments: {}`。
- `adapter.refresh_task_case_for_experiment()` 同步刷新 expected、stage semantics 和 ToolSandbox 动态 metadata，不能只刷新 `constraint.expected`。

### 5.2 分离阶段结算与引用锚点

policy-stop 和阶段首次结算保持不变，但用于后续依赖约束的 reference snapshot 不能永久停留在最早快照。

修改 `dynsteer/model.py::RuntimeEvaluationState`：

- 保留现有 `matched_settlements`，它仍代表阶段首次成功/失败结算，不回退、不重试。
- 新增 `reference_anchor_snapshots: dict[str, StateSnapshot]`，只用于后续 reference-dependent constraint 取值。

修改 `dynsteer/evaluate/settlement.py`：

- milestone 首次通过时，同时写入 settlement 和 reference anchor。
- 后续每个新 boundary 到达时，只对“已经 matched 且被未完成 milestone 引用”的 ToolSandbox milestone 检查其约束是否仍成立。
- 若仍为 PASS，把引用锚点前移到较新的快照；不重算原阶段分、不改变 coverage、不撤销 policy-stop、不产生新的成功机会。
- 若新快照不满足原 milestone，保持旧锚点。

该行为对应 ToolSandbox 原生全轨迹 matcher 的“同一 milestone 可以映射到更晚、仍满足约束的快照”，同时不违背 DynSTEER 对当前阶段即时结算的原则。

修改 `dynsteer/evaluate/runtime.py::scoring_context()`：

- 优先把 `reference_anchor_snapshots` 放入 `ScoringContext.matched_snapshots`。
- settlement boundary 仍作为 stage trace 起点，不被引用锚点前移影响。

### 5.3 ToolSandbox scorer

修改 `dynsteer/adapter/toolsandbox/scorer.py`：

- `tool_trace_dependant_similarity` 继续调用 ToolSandbox 原生 extractor 和 measure，不自造近似参数算法。
- `_reference_dataframe()` 从 scoring context 获得已刷新 reference anchor。
- reference evidence 增加：

```json
{
  "reference_anchor_policy": "latest_still_satisfied",
  "reference_snapshot_id": "toolsandbox:...",
  "reference_milestone_id": "m0"
}
```

- preserve-state 必须使用约束指定的 reference milestone；不得简单改成直接 predecessor，以免改变原生 scenario 语义。

### 5.4 防止过度放宽

修复后仍必须失败的情况：

- 当前阶段未调用目标工具。
- 工具只在 policy-stop 之后调用。
- `search_holiday("Christmas")` 与明确要求的 `"Christmas Day"` 不满足 exact 参数策略。
- 动态参数 extractor 得到的 timestamp 与实际 `timestamp_diff` 参数超出原生 `atol_dict` 容差。
- 状态确实修改了不允许改变的 namespace。

## 6. 修改三：降低诊断噪声

修改 `dynsteer/evaluate/diagnostics.py::build_quality_diagnostics()` 与 `_classify_empty_tool_result()`。

### 6.1 成功但无 payload 的状态变更

以下条件不产生 warning：

```text
tool_result.success == true
tool_result.exception is None
tool_result.content is None
tool name 属于 set_/add_/modify_/remove_/send_ 等 mutation 工具
```

这类记录放入 `informational_tool_results`，类别为 `state_mutation_no_payload`；不再混入 `empty_tool_results` 的问题计数。

### 6.2 合法空查询

查询工具成功返回 `[]`、空 dataframe 或空对象时：

- 默认 severity 改为 `info`，类别为 `query_no_match`。
- 不因为下一条 agent 消息存在就自动生成 `agent_answer_after_empty_tool_result`。
- agent 声称“找到具体实体”但工具返回空，才应由 grounding/semantic 检查产生 warning；简单的“未找到，请补充信息”不告警。

### 6.3 真异常保持告警

以下记录继续计入 warning：

- `success=false`
- exception 非空
- tool call/result 无法归因
- 未知工具成功返回无法解释的空 payload
- 空查询之后 agent 给出与空结果冲突的确定性事实

### 6.4 输出结构

统一输出：

```json
{
  "warning_count": 2,
  "failed_tool_results": [],
  "grounding_warnings": [],
  "informational_tool_results": [],
  "query_no_match_results": []
}
```

`warning_count` 只累计真正 warning/error，不累计 info。

## 7. 修改四：保持现有 DynSTEER 会话限制

### 7.1 ToolSandbox 源码现状

ToolSandbox 已包含两层原生终止机制：

1. `tool_sandbox.common.scenario.Scenario.max_messages` 默认值为 30；`Scenario.play()` 的 while 条件在消息索引达到上限时结束。
2. user simulator 的系统提示要求：任务完成时调用 `end_conversation()`；连续 5 次仍无法完成时也调用 `end_conversation()`。该工具把 `conversation_active` 置为 false，使主循环自然结束。

ToolSandbox 没有以下源码级硬限制：

- 重复 user utterance 计数器。
- 重复空查询签名计数器。
- `simulator_stall` termination。
- 基于文本规范化或空结果次数的额外停止规则。

因此本次不新增这些机制，保持 ToolSandbox 原生行为和可比性。

### 7.2 DynSTEER harness 保持现状

当前 `ToolSandboxHarness.start_case()` 使用：

```python
max_messages = int(config.metadata.get("max_messages", 100))
```

本次不修改该逻辑。DynSTEER harness 默认 `max_messages=100` 是当前实验运行口径，保持不变；不因 ToolSandbox 原生 scenario 默认值为 30 而做对齐。

- 未显式配置时继续使用现有隐式默认 100。
- 显式配置时继续沿用现有 override 行为。
- 继续沿用现有 `conversation_active=false` 和 max_messages 判断，不添加第三种停止条件。

### 7.3 终止记录

不改变终止行为，只把已有原因结构化：

```text
natural_end_conversation
max_messages
role_error
```

- user simulator 主动调用 `end_conversation()` 记为 `natural_end_conversation`。
- 达到 scenario/override 上限记为 `max_messages`。
- 不新增 `simulator_stall`。
- 终止原因只用于分析，不覆盖 DEFAULT/replay 的连续 score。

### 7.4 复测配置

`toolsandbox_partial_retest.json` 的四个模型均不显式设置 `max_messages`，复测沿用 DynSTEER harness 当前隐式默认值 100。

## 8. 复测配置

已新增：`data/experiments/toolsandbox_partial_retest.json`。

配置保持主实验的：

- 4 个 agent 模型
- DEFAULT 与 DYNSTEER_REPLAY 两种方法
- 3 次 repeat
- default threshold profile

仅保留已确认 matcher 问题 case：

```json
"case_ids": [
  "find_days_till_holiday_wifi_off_alt"
]
```

理论运行数：

```text
4 models × 2 methods × 3 repeats × 1 case = 24
```

配置已通过 `expand_experiment_matrix()` 验证，可展开为 24 个唯一 run spec。

新配置不复制主配置中的明文 API key，改用：

- `DEEPSEEK_API_KEY`
- `DASHSCOPE_API_KEY`

运行前需在环境变量中提供对应值。

## 9. 测试与验收

### 9.1 单元测试

#### 连续评分 schema

- DEFAULT 的 `similarity`、`milestone_similarity`、`minefield_similarity` 原样保留。
- replay 的 `overall_score`、`milestone_coverage`、stage score 原样保留。
- 两种方法的结果均不包含 `resolved`、`success_basis` 或隐含的二元成功测试。
- 不存在 `default_success_threshold`、`success_threshold` 或其他替代阈值。
- summary、report、raw、index 的连续分字段一致。

#### matcher

- 真正零参数的 `get_current_timestamp` 使用 exact `{}`。
- `tool_trace_dependant_similarity` 使用 `reference_derived`，诊断不显示字面 `{}`。
- m0 早期通过、m1 改 Wi-Fi、m2 通过后，m0 reference anchor 可前移到仍满足 m0 的新快照。
- 前移只改变 reference anchor，不改变 m0 原 stage settlement、stage score 或 policy-stop。
- `timestamp_diff` 动态参数由 m0/m2 extractor 填充后通过原生 scorer。
- 参数不符合 extractor/容差时继续失败。
- 工具只在 policy-stop 后出现时继续失败。

#### 诊断

- mutation 成功返回 None：info，不增加 warning_count。
- query 成功返回 []：`query_no_match` info。
- query 返回 [] 后 agent 回答“未找到”：不产生 grounding warning。
- query 返回 [] 后 agent 声称找到实体：产生 grounding warning。
- exception 和 `success=false`：继续产生 warning。

#### ToolSandbox 原生会话限制

- 未显式 override：使用 DynSTEER harness 当前隐式默认值 100。
- experiment 显式配置 `max_messages`：继续使用现有 override。
- `conversation_active=false`：按原生自然结束。
- 达到 max_messages：按原生上限结束。
- 不存在重复话术、重复空查询或 `simulator_stall` 分支。

核心新增逻辑单元测试覆盖率要求不低于 80%。

### 9.2 复测实验验收

运行 `toolsandbox_partial_retest` 后验收：

1. 24 份 trajectory、raw summary、summary 和 report 全部存在。
2. 12 条 replay 记录均不包含 `resolved`、`success_basis` 或其他二元 success 字段。
3. 正确执行 Wi-Fi→holiday→timestamp_diff 的轨迹，不再因 m3 引用早期 m0 的 SETTING 而 partial。
4. qwen-plus 的动态 `timestamp_diff` 参数诊断显示 `reference_derived` 证据，不显示误导性 `expected_arguments={}`。
5. deepseek-v4-flash 若仍使用不符合 exact 目标的 `Christmas`，允许继续失败，证明 matcher 未过度放宽。
6. policy-stop 触发时仍立即截断，行为与修改前一致。
7. 成功 None 返回和合法空查询不增加 warning_count。
8. 所有模型沿用 DynSTEER harness 当前 max_messages 行为，不新增重复话术或重复空查询终止。

### 9.3 回归检查

- 重新生成 `scores.json` 和 `metrics.json`，确认不再输出二元 success consistency，改为 score delta、coverage 和 termination 分组。
- 检查旧 replay cache 缺失 schema v2 时会重建，且不会把 `milestone_coverage` 转换成二元 success。
- 检查 DEFAULT 和 replay 连续 score 均保持各自原生/阶段评分值，不存在二元 success 派生。
- 检查 policy-stop、stage score、milestone coverage 和 replay trajectory 截断规则无改动。

## 10. 实施顺序

1. 先实现统一连续评分 schema，删除 DEFAULT/replay 二元 success 派生，补齐 schema 测试。
2. 实现动态工具参数语义标记和 reference anchor 分离。
3. 用合成 DAG 单测 matcher，再运行 `toolsandbox_partial_retest`。
4. 实现诊断分级，更新 warning_count 测试。
5. 保持 ToolSandbox harness 当前 max_messages 行为，验证 DEFAULT 和 replay 两条执行路径。
6. 运行全部 pytest、复测实验和结果结构检查。
7. 检查无冗余字段、未使用函数和重复归一化逻辑。

## 11. 约束检查

- 不修改或删除已有 `docs/constraints`、`docs/plans` 文档。
- 不复制实验配置中的明文凭据。
- 不引入懒加载或动态 re-export。
- 新增/修改核心函数提供类型标注和中文 docstring。
- matcher 复用 ToolSandbox 原生 scorer/extractor，不复制一套近似评分算法。
- 不新增 ToolSandbox 原生不存在的 simulator stall 检查。
- 不主动执行 git commit。

## 附录A. 项目中没有把握实现的模块部分

1. **连续分数的跨方法可比性**：DEFAULT 的 ToolSandbox 原生 `similarity` 与 replay 的 DynSTEER `overall_score` 都是连续分数，但内部组成不同；本方案保留并并列呈现它们，不把它们强行压成统一二元 success。若未来需要统一连续标尺，仍需单独设计校准实验。
2. **reference anchor 刷新的完整等价性**：`latest_still_satisfied` 能直接修复已确认 Wi-Fi case，并保持即时阶段结算，但 ToolSandbox 原生采用全局 DAG 最优化。对所有复杂分支图是否完全等价，需要通过更多原生 scenario 回归验证。
