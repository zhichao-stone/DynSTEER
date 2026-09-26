# Milestone minefield reason 支持度聚合优化方案

## 背景

`results/milestone/toolsandbox_milestone_reliability_main` 中的保存响应已完成无 LLM 离线聚合扫描。当前 compiler 输出与扫描器模拟结果完全一致，因此策略对比可信。当前 fatal exact 指标为 67/98/97，precision 68.37%、recall 69.07%、F1 68.72%。主要问题是部分 fatal tool identity 已达到阈值，但最终 reason 只有个别 observation 支持，导致误报。

## 离线扫描结论

保持现有 readonly fatal 1/3、write fatal 1/2 tool identity 阈值不变，仅要求最终 `reason_code` 至少获得 2 个 observation 支持：

- 当前：67 TP / 98 Pred / 97 Ref，P=68.37%，R=69.07%，F1=68.72%。
- 新策略：67 TP / 92 Pred / 97 Ref，P=72.83%，R=69.07%，F1=70.90%。
- 变化：FP 从 31 降到 25，TP 和 FN 不变。

提高 reason 支持度到 3 会损失 TP；降低 write tool identity 阈值只增加 FP；任意 proposal 聚合会显著破坏 precision。因此本次只采用 reason support >= 2。

## 代码修改

1. `dynsteer/milestone/compiler.py`
   - 修改位置：`_compile_minefields()`。
   - 在契约归一化选出最终 `reason_code` 后，读取 `reason_counts[core][reason_code]`。
   - 若该值小于 2，则跳过该 minefield。
   - metadata 新增：
     - `reason_support_count`：最终 reason 的支持数。
     - `reason_support_policy`：固定为 `min_two_observations`。
   - 保留现有 `support_count`、`support_policy` 与 `reason_support_counts`，便于诊断。
   - 不修改普通 graph node 聚合、fatal tool identity、prompt 和生成参数。

2. `tests/milestone/test_generation_stability.py`
   - 增加 core 支持达到 readonly 1/3 阈值，但最终 reason 只有 1 票时被拒绝的测试。
   - 扩展已有 readonly 2/6 支持测试，确认最终 reason 有 2 票时接受，并记录新增 metadata。
   - 保留现有 write 半数和 readonly 三分之一阈值测试，防止 tool identity 行为回退。

## 验证

- 运行 `tests/milestone` 全量测试。
- 运行 `py_compile`。
- 使用当前结果目录中的保存 LLM response 做无网络 replay，重新输出：
  - tool operation micro/macro；
  - fatal exact 与 tool-only；
  - FP/FN confusion。
- 由于 fatal minefield 会触发 `aggregated_minefield_terminal_conflict` 并影响部分 graph node，最终报告必须以完整 compiler replay 为准，而不是只看离线 minefield multiset 模拟。

## 附录A. 项目中没有把握实现的模块部分

- reason 支持度目前沿用现有 `reason_counts` 统计口径；如果同一 observation 中同一 core 存在多个非完全相同 reason 变体，统计的是候选支持而不是严格去重后的 observation 数。离线扫描已按该口径与当前 compiler 完全对齐，但该口径未来若要改为 per-observation 去重，需要重新评估指标。
- 完整 replay 中有 1 个历史保存响应存在 replay error；该 case 不是本次聚合修改引入，报告时需单独说明。
