# DynSTEER milestone graph 最小化修复方案

> **已废止（2026-08-11 二次复核）**：本方案虽然删除了 binding 等抽象，但仍引入 10 个任务族规则和 required-capability DAG，与 LLM 规划形成重复真值，存在继续过拟合 ToolSandbox 的风险。不得按本方案实施。最终采用 `2026-08-11-milestone-generation-deletion-first-repair-plan.md`。

## 1. 方案结论

上一版修复方案无效，原因是它在尚未删除错误实现前，又计划新增 4 个生产模块和多组数据模型。当前应执行“删除优先”的最小修复：

1. 删除与 graph 生成正确性无关的冻结、摘要、候选支持度和重复报告字段；
2. 删除导致正确路径不可表达的强 binding 协议及其 fatal 校验；
3. 删除关键词 invariant、case ID disposition 和最短路径放大器；
4. 将 GoalContract 收缩为“逐轮必要 capability、依赖边、response 与禁止 capability”；
5. LLM 只返回 evidence ID 序列，不再返回参数来源、selector、理由和候选策略；
6. ToolSandbox adapter 用少量有限任务规则从公开 instruction、可用工具和初始 setting 构造必要步骤；
7. reliability 第一阶段只比较 operation multiset、topology 和 fatal minefield，不比较当前不可同构的 disposition、binding 和 response。

本方案不新增生产代码文件。预计相对当前工作区净删除不少于 250 行生产有效代码；若实施后生产代码净增加，必须停止并重新审查。

## 2. 当前代码冗余审计

### 2.1 明确只写不读或无生产消费者的字段

| 字段 | 当前情况 | 处理 |
|---|---|---|
| `GoalSlot.semantic_type` | 只构造，不读取 | 删除 |
| `GoalEffect.required_state` | 只有字段定义 | 删除 |
| `GoalEffect.source_refs` | 不参与任何决策 | 删除 |
| `ToolEffect.reads` | 只用于构造 `result_bindings` | 删除 |
| `ToolEffect.writes` | 只用于推导其他字段，形成重复真值 | 删除 |
| `ToolEffect.optional_arguments` | 不参与 compiler 校验 | 删除 |
| `PublicEvidence.expected_policy` | v3 compiler 不读取 | 删除 |
| milestone metadata `support_count` | 只写不读 | 删除 |
| task metadata `generation_phase` | 只写不读 | 删除 |
| `GenerationReport.round_summaries` | 只序列化质量 tuple，无消费方 | 删除 |
| `selected_path_digest` | 只写 report/metadata，不参与运行期 | 删除 |

### 2.2 应整体删除的错误机制

#### Graph freeze/digest

删除：

- `stable_graph_digest()`；
- `MilestoneFrontierState.graph_digest`；
- `assert_milestone_graph_frozen()`；
- evaluator 每个 closed step 和 finish 前的 digest 重算；
- loader 中 graph digest 写入、加载校验和 refresh 前后校验；
- `generation_phase=pre_execution` metadata。

理由：graph 本来就是 session 输入，当前没有运行期修改 graph 的生产路径，也没有相关缺陷。该机制横跨 loader、frontier、evaluator，每步重复序列化 graph，不能改善生成质量。

#### Candidate path 支持度与质量向量

删除：

- `max_candidate_path_count`；
- `_distinct_complete_paths()`；
- `_operation_support()`；
- `support_count`；
- `_round_quality()` 的七元 tuple；
- `_select_round()`；
- `unique_complete_path_count`。

改为每轮只返回一条 path。首轮有效则使用首轮；首轮无效才 repair；repair 有效则使用 repair，否则拒绝。无需候选投票、支持度或质量排序。

#### Binding fatal gate

删除：

- `ArgumentBinding`；
- `_parse_binding()`；
- `_validate_bindings()`；
- `_source_values()`、`_collect_source_values()`、`_instruction_for_ref()`；
- `_contains_literal()`；
- `ToolEffect.result_bindings`；
- semantic metrics 中的 `argument_bindings`。

理由：milestone graph 表示必要阶段，不执行工具。当前人工 graph 也没有同构 binding 标注。把完整可执行参数数据流设为 graph 是否返回的前置条件，是 14 个 rejected case 的直接原因。

第一阶段 operation 只声明 evidence/tool capability。参数正确性继续由 ToolSandbox 实际执行和最终状态评分负责，不在 graph 生成阶段重复实现一套执行计划类型系统。

#### 错误 invariant 解析

删除 ToolSandbox 路径中的：

- `_INVARIANT_PATTERN`；
- `_tool_mentioned()`；
- `build_public_invariants()` 对 ToolSandbox system/user 文本的调用；
- `_compile_minefields()` 中 public invariant 分支。

ToolSandbox 第一阶段只从“必要 prerequisite 不可用”产生 fatal minefield。普通 `do not have more information` 不再经过禁止语义解析。

#### False-green input coverage

删除：

- `compare_input_coverage()`；
- case/summary 的 `input_coverage`；
- `complete_semantic_exact` 对该 coverage 的依赖。

当前函数只检查 capability subject 和 initial state 非空，无法发现 source/selector 不可表达，继续保留只会制造假绿。第一阶段直接报告 `contract_supported: bool`。

### 2.3 应回退的跨模块扩张

以下修改与本次 ToolSandbox graph 生成修复无关，应恢复为当前 `HEAD` 行为，不在本任务继续扩张：

- `dynsteer/adapter/agentcompass/contract.py` 为适配 v3 GoalContract 增加的关键词 required-effect 逻辑；
- `dynsteer/adapter/contract.py` 的通用 ToolEffect resolver 和工具 token invariant；
- `dynsteer/evaluate/evaluator.py` 的 freeze assertions；
- `dynsteer/evaluate/matching/frontier.py` 的 graph digest；
- `dynsteer/model.py` 的 frontier `graph_digest` 字段。

`harness/outputs.py`、`metrics.py` 等其他方案产生的 schema-version 修改不属于本修复范围，不碰触，避免覆盖用户的其他工作。

## 3. 必须保留的最小逻辑

保留：

- pre-execution 生成时点；
- first-user boundary initial setting；
- 预定义 future user turns；
- Agent 最终可见 tool schema/evidence；
- 一次 generation + 最多一次 repair；
- 必要 capability 覆盖校验；
- low-battery/network/cellular recovery；
- missing prerequisite 的 fatal minefield；
- operation 节点、依赖边、必要 response；
- reliability 的 operation、topology、minefield 指标；
- 原始 LLM 响应文件。

不保留旧共识 DAG、6-path diversity、binding provenance、graph freeze 或任意自然语言 invariant。

## 4. 精简后的数据模型

### 4.1 `dynsteer/milestone/model.py`

删除 `ArgumentBinding`、`GoalSlot`、`GoalEffect`。

将 `TurnGoalContract` 收缩为：

```python
@dataclass(frozen=True)
class TurnGoalContract:
    turn_id: str
    instruction: str
    disposition: TurnDisposition
    required_capabilities: tuple[str, ...] = ()
    dependency_edges: tuple[tuple[int, int], ...] = ()
    response_required: bool = False
    forbidden_capabilities: tuple[str, ...] = ()
```

说明：

- `required_capabilities` 是有序且保留重复项的 tuple，两次 contact update 表示为两个 occurrence；
- `dependency_edges` 直接引用 required capability 的位置索引；
- `forbidden_capabilities` 只用于 prerequisite 缺失时的 fatal minefield；
- 不再存在 slot/effect/result binding 三套中间真值。

`GoalContract` 只保留 `turns`，删除 `metadata` 和 digest。

将 `ToolEffect` 收缩为：

```python
@dataclass(frozen=True)
class ToolEffect:
    tool_name: str
    capability: str
    precondition_rule_ids: tuple[str, ...] = ()
```

`GenerationReport` 只保留：

```text
generation_status
selected_round
graph_returned
repair_triggered
violations
```

graph node/edge/minefield 数由 graph 自身和 reliability 计算，不在 report 重复存储。

`MilestoneGenerationConfig` 删除 `max_candidate_path_count`，保留：

- `use_origin_milestone`；
- `enable_repair`；
- `generator`。

### 4.2 `GeneratorTaskView`

保留当前通用输入字段，避免本修复再次重构所有 benchmark。ToolSandbox compiler 实际只消费：

- benchmark/task/case/language/instruction；
- public assets 中的 future turns；
- initial state；
- tool schema；
- environment rules；
- tool effects；
- goal contract；
- evidence catalog。

`environment_schema/output_contract` 暂留给 AgentCompass，不在 ToolSandbox 做递归 source 扫描或新增逻辑。

## 5. ToolSandbox 必要能力表

### 5.1 只修改现有文件

不新增 `sources.py`、`intent.py`、`validation.py`、`environment.py`。

只使用：

- `dynsteer/adapter/toolsandbox/utils/effects.py`：工具名→capability 和环境前置规则；
- `dynsteer/adapter/toolsandbox/utils/contract.py`：instruction→逐轮必要能力与依赖边；
- `dynsteer/milestone/compiler.py`：LLM evidence 选择、校验和 graph 编译；
- `dynsteer/milestone/semantics.py`：离线 reliability canonicalization。

### 5.2 `effects.py`

删除 `_subject()`、`_action()` 字符串猜测，改为一个显式字典。当前 25 case 至少包含：

```text
add_contact                         -> contact.create
search_contacts                     -> contact.search
modify_contact                      -> contact.update
remove_contact                      -> contact.delete
search_messages                     -> message.search
send_message_with_phone_number      -> message.send
add_reminder                        -> reminder.create
search_reminder                     -> reminder.search
modify_reminder                     -> reminder.update
get_current_timestamp               -> time.current
datetime_info_to_timestamp          -> time.convert
timestamp_to_datetime_info          -> time.decompose
timestamp_diff                      -> time.diff
search_holiday                      -> holiday.search
set_low_battery_mode_status         -> battery.set
set_location_service_status         -> location.set
set_cellular_service_status         -> cellular.set
set_wifi_status                     -> wifi.set
```

未登记的可见工具仍可放入 prompt，但 capability 为 `other`，不能满足 required capability。

环境规则用现有 JSON dict，补齐：

- location enable → battery false；
- cellular enable → battery false；
- Wi-Fi enable → battery false；
- message send → cellular true；
- holiday query → Wi-Fi true。

recovery 递归展开使用一个函数完成，不新增 EnvironmentRule 类。

### 5.3 `contract.py`

删除：

- `force_unresolved`；
- case ID 中 `insufficient_information` 判断；
- 当前 `_intended_actions()` 的宽泛 subject/action 组合；
- ToolSandbox public invariant 构造。

增加一个有限 `_TASK_RULES` tuple。每条规则只有：

```text
完整短语 regex
required capabilities
dependency edges
response required
terminal capability
```

覆盖当前 25 case 的任务族：

1. contact create；
2. relationship 批量 update；
3. message-recency contact update；
4. message send by contact；
5. reminder create with datetime；
6. latest reminder update；
7. oldest message search；
8. days-till holiday；
9. setting enable；
10. contact delete。

这些规则只读取每个 turn 的公开 instruction，不读取 case ID 或人工 graph。

### 5.4 多结果与多轮

批量 relationship update 的 update occurrence 数根据 first-user initial CONTACT 中符合公开条件的行数确定。两个 update 使用两个 capability occurrence，依赖边为：

```text
search -> update_0
search -> update_1
```

不引入 foreach/collection binding。

future user turns 继续使用当前 `_interaction_plan_from_steps()`，但每个解析出的 instruction 单独生成一个 TurnGoalContract。修正测试期望与实际 turn 数，不在 graph 执行后追加 turn。

### 5.5 不可执行判定

构造 required capability 后与可见 ToolEffect 比较：

- 全部可用 → executable；
- terminal tool 不可用 → response-only；
- terminal tool 可用但必要 prerequisite 不可用 → needs-clarification/no-action graph，并将 terminal capability 加入 forbidden；
- instruction 无已知规则 → adapter 抛 `MilestoneContractUnsupportedError`，不能默认 executable。

例子：

- holiday insufficient 缺 `time.current` → 空 graph，禁止 `time.diff`；
- message-recency contact update 缺 `message.search` → 空 graph，禁止 `contact.update`；
- remove-contact 缺 `contact.delete` → response-only。

## 6. 极简 LLM 协议

### 6.1 generation schema

每轮只返回 evidence ID：

```json
{
  "turns": [
    {
      "turn_id": "turn_0",
      "operations": [
        "tool_call_get_current_timestamp",
        "tool_call_search_holiday",
        "tool_call_timestamp_diff"
      ]
    }
  ]
}
```

删除：

- `paths` 数组；
- `disposition_reason`；
- `unresolved_slots`；
- operation `arguments`；
- argument binding kind/source/selector；
- path strategy/support/digest。

disposition 已由 contract 确定，LLM 无权重复决定。

### 6.2 prompt

prompt 只提供：

- 每轮 instruction；
- required capability occurrence 列表；
- 每个 evidence ID 对应的 tool name/capability/description；
- 要求只选择覆盖 required capabilities 的最小 evidence 列表；
- 不加入 status getter 或 capability=other 的工具。

不向 LLM 注入完整 initial state 和 environment rules；recovery 已由 adapter/compiler 确定，减少敏感输入和模型推理负担。

### 6.3 repair

首轮校验只可能产生：

- missing turn；
- unknown evidence；
- missing capability occurrence；
- duplicate/unexpected capability。

repair prompt 给出缺失 capability 和合法 evidence 对照。只修复一次，不计算 round quality。

## 7. compiler 精简

### 7.1 保留的数据结构

内部只保留：

```text
_TurnCandidate(turn_id, evidence_ids)
_PathCandidate(turns)
```

删除 `_OperationCandidate`、`_PathValidation`、`_RoundCandidate`；校验函数直接返回 violation tuple。

### 7.2 主流程

`compile_task_case()` 固定流程：

1. 检查 GoalContract 是否 supported；
2. 非 executable contract 确定性编译空图/response/minefield，不调用 LLM；
3. executable contract 调用一次 LLM；
4. 解析一条 path；
5. 按 capability occurrence 校验；
6. 首轮失败且 enable_repair 时调用一次 repair；
7. repair 仍失败则拒绝；
8. 按 GoalContract required order 生成节点；
9. 按 dependency_edges 生成边；
10. 追加必要 response 和 forbidden minefield；
11. 返回精简 report。

### 7.3 capability occurrence 校验

将 evidence ID 映射为 capability 后：

- required multiset 必须被完整覆盖；
- capability=other 或 required multiset 之外的 evidence 不进入 graph，并记录 non-fatal `ignored_extra_evidence`；
- 同一 required capability 出现多次时按 occurrence 依次匹配；
- 未覆盖 required occurrence 才触发 repair/reject。

这样 status getter/distraction 不再导致整条正确路径被拒绝，也不会进入最终 graph。

### 7.4 graph 编译

节点 constraint 只检查 tool name/capability。第一阶段不生成 argument binding 约束。

milestone ID 使用 `turn_id + required capability index + tool name` 的稳定 digest；不需要 selected path digest。

边直接来自 GoalContract dependency_edges；不调用 `transitive_reduction()`。规则定义时就必须给最小边集，测试负责 DAG 校验。

### 7.5 minefield

只遍历 `forbidden_capabilities`，为当前可见的对应工具创建 TOOL_CALL fatal constraint。没有 forbidden capability 就不生成 minefield。

## 8. reliability 精简

### 8.1 `semantics.py`

保留并修正 `canonical_graph_semantics()`，只输出：

- operation capability multiset；
- directed topology；
- normalized fatal minefields；
- user-visible response 作为独立 diagnostic。

删除：

- dispositions；
- argument bindings；
- generated-only effects；
- `stable_graph_digest()`；
- `compare_input_coverage()`；
- `_capability_covered()`。

operation 必须使用 list/Counter，不能用 set 合并两次 contact.update。response 只识别 recipient=USER。

### 8.2 `milestone_reliability.py`

第一阶段 primary metric 改为：

```text
operation_topology_exact =
    operation multiset exact
    and topology exact
    and fatal minefield exact
```

删除 summary 中：

- disposition/binding/effect macro F1；
- false-green input coverage；
- structural GED（本实验与 strict 完全重复）；
- `raw_response_records: []` 空占位；
- repair quality tuple summary。

保留：

- generation status counts；
- graph return ratio；
- operation/topology/minefield precision、recall、F1、exact；
- node/edge count delta；
- 一套 GED diagnostics；
- LLM call/token；
- empty graph confusion matrix。

response 只作 diagnostic，不进入 primary，直到能从公开 contract 稳定推导人工 response 标注。

## 9. Loader 与运行期清理

### 9.1 `dynsteer/adapter/loader.py`

删除 graph digest、generation phase 和 refresh freeze 检查。保留两个正确修复：

- `has_origin_graph = graph is not None`，允许合法空 graph；
- generated graph 在 enrich 后进入 TaskCase。

恢复 `save_task_case()` 对不应持久化 initial state 的原行为，避免为生成方案扩大缓存中的敏感状态范围。若 runtime 另有保存 initial state 的明确需求，应单独立项，不混入本修复。

### 9.2 `graph.py`、frontier、evaluator

删除本轮新增 `transitive_reduction()` 及 helper；compiler 直接使用规则给出的最小 DAG 边。

frontier/evaluator 恢复不含 digest 的原流程。frontier 只保存 topology、remaining predecessor count 和 ready IDs。

## 10. 文件级修改清单

| 文件 | 处理 |
|---|---|
| `dynsteer/milestone/model.py` | 删除 3 个模型和大量未消费字段，收缩 GoalContract/ToolEffect/Report |
| `dynsteer/milestone/compiler.py` | 删除 binding、候选、质量、支持度、source、fallback；改成单 path capability 校验 |
| `dynsteer/milestone/semantics.py` | 删除 freeze/coverage/不可比维度，只保留 graph reliability 语义 |
| `dynsteer/adapter/toolsandbox/utils/effects.py` | 用显式 tool capability 表替换字符串猜测，补真实 recovery |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 用有限任务规则生成 required capabilities/edges，删除 case ID 和 invariant |
| `dynsteer/adapter/contract.py` | 恢复通用 adapter 原职责，不承担 ToolSandbox ToolEffect/invariant 推理 |
| `dynsteer/adapter/agentcompass/contract.py` | 回退本轮为 v3 GoalContract 增加的关键词逻辑 |
| `dynsteer/adapter/loader.py` | 删除 digest/freeze/generation_phase，保留合法空图处理 |
| `dynsteer/evaluate/matching/frontier.py` | 删除 graph digest/assertion |
| `dynsteer/evaluate/evaluator.py` | 删除 freeze 调用 |
| `dynsteer/graph.py` | 删除当前只被 compiler 使用的 reduction 新增逻辑 |
| `dynsteer/model.py` | 删除 frontier.graph_digest |
| `dynsteer/prompt/templates/milestone/*.md` | 改成 evidence ID 单 path 协议 |
| `milestone_reliability.py` | 删除不可比指标和重复 GED，输出最小可靠漏斗 |
| `docs/apis/milestone.md` | 同步精简接口，删除 binding/source/digest 章节 |

不新增任何生产 `.py` 文件。

## 11. 代码量门槛

相对当前工作区设置以下硬门槛：

- `dynsteer/milestone/model.py`：216 行降至不超过 130 行；
- `dynsteer/milestone/compiler.py`：888 行降至不超过 560 行；
- `dynsteer/milestone/semantics.py`：198 行降至不超过 110 行；
- ToolSandbox `contract.py + effects.py` 合计不超过当前 390 行；
- 不新增生产文件；
- milestone 相关生产代码净减少不少于 250 行。

行数不是质量指标，但本次将其作为防止再次过度设计的停止条件。若必须超过，需先用失败测试证明新增逻辑不可省略。

## 12. 测试方案

### 12.1 只保留三组测试文件

不按每个抽象新增测试模块。使用：

- `tests/milestone/test_compiler.py`：单 path、repair、capability multiset、DAG、non-executable；
- `tests/adapter/test_toolsandbox_contract.py`：真实 25-case required capabilities/recovery/minefield；
- `tests/milestone/test_reliability.py`：multiset、topology、minefield、empty graph。

### 12.2 必须覆盖的失败回归

1. `do not have more information` 生成 0 个 public invariant；
2. location/cellular/Wi-Fi low-battery 都包含 battery recovery；
3. reminder create 包含 time.convert 和 reminder.create；
4. holiday query 包含 time.current、holiday.search、time.diff；
5. oldest message 包含 time.current、message.search；
6. latest reminder 包含 search/current-time/update 必要步骤；
7. send-message 包含 contact.search、cellular recovery、message.send；
8. relationship all 的两个 update 不被合并，边为 search→两次 update；
9. multi-turn 在执行前完整生成；
10. 两个 capability-insufficient case 为空图且 minefield 正确；
11. remove tool unavailable 为 response-only；
12. executable 多步 reference case 不得输出单节点图；
13. `complete_path_count=0` 不存在 completed 状态。

### 12.3 版本管理与覆盖率

不修改 `.gitignore`。测试完成后向用户明确说明 tests 当前被忽略，最终提交由用户决定是否 `git add -f`。

核心 compiler、ToolSandbox contract/effects 和 semantics 行覆盖率不低于 80%。测试结束清理本次生成的 cache，不删除 tests 源文件。

## 13. 实施顺序和停止条件

### 阶段 1：只删除

先删除 freeze/digest、unused fields、candidate/support、binding/source、ToolSandbox invariant 和跨 benchmark 扩张。完成后运行 compileall 和现有 tests。

停止条件：删除后出现与现有正常 runtime 无关的功能缺失，先恢复必要调用，不进入新增修复。

### 阶段 2：最小 ToolSandbox contract

实现显式 capability 表、10 个任务族规则和完整 recovery。使用真实 25 case 测试，不调用网络 LLM。

门槛：25 case contract supported；required capability、重复 occurrence、edges、disposition/minefield 全部符合公开任务语义。

### 阶段 3：极简 compiler/prompt

实现一条 path 的 evidence ID 协议和一次 repair。

门槛：stub LLM 下 25/25 返回合法 graph；所有多步 case 节点/边不退化；无 binding/schema rejection。

### 阶段 4：精简 reliability

修正 multiset/capability/topology/minefield 并删除不可比指标。

门槛：相同语义 fixture exact=true；重复 update 不被合并；空图和 fatal minefield confusion 正确。

### 阶段 5：真实 LLM

先运行 5 个 smoke case：reminder、location、holiday、relationship、insufficient。全部成功后再运行新的 25-case experiment id。

最终最低门槛：

- graph outcome 25/25；
- executable graph return≥95%；
- 多步 executable 单节点畸变=0；
- recovery recall=100%；
- spurious fatal minefield=0；
- fatal minefield miss=0；
- operation macro-F1≥0.90；
- topology edge recall≥0.80；
- operation_topology_exact≥70%；
- milestone 生产代码净减少≥250行。

任一安全或复杂度门槛失败即停止，不通过新增模型绕过。

## 14. 最终冗余检查

使用 `rg` 确认以下符号不存在生产代码：

```text
ArgumentBinding
GoalSlot
GoalEffect
stable_graph_digest
assert_milestone_graph_frozen
graph_digest
support_count
selected_path_digest
unique_complete_path_count
_operation_support
_round_quality
_select_round
_source_values
_collect_source_values
_parse_binding
_validate_bindings
_INVARIANT_PATTERN（ToolSandbox 路径）
force_unresolved
```

检查 import、只写 metadata、无生产调用 helper 和重复统计字段。核心流程中每份信息只有一个真值来源：

- task steps：GoalContract；
- tool capability：effects.py 显式表；
- graph edges：GoalContract dependency_edges；
- non-executable minefield：forbidden_capabilities；
- experiment comparison：semantics.py canonical output。

本方案不执行 git commit、push 或修改 `.gitignore`。

## 附录A. 项目中没有把握实现的模块部分

### A.1 任意新任务的 required capability 推导

最小方案只覆盖当前 ToolSandbox 10 个明确任务族。未来任意自然语言任务无法保证由有限规则正确解析。

未知任务必须返回 contract unsupported，而不是继续添加宽泛关键词。新增任务族时只允许增加一条规则和对应真实 case 测试；若规则规模明显增长，再单独评估是否需要新的语义解析器。

### A.2 参数级目标正确性

第一阶段主动删除 binding fatal gate，因此 graph 主要判断“调用了哪些必要能力、依赖关系是否正确”，不能单独证明 phone number、person ID 等动态参数完全正确。

参数正确性仍由 ToolSandbox 实际执行、state snapshot 和最终评分保证。只有在节点/拓扑可靠恢复后，并且出现参数错误无法被现有评分捕获的实证，才允许设计一个小型、非 fatal 的参数诊断；不得预先恢复完整 binding 类型系统。

### A.3 人工 response 标注不一致

部分 ToolSandbox 人工图要求 Agent→User response，部分相似写任务不要求。该选择未必能从公开 instruction 稳定推导。

第一阶段 response 不进入 primary exact。GoalContract 只对明确 query、明确 response-only 和已验证任务族添加 response；不得读取人工 graph 决定 runtime response。

### A.4 其他 benchmark 的自动 milestone 生成

本次证据只覆盖 ToolSandbox。AgentCompass/SkillsBench/SWE-bench Pro 没有相同的 25-case reliability 验收，不应为了统一接口继续增加猜测式 GoalContract。

本修复将相关跨 benchmark 扩张回退到 HEAD。其他 benchmark 若要迁移极简协议，应单独建立数据和测试后实施。
