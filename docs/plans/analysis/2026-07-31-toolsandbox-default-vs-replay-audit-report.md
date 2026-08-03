# 2026-07-31 ToolSandbox `toolsandbox_partial_main` DEFAULT vs DYNSTEER_REPLAY 核查报告

## 1. 核查范围

本报告仅基于当前工作区内已有产物核查，不重新运行实验。

- `results/exp/toolsandbox_partial_main/index.json`
- `results/exp/toolsandbox_partial_main/scores.json`
- `results/exp/toolsandbox_partial_main/metrics.json`
- `results/exp/toolsandbox_partial_main/toolsandbox/{default,dynsteer_replay}/...`
- `runs/exp/toolsandbox_partial_main/toolsandbox/{default,dynsteer_replay}/...`

当前数据覆盖：

- 4 个模型
- 25 个 case
- 2 种方法：`default` / `dynsteer_replay`
- 共 100 对 `model + case` 成对比较

## 2. 总体结论

1. `results` 和 `runs` 的文件覆盖完整，全部可解析，没有缺文件或 JSON 解析异常。
2. `default` 与 `dynsteer_replay` 的成功/失败标签并不完全一致，100 对里一致 85 对，不一致 15 对。
3. 这 15 对不一致全部是 `default` 判成功、`replay` 判失败，没有出现 `replay` 反过来“补救” `default` 失败的情况。
4. 15 对不一致里，14 对是“同一条 trajectory、不同判定”；只有 1 对同时发生 trajectory 变化和判定变化。
5. `dynsteer_replay` 的阶段评估流程本身是正常的，存在少量 `evaluation_policy_stop`、`minefield` 和 `milestone_no_progress` 终止，但都能在最终报告中闭环，没有 LLM 调用失败。
6. 从模型区分度上，`dynsteer_replay` 更好；从 wall-clock 上，它平均也更快一点，但 LLM 成本明显更高，且 binary label 变化更激进，因此还不能说它已经全面优于 `default`。

## 3. 成功/失败一致性

### 3.1 总体对比

| 项目 | 数值 |
|---|---:|
| 成对比较数 | 100 |
| 一致 | 85 |
| 不一致 | 15 |
| `default` 成功、`replay` 失败 | 15 |
| `default` 失败、`replay` 成功 | 0 |
| 轨迹完全一致 | 83 |
| 轨迹不同 | 17 |

### 3.2 family 级结论

| family | case 数 | `default` 成功 | `replay` 成功 | 一致 | 备注 |
|---|---:|---:|---:|---:|---|
| `add_reminder_content_and_date_and_time` | 16 | 16 | 16 | 16 | replay 全部成功，且其中 8 对轨迹更短 |
| `modify_contact_with_message_recency` | 16 | 11 | 0 | 5 | 15 个 label 分歧里有 11 个来自这里 |
| `modify_reminder_with_recency_latest` | 4 | 4 | 0 | 0 | 4 个 pair 全部从 success 被 replay 判成 partial/fail |
| 其余 8 个 family | 64 | 0 | 0 | 64 | 两种方法均一致失败 |

### 3.3 关键不一致点

- `modify_contact_with_message_recency*`：`default` 认为成功，但 replay 认为没有真正完成目标更新，尤其是“insufficient_information” 变体里，replay 的 whole-trajectory 诊断指出 agent 没有真正定位并修改目标联系人。
- `modify_reminder_with_recency_latest`：`default` 成功，replay 只给到 partial；其中 1 对 `qwen-plus-2025-12-01` 的轨迹也发生了变化，`default` 17 steps / 35 raw steps，replay 13 steps / 26 raw steps。
- 其余 14 个 label 分歧都是“同一轨迹，不同判定”，说明主要问题在评估口径，不在 agent 行为本身。

## 4. 轨迹与运行记录核查

### 4.1 轨迹一致性

| 项目 | 数值 |
|---|---:|
| trajectory 完全一致 | 83 |
| trajectory 不同 | 17 |
| 其中 binary label 也不同 | 1 |
| 其中 binary label 相同 | 16 |

说明：

- `add_reminder_content_and_date_and_time*` 的 8 对轨迹不同，但 binary 结果一致，属于 replay 评估过程更短、但结论不变的情况。
- 其余大多数轨迹差异都发生在失败类 case，属于 replay 侧更早收敛或更短的评估路径。

### 4.2 replay 流程是否正常

`dynsteer_replay` 的 raw 运行记录总体正常，没有异常中断或调用失败：

| 项目 | 数值 |
|---|---:|
| `terminated_by_policy=true` | 20 |
| `termination_code = evaluation_policy_stop` | 8 |
| `termination_code = minefield:mf0` | 5 |
| `termination_code = milestone_no_progress:m0` | 3 |
| `termination_code = milestone_no_progress:m1` | 1 |
| `termination_code = milestone_no_progress:m2` | 2 |
| `termination_code = milestone_no_progress:m4` | 1 |
| stage settlement `pass` | 156 |
| stage settlement `fail` | 24 |
| stage settlement `warn` | 1 |
| LLM 调用失败 | 0 |

补充说明：

- 8 个 `evaluation_policy_stop` 全部出现在 `add_reminder_content_and_date_and_time*` 的 replay 中，且最终报告仍是 `milestone_coverage=full`，属于“虚拟停点后 finish 继续闭环”，不是流程异常。
- 唯一的 `warn` 出现在 `qwen-plus-2025-12-01 / modify_contact_with_message_recency_alt_10_distraction_tools` 的某个 stage，属于单点警告，没有破坏最终收敛。

## 5. 效率、成本与模型区分度

### 5.1 方法级对比

| 指标 | DEFAULT | DYNSTEER_REPLAY |
|---|---:|---:|
| 平均分数 | 0.8457 | 0.5300 |
| 平均耗时(s) | 28.73 | 22.77 |
| 平均 step 数 | 6.36 | 5.35 |
| 平均 raw step 数 | 13.70 | 11.57 |
| 平均 tool call 数 | 3.73 | 3.28 |
| 平均 LLM call 数 | 0 | 2.4 |
| 平均 LLM tokens | 0 | 13132.41 |

### 5.2 模型区分度

| 模型 | DEFAULT avg score | REPLAY avg score | 差值 |
|---|---:|---:|---:|
| `deepseek-v4-flash` | 0.8588 | 0.5615 | -0.2973 |
| `deepseek-v4-pro` | 0.8513 | 0.5266 | -0.3248 |
| `qwen-plus-2025-12-01` | 0.8157 | 0.4929 | -0.3228 |
| `qwen3-max-2026-01-23` | 0.8570 | 0.5389 | -0.3181 |

额外指标：

- `psep(default)=0.02249`
- `psep(dynsteer_replay)=0.03636`
- `rank_tau(dynsteer_replay)=1.0`

结论是：`dynsteer_replay` 的模型区分度更强，score 排序更稳定，但整体分数也被压得更低，说明它更严格。

## 6. 是否优于 DEFAULT

我的判断是：**当前还不能说 `DYNSTEER_REPLAY` 已经全面优于 `DEFAULT`**。

原因是：

1. 它确实提升了模型区分度，且平均 wall-clock 略快。
2. 但它引入了明显的 LLM 成本，100 对数据累计 240 次 LLM 调用、1,313,241 tokens。
3. 更重要的是，它把 15 个 `default` 成功样本改判为失败，而且这些分歧里绝大多数发生在同一条轨迹上，说明 replay 的评估口径仍然偏敏感。

## 7. 建议改进

1. 优先修 `modify_contact_with_message_recency*` 和 `modify_reminder_with_recency_latest` 两个 family 的语义对齐，尤其是 whole-trajectory / final-state 之间的一致性。
2. 对时间敏感 case，避免使用冻结的绝对时间目标，改为运行时重建 reference，减少 stale target 带来的 replay 偏差。
3. 保留 replay 的虚拟停点能力，但在报告里明确区分：
   - 虚拟 stop
   - 最终 full / partial / none
   - trajectory 本身是否变化
4. 降低 replay 的 LLM judge 成本，尽量减少重复 stage 判断和重复 prompt。

## 8. 最终判断

`DYNSTEER_REPLAY` 目前更像一个**更严格、区分度更强的评估模式**，而不是一个已经完全替代 `DEFAULT` 的最终版本。  
如果当前目标是“更准地分辨模型”，它是有价值的；如果目标是“更低成本、且与 default 保持稳定一致”，它还需要继续补强。  
补充看第 9 节：真正由坏执行触发的中途截断只覆盖了 12/100 个 replay 样本，而且其中 3 个并没有缩短轨迹，所以“提前中断”这件事还没有稳定到足够省等待的程度。

## 9. 提前中断专项核查

### 9.1 口径说明

- 中断判定：`runs` 里的 `raw_summary.json` 出现 `termination_code != null` 或 `terminated_by_policy = true`。
- 进度主口径：`replay_raw_step_count / default_raw_step_count`。
- 辅助口径：`replay_step_count / default_step_count`，结论一致，只是数值略有差异。

### 9.2 是否真的做到了“因为 agent 执行效果不佳而提前中断”

- 20 / 100 个 replay 样本触发了中断。
- 其中 8 个是 `evaluation_policy_stop`，全部来自 `add_reminder_content_and_date_and_time*`，属于虚拟停点后继续 `finish` 复核，不是 agent 失败导致的中断。
- 剩下 12 个才是失败驱动中断（`minefield` / `milestone_no_progress`）。
- 在这 12 个里，9 个确实比 default 更短，3 个没有缩短轨迹，说明 replay 只是在部分 case 上实现了真正的中途截断。

### 9.3 各模型中断占比

| 模型 | 中断数 | 中断率 | 真实早停数 | 真实早停率 | policy stop |
|---|---:|---:|---:|---:|---:|
| `deepseek-v4-flash` | 4 | 16% | 4 | 16% | 0 |
| `deepseek-v4-pro` | 1 | 4% | 1 | 4% | 0 |
| `qwen-plus-2025-12-01` | 7 | 28% | 3 | 12% | 4 |
| `qwen3-max-2026-01-23` | 8 | 32% | 4 | 16% | 4 |

### 9.4 policy stop 的 8 个 case

- `qwen-plus-2025-12-01`：`add_reminder_content_and_date_and_time`、`add_reminder_content_and_date_and_time_10_distraction_tools`、`add_reminder_content_and_date_and_time_3_distraction_tools`、`add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled`
- `qwen3-max-2026-01-23`：同上 4 个 case

这 8 个 case 的进度都一样：`4 / 7 = 0.571`（辅助口径 `2 / 3 = 0.667`），而且最终 `milestone_coverage` 仍然是 `full`。它们是“虚拟停点后 finish 闭环”，不等同于 agent 因错误被直接截断。

### 9.5 失败驱动中断的逐 case 明细

| 模型 | case | default→replay | early_stop | total_steps | progress |
|---|---|---|---:|---:|---:|
| `deepseek-v4-pro` | `find_days_till_holiday_insufficient_information` | `fail → none` | 3 | 23 | 0.130 |
| `qwen3-max-2026-01-23` | `turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled` | `fail → none` | 18 | 100 | 0.180 |
| `qwen-plus-2025-12-01` | `modify_contact_with_message_recency_insufficient_information` | `fail → none` | 13 | 37 | 0.351 |
| `deepseek-v4-flash` | `find_days_till_holiday_insufficient_information` | `fail → none` | 11 | 31 | 0.355 |
| `qwen-plus-2025-12-01` | `find_days_till_holiday_insufficient_information` | `fail → none` | 3 | 7 | 0.429 |
| `qwen3-max-2026-01-23` | `find_days_till_holiday_insufficient_information` | `fail → none` | 3 | 7 | 0.429 |
| `deepseek-v4-flash` | `modify_contact_with_message_recency_alt_10_distraction_tools` | `fail → partial` | 26 | 49 | 0.531 |
| `qwen-plus-2025-12-01` | `modify_reminder_with_recency_latest` | `success → partial` | 26 | 35 | 0.743 |
| `deepseek-v4-flash` | `search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled` | `fail → none` | 18 | 21 | 0.857 |
| `deepseek-v4-flash` | `update_contact_relationship_with_relationship_twice_multiple_user_turn` | `fail → partial` | 21 | 21 | 1.000 |
| `qwen3-max-2026-01-23` | `remove_contact_by_phone_no_remove_contact_insufficient_information` | `fail → none` | 19 | 19 | 1.000 |
| `qwen3-max-2026-01-23` | `search_message_with_recency_oldest_multiple_user_turn` | `fail → partial` | 27 | 27 | 1.000 |

补充两点：

- 12 个失败驱动中断里，只有 `modify_reminder_with_recency_latest` 同时改变了 trajectory 和 binary label；其余 11 个仍然是“同一条轨迹、不同评估口径”或“同长度轨迹”。
- 3 个没有缩短轨迹的 case，才是当前最值得补强的地方，因为它们说明 `termination_code` 已经触发，但真正省下来的执行等待并不明显。

### 9.6 为什么会出现“中断但没短下来”

这 3 个 case 的共同点是：**终止信号出现得太晚，或者说默认轨迹本身已经走到了同一个终点附近**。

1. `deepseek-v4-flash / update_contact_relationship_with_relationship_twice_multiple_user_turn`
   - 触发的是 `milestone_no_progress:m1`
   - 原因是 `m0` 已经完成，但 `m1` 连续 8/8 次评分都没有有效提升
   - 这类 stop 是“穷尽后判死”，不是很早就能切断的那种
   - 结果是 replay 和 default 都落在同一段尾部，`raw_step_count` 没有变短

2. `qwen3-max-2026-01-23 / remove_contact_by_phone_no_remove_contact_insufficient_information`
   - 触发的是 `milestone_no_progress:m0`
   - 第一关卡本身就失败，而且已经做满了 9 次尝试
   - 这说明失败点就在起跑段，系统没有更早的可裁剪前缀
   - 因而虽然 stop 发生了，但轨迹长度和 default 基本一致

3. `qwen3-max-2026-01-23 / search_message_with_recency_oldest_multiple_user_turn`
   - 先过 `m0`、`m1`，直到 `m2` 才失败
   - 这时大部分轨迹已经执行完了，stop 更像是在“尾段收口”
   - default 本身也跑到了同样的位置，所以 replay 没有再省出额外步数

所以，**“没有截断”并不表示 replay 没起作用，而是说明当前 stop 粒度还不够早**。它已经能判定失败，但判定点往往落在轨迹末尾，或者就是默认轨迹本来就已经很短。要进一步减少这种情况，需要更早的局部错误检测、更细的前沿判定，或者让 `milestone_no_progress` 在更少轮次内就能稳定停下来。
