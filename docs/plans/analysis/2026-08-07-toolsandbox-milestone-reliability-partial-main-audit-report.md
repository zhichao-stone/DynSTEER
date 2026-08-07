# `toolsandbox_milestone_reliability_partial_main` Milestone 生成核查报告

## 1. 核查范围与结论摘要

本报告核查以下对象：

- 实验配置：`data/experiments/toolsandbox_milestone_reliability_partial_main.json`
- 实验索引与汇总：`results/milestone/toolsandbox_milestone_reliability_partial_main/index.json`、`summary.json`
- 25 个逐 case 结果：`results/milestone/toolsandbox_milestone_reliability_partial_main/toolsandbox/*.json`
- 人工 reference：ToolSandbox 原生 scenario 经 `milestone_graph_from_scenario()` 转换后的 milestone graph
- 对比实现：`milestone_reliability.py` 的 `_graph_descriptor()`、`_compute_ged()` 与 `_node_set_f1()`

核心结论如下：

1. **当前生成结果距离“可稳定替代人工 milestone”仍较远。** 25 个 case 仅 19 个生成成功，成功覆盖率为 76%，有 6 个 case（24%）在编译阶段被拒绝。成功 case 的平均严格 GED 相似度为 0.4073，即平均归一化差距为 59.27%；若把生成失败按相似度 0 计入，25 个 case 的有效平均相似度只有 0.3095，有效差距为 69.05%。
2. **严格节点标签没有任何完全匹配。** 19 个成功 case 中，prediction 共 49 个节点、reference 共 50 个节点，零代价匹配节点为 0，`node_set_f1` 全部为 0。但这不能简单解释为“语义完全错误”：当前 reference 使用 ToolSandbox 私有 `state_snapshot/custom` 约束，prediction 只能基于公开证据生成 `step/tool_call/tool_result` 约束；严格指标还把名称、描述和路由纳入整段 JSON 完全相等比较，存在明显的表示空间不一致。
3. **节点总量接近是平均数假象。** 49 与 50 看似接近，但仅 4/19（21.1%）成功 case 的节点数相等；8 个生成偏少、7 个生成偏多。GED 编辑路径合计需要补 14 个 reference 节点、删 13 个 prediction 多余节点、替换 36 个节点，并补 14 条、删 11 条边。
4. **信息不足任务处理错误最明确。** 4 个 reference 为空图的信息不足 case 中，3 个生成出 2 至 3 个节点且相似度为 0，另 1 个生成被拒绝。生成器尚未学会先判定“任务是否具备可执行信息”，而是倾向于为可见工具构造虚假执行路径。
5. **同义扰动下不稳定。** 人工图相同或同构的 base、distraction、参数描述/类型扰动变体，生成结果会在“拒绝、1 节点、2 节点、3 节点、4 节点”之间变化；部分干扰版本反而显著优于 base，说明结果仍受 prompt/tool 列表表面形态影响，而非稳定抓住任务语义。
6. **近期 prompt 重构能处理一部分 schema/path 拒绝，但不能解决全部差距。** 当前工作区已有 `2026-08-07-milestone-generation-prompt-redesign-plan.md` 及对应未提交实现，主要针对 allowlist、terminal、重复 atom 和路径数自检。这有望降低 24% 的生成拒绝率；但空图判定、公开证据与私有 reference 的表示鸿沟、节点粒度和多轮/分支拓扑仍需单独改进。

本报告评价的是 `index.json` 记录的 2026-08-07 07:42:58Z 至 07:51:40Z 这一批结果。当前工作区的 prompt/compiler 未提交修改尚未重新运行本实验，因此不能用本报告中的数值评价这些新修改是否已经生效；修改后必须用同一 25-case 配置复跑做前后对照。

## 2. 差距量化

### 2.1 总体指标

| 指标 | 结果 | 含义 |
|---|---:|---|
| 总 case 数 | 25 | partial 主样本 |
| 成功生成 | 19（76.0%） | 进入 GED 比较 |
| 生成拒绝 | 6（24.0%） | 均因有效差异路径不足 |
| 成功 case 平均 GED 相似度 | 0.4073 | 平均严格图差距 59.27% |
| 成功 case GED 中位数 | 0.4000 | 典型 case 仍需约 60% 归一化编辑 |
| 成功 case GED 范围 | 0.0000–0.7000 | 没有 case 达到 0.75 |
| 全 25 case 有效平均相似度 | 0.3095 | 失败按 0 计，有效差距 69.05% |
| 严格节点集合 F1 | 0.0000 | 49 个 prediction 节点中无完全同标签节点 |
| 成功 case 节点数一致 | 4/19（21.1%） | 8 个偏少、7 个偏多 |
| 成功 case reference/prediction 节点 | 50/49 | 总量接近但逐 case 分配错误 |

19 个成功 case 的 GED 相似度分布为：

- `[0, 0.25)`：4 个；
- `[0.25, 0.50)`：6 个；
- `[0.50, 0.75)`：9 个；
- `[0.75, 1.00]`：0 个。

编辑距离合计为 88，其中 36 次节点替换占 40.9%，27 次节点增删占 30.7%，25 次边增删占 28.4%。这说明问题不只在节点文本标签：即使暂时忽略标签完全匹配，节点粒度和前后依赖关系也存在大量偏差。

### 2.2 逐 case 对照

下表中 `N(+/−/~)` 与 `E(+/−/~)` 表示把 prediction 转为 reference 时的节点/边“插入、删除、替换”次数；插入表示生成结果缺少人工项，删除表示生成结果存在多余项。

| case | 状态 | reference/prediction 节点 | GED 相似度 | N(+/−/~) | E(+/−/~) | 核查判断 |
|---|---|---:|---:|---:|---:|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 成功 | 2/1 | 0.250 | 1/0/1 | 1/0/0 | 压缩为单节点，遗漏一个人工阶段及其依赖边。 |
| `add_reminder_content_and_date_and_time` | 拒绝 | 2/— | — | — | — | 仅 4 条有效路径，低于 5 条门槛；同义干扰版本却可成功。 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | 成功 | 2/2 | 0.667 | 0/0/2 | 0/0/0 | 数量与拓扑匹配，但两个节点标签均不完全一致。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | 成功 | 2/4 | 0.400 | 0/2/2 | 0/2/0 | 将工具证据拆得过细，多生成两个节点和两条边。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | 成功 | 2/4 | 0.400 | 0/2/2 | 0/2/0 | 与对应 3-distraction 结果一致，但仍过度拆分。 |
| `find_days_till_holiday` | 成功 | 4/2 | 0.400 | 2/0/2 | 2/0/0 | 遗漏人工图中的两个准备/中间阶段及两条依赖。 |
| `find_days_till_holiday_insufficient_information` | 成功 | 0/2 | 0.000 | 0/2/0 | 0/1/0 | 人工为空图，生成器错误构造可执行路径。 |
| `find_days_till_holiday_wifi_off_alt` | 拒绝 | 5/— | — | — | — | 6 条路径全部无效：5 条未以 terminal 结束，1 条重复 atom。 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 成功 | 5/3 | 0.500 | 2/0/3 | 2/0/0 | 复杂替代路径被压缩，缺少两个人工节点和两条边。 |
| `modify_contact_with_message_recency_insufficient_information` | 成功 | 0/3 | 0.000 | 0/3/0 | 0/2/0 | 空图任务错误生成 3 节点链。 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 成功 | 0/2 | 0.000 | 0/2/0 | 0/1/0 | 空图任务错误生成 2 节点链。 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 拒绝 | 0/— | — | — | — | 6 条路径全部未以 terminal 结束；同族三个版本给出三种不同结果。 |
| `modify_reminder_with_recency_latest` | 成功 | 3/2 | 0.500 | 1/0/2 | 1/0/0 | 缺少一个查找/选择阶段及依赖边。 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 拒绝 | 1/— | — | — | — | 只有 1 条有效路径；未稳定表达“不能删除、应澄清/保持状态”。 |
| `search_message_with_recency_oldest_multiple_user_turn` | 成功 | 3/1 | 0.167 | 2/0/1 | 2/0/0 | 多轮三阶段被压成单节点，是成功 case 中最严重的结构压缩之一。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | 成功 | 3/4 | 0.583 | 0/1/3 | 0/1/0 | 干扰版反而补足阶段，但多出一个节点和一条边。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | 成功 | 3/3 | 0.700 | 0/0/3 | 0/0/0 | 数量与拓扑匹配，是本实验最好结果之一，但节点标签仍全不匹配。 |
| `send_message_with_contact_content_cellular_off` | 成功 | 4/2 | 0.400 | 2/0/2 | 2/0/0 | 未表达网络状态恢复/准备阶段，结构被压缩。 |
| `turn_on_cellular_low_battery_mode` | 成功 | 3/4 | 0.538 | 0/1/3 | 0/2/0 | 多生成节点和依赖，未与三阶段人工链对齐。 |
| `turn_on_location_low_battery_mode` | 拒绝 | 3/— | — | — | — | expected 越过 allowlist 后引发未知 atom，并且有效路径仅 3 条。 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | 成功 | 3/3 | 0.700 | 0/0/3 | 0/0/0 | 数量与拓扑匹配，但 base 版本反而失败，鲁棒性不足。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | 拒绝 | 3/— | — | — | — | 2 条路径含重复 atom，最终仅 4 条有效路径。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | 成功 | 3/3 | 0.700 | 0/0/3 | 0/0/0 | 参数类型扰动版成功且拓扑匹配，与 base/描述扰动版形成反常波动。 |
| `update_contact_relationship_with_relationship` | 成功 | 3/2 | 0.500 | 1/0/2 | 1/0/0 | 缺少一个中间阶段。 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 成功 | 5/2 | 0.333 | 3/0/2 | 3/0/0 | 两轮顺序操作被压成两节点，缺少三个节点和三条边。 |

## 3. 生成失败与编译质量

6 个失败 case 都是 `generation_rejected`，没有 LLM 调用失败、适配失败或 GED 求解失败。直接失败条件均为“有效差异路径少于配置要求”，有效路径数分别为 4、0、0、1、3、4。

25 个 case 的 generation report 共记录：

| 校验问题 | 出现次数 | 涉及 case 数 | 影响 |
|---|---:|---:|---|
| minefield schema 不合法 | 91 | 19 | 大多不阻断主图，但说明 minefield 输出几乎系统性失效。 |
| 路径未以 terminal atom 结束 | 11 | 2 | 两个 case 的路径整体被淘汰，是直接拒绝主因。 |
| 有效差异路径不足 | 6 | 6 | 所有拒绝 case 的最终错误。 |
| 路径包含重复 atom | 5 | 4 | 两个边界 case 因少一至两条有效路径而失败。 |
| 路径引用未知 atom | 2 | 1 | 来自上游 atom 被拒绝后的级联错误。 |
| expected 不在 evidence allowlist | 1 | 1 | 公开证据复制不精确。 |

这里应区分两类问题：terminal、重复 atom、未知引用和 allowlist 属于模型输出契约问题，可以通过 prompt 自检和更清晰的输入表显著缓解；“必须构造至少 5 条不同路径”则可能与任务本身不匹配。简单设置任务或信息不足任务未必存在 5 条有语义的执行路径，强迫模型凑数会制造冗余节点和伪路径。

## 4. 人工 reference 与 prediction 的表示鸿沟

### 4.1 严格 F1 为 0 的原因

人工 reference 的节点具有以下特征：

- 名称、描述均为 `ToolSandbox milestone N`；
- 约束目标统一为 `state_snapshot`，operator 统一为 `custom`；
- `expected` 是 ToolSandbox 原生 scorer 所需的私有结构化对象；
- matching route 包含 `environment→agent`、`agent→user` 或空路由；
- 单个节点可能聚合 1 至 10 条底层 snapshot/guardrail/tool-trace 约束。

自动生成器的公开 evidence catalog 只允许生成：

- 用户指令 `step/$.content/fuzzy_match`；
- 工具调用 `tool_call/$.name/equals`；
- 工具结果存在性 `tool_result/$/added`；
- 从公开系统消息提取的 invariant。

`_graph_descriptor()` 又把名称、描述、route、terminal 和完整 constraints 一起编码为 canonical JSON，并以完全相等作为零成本替换条件。两侧在约束目标、operator、expected 形状和命名上天然不同，因此严格节点 F1 更接近“序列化表示完全一致率”，不是语义召回率。

### 4.2 当前结果能说明与不能说明的内容

当前结果能够可靠说明：

- 生成是否通过编译契约；
- 节点数和有向拓扑是否接近人工图；
- prediction 与 reference 是否达到完整 descriptor 一致。

当前结果不能可靠说明：

- 一个 `tool_call` 节点是否在语义上对应某个人工 `state_snapshot/custom` 节点；
- 自动图在真实轨迹上是否能复现人工 milestone 的命中时刻和覆盖率；
- 名称或描述不同但检查同一任务事实的节点是否等价；
- 被拒绝 case 若保留其原始候选图，离人工图究竟有多远。

此外，逐 case 产物没有保存 prediction/reference 的节点 descriptor，仅保存计数、编辑路径中的 ID 和 generation report。因而本次只能审计严格指标和人工 source，无法从结果目录对 49 个生成节点做逐条人工语义复核。这是实验可审计性的直接缺口。

## 5. 需要改进的方面

### P0：先修复空图与可执行性判定

1. 在 atom/path 生成前增加任务可执行性分类：`executable`、`needs_clarification`、`no_action_or_refusal`。信息不足且人工定义为空图的任务应允许直接输出空 graph，而不是从可见工具名推导执行链。
2. 明确“澄清用户”和“保持状态”是否属于 milestone。当前四个空图 reference 与 `remove_contact...` 的单节点 reference 口径不同，必须先固化 benchmark 语义，再训练/提示生成器。
3. 对上述 5 个信息不足 case 建立独立回归集；验收标准首先是空图/单节点类别正确，其次才是路径多样性。

### P0：让生成契约与任务复杂度匹配

1. 不应对所有 case 固定要求至少 5 条不同路径。先根据可用 evidence 和任务结构估计可支持路径数；简单线性任务允许 1 至 2 条，只有存在真实可交换步骤或替代工具时才增加路径数。
2. 路径共识不应把“出现于多个伪路径”当作节点重要性的主要证据，否则凑数路径会放大多余工具节点。
3. 保留严格 terminal、未知 atom、重复 atom 和 allowlist 校验；这些是正确的安全边界，不应用自动修补掩盖模型错误。

### P0：重做可解释的比较指标

1. 保留当前 strict GED 作为“完整 descriptor 一致性”指标，但改名或在报告中明确为 `strict_descriptor_ged`，不能单独代表语义可靠性。
2. 增加分层指标：节点数量误差、无标签拓扑 GED、route/terminal 一致性、约束 target/operator 一致性、expected 值一致性；避免一个名称差异让整个节点记为完全不匹配。
3. 建立人工/规则映射，把公开 `tool_call/tool_result` 证据与 ToolSandbox 私有 `state_snapshot` milestone 对齐；无法对齐的部分明确标为 `unobservable_from_public_view`，而不是全部计为生成错误。
4. 最关键的有效性检查是把同一批真实轨迹分别用人工图和生成图执行，比较逐 step milestone 命中序列、最终 coverage、virtual stop 决策和任务排序一致性。只有运行行为接近，才能说明生成图可替代人工图。

### P1：提升节点粒度、分支和多轮结构

1. 对“查找/筛选→变更→确认”类任务显式建模阶段角色，避免 `find_days_till_holiday`、`modify_reminder...` 等被压缩。
2. 对多轮任务按用户轮次和每轮终态建立顺序边，重点修复 `search_message...multiple_user_turn` 和 `update_contact...twice_multiple_user_turn`。
3. 对网络、低电量等环境前置条件，区分“状态检查”“状态变更”“业务动作”“用户反馈”，避免遗漏准备阶段或把所有可见工具各拆成一个节点。
4. 对具有并行前置条件的人工图保留 DAG 分支，不要强制压成单链；节点合并应依据相同可观察事实，而不是路径共现次数。

### P1：提升扰动鲁棒性

1. 对相同人工图的 base、3/10 distraction、arg-description/type-scrambled 变体增加一致性损失或回归断言：可用工具顺序和无关工具数量变化不应显著改变 milestone 图。
2. 生成输入中将“任务相关候选证据”和全部可见工具分离；先做与用户目标的关联筛选，再让模型构图，降低 distraction 工具直接膨胀 atom 数量的概率。
3. 报告同族变体的 graph consistency，而不只报告各自对 gold 的分数。当前 base 失败而扰动版达到 0.7 的反常情况，应直接触发鲁棒性告警。

### P1：修复 minefield 和结果可审计性

1. 19 个 case 共 91 个 minefield schema 错误，说明当前 minefield 生成接口基本不可用。若 minefield 不是本轮生成目标，应停止要求模型生成；若必须生成，应像 evidence 一样提供封闭的 invariant/expected 表并增加输出前自检。
2. 每个 case 结果至少保存规范化的 prediction/reference 节点 descriptor 与边，或保存稳定 digest 并提供独立 descriptor 文件。否则无法解释某次节点替换具体差在哪个字段。
3. 对拒绝 case 保存原始候选的脱敏结构和每条路径的拒绝原因，使失败样本也能参与“接近程度”分析。

### P2：扩大样本与重复实验

1. 当前仅 25/509 case、单模型、单次生成，不能估计总体可靠性和随机波动。修复 P0 后再跑 25-case 回归，随后跑完整 509 case。
2. 即使 temperature=0，服务端模型版本和生成仍可能变化；建议至少 3 次重复并报告生成成功率、同 case 图一致性和置信区间。
3. 按信息不足、多轮、环境前置、distraction、参数扰动等类型分层汇总，避免总体均值掩盖结构性失败。

## 6. 本次 `index.json` 精简实施方案

用户指定从 milestone reliability 的 `index.json` 删除以下字段：

- `experiment_config_sha256`
- `networkx_version`
- `ignored_matrix_dimensions`
- `run_id`
- `schema_version`

具体修改范围：

1. 修改 `milestone_reliability.py::_write_report()`，不再接收只为 index 使用的 `run_id` 参数，并从 index payload 删除上述 5 个字段。
2. 删除不再使用的 `_file_digest()`。`hashlib` 仍被 `_digest()` 使用，因此保留 import。
3. `run_id` 仍保留在逐 case 文件中。本次需求明确限定为 `index.json`；case 文件的 `run_id` 用于标识同一批重跑覆盖产生的 case，`schema_version` 也仍属于 case/summary 自身协议。若要连同 case/summary 一并删除，应作为独立 schema 变更处理，不能从“index 精简”外推。
4. 同步精简本次已有结果目录中的 `index.json`，使现有产物与新代码一致。
5. 验收：执行 Python 语法编译、静态字段搜索和最小序列化断言，确认 index 不再输出 5 个字段且其余字段保持不变。

## 7. 验收标准

本次报告与代码精简完成需满足：

- 报告可从现有 25 个 case JSON 复算所有核心数值；
- 不把 strict `node_set_f1=0` 误解为语义绝对为 0；
- 明确区分生成失败、结构差距和评价口径差距；
- `milestone_reliability.py` 无语法错误；
- 新生成及现有实验 `index.json` 均不含指定 5 个字段；
- 不改动用户当前对 milestone compiler、model、prompt 和 API 文档的未提交修改。

## 8. 本次实施与验收结果

已完成以下修改：

- `milestone_reliability.py` 的 index payload 已删除指定 5 个字段；
- `_write_report()` 已删除只服务于 index 的 `run_id` 形参及调用实参；
- 已删除不再使用的 `_file_digest()`；
- 现有 `results/milestone/toolsandbox_milestone_reliability_partial_main/index.json` 已同步精简；
- summary 和逐 case schema 未扩大修改范围。

已通过以下检查：

- `ast.parse()` 语法检查通过；
- 现有 index JSON 可正常解析，且指定 5 个字段均不存在；
- 临时调用 `_write_report()` 生成新 index，指定 5 个字段均不存在，保留的 experiment、seed、group 信息正确；
- `milestone_reliability.py` 中已无 `_file_digest`、index schema、配置哈希、NetworkX 版本和 ignored matrix 处理；保留的 `run_id` 仅属于逐 case 输出。

## 附录A. 项目中没有把握实现的模块部分

本次没有把握直接落地的是“公开 evidence 节点与 ToolSandbox 私有 state snapshot 人工节点的语义对齐器”。原因是现有结果未保存 prediction descriptor，且公开视图有意隔离了 benchmark 私有 expected/scorer 结构；若直接用私有标签训练或注入生成器，会造成 benchmark 泄漏。该部分需要先确定允许使用的监督边界，并通过真实轨迹命中行为验证映射，而不能仅凭节点名称猜测。

本次只按明确需求落地 `index.json` 精简，不擅自修改上述生成算法或评价协议；改进项作为后续开发依据。
