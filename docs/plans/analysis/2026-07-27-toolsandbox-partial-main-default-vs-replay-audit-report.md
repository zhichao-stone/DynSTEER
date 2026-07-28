# 2026-07-27 ToolSandbox `toolsandbox_partial_main` default vs replay 核查报告

## 1. 核查范围

本次核查基于当前工作区已有产物，不重新运行实验，主要查看：

- `results/experiments/toolsandbox_partial_main/index.json`
- `results/experiments/toolsandbox_partial_main/scores.json`
- `results/experiments/toolsandbox_partial_main/metrics.json`
- `results/experiments/toolsandbox_partial_main/toolsandbox/default/run_0/*`
- `results/experiments/toolsandbox_partial_main/toolsandbox/dynsteer_replay/run_0/*`
- `runs/experiments/toolsandbox_partial_main/toolsandbox/default/run_0/*`
- `runs/experiments/toolsandbox_partial_main/toolsandbox/dynsteer_replay/run_0/*`

本次仓库里未见单独的 `.log` 文件，因此运行核查主要依赖 JSON 产物。

## 2. 总体结论

1. **运行链路正常。** default 和 replay 各有 10 个 case，`summary.json` / `report.json` / `raw_summary.json` / `trajectory.json` 都齐全，case 名称一一对应。
2. **agent 轨迹完全一致。** 10/10 case 的 `trajectory.json` 在 default 与 replay 间一致，step 数、raw step 数、tool call 数也一致，所以分数差异不是 agent 行为差异，而是评估层差异。
3. **整体 replay 分数低于 default。** default 平均分 `0.7168596616`，replay 平均分 `0.6055906309`，差值约 `-0.1113`。replay 平均耗时更高，主要来自 37 次 LLM judge 调用；default 没有 LLM judge 调用。
4. **case 层面并不完全一致。** 按 default `score >= 0.8` 作为成功候选、replay 以 `milestone_coverage=full` 作为成功的操作性口径，10 个 case 中有 6 个一致、4 个不一致。
5. **差异主要集中在 4 个 case。** `add_contact...` 和 `modify_reminder...` 在 replay 中被压低；`remove_contact...` 和 `update_contact_relationship...` 在 replay 中被抬高。
6. **运行异常不多，且都属于预期 stop。** replay 只有 2 个 policy stop：一个是 fatal minefield，一个是 no-progress 虚拟早停；`llm_failed_call_count=0`，没有运行崩溃迹象。

## 3. 数据覆盖与运行健康度

| 项目 | 数量 | 说明 |
|---|---:|---|
| unique case 数 | 10 | default 与 replay 的 case 集合一致 |
| method-expanded case 记录 | 20 | `index.json` 按 default / replay 展开 |
| default 平均分 | 0.7168596616 | `scores.json` / `index.json` 一致 |
| replay 平均分 | 0.6055906309 | `scores.json` / `index.json` 一致 |
| default 平均耗时 | 19.4206466 s | 10 case 平均 |
| replay 平均耗时 | 27.79363624 s | 10 case 平均，主要是 judge 开销 |
| default / replay 平均 step | 5.7 / 5.7 | 轨迹一致 |
| default / replay 平均 raw step | 12.4 / 12.4 | 轨迹一致 |
| default / replay total tool calls | 34 / 34 | 轨迹一致 |
| replay total LLM calls | 37 | 全部成功 |
| replay LLM failed calls | 0 | 正常 |
| replay policy stop | 2 | `minefield:mf0`、`milestone_no_progress:m2` |

补充观察：

- `metrics.json` 里的 `psep` 仍为 `0.0`，`rank_tau` 也是 `0.0`。
- `trajectory_total_tokens=0` 仍然不可用，但 replay 的 judge token 已正常记录。

## 4. Case 级对比

说明：default 侧没有统一的 `full/partial/none` 字段，因此这里用“default score >= 0.8 视作成功候选”的操作性口径，只用于横向比较，不代表 benchmark 官方阈值。

| case | default_score | replay_score | replay_coverage | 一致性 | 主要原因 |
|---|---:|---:|---|---|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 0.844306 | 0.000000 | none | 否 | replay 在 `m0` 直接被 `REMINDER/MESSAGING` preserve-state guardrail 卡死，`guardrail_similarity=0.0` |
| `find_days_till_holiday` | 0.972725 | 0.932450 | full | 是 | 两边都认为成功，replay stage 全 pass |
| `find_days_till_holiday_insufficient_information` | 0.000000 | 0.000000 | none | 是 | fatal minefield，预期失败 |
| `find_days_till_holiday_wifi_off_alt` | 0.762313 | 0.554723 | partial | 是 | replay 仍能完成查找，但 `m3` 因 `SETTING` preserve-state 失败，且缺少 `get_current_timestamp` 链路 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | 0.314711 | 0.172775 | partial | 是 | 两边都认为失败，replay 诊断里仍可见联系人污染与状态漂移 |
| `modify_reminder_with_recency_latest` | 1.000000 | 0.621736 | partial | 否 | default 满分，但 replay 的 `m2_c0` `update_similarity=0.0`，`actual_rows=3` vs `expected_rows=1`，说明 reminder 状态未按 replay 结构化评分收敛 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 0.617358 | 0.955000 | full | 否 | replay 的 stage m0 + finish 都 pass，语义上把“拒绝删除”认作成功；default 原生 similarity 偏保守 |
| `send_message_with_contact_content_cellular_off` | 0.944456 | 0.937056 | full | 是 | 两边都认为成功，且 replay 通过 semantic / finish verification |
| `turn_on_cellular_low_battery_mode` | 0.924268 | 0.935950 | full | 是 | 两边都认为成功，replay 语义复判通过 |
| `update_contact_relationship_with_relationship` | 0.788461 | 0.946216 | full | 否 | default 原生 similarity 只给到 0.788，但 replay 的结构化状态与语义审查看作完整成功 |

## 5. 为什么 replay 比 default 差很多

### 5.1 不是 agent 轨迹变了，而是评估规则变了

两条链路的 `trajectory.json` 完全一致，所以 replay 与 default 的差异主要来自评估层：

- default 用的是 benchmark 原生 similarity / resolved 逻辑。
- replay 用的是 DynSTEER 的分 stage 评估、guardrail、semantic review 和 policy stop。

这意味着 replay 既可能更严格，也可能更懂语义。

### 5.2 replay 拉低分数的主因

1. **`add_contact...` 被 preserve-state guardrail 误杀。** replay 的 `m0` 对 `REMINDER` 和 `MESSAGING` 的 guardrail similarity 都是 `0.0`，即使联系人新增本身成功，也在起始 stage 直接失败，最终 `none / 0.0`。
2. **`modify_reminder...` 被结构化状态校验压低。** replay 诊断明确写到 `m2_c0` 是 `update_similarity=0.0`，`actual_rows=3`、`expected_rows=1`。也就是说，目标 reminder 虽然被触达，但 replay 认为最终 reminder state 没有按预期收敛。
3. **`find_days_till_holiday_wifi_off_alt` 对依赖链更严格。** replay 仍能完成一部分任务，但 `SETTING` preserve-state 在 `m3` 被打回，同时缺少 `get_current_timestamp` 这一关键前置步骤。

### 5.3 replay 抬高分数的主因

1. **`remove_contact...` 更接近真实成功语义。** replay stage m0 pass，finish 也 pass，说明“拒绝删除”的语义被接受；default 原生 similarity 只给了 0.617。
2. **`update_contact_relationship...` 更依赖结构化状态和语义 review。** default 原生 similarity 只有 0.788，但 replay 的 m0 / m1 / m2 全 pass，最终 full 0.946。这里 replay 明显比 default 更贴近“真的改对了”。

### 5.4 运行层面是正常的

- replay 只有 2 个 policy stop，且都能在 raw summary / report 中对上：
  - `find_days_till_holiday_insufficient_information`：fatal minefield，预期 stop。
  - `modify_reminder_with_recency_latest`：`milestone_no_progress:m2` 虚拟早停，但 `replay_continue_after_virtual_stop=true`，所以评估仍完整落盘，这属于正常流程。
- 没有 `llm_failed_call_count`，没有 JSON 缺失，也没有轨迹不一致。

## 6. 结论

当前这轮实验的运行记录是正常的，问题主要在评估口径不一致：

- default 和 replay 看的是同一条轨迹；
- replay 更严格地检查状态保留、结构化更新和语义终态；
- default 原生 similarity 有时偏松，有时偏保守。

因此，`add_contact...` 和 `modify_reminder...` 更像 replay 侧的过严或适配问题；`remove_contact...` 和 `update_contact_relationship...` 则说明 default 原生分数低估了真实完成度。后续若继续用这组结果做方法对比，建议优先以 replay 的分 stage 证据为准，同时对 `REMINDER/MESSAGING/SETTING` 的 guardrail 规则再做一次适配复核。

## 7. 补充核查：default 判对但 replay 判错的 case

本节聚焦非语义宽松问题。也就是说，`remove_contact...`、`update_contact_relationship...` 这类 replay 通过语义复判给出更高分的情况不作为问题处理；这里仅看 default 明显给高分、replay 明显压低的 case。

### 7.1 `add_contact_with_name_and_phone_number_3_distraction_tools`

结论：replay 判定存在明显问题，问题在 preserve-state guardrail 的结构化参考状态。

核查事实：

- default 原生结果为 `0.844306`，milestone 0 命中 `snapshot_index=17` 且 similarity `1.0`；agent 实际调用 `add_contact` 后，最终 `CONTACT` 增加了 `Stephen Sondheim / +19876543210`。
- replay 在 `__start__->m0` 直接判 `fail / 0.0`，导致后继消息 milestone `m1` 因前置未完成变成 `missing`。
- replay 失败的硬约束是 `m0_c1` 与 `m0_c3`：
  - `m0_c1`: `REMINDER` preserve-state，`guardrail_similarity=0.0`
  - `m0_c3`: `MESSAGING` preserve-state，`guardrail_similarity=0.0`
- 但这两个约束的 stage goal 写的是“Preserve ... state relative to reference milestone index -1. This means the relevant state should remain unchanged or equivalent, not that the namespace must be empty.”
- replay 报告里的失败证据却显示 `expected_rows=0`、`expected_excerpt={'rows': [], 'columns': []}`，而实际状态分别是 `REMINDER actual_rows=3`、`MESSAGING actual_rows=5`。

判断：

这里的 replay 不是发现了真实状态污染，而是把“相对于初始状态保持不变”的约束错误地落成了“期望空表”。`reference_milestone_node_index=-1` 应该指向 runtime initial snapshot；当前证据显示 expected 侧没有正确装载初始 snapshot。因此该 case 的 replay `none / 0.0` 不能作为 agent 失败结论。

### 7.2 `modify_reminder_with_recency_latest`

结论：replay 判定也存在问题，问题在 exact timestamp 目标与运行时语义不一致。

核查事实：

- default 原生结果为 `1.0`，`resolved=true`，milestone 0/1/2 全部 similarity `1.0`。
- agent 执行链路完整：先拿当前时间，再计算明天 17:00，最后调用 `modify_reminder`：
  - `datetime_info_to_timestamp({"year": 2026, "month": 7, "day": 28, "hour": 17, ...}) -> 1785229200.0`
  - `modify_reminder(reminder_id="54f20be9-...", reminder_timestamp=1785229200.0)` 成功
- replay 在最后一次 `m2` attempt，即 `step_index=42 / snapshot_id=toolsandbox:42`，已经能看到最终 `REMINDER` 目标行被改成 `reminder_timestamp=1785229200.0`。
- 但 replay 的 `m2_c0` 期望值是 `reminder_timestamp=1784883600.0`，对应 2026-07-24 17:00（Asia/Shanghai）；实际 agent 写入的是 2026-07-28 17:00（Asia/Shanghai），二者相差 `345600` 秒，即 4 天。
- 结合本轮运行日期与工具返回时间，`1785229200.0` 才是“tomorrow 5PM”的运行时语义目标；`1784883600.0` 明显是过期或错误冻结的 adapted target。

判断：

replay 在这个 case 中不是因为 agent 没有修改 reminder，而是因为结构化 milestone 固定了一个与运行时“tomorrow 5PM”不一致的绝对时间戳。default 原生 evaluator 接受了 agent 的运行时正确行为，replay 则被 stale expected timestamp 卡住。因此 replay 的 `partial / 0.621736` 不适合作为 agent 未完成任务的证据。

### 7.3 最大共性

这两条 replay 误判的最大共性是：**DynSTEER replay 的结构化状态约束依赖了错误或过期的 expected/reference state，而不是可靠地绑定到当前运行的 task semantics 与 runtime snapshot。**

更具体地说：

- `add_contact...` 是 reference snapshot 缺失或没有正确装载，导致 preserve-state guardrail 把“保持初始状态”误读成“命名空间应为空”。
- `modify_reminder...` 是动态时间目标被静态绝对时间戳冻结，导致 replay 用过期 target 去比较运行时正确结果。

这类问题的本质不是 LLM judge 是否宽松，而是 structured scorer / adapted case reference 的状态基准不可信。后续优先修复方向应是：

1. preserve-state guardrail 必须明确使用 runtime initial snapshot 或已 matched milestone snapshot，禁止把空 `expected.rows` 当作真实期望状态。
2. 时间敏感目标不能在 adapted case 中长期冻结绝对 timestamp；需要基于 runtime current timestamp 重新生成目标，或在 replay 评分时使用相对时间语义/benchmark 原生 helper 计算期望值。
3. 当 structured scorer 的 `expected_rows=0` 但 stage goal 是 preserve-state 时，应触发诊断告警，提示“expected 为空只是占位符，不可直接作为空表期望”。
