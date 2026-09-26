# Milestone graph 单图稳定性修复方案

- 日期：2026-09-25
- 状态：修订版方案，替代 `2026-09-25-milestone-generation-toolsandbox-alignment-optimization-plan.md` 中“双层图 / evaluation projection”方向的建议
- 审计依据：`docs/plans/2026-09-25-milestone-generation-regression-audit.md`
- 主要问题结果：`results/milestone/toolsandbox_milestone_reliability_main`
- 本方案只修改生成与评估的设计方案，不直接修改生成代码

## 1. 修订结论

用户补充意见成立：前版方案中“candidate runtime graph + ToolSandbox evaluation graph”的双层图设计过重，会把生成器架构绑定到 ToolSandbox 的 snapshot matcher 形态上，损害跨 benchmark 泛化性。尤其是要求生成端补齐人工标注中的期望回答原文、默认线性链或 preserve 内部引用，本质上是在拟合标注格式，而不是提升执行前 milestone 推理质量。

修订后的目标改为：

> 保持一个通用的 `MilestoneGraph` 输出，先修复当前链路中确定性的 prompt / contract / validation 不一致，再把“任一字段错误导致整图拒绝”改为可审计的分级校验与部分观测聚合。ToolSandbox 人工图只用于理解最终指标和提供诊断拆分，不反向要求生成器复制其标注形态。

因此，本方案明确不做以下事情：

1. 不新增 runtime graph / evaluation graph 双层图；
2. 不把 ToolSandbox 默认线性链强行写入生成图；
3. 不要求模型预测隐藏工具输出的期望回答原文；
4. 不把 reference milestone index、expected rows 或人工答案文本暴露给生成器；
5. 不因为要对齐 ToolSandbox 而放宽所有校验；
6. 不改变“无有效观测时返回空图”的安全策略，仅改善其原因与指标口径的可观测性。

## 2. 保留的核心事实

当前 main 结果的主要断点仍然是确定性的：

```text
request success: 1997 / 1997
returned graphs: 3979
parsed graphs:   3969
valid graphs:     843
rejected graphs: 3136
valid=0 case:     287 / 509
```

这说明问题不在 LLM 调用或 JSON 解析，而在候选图校验与聚合。必须优先修复：

1. prompt 示例 selector 与私有 output contract 不一致；
2. prompt 没有暴露 output contract，却要求 selector/cardinality 匹配真实输出；
3. prompt 示例 `instruction:0` 与真实 `instruction` 不一致；
4. public literal 的语义在 instruction 与 public asset 两类来源上不一致；
5. minefield 被限制为必须具有 `writes` 的副作用工具，与“错误调用只读工具也可能是 fatal”的任务安全语义冲突；
6. 任一字段级 issue 导致整张候选图丢失；
7. canonical identity 过细，同一工具意图会因 binding 表述差异分裂，低样本下难以取得严格多数。

这些问题是通用正确性问题，不是 ToolSandbox 特化问题，应全部保留在 P0 中。

## 3. 修订后的目标链路

仍维持单张 `MilestoneGraph`：

```text
GeneratorTaskView
    -> LLM candidate graphs
    -> exact JSON parse
    -> deterministic normalization / repair
    -> graded validation
    -> valid + partial observations
    -> operation-level strict majority
    -> field-level binding majority
    -> one MilestoneGraph
```

这里的 `partial observation` 不是第二张图，而是候选图审计状态：结构可解析、部分节点或 binding 可用时，不让整张图从 ensemble 中消失。最终仍只编译一个通用 milestone graph。

### 3.1 设计原则

1. **契约先行**：模型能看到哪些 selector，就只允许使用哪些 selector；validator 校验同一契约。
2. **确定性优先**：能由 contract 唯一推断的 selector、source_ref、cardinality 先做确定性修复，不额外请求 LLM。
3. **错误分级**：schema 破损、节点局部错误、图结构错误分开处理，不用一个 `issues` 列表决定整图生死。
4. **聚合语义分层**：operation 意图与 runtime binding 分开计数，但输出仍是同一图。
5. **安全保守**：无法闭合的 binding 不伪装成可执行；无有效观测继续返回空图。
6. ** benchmark 只影响诊断**：ToolSandbox 图形态用于解释指标差异，不作为生成 schema 或拓扑改写规则。

## 4. P0-1 统一 prompt 与 output contract

### 修改文件

```text
dynsteer/prompt/template.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

### 当前问题

`GeneratorTaskView` 中存在 `tool_contracts`，validator 通过它校验：

```text
selector
cardinality
output type
required_dynamic_inputs
effect
fatal_reasons
```

但 prompt payload 只有：

```text
tool_schema
public_assets
public_state
evidence_catalog
```

同时 `_FORBIDDEN_GENERATION_INPUTS` 明确禁止把私有 `tool_contracts` 直接放入 prompt。这导致模型被要求匹配一个不可见契约，只能靠示例猜。

### 修改方案

在 `MilestonePromptBuilder` 中新增安全展示字段：

```json
"tool_output_contracts": {
  "evidence_id_1": {
    "tool_name": "search_reminders",
    "outputs": {
      "reminder_id": {
        "selector": "$.reminder_id",
        "type": "string",
        "cardinality": ["one", "all"]
      }
    }
  }
}
```

第一版只暴露：

```text
evidence_id
tool_name
outputs.selector
outputs.type
outputs.cardinality
```

不暴露：

```text
simulation_state
environment_rules
fatal_reasons
writes
reference graph
expected final state
```

这样不会泄漏评估答案，却能让模型与 validator 使用同一 output grammar。

### prompt 规则重写

英文与中文模板必须同步声明：

1. raw scalar 工具输出使用：

```json
{"selector": "$", "cardinality": "one"}
```

2. 结构化输出才使用 contract 中列出的 `$.field`；
3. 不允许凭工具名或常识发明 selector；
4. `cardinality` 必须来自 output contract；
5. producer 必须是同一候选图中更早的 `tool_call`；
6. prompt 示例若使用抽象 evidence，必须同时给出对应抽象 output contract，且示例能被 validator 接受。

### 必须删除或改写的示例

```text
$.timestamp -> 若示例 producer 是 raw scalar，应改为 $
$.reminders[0].reminder_id -> 应改为示例 contract 明确支持的 selector
instruction:0 -> instruction
```

示例可以继续使用假想任务，但必须满足：

```text
示例 evidence 存在
示例 source_ref 存在
示例 selector 来自示例 output contract
示例 cardinality 被示例 contract 支持
```

### 验收

新增 prompt 一致性测试，扫描两个语言模板中的所有结构性示例，确保不存在已知冲突；更重要的是，将示例 payload 送入 contract 校验时可以通过。

## 5. P0-2 明确并统一 public literal 语义

### 修改文件

```text
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
dynsteer/milestone/compiler.py
dynsteer/adapter/toolsandbox/utils/contract.py
```

### 当前问题

当前行为实质上是：

1. `instruction` 来源：literal 只要在 instruction 文本中出现即可；
2. `public_assets` 来源：literal 必须与整个 asset value 完全相等。

这既不符合模型对“来自某条消息的字段”的自然理解，也导致姓名、电话、日期、时间、地点等合理抽取被 `public_literal_mismatch` 拒绝。

### 修订原则

不引入模糊语义匹配，不接受任意模型改写，但允许“来自 Agent 可见文本的精确标量片段”。

建议统一为：

1. 字符串型 public asset：literal 必须是 source 文本中实际出现的标量文本片段，并通过目标 schema 类型校验；
2. 非字符串 public asset：仍要求 JSON 标量完全相等；
3. 数组或对象 asset：只允许引用其中的 JSON leaf，不允许伪造；
4. instruction 与 public asset 采用同一可解释规则；
5. normalization 只做空格、日期时间表面形式等已有 deterministic normalizer 支持的归一，不做 embedding 或 LLM fuzzy match；
6. prompt 明确说明 source_ref 的含义与可引用粒度。

### 实现方式

在 compiler 中抽出一个小型共享函数：

```python
_literal_visible_in_public_source(literal: JsonValue, source_value: JsonValue, field: str) -> bool
```

让 instruction 与 public asset 共用同一语义。不要为两个来源维护两套相似逻辑。

### 不采用的替代方案

不建议直接把整段消息拆成模型可见的姓名、电话、时间等“伪 public facts”，除非这些事实原本就是结构化 Agent 可见数据。自然语言 NER 抽取容易引入 benchmark 特化规则和错误 provenance。

### 验收

1. `public_literal_mismatch` 明显下降；
2. literal 仍不能是来源文本中不存在的值；
3. 字符串片段不会绕过 number/boolean/date schema 校验；
4. 不引入模糊匹配。

## 6. P0-3 parse 后、validate 前增加确定性修复

### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/milestone/model.py
```

### 修复阶段位置

```text
_parse_candidate_batch
    -> _repair_candidate_graph
    -> _validate_candidate_graph
    -> _canonicalize_candidate_graph
```

修复必须发生在 validation 之前，并在 report 中留下完整 audit。

### 6.1 selector 修复

仅当 contract 能唯一确定结果时修复：

1. producer contract 只有一个 output selector 时，将错误非空 selector 修复为唯一 selector；
2. raw scalar contract 中，将 `$.timestamp`、`$.value`、`$.result` 等常见字段形式修复为 `$`；
3. `$.reminders[0].reminder_id` 在 contract 仅存在 `$.reminder_id` 时修复为 `$.reminder_id`；
4. selector 正确但 cardinality 缺省或写错时，仅当 contract 只支持一个 cardinality 时修复；
5. 多个候选 selector 无法唯一映射时，不修复，保留 issue。

### 6.2 source_ref 修复

只允许确定性别名修复：

```text
instruction:0 -> instruction
```

更一般地，若 `source_ref` 不存在，但去掉末尾 `:0` 后能得到唯一存在的 Agent 可见 source，则可修复；否则不猜测。

### 6.3 结构轻量修复

1. producer 存在、binding direction 正确，仅缺 producer→consumer edge：继续由 validator 自动补边；
2. edge 端点存在且方向不造成环时，将多余反向 dependency edge 标记为 non-fatal ensemble edge；
3. 不修复 unknown evidence、unknown tool、unknown turn、缺 required argument、捏造节点等实质规划错误。

### 审计输出

`GenerationReport` 增加：

```text
repaired_graph_count
repair_action_count
repair_actions
```

每条 repair action 包含：

```text
batch_index
graph_index
node_local_id
field
before
after
basis
```

### 验收

1. `contract_incomplete` 中可唯一修复的 selector 错误消失；
2. repair 不改变工具意图，只改变 binding 表述；
3. 每个 repair 可追溯；
4. 无法唯一确定时不静默猜测。

## 7. P0-4 取消整图拒绝，改为分级校验与 partial observation

这是本方案的第二项必改点。

### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/milestone/model.py
```

### 当前问题

`_validate_candidate_graph` 只要发现任意 issue 就返回 `None`。一个 selector、一个 public literal、一个 minefield reason 错误，都会让已经正确的工具链、状态目标、消息目标和 minefield 全部丢失。

### 7.1 issue 分级

将 issue 至少分为四级：

```text
fatal_parse
fatal_graph
node_local
field_local
warning
```

建议归类如下。

#### fatal_parse

整张候选对象不可靠，不能作为 observation：

```text
disposition 覆盖缺失
node kind / local_id 破损
value source 结构破损
unknown turn
unknown evidence
unknown tool argument 造成的节点语义不明
```

#### fatal_graph

影响候选图结构，但不应自动丢弃所有节点：

```text
graph cycle
binding producer 不存在
future turn binding
binding type 无法兼容且无法修复
```

处理方式是删除相关 binding/edge 或相关节点，保留其余可闭合部分。

#### node_local

只影响一个节点：

```text
missing required tool argument
state required input missing
state contract mismatch
invalid message route
empty content requirement
```

处理方式是删除该节点，保留同图其他节点与 minefield。

#### field_local

只影响一个 binding 字段：

```text
selector mismatch 且无法修复
cardinality mismatch 且无法修复
public literal mismatch
schema value mismatch
```

处理方式是标记该 binding unresolved 并从可执行绑定中排除；若该字段是 required argument，则删除对应 tool node，不影响其他节点。

#### warning

不阻塞 observation：

```text
重复 edge
显式 edge 与自动 binding edge 重复
非关键 metadata 形式问题
```

### 7.2 observation 状态

在 `GenerationReport` 中新增：

```text
partial_graph_count
fatal_parse_graph_count
node_pruned_in_partial_count
binding_unresolved_count
```

candidate summary 的 status 增加：

```text
valid
partial
fatal_parse
```

不再只有 accepted / rejected。

### 7.3 聚合规则

partial observation 参与 ensemble，但只有其中通过校验的节点参与对应节点多数投票：

```text
operation node majority: valid + partial 中保留下来的节点
binding majority:       保留下来的同 operation 节点内部再投票
minefield majority:     通过契约核实的 minefield
```

不允许：

1. unresolved binding 参与可执行 closure；
2. 已删除节点参与 operation majority；
3. fatal parse 图参与任何语义投票。

### 7.4 安全边界

如果 partial 修复后没有可闭合节点，则该 observation 仍不产出节点；case 层面若所有 observation 均为空，继续返回空图。这保留了当前安全策略。

### 验收

1. 3136 个 rejected 中可解释的 node-local / field-local issue 不再导致整图丢失；
2. `valid_graph_count + partial_graph_count` 显著上升；
3. `valid=0 case` 数显著下降；
4. 每个 partial 图的删节点、删 binding、删边原因可审计；
5. 不把缺 required argument 的工具误报为可执行。

## 8. P0-5 minefield 改为通用错误调用语义

### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/adapter/toolsandbox/utils/effects.py
dynsteer/prompt/templates/milestone/generation.en.md
dynsteer/prompt/templates/milestone/generation.zh.md
```

### 当前问题

当前逻辑要求：

```python
contract.get("writes")
```

才允许 minefield。这把 “fatal minefield” 等同于 “有副作用的工具”。但任务安全语义中，错误调用只读工具同样可能 fatal，例如：

```text
timestamp_diff
search_reminder
search_lat_lon
```

这不是为了拟合 ToolSandbox 标注，而是通用任务安全事实：错误工具调用本身可能构成任务失败。

### 修改方案

1. minefield 是否可信由 `fatal_reasons` 或明确的 forbidden-tool contract 支持，不再要求 `writes`；
2. `writes` 只用于判断 terminal side-effect conflict，不作为 minefield 存在条件；
3. reason code 建议从当前的 `unsafe_side_effect` 扩展或改名为更通用的：

```text
unsafe_tool_call
```

4. `missing_required_input` 适用于任何工具调用前无法确定必需输入的情况；
5. prompt 不再说 “minefield 必须是 side-effecting tool”，改为 “调用该工具会不可逆地偏离任务目标，或会基于缺失/错误输入产生 fatal 错误”；
6. 同一 turn 中同一工具既是 executable goal 又是 fatal minefield 时仍冲突，但 read-only producer 与 read-only forbidden tool 应按 evidence/tool intent 区分。

### 验收

1. read-only fatal 正例不再结构性不可达；
2. spurious fatal 不显著上升；
3. minefield 仍必须有契约依据，不能由模型随意标记所有工具；
4. `minefield_contract_unverified` 下降。

## 9. P0-6 保持空图策略，只改善口径与原因

### 修改文件

```text
dynsteer/milestone/compiler.py
milestone_reliability.py
```

### 明确决策

无有效观测时返回空图是合理的兼容性与稳定性举措，本方案不改变该行为。

但需要区分：

```text
generation_failed
no_parsed_graph
no_valid_or_partial_observation
no_majority_operation
majority_result_is_empty
```

建议 `empty_reason` 至少拆为：

```text
generation_failed
no_observation
no_majority_operation
no_safe_terminal_goal
```

### summary 双口径

保留 all-case penalty 作为主兼容口径，空图继续计为失败；同时输出诊断口径：

```text
all_case_goal_effect_exact
all_case_operation_exact
all_case_empty_rate

nonempty_graph_goal_effect_exact
nonempty_graph_operation_exact
nonempty_graph_rate
```

这样既不美化空图，也能观察“生成器确实返回图之后”的质量，避免 M12 类状态口径变化误导归因。

## 10. P1-1 operation identity 与 binding identity 分离

### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/milestone/model.py
```

### 当前问题

`_node_identity()` 把每个 argument / match / value 的完整 source identity 放进 node key。结果如下两张图会被视为不同节点：

```text
图 A：search_contacts(person=$.person_id)
图 B：search_contacts(person=$.contact_id)
```

即使二者 contract 语义相同，也会因字段路径表述差异分裂，进而在低样本下无法取得严格多数。

### 修改方案

在 `_CanonicalNode` 中保存两个 key：

```text
operation_key
binding_key
```

#### operation_key

只描述任务意图：

```text
tool_call:
    turn_id
    evidence_id 或 tool_name
    occurrence

set_state:
    turn_id
    namespace
    operation
    cardinality
    executor tool identity

emit_message:
    turn_id
    sender
    recipient
    normalized content requirement
```

#### binding_key

在 operation_key 之下描述：

```text
arguments.{field}.source
arguments.{field}.selector
arguments.{field}.cardinality
...
```

### 聚合流程

```text
1. operation_key 严格多数决定节点是否保留
2. 保留节点的每个字段再按 binding_key 严格多数
3. 无严格多数时选择唯一 contract-supported binding
4. 仍无唯一结果则 binding unresolved
5. unresolved required binding 删除该节点并记录 issue
```

这不是双层图，只是同一图内部的聚合粒度拆分。

### 验收

1. selector 表述差异不再拆散正确工具链；
2. 不同参数意图的同名工具仍可通过 occurrence / evidence / target semantics 区分；
3. binding unresolved 不伪装为可执行；
4. support 中同时输出 operation support 与 binding support。

## 11. P1-2 保持真实依赖拓扑，不强行生成 ToolSandbox 线性链

### 修改文件

```text
dynsteer/milestone/compiler.py
dynsteer/milestone/semantics.py
milestone_reliability.py
```

### 设计决策

不把 ToolSandbox `edge_list=None` 时的默认链写入生成图。生成图继续保留：

```text
producer -> consumer dataflow
turn order recovery edge
可多数聚合的 ensemble dependency edge
```

这能保留跨 benchmark 的真实依赖语义，避免为了一个 benchmark 的默认标注形式而改写图。

### 诊断指标

为了解释与 ToolSandbox reference 的差异，可以额外报告：

```text
tool_multiset_exact
tool_turn_sequence_exact
dataflow_topology_exact
current_graph_topology_exact
```

这些指标只是诊断，不改变生成图，也不作为 P0 修改依据。

如果后续发现工具集合与 turn sequence 正确而 topology 指标仍系统性偏低，再单独讨论是否在 benchmark adapter 的比较层做线性顺序解释，而不是改生成器。

## 12. P1-3 emit message 不要求期望回答原文

### 修改文件

```text
dynsteer/milestone/semantics.py
milestone_reliability.py
```

### 设计决策

撤回前版方案中的 static/dynamic answer canonicalization 作为必做项。生成端继续使用：

```text
content_requirement
```

因为它表达的是“回答应满足的要求”，不是对隐藏工具输出的预测。要求模型生成期望原文既不可验证，也会诱导编造。

### 可保留的诊断拆分

只在 metrics 中区分：

```text
emit_goal_present
emit_route_correct
emit_requirement_nonempty
```

暂不把回答文本 exact match 作为生成修复目标。若后续发现消息类 case 的主要差距在回答内容评估，再研究不泄漏 reference 的 semantic matcher，而不是在 prompt 中要求猜原文。

## 13. P1-4 preserve 只作为独立诊断，不参与生成逻辑

### 修改文件

```text
dynsteer/milestone/semantics.py
milestone_reliability.py
```

ToolSandbox 的 preserve guardrail 属于 evaluator 内部机制，reference 侧使用 `milestone_index`，生成侧使用生成图内部 milestone id。直接比较内部引用格式会把结构性差异误判为业务语义差异。

本方案只建议：

```text
preserve_f1
preserve_recall
preserve_precision
```

单独报告，暂不修改生成逻辑，也不强制实现完全等价 canonicalization。主修复首先集中在 operation graph 是否能返回。

## 14. P2 可选 repair pass

如果 P0 后 `valid + partial observation` 仍不足，再考虑第二轮 LLM repair，不作为第一阶段必做。

### 触发条件

```text
valid + partial observation count < target_candidate_graph_count
```

### 输入

只提供：

```text
当前候选图
安全 output contract
validator issue
确定性修复规则
```

不提供：

```text
reference graph
final state
expected answer
```

### 限制

1. 不允许改变工具拓扑，除非 issue 是 missing producer；
2. 只修复 selector、source_ref、cardinality、minefield reason；
3. repair 前后 diff 全量落盘；
4. repair 失败不覆盖原始 observation。

## 15. 测试方案

### 15.1 单元测试

新增或扩展 pytest：

1. prompt 英文 / 中文模板中的结构性示例与 contract 校验一致；
2. `instruction:0` 可确定性修复为 `instruction`；
3. raw scalar selector 可修复为 `$`；
4. 唯一字段 selector 可从 indexed path 修复；
5. 无法唯一映射的 selector 不被静默修复；
6. public asset 字符串片段通过可见性与 schema 校验；
7. 伪造 literal 仍被拒绝；
8. node-local issue 只删除受影响节点；
9. field-local issue 只标记 binding unresolved；
10. required argument unresolved 时删除工具节点；
11. partial observation 参与聚合；
12. fatal parse 图不参与聚合；
13. read-only fatal minefield 可通过；
14. 同一 terminal effect 与 minefield 冲突仍被拒绝；
15. operation majority 不因 binding selector 差异分裂；
16. binding 多数冲突有审计记录；
17. 空图 `empty_reason` 正确区分 generation failure 与无观测；
18. summary 同时输出 all-case 与 nonempty 诊断口径。

### 15.2 快照测试

为以下阶段保存 fixture：

```text
raw response
parsed graph
repair actions
graded issues
partial observation
aggregated operation support
aggregated binding support
final single MilestoneGraph
```

### 15.3 frozen microbenchmark

先复用 historical partial_main 的 30 个 case，再扩展 50 个，覆盖：

```text
raw scalar 输出
结构化输出
selector 别名
public message 字段抽取
state add/update/remove/set
insufficient information
read-only fatal minefield
side-effect fatal minefield
同名工具多次调用
```

输出对比：

```text
修复前 / 后 parsed rate
repair rate
valid rate
partial rate
fatal parse rate
valid=0 case rate
operation graph return rate
empty graph rate
all-case goal effect
nonempty graph goal effect
```

## 16. 实施顺序

### Phase A：契约与 prompt 修复

```text
1. 构造安全 tool_output_contracts payload
2. 修正英文 / 中文模板示例
3. 明确 public literal 语义
4. 增加 prompt 一致性测试
```

预期：

```text
contract_incomplete 下降
unknown_public_source 下降
public_literal_mismatch 下降
```

### Phase B：确定性修复与错误分级

```text
1. _repair_candidate_graph
2. issue severity model
3. partial observation
4. report 统计扩展
```

预期：

```text
rejected_graph_count 不再等价于整图丢失
partial_graph_count 上升
valid=0 case 显著下降
```

### Phase C：minefield 与聚合稳定性

```text
1. read-only fatal 支持
2. operation_key / binding_key 分离
3. binding 单独多数
4. unresolved closure 审计
```

预期：

```text
read-only fatal recall 不再结构性为 0
正确工具链不再因 binding 表述分裂
```

### Phase D：指标诊断与 microbenchmark

```text
1. empty reason 拆分
2. all-case / nonempty 双口径
3. tool multiset 与 dataflow topology 诊断
4. preserve 独立诊断
5. frozen 30/50 case 回归
```

## 17. 代码修改点汇总

| 文件 | 修改内容 |
|---|---|
| `dynsteer/prompt/template.py` | 构造安全 `tool_output_contracts` payload，继续过滤 evaluator-only 信息。 |
| `dynsteer/prompt/templates/milestone/generation.en.md` | 修正 selector、source_ref、public literal、minefield 与 output contract 规则。 |
| `dynsteer/prompt/templates/milestone/generation.zh.md` | 与英文模板同步，避免双语行为漂移。 |
| `dynsteer/milestone/compiler.py` | 增加确定性修复、issue 分级、partial observation、read-only minefield、operation/binding 分离聚合。 |
| `dynsteer/milestone/model.py` | 扩展 `GenerationReport` 的 repair、partial、unresolved 审计字段。 |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 保持 Agent-visible source 构造一致，必要时提供结构化 JSON leaf 的来源语义。 |
| `dynsteer/adapter/toolsandbox/utils/effects.py` | 将 fatal reason 与 writes 解耦，补充只读工具错误调用的契约支持。 |
| `dynsteer/milestone/semantics.py` | 增加工具集合、真实依赖拓扑、preserve 独立诊断，不改变生成图形态。 |
| `milestone_reliability.py` | 输出 all-case 与 nonempty 双口径，保留空图 all-case penalty。 |
| tests | 覆盖 prompt contract 一致性、修复、分级校验、partial 聚合、minefield、identity 聚合。 |

## 18. 成功标准

第一阶段不设定论文级指标目标，以回归修复为准：

```text
parsed rate 保持 >= 99%
prompt 结构性示例校验通过率 = 100%
可唯一修复 selector 的 repair audit 覆盖率 = 100%
fatal parse rate <= 5%
valid + partial observation rate 显著高于当前 21.2%
valid=0 case rate 显著低于当前 56.4%
read-only fatal reference 不再结构性不可达
binding unresolved 可解释率 >= 90%
```

主结果观察顺序：

1. 先看 `valid + partial observation rate`；
2. 再看 operation graph return rate；
3. 再看 tool multiset / dataflow topology；
4. 最后看 goal effect exact。

不应在候选图仍大量整图丢失时，直接用最终 goal exact 判断生成质量。

## 19. 风险与约束

### 风险一：public literal 允许文本片段后 provenance 变宽

必须坚持“精确出现在 Agent 可见文本中 + schema 类型兼容”两个条件；禁止语义改写、模糊匹配和跨来源拼接。

### 风险二：partial observation 可能高估生成质量

partial 只保留可闭合节点，报告必须同时输出被删除节点和 unresolved binding。all-case penalty 仍将最终空图计为失败。

### 风险三：operation_key 可能合并同名不同意图工具

需要用 evidence、turn、occurrence 与 state target 语义约束 operation identity，并在重复工具 case 上验证。

### 风险四：minefield 放宽后可能出现误报

fatal 仍必须由私有契约或 forbidden-tool 语义核实；不允许模型仅凭“看起来不该调用”自由标注。

### 风险五：只修 P0 后最终 goal exact 仍不高

这是可接受的。最终指标还受 state snapshot、emit content、preserve 等评估语义影响。应先确认候选图与 operation graph 不再结构性丢失，再评估是否需要后续 metric 层诊断。

## 附录A. 项目中没有把握实现的模块部分

1. **public asset 精确片段语义的完全无损覆盖**
   - 对字符串型 Agent 可见消息允许“实际出现的标量片段”是通用且合理的方向。
   - 但日期、电话、坐标等表面形式存在多样化 normalization 边界；第一版必须只启用已有确定性 normalizer，避免引入模糊匹配。
   - 我没有把握一次性覆盖所有文本形式，需要通过 microbenchmark 统计误放与漏放。

2. **issue 分级的最终边界**
   - node-local 与 field-local 的划分原则清晰，但 state goal 同时涉及 executor contract、match、values 和 required dynamic input，某些错误会同时影响节点闭合与图结构。
   - 需要在实现时以“删除后剩余图是否仍可闭合”为准迭代归类，不能只靠 issue code 静态分类。

3. **operation_key 的最优粒度**
   - evidence_id、turn、occurrence 与 target semantics 的组合可以避免多数工具误合并，但同名工具在同一 turn 中承担不同意图的 case 仍可能出错。
   - 需要用重复工具和多阶段状态修改 case 验证。

4. **read-only fatal 的契约来源**
   - 语义上应支持只读工具 fatal，但当前私有契约是否已为所有相关工具提供充分 `fatal_reasons` 需要逐项核查。
   - 不应为了提高 recall 而把所有只读工具默认视为 fatal。

5. **P0 修复后的最终 goal effect 提升幅度**
   - P0 解决的是候选图结构性丢失和契约不一致，不直接解决 reference 的 snapshot matcher、preserve、消息内容等评估语义差异。
   - 因此我无法承诺 P0 后 goal exact 达到某个固定数值，只能承诺 operation graph return 与候选观测覆盖率显著改善。
