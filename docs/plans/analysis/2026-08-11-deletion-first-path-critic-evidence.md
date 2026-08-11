# 删除优先方案 path critic 失败证据与决策

## 1. 25-case 首轮证据

实验：`toolsandbox_milestone_reliability_25_deletion_first`

- graph return：25/25；generation failure：0；
- operation macro-F1：0.667；precision：0.727；recall：0.660；
- topology macro-F1：0.493；fatal minefield macro-F1：0.867；
- false-empty：1；false-nonempty：2；spurious fatal minefield：1。

主要失败类型：

1. `planner_missing_required_operation`：date/time、recency producer 被 counterexample 路径删除；
2. `planner_spurious_operation`：信息不足 case 使用 initial state 业务记录编造可执行路径；
3. `aggregation_false_optional`：同一 ensemble 混入 producer-only、terminal-only 路径，operation 交集变空；
4. `topology_alignment_wrong`：不完整路径或未交换独立顺序造成共同边错误；
5. reference/generated tool-name 不可比：scrambled case 已通过 reliability-only alias 修复，alias 不进入 generator view。

## 2. Prompt 定向修复后的证据

8-case 定向回归已明确要求：

- initial state 业务行不能代替 Agent producer；
- relative time 必须保留 current-time 与 conversion chain；
- 每条 executable path 必须独立包含终端效果及全部 producer；
- avoidance target 不是删除必需 operation 的命令。

两次定向回归 operation macro-F1 仍约 0.74；raw response 仍包含：

- 只有 `datetime_info_to_timestamp`、没有 `add_reminder` 的路径；
- 只有 `add_reminder`、硬编码 timestamp 的路径；
- 删除 `search_contacts` 后直接使用 initial state person ID 的路径；
- `response_only` 与错误 executable 路径混合，导致 fatal forbidden 交集消失。

因此失败主因已符合方案附录 A.2 的条件：第二轮持续提交漏步反例，确定性环境模拟无法判定自然语言任务是否完成。

## 3. 最小 critic 评估结果

曾以以下限制试验一次批量 path critic：

- 复用 generation/refinement 的同一 `paths` schema；
- 不新增生产 `.py` 模块、配置字段、planner 类或 task-family 分支；
- critic 只筛除/修复不完整 ensemble，不读取 reference、verifier、trajectory 或 final state；
- critic 不重新挑战共同 operation；
- critic 至少有一条路径通过结构与环境模拟时采用，否则回退 refinement/draft；
- raw response 与 round/path violation 继续进入现有审计报告。

8-case 实验结果：

- 允许 critic 重写路径时：operation F1 0.73、topology F1 0.42；
- 限制 critic 只能筛选候选子集时：operation F1 0.69、topology F1 0.38；
- 两种方式均未达到门槛，且 critic 仍无法可靠识别硬编码动态 timestamp/ID。

按照方案“指标未提升则停止实施，不增加更多字段、规则或 fallback”的约束，最终代码不保留第三次 critic 调用，恢复最多 generation + refinement 两轮。该失败应作为独立后续研究证据，而不是继续扩展当前 compiler。

## 4. 最终验收结论

修正 reference state-change→具体工具映射、scrambled reference→Agent 工具别名以及 operation 多重集口径后，对 25-case 原始生成结果离线重放：

- operation macro-F1：0.737；precision：0.800；recall：0.727；
- topology macro-F1：0.493；minefield macro-F1：0.867；
- false-empty：1；false-nonempty：2；spurious fatal minefield：1；fatal miss：3。

其中 graph return 25/25、generation failure 0 已达到硬门槛，但相似性门槛没有达到。后续两轮 8-case prompt 定向回归最高 operation macro-F1 约 0.74，未证明继续增加 prompt 条目能够改善全量指标。

因此本次实现严格停在方案允许的边界：保留多路径生成、确定性环境模拟、交集聚合和一次 refinement；不恢复 binding/GoalContract，不加入 task-family 分支，不以 reference、fallback 或放宽指标制造 false green。
