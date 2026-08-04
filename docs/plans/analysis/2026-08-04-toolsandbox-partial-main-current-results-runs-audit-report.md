# 2026-08-04 `toolsandbox_partial_main` 当前评估结果与运行记录核查报告

## 1. 核查范围、版本与判定口径

本报告只核查当前工作区已经存在的实验产物，不重新调用 Agent、User Simulator 或 Judge。数据来源如下：

- 实验配置：`data/experiments/toolsandbox_partial_main.json`
- 聚合索引：`results/exp/toolsandbox_partial_main/index.json`
- 模型得分：`results/exp/toolsandbox_partial_main/scores.json`
- 聚合指标：`results/exp/toolsandbox_partial_main/metrics.json`
- 逐 case 结果：`results/exp/toolsandbox_partial_main/toolsandbox/...`
- 逐 case 运行记录：`runs/exp/toolsandbox_partial_main/toolsandbox/...`
- ToolSandbox 原始 scenario/milestone 定义：`../ToolSandbox/tool_sandbox/scenarios/*.py`

配置包含 4 个模型、25 个 ToolSandbox case、2 种方法、1 次重复，共 `4 × 25 × 2 × 1 = 200` 条结果和 100 对可配对的 DEFAULT / DYNSTEER_REPLAY 记录。配置中没有独立于 case 的 scenario 层，因此下文将每个 `case_id` 视为一个场景。

成功判定遵循当前代码和产物的一等字段：

- DEFAULT：`resolved=true`。当前实现中等价于 ToolSandbox 原生 `overall_score >= 1.0`，本批数据实际也只有精确 `1.0` 才成功。
- DYNSTEER_REPLAY：`milestone_coverage=full`；`partial` 和 `none` 均视为未成功。

两种 `overall_score` 不是同一量尺：DEFAULT 是 ToolSandbox 原生 milestone/minefield similarity；replay 是分阶段、多维度、终局和 minefield 的组合评分。因此，本报告只直接比较成功标签、模型排序和统计分布，不把两侧某个相同分数阈值当作等价成功线。

模型简称和表格顺序如下：`DP` = deepseek-v4-pro，`DF` = deepseek-v4-flash，`QP` = qwen-plus-2025-12-01，`QM` = qwen3-max-2026-01-23。

### 1.1 重要版本说明

当前 `metrics.json`、`scores.json`、`index.json` 的文件修改时间为 2026-08-04 02:00 UTC 左右；索引内 case 运行窗口为 2026-08-03 09:21–12:02 UTC。它们与 `docs/plans/analysis/2026-08-03-toolsandbox-partial-main-default-vs-replay-audit-report.md` 分析的旧产物不是同一批结果：旧报告记载 88% 成功一致率、replay 19/100 成功，而当前产物是 52% 一致率、replay 75/100 成功。

同一个 `experiment_id` 下的新运行会覆盖旧产物，这是当前项目只保留最新代码运行结果的既定行为，不作为本报告的问题或改造项。本报告结论只适用于 2026-08-04 当前工作区可见的最新产物。

## 2. 结论摘要

1. **主体运行链路完整且可读取。** 200 条索引和 800 个核心 case 文件全部存在、可解析；索引、summary、report、trajectory 的标识、得分和 step/snapshot 数量对齐；100 条 replay 轨迹均与对应 DEFAULT 轨迹或其前缀逐项一致；139 次 Judge LLM 调用失败数为 0。
2. **DEFAULT 与 replay 成功/失败高度不一致。** 100 对中仅 52 对一致，48 对不一致：47 对是 DEFAULT 失败但 replay `full`，1 对是 DEFAULT 成功但 replay `partial`。
3. **47 条新增成功多数不是简单的 replay 假阳性。** 抽查工具调用、状态变化和最终回复后，联系人新增、系统设置、节日计算、发送消息等大量轨迹已完成实质目标，只因 DEFAULT 的最终消息相似度低于精确 1.0 而被判失败。replay 对语义等价回复更宽容，这是当前版本相对 DEFAULT 的真实优点。
4. **Replay 与 DEFAULT 的目标不同，不能把分歧自动视为 replay 错误。** Replay 会把 ToolSandbox milestone 规定的工具路径、交互顺序、工具质量和效率纳入判断；即使 DEFAULT 完整轨迹最终成功，只要当前阶段质量低于停止阈值，replay 仍可合理停止。当前仍需改进的是诊断信息的明确性和并行 tool call 归因，而不是为了复现 DEFAULT 放宽评估。
5. **全量模型区分度更高但不均匀；排除语义严格性混杂后结论明显改善。** 全量 replay 在 epsilon 0.01–0.05 的 DS 都高于 DEFAULT，但排序 Kendall tau-b 为 0，离散度主要由 DP 单一低分点驱动。整场排除 14 个受 DEFAULT 语义严格性影响的 scenario 后，剩余 11 个共同 scenario 上两种方法排序均为 `DP > QM > QP > DF`、tau-b 为 1.0，且 replay DS 在五个阈值上高 61.9%–129.0%。
6. **replay 没有证明效率更优。** 虚拟前缀相对完整 execution timing 节省 27.2%，但 Judge 平均再花 7.95 秒；同口径 effective time 平均比 DEFAULT execution time 高 7.6%，中位数高 91.7%，只有 11/100 对更快。离线 replay 还必须先支付完整 DEFAULT 轨迹成本。
7. **成本维度明确新增 Judge 开销，Agent 成本却不可观测。** replay 新增 139 次 Judge 调用和 527,025 tokens；trajectory token 全部缺失，无法证明虚拟早停节约的 Agent token 大于 Judge 增量。
8. **最终判断：当前 DYNSTEER_REPLAY 尚不能判定为整体优于 DEFAULT。** 它在语义容忍、阶段诊断和安全审计上明显更有价值，也可能比 DEFAULT 的“精确 1.0”成功线更符合人工直觉；但模型排序稳定性、过程鲁棒性、端到端时间和成本仍不支持替代 DEFAULT。现阶段适合双轨输出和人工校准，不适合单独作为主评估真值。

## 3. 产物完整性与评估流程核查

### 3.1 文件与字段完整性

| 检查项 | 结果 |
|---|---:|
| 配置期望结果数 | 200 |
| `index.json.case_count` / 实际索引叶子数 | 200 / 200 |
| `summary.json` | 200 / 200 |
| DEFAULT `default_report.json` | 100 / 100 |
| replay `report.json` | 100 / 100 |
| `raw_summary.json` | 200 / 200 |
| `trajectory.json` | 200 / 200 |
| JSON 解析失败 | 0 |
| summary/report/index 得分不一致 | 0 |
| trajectory step/snapshot 数与 runtime metric 不一致 | 0 |
| replay 内嵌 DEFAULT 引用分数不一致 | 0 |
| replay 轨迹不是 DEFAULT 轨迹前缀 | 0 |
| Judge LLM failed calls | 0 / 139 |

从“是否完整执行并正常落盘”的工程角度，当前实验主体流程是正常的。

### 3.2 Replay 终止、截断和恢复

| 项目 | 数量 |
|---|---:|
| replay case | 100 |
| 未触发 virtual stop | 80 |
| 触发 virtual stop / 轨迹实际截断 | 20 / 20 |
| `evaluation_policy_stop` | 10 |
| `minefield:mf0` | 5 |
| `milestone_no_progress:m0` | 5 |
| stop 后终局恢复为 `full` | 9 |
| 最终 `full / partial / none` | 75 / 15 / 10 |

20 条截断轨迹都与原 DEFAULT 轨迹前缀完全一致，说明 replay 没有重新生成 Agent 行为，而是离线扫描并虚拟截断既有轨迹。9 个 reminder 创建类 case 在中间阶段触发 policy stop，随后 finish 复核恢复为 `full`；剩余 1 个 policy stop 是 DF 的 `modify_reminder_with_recency_latest`，它在 Agent 后续实际成功修改 reminder 之前就截断并判为 `partial`。

### 3.3 阶段与诊断记录

| 指标 | 数量 |
|---|---:|
| stage `pass / fail / missing` | 270 / 35 / 31 |
| 含 warning 的 model-case | 40 |
| warning 记录数 | 58 |
| empty tool result 记录 | 71 |
| failed tool result 记录 | 40 |
| grounding warning | 6 |
| tool argument warning | 0 |
| LLM unique prompts | 139 |
| LLM cache hits / duplicate prompts avoided | 176 / 176 |

空 tool result 中有不少是状态修改工具成功返回 `None`，属于无 payload 的正常写操作，不应等价视为失败。failed tool result 则主要来自搜索参数为空/越界、关闭的网络服务、低电量模式权限约束和扰动后的参数类型错误；这些都被记录下来，没有导致评估器崩溃。

### 3.4 流程记录中需要修复或澄清的问题

#### A. 并行 tool call 的诊断归因错误

逐项比对诊断中的 `step_index/tool_name` 和 trajectory 实际 `openai_function_name` 后发现：

- 40 条 failed-tool 诊断中有 6 条工具名不匹配；
- 71 条 empty-result 诊断中有 6 条工具名不匹配。

典型例子是 DF 的 `modify_reminder_with_recency_latest`：step 18 实际是 `search_reminder` 失败，stage diagnostics 却标成 `get_current_timestamp`；step 22 实际仍是 `search_reminder` 失败，却标成 `timestamp_to_datetime_info`。这与并行调用时按相邻位置而非 `tool_call_id` 配对高度一致，会直接污染 tool quality 证据和失败诊断。

#### B. 阶段 hard constraint 与质量评估字段需要明确分层

有 10 个 stage 同时满足：

- `status=fail`；
- `hard_constraints_all_pass=true`；
- cheap Judge 为 pass、standard Judge 为 fail。

这不是代码逻辑矛盾：`hard_constraints_all_pass=true` 只表示 ToolSandbox 结构化 hard milestone 已命中；stage status 还会综合 Standard/Expensive Judge 对 progress、tool quality、efficiency 等维度的判断。质量分低于 0.4 时判 fail 并停止，符合 DYNSTEER 评估执行质量的目标。需要改进的是报告应明确标出失败来自 `structural_hard_constraint` 还是 `quality_score`，避免使用者误读。

#### C. 聚合指标不利于方法对比

`metrics.json.efficiency` 把 100 条 DEFAULT 与 100 条 replay 混合成 `case_count=200` 的单组均值，只有 effective timing 的可用数是 100；`cost.llm_cost_available=true` 也只代表 Judge token 可用，并不包含货币金额。文件本身计算可对账，但表达不足以直接判断两种方法的优劣，必须像本报告一样重新按 method 拆分。

## 4. 各模型总体结果

| 模型 | DEFAULT 均分 / 排名 | DEFAULT 成功 | Replay 均分 / 排名 | Replay full | Replay Judge calls / tokens |
|---|---:|---:|---:|---:|---:|
| DP | 0.828452 / 2 | 8 | 0.718606 / 4 | 18 | 29 / 109,764 |
| DF | 0.813571 / 4 | 7 | 0.764927 / 3 | 19 | 36 / 139,977 |
| QP | 0.824621 / 3 | 7 | 0.771029 / 1 | 19 | 40 / 150,058 |
| QM | 0.844936 / 1 | 7 | 0.770934 / 2 | 19 | 34 / 127,226 |

DEFAULT 的成功数为 7–8，replay 的 full 数为 18–19；两者的二值成功率都几乎无法区分四个模型。Replay 连续分数主要把 DP 拉低，QP 与 QM 仅差 `0.000095`，不能把它们的名次差当作实质差异。

## 5. 逐 case Agent 执行与评估结果

表中 DEFAULT 使用 `✓/×` 表示 resolved；Replay 使用 `全/部/无` 表示 full/partial/none。四个符号顺序固定为 `DP/DF/QP/QM`。

| Case | DEFAULT | Replay | Agent 执行与判定核查 |
|---|---|---|---|
| `find_days_till_holiday_insufficient_information` | ×/×/×/× | 无/无/无/无 | 四模型在缺少可靠当前日期时仍给出互相矛盾的 5、185、758、1095 天等答案；4 条均触发 minefield 截断。双方失败正常。 |
| `modify_contact_with_message_recency_insufficient_information` | ✓/✓/✓/✓ | 全/全/全/全 | 四模型均吸收后续澄清并完成联系人处理；双方一致成功。 |
| `modify_contact_with_message_recency_insufficient_information_10_distraction_tools` | ✓/✓/✓/✓ | 全/全/全/全 | 10 个干扰工具未破坏任务闭环；双方一致成功。 |
| `modify_contact_with_message_recency_insufficient_information_3_distraction_tools` | ✓/×/✓/✓ | 全/无/全/全 | DF 重复搜索二十余次、一次搜索失败，最后错误修改 Homer；触发 minefield。其余三条完成，双方逐模型一致。 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | ×/×/×/× | 全/全/全/全 | Agent 查到联系人但无删除工具，明确拒绝谎称已删除。Replay 把安全、诚实拒答判 full；DEFAULT 只因回复与参考文本不完全一致而失败。Replay 更符合任务安全语义。 |
| `add_reminder_content_and_date_and_time` | ✓/✓/✓/✓ | 全/全/全/全 | 四模型均创建正确 reminder；2 条触发 policy stop 后被 finish 恢复 full。 |
| `add_reminder_content_and_date_and_time_10_distraction_tools` | ✓/✓/✓/✓ | 全/全/全/全 | 扰动未影响最终 reminder；2 条 stop 后恢复。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools` | ✓/✓/✓/✓ | 全/全/全/全 | 四条一致成功；2 条 stop 后恢复。 |
| `add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled` | ✓/✓/✓/✓ | 全/全/全/全 | 参数描述扰动下仍全部完成；3 条 stop 后恢复。 |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | ×/×/×/× | 全/全/全/全 | 四模型都调用 `add_contact` 并写入正确姓名和号码；DEFAULT 状态 milestone 为 1.0，但确认消息相似度约 0.71–0.93，故整体未到 1.0。Replay full 更合理。 |
| `modify_reminder_with_recency_latest` | ✓/✓/×/× | 全/部/部/部 | DP 正常完成；QP/QM 没找到 reminder，未修改。DF 虽在完整 DEFAULT 轨迹中最终恢复，但当前阶段先后两次错误搜索，StandardJudge 给出 progress=0.0、tool quality=0.25、efficiency=0.25，stage score=0.2742 后停止；这是质量驱动的预期策略分歧，不应简单定性为 replay 假阴性。 |
| `search_message_with_recency_oldest_multiple_user_turn` | ×/×/×/× | 无/全/全/全 | DF/QP/QM 被 replay 识别为成功；DP 多次调用 `search_messages` 并返回正确最旧消息，但没有调用参考 milestone 要求的 `get_current_timestamp`，因此在 m0 判 none。任务结果正确而参考过程未满足，体现过程约束过严。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools` | ×/×/×/× | 无/全/部/全 | DP 长时间循环和重复搜索，QP错误判断无消息；DF/QM完成。Replay 的相对判定大体符合轨迹质量。 |
| `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | ×/×/×/× | 无/无/全/全 | DP/DF 实际找到并回复最旧消息，但未执行参考流程要求的 `get_current_timestamp`，replay 在 m0 判 none；说明评估过度依赖参考工具路径，而非最终回答正确性。 |
| `turn_on_cellular_low_battery_mode` | ×/×/×/× | 全/全/全/全 | 四模型完成相关设置状态；DEFAULT 因非精确回复失败，replay 通过状态和语义判 full。 |
| `turn_on_location_low_battery_mode` | ×/×/×/× | 全/全/全/全 | Agent 先遇到低电量阻断，再关闭低电量并成功开启定位；状态完成。Replay 更符合实质结果。 |
| `turn_on_location_low_battery_mode_3_distraction_tools` | ×/×/×/× | 全/全/全/全 | 四模型均恢复并完成状态设置；干扰工具未改变 replay 结论。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled` | ×/×/×/× | 全/全/全/全 | 四模型均完成；replay 对描述扰动保持成功。 |
| `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | ×/×/×/× | 全/全/全/无 | DP/DF/QP克服类型扰动完成；QM 出现约 18 次相关调用、17 次失败并反复重试，最终未开启定位。Replay 的 QM 失败判定正常。 |
| `update_contact_relationship_with_relationship` | ×/×/×/× | 全/全/全/全 | 四模型正确修改两位联系人关系；DEFAULT 主要因最终消息相似度仅约 0.65–0.78 而失败，replay 的状态型判定更合理。 |
| `find_days_till_holiday` | ×/×/×/× | 全/全/全/全 | 四模型均调用时间、节日搜索和时间差工具并给出约 143 天；DEFAULT 前三 milestone 为 1.0、最终回复非精确匹配，replay full 更合理。 |
| `send_message_with_contact_content_cellular_off` | ×/×/×/× | 全/全/全/全 | Agent 找联系人、首次发送遇到蜂窝关闭、开启蜂窝后重发成功；replay 识别恢复路径，DEFAULT 被确认消息相似度卡住。 |
| `find_days_till_holiday_wifi_off_alt` | ×/×/×/× | 部/部/部/部 | 四模型均开启 Wi-Fi、检索并计算约 143 天，但没有恢复初始 `SETTING` 状态；replay 在 preserve-state milestone 失败，partial 合理。 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | ×/×/×/× | 部/部/部/部 | DF 反复搜索且未正确闭环，QP新增联系人而非预期更新；DP经过多次错误修正，QM直接修改但未完成参考搜索链。全体 partial 体现过程/终局未完整，但 QM/DP 也暴露 replay 对参考过程过严。 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn` | ×/×/×/× | 部/全/部/部 | DF收到分开的两轮指令并分别回复，因此 full；DP/QM收到合并指令，一次性完成“enemy 再 friend”且最终状态正确，却因没有中间“现为 enemy”消息被卡为 partial；QP没有执行关系更新。Replay 对 User Simulator 的措辞分支过度敏感。 |

总体看，Agent 的主要失败模式包括：缺信息时编造日期、搜索参数错误或重复搜索、扰动参数类型无法恢复、没有恢复临时开启的系统设置、以及在多轮场景中误解/合并用户意图。评估器能够捕获大多数失败；当前确定需要修复的是并行 tool call 诊断归因，并需要更明确地区分“缺少哪个工具/参数”“结构 hard 失败”和“质量低分失败”。

## 6. DEFAULT 与 Replay 成功一致性

### 6.1 混淆矩阵

| DEFAULT | Replay full | Replay 非 full | 合计 |
|---|---:|---:|---:|
| 成功 | 28 | 1 | 29 |
| 失败 | 47 | 24 | 71 |
| 合计 | 75 | 25 | 100 |

派生结果：

- 一致：52；不一致：48；一致率 52%。
- DEFAULT 成功、replay 失败：1 条，即 DF / `modify_reminder_with_recency_latest`。
- DEFAULT 失败、replay 成功：47 条。
- 若机械地把 DEFAULT 当参考标签，replay recall 为 `28/29 = 96.6%`，precision 只有 `28/75 = 37.3%`；但 DEFAULT 的精确 1.0 口径本身明显低召回，因此这两个数不能当作真实准确率。

### 6.2 分模型一致性

| 模型 | 一致 / 25 | DEFAULT 成功→Replay 失败 | DEFAULT 失败→Replay 成功 |
|---|---:|---:|---:|
| DP | 15 | 0 | 10 |
| DF | 11 | 1 | 13 |
| QP | 13 | 0 | 12 |
| QM | 13 | 0 | 12 |

### 6.3 47 条 Replay 新增成功如何解释

新增成功集中于以下场景：安全拒绝删除联系人 4 条、添加联系人 4 条、三组搜索消息 7 条、5 组系统设置 19 条、关系更新 4 条、节日计算 4 条、发送消息 4 条、多轮关系更新 1 条，合计 47 条。

代表性 DEFAULT 失败轨迹中，关键状态 milestone 往往已经是 1.0，只有用户可见消息相似度低于 1.0：

- DP / `add_contact...`：状态 1.0，确认消息 0.8202，整体 0.9101；
- DP / `turn_on_location...`：前两个 milestone 1.0，确认消息 0.6114，整体 0.8705；
- DP / `find_days_till_holiday`：前三个 milestone 1.0，回答消息 0.8281，整体 0.9570；
- DP / `send_message...`：前三个 milestone 1.0，确认消息 0.7495，整体 0.9374。

这些轨迹的工具动作、最终状态和自然语言语义均基本正确。由此可见，当前 replay 的大量“新增成功”主要来自更合理的语义容忍，而不是无条件放宽。

不过，当前数据没有独立人工 gold label；DEFAULT 与 replay 又分别强调最终原生相似度和动态阶段质量。因此正确结论不是“replay 必须与 DEFAULT 一致”，而是两者口径不同，应分别解释任务最终结果和阶段执行质量。

## 7. 模型区分度分析

### 7.1 当前官方 Discriminability Score

| epsilon | DEFAULT DS | DEFAULT 显著对/6 | Replay DS | Replay 显著对/6 |
|---:|---:|---:|---:|---:|
| 0.01 | 0.012409 | 5 | 0.020516 | 3 |
| 0.02 | 0.007848 | 2 | 0.020516 | 3 |
| 0.03 | 0.005549 | 1 | 0.020516 | 3 |
| 0.04 | 0.000000 | 0 | 0.020516 | 3 |
| 0.05 | 0.000000 | 0 | 0.016751 | 2 |

按当前 DS 公式，replay 在所有 epsilon 上数值更高。这是 replay 的正面信号，但不能孤立解读：

1. epsilon=0.01 时，DEFAULT 有 5 个显著模型对，replay 只有 3 个；replay DS 更高是因为均分离散度更大，而不是覆盖更多模型对。
2. replay 的 3 个显著对在 epsilon 0.01–0.04 基本都是 DP 对其他三个模型；DF、QP、QM 三者均分仅相差约 0.0061，QP 与 QM 几乎完全并列。
3. 二值成功数 DEFAULT 为 `8/7/7/7`，replay 为 `18/19/19/19`，两种二值标签都几乎没有模型区分力。

### 7.2 排序和逐 case 分散度

- DEFAULT 排序：`QM > DP > QP > DF`。
- Replay 排序：`QP > QM > DF > DP`。
- `rank_tau(REPLAY vs DEFAULT) = 0.0`。
- 100 对连续分数 Pearson 相关约 0.777，相关但不等价。
- 每个 case 内四模型两两分差的平均值：DEFAULT 0.1042，replay 0.1636，replay 高约 57.0%。
- 同一指标的中位数：DEFAULT 0.0390，replay 0.0272，replay 反而低约 30.3%。
- 四模型完全同分的 case：两种方法均为 7 个。

“均值上升、但中位数下降”说明 replay 的 case 内区分提升由少数高分散 case 驱动，并非普遍拉开模型差异。结合 DP 在若干过程型 milestone 上被额外惩罚、QP/QM 近似并列和 `repeats=1`，当前只能说 **replay 的 DS 数值更高，不能说模型区分结论更稳定或更可信**。

Replay 的 continuous score 与自身 coverage 倒是分离良好：full 最低分 0.7230，non-full 最高分 0.6217，本批数据没有分数区间重叠。这有利于内部解释，但尚未证明跨数据集、跨 Judge 仍成立。

### 7.3 排除 DEFAULT 语义过严 case 后的重算

为回答“排除因语义严格造成的 DEFAULT 失败、replay 成功后，区分度如何”，本节按当前报告对 47 个此类模型–case 分歧的人工核查结果做敏感性分析。由于只删除单个模型–case 配对会使四个模型面对不同题集，正式比较采用更保守、可比性更好的 **scenario 级排除**：只要某个 scenario 出现此类分歧，就把该 scenario 的四模型结果全部排除。

共排除 14 个 scenario：

- `remove_contact_by_phone_no_remove_contact_insufficient_information`
- `add_contact_with_name_and_phone_number_3_distraction_tools`
- `search_message_with_recency_oldest_multiple_user_turn`
- `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools`
- `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled`
- `turn_on_cellular_low_battery_mode`
- `turn_on_location_low_battery_mode`
- `turn_on_location_low_battery_mode_3_distraction_tools`
- `turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled`
- `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled`
- `update_contact_relationship_with_relationship`
- `find_days_till_holiday`
- `send_message_with_contact_content_cellular_off`
- `update_contact_relationship_with_relationship_twice_multiple_user_turn`

排除后保留 11 个共同 scenario、每个模型每种方法 11 条结果，共 44 个模型–case 配对。使用与 `metrics.json` 相同的模型均分、DS 和 Kendall tau-b 算法重算。

#### 模型均分与排序

| 模型 | DEFAULT 均分 / 排名 | Replay 均分 / 排名 |
|---|---:|---:|
| DP | 0.859414 / 1 | 0.749160 / 1 |
| DF | 0.770218 / 4 | 0.630236 / 4 |
| QP | 0.814088 / 3 | 0.657111 / 3 |
| QM | 0.837161 / 2 | 0.691658 / 2 |

两种方法的模型排序均为：

`DP > QM > QP > DF`

因此：

`rank_tau(REPLAY vs DEFAULT) = 1.0`

这说明原始全量结果中的 `rank_tau=0.0` 主要由语义严格性分歧场景驱动；排除这些场景后，replay 不再改变模型相对排序。

#### 重算后的 Discriminability Score

| epsilon | DEFAULT DS | DEFAULT 显著对/6 | Replay DS | Replay 显著对/6 | Replay 相对变化 |
|---:|---:|---:|---:|---:|---:|
| 0.01 | 0.040256 | 6 | 0.065170 | 6 | +61.9% |
| 0.02 | 0.040256 | 6 | 0.065170 | 6 | +61.9% |
| 0.03 | 0.032869 | 4 | 0.059492 | 5 | +81.0% |
| 0.04 | 0.032869 | 4 | 0.053211 | 4 | +61.9% |
| 0.05 | 0.023242 | 2 | 0.053211 | 4 | +129.0% |

排除后，replay 的 DS 在五个阈值上均高于 DEFAULT，而且不再依赖“显著模型对更少但单个模型被拉低”的现象：epsilon 0.01 和 0.02 时双方均有 6/6 个显著模型对；epsilon 0.03 和 0.05 时 replay 的显著模型对还更多。结合 `rank_tau=1.0`，这是当前数据中支持 replay 区分度优于 DEFAULT 的最强证据：**在不受 DEFAULT 文本语义严格性分歧干扰的共同子集上，replay 保持相同模型排序，同时扩大了模型分数间距。**

作为敏感性对照，如果只删除 47 个分歧配对而保留同一 scenario 的其他模型结果，会剩下 DP/DF/QP/QM 分别 15/12/13/13 条，模型面对的题集不同。该不等样本口径得到 `rank_tau=-0.3333`，因此不适合用作正式结论；它恰好说明必须整场排除，而不能按单模型结果选择性删除。

上述正面结果仍有三个限制：

1. scenario 是根据本批已经观察到的 DEFAULT/replay 分歧事后筛选，存在选择偏差；
2. 只剩 11 个 case，且部分属于同一 reminder/contact 变体，独立场景多样性有限；
3. `repeats=1`，无法给 DS 增幅和 tau=1.0 提供方差或置信区间。

因此应把重算结果解释为“排除已识别语义混杂因素后，replay 显示出更强且排序一致的区分信号”，而不是已经证明其总体评估能力优于 DEFAULT。

## 8. 效率分析

### 8.1 同口径拆分

| 指标 | DEFAULT | DYNSTEER_REPLAY |
|---|---:|---:|
| case 数 | 100 | 100 |
| Agent step / raw step / tool call 总数 | 680 / 1459 / 416 | 544 / 1175 / 354 |
| DEFAULT wall-clock 总计 / 均值 / 中位数 | 3250.646 / 32.506 / 10.945 s | — |
| 完整 source execution 总计 / 均值 / 中位数 | 2282.001 / 22.820 / 10.188 s | — |
| replay 虚拟前缀总计 / 均值 / 中位数 | — | 1661.018 / 16.610 / 10.028 s |
| replay Judge wall-clock 总计 / 均值 / 中位数 | — | 795.448 / 7.954 / 7.993 s |
| replay effective 总计 / 均值 / 中位数 | — | 2456.466 / 24.565 / 19.527 s |

Replay 截短后，Agent step、raw step、tool call 分别表面减少约 20.0%、19.5%、14.9%；虚拟前缀相对完整 source execution 节省 620.983 秒，即 27.2%。但这些都是对已经产生的 DEFAULT 轨迹做“假设在此停止”的离线统计，不是实际重新运行 Agent 后产生的节省。

### 8.2 Judge 开销是否覆盖早停收益

以 `execution_total_seconds` 和 `prefix + Judge` 做最接近的同口径比较：

- 平均：replay effective 24.565 秒，比 DEFAULT execution 22.820 秒高 7.6%；
- 中位数：19.527 秒比 10.188 秒高 91.7%；
- 逐 pair：replay 只有 11/100 条更快，89/100 条更慢；
- 平均净增：每 case 约 1.745 秒。

若错误地拿 replay effective 与 DEFAULT wall-clock 比，replay 平均看似快 24.4%，但 DEFAULT 有一条 DF / `modify_contact_with_message_recency_alt_10_distraction_tools` wall-clock 780.668 秒、execution 121.608 秒的未解释异常。移除唯一 `wall/execution > 2` 的样本后：

- DEFAULT wall-clock 均值 24.949 秒；
- 对应 replay effective 均值 24.553 秒，仅低约 1.6%；
- 仍只有 25/99 条 replay 更快；
- replay effective 中位数仍显著更慢。

因此，当前证据不支持“replay 更高效”。如果计算这次离线实验真实付出的工作量，必须先完整生成 DEFAULT，再增加 795.448 秒 Judge 时间，约为 `3250.646 + 795.448 = 4046.095` 秒，比只做 DEFAULT 多 24.5%。只有在 DEFAULT 轨迹已经存在、replay 作为边际离线审计时，795.448 秒才是正确的增量时间。

## 9. 成本分析

| 成本项 | DEFAULT | DYNSTEER_REPLAY |
|---|---:|---:|
| DynSTEER Judge calls | 0 | 139 |
| Judge failed calls | 0 | 0 |
| Judge tokens | 0 | 527,025 |
| 平均 Judge tokens / replay case | 0 | 5,270 |
| 平均 tokens / Judge call | 0 | 约 3,791 |
| unique prompts | 0 | 139 |
| cache hits / duplicate prompts avoided | 0 | 176 / 176 |
| Agent trajectory tokens | 不可用 | 不可用 |
| Agent 货币成本 | 不可用 | 不可用 |

`trajectory_total_tokens=0` 且 `trajectory_cost_available=false`，表示 Agent 侧 token 没有被采集，不是 Agent 调用免费。`llm_cost_available=true` 也没有输出币种、单价或货币金额，只能确认 Judge token 可统计。

当前可确认的是 replay 新增了 527,025 Judge tokens；无法证明假设截短的 284 个 raw steps 能节省更多 Agent tokens。考虑到离线 replay 实际没有避免完整 Agent 运行，本批实验的真实总成本一定高于只做 DEFAULT。成本维度上，DYNSTEER_REPLAY 当前不优于 DEFAULT。

## 10. DYNSTEER_REPLAY 是否优于 DEFAULT

### 10.1 明确优点

1. 对状态已完成但确认消息是自然语言改写的轨迹更宽容，修正了 DEFAULT “必须精确 1.0”造成的大量语义假阴性。
2. 能输出 milestone、维度分数、minefield、失败阶段、终止原因、状态守恒、工具失败和恢复路径，诊断信息远丰富于 DEFAULT。
3. 对低电量/网络服务等恢复路径、参数扰动和安全拒绝能够给出更接近人工直觉的 full/partial/none。
4. 当前 DS 数值和 case 内平均分散度更高，说明部分 case 确实获得更细粒度的模型差异。
5. 工程链路完整，139 次 Judge 调用无失败，轨迹前缀和聚合输出均可对账。

### 10.2 尚未优于 DEFAULT 的原因

1. 52% 标签一致率表明成功口径发生了大幅变化，但没有独立人工 gold label 证明 47 条新增成功和 25 条 non-full 全部正确。
2. Replay 会把 ToolSandbox 原始 milestone 指定的工具和交互路径纳入 hard 约束，因此其结果不能只按最终状态与 DEFAULT 对齐；当前实验仍需更多重复来判断这种更严格口径下的稳定性。
3. 多轮关系更新结果会受 User Simulator 将请求拆分还是合并的实际轨迹影响；这是 ToolSandbox 模拟行为与固定 milestone 共同决定的结果，不在本轮修改中改写原始场景语义。
4. 并行 tool call 的诊断归因存在 12 条工具名不匹配记录，直接影响 tool-quality 可解释性。
5. 全量结果排序 tau 为 0，DS 提升主要由 DP 单点拉开，case 内分散度中位数反而下降；虽然排除 14 个语义严格性 scenario 后 tau 提升为 1.0、replay DS 全面更高，但该结果来自事后筛选的 11-case 子集，尚需独立样本验证。
6. effective time 在同口径下平均和中位数都更慢，绝大多数 pair 没有抵消 Judge 时间。
7. Judge 新增 527,025 tokens，而 Agent token 和货币成本缺失。
8. 单次重复无法评估 Agent、User Simulator 和 Judge 的运行方差。

综合判断：**DYNSTEER_REPLAY 在“语义诊断质量”上局部优于 DEFAULT；排除语义严格性混杂因素后，它也展示了保持模型排序并增强 DS 的潜力；但在运行稳定性、效率和成本上仍需更多重复实验验证。** DEFAULT 保留原生 benchmark 可比性，replay 则评估阶段执行质量；两者不要求逐 case 标签完全一致。

## 11. 改进建议

### P0：诊断正确性与输出语义

1. **修复并行 tool call/result 配对。** 所有 diagnostics、stage interval 和 tool-quality 证据必须按 `openai_tool_call_id` 关联，禁止按相邻 step 推断工具名；为本报告列出的错配模式增加回归测试。
2. **通用输出缺失工具与参数。** 从每条 `tool_call` constraint 的 `stage_goal_semantics.tool_name/arguments` 直接生成诊断，明确输出实际缺少的任意工具和期望参数；不对 `get_current_timestamp` 或任何具体工具做特判，也不放宽 ToolSandbox hard constraint。
3. **明确结构失败与质量失败。** 保留“hard 失败必然 fail；hard 通过后 Standard/Expensive Judge 仍可因质量分低于 0.4 判 fail 并停止”的现有逻辑，在 stage metadata 和 termination detail 中明确输出 `failure_basis=structural_hard_constraint/quality_score/fatal_minefield/no_progress`。
4. **保持原始 ToolSandbox 多轮语义。** 不修改 ToolSandbox Agent/User Simulator 和 scenario milestone，不根据单次合并提示动态改写 milestone graph；合并式用户请求只作为报告解释项，不在当前代码中放宽成功条件。
5. **保持当前质量停止策略。** Replay 在停点不读取未来 DEFAULT 后缀；DF reminder 后续能恢复不构成当时延迟停止的依据。继续保留 `stage_score < 0.4`、fatal minefield 和 ready-frontier no-progress 停止。

### P1：区分度与稳定性

1. 将 `toolsandbox_partial_main.json` 的 `repeats` 从 1 提升到 3；报告 repeat 均分、总体标准差、成功率方差和排名稳定率。暂不提升到 5，控制 ToolSandbox Agent/User 模拟耗时。
2. DS 除最终 score 外同时报告每个模型对的差值；避免“显著对更少但单一离群模型使 DS 更高”被误读为全面提升。
3. 同时报告 case 内分散度的均值、中位数和分位数，以及 binary success 的模型间方差。

### P1：效率与成本

1. `metrics.json` 按 method 分层输出 mean、median、P90/P95、trimmed mean、step、tool call、LLM call、token 和异常样本，避免 200 条混合均值。
2. 保持当前“CheapJudge 先结构化评估、动态路由后仅对 Standard/Expensive 维度调用 LLM”的架构，不重复设计相同机制。
3. 单独报告“已有轨迹上的边际审计成本”和“从零运行 DEFAULT + replay 的总成本”；暂不新增 online 对照实验，因为 online 轨迹无法与 DEFAULT 轨迹逐项配对。

### P2：输出语义

1. 明确 `stage status`、`hard_constraints_all_pass`、`evaluation_policy_stop`、`recovered_after_virtual_stop` 和最终 `milestone_coverage` 的优先级。
2. 对 stop 后恢复 full 的 case 增加清晰的最终状态摘要，避免下游把中间 fail 当作最终失败。
3. 在聚合结果直接输出逐模型/逐 case 的成功混淆矩阵和 disagreement 原因分类，减少二次离线脚本分析。
