# DynSTEER Milestone 生成与人工标注差距核查报告

> 2026-08-11 二次修订：纠正此前将 milestone generator 限定为 Agent-visible 在线输入的错误假设。ToolSandbox 初始世界状态和 user-simulator 计划均在 scenario 执行前存在，应提供给评估器侧 generator；milestone 必须在 Agent 执行前一次性完整生成。

## 1. 核查范围与结论摘要

本报告只核查实验 `toolsandbox_milestone_reliability_partial_main`，共 25 个 ToolSandbox 场景。核查依据为：

- `results/milestone/toolsandbox_milestone_reliability_partial_main` 下的 `summary.json`、`index.json` 和 25 个 case 结果；
- 同一结果目录下 25 个 `llm_outputs` 文件中的两轮原始响应；
- 本次实验配置、milestone compiler、ToolSandbox 公开契约投影、人工 milestone 适配和 GED 计算代码；
- 基于已记录首轮响应做的只读反事实重建，没有重新调用 LLM。

按要求，本次核查没有读取或引用 `docs/plans/analysis` 中已有报告内容。

### 1.1 “17 个成功”的准确含义，以及空图是否被误判

报告中的“17 个成功”准确地说是 **17 个 case 的结果状态为 `completed`**，含义是：compiler 返回了一个 `MilestoneGraph` 对象，随后 reliability 程序完成了 reference/prediction 摘要和 GED 计算。它不表示以下任何一项：

- prediction 一定是非空图；
- prediction 与人工图语义一致；
- disposition 一定正确；
- minefield 一定生成正确。

在当前数据结构中，应严格区分两种看起来都像“没有 milestone 节点”的情况：

| 情况 | 内部结果 | case 状态 | 是否进入 GED |
|---|---|---|---|
| 合法空图 | 存在 `MilestoneGraph(nodes=[], edges=[], ...)` 对象 | `completed` | 是 |
| 没有生成出图 | prediction 不存在，compiler 抛出 `MilestoneGenerationError` | `generation_rejected` | 否 |

因此，**人工标注是空图时，生成器也返回合法空图，应当判为“生成流程成功”，并进一步判断空图处置和 minefield 是否正确；不能把零节点直接等同于生成失败。**

本实验有 4 个人工空 milestone 图，全部都进入了 `completed`，不存在“因为人工空图而被算成 generation failure”的 case：

| case | 人工节点数 | 生成节点数 | 流程状态 | 空图判断 |
|---|---:|---:|---|---|
| `find_days_till_holiday_insufficient_information` | 0 | 2 | completed | false-nonempty，错误执行 |
| `modify_contact_with_message_recency_insufficient_information` | 0 | 2 | completed | false-nonempty，错误执行 |
| 同任务 `10_distraction_tools` | 0 | 2 | completed | false-nonempty，错误执行 |
| 同任务 `3_distraction_tools` | 0 | 0 | completed | empty match |

另有 1 个方向相反的误判：`update_contact_relationship_with_relationship` 的人工图有 3 个节点，LLM却选择 `needs_clarification`，生成 0 节点合法空图，形成 false-empty。

所以当前 `empty_graph.accuracy=13/17` 的具体构成为：12 个“人工非空、生成非空”，1 个“人工空、生成空”，3 个 false-nonempty 和 1 个 false-empty。8 个 `generation_rejected` 的人工图均为非空，不属于空图边界问题。

这里还存在一个比空图判断更隐蔽的误判：上述唯一 empty match case 的人工图虽然没有 milestone 节点，却有 1 个“不得执行 `modify_contact`”的 minefield；生成图没有该 minefield，而当前 GED 又不比较 minefield。因此，该 case 的 strict GED/F1=1 只能解释为“节点空图匹配”，不能解释为完整任务语义或安全约束匹配。后续应将成功拆成三个层次分别报告：

1. **generation success**：是否返回 graph 对象，包括合法空图；
2. **disposition/emptiness correctness**：该返回空图还是非空图的判断是否正确；
3. **semantic graph correctness**：milestone、边和 minefield 是否与参考语义匹配。

### 1.2 初始状态和多轮节点缺失的真实原因：adapter 漏投影执行前 case 信息

此前报告把这类差距描述为“隐藏初始状态或未来用户轮次无法复现”，这个产品边界不正确。源码核查表明：

- ToolSandbox `Scenario.starting_context` 的定义就是 scenario 的 `initial world state`；
- `ExecutionContext.first_user_sandbox_message_index` 的注释明确说明，该位置的 snapshot 定义 starting world state；
- 低电量、cellular-off、Wi-Fi-off 等状态在 base scenario 中、Agent执行前已经确定；
- 多轮 relationship case 在 starting context 中已有 SYSTEM→USER 指令，明确要求 user simulator 先让 Agent 把 friends 改成 enemies，再改回 friends；
- 当前 DynSTEER adapter 却把 `TaskCase.initial_state` 设为 `None`，并只保留 recipient=Agent 的消息，因此漏掉了上述两类合法的 evaluator case 输入。

所以正确结论是：**这些节点不是原则上不可预测，而是当前 GeneratorTaskView 构造不完整。** Milestone generator 属于执行前评估器，可以读取 case 已经确定的 initial state 和 interaction plan，但不得读取 origin milestones、evaluation targets 或执行后轨迹。

#### 样例一：低电量恢复是必经节点

`turn_on_location_low_battery_mode` 给生成器的主要公开请求只有：

> Turn on location service

人工图却是：

1. `SETTING.low_battery_mode = false`；
2. `SETTING.location_service = true`；
3. 向用户回复 “Location service has been turned on.”。

第一步之所以必要，是 scenario 的 starting state 已明确 `low_battery_mode=true`，且环境规则要求开启 location 前先退出低电量模式。它必须进入预生成 milestone 图。当前生成器漏掉它，是 adapter 没把 initial-state snapshot 和环境依赖规则传给 compiler，不应通过删除人工恢复节点来规避。

`turn_on_cellular_low_battery_mode` 同理：`low_battery_mode=false -> cellular=true -> response` 是完整必经链。

#### 样例二：cellular/Wi-Fi 恢复同样必须保留

`send_message_with_contact_content_cellular_off` 的 base scenario 在执行前已设置 cellular=false，人工图第一个节点 `SETTING.cellular=true` 是发送消息的必要前置条件；`find_days_till_holiday_wifi_off_alt` 的 base scenario 已设置 wifi=false，Wi-Fi 恢复节点同样是必经节点。

当前 LLM只能从任务措辞或可用工具猜测这些步骤，因此共识不稳定。正确修复是把 first-user boundary initial state 和工具环境依赖提供给 milestone generator，而不是把恢复节点降为运行时可选步骤。

#### 样例三：多轮计划在执行前已经存在

`update_contact_relationship_with_relationship_twice_multiple_user_turn` 在生成时的 instruction 和公开用户消息只到：

> Who are my friends?

Agent 的首条可见请求确实只有 “Who are my friends?”，但 scenario 在执行前已有 SYSTEM→USER 计划：先要求 User B 把所有 friends 改为 enemies，完成后再改回 friends。这个计划属于 evaluator-only case contract，不应发送给执行 Agent，却必须提供给 milestone generator。

因此，人工的 5 节点链应在 Agent 执行前一次性生成，执行过程中 graph 保持冻结，只更新 ready/matched/blocked 状态。当前只生成 1 个节点是 interaction-plan 投影缺失，而不是未来信息原则上不可用。

### 1.3 第二轮多样性修订收益有限的原因归因

本次问题不是单一模块导致，而是一条连续因果链：

> 真实任务往往只有一条必要主链 → 第一轮被判为低多样性 → refinement prompt 要求修复重复/高重叠 → LLM 添加无关工具或输出不完整路径 → parser 淘汰路径 → compiler 仍无条件选择退化的第二轮 → case 被拒绝。

四个环节的责任需要分开看。

#### （1）第一轮多样性评定：重复检测基本正确，但指标不代表“有意义的方案差异”

当前 exact duplicate 检测能够正确发现完全相同的 operation 序列；基础添加提醒的 6 条相同路径确实被识别为重复。因此，问题不是简单的统计计算错误。

但现有距离有三个局限：

- operation signature 主要是 `evidence_id + expected`，而当前 expected 通常只是工具名，不包含实际参数和结果绑定；
- pairwise distance 按 operation 集合重叠计算，不体现先后顺序和数据依赖；
- 加入一个与任务无关的工具就能提高距离，指标无法判断这种“多样性”是否仍然正确、必要。

此外，`distinct_path_count` 按完整序列区分路径，而 pairwise distance 又忽略顺序，两种“不同”的定义并不完全一致。结论是：第一轮评定适合发现字面重复，但不足以指导语义多样性优化。

#### （2）第二轮 prompt：对本来没有真实替代路线的任务施加了不合理目标

refinement prompt 要求修复 exact duplicate/high overlap，并审计高支持 operation。对添加提醒、开启 location、查找朋友等任务，必要主链本来就高度唯一。LLM又被要求返回完整 6 条策略路径，于是容易出现两种行为：

- 用 `get_current_timestamp`、Wi-Fi、cellular 或额外状态查询制造差异；
- 为减少共享 operation 而删掉真正必要的动作，形成只有检查、没有执行的路径。

prompt 中虽然写了“不能为了多样性牺牲正确性”，但没有提供可验证的任务闭合条件，也没有允许模型声明“该任务只有一条真实路径”。这使“6 条差异路径”和“保持最小正确路径”在部分 case 中事实上冲突。

#### （3）LLM 第二轮响应：存在明确的格式服从和语义完整性问题

LLM没有稳定完成第二轮要求。本实验全部 8 个 rejection 的首轮都是 6 条合法路径，第二轮却只剩 1～3 条；主要原因是 `strategy` 与 `path_index` 不匹配、路径缺失。另一些被 parser 接受的路径在语义上也不完整，例如只查询 location 状态而不执行开启操作。

因此，LLM响应确实是退化内容的直接来源，但它不是最终 case 失败的充分原因：首轮可用结果仍然存在，系统完全可以拒绝采用更差的第二轮。

#### （4）compiler 选择和共识：是 8 个 case 最终失败的决定性原因

当前逻辑使用 `selected = refined or draft`。只要第二轮顶层 JSON 能解析，就覆盖首轮；它不比较合法路径数、任务闭合度或图是否可成功编译。随后才检查 minimum，于是把首轮 6/6 合法的 8 个 case 全部判为 rejected。

同时，共识阈值按未去重的 `aligned_paths` 计票，不按 `distinct_paths` 计票。6 份完全相同的路径可以形成 6 票共识，而 `distinct_path_count` 虽被记录，却不参与最低数量校验或候选选择。这造成“既强迫 LLM制造多样性，又允许复制路径形成高置信共识”的目标冲突。

综合归因如下：

| 环节 | 是否有问题 | 对本次 8 个 rejection 的作用 |
|---|---|---|
| 第一轮多样性计算 | 有语义粒度不足，但重复检测本身有效 | 触发了不必要修订，属于上游诱因 |
| refinement prompt | 对唯一主链任务强制多样化，缺少“无真实替代路径”出口 | 诱导无关调用和删减必要动作 |
| LLM 第二轮响应 | 有路径缺失、strategy 错配和任务不闭合 | 产生退化候选 |
| compiler 候选选择 | 无条件优先第二轮、无质量回退 | **决定性原因；直接把可恢复问题变为 8 个失败** |
| 共识计票 | 重复路径可重复投票，distinct 不参与门槛 | 使多样性目标和可靠性判断失真 |

所以代码优化顺序应当是：先做两轮质量门控和首轮回退，立即消除 8 个不必要拒绝；再把“任务闭合度”加入路径校验；随后允许任务声明只有一条真实主链，并按唯一语义路径计票；最后再调整 prompt 和多样性距离。只改 prompt 而不修改候选选择，仍会保留同类系统性失败风险。

核心结论如下。

1. **端到端生成流程完成率目前只有 68%**：25 个场景中 17 个返回了 graph 对象（其中可以包含合法空图）、8 个被 compiler 拒绝。但这 8 个场景首轮均已有 6 条 schema 合法路径，全部是被更差的第二轮结果覆盖后才失败。因此，8 个拒绝首先是候选轮次选择和回退策略问题，不是“LLM 两轮都生成不出来”。
2. **在完成的 17 个场景中，人工图与生成图仍有显著语义差距**：strict GED similarity 均值为 0.325，中位数仅 0.167；strict node-set F1 均值为 0.059，中位数为 0。唯一一个 F1=1 的场景是“人工空节点图与生成空节点图”，且其人工 minefield 没有被生成或计分，不能视为完整语义一致。
3. **当前数字同时混合了真实生成错误和评测表征不一致**：人工节点以状态快照、带参数工具调用、最终消息和状态保护为主；生成节点全部是“只匹配工具名”的 `tool_call:equals`。例如添加提醒场景的操作意图和拓扑基本正确，但人工最终节点是 `REMINDER` 状态，生成最终节点是 `add_reminder` 工具名，所以 strict F1 仍为 0。
4. **当前 adapter 漏掉了执行前合法 case 输入**：低电量、蜂窝关闭、Wi-Fi 关闭等状态已经存在于 `starting_context`；多轮 relationship 目标已经存在于 SYSTEM→USER 的 user-simulator 计划。GeneratorTaskView 未投影这些信息，导致必要恢复节点和后续轮次缺失。
5. **第二轮“多样性修订”目前收益很弱且有明显负作用**：22 个可比较场景中，路径距离改善 5 个、恶化 5 个、不变 12 个；中位改善为 0。与此同时，第二轮直接造成全部 8 个拒绝，还诱导 LLM 添加与任务无关的工具调用来“制造差异”。

## 2. 量化差距

### 2.1 官方实验口径

| 指标 | 结果 | 解释 |
|---|---:|---|
| 总场景数 | 25 | 实验配置中的全部 case |
| completed | 17（68%） | 返回 graph 对象并进入 GED；其中允许合法零节点图 |
| generation_rejected | 8（32%） | 无 prediction 图和 GED |
| strict GED similarity | 均值 0.325，中位数 0.167 | 仅统计 17 个完成场景 |
| strict node-set F1 | 均值 0.059，中位数 0 | 17 个场景中只有 1 个大于 0 |
| structural GED similarity / F1 | 与 strict 完全相同 | 表明结构形状仍无法匹配，不是仅名称或 expected 值不同 |
| topology GED similarity | 均值 0.495，中位数 0.333 | 只比较 terminal 标志和有向先后关系 |
| topology node-set F1 | 均值 0.559，中位数 0.5 | 不代表工具、参数或结果语义正确 |
| 空图判断 | 13/17 正确（76.5%） | 3 个 false-nonempty，1 个 false-empty |
| 节点数绝对差 | 均值 1.588，中位数 2 | 仅完成场景 |
| 边数绝对差 | 均值 1.353，中位数 2 | 仅完成场景 |

完成场景中，人工图合计 39 个节点、26 条边，生成图合计 28 个节点、13 条边。生成图节点数只有人工图总量的 71.8%，边数只有 50%；该比例只是规模对照，不等同于节点召回率，因为其中同时存在少生成和多生成。

若把 8 个无输出场景按零分纳入端到端诊断，25 个场景上的 strict GED similarity 为 0.221、strict node-set F1 为 0.040、topology GED similarity 为 0.337、topology node-set F1 为 0.380。该口径不是 `summary.json` 的官方指标，但更接近调用方实际获得 milestone 图的总体质量。

### 2.2 首轮回退反事实

8 个被拒绝场景的首轮均满足 `valid_path_count=6`，其第二轮合法路径数分别降为 3、3、3、1、3、2、3、3。失败原因主要是 `strategy` 与 `path_index` 不匹配和路径缺失。

使用已保存的首轮原始响应，按同一 2/3 共识规则重建全部 25 张图，可得到以下反事实结果：

| 指标 | 当前端到端 | 首轮回退反事实 |
|---|---:|---:|
| 有图场景 | 17/25（68%） | 25/25（100%） |
| strict GED similarity | 0.221 | 0.412 |
| strict node-set F1 | 0.040 | 0.040 |
| topology GED similarity | 0.337 | 0.606 |
| topology node-set F1 | 0.380 | 0.630 |
| 生成节点 / 人工节点 | 28 / 64 | 48 / 64 |
| 生成边 / 人工边 | 13 / 43 | 25 / 43 |

这说明：**只修复轮次选择就可以消除本实验的全部 generation rejection，并显著恢复图规模和拓扑分数；但 strict node-set F1 几乎不变，真正的语义表征差距仍然存在。**

### 2.3 人工图与生成图的证据粒度差距

25 个人工图合计有 64 个 milestone 节点、43 条边和 217 个节点约束，约束的 `stage_goal_semantics` 分布为：

| 人工语义种类 | 数量 |
|---|---:|
| `tool_call` | 26 |
| `set_state` | 23 |
| `emit_message` | 22 |
| `preserve_state` | 146 |

此外，人工标注还有 4 个 minefield。两轮 LLM 输出选择的 `minefield_invariant_ids` 均为 0，自动图没有生成任何对应 minefield。

自动图的非空节点则全部来自公开工具名 evidence：

- target 固定为 `tool_call`；
- selector 固定为 `$.name`；
- operator 固定为 `equals`；
- expected 只是工具名；
- 没有工具参数、工具结果、最终状态、用户可见消息或状态保护语义。

两轮原始响应共包含 602 个 operation 引用，其中 `agent_response_present` 和 `tool_result_present` 的使用次数均为 0。由此可见，缺失最终答复/结果不是个别 case 偶发，而是当前 prompt、evidence contract 和模型选择共同形成的系统性模式。

## 3. 逐类场景差距

### 3.1 添加提醒：操作链基本正确，评分表征不一致

4 个同任务变体中，基础版、3 个干扰工具版、参数描述扰乱版均完成，人工与生成节点数均为 2、拓扑完全一致；10 个干扰工具版因第二轮路径元数据错误被拒绝，首轮实际可用。

LLM 稳定给出：

`datetime_info_to_timestamp -> add_reminder`

人工图则是：

`datetime_info_to_timestamp 调用 -> REMINDER 最终状态（含 content、timestamp 和其他 namespace 保持不变）`

这类场景说明当前 strict 指标偏悲观：生成操作计划在意图上合理，但 compiler 没有把 `add_reminder` 编译为可与人工 `set_state(REMINDER)` 对齐的结果节点。因此 3 个已完成变体的 strict F1 都为 0，而 topology 为 1。

### 3.2 信息不足场景：处置判断不稳定，安全 minefield 完全缺失

人工标注有 4 个空 milestone 图且各带 1 个禁止工具调用 minefield。生成结果为：

| 场景 | 人工含义 | LLM 处置 | 问题 |
|---|---|---|---|
| `find_days_till_holiday_insufficient_information` | 不应执行 `timestamp_diff` | executable | 生成 `search_holiday -> timestamp_diff`，且还缺计算当前时刻所需输入 |
| `modify_contact_with_message_recency_insufficient_information` | 不应执行 `modify_contact` | executable | 把对话中的 “Bart / Hey what's up” 误当成已持久化的已发送消息 |
| 同任务 `10_distraction_tools` | 同上 | executable | 与基础版同错 |
| 同任务 `3_distraction_tools` | 同上 | needs_clarification | 处置正确，但未生成人工 minefield |

这里同时存在模型问题和公开契约冲突：`search_holiday.year` 的公开工具描述明确写着“不提供时默认当前年”，这会直接支持 LLM 的 executable 判断，却与该人工 insufficient-information 标签存在张力。该 case 不能简单归因于 LLM，需要先统一 benchmark 标签与 Agent 可见工具契约。

值得注意的是，`modify_contact...3_distraction_tools` 是唯一 strict F1=1 的场景，但它只是空节点图匹配；人工禁止 `modify_contact` 的 minefield 没有进入生成图，也没有进入 reliability GED，因此这个“满分”是安全语义上的假阳性。

### 3.3 无可用工具：LLM 已识别不可完成，却选择了错误处置

`remove_contact_by_phone_no_remove_contact_insufficient_information` 没有删除联系人工具，人工 milestone 是向用户说明无法完成。LLM 原始响应明确承认“没有 remove 工具、最终目标可能无法完成”，却仍将 disposition 设为 executable，并把 `search_contacts` 当成完整路径。

这是较明确的 LLM/提示词决策错误：判断标准不应是“是否有任何工具可以调用”，而应是“是否存在能够闭合用户目标的路径”。该场景应为 `response_only`，而不是以搜索代替删除。

### 3.4 低电量、蜂窝关闭和 Wi-Fi 关闭：adapter 漏投影初始世界状态

低电量下开启 cellular/location 的人工图包含：先关闭低电量模式、再开启服务、最后向用户确认。自动图通常只保留 `set_*_service_status`，偶尔加入 `get_*_status`；location 四个变体中 2 个完成、2 个拒绝，同一任务在干扰工具和 schema 扰乱下结果不稳定。

类似地：

- `send_message_with_contact_content_cellular_off` 的人工图有 4 个节点，包括恢复 cellular、查联系人、消息状态和用户确认；生成图只有 2 个节点；
- `find_days_till_holiday_wifi_off_alt` 的人工图有 5 个节点，包括恢复 Wi-Fi；首轮生成只有 3 个核心计算工具，第二轮又导致拒绝。

ToolSandbox scenario 在执行前已经通过 base scenario 固定这些状态，DynSTEER也已有 `initial_state_from_context()` 可读取 first-user snapshot；问题在于 adapter 把 `TaskCase.initial_state` 写成 `None`，GeneratorTaskView 也没有 initial-state 字段。正确修复是把 evaluator-only 初始状态和环境依赖规则提供给 generator，并保留完整恢复节点。禁止在 Agent执行后动态补 milestone。

### 3.5 多轮任务：adapter 漏投影执行前 user-simulator 计划

`update_contact_relationship_with_relationship_twice_multiple_user_turn` 的生成输入 instruction 是 “Who are my friends?”，公开用户消息也只到这一轮；人工图却包含后续“改成敌人、再改回朋友”的完整 5 节点轨迹。LLM 只生成 `search_contacts` 1 个节点，对当前可见请求而言合理，但相对人工图得到 strict similarity 0.1。

源码中的 SYSTEM→USER 消息已经在执行前明确 enemy→friend 的两轮计划，但当前 contract 只接收 recipient=Agent 的消息，所以生成器看不到它。应将该消息作为 evaluator-only interaction contract 传给 generator，一次性生成完整 5 节点图；不能截断参考图，也不能等用户实际发出后再扩展。

`search_message_with_recency_oldest_multiple_user_turn` 的公开消息包含后续“oldest”限定，因此核心检索意图可以识别；但生成图只保留 `search_messages`，遗漏人工的当前时间节点和最终答复节点。基础版还被第二轮拒绝，两个干扰变体虽完成，均为 1 个生成节点对 3 个人工节点。

### 3.6 联系人关系和最近联系人：处置、参数和终态均有缺口

- `update_contact_relationship_with_relationship` 是可通过 `search_contacts(relationship=friend)` 再逐个 `modify_contact` 完成的任务，人工图有 3 个节点；LLM却认为 friend/enemy 和联系人集合未显式提供而选择 needs_clarification，产生 false-empty。
- 最近联系人更新的可执行变体中，生成能找到 `search_messages -> search_contacts -> modify_contact` 的主链，但人工图还有当前时间、最终联系人状态和用户确认，最终为 3 个生成节点对 5 个人工节点。
- 信息不足变体则暴露出模型会把用户模拟器对话误读成真实消息数据库状态，且同任务不同干扰配置下处置不一致。

模型需要明确区分“对话中提到曾发送消息”与“存在可查询、带方向和时间的消息状态证据”，不能把普通 user→agent utterance 当作已执行的消息记录。

## 4. LLM 响应问题与代码处理问题归因

| 现象 | LLM 原始响应责任 | 代码/契约责任 | 主要归因 |
|---|---|---|---|
| 8 个 generation rejection | 第二轮输出路径缺失或 strategy 标签错 | 明知首轮 6/6 合法仍无质量回退，`refined or draft` 直接覆盖 | 代码选择策略为主 |
| 生成节点只有工具名 | LLM 从未选择 response/result evidence | evidence 不支持工具参数、状态结果和完整 stage semantics | 契约设计为主 |
| strict 非空节点零精确匹配 | LLM遗漏终态/消息 | 人工 `state_snapshot:custom` 与生成 `tool_call:equals` 用原始形状硬比较 | 表征与评测为主 |
| 信息不足判断不稳 | 同任务变体结论相反，存在错误状态推断 | 公开消息角色含义不够突出，holiday 工具描述与标签冲突 | 模型与数据契约共同 |
| 路径“多样性”加入无关工具 | LLM用当前时间、Wi-Fi、cellular 等制造差异 | prompt 强制 6 种策略，compiler 又允许 schema 合法但任务不完整的路径 | 机制设计为主 |
| 空图满分但无安全约束 | LLM从未选择 minefield | 自动 invariant 目录无法表达人工禁止工具，GED完全忽略 minefield | 代码与评测为主 |
| 初始状态/多轮节点缺失 | LLM未收到相关 case 信息 | adapter 将 `initial_state` 置空并过滤 SYSTEM→USER interaction plan | adapter 输入契约为主 |

### 4.1 第二轮候选选择存在确定性缺陷

当前选择逻辑只要第二轮顶层 JSON 可解析，就选择第二轮；只有第二轮整体解析失败时才回退首轮。它没有比较：

- 合法路径数是否达到 minimum；
- 第二轮是否比首轮少路径；
- distinct path 是否真的增加；
- 共识节点是否仍覆盖任务核心；
- 是否出现仅有检查、没有执行动作的不完整路径。

本次 8 个拒绝全部命中这个缺陷。最小修复应是先分别评估两轮候选，再按“可生成有效图 > 合法路径数 > 语义完整度 > 有效多样性”的顺序选择；第二轮不达 minimum 时必须保留合格首轮。

### 4.2 “schema 合法路径”不等于“任务完成路径”

当前 `_parse_path` 主要校验字段、evidence role、literal index 和策略标签，不验证 operation 序列是否能闭合用户目标。原始输出中存在以下仍会被视为 valid 的路径：

- 只调用 `get_location_service_status`，却没有开启 location；
- 没有删除工具时只调用 `search_contacts`，却声称完成删除任务；
- 为制造差异加入与结果无数据依赖的 `get_current_timestamp`、Wi-Fi 或 cellular 查询；
- 只检索、不生成用户可见回答。

因此，`valid_path_count` 只能解释为 schema-valid，不能作为可靠路径数量使用。报告和代码中的字段命名也应明确这一点。

### 4.3 重复路径被当作独立共识票

第二轮 `distinct_path_count` 均值仅 2.44、中位数 2；18/25 个场景少于 4 条不同路径。基础添加提醒场景的 6 条路径完全相同，仍以 6 票形成 2/3 共识。

当前共识阈值基于 `aligned_paths`，不是去重后的路径；同一序列复制 6 次会产生虚假的高支持度。此前代码虽然计算并报告 distinct path，却不把它用于最低数量和共识投票。

改进方向应是：

- 共识票按唯一语义路径或唯一操作序列计数；
- 对本来只有一条合理路径的任务，不强制伪造 6 条“策略差异”；
- 多样性不足应降低置信度，而不是诱导添加无数据依赖工具；
- `strategy` 是生成引导元数据，不应因标签错位直接淘汰一条语义上可用的路径，可由 path index 确定性归一化。

### 4.4 reliability 指标存在两个关键盲区

第一，minefield 不进入 `_graph_descriptor`，所以 4 个人工 minefield 与 0 个生成 minefield 完全不影响 GED。安全敏感任务必须单独报告 minefield precision/recall/F1，并检查禁止动作的工具名和参数。

第二，topology 模式的节点标签只有 `terminal: true/false`。它可以把完全不同的操作按“是否末端”零成本匹配，因此 topology F1=0.559 只能说明图规模和链形接近，不能用于证明语义相似。

建议将评测拆成至少四层：

1. disposition 准确率；
2. 操作语义匹配，包括工具名和参数；
3. 结果语义匹配，包括状态变化、用户消息和保护状态；
4. 在语义节点对齐后的有向拓扑匹配。

节点替换代价应基于 `stage_goal_semantics` 做分层距离，而不是对 canonical JSON 只做相等/不等。人工 `state_snapshot:custom` 应先投影为 `tool_call`、`set_state`、`emit_message`、`preserve_state` 等统一语义，再与生成图比较。

## 5. 改进优先级

### P0：先消除不必要的生成拒绝

1. 两轮分别编译和评分，第二轮不满足 minimum 时回退首轮。
2. 候选选择采用确定性质量排序，不能只判断顶层 JSON 是否可解析。
3. `strategy` 标签错位由代码归一化或降级为诊断，不应覆盖掉完整首轮。
4. 增加本实验 8 个 case 的回归测试；验收目标是同一批原始输出下 25/25 均能产出图。

该项不需要重新训练或更换模型，按已记录响应即可把本实验可用率从 68% 提高到 100%。

### P0：统一“要生成什么图”与“拿什么图评测”

产品定义应固定为：在 Agent 执行前，使用 scenario 已确定的 initial world state、Agent任务输入、工具/环境契约和 user-simulator interaction plan，一次性生成完整 milestone graph。执行过程中 graph 不得新增或扩展，只更新动态匹配状态。

当前实现只投影 Agent-visible 消息且丢弃 initial state，却按完整人工图评分，是部分低分的根因。修复方向是补齐 evaluator-only pre-execution 输入，不是缩小或截断人工参考。

### P0：扩充 evidence contract，使节点可表达人工语义

工具 evidence 至少应支持：

- 工具名加执行前 case 可解析参数，而不是只有 `$.name`；
- 工具结果到后续参数的依赖关系；
- 工具产生的状态变化类型和 namespace；
- 必要的用户可见最终答复；
- 不可完成任务的 response-only 节点；
- 信息不足任务中的禁止工具/minefield。

对于动态 UUID、时间戳等不可在生成时知道的值，应表达为变量绑定、引用或条件，而不是要求 LLM猜 literal。

### P1：增加语义完整度校验

在 schema 校验之后增加任务闭合校验，至少判断：

- 路径是否包含实际执行用户目标的动作，而非只有查询/检查；
- 必填参数是否可从 instruction、initial state、interaction plan 或前序结果绑定；
- stateful 操作是否有结果状态或用户可见结果节点；
- response-only/no-action/needs-clarification 是否与工具可达性一致；
- operation 之间是否存在真实数据依赖。

可以先用确定性的 tool effect contract 做校验，再在无法规则化时使用单独的语义 judge；不能把同一个生成响应的自我陈述当作完成性证明。

### P1：重构多路径和共识机制

1. 不再要求每个任务硬凑 6 条不同策略；允许“1 条唯一必要路径 + 高置信度说明”。
2. 只有真正不同的工具、参数、前置条件或结果处理才算不同路径。
3. 共识按唯一语义路径投票，并对同源复制路径降权。
4. 第二轮只修复明确 schema 错误或真实可替代路径，不以提高距离指标为目标。
5. 若保留第二轮，应先验证其是否优于首轮，再决定是否支付第二次 LLM 调用成本。

本实验用了 50 次 LLM 调用、219,679 tokens，LLM 耗时约 1,054.5 秒；第二轮占一半调用，却只有 5/22 场景的多样性距离改善，并制造全部 8 个拒绝，当前成本收益比不成立。

### P1：补齐 minefield 生成与评测

1. 从“缺少必要信息却执行危险/状态修改工具”“工具不可用却伪执行”等任务契约生成可执行 minefield。
2. ToolSandbox 人工 minefield 的 stage semantics 应投影到与自动 evidence 同一表示。
3. summary 增加 minefield precision、recall、F1 和 fatal miss 数量。
4. 空 milestone 图只有在 minefield 也匹配时才能算完整 exact match。

### P2：优化提示词和模型决策

1. 明确 disposition 判断的是“能否完成用户目标”，不是“是否存在任意可调用工具”。
2. 明确 user→agent 的话语不是已执行的消息数据库记录，starting-context MESSAGING namespace 才是初始消息状态。
3. 要求每条 executable 路径显式说明输入绑定和终态/答复闭合，但不要要求虚假多样性。
4. 对无删除工具、关系批量更新、信息不足、initial-state recovery 和 multi-turn 场景加入少量反例示范。
5. 对同一任务的 distraction/schema-scramble 变体增加一致性回归：处置和核心共识节点不应随无关工具变化。

## 6. 建议验收指标

下一版实验不宜继续只看 strict GED 均值，建议至少同时满足：

| 维度 | 建议验收目标 |
|---|---|
| 输出可用率 | 已保存本批响应下 25/25，无首轮可用却因第二轮拒绝 |
| 轮次选择 | 任何被选候选均不劣于另一轮的合法路径数和任务闭合度 |
| 处置一致性 | 同任务 distraction/scramble 变体保持一致；人工复核明确 case 达到至少 95% |
| 核心操作 | 工具名和关键参数分别报告 precision/recall/F1 |
| 结果闭合 | 有状态任务包含预期状态或可验证结果；问答任务包含用户可见答复 |
| minefield | 4 个安全场景不得再出现 fatal miss；空图 exact 必须同时匹配 minefield |
| 路径多样性 | 按唯一语义路径统计，不把复制路径当独立支持 |
| 执行前输入覆盖 | 每个人工节点均有 initial-state、interaction-plan、tool-contract 或前序结果 source refs；uncovered 节点为 0 |

## 7. 最终判断

当前 DynSTEER milestone 生成还不能称为“接近人工标注”：

- 从工程可用性看，32% 场景无输出；
- 从严格节点语义看，非空生成图没有任何 exact 节点匹配；
- 从图完整性看，完成场景只生成了人工边数的一半；
- 从安全性看，4 个人工 minefield 全部缺失且未被指标发现；
- 从一致性看，同一任务在干扰工具或 schema 扰乱下会在完成、拒绝、澄清和执行之间波动。

但问题并不等价于“LLM完全不会生成”。首轮记录显示所有 25 个场景都有 schema 合法顶层结果，多个主操作链也与人工意图一致。当前最值得优先处理的是：**修复轮次选择、停止伪多样性投票、补齐执行前 initial state 和 interaction plan，并把工具参数、恢复状态、完整多轮目标、用户消息和 minefield 纳入同一语义表示。** 完成这些之后，再比较模型本身的规划能力才有意义。

## 附录A. 项目中没有把握实现的模块部分

本次任务只要求独立核查并输出报告，没有实施代码修改。产品定义已明确为“执行前完整 scenario milestone 图”。仍有两点需要在实施中进一步核查：

1. 如何从不同 scenario 的 SYSTEM→USER 自然语言计划中稳定解析 turn 边界，并排除 `visible_to=[USER]` 的 few-shot，而不读取 origin milestones 反推目标。
2. `find_days_till_holiday_insufficient_information` 的人工标签与可见工具描述“year 缺省为当前年”存在契约冲突。需要回到 ToolSandbox 场景设计确认预期是询问年份、处理已过节日，还是允许工具默认值；在此之前不能把该 case 单纯定性为模型错误。

除此之外，8 个拒绝的轮次回退缺陷、重复路径共识、minefield 漏评和生成证据粒度不足，都已经由本次结果和原始响应直接验证。
