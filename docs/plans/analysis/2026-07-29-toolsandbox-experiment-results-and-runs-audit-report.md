# 2026-07-29 ToolSandbox 实验结果与运行记录核查报告

## 1. 核查范围

本次核查仅基于当前工作区已有产物，不重新运行实验，不修改评估代码。

- 汇总结果：`results/experiments/toolsandbox_partial_main/index.json`
- 汇总分数：`results/experiments/toolsandbox_partial_main/scores.json`
- 汇总指标：`results/experiments/toolsandbox_partial_main/metrics.json`
- 评估明细：`results/experiments/toolsandbox_partial_main/toolsandbox/{default,dynsteer_replay}/run_0/*`
- 运行记录：`runs/experiments/toolsandbox_partial_main/toolsandbox/{default,dynsteer_replay}/run_0/*`
- 日志记录：`logs/2026-07-28.log`

## 2. 总体结论

1. 当前产物覆盖 25 个 ToolSandbox case，按 default 与 dynsteer_replay 两种方法展开后共 50 条结果；`results` 与 `runs` 两侧 case 集合完全一致。
2. 所有 `summary.json`、`default_report.json` / `report.json`、`raw_summary.json`、`trajectory.json` 均存在且可解析；顶层 `scores.json`、`metrics.json` 与 case 明细一致。
3. default 与 replay 的 25 条 `trajectory.json` 行为轨迹完全一致，step 数、raw step 数、tool call 数也一致；因此 default / replay 的结果差异主要来自评估口径与 replay 阶段化评估链路，不是 agent 在两种方法下执行了不同轨迹。
4. 严格成败口径下，default 使用原生 `resolved=true`，replay 使用 `milestone_coverage=full`。25 个 case 中 23 个成败一致，2 个不一致。
5. 不一致的两个 case 均为 `modify_contact_with_message_recency_insufficient_information` 的工具干扰变体：default 原生评估满分成功，replay 因空 milestone graph 且未配置 whole-trajectory fallback 判为 `invalid / none / 0.0`。这更像 replay 适配或终态评估缺口，不像 agent 执行失败。
6. replay 平均分显著低于 default：default 平均 `0.819681`，replay 平均 `0.470566`。若不用官方 `resolved`，而用 default score `>=0.8` 作为成功候选，则 default 有 19 个高分 case，replay 只有 4 个 full case，二者一致性会降到 10/25。后续比较必须明确成败口径。
7. 运行日志显示最终一轮实验成功落盘 50 条结果，但在此之前有两次失败尝试：一次 stage goal 生成参数错误，一次 replay 输入解析错误。当前产物时间与最终成功运行对应，早期错误不污染当前结果，但说明实验流程曾经不稳定。

## 3. 数据完整性与汇总指标

| 项目 | 当前值 | 判断 |
|---|---:|---|
| 顶层 `index.case_count` | 50 | default + replay 两方法展开 |
| unique case 数 | 25 | 两方法 case 集合完全一致 |
| default case 数 | 25 | 正常 |
| replay case 数 | 25 | 正常 |
| 缺失 JSON 文件 | 0 | 正常 |
| JSON 解析错误 | 0 | 正常 |
| default 平均分 | 0.819681 | 与 `scores.json` 一致 |
| replay 平均分 | 0.470566 | 与 `scores.json` 一致 |
| `metrics.average_elapsed_seconds` | 20.025717 | 50 条 method-expanded 结果平均 |
| `metrics.average_agent_step_count` | 6.12 | default/replay 轨迹一致 |
| `metrics.average_raw_step_count` | 13.24 | default/replay 轨迹一致 |
| `llm_total_tokens` | 297547 | replay judge token 正常记录 |
| `trajectory_total_tokens` | 0 | agent 轨迹 token 成本仍不可用 |
| `psep` / `rank_tau` | 0.0 / 0.0 | 当前指标未体现有效区分度 |

## 4. 成败口径

本报告采用两个口径：

- 严格成败：default 使用 `resolved=true`；replay 使用 `milestone_coverage=full`。
- 辅助分数：同时列出 `overall_score`，用于观察高分未成功、低分但部分完成等情况。

严格口径结果：

| 方法 | 成功 | 失败或非 full | 平均分 |
|---|---:|---:|---:|
| default | 6 | 19 | 0.819681 |
| dynsteer_replay | 4 | 21 | 0.470566 |

default 分数阈值敏感性：

| 阈值 | default score 达标 | replay score 达标 |
|---|---:|---:|
| >= 0.8 | 19 | 0 |
| >= 0.9 | 15 | 0 |
| = 1.0 | 6 | 0 |

这说明 default 原生分数与 `resolved` 并不等价。大量 default 高分 case 仍为 `resolved=false`，不能直接按分数阈值解释为成功。

## 5. Scenario 级对比

这里按基础 scenario 聚合：移除 `_3_distraction_tools`、`_10_distraction_tools`、`_arg_description_scrambled`、`_arg_type_scrambled` 等变体后统计。

| scenario | case 数 | default 成功 | replay 成功 | 严格成败一致 | 判断 |
|---|---:|---:|---:|---:|---|
| `add_contact_with_name_and_phone_number` | 1 | 0 | 0 | 1/1 | agent 执行看似完成，但两侧严格口径均未成功 |
| `add_reminder_content_and_date_and_time` | 4 | 4 | 4 | 4/4 | 两侧成功一致 |
| `find_days_till_holiday` | 1 | 0 | 0 | 1/1 | default 高分但未 resolved，replay partial |
| `find_days_till_holiday_insufficient_information` | 1 | 0 | 0 | 1/1 | fatal minefield，失败一致 |
| `find_days_till_holiday_wifi_off_alt` | 1 | 0 | 0 | 1/1 | agent 有恢复动作，replay 仍 partial |
| `modify_contact_with_message_recency_alt` | 1 | 0 | 0 | 1/1 | 两侧失败一致 |
| `modify_contact_with_message_recency_insufficient_information` | 3 | 2 | 0 | 1/3 | 两个变体存在 default/replay 严格成败不一致 |
| `modify_reminder_with_recency_latest` | 1 | 0 | 0 | 1/1 | agent 未执行最终 reminder 修改，失败一致 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 1 | 0 | 0 | 1/1 | agent 拒绝删除，但 replay 判 none，需复核约束 |
| `search_message_with_recency_oldest_multiple_user_turn` | 3 | 0 | 0 | 3/3 | strict 失败一致，replay partial |
| `send_message_with_contact_content_cellular_off` | 1 | 0 | 0 | 1/1 | 用户可见执行成功，但严格评估未成功 |
| `turn_on_cellular_low_battery_mode` | 1 | 0 | 0 | 1/1 | 用户可见执行成功，但严格评估未成功 |
| `turn_on_location_low_battery_mode` | 4 | 0 | 0 | 4/4 | 用户可见执行成功，但严格评估未成功 |
| `update_contact_relationship_with_relationship` | 1 | 0 | 0 | 1/1 | 最终状态与显式工具轨迹存在证据缺口 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 1 | 0 | 0 | 1/1 | 多轮执行质量较差，strict 失败一致 |

## 6. Case 级对比与 agent 执行情况

| case | default | replay | 一致 | agent / 评估摘要 |
|---|---:|---:|---|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 失败 / 0.854 | partial / 0.452 | 是 | agent 调用 `add_contact` 并告知成功；default 高分但未 resolved，replay 在 `m0->m1` 失败，偏向终态或消息约束未完全满足。 |
| `add_reminder_content_and_date_and_time` | 成功 / 1.000 | full / 0.723 | 是 | agent 调用 `datetime_info_to_timestamp -> add_reminder`；最终成功，但 replay 中间 `m0->m1` 出现一次 policy stop 后 finish 重检通过。 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | 成功 / 1.000 | full / 0.723 | 是 | 同上，干扰工具未影响 agent 成功路径；replay 中间诊断仍有同类 policy stop。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | 成功 / 1.000 | full / 0.723 | 是 | 同上。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | 成功 / 1.000 | full / 0.723 | 是 | 同上，参数描述扰动未影响执行成功。 |
| `find_days_till_holiday` | 失败 / 0.973 | partial / 0.224 | 是 | agent 调用 `get_current_timestamp -> timestamp_diff`，未调用 replay 要求的 `search_holiday`；default 高分但未 resolved，replay 认为依赖链缺失。 |
| `find_days_till_holiday_insufficient_information` | 失败 / 0.000 | none / 0.000 | 是 | agent 在信息不足时仍执行 `search_holiday -> timestamp_diff`，触发 fatal minefield，失败合理。 |
| `find_days_till_holiday_wifi_off_alt` | 失败 / 0.762 | partial / 0.555 | 是 | agent 先遇到 WiFi off，再 `set_wifi_status` 恢复后查询并回答；replay 仍在后续 milestone 失败，属于部分完成。 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 失败 / 0.770 | partial / 0.519 | 是 | agent 执行 `search_messages -> get_current_timestamp -> search_messages -> search_contacts -> modify_contact`，有失败工具调用和状态偏移，partial 合理。 |
| `modify_contact_with_message_recency_insufficient_information` | 失败 / 0.000 | none / 0.000 | 是 | agent 最终触发 `modify_contact` minefield，失败合理。 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | 成功 / 1.000 | none / 0.000 | 否 | agent 未越权修改已有联系人，澄清后 `search_contacts -> add_contact` 新增 Bart；replay 因空 milestone graph 且无 fallback 判 invalid。 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | 成功 / 1.000 | none / 0.000 | 否 | 同上，default 原生成功，replay invalid；这是本轮最明确的 replay 评估缺口。 |
| `modify_reminder_with_recency_latest` | 失败 / 0.667 | partial / 0.622 | 是 | agent 计算时间后只调用 `search_reminder`，未执行 `modify_reminder`，失败合理。 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 失败 / 0.540 | none / 0.000 | 是 | agent `search_contacts` 后持续拒绝删除；replay 的 `m0` 诊断仍提示“Need to call required tool”，与轨迹中已有 `search_contacts(phone_number=...)` 存在复核价值。 |
| `search_message_with_recency_oldest_multiple_user_turn` | 失败 / 0.915 | partial / 0.588 | 是 | agent 查询消息并输出 oldest message，但 replay 在 `m1->m2` 失败；default 高分但未 resolved。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | 失败 / 0.911 | partial / 0.588 | 是 | agent 多次查询和纠正，工具链更长；replay partial。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | 失败 / 0.914 | partial / 0.588 | 是 | agent 查询链较短但最终未满足 replay 后续 stage；partial。 |
| `send_message_with_contact_content_cellular_off` | 失败 / 0.925 | partial / 0.672 | 是 | agent 先发送失败，再开启 cellular 并重发成功；用户可见任务完成，但 replay `m2->m3` 失败，可能是终态消息或 terminal 约束偏严。 |
| `turn_on_cellular_low_battery_mode` | 失败 / 0.924 | partial / 0.617 | 是 | agent 关闭低电量模式后打开 cellular；用户可见成功，但 replay 终态 stage 失败。 |
| `turn_on_location_low_battery_mode` | 失败 / 0.898 | partial / 0.617 | 是 | agent 关闭低电量模式后打开 location service；用户可见成功，但 replay 终态 stage 失败。 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | 失败 / 0.912 | partial / 0.617 | 是 | 同类设置任务，用户可见成功，replay partial。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | 失败 / 0.918 | partial / 0.617 | 是 | 同上。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | 失败 / 0.943 | partial / 0.617 | 是 | 同上。 |
| `update_contact_relationship_with_relationship` | 失败 / 0.837 | partial / 0.617 | 是 | agent 只显式调用一次 `modify_contact`，但最终 CONTACT 状态显示两名 friend 都变为 enemy；轨迹与状态之间存在可解释性缺口。 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | 失败 / 0.829 | partial / 0.360 | 是 | agent 多轮修改、搜索、回滚，warning 最多；最终执行质量较差，partial/低分合理。 |

## 7. replay 运行与评估流程核查

### 7.1 轨迹一致性

25 个 case 的 default 与 replay `trajectory.json` 行为签名全部一致：

- agent step 数一致；
- raw step 数一致；
- snapshot 数一致；
- tool call 数一致；
- tool call 名称序列一致；
- final state 均存在。

因此 replay 不是重新驱动 agent，而是对同一轨迹做 DynSTEER 阶段化重放评估。

### 7.2 replay stage 状态分布

replay 的 `stage_reports` 状态合计：

| status | 数量 |
|---|---:|
| pass | 40 |
| fail | 23 |
| missing | 6 |
| invalid | 2 |
| warn | 1 |

replay judge 调用：

| 指标 | 数量 |
|---|---:|
| 调用 LLM judge 的 case | 18 |
| LLM judge 调用总数 | 57 |
| LLM judge 失败数 | 0 |
| LLM token 总数 | 297547 |

### 7.3 virtual stop 与 fatal minefield

按 `runs/.../raw_summary.json` 顶层 `termination_detail.virtual_stop_step_index` 统计，replay 中可观察到 7 条 virtual stop 记录；其中 6 条发生在轨迹中段，1 条发生在终止步，完整点位统计见下节。

| 类型 | case | 判断 |
|---|---|---|
| `evaluation_policy_stop` | 4 个 `add_reminder_content_and_date_and_time*` | 中间 stage 分数低于阈值，但 `replay_continue_after_virtual_stop=true`，最终 finish 重检 full |
| `minefield:mf0` | `find_days_till_holiday_insufficient_information` | fatal minefield，失败合乎预期 |
| `minefield:mf0` | `modify_contact_with_message_recency_insufficient_information` | fatal minefield，失败合乎预期 |
| `milestone_no_progress:m2` | `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | virtual stop 落在最后一步，progress=1.0 |

需要注意：4 个 add_reminder case 最终都是 full，但 `first_failure_stage_id=m0->m1`，且中间诊断文本明确提示 hard constraint 未满足；随后 finish 又报告 terminal 状态重检通过。这不是最终失败，但会干扰人工审计，建议后续把“历史失败尝试”和“最终失败”字段拆开。

### 7.4 virtual stop 点位统计

`virtual_stop_step_index` 是绝对 raw step 编号；为了得到“在该条完整轨迹中的位置”，这里按 case 自身轨迹起点归一化，使用 `stop_order = virtual_stop_step_index - first_step_index + 1`，再除以 `total_steps = len(trajectory.steps)`。

| case | termination_code | stop_order / total | progress |
|---|---|---:|---:|
| `add_reminder_content_and_date_and_time` | `evaluation_policy_stop` | 4 / 7 | 0.571429 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | `evaluation_policy_stop` | 4 / 7 | 0.571429 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | `evaluation_policy_stop` | 4 / 7 | 0.571429 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | `evaluation_policy_stop` | 4 / 7 | 0.571429 |
| `find_days_till_holiday_insufficient_information` | `minefield:mf0` | 3 / 7 | 0.428571 |
| `modify_contact_with_message_recency_insufficient_information` | `minefield:mf0` | 15 / 31 | 0.483871 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | `milestone_no_progress:m2` | 33 / 33 | 1.000000 |

结论：7 条 virtual stop 里，6 条是真正的中途早停，1 条是轨迹最后一步才触发；按这 7 条统计的平均进度约为 `0.599737`，若只看中途早停则约为 `0.533026`。
## 8. 运行日志核查

`logs/2026-07-28.log` 中关键记录如下：

| 时间 | 事件 | 判断 |
|---|---|---|
| 18:59:52 | 日志初始化 | 第一次尝试开始 |
| 19:00:52 | `benchmark 执行失败`，`_generate_stage_goals_by_semantic()` 参数数量错误 | 失败尝试，不对应当前最终产物 |
| 19:01:54 | 日志重新初始化 | 第二次尝试开始 |
| 19:17:12 | `benchmark 输入解析失败`，`tool_result` 不是 JSON 对象 | 失败尝试，不对应当前最终产物 |
| 19:30:26 | 日志重新初始化 | 最终成功尝试开始 |
| 19:47:22 | `统一实验完成，case结果数量: 50` | 与当前 `index.case_count=50` 对齐 |

当前 JSON 的 `started_at` 使用 UTC，例如 default 结果约从 `2026-07-28T11:34:04+00:00` 开始，换算为北京时间约 19:34，与 19:30 后的最终成功运行一致。

结论：最终运行闭环正常，但日志中存在两次前置失败，说明本轮实验过程不是一次性稳定完成。

## 9. 主要异常与风险点

### P0. minefield-only / 空 milestone graph 的 replay 终态缺口

`modify_contact_with_message_recency_insufficient_information_10_distraction_tools` 与 `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` 的 adapted case 只有 fatal minefield，没有 milestone 节点，也没有 stage goal。agent 未触发 `modify_contact` minefield，并在用户进一步授权新增联系人后调用 `add_contact`，default 原生评估为成功。

replay 当前给出：

- `milestone_coverage=none`
- `overall_score=0.0`
- `stage_status=invalid`
- 诊断：空 milestone graph 未配置 whole-trajectory fallback，不能默认通过

这会把“避免 fatal minefield 且完成替代目标”的轨迹判成失败。建议为 minefield-only case 增加 terminal / whole-trajectory fallback，或显式定义“无 fatal minefield + 用户可见合理拒绝或澄清完成”的成功条件。

### P1. add_reminder full case 中间 policy stop 与最终 full 冲突

4 个 add_reminder 变体最终都是 full，但中间 `m0->m1` stage 的 standard judge 诊断认为 actual excerpt 没有目标 reminder，并触发 `evaluation_policy_stop`。同一 stage 又显示结构化 hard constraints 通过，finish 再做 terminal 状态重检并通过。

这说明当前 evidence excerpt、structured scorer、standard judge 三者之间仍存在不一致。建议：

- standard judge 使用完整命中行或结构化摘要，不要只看可能截断或错位的 excerpt；
- `first_failure_stage_id` 明确标识为 first failed attempt，或在 full case 中不要让它看起来像最终失败；
- full case 中的 virtual stop 应单独汇总为“已被 finish 修复的历史失败”。

### P1. 用户可见成功但 strict 失败的设置 / 发送类 case

`send_message_with_contact_content_cellular_off`、`turn_on_cellular_low_battery_mode`、4 个 `turn_on_location_low_battery_mode*` 都呈现类似模式：

- agent 先遇到状态依赖失败；
- agent 恢复 cellular / location / low battery 状态；
- 最终用户可见回复显示任务完成；
- default 分数较高但 `resolved=false`；
- replay 多数在最后一个 terminal 或消息 stage 失败。

这类结果不一定是评估错误，但需要确认实验意图：如果必须严格满足完整 milestone graph，则当前 partial 合理；如果任务完成度更看重最终状态与用户可见效果，则 terminal stage 可能过严。

### P1. update_contact relationship 的轨迹可解释性缺口

`update_contact_relationship_with_relationship` 中，trajectory 只显式记录一次 `modify_contact`，参数为 John Petrucci；但 final CONTACT state 显示 Fredrik Thordendal 与 John Petrucci 均变为 `enemy`。replay 结构化 scorer 接受 final state，但这对人工审计不友好。

建议在轨迹导出中补充 state mutation 对应事件，或明确说明 snapshot 中 `sandbox_message_index=22` 的内部状态更新来源，避免出现“状态变了但轨迹看不到对应工具调用”的审计疑问。

### P2. default 高分未 resolved 容易误读

default 有 13 个 case 分数 `>=0.8` 但 `resolved=false`。例如：

- `find_days_till_holiday`：0.973，但未 resolved；
- `send_message_with_contact_content_cellular_off`：0.925，但未 resolved；
- `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled`：0.943，但未 resolved；
- `update_contact_relationship_with_relationship`：0.837，但未 resolved。

后续报告或可视化不应只展示平均分或高分数量，应同时展示 `resolved` 与分数。

### P2. agent token 成本不可用

`trajectory_total_tokens=0` 且 `trajectory_cost_available=false`。replay judge token 已可统计，但 agent 执行本身的 token 成本仍无法审计。若后续要比较效率或成本，需要补齐 trajectory step cost。

## 10. 最终判断

当前最终实验产物在文件完整性、JSON 可解析性、顶层汇总一致性、default/replay 轨迹一致性方面是正常的。最终一轮运行日志也显示 50 条 method-expanded case 成功落盘。

当前评估结果本身需要分层理解：

- 严格成功/失败对比：25 个 case 中 23 个一致，2 个 replay 判错风险较高。
- 分数对比：replay 明显低于 default，但主要来自阶段化依赖、terminal 约束和终态复核口径，而不是 agent 轨迹变化。
- agent 执行质量：add_reminder 变体稳定成功；modify_reminder、find_days 无 holiday search、部分 search/message/contact 多轮任务存在真实执行缺口；设置与发送类 case 多数用户可见完成但严格评估未 full。
- 流程健康度：最终闭环正常，但日志保留两次早期失败；replay 内部仍有 invalid、virtual stop、证据链不一致等需要后续修复或在报告中显式标注的风险。

建议优先修复 minefield-only fallback 与 add_reminder 证据链冲突，再复核设置 / 发送 / relationship 类 terminal stage 的严格性，最后补齐 trajectory token 成本与状态变更可解释性。

## 11. 追加分析：DynSTEER 是否实现了“阶段评估驱动的早停节省”

### 11.1 结论
就当前产物看，**还不能说 DynSTEER 已经实现了“在 Agent 执行过程中，因阶段式评估发现执行效果不利而提前终止，从而节省时间与成本”**。当前更准确的说法是：DynSTEER 已经能在评估层识别阶段性风险和失败信号，但本轮实验产物没有证明它真的把 Agent 执行流程提前截断了。

### 11.2 为什么目前不能算“已实现早停”
- default 与 `dynsteer_replay` 的 25 个 case 轨迹完全一致，`step_count`、`raw_step_count`、`tool_call_count` 都没有任何一个 case 变短。若真有执行侧早停，至少应能看到部分 case 的 Agent 步数或工具调用数减少，但当前没有。
- replay 配置里明确写着 `replay_continue_after_virtual_stop=true`。也就是说，即使阶段评估触发了 `evaluation_policy_stop`，当前回放仍会继续把后续轨迹跑完，用于审计而不是执行中止。
- 4 个 `add_reminder` 变体确实出现了中间阶段的 `evaluation_policy_stop`，但最终都被 `finish` 重新判成 full。这说明阶段评估能“发现问题”，但并没有把执行流程真的停掉。
- 另有 2 个 `minefield-only / empty milestone graph` case 直接落在评估覆盖缺口上，属于判定器或回放适配问题，不是通过早停节省了 Agent 执行成本。
- 成本侧也看不到节省：default 的 `trajectory_total_tokens=0`，`dynsteer_replay` 的 `llm_total_tokens=297547`。这说明当前产物至少额外产生了评估侧 LLM 成本，而不是已证明的执行侧节省。
- 虽然 `dynsteer_replay` 的平均 `elapsed_seconds` 为 `19.273862`，略低于 default 的 `20.777573`，但在 step/raw/tool 计数完全一致、且 replay 额外包含大量评估调用的前提下，这个时间差不能直接归因于早停，更像是运行波动或评估实现差异。

注：本节里引用的 `replay_continue_after_virtual_stop=true` 是对 2026-07-28 那次实验产物的历史描述，不是当前默认值；当前代码默认已改为 `false`。

### 11.3 目前能成立的表述
更准确地说，当前结果只能支持下面这句话：

“DynSTEER 在阶段评估层面能够识别出部分不利执行、虚拟停止和 minefield 风险，但在当前 replay 产物中，这些停止是审计信号而不是终止信号，因此还不能把它表述为已经实现了面向 Agent 执行的真实早停节省。”

### 11.4 若要证明这一点，还缺什么
- 需要一组真正 enforce stop 的 online 运行，或者 `replay_continue_after_virtual_stop=false` 的对照。
- 需要按 case 统计 `first_virtual_stop_stage` 之前后节省了多少 step / tool call / token。
- 需要补上 Agent 侧 token 成本或更细粒度的执行耗时，否则只能讨论评估侧开销，不能证明总成本下降。
