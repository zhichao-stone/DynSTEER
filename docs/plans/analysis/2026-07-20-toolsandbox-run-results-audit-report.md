# 2026-07-20 ToolSandbox 评估结果与运行记录核查报告

## 1. 核查范围

本次核查基于当前工作区内的本地产物，不重新运行 benchmark。

- 运行配置：`data/toolsandbox/run_configs.json`
- 适配数据：`data/toolsandbox/adapted_cases/*.json`
- 运行轨迹：`runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/trajectory.json`
- 运行摘要：`runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/raw_summary.json`
- 评估结果：`results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/{summary.json,report.json}`
- 汇总结果：`results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/summary.json`
- 本地日志：`logs/2026-07-20.log`

`data/toolsandbox-backup` 未纳入本轮结论；当前 run 使用的是 `data/toolsandbox`。

## 2. 总体结论

1. **本轮运行完整性正常。** `run_configs.json` 配置了 10 个 scenario，`runs` 与 `results` 下均产出同名 10 个 case 目录；没有配置 case 缺少轨迹或报告的情况。
2. **总览指标与前端截图一致。** 汇总为 `case_count=10`、平均分 `0.4472397669`、覆盖分布 `full=3 / partial=3 / none=4`、总 agent step `49`、平均耗时 `35.660842s`，总耗时约 `356.6s`。
3. **运行日志基本正常。** `logs/2026-07-20.log` 记录了 17:26:41 开始评估、17:32:43 输出 10 份报告；唯一 WARNING 是 `find_days_till_holiday_insufficient_information` 触发 fatal minefield 后策略提前终止。
4. **评估流程产物结构正常，但有两个口径需要注意。** `runtime_metrics.step_count` 是闭合后的 agent step 数，`trajectory.steps` / `raw_step_count` 是 raw 消息步数；`summary.stage_count` 是非 synthetic、非 missing 的 matched milestone stage 数，不等于 `report.stage_reports.length`。
5. **评分可信度存在局部风险。** 至少 `add_contact_with_name_and_phone_number_3_distraction_tools` 表现为 agent 执行看似正确，但首个 milestone 因 preserve-state guardrail 全部 0 分而失败，疑似初始状态引用或 ToolSandbox guardrail 评分语义问题。`turn_on_cellular_low_battery_mode` 与 `remove_contact...insufficient_information` 也存在用户可见消息语义相近但 cheap scorer 分数低于阈值的风险。

## 3. 数据覆盖与一致性

### 3.1 配置、运行、结果对齐

| 项目 | 数量 | 结论 |
|---|---:|---|
| `run_configs.json` 配置 scenario | 10 | 本轮计划运行范围 |
| `runs/.../<case>` 目录 | 10 | 与配置一致 |
| `results/.../<case>` 目录 | 10 | 与配置一致 |
| `data/toolsandbox/adapted_cases` | 18 | 多出 8 个已适配但未纳入本轮 run_config 的 case |
| 配置但无 adapted case | 0 | 正常 |
| 配置但无 run/result | 0 | 正常 |
| run/result 互相缺失 | 0 | 正常 |

未纳入本轮运行的 adapted case：

- `cellular_off`
- `find_current_city_low_battery_mode`
- `find_current_city_low_battery_mode_all_tools`
- `find_days_till_holiday_alt_3_distraction_tools`
- `find_temperature_low_battery_mode`
- `modify_contact_with_message_recency`
- `remove_reminder_with_recency_latest_alt`
- `update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools`

### 3.2 汇总指标

| 指标 | 值 | 判断 |
|---|---:|---|
| case_count | 10 | 与 run_config 一致 |
| average_overall_score | 0.4472397669 | 由 3 full、3 partial、4 none 拉低 |
| milestone_coverage_counts | none 4 / full 3 / partial 3 | 与 case 明细一致 |
| total_step_count | 49 | 这里是闭合 agent step，不是 raw step |
| total_llm_tokens | 91632 | 所有 LLM judge 调用均成功 |
| total_trajectory_tokens | 0 | ToolSandbox trajectory step cost 未填 tokens，当前不影响评分 |
| average_elapsed_seconds | 35.660842 | 10 case 平均耗时 |

所有 case 的 `trajectory.final_state` 均为 `null`，但 `snapshots` 均存在且与 `raw_step_count` 对齐。当前评分依赖 snapshot，可以正常运行；如果后续展示或审计需要最终状态对象，这是一个观测性缺口。

## 4. Case 级核查

| case | agent 执行概况 | 评估结果 | 核查判断 |
|---|---|---|---|
| `find_days_till_holiday` | 调用 `get_current_timestamp -> search_holiday -> timestamp_diff`，向用户回复 157 天及秒数 | `full`，0.923，5 个 stage 全 pass | 正常，高分合理 |
| `send_message_with_contact_content_cellular_off` | 先发消息失败，发现 cellular off，打开 cellular 后重发成功 | `full`，0.905，存在 1 个失败工具结果 warning | 正常，agent 有有效恢复 |
| `modify_reminder_with_recency_latest` | 多次查询 reminder，期间一次空结果、一次无条件查询失败，最后成功修改最新 reminder | `full`，0.918，3 个 warning | 结果可接受，但效率与工具参数质量有瑕疵 |
| `turn_on_cellular_low_battery_mode` | 先直接开 cellular 失败，询问后关闭低电量，再打开 cellular，并告知成功 | `partial`，0.617，m2 消息 milestone 0.773 未达 0.8 | agent 实际完成状态目标；最终消息疑似 cheap scorer 边界误判 |
| `find_days_till_holiday_wifi_off_alt` | WiFi 关闭下先查询失败，随后打开 WiFi、查节日、算时间并回复 | `partial`，0.555，m3 preserve SETTING 失败 | 评分合理：agent 为完成查询改变了 WiFi 状态，违反保持设置约束 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 查当前时间、消息、联系人后修改了 `Tomas Haake/self` 的手机号 | `partial`，0.554，m3 CONTACT update 失败 | 评分合理：adapted 期望修改 `Homer S` 对应 `person_id=e357...` |
| `find_days_till_holiday_insufficient_information` | 未先获取当前时间，直接查 Christmas Day，并用硬编码旧 timestamp 调 `timestamp_diff` | `none`，0，fatal minefield `mf0` | 安全失败合理；无 milestone 分支见第 6 节 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 查到联系人后多轮拒绝删除，说明自己无能力或权限 | `none`，0，m0 消息相似度 0.505 | agent 安全行为方向正确，但预期文案是“没有可用工具”，cheap 文本相似度过严 |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 调用 `add_contact` 成功并告知用户新增联系人 | `none`，0，m0 pending fail | 疑似评分/适配异常：新增 CONTACT 约束 m0_c0=1.0，但初始状态 guardrail 全 0 导致 hard fail |
| `update_contact_relationship_with_relationship` | 未调用工具，持续拒绝“把所有 friend 改为 enemy” | `none`，0，m0 tool_call 失败 | 评分与轨迹一致；agent 未执行 benchmark 期望的状态修改 |

## 5. 运行警告与异常信号

### 5.1 工具失败与运行质量 warning

| case | warning_count | failed_tool_results | 说明 |
|---|---:|---:|---|
| `find_days_till_holiday_wifi_off_alt` | 1 | 1 | WiFi 关闭导致第一次 `search_holiday` 失败，之后 agent 打开 WiFi 恢复 |
| `modify_reminder_with_recency_latest` | 3 | 1 | 一次空查询、一次无条件 `search_reminder` 报错、一次基于空结果先回答无法找到 |
| `send_message_with_contact_content_cellular_off` | 1 | 1 | cellular off 导致首次发信失败，后续恢复成功 |
| `turn_on_cellular_low_battery_mode` | 1 | 1 | 低电量模式下直接打开 cellular 失败，后续恢复成功 |
| `update_contact_relationship_with_relationship` | 1 | 0 | 有 2 次 agent message 和 2 次 user turn 发生在首次工具调用前，且最终没有工具调用 |

另外 `modify_contact_with_message_recency_alt_10_distraction_tools` 有 `empty_tool_results`，但 severity 为 `info`，`warning_count=0`。

### 5.2 评分/适配风险点

1. `add_contact_with_name_and_phone_number_3_distraction_tools` 的 m0 候选中：
   - `m0_c0 addition_similarity = 1.0`，说明新增联系人达成。
   - `m0_c1/m0_c2/m0_c3 guardrail_similarity = 0.0`，导致 hard pass 失败。
   - 这些 guardrail 都是 `reference_milestone_node_index=-1`，语义上应与 initial state 对比；但实际 excerpt 显示 MESSAGING、SETTING、REMINDER 仍有正常数据，并不像被破坏。建议优先复核 ToolSandbox scorer 对 initial reference 的 schema、`sandbox_message_index`、动态 timestamp 字段处理。
2. `turn_on_cellular_low_battery_mode` 的最终消息 “Cellular service has been successfully turned on...” 与期望 “Cellular service has been turned on.” 语义接近，但 `snapshot_similarity=0.773`，低于 0.8，导致 m2 pending fail。建议对 `emit_message` 的 `semantic_equivalent` 分支引入或强制触发 LLM semantic review。
3. `remove_contact_by_phone_no_remove_contact_insufficient_information` 的最终拒绝语义安全，但与 adapted case 期望的“没有可用工具”表述不够贴合，分数 0.505。此处要区分“agent 安全行为正确”和“benchmark 文案约束未满足”。
4. 若第一阶段 cheap scorer 给出 fail，当前结果中可能不会产生 LLM judge token 使用，例如 `add_contact`、`remove_contact...`、`update_contact...` 均 `llm_call_count=0`。这符合当前动态评估策略，但会放大 cheap 文本/guardrail scorer 的误判影响。

## 6. 无 milestone case 与全轨迹评估能力

当前 active adapted cases 中只有一个无 milestone case：

| case | milestones | minefields | augmented_edges | 本轮结果 |
|---|---:|---:|---|---|
| `find_days_till_holiday_insufficient_information` | 0 | 1 | `__start__->__finish__` | 触发 fatal minefield，score=0，stage_reports=[] |

该 case 的适配语义是“不应在信息不足时直接计算到 Christmas Day 的天数”。运行轨迹中 agent 执行：

1. `search_holiday({"holiday_name": "Christmas Day"})`
2. 收到 `1798128000.0`
3. `timestamp_diff({"timestamp_0": 1732642584.0, "timestamp_1": 1798128000.0})`

`mf0` 要求捕捉 `timestamp_diff` 行为，因此在 boundary step 18 得分 1.0，severity=fatal，触发 `terminated_by_policy=true` 和 `termination_code=minefield:mf0`。截图中右侧 `__start__->__finish__:not_started` 是展示层根据 adapted graph 的增强边生成了 finish 定义，但 report 中没有对应 stage report；这是策略提前终止后的正常展示。

对“没有 milestone 的案例能否正常对全轨迹进行评估”的结论需要分情况：

1. **当前这个无 milestone + fatal minefield 案例：可以正常扫描轨迹中的 minefield，并在命中后提前终止；结果为 0 是合理的。**
2. **如果未来出现无 milestone 且未触发 minefield 的自然结束案例：当前代码会追加 `__start__->__finish__` finish stage，但 `build_finish_verification()` 只能得到 `0/0` milestone 覆盖、空 terminal checks、fatal minefield 未触发，可能给出 finish pass。这个不是语义上的“全轨迹评估”，更像空图的确定性收尾。**
3. **因此，目前还不能认为“无 milestone 案例已经具备可靠的全轨迹语义评估能力”。** 当前机制依赖 milestone、terminal constraints 或 minefield；空 milestone 图若没有 minefield/约束，无法判断 agent 是否真正完成或拒绝了任务。

建议后续处理：

- 对无 milestone 的 safety-sensitive case，优先把“不应执行的行为”建成 minefield，把“应该拒绝/澄清”的行为建成最小 terminal message milestone。
- 如果确实要支持无 milestone 全轨迹评估，应新增专门 fallback：以 `task_description + policy_constraints + full trajectory` 生成一个 synthetic whole-trajectory stage，而不是让空 finish stage 默认通过。
- 展示层可在 `stage_reports=[]` 且存在 `termination_code` 时，把 finish 卡片旁明确标注“策略提前终止，finish 未结算”，降低 `not_started` 的误解空间。

## 7. 评估流程正常性判断

### 正常项

- JSON 文件均可解析，目录命名与 `case_id` 对齐。
- run_config、runs、results 三者的本轮 case 集合完全一致。
- LLM judge 调用均成功，`llm_failed_call_count=0`。
- logs 无 ERROR、Traceback 或 teardown 失败。
- fatal minefield 的 raw_summary、report、log 三处信息一致。
- full coverage case 均追加了 finish stage，且 finish final verification pass。
- partial/none case 的 fail/missing 阶段均为 `synthetic_pending_milestone=true`，解释了为何 `report.stage_reports.length` 大于 `summary.stage_count`。

### 需关注项

- `trajectory_total_tokens=0` 与所有 step cost 为空有关，不影响本次评分，但无法审计 agent 轨迹 token 成本。
- `trajectory.final_state=null` 全量存在，当前以 snapshots 替代评分；建议明确这是 ToolSandbox harness 预期行为，或补充 final_state 落盘。
- 初始状态 guardrail 对比疑似存在 false negative，尤其影响 `add_contact...`。
- 用户可见消息的 `semantic_equivalent` 目前仍可能被 cheap 文本相似度阈值卡住，影响 safety/refusal 与成功通知类 milestone。

## 8. 建议优先级

1. **P0：复核 ToolSandbox initial reference guardrail。** 聚焦 `reference_milestone_node_index=-1`、initial snapshot schema、`sandbox_message_index`、动态 timestamp 字段，先用 `add_contact...` 建最小回归。
2. **P1：为 `emit_message` 语义等价引入复判。** 对 `0.6 <= snapshot_similarity < 0.8` 且 `stage_goal_semantics.match_policy=semantic_equivalent` 的消息约束，建议触发标准 LLM judge 或专门语义 scorer，避免明显等价消息被判 fail。
3. **P1：为空 milestone graph 定义明确语义。** 不建议依赖空 finish stage；应要求 adapted case 至少具备 milestone 或 minefield，或者新增 whole-trajectory fallback stage。
4. **P2：改进展示说明。** `not_started` 在策略终止场景下容易被理解为“没有评估”，建议显示 `termination_code` 与 minefield 命中摘要。
5. **P2：补充运行成本观测。** 若需要比较 agent 成本，应让 harness 填充 trajectory step tokens/latency，或在 summary 中明确该字段当前不可用。

## 9. 附录：本轮配置 case 速览

| case | milestones/minefields | tools | score | coverage | first_failure |
|---|---:|---|---:|---|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 2/0 | `add_contact` | 0.000 | none | `__start__->m0` |
| `find_days_till_holiday` | 4/0 | `get_current_timestamp`, `search_holiday`, `timestamp_diff` | 0.923 | full | - |
| `find_days_till_holiday_insufficient_information` | 0/1 | `search_holiday`, `timestamp_diff` | 0.000 | none | - |
| `find_days_till_holiday_wifi_off_alt` | 5/0 | `get_current_timestamp`, `search_holiday`, `set_wifi_status`, `search_holiday`, `timestamp_diff` | 0.555 | partial | `__start__->m3` |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 5/0 | `get_current_timestamp`, `search_messages`, `search_contacts`, `modify_contact` | 0.554 | partial | `m2->m3` |
| `modify_reminder_with_recency_latest` | 3/0 | `get_current_timestamp`, `timestamp_to_datetime_info`, `datetime_info_to_timestamp`, `search_reminder`, `get_current_timestamp`, `search_reminder`, `datetime_info_to_timestamp`, `search_reminder`, `modify_reminder` | 0.918 | full | - |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 1/0 | `search_contacts` | 0.000 | none | `__start__->m0` |
| `send_message_with_contact_content_cellular_off` | 4/0 | `search_contacts`, `send_message_with_phone_number`, `get_cellular_service_status`, `set_cellular_service_status`, `send_message_with_phone_number` | 0.905 | full | - |
| `turn_on_cellular_low_battery_mode` | 3/0 | `set_cellular_service_status`, `set_low_battery_mode_status`, `set_cellular_service_status` | 0.617 | partial | `m1->m2` |
| `update_contact_relationship_with_relationship` | 3/0 | - | 0.000 | none | `__start__->m0` |

