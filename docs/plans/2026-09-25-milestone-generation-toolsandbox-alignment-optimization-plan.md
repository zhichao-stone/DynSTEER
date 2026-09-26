# Milestone graph 生成与 ToolSandbox 人工标注图对齐优化方案

- 日期：2026-09-25
- 依据报告：`docs/plans/2026-09-25-milestone-generation-regression-audit.md`
- 当前结果：`results/milestone/toolsandbox_milestone_reliability_main`
- 目标：自动生成图在 schema、目标语义、拓扑和 minefield 语义上向 ToolSandbox 原生人工标注 milestone graph 靠拢，同时保留可执行 binding 的审计能力。
- 本方案只设计后续代码修改，本次不修改生成代码。

## 1. 核心判断：prompt 与 validator 差异是直接断点，但不是全部根因

用户对审计结论的理解基本正确：**prompt 与 validator 之间存在较大差异，是当前 509 case 结果变差的直接断点之一**。典型证据是：

1. prompt 示例引导 `$.timestamp`，validator 要求 raw scalar contract `$`；
2. prompt 示例引导 `$.reminders[0].reminder_id`，validator 要求抽象 selector `$.reminder_id`；
3. prompt payload 不包含 `tool_contracts`，但 validator 按 hidden contract 校验；
4. prompt 示例使用 `instruction:0`，真实 turn source_ref 是 `instruction`；
5. `message:15:content` 的 public asset value 是整段消息，validator 要求 literal 与整段消息完全相等；
6. prompt 将 minefield 描述为 fatal side-effect tool，validator 也要求工具必须有 `writes`，但 ToolSandbox 人工标注中大量 fatal minefield 是只读工具；
7. prompt 要求“只添加真实依赖边、独立 producer 不连边”，而 ToolSandbox 原生人工图默认把 milestone list 编成线性链。

但需要注意：**这不是完整根因**。即使把 prompt 与 validator 完全统一，当前系统仍可能无法很好对齐 ToolSandbox 人工图，因为当前生成目标与人工标注目标存在更深层差异：

| 维度 | 当前生成目标 | ToolSandbox 人工标注目标 |
|---|---|---|
| 图的含义 | Agent 可执行的必要 candidate goal graph | 原生 evaluation 的 snapshot milestone matcher |
| 节点含义 | tool call / symbolic state / emit message 的必要目标 | 某个执行阶段必须达到的快照状态或事件 |
| 参数地位 | 必须精确绑定并过 hidden contract | operation topology 主指标只比较工具名，不比较参数 |
| 边含义 | true dependency / producer-consumer / recovery | 默认 milestone 顺序链，更多是评估顺序，不一定是因果依赖 |
| minefield | 当前实现要求 side-effect | 人工标注也禁止只读工具被错误调用 |
| preserve | 生成图使用 milestone_id 引用 | 原生图由 guardrail 自动生成 milestone_index 引用 |
| 最终回答 | 生成自然语言 content requirement | 人工图常包含期望回答原文与关键事实包含约束 |
| guardrail | 编译器自己推导 | ToolSandbox `MilestoneMatcher` 在原始图上自动追加 |

因此，后续优化不能只做“prompt 修正”或“validator 放宽”，而应分成两层：

1. **生成层**：继续生成可执行、可审计的 tool/binding/state 图；
2. **评估投影层**：把生成图投影成 ToolSandbox 语义的 evaluation milestone graph，再与人工图比较。

推荐目标是：

```text
LLM candidate graph
    -> deterministic repair
    -> operation-level ensemble
    -> runtime executable graph
    -> ToolSandbox evaluation graph projection
    -> reference comparison
```

不要直接把 runtime graph 与人工 reference graph 混在同一语义层比较。

## 2. ToolSandbox 原生 milestone graph 设计核查

### 2.1 原生图来源

ToolSandbox 原始项目路径：

```text
../ToolSandbox/tool_sandbox
```

关键实现：

```text
../ToolSandbox/tool_sandbox/common/evaluation.py
../ToolSandbox/tool_sandbox/common/scenario.py
../ToolSandbox/tool_sandbox/scenarios/*.py
```

DynSTEER 侧转换实现：

```text
dynsteer/adapter/toolsandbox/utils/scenario.py
```

核心函数：

```python
milestone_graph_from_scenario(scenario)
constraint_from_snapshot_constraint(constraint_id, constraint)
_matcher_nodes(matcher, prefix, is_milestone)
edge_list(matcher, prefix)
```

### 2.2 ToolSandbox 的 milestone 是 snapshot matcher，不是完整执行计划

ToolSandbox 原生 `Milestone` 定义包含：

```python
snapshot_constraints: List[SnapshotConstraint]
guardrail_database_list
guardrail_exclusion_list
```

每个 `SnapshotConstraint` 可以约束：

- `CONTACT`
- `MESSAGING`
- `REMINDER`
- `SETTING`
- `SANDBOX`

其语义是比较两个快照之间的 database similarity。也就是说，人工标注的 milestone 更接近：

```text
某个时间点的世界状态或事件必须与期望快照相似
```

而不是：

```text
Agent 计划中的每个必要工具调用和动态 binding
```

这一点决定了生成端不应把“可执行性校验”与“人工图相似度评估”完全耦合。

### 2.3 SANDBOX namespace 承担工具与消息事件

DynSTEER 的 `constraint_from_snapshot_constraint` 将 SANDBOX row 转成四类语义：

1. Agent -> Execution Environment 且含 tool trace：
   - `tool_call`
2. Execution Environment -> Agent 且含 tool trace：
   - `tool_call`
3. Agent/System/Environment -> User：
   - `emit_message`
4. 其他 namespace：
   - `set_state`

因此 ToolSandbox 人工图中的工具 milestone 本质是“发生过某个工具调用或拿到某个工具结果”的 snapshot event。

### 2.4 原生图默认是线性链

本地 ToolSandbox 源码中共有 129 个基础 `ScenarioExtension` 模板。本次只读 AST 核查显示：

```text
129/129 没有显式提供 milestone edge_list
```

ToolSandbox `MilestoneMatcher.__attrs_post_init__` 在 `edge_list is None` 时构造：

```python
[(i, i + 1) for i in range(len(milestones) - 1)]
```

因此这些人工图默认是 milestone list 的线性链。509 个实验 case 由这些模板加扰动和变体扩展而来，参考图也继承这一设计。

这与当前 prompt 的规则相反：

```text
当前 prompt：只添加真实依赖边，独立 producer 不连边
ToolSandbox reference：默认把所有 milestone 按列表顺序连成链
```

所以即使工具集合正确，只要边不是参考约定形态，topology exact 仍会失败。

### 2.5 人工图中有大量 minefield-only 空图

只读 AST 统计显示，129 个基础模板中：

```text
26/129 的 milestone list 为空
26/129 同时包含 minefield
```

DynSTEER 转换时将这些图标记为：

```text
empty_graph_completion_basis = minefield_only
```

这类 case 的正确生成目标通常不是空操作加泛化 minefield，而是：

```text
不允许调用某些工具，同时以澄清或安全回复结束
```

但主评估中空图的 completion basis 主要来自 minefield-only。当前生成端需要显式理解这个语义。

### 2.6 人工 minefield 不要求工具有副作用

本地源码中的 minefield 工具包括：

```text
search_lat_lon
search_weather_around_lat_lon
timestamp_diff
unit_conversion
modify_contact
remove_contact
send_message_with_phone_number
calculate_lat_lon_distance
```

其中相当一部分是只读或计算工具。按当前 DynSTEER `effects.py` 契约归类：

- 无 `writes` 的 minefield 工具：12 处；
- 有 `writes` 的 minefield 工具：6 处。

典型源码注释也明确写了：

```text
# timestamp_diff should never be called.
```

因此，当前 validator 的：

```python
if not contract or not contract.get("writes"):
    issues.append("minefield_contract_unverified")
```

与 ToolSandbox 人工安全语义直接冲突。

### 2.7 工具参数不是 operation topology 的比较对象

人工 scenario 中确实有部分 tool trace 带非空 arguments，但也有大量 tool milestone 只要求工具名发生。只读统计基础模板中的 tool occurrence 约束可见：

```text
tool trace 无参数或空参数：多数
tool trace 带非空参数：少数
```

更重要的是 DynSTEER 当前主指标：

```text
operation_topology_exact
```

来自 `canonical_graph_semantics()` 的：

```python
operations = [tool_name, ...]
```

不包含工具参数。

因此，一个候选图如果工具名和顺序正确，但某个 selector 或 literal 来源错误，当前 validator 会把整图拒绝；即使该错误不影响这次 reference 主指标。这是当前生成与评估目标之间的关键错位。

### 2.8 最终回答节点包含期望回答文本

人工图中的用户可见回答 milestone 常包含两个 SANDBOX constraints：

1. 期望 Agent 完整回答；
2. 回答中必须包含原始关键信息，常使用 `column_contains_similarity`。

示例：

```text
Agent -> USER:
    "Your most recent message says 'Good, keep me posted'."

同时要求包含：
    "Good, keep me posted"
```

而当前生成 schema 只有：

```text
content_requirement
```

模型通常输出“回答最近一条消息内容”这类抽象要求，无法与人工图中的具体回答原文做 exact canonical match。这会同时伤害：

- `goals`
- `topology`
- `goal_effect_exact`

这个问题不能靠要求模型猜出隐藏 tool result 解决，需要引入更合理的 answer goal 语义匹配。

### 2.9 preserve/guardrail 是原生 matcher 自动生成的，不是人工手写目标

ToolSandbox `MilestoneMatcher._add_guardrail_constraints()` 会自动向 milestone 添加 guardrail constraints。DynSTEER 转换后成为：

```python
{
    "kind": "preserve_state",
    "namespace": namespace,
    "reference": {"type": "milestone_index", "value": index}
}
```

当前编译器生成的 preserve 是：

```python
{
    "kind": "preserve_state",
    "namespace": namespace,
    "reference": {"type": "milestone_id", "value": milestone_id}
}
```

而 `canonical_graph_semantics()` 直接把整个 `reference` 放入 identity。由于生成图不可能知道 reference milestone index，当前 preserve identity 几乎不可能 exact match。

这解释了当前结果中 preserve 对齐极差的现象，也说明把 preserve 直接纳入 `goal_effect_exact` 会惩罚一个生成器原理上无法命中的内部引用格式。

## 3. 当前实现与人工图设计的具体错位清单

| 编号 | 错位点 | 当前实现 | ToolSandbox 设计 | 影响 |
|---|---|---|---|---|
| M1 | output contract 不可见 | validator 用 hidden `tool_contracts` | 模型需知道可绑定 selector | 大量 `contract_incomplete` |
| M2 | prompt 示例错误 | `$.timestamp`、nested selector、`instruction:0` | contract/source 必须真实 | 引导模型稳定犯错 |
| M3 | public literal 粒度 | 整段消息 exact equality | 业务参数常是消息中的字段 | `public_literal_mismatch` |
| M4 | 整图拒绝 | 任一 issue 丢弃全图 | 图中不同 goal 可独立评估 | 3136 张图被拒 |
| M5 | 参数与 operation metric 耦合 | 参数错则工具节点丢失 | operation 主指标不看参数 | operation recall 被不必要压低 |
| M6 | canonical identity 过细 | identity 含 source_ref/selector/argument | 人工图按工具名、状态效果、消息目标比较 | ensemble 分裂 |
| M7 | 边语义不同 | true dependency | 默认线性 milestone chain | topology exact 失败 |
| M8 | minefield 语义过窄 | 必须有 `writes` | 只读工具也可 fatal | fatal recall 上限受限 |
| M9 | preserve 引用不同 | milestone_id | milestone_index | preserve exact 几乎不可达 |
| M10 | emit 内容语义不同 | 自然语言 requirement | 期望具体回答文本/包含事实 | goals 与 topology 失败 |
| M11 | 状态目标表达 | symbolic match/values + required dynamic inputs | 原生 expected snapshot rows | 需要 canonical projection |
| M12 | 生成状态口径 | 无有效观测也返回 completed 空图 | 历史 summary 曾排除失败图 | 结果诊断混淆 |

## 4. 优化总原则

### 4.1 不建议全量回退到旧路径 ensemble

旧实现可能更容易命中工具名集合，但没有解决：

- state goal 对齐；
- emit message 对齐；
- preserve 语义；
- ToolSandbox minefield-only 空图；
- read-only fatal minefield；
- 绑定与执行审计。

直接回退会换取短期指标，损失当前架构的表达能力。

### 4.2 不建议简单放宽 validator

全部放宽会出现：

- 工具名碰巧正确但参数捏造；
- minefield 泛化或虚构；
- state goal 字段不完整；
- 无法区分模型规划失败与契约修复失败。

应改为错误分级和部分观测。

### 4.3 推荐双层图模型

保留两层目标：

#### Runtime plan graph

用于回答：

```text
这个图是否可执行？
每个动态参数从哪里来？
哪个 producer 支撑哪个 consumer？
哪些 binding 未解决？
```

保持严格 contract 校验。

#### ToolSandbox evaluation graph

用于回答：

```text
这个图与人工 snapshot milestone 是否同形？
工具事件是否相同？
状态效果是否相同？
安全禁令是否相同？
最终回答目标是否可比较？
```

该层按 ToolSandbox 评估规则投影，不要求暴露 reference。

## 5. P0 修改方案：先消除确定性错误和不可达比较

### P0-1 将 output contract 纳入 prompt payload

#### 修改文件

```text
dynsteer/prompt/template.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

#### 修改内容

`MilestonePromptBuilder.payload` 增加安全可暴露的：

```json
"output_contracts": {
  "get_current_timestamp": {
    "outputs": {
      "value": {
        "selector": "$",
        "type": "number",
        "cardinality": ["one"]
      }
    }
  }
}
```

注意不要暴露：

```text
writes
effect
required_dynamic_inputs
environment_rules
simulation_state
```

prompt 需明确说明：

1. raw scalar 输出 selector 是 `$`；
2. search 输出使用 `$.field` + `one/all`；
3. 不使用 `$.rows[0].field` 或 `$.contacts[*].field`；
4. 当前示例中的 selector 必须与 payload contract 完全一致。

#### 验收

- prompt 中每个示例 selector 都能被 validator 接受；
- payload 中每个待绑定 selector 都可见；
- 单元测试扫描 prompt 与 `tool_contracts`，不允许出现冲突 selector；
- `contract_incomplete` 中 raw scalar 类错误显著下降。

### P0-2 删除或重写冲突示例

#### 具体冲突

必须删除或替换：

```json
{"selector": "$.timestamp"}
```

替换为：

```json
{"selector": "$"}
```

必须删除或替换：

```json
{"selector": "$.reminders[0].reminder_id"}
```

替换为：

```json
{"selector": "$.reminder_id", "cardinality": "one"}
```

必须替换：

```text
instruction:0
```

为当前 task JSON 可复制的 source ref 示例，或使用明确的占位符说明。

#### 验收

新增 prompt snapshot test：

```text
assert "$.timestamp" not in raw_scalar_example
assert "reminders[0]" not in search_example
assert "instruction:0" not in hard_example
```

更理想的做法是测试直接解析示例 JSON 并调用 validator 的 contract 检查。

### P0-3 增加确定性 selector repair

#### 修改文件

```text
dynsteer/milestone/compiler.py
```

#### 插入位置

在 `_parse_candidate_batch()` 后、`_validate_candidate_graph()` 前增加：

```python
_repair_candidate_graph(graph, view)
```

#### 安全修复规则

1. raw scalar 唯一输出：

```text
$.timestamp -> $
$.value -> $
$.shifted_timestamp -> $
```

仅当 producer contract 只有一个 number/boolean scalar 输出且目标类型兼容时修复。

2. search leaf path：

```text
$.contacts[0].person_id -> $.person_id + one
$.contacts[*].person_id -> $.person_id + all
$.reminders[0].reminder_id -> $.reminder_id + one
```

仅当 leaf field、类型和 cardinality 可由唯一 contract output 推断时修复。

3. `instruction:0 -> instruction`

仅当当前 view 确实存在 `instruction` 且不存在 `instruction:0` 时修复。

#### 审计字段

repair 后记录：

```json
{
  "repair_rule": "raw_scalar_selector",
  "original_selector": "$.timestamp",
  "repaired_selector": "$",
  "producer_evidence_id": "..."
}
```

#### 验收

- 3969 张可解析图中，raw scalar selector 类拒绝大幅下降；
- 歧义 selector 不静默修复；
- 每个 repair 可在 result 中审计；
- 增加 selector normalization 单元测试。

### P0-4 将 public asset 拆成 structured public facts

#### 修改文件

```text
dynsteer/adapter/toolsandbox/utils/contract.py
dynsteer/prompt/template.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

#### 设计

不要让模型引用整段消息作为字段 literal。将可见消息确定性拆成字段级 facts，例如：

```json
{
  "source_ref": "message:15:content.name",
  "parent_source_ref": "message:15:content",
  "value": "Stephen Sondheim",
  "kind": "person_name"
}
```

初期可只覆盖 ToolSandbox 稳定模式：

- 姓名；
- 电话号码；
- 日期；
- 时间；
- 经纬度；
- 布尔状态；
- 明确引用的消息内容。

规则必须 deterministic，不使用 LLM extractor。

对于尚未结构化的内容，保留：

```json
{
  "source_ref": "message:15:content",
  "value": "整段消息",
  "usable_for": "exact_full_text_only"
}
```

#### 验收

- `public_literal_mismatch` 中消息字段类错误下降；
- 不允许短数字或常见词在多个来源中模糊匹配；
- 每个 structured fact 可回溯 parent source；
- 不暴露 evaluator-only 消息。

### P0-5 校验错误分级与 partial observation

这是对结果影响最大的结构性修改。

#### 修改文件

```text
dynsteer/milestone/model.py
dynsteer/milestone/compiler.py
milestone_reliability.py
```

#### 错误等级

| level | 示例 | 处理 |
|---|---|---|
| fatal schema | top-level shape 错、node kind 错、unknown evidence | 拒绝整图 |
| repairable binding | raw scalar selector、nested search selector | deterministic repair 后重验 |
| local unresolved binding | required argument 缺失或来源不可证 | 保留 tool operation，标记 partial |
| local provenance error | public literal 来源/值不匹配 | 保留 operation 或 state shape，标记 unresolved |
| independent minefield error | minefield contract/reason 不符 | 剔除或修复 minefield，不丢 operation graph |
| state contract error | state 字段与 executor contract 不一致 | 视字段影响决定 partial 或拒绝 state goal |

#### 关键原则

工具节点的 operation topology 不应因为参数 binding 失败而完全消失。因为 ToolSandbox 主指标比较工具名，不比较参数。

#### 新报告字段

```text
partial_candidate_count
operation_only_candidate_count
runtime_invalid_candidate_count
rejected_by_fatal_schema_count
rejected_by_local_binding_count
minefield_rejected_count
```

#### 验收

- 3136 个 rejected graph 中，大量转为 partial observation；
- `valid_graph_count=0` case 数显著下降；
- result 能区分：

```text
模型规划错误
schema 错误
selector 错误
literal 来源错误
minefield 错误
聚合失败
```

### P0-6 operation identity 与 binding identity 分离

#### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/milestone/semantics.py
```

#### 当前问题

`_canonicalize_candidate_graph()` 的 node identity 包含完整 argument source identity。两个候选只要来源写法不同，即使工具名和执行意图相同，也会分裂，严格多数均不过半。

#### 修改方案

候选聚合至少使用两层 identity：

#### 1. operation identity

用于 ToolSandbox 对齐：

```json
{
  "kind": "tool_call",
  "tool_name": "search_reminder",
  "turn_id": "turn_0",
  "occurrence_index": 0
}
```

不包含：

```text
source_ref
selector
argument literal
producer local id
```

#### 2. runtime binding identity

用于可执行性审计：

```json
{
  "field": "reminder_id",
  "producer_tool": "search_reminder",
  "selector": "$.reminder_id",
  "cardinality": "one"
}
```

聚合时：

- operation node 用 operation identity 严格多数；
- binding 用 runtime identity 单独多数；
- binding 无多数不删除 operation，只标记 unresolved；
- state goal 用 canonical state effect identity 聚合。

#### 验收

- 同工具不同 source 写法不再互相稀释；
- unresolved binding 不导致正确工具拓扑丢失；
- runtime graph 与 evaluation graph 的支持度分别可审计。

### P0-7 minefield 支持只读工具

#### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/adapter/toolsandbox/utils/effects.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

#### 语义修改

将 minefield 定义从：

```text
fatal side-effecting tool must not happen
```

改为：

```text
fatal forbidden operation must not happen
```

允许以下 reason：

- `missing_required_input`
- `unsafe_side_effect`
- `unsafe_computation`
- `unsafe_lookup`
- `unsafe_state_mutation`

初期为减少 schema 变化，可保持 reason code 不变，但 validator 不再要求 `writes`。后续再细分只读 reason。

#### 判定规则

1. 信息不足且工具 required input 无法获得：
   - 任何可见工具都可能成为 forbidden operation；
2. 工具有副作用：
   - `unsafe_side_effect`；
3. 工具会基于缺失信息计算或搜索：
   - 只读也可 fatal；
4. minefield 错误独立处理，不影响同图 operation。

#### 验收

- `timestamp_diff`、`search_reminder`、`search_lat_lon` 等参考 fatal 正例可生成；
- `minefield_contract_unverified` 显著下降；
- read-only fatal reference 的 per-tool recall 不再结构性为 0；
- 不新增泛化到所有工具的 minefield。

## 6. P0 评估层修改：先移除不可达比较

### P0-8 preserve 不应继续直接参与 goal_effect_exact

#### 修改文件

```text
milestone_reliability.py
dynsteer/milestone/semantics.py
```

#### 原因

ToolSandbox preserve guardrail 是原生 matcher 自动生成的 evaluator 内部约束，reference 使用 `milestone_index`；生成图只能使用自己的 `milestone_id`。当前 canonical identity 直接包含该引用格式，导致生成图原理上难以命中。

#### 建议分两步

#### 第一步：诊断分离

`goal_effect_exact` 暂时只要求：

```text
goals
operations
minefields
topology
```

preserve 单独报告：

```text
preserve_f1
preserve_recall
preserve_precision
```

不参与 primary exact。

#### 第二步：实现语义归一化后再纳入

将两侧 preserve reference 归一为：

```json
{
  "kind": "preserve_state",
  "namespace": "CONTACT",
  "anchor_ordinal": 3
}
```

其中 `anchor_ordinal` 由图拓扑排序得到，而不是暴露或复制 reference ID。

#### 验收

- 不再出现因 `milestone_index` vs `milestone_id` 造成的 preserve 必然 mismatch；
- preserve 指标仍单独可观测；
- 归一化规则有单元测试。

### P0-9 按工具名先计算 operation topology，再计算完整 topology

#### 修改文件

```text
milestone_reliability.py
dynsteer/milestone/semantics.py
```

#### 新增指标

```text
tool_name_multiset_exact
tool_chain_topology_exact
evaluation_topology_exact
goal_effect_exact
```

这样能区分：

1. 工具集合已正确，但 state/emit 内容不对；
2. 工具集合正确，但链顺序不对；
3. 工具集合本身缺失；
4. 全图因校验被清空。

这对后续优化非常重要，因为当前 `operation_topology_exact` 实际也依赖 set_state 和 emit 的 canonical identity，名称容易误导。

### P0-10 summary 增加 all-case penalty 与 completed-only 双口径

#### 修改文件

```text
milestone_reliability.py
```

#### 输出

每轮同时报告：

```text
all_case_operation_exact
all_case_goal_exact
all_case_empty_rate
valid_observation_rate
partial_observation_rate
fatal_schema_reject_rate
completed_only_goal_exact
graph_return_rate
```

#### 验收

- 不再因状态口径改变误判算法变化；
- 与 historical partial_main 的对比必须使用 all-case penalty。

## 7. P1 修改方案：生成图向 ToolSandbox milestone 形态投影

### P1-1 增加 ToolSandbox evaluation graph projection

#### 修改文件

```text
dynsteer/milestone/compiler.py
```

可新增内部函数：

```python
_project_toolsandbox_evaluation_graph(graph: MilestoneGraph, view: GeneratorTaskView) -> MilestoneGraph
```

不建议新建多余模块；若函数过大，再考虑独立 `projection.py`，但第一版应尽量留在 compiler 内。

#### 投影规则

1. 保留：
   - tool_call operation；
   - set_state terminal effect；
   - emit_message goal；
2. 剔除：
   - runtime-only recovery 边；
   - unresolved binding metadata；
   - 不参与人工图语义的中间审计字段；
3. 按 deterministic evaluation order 重排节点；
4. 增加 ToolSandbox 默认 milestone chain；
5. derive guardrails；
6. compile read-only fatal minefields。

### P1-2 增加 ToolSandbox 默认链边投影

#### 修改文件

```text
dynsteer/milestone/compiler.py
```

#### 设计

在 `_prune_dangling_producers()` 与 `_ordered_milestones()` 完成后，ToolSandbox benchmark 专用执行：

```python
_attach_toolsandbox_evaluation_order_edges(graph)
```

排序键建议：

1. turn order；
2. producer before consumer；
3. runtime dependency topological order；
4. terminal set_state / emit_message last；
5. stable canonical key。

然后连接：

```text
node_0 -> node_1 -> node_2 -> ...
```

#### 注意

不要在模型 candidate schema 中强制模型编造依赖边。真实依赖仍用于 runtime graph；评估投影层按 ToolSandbox 约定生成默认链。

#### 验收

当工具集合与 goal 集合正确时，evaluation topology 不因“独立 producer 不连边”而被惩罚。

### P1-3 state goal 对齐

#### 修改文件

```text
dynsteer/adapter/toolsandbox/utils/contract.py
dynsteer/milestone/compiler.py
dynsteer/milestone/semantics.py
```

#### 当前参考转换

ToolSandbox state milestone 最终转换成：

```text
namespace
operation: add/update/remove/set
expected rows
```

DynSTEER 再用 `_canonical_state_goal()` 归一为：

```text
match
values
dynamic/public literal
```

#### 生成端要求

1. `set_state` 必须是业务终态目标；
2. 不再重复输出 terminal tool_call；
3. operation 必须与 executor contract 一致；
4. match/values 只保留参考状态语义需要的字段；
5. public 字段用 structured facts；
6. dynamic 字段用 producer output；
7. canonical state identity 与 origin expected rows 归一到同一格式。

#### 验收

- `add/update/remove/set` 的工具集合通过 `_tool_name_from_node()` 能正确推导；
- generated symbolic state 与 origin snapshot state 在相同业务场景下 canonical equal；
- `state_required_input_missing` 不再导致整图丢失，而是标记 state goal partial。

### P1-4 emit message 分为 static answer 与 dynamic answer

这是提高 `goal_effect_exact` 的关键，但不能要求模型猜隐藏工具结果。

#### 修改文件

```text
dynsteer/milestone/model.py
dynsteer/milestone/compiler.py
dynsteer/milestone/semantics.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

#### 目标分类

#### 1. static public answer

答案完全来自当前可见输入，可要求具体 content。

#### 2. dynamic tool-derived answer

答案依赖工具输出，例如：

```text
最近一条消息内容
联系人关系
距离圣诞节天数
当前位置
```

生成图应表达：

```json
{
  "kind": "emit_message",
  "answer_basis": {
    "producer_local_id": "n0",
    "selector": "$.content",
    "cardinality": "one"
  }
}
```

或第一版先由 compiler 根据前置 producer 推断 answer basis，暂不改 prompt schema。

#### canonical 规则

dynamic answer 的 goal identity 不使用自然语言全文，而使用：

```json
{
  "kind": "emit_message",
  "sender": "AGENT",
  "recipient": "USER",
  "answer_kind": "message_content"
}
```

answer_kind 可由前置工具类型推断：

| producer | answer_kind |
|---|---|
| `search_messages` | `message_content` |
| `search_contacts` | `contact_information` |
| `timestamp_diff` | `duration` |
| `search_holiday` + `timestamp_diff` | `days_until_holiday` |
| `get_current_location` | `location` |
| state operation | `operation_confirmation` |

#### 参考侧归一

reference 转换时不暴露给生成器；只在 metric 阶段根据 reference 图的前置工具推导同样 answer_kind。

#### 验收

- dynamic answer 不再要求模型猜具体隐藏输出；
- reference 与 generated graph 都能归一成相同 answer goal；
- static answer 仍要求具体内容；
- 不把完全无关的回答误判为相同。

### P1-5 候选聚合使用 evaluation identity 与 runtime identity 双通道

#### 修改文件

```text
dynsteer/milestone/compiler.py
```

#### 聚合流程

```text
1. operation/state/emit 用 evaluation identity 严格多数
2. 保留的每个节点内，再对 runtime binding 严格多数
3. unresolved binding 不删节点，标记 unresolved
4. minefield 单独严格多数
5. evaluation graph 与 runtime graph 分别输出支持度
```

#### 验收

- 正确工具链不会被 source identity 分裂；
- runtime graph 仍能指出哪个参数不可执行；
- result 同时报：

```text
operation_support
binding_support
state_goal_support
minefield_support
```

### P1-6 增加第二轮 contract repair prompt

#### 修改文件

```text
dynsteer/prompt/template.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
dynsteer/milestone/compiler.py
```

#### 触发条件

当有效或 partial observation 数不足时，仅修复确定性可修复问题：

1. selector；
2. source_ref；
3. missing producer；
4. state required field；
5. minefield reason。

#### prompt 内容

不暴露 reference，只暴露：

- 当前候选图；
- 相关 output contract；
- validator issue；
- 修复规则。

#### 验收

- repair 前后图结构 diff 可审计；
- 不允许模型借 repair 改变工具拓扑，除非 issue 是 missing producer；
- repair 成功率与副作用单独报告。

## 8. P2 修改方案：建立可复现实验与回归体系

### P2-1 frozen microbenchmark

#### case 选择

先使用 30 个 historical partial_main case，再扩展到 50 个，覆盖：

- raw timestamp；
- datetime conversion；
- relative time；
- contact/reminder/message search；
- public message field extraction；
- state add/update/remove/set；
- insufficient information；
- read-only fatal minefield；
- side-effect fatal minefield；
- 3/10 distraction tools；
- tool name/description/arg scrambling；
- minefield-only empty reference。

#### 输出指标

```text
request success rate
parse rate
repair rate
partial observation rate
valid runtime graph rate
evaluation graph return rate
valid=0 case rate
tool multiset exact
evaluation topology exact
goal effect exact
fatal recall
spurious fatal
reference nonempty/generated empty
```

### P2-2 干净 A/B 归因

在改代码前后运行：

1. committed `fadbef85`；
2. 当前 HEAD；
3. P0 修复版；
4. P0+P1 修复版。

模型固定为：

```text
qwen-plus-latest
qwen3-max-2026-01-23
```

统一：

```text
temperature
seed
max tokens
retry
batch count
case order
all-case penalty
```

### P2-3 prompt/contract/reference 一致性测试

新增 pytest：

1. prompt 中所有示例 selector 可被 contract validator 接受；
2. prompt source_ref 不含不存在的示例引用；
3. structured public facts 只来自 agent-visible assets；
4. read-only minefield 可通过；
5. default chain projection 与 ToolSandbox `edge_list=None` 行为一致；
6. preserve canonicalization 不依赖真实 milestone_id；
7. dynamic emit answer 不要求隐藏输出原文；
8. operation aggregation 不因 binding 差异分裂。

### P2-4 结果快照测试

保存小型 fixture：

```text
candidate raw response
parsed graph
repaired graph
validation issues
aggregated runtime graph
evaluation projection
semantic metrics
```

任何 projection 变化都必须显式更新快照，避免隐性指标漂移。

## 9. 建议实施顺序

### Phase A：只修确定性断点，不做大架构调整

预计改动：

1. output contract payload；
2. 冲突 prompt 示例；
3. selector normalization；
4. public structured facts；
5. minefield 允许只读；
6. preserve 从 primary exact 分离；
7. 诊断字段。

预期观察：

```text
contract_incomplete 下降
public_literal_mismatch 下降
minefield_contract_unverified 下降
valid=0 case 数下降
```

### Phase B：错误分级与 operation identity 分离

预计改动：

1. partial observation；
2. operation/runtime 双 identity；
3. binding 单独聚合；
4. all-case summary 口径。

预期观察：

```text
operation graph 不再因参数错误整图消失
valid_graph_count 与 graph_return_rate 改善
诊断能区分规划失败与契约失败
```

### Phase C：ToolSandbox evaluation projection

预计改动：

1. default chain；
2. state goal canonical projection；
3. dynamic emit answer；
4. guardrail 归一；
5. minefield-only 空图策略。

预期观察：

```text
evaluation topology exact 改善
goal effect exact 改善
read-only fatal recall 改善
```

### Phase D：repair pass 与微基准扩展

预计改动：

1. contract repair prompt；
2. frozen microbenchmark；
3. A/B 报告；
4. snapshot tests。

## 10. 关键代码修改点汇总

| 文件 | 修改 |
|---|---|
| `dynsteer/prompt/template.py` | payload 增加 output contracts、structured facts、target grammar；确保 source_ref 真实。 |
| `dynsteer/prompt/templates/milestone/generation.en.md` | 删除冲突示例；解释 `$`、`$.field + one/all`、read-only minefield、ToolSandbox milestone 顺序语义。 |
| `dynsteer/prompt/templates/milestone/generation.zh.md` | 与英文同步。 |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 构造 agent-visible structured public facts；不暴露 evaluator-only 状态。 |
| `dynsteer/adapter/toolsandbox/utils/effects.py` | 增加工具输出契约展示辅助；不要求 minefield 工具必须 writes。 |
| `dynsteer/milestone/model.py` | 增加 partial observation、repair、runtime/evaluation graph 诊断字段。 |
| `dynsteer/milestone/compiler.py` | selector repair、错误分级、双 identity 聚合、default chain projection、read-only minefield、state/emit projection。 |
| `dynsteer/milestone/semantics.py` | operation/tool name 指标拆分；preserve 归一；dynamic emit answer canonicalization。 |
| `milestone_reliability.py` | all-case penalty、completed-only、partial/repair/evaluation projection 指标。 |
| tests | prompt/contract 一致性、selector repair、partial observation、minefield、default chain、state/emit canonicalization。 |

## 11. 明确不建议的做法

1. **不建议只改 prompt**。它会降低 selector/literal 错误，但无法解决 topology、preserve、emit answer 与 partial observation 的结构性错位。
2. **不建议只放宽 validator**。工具名可能短期变好，但会引入捏造参数和不安全 minefield。
3. **不建议把 reference milestone index 或 expected rows 暴露给生成器**。这会泄漏评估答案。
4. **不建议要求模型直接猜动态工具输出的完整回答原文**。例如最近消息内容、联系人关系、距离天数。
5. **不建议把 runtime graph 和 evaluation graph 混为一张图**。前者需要严格 binding，后者需要与 ToolSandbox snapshot matcher 对齐。
6. **不建议继续让 preserve 内部引用格式直接决定 primary exact**。
7. **不建议继续用单一 `operation_topology_exact` 概括工具名、state goal、emit content 和拓扑**。应拆分诊断指标。

## 12. 成功标准

### 技术正确性

1. prompt、payload、repair、validator 使用同一 output contract；
2. structured public facts 只来自 Agent 可见输入；
3. runtime binding unresolved 可审计；
4. minefield 与 ToolSandbox 安全语义一致；
5. evaluation projection 不泄漏 reference；
6. all-case 与 completed-only 双口径同时输出。

### 微基准目标

第一阶段不设定论文级目标，只要求相对当前明显改善：

```text
parsed rate >= 99%
repair 可审计率 100%
fatal schema reject rate <= 5%
valid=0 case rate 显著低于当前 56.4%
partial observation 可解释率 >= 90%
read-only fatal reference 不再结构性不可达
```

### 主指标目标

在 30/50 frozen microbenchmark 上观察：

1. `tool_name_multiset_exact` 先提升；
2. `evaluation_topology_exact` 随 default chain 与 goal canonicalization 提升；
3. `goal_effect_exact` 在 preserve 分离和 dynamic emit 归一后提升；
4. fatal recall 提升，同时 spurious fatal 不显著上升。

## 13. 风险与替代方案

### 风险一：dynamic emit answer 归一过粗

如果只按 answer_kind 匹配，可能把不同具体问题误判为同一目标。

替代方案：

- answer_kind 加入 query namespace；
- static answer 保持 exact；
- 对 dynamic answer 增加前置工具 multiset 条件；
- 后续引入受控 semantic matcher，但不用 LLM 直接放宽所有 validator。

### 风险二：default chain projection 可能掩盖真实依赖错误

替代方案：

- runtime graph 保留 true dependency；
- evaluation graph 单独输出 chain；
- summary 分别报告 runtime topology 与 evaluation topology。

### 风险三：partial observation 可能高估图质量

替代方案：

- partial 不计入 runtime valid graph；
- 单独报告 partial rate；
- primary 指标使用 evaluation graph，但审计指标必须包含 unresolved binding。

### 风险四：structured public facts 覆盖不足

替代方案：

- 第一版只对高置信字段启用；
- 未覆盖消息仍要求整段 exact；
- 报告 structured fact coverage；
- 不用模糊 substring 全局放宽。

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox 人工图语义的完整无损复刻**
   - 我已确认原生图是 snapshot matcher，并核查了默认线性链、guardrail 和 minefield 设计。
   - 但尚未逐个执行 129 个基础 scenario，因为当前 uv 环境缺少 ToolSandbox 运行依赖 `dill`。
   - 因此本方案中的统计来自对本地 ToolSandbox scenario 源码的只读 AST 解析，不能保证覆盖运行期动态生成或序列化时的所有边界情况。

2. **dynamic emit answer 的无泄漏 canonicalization**
   - 可以确定当前 `content_requirement` 与人工图具体回答文本不一致是结构性问题。
   - 但如何把 `search_messages`、`search_contacts`、`timestamp_diff` 等答案细分为足够精确的 answer_kind，而不引入过粗匹配，需要用 microbenchmark 验证。
   - 该部分是本方案中不确定性最高的修改。

3. **preserve guardrail 的完全等价重建**
   - preserve 是 ToolSandbox `MilestoneMatcher` 根据 milestone 图、reference snapshot 和 predecessor 自动生成的。
   - 简单把 `milestone_index` 与 `milestone_id` 归一为 ordinal 未必在所有图形态下等价。
   - 因此第一阶段建议先将 preserve 从 primary exact 分离，之后再重建语义等价的 guardrail projection。

4. **operation identity 与 runtime binding 的最优聚合关系**
   - 将 operation identity 简化为工具名 + turn + occurrence 能对齐主指标，但可能掩盖同一工具在不同参数意图下的多目标情况。
   - 需要通过多工具、多轮、同工具重复调用的 case 验证 occurrence 与 state target 的组合规则。

5. **structured public facts 的完整抽取覆盖**
   - 姓名、电话、日期、时间、坐标等可先确定性抽取。
   - 但 ToolSandbox 消息中可能存在复合指令、指代和隐含字段。
   - 不宜为了指标放宽所有 substring，必须逐类建立覆盖率和误报率统计。

6. **ToolSandbox 真实工具返回 shape 与当前 `$` 契约的最终对齐**
   - 当前 `effects.py` 将 raw scalar 输出统一为 `$`。
   - 虽然从当前 validator 设计看这是内部 contract，而非必须等于工具原始 JSON 字段，但仍应用真实工具 runtime 采样确认。
   - 若未来决定 contract 改为 `$.timestamp`，prompt、repair 与 validator 必须同步修改，不能只改一处。
