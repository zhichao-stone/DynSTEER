# toolsandbox_partial_main 评估结果与运行记录核查报告

> 核查日期：2026-08-04  
> 实验配置：`data/experiments/toolsandbox_partial_main.json`  
> 结果目录：`results/exp/toolsandbox_partial_main`  
> 运行目录：`runs/exp/toolsandbox_partial_main`

## 1. 结论摘要

本次实验的文件完整性正常：配置包含 4 个 agent 模型、2 种评估方法、3 次 repeat、25 个 scenario，理论记录数为 `4 × 2 × 3 × 25 = 600`；实际存在 600 份 trajectory、600 份 raw summary、600 份 result summary 和 600 份 report，`index.json` 也记录了 600 cases。未发现缺失、无法解析、负耗时、评估 LLM 调用失败或 timing 缺失。

但评估口径和汇总数据存在显著问题，因此目前不能直接依据“成功率”判断 DYNSTEER_REPLAY 优于 DEFAULT：

1. DEFAULT 的 `resolved` 是严格的原生满分判定；300 条中 93 条成功，成功率 31.0%，但平均连续分数仍高达 0.8457。大量任务实际完成，仅因最终文本与参考文本的 Rouge/相似度不足而被记为失败。
2. DYNSTEER_REPLAY 的 300 条 `summary.json` 和 `index.json` 中 `resolved` 全部为 `null`。`metrics.json` 另行把 `milestone_coverage == full` 当成 replay success，得到 234/300（78.0%）。两个方法的成功字段不是同一数据口径。
3. 按 `metrics.json` 的派生口径，DEFAULT 与 replay 成功/失败仅 157/300 一致，一致率 52.33%；有 142 条 DEFAULT 失败→replay 成功，1 条 DEFAULT 成功→replay 失败。
4. 142 条转成功中，多数是 replay 对“语义等价文本”或“正确状态变更”的修正，通常比 DEFAULT 更合理；但并非全部都已得到人工真值确认。replay 还存在阶段边界、工具轨迹匹配和 policy-stop 过早导致的假失败。
5. replay 的模型分数标准差从 DEFAULT 的 0.0070 增至 0.0222，模型区分度显著增强；但模型排序 Kendall tau 仅 0.3333，且多数模型的 repeat 波动变大。更大的分数间距不等同于更准确的区分。
6. replay 的纯评估耗时为 2,575.75 秒，并新增 438 次 `qwen-plus-latest` 评估调用、1,719,691 tokens。虚拟早停将源轨迹执行时间从 5,816.01 秒降至 4,951.08 秒（理论节省 14.87%），但加上 replay 评估后有效总时长为 7,526.83 秒，反而比完整源轨迹执行时间高 29.42%。当前数据不能证明端到端效率或成本优于 DEFAULT。

**总体判断：DYNSTEER_REPLAY 目前适合作为比 DEFAULT 更丰富的诊断型补充评估器，但尚不适合作为无条件替代。** 它在语义合理性、阶段诊断和分数区分度上有明显优势；在成功口径、假失败控制、端到端成本、可复现性和汇总字段完整性方面仍需改进。

## 2. 核查范围与口径

### 2.1 数据来源

- 实验定义：`data/experiments/toolsandbox_partial_main.json`
- 汇总结果：`results/exp/toolsandbox_partial_main/index.json`
- 模型分数：`results/exp/toolsandbox_partial_main/scores.json`
- 实验指标：`results/exp/toolsandbox_partial_main/metrics.json`
- 单次 DEFAULT：`.../default/<case>/default_report.json`
- 单次 replay：`.../dynsteer_replay/<case>/report.json`
- agent 轨迹：`runs/exp/.../<method>/<case>/trajectory.json`
- 运行摘要：`runs/exp/.../<method>/<case>/raw_summary.json`

### 2.2 成功口径

- DEFAULT 成功：`default_report.json.resolved == true`，在本实验中等价于原生 ToolSandbox 总分为 1.0。
- replay 成功：结果文件没有 `resolved` 字段；本报告沿用 `metrics.json` 的派生口径，把 `milestone_coverage == "full"` 视为成功，把 `partial/none` 视为失败。
- replay 的 `overall_score` 是阶段质量加权分，不应与 DEFAULT 的原生 snapshot/message similarity 当作同一标尺直接比较。

## 3. 数据完整性与评估流程健康度

| 检查项 | 结果 | 判断 |
|---|---:|---|
| 配置期望记录数 | 600 | 正常 |
| trajectory / raw summary / result summary / report | 各 600 | 正常，无缺失 |
| DEFAULT / replay 记录数 | 各 300 | 正常 |
| replay timing 可用 | 300/300 | 正常 |
| replay LLM failed calls | 0 | 正常 |
| JSON 解析或负耗时 | 0 | 正常 |
| DEFAULT `resolved` 非空 | 300/300 | 正常 |
| replay `resolved` 非空 | 0/300 | **异常** |
| replay/source trajectory 对齐 | 250 份字节一致；50 份按 policy-stop 截短 | 与 replay 设计一致 |
| virtual stop | 52/300；实际轨迹改变 50/300 | 2 条在末端触发但未改变轨迹，建议明确记录原因 |

运行和评估管线整体完成，没有基础设施级中断。需要区分以下“轨迹内异常”和“评估流程异常”：

- agent 工具调用失败、空搜索结果和反复追问属于被评 agent 的行为，不是管线崩溃。
- `None` 是部分 set/modify 工具的正常成功返回，但 `runtime_quality_diagnostics.empty_tool_results` 仍将其计为空结果。例如四个低电量开关场景各出现 24 次 empty result 记录；该诊断会制造噪声。
- DEFAULT 有明显长尾：20/300 超过 60 秒、8/300 超过 120 秒、2/300 超过 300 秒。最大值是 `deepseek-v4-flash/r2/modify_contact_with_message_recency_alt_10_distraction_tools` 的 2,078.97 秒、51 agent steps、48 tool calls。另有多轮 user simulator 重复同一句话的长循环。这些记录最终完整落盘并评分，但说明 harness 缺少更有效的循环/超时保护。
- DEFAULT raw summary 中统一出现 `native_evaluation_skipped: true`，同时又保存了 `default_result`。从最终结果看原生评估确已执行或被后处理回填，但字段命名容易误导，建议改成能区分“运行时跳过、后处理已完成”的状态枚举。

## 4. 模型总体结果

### 4.1 分数、成功率与运行行为

| agent 模型 | DEFAULT 均分 | DEFAULT 成功 | replay 均分 | replay full | DEFAULT 平均耗时(s) | replay 评估耗时(s) | replay judge tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| deepseek-v4-flash | 0.8412 | 24/75 (32.0%) | 0.7991 | 58/75 (77.3%) | 47.97 | 8.24 | 410,667 |
| deepseek-v4-pro | 0.8562 | 24/75 (32.0%) | 0.8015 | 61/75 (81.3%) | 22.88 | 8.69 | 433,912 |
| qwen-plus-2025-12-01 | 0.8378 | 22/75 (29.3%) | 0.7713 | 59/75 (78.7%) | 26.46 | 9.22 | 464,171 |
| qwen3-max-2026-01-23 | 0.8476 | 23/75 (30.7%) | 0.7473 | 56/75 (74.7%) | 16.11 | 8.20 | 410,941 |
| **总体** | **0.8457** | **93/300 (31.0%)** | **0.7798** | **234/300 (78.0%)** | **28.35** | **8.59** | **1,719,691** |

注意：replay 评估耗时不包含生成源轨迹的完整 wall time；不能把 8.59 秒直接与 DEFAULT 的 28.35 秒解释为端到端加速。

### 4.2 每个 case 的模型执行结果

下表单元格为 `DEFAULT成功次数 / replay full次数`，每个模型每个 case 共 3 次 repeat。缩写：DS-F=deepseek-v4-flash，DS-P=deepseek-v4-pro，QW-P=qwen-plus，QW-M=qwen3-max。

| case | DS-F | DS-P | QW-P | QW-M |
|---|---:|---:|---:|---:|
| add_contact_with_name_and_phone_number_3_distraction_tools | 0/3 | 0/3 | 0/3 | 0/3 |
| add_reminder_content_and_date_and_time | 3/3 | 3/3 | 3/3 | 3/3 |
| add_reminder_content_and_date_and_time_10_distraction_tools | 3/3 | 3/3 | 3/3 | 3/3 |
| add_reminder_content_and_date_and_time_3_distraction_tools | 3/3 | 3/3 | 3/3 | 3/3 |
| add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled | 3/3 | 3/3 | 3/3 | 3/3 |
| find_days_till_holiday | 0/3 | 0/3 | 0/3 | 0/3 |
| find_days_till_holiday_insufficient_information | 1/1 | 0/0 | 0/0 | 0/0 |
| find_days_till_holiday_wifi_off_alt | 0/0 | 0/0 | 0/0 | 0/0 |
| modify_contact_with_message_recency_alt_10_distraction_tools | 0/0 | 0/2 | 0/0 | 0/0 |
| modify_contact_with_message_recency_insufficient_information | 2/2 | 3/3 | 2/2 | 3/3 |
| modify_contact_with_message_recency_insufficient_information_10_distraction_tools | 3/3 | 3/3 | 3/3 | 3/3 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools | 3/3 | 3/3 | 3/3 | 3/3 |
| modify_reminder_with_recency_latest | 3/3 | 3/2 | 2/2 | 2/2 |
| remove_contact_by_phone_no_remove_contact_insufficient_information | 0/3 | 0/3 | 0/3 | 0/1 |
| search_message_with_recency_oldest_multiple_user_turn | 0/1 | 0/2 | 0/3 | 0/3 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools | 0/2 | 0/1 | 0/3 | 0/3 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0/2 | 0/2 | 0/3 | 0/2 |
| send_message_with_contact_content_cellular_off | 0/2 | 0/3 | 0/3 | 0/3 |
| turn_on_cellular_low_battery_mode | 0/3 | 0/3 | 0/3 | 0/3 |
| turn_on_location_low_battery_mode | 0/3 | 0/3 | 0/3 | 0/3 |
| turn_on_location_low_battery_mode_3_distraction_tools | 0/3 | 0/3 | 0/3 | 0/3 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0/3 | 0/3 | 0/3 | 0/3 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0/3 | 0/3 | 0/3 | 0/0 |
| update_contact_relationship_with_relationship | 0/3 | 0/3 | 0/1 | 0/3 |
| update_contact_relationship_with_relationship_twice_multiple_user_turn | 0/0 | 0/1 | 0/0 | 0/0 |

## 5. 按 scenario / case 的核查

### 5.1 聚合表

| case | DEFAULT 均分/成功 | replay 均分/覆盖 | agent 执行与核查结论 |
|---|---:|---:|---|
| add_contact_with_name_and_phone_number_3_distraction_tools | 0.855 / 0/12 | 0.953 / 12 full | 12 次均完成联系人新增；DEFAULT 主要被确认文本相似度拉低，replay 语义复核更合理。 |
| add_reminder_content_and_date_and_time | 1.000 / 12/12 | 0.841 / 12 full | 全部正常；6 次 virtual stop 删除结束后的冗余尾部，不影响完成。 |
| add_reminder_content_and_date_and_time_10_distraction_tools | 1.000 / 12/12 | 0.841 / 12 full | 全部正常；干扰工具未造成失败。 |
| add_reminder_content_and_date_and_time_3_distraction_tools | 1.000 / 12/12 | 0.841 / 12 full | 全部正常。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled | 1.000 / 12/12 | 0.782 / 12 full | 全部完成，但 replay 对 Qwen 两模型给出系统性较低阶段分，需检查 judge/权重是否有模型风格偏差。 |
| find_days_till_holiday | 0.970 / 0/12 | 0.942 / 12 full | 工具链与答案均完成；DEFAULT 因最终措辞非满分而全部失败，replay 更合理。 |
| find_days_till_holiday_insufficient_information | 0.083 / 1/12 | 0.083 / 1 full、11 none | 11 次触发 minefield，1 次正确避免；两方法高度一致，结果正常。 |
| find_days_till_holiday_wifi_off_alt | 0.786 / 0/12 | 0.534 / 12 partial | 12 次先因 Wi-Fi 关闭失败，随后均开启 Wi-Fi 并恢复。replay 错误地未匹配实际存在的 `timestamp_diff` 调用，是阶段工具轨迹匹配假失败；replay 不比 DEFAULT 更合理。 |
| modify_contact_with_message_recency_alt_10_distraction_tools | 0.766 / 0/12 | 0.480 / 2 full、10 partial | 6 个 run 出现 7 次失败工具调用，12 个 run 都有空搜索结果，且存在长循环；replay 对执行质量的降分有依据，但 2,078.97 秒极端 run 说明应加循环/超时保护。 |
| modify_contact_with_message_recency_insufficient_information | 0.833 / 10/12 | 0.833 / 10 full、2 none | 成功/失败完全一致；空搜索和澄清行为较多，但结果口径正常。 |
| modify_contact_with_message_recency_insufficient_information_10_distraction_tools | 1.000 / 12/12 | 1.000 / 12 full | 全部正常；诊断中的 empty result 多为预期搜索结果或无返回值，不应直接视为流程故障。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools | 1.000 / 12/12 | 1.000 / 12 full | 全部正常。 |
| modify_reminder_with_recency_latest | 0.944 / 10/12 | 0.815 / 9 full、3 partial | 10 个 run 有 16 次工具失败。存在明确 replay 假失败：DS-P r0 虽前两次参数错误，但最终找到并成功修改提醒，DEFAULT=1.0；replay 在恢复动作前 policy-stop，判 partial。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information | 0.630 / 0/12 | 0.796 / 10 full、2 none | 工具集不支持删除时，多数 agent 正确说明限制；DEFAULT 被消息相似度（最低 0.575）误伤，replay 的语义复核更合理。2 个 none 需保留为真实失败。 |
| search_message_with_recency_oldest_multiple_user_turn | 0.814 / 0/12 | 0.741 / 9 full、1 partial、2 none | 多轮检索存在失败工具调用和空结果；replay 能区分恢复成功与未完成，比 DEFAULT 全失败更有信息，但需对 9 个 full 做抽样人工复核。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools | 0.864 / 0/12 | 0.741 / 9 full、1 partial、2 none | 与上一场景相似；13 次失败工具调用、17 次空结果，执行质量确有差异，replay 更有区分力。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.846 / 0/12 | 0.734 / 9 full、1 partial、2 none | 18 次失败工具调用，是三个搜索消息场景中最不稳定者；replay 的部分降级合理。 |
| send_message_with_contact_content_cellular_off | 0.931 / 0/12 | 0.920 / 11 full、1 partial | 8 个 run 先遇蜂窝网络关闭错误后恢复；DEFAULT 因非满分文本全部失败不合理，replay 对 11 个恢复成功 run 的判断更合理。 |
| turn_on_cellular_low_battery_mode | 0.902 / 0/12 | 0.936 / 12 full | 均先遇低电量限制，征得用户同意后关闭低电量并开启蜂窝；replay 更合理。正常返回 `None` 被计 empty warning，属诊断噪声。 |
| turn_on_location_low_battery_mode | 0.887 / 0/12 | 0.936 / 12 full | 12 次最终状态均正确；replay 更合理。 |
| turn_on_location_low_battery_mode_3_distraction_tools | 0.900 / 0/12 | 0.935 / 12 full | 12 次最终状态均正确；干扰工具未破坏执行。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0.892 / 0/12 | 0.936 / 12 full | 12 次最终状态均正确；replay 更合理。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0.706 / 0/12 | 0.701 / 9 full、3 none | QW-M 三次均失败且均分仅 0.194/0.000；其余 9 次完成。replay 对模型/类型扰动的区分合理。 |
| update_contact_relationship_with_relationship | 0.741 / 0/12 | 0.777 / 10 full、2 none | 10 次状态更新完成但 DEFAULT 被消息/中间状态相似度压低；replay 通常更合理。QW-P 有 2 次真实未完成。 |
| update_contact_relationship_with_relationship_twice_multiple_user_turn | 0.792 / 0/12 | 0.398 / 1 full、11 partial | 多数 run 的 user simulator 偏离目标并反复发送无关/缺信息请求，45 次空搜索结果；replay 判 partial 基本合理。需把 simulator 漂移与 agent 能力分别计量。 |

### 5.2 关键不一致样例

#### replay 明显更合理：语义正确但 DEFAULT 文本相似度不足

- `qwen-plus-2025-12-01/r2/remove_contact_by_phone_no_remove_contact_insufficient_information`
- DEFAULT：0.5754、失败。
- agent 正确搜索到联系人，明确说明当前工具不具备删除能力，并给出手动操作建议。
- replay：语义消息复核置信度 0.95，判断实际文本与参考文本核心含义一致，最终 full、0.955。
- 结论：DEFAULT 把表述差异当任务失败；replay 更符合任务事实。

类似模式还出现在新增联系人、节日天数、低电量模式下开启网络/定位、发送消息等场景。

#### replay 明显不合理：policy-stop 阻断后续恢复

- `deepseek-v4-pro/r0/modify_reminder_with_recency_latest`
- DEFAULT：最终成功修改提醒，原生分数 1.0、成功。
- agent 前两次 `search_reminder` 参数错误，第三次搜索成功，随后正确调用 `modify_reminder` 并向用户确认。
- replay：在 step 26 搜索恢复成功处触发 virtual stop，源轨迹 17 steps 被截为 11 steps，未看到 step 28 的最终修改，判 partial、0.4093。
- 结论：replay 对早期工具质量的批评合理，但把“过程低效”升级成“任务未完成”不合理；这正是 1 条 DEFAULT 成功→replay 失败。

#### replay 阶段匹配错误：存在的工具调用未被识别

- `qwen-plus-2025-12-01/r2/find_days_till_holiday_wifi_off_alt`
- agent 首次查询因 Wi-Fi 关闭失败，随后开启 Wi-Fi、重新查询节日时间、调用 `timestamp_diff`，并返回天数。
- DEFAULT 连续分数 0.9605，但因非满分仍记失败。
- replay 在 m3 报告“Required tool call not matched: timestamp_diff, expected_arguments={}”，而 trajectory step 26 明确存在带实际参数的 `timestamp_diff` 调用，最终判 partial、0.5547。
- 结论：两个二元成功判定都不理想；replay 的失败原因属于 matcher bug。

## 6. DEFAULT 与 DYNSTEER_REPLAY 成功一致性

| 配对结果 | 数量 | 占比 |
|---|---:|---:|
| DEFAULT 成功、replay full | 92 | 30.67% |
| DEFAULT 失败、replay 非 full | 65 | 21.67% |
| DEFAULT 失败、replay full | 142 | 47.33% |
| DEFAULT 成功、replay 非 full | 1 | 0.33% |
| **一致** | **157** | **52.33%** |

142 条 DEFAULT 失败→replay full 的 DEFAULT 原生分数分布：

- `[0.9, 1.0)`：79 条
- `[0.8, 0.9)`：51 条
- `[0.5, 0.8)`：12 条

前两档共 130/142，且多为状态已正确、确认文本不完全一致，replay 大概率更合理。低于 0.8 的 12 条集中在“不具备删除联系人工具时正确拒绝”、联系人关系更新和新增联系人等场景；其中 replay 报告给出了结构化状态或语义等价证据，但仍建议纳入人工金标集复核，不能仅靠 replay 自证正确。

## 7. 模型区分度与稳定性

### 7.1 区分度

| 指标 | DEFAULT | replay | 结论 |
|---|---:|---:|---|
| 四模型均分 | 0.8457 | 0.7798 | 标尺不同，不比较绝对高低 |
| 模型均分总体标准差 | 0.0070 | 0.0222 | replay 为 DEFAULT 的 3.17 倍 |
| ε=0.01 显著模型对 | 2/6 | 5/6 | replay 更强 |
| ε=0.02 显著模型对 | 0/6 | 5/6 | replay 更强 |
| ε=0.03 显著模型对 | 0/6 | 3/6 | replay 更强 |
| ε=0.04/0.05 显著模型对 | 0/6 | 2/6 | replay 更强 |
| 二元成功率模型间跨度 | 2.67 个百分点 | 6.67 个百分点 | replay 更强 |

从纯“拉开模型分数”看，DYNSTEER_REPLAY 明显优于 DEFAULT。DEFAULT 的精确满分制把大量近似正确结果都压为失败，导致四个模型集中在很窄区间。

### 7.2 排名与 repeat 稳定性

- DEFAULT 排名：DS-P > QW-M > DS-F > QW-P。
- replay 排名：DS-P > DS-F > QW-P > QW-M。
- replay 相对 DEFAULT 的总体 Kendall tau = 0.3333；按 repeat 分别为 0.3914、0.4566、0.4731。
- 每模型 repeat 分数总体标准差：
  - DS-F：0.0250 → 0.0357，变差。
  - DS-P：0.0063 → 0.0161，变差。
  - QW-P：0.0209 → 0.0104，改善。
  - QW-M：0.0034 → 0.0148，变差。

因此，replay 的区分度提升伴随排序重排和较高随机波动。当前没有独立人工金标或能力先验能证明 replay 新排序更准确。尤其 replay judge 使用可漂移别名 `qwen-plus-latest`，不是固定版本，进一步影响可复现性。

## 8. 效率与成本

### 8.1 时间与步骤

| 指标 | DEFAULT/source 完整轨迹 | replay |
|---|---:|---:|
| 记录数 | 300 | 300 |
| 平均 wall/evaluator elapsed | 28.35 s | 8.59 s（仅 replay 评估） |
| 中位数 | 10.67 s | 8.65 s |
| p95 | 70.25 s | 19.90 s |
| 源轨迹 raw steps | 4,087 | 3,636（虚拟截断后） |
| 源轨迹 tool calls | 1,164 | 1,047 |
| 源完整执行时间 | 5,816.01 s | — |
| replay 前缀执行时间 | — | 4,951.08 s |
| replay evaluator 时间 | — | 2,575.75 s |
| replay 有效总时间 | — | 7,526.83 s |

replay 虚拟早停节省了 11.03% raw steps、10.05% tool calls 和 14.87% 源执行时间；但 evaluator 开销超过节省量：

- 相对源完整执行时间：`7,526.83 / 5,816.01 - 1 = +29.42%`。
- 如果拿 replay 有效总时长与 DEFAULT wall time 8,506.23 秒比较，表面上低 11.51%；但前者用 execution latency，后者用 wall time，存在口径不一致，不能作为真实加速结论。
- 当前 replay 是离线复用 DEFAULT trajectory；250/300 根本没有改变轨迹。只有把 policy-stop 真正前移到在线 agent 执行，并确保不会引入假失败，14.87% 的理论节省才可能转化成真实节省。

### 8.2 token 与费用

- agent trajectory token：两方法均记录为 0，`trajectory_cost_available=false`，无法计算被评 agent 的 token 或货币成本。
- DEFAULT 原生评估 LLM calls：0。
- replay judge：438 次成功调用，236/300 cases 有 LLM tokens，合计 1,719,691 tokens；64 cases 为纯结构化/规则评估，无 LLM 调用。
- replay judge 使用 `qwen-plus-latest`；项目记录了 token，但没有按方法输出货币总价。
- `metrics.json.cost` 只给整个实验的合计，不按 DEFAULT/replay、模型、case 拆分；`metrics.json.efficiency` 也混合 600 条记录，不能直接用于方法对比。

成本结论：在现有可观测字段下，replay 明确增加评估 token 和调用次数，无法证明总成本下降；若要声称成本优势，必须补全 agent token、judge token、缓存命中、单价和在线早停节省的同口径核算。

## 9. DYNSTEER_REPLAY 是否优于 DEFAULT

| 维度 | 判断 | 说明 |
|---|---|---|
| 语义合理性 | **多数场景优于** | 能纠正 DEFAULT 对非标准措辞、语义等价确认文本的误伤。 |
| 过程诊断 | **优于** | 有 milestone、阶段分、工具质量、状态一致性、证据和 diagnosis。 |
| 模型分数区分度 | **优于** | 模型分数标准差 3.17 倍，ε=0.02 下显著模型对 5/6 vs 0/6。 |
| 二元成功可靠性 | **尚未优于** | 成功字段缺失；一致率仅 52.33%；存在已确认 matcher/policy-stop 假失败。 |
| repeat 稳定性 | **总体不优于** | 4 个模型中 3 个 replay 分数标准差变大。 |
| 在线效率 | **尚未证明** | 只有虚拟早停；评估开销使同口径有效时间高 29.42%。 |
| token/货币成本 | **不优于或不可判定** | 新增 1.72M judge tokens，agent 成本与货币成本缺失。 |
| 可复现性 | **不优于** | judge 使用 `latest` 别名，成功派生逻辑与 result schema 不一致。 |

综合结论：**DYNSTEER_REPLAY 在“评分解释力与区分度”上优于 DEFAULT，但在“可作为最终成功判定、效率和成本”上尚未优于 DEFAULT。** 推荐短期并行保留两者：DEFAULT 作为可重复的原生结构化基线，replay 作为语义/过程诊断器；在完成下述 P0 修复和人工校准前，不应仅发布 replay 成功率。

## 10. 改进建议

### P0：影响结论可信度

1. **统一结果 schema 和成功口径**
   - replay `summary.json`、`report.json`、`index.json` 必须显式写入 `resolved`。
   - 同时记录 `success_basis`（如 `native_exact`、`milestone_full`、`human_adjudicated`），避免 `metrics.json` 私下派生另一套成功定义。
   - DEFAULT 与 replay 保留各自 score，但不得把两者当同一连续量直接求差。

2. **修复工具轨迹 matcher**
   - `expected_arguments={}` 应明确表示“任意合法参数”还是“必须空参数”；当前 `timestamp_diff` 实际调用被漏匹配。
   - 为有实际参数、参数次序变化、可选参数省略和类型扰动建立回归用例。

3. **修复 policy-stop 的恢复误判**
   - 非 fatal 工具错误后增加 recovery grace window，不能在第一次有效恢复结果处立即停止。
   - 离线 replay 同时输出“完整源轨迹结论”和“假设早停结论”，把任务真实完成度与策略反事实分开。
   - 对 `replay_continue_after_virtual_stop=false` 做消融；至少在 recovery 维度未结算前继续。

4. **建立人工裁决集**
   - 全量复核 1 条 DEFAULT 成功→replay 失败。
   - 优先复核 12 条 DEFAULT<0.8 但 replay full、6 条 DEFAULT≥0.9 但 replay 非 full。
   - 从其余 130 条高分不一致中按 case/model/repeat 分层抽样，计算 replay 相对人工金标的 precision/recall，而不是用 DEFAULT 自身作为真值。

### P1：效率、稳定性与可复现性

5. **同口径成本报表**
   - 按 method/model/case/repeat 分别记录 agent prompt/completion tokens、judge tokens、货币成本、wall time、execution time、缓存节省。
   - replay 单列 `source_generation_cost`、`replay_evaluation_cost`、`counterfactual_saved_cost`，禁止把复用源轨迹的离线时间直接宣称为在线节省。

6. **固定 judge 和评估配置**
   - 将 `qwen-plus-latest` 改为固定版本，记录 temperature、seed、prompt/version hash、threshold profile。
   - 对 replay judge 做重复评估，单独估计“被评 agent 随机性”和“judge 随机性”。

7. **降低诊断噪声**
   - 工具成功且返回 `None` 时，不计 `empty_tool_results` warning。
   - 区分合法空搜索结果、工具异常、无 grounding 和预期拒绝场景。

8. **分离结果正确性和过程质量**
   - agent 最终恢复成功时，coverage/resolved 应保持成功；早期失败、冗余调用和低效单独体现在 quality/efficiency 分，不应反向抹除最终完成事实。
   - 为每个 case 同时发布 outcome score、process score、safety score 和 binary success。

9. **控制长循环和 simulator 漂移**
   - 对重复 user utterance、重复空结果和连续无进展设置可观测的 stall detector。
   - 超时/最大轮次应单独标记 `infrastructure_timeout` 或 `simulator_stall`，不要与 agent task failure 混为一类。

### P0：安全问题

10. **移除并轮换明文凭据**
    - `data/experiments/toolsandbox_partial_main.json` 和 `data/toolsandbox/run_configs.json` 含明文 API key，违反项目安全约束。
    - 应立即轮换已暴露凭据，配置中改用环境变量引用或本地不入库的 secret 文件；报告中不复述任何 key 值。

## 11. 最终建议

下一轮实验建议采用以下验收门槛后再判断 replay 是否可替代 DEFAULT：

1. replay `resolved` 完整率 100%，并与 `metrics.json` 口径一致。
2. 已知两个假失败（`modify_reminder...`、`find_days...wifi_off_alt`）通过回归。
3. 人工裁决集上 replay binary success 的 precision/recall 均达到预设阈值。
4. judge 固定版本后，repeat/judge 双重方差不高于 DEFAULT 或在报告中可解释。
5. 在线早停真实运行后，按同口径计算的端到端 wall time、agent token 和总货币成本确有下降。

在这些条件满足前，推荐对外报告：

- DEFAULT 原生连续分数和 exact success；
- replay milestone coverage、过程分和诊断；
- 两者不一致率及人工裁决率；
- 不发布单一“replay 78% vs DEFAULT 31%”作为方法优越性的证据。
