# ToolSandbox Milestone Reliability 重算与结果分析

## 1. 结论摘要

本报告针对 `results/milestone/toolsandbox_milestone_reliability_main` 中 2026-09-25 13:59:52 UTC 开始的 509-case 批次重新整理。由于服务器运行时使用的是旧版 `milestone_reliability.py`，目录内原始 `summary.json` 的汇报口径与当前脚本不一致；因此本报告不直接沿用该 summary，而是从 509 个逐 case JSON 记录重新聚合出当前脚本会输出的指标。

核心结论如下：

1. **运行链路本身没有失败**：509/509 个 case 均为 `completed`，没有 `generation_failed`、`adapt_failed` 或 `ged_failed`。
2. **工具操作集合表现中等偏稳**：primary 集合上 Tool Operation Micro P/R/F1 为 **73.13% / 73.49% / 73.31%**，Macro F1 为 **66.70%**。Micro 与 Macro 的差距说明部分 case 的错误较重，简单任务掩盖不了全部失败模式。
3. **Fatal minefield 是最大短板**：98 个参考 fatal minefield 身份中仅命中 10 个，Micro P/R/F1 为 **43.48% / 10.20% / 16.53%**。漏检远重于误报，且命中几乎集中在 `remove_contact` 相关身份。
4. **拓扑相似度呈两极分布**：Topological GED Similarity 均值为 **0.6142**，但中位数为 0.75，25% 分位为 0，75% 分位为 1。生成结果在部分 case 上形状完全正确，在另一部分上完全失效。
5. **当前脚本的 `valid_dag_compilation_rate` 不能理解为“至少一个候选有效”**：当前脚本按最终是否返回可评测 graph 计算，本批为 **100%**；逐候选记录显示只有 418/509 = **82.12%** 的 case 至少有一个有效候选。
6. **任务级完全正确率很低**：primary 集合中 `goal_effect_exact` 仅 **10/485 = 2.06%**，`operation_topology_exact` 仅 **22/485 = 4.54%**。工具集合指标尚可，但完整 blueprint 的目标效果、约束和拓扑仍明显不可靠。
7. **无需重跑实验来恢复当前脚本的核心汇总指标**：逐 case JSON 保留了官方汇总所需的 operation、minefield 和 topology 记录，已可重算。

## 2. 数据与重算口径

### 2.1 实验元信息

| 项目 | 数值 |
|---|---:|
| 实验 ID | `toolsandbox_milestone_reliability_main` |
| Benchmark | ToolSandbox |
| 计划 case 数 | 509 |
| 实际逐 case JSON 数 | 509 |
| Generator | `qwen3-max-2026-01-23`，temperature 0.2 |
| Random seed | 202608 |
| Worker 数 | 5 |
| 开始时间 | 2026-09-25 13:59:52.308465 UTC |
| 结束时间 | 2026-09-25 14:46:37.510712 UTC |
| 总耗时 | 46 分 45.2 秒 |

### 2.2 数据来源

- 原始索引：`results/milestone/toolsandbox_milestone_reliability_main/index.json`
- 服务器旧口径汇总：`results/milestone/toolsandbox_milestone_reliability_main/summary.json`
- 逐 case 记录：`results/milestone/toolsandbox_milestone_reliability_main/toolsandbox/repeat_00/*.json`
- 当前脚本口径重算结果：`results/milestone/toolsandbox_milestone_reliability_main/summary_current_script.json`

原始 `summary.json` 保留为服务器运行时产物，未修改。`summary_current_script.json` 是按当前 `milestone_reliability.py` 的 `_write_report()` 结构重写的新文件。

### 2.3 集合过滤规则

当前脚本在 semantic 评测前排除两个 few-shot 污染前缀：

- `search_relationship_with_phone_number`
- `remove_reminder_with_recency_latest`

本批共有：

| 集合 | 数量 |
|---|---:|
| 全部 case | 509 |
| completed case | 509 |
| few-shot 污染排除 | 24 |
| primary completed case | 485 |

Tool Operation 与 Fatal Minefield 在这 485 个 primary completed case 上聚合；Topological GED Similarity 按当前脚本在全部 509 个 completed case 上求均值。

## 3. 当前脚本官方汇报指标

### 3.1 Milestone Blueprint Compilation Reliability

| Evaluation Dimension | Precision | Recall | F1 Score |
|---|---:|---:|---:|
| Tool Operation (Micro) | 73.13% | 73.49% | 73.31% |
| Tool Operation (Macro) | 68.76% | 68.05% | 66.70% |
| Fatal Minefield Detection (Micro) | 43.48% | 10.20% | 16.53% |

| Structural Topology Metric | Score |
|---|---:|
| Topological GED Similarity Mean | 0.6142 |
| Valid DAG Compilation Rate | 100.00% |

### 3.2 计数支撑

| 指标 | TP / Predicted / Reference | 说明 |
|---|---:|---|
| Tool Operation Micro | 607 / 830 / 826 | 先累加 485 个 primary case 的 TP、生成数、参考数，再计算 P/R/F1 |
| Fatal Minefield Micro | 10 / 23 / 98 | 同样按身份计数 micro 聚合 |
| Graph Returned | 509 / 509 | 当前脚本以最终返回可评测 graph 为准 |
| Topological GED | 509 个 completed case | 均值 0.6142220255 |

注意：当前脚本只输出 Fatal Minefield 的 micro 指标，不输出 macro。逐 case 平均得到的 fatal macro F1 为 0.7918，但该值被大量无 minefield 的负例抬高，不适合作为安全指标汇报。

## 4. 主要现象分析

### 4.1 Tool Operation：总体覆盖与冗余基本平衡，但 case 级稳定性不足

Micro Precision 与 Recall 分别为 73.13% 和 73.49%，说明整体上既没有明显的“过度生成工具”，也没有明显的“系统性少生成工具”。485 个 primary case 中，参考操作 826 个，生成操作 830 个，TP 607 个。

但 Macro F1 只有 66.70%，比 Micro F1 低 6.61 个百分点。这说明错误并非均匀分布在每个操作上，而是集中在部分 case；一些任务操作全对，另一些任务则出现大片段缺失或错误。

逐 case operation F1 分布也验证了这一点：

| 统计量 | 数值 |
|---|---:|
| Min | 0.0000 |
| P25 | 0.4000 |
| Median | 0.8571 |
| P75 | 1.0000 |
| Max | 1.0000 |
| Mean | 0.6670 |

因此，后续优化不应只追求全局操作数量匹配，而应针对低分 case 做错误归因，尤其是完全漏路径、搜索类误调用和多步约束传递失败。

### 4.2 Fatal Minefield：安全关键身份漏检严重

Fatal minefield 的主要问题不是误报，而是漏检：

- 参考 fatal 身份：98
- 生成 fatal 身份：23
- 命中：10
- 漏检：88
- 误报：13
- Recall：10 / 98 = 10.20%
- Precision：10 / 23 = 43.48%

按结果记录中的 per-tool recall 观察：

| 工具 / 身份族 | 参考出现 | 表现 |
|---|---:|---|
| `remove_contact` | 10 | 平均 recall 0.8 |
| `contact_2` | 2 | 平均 recall 1.0 |
| `timestamp_diff` | 10 | 全部漏检 |
| `search_reminder` / `reminder_3` | 36 / 6 | 全部漏检 |
| `send_message_with_phone_number` / `messaging_1` | 10 / 2 | 全部漏检 |
| `modify_contact` / `contact_1` | 12 / 2 | 全部漏检 |
| `modify_reminder` / `reminder_1` | 5 / 1 | 全部漏检 |

10 个命中基本集中在联系人删除族；时间差、reminder 检索、联系人修改和短信发送等安全前置条件几乎完全缺失。这说明当前生成或共识阶段没有稳定保留“不可执行条件”，而不是阈值轻微偏低。

### 4.3 Topology：平均分尚可，但两极分化

Topological GED Similarity 均值 0.6142，中位数 0.75。其分布为：

| 统计量 | 数值 |
|---|---:|
| Min | 0.0000 |
| P25 | 0.0000 |
| Median | 0.7500 |
| P75 | 1.0000 |
| Max | 1.0000 |
| Mean | 0.6142 |

该分布说明系统在多数普通任务上能接近参考拓扑，但一旦遇到多步搜索、修改或 fatal 前置条件，容易整体塌缩为空图或单节点图，导致得分直接落向 0。仅用平均值会掩盖这种失败模式。

Strict 视角进一步暴露语义标签问题：

| 视角 | GED Similarity Mean | Node-set F1 Mean |
|---|---:|---:|
| Semantic | 0.5563 | — |
| Strict | 0.4357 | 0.0668 |
| Topology | 0.6142 | 0.6394 |

Topology 与 Strict 的巨大差距说明：生成图的大致节点数量或先后关系可能接近参考，但节点语义标签、状态效果和约束身份很难严格对齐。Blueprint 可执行性不能只依赖拓扑指标判断。

### 4.4 Goal Effect 与任务级完全正确率

从逐 case 记录继续计算：

| 指标 | 数值 |
|---|---:|
| `goal_effect_exact` | 10 / 485 = 2.06% |
| `operation_topology_exact` | 22 / 485 = 4.54% |

10 个 `goal_effect_exact` case 全部来自 `remove_contact` 任务族，其余任务族没有完全命中。这说明即使 Tool Operation Micro F1 达到 73.31%，完整目标效果和约束仍很难同时正确。

### 4.5 空图问题

509 个 completed case 中：

- 参考语义空图：0
- 生成语义空图：75
- false-empty case：75
- 非空生成 case：434

空图原因分布：

| 原因 | Case 数 |
|---|---:|
| `empty_after_terminal_majority` | 40 |
| `empty_after_aggregation` | 30 |
| `empty_after_binding_closure` | 4 |
| `empty_after_state_projection` | 1 |

空图与非空图的对比：

| 生成状态 | Case 数 | Operation Macro F1 | Topological GED | 参考操作数 | Goal Exact |
|---|---:|---:|---:|---:|---:|
| 非空 | 434 | 0.7108 | 0.6651 | 773 | 10 |
| 空 | 75 | 0.4800 | 0.3200 | 95 | 0 |

所有 75 个空图都是 false empty。尤其 `empty_after_terminal_majority` 占 40 个，说明 terminal majority 判定会把本应保留的前置操作或目标效果压掉，是当前重要的聚合策略风险。

## 5. 扰动与任务族切片

### 5.1 按 case 后缀扰动类型切片

下表在 primary completed case 上计算 Tool Operation，在 completed case 上计算拓扑与 strict 指标。它是诊断切片，不属于当前脚本官方 summary 字段。

| 扰动类型 | Primary n | Operation Micro F1 | Operation Macro F1 | Topology GED | Strict Node F1 | Goal Exact |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 87 | 0.7541 | 0.7261 | 0.6711 | 0.1099 | 0.00% |
| 3 distraction tools | 75 | 0.7273 | 0.6608 | 0.6122 | 0.0633 | 2.67% |
| 10 distraction tools | 23 | 0.7333 | 0.5122 | 0.4714 | 0.0435 | 0.00% |
| arg description scrambled | 75 | 0.7451 | 0.6763 | 0.6185 | 0.0633 | 2.67% |
| arg type scrambled | 75 | 0.7236 | 0.6504 | 0.5981 | 0.0506 | 2.67% |
| tool description scrambled | 75 | 0.7171 | 0.6507 | 0.6041 | 0.0633 | 2.67% |
| tool name scrambled | 75 | 0.7266 | 0.6757 | 0.6143 | 0.0506 | 2.67% |

观察：

1. 3 个干扰工具和字段级 scramble 对 operation micro F1 的影响较小，多数仍在 0.72–0.75。
2. 10 个干扰工具时 micro F1 仍为 0.7333，但 macro F1 降到 0.5122，拓扑降到 0.4714，说明大规模干扰会造成部分 case 的严重失败。
3. `arg_type_scrambled`、`tool_description_scrambled` 和 `tool_name_scrambled` 的 topology 与 strict 指标略差，但差异不极端；当前失败更多来自任务语义与聚合，而不是单纯的字段扰动识别。

### 5.2 主要低分任务族

| 任务族 | Primary n | Operation P | Operation R | Operation F1 | Topology GED | Fatal TP / Pred / Ref | 生成空图 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `search_reminder` | 78 | 38.46% | 76.39% | 51.16% | 0.4001 | 0 / 0 / 42 | 2 |
| `modify_contact` | 42 | 74.19% | 41.07% | 52.87% | 0.4348 | 0 / 0 / 14 | 14 |
| `update_contact` | 10 | 69.23% | 45.00% | 54.55% | 0.3500 | 0 / 0 / 0 | 5 |
| `add_reminder` | 76 | 64.86% | 63.16% | 64.00% | 0.6625 | 0 / 13 / 0 | 5 |
| `remove_contact` | 36 | 50.00% | 50.00% | 66.67% | 0.4306 | 10 / 10 / 12 | 21 |
| `send_message` | 41 | 75.29% | 75.29% | 75.29% | 0.4172 | 0 / 0 / 12 | 8 |
| `find_days` | 51 | 86.39% | 92.03% | 89.12% | 0.7647 | 0 / 0 / 12 | 6 |

主要问题分为三类：

1. **`search_reminder`：高冗余、低 precision**。生成 143 个操作，而参考只有 72 个；同时 42 个 fatal 身份全部漏检。
2. **`modify_contact` / `update_contact`：路径覆盖不足**。Recall 只有 41.07% 和 45.00%，并伴随大量空图。
3. **`send_message` / `find_days`：操作集合尚可，但 fatal 前置和拓扑丢失**。Operation F1 达到 75%–89%，但拓扑只有 0.42–0.76，fatal 全部漏检。

相对稳定的是 `turn_on_*`、`get_*`、简单搜索和联系人/reminder 创建类任务；其中 `turn_on_*` Operation F1 为 0.9412。

## 6. 生成效率与候选质量

| 指标 | 数值 |
|---|---:|
| LLM 请求数 | 1,770 |
| 请求失败数 | 0 |
| 平均请求数 / case | 3.477 |
| 触发第 4 次补偿请求的 case | 243 / 509 = 47.74% |
| 返回候选数 | 3,540 |
| 解析候选数 | 3,518 |
| 有效候选数 | 2,373 |
| 至少一个有效候选的 case | 418 / 509 = 82.12% |
| 平均返回候选 / case | 6.9548 |
| 平均解析候选 / case | 6.9116 |
| 平均有效候选 / case | 4.6621 |
| 平均 accepted observation / case | 5.3752 |
| 平均全局唯一候选 / case | 2.6405 |
| 达到目标候选数的 case | 335 / 509 = 65.81% |
| low sample case | 174 |
| low diversity case | 77 |

Token 与耗时：

| 指标 | 数值 |
|---|---:|
| Prompt tokens | 10,753,559 |
| Completion tokens | 1,125,778 |
| Total tokens | 11,879,337 |
| 平均 tokens / case | 23,338.35 |
| LLM elapsed seconds 总和 | 13,883.78 |
| 平均逐 case wall time | 27.39 秒 |

候选质量问题主要体现在：

- 目标 6 个候选，但平均全局唯一候选只有 2.64。
- 47.74% 的 case 触发第 4 次补偿请求，成本被明显放大。
- 3,540 个返回候选中，只有 2,373 个通过有效性检查。
- 91 个 case 没有任何有效候选，但当前最终 `graph_returned` 仍为 true；这解释了官方 100% 编译率与逐候选 82.12% 有效率之间的差异。

## 7. 改进建议

### 7.1 优先级 1：重构 fatal minefield 生成与验收

建议：

1. 在候选图进入共识前显式抽取 fatal identity，而不是依赖普通节点语义间接保留。
2. 为 `timestamp_diff`、`search_reminder`、`modify_contact`、`send_message_with_phone_number` 建立工具族级 fatal 模板。
3. 将 per-tool fatal recall 作为生成期硬校验：参考图存在高风险前置条件时，最终图缺失 fatal identity 不应被静默接受。
4. 报告继续使用 micro recall / F1，避免用被负例抬高的 macro 指标判断安全性。

目标应至少把 10.20% 的 fatal recall 提升到可解释、可回归监控的水平；在本批数据上，每多命中 10 个参考身份，recall 约提高 10.2 个百分点。

### 7.2 优先级 2：修复 false-empty 聚合

`empty_after_terminal_majority` 与 `empty_after_aggregation` 合计造成 70 个空图。建议：

1. 当输入任务包含可执行工具调用且候选中出现非空图时，禁止 terminal majority 单独把最终图压成空图。
2. 对空图与非空候选冲突的 case 保留分歧信号，引入恢复式聚合或二次生成。
3. 将 75 个 false-empty case 作为最小回归集，目标是清零或至少显著降低 `empty_after_terminal_majority`。

### 7.3 优先级 3：针对低分任务族做专项归因

优先审计：

- `search_reminder`：为什么生成 143 个操作而参考只有 72 个，且 42 个 fatal 全漏。
- `modify_contact` 与 `update_contact`：为什么关键前置路径 recall 不足。
- `send_message` 与 `find_days`：操作集合较正确时，为什么拓扑与 fatal 前置仍丢失。

建议为这些任务族建立小的 golden regression set，并同时监控 operation F1、fatal recall、topology GED 和 goal exact，避免单指标回升掩盖其他退化。

### 7.4 优先级 4：提升候选多样性

当前平均全局唯一候选 2.64，明显低于目标 6；47.74% case 触发补偿请求。建议：

1. 降低同批候选重复惩罚的触发滞后，或在 prompt 中显式要求不同规划路径。
2. 对批内完全重复的响应提前终止同方向采样，改变温度或提示策略。
3. 对 `low_sample_count` 与 `low_diversity` case 采用不同补偿策略，而不是统一追加一次请求。

### 7.5 优先级 5：报告与运行可追溯性

这次问题的直接原因是服务器脚本版本未同步。建议：

1. 在 `index.json` 中记录 `milestone_reliability.py` 的 git commit 或文件 digest。
2. 启动脚本在运行前打印并持久化关键代码版本。
3. 汇总生成完成后校验 summary 顶层字段与当前脚本期望 schema 是否一致。
4. 保留原始服务器 summary，同时自动生成带代码版本标记的重算 summary，避免手工口径混淆。

## 8. 总体判断

本批结果呈现出“链路稳定、普通操作可用、安全语义和完整 blueprint 不可靠”的状态。509/509 case 完成评测说明基础设施和 API 调用稳定；Tool Operation Micro F1 73.31% 也说明生成器已经能识别大部分常规工具序列。但 Fatal Minefield Micro F1 仅 16.53%、goal-effect exact 仅 2.06%，且 75 个参考非空 case 被生成成空图，说明当前系统距离可靠的 milestone blueprint 还有明显差距。

最优先的工程目标不是继续提升拓扑平均分，而是把 fatal identity 保留、false-empty 恢复和低分任务族回归集建立起来。若这些问题不解决，拓扑或工具集合指标的提升可能无法转化为安全、可执行的任务规划。

## 7. Minefield 聚合热修重放（2026-09-26）

按当前工作区 compiler 将 fatal minefield 第一轮身份从 `(turn_id, evidence_id, reason_code)` 调整为 `(turn_id, evidence_id)`，reason 在 tool identity 获得半数支持后按公开契约确定化；prompt 与普通 graph 聚合保持不变。重放输入为已保存的 509 个 LLM response，无网络调用。

| Metric | 原始 current-script 汇总 | 聚合热修重放 |
|---|---:|---:|
| Tool Operation Micro P/R/F1 | 73.13% / 73.49% / 73.31% | 77.56% / 73.24% / 75.34% |
| Tool Operation Macro P/R/F1 | 68.76% / 68.05% / 66.70% | 75.35% / 74.85% / 73.43% |
| Fatal Minefield Micro P/R/F1 | 43.48% / 10.20% / 16.53% | **64.91% / 37.76% / 47.74%** |

Fatal identity 计数从 10 / 23 / 98 提升到 37 / 57 / 98；仅按 fatal tool 匹配时为 40 / 57 / 98，即 P/R/F1 = 70.18% / 40.82% / 51.61%。

剩余误报集中在：

- `add_reminder + missing_required_input`：15；
- `modify_contact + missing_required_input`：3；
- `search_contacts + unsafe_tool_call`：2。

因此下一阶段优先做 view-level recoverability，而不是先分离 safety channel 或调整 graph 聚合。

Graph 节点阈值只读对照显示，放宽到 `>=1/2` 后 Operation Micro 为 65.94% / 84.62% / 74.13%，Macro 为 69.31% / 82.17% / 72.81%；继续放宽到 `>=1/6` 时 Micro F1 降至 70.89%，Macro F1 降至 69.74%。放宽 graph 聚合会以 precision 换 recall，micro/macro F1 均不及当前严格多数，不建议本轮修改。

完整重放文件：
`results/milestone/toolsandbox_milestone_reliability_main/summary_minefield_tool_identity_replay.json`
