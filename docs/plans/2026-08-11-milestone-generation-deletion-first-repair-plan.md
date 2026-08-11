# DynSTEER milestone graph 删除优先、多路径模拟聚合修复方案

## 1. 本次修订结论

上一版方案提出“删除候选路径模拟，由 LLM 直接生成唯一 DAG，compiler 只做薄校验”。该判断是方向性错误，本方案明确废止这一算法，不得据此修改生产代码。

唯一 DAG 只能表达一条被 LLM 选中的执行方案，不能回答以下核心问题：

- DAG 中的每个操作是否在其他可行方案中仍然必需；
- 是否存在不经过某个操作但仍能完成任务的替代路径；
- status getter、确认操作和辅助搜索究竟是必经步骤还是单一路径偶然选择；
- 多个互换顺序的独立操作之间是否真的存在 milestone 依赖边。

milestone graph 需要表达的是多条完整可行路径的共同约束，而不是任意一条执行计划。因此，修复后的语义边界为：

- LLM 负责基于公开任务输入模拟规划多条有差异的完整路径；
- compiler 对每条路径做结构校验和确定性环境状态模拟，不再用关键词 GoalContract 重新规划任务；
- compiler 聚合所有可模拟路径的必经操作多重集与共同先后关系；
- 第二轮 LLM 调用既修复结构/环境错误，也主动寻找绕过初步必经操作的反例路径；
- 对合法任务视图，任何 LLM 超时、非法 JSON、零路径或全部路径不可模拟的情况都返回合法空 graph，并在 report 中记录原因；
- reliability 使用人工 reference 评价相似性，但 reference graph 不进入 generator、adapter 公开视图或 compiler。

本次修复仍坚持删除优先：恢复的是“路径集合、状态模拟、必经操作聚合”三个必要能力，不恢复 GoalContract、复杂 binding、最短路径选择、质量 tuple 或第二套关键词 planner。

## 2. 当前崩溃的算法原因

`toolsandbox_milestone_reliability_partial_main` 当前 25 个 case 中只有 11 个 completed、14 个 generation rejected；当前 summary 还显示每个 case 平均只返回 1.04 条路径、平均只有 0.36 条路径通过完整性校验。现有 compiler 的实际流程是：

```text
LLM 生成路径集合
        ↓
GoalContract/ArgumentBinding 以不完整规则拒绝路径
        ↓
round quality 在两轮中选一轮
        ↓
_select_best_path() 选择操作数最少的一条路径
        ↓
_compile_selected_path() 把该路径线性编译为 graph
```

这里有四个相互叠加的问题：

1. `GoalContract` 用关键词推导 required capability、disposition 和 slot，建立了错误的第二套任务真值；
2. `ArgumentBinding` 依赖不完整的 source ref 与 result selector，导致 LLM 已给出的合理工具链被批量拒绝；
3. `_select_best_path()` 按操作数量选择最短路径，恰好会优先保留漏步路径；
4. `_operation_support()` 只写入 metadata，实际 graph 并没有按多路径支持度或交集聚合。

因此，问题不在“生成了多条路径”，而在“正确路径被错误校验淘汰，剩余路径又被最短路径选择压缩”。正确修复应删除错误筛选和单路径选择，同时让路径集合真正参与 graph 的节点与边生成。

## 3. 修复目标、保证边界与非目标

### 3.1 必须实现的目标

1. 每次规划允许返回 1～`max_candidate_path_count` 条路径，并按规范化操作序列去重；
2. compiler 使用 initial state 和 ToolSandbox 明确环境规则逐条模拟路径，递归插入必要 recovery；
3. graph operation 节点来自所有可模拟路径的操作多重集交集，不再来自某一条最短路径；
4. graph edge 来自所有可模拟路径共同成立的先后关系，再执行传递约简；
5. 第二轮主动生成替代路径，挑战第一轮得到的候选必经操作；
6. 合法 task view 下 graph 返回率为 100%，空图是正式输出，不再用伪 response 单节点掩盖失败；
7. 当前 25-case 实验必须按全部 case 计入分母，并达到本方案第 13 节的质量门槛。

### 3.2 能够严格保证的内容

- parser、LLM 调用或路径模拟失败不会使合法 task view 变成 `generation_rejected`；
- 返回值始终是 schema 合法的 `MilestoneGraph`，可能没有 nodes、edges 和 minefields；
- 只有全部可模拟路径共同包含的具体工具操作才会成为 operation milestone；
- 只有全部可模拟路径共同成立的先后关系才会成为拓扑边；
- reference、verifier、trajectory 和 final state 不进入生成输入。

### 3.3 不能伪称能够严格保证的内容

有限次 LLM 采样不能数学证明已经枚举全部可能路径。若要证明某操作在真实工具系统中绝对不可绕过，需要完整、正确的工具状态转移模型和穷举搜索；当前 ToolSandbox 公开 schema 不具备这种完备性。

本方案通过“多样路径采样 + 确定性环境模拟 + 候选必经操作反例搜索”提高必经操作可信度，并以人工 reference 的 operation、topology、minefield 指标作为最终经验性证据。不得在代码或文档中把 sampled-path intersection 描述成形式化完备证明。

### 3.4 本次非目标

- 不执行真实工具，不产生任务副作用；
- 不恢复参数级 `ArgumentBinding` 和 result selector 验证；
- 不构建 intent resolver、SourceCatalog、ResultShape、solvability 或任务族规则库；
- 不把 capability 模糊相似度用于合并不同具体工具；
- 不解决 ToolSandbox 以外 benchmark 的自动 milestone 质量问题；
- 不修改 `.gitignore`，不执行 git commit 或 push。

## 4. 修复后的完整算法

### 4.1 公开输入

adapter 只向 generator 提供：

```text
benchmark / task_id / case_id / language
turns: [{turn_id, instruction, source_ref}]
public_assets
initial_state
tool_schema
environment_rules
evidence_catalog
```

`turns` 直接来自当前用户 instruction 和执行前已经公开的 future-user-turn instruction。它只负责稳定 turn ID 和顺序，不包含 required capability、slot、effect 或 disposition 推断。

`evidence_catalog` 继续保存 evidence ID 到真实工具名、constraint selector 和 matching route 的映射。operation 身份以具体 `evidence_id`/tool name 为准，不使用模糊 capability 代替。

### 4.2 LLM 路径 schema

generation 和 refinement 统一使用一个最小 schema：

```json
{
  "paths": [
    {
      "turns": [
        {
          "turn_id": "turn_0",
          "disposition": "executable",
          "operations": [
            {
              "evidence_id": "tool_call_search_holiday_675a1156fc",
              "arguments": {
                "holiday_name": "Christmas Day"
              }
            },
            {
              "evidence_id": "tool_call_get_current_timestamp_a44535fe63",
              "arguments": {}
            },
            {
              "evidence_id": "tool_call_timestamp_diff_2f50a247d0",
              "arguments": {}
            }
          ],
          "forbidden_evidence_ids": []
        }
      ]
    }
  ]
}
```

只保留：path、turn、disposition、按执行顺序排列的 operation、普通 JSON arguments 和 forbidden evidence。

明确删除：

- `GoalContract`、slot、effect；
- `disposition_reason`；
- `unresolved_slots`；
- `ArgumentBinding.kind/source_refs/operation_index/selector`；
- strategy、quality、support、digest；
- 由 LLM 直接输出 milestone、edge 或 minefield 对象。

`arguments` 只用于 prompt 上下文和确定性 recovery 状态更新，不进入 operation 必经性身份，也不因普通业务参数无法静态证明来源而拒绝路径。

### 4.3 第一轮多样化路径生成

generation prompt 必须要求：

- 每条路径覆盖完全相同且顺序一致的全部 turns；
- 每条 executable 路径包含完成原始 instruction 所需的完整工具链；
- 路径之间应尝试不同工具、不同信息来源和不同独立操作顺序；
- initial state 已经公开某状态时，不得为了确认状态而强制加入 status getter；
- 环境阻塞时可显式加入 recovery，也允许 compiler 根据真实规则补入；
- 无需工具、信息不足或没有任何共同具体操作时允许 operations 为空；
- 不得为了制造多样性而删除完成任务必需的 search/get/conversion/write 操作；
- 不得创造 evidence ID 或调用未公开工具。

不同路径按以下规范化键去重：

```text
[(turn_id, disposition, [evidence_id...], sorted(forbidden_evidence_ids))...]
```

arguments 和自由文本不参与去重，避免仅参数书写差异被误计为路径多样性。

### 4.4 宽容的逐路径解析

`_parse_response()` 不再因一条坏路径丢弃整个 round：

- 顶层不是 JSON object 或没有 `paths` list：该轮得到零路径并记录 violation；
- `paths` 超过配置上限：只读取前 `max_candidate_path_count` 条并记录截断；
- 单条 path/turn/operation 字段错误：只淘汰该 path；
- 未识别的附加字段忽略并记录，不把其带入后续 compiler；
- unknown evidence、turn 缺失/乱序、non-executable 含 operation：该 path 不可模拟；
- `paths: []` 是可处理输入，不抛生成异常。

宽容解析不意味着接受未知工具。任何 operation 仍必须命中当前 view 的 TOOL_CALL evidence 白名单。

### 4.5 确定性路径状态模拟

新增的 `_simulate_path()` 是离线符号模拟，不调用真实工具。对每条结构合法路径：

1. 深拷贝 `initial_state` 为当前模拟状态；
2. 按 turn 顺序和 operation 顺序遍历；
3. 查找当前 tool 是否命中 `environment_rules[*].applies_to_tools`；
4. 若规则的 `state_path` 当前等于 `blocked_value`，递归模拟 `recovery_tool`；
5. recovery tool 也受其他规则阻塞时继续递归，例如低电量先阻塞 Wi-Fi enable；
6. recovery evidence 不可见、规则字段非法或形成 recovery cycle 时，将该 path 标记为不可模拟，不删除或改写其他路径；
7. recovery 成功后把规则对应 state path 更新为 `recovered_value`，再执行目标 operation；
8. LLM 已显式给出同一 recovery tool 且 arguments 与规则的 `recovery_arguments` 一致时复用，不重复插入；
9. status getter 不改变状态，compiler 永远不会自动插入 status getter；
10. 对没有明确环境规则的工具按 opaque operation 处理，不凭工具名猜测读写 namespace。

ToolSandbox 当前必须显式覆盖：

| 被阻塞操作 | 阻塞状态 | recovery |
|---|---|---|
| `set_location_service_status(on=true)` | low battery=true | `set_low_battery_mode_status(on=false)` |
| `set_cellular_service_status(on=true)` | low battery=true | `set_low_battery_mode_status(on=false)` |
| `set_wifi_status(on=true)` | low battery=true | `set_low_battery_mode_status(on=false)` |
| message send tools | cellular=false | `set_cellular_service_status(on=true)` |
| network/holiday search tools | wifi=false | `set_wifi_status(on=true)` |

每条环境规则统一使用以下 JSON 形状，compiler 不再识别其他隐式格式：

```json
{
  "low_battery_blocks_wifi_enable": {
    "applies_to_tools": ["set_wifi_status"],
    "applies_when_arguments": {"on": true},
    "state_path": "SETTING.low_battery_mode",
    "blocked_value": true,
    "recovery_tool": "set_low_battery_mode_status",
    "recovery_arguments": {"on": false},
    "recovered_value": false
  }
}
```

`applies_when_arguments` 为空时规则适用于列出的全部调用；非空时必须与 operation arguments 中对应键值一致。由此区分“开启 Wi-Fi 受低电量阻塞”和“关闭 Wi-Fi”之类方向相反的调用。`recovered_value` 必须显式给出，不由 compiler 对 `blocked_value` 做布尔取反猜测。

规则必须使用确切 tool name 列表，不再通过 `_subject()`、`_action()` 或字符串包含关系推导。当前状态值和工具名称以 ToolSandbox 实际 schema 为准，落地前通过 fixture 核对，不允许直接照抄本表而不核实名称。

模拟输出仍是 `_PathCandidate`，但 operations 已包含确定性 recovery。不可模拟原因进入 report，不转成伪 milestone 或伪 minefield。

### 4.6 第二轮修复与反例路径搜索

保留最多一次 refinement LLM 调用，但改变触发条件和职责。满足任一条件时触发：

- 第一轮 JSON/结构错误或存在不可模拟路径；
- 第一轮没有可模拟路径；
- 规范化后只有一条不同路径；
- 第一轮聚合得到至少一个候选必经 operation，需要寻找绕过它的反例。

refinement prompt 输入：

- 原始公开 task view；
- 第一轮原始响应；
- 每条坏路径的确定性 violation；
- 第一轮可模拟路径的规范化序列；
- 初步共同 operation 列表。

refinement prompt 是对第一轮 ensemble 的审查与重写，要求一次完成三件事：

1. 重新核查每条路径能否完整实现原始 instruction，漏步路径不得进入最终 ensemble；
2. 修复结构错误、unknown evidence 和环境上不可执行的路径；
3. 针对每个初步共同 operation，尝试规划一条不调用它但仍完整完成原任务的路径；确实不能绕过时保留该 operation，不得通过提交漏步路径伪造反例。

第二轮输出仍使用第 4.2 节相同 schema，并且必须返回“审查后的完整最终路径集合”，而不是只返回新增反例。若第二轮至少有一条路径通过结构校验和状态模拟，聚合只使用第二轮审查后的 ensemble；若第二轮完全不可用，才回退到第一轮可模拟 ensemble。这个确定性优先级不比较 quality、不选择单条路径，也不按路径长度排序。

本阶段不新增第三次 LLM verifier 调用。若 25-case 实验证明第二轮持续提交漏步反例并造成 false-empty，必须先记录具体失败类型，再单独评估是否增加批量 path critic；不得在没有失败证据时预建第三套 schema 和 verifier 类。

### 4.7 必经 operation 多重集聚合

按 turn 独立计算具体 evidence 的最小出现次数。设可模拟路径集合为 `P`：

```text
mandatory_count(turn, evidence_id)
    = min(count(path, turn, evidence_id) for path in P)
```

例一：

```text
path_1: search_contact → get_status → update_contact
path_2: search_contact → update_contact
```

聚合结果只保留 `search_contact` 和 `update_contact`，`get_status` 被判定为非必经。

例二：

```text
path_1: update_contact → update_contact
path_2: update_contact → update_contact
```

聚合结果保留两个 update 节点。不得使用 set 丢失重复 operation。

每个必经 occurrence 的稳定身份为：

```text
(turn_id, evidence_id, occurrence_index)
```

不按 arguments、description 或 LLM reason 合并；不同 evidence ID 即使 capability 文字相似，也不创建虚构的抽象 capability milestone。若多条完整路径使用完全不同的替代工具，具体 operation 交集可以为空，这属于合法空 graph。

### 4.8 共同拓扑聚合

对每条规范化路径，把同一 `(turn_id, evidence_id)` 的第 N 次出现对齐为相同 occurrence。对任意两个必经 occurrence `A`、`B`：

```text
A → B 成立
当且仅当 A 在每一条可模拟路径中都先于 B
```

先构造共同先后关系，再调用现有 `transitive_reduction()` 删除可推导的间接边。

例如：

```text
path_1: search_holiday → get_current_timestamp → timestamp_diff
path_2: get_current_timestamp → search_holiday → timestamp_diff
```

聚合拓扑为：

```text
search_holiday ─┐
                ├→ timestamp_diff
get_timestamp ──┘
```

不会凭第一条路径错误添加 `search_holiday → get_current_timestamp`。

不同预定义 turn 仍具有确定的交互顺序。完成 turn N 的必经 sink 与 turn N+1 的必经 root 连接；若任一 turn 没有必经 operation，则不生成伪节点来维持连接。

### 4.9 Graph 编译

`_compile_selected_path()` 替换为 `_compile_aggregated_paths()`：

- 一个必经 occurrence 编译为一个 operation milestone；
- tool name、selector、operator 和 matching route 直接来自 evidence；
- milestone ID 由 `(turn_id, evidence_id, occurrence_index)` 稳定生成；
- metadata 只保留 `turn_id`、`evidence_id`、`occurrence_index` 和 `necessity_basis=path_intersection`；
- 不写 `support_count`、selected path digest、argument binding、GoalEffect；
- graph edges 使用第 4.8 节共同拓扑；
- 本阶段不自动创建 Agent→User response milestone，避免再次出现 response 单节点畸变；
- response 只在 reliability 中作为 diagnostic，不进入 primary operation/topology 指标。

### 4.10 Minefield 聚合

删除 token-based public invariant 和 unresolved slot 推导。minefield 只来自可模拟路径对同一 turn 的 `forbidden_evidence_ids` 交集：

```text
mandatory_forbidden(turn)
    = intersection(path.forbidden_evidence_ids for path in P)
```

约束：

- forbidden evidence 必须是可见 TOOL_CALL evidence；
- executable turn 的 `forbidden_evidence_ids` 必须为空；
- 非 executable 路径如果对禁止工具存在分歧，只保留所有路径一致禁止的工具；
- severity 固定 `fatal`、penalty 固定 1.0；
- recovery 不可完成只使对应路径不可模拟，不自动推断为 minefield；
- `You do not have more information` 等普通否定句不再由正则生成 minefield。

### 4.11 始终返回 graph 与空图语义

只要 adapter 已构造合法 `GeneratorTaskView`，`compile_task_case()` 对所有 LLM 结果都返回 `MilestoneGraph`：

| 情况 | graph | report `empty_reason` |
|---|---|---|
| 有可模拟路径且存在共同 operation | 正常 graph | `null` |
| 可模拟路径全部无需工具 | 空 graph | `no_operation_required` |
| 可模拟路径存在但无共同具体 operation | 空 graph | `no_common_operation` |
| 两轮均无可模拟路径 | 空 graph | `no_simulatable_path` |
| LLM 调用失败或响应无法解析 | 空 graph | `planner_failed` / `invalid_response` |

仅 view 自身违反程序不变量，例如 evidence ID 重复、tool evidence 缺少真实工具名，才允许抛出配置/适配错误。这类错误不属于“LLM 生成失败”，不能用空图掩盖。

不得再调用 `_deterministic_non_executable_path()` 生成 response fallback。删除只为 generation rejected 服务的 `MilestoneGenerationError`；view 自身非法时直接抛带明确信息的 `ValueError`。

## 5. 删除、保留与替换清单

### 5.1 继续删除

从 `dynsteer/milestone/model.py` 删除：

- `ArgumentBinding`、`BindingKind`；
- `GoalSlot`、`GoalEffect`、`TurnGoalContract`、`GoalContract`；
- `ToolEffect` 的 reads/writes/result bindings 等整套复杂语义；本方案直接删除 `ToolEffect`，环境模拟只读 `environment_rules`；
- `PublicInvariant` 和 `GeneratorTaskView.invariant_catalog`；
- `MilestoneGenerationError`；
- `GeneratorTaskView.goal_contract/tool_effects/output_contract`；
- report 中 selected path/round quality、binding/effect coverage 和重复 graph 计数。

从 `dynsteer/milestone/compiler.py` 删除：

- `_parse_binding()`；
- `_validate_bindings()`；
- `_validate_task_completeness()`；
- `_precheck_goal_contract()`；
- GoalContract 分支的 `_precondition_blocked()`；
- `_round_quality()`、`_select_round()`；
- `_select_best_path()`；
- `_deterministic_non_executable_path()`；
- `_operation_support()`；
- `_source_values()`、`_collect_source_values()`、`_instruction_for_ref()`、`_contains_literal()`；
- selected path digest、support 和 quality metadata；
- `_compile_selected_path()` 的线性单路径实现。

跨模块继续删除：

- `stable_graph_digest()`、frontier graph digest 和 freeze assertion；
- ToolSandbox token-based invariant；
- false-green `compare_input_coverage()`；
- disposition/binding/effect exact 与不可比的 `complete_semantic_exact` 合取；
- AgentCompass 为 GoalContract 增加的关键词任务推断。

### 5.2 必须保留

- `MilestoneGenerationConfig.max_candidate_path_count`，默认 6、范围 1～8；
- `MilestoneGenerationConfig.enable_repair`，表示是否允许第二轮修复/反例搜索，不新增第三个配置开关；
- `_PathCandidate` 和每轮多 path 解析；
- 多路径规范化去重和 path-level diagnostics；
- initial state、future turns、真实工具 evidence 和环境规则；
- 一次 generation + 最多一次 refinement；
- raw LLM response 分轮记录；
- `transitive_reduction()`，用于共同先后关系约简；
- `BaseBenchmarkAdapter.reference_milestone_graph()`，只供 reliability 使用；
- loader 对 `graph is not None` 的判断，确保合法空 graph 不被当成缺图。

### 5.3 必须替换

| 旧逻辑 | 新逻辑 |
|---|---|
| GoalContract 完整性校验 | LLM 多路径规划 + 25-case reliability 观测 |
| binding/source 递归校验 | 普通 JSON arguments；只校验 evidence 白名单 |
| path quality tuple | 每条路径独立结构校验和状态模拟 |
| 用 quality tuple 选择一轮 | 第二轮作为完整审查 ensemble；第二轮不可用才回退第一轮 |
| 选择最短 path | operation 多重集交集 |
| 单路径线性边 | 全路径共同先后关系 + 传递约简 |
| repair 只修错误 | repair + 必经 operation 反例搜索 |
| 失败抛 generation rejected | 返回带 `empty_reason` 的合法空 graph |
| response 单节点 fallback | 真正的零节点 graph |

## 6. 精简后的数据模型

### 6.1 `MilestoneGenerationConfig`

保留现有三个字段：

```text
use_origin_milestone
max_candidate_path_count
enable_repair
generator
```

不新增 candidate minimum、counterexample round、critic 或 confidence 配置。第二轮触发条件由 compiler 固定实现，避免配置组合膨胀。

### 6.2 `GeneratorTaskView`

调整为：

```text
benchmark / task_id / case_id / language
instruction
turns: tuple[JsonObject, ...]
public_assets
initial_state
tool_schema
environment_schema
environment_rules
evidence_catalog
```

`turns` 使用普通 JSON object，不新增 TurnContract dataclass。每项只允许 `turn_id/instruction/source_ref`。

### 6.3 compiler 内部类型

只保留三个内部 dataclass：

```text
_OperationCandidate(evidence_id, arguments)
_TurnCandidate(turn_id, disposition, operations, forbidden_evidence_ids)
_PathCandidate(turns)
```

删除 `_PathValidation` 和 `_RoundCandidate`。路径 violation 使用仅供 report 消费的普通 JSON object，避免再建立质量对象层级。

### 6.4 `GenerationReport`

只保留会被日志或 reliability 实际读取的字段：

```text
returned_path_count
simulatable_path_count
unique_path_count
repair_triggered
empty_reason: string | null
path_summaries
reasons
```

`GenerationReport` 只在 graph 正常返回时产生，因此不再保存恒为 `generated/true` 的 `generation_status` 和 `graph_returned`。如果 adapter/view 本身错误，直接抛适配异常，不构造误导性的 generation report。

删除 selected round/path digest、binding valid count、effect coverage、round quality、support count、generation phase 和可从 graph 推导的节点/边/minefield数量。

## 7. 具体文件修改内容

| 文件 | 具体修改 |
|---|---|
| `dynsteer/milestone/model.py` | 按第 5、6 节删除 GoalContract、ArgumentBinding、ToolEffect、PublicInvariant；保留 path 数配置；增加最小 `turns` 字段；收缩 GenerationReport |
| `dynsteer/milestone/compiler.py` | 保留多路径主流程；重写 parser 为逐路径容错；新增 `_simulate_path()`、recovery 递归、reviewed ensemble 选择、`_aggregate_operations()`、`_aggregate_edges()`；删除质量排序、最短路径和 binding/GoalContract 校验；任何 LLM 失败返回空 graph |
| `dynsteer/adapter/toolsandbox/utils/contract.py` | 删除 `_goal_contract()`、`_intended_actions()`、ToolEffect/PublicInvariant 构造；直接投影当前/future turn、first-user initial state、tool evidence 和显式环境规则 |
| `dynsteer/adapter/toolsandbox/utils/effects.py` | 删除 `_action()`、`_subject()`、`_preconditions()` 和模糊 capability 推断；改为确切 ToolSandbox tool-name 环境规则常量及一个返回 JSON 的查询函数 |
| `dynsteer/adapter/contract.py` | `normalize_tool_contract()` 只返回规范化 tool schema 和 evidence；删除默认 ToolEffect resolver、`build_public_invariants()` 与 `_tool_mentioned()` |
| `dynsteer/adapter/agentcompass/contract.py` | 删除 GoalContract 适配扩张；只构造新的最小 task view，不加入 ToolSandbox 环境规则或关键词目标推断 |
| `dynsteer/milestone/__init__.py` | 删除已移除类型和 `MilestoneGenerationError` 的显式导出，只导出仍存在的 config/view/report/evidence 接口 |
| `dynsteer/adapter/loader.py` | 删除 digest/freeze/generation_phase；保留合法空 graph 的 `graph is not None` 语义；普通空 graph 不走 generation rejected 分支 |
| `dynsteer/evaluate/evaluator.py` | 删除运行期 graph freeze 调用，不改变其他评价流程 |
| `dynsteer/evaluate/matching/frontier.py` | 删除 graph digest 字段与断言，不改变 frontier 匹配语义 |
| `dynsteer/model.py` | 删除 `MilestoneFrontierState.graph_digest` |
| `dynsteer/graph.py` | 保留现有 `transitive_reduction()`；不再执行上一版方案中“删除传递约简”的修改 |
| `dynsteer/milestone/semantics.py` | 删除 digest/input coverage/不可比较的 binding/effect；operation 使用具体 tool name 多重集；topology 使用 operation occurrence；minefield 使用 fatal tool identity |
| `dynsteer/prompt/templates/milestone/generation.zh.md`、`.en.md` | 改为第 4.2、4.3 节多样化路径 schema 和约束 |
| `dynsteer/prompt/templates/milestone/refinement.zh.md`、`.en.md` | 改为同时修复坏路径和搜索候选必经 operation 反例；输出与 generation 完全相同 schema |
| `dynsteer/harness/config.py` | 保留现有 `max_candidate_path_count/enable_repair` 读取，不新增配置字段；删除仅为旧类型服务的解析（如存在） |
| `dynsteer/experiment/runner.py` | 保留 generator 配置摘要；将 repair 文案更新为 refinement/反例搜索语义，不新增重复统计 |
| `milestone_reliability.py` | 全部 25 case 进入 operation/topology/minefield 分母；删除 false-green exact；增加 empty graph confusion、单节点畸变和 generation return 统计；读取新的 report 字段 |
| `docs/apis/milestone.md` | 更新 task view、路径 schema、始终返回 graph、空图原因、多路径聚合与 report；删除 GoalContract/binding/freeze 文档 |
| `tests/milestone/support.py` | 更新最小 view 与多路径 response fixture，不再构造 GoalContract/ToolEffect/binding |
| `tests/milestone/test_compiler.py` | 改写为多路径模拟、反例、交集、拓扑、recovery 和空图测试 |
| `tests/adapter/test_toolsandbox_contract.py` | 验证 turns、initial state、evidence 和确切 recovery rules，不再验证 GoalContract |
| `tests/milestone/test_reliability.py` | 验证 operation multiset、occurrence topology、fatal minefield 和空图统计 |

不新增生产 `.py` 文件。共享的路径模拟和聚合只在 `dynsteer/milestone/compiler.py` 实现一次，不在 adapter、reliability 或测试中复制。

## 8. compiler 主流程伪代码

```python
def compile_task_case(view, config, llm, response_output_file=None):
    evidence = validate_view_and_build_evidence(view)

    draft_paths, draft_violations = generate_and_parse(...)
    draft_valid = simulate_and_deduplicate(draft_paths, ...)
    preliminary = aggregate_operations(draft_valid)

    refined_valid = []
    repair_triggered = needs_refinement(
        draft_violations,
        draft_valid,
        preliminary,
        config,
    )
    if repair_triggered:
        refined_paths, refined_violations = generate_refinement(
            violations=draft_violations,
            common_operations=preliminary,
        )
        refined_valid = simulate_and_deduplicate(refined_paths, ...)

    valid_paths = refined_valid if repair_triggered and refined_valid else draft_valid
    nodes = aggregate_operations(valid_paths)
    edges = aggregate_common_precedence(valid_paths, nodes)
    minefields = aggregate_forbidden_evidence(valid_paths)

    graph = MilestoneGraph(
        nodes=compile_nodes(nodes, evidence),
        edges=transitive_reduction(node_ids, edges),
        minefields=compile_minefields(minefields, evidence),
        metadata={"source": "generated", "view_digest": view.digest()},
    )
    return graph, build_report(...)
```

主流程中不得出现：

- `_select_best_path()` 或任何 shortest/min operation 选择；
- round quality 比较；
- reference graph 读取；
- benchmark case name 分支；
- task-family required capability 推导；
- 为避免空图而添加 response/fallback 节点。

## 9. Reliability 定义

### 9.1 Primary

第一阶段 primary 定义为：

```text
operation_topology_exact =
    operation tool-name multiset exact
    and mandatory occurrence topology exact
    and fatal minefield tool multiset exact
```

operation、edge 和 minefield 分别提供 precision/recall/F1，不能只报告 exact。重复 operation 使用 `Counter` 和 occurrence identity，不能用 set 合并。

response 暂不进入 primary；disposition 只做 diagnostic。人工 graph 中 response 标注不一致，不得让该维度结构性地把所有 case 判为失败。

### 9.2 端到端分母

- 所有选择的 case 都进入分母；
- 返回空 graph 的 case 仍参与 operation/topology/empty confusion；
- adapter/view 错误单独标记 infrastructure failure；
- 不再以“只有 completed case”计算看似较高的平均值；
- 保留 raw LLM response，能够区分 planner 漏步、路径模拟拒绝和聚合删减。

### 9.3 必须新增的诊断

- reference/generation 是否为空的 confusion matrix；
- reference 多节点但 generation 单节点的畸变计数；
- 每个 evidence occurrence 在各可模拟路径中的 count；
- 初步必经 operation 被第二轮反例移除的列表；
- 路径在 parser、environment simulation 哪一步被淘汰；
- recovery 插入前后的规范化路径序列。

这些诊断写入 case reliability JSON 或现有 report 消费字段，不复制到 milestone node metadata。

## 10. 分阶段实施顺序

### 阶段一：删除错误语义与单路径选择

1. 删除 GoalContract、ArgumentBinding、ToolEffect 和 PublicInvariant；
2. adapter 改为最小 turns/state/tools/evidence/rules 投影；
3. 删除 binding/completeness/quality/shortest-path/fallback；
4. 暂时保留现有多路径 parser fixture，使代码可加载；
5. 运行 compileall 和不依赖新算法的 adapter/model tests。

门禁：代码中不再存在第二套关键词任务真值，origin milestone 和 default harness 不受影响。

### 阶段二：实现路径模拟和聚合 fixture

1. 切换到第 4.2 节 schema；
2. 实现逐路径容错 parser；
3. 实现显式环境规则和递归 recovery；
4. 实现 operation 多重集交集；
5. 实现 occurrence 对齐、共同先后关系和传递约简；
6. 实现 forbidden evidence 交集；
7. 实现所有 LLM 失败返回空 graph。

门禁：第 11.1 节 deterministic tests 全部通过，compiler 核心覆盖率不低于 80%。

### 阶段三：实现 refinement 反例搜索

1. generation prompt 强制路径多样性；
2. preliminary aggregation 后提取候选必经 evidence；
3. refinement prompt 同时接收 violations 和 avoidance targets，并返回完整审查 ensemble；
4. 第二轮可用时使用其完整 ensemble，完全不可用时回退第一轮；不计算 quality、不选择最短路径；
5. raw output 文件继续保留两轮原始响应。

门禁：fixture 能证明第二轮合法反例会移除非必经 operation，非法反例不会因结构/环境错误进入聚合。

### 阶段四：真实实验门禁

先运行 5-case smoke，再运行完整 25-case。实验不达第 13 节门槛时停止，不继续添加抽象层；必须从 raw LLM path、simulation violation 和 aggregation diagnostics 定位属于 prompt、环境规则还是聚合算法的问题。

## 11. 单元测试设计

### 11.1 `tests/milestone/test_compiler.py`

必须覆盖：

1. 两条相同完整路径生成共同 operation；
2. 一条含 status getter、一条不含时 getter 被交集删除；
3. 两条路径独立操作顺序相反时不产生二者之间的边；
4. 两条路径的共同 sink 生成两条直接依赖边；
5. 同一 evidence 重复两次时按最小 occurrence count 保留；
6. 第一轮候选必经 operation 被第二轮合法替代路径移除；
7. 一个坏 path 不影响同轮其他好 path；
8. unknown evidence path 被淘汰；
9. non-executable path 含 operation 被淘汰；
10. low-battery → Wi-Fi enable → holiday search 的递归 recovery；
11. LLM 已给出正确 recovery 时不重复插入；
12. recovery tool 不可见或 recovery cycle 时只淘汰对应 path；
13. invalid JSON、`paths: []`、两轮均失败都返回合法空 graph；
14. 多条完整路径无共同具体 operation 时返回合法空 graph；
15. 非 executable 路径 forbidden evidence 交集生成 fatal minefield；
16. 空图不产生 response fallback；
17. multi-turn 的稳定 occurrence ID 与跨 turn 边；
18. raw LLM response 保留 generation/refinement 两轮。

### 11.2 `tests/adapter/test_toolsandbox_contract.py`

必须覆盖：

- first-user initial state 对 generator 可见；
- future turn 顺序和 instruction 完整；
- reference/evaluation/verifier/final state 不进入 view；
- tool evidence ID 唯一且可回查确切 tool name；
- low battery 对 location、cellular、Wi-Fi 三类 enable 的阻塞规则；
- cellular 与 Wi-Fi recovery 规则；
- `You do not have more information` 不生成 invariant/minefield；
- distraction/scrambled tool schema 不触发字符串 task-family 推断。

### 11.3 `tests/milestone/test_reliability.py`

必须覆盖：

- operation tool-name multiset 保留重复项；
- occurrence topology 的 precision/recall/F1；
- empty reference/empty generated 四象限；
- fatal minefield tool identity；
- response/disposition 不进入 primary；
- 空 graph 和 no graph/adapter failure 被区分；
- 所有 case 都进入总体分母。

测试结束清理本轮生成的 `__pycache__` 和 `.pytest_cache`，不得删除 tests 源文件。当前 tests 被 `.gitignore` 忽略，本方案不修改 `.gitignore`。

## 12. 真实 case 核查顺序

### 12.1 5-case smoke

按以下顺序检查 raw paths、模拟后 paths、聚合 graph 和 reference：

1. `find_days_till_holiday`：验证 search/current timestamp/diff 都能成为共同步骤；
2. `find_days_till_holiday_wifi_off_alt`：验证 low-battery（如存在）→Wi-Fi recovery→search 的递归模拟；
3. `turn_on_location_low_battery_mode`：验证 status getter 被替代路径剔除，必要 recovery 和 location set 保留；
4. `send_message_with_contact_content_cellular_off`：验证 contact search、cellular recovery、send 的共同操作与先后关系；
5. `remove_contact_by_phone_no_remove_contact_insufficient_information`：验证合法空 graph 和 fatal minefield，不产生伪 response 节点。

### 12.2 25-case 分类核查

每个失败必须归入一个且仅一个主要类别：

```text
planner_missing_required_operation
planner_spurious_operation
parser_path_loss
environment_rule_missing_or_wrong
recovery_normalization_wrong
aggregation_false_required
aggregation_false_optional
topology_alignment_wrong
minefield_false_positive_or_negative
reference_response_inconsistency
```

不得用新增 task family 分支直接修复单个 case。只有多个 case 证明同一公开工具环境规则缺失时，才修改 `effects.py` 的确切 tool-name 规则。

## 13. 验收门槛

### 13.1 硬门槛

- 合法 task view 的 graph return：25/25；
- `generation_rejected`：0；
- schema 合法空 graph 可以被 loader、runner、reliability 正常处理；
- reference 多节点 case 的“生成单 response/fallback 节点”畸变：0；
- spurious fatal minefield：0；
- 明确环境规则 recovery recall：100%；
- compiler、ToolSandbox contract/effects、milestone semantics 行覆盖率均不低于 80%；
- reference/evaluation/verifier/final state 泄漏测试全部通过。

### 13.2 相似性门槛

以下指标必须以全部 25 case 为分母：

- operation macro-F1 ≥ 0.85；
- operation macro-precision ≥ 0.85；
- operation macro-recall ≥ 0.85；
- topology edge macro-F1 ≥ 0.75；
- fatal minefield macro-F1 ≥ 0.80；
- reference empty case 的 false-nonempty 数=0；
- reference nonempty case 的 false-empty 数≤2；
- reference 节点数≥2 的 case 中，generated operation 节点数=1 的数量≤1。

完整 25-case 至少独立运行 3 次。三次均须满足 graph return 和 spurious minefield 硬门槛；相似性指标以三次中位数验收，任一次 operation macro-F1 不得低于 0.80。若模型配置固定为确定性输出，可用相同输入摘要验证结果确实稳定后只运行一次。

### 13.3 未达门槛时的处理

- planner 多次漏同一步骤：先调整 generation/refinement prompt；
- recovery 错误：只修正确切环境规则；
- status getter 等额外步骤进入 graph：检查路径多样性和反例搜索，不加禁止 getter 的 task-family 代码；
- 必经步骤被反例错误删除：检查第二轮是否提交漏步路径；记录失败后再决定是否需要批量 path critic；
- 聚合/occurrence 错位：只修复通用 multiset/precedence 算法；
- 指标未提升：停止实施，不以增加更多字段、规则或 fallback 掩盖失败。

## 14. 代码复杂度与删除门槛

相对当前工作区：

- 不新增生产 `.py` module；
- `dynsteer/milestone/model.py` 从约 216 行降至 ≤140 行；
- `dynsteer/milestone/compiler.py` 从约 888 行降至 ≤650 行；
- `dynsteer/milestone/semantics.py` 从约 198 行降至 ≤110 行；
- ToolSandbox `contract.py + effects.py` 从约 390 行降至 ≤280 行；
- milestone 相关生产代码净删除不少于 250 行；
- compiler 内部 dataclass 不超过 3 个；
- 不新增配置字段；
- 不新增只调用另一个函数的薄 wrapper；
- 不在多个模块重复实现 evidence 规范化、路径 identity 或 operation occurrence 对齐。

如果落地后超过任一门槛，必须逐项列出超额代码的唯一消费者和不可删除理由；没有生产读取方的字段、metadata、helper 和 import 一律删除。

## 15. 最终冗余检查

生产代码中应不存在：

```text
ArgumentBinding
BindingKind
GoalSlot
GoalEffect
TurnGoalContract
GoalContract
ToolEffect
PublicInvariant
MilestoneGenerationError
stable_graph_digest
assert_milestone_graph_frozen
graph_digest
support_count
selected_path_digest
unique_complete_path_count
_PathValidation
_RoundCandidate
_round_quality
_select_round
_select_best_path
_operation_support
_source_values
_collect_source_values
_parse_binding
_validate_bindings
_validate_task_completeness
_precheck_goal_contract
_deterministic_non_executable_path
force_unresolved
```

生产代码中必须仍存在并有测试覆盖：

```text
max_candidate_path_count
_PathCandidate
_simulate_path
多路径规范化去重
operation multiset intersection
common precedence aggregation
transitive_reduction
一次 refinement/counterexample search
raw LLM response recording
valid empty graph handling
```

最后使用 `rg` 检查已删除符号、无消费者字段、未使用 import 和只写 metadata；运行 compileall、目标 pytest、覆盖率与 25-case 验收后再清理缓存。本方案不执行 git commit、push 或修改 `.gitignore`。

## 附录A. 项目中没有把握实现的模块部分

### A.1 有限路径集合无法形式化证明绝对必经性

本方案最没有把握的部分，是仅凭有限次 LLM 采样证明某操作在全部现实可行路径中必经。反例搜索能显著降低“单一路径偶然步骤”进入 graph 的概率，但不能替代完备状态空间搜索。

因此实现中必须把它称为“可模拟路径集合中的共同 operation”，并通过多次 25-case 实验验证与人工标注的相似性，不得宣称逻辑完备。

### A.2 LLM 可能提交漏步反例

第二轮被要求绕过候选 operation 时，可能通过漏掉真正必需步骤伪造替代路径。确定性环境模拟只能识别结构、白名单和已知环境前置条件，无法完全判断自然语言任务是否已经完成。

本方案暂不增加第三次 LLM critic，以免再次形成复杂且未经验证的第二套 planner。若 raw outputs 明确证明这是 false-empty 的主要来源，再以批量验证全部候选路径的最小方案单独立项。

### A.3 ToolSandbox 环境状态模型并不完备

目前有把握确定的是 low-battery、location、cellular、Wi-Fi、message send 和 network search 的已知恢复关系。其他工具的隐含前置条件若没有公开 schema 或真实实验依据，不能由工具名猜测。

遇到新失败时只能增加有 ToolSandbox 行为证据支持的确切规则，不得恢复 `_subject()`/`_action()` 之类字符串语义推断。

### A.4 不同具体工具的语义等价

两条路径可能使用不同工具实现同一抽象能力。将二者合并为 capability milestone 会提高 recall，也可能创建人工 reference 中不存在的虚构节点。本方案选择保守的具体 evidence 交集，因此这类任务可能生成空 graph。

只有人工 reference 和多个真实 case 证明需要抽象等价时，才能设计显式、可审计的 tool equivalence table；本次不预建。

### A.5 参数级正确性

删除 ArgumentBinding 后，milestone graph 不独立证明动态 person ID、phone number、timestamp 或 reminder ID 的来源正确。arguments 只辅助 LLM 规划和 recovery 模拟，不进入 primary operation identity。

ToolSandbox 的真实执行与最终 state scorer 仍负责参数结果正确性。若最终评分捕捉不到参数错误，需要另行补充 scorer，而不是重新把不完整 result selector 强塞进 milestone compiler。

### A.6 Minefield recall 与 response 标注

自由文本中的信息不足是否必须禁止某个终端工具，仍依赖 LLM 对路径 disposition 和 forbidden evidence 的理解；去除正则只能保证降低 false positive，不能保证一次达到 100% recall。

人工 graph 对 Agent→User response 节点的选择也不一致，所以本阶段不自动生成 response milestone，并将 response 留在 diagnostic。需要先统一 reference 标注规范，才能重新纳入 primary。

### A.7 其他 benchmark

本方案只以 ToolSandbox 当前 25-case 为修复和验收范围。AgentCompass、SkillsBench、SWE-bench Pro 是否适合相同的路径模拟和交集算法尚无对应人工样本证据；本次只保证它们能构造最小 task view，不承诺自动 milestone 相似性。
