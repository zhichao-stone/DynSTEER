# ToolSandbox milestone 语义优化回归根因复核报告

## 1. 修正后的结论

按照 `2026-08-11-milestone-semantic-reliability-comprehensive-optimization-plan.md` 落地的当前实现，已经发生 **milestone graph 生成链路的系统性功能回归**。这不是“指标偏低”或“仍有若干可优化点”，而是核心生成协议、任务契约、校验器、图编译和评测语义同时失配，造成两个直接后果：

1. 正确或基本正确的多步 LLM 路径在 compiler 中不可表示或被主动拒绝，产出率降为 11/25；
2. 能通过校验的 executable 路径全部被压缩为单操作图，最终 11 个 completed graph 全为单节点、零边。

当前实现不满足该方案自己规定的实验准入和最终验收门槛，不应继续作为优化后的有效版本评价，更不应据此调整模型。应先停止扩大实验，保存失败现场，然后修复或回退生成契约。

## 2. 核查证据与边界

本次复核直接检查了：

- 指定优化方案及其硬性验收条件；
- 当前未提交工作区相对 `HEAD` 的代码变更；
- 25 个 case、25 份结果和 25 份两轮 LLM 原始输出；
- ToolSandbox 上游真实工具前置条件与人工 milestone；
- 当前 adapter 生成的 `GeneratorTaskView`、`GoalContract`、source catalog、ToolEffect、invariant；
- 当前 compiler 对两轮原始响应复算得到的最终违规码；
- 当前 milestone/adapter/frontier 目标测试。

没有重新请求 LLM，也没有用人工 milestone 参与生成。人工 milestone 仅用于 reliability 对比和判断当前 contract 是否覆盖 benchmark 要求。

仓库中没有保存一份完整、同 case、同模型、同配置的优化前 reliability summary。旧 v1 目录只有 2 个中途失败 case，因此无法从现存工件严格计算“优化前→优化后”的数值跌幅。但当前版本内部的生成前后漏斗、方案验收冲突和代码路径已经足以证明本次系统性回归。

## 3. 方案验收条件与实际结果直接冲突

| 方案硬门槛 | 实际结果 | 判断 |
|---|---:|---|
| graph object return 25/25 | 11/25 | 失败 |
| input coverage uncovered=0 | 0 | 表面通过，但 coverage 实现遗漏参数可达性，属于假阳性 |
| fatal minefield miss=0 | 4 | 失败 |
| location/cellular/Wi-Fi recovery 完整 | location 4 个全 rejected；cellular 漏 battery；Wi-Fi case rejected | 失败 |
| relationship-twice 完整预生成 | graph rejected | 失败 |
| distraction/scramble 变体 disposition/required capability 一致 | location 变体一致地全部失败 | 形式一致、功能失败 |
| complete semantic exact 为主指标 | 0/11，且该指标结构性不可达 | 指标设计失败 |

方案要求在完成测试和 25-case 验收后才认为落地结束。实际实验全面违反硬门槛，说明实现没有经过有效的阶段验收便进入了正式实验。

## 4. 崩溃发生在哪里

### 4.1 LLM 之后发生了数量级压缩

25 个 case 的首轮第一候选路径共包含 59 个 operation；每个 case 取最终一轮第一候选路径后仍有 52 个 operation。compiler 最终只产生：

- 6 个 operation milestone；
- 5 个 response/fallback milestone；
- 合计 11 个节点、0 条边。

人工图共有 64 个节点、43 条边；仅看 completed case，人工图也有 19 个节点、12 条边。

因此“LLM 没生成多步方案”不是主要解释。location、send-message、Wi-Fi、reminder、relationship 等原始响应都包含多步工具链；这些链在 binding/来源/completeness 校验中被拒绝。最终成功的 6 个 executable graph 恰好都是单操作图，其余 5 个 completed graph 是澄清/拒绝响应或 deterministic fallback。

### 4.2 14 个 rejected case 的最终违规分布

按 case 统计第二轮最终响应：

| 最终违规 | 涉及 case 数 |
|---|---:|
| `literal_not_in_source` | 7 |
| `unrelated_operation` | 6 |
| `unknown_source_ref` | 3 |
| binding 含未知字段，解析失败 | 2 |
| `case_literal` 缺少 `source_refs`，解析失败 | 1 |
| `missing_required_argument` | 1 |

一个 case 可同时包含多个违规。这里最重要的不是 LLM 是否写错字段，而是当前合法协议无法表达多个正确 ToolSandbox 工具链，导致 repair 只能在“任务完整”与“通过 validator”之间二选一。

## 5. 根因一：正确答案在 binding 协议中不可表达

这是产出率断崖式下降的首要原因。

### 5.1 初始状态可见，但不可引用

`GeneratorTaskView.initial_state` 被完整放入 prompt，LLM 能看到联系人、phone number、reminder ID 等值；然而 `_source_values()` 只登记带 `source_ref` 字段的对象。当前 initial-state namespace/row 没有稳定 source ref。

实际 source catalog 只有：

- `instruction`；
- message/simulator content；
- 3 个 environment rule；
- tool name。

不存在 `initial_state`、`CONTACT`、`REMINDER` 或具体 row/field source。于是：

- relationship case 从初始联系人表读取 person ID 后使用 `CONTACT`，被判 `unknown_source_ref`；
- reminder case 使用 `initial_state`，被判 `unknown_source_ref`；
- send-message 使用初始状态中的 phone number，却只能错误标为 instruction literal。

方案把 initial state 定义为合法生成输入，却没有设计可校验的寻址协议。实现因此出现“模型可看见、但不能合法引用”的自相矛盾。

### 5.2 Tool result selector 白名单严重缺失

ToolEffect 对所有 read 工具统一只提供：

```text
id -> $.id
person_id -> $.person_id
```

以下必要数据流均无法表示：

- `datetime_info_to_timestamp` → `add_reminder.reminder_timestamp`；
- `get_current_timestamp/search_holiday` → `timestamp_diff`；
- `search_reminder` → `modify_reminder.reminder_id`；
- `search_contacts` → `send_message.phone_number`；
- 多结果 `search_contacts` → 多次 `modify_contact.person_id`；
- recency search、list item selection 和 map-over-results。

这意味着 reminder、holiday、recency、send-message、relationship-update 的正确路径即使由 LLM 完整生成，也不能通过 `operation_result` selector 校验。

### 5.3 Prompt 没有说明完整 binding 协议

generation prompt 只展示 `case_literal`；没有展示 `operation_result`、`operation_index`、selector 或合法 source catalog。refinement prompt 更只给一个 `arguments:{}` 外壳。

原始输出出现 `case_binding`、`case_reference`、`source_evidence_id` 等模型自创字段，是这个提示缺口的直接表现。它们当然是模型协议错误，但当前 prompt 没有给模型完成正确修复所需的信息。

## 6. 根因二：GoalContract 不是任务真值，而是脆弱的关键词分类器

方案把 GoalContract 定义为 compiler 的“任务真值边界”，但实现仅用 case-insensitive substring 匹配 subject/action：

- `friend/enemy/person` 推断 contact；
- `remind` 推断 reminder；
- `Christmas/how far` 推断 holiday；
- `add/find/update/turn on` 推断 action。

这一实现不能推导工具依赖闭包，只能命中用户文本中显式出现的核心能力。典型漏项：

- holiday days-till 漏 current timestamp 和 timestamp diff；
- oldest message 漏 current timestamp；
- latest reminder 漏 current timestamp、search 和 timestamp conversion；
- relationship update 漏 contact search；
- send-message 漏 contact search；
- cellular low-battery 漏 battery recovery。

当前 25 个 case 中，有 16 个 reference graph 包含 Agent→User response，但 GoalContract 只有 2 个 case 的 `response_required=True`。实现使用 `order > 0 or not unique_effect` 判定响应，导致几乎所有正常 executable 首轮任务都丢失最终响应节点。

GoalContract 一旦欠建模，后续 validator 会把正确的辅助步骤视为无关，最短路径选择又会主动选择更短、更残缺的路径。这个架构会系统性放大任何 contract 漏项。

## 7. 根因三：validator 和最短路径策略把欠建模转化成图畸变

`_validate_task_completeness()` 只要求覆盖 precheck 中的 capability，并把不属于 required capability、又没有被合法 operation-result 引用的操作判为 `unrelated_operation`。

由于 result selector 和 response/output dataflow 不完整：

- timestamp diff 即使是最终回答所必需，也没有后继 operation 引用，因而被判 unrelated；
- status getter、search、conversion 等人工 milestone 被判 distraction；
- repair 接到错误后会删掉真正必要的步骤。

随后 `_select_best_path()` 直接按 operation 总数最小化。这个规则只有在 GoalContract 已证明完备时才安全；在当前欠建模状态下，它等价于“从候选中选择最残缺但能骗过 validator 的路径”。

`modify_reminder_with_recency_latest` 的第二轮原始输出已经清楚展示这一失败：模型知道删除时间计算后任务无法完成，但为了避开 `unrelated_operation/literal_not_in_source`，最终省略 `reminder_timestamp`。这不是正常的模型规划退化，而是 repair 目标函数把模型推向错误答案。

## 8. 根因四：环境依赖模型与 ToolSandbox 实际规则不一致

ToolSandbox 上游 `setting.py` 明确规定：low-battery mode 为 true 时，location、cellular、Wi‑Fi 都不能开启。

当前 environment rules 只有：

- location → battery recovery；
- message → cellular recovery；
- holiday/network → Wi‑Fi recovery。

它缺少：

- cellular enable → battery recovery；
- Wi‑Fi enable → battery recovery；
- recovery operation 自身的递归前置条件展开。

因此 `turn_on_cellular_low_battery_mode` 被错误判定为只需 cellular.set，并生成一个单节点图；`find_days_till_holiday_wifi_off_alt` 即使需要开启 Wi‑Fi，也没有检查开启 Wi‑Fi 是否先受 low-battery 阻止。

这里不只是实现遗漏。优化方案列出的“必须覆盖的已知规则”本身就没有完整枚举 ToolSandbox 对 cellular/Wi‑Fi 的 low-battery 约束，也没有要求依赖图递归闭包。这是方案阶段的事实核查不足。

## 9. 根因五：invariant 解析把“信息不足”反转成“禁止执行任务”

`build_public_invariants()` 只要一行包含 `must not/do not/never`，就将其视为禁止规则；工具匹配又只需 tool name 或 capability 的任一 token 出现在整行中。

ToolSandbox user-simulator 文本普遍包含：

> You do not have more information.

这句话表示“没有更多信息”，并不表示“禁止执行”。当前代码却把其中的 `do not` 当作否定指令，再根据同一行的 `contact/message/holiday/reminder` 等词匹配工具。

结果：

- 25 个 case 中有 16 个“人工无 minefield、生成 view 却有 invariant”的普通 case；
- add-contact 会把 add/modify/remove/search contact 全部编译成 fatal minefield；
- holiday/message 等正常目标工具也被标成 fatal。

这是明确的语义反转 bug，直接污染了生成图和 minefield 指标。它不是 LLM 问题。

## 10. 根因六：insufficient-information 由 case ID 强制判定

当前实现使用：

```text
"insufficient_information" in task_case.case_id
```

一旦命中，就把所有匹配写工具的 required arguments 全部设为 unresolved，而不是解析实际缺失的 slot、检查初始状态是否可解析，或判断唯一性。

这会造成：

- disposition 由文件命名而非公开语义决定；
- search/read 和 write capability 混入同一 GoalContract；
- 某些 case 的真正 minefield 工具不匹配；
- deterministic precheck 对 benchmark 命名产生隐式依赖。

这与方案声称的“只从合法执行前输入生成任务真值”不一致，也无法泛化到没有这种命名约定的新 case。

## 11. 根因七：评测主指标结构性不可达

当前 `complete_semantic_exact` 合取 disposition、operation、argument binding、effect、response、minefield 和 topology 的 exact。

但在 11 个 completed case 中：

- reference disposition count 全为 0；
- generated disposition count 全为 1。

因此 disposition exact 对每个 generated graph 都必然为 false，`complete_semantic_exact` 在当前 reference schema 下不可能为 true。

其他不可比项包括：

- reference 节点没有 `turn_id`，generated 节点有 `turn_0`，节点/拓扑 canonical label 无法精确相等；
- reference 没有生成侧 ArgumentBinding 表示；
- add-contact 的 reference SET_STATE 被推导为 `contact.update`，generated tool 被映射为 `contact.create`；
- canonicalizer 把任何 `matching_route` 都记为 response，把 Agent→Environment 工具调用也混入 response；
- generated minefield 固定带 `protection=pre_execution`，reference minefield 常无该字段，造成 fatal exact miss。

`input_coverage.uncovered_count=0` 也是假绿：它只检查 capability subject 是否存在和 initial_state 是否非空，不检查 initial-state source ref、参数值可达性、result selector、response 或完整工具链。因此正确答案明明不可表达，coverage 仍报告 0。

方案在把 `complete_semantic_exact` 定为主指标前，没有先证明人工图和生成图能映射到同一 canonical schema。这是方案设计层面的评测偏差。

## 12. 根因八：fallback 和报告结构掩盖真实失败

3 个 `completed` case 的 `complete_path_count=0`：

- `modify_contact_with_message_recency_insufficient_information`；
- `modify_contact_with_message_recency_insufficient_information_10_distraction_tools`；
- `remove_contact_by_phone_no_remove_contact_insufficient_information`。

它们由 `_deterministic_non_executable_path()` 在所有 LLM path 无效后直接生成，却仍记作 completed。这一 fallback 不在方案的生成成功定义中，导致表面完成率从真实 LLM-path 产出率 8/25（32%）抬高到 11/25（44%）。

此外，rejected report 只保留 draft validation，不保留最终 repair violation；case JSON 的 `raw_response_records` 为空。若不是重新加载 `llm_outputs` 并复算 compiler，无法从正式结果得知最终为什么失败。当前可观测性不满足方案要求，也延迟了问题定位。

## 13. 测试为何全部通过却没有拦住回归

目标测试执行结果为 21 passed，但它们没有覆盖真实失败链路：

- compiler fixture 的 happy path 是单 turn、单 update tool、instruction literal，恰好绕开所有真实 dataflow；
- recovery 测试只断言 capability/rule 存在，不编译并核对完整 graph；
- relationship-twice 只检查 GoalContract turn 数，不检查最终 graph；
- insufficient-information 只检查 protected capability，不与人工 minefield canonical 语义比较；
- 没有测试 initial-state source ref；
- 没有测试 timestamp/reminder_id/phone_number selector；
- 没有测试 `You do not have more information` 不应生成 invariant；
- 没有测试普通 executable case 的最终 Agent→User response；
- 没有断言 current 25-case 实验门槛。

更严重的是，`.gitignore` 当前使用 `tests/*` 忽略整个测试目录。这些测试不是版本控制中的可靠验收资产，无法随实现提交并在其他环境稳定复现。

方案列出了大量正确的测试名称，但实现只覆盖了结构存在性，没有覆盖行为结果。这是典型的“测试对着实现写、没有对着 benchmark 失败模式写”。

## 14. 方案本身的问题与实现偏差应分别承担什么责任

### 14.1 方案设计阶段的问题

1. 把 GoalContract 设为唯一真值，却没有给出能覆盖 25 个 case 的可实现构造算法；附录承认通用解析无把握，但主方案仍依赖其完备性。
2. ToolSandbox environment rule 核查不完整，漏掉 cellular/Wi‑Fi 的 low-battery 前置条件和递归恢复。
3. “恢复由 compiler 推导”与“LLM path 必须包含恢复 operation”之间职责不清，实际实现把关键恢复重新交给 LLM。
4. 没有先建立人工/reference 与 generated 的同构语义，再把 complete exact 定为主指标。
5. 采用最短完整路径策略，却没有要求先证明 GoalContract closure 完备。
6. input coverage 的设计只覆盖 capability 存在性，没有定义参数/dataflow/response 可表达性证明。
7. 验收清单遗漏了会暴露关键 bug 的 negative assertions，例如普通任务不得生成 fatal invariant。

### 14.2 实现偏离或缩减

1. GoalContract 被实现为关键词集合，而非结构化字段优先的任务闭包。
2. initial state 没有 source refs。
3. ToolEffect result bindings 被缩减为通用 `id/person_id`。
4. response contract 被简化为 `order > 0 or no effect`。
5. invariant 被简化为单行否定正则和 token 命中。
6. insufficient-information 直接读取 case ID。
7. 新增 deterministic fallback，却没有独立状态。
8. 25-case 行为回归未落实为端到端测试。

因此责任不能只归给“实现没有完全按方案写”。方案的若干中心假设本身也未经数据验证；实现又把这些高风险部分进一步简化，最终共同造成崩溃。

## 15. 对上一份核查报告的自我审计

上一份报告有一些事实判断是正确的：它识别了 11/25、单节点零边、binding/source 问题、response/minefield 错配和 primary exact 不可比。但它存在严重不足：

1. **定性过轻**：把结果描述为“尚不能可靠使用”，没有明确判定为优化后的系统性功能回归。
2. **没有对照方案验收门槛**：遗漏了实际结果与 25/25、fatal miss=0、恢复/多轮门槛的直接冲突。
3. **没有证明正确答案空间为空**：只说 prompt 和 binding 要改，没有发现 initial state 不可引用及 selector 白名单使多个 case 无合法正确输出。
4. **对 LLM 归因偏高**：虽然区分了模型和代码，但没有量化 52 个最终 operation 意图被压缩为 6 个 operation 节点，因而低估了 compiler 的主导责任。
5. **没有识别 invariant 语义反转**：遗漏了 `do not have more information` 被解析成 fatal 禁止项这一明确 bug。
6. **没有检查 ToolSandbox 源码**：遗漏了 cellular/Wi‑Fi 同样受 low-battery 约束，因而没有指出方案本身的规则清单错误。
7. **没有检查测试有效性**：没有发现 21 个 passing tests 对真实 case 几乎没有端到端约束，且 tests 目录被忽略。
8. **没有提出立即止损**：给了 P0/P1 改进建议，但没有明确建议暂停合并和扩大实验。

这说明上一轮核查停留在结果归纳，没有完成因果审计。该偏差需要由本报告明确纠正。

## 16. 恢复建议

### 16.1 立即止损

1. 将当前实验标记为 regression failed，不把 11/25 或任何 v3 primary 指标用于模型选择；
2. 暂停合并当前未提交的 milestone 语义优化实现；
3. 保存当前 diff、实验结果和 llm_outputs 作为失败复现工件；
4. 不在现有代码上继续零散放宽 validator，否则会把“拒绝正确路径”变成“接受错误路径”。

### 16.2 先恢复可表达性，再讨论生成质量

1. 为 initial state 的 namespace、row、field 建立稳定 source ref 和 selector；
2. 从真实工具返回 schema/适配器显式 registry 建立每个工具的 result bindings；
3. 支持 scalar、list item、argmax/argmin、filter、map-over-results 等本批 case 必需的数据流；
4. generation/refinement prompt 直接注入机器可读 JSON Schema、legal source catalog 和 legal selector catalog；
5. 在调用 LLM 前运行 contract-solvability 检查：每个 required argument 必须存在至少一条合法来源链。

### 16.3 重建 GoalContract 和依赖闭包

1. 删除基于 case ID 的 `force_unresolved`；
2. 删除以关键词集合充当任务真值的做法；
3. 用结构化 tool/effect/dependency registry 加受约束的语义解析器，显式输出 unknown/ambiguous；
4. 递归展开目标操作的参数生产者、环境前置条件、恢复操作和用户可见 response；
5. 只有 closure 完备后才允许最短路径 tie-break，不能用长度代替正确性。

### 16.4 修复环境与 invariant

1. 以 ToolSandbox 实际工具逻辑建立 low-battery→location/cellular/Wi‑Fi 完整依赖图；
2. 支持恢复操作自身的递归前置条件；
3. 暂时关闭自然语言 invariant 自动编译，直到有独立否定作用域和 benchmark fixtures；
4. `do not have more information` 必须有明确 negative test，绝不能产生禁止工具 minefield；
5. minefield canonical schema 必须让人工和生成两侧表达同一 trigger。

### 16.5 先校准评测，再恢复 primary exact

1. reference 缺失 disposition 时，不把 generated disposition 当 false positive；或为 reference 确定性补同构 disposition；
2. canonical node/topology 排除单侧专有 `turn_id`，或为 reference 映射 turn；
3. response 只识别 recipient=USER 的 emit-message；
4. capability 统一通过同一 registry 映射；
5. argument binding 比较 resolved provenance/value，而不是一侧为空一侧为 binding JSON；
6. input coverage 扩展为 source、selector、dataflow、precondition、response 全链可达性。

### 16.6 建立真正能阻止回归的门禁

至少增加并版本控制以下端到端断言：

- 4 个 location low-battery 变体均生成 battery→location→response；
- cellular low-battery 生成 battery→cellular→response；
- holiday Wi-Fi case 生成完整递归 recovery 和计算链；
- reminder date/time 生成 conversion→reminder，并有合法 timestamp binding；
- send-message 生成 contact search/data binding→cellular recovery→send→response；
- relationship-all/twice 支持多结果和多轮完整图；
- insufficient-information 空图、response、minefield 与 reference 语义同构；
- 普通 `do not have more information` 文本生成 0 个 invariant；
- executable multi-step reference case 不得输出单节点 graph；
- fallback 必须单独状态，不能计为 LLM-generated completed。

测试目录必须进入版本控制。任何一个门禁失败都不得运行或发布“优化后正式结果”。

## 17. 最终责任判断

本次崩溃的直接责任排序如下：

1. **代码侧合法输出空间不完备**：initial-state source 和 tool-result binding 缺失；
2. **GoalContract 欠建模与 response 缺失**：把任务闭包降为关键词命中；
3. **validator/最短路径放大错误**：拒绝正确辅助操作并偏好残缺图；
4. **invariant 语义反转**：普通“没有更多信息”被编译成 fatal 禁止项；
5. **环境规则核查不完整**：漏 low-battery 对 cellular/Wi‑Fi 的限制；
6. **评测不可比**：primary exact 结构性为 0，coverage 假绿；
7. **验收和测试失效**：方案硬门槛未执行，passing tests 未覆盖真实行为。

LLM 确实存在 schema adherence、source hallucination 和 repair 推理问题，但它不是本次 graph 畸变的首要原因。当前 compiler 给模型的正确答案空间本身不完整，并把多步响应压缩或拒绝。继续换模型不会修复这个问题。
