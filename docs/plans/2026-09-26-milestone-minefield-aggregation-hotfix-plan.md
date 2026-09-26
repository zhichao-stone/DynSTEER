# Milestone Minefield 聚合热修与离线重放计划

- 日期：2026-09-26
- 范围：`dynsteer/milestone/compiler.py`、`tests/milestone/test_generation_stability.py`、`results/milestone/toolsandbox_milestone_reliability_main`
- 目标：仅调整 fatal minefield 的聚合身份，不修改 prompt、不调用 LLM、不改变普通 milestone graph 的节点与边聚合。

## 1. 修改原则

1. 保持候选生成、repair、候选级校验、graph node/edge majority 不变。
2. fatal minefield 第一轮投票不再把 `reason_code` 放入核心身份。
3. fatal tool identity 获得严格多数后，reason 由确定性规则归一：
   - 候选 reason 可通过 contract 语义校验时保留；
   - `missing_required_input` 缺少合法 required dynamic input 且工具无写集时，归一为 `unsafe_tool_call`；
   - `unsafe_side_effect` 只保留给有写集的工具；
   - `unsafe_tool_call` 只保留给无写集的工具；
   - 无法确定合法 reason 时不输出 minefield。
4. `missing_inputs` 不参与投票身份；仅在归一后 reason 为 `missing_required_input` 时从支持候选中择优。
5. 本轮不实现 view-level producer recoverability，不改 prompt，也不新增 safety channel。

## 2. 代码修改

### 2.1 `dynsteer/milestone/compiler.py`

修改 `_compile_minefields()`：

- 第一轮 support core 从 `(turn_id, evidence_id, reason_code)` 改为 `(turn_id, evidence_id)`。
- 每个 observation 对同一 tool identity 只计一次支持。
- 统计支持该 tool identity 的 reason 变体和 missing inputs 变体。
- 新增内部 helper `_deterministic_minefield_reason()`，根据 tool contract 写集、required dynamic inputs 和候选 reason 变体归一 reason。
- 获得严格多数的 tool identity 才进入 reason 归一；归一失败则跳过。
- 输出的 minefield metadata 继续记录 support、reason 支持计数和 observation 总数，便于后续消融。

### 2.2 测试

在现有生成稳定性测试中补充：

1. 同一 fatal tool 的 reason 被拆票时，tool identity 可聚合，reason 按只读工具规则归一。
2. 写工具的 `unsafe_side_effect` 不被错误归一为只读 unsafe call。
3. 无法与 contract 匹配的 reason 不输出。
4. 普通节点严格多数行为不变。

## 3. 离线重放与指标

使用现有 `raw_response_records` 与本地 `data/experiments/toolsandbox_milestone_reliability_main.json` 重建 Toolsandbox view，用假 LLM 按原始响应顺序回放，不发起网络请求。

输出：

- 当前 primary 485 case 的 Tool Operation micro / macro；
- 调整后的 Fatal Minefield micro；
- fatal identity 混淆；
- 与原始 summary 的差值；
- 单独模拟普通 graph 聚合阈值放宽对 Tool Operation micro / macro 的影响，不落盘覆盖原始结果。

## 4. 附录A. 项目中没有把握实现的模块部分

1. 候选级 `_validate_candidate_graph()` 仍会先过滤部分 reason 错误的 minefield；本轮只在最终聚合阶段归一，实际提升可能低于直接从 raw response 统计的离线上限。
2. graph 聚合放宽目前只做只读对照模拟，不修改生产逻辑；不同节点身份、binding closure 与 pruning 之间存在交互，不能把阈值模拟线性外推为真实收益。
3. 本地 Toolsandbox 数据与服务器数据存在路径差异；重放以本地同一 case 配置和保存 response 为准，若数据版本不同，指标可能有轻微差异。

## 5. 实施与重放结果（2026-09-26 已完成）

实际落地内容：

1. fatal tool 第一轮投票身份改为 `(turn_id, evidence_id)`。
2. fatal tool support 阈值调整为 `>= 1/2`；普通 graph node 仍保持严格多数 `> 1/2`。
3. 候选 minefield reason 仅按公开契约做最小归一：
   - 无写集工具的非法 `missing_required_input` 归一为 `unsafe_tool_call`；
   - 无写集工具的 `unsafe_side_effect` 归一为 `unsafe_tool_call`；
   - 写工具上语义不相容的 `unsafe_tool_call` 不猜测为 `unsafe_side_effect`，直接拒绝；
   - `missing_inputs` 不参与第一轮身份。
4. prompt、LLM、普通 graph node/edge/disposition 聚合未修改。
5. 使用保存的 509 个 LLM response 离线重放，无网络调用；输出为：
   - `results/milestone/toolsandbox_milestone_reliability_main/summary_minefield_tool_identity_replay.json`

核心重放结果：

| Metric | 修改前汇总 | 保存响应重放 |
| --- | ---: | ---: |
| Tool Operation Micro P/R/F1 | 73.13% / 73.49% / 73.31% | 77.56% / 73.24% / 75.34% |
| Tool Operation Macro P/R/F1 | 68.76% / 68.05% / 66.70% | 75.35% / 74.85% / 73.43% |
| Fatal Minefield Micro P/R/F1 | 43.48% / 10.20% / 16.53% | **64.91% / 37.76% / 47.74%** |

Fatal 计数从 10 / 23 / 98 提升到 37 / 57 / 98。Tool-only fatal 口径为 40 / 57 / 98，P/R/F1 = 70.18% / 40.82% / 51.61%。

注意：Tool Operation 的重放值同时包含当前工作区在服务器运行后已有的 compiler 修复，因此不能把 Tool Operation 的全部变化归因于本轮 minefield 聚合热修；原始服务器 summary 与当前工作区代码存在版本差。

## 6. Graph 节点阈值对照模拟

只临时修改 `2 * len(values) > n` 为不同阈值并重放，模拟后已恢复生产代码。结果仅用于对照，不代表建议直接落地：

| Graph node 支持阈值 | Operation Micro P/R/F1 | Operation Macro P/R/F1 |
| ---: | ---: | ---: |
| 当前严格多数 > 1/2 | 77.56% / 73.24% / 75.34% | 75.35% / 74.85% / 73.43% |
| >= 1/2 | 65.94% / 84.62% / 74.13% | 69.31% / 82.17% / 72.81% |
| >= 1/3 | 62.75% / 86.08% / 72.59% | 66.90% / 82.02% / 71.38% |
| >= 1/4 | 62.75% / 86.08% / 72.59% | 66.90% / 82.02% / 71.38% |
| >= 1/6 | 59.97% / 86.68% / 70.89% | 64.35% / 82.07% / 69.74% |

结论：放宽 graph 聚合会显著提高 recall，但 precision 大幅下降，micro/macro F1 均低于当前严格多数，因此本轮不建议调整 graph 聚合。优先继续处理 minefield 的 `add_reminder` 可恢复输入误报和 reason 确定化。
