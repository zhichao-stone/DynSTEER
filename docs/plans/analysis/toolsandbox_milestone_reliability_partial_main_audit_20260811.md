# DynSTEER Milestone 生成核查报告

## 1. 核查范围与结论

本报告仅核查当前实验 `results/milestone/toolsandbox_milestone_reliability_partial_main`，依据如下材料独立完成：

- `index.json`、`summary.json` 与 30 个逐 case 结果；
- 30 个 `toolsandbox/llm_outputs/*.json` 原始 LLM 响应；
- 当前 milestone 生成、模拟、聚合、ToolSandbox 适配与 reliability 指标代码；
- 本次运行对应的 ToolSandbox 原生人工 milestone graph。

未读取或引用 `docs/plans/analysis` 中已有报告的内容。实验仅运行 1 次，生成模型为 `qwen-plus-latest`、temperature 为 0，因此本文结论适用于这批 30 个 case，不能直接外推为稳定总体能力。

核心结论如下：

1. **若只看工具名称及其拓扑骨架，当前完全一致率为 17/30，即 56.7%。** 操作名称层面的 micro precision/recall/F1 分别约为 81.7%/90.7%/86.0%，说明模型通常能识别主要工具，但“整个图完全正确”的稳定性仍不足。
2. **若按人工 milestone 的完整表示比较，当前非空图没有一个节点达到严格或结构等价。** 28 个 case 的 strict/structural node-set F1 为 0；均值 0.067 仅来自另外 2 个“人工与生成都没有 operation 节点”的空图 case，并不代表非空 milestone 已有 6.7% 的节点完全对齐。
3. **安全性是最严重的功能缺口。** 人工标注共有 4 个 fatal minefield，生成结果命中 0 个，正例召回率为 0%。`minefields macro F1=0.867` 是因为 26 个无 minefield case 被计为 1，不能用于说明危险操作识别可靠。
4. **差距并非全部来自 LLM。** 原始响应确实存在漏步骤、复制隐藏初始状态、硬编码动态值、重复/额外工具和错误顺序；但代码又缺少终态完整性、参数 schema、数据来源和 disposition 一致性校验，并对全部“可模拟”路径做硬交集，导致一条坏路径即可删除真正必经的 milestone。
5. **当前 17/30 更接近“工具名骨架的上限分”，不是完整 milestone 质量。** 编译结果丢弃 LLM 给出的全部 `arguments`，也不生成 `set_state`、`emit_message`、`preserve_state` 类型节点，所以该分数无法证明参数、终态、用户响应和状态保护正确。

## 2. 量化差距

### 2.1 实验完整性与资源消耗

| 项目 | 当前结果 | 解读 |
|---|---:|---|
| case 数 | 30 | 全部 completed，无 generation/adapt/GED 失败 |
| reference 输入覆盖 | 30/30 | 所需 reference 工具均出现在生成器输入中，失败不能归因于工具不可见 |
| operation topology exact | 17/30（56.7%） | 当前主指标 |
| operation exact | 21/30（70.0%） | 只比较工具名及 occurrence 数 |
| topology exact | 19/30（63.3%） | 只比较 operation 间有向先后关系 |
| fatal minefield 命中 | 0/4（0%） | 4 个正例全部漏报 |
| repair 触发 | 28/30 | 几乎所有 case 都调用第二轮 |
| LLM 调用 | 58 次 | 平均 1.93 次/case |
| token 总量 | 558,176 | 平均约 18,606 token/case |
| 最终候选路径 | 平均 3.03 条 | 返回路径累计平均 9.13 条/case，包含两轮 |

未触发 repair 的 2 个 case——`add_reminder_content_and_date_and_time` 与 `find_days_till_holiday_insufficient_information`——主指标均失败。当前 repair gate 会在“至少两条路径、无结构 violation、且公共 operation 为空”时直接结束；这恰好放过了两类高风险结果：路径被拆成互补残片，以及 executable/response-only 相互冲突。

### 2.2 操作、拓扑与图规模

| 维度 | 人工 | 生成 | 真阳性 | micro precision | micro recall | micro F1 |
|---|---:|---:|---:|---:|---:|---:|
| operation occurrence | 54 | 60 | 49 | 81.7% | 90.7% | 86.0% |
| operation topology edge | 27 | 33 | 19 | 57.6% | 70.4% | 63.3% |
| fatal minefield | 4 | 0 | 0 | 不适用 | 0% | 0% |

图规模方面：

- 人工图共 75 个节点、49 条边；生成图共 60 个节点、33 条边，净少 15 个节点（20.0%）和 16 条边（32.7%）。
- 20/30 个 case 少节点，7/30 相同，3/30 多节点；平均绝对节点数差为 1.03。
- 19/30 个 case 少边，8/30 相同，3/30 多边；平均绝对边数差为 0.93。
- strict/structural GED similarity 均值只有 0.447；只保留先后拓扑后升至 0.644，说明主要差距不只是节点数量，而是节点语义表示不同。

### 2.3 完整 milestone 表示的结构性缺口

人工 75 个节点由以下目标类型构成：

| 人工节点类型 | 数量 | 当前生成支持 |
|---|---:|---|
| `tool_call` | 26 | 生成成 `tool_call:equals` |
| `set_state` | 28 | 不生成；reliability 指标仅把它近似映射为一个工具名 |
| `emit_message` | 21 | 不生成 |

人工节点共包含 248 个 `state_snapshot:custom` 约束，其中还包括 45 个 `preserve_state` 语义；生成的 60 个约束全部是 `tool_call:equals`。因此：

- 28 个非空或单边非空 case 的 strict/structural node-set F1 全为 0；
- 当前生成器不能复现人工图中的结果状态、用户可见回复、跨阶段状态保持与 ToolSandbox 自定义 scorer 约束；
- `operation_topology_exact` 通过把 `set_state` 近似成一个工具名绕过了这种表示差异，但也引入批量状态目标与具体工具调用次数不一致的问题。

例如 `Make all of my friends my enemy` 的人工图用一个 `set_state(CONTACT)` milestone 表示两个人都被更新，指标把它映射为一次 `modify_contact`；LLM 则按实际执行生成两次 `modify_contact`。这会被统计为“多一个 operation”，但本质首先是**目标状态 milestone 与逐次工具 occurrence 的粒度不一致**，不能简单归为模型多调用。

## 3. 13 个主指标失败 case 逐项核查

表中“生成”使用 `TP/reference <- generated` 记法。

| case | 生成差距 | 原始 LLM 与代码证据 | 主要归因 |
|---|---|---|---|
| `add_reminder_content_and_date_and_time` | operation `0/2 <- 0`，空图 | LLM 同时返回完整 `datetime_info_to_timestamp -> add_reminder`、仅 `add_reminder`、仅转换三条路径；后两条明显不完整却通过模拟，公共交集为空且未触发 repair | LLM 漏步骤 + 完整性校验缺失 + repair gate 缺陷 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | `1/2 <- 1`，缺转换及边 | 三条最终路径中一条正确，另两条用无关 `shift_timestamp`/`search_reminder` 构造替代；交集只剩 `add_reminder` | LLM 错误替代 + 数据来源/终态校验缺失 + 硬交集放大 |
| `find_days_till_holiday_insufficient_information` | operation 空图看似正确，但漏 1 个 fatal minefield | LLM 一条 executable 路径硬编码时间戳，另一条 response-only 且禁止 `timestamp_diff`；混合 disposition 被接受，forbidden 交集被 executable 路径清空 | LLM 未统一判定不可执行 + 动态值校验缺失 + minefield 聚合缺陷 |
| `find_days_till_holiday_wifi_off_alt` | operation 全对，topology `2/3 <- 3` | 所有模拟后路径都把 `get_current_timestamp` 放在 `search_holiday` 前，产生人工图没有的先后约束；未覆盖二者独立执行的反序 | LLM 路径多样性不足；拓扑应由数据/环境依赖推导而非采样顺序决定 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | `3/4 <- 3`，漏 `get_current_timestamp` | 一条路径包含完整 reference operation 集，其他路径省略 current time 或加入无依据 `timestamp_diff`；LLM 还直接使用初始状态中的 person ID | 隐藏状态泄漏 + LLM 硬编码 + provenance 校验缺失 + 硬交集放大 |
| `modify_contact_with_message_recency_insufficient_information` | 人工空 operation + fatal `modify_contact`；生成 2 个 operation、无 minefield | LLM 从初始状态复制 person ID，构造 `search_contacts -> modify_contact`；缺少可判断“最后联系人”的消息搜索工具仍被当作可执行 | 隐藏业务状态暴露 + LLM 违背提示 + 可执行性/数据来源校验缺失 |
| 同上 `_10_distraction_tools` | operation 最终为空，但仍漏 fatal minefield | 4 条伪 executable 路径与 1 条正确 response-only 路径混合；硬交集删掉 operation，同时 forbidden 交集也被清空 | 混合 disposition 未被视为冲突 + 双重硬交集产生“空但不安全”的图 |
| 同上 `_3_distraction_tools` | 生成 4 个 operation、无 minefield | 全部路径都复制隐藏 ID/时间戳并执行 `modify_contact`；干扰工具使模型进一步加入无意义 current-time/diff 链 | LLM 抗干扰失败 + 隐藏状态泄漏 + provenance/终态校验缺失 |
| `modify_reminder_with_recency_latest` | `3/3 <- 5`，多两种转换且多两条 topology | LLM 在 reference 的 current/search/modify 外固定加入 `timestamp_to_datetime_info` 与 `datetime_info_to_timestamp`；当前 prompt 又硬性要求自然语言时间保留完整转换链 | prompt 生成政策与人工标注口径不一致；额外支持 operation 未被指标容忍 |
| `search_message_with_recency_oldest_multiple_user_turn` | operation 全对，topology `0/1 <- 0` | 最终两条路径分别为 current->search 和 search->current，导致公共边消失；后一条仍硬编码 current-time 相关参数 | prompt 的“独立 operation 反序”规则误判真实数据依赖 + provenance 校验缺失 |
| 同上 `_3_distraction_tools_arg_description_scrambled` | `1/2 <- 1`，只剩 `search_messages` | 最终路径分别加入 shift/diff/unit_conversion，另有一条只 search；公共交集删除 `get_current_timestamp` | LLM 对参数描述扰动不稳 + 完整链校验缺失 + 硬交集放大 |
| `update_contact_relationship_with_relationship` | `2/2 <- 3`，多一次 `modify_contact` | 人工用一个批量 `set_state` milestone 表示两条联系人记录更新；LLM 根据隐藏初始行生成两次具体 modify | 评测/表示粒度不一致为主，同时存在隐藏初始状态依赖 |
| 同上 `_twice_multiple_user_turn` | `3/3 <- 5`，多两次 `modify_contact` | 人工用两次批量状态 milestone 表示“改成 enemy、再改回 friend”；LLM 对两个人逐个修改，共四次 modify | 评测/表示粒度不一致 + generator 只会 occurrence graph，不能表达批量 goal-state milestone |

## 4. LLM 问题与代码问题的边界

### 4.1 可明确归因于 LLM 响应的问题

1. **违反完整路径要求。** 基础 add-reminder case 明确返回 producer-only 和 terminal-only 路径，尽管 prompt 首行已经禁止这种输出。
2. **复制或编造动态值。** 多个 insufficient-information 与 recency case 直接输出 person/reminder ID、Unix timestamp，未从同一路径 producer operation 获得。
3. **为制造“多样性”加入无意义工具。** 包括零偏移 `shift_timestamp`、同值 `timestamp_diff`、seconds-to-seconds 的 `unit_conversion` 和任务前无意义的 search。
4. **对不可执行任务判定不一致。** 同一最终候选集合同时存在 executable 与 response-only，使 operation 和 forbidden 两侧都无法形成可靠共识。
5. **工具描述扰动下不稳定。** location 设置系列在 arg-description/type scrambled 下仍正确，但 search-message 的 arg-description scrambled 从 exact 退化为只保留一个 operation，说明抗扰动能力与任务族相关。

### 4.2 可明确归因于当前代码处理的问题

1. **模型可见状态与模拟器状态未隔离。** `build_toolsandbox_generator_view()` 把完整 `initial_state_from_context()` 放入 `GeneratorTaskView`，`MilestonePromptBuilder` 又把该状态原样写入 LLM payload。提示词虽然说“不得复制隐藏业务行”，数据本身仍已暴露，insufficient-information case 的原始响应证明模型确实复制了这些 ID。
2. **“模拟”只验证结构和环境恢复。** `_simulate_path()` 只检查 turn 顺序、disposition/forbidden 形式、evidence 是否存在，并按 environment rules 插入 recovery；不检查工具参数 schema、终态是否完成、返回值绑定、动态参数 provenance、冗余/重复调用或 response-only 的业务合理性。
3. **operation arguments 最终被完全丢弃。** `_OperationCandidate.arguments` 只参与少量环境规则匹配；`milestones_from_occurrences()` 生成约束时只保存工具名。因此当前图和主指标无法发现错误手机号、错误 timestamp、错误 search 条件或错误联系人 ID。
4. **repair 触发条件反向漏掉高风险空交集。** 当多条路径都“可模拟”且公共 operation 为空时，`preliminary` 为假，代码可能不 repair；本实验两个未 repair case 均失败。
5. **硬交集对坏路径极敏感。** `_mandatory_occurrences()` 对全部最终路径取 occurrence 最小交集；只要一条不完整路径漏掉必要工具，该工具就从 graph 永久消失。
6. **minefield 也采用全路径硬交集。** `_forbidden_intersection()` 要求每条路径都禁止同一 evidence；一个伪 executable 路径即可抹掉正确 response-only 路径中的 fatal 禁止项。
7. **没有完整 milestone 类型的编译能力。** 生成端只能产出 operation milestone 和 fatal minefield，不会构造人工图中的 `set_state`、`emit_message`、`preserve_state` 及相应依赖边。

### 4.3 prompt 与人工标注口径不一致的问题

- prompt 强制自然语言日期/相对时间保留转换链，但部分 ToolSandbox 人工 milestone 只标核心 search/current/state-update，导致正确遵循 prompt 的支持 operation 被判为额外节点。
- prompt 要求对“无依赖 operation”同时生成正反序，再由公共顺序决定边；但系统没有先建立可靠数据依赖，LLM 会把实际使用同一动态时间参数的 operation 错当作可反序。
- 人工 `set_state` milestone 可一次表示多行批量终态；生成端按实际每条工具调用计 occurrence。两者的节点粒度不同，不能用严格 occurrence 数直接裁决谁对谁错。

## 5. 改进建议与优先级

### P0：先修正确性与安全边界

1. **拆分模型公开状态与编译器模拟状态。**
   - LLM payload 只保留用户/Agent 真正可见的设备设置等公开字段。
   - CONTACT、MESSAGING、REMINDER、SANDBOX 历史行及业务 ID 只放在 compiler-private simulation state，禁止进入 prompt。
   - 不要依赖自然语言提示阻止模型使用已经提供的数据。

2. **在路径进入共识计算前增加确定性语义校验。** 至少覆盖：
   - 按公开 tool schema 校验必填参数、类型、枚举与多余字段；
   - 记录每个工具的输入/输出槽位，动态 ID、timestamp、坐标必须来自同路径先前 producer 或明确公开常量；
   - executable 路径必须存在满足用户终态/回答的 terminal effect，producer-only、conversion-only、search-only 不得通过；
   - 检测无作用转换、重复 terminal call、与任务无关的额外操作；
   - 同一 turn 的 candidate disposition 不一致时标记为冲突并强制修复，不能直接进入交集。

3. **重新设计不可执行判定与 minefield 生成。**
   - 先独立判定 turn 是 executable、needs-clarification 还是 response-only，再在该判定下生成路径；
   - 出现 executable/response-only 混合时必须 repair 或失败关闭，不能产出无 operation 且无 minefield 的假安全图；
   - fatal minefield 应来自“不可执行原因 + 有副作用 terminal tool”的确定性规则，而不是仅依赖所有 LLM 路径的 forbidden 交集。

4. **修复 repair gate。** 以下任一情况都应触发 repair：公共 operation 为空但任一路径含 operation、disposition 混合、forbidden 集合冲突、没有合法 terminal effect、参数 provenance 不完整。若 repair 后仍冲突，应返回可审计失败/低置信度，而不是静默输出空图。

### P1：统一 milestone 表达与聚合算法

1. **从“工具 occurrence 图”升级为“目标/效果图”。** operation 只是证据之一；节点应能表达 `tool_call`、`set_state`、`emit_message`、`preserve_state`，并保留 argument policy、动态 binding 与 expected effect。
2. **对批量更新按 goal-state 聚合。** 两次 `modify_contact` 可以支持一个“所有 friend 变 enemy”的状态 milestone；同时保留底层 evidence occurrence 供轨迹匹配，不要让节点粒度与调用次数强绑定。
3. **拓扑由依赖推导，路径顺序只作为补充证据。** 优先使用 producer-consumer binding、环境 recovery、跨 turn 顺序和 terminal effect 依赖构边；只有确定独立的操作才允许通过路径反序消除边。
4. **停止对未验证路径做无权重硬交集。** 先过滤语义非法路径，再按完成度、数据来源、终态覆盖聚类；对等价策略求共同 goal，而不是对原始 evidence occurrence 直接求集合交集。
5. **保留并编译参数语义。** 至少保留公开 literal、argument match policy、producer binding 和关键字段；否则“工具名正确、参数完全错误”仍会被计为 exact。

### P2：校准提示词、指标和实验设计

1. **明确 support operation 的评价口径。** 若转换工具属于允许但非人工 milestone 的实现细节，指标应区分 required/core、allowed-support、spurious；若要求严格复现，则 prompt 必须与人工标注统一。
2. **拆分三套指标并同时报告。** 建议分别报告：
   - goal-state/response/preserve 的完整 milestone 语义；
   - core operation 与数据依赖拓扑；
   - 参数/binding 正确性及安全 minefield 正例召回。
3. **minefield 报告必须增加正例指标。** 单独输出 positive-case recall、每类 fatal 工具 recall；禁止只用包含大量 empty-empty 的 macro F1。
4. **增加变体一致性指标。** 对 base、3/10 distraction、tool/arg scrambled 成组比较，统计同源 case 的图一致率。目前 add-reminder 与 search-message 组均出现非单调波动。
5. **多次重复并换 seed/model 复验。** 当前只有单次 temperature=0 结果，无法区分系统性缺陷与模型一次性输出偏差。
6. **增强结果可审计性。** 逐 case 保存完整 prediction graph、保留的 argument/binding、每条路径被接受/拒绝的确定性原因，以及从候选到最终节点的 provenance；当前需从 `llm_outputs` 和 path summaries 反推，审计成本较高。

## 6. 建议的下一轮验收门槛

在扩大样本前，建议先用同一批 30 case 做回归门禁：

1. 4 个 fatal minefield 正例全部命中，且 spurious fatal 仍为 0；
2. 所有 insufficient-information case 不得从隐藏初始状态复制业务 ID，不得产出伪 executable terminal write；
3. `add_reminder_content_and_date_and_time` 的 partial path 必须被 deterministic validator 拒绝，不能再因空交集输出空图；
4. 13 个当前失败 case 中，先剔除“批量 goal-state 与 occurrence 粒度冲突”后重新定义指标，再比较真实改善；
5. 非空图必须开始出现 `set_state`/`emit_message` 语义对齐，不能继续以 strict/structural node-set F1 全 0 的状态仅优化 operation topology；
6. 对 exact case 增加参数与 binding 验证，防止错误 timestamp/ID 被工具名指标掩盖；
7. 在质量提升的同时降低 28/30 的 repair 触发率和约 18.6k token/case 的成本，确定性校验应优先于继续堆叠 prompt 和 LLM 轮次。

## 7. 最终判断

当前 DynSTEER 已具备一定的**核心工具骨架识别能力**，尤其设备状态恢复系列 8 个 case 全部达到 operation-topology exact；这说明环境 recovery 注入与常见设置类任务的基础方向有效。

但它尚不能可靠生成与人工标注等价的**完整 milestone graph**。最关键的短板依次是：安全 minefield 正例全漏、隐藏状态与动态参数缺少隔离/来源校验、坏路径参与硬交集、参数被编译器丢弃，以及生成节点类型与人工 goal-state/response milestone 不一致。建议先完成 P0 的安全与确定性校验，再统一 P1 的 milestone 表达；在此之前，仅提高 LLM 模型能力或继续强化 prompt，预计只能局部改善 56.7% 的骨架 exact，无法解决完整图 F1 为 0 的结构性问题。
