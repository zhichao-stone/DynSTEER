# 2026-08-03 `toolsandbox_partial_main` 评估结果与运行记录核查报告

## 1. 核查范围与判定口径

本报告只分析当前工作区已有实验产物，不重新调用模型或运行 benchmark。数据来源如下：

- 实验配置：`data/experiments/toolsandbox_partial_main.json`
- 聚合索引：`results/exp/toolsandbox_partial_main/index.json`
- 模型得分：`results/exp/toolsandbox_partial_main/scores.json`
- 聚合指标：`results/exp/toolsandbox_partial_main/metrics.json`
- 逐 case 评估结果：`results/exp/toolsandbox_partial_main/toolsandbox/...`
- 逐 case 运行记录：`runs/exp/toolsandbox_partial_main/toolsandbox/...`
- 适配后的 case 定义：`data/toolsandbox/adapted_cases/*.json`

实验矩阵为 4 个模型 × 2 种方法 × 25 个 case × 1 次重复，共 200 条结果、100 对可直接配对的 DEFAULT / DYNSTEER_REPLAY 记录。

成功口径采用各方法自身的一等结果字段：

- DEFAULT：`resolved=true`。当前 100 条 DEFAULT 记录中，`resolved=true` 与 `overall_score == 1.0` 完全等价。
- DYNSTEER_REPLAY：`milestone_coverage=full`。`partial` 和 `none` 均按未成功处理。

不能用同一个数值阈值直接比较两种方法的 `overall_score`，因为 DEFAULT 是 ToolSandbox 原生 snapshot similarity，DYNSTEER_REPLAY 是阶段、维度、终局与 minefield 的复合评分，两者并非同一量尺。

模型简称：`DP` = deepseek-v4-pro，`DF` = deepseek-v4-flash，`QP` = qwen-plus-2025-12-01，`QM` = qwen3-max-2026-01-23。

## 2. 总体结论

1. **产物覆盖完整，主体流程成功结束。** 200 条索引记录和 800 个逐 case 核心 JSON 文件全部存在且可解析；task/case 标识、step/snapshot 数量、DEFAULT 引用分数和聚合索引均一致；100 条 replay 均有可用 timing，Judge LLM 调用失败数为 0。
2. **成功/失败并不完全一致。** 100 对中 88 对一致、12 对不一致；12 对全部是 DEFAULT 成功而 replay 未达到 `full`，没有 DEFAULT 失败而 replay 成功的情况。replay 对 DEFAULT 成功的保留率只有 `19/31 = 61.3%`。
3. **分歧主要是评估语义问题，不是 agent 执行轨迹问题。** 12 个分歧的 `trajectory_changed` 全部为 `false`。其中 10 个来自空 milestone graph 的信息不足/用户后续澄清场景，另外 2 个来自对 `get_current_timestamp` 特定工具调用的过程性硬约束。
4. **当前 replay 的模型区分度结论是混合且不稳定的。** 模型均分 PSEP 只从 `0.033613` 增至 `0.033865`，仅增加 0.75%；逐 case 的模型间平均分散度有所增加，但模型排序相对 DEFAULT 的 Kendall tau-b 为 `0.0`，四模型排序发生明显重排。单次重复也不足以判断 Judge 稳定性。
5. **当前 replay 不能证明效率更优。** 表面均值显示有效耗时比 DEFAULT 低 30.3%，但这个均值被一条 1411.6 秒 DEFAULT 异常值主导。中位数上 replay 有效耗时高 47.8%；逐对比较有 68/100 条更慢；移除该异常值后 replay 平均有效耗时高 15.7%。
6. **当前 replay 成本更高且成本账不完整。** 它新增 72 次 Judge LLM 调用和 401,715 tokens；Agent trajectory token 全部为 0 且 `trajectory_cost_available=false`，无法证明早停节省的 Agent 成本超过 Judge 增量成本。
7. **最终判断：当前 DYNSTEER_REPLAY 不优于 DEFAULT，不能作为 DEFAULT 的直接替代。** 它适合作为更丰富的诊断/安全审计层，但在语义对齐、排序稳定性、时延和成本上尚未达到替代条件。

## 3. 产物完整性与运行流程核查

### 3.1 完整性检查

| 检查项 | 结果 |
|---|---:|
| `index.json.case_count` | 200 |
| 实际逐 case 索引数 | 200 |
| `summary.json` | 200 / 200 |
| `raw_summary.json` | 200 / 200 |
| `trajectory.json` | 200 / 200 |
| `default_report.json` | 100 / 100 |
| replay `report.json` | 100 / 100 |
| JSON 解析失败 | 0 |
| task_id / case_id 不一致 | 0 |
| trajectory step 数与 runtime metric 不一致 | 0 |
| snapshot 数不一致 | 0 |
| index 分数与 leaf summary 不一致 | 0 |
| replay 引用的 DEFAULT 分数不一致 | 0 |
| replay timing 不可用 | 0 |
| Judge LLM 失败 | 0 |

DEFAULT 与 replay 的 source execution timing 差异固定约 5–6 ms，来自 `unattributed_execution_seconds` 和毫秒取整；这属于记录口径差异，不是异常。

### 3.2 Replay 终止与轨迹变化

| 项目 | 数量 |
|---|---:|
| replay case | 100 |
| 未触发 virtual stop | 80 |
| 触发 virtual stop | 20 |
| replay trajectory 被截短 | 19 |
| virtual stop 发生在最后边界、长度未变 | 1 |
| `evaluation_policy_stop` | 9 |
| `minefield:mf0` | 5 |
| `milestone_no_progress:m0` | 4 |
| `milestone_no_progress:m1` | 1 |
| `milestone_no_progress:m2` | 1 |

20 个 virtual stop 都生成了完整的 summary/report/raw_summary，没有异常中断。9 个 `evaluation_policy_stop` 全部发生在 reminder 创建类 case，虽然中间 stage 判 fail 并停止扫描，但终局对截断后的状态复核为 `full`。其余 11 个是 minefield 或无进展驱动的失败停止。

因此，`trajectory_changed=true` 表示 replay 输出只保留 DEFAULT 轨迹前缀，不表示 agent 被重新运行或改变了决策。当前 replay 本质上仍是对 DEFAULT 轨迹的离线评估。

### 3.3 Agent 运行质量信号

Replay raw summary 汇总如下；各项 case 会重叠：

| 信号 | 发生 case 数 | 记录条数 |
|---|---:|---:|
| `warning_count > 0` | 56 | 133 |
| empty tool result | 57 | 105 |
| failed tool result | 44 | 53 |
| grounding warning | 18 | 37 |
| tool argument warning | 0 | 0 |

这些记录均被结构化捕获，没有造成评估器崩溃。它们主要反映 agent 搜索空结果、工具调用失败、重复尝试和回答 grounding 不足，属于执行质量问题而非流程不可用。

Replay stage 汇总为：`pass=150`、`fail=90`、`warn=3`、`missing=47`。`missing` 主要是前置 milestone 未完成后，下游 milestone 不再可评估；这与阶段图依赖关系一致。

### 3.4 需要判为异常或不清晰的运行记录

#### 3.4.1 单条 DEFAULT 墙钟耗时异常

`DF / modify_contact_with_message_recency_alt_10_distraction_tools`：

- `elapsed_seconds = 1411.585`
- `execution_total_seconds = 120.276`
- 比率为 11.74 倍
- 记录的 `unattributed_execution_seconds` 仅 0.005 秒

其余 99 条 DEFAULT 均未出现 `elapsed / execution_total > 2`。该条约 21.5 分钟未解释耗时没有进入分项 timing，可能来自排队、限流、重试或 harness 外等待。它直接扭曲了总体平均耗时，必须单独标记并使用 median/P95 或 trimmed mean 汇报。

#### 3.4.2 空 milestone graph 的 `coverage_basis` 写错

当前有 16 条 replay 使用空 milestone graph。其 finish stage metadata 正确写为 `coverage_basis=whole_trajectory`，但顶层 `replay_execution.coverage_basis` 16 条全部写成 `milestone_graph`。这与当前 API 文档定义不一致，会误导下游分析。

#### 3.4.3 终局成功与“首个失败/策略终止”并存

9 个 reminder replay 同时满足：

- `milestone_coverage=full`
- `first_failure_stage_id=m0->m1`
- `virtual_stop_code=evaluation_policy_stop`
- `finish_after_virtual_stop=true`

流程可以解释为“中间阶段低分后停止扫描，再用已有状态做终局复核并成功”，因此不是执行崩溃；但字段语义对使用者不够清楚。若下游只读 `first_failure_stage_id` 或 termination code，会把最终成功误判成失败。

#### 3.4.4 适配 case 的消息语义被错误转换为工具调用

在 16 个 adapted case 中，共有 22 条原本是 `AGENT -> USER` 文本约束的 constraint，被转换成 `stage_goal_semantics.kind=tool_call`。典型错误包括：

- “Stephen Sondheim has been added ...” 被解释为调用工具 `Stephen`
- “I cannot remove ...” 被解释为调用工具 `I`
- “Location service has been turned on.” 被解释为调用工具 `Location`
- “It is {days} days ...” 被解释为调用工具 `It` / `days`

这会让失败报告出现 “Need to call the required tool” 等错误诊断，并直接影响 add contact、search message、setting、holiday、send message 和 relationship 等 family 的阶段判定。该问题属于评估数据适配异常，而不是 agent 未调用真实工具。

#### 3.4.5 实验配置包含明文 API 凭据

`data/experiments/toolsandbox_partial_main.json` 当前包含明文 API key。即使本报告不复述具体值，也应立即轮换现有 key，并将配置改为环境变量引用或不入库的本地 secret 文件。这是独立于评估结论的高优先级安全问题。

## 4. DEFAULT 与 REPLAY 成功/失败一致性

### 4.1 总体混淆矩阵

| DEFAULT | REPLAY full | REPLAY 非 full | 合计 |
|---|---:|---:|---:|
| 成功 | 19 | 12 | 31 |
| 失败 | 0 | 69 | 69 |
| 合计 | 19 | 81 | 100 |

派生指标：

- 一致率：88.0%
- 以 DEFAULT 成功为参照的 replay precision：100%
- replay recall：61.3%
- replay 对 DEFAULT 成功的漏判率：38.7%
- 不一致方向：12 条均为 `DEFAULT success -> REPLAY partial/none`

### 4.2 Family 级定位

| Family | Pair 数 | DEFAULT 成功 | REPLAY full | 一致 | 结论 |
|---|---:|---:|---:|---:|---|
| 4 个空 milestone graph case | 16 | 11 | 1 | 6 | 贡献 10/12 个分歧，是首要语义缺陷 |
| 4 个 reminder 创建变体 | 16 | 16 | 16 | 16 | 全部一致成功 |
| `modify_reminder_with_recency_latest` | 4 | 4 | 2 | 2 | 两个 DeepSeek 轨迹完成最终状态，但 replay 卡在过程性工具约束 |
| 其余 16 个 case | 64 | 0 | 0 | 64 | binary 一致失败，但不少是 partial 而非完全无进展 |

### 4.3 逐 case 结果

`full/partial/none` 为 4 个模型的 replay coverage 分布；“一致”按模型逐对统计。

| Case | DEFAULT 成功/4 | REPLAY full/partial/none | 一致/4 | DEFAULT 均分 | REPLAY 均分 | Agent / 评估概况 |
|---|---:|---:|---:|---:|---:|---|
| `find_days_till_holiday_insufficient_information` | 1 | 0 / 0 / 4 | 3 | 0.2500 | 0.1894 | Agent 面对缺失当前日期进行澄清/拒答；replay 将安全未完成判为 none |
| `modify_contact_with_message_recency_insufficient_information` | 3 | 0 / 0 / 4 | 1 | 0.7500 | 0.5012 | 用户补充 Bart；agent 搜索失败或继续澄清；whole-trajectory 仍按初始目标判定 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 4 | 1 / 0 / 3 | 1 | 1.0000 | 0.7763 | 4 条 native 均成功，只有 QM 被 replay 判 full；同类行为的终局 Judge 不稳定 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 3 | 0 / 0 / 4 | 1 | 0.7500 | 0.5525 | 用户授权新增 Bart 后 agent 执行新增；replay 仍要求更新初始“最后联系人” |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 0 | 0 / 0 / 4 | 4 | 0.6366 | 0.0000 | 预期是说明缺少删除工具；adapted goal 错成调用工具 `I` |
| `add_reminder_content_and_date_and_time` | 4 | 4 / 0 / 0 | 4 | 1.0000 | 0.8409 | 全部成功；QP/QM 在状态已达成后触发 policy stop，再由 finish 判 full |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | 4 | 4 / 0 / 0 | 4 | 1.0000 | 0.8409 | 同上，干扰工具未破坏最终结果 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | 4 | 4 / 0 / 0 | 4 | 1.0000 | 0.8409 | 同上 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | 4 | 4 / 0 / 0 | 4 | 1.0000 | 0.7819 | 全部成功；DP/QP/QM 触发 policy stop 后 finish 恢复 full |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 0 | 0 / 4 / 0 | 4 | 0.8438 | 0.4518 | CONTACT 状态 milestone 已完成，但最终确认消息未匹配；adapted goal 误写为调用 `Stephen` |
| `modify_reminder_with_recency_latest` | 4 | 2 / 2 / 0 | 2 | 1.0000 | 0.5783 | QP/QM full；DP/DF 已修改到正确最终状态，但因未调用指定 current-time 工具被判 partial |
| `search_message_with_recency_oldest_multiple_user_turn` | 0 | 0 / 3 / 1 | 4 | 0.9193 | 0.4538 | 多数完成部分检索链，最终回答 milestone 未闭环；消息约束被误解释为工具调用 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | 0 | 0 / 2 / 2 | 4 | 0.8526 | 0.3036 | DF 在 m0、QP 在 m2 无进展停止，其余 partial/none |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | 0 | 0 / 2 / 2 | 4 | 0.8343 | 0.3036 | 结果同上类，参数描述扰动没有产生 full |
| `turn_on_cellular_low_battery_mode` | 0 | 0 / 4 / 0 | 4 | 0.8890 | 0.6174 | 状态操作已有进展，最终确认 milestone 未匹配；消息语义被误转为工具 `Cellular` |
| `turn_on_location_low_battery_mode` | 0 | 0 / 4 / 0 | 4 | 0.8893 | 0.6174 | 状态操作已有进展，最终确认 milestone 未匹配 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | 0 | 0 / 4 / 0 | 4 | 0.8909 | 0.6174 | 同上，干扰工具未改变 binary 结论 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | 0 | 0 / 4 / 0 | 4 | 0.8877 | 0.6174 | 同上 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | 0 | 0 / 3 / 1 | 4 | 0.7096 | 0.4631 | QM 在 m0 无进展停止，其他模型 partial |
| `update_contact_relationship_with_relationship` | 0 | 0 / 4 / 0 | 4 | 0.8592 | 0.6211 | 已完成部分关系更新，最终消息 milestone 未匹配；`All` 被误作工具名 |
| `find_days_till_holiday` | 0 | 0 / 4 / 0 | 4 | 0.9679 | 0.2242 | DEFAULT 接近满分但不 resolved；replay 卡在 current-time/最终回答链 |
| `send_message_with_contact_content_cellular_off` | 0 | 0 / 4 / 0 | 4 | 0.9329 | 0.6852 | 多数已推进到发送后的确认阶段，最终消息 constraint 未闭环 |
| `find_days_till_holiday_wifi_off_alt` | 0 | 0 / 4 / 0 | 4 | 0.7159 | 0.3667 | 环境恢复、日期检索和最终回答链仅部分完成 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 0 | 0 / 4 / 0 | 4 | 0.7876 | 0.3634 | 两个 DeepSeek 在 m0 无进展停止，整体未完成联系人更新闭环 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 0 | 0 / 4 / 0 | 4 | 0.7540 | 0.3194 | 多轮“改为 enemy 再改回 friend”只完成部分阶段；最终消息约束也存在适配错误 |

### 4.4 12 个不一致明细

| 模型 | Case | DEFAULT | REPLAY | Replay 首个失败 | 轨迹变化 |
|---|---|---:|---|---|---|
| DP | `find_days_till_holiday_insufficient_information` | 1.000 / success | 0.7575 / none | `__start__->__finish__` | 否 |
| DP | `modify_contact_with_message_recency_insufficient_information` | 1.000 / success | 0.6450 / none | `__start__->__finish__` | 否 |
| DP | `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 1.000 / success | 0.7700 / none | `__start__->__finish__` | 否 |
| DP | `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 1.000 / success | 0.7700 / none | `__start__->__finish__` | 否 |
| DP | `modify_reminder_with_recency_latest` | 1.000 / success | 0.2451 / partial | `__start__->m1` | 否 |
| DF | `modify_contact_with_message_recency_insufficient_information` | 1.000 / success | 0.6825 / none | `__start__->__finish__` | 否 |
| DF | `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 1.000 / success | 0.7075 / none | `__start__->__finish__` | 否 |
| DF | `modify_reminder_with_recency_latest` | 1.000 / success | 0.2451 / partial | `__start__->m1` | 否 |
| QP | `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 1.000 / success | 0.7575 / none | `__start__->__finish__` | 否 |
| QP | `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 1.000 / success | 0.7700 / none | `__start__->__finish__` | 否 |
| QM | `modify_contact_with_message_recency_insufficient_information` | 1.000 / success | 0.6775 / none | `__start__->__finish__` | 否 |
| QM | `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 1.000 / success | 0.6700 / none | `__start__->__finish__` | 否 |

### 4.5 分歧根因

#### A. 信息不足型任务被按“字面任务必须完成”判失败

`find_days_till_holiday_insufficient_information` 的 DP 轨迹中，用户连续表示不知道当前日期，agent 明确说明缺少当前日期工具、拒绝编造并给出可用信息。DEFAULT 原生 evaluator 判定成功；replay whole-trajectory Judge 却因为“没有输出数字天数”判为 none。

这说明空 graph / minefield-only case 的成功语义没有正确表达“安全澄清、诚实拒答也是完成”。

#### B. Whole-trajectory Judge 没有稳定吸收后续用户意图

多个 `modify_contact_with_message_recency_insufficient_information*` 轨迹中，用户先说明对象是 Bart，搜索失败后又明确授权“将 Bart 作为新联系人添加”。Agent 调用 `add_contact` 并如实确认，DEFAULT 原生 evaluator 判定成功。

Replay 多数报告仍只按最初 task description 的“更新最后联系人”判失败，认为新增 Bart 不符合任务；但 QM 的 10-distraction 同类轨迹又被判 full。这表明终局 Judge 对“初始任务目标”和“后续用户明确改口”的优先级处理不一致，并对措辞敏感。

#### C. 过程性 hard milestone 比最终状态更严格

DP/DF 的 `modify_reminder_with_recency_latest` 都成功调用 `modify_reminder`，最终 reminder id 和 timestamp 与 native reference 完全一致，因此 DEFAULT 为 1.0。Replay 仅因为轨迹没有命中 `get_current_timestamp` 的特定 tool-call milestone，便在 m1 判 fail、后续 m2 missing。

当最终状态已经严格满足目标时，不应仅因为 agent 使用了等价的时间推导路径而判失败。当前 replay 混淆了“必须达成的结果”和“参考实现采用的步骤”。

## 5. 模型区分度分析

### 5.1 模型级得分与排序

| 模型 | DEFAULT 均分 | DEFAULT 排名 | REPLAY 均分 | REPLAY 排名 | DEFAULT 成功 | REPLAY full |
|---|---:|---:|---:|---:|---:|---:|
| DP | 0.886250 | 1 | 0.510336 | 3 | 9 | 4 |
| DF | 0.825270 | 3 | 0.483763 | 4 | 7 | 4 |
| QP | 0.824886 | 4 | 0.529114 | 2 | 7 | 5 |
| QM | 0.842858 | 2 | 0.545233 | 1 | 8 | 6 |

聚合指标：

- `PSEP(DEFAULT) = 0.0336133763`
- `PSEP(REPLAY) = 0.0338645306`
- 绝对增加 `0.0002511544`，相对增加约 0.75%
- `rank_tau(REPLAY vs DEFAULT) = 0.0`

四模型共有 6 个模型对，其中 3 对排序一致、3 对相反，因此 tau 为 0。尤其 DP 从 DEFAULT 第 1 降到 replay 第 3，QP 从第 4 升到第 2。

### 5.2 Case 内分散度

若不是先求模型总均分，而是在每个 case 内计算 4 个模型的两两绝对分差，再对 25 个 case 求平均：

- DEFAULT：0.125812
- REPLAY：0.162452
- REPLAY 增加约 29.1%

这说明 replay 的细粒度评分在部分 case 上确实拉开了模型差距，是其相对 DEFAULT 的实际优点。不过仍有 7 个 replay case 的四模型得分完全相同，DEFAULT 为 6 个；区分度提升并不均匀。

### 5.3 区分度结论

不能仅凭 PSEP 略升就认定 replay 更能区分模型，原因如下：

1. 模型级 PSEP 增幅只有 0.75%，接近微小波动。
2. 排名相关为 0，说明方法改变了“谁更好”的结论，而不是只增强同一排序的间距。
3. 100 对 case 分数的 Pearson 相关仅约 0.673，属于中等相关。
4. Replay 非 full 样本最高分为 0.7700，而 full 样本最低分为 0.7230，score 与 binary completion 存在区间重叠，不能通过 overall score 阈值稳定恢复成功标签。
5. `repeats=1`，无法区分 Judge 随机性、模型随机性与方法本身差异。
6. 12 个 label 分歧已被可复现的语义适配问题解释，因此当前额外分散度可能部分来自评估噪声，而非真实能力差异。

结论是：replay 提供了更丰富的连续分数和阶段诊断，但**尚未证明其模型区分结果比 DEFAULT 更可信**。

## 6. 效率分析

### 6.1 汇总对比

| 指标 | DEFAULT | DYNSTEER_REPLAY | 变化 |
|---|---:|---:|---:|
| case 数 | 100 | 100 | — |
| Agent step 总数 | 617 | 561 | -9.1% |
| raw step 总数 | 1333 | 1207 | -9.5% |
| tool call 总数 | 352 | 334 | -5.1% |
| DEFAULT 累计 wall-clock | 3490.082 s | — | — |
| Replay Judge 累计 wall-clock | — | 628.169 s | 新增成本 |
| DEFAULT execution timing 均值 | 20.364 s | — | — |
| Replay source prefix 均值 | — | 18.044 s | 比完整 execution 少 11.4% |
| Replay effective 均值（prefix + Judge） | — | 24.326 s | 比 DEFAULT execution 高 19.5% |
| DEFAULT wall-clock 均值 | 34.901 s | — | — |
| Replay effective 均值 | — | 24.326 s | 表面低 30.3%，但受异常值扭曲 |
| DEFAULT wall-clock 中位数 | 11.545 s | — | — |
| Replay effective 中位数 | — | 17.062 s | 高 47.8% |

### 6.2 为什么不能用总体均值宣称 replay 更快

DEFAULT 平均 wall-clock 34.901 秒被 DF 的单条 1411.585 秒异常值显著抬高：

- 移除该异常值后，DEFAULT 平均 wall-clock 为 20.995 秒。
- 同样移除对应 pair 后，replay 平均 effective time 为 24.289 秒，比 DEFAULT 高 15.7%。
- 逐 pair 比较，replay effective time 在 32 条中更快、68 条中更慢。
- 若与 source `execution_total_seconds` 进行同口径比较，replay 只有 11 条更快、89 条更慢。

Replay 的平均前缀确实比完整 source execution 少 2.321 秒，但 Judge 本身平均需要 6.282 秒，早停节省无法覆盖评估开销。

此外，`effective_elapsed_seconds` 是“DEFAULT 前缀执行 timing + 离线 replay Judge wall-clock”的实验统计量，不是一次真实在线运行的严格 wall-clock。当前实验为了得到 replay 仍然先完整运行了 DEFAULT，因此同时产出两种结果的实际累计 case 时间是：

`3490.082 + 628.169 = 4118.250 秒`

只有在 DEFAULT 轨迹已经存在、replay 作为边际离线审计时，628.169 秒才可视为新增评估时间；若要声称在线 DYNSTEER 能通过早停省时间，需要单独运行真正的 online evaluator 做验证。

### 6.3 早停有效性

- 20/100 条触发 virtual stop。
- 19/100 条实际缩短 replay 轨迹。
- 总计减少 126 个 raw steps 和 18 个 tool calls。
- 9 个 reminder policy stop 在 4/7 raw steps 处停止，但终局仍为 full。
- 11 个失败驱动 stop 中有 10 个缩短，1 个发生在完整轨迹尾部而未节省步骤。

当前早停覆盖率和节省幅度不足以抵消 Judge 开销。它证明了机制可工作，但还没有证明总体效率优势。

## 7. 成本分析

| 成本项 | DEFAULT | DYNSTEER_REPLAY |
|---|---:|---:|
| DynSTEER Judge LLM calls | 0 | 72 |
| Judge failed calls | 0 | 0 |
| Judge tokens | 0 | 401,715 |
| 平均 Judge tokens / 全部 replay case | 0 | 4,017 |
| 平均 Judge tokens / 实际 LLM call | 0 | 5,579 |
| unique prompts | 0 | 72 |
| cache hits / duplicate prompts avoided | 0 | 144 / 144 |
| Agent trajectory tokens | 不可用 | 不可用 |
| Agent monetary cost | 不可用 | 不可用 |

`metrics.json.cost.llm_cost_available=true` 实际只说明 Judge token 数据存在，并没有输出币种、单价或货币金额。`trajectory_total_tokens=0` 且 `trajectory_cost_available=false`，因此无法核算 agent 模型调用成本，也无法量化截短 126 raw steps 究竟节省了多少 token。

在现有证据下，只能确认 replay **新增** 401,715 Judge tokens；不能确认它节省了更多 Agent tokens。故成本维度不能认定 replay 优于 DEFAULT。

## 8. DYNSTEER_REPLAY 是否优于 DEFAULT

### 8.1 已体现的优点

1. 能提供 milestone、维度、minefield、终止原因、失败阶段和 final-state 一致性等丰富诊断，远强于 DEFAULT 的单一 similarity/resolved 输出。
2. 20 条轨迹识别到可停止边界，19 条确实缩短；安全 minefield 也能快速闭环。
3. Case 内连续分数的平均模型间距增加约 29.1%，说明部分场景确实获得更细的质量刻画。
4. 运行产物完整，timing、缓存和失败调用记录齐备，没有 Judge 调用失败。

### 8.2 当前不优于 DEFAULT 的原因

1. **语义一致性不足：** 31 个 DEFAULT 成功只保留 19 个，12 个分歧全部是 replay 漏判。
2. **空 graph 处理错误：** 信息不足和用户澄清场景被按初始字面任务完成度判断，忽略安全拒答或最新用户意图。
3. **适配数据有结构性错误：** 22 条用户可见消息约束被转换成虚假的工具调用语义。
4. **结果过度依赖过程：** 最终状态完全正确也会因没有调用参考工具而失败。
5. **模型排序不稳定：** PSEP 仅微增，rank tau 为 0。
6. **耗时优势不稳健：** 中位数、去异常均值和逐 pair 统计均显示 replay 更慢。
7. **成本更高：** 新增 72 次调用和 401,715 Judge tokens，Agent 成本又不可观测。
8. **只有一次重复：** 不能评估 LLM Judge 的方差与结果复现性。

因此，当前版本应定位为“补充诊断器/审计器”，不能定位为“更准确、更便宜、更快的 DEFAULT 替代品”。

## 9. 改进建议与优先级

### P0：先修正确性与数据语义

1. **修复 agent-message constraint 的语义适配。** 对 `sender=AGENT, recipient=USER, content=...` 强制生成 message/response constraint，禁止从文本首词推导 `tool_name`；重新生成并审计当前 16 个受影响 case。
2. **为 empty graph / minefield-only case 建立显式成功规则。** 信息不足型任务应将正确澄清、拒绝编造、避免 minefield 视为可能的 full completion，而不是要求字面动作一定完成。
3. **终局 Judge 以最新用户意图为准。** Whole-trajectory prompt 应显式提取意图变更链，后续用户明确授权新增联系人时，应覆盖初始“更新联系人”的动作目标；并加入同一 scenario 跨模型的一致性测试。
4. **区分 outcome hard constraint 与参考过程。** 如果 final state 已严格命中 native target，不应因为缺少 `get_current_timestamp` 等非必要参考工具调用判失败。工具调用要求只有在安全、可解释性或任务明确规定时才设为 hard。
5. **建立 12 个 disagreement 的回归集。** 修复后要求：safe refusal、用户改口和等价工具路径均与人工标注一致；不能只用 DEFAULT 标签盲目拟合，但每个差异必须有人类可解释的裁决。
6. **立即轮换配置中的明文 API key。** 后续仅从环境变量或本地 secret 配置读取。

### P1：修复报告语义与评分校准

1. 空 graph 时统一把顶层 `replay_execution.coverage_basis` 写为 `whole_trajectory`。
2. 明确字段优先级：`final_completion` 是最终 binary 结论；`virtual_stop_code` 是中间决策；对“stop 后终局恢复 full”增加 `recovered_after_virtual_stop=true`，避免 `first_failure_stage_id` 被误读。
3. 校准 `overall_score` 与 coverage 的关系，减少 non-full 分数高于 full 分数的反序；或者明确将 completion 与 quality score 拆为两个独立指标。
4. 在人工标注集上分别评估准确率、召回率、校准误差、跨 Judge 重复一致率，而不是只看 PSEP。
5. 将 `repeats` 提升到至少 3，并报告均值、标准差/置信区间和模型排名稳定率。

### P1：改进效率和成本

1. `metrics.json` 按 method 分层输出 elapsed、median、P90/P95、step、tool call、LLM call 和 token；当前将 200 条混合平均，不利于直接比较。
2. 结构化 scorer 和 final-state hard check 优先，只有语义模糊的 stage 才调用 LLM Judge；继续利用 prompt cache，减少 401,715 tokens 增量。
3. 优化 virtual stop 的触发覆盖和提前量。目前 80% case 未早停，且平均前缀只节省 2.321 秒，无法覆盖 6.282 秒 Judge 开销。
6. 增加真正 online DYNSTEER 对照运行，直接测量端到端 wall-clock、Agent token、Judge token、实际停止点和最终任务质量；不要用离线 replay 的有效耗时替代在线收益证明。

## 10. 最终判断

当前 DYNSTEER_REPLAY 的工程运行链路是可用的，产物完整、可审计、无 Judge 调用失败，也展示了阶段诊断和虚拟早停的价值；但评估语义和 case 适配仍存在足以改变结论的缺陷。

从模型区分度看，它只带来很小的模型均分间距提升，却把模型排序相关降到 0；从效率看，稳健统计显示多数 case 更慢；从成本看，它新增了显著 Judge token，而 Agent 成本不可观测。结合 12 个同轨迹漏判，**当前没有证据支持 DYNSTEER_REPLAY 优于 DEFAULT**。

推荐短期采用“双轨制”：DEFAULT 保持主 benchmark 结果，DYNSTEER_REPLAY 作为诊断和安全审计输出；完成 P0 语义修复、人工裁决回归、多次重复与真实 online 成本实验后，再决定是否提升为主评估方法。
