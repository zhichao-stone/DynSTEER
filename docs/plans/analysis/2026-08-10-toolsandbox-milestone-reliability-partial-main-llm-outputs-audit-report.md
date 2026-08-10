# `toolsandbox_milestone_reliability_partial_main` Milestone 生成与 LLM 原始响应核查报告

## 1. 结论摘要

本报告核查 `results/milestone/toolsandbox_milestone_reliability_partial_main` 中 2026-08-10 03:14:06Z—03:26:34Z 的最新批次，共 25 个 ToolSandbox case、150 次独立路径生成，并逐条复核 `toolsandbox/llm_outputs/*.json` 中的原始响应。

核心结论如下：

1. **按正式实验产物，DynSTEER 与人工 milestone 的可交付覆盖差距为 100%。** 25/25 个 case 均为 `generation_rejected`，0 张 prediction graph 进入 GED/F1；人工 reference 覆盖 25 个 case，合计 64 个节点、43 条边、217 条约束。当前 strict/structural GED 和 node F1 的样本数都是 0，不能把 `null` 误读为相似度 0。
2. **首层失败已由 `llm_outputs` 明确证实为输出协议集成问题。** 150/150 条原始回复都以 ` ```json ` 开头并以 ` ``` ` 结束，`json.loads(raw)` 因首字符为反引号而在 line 1 / column 1 失败。精确移除单层围栏后，150/150 都能通过当前 `_parse_path_response()` 的完整 schema、evidence ID、索引、terminal 和 invariant 校验。因此，“LLM 返回内容不是 JSON 对象”并不准确；实际是“模型返回了围栏包裹的合法 JSON，而代码只接受裸 JSON”。
3. **围栏不是唯一问题。** 在只移除围栏、保持其余编译逻辑不变的反事实重放中，只有 2/25 个 case 可生成图：20 个因有效去重路径少于 4 被拒绝，3 个因去重后没有达到三分之二 terminal 共识被拒绝。也就是说，修复 JSON 边界后，现有算法预计仍有 **92% 的 case 被拒绝**。
4. **固定“至少 4 条不同路径”的门槛与本批任务明显不匹配。** 10/25 个 case 的六次回复只有 1 条 evidence 序列，另有 7 个只有 2 条；这往往是简单线性任务上的稳定一致，而不是生成失败。当前实现把“一致”当作“缺少多样性”淘汰，丢失了重复采样本应提供的置信度。
5. **LLM 的主要语义差距集中在不可执行任务、阶段粒度、多轮任务和证据角色。** 四个人工空图 case 在原始回复中全部被构造成非空工具路径；55/392 个 atom 直接把用户指令当 milestone，16/392 个把公开 invariant 当正向 milestone；常规任务又常遗漏人工图中的最终用户反馈、环境前置状态或第二轮操作。
6. **生成 evidence 与人工标注处于不同表示空间。** 人工节点均使用 `state_snapshot/custom`，单节点可聚合多条状态、guardrail 和消息约束；生成节点只能从公开 evidence 中选一个 `step/tool_call/tool_result` 约束，而且工具 evidence 只检查 `$.name`，不检查参数、结果或最终状态。即使拓扑接近，strict descriptor 也几乎不可能完全一致。
7. **改进顺序应为：输出协议 → 共识门槛 → 空图/拒绝建模 → evidence 粒度与多轮结构 → 评价协议。** 仅放宽 JSON parser、仅改 prompt 或仅调 GED 代价，都不足以解决当前结果。

## 2. 核查范围与方法

### 2.1 核查材料

- 实验索引与汇总：`results/milestone/toolsandbox_milestone_reliability_partial_main/index.json`、`summary.json`
- 25 个逐 case 结果：`results/milestone/toolsandbox_milestone_reliability_partial_main/toolsandbox/*.json`
- 150 条 LLM 原始回复：`results/milestone/toolsandbox_milestone_reliability_partial_main/toolsandbox/llm_outputs/*.json`
- 实验配置：`data/experiments/toolsandbox_milestone_reliability_partial_main.json`
- 人工 reference：ToolSandbox scenario 经 `milestone_graph_from_scenario()` 适配得到的原生图
- 当前编译器：`dynsteer/milestone/compiler.py`
- 公开 evidence 构造：`dynsteer/adapter/contract.py`、`dynsteer/adapter/toolsandbox/utils/contract.py`
- 生成 prompt：`dynsteer/prompt/templates/milestone/generation.en.md`
- 评价实现：`milestone_reliability.py`

### 2.2 离线反事实重放口径

为了区分 LLM 响应问题与代码处理问题，本报告进行了两种只读离线重放，没有修改正式结果文件：

1. **仅解围栏重放**：只精确删除响应开头的 ` ```json\n` 和末尾的 ` ``` `，然后调用当前 `_parse_path_response()` 及当前去重、共识和 terminal 规则。
2. **六次调用直接投票诊断**：围栏解开后，不再要求至少 4 条“不同”路径，而是把 6 次 schema 合法响应都作为独立票，保留三分之二即 4 票共识。该结果只用于判断去重门槛的影响，不是正式 benchmark 分数，也不是建议直接照搬的最终算法。

所有反事实 GED 都使用当前代码和同一 strict cost profile。它们不能替代重新运行实验，只用于根因隔离。

## 3. 正式实验结果：当前可评测覆盖为 0

| 指标 | 当前值 | 解释 |
|---|---:|---|
| case 数 | 25 | 人工 reference 全部存在 |
| `completed` | 0（0%） | 无 prediction graph 进入比较 |
| `generation_rejected` | 25（100%） | 全部在 milestone 编译阶段拒绝 |
| LLM 路径请求 | 150 | 每 case 6 次独立调用 |
| 正式解析有效路径 | 0/150（0%） | 围栏导致顶层解析失败 |
| 正式可比较图 | 0/25（0%） | 对人工图的交付覆盖差距 100% |
| strict/structural GED 样本 | 0 | 图差距不可计算 |
| strict/structural node F1 样本 | 0 | 节点差距不可计算 |
| 人工 reference | 64 节点 / 43 边 | 21 个非空图、4 个空图 |
| 人工约束 | 217 | 全部为 `state_snapshot:custom` |

正式结果只能说明生成链路没有交付图，不能据此声称语义 GED 为 0。`prediction: {}` 表示流程被拒绝，不是合法的 0 节点 prediction。

## 4. 原始响应核查：直接故障属于 prompt/代码协议不一致

### 4.1 150 条响应的真实格式

| 检查项 | 数量 |
|---|---:|
| 原始响应总数 | 150 |
| 以 JSON Markdown 围栏开头 | 150（100%） |
| 以裸 `{` 开头 | 0（0%） |
| 精确解围栏后可由 `json.loads` 解析 | 150（100%） |
| 解围栏后通过当前完整 path schema | 150（100%） |
| 解围栏后未知 evidence / 非法索引 / terminal 错误 | 0 |
| atom 总数 | 392 |

因此当前错误链路是：

```text
prompt 一方面要求“Return JSON only”
  + 同一 prompt 用 ```json 围栏展示 schema
  + milestone 调用未请求 provider 结构化输出
  → Qwen 150/150 返回围栏包裹的合法 JSON
  → compiler 对 raw 直接 json.loads
  → line 1 / column 1 解析失败
  → 150 条路径全部记为 rejected
```

这不是单纯的“模型不听指令”，而是 prompt 示例、provider 调用和 parser 三者没有形成一致协议：prompt 示范了围栏，parser 却拒绝围栏；现有 LLM 边界支持 JSON object 模式，但 milestone 调用没有启用。

### 4.2 LLM schema 质量与语义质量必须分开评价

解围栏后的 150/150 schema 合法，说明 LLM 对字段、evidence 白名单、terminal 位置和类型约束执行得很好。但 schema 合法不等于 milestone 语义正确：

- 392 个 atom 中，320 个（81.6%）选择工具调用 evidence；
- 55 个（14.0%）选择 `agent_message_instruction`；
- 16 个（4.1%）选择公开 invariant message；
- 仅 1 个（0.3%）选择 `tool_result_present`；
- 150 个 terminal 中有 12 个不是工具调用，其中 5 个是用户指令、6 个是 invariant、1 个是 tool result；
- 12/150 条路径选择了 minefield invariant，选择分布与人工 minefield 并不一致。

用户指令和 invariant 适合做生成上下文或安全约束，却不应普遍作为“已经完成的正向 milestone”。当前 evidence catalog 允许模型这样选择，是输入契约与证据角色设计的问题，不应只归咎于 LLM。

## 5. 仅修复围栏后，现有编译规则仍会拒绝 23/25

### 5.1 去重路径数分布

精确解围栏后，每个 case 都有 6 条 schema 合法路径，但按当前 `(evidence_id, expected, occurrence)` 序列去重后的分布为：

| 不同路径数 | case 数 |
|---:|---:|
| 1 | 10 |
| 2 | 7 |
| 3 | 3 |
| 4 | 2 |
| 5 | 2 |
| 6 | 1 |

20/25 个 case 少于固定门槛 4，直接被拒绝。其余 5 个中，3 个在“只看不同路径”后没有 terminal 节点获得三分之二支持，最终仅 2 个可生成。

| 仅解围栏后的结果 | case 数 | 占比 |
|---|---:|---:|
| 不同路径不足而拒绝 | 20 | 80% |
| terminal 共识不足而拒绝 | 3 | 12% |
| 可生成 | 2 | 8% |

两张反事实生成图分别是：

- `modify_contact_with_message_recency_insufficient_information`：人工为空图，反事实生成 2 节点 / 1 边，strict/structural GED 相似度均为 0；
- `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools`：人工 3 节点 / 2 边，反事实生成 2 节点 / 1 边，strict/structural GED 相似度均为 0.5。

即便只看这两个成功样本，一个仍是明确的空图误判。因此 JSON 修复只能恢复观测能力，不能恢复 milestone 可靠性。

### 5.2 “必须不同”正在惩罚稳定一致

以下简单任务的六次输出全部给出相同 evidence 序列，却会因 `distinct=1 < 4` 被拒绝：

- 添加联系人：`add_contact`；
- 打开 location：`get_location_service_status → set_location_service_status`；
- 更新联系人关系：`search_contacts → modify_contact`；
- 查询节日天数：`get_current_timestamp → search_holiday → timestamp_diff`；
- wifi-off 节日变体、message-recency 变体等。

这些序列未必与人工节点完全一致，但六次相同首先表示模型对线性工具路径有较强一致性。当前算法把重复采样当作需要删除的重复项，随后又要求至少 4 条不同序列，等价于要求模型为大量本来只有一种自然解法的任务制造替代路径。

### 5.3 六次合法调用直接投票的诊断结果

若仅作为诊断，把六次合法调用都当作票、以 4/6 为共识，而不要求 4 条不同路径：

- 22/25 个 case 可形成图；
- 3/25 仍因 terminal 共识不足被拒绝；
- 22 个可形成图的 strict/structural GED 相似度均值为 0.4064；
- 对应人工/生成节点总数恰好都是 55，但这是抵消后的总量假象；18 个非空可比 case 中，13 个生成偏少、1 个相等、4 个偏多；
- 四个人工空图全部被生成成非空图，空图类别准确率仍为 0/4；
- 人工/生成边总数为 37/33，复杂任务仍被压缩。

strict 与 structural 的诊断均值完全相同，不代表语义很好，而是两种 descriptor 下生成节点与人工节点仍全部发生单位代价替换。该 0.4064 只说明节点/边数量与拓扑的粗略接近程度。

## 6. 逐 case 差距核查

下表中的“解围栏后结果”均为离线诊断，不是正式结果。

| case | 人工节点/边 | 不同路径 | 解围栏后结果 | LLM 原始回复暴露的主要差距 |
|---|---:|---:|---|---|
| `find_days_till_holiday_insufficient_information` | 0/0 | 2 | 路径不足 | 5/6 回复仍执行 `search_holiday → timestamp_diff`，没有识别不可执行/空图。 |
| `modify_contact_with_message_recency_insufficient_information` | 0/0 | 6 | 生成 2/1 | 路径在工具执行、角色约束和结束对话之间高度分裂；最终仍错误生成非空图。 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 0/0 | 1 | 路径不足 | 6/6 构造 `instruction → search_contacts → modify_contact`，把信息不足任务误判为可执行。 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 0/0 | 1 | 路径不足 | 与 10-distraction 相同，稳定但稳定地错误。 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 1/0 | 3 | 路径不足 | 人工要求拒绝/说明无法删除；模型多次构造搜索，并用 instruction evidence 伪装不存在的删除步骤。 |
| `add_reminder_content_and_date_and_time` | 2/1 | 2 | 路径不足 | 主流为 `instruction → datetime conversion → add_reminder`；把解析指令当节点，并遗漏最终反馈语义。 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | 2/1 | 3 | 路径不足 | 路径在 1、2、3 节点间摆动；直接投票可得 2/1，但严格标签仍不对齐。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | 2/1 | 2 | 路径不足 | 与 base 基本一致，3 节点主流相对人工 2 节点偏细。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | 2/1 | 2 | 路径不足 | 与未扰动 3-distraction 相同，说明该族语义较稳定，但仍被多样性门槛拒绝。 |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 2/1 | 1 | 路径不足 | 6/6 只有 `add_contact`，捕获核心动作，但遗漏人工最终确认阶段。 |
| `modify_reminder_with_recency_latest` | 3/2 | 3 | 路径不足 | 主流生成 5 个工具步骤，过度展开时间转换工具，同时没有与人工的阶段聚合方式对齐。 |
| `search_message_with_recency_oldest_multiple_user_turn` | 3/2 | 5 | terminal 不足 | 路径长度 1—5，高度不稳定；多轮用户模拟指令与实际业务步骤混在一起。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | 3/2 | 4 | 生成 2/1 | 少 1 个节点和 1 条边；部分路径把 instruction/invariant 当 milestone。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | 3/2 | 4 | terminal 不足 | 参数描述扰动使路径分裂为“搜索”“时间推断”“角色约束”等不同终点。 |
| `turn_on_cellular_low_battery_mode` | 3/2 | 2 | 路径不足 | 主流只做 `get status → set status`，遗漏低电量前置状态与最终用户反馈中的至少一个阶段。 |
| `turn_on_location_low_battery_mode` | 3/2 | 1 | 路径不足 | 6/6 为 `get location status → set location status`，稳定但比人工三阶段少一阶段。 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | 3/2 | 2 | 路径不足 | 与 base 高度一致；当前拒绝主要来自代码门槛，不是 distraction 语义漂移。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | 3/2 | 1 | 路径不足 | 6/6 同一两节点序列，仍被“必须不同”拒绝。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | 3/2 | 1 | 路径不足 | 与描述扰动版一致，说明工具主路径鲁棒，但人工阶段覆盖不足。 |
| `update_contact_relationship_with_relationship` | 3/2 | 1 | 路径不足 | 6/6 为 `search_contacts → modify_contact`，遗漏人工确认阶段。 |
| `find_days_till_holiday` | 4/3 | 1 | 路径不足 | 6/6 为三工具链；遗漏最终回答，并把人工图中两个并行前置节点串行化。 |
| `send_message_with_contact_content_cellular_off` | 4/3 | 2 | 路径不足 | 部分路径包含恢复 cellular，部分直接发送；共识会漏掉关键环境恢复或最终反馈。 |
| `find_days_till_holiday_wifi_off_alt` | 5/4 | 1 | 路径不足 | 6/6 与普通节日查询相同，完全遗漏 wifi-off 变体增加的环境前置阶段及最终回答。 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 5/4 | 1 | 路径不足 | 6/6 为 `search_messages → search_contacts → modify_contact`，复杂人工五阶段被压成三阶段。 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 5/4 | 5 | terminal 不足 | 只有 2/6 路径清楚表达 `search → enemy → friend`；其余路径主要描述 User A 角色/invariant，严重受多轮模拟文本干扰。 |

## 7. 根因归属：LLM、代码和评价协议各自的问题

| 层级 | 已确认问题 | 证据 | 责任判断 |
|---|---|---|---|
| 输出协议 | 150/150 返回围栏，parser 只收裸 JSON | 原始响应与 line 1 解析错误 | prompt、provider 调用、parser 的集成问题；不是 JSON 内容损坏 |
| LLM schema | 解围栏后 150/150 通过完整 schema | 离线调用当前 parser | 当前字段级遵循良好 |
| 路径共识 | 20/25 因不同路径少于 4 被拒绝 | 去重路径分布 | 固定门槛与任务结构不匹配，属于算法规则问题 |
| terminal 共识 | 3/25 在去重后无 2/3 terminal | 多轮/扰动 case | LLM 路径分裂与去重计票共同导致 |
| 可执行性 | 4/4 人工空图均生成工具链 | raw outputs | LLM/prompt 缺少显式 disposition；compiler 又禁止空 atoms |
| 证据角色 | 71 个 atom 使用 instruction/invariant | 392 个 atom 分类 | evidence catalog 未区分上下文、正向里程碑和 minefield |
| 阶段粒度 | 常规任务遗漏反馈/前置阶段，复杂任务被压缩 | 逐 case 主流路径 | prompt 阶段定义和 evidence 粒度不足 |
| 多轮理解 | 用户模拟规则被当成业务步骤 | 两类 multiple-user-turn 原文 | 公开任务投影与 prompt 没有清楚隔离 simulator role |
| 严格评价 | 人工为 state snapshot，生成为单一公开 evidence | 217 条 reference 约束与生成模型 | 表示空间不同，strict GED 不能单独代表语义质量 |

## 8. 改进建议与优先级

### P0-A：统一结构化输出协议

1. milestone 调用显式请求 provider 的 JSON object/structured output；当前 OpenAI-compatible 边界已有相关能力，应由生成调用实际启用。
2. 删除 prompt 示例外层的 Markdown 围栏，确保“示例格式”和 parser 接受格式一致。
3. 若必须兼容不支持结构化输出的 provider，只允许在统一 LLM 边界精确解包单层 ` ```json ... ``` `，并记录解包指标；不要在 compiler 中从任意正文贪婪提取 `{...}`。
4. 增加本批真实响应回归样本：裸 JSON、单层围栏、解释正文、截断 JSON、数组、缺字段和未知 evidence。
5. 全量运行前先跑 3 个 smoke case；若前 12 次调用出现同构顶层解析错误，应快速失败，而不是继续消耗剩余调用。

### P0-B：重构路径有效性与共识计票

1. 将“schema 合法调用数”和“不同路径数”拆成两个指标。成功门槛应首先要求足够的合法独立调用，而不是足够多的不同答案。
2. 对简单线性任务，重复路径应增加该路径的支持度；不同路径数只作为不确定性/探索度诊断，不应成为一票否决条件。
3. terminal 支持度也应按合法独立调用计票，不能先删除重复路径再计算，否则越一致的终点反而越容易失去票数。
4. 若任务确有多种可交换前置或替代工具，再要求适度多样性；不要对所有任务统一要求 `N-2` 条差异路径。
5. 使用本报告的反事实结果作为回归基线：协议修复后至少应从 2/25 的现规则可生成覆盖显著提升，同时防止 4 个空图被错误放行。

### P0-C：把不可执行、澄清和空图建模为一等结果

1. 路径响应顶层增加 `disposition`，至少区分 `executable`、`needs_clarification`、`no_action_or_refusal`。
2. 对 `needs_clarification/no_action` 允许 `atoms=[]`，并通过多次分类投票形成合法空 graph，而不是触发 schema 拒绝。
3. 先统一 5 个信息不足 case 的 benchmark 口径：四个是空图，`remove_contact...` 是一个拒绝说明节点；模型需要判断“无动作”与“需要输出拒绝消息”的差别。
4. 将这 5 个 case 设为独立 P0 回归集，第一指标是 disposition/空图类别，第二指标才是图结构。

### P1-A：重新设计公开 evidence 的角色和粒度

1. `agent_message_instruction` 只能作为任务上下文，不应默认允许成为正向 milestone constraint。
2. invariant evidence 应主要用于 minefield/安全约束，不应与业务 atom 共用同一选择空间；还需区分系统政策、用户模拟器规则和业务目标。
3. 工具 evidence 不能只检查 `$.name`。至少需要在不泄漏私有答案的前提下支持公开参数、关键返回字段、可观察状态变更和最终用户回复。
4. 一个生成人工可比 milestone 往往需要多个约束；当前“一 atom 一 evidence 一 constraint”无法表达人工节点中状态事实加 guardrail 的聚合语义。
5. 对无法从公开视图观察的人工约束明确标记 `unobservable_from_public_view`，不要让生成器通过私有 gold 反推答案。

### P1-B：补齐阶段粒度、DAG 和多轮结构

1. 明确阶段角色：前置状态、实体查找/筛选、业务变更、结果验证、最终用户反馈；按任务需要选择，不强制每类都存在。
2. 对 `find_days_till_holiday` 保留“当前时间”和“节日时间”两个并行前置，再汇合到差值计算和最终回复，而不是统一压成串行工具链。
3. 对 cellular/wifi/location 场景显式建模环境恢复步骤，避免变体与 base 生成完全相同图。
4. 多轮任务按用户轮次和每轮终态建边；prompt 中必须明确 User A simulator 文本是对话驱动规则，不是 Agent 要执行的 milestone。
5. 最终用户反馈是否构成 milestone 必须与人工标注统一；当前大量 1—2 节点缺口来自生成器只关注工具调用。

### P1-C：改进评价口径和鲁棒性指标

1. 保留当前 strict GED，但明确命名为 descriptor 级一致性，不把它单独解释成语义相似度。
2. 增加无标签拓扑 GED、节点/边数量误差、阶段角色匹配、terminal/route、一致性和 constraint 字段分层指标。
3. 建立公开 evidence 与 ToolSandbox state snapshot 的非泄漏语义映射，并在真实轨迹上比较人工图与生成图的命中序列、coverage 和 virtual-stop 决策。
4. 对 base、3/10 distraction、arg-description/type-scrambled 同族增加 graph consistency。相同任务语义不应因无关工具或参数描述表面变化而改变 disposition、节点数或主路径。
5. 对拒绝 case 也保存规范化候选图/投票摘要，让失败样本可参与误差分析。

### P2：复跑与扩大实验

1. 完成 P0 后先复跑同一 25-case；要求 150 条响应的 parse/schema 状态可分层统计，不再出现全批同构错误。
2. 同一配置至少重复 3 次，报告生成成功率、空图分类准确率、同 case 图一致性和分层 GED。
3. 25-case 回归稳定后再扩到完整 ToolSandbox，避免用更大规模放大协议和共识缺陷。

## 9. 建议验收标准

### 阶段 1：协议恢复

- provider 调用成功率不低于 99%；
- JSON parse 和 schema validation 分别统计，均不低于 99%；
- 围栏解包若存在，必须有明确计数，不允许静默宽松提取；
- 3-case smoke 通过后才运行 25-case。

### 阶段 2：生成覆盖与空图

- 25/25 都产出可评测结果，包括合法空图/拒绝节点；
- 四个人工空图的 disposition 全部正确；
- `remove_contact...` 能稳定生成 benchmark 期望的拒绝说明节点；
- 不再因“路径过于一致”拒绝简单线性任务。

### 阶段 3：语义与结构

- 分别报告常规、信息不足、多轮、环境前置和扰动 case；
- 同族变体 disposition 和核心阶段一致；
- 多轮任务保留轮次顺序，环境变体包含必要前置；
- 除 strict descriptor 外，必须提供行为或语义层指标。

## 10. 最终判断

当前结果与人工标注的差距需要按三层表述：

- **正式可交付覆盖差距：100%**，因为 25/25 没有 prediction；
- **当前正式语义/结构差距：不可测**，因为 GED/F1 样本数为 0；
- **原始候选语义仍有显著差距**：即使修复围栏，现规则只能生成 2/25；即使把六次合法调用直接计票，四个空图仍全部错误，复杂任务普遍缺阶段，多轮任务明显混淆。

因此本批次不能用一个 GED 数字概括。最重要的工程判断是：**LLM 已经能稳定生成符合内部 schema 的内容，但系统没有稳定接住它；接住之后，路径共识、空图判定、evidence 表达和多轮语义仍需要连续改进。**

## 附录 A：项目中没有把握实现的模块部分

最没有把握直接落地的是“公开 evidence 与 ToolSandbox 私有 `state_snapshot/custom` 人工约束的语义对齐器”。原因是生成侧有意看不到 benchmark 私有 expected/scorer，直接把私有 reference 映射规则注入生成器会造成评测泄漏。该模块需要先确定允许使用的监督边界，再通过真实轨迹命中行为验证，不能仅凭节点名称或工具名建立映射。

另一个需要 benchmark 维护者确认的口径是信息不足任务：四个 reference 是空图，而 `remove_contact...` 是一个拒绝说明节点。若不先明确“拒绝消息何时算 milestone”，生成器无法仅靠统一规则稳定复现这两种标注。

本报告中的“仅解围栏”和“六次调用直接投票”均为只读反事实诊断，不是对正式结果的修改，也不是最终算法承诺；正式改进效果必须在代码修复后用同一配置复跑确认。
