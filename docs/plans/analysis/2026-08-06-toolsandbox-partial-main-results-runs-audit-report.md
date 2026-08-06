# `toolsandbox_partial_main` 评估结果与运行记录核查报告

## 1. 核查范围与结论摘要

核查对象为：

- 配置：`data/experiments/toolsandbox_partial_main.json`
- 结果索引、汇总指标：`results/exp/toolsandbox_partial_main/index.json`、`scores.json`、`metrics.json`
- 每 case 评估：`results/exp/toolsandbox_partial_main/toolsandbox/...`
- 每 case 运行轨迹：`runs/exp/toolsandbox_partial_main/toolsandbox/...`
- 运行日志：`logs/2026-08-04.log`

实验矩阵完整：4 个模型 × 25 个 case × 3 次重复 × 2 个方法 = 600 条结果。索引中的 600 个 `summary.json`、600 个 replay/default 报告和 600 条 `trajectory.json` 均存在，JSON 全部可解析，索引引用的输出路径全部有效。

核心判断：

1. **运行流程总体正常**。本实验在日志中正常完成 600 条 case；没有本实验 ID 对应的致命 benchmark 异常，也没有 replay Judge 调用失败。轨迹中的工具异常主要是 Agent 在场景中调用错误工具/错误参数后由 ToolSandbox 返回的任务级错误，不是评估器崩溃。
2. **DEFAULT 与 replay 的“成功”不是同一口径**。DEFAULT 使用 ToolSandbox 原生 `resolved`（实际只有 native score=1.0 才为 true）；`metrics.json` 的 replay 成功一致性则把 `milestone_coverage == "full"` 当作 replay_success。两者不能直接解释为同一准确率。
3. **结构完成度上 replay 明显更宽松**：DEFAULT `resolved=true` 为 93/300（31.0%），replay `full` 为 234/300（78.0%），配对一致率为 157/300（52.33%）。不一致的 143 条中，1 条是 DEFAULT 成功而 replay 未 full，142 条是 DEFAULT 未 resolved 而 replay full。
4. **质量分数上 replay 反而更低且更不稳定**：全体平均分 DEFAULT=0.84569，replay=0.77981，平均差值 replay−DEFAULT=-0.06588。replay 的分数是阶段质量 Judge 的结果，不是原生 milestone similarity 的替代品。
5. **模型区分度的名义指标 replay 更好，但不能据此判定整体优于 DEFAULT**：在 epsilon=0.01 时，Discriminability Score 从 0.00478 提升到 0.02600，显著模型对从 2/6 提升到 5/6；但模型排序 Kendall tau 只有 0.333（按 repeat 为 0.391、0.457、0.473），且 replay 平均分下降、成功口径变宽、分数压缩明显。因此当前结论是：**DYNSTEER_REPLAY 在“拉开模型分数”这一单项指标上优于 DEFAULT，但作为可靠的整体评估方案尚不能判定优于 DEFAULT。**

## 2. 逐 case 对照

下表按 12 个配对样本（4 模型 × 3 repeat）汇总：

- `D 分/成功`：DEFAULT 平均 native score / `resolved=true` 次数；
- `R 分/full`：replay 平均 overall score / `milestone_coverage=full` 次数；
- `Δ`：两种平均分之差；
- `coverage`：replay 的 full/partial/none 次数；
- `V/C`：replay 触发 virtual stop / 轨迹被截断改变的次数。

| case | D 分/成功 | R 分/full | Δ | coverage | V/C | 核查判断 |
|---|---:|---:|---:|---|---:|---|
| add_contact_with_name_and_phone_number_3_distraction_tools | 0.855 / 0 | 0.953 / 12 | +0.098 | 12/0/0 | 0/0 | replay 结构完成明显更宽松；从任务完成角度可能更合理，但需人工抽查参数/最终状态。 |
| add_reminder_content_and_date_and_time | 1.000 / 12 | 0.841 / 12 | -0.159 | 12/0/0 | 6/6 | 结构结果一致；replay 因质量阶段/virtual stop 降分，不能称为任务失败。 |
| add_reminder_content_and_date_and_time_10_distraction_tools | 1.000 / 12 | 0.841 / 12 | -0.159 | 12/0/0 | 6/6 | 同上。 |
| add_reminder_content_and_date_and_time_3_distraction_tools | 1.000 / 12 | 0.841 / 12 | -0.159 | 12/0/0 | 6/6 | 同上。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled | 1.000 / 12 | 0.782 / 12 | -0.218 | 12/0/0 | 9/9 | 结构一致但质量惩罚更重，说明 replay stop/quality 与 native 成功混在一起。 |
| find_days_till_holiday | 0.970 / 0 | 0.942 / 12 | -0.028 | 12/0/0 | 0/0 | DEFAULT 高相似度但严格 resolved=false；replay full 更接近“已完成”，但两者判据不一致。 |
| find_days_till_holiday_insufficient_information | 0.083 / 1 | 0.083 / 1 | +0.000 | 1/11/0 | 11/11 | 基本一致；minefield/信息不足路径被 replay 保留为 none。 |
| find_days_till_holiday_wifi_off_alt | 0.786 / 0 | 0.534 / 0 | -0.252 | 0/12/0 | 0/0 | 两者均未 full；wifi 禁用导致的连接错误是场景预期，replay 未改善。 |
| modify_contact_with_message_recency_alt_10_distraction_tools | 0.766 / 0 | 0.480 / 2 | -0.286 | 2/10/0 | 3/3 | replay 多数在 milestone_no_progress 前停止，结果更保守；应检查工具搜索失败归因。 |
| modify_contact_with_message_recency_insufficient_information | 0.833 / 10 | 0.833 / 10 | +0.000 | 10/0/2 | 2/2 | 成功/失败计数完全一致。 |
| modify_contact_with_message_recency_insufficient_information_10_distraction_tools | 1.000 / 12 | 1.000 / 12 | +0.000 | 12/0/0 | 0/0 | 完全一致。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools | 1.000 / 12 | 1.000 / 12 | +0.000 | 12/0/0 | 0/0 | 完全一致。 |
| modify_reminder_with_recency_latest | 0.944 / 10 | 0.815 / 9 | -0.130 | 9/3/0 | 2/1 | 有 1 条 DEFAULT 成功→replay 非 full；与已知“早期工具错误触发质量 stop、后缀才恢复”一致。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information | 0.630 / 0 | 0.796 / 10 | +0.165 | 10/0/2 | 0/0 | replay 可能正确识别“信息不足时保持状态/澄清”的语义，但 native 未 resolved；需独立黄金标签确认，不能直接视为提升。 |
| search_message_with_recency_oldest_multiple_user_turn | 0.814 / 0 | 0.741 / 9 | -0.072 | 9/1/2 | 2/1 | replay 部分 full，但分数较低；质量与结构应拆开报告。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools | 0.864 / 0 | 0.741 / 9 | -0.122 | 9/1/2 | 1/1 | 同上，replay 未显示稳定质量优势。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.846 / 0 | 0.734 / 9 | -0.112 | 9/1/2 | 0/0 | replay 结构覆盖增加但质量分降低，需检查工具描述扰动下的判定校准。 |
| send_message_with_contact_content_cellular_off | 0.931 / 0 | 0.920 / 11 | -0.011 | 11/1/0 | 0/0 | replay 与 DEFAULT 高分趋势一致，full 率略高；native strict resolved 造成表面不一致。 |
| turn_on_cellular_low_battery_mode | 0.902 / 0 | 0.936 / 12 | +0.034 | 12/0/0 | 0/0 | replay 对设置任务判为 full，可能比 strict native 标签更符合完成语义。 |
| turn_on_location_low_battery_mode | 0.887 / 0 | 0.936 / 12 | +0.049 | 12/0/0 | 0/0 | 同上。 |
| turn_on_location_low_battery_mode_3_distraction_tools | 0.900 / 0 | 0.935 / 12 | +0.035 | 12/0/0 | 0/0 | 同上。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0.892 / 0 | 0.936 / 12 | +0.044 | 12/0/0 | 0/0 | 同上，未见 distraction 造成结构性丢失。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0.706 / 0 | 0.701 / 9 | -0.005 | 9/0/3 | 3/3 | replay 基本保持 DEFAULT 的低质量方向，3 条 none 与参数类型扰动相关。 |
| update_contact_relationship_with_relationship | 0.741 / 0 | 0.777 / 10 | +0.035 | 10/0/2 | 0/0 | replay full 增多，但仍需核查关系字段最终值，不能只看 full。 |
| update_contact_relationship_with_relationship_twice_multiple_user_turn | 0.792 / 0 | 0.398 / 1 | -0.395 | 1/11/0 | 1/1 | replay 明显更差；多轮顺序 milestone 未完成时过早停止/覆盖不足，是当前最突出失败场景。 |

总体配对结果：`success_consistency` 为 157/300=52.33%；其中 DEFAULT 成功→replay 非 full 1 条，DEFAULT 未 resolved→replay full 142 条。后者主要集中在 DEFAULT native score 已较高但未达到严格 1.0 的场景，并不自动证明 replay 误报。

## 3. Agent 执行与运行记录

### 3.1 文件、索引和时间一致性

- 600 条索引记录均能定位到 raw run、result、report、summary、trajectory 五类路径；没有断链或缺文件。
- 600 个结果 JSON 全部可解析；在 summary/report 同时提供的字段上，`overall_score`、`milestone_coverage` 一致（DEFAULT 报告本身不写 replay coverage 字段）。
- 所有 summary 的 `finished_at - started_at` 与 `elapsed_seconds` 误差小于 0.1 秒，时间记录正常。
- `logs/2026-08-04.log` 记录了“统一实验完成，case结果数量: 600”。同一实验期间有 1 次 ToolSandbox role response JSON 解析重试（`modify_contact_with_message_recency_alt_10_distraction_tools`），之后实验仍完整结束；未见该实验 ID 的 fatal benchmark error。

### 3.2 DEFAULT 轨迹

300 条 DEFAULT trajectory 的 step/snapshot 数量一致，工具调用与工具结果均可配对。141/300 条轨迹包含 ToolSandbox 工具异常，主要是：

- 信息不足时无搜索条件；
- Wi‑Fi/蜂窝网络未开启；
- 低电量模式下位置/蜂窝权限限制；
- 时间参数越界；
- 参数类型错误。

这些异常与 case 的干扰、权限和错误恢复设计相符，属于 Agent 执行证据；不能把它们当成 evaluator 崩溃。原生评估仍能为每条轨迹产出 score/report。

### 3.3 DYNSTEER_REPLAY 轨迹与停止

- coverage：full 234、partial 40、none 26。
- virtual stop：52 条；其中 27 条 stop 后仍恢复到 full，8 条停在 partial，17 条停在 none。
- `trajectory_changed=true` 50 条；其余 replay 使用完整 DEFAULT 轨迹。
- replay trajectory 中 tool_call 数为 1,047、tool_result 数为 1,031，差额 16 条均出现在 virtual-stop 截断路径；应在输出中明确这是截断造成的未完成后缀，而不是环境执行丢结果。
- replay evaluator 无失败 LLM 调用（`llm_failed_call_count=0`），共 438 次 Judge 调用、1,719,691 tokens；缓存命中/重复 prompt 避免各 552 次。
- 有 27 条“full 但某阶段 status=fail”的报告，均为 `evaluation_policy_stop` 后 `recovered_after_virtual_stop=true`。这表示结构 milestone 已覆盖但阶段质量分低，属于当前 DYNSTEER 的设计语义，不是文件损坏；但输出字段容易被误读为“任务失败”。

一个需要重点修复的诊断一致性例子是：`qwen-plus-2025-12-01/r0/add_reminder_content_and_date_and_time` 的 replay report 中，`m0->m1` stage score=0.272，诊断称 REMINDER 目标行未匹配；同一报告的结构化 `m1_c0` addition similarity 又为 1.0，finish 重检也为 pass=1.0。说明 Judge 使用的证据前缀/状态快照与结构化 scorer 之间存在不一致，应增加 snapshot ID、约束命中行和 judge 输入的逐字段对照。

## 4. DEFAULT 与 replay 的评估口径与合理性

### 4.1 不能直接比较二元成功率

DEFAULT 的 `resolved` 是 ToolSandbox native 二元标签，严格要求原生评分达到完成条件；例如 `find_days_till_holiday` 的 native score 均值约 0.970，但 12 条均为 `resolved=false`。replay 的 `full` 表示 milestone graph 覆盖完整，即使阶段质量 Judge 曾给出 fail，也可能最终为 full。因此“31.0% vs 78.0%”是两个不同事件的比例，不应写成 replay 准确率提升 47 个百分点。

### 4.2 replay 更合理的情形

从任务完成语义而非 strict native 标签看，下列模式中 replay 更可能提供有用信息：

- DEFAULT score 约 0.89–0.97、无 minefield、但因未达到 1.0 全部 unresolved 的设置/查询/消息场景；replay 多数 full（如 `find_days_till_holiday`、四个 low-battery 设置 case、`send_message...`）。
- 信息不足或应保持状态的 case（如 `remove_contact_by_phone...`）中，replay full 可能比“非 1.0 即失败”更符合用户意图，但必须用独立黄金标签确认。

### 4.3 replay 更不合理或需要谨慎的情形

- DEFAULT 已 12/12 resolved 的四个 add-reminder case，replay 虽 full，却有 6–9 条触发 quality stop，overall score 降至 0.782–0.841；这不是任务完成度下降，而是质量分和结构完成被混成一个总分。
- `modify_reminder_with_recency_latest` 有 1 条明确 DEFAULT success→replay 非 full，且与“早期错误搜索导致 online policy stop、后缀才恢复”的 replay 语义一致；这体现 replay 的在线决策性质，但不应当覆盖 native 最终完成标签。
- `update_contact_relationship_with_relationship_twice_multiple_user_turn` 的 replay full 仅 1/12、均分 0.398，说明多轮顺序 milestone 对当前 replay 很脆弱；不能据此说 replay 优于 DEFAULT。
- `find_days_till_holiday_wifi_off_alt` 与 `modify_contact...alt_10_distraction_tools` 的 replay 主要 partial，表明网络条件和工具搜索失败下的诊断/停止仍需改进。

## 5. DEFAULT 与 DYNSTEER_REPLAY 的实验指标比较

### 5.1 模型分数与排序

| method | deepseek-v4-flash | deepseek-v4-pro | qwen-plus-2025-12-01 | qwen3-max-2026-01-23 | 均值 |
|---|---:|---:|---:|---:|---:|
| DEFAULT | 0.84116 | 0.85618 | 0.83782 | 0.84760 | 0.84569 |
| DYNSTEER_REPLAY | 0.79913 | 0.80148 | 0.77133 | 0.74729 | 0.77981 |

DEFAULT 排序为 `pro > qwen3-max > flash > qwen-plus`；replay 排序为 `pro > flash > qwen-plus > qwen3-max`，两者之间的总体 Kendall tau=0.333 仅作为参考，不作为方法优劣的主要判据。replay 分数还出现明显压缩：300 条 case 的 unique score 数 DEFAULT 为 139，replay 仅 58；大量结果集中在 0.93595、0.72298、0.95876 等固定值，说明 Judge/阶段模板可能主导了分数，削弱了细粒度 Agent 差异解释。

### 5.2 Discriminability Score

| epsilon | DEFAULT DS | DEFAULT 显著对比例 | replay DS | replay 显著对比例 |
|---:|---:|---:|---:|---:|
| 0.01 | 0.00478 | 2/6 (33.3%) | 0.02600 | 5/6 (83.3%) |
| 0.02 | 0 | 0/6 | 0.02600 | 5/6 (83.3%) |
| 0.03 | 0 | 0/6 | 0.02014 | 3/6 (50.0%) |
| 0.04 | 0 | 0/6 | 0.01644 | 2/6 (33.3%) |
| 0.05 | 0 | 0/6 | 0.01644 | 2/6 (33.3%) |

因此，若只看“不同模型的平均分是否被拉开”，replay 优于 DEFAULT；但这部分提升伴随平均分下降、排序改变、分数模板化和更宽的 full 判据，当前不能解释为更高的评估有效性。

### 5.3 重复稳定性与成本/效率

- DEFAULT 三次 repeat 的模型分数标准差：flash 0.0250、pro 0.0063、qwen-plus 0.0209、qwen3-max 0.0034。
- replay 标准差：flash 0.0357、pro 0.0161、qwen-plus 0.0104、qwen3-max 0.0148；flash/pro/qwen3 的 repeat 分数波动更大或相近，不能说 replay 更稳定。
- DEFAULT 平均 wall-clock 28.35 秒/case；replay evaluator 自身平均 8.59 秒/case。若加上复用的 DEFAULT prefix，replay effective elapsed 平均 25.09 秒/case，约比 DEFAULT wall-clock 少 11.5%，但这不是完全同工作量比较。
- replay 使用 1,719,691 个 Judge tokens（轨迹 token 不可用），因此“更快”不等于“更便宜”；应同时报告 token/费用和 prefix、Judge、effective 三种时间口径。

### 5.4 同一方法跨 repeat 的模型排名一致性

这里不比较 DEFAULT 与 replay 的排名，而是分别在每种方法内部做跨 repeat 比较。每个 repeat 先按该 repeat 内 25 个 case 的模型平均分排序，再对 3 个 repeat 两两计算 Kendall tau；4 个模型无并列分数。

| method | repeat 0 排名 | repeat 1 排名 | repeat 2 排名 | 两两 tau | 平均 tau | 完全相同排序对数 | 平均绝对名次位移 |
|---|---|---|---|---|---:|---:|---:|
| DEFAULT | flash > pro > qwen-plus > qwen3-max | flash > qwen-plus > qwen3-max > pro | pro > qwen3-max > qwen-plus > flash | 0.333、-0.333、-1.000 | -0.333 | 0/3 | 1.500 |
| DYNSTEER_REPLAY | flash > pro > qwen-plus > qwen3-max | flash > pro > qwen-plus > qwen3-max | pro > qwen-plus > flash > qwen3-max | 1.000、0.333、0.333 | 0.556 | 1/3 | 0.667 |

补充观察：两种方法的 top-1 repeat 一致对数都为 1/3（DEFAULT：repeat 0/1 为 flash，repeat 2 为 pro；replay 同样在 repeat 2 切换为 pro）。因此 replay 的改善主要是完整排序更稳定、平均名次位移更小，而不是 top-1 模型已经稳定。

这项结果支持一个较温和的判断：**在本实验的 3 次 repeat 样本中，REPLAY 的跨 repeat 排名稳定性优于 DEFAULT（平均 tau -0.333 vs 0.556，完全排序一致 0/3 vs 1/3），但样本数只有 3，不能据此证明 replay 的统计稳定性已经充分。** 该指标比 DEFAULT↔REPLAY 的 rank_tau 更适合回答“同一评估方法是否稳定地区分模型”，但仍应与分数方差、置信区间和 case-level 稳定性一起使用。

## 6. 改进优先级

### P0：统一并拆分评估语义

1. 为每条结果同时输出 `native_task_success`、`structural_completion`、`quality_score`、`policy_stop`，不要把 `resolved` 和 `milestone_coverage=full` 放在同一个 success_consistency 二元指标中。
2. replay 轨迹应在相同 prefix 上重新执行原生 ToolSandbox scorer，形成与 DEFAULT 可直接比较的 `replay_native_score/resolved`；阶段 Judge 分数另列，不能替代 native task label。
3. 对“full 但 stage fail/recovered”保留双标签：结构完成仍为 full，质量失败仅影响 quality outcome，不回写为任务失败。

### P1：修复证据与停止决策一致性

1. 在 Judge 输入、结构化 scorer 和 finish recheck 中记录同一 `snapshot_id`/step prefix；出现“constraint score=1 但 matched rows=0”时自动标记 `evidence_inconsistency` 并阻止静默聚合。
2. 保留 minefield/fatal stop（本实验 13 条 minefield 均被 replay 识别为 none），但将 `evaluation_policy_stop`、`milestone_no_progress`、`milestone_predecessor_gap` 分开统计。
3. 对多轮顺序任务增加针对性回归集，重点修复 `update_contact_relationship_with_relationship_twice_multiple_user_turn` 的 predecessor/前缀处理；对 wifi-off 和参数类型扰动 case 增加工具失败归因检查。

### P1：校准 Judge 与模型区分度

1. 用 native scorer 产出的黄金标签/人工抽样对 cheap/standard Judge 做校准，报告 AUROC、F1、Brier/ECE，而不是只报告 full 率和 DS。
2. 固定 Judge 模型会引入系统偏差；应至少做 Judge 交叉验证或少量人工双盲复核，并按 case 类型报告分数压缩和模型对差异。
3. 增加按 case 的 discriminability（均值、P90、零方差 case 数），确认 DS 提升不是少数固定模板分数造成的。

### P2：完善成本与重复实验报告

按 method 拆分 wall/prefix/effective 时间、Judge 调用和 token 成本；保留 3 次 repeat 的置信区间和逐 case 方差。当前 replay 的缓存命中较高，但 semantic review cache 为 0，需确认缓存键是否覆盖可复用的阶段证据。

## 7. 最终结论

`toolsandbox_partial_main` 的运行产物完整、流程未发生系统性中断，DEFAULT 和 replay 都能稳定落盘并生成报告。replay 在结构 milestone 覆盖、名义模型区分度以及本实验 3 次 repeat 内的模型排名稳定性上有优势，但其二元“成功”口径比 DEFAULT 宽，整体质量均分更低，分数还存在明显模板化；同时存在 full/quality-fail 混合和 Judge/scorer 证据不一致案例。

所以当前不建议下结论“DYNSTEER_REPLAY 全面优于 DEFAULT”。更准确的表述是：**replay 是一个能在同一 DEFAULT 轨迹上提供阶段质量、停止策略和模型区分信号的补充评估器；在统一 native 成功标签、修复证据对齐并完成 Judge 校准之前，不应把它作为 DEFAULT 的无条件替代。**
