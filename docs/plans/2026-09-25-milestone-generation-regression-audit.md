# Milestone graph generation 退化审计报告

- 日期：2026-09-25
- 审计对象：当前工作区 `no_agentcompass` 分支的 milestone 自动生成链路
- 当前 HEAD：`533855255a7e841ceb7241c05fbdde957f690f22`
- 主要结果：`results/milestone/toolsandbox_milestone_reliability_main`
- 结果运行时间：2026-09-23 14:19:02Z 至 2026-09-23 15:17:23Z
- 生成模型：`qwen3-max-2026-01-23`，temperature `0.2`，max tokens `4096`
- 本报告只做审计与方案建议，不修改生成代码。

## 1. 结论摘要

本次结果差不是 LLM 服务不可用、JSON 解析失败或输出截断导致的。相反：

- 509/509 个 case 完成，`generation_failed = 0`。
- 1997/1997 次 LLM 请求成功。
- LLM 返回 3979 张候选图，其中 3969 张能被解析成候选图对象，解析成功率约 99.7%。
- 但只有 843 张候选图通过当前确定性校验，解析后有效率仅约 21.2%。
- 3136 张候选图被拒绝；287/509 个 case 没有任何有效候选图。
- 395/509 个最终预测没有节点；其中 335 个是参考图非空而生成图为空。
- 451/509 个 case 处于 `low_sample_count` 状态，只有 58 个 case 达到 6 个有效观测的目标。

因此，主要断点在“候选图校验与聚合”之前或之中，而不是模型基本调用层。

更具体地说，当前实现存在四个确定性不一致或过严点：

1. **prompt 示例与真实 output contract 不一致**：prompt 多处引导 `$.timestamp`、`$.reminders[0].reminder_id` 等字段型 selector；validator 却按私有 `tool_contracts` 要求 raw scalar 工具使用 `$`，search 工具使用 `$.reminder_id`、`$.person_id` 等抽象 selector。
2. **真实 output contract 没有暴露给模型**：prompt payload 只有 `tool_schema` 与 `evidence_catalog`，没有 `tool_contracts`；但 prompt 要求 selector/cardinality 必须匹配 producer 的 real output。模型实际被要求猜一个 hidden contract。
3. **public literal 来源校验与 prompt 提供的来源形态不一致**：prompt 示例硬编码 `instruction:0`，实际 turn source_ref 是 `instruction`；`message:15:content` 这类 public asset 的 value 是整段消息，validator 要求 literal 与整段消息完全相等，而模型合理地从中抽取姓名、电话、时间等子字段，于是被拒。
4. **minefield 语义与参考安全语义不一致**：当前 validator 只允许有 `writes` 的 side-effect 工具作为 fatal minefield；但参考结果中 fatal 正例包含 `timestamp_diff`、`search_reminder` 等只读工具。因此这些 recall 在当前算法下不可达。

这些问题被“一张候选图只要出现任一 issue 就整图拒绝”的策略放大：模型经常已经给出逻辑上正确的工具链，但一个 selector 或 literal provenance 错误会导致完整候选图丢失；后续严格多数聚合只能看到退化后的候选图。

历史版本对比显示：

- `a080ca55` 是旧的“完整路径 ensemble”算法：模型输出 operation path，代码做环境模拟、repair、路径交集与共同 precedence。
- `fadbef85` 将其替换为当前 candidate milestone graph 架构，并引入 exact schema、binding、output contract 与 strict majority aggregation。
- `b19ae47c` 又增加了 `$.timestamp` 日期示例、更强 minimality/insufficient-information 指令和 dangling producer pruning。
- 当前 milestone 相关文件在 `b19ae47c` 之后没有再被后续提交修改；本次 2026-09-23 结果运行的是 `b19ae47c` 这一版生成逻辑。

如果之前较好的实验来自 `a080ca55` 路径 ensemble，那么当前结果与之相悖的根本原因是生成范式与校验范式被整体替换，而不是后续小修小补造成的随机波动。若之前较好结果声称来自 `fadbef85`，则 `b19ae47c` 的新示例与更强 minimality 确实会放大冲突，但 hidden contract 与 public asset 粒度问题在 `fadbef85` 中已经存在。补充核查发现，partial_main artifact 并不是 committed `fadbef85` 的直接输出，不能用于精确量化这次退化；详见第 10 节。


补充核查 `results/milestone/toolsandbox_milestone_reliability_partial_main` 后，需要额外强调：该 artifact 不是当前仓库提交 `fadbef85` 的严格复现。它的 result schema 与原始 LLM response schema 均对应一份当前 Git 历史中找不到的 simulation/milestone 中间实现，且模型为 `qwen-plus-latest`，与当前 `qwen3-max-2026-01-23` 不同。它不能作为 `fadbef85 -> b19ae47c` 的定量 baseline；详见第 10 节。
## 2. 当前算法链路

### 2.1 实验入口与任务视图

入口在 `milestone_reliability.py::_run_case`：

1. 通过 adapter 读取原生 TaskCase 与 reference milestone graph。
2. 调用 `adapter.generator_task_case(...)` 构造 `GeneratorTaskView`。
3. 构造实验配置中的 LLM。
4. 调用 `dynsteer.milestone.compiler.compile_task_case(view, config, llm, response_output_file)`。
5. 对 prediction 与 reference 计算 semantic/strict/structural/topology GED、node F1、operation topology exact、goal effect exact 和 minefield 指标。

ToolSandbox 的视图构造在 `dynsteer/adapter/toolsandbox/utils/contract.py::build_toolsandbox_generator_view`：

- 从 sandbox interaction plan 中提取 turn。
- 将 Agent 可见消息放入 `public_assets`。
- `public_state` 当前固定为 `{}`。
- 初始业务状态放在 `simulation_state`，只给 deterministic recovery 逻辑使用，不进入 prompt。
- `tool_schema` 只包含 Agent 可见工具及其输入 schema。
- `tool_contracts` 与 `environment_rules` 是私有确定性契约，供 validator/compiler 使用。
- ToolSandbox 当前 `public_assets` 的 `source_ref` 形态例如：
  - `message:15:content`
  - `simulator:14:content`
- 首个 turn 的实际 `source_ref` 是 `instruction`，不是 `instruction:0`。

### 2.2 候选图生成批次

`compile_task_case` 的主循环配置为：

- `target_candidate_graph_count = 6`
- `max_candidate_batch_count = 4`
- 每批要求 exactly 2 张 complete standalone candidate graph。
- 达到 6 个有效观测后提前停止。
- 最多 4 次请求。

每批 focus 顺序循环：

| batch | focus | 当前英文指令核心 |
|---|---|---|
| 1 | `minimality` | 严格质疑每个节点，只有删除后任务必然失败才保留；禁止防御性 `shift_timestamp`、`timestamp_diff`、`unit_conversion`；能直接用 public literal 就不要中间 getter/conversion。 |
| 2 | `alternative` | 探索合法替代实现：直接 public state、bulk `set_state`、direct answer tool；不得制造虚假多样性。 |
| 3 | `dependency_safety` | 审计 producer-consumer dataflow；独立 producer 不连边；信息不足时 `response_only`，输出空操作图或澄清消息，并对危险副作用注册 fatal minefield。 |
| 4 | 回到 `minimality` | 同 batch 1。 |

### 2.3 解析：exact JSON schema

`_parse_candidate_batch` 要求：

- 顶层 JSON object 只能有一个 key：`graphs`。
- `graphs` 必须是数组，最多 2 个；少于 2 个记 `batch_incomplete`，多于 2 个整批 schema error。
- 每张图只能有：
  - `dispositions`
  - `nodes`
  - `edges`
  - `minefields`
- node 只允许：
  - `tool_call`
  - `set_state`
  - `emit_message`
- 每种 node 的字段必须精确等于预定义字段集合，不能有额外字段。
- edge 必须是二元数组。
- minefield 字段也必须精确匹配。

“top-level success”只表示返回了合法 JSON shape；它不代表任何一张图通过语义校验。因此 `generation_failed = 0` 与大量空图并不矛盾。

### 2.4 候选图校验：任一 issue 整图拒绝

`_validate_candidate_graph` 做以下校验。注意，它的返回策略是：**只要 `issues` 非空，整张候选图返回 `None`**。

主要规则包括：

1. disposition 必须精确覆盖所有 turn，取值必须在四个合法枚举内。
2. node 的 `turn_id`、kind、local id 必须合法；非 executable turn 不能有 tool/state goal。
3. `tool_call.arguments`：
   - 必须覆盖工具 schema 的 required 参数；
   - 不能包含 schema 不允许的参数；
   - 每个参数必须是 `public_literal` 或 `node_output` binding；
   - public literal 必须通过来源可见性、值一致性和目标 schema 校验。
4. `set_state`：
   - namespace/operation/cardinality/match/values 必须满足结构；
   - 必须与 executor tool 的 effect contract 一致；
   - 必须覆盖 `required_dynamic_inputs`；
   - match/value 也必须通过 binding 校验。
5. `node_output`：
   - producer 必须是同一候选图中的 `tool_call`；
   - producer 不能来自后续 turn；
   - selector 必须精确等于 producer contract 中某个 output selector；
   - cardinality 必须在 contract 声明中；
   - output 类型必须与目标参数 schema 兼容；
   - validator 会自动补 producer→consumer edge。
6. 图结构：
   - edge 端点必须存在；
   - 不允许重复 edge、自环或环；
   - executable turn 必须有 terminal goal。
7. minefield：
   - severity 必须是 fatal；
   - reason code 只允许三种；
   - evidence 必须对应有 `writes` 的副作用契约；
   - `missing_required_input` 的字段必须属于契约 `required_dynamic_inputs`；
   - 若同 turn 已提供所谓缺失输入，则拒绝；
   -同一 terminal effect 不能既是 executable goal 又是 fatal minefield。

### 2.5 canonicalize 与批内去重

通过校验的图会进入 `_canonicalize_candidate_graph`：

- node identity 包含完整语义身份：
  - tool call：turn、kind、evidence_id、每个 argument 的 source identity；
  - set_state：turn、kind、namespace、operation、cardinality、match/values 的 source identity；
  - emit_message：turn、route、规范化后的 content requirement。
- `public_literal` identity 包含 `source_ref` 和 value。
- `node_output` identity 包含 selector 和 cardinality，但不包含 producer local id。
- 图签名由 disposition、节点 canonical key、边和 minefield 组成。
- 同一批内签名相同的候选只保留一个。
- 跨批次相同签名仍作为独立 observation 参与后续多数聚合。

### 2.6 严格多数聚合与编译

`_aggregate_and_compile` 的规则：

1. disposition：每个 turn 的取值必须在全部有效观测中取得严格多数。
2. node：canonical identity 相同的节点必须在全部观测中出现超过半数才保留。
3. binding：同一字段的动态 binding 也需要严格多数；如果无严格多数，只有“唯一 contract-supported selector”可作为兜底。
4. state executor：也必须取得严格多数。
5. edge：只在两端共同出现的观测中计算条件多数。
6. deterministic binding edge 优先加入。
7. 之后加入：
   - recovery dependency；
   - turn-order edge；
   - preserve constraint；
8. `_validate_aggregated_closure` 删除不可闭合节点或未保留 producer 的状态目标。
9. `_prune_dangling_producers` 删除未被消费、无副作用、无依赖的只读 producer。
10. minefield 单独严格多数聚合。
11. 若聚合后的 terminal effect 与 fatal minefield 冲突，则相关 turn 降级为 `response_only`，只保留 emit_message。

这意味着：候选图阶段的一个字段级错误不只是“丢一个参数约束”，而是丢整张图；聚合阶段的 source_ref/selector 差异也不只是审计信息差异，而会把本应相同的节点拆成不同 canonical identity，导致两者都无法过严格多数。

## 3. 当前实际 prompt

### 3.1 模板与语言

Prompt builder 在 `dynsteer/prompt/template.py::MilestonePromptBuilder`。

语言选择逻辑：

1. 读取 `view.language` 并 normalize。
2. 有对应语言模板则使用对应模板。
3. 否则 fallback 到英文模板，再否则第一个可用模板。

本次 ToolSandbox 任务为英文，因此实际使用：

- `dynsteer/prompt/templates/milestone/generation.en.md`

### 3.2 prompt 的宏观结构

当前英文模板依次包含：

1. 任务定位：
   - “You generate candidate milestone goal graphs, not step-by-step trajectories.”
   - 每个节点必须是该候选解释下的必要目标；必要性由后续代码聚合决定。
2. batch 序号与 focus 指令。
3. 每批 exactly 2 张 standalone graph 的要求。
4. diversity 规则：
   - 两张图各自独立；
   - 不允许共享 node table 或 delta/patch；
   - 没有真实替代方案时可以等价；
   - 不得通过省略 producer、添加无关工具或捏造参数制造差异。
5. 三个结构性示例。
6. `Current public task JSON`。
7. Required semantics：
   - turn/disposition；
   - necessity/feasibility/goals；
   - node schema/provenance；
   - edge 语义；
   - minefield 与 legal empty graph；
   - exact response shape；
   - final checklist。
8. 固定返回骨架：
   - `{"graphs":[{...},{...}]}`

### 3.3 payload 中实际暴露给模型的数据

`MilestonePromptBuilder.payload` 只包含：

```text
benchmark
task_id
case_id
language
turns
public_assets             # 只保留 visibility=agent 的资产
public_state              # 叶节点会被包装为 source_ref/value
tool_schema
evidence_catalog
```

其中 `turns` 序列化后包含：

```text
turn_id
instruction
source_ref
```

当前 prompt **不包含**：

```text
tool_contracts
environment_rules
simulation_state
reference_graph
```

这带来一个关键问题：模板要求模型输出 contract-supported selector，但 contract 本身没有进入 prompt。模型只能根据工具名、工具描述和示例猜测。

### 3.4 binding 形式

prompt 要求每个参数和 state 字段只能是两种来源之一。

Public literal：

```json
{
  "source": "public_literal",
  "source_ref": "an exact current public source reference",
  "value": "the literal value from that source"
}
```

Node output：

```json
{
  "source": "node_output",
  "producer_local_id": "a producer in this same graph",
  "selector": "$.field",
  "cardinality": "one"
}
```

prompt 明确要求：

- source_ref 必须来自当前 JSON；
- producer 必须是同图更早可达的 tool call；
- selector/cardinality 必须匹配 producer real output；
- 每个 node-output binding 必须显式加 producer→consumer edge；
- 不允许 `derived` source；
- 不允许把动态 ID、timestamp、坐标等硬编码为 public literal。

这些要求本身合理，但当前任务数据与示例没有给模型足够信息稳定满足它们。

### 3.5 与 validator 冲突的示例

#### 冲突一：raw scalar 输出示例

模板 Example 3 使用：

```json
{
  "source": "node_output",
  "producer_local_id": "n0",
  "selector": "$.timestamp",
  "cardinality": "one"
}
```

但 `dynsteer/adapter/toolsandbox/utils/effects.py` 中以下工具的真实 contract 是：

```python
{
  "value": {
    "selector": "$",
    "type": "number",
    "cardinality": ["one"]
  }
}
```

 affected 工具包括：

- `get_current_timestamp`
- `datetime_info_to_timestamp`
- `shift_timestamp`
- `unit_conversion`
- `search_holiday`
- `calculate_lat_lon_distance`

也就是说，prompt 示例直接引导了一个 validator 必然拒绝的 selector。

#### 冲突二：search 输出示例

模板 Example 2 使用：

```json
{
  "selector": "$.reminders[0].reminder_id"
}
```

而 contract 中 search reminder 的输出是抽象字段 selector：

```text
$.reminder_id
```

cardinality 由 `one/all` 表达，而不是通过 `reminders[0]` 或 `contacts[*]` 路径表达。

模型大量输出：

- `$.reminders[0].reminder_id`
- `$.contacts[0].person_id`
- `$.contacts[0].phone_number`
- `$.messages[0].recipient_person_id`

这些与示例一致，但与 hidden contract 不一致。

#### 冲突三：turn source reference 示例

模板示例使用：

```json
{
  "source_ref": "instruction:0"
}
```

但当前 ToolSandbox view 中首个 turn 的实际 source_ref 是：

```text
instruction
```

虽然模板声称示例 illustrative only，但 exact schema 任务中示例的强烈程度很高。原始输出中确实出现了 155 个 `instruction:0` binding。

#### 冲突四：public asset 粒度

prompt 中的 public asset 是整段消息：

```text
message:15:content -> "整段消息内容"
```

validator 对 public asset 的校验是：

```python
asset_sources[source_ref] == literal
```

即模型如果要引用 `message:15:content`，给出的 value 必须等于整段消息，而不是消息中的姓名、电话或时间。这个设计无法表达“从可见消息中抽取一个字段”。

## 4. 当前结果中的退化信号

### 4.1 summary.json 主指标

| 指标 | 当前值 |
|---|---:|
| case 总数 | 509 |
| completed | 509 |
| generation_failed | 0 |
| semantic node-set F1 mean | 0.2902 |
| semantic GED similarity mean | 0.3082 |
| operation topology exact | 40/509 = 7.86% |
| goal effect exact | 28/485 = 5.77% |
| fatal minefield recall | 0.3163 |
| fatal minefield miss | 67 |
| spurious fatal minefield | 36 |
| 参考图非空但生成图空 | 335 |

### 4.2 生成漏斗

对 509 个 case 的 `generation_report` 汇总：

| 阶段 | 数量 |
|---|---:|
| LLM request | 1997 |
| request success | 1997 |
| returned graphs | 3979 |
| parsed graphs | 3969 |
| valid graphs | 843 |
| accepted observations | 808 |
| rejected graphs | 3136 |
| target reached | 58 |
| low sample | 451 |

有效候选图数量的 case 分布：

| valid graph count | case 数 |
|---:|---:|
| 0 | 287 |
| 1 | 49 |
| 2 | 18 |
| 3 | 28 |
| 4 | 53 |
| 5 | 13 |
| 6 | 44 |
| 7 | 3 |
| 8 | 14 |

中位数为 0。说明多数 case 的聚合输入已经失效。

### 4.3 校验 issue 分布

| issue code | 出现次数 |涉及 case 数 |
|---|---:|---:|
| `public_literal_mismatch` | 4762 | 301 |
| `contract_incomplete` | 3695 | 329 |
| `state_required_input_missing` | 480 | 76 |
| `minefield_contract_unverified` | 339 | 83 |
| `minefield_input_unverified` | 206 | 36 |
| `unknown_public_source` | 168 | 31 |
| `missing_terminal_goal` | 87 | 31 |
| `state_contract_mismatch` | 58 | 18 |
| `orphan_support_chain` | 46 | 46 |
| `minefield_reason_unverified` | 31 | 19 |

最高频字段包括：

| issue | field | 次数 |
|---|---|---:|
| `contract_incomplete` | `arguments.timestamp` | 985 |
| `public_literal_mismatch` | `arguments.hour` | 613 |
| `public_literal_mismatch` | `arguments.minute` | 565 |
| `public_literal_mismatch` | `arguments.second` | 565 |
| `contract_incomplete` | `values.reminder_timestamp` | 553 |
| `public_literal_mismatch` | `arguments.days` | 539 |
| `contract_incomplete` | `arguments.creation_timestamp_upperbound` | 329 |
| `contract_incomplete` | `match.reminder_id` | 254 |
| `public_literal_mismatch` | `arguments.holiday_name` | 253 |
| `contract_incomplete` | `match.person_id` | 241 |

这些字段高度集中在时间转换、相对时间、search ID 和可见消息抽取，正对应 prompt/contract/provenance 的三类不一致。

## 5. 代表性证据

### 5.1 `search_reminder_with_recency_upcoming`

LLM 多次输出逻辑上正确的完整链：

```text
get_current_timestamp
  -> search_reminder(reminder_timestamp_lowerbound = current timestamp)
  -> emit_message
```

原始输出中的 binding 是：

```json
{
  "source": "node_output",
  "producer_local_id": "n0",
  "selector": "$.timestamp",
  "cardinality": "one"
}
```

但 `get_current_timestamp` 的契约 selector 是 `$`，所以这些完整图全部因 `contract_incomplete` 被拒绝。

同一批次中另一张省略 current timestamp 参数的退化图可以通过：

```text
search_reminder -> emit_message
```

最终聚合结果缺少 reference 中必要的 `get_current_timestamp`。这不是模型没有规划出正确 producer，而是正确 producer 被 validator 丢弃。

### 5.2 `add_contact_with_name_and_phone_number_3_distraction_tools_arg_description_scrambled`

模型输出：

```json
{
  "source": "public_literal",
  "source_ref": "message:15:content",
  "value": "Stephen Sondheim"
}
```

以及：

```json
{
  "source": "public_literal",
  "source_ref": "message:15:content",
  "value": "+19876543210"
}
```

这些值确实是 Agent 可见消息中的内容，但当前 `message:15:content` 的 asset value 是整段消息。validator 要求 literal 与整段消息完全相等，于是所有候选图被拒绝，最终预测为空。

### 5.3 `add_reminder_content_and_date_and_time`

模型需要为 `datetime_info_to_timestamp` 提供拆分后的年、月、日、时、分、秒。常见输出类似：

```json
{
  "source": "public_literal",
  "source_ref": "instruction",
  "value": 17
}
```

但自然语言指令可能写的是 `5pm`，或者没有显式写出 minute/second 的默认 0。当前 `_literal_appears_in_instruction` 主要做大小写归一化后的 substring 检查：

- `5pm` 到 `17` 不通过；
- 默认 minute/second `0` 不一定作为独立 token 出现；
- `tomorrow` 到具体 day 更不可能通过。

同一图里 `datetime_info_to_timestamp -> add_reminder` 的 `reminder_timestamp` 又会被 `$.timestamp` selector 问题拒绝。因此日期任务同时受到 literal 校验与 selector 校验的双重打击。

### 5.4 fatal minefield

参考结果的 fatal 正例包含：

- `timestamp_diff`
- `search_reminder`
- `send_message_with_phone_number`
- `modify_reminder`
- scrambled utility 工具等。

当前候选图阶段却要求 minefield evidence 必须对应有 `writes` 的工具：

```python
elif not contract or not contract.get("writes"):
    issues.append("minefield_contract_unverified")
```

因此：

- `timestamp_diff` 10/10 个相关工具条目 recall 为 0；
- `search_reminder` 36/36 个相关工具条目 recall 为 0；
- `send_message_with_phone_number` 10/10 个相关工具条目 recall 为 0。

这不是模型没有尝试 minefield，而是当前允许的 minefield 语义比参考语义窄。

## 6. 根因分析

### 6.1 直接根因：模型被要求猜 hidden contract

这是最强的确定性根因。

当前链路中：

- validator 使用 `view.tool_contracts`；
- prompt payload 不包含 `tool_contracts`；
- prompt 示例还给出与 contract 相反的 selector。

对原始 LLM 输出的统计显示：

- 1124 张候选图包含 raw scalar producer binding；
- 217 个 case 受影响；
- 2398 个 node-output binding 指向 raw scalar 工具；
- 其中 1847 个使用 `$.timestamp`。

具体 selector 分布包括：

| producer | 模型 selector | 次数 |
|---|---|---:|
| `get_current_timestamp` | `$.timestamp` | 980 |
| `datetime_info_to_timestamp` | `$.timestamp` | 543 |
| `shift_timestamp` | `$.shifted_timestamp` | 490 |
| `search_holiday` | `$.timestamp` | 218 |
| `shift_timestamp` | `$.timestamp` | 106 |

这些全部与契约 `$` 不一致。统计中还发现：

- 212 个 `$.reminders[0].reminder_id`
- 164 个 `$.contacts[0].person_id`
- 123 个 `$.contacts[0].phone_number`
- 94 个 `$.messages[0].recipient_person_id`

这些与 search contract 的 `$.field + one/all` 表示不一致。

### 6.2 直接根因：public literal 证据模型粒度错误

当前 public asset 是“整段消息”，但业务参数需要“消息中的字段”。这两者不能同时满足当前 exact equality 校验。

原始输出中 public literal source ref 使用量：

| source_ref | raw binding 次数 |
|---|---:|
| `instruction` | 3192 |
| `message:15:content` | 2822 |
| `instruction:0` | 155 |
| `message:27:content` | 45 |
| `message:21:content` | 43 |

问题分为两类：

1. `instruction`：
   - 值在语义上可从指令推导，但不是字面子串；
   - 典型字段是 hour/minute/second/day/month/year/days/on。
2. `message:*:content`：
   - 值确实是可见消息子串；
   - 但当前 asset value 是整段消息，exact equality 必然失败。

`instruction:0` 则是 prompt 示例与实际 source_ref 的直接不一致。

### 6.3 放大器：整图拒绝策略

当前 `_validate_candidate_graph` 不区分：

- schema 破坏；
- 单字段 selector 可修复错误；
- 单字段 literal provenance 可修复错误；
- minefield 错误但 operation graph 正确；
- 确实不可执行的图。

只要 issues 非空，整张图丢弃。

统计到：

- 1124 张图含 raw scalar wrong selector；
- 其中至少 264 张图的唯一 issue 集合是 `contract_incomplete`；
- 340 张被拒图的 issue 集合只涉及 public literal 来源/值；
- 1130 张图同时受到 `contract_incomplete` 与 `public_literal_mismatch` 打击。

这些候选中有相当一部分是逻辑结构正确的图，不应该完全退出 ensemble。

### 6.4 放大器：canonical identity 过细

即使候选通过校验，当前 canonical identity 也可能把语义相同的目标拆开：

- 同一个工具参数值相同，但一个来源是 `instruction`，一个是 `message:15:content`，会被视为不同 node identity；
- 同一个 producer 输出，但 selector 写成 `$.timestamp` 与 `$`，会被视为不同 binding identity；
- search 输出写成 `$.contacts[0].person_id` 与 `$.person_id`，也会被视为不同 identity。

严格多数聚合要求每个 identity 单独过半。identity 拆分会导致：

- 两个语义等价候选互相稀释；
- 都无法达到严格多数；
- 最终节点被删除；
- 或只剩某个退化候选。

### 6.5 放大器：低样本下的严格多数

多数 case 只有 0～2 个有效观测：

- 287 个 case 为 0；
- 49 个 case 为 1；
- 18 个 case 为 2。

在 `n=1` 时，任何单张有效图都会 100% 通过多数；在 `n=2` 时，一旦分歧则 disposition 回落 `response_only`。当前质量高度依赖“哪张图碰巧通过校验”，而不是 ensemble 的真实共识。

### 6.6 安全语义根因：minefield 只允许 side effect

当前 prompt 和 validator 都把 minefield 定义为 fatal side-effecting tool call。但参考图把“信息不足时禁止调用某个计算或搜索工具”也视为 fatal safety 约束。

这导致：

- `minefield_contract_unverified` 339 次；
- `minefield_input_unverified` 206 次；
- fatal recall 只有 0.3163；
- 大量 reference fatal 正例在当前表示下不可生成。

### 6.7 历史版本差异

#### `a080ca55`：路径 ensemble

旧算法输出：

```json
{
  "paths": [
    {
      "turns": [
        {
          "turn_id": "turn_0",
          "disposition": "executable",
          "operations": [
            {"evidence_id": "...", "arguments": {}}
          ],
          "forbidden_evidence_ids": []
        }
      ]
    }
  ]
}
```

特点：

- 模型只需要规划 operation path，不需要生成完整 milestone graph 的 binding/edge/minefield schema。
- 单条坏路径不影响同轮其他路径。
- 有第二轮 refinement/counterexample repair。
- 环境模拟逐路径插入 recovery。
- 聚合方式是所有路径的 operation intersection 与 common precedence。
- 不做 output selector 精确校验，也不要求 public literal source_ref 精确 provenance。

这解释了为什么旧版 operation 图可能更稳：它把模型负担集中在工具选择和顺序上，而当前版本还要求模型精确猜 hidden output contract 和消息字段级 provenance。

#### `fadbef85`：candidate graph 架构

该提交引入：

- exact graph schema；
- `public_literal`/`node_output` binding；
- output contract 校验；
- strict majority graph aggregation；
- deterministic recovery/preserve；
- minefield contract 校验。

同时也引入了 raw scalar contract `$` 与 prompt `$.field` 的冲突。

#### `b19ae47c`：当前结果相关优化

该提交在当前架构内新增：

- Example 3，直接展示 `datetime_info_to_timestamp -> $.timestamp -> add_reminder`；
- 更强 minimality 与禁止防御性计算工具指令；
- 信息不足时要求 `response_only` + fatal minefield；
- dangling producer pruning；
- binding contract 辅助裁决。

其中 Example 3 与强 minimality 会显著放大日期任务中的 selector/literal 双重失败；minefield 指令则与“只允许 writes 工具”的 validator 产生更多冲突。

不过，`b19ae47c` 不是 hidden contract 问题的源头；`fadbef85` 已经把 validator 建立在未暴露的 contract 上。

## 7. 优化建议

建议不要直接全量回退到 `a080ca55`。旧算法可能恢复 operation recall，但会失去当前 state goal、显式 binding、minefield 和 preserve 语义，也会重新暴露旧 payload 中 initial_state/public assets 的信息边界问题。更合理的是保留当前 candidate graph 表示，但把“模型可猜”的部分改成契约驱动，并把整图失败改成可审计的局部修复。

### P0-1：建立 prompt/validator 单一契约来源

目标：模型看到、被要求遵守、最终被校验的 selector 必须来自同一个 contract 对象。

建议修改：

1. `MilestonePromptBuilder.payload` 增加安全可暴露的 `output_contracts`：
   - 按 `evidence_id` 或 tool name 索引；
   - 只包含 selector、type、cardinality；
   - 不包含 `writes`、`effect`、`required_dynamic_inputs` 等私有评分契约也可以，但至少 outputs 必须暴露。
2. prompt 中明确解释 contract selector 的语义：
   - raw scalar 是 `$`；
   - search 结果用 `$.field` 加 `one/all` 表示，不使用 `[0]` 或 `[*]` 路径。
3. 删除或替换所有与当前契约冲突的硬编码示例。
4. 示例中的 `instruction:0` 改成从当前 task JSON 复制 exact source_ref 的占位说明，或直接使用当前样例可复制的真实引用。

验收标准：

- prompt 中出现的每个 selector 形态都能被 validator 接受；
- validator 接受的每个 selector 在 prompt 或 contract payload 中可见；
- 不再存在 `$.timestamp` vs `$` 的示例级冲突。

### P0-2：先做 deterministic selector normalization，再校验

即使修正 prompt，也需要处理历史行为和模型偶发错误。建议在 schema parse 后、严格校验前增加一个显式 repair 阶段。

可安全修复的规则：

1. raw scalar 唯一 number output：
   - `$.timestamp`、`$.value`、`$.shifted_timestamp` 等；
   - producer contract 只有一个 number output；
   - 目标类型兼容；
   - 统一修复为 `$`。
2. search leaf path：
   - `$.contacts[0].person_id` -> `$.person_id` + `one`；
   - `$.contacts[*].person_id` -> `$.person_id` + `all`；
   - `$.reminders[0].reminder_id` -> `$.reminder_id` + `one`；
   - 仅当 leaf field、类型、cardinality 与唯一 contract output 匹配时修复。
3. 修复必须写入 audit metadata，例如：
   - `repair_rule`
   - `original_selector`
   - `repaired_selector`
   - `producer_evidence_id`
4. 歧义时不修复，且只降级该 binding，不直接丢整图。

预期影响：直接针对 3695 次 `contract_incomplete`、1124 张 raw scalar 受影响图和大量 search nested selector 拒绝。

### P0-3：修正 public asset 的证据粒度

有两种可选方案，推荐方案 A。

#### 方案 A：prompt 提供 structured public facts

在构造 `public_assets` 时，将可见消息中的业务字段抽成稳定条目：

```json
{
  "source_ref": "message:15:content.name",
  "value": "Stephen Sondheim",
  "parent_source_ref": "message:15:content"
}
```

要求 extractor 规则确定、可审计，不使用 LLM 判断来源。这样 prompt 与 validator 的 exact equality 语义可以保持。

#### 方案 B：允许可见文本 `contains` provenance

对 `message:*:content` 这类文本资产：

- 若 scalar literal 是整段文本的 substring，则接受；
- 同时记录 provenance policy 为 `visible_text_contains`；
- 对 ID、电话、坐标等高风险值仍可要求更强规则。

不建议简单放开所有 substring，因为短数字或常见词可能误匹配。方案 A 更符合当前 exact provenance 设计。

### P0-4：literal 语义校验分级

当前 substring 校验无法覆盖工具调用必需的拆分值。建议按字段类型分级：

1. 直接字面值：
   - 姓名、电话、明确文本内容；
   - 用 exact/substring/结构化 public fact 校验。
2. 数字/单位：
   - `2 days` -> `days=2`；
   - `10am`/`5pm` -> hour；
   - 用确定性 parser 归一化。
3. 布尔：
   - 保留现有 near-field positive/negative 逻辑，但补充 `turn on/off`、`enable/disable` 等模式。
4. 日期/相对时间：
   - 不能把 `tomorrow` 直接当成 public literal day；
   - 应明确表示为 `instruction_extraction` 或由当前时间 producer + shift 工具组成；
   - 若 ToolSandbox benchmark 的参考图本身允许固定日期 literal，需要先审计 reference 的时间基准，避免引入 benchmark leakage。

这一部分不应通过宽松 LLM judge 解决；必须是确定性、可测试规则。

### P1-1：候选图错误分级，不再一律整图拒绝

建议将错误分为四类：

| 级别 | 示例 | 处理 |
|---|---|---|
| fatal schema | 顶层结构错误、node kind 错误、unknown evidence | 拒绝整图 |
| repairable binding | raw scalar selector、nested search selector | deterministic repair 后重验 |
| local provenance error | public literal 来源不匹配 | 修复或移除该字段，记录 unresolved，不丢整图 |
| independent minefield error | minefield contract 不符 | 剔除 minefield，保留 operation graph |

若某个 required binding 无法修复，应将候选标记为 `partial`，不要当作完整 observation 参与 goal 聚合；但仍可用于诊断。最好不要静默把 partial 图当成完整图。

### P1-2：minefield 单独建模并与参考语义对齐

需要先明确产品定义：

- fatal minefield 是否只表示 side effect？
- 还是表示“该 turn 禁止调用该工具，即使工具只读”？

从当前参考结果看，后者才是评估语义。建议：

1. contract 增加每个工具的 `fatal_reasons` 与 `missing_input_fields`，不要求工具必须 `writes`。
2. `timestamp_diff`、`search_reminder` 等信息不足场景可声明为 fatal forbidden operation。
3. invalid minefield 只剔除 minefield，不影响同图 operation。
4. prompt 中同步更新 minefield 定义。

### P1-3：聚合前先归一化 semantic identity

修复后的候选需要在 canonicalization 前归一化：

- selector 使用 contract canonical selector；
- public literal 若多个 source 均可验证同一 value，可聚合为 normalized value identity，同时保留 provenance 列表；
- 相同工具 + 相同 canonical arguments 才合并；
- content requirement 可保留规范化文本，但应与 reference matcher 的语义策略一致。

这样避免“同一语义目标因 source_ref 或 selector 表面写法不同而互相稀释”。

### P1-4：报告真正的 generation failure mode

当前 `top_level_success_count > 0` 且 observations 为空时仍返回 `generated`，最后 `empty_reason=no_majority_goal`。这会掩盖主要故障。

建议增加：

- `all_candidates_rejected`
- `no_valid_observation`
- `repaired_candidate_count`
- `partial_candidate_count`
- `operation_graph_valid_but_minefield_rejected_count`
- `empty_after_aggregation`
- `empty_after_closure`
- `empty_after_pruning`

这些状态应进入 summary 聚合，便于下次立刻区分模型规划失败与 validator/repair 失败。

### P2-1：恢复旧算法的 repair pass 思想

在当前 graph 架构上可以保留双阶段：

1. 第一阶段生成 candidate graph。
2. 若有效观测不足，将 deterministic validation issues 转成一条 repair prompt：
   - 不暴露 reference；
   - 只暴露该工具的 output contract；
   - 明确要求修正 selector/source_ref；
   - 保持工具拓扑不变，除非确有缺 producer。

这比简单增加采样次数更有效，因为当前失败集中在可说明的格式/契约错误，而不是随机采样不足。

### P2-2：建立小回归集与消融

建议先构造 30～50 个 case 的 frozen microbenchmark，覆盖：

- raw scalar timestamp；
- datetime conversion；
- relative time/recency；
- search contact/reminder/message；
- public message field extraction；
- insufficient information；
- read-only fatal minefield；
- side-effect fatal minefield；
- 3/10 distraction tools；
- tool name/description/arg type scrambling。

每轮输出：

- parsed rate；
- repaired rate；
- valid rate；
- valid=0 rate；
- reference nonempty but empty prediction；
- operation topology exact；
- goal effect exact；
- fatal recall；
- prompt/contract一致性检查结果。

建议消融顺序：

1. 只加 contract payload，不改 validator。
2. 只加 deterministic selector repair，不改 prompt。
3. 两者同时开启。
4. 再处理 public literal。
5. 最后处理 minefield。

这样能定量判断每个根因贡献。

## 8. 建议的代码修改点

后续若按本报告实施，优先修改以下文件：

| 文件 | 建议修改 |
|---|---|
| `dynsteer/prompt/template.py` | payload 增加可安全暴露的 output contracts；确保 public source refs 与 validator 一致。 |
| `dynsteer/prompt/templates/milestone/generation.en.md` | 删除冲突示例；加入 contract selector 说明；修正 `instruction:0`；明确 search 的 `$.field + one/all` 表示。 |
| `dynsteer/prompt/templates/milestone/generation.zh.md` | 与英文模板同步。 |
| `dynsteer/milestone/compiler.py` | 增加 deterministic selector repair、错误分级、minefield 独立校验、semantic identity 归一化、no-valid-observation 报告。 |
| `dynsteer/adapter/toolsandbox/utils/effects.py` | 核实并修正真实 output selector；若当前 `$`/`$.field` 与工具实际返回不一致，以真实返回为第一依据；补充 fatal reason 契约。 |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 如采用 structured public facts，生成字段级 source_ref，并保证只暴露 Agent 可见内容。 |
| `milestone_reliability.py` | 汇总 repair/no-valid-observation/empty-after-aggregation 等新诊断字段。 |
| tests | 为 selector normalization、public provenance、minefield 独立剔除和 canonical identity 增加单元测试。 |

## 9. 不建议的做法

1. **不建议只把 validator 全部放宽**。这会让 `valid_graph_count` 变好，但可能引入捏造参数和不安全 minefield，指标改善可能是假象。
2. **不建议简单增加采样次数**。当前错误是确定性契约/来源错误，重复采样只会重复同类失败。
3. **不建议立即全量回退 `a080ca55`**。可以将其作为 operation recall baseline，但直接回退会丢掉当前更严格的 provenance/state goal 设计。
4. **不建议用 LLM judge 代替 literal 校验**。日期、数字、布尔和 provenance 应使用确定性规则。
5. **不建议继续把 minefield 错误与 operation graph 混在同一个整图拒绝决策中**。

## 10. 补充核查：partial_main 历史结果与 `fadbef85` 归因

### 10.1 结果路径与实验配置

用户消息中写的 `docs\results\milestone\toolsandbox_milestone_reliability_partial_main` 在当前工作区不存在。实际可核查的历史结果位于：

```text
results/milestone/toolsandbox_milestone_reliability_partial_main
```

当前结果位于：

```text
results/milestone/toolsandbox_milestone_reliability_main
```

两者的直接实验条件已经不同：

| 项目 | historical partial_main | current main |
|---|---|---|
| 运行时间 | 2026-08-24 | 2026-09-23 |
| case 数 | 30 | 509 |
| 模型 | `qwen-plus-latest` | `qwen3-max-2026-01-23` |
| temperature | 0.2 | 0.2 |
| random seed | 202608 | 见当前 `index.json` |
| 结果生成机器路径 | 旧机器 `E:\Studies\DynamicTracjectoryEvaluation\...` | 当前工作区 |
| 主结果状态口径 | 27 个 case 被标为 ambiguous/insufficient，不进主指标 | 509 个 case 均为 completed，空图纳入惩罚 |

因此，它不是控制变量实验。即使代码完全相同，仅模型从 `qwen-plus-latest` 换到 `qwen3-max-2026-01-23` 也可能显著改变候选图行为。

### 10.2 historical summary 的 survivorship bias

historical `summary.json` 的状态分布是：

```text
completed:              3
ambiguous:              7
insufficient_evidence: 20
generation_failed:      0
failed_case_count:     27
```

关键口径字段：

```text
graph_return_rate = 0.1
graph_return_count = 3
metric_sample_counts.graph = 3
metric_sample_counts.ged = 3
metric_sample_counts.goal_effect = 3
metric_sample_counts.operation_topology = 3
```

也就是说，历史 summary 的主指标只统计 3 个返回可比预测图的 case，另外 27 个 ambiguous/insufficient case 不进入主指标。这些主指标包括：

| 指标 | historical summary 口径 |
|---|---:|
| operation topology exact | 1/3 = 33.3% |
| goal effect exact | 0/3 = 0% |
| topology GED similarity mean | 0.6667 |
| topology node-set F1 mean | 0.6667 |
| fatal positive recall | 1/1 = 100% |
| reference nonempty/generated nonempty | 3/3 |

这不是 30 个 case 的全量惩罚口径。当前实验把没有有效候选的 case 编译成空图，并让空图参与 GED/F1/exact/empty-graph 惩罚；因此两者 summary 不能直接比较。

历史结果自身也不是“稳定生成 30 个好图”。其官方 30 case 生成漏斗为：

| 阶段 | historical partial_main |
|---|---:|
| LLM request | 70 |
| returned simulation | 140 |
| parsed simulation | 140 |
| repaired simulation | 9 |
| valid simulation | 38 |
| rejected simulation | 100 |
| selected simulation | 6 |

有效候选率：

```text
38 / 140 = 27.14%
```

可直接解析的 29 个 case JSON 中，历史校验问题分布为：

| historical issue | 出现次数 |
|---|---:|
| `missing_required_action_input` | 53 |
| `public_value_reference_required` | 48 |
| `missing_primary_constraint` | 37 |
| `primary_constraint_reordered` | 12 |
| `invalid_minefield` | 8 |
| `constraint_template_not_allowed` | 3 |
| `invalid_dynamic_binding` | 1 |
| `missing_terminal` | 1 |

此外，30 个历史 case JSON 中有 1 个文件损坏：

```text
search_message_with_recency_oldest_multiple_user_turn.json
```

该文件包含连续 NUL 字节，从 `reference` 附近开始损坏，文件尾部仍存在；可读前缀显示其外层状态为 `ambiguous`，但完整 generation report 无法恢复。因此逐文件直接统计只能覆盖 29 个 case，得到 67 次请求、134 个返回/解析 simulation；上表的 70/140 来自未损坏前的 summary 聚合。

### 10.3 该 artifact 不能严格对应 committed `fadbef85`

用户认为该历史结果对应 `fadbef85`。但多重证据表明，它不是当前仓库提交 `fadbef85` 中 committed milestone 代码的直接输出。

#### 证据一：`GenerationReport` schema 不匹配

当前 Git 中：

```text
fadbef85:dynsteer/milestone/model.py
```

的 `GenerationReport` 使用 candidate graph 字段：

```text
target_candidate_graph_count
returned_graph_count
parsed_graph_count
valid_graph_count
accepted_observation_count
```

而 historical partial_main 的 result JSON 使用：

```text
returned_simulation_count
parsed_simulation_count
repaired_simulation_count
valid_simulation_count
rejected_simulation_count
selected_simulation_count
terminal_clusters
simulation_support
constraint_support
edge_support
minefield_support
```

这些 simulation 字段在当前仓库的 `--all` 历史中没有来源；`git log --all -S'returned_simulation_count' -- dynsteer` 没有找到任何提交。

#### 证据二：原始 LLM response schema 不匹配

`fadbef85` 的英文 prompt 明确要求：

```text
Return one JSON object with exactly one top-level key, graphs.
Every graph object has exactly dispositions, nodes, edges, and minefields.
```

绑定形式是：

```text
public_literal
node_output
```

但 historical partial_main 的 30 个 `llm_outputs/*.json` 文件中：

```text
30/30 包含 milestones
 0/30 包含 graphs
26/30 包含 public_value
 0/30 包含 public_literal
```

代表响应形态是：

```json
[
  {
    "turn_dispositions": {"turn_0": "executable"},
    "milestones": [
      {
        "milestone_id": "m0",
        "constraints": [
          {
            "template_id": "action_invoked",
            "bindings": {"action_id": "search_messages"}
          },
          {
            "template_id": "action_input_equals",
            "bindings": {
              "action_id": "search_messages",
              "field_id": "content",
              "value": {
                "source": "public_value",
                "source_id": "turn:turn_0",
                "raw_value": "..."
              }
            }
          }
        ]
      }
    ],
    "graph": [["m0", "m1", "effect"]],
    "minefields": []
  }
]
```

这不是 `fadbef85` prompt 要求的 exact `graphs + nodes + public_literal/node_output` schema，也不是 `a080ca55` 的 path ensemble schema。它更像一份存在于旧机器工作区、未提交或已丢失的 simulation/milestone 中间实现。

#### 证据三：时间和运行路径也支持“旧工作区状态”

`fadbef85` 提交时间是 2026-08-14，结果运行时间是 2026-08-24，且 result/index 指向旧机器 `E:` 路径。时间上可能包含后续未提交修改。结合 schema 差异，不能仅凭用户记忆把它归到 `fadbef85`。

**结论：** historical partial_main 可以作为一份“旧中间实现的 30 case 观测”，不能作为 committed `fadbef85` 的精确 baseline，也不能用来量化 `fadbef85 -> b19ae47c` 的代码退化幅度。

### 10.4 同名 30 case 的当前结果对照

30 个历史 case 在当前 main 结果中均存在。当前同名子集的全量惩罚口径如下：

| 指标 | current main 同名 30 case |
|---|---:|
| completed | 30 |
| valid graph count = 0 | 23 |
| 预测图节点数为 0 | 27 |
| 非空预测图 | 3 |
| operation topology exact | 4/30 = 13.3% |
| goal effect exact | 3/30 |
| semantic GED similarity mean | 0.2167 |
| semantic node-set F1 mean | 0.2200 |
| topology GED similarity mean | 0.2167 |
| topology node-set F1 mean | 0.2200 |
| LLM request | 117 |
| returned/parsed graphs | 234 |
| valid graphs | 27 |
| rejected graphs | 207 |

当前同名 30 case 的有效候选率为：

```text
27 / 234 = 11.54%
```

这比 historical partial_main 的 `38/140 = 27.14%` 更差，说明在这些同名 case 上，当前“模型输出 -> 校验 -> 有效观测”链路确实退化了。这个现象是真实的，不能只靠口径差异解释。

不过，两者的“返回非空可比图比例”反而相同：

```text
historical: 3/30 = 10%
current:    3/30 = 10%
```

区别是：

- historical 将另外 27 个 case 标为 `ambiguous` 或 `insufficient_evidence`，主指标只看 3 个；
- current 将它们编译为空图并全部标为 `completed`，主指标看 30 个。

因此，历史 summary 中 33.3% operation exact 主要来自 survivorship bias；当前 13.3% 则包含 27 个空图惩罚。两者不是同一分母。

### 10.5 当前同名 30 case 的校验问题分布

当前同名 30 case 的 `generation_report.validation_issues` 分布为：

| current issue | 出现次数 |涉及 case 数 |
|---|---:|---:|
| `public_literal_mismatch` | 291 | 18 |
| `contract_incomplete` | 131 | 16 |
| `state_required_input_missing` | 69 | 11 |
| `minefield_contract_unverified` | 31 | 6 |
| `missing_terminal_goal` | 9 | 2 |
| `unknown_public_source` | 18 | 1 |
| `state_contract_mismatch` | 5 | 2 |
| `minefield_reason_unverified` | 3 | 2 |
| `minefield_terminal_conflict` | 2 | 1 |
| `orphan_support_chain` | 1 | 1 |

该分布与全量 509 case 的主导问题一致，仍集中在：

1. hidden output contract / selector；
2. public literal provenance；
3. state required dynamic input；
4. minefield 契约语义。

这说明第 6 节的根因分析在 historical partial_main 的同名压力子集上同样成立，不是全量结果中的偶然分布。

### 10.6 历史 3 个 completed case 的逐一对照

historical summary 的 3 个 completed case 与当前同名结果对比如下：

| case | old op exact | old goal exact | old topo GED/F1 | current op exact | current goal exact | current topo GED/F1 |
|---|---:|---:|---:|---:|---:|---:|
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | true | false | 0 / 0 | true | true | 1 / 1 |
| `search_message_with_recency_latest_multiple_user_turn_alt` | false | false | 1 / 1 | false | false | 1 / 1 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | false | false | 1 / 1 | false | false | 0.75 / 0.8 |

结果并非“历史 3 个全部好、当前全部坏”：

- 1 个 case 当前明显更好；
- 1 个 case 基本持平；
- 1 个 case 当前略差。

因此，即使只看 historical summary 实际纳入的 3 个 survivor case，也没有证据支持“旧实现整体显著优于当前实现”。更准确的描述是：两代实现各有胜负，但旧 summary 的小样本与 survivorship bias 放大了幸存 case 的指标。

### 10.7 归因结论

综合 committed source、result schema、raw response schema、模型配置与评估口径，补充归因如下。

#### 可以确认的事实

1. 当前同名 30 case 的候选有效率从历史 artifact 的 27.14% 降到 11.54%，当前链路确实更差。
2. 当前同名子集的拒绝原因仍高度集中在 `contract_incomplete`、`public_literal_mismatch`、state 动态输入和 minefield 契约，与全量 509 case 的根因一致。
3. `b19ae47c` 当前的 prompt 示例、hidden contract、public literal 粒度和 minefield 语义冲突是确定性缺陷，会继续放大退化。
4. 当前空图纳入惩罚，而历史 27 个无法返回可比图的 case 不纳入主指标，导致 summary 层面严重不同口径。

#### 不能确认的事实

1. 不能确认 historical partial_main 来自 committed `fadbef85`。
2. 不能把 27.14% -> 11.54% 的候选有效率差异单独归因于 `b19ae47c` 的代码修改，因为：
   - 历史 result schema 与 committed `fadbef85` 不一致；
   - 历史模型是 `qwen-plus-latest`，当前是 `qwen3-max`；
   - 历史与当前的 result/status/metric 口径不一致；
   - 历史实现包含当前 Git 历史中找不到的 simulation ensemble 字段。
3. 不能用 historical summary 的 33.3% operation exact 与当前全量 7.86% 直接相减来计算退化幅度。

#### 最稳妥判断

当前结果差由三层因素叠加：

1. **真实代码/契约缺陷**：当前 candidate graph 生成确实存在第 6 节的 hidden contract、public literal、minefield 与整图拒绝问题。
2. **模型差异**：`qwen-plus-latest` 与 `qwen3-max-2026-01-23` 的输出习惯不同，当前模型更容易被 prompt 示例引导到 `$.timestamp`、nested search selector 和消息子字段 literal。
3. **评估口径变化**：历史把 27/30 case 排除出主指标，当前把空图全部纳入惩罚，使 summary 显著变差。

因此，“后续优化把代码逻辑改崩”是一个合理怀疑，但现有 artifact 不能证明退化全部来自 `b19ae47c`。更准确地说：`b19ae47c` 的优化在已有 hidden-contract 设计上进一步增加了强约束与冲突示例，确实可能放大失败；但用户引用的历史结果不是同代码、同模型、同口径 baseline。

### 10.8 基于补充核查的额外实验建议

在做生成算法修复前，应先补一个最小 A/B 归因实验：

1. **代码版本**
   - checkout 干净的 committed `fadbef85`；
   - checkout 干净的 `b19ae47c` 或当前 HEAD。
2. **case 集**
   - 先用 historical partial_main 的 30 个同名 case；
   - 再扩展到第 7.2 节建议的 30～50 个 frozen microbenchmark。
3. **模型**
   - `qwen-plus-latest` 与 `qwen3-max-2026-01-23` 各跑一遍；
   - 固定 temperature、seed、max tokens、retry 和采样批次。
4. **统一输出评估口径**
   - 所有 case 均纳入分母；
   - 无法生成有效观测时显式记为 empty/all-case penalty；
   - 同时报告：
     - request/returned/parsed/repaired/valid/rejected；
     - valid=0 case 数；
     - graph return rate；
     - completed-only 指标；
     - all-case penalty 指标。
5. **报告 schema 差异**
   - 如果两个版本无法用同一 result schema，应在实验脚本层做显式 adapter，而不是直接比较旧 `simulation_count` 与新 `graph_count`。
6. **保存 prompt 与 raw response**
   - 保存完整 prompt、响应和请求参数；
   - 记录 git commit、dirty 状态与未提交文件列表；
   - 避免再次出现“结果无法归因到提交”的问题。

只有这个 A/B 结果才能区分：

```text
模型差异贡献
committed fadbef85 -> b19ae47c 代码差异贡献
评估口径变化贡献
```

在第 7 节的 P0 修复中，仍应优先处理 output contract 暴露、selector normalization、public literal 粒度和 minefield 独立校验；这些问题已经由当前 509 case 与同名 30 case 的 validation issue 分布交叉证实。
## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox 真实返回 shape 与当前 contract 的最终对齐**
   - 当前 `effects.py` 使用 `$` 和抽象 `$.field + one/all` 表示。
   - 我没有在本次审计中实际调用 ToolSandbox runtime 逐工具采样返回 JSON。
   - 因此最终应把 raw scalar contract 改成 `$.timestamp`，还是把 prompt/修复逻辑统一到 `$`，需要以真实工具返回为准，而不能只看当前代码风格。

2. **自然语言日期与相对时间的无泄漏抽取**
   - `tomorrow`、`upcoming`、`yesterday` 需要时间基准。
   - 当前 prompt 是否应该允许模型根据 benchmark 固定日期推出 year/month/day，还是必须通过 current timestamp + shift 工具表达，需要先明确任务定义与参考图设计。
   - 这部分容易不小心把 evaluator-only 时间基准泄漏给生成器。

3. **public message 字段抽取的完整覆盖**
   - 可以确定姓名、电话、内容子串当前被过严校验伤害。
   - 但是否所有 ToolSandbox 可见消息都能用确定性规则抽成字段级 source_ref，需要逐数据集审计。
   - 简单 substring policy 的误报边界也尚未量化。

4. **旧版实验结果的定量归因**
   - 已找到 `results/milestone/toolsandbox_milestone_reliability_partial_main`，但其 result schema、raw response schema 与 committed `fadbef85` 均不一致，且模型与当前不同。
   - 因此它不能作为 `fadbef85` 的精确复现，也不能量化 `fadbef85 -> b19ae47c` 的退化幅度。
   - 仍需按第 10.8 节做同代码、同模型、同 case、同口径的干净 A/B 实验。
5. **低样本 ensemble 的最佳聚合策略**
   - 修复 hidden contract 后，严格多数可能已经足够。
   - 是否需要对跨请求重复签名降权、引入质量分层或 partial observation，应基于修复后的数据再决定；现在直接设计复杂加权容易引入新偏差。

6. **minefield 安全语义的产品边界**
   - 参考结果显然包含 read-only forbidden operation。
   - 但哪些只读工具在真实运行中应当 fatal、哪些只是无效而非危险，需要结合 ToolSandbox 原始 evaluation 定义确认。
