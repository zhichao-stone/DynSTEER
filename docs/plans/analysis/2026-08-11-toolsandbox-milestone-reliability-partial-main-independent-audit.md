# ToolSandbox milestone 生成可靠性独立核查报告

> **修订说明（2026-08-11）**：本报告准确记录了实验症状，但对优化方案导致的系统性回归定性不足，也没有完成方案—实现—实验的因果追踪。请以同目录的《ToolSandbox milestone 语义优化回归根因复核报告》作为当前根因结论；本报告保留为第一轮核查记录。

## 1. 核查范围与方法

本报告仅核查实验 `results/milestone/toolsandbox_milestone_reliability_partial_main`，未读取或引用 `docs/plans/analysis` 中任何既有报告。

核查对象包括：

- `summary.json`、`index.json` 和 25 份 case 结果；
- 25 份 `toolsandbox/llm_outputs/*.json` 原始 LLM 响应；
- 当前版本的 ToolSandbox adapter、goal contract、compiler、prompt 和 semantic canonicalizer；
- 通过当前项目适配器重新加载的 ToolSandbox 人工 milestone graph。

核查时没有重新请求 LLM。对已有两轮响应使用当前 compiler 重新执行了解析、binding 和 task completeness 校验，以恢复 rejected case 在结果文件中未保存的具体违规码。

本实验仅有 25 个经过选择的 ToolSandbox case，模型为 `qwen-plus-latest`、温度为 0。以下结论适用于该实验切片，不应直接外推为全部 ToolSandbox 或其他模型的总体表现。

## 2. 结论摘要

当前结果与人工 milestone 仍存在实质性差距，尚不能作为可靠的 milestone 自动生成方案使用。

1. **生成可用率低**：25 个 case 中仅 11 个标记为 `completed`，14 个被拒绝，表面完成率为 **44%**。其中 3 个 completed case 没有任何通过校验的 LLM path，而是由 deterministic non-executable fallback 生成；若只计算至少存在一条完整 LLM path 的 case，产出率为 **8/25=32%**。
2. **没有一个语义完全匹配**：`complete_semantic_exact=0/11`。即使只看 completed case，严格 GED similarity 均值也只有 **0.1402**，中位数 **0.1667**；拓扑 GED similarity 均值 **0.2803**。
3. **图结构明显退化**：11 个 completed case 的人工图共 19 个节点、12 条边，生成图共 11 个节点、**0 条边**。每一个 completed 生成图都只有一个节点。人工图非空时，生成器普遍只保留一个最终能力或一个响应节点，丢失辅助操作、响应和依赖顺序。
4. **空图识别失败**：人工 reference 为空图的 4 个 case 全部被生成成 1 节点图，空图 recall 为 **0/4**，并且 4 个 reference fatal minefield 均未精确匹配。
5. **操作层面好于完整图，但不足以支撑可靠执行**：completed case 的 operation macro-F1 为 **0.7424**、argument binding macro-F1 为 **0.7273**；但 effect F1 只有 **0.5152**，response 和 minefield F1 均只有 **0.0909**，semantic topology F1 为 **0.4545**。
6. **失败同时来自 LLM 和代码契约**：LLM 经常输出错误 binding 协议、虚构 source ref、冗余查询或不完整参数；但当前 prompt 没有完整说明 `operation_result` binding，repair prompt 更没有提供 binding schema。同时，goal contract、前置条件、响应契约和 invariant 解析也不足以表达人工图。
7. **部分指标存在系统性不可比问题**：人工图没有 disposition 语义，而生成图必然带 disposition，因此 completed case 的 disposition F1 全为 0；人工图没有生成侧的 argument binding 表示；operation 节点的 matching route 又被 canonicalizer 计作 response。`complete_semantic_exact=0` 因此既反映真实生成差距，也包含表示层不对齐，当前不能把它单独当作生成质量的无偏估计。

## 3. 定量差距

### 3.1 端到端产出

| 指标 | 结果 | 含义 |
|---|---:|---|
| case 总数 | 25 | 本次实验切片 |
| completed | 11（44%） | 得到 graph，包括 fallback |
| generation_rejected | 14（56%） | 两轮后仍无可接受 graph |
| 至少有完整 LLM path | 8（32%） | 排除 3 个 fallback completed case |
| complete semantic exact | 0/11（0%） | completed 中无完整语义精确匹配 |
| repair 触发 | 19/25（76%） | 首轮稳定性较差 |
| LLM 调用 | 44 次 | 共 208,346 tokens |

3 个“completed 但 complete_path_count=0”的 case 为：

- `modify_contact_with_message_recency_insufficient_information`；
- `modify_contact_with_message_recency_insufficient_information_10_distraction_tools`；
- `remove_contact_by_phone_no_remove_contact_insufficient_information`。

这说明 `completed` 当前混合了“LLM 成功生成”和“编译器确定性兜底”两种完全不同的产出来源，不适合直接作为模型完成率。

### 3.2 图规模与拓扑

全部 25 个人工图合计有 64 个 milestone 节点、43 条边；14 个 rejected case 完全没有生成图。11 个 completed case 的对比如下：

| 范围 | 人工节点 | 生成节点 | 人工边 | 生成边 |
|---|---:|---:|---:|---:|
| 11 个 completed case | 19 | 11 | 12 | 0 |

completed case 的绝对节点数差均值为 1.4545，绝对边数差均值为 1.0909。更关键的是，生成结果不是在人工拓扑上出现少量错边，而是统一退化为零边图。典型表现包括：

- `find_days_till_holiday`：人工图包含 current timestamp、holiday search、timestamp diff 和响应链，生成图只保留 holiday search；
- 3 个 `search_message_with_recency_oldest_multiple_user_turn*`：人工图包含 current timestamp、message search 和响应，生成图只保留 message search；
- `turn_on_cellular_low_battery_mode`：人工图包含 battery recovery、cellular set 和响应，生成图只保留 cellular set；
- `add_contact_with_name_and_phone_number_3_distraction_tools`：人工图有状态变化和响应两个节点，生成图只有 add-contact 操作。

因此 topology GED/F1 仍高于严格语义分数，主要是空拓扑或单节点的集合性质造成，不能解释为依赖关系已经基本正确。

### 3.3 分维度语义

| 维度 | completed macro-F1 | 判断 |
|---|---:|---|
| operations | 0.7424 | 常能找到一个核心工具，但容易漏辅助工具或恢复工具 |
| argument bindings | 0.7273 | 受人工图无同构 binding 表示影响，数值偏乐观且不可直接解读 |
| effects | 0.5152 | 能力/效果映射不稳定，漏恢复效果明显 |
| topology | 0.4545 | 生成图零边，空集合 exact 抬高了部分 case |
| responses | 0.0909 | 普遍缺人工响应节点，且 canonical route 定义混杂工具交互 |
| minefields | 0.0909 | fatal minefield 全部未精确命中，并大量生成非人工 minefield |
| dispositions | 0.0000 | reference 不含 disposition，属于表示不对齐 |

严格和 structural 的 GED、node-set F1 在本实验中完全相同，且 strict/structural node-set F1 均为 0。这表明现有 structural descriptor 没有提供可辨识的额外诊断，或者生成与人工节点在 canonical label 上根本没有零代价匹配。

## 4. Rejected case 的直接原因

使用当前 compiler 复算第二轮响应后，14 个 rejected case 可归为以下几类。一个 case 可能同时属于多类。

### 4.1 Binding JSON 协议无法解析

至少 3 个 case 的第二轮响应在 binding dataclass 构造前后即失败：

- `add_reminder_content_and_date_and_time` 使用未定义的 `case_binding/source_evidence_id/binding_path`；
- `add_reminder_content_and_date_and_time_10_distraction_tools` 使用未定义的 `case_reference/source_ref/path`；
- `find_days_till_holiday_wifi_off_alt` 把 `case_literal` 写成缺少 `source_refs` 的形式。

首轮还有多类 case 直接把参数写成原始字符串、布尔值或数字，而不是带 `kind` 的 binding 对象，例如 location、send-message、wifi 和 multi-turn contact case。repair 通常能把外层格式包成 `case_literal`，但无法正确建立 operation-result dataflow。

这既是 LLM schema adherence 问题，也是 prompt 契约问题：generation prompt 只给了 `case_literal` 示例；refinement prompt 的 `arguments` 示例为空，完全没有展示合法的 `operation_result` 结构、`operation_index` 和 `selector`。

### 4.2 来源校验与数据流表达失败

第二轮仍有大量 `literal_not_in_source` 或 `unknown_source_ref`：

- LLM 使用不存在的 `CONTACT`、`initial_state` 等 source ref；
- 把搜索得到的 `person_id`、`reminder_id` 或 phone number 当作 instruction literal；
- 把 timestamp conversion 的结果当作新 literal，而不是 `operation_result` binding；
- recovery 的 `true/false` 虽来自 environment rule，却常错误标成 `instruction`。

典型例子是 `send_message_with_contact_content_cellular_off`：LLM 对“查联系人→开启 cellular→发消息”的高层理解基本正确，但 phone number 来自联系人记录、`on=true` 来自恢复规则，第二轮仍全部标为 instruction literal，最终被拒绝。

### 4.3 Completeness 校验把必要辅助操作视为 unrelated

当前 completeness 主要比较 `goal_contract.required_effects` 和操作 capability 集合，不计算完整的数据依赖闭包。因此：

- datetime/timestamp conversion；
- current-time retrieval；
- contact/reminder search；
- status getter；

经常被标为 `unrelated_operation`，即使人工 milestone 明确包含其中一部分。LLM 在 repair 中收到这些违规后，会删掉完成人工任务所必需的步骤。

`modify_reminder_with_recency_latest` 的第二轮原始响应对此给出了非常直接的证据：LLM 明确意识到“不使用 timestamp 工具就无法真正 postpone”，但为了通过 validator，最终删除 timestamp 计算并省略 `reminder_timestamp`，形成“可能通过结构校验但不能完成任务”的错误修复方向。

### 4.4 LLM 本身的推理与修复问题

不能将失败全部归因于 validator。原始响应也显示出以下模型问题：

- 首轮频繁忽略明确 JSON binding schema；
- 在不知道合法 source ref 时自行发明名称；
- 过度加入 getter/status check，未区分“执行必需”与“可以直接从初始状态确定”；
- repair response 极度冗长，在 `disposition_reason` 中反复自我矛盾；
- 面对 contract 与任务语义冲突时，选择臆测工具会自动推断缺失参数；
- 6-path 输出中存在大量近重复或无效路径，没有形成有效候选多样性。

因此需要同时改进 prompt/contract 和模型输出约束，单纯放宽 validator 也会接受不能完成任务的路径。

## 5. Completed case 仍然不准确的代码侧原因

### 5.1 Goal contract 只识别表面关键词，无法形成任务闭包

`_goal_contract()` 通过 instruction 中的 subject/action 词与 ToolEffect capability 做匹配。这能找到“holiday.search”“message.search”等核心能力，但不能从目标反推出：

- 计算“距节日多少天”需要 current timestamp 和 timestamp diff；
- “oldest/latest”需要时间基准、搜索和选择语义；
- 写工具的参数来自前序搜索结果；
- 执行后通常需要面向用户的完成响应；
- benchmark 的真实前置条件可能要求 battery、network 等恢复操作。

结果是 validator 会接受单工具路径，compiler 再把它编译成单节点图。

### 5.2 ToolSandbox 环境规则不完整

当前硬编码规则只覆盖 location/battery、message/cellular、holiday/wifi 三组关系。人工图显示 `turn_on_cellular_low_battery_mode` 还需要 battery recovery，但生成结果只执行 cellular set 并被判完整，说明公开环境规则与 benchmark 实际依赖没有对齐。

规则还只声明 recovery capability 和参数，未把“为何该辅助操作必要”与 argument source/dataflow 明确暴露给 LLM。

### 5.3 Response contract 与人工 milestone 不一致

当前 `_goal_contract()` 在首轮存在 required effect 时通常令 `response_required=False`，所以大部分普通执行任务不会生成最终 Agent→User 响应节点；人工图则普遍包含响应 milestone。

另一方面，semantic canonicalizer 将任何带 `matching_route` 的 milestone 都计入 responses，导致工具调用的 Agent→Environment 或 Environment→Agent route 也进入 response 集合。于是 response F1 同时受“缺最终回复”和“把工具交互当回复”两种问题影响。

### 5.4 Minefield 生成过宽，且与人工语义格式不一致

在 11 个 completed case 中，生成图有 9 个包含 minefield，而人工图只在 4 个 insufficient-information case 中包含 fatal minefield。普通 add-contact、holiday-search、message-search 和 remove-contact case 也生成了禁止相关工具的 fatal minefield。

原因之一是 invariant 解析按整行搜索 `must not/do not/never`，然后只要规则中出现通用 subject/action token 就匹配工具。一条包含多项说明的 simulator/system 文本可能把“contact”匹配到所有 contact 工具，甚至把任务要求的工具本身标为禁止。

对于真正的 insufficient-information case，生成 minefield 又使用 capability/protection 形式，而人工 graph 的 canonical minefield 主要来自精确 tool-call trigger，造成 4 个 fatal reference 全部 miss。

### 5.5 Deterministic fallback 掩盖生成失败

当全部 LLM path 不完整且 precheck 判断 turn 非 executable 时，compiler 会直接构造 deterministic path。该路径可以让 case 标记为 completed，即使 `complete_path_count=0`。这在本实验中发生 3 次。

fallback 本身可作为安全降级策略，但报告必须单独标记 `fallback_generated`，不能与“LLM path 通过校验”合并为 completed；否则会高估模型可靠性，也会让 selected round/path summary 与真实 graph 来源不一致。

## 6. 当前评测口径的偏差

本实验揭示的真实缺陷足够严重，但若要准确衡量改进幅度，评测口径也必须先校准。

### 6.1 Primary exact 指标目前存在结构性零分

生成图始终带 `turn_dispositions`，人工 graph 没有 disposition 标注，因此 disposition exact 必然为 false。只要 `complete_semantic_exact` 合取 disposition exact，生成器就几乎不可能取得 primary exact 成功。

建议：只有两侧都定义该维度时才纳入 exact，或者先把人工 graph 映射为同一份 disposition contract；不能把“reference 缺字段”解释为“生成 disposition 错误”。

### 6.2 Argument binding 不是同构标注

人工 ToolSandbox graph 的 canonical `argument_bindings` 在本切片中为空，而生成图显式记录 binding。当前 F1 很大程度上衡量“生成侧是否也为空”，不能反映参数是否正确 grounded。

建议从人工 constraint 的 exact/reference-derived arguments 与生成 binding 可解析出的值建立可比较表示，分别报告：argument presence、source validity、resolved-value equivalence 和 dataflow correctness。

### 6.3 Capability canonicalization 混淆等价效果

`add_contact` 在生成侧是 `contact.create`，人工 SET_STATE 约束经 `_semantic_capability()` 后成为 `contact.update`。二者对该场景可能是同一目标状态变化，却被 operation/effect exact 视为完全不同。

建议基于 benchmark tool/effect registry 统一映射人工和生成 capability，而不是分别依赖 tool name 与 namespace fallback 推导。

### 6.4 Response canonicalization 混入工具 route

建议 responses 只接收 `kind=emit_message` 且 recipient 为 USER 的语义；工具调用/返回 route 应留在 operation 或 evidence route 维度，不能因 milestone 有 matching route 就算作 response。

### 6.5 Empty-graph 指标名称容易误读

summary 中 `empty_graph.accuracy=0.6364` 是 11 个 completed case 上“是否为空”的总体准确率；它掩盖了 4 个真实空图全部预测成非空。应同时报告 empty precision、recall、F1 和 confusion matrix。本次真正关键的是 empty recall 为 0%。

## 7. 改进建议与优先级

### P0：先修正生成契约与评测可比性

1. **定义唯一、机器可读的 binding schema**：在 generation 和 repair prompt 中完整列出 `case_literal`、`operation_result`、`unresolved` 的字段、合法 source ref、operation index 与 selector；prompt 示例必须覆盖“搜索结果传给写工具”和“转换结果传给业务工具”。
2. **显式输出 legal source catalog**：为 instruction、每条 public asset、initial-state namespace/row、environment rule 及其 recovery arguments 提供稳定 source ref；禁止让 LLM猜测 `CONTACT` 或 `initial_state`。
3. **repair prompt 保留完整契约**：不能只给 goal contract 和一个 `arguments:{}` 外壳。应再次提供工具参数、result bindings、合法 source refs、允许的辅助依赖及针对每个 violation 的最小修复示例。
4. **修正 primary semantic exact**：先统一 disposition、capability、argument binding、response 和 minefield 表示，再用 exact 作为主指标。在此之前保留分维度指标，并把 exact 标记为“未校准”。
5. **区分生成来源状态**：至少拆为 `llm_generated`、`deterministic_fallback`、`rejected`。fallback 不计入 LLM generation success。

### P0：建立任务依赖闭包，而非只做关键词 capability 匹配

1. 为每个 ToolEffect 增加参数生产者、结果 selector、读写依赖和必要支持能力；
2. 从目标效果反向展开到可执行工具、所需参数、参数来源、恢复操作、观察操作和最终响应；
3. completeness 校验验证“目标效果 + 参数可得 + 前置条件恢复 + 用户可见完成”，而不是只验证核心 capability 是否出现；
4. 对 `latest/oldest/recency/date/time/days till` 等组合语义建立明确 resolver，避免依赖词面匹配；
5. 从 ToolSandbox 实际工具/场景元数据生成环境规则，减少手工维护的不完整 hardcode。

### P1：重做 response 与 minefield 语义

1. 依据人工场景约定决定每个 turn 是否需要 Agent→User response，multi-turn 必须保留全部预定义 user turns；
2. response canonicalizer 只识别用户可见消息；
3. invariant parser 先切分独立命题，再做否定作用域和具体 action/tool 匹配，不能用 subject token 把整类工具全部设为 fatal；
4. insufficient-information 的保护项应直接复用与人工 graph 相同的 trigger capability、severity 和 protection 表示；
5. 增加“禁止项不得与同一 turn 的 required capability 冲突”的静态校验。

### P1：约束 LLM 输出与候选选择

1. 优先使用 provider 的严格 structured output/JSON Schema，而不是仅请求 `json_object`；
2. 将 binding schema 验证错误转换为明确、字段级 repair 指令；
3. 对候选路径去重后再做 repair，避免 6 条近重复路径浪费 token；
4. 候选排序加入人工不可见但代码可验证的 task closure、dataflow 和 response completeness；
5. 限制 `disposition_reason` 长度，禁止把长篇推理写入输出协议。

### P2：扩展回归实验

建议建立按能力族分层的回归集，并至少报告：

- raw schema-valid、binding-valid、task-complete、LLM-generated、fallback、rejected 六级漏斗；
- 核心工具 recall、支持工具 recall、恢复工具 recall、最终响应 recall；
- argument source validity 与 operation-result dataflow accuracy；
- empty-graph precision/recall；
- fatal minefield recall 与 spurious fatal minefield rate；
- 节点数/边数差、拓扑 exact、分维度 canonical semantic F1；
- 扰动工具数量、参数 description/type scrambling、multi-user-turn、insufficient-information 等切片结果。

在修复后应至少用相同 25 case、相同模型和温度复跑，保证改进来自代码而不是样本/模型变化；随后再扩大到完整 ToolSandbox case 集，并增加多随机种子或多模型复验。

## 8. 建议验收门槛

下一轮进入更大规模实验前，建议至少满足：

- LLM path 产出率不低于 90%，fallback 单独统计且不高于 5%；
- 当前 25 case 上不再出现 binding JSON parse rejection；
- reference-empty recall 达到 100%，fatal minefield recall 不低于 95%；
- 普通 executable case 的 spurious fatal minefield rate 为 0；
- 支持/恢复工具 recall 不低于 90%；
- 用户可见 response recall 不低于 95%；
- 非空人工图的 generated edge recall 不低于 80%；
- 完成 canonicalizer 校准后，complete semantic exact 至少达到 70%，再讨论以其作为主指标。

这些门槛不是最终性能目标，而是证明生成链路、任务契约和评测口径已经基本可用的最低条件。

## 9. 最终判断

本次结果的核心问题不是单一模型能力不足，而是三层问题叠加：

1. LLM 没有稳定遵守 binding/source 协议；
2. 当前 goal contract 和 validator 无法表达人工 milestone 所需的完整操作闭包、响应、恢复与 minefield；
3. 人工图与生成图的 canonical 表示尚未完全对齐，使主 exact 指标存在结构性零分。

优先修复顺序应为：**先校准语义表示和输出契约，再补全任务依赖闭包与环境规则，最后优化模型提示和候选策略**。如果只更换模型或放宽 validator，预计只能减少部分 rejected case，无法解决单节点零边、响应缺失、minefield 错配和指标不可比等根本问题。
