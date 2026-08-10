# `toolsandbox_milestone_reliability_partial_main` 当前 Milestone 生成结果核查报告

## 1. 核查结论

本报告核查 `results/milestone/toolsandbox_milestone_reliability_partial_main` 中 2026-08-10 02:01:17Z—02:14:21Z 的当前批次，共 25 个 ToolSandbox case。

结论需要分成两个层面：

1. **从可交付性看，DynSTEER 与人工 milestone 的当前差距是 100%。** 25/25 个 case 均为 `generation_rejected`，生成覆盖率为 0%；150/150 条独立候选路径均未通过最外层 JSON 解析，最终没有任何 prediction graph 进入 GED 或节点 F1 比较。人工 reference 覆盖 25 个 case、合计 64 个节点、43 条边、21 个 terminal 节点和 217 条约束，而 DynSTEER 当前没有一张可用的生成图。
2. **从 milestone 语义和图结构看，当前差距不可计算，而不是“相似度为 0”。** `strict.ged_similarity`、`structural.ged_similarity`、`node_set_f1` 的样本数全部为 0；逐 case 的 `prediction`、`metrics` 也都是空对象。失败发生在 atom 解析之前，不能据此判断模型是否理解了任务，更不能把人工图的 64 个节点直接解释为“全部漏生成”。
3. **本批次暴露的首要问题是 LLM 输出协议回归，而不是节点语义质量。** 所有 case、所有六种路径策略都以同一个错误结束：`LLM milestone 返回必须是 JSON 对象`。这种 150/150 完全一致的失败模式说明系统尚未进入任务相关的 atom、路径对齐和共识图阶段。
4. **与 2026-08-07 的同名实验历史批次相比，生成可用率从 76% 降为 0%，下降 76 个百分点。** 历史审计记录当时 19/25 个 case 可进入 GED，对成功 case 的平均严格 GED 相似度为 0.4073；当前批次为 0/25，无法计算 GED。这说明最近的逐路径生成重构至少在端到端输出契约上产生了严重回归。历史数值只能用于定位回归，不能替代当前代码的语义质量评估。
5. **改进顺序必须是“恢复协议可用性 → 补齐空图/不可执行任务建模 → 再优化语义与结构”。** 在 JSON 解析成功率恢复之前，调整共识阈值、节点粒度或 GED 代价都不会改善当前结果。

## 2. 核查范围与口径

### 2.1 核查材料

- 当前实验索引：`results/milestone/toolsandbox_milestone_reliability_partial_main/index.json`
- 当前实验汇总：`results/milestone/toolsandbox_milestone_reliability_partial_main/summary.json`
- 25 个逐 case 结果：`results/milestone/toolsandbox_milestone_reliability_partial_main/toolsandbox/*.json`
- 实验配置：`data/experiments/toolsandbox_milestone_reliability_partial_main.json`
- 当前生成实现：`dynsteer/milestone/compiler.py`
- 当前生成 prompt：`dynsteer/prompt/templates/milestone/generation.en.md`、`generation.zh.md`
- OpenAI-compatible LLM 边界：`dynsteer/llm/openai.py`
- 历史对照：`docs/plans/analysis/2026-08-07-toolsandbox-milestone-reliability-partial-main-audit-report.md`

### 2.2 “差距”的三个口径

| 口径 | 当前结论 | 是否可量化 |
|---|---:|---|
| 生成/可交付覆盖率 | 0/25，对人工 reference 的 case 覆盖差距为 100% | 可以 |
| 图结构差距 | prediction graph 不存在，GED、节点/边差值均无样本 | 不可以 |
| milestone 语义差距 | 没有保存可审计的 prediction atom 或原始响应 | 不可以 |

这里必须避免两个误读：

- `prediction: {}` 表示生成流程被拒绝，没有产出 prediction；它不等价于“生成了合法空图”。
- summary 中 GED/F1 的 `mean: null` 表示无样本，不等价于数值 0。

## 3. 当前批次的量化结果

### 3.1 总体结果

| 指标 | 当前值 | 与人工标注的含义 |
|---|---:|---|
| case 数 | 25 | 人工 reference 全部存在 |
| `completed` | 0（0%） | 无生成图进入比较 |
| `generation_rejected` | 25（100%） | 全部在生成编译阶段拒绝 |
| LLM 候选路径请求数 | 150 | 每 case 6 条路径 |
| 通过解析的有效路径 | 0/150（0%） | atom 层覆盖为 0 |
| 去重有效路径 | 0/150（0%） | 无路径可参与共识 |
| 共识节点 | 0 | 这是拒绝流程的中间统计，不是合法空图 |
| 可比较 prediction graph | 0/25（0%） | 可交付覆盖差距 100% |
| strict/structural GED 样本 | 0 | 结构差距不可计算 |
| strict/structural node F1 样本 | 0 | 节点差距不可计算 |
| 人工 reference 节点/边 | 64/43 | 当前 prediction 无法对照 |
| 人工 terminal 节点 | 21 | 4 个空 reference 无 terminal |
| 人工约束 | 217 | 均为 `state_snapshot:custom` |

25 个 case 的处理耗时合计为 773,887 ms，单 case 平均 30,955 ms，范围为 17,286—53,249 ms。实验没有 `generation_failed` 或超时记录，说明 provider 调用链整体返回了非空内容；问题集中在返回文本不能被当前严格 JSON 入口接受。

### 3.2 按任务类型看人工 reference 的待覆盖规模

分类仅用于说明当前丢失了哪些类型的评测覆盖，不代表已测得对应类型的语义错误。

| 任务类型 | case 数 | 人工节点 | 人工边 | 人工约束 | 当前可比较 case |
|---|---:|---:|---:|---:|---:|
| 信息不足 | 5 | 1 | 0 | 1 | 0 |
| 多轮任务 | 4 | 14 | 10 | 56 | 0 |
| distraction/参数扰动 | 8 | 22 | 14 | 68 | 0 |
| 环境前置条件 | 4 | 15 | 11 | 51 | 0 |
| 其他常规单轮任务 | 4 | 12 | 8 | 41 | 0 |

其中 5 个信息不足 case 包含 4 张人工空图，以及 `remove_contact_by_phone_no_remove_contact_insufficient_information` 的 1 节点图。当前编译协议要求 `atoms` 为非空数组，并要求共识产生 terminal milestone，因此即使 JSON 协议恢复，**四个合法人工空图仍缺少明确的生成表达方式**。这是协议修复之后马上会暴露的第二个 P0 问题。

### 3.3 逐 case 核查

表中的“prediction”均为“未产出”，不能按 0 节点图参与 GED。

| case | 人工节点/边 | 人工约束 | 当前状态 | 有效路径 | prediction |
|---|---:|---:|---|---:|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 2/1 | 8 | rejected | 0/6 | 未产出 |
| `add_reminder_content_and_date_and_time` | 2/1 | 5 | rejected | 0/6 | 未产出 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | 2/1 | 5 | rejected | 0/6 | 未产出 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | 2/1 | 5 | rejected | 0/6 | 未产出 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | 2/1 | 5 | rejected | 0/6 | 未产出 |
| `find_days_till_holiday` | 4/3 | 18 | rejected | 0/6 | 未产出 |
| `find_days_till_holiday_insufficient_information` | 0/0 | 0 | rejected | 0/6 | 未产出 |
| `find_days_till_holiday_wifi_off_alt` | 5/4 | 22 | rejected | 0/6 | 未产出 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 5/4 | 18 | rejected | 0/6 | 未产出 |
| `modify_contact_with_message_recency_insufficient_information` | 0/0 | 0 | rejected | 0/6 | 未产出 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 0/0 | 0 | rejected | 0/6 | 未产出 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 0/0 | 0 | rejected | 0/6 | 未产出 |
| `modify_reminder_with_recency_latest` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 1/0 | 1 | rejected | 0/6 | 未产出 |
| `search_message_with_recency_oldest_multiple_user_turn` | 3/2 | 12 | rejected | 0/6 | 未产出 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | 3/2 | 12 | rejected | 0/6 | 未产出 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | 3/2 | 12 | rejected | 0/6 | 未产出 |
| `send_message_with_contact_content_cellular_off` | 4/3 | 11 | rejected | 0/6 | 未产出 |
| `turn_on_cellular_low_battery_mode` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `turn_on_location_low_battery_mode` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `update_contact_relationship_with_relationship` | 3/2 | 9 | rejected | 0/6 | 未产出 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 5/4 | 20 | rejected | 0/6 | 未产出 |

## 4. 失败链路与根因判断

### 4.1 已被结果直接证明的事实

当前每个 case 独立请求以下六种策略：

1. `direct-shortest`
2. `prerequisite-first`
3. `state-check-first`
4. `artifact-or-result-first`
5. `alternative-tool`
6. `verification-first`

150 条路径的 `path_summaries` 全部记录：

```text
status = rejected
reason = LLM milestone 返回必须是 JSON 对象
atom_count = 0
minefield_count = 0
```

随后每个 case 都因 `requested=6, minimum=4, actual=0` 被整体拒绝。因此失败顺序为：

```text
LLM 返回非空文本
  → json.loads(raw) 无法得到所需 JSON 对象
  → 单条路径在 atom schema 校验前被拒绝
  → 6 条路径全部失效
  → 有效路径数 0 < 4
  → 整个 case generation_rejected
  → 不执行 GED/F1
```

任务内容、干扰工具数量、参数描述/类型扰动和路径策略都没有改变失败形态，因而当前结果不支持“某一类任务更难”的结论。

### 4.2 高概率根因：结构化输出没有在 LLM 边界强制执行

代码中存在三个相互放大的事实：

1. `compiler.py::_simulate_path()` 调用 `llm.chat(...)` 时没有传入 `response_format="json_object"`。
2. `openai.py` 已支持把该参数转换为 OpenAI-compatible 的 `{"type": "json_object"}`，但 milestone 调用没有使用这一能力。
3. 当前中英文 prompt 在“只返回 JSON”之后，都用 Markdown ` ```json ... ``` ` 代码围栏展示输出 schema；而 `_parse_path_response()` 直接执行 `json.loads(raw)`，不会接受围栏、解释正文或其他包裹。

因此最可能的实际情况是模型跟随 prompt 示例返回了带代码围栏或说明文字的 JSON，严格解析器将其拒绝。这个判断与 150/150 的一致失败高度吻合，也能解释为何 provider 调用成功而 atom 计数始终为 0。

但当前结果**没有保存原始 LLM 回复**，所以无法确认实际返回究竟是代码围栏、前后正文、截断 JSON、JSON 数组，还是其他非法文本。根因应标记为“高概率、待原始响应证实”，不能写成已确认事实。

### 4.3 当前错误报告还混合了不同故障

`json.loads(raw)` 的语法错误和输入类型错误统一变成“返回必须是 JSON 对象”；合法 JSON 但顶层字段不符则变成另一条错误。当前逐 case 结果没有记录：

- 返回字符长度；
- 首个非空字符与末尾字符类型；
- 是否检测到 Markdown fence；
- `JSONDecodeError` 的行、列和 message；
- 原始响应的安全摘要或可选审计文件。

因此现有产物能定位到“最外层解析失败”，但不足以完成最后一步根因闭环。

## 5. 与历史批次的差距及仍然存在的语义问题

2026-08-07 的同名实验历史审计记录了另一批代码状态下的结果：19/25 个 case 生成成功，成功覆盖率 76%，成功 case 平均严格 GED 相似度 0.4073，生成失败按 0 计的有效平均相似度为 0.3095。当前批次对照如下：

| 指标 | 2026-08-07 历史批次 | 2026-08-10 当前批次 | 变化 |
|---|---:|---:|---:|
| 生成成功 case | 19/25 | 0/25 | -19 |
| 生成成功率 | 76% | 0% | -76 pp |
| 成功 case strict GED 均值 | 0.4073 | 不可计算 | 指标覆盖归零 |
| 可计算 GED case | 19 | 0 | -19 |

该对照说明当前重构发生了协议层回归，但不能说明恢复 JSON 后语义质量会优于或劣于旧批次。根据历史结果，协议恢复后仍需面对以下问题：

- 信息不足任务倾向于错误构造执行路径；
- 多轮任务和“查找→筛选→变更→确认”任务容易被压缩，遗漏人工阶段；
- base、distraction、参数描述/类型扰动之间的图不稳定；
- 自动生成的公开 `tool_call/tool_result` 证据与人工 `state_snapshot:custom` reference 存在表示空间差异；
- strict 节点标签完全相等过于苛刻，历史节点 F1 为 0 不能直接解释为语义 F1 为 0。

所以当前不是“只修一个 JSON parser 就完成 milestone 优化”，而是 JSON 协议错误挡住了后续语义问题的测量。

## 6. 改进建议与优先级

### P0-A：先修复 LLM 输出协议

1. milestone 生成调用应显式请求结构化输出；对当前 OpenAI-compatible Qwen 路径，优先在 LLM 边界传入 `response_format="json_object"`。
2. 删除 prompt 输出示例外层的 Markdown 代码围栏，保留纯 JSON schema 文本；prompt 与 parser 必须表达同一个协议。
3. 不建议在 compiler 中宽松提取任意正文里的 `{...}`。这会掩盖 prompt injection、截断和多对象回复。若确需兼容单层 Markdown fence，应只在统一 LLM 响应边界做一次、规则明确的解包，并对发生次数做指标统计。
4. 将“provider 调用成功”和“结构化响应通过”分成两个指标：`llm_call_success_rate`、`json_parse_success_rate`、`schema_validation_success_rate`。
5. JSON/Schema 解析失败时允许一次有界重试，并把原错误类型反馈给模型；不要让每条路径无限重试，也不要以自动填字段掩盖非法输出。

### P0-B：补齐最小测试和预跑门禁

1. 为 `_parse_path_response()` 增加纯 JSON、代码围栏、前后正文、截断 JSON、合法数组、缺字段、额外字段等单元测试。
2. 使用 fake LLM 验证 milestone 调用确实把 `response_format` 传到 provider 边界。
3. 完整 25-case 实验前先跑 1 个常规 case、1 个信息不足 case、1 个多轮 case；只有 JSON parse 成功率达到 100% 才扩大运行。
4. 对全量实验设置快速失败门禁：前 2 个 case 若连续 12/12 条路径发生相同的顶层解析错误，应停止批次并报告协议故障，避免继续消耗 138 次无效调用。

### P0-C：让空图和不可执行任务成为一等输出

1. 在路径响应顶层增加明确任务处置，例如 `disposition=executable|needs_clarification|no_action`，不能只依赖非空 `atoms`。
2. 当多条独立路径对 `needs_clarification/no_action` 达成共识时，允许生成合法空 milestone graph；不能把空图当作生成失败。
3. 对 `remove_contact...insufficient_information` 这类人工 1 节点任务，先统一“拒绝/澄清行为是否构成 milestone”的 benchmark 口径，再决定生成节点。
4. 将 5 个信息不足 case 作为独立回归集，首先检查 disposition/空图类别，其次才检查路径数。

### P1-A：恢复协议后优化节点和拓扑

1. 显式建模“前置状态检查→实体查找/筛选→业务变更→结果确认”的阶段角色，减少复杂任务被压缩为一两个节点。
2. 多轮任务按用户轮次保留顺序边和每轮终态，重点覆盖 `search_message...multiple_user_turn` 与 `update_contact...twice_multiple_user_turn`。
3. 区分必要前置条件与可选验证，不要为满足固定路径数制造不存在的步骤。
4. 路径数量下限应与任务可执行性和公开 evidence 数量相关；简单线性任务或合法空图不应强制凑出 4 条有效差异路径。

### P1-B：提升扰动一致性

1. 对共享人工图的 base、3/10 distraction、arg-description/type-scrambled 变体增加图一致性指标。
2. 先从工具 schema 中筛出任务相关 evidence，再生成路径；不要让无关工具数量直接改变 milestone 节点数。
3. 如果同族变体在“空图、拒绝、不同节点数”间跳变，直接标记鲁棒性失败，不等到总体 GED 汇总后再发现。

### P1-C：改进与人工标注的评价口径

1. 保留 strict GED 作为完整 descriptor 一致性指标，但不能把它单独称为语义相似度。
2. 分层报告无标签拓扑 GED、节点/边数量误差、route/terminal 一致性、constraint target/operator 一致性和 expected 一致性。
3. 对公开 `tool_call/tool_result` 与私有 `state_snapshot:custom` 建立不泄漏 benchmark 答案的语义映射，无法从公开视图观察的人工节点标为 `unobservable_from_public_view`。
4. 最终应在同一真实轨迹上同时执行人工图和生成图，对比逐 step 命中序列、最终 coverage、virtual stop 决策和任务排序一致性；这比 descriptor 完全相等更接近“能否替代人工 milestone”。

### P1-D：补齐结果可审计性

1. 每个 case 保存规范化的 prediction/reference descriptor 和边，拒绝 case 也保存到达 parser 前后的诊断摘要。
2. 默认至少保存安全诊断：响应长度、SHA-256、首尾 token 类别、是否含 fence、JSON 错误位置；需要保存原始响应时使用显式调试开关，并避免将敏感任务数据写入常规日志。
3. 为每条路径记录 `llm_returned`、`json_parsed`、`schema_validated`、`atom_validated`、`path_validated` 五级状态，避免所有问题都折叠成 `rejected`。

## 7. 建议的分阶段验收标准

### 阶段 1：协议恢复

- 25-case、150 条路径的 provider 调用成功率不低于 99%；
- JSON parse 成功率不低于 99%；
- 不再出现全批次同构解析错误；
- 失败产物可以明确区分 syntax、schema、evidence 和 consensus 问题。

### 阶段 2：任务覆盖恢复

- 25/25 个 case 都产出可评测结果，包括合法空图；
- 5 个信息不足 case 的空图/单节点类别与人工 reference 一致；
- base 与扰动同族不再出现生成状态跳变。

### 阶段 3：语义和结构质量

- 在 descriptor 严格指标之外，至少提供结构、约束字段和真实轨迹命中三层指标；
- 多轮、环境前置和扰动 case 分层报告，不只给总体均值；
- 同一配置至少重复 3 次，报告生成成功率和图一致性，避免单次结果被服务端波动主导。

## 8. 最终判断

当前 DynSTEER milestone 生成结果尚不能与人工标注进行有效的节点级或图级比较。最准确的表述不是“与人工标注相差多少 GED”，而是：

- **可交付覆盖差距：100%（25/25 没有可用 prediction）；**
- **当前语义/结构差距：不可测（GED/F1 样本数为 0）；**
- **首要改进：修复并测试结构化输出协议；**
- **协议恢复后的核心改进：合法空图、任务阶段粒度、多轮/DAG、扰动一致性、语义评价与审计产物。**

在完成 P0-A 至 P0-C 并复跑同一 25-case 配置之前，不应使用当前 summary 的空指标评价 DynSTEER milestone 算法本身，也不应扩大到完整 ToolSandbox 数据集。

## 附录 A：证据边界与未能确认的部分

本次最无法确认的是 150 条原始 LLM 回复的实际内容。当前结果只保存统一后的解析错误，没有保存原始文本或足以区分 fenced JSON、解释正文和截断 JSON 的诊断。因此：

- “prompt 中的 Markdown fence 被模型复制”是高概率根因假设，不是已经由原始响应证实的事实；
- 当前无法人工复核任何生成 atom 的语义，因为 atom 解析计数为 0 且原始响应缺失；
- 历史批次的 0.4073 GED 只能作为回归背景，不能代表当前代码在修复协议后的预期成绩。

这些不确定性本身说明结果可审计性需要作为生成质量的一部分建设，而不能只保存最终 GED 汇总。
