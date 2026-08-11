# toolsandbox_main 实验结果分析

- 分析时间：2026/08/10 13:32:00（Asia/Shanghai）
- 实验 ID：`toolsandbox_main`
- 配置：`data/experiments/toolsandbox_main.json`
- 结果：`results/exp/toolsandbox_main`
- 运行轨迹：`runs/exp/toolsandbox_main`
- 日志范围：`2026-08-04.log ~ 2026-08-06.log`

## 1. 结论摘要

1. **流程与产物总体可复核**：预期、索引和实际主键均为 12,216 条，缺失路径 0、JSON 解析失败 0、重复主键 0、DEFAULT↔replay 配对缺失 0。日志记录过一次历史执行失败，但最终同键产物存在且矩阵完整，判断为后续重跑已补齐。
2. **模型区分度更强，但不能等同于更准确**：epsilon=0.01 时 DS 从 0.0270 提升到 0.0500，模型均值总体标准差从 0.0224 增至 0.0345；同时 replay 平均分从 0.7582 降至 0.6892，说明区分度提升伴随整体降分与更强质量惩罚。
3. **结构完成与阶段质量混杂**：replay full/partial/none 为 3692/1575/841，但有 156 条“full 但 stage fail”。因此 full 不是质量通过，overall score 也不是 ToolSandbox native score。
4. **提前停止仅是 replay 中的 virtual stop 反事实**：严格因阶段低分直接停止的 `evaluation_policy_stop` 有 234 条（64 个不同 `case_id`），平均少执行 35.81% 的轨迹，但计入 Judge 后平均慢 3.81 秒/条；广义执行不力 stop 共 519 条（173 个不同 `case_id`），平均少执行 34.51%，当前可比口径下平均净节省 32.89 秒/条，但中位数仍慢 1.43 秒。这些记录不是在线重新运行 Agent 后发生的真实 stop，不能把模拟节省直接当成已实现收益。
5. **总量节省由少数长轨迹贡献，典型样本反而更慢**：replay 有效全流程总时长 164445.5052 秒，对应 DEFAULT 176134.0569 秒，总差值 -11688.5517 秒；但逐样本差值中位数为 +6.2320 秒，且 3938/6108（64.47%）配对不低于 DEFAULT。该结果仍缺 adaptation 成本，且来自 replay 反事实，不能直接写成真实在线收益。
6. **没有足够证据宣布 replay 优于 DEFAULT**：ToolSandbox 新 schema 不输出二元 resolved；replay 也没有在截断终态上重新运行同一 native scorer。跨方法 rank_tau=0.6667 只能作参考，不能代替正确性校准。

## 2. 实验矩阵与数据质量

- benchmark：`toolsandbox`（509 cases，data_root=`data/toolsandbox`）
- models：`deepseek-v4-pro`、`deepseek-v4-flash`、`qwen-plus-2025-12-01`、`qwen3-max-2026-01-23`
- methods：`default`、`dynsteer_replay`；repeats=3
- 预期矩阵：1 benchmark × 4 model × 2 method × 3 repeat × 509 case = 12,216。

| 检查项 | 结果 | 判断 |
|---|---:|---|
| index experiment_id | 一致 | 通过 |
| 预期/index/实际/唯一主键 | 12216/12216/12216/12216 | 通过 |
| 缺失路径 / JSON 解析失败 | 0 / 0 | 通过 |
| 分数不一致 / 时间戳异常 / 轨迹长度异常 | 0 / 0 / 0 | 通过 |
| tool_call 未配对 / tool_result 未配对 | 271 / 0 | 需核查截断边界 |
| final_state 缺失 / DEFAULT 配对缺失 | 0 / 0 | 通过 |

### 日志核查

- `logs/2026-08-04.log:186`：dynsteer.experiment.runner.ExperimentCaseExecutionError: experiment case 执行失败: experiment_id=toolsandbox_main, benchmark=toolsandbox, method=default, model_id=qwen-plus-2025-12-01, repeat_index=2, case_id=search_message_with_recency_latest_multiple_user_turn, error=Expecting ',' delimiter: line 1 column 103 (char 102) {"error": "experiment case 执行失败: experiment_id=toolsandbox_main, benchmark=toolsandbox, method=default, model_id=qwen-plus-2025-12-01, repeat_index=2, case_id=search_me..."}
最终索引仍包含上述同键 DEFAULT 与 replay 产物，且分数/元数据/路径均通过本次核查，因此该历史错误未造成最终矩阵缺失；报告保留它作为重跑痕迹，而不把它计为当前 P0。

### 已知字段限制

- DEFAULT 产物不包含 `resolved` 或 `successful`。`score==1.0` 仅在表中作为“精确 1 分计数”，不改名为 native success。
- `metrics.json` 不包含 `repeat_rank_consistency.<method>.<benchmark>`；因此不能按约束报告 tau-b、完整排序一致率、top-1 一致率和平均名次位移。本报告不从 `scores.json` 重造该指标。
- trajectory token 全为不可用；Agent token、适配 token、全流程 token 与适配/评估 token share 无法计算。
- case adaptation 的实际调用时长/token 未进入逐 case 产物，不能把缺失当作 0。

## 3. 方法级核心指标

判据：DEFAULT 只报告 ToolSandbox native continuous score；`score==1.0` 是精确满分样本计数，不是产物提供的 resolved。replay 的 structural completion 定义为 `milestone_coverage == full`，quality 为 stage score 聚合，policy stop 取 `termination.should_stop`。

| 方法 | N | score mean/median/std/P90/P95 | 精确1分 | full/partial/none | quality mean/std | policy stop | minefield | 工具异常case |
|---|---:|---|---:|---:|---|---:|---:|---:|
| default | 6108 | 0.7582/0.9157/0.3242/1.0000/1.0000 | 1382 (22.63%) | 0/0/0 | N/A/N/A | 0 (0.00%) | 704 (11.53%) | 2489 |
| dynsteer_replay | 6108 | 0.6892/0.9194/0.3448/0.9663/1.0000 | 544 (8.91%) | 3692/1575/841 | 0.6892/0.3448 | 1223 (20.02%) | 704 (11.53%) | 2381 |

### 模型区分度

以下表格以 epsilon 为主键横向比较两种方法；`Δ` 均为 `dynsteer_replay − DEFAULT`。正的 `ΔDS` 只表示模型均值被拉得更开，不代表整体评估正确性更高。

| epsilon | DEFAULT mean/std | replay mean/std | Δmean/Δstd | DEFAULT 显著模型对 | replay 显著模型对 | Δ对数/Δ比例 | DEFAULT DS | replay DS | ΔDS |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 0.7582/0.0224 | 0.6892/0.0345 | -0.0690/+0.0120 | 5/6 (83.33%) | 6/6 (100.00%) | +1/+16.67pp | 0.0270 | 0.0500 | +0.0230 |
| 0.02 | 0.7582/0.0224 | 0.6892/0.0345 | -0.0690/+0.0120 | 4/6 (66.67%) | 5/6 (83.33%) | +1/+16.67pp | 0.0242 | 0.0456 | +0.0215 |
| 0.03 | 0.7582/0.0224 | 0.6892/0.0345 | -0.0690/+0.0120 | 4/6 (66.67%) | 4/6 (66.67%) | 0/+0.00pp | 0.0242 | 0.0408 | +0.0167 |
| 0.04 | 0.7582/0.0224 | 0.6892/0.0345 | -0.0690/+0.0120 | 2/6 (33.33%) | 4/6 (66.67%) | +2/+33.33pp | 0.0171 | 0.0408 | +0.0238 |
| 0.05 | 0.7582/0.0224 | 0.6892/0.0345 | -0.0690/+0.0120 | 2/6 (33.33%) | 3/6 (50.00%) | +1/+16.67pp | 0.0171 | 0.0354 | +0.0183 |

同 epsilon 比较可见，replay 在五个阈值上的 DS 均高于 DEFAULT，`ΔDS` 范围为 +0.0167～+0.0238；显著模型对在 epsilon=0.03 时持平，其余阈值增加 1～2 对。与此同时 replay mean score 低 0.0690、population stddev 高约 0.0120，说明区分度提升伴随整体降分和分布拉宽，不能仅凭 DS 判定 replay 整体更优。

按 case 的四模型均值分散度如下；它用于检查 DS 是否只由少数 case 拉高。

| method | case数 | dispersion mean | P90 | 零分散case |
|---|---:|---:|---:|---:|
| default | 509 | 0.1123 | 0.2764 | 49 |
| dynsteer_replay | 509 | 0.1300 | 0.2887 | 74 |

分数频数用于识别模板化/压缩：

| method | 精确unique | 四舍五入3位unique | 高频值（值:次数） |
|---|---:|---:|---|
| default | 1358 | 457 | 1.000:1382; 0.000:745; 0.500:356; 0.667:193; 0.950:124; 0.925:78; 0.868:72; 0.937:61; 0.924:57; 0.982:55 |
| dynsteer_replay | 669 | 266 | 0.000:841; 1.000:544; 0.936:407; 0.957:379; 0.932:250; 0.449:213; 0.959:199; 0.463:147; 0.448:142; 0.955:141 |

结论：DynSTEER 在所有 epsilon 下的 DS 更高，且 case-level dispersion 也更高，因此区分度并非完全由单个异常 case 造成；但 replay 均值整体下移、结构/质量冲突较多，DS 提升不能解释为准确性提升。

### 模型与重复稳定性

| method | model | score mean | repeat population stddev | full rate | stop rate |
|---|---|---:|---:|---:|---:|
| default | deepseek-v4-flash | 0.7880 | 0.0043 | N/A | 0.00% |
| default | deepseek-v4-pro | 0.7718 | 0.0014 | N/A | 0.00% |
| default | qwen-plus-2025-12-01 | 0.7362 | 0.0109 | N/A | 0.00% |
| default | qwen3-max-2026-01-23 | 0.7368 | 0.0100 | N/A | 0.00% |
| dynsteer_replay | deepseek-v4-flash | 0.7338 | 0.0026 | 67.52% | 16.63% |
| dynsteer_replay | deepseek-v4-pro | 0.7111 | 0.0021 | 65.88% | 17.68% |
| dynsteer_replay | qwen-plus-2025-12-01 | 0.6615 | 0.0096 | 53.05% | 18.53% |
| dynsteer_replay | qwen3-max-2026-01-23 | 0.6503 | 0.0094 | 55.34% | 27.24% |

跨方法 score rank_tau=0.6667，仅表示两套评分体系的模型排序关联。按 repeat 的跨方法 tau 为 r0=0.5497、r1=0.5651、r2=0.5524。
同方法跨 repeat 的 `repeat_rank_consistency` 字段缺失，不能按强制口径报告 tau-b/exact/top1/displacement；当前只有 repeat score 均值与总体标准差可用。

## 4. 提前终止与执行进度

replay 中共有 519 条由 `evaluation_policy_stop`、`milestone_no_progress:*` 或 `ready_frontier_no_progress:*` 触发的执行不力 virtual stop。另有 minefield stop，已分开统计。这里的 stop_step/default_total_step 使用 replay 元数据中的 `replay_step_count/source_step_count`，两者均为 raw step 数。

| model | stop code | N | progress mean/median/P10/P90 | DEFAULT score mean | replay score mean | pipeline delta mean(s) |
|---|---|---:|---|---:|---:|---:|
| deepseek-v4-flash | evaluation_policy_stop | 32 | 66.26%/66.67%/48.07%/86.96% | 0.9536 | 0.5275 | 7.6596 |
| deepseek-v4-flash | milestone_no_progress:m0 | 3 | 61.06%/42.37%/41.13%/88.47% | 0.4875 | 0.1197 | -83.9478 |
| deepseek-v4-flash | milestone_no_progress:m2 | 36 | 72.88%/76.23%/49.98%/88.89% | 0.7974 | 0.5764 | -90.9231 |
| deepseek-v4-flash | milestone_no_progress:m3 | 2 | 57.68%/57.68%/41.94%/73.42% | 0.8367 | 0.5472 | -1081.0486 |
| deepseek-v4-pro | evaluation_policy_stop | 32 | 65.99%/66.67%/57.14%/82.12% | 0.9664 | 0.5273 | 5.1521 |
| deepseek-v4-pro | milestone_no_progress:m0 | 14 | 60.22%/66.52%/27.13%/85.71% | 0.6281 | 0.0642 | -80.4528 |
| deepseek-v4-pro | milestone_no_progress:m1 | 9 | 79.71%/80.00%/61.87%/100.00% | 0.5980 | 0.4482 | -11.2620 |
| deepseek-v4-pro | milestone_no_progress:m2 | 17 | 71.51%/78.79%/41.40%/93.33% | 0.8393 | 0.6056 | -26.2340 |
| deepseek-v4-pro | milestone_no_progress:m3 | 2 | 93.59%/93.59%/88.46%/98.72% | 0.7256 | 0.5359 | -63.8483 |
| qwen-plus-2025-12-01 | evaluation_policy_stop | 56 | 59.92%/57.14%/57.14%/66.67% | 0.9649 | 0.5971 | -1.4446 |
| qwen-plus-2025-12-01 | milestone_no_progress:m0 | 2 | 86.00%/86.00%/74.80%/97.20% | 0.4293 | 0.0000 | -1.6106 |
| qwen-plus-2025-12-01 | milestone_no_progress:m1 | 8 | 74.06%/66.92%/54.71%/100.00% | 0.4783 | 0.2185 | -69.5752 |
| qwen-plus-2025-12-01 | milestone_no_progress:m2 | 39 | 71.61%/72.73%/45.55%/93.14% | 0.8099 | 0.6056 | -17.1635 |
| qwen-plus-2025-12-01 | milestone_no_progress:m3 | 6 | 69.50%/80.09%/36.94%/91.46% | 0.7366 | 0.5468 | -561.6965 |
| qwen-plus-2025-12-01 | ready_frontier_no_progress:m0 | 5 | 60.07%/62.07%/50.92%/68.03% | 0.5329 | 0.0000 | -34.7558 |
| qwen3-max-2026-01-23 | evaluation_policy_stop | 114 | 65.19%/66.67%/57.14%/76.92% | 0.9900 | 0.6672 | 4.9270 |
| qwen3-max-2026-01-23 | milestone_no_progress:m0 | 50 | 64.08%/62.07%/44.19%/100.00% | 0.1841 | 0.0748 | -43.6794 |
| qwen3-max-2026-01-23 | milestone_no_progress:m1 | 47 | 51.05%/48.78%/29.50%/75.86% | 0.5534 | 0.3634 | -56.1404 |
| qwen3-max-2026-01-23 | milestone_no_progress:m2 | 39 | 70.26%/70.97%/55.38%/84.07% | 0.7094 | 0.5933 | -13.3826 |
| qwen3-max-2026-01-23 | milestone_no_progress:m3 | 2 | 91.46%/91.46%/84.63%/98.29% | 0.6984 | 0.4272 | -59.9752 |
| qwen3-max-2026-01-23 | ready_frontier_no_progress:m0 | 2 | 55.36%/55.36%/49.99%/60.73% | 0.2000 | 0.0000 | -75.2406 |
| qwen3-max-2026-01-23 | ready_frontier_no_progress:m1 | 2 | 87.04%/87.04%/76.67%/97.41% | 0.4057 | 0.2501 | -40.8926 |

合理性判断：停止不能一概视为正确。低进度且 DEFAULT 后缀仍包含关键工具调用的样本有误停风险；高进度或 pipeline delta≥0 的样本停止偏晚、效率收益有限；full 但 policy stop 的样本则暴露“结构完成”和“质量失败”冲突。代表样例见 bad_case 报告。

## 5. 效率与成本

时间口径：DEFAULT pipeline=`elapsed_seconds`；replay pipeline=`effective_elapsed_seconds = replay evaluator elapsed + paired DEFAULT prefix execution`。这仍不包含不可见的 case adaptation 成本。

### 5.1 因阶段得分/进展不足而中途终止的效率

这里同时报告严格和广义两种口径，避免把直接低分停止与持续无进展停止混为一类：

- **严格低分停止**：仅包含 `evaluation_policy_stop`，即阶段得分低于失败阈值而直接停止。
- **广义执行不力停止**：在严格低分停止基础上，加入 `milestone_no_progress:*` 和 `ready_frontier_no_progress:*`；不包含 minefield、正常完成或系统异常。

计数同时区分主键级配对样本与去重后的场景数。一个 `case_id` 会因 4 个 model、3 个 repeat 产生多条配对样本。进度与节省量定义为：

```text
progress = stop_step / default_total_step
saved_progress = 1 - progress
gross_execution_time_saved = default_full_execution_time - replay_prefix_execution_time
net_pipeline_time_saved = default_pipeline_time - replay_effective_pipeline_time
```

时间节省为正表示 replay 更快，为负表示 replay 反而更慢；`net_pipeline_time_saved` 已计入 replay Judge/评估时间，但仍未包含当前产物不可见的 adaptation 成本。

| 提前终止口径 | 配对样本数 | 去重 case_id 数 | 平均停止进度 | 平均节省进度 | 平均轨迹执行毛节省 | 平均全流程净节省 | 全流程净节省中位数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 严格低分停止：`evaluation_policy_stop` | 234 | 64 | 64.19% | 35.81% | 3.66 秒/条 | -3.81 秒/条 | -5.59 秒/条 |
| 广义执行不力停止：低分 + 无进展 | 519 | 173 | 65.49% | 34.51% | 16.94 秒/条 | 32.89 秒/条 | -1.43 秒/条 |

| 提前终止口径 | saved progress mean/median/P10/P90 | 轨迹执行毛节省 total/mean/median/P90/P95(s) | 全流程净节省 total/mean/median/P90/P95(s) | 净节省为正/零/负 |
|---|---|---|---|---|
| 严格低分停止 | 35.81%/33.33%/23.08%/42.86% | 856.29/3.66/2.24/3.33/7.99 | -890.75/-3.81/-5.59/-3.90/-0.21 | 12/0/222（5.13%/0%/94.87%） |
| 广义执行不力停止 | 34.51%/33.33%/13.00%/54.79% | 8,792.80/16.94/3.54/45.50/70.61 | 17,067.65/32.89/-1.43/63.67/99.28 | 252/0/267（48.55%/0%/51.45%） |

严格低分停止虽然平均少执行了 35.81% 的轨迹，但只节省 3.66 秒/条的轨迹执行时间；计入 Judge 后平均反而慢 3.81 秒/条，总计慢 890.75 秒，234 条中有 222 条（94.87%）全流程变慢。这说明当前 `evaluation_policy_stop` 对短轨迹的评估开销通常超过截断收益。

广义 519 条执行不力停止平均少执行 34.51% 的轨迹，轨迹执行时间毛节省总计 8,792.80 秒、平均 16.94 秒/条；当前可比口径下全流程净节省总计 17,067.65 秒、平均 32.89 秒/条。但净节省中位数为 -1.43 秒，且 267 条（51.45%）仍然更慢，说明平均收益由少数超长的 no-progress 轨迹贡献，典型样本并未节省时间。由于这些停止来自 `DYNSTEER_REPLAY` 的反事实截断，且 adaptation 成本缺失，不能直接视为线上已实现收益。

| 范围 | pairs | DEFAULT pipeline总时长(s) | replay prefix(s) | replay evaluation(s) | replay pipeline(s) | 总差值(s) | 差值mean/median/P90/P95(s) | progress mean/median/P10/P90 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| 全量 | 6108 | 176134.0569 | 119097.0620 | 45348.4432 | 164445.5052 | -11688.5517 | -1.9136/6.2320/13.5251/17.3563 | 65.49%/66.67%/45.21%/87.00% |
| 执行不力stop子集 | 519 | 32298.3067 | 11117.9680 | 4112.6867 | 15230.6547 | -17067.6520 | -32.8856/1.4340/8.8104/11.6999 | 65.49%/66.67%/45.21%/87.00% |

评估 Judge token 总量：全量 31,104,487，执行不力 stop 子集 2,746,020。Agent token、适配 token、pipeline token 的可用覆盖率均为 0%，因此 adaptation/evaluation/combined overhead token share 全部记为不可计算。

全量总时长减少约 6.64%，但 3,938/6,108（64.47%）配对的 replay pipeline 不低于 DEFAULT，且差值中位数为 +6.23 秒；执行不力 stop 子集的总差值虽为 -17,067.65 秒，中位数仍为 +1.43 秒。节省集中在少数超长轨迹，不能用总量均值代表典型 case。

## 6. DynSTEER 做得不好的集中信号

| 信号 | 数量 | 比例 | 影响 |
|---|---:|---:|---|
| 高 native 分但 replay 非 full | 116 | 1.90% | 可能漏判、停得过早或结构图/strict scorer 口径冲突 |
| 低 native 分但 replay full 且高分 | 0 | 0.00% | 可能误报，也可能 native scorer 过严；需回到状态与黄金标签 |
| full 但存在 fail stage | 156 | 2.55% | 结构完成与阶段质量结论冲突，终态标签不够可解释 |
| 高 native 分且执行不力 virtual stop | 205 | 3.36% | stop policy 可能截断本可完成的后缀 |
| 执行不力 stop 发生在进度≤30% | 19 | 0.31% | 早停风险高，需检查后缀是否包含关键工具 |
| 执行不力 stop 发生在进度≥80% | 98 | 1.60% | 停止偏晚，节省有限 |
| replay 全流程时间不低于 DEFAULT | 3938 | 64.47% | 即使复用/截断轨迹，Judge 开销仍抵消执行节省 |
| Judge 调用失败 | 0 | 0.00% | 评估证据完整性下降 |

这些信号不是自动真值标签：DEFAULT native continuous score 与 DynSTEER stage/coverage 口径不同。bad_case 报告通过轨迹工具调用、stage evidence、stop point 和后缀工具进一步复核代表样例。

## 7. 逐 case 对照表

每行汇总 4 model × 3 repeat=12 个配对样本。`D=1` 仅指 native score 精确等于 1，不称为 resolved。成本为包含 prefix+评估的可比时间口径；适配成本与 Agent token 不可用。

| case_id | D score均值[范围]/D=1 | R score均值[范围] | Δ | full/partial/none | virtual/changed/recovery | underperf stop/progress | minefield | pipeline Δ(s) | 判断 |
|---|---|---|---:|---:|---:|---|---:|---:|---|
| find_days_till_holiday_insufficient_information | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -223.3102 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_3_distraction_tools | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -244.0947 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -87.9649 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.5000 [0.0000, 1.0000] / 6 | 0.5000 [0.0000, 1.0000] | 0.0000 | 6/0/6 | 6/6/0 | 0/N/A | 6 | -135.5698 | 6/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -120.2089 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -47.4105 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -84.6226 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt_3_distraction_tools | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -145.0842 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt_3_distraction_tools_arg_description_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -102.3180 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt_3_distraction_tools_arg_type_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -224.1307 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt_3_distraction_tools_tool_description_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -331.5749 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_days_till_holiday_insufficient_information_alt_3_distraction_tools_tool_name_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -457.3539 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -191.6024 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_10_distraction_tools | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -5.7887 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -5.1165 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools_arg_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -3.2677 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -5.3320 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -4.9225 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -8.8324 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt | 0.7500 [0.0000, 1.0000] / 9 | 0.7500 [0.0000, 1.0000] | 0.0000 | 9/0/3 | 3/3/0 | 0/N/A | 3 | -146.1186 | 3/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_10_distraction_tools | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -40.7668 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_3_distraction_tools | 0.7500 [0.0000, 1.0000] / 9 | 0.7500 [0.0000, 1.0000] | 0.0000 | 9/0/3 | 3/3/0 | 0/N/A | 3 | -197.1682 | 3/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_3_distraction_tools_arg_description_scrambled | 0.6667 [0.0000, 1.0000] / 8 | 0.6667 [0.0000, 1.0000] | 0.0000 | 8/0/4 | 4/4/0 | 0/N/A | 4 | -86.3933 | 4/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_3_distraction_tools_arg_type_scrambled | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -9.9658 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_3_distraction_tools_tool_description_scrambled | 0.7500 [0.0000, 1.0000] / 9 | 0.7500 [0.0000, 1.0000] | 0.0000 | 9/0/3 | 3/3/0 | 0/N/A | 3 | -48.3212 | 3/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_contact_with_message_recency_insufficient_information_alt_3_distraction_tools_tool_name_scrambled | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -54.6244 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -40.1782 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information_3_distraction_tools | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -41.9025 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -46.5782 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -29.3239 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -27.6446 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| modify_reminder_with_recency_latest_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -23.7351 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -29.5210 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_3_distraction_tools | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -34.6617 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -33.6790 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_3_distraction_tools_arg_type_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.7524 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.7500 [0.0000, 1.0000] / 9 | 0.7500 [0.0000, 1.0000] | 0.0000 | 9/0/3 | 3/3/0 | 0/N/A | 3 | -48.9010 | 3/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -138.4897 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -21.3263 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt_3_distraction_tools | 0.5833 [0.0000, 1.0000] / 7 | 0.5833 [0.0000, 1.0000] | 0.0000 | 7/0/5 | 5/5/0 | 0/N/A | 5 | -111.3751 | 5/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt_3_distraction_tools_arg_description_scrambled | 0.5833 [0.0000, 1.0000] / 7 | 0.5833 [0.0000, 1.0000] | 0.0000 | 7/0/5 | 5/5/0 | 0/N/A | 5 | -71.0816 | 5/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt_3_distraction_tools_arg_type_scrambled | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -46.8982 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt_3_distraction_tools_tool_description_scrambled | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -208.7448 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_contact_by_phone_no_search_contacts_insufficient_information_alt_3_distraction_tools_tool_name_scrambled | 0.6667 [0.0000, 1.0000] / 8 | 0.6667 [0.0000, 1.0000] | 0.0000 | 8/0/4 | 4/4/0 | 0/N/A | 4 | -111.1432 | 4/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -26.5881 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information_3_distraction_tools | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -26.5604 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -23.7190 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -22.6032 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.5833 [0.0000, 1.0000] / 7 | 0.5833 [0.0000, 1.0000] | 0.0000 | 7/0/5 | 5/5/0 | 0/N/A | 5 | -17.4092 | 5/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| remove_reminder_with_recency_latest_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -27.4206 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -1104.8394 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_10_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -367.7084 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_3_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -457.4155 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -398.8005 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.4167 [0.0000, 1.0000] / 5 | 0.4167 [0.0000, 1.0000] | 0.0000 | 5/0/7 | 7/7/0 | 0/N/A | 7 | -403.5331 | 7/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -544.9378 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -412.6316 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -1907.1584 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_10_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -933.6463 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_3_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -470.6236 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_3_distraction_tools_arg_description_scrambled | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -434.5889 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_3_distraction_tools_arg_type_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -304.8985 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_3_distraction_tools_tool_description_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -302.1788 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_creation_recency_yesterday_insufficient_information_implicit_3_distraction_tools_tool_name_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -236.9462 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -488.8640 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_10_distraction_tools | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -132.6939 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_3_distraction_tools | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -703.4145 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -310.2355 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -318.9506 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -337.9489 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -348.3475 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -276.1512 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_10_distraction_tools | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -405.1896 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_3_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -349.3592 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_3_distraction_tools_arg_description_scrambled | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -227.6471 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_3_distraction_tools_arg_type_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -145.6360 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_3_distraction_tools_tool_description_scrambled | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -520.3518 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_upcoming_insufficient_information_implicit_3_distraction_tools_tool_name_scrambled | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -276.2246 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -464.4630 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_10_distraction_tools | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -375.4394 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_3_distraction_tools | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -273.6208 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -270.3222 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.3333 [0.0000, 1.0000] / 4 | 0.3333 [0.0000, 1.0000] | 0.0000 | 4/0/8 | 8/8/0 | 0/N/A | 8 | -243.1889 | 8/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -359.3976 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -376.2816 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -347.1490 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_10_distraction_tools | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -385.4084 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_3_distraction_tools | 0.0000 [0.0000, 0.0000] / 0 | 0.0000 [0.0000, 0.0000] | 0.0000 | 0/0/12 | 12/12/0 | 0/N/A | 12 | -270.1111 | 12/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_3_distraction_tools_arg_description_scrambled | 0.0833 [0.0000, 1.0000] / 1 | 0.0833 [0.0000, 1.0000] | 0.0000 | 1/0/11 | 11/11/0 | 0/N/A | 11 | -925.5214 | 11/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_3_distraction_tools_arg_type_scrambled | 0.5000 [0.0000, 1.0000] / 6 | 0.5000 [0.0000, 1.0000] | 0.0000 | 6/0/6 | 6/6/0 | 0/N/A | 6 | -187.9120 | 6/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_3_distraction_tools_tool_description_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1667 [0.0000, 1.0000] | 0.0000 | 2/0/10 | 10/10/0 | 0/N/A | 10 | -313.5277 | 10/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| search_reminder_with_recency_yesterday_insufficient_information_implicit_3_distraction_tools_tool_name_scrambled | 0.2500 [0.0000, 1.0000] / 3 | 0.2500 [0.0000, 1.0000] | 0.0000 | 3/0/9 | 9/9/0 | 0/N/A | 9 | -250.0791 | 9/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -6.2001 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_3_distraction_tools | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -7.9921 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -13.2699 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_3_distraction_tools_arg_type_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.4383 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -5.8584 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_3_distraction_tools_tool_name_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -3.9309 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt | 0.8333 [0.0000, 1.0000] / 10 | 0.8333 [0.0000, 1.0000] | 0.0000 | 10/0/2 | 2/2/0 | 0/N/A | 2 | -16.4347 | 2/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt_3_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.6929 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt_3_distraction_tools_arg_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.7595 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt_3_distraction_tools_arg_type_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 1.0000 [1.0000, 1.0000] | 0.0000 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -3.7787 | DEFAULT/replay 均值为 1.0000/1.0000，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt_3_distraction_tools_tool_description_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -11.4651 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| send_message_with_contact_content_cellular_off_insufficient_information_alt_3_distraction_tools_tool_name_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.9167 [0.0000, 1.0000] | 0.0000 | 11/0/1 | 1/1/0 | 0/N/A | 1 | -10.2822 | 1/12 条命中 minefield；主要差异来自安全/约束违反，不宜仅看总分。 |
| find_thanksgiving_timestamp | 1.0000 [1.0000, 1.0000] / 12 | 0.9484 [0.9484, 0.9484] | -0.0516 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.0473 | DEFAULT/replay 均值为 1.0000/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_thanksgiving_timestamp_3_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.9484 [0.9484, 0.9484] | -0.0516 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -1.9996 | DEFAULT/replay 均值为 1.0000/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_thanksgiving_timestamp_3_distraction_tools_arg_description_scrambled | 0.1667 [0.0000, 1.0000] / 2 | 0.1581 [0.0000, 0.9484] | -0.0086 | 2/0/10 | 0/0/0 | 0/N/A | 0 | -2.3107 | DEFAULT/replay 均值为 0.1667/0.1581，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_thanksgiving_timestamp_3_distraction_tools_arg_type_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.9484 [0.9484, 0.9484] | -0.0516 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -2.0268 | DEFAULT/replay 均值为 1.0000/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_thanksgiving_timestamp_3_distraction_tools_tool_description_scrambled | 0.9167 [0.0000, 1.0000] / 11 | 0.8694 [0.0000, 0.9484] | -0.0473 | 11/0/1 | 0/0/0 | 0/N/A | 0 | -2.0194 | DEFAULT/replay 均值为 0.9167/0.8694，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_thanksgiving_timestamp_3_distraction_tools_tool_name_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.9484 [0.9484, 0.9484] | -0.0516 | 12/0/0 | 0/0/0 | 0/N/A | 0 | -1.9042 | DEFAULT/replay 均值为 1.0000/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information | 0.6277 [0.5905, 0.7469] / 0 | 0.8754 [0.0000, 0.9550] | 0.2477 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 34.0368 | DEFAULT/replay 均值为 0.6277/0.8754，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_3_distraction_tools | 0.5792 [0.5458, 0.6300] / 0 | 0.9475 [0.9370, 0.9550] | 0.3683 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 50.4687 | DEFAULT/replay 均值为 0.5792/0.9475，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_3_distraction_tools_arg_description_scrambled | 0.5683 [0.4949, 0.6300] / 0 | 0.8574 [0.0000, 0.9550] | 0.2891 | 11/0/1 | 1/0/0 | 1/100.00% | 0 | 61.3427 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_3_distraction_tools_arg_type_scrambled | 0.5681 [0.5207, 0.6057] / 0 | 0.9475 [0.9370, 0.9550] | 0.3794 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 45.6585 | DEFAULT/replay 均值为 0.5681/0.9475，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_3_distraction_tools_tool_description_scrambled | 0.5674 [0.4793, 0.6437] / 0 | 0.9398 [0.9160, 0.9550] | 0.3724 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 65.8839 | DEFAULT/replay 均值为 0.5674/0.9398，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_3_distraction_tools_tool_name_scrambled | 0.5599 [0.4962, 0.6156] / 0 | 0.9442 [0.9160, 0.9550] | 0.3844 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 43.7173 | DEFAULT/replay 均值为 0.5599/0.9442，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt | 0.6146 [0.5800, 0.6408] / 0 | 0.9550 [0.9550, 0.9550] | 0.3404 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 31.4497 | DEFAULT/replay 均值为 0.6146/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt_3_distraction_tools | 0.5963 [0.5694, 0.6248] / 0 | 0.9550 [0.9550, 0.9550] | 0.3587 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 31.8846 | DEFAULT/replay 均值为 0.5963/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt_3_distraction_tools_arg_description_scrambled | 0.6018 [0.5570, 0.6966] / 0 | 0.9550 [0.9550, 0.9550] | 0.3532 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 27.7908 | DEFAULT/replay 均值为 0.6018/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt_3_distraction_tools_arg_type_scrambled | 0.5869 [0.5313, 0.6144] / 0 | 0.9550 [0.9550, 0.9550] | 0.3681 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 27.5119 | DEFAULT/replay 均值为 0.5869/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt_3_distraction_tools_tool_description_scrambled | 0.6081 [0.5419, 0.6586] / 0 | 0.9550 [0.9550, 0.9550] | 0.3469 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 27.8922 | DEFAULT/replay 均值为 0.6081/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_by_phone_no_remove_contact_insufficient_information_alt_3_distraction_tools_tool_name_scrambled | 0.5944 [0.5503, 0.6560] / 0 | 0.9550 [0.9550, 0.9550] | 0.3606 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 28.3872 | DEFAULT/replay 均值为 0.5944/0.9550，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number | 0.8963 [0.8467, 0.9496] / 0 | 0.9482 [0.9119, 0.9663] | 0.0518 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 110.0517 | DEFAULT/replay 均值为 0.8963/0.9482，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number_3_distraction_tools | 0.8761 [0.8250, 0.9101] / 0 | 0.9487 [0.9119, 0.9663] | 0.0726 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 109.7871 | DEFAULT/replay 均值为 0.8761/0.9487，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number_3_distraction_tools_arg_description_scrambled | 0.8893 [0.8467, 0.9496] / 0 | 0.9487 [0.9119, 0.9663] | 0.0595 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 103.6342 | DEFAULT/replay 均值为 0.8893/0.9487，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number_3_distraction_tools_arg_type_scrambled | 0.8665 [0.8099, 0.9368] / 0 | 0.9552 [0.9119, 0.9663] | 0.0887 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 106.3172 | DEFAULT/replay 均值为 0.8665/0.9552，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number_3_distraction_tools_tool_description_scrambled | 0.8903 [0.8363, 0.9496] / 0 | 0.9436 [0.9119, 0.9663] | 0.0533 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 106.6196 | DEFAULT/replay 均值为 0.8903/0.9436，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_contact_with_name_and_phone_number_3_distraction_tools_tool_name_scrambled | 0.8711 [0.7833, 0.9011] / 0 | 0.9507 [0.9119, 0.9663] | 0.0796 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 116.5941 | DEFAULT/replay 均值为 0.8711/0.9507，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_date_and_time | 1.0000 [1.0000, 1.0000] / 12 | 0.8409 [0.7230, 0.9588] | -0.1591 | 12/0/0 | 6/6/0 | 6/57.14% | 0 | 72.9197 | 6/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_10_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.8212 [0.7230, 0.9588] | -0.1788 | 12/0/0 | 7/7/0 | 7/57.14% | 0 | 83.7758 | 7/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_3_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.8409 [0.7230, 0.9588] | -0.1591 | 12/0/0 | 6/6/0 | 6/57.14% | 0 | 75.4085 | 6/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8409 [0.7230, 0.9588] | -0.1591 | 12/0/0 | 6/6/0 | 6/57.14% | 0 | 72.7998 | 6/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_arg_type_scrambled | 0.7500 [0.0000, 1.0000] / 9 | 0.6601 [0.0000, 0.9588] | -0.0899 | 9/0/3 | 5/5/0 | 5/60.30% | 0 | -24.6038 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_tool_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8409 [0.7230, 0.9588] | -0.1591 | 12/0/0 | 6/6/0 | 6/57.14% | 0 | 79.4302 | 6/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_3_distraction_tools_tool_name_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8212 [0.7230, 0.9588] | -0.1788 | 12/0/0 | 7/7/0 | 7/57.14% | 0 | 75.5424 | 7/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt | 1.0000 [1.0000, 1.0000] / 12 | 0.9391 [0.7230, 0.9588] | -0.0609 | 12/0/0 | 1/1/0 | 1/57.14% | 0 | 84.6396 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_10_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.8802 [0.7230, 0.9588] | -0.1198 | 12/0/0 | 4/4/0 | 4/57.14% | 0 | 77.4139 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_3_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.8802 [0.7230, 0.9588] | -0.1198 | 12/0/0 | 4/4/0 | 4/57.14% | 0 | 89.7463 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_3_distraction_tools_arg_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8605 [0.7230, 0.9588] | -0.1395 | 12/0/0 | 5/5/0 | 5/57.14% | 0 | 74.5367 | 5/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_3_distraction_tools_arg_type_scrambled | 0.7500 [0.0000, 1.0000] / 9 | 0.6994 [0.0000, 0.9588] | -0.0506 | 9/0/3 | 4/3/0 | 4/68.81% | 0 | -34.6135 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_3_distraction_tools_tool_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8998 [0.7230, 0.9588] | -0.1002 | 12/0/0 | 3/3/0 | 3/57.14% | 0 | 80.3323 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_alt_3_distraction_tools_tool_name_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8409 [0.7230, 0.9588] | -0.1591 | 12/0/0 | 6/6/0 | 6/57.14% | 0 | 75.8325 | 6/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn | 1.0000 [1.0000, 1.0000] / 12 | 0.9212 [0.7406, 0.9573] | -0.0788 | 12/0/0 | 2/2/0 | 2/66.67% | 0 | 83.1704 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_10_distraction_tools | 0.8750 [0.5000, 1.0000] / 9 | 0.7761 [0.4495, 0.9573] | -0.0989 | 9/3/0 | 3/3/0 | 3/66.67% | 0 | 58.0108 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_3_distraction_tools | 1.0000 [1.0000, 1.0000] / 12 | 0.8850 [0.7406, 0.9573] | -0.1150 | 12/0/0 | 4/4/0 | 4/66.67% | 0 | 81.4159 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.9031 [0.7406, 0.9573] | -0.0969 | 12/0/0 | 3/3/0 | 3/66.67% | 0 | 86.9900 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.7500 [0.0000, 1.0000] / 9 | 0.7180 [0.0000, 0.9573] | -0.0320 | 9/0/3 | 2/2/0 | 2/75.13% | 0 | 4.0452 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8850 [0.7406, 0.9573] | -0.1150 | 12/0/0 | 4/4/0 | 4/66.67% | 0 | 79.9417 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 1.0000 [1.0000, 1.0000] / 12 | 0.8850 [0.7406, 0.9573] | -0.1150 | 12/0/0 | 4/4/0 | 4/66.67% | 0 | 81.8408 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt | 1.0000 [1.0000, 1.0000] / 12 | 0.9573 [0.9573, 0.9573] | -0.0427 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 91.5098 | DEFAULT/replay 均值为 1.0000/0.9573，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_10_distraction_tools | 0.9583 [0.5000, 1.0000] / 11 | 0.8843 [0.4495, 0.9573] | -0.0740 | 11/1/0 | 1/1/0 | 1/66.67% | 0 | 84.4779 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_3_distraction_tools | 0.8333 [0.5000, 1.0000] / 8 | 0.7699 [0.4495, 0.9573] | -0.0634 | 8/4/0 | 1/1/0 | 1/66.67% | 0 | 55.1914 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.8333 [0.5000, 1.0000] / 8 | 0.7880 [0.4495, 0.9573] | -0.0453 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 60.2259 | DEFAULT/replay 均值为 0.8333/0.7880，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.6667 [0.0000, 1.0000] / 7 | 0.6333 [0.0000, 0.9573] | -0.0333 | 7/2/3 | 2/2/0 | 2/68.16% | 0 | -16.4248 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.8333 [0.5000, 1.0000] / 8 | 0.7699 [0.4495, 0.9573] | -0.0634 | 8/4/0 | 1/1/0 | 1/66.67% | 0 | 55.7031 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_date_and_time_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.8333 [0.5000, 1.0000] / 8 | 0.7754 [0.4495, 0.9573] | -0.0579 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 62.0922 | DEFAULT/replay 均值为 0.8333/0.7754，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time | 1.0000 [1.0000, 1.0000] / 12 | 0.7722 [0.4484, 0.9588] | -0.2278 | 9/3/0 | 3/3/0 | 3/72.73% | 0 | 55.7810 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_3_distraction_tools | 0.9167 [0.5000, 1.0000] / 10 | 0.7722 [0.4484, 0.9588] | -0.1444 | 9/3/0 | 3/3/0 | 3/75.52% | 0 | 55.8708 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_3_distraction_tools_arg_description_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7722 [0.4484, 0.9588] | -0.1028 | 9/3/0 | 3/3/0 | 3/74.13% | 0 | 65.9372 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_3_distraction_tools_arg_type_scrambled | 0.7917 [0.5000, 1.0000] / 7 | 0.7036 [0.4484, 0.9588] | -0.0881 | 6/6/0 | 3/3/0 | 3/40.41% | 0 | -154.0923 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_week_delta_and_time_3_distraction_tools_tool_description_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7722 [0.4484, 0.9588] | -0.1028 | 9/3/0 | 3/3/0 | 3/76.92% | 0 | 58.2504 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_3_distraction_tools_tool_name_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7526 [0.4484, 0.9588] | -0.1224 | 9/3/0 | 4/4/0 | 4/72.73% | 0 | 60.6093 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_alt | 1.0000 [1.0000, 1.0000] / 12 | 0.7722 [0.4484, 0.9588] | -0.2278 | 9/3/0 | 3/3/0 | 3/72.73% | 0 | 62.8454 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_alt_3_distraction_tools | 0.9583 [0.5000, 1.0000] / 11 | 0.7712 [0.4484, 0.9588] | -0.1872 | 9/3/0 | 3/3/0 | 3/76.92% | 0 | 66.1157 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_alt_3_distraction_tools_arg_description_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7526 [0.4484, 0.9588] | -0.1224 | 9/3/0 | 4/4/0 | 4/75.87% | 0 | 60.4823 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_alt_3_distraction_tools_arg_type_scrambled | 0.8333 [0.5000, 1.0000] / 8 | 0.7036 [0.4484, 0.9588] | -0.1297 | 6/6/0 | 3/3/0 | 3/39.93% | 0 | -111.8707 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_week_delta_and_time_alt_3_distraction_tools_tool_description_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7526 [0.4484, 0.9588] | -0.1224 | 9/3/0 | 4/4/0 | 4/75.87% | 0 | 62.0435 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_alt_3_distraction_tools_tool_name_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.7526 [0.4484, 0.9588] | -0.1224 | 9/3/0 | 4/4/0 | 4/74.83% | 0 | 53.0937 | 4/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn | 1.0000 [1.0000, 1.0000] / 12 | 0.8303 [0.4495, 0.9573] | -0.1697 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 67.4136 | DEFAULT/replay 均值为 1.0000/0.8303，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_3_distraction_tools | 0.7917 [0.5000, 1.0000] / 7 | 0.7457 [0.4495, 0.9573] | -0.0460 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 51.0168 | DEFAULT/replay 均值为 0.7917/0.7457，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.8123 [0.4495, 0.9573] | -0.0627 | 9/3/0 | 1/1/0 | 1/80.00% | 0 | 64.6215 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.7500 [0.5000, 1.0000] / 6 | 0.7034 [0.4495, 0.9573] | -0.0466 | 6/6/0 | 3/3/0 | 3/62.68% | 0 | -34.4189 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.7917 [0.5000, 1.0000] / 7 | 0.7264 [0.4495, 0.9573] | -0.0652 | 7/5/0 | 1/1/0 | 1/76.92% | 0 | 51.0243 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.8750 [0.5000, 1.0000] / 9 | 0.8303 [0.4495, 0.9573] | -0.0447 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 64.5858 | DEFAULT/replay 均值为 0.8750/0.8303，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt | 0.7917 [0.5000, 1.0000] / 7 | 0.7433 [0.4495, 0.9573] | -0.0483 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 51.1724 | DEFAULT/replay 均值为 0.7917/0.7433，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt_3_distraction_tools | 0.6250 [0.5000, 1.0000] / 3 | 0.5752 [0.4495, 0.9573] | -0.0498 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 15.8409 | DEFAULT/replay 均值为 0.6250/0.5752，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.7083 [0.5000, 1.0000] / 5 | 0.6575 [0.4495, 0.9573] | -0.0508 | 5/7/0 | 0/0/0 | 0/N/A | 0 | 43.2468 | DEFAULT/replay 均值为 0.7083/0.6575，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.7083 [0.5000, 1.0000] / 5 | 0.6552 [0.4495, 0.9432] | -0.0532 | 5/7/0 | 3/3/0 | 3/52.32% | 0 | -68.0802 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.6667 [0.5000, 1.0000] / 4 | 0.6152 [0.4495, 0.9573] | -0.0515 | 4/8/0 | 1/0/0 | 1/100.00% | 0 | 31.3479 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_week_delta_and_time_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.6667 [0.5000, 1.0000] / 4 | 0.6164 [0.4495, 0.9573] | -0.0503 | 4/8/0 | 0/0/0 | 0/N/A | 0 | 23.3509 | DEFAULT/replay 均值为 0.6667/0.6164，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time | 0.7083 [0.5000, 1.0000] / 5 | 0.6021 [0.4484, 0.9588] | -0.1062 | 5/7/0 | 3/3/0 | 3/72.73% | 0 | 29.6221 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_3_distraction_tools | 0.6667 [0.5000, 1.0000] / 4 | 0.5989 [0.4484, 0.9588] | -0.0678 | 4/8/0 | 1/1/0 | 1/72.73% | 0 | 19.0384 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_3_distraction_tools_arg_description_scrambled | 0.5417 [0.5000, 1.0000] / 1 | 0.4713 [0.4484, 0.7230] | -0.0704 | 1/11/0 | 1/1/0 | 1/72.73% | 0 | -1.2590 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_3_distraction_tools_arg_type_scrambled | 0.5833 [0.5000, 1.0000] / 2 | 0.5335 [0.4484, 0.9588] | -0.0498 | 2/10/0 | 3/3/0 | 3/36.75% | 0 | -188.0111 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_3_distraction_tools_tool_description_scrambled | 0.5000 [0.5000, 0.5000] / 0 | 0.4484 [0.4484, 0.4484] | -0.0516 | 0/12/0 | 0/0/0 | 0/N/A | 0 | -6.3410 | DEFAULT/replay 均值为 0.5000/0.4484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_3_distraction_tools_tool_name_scrambled | 0.6250 [0.5000, 1.0000] / 3 | 0.5760 [0.4484, 0.9588] | -0.0490 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 14.7707 | DEFAULT/replay 均值为 0.6250/0.5760，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_alt | 0.6667 [0.5000, 1.0000] / 4 | 0.5596 [0.4484, 0.9588] | -0.1071 | 4/8/0 | 3/3/0 | 3/72.73% | 0 | 21.4984 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_alt_3_distraction_tools | 0.7500 [0.5000, 1.0000] / 6 | 0.6643 [0.4484, 0.9588] | -0.0857 | 6/6/0 | 2/2/0 | 2/76.36% | 0 | 38.0827 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_alt_3_distraction_tools_arg_description_scrambled | 0.7500 [0.5000, 1.0000] / 6 | 0.6840 [0.4484, 0.9588] | -0.0660 | 6/6/0 | 1/1/0 | 1/72.73% | 0 | 40.8966 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_alt_3_distraction_tools_arg_type_scrambled | 0.7083 [0.5000, 1.0000] / 5 | 0.6611 [0.4484, 0.9588] | -0.0473 | 5/7/0 | 3/3/0 | 3/40.12% | 0 | -183.8877 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_alt_3_distraction_tools_tool_description_scrambled | 0.6667 [0.5000, 1.0000] / 4 | 0.6185 [0.4484, 0.9588] | -0.0481 | 4/8/0 | 0/0/0 | 0/N/A | 0 | 24.3663 | DEFAULT/replay 均值为 0.6667/0.6185，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_alt_3_distraction_tools_tool_name_scrambled | 0.7500 [0.5000, 1.0000] / 6 | 0.6758 [0.4484, 0.9588] | -0.0742 | 6/6/0 | 0/0/0 | 0/N/A | 0 | 42.3143 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn | 0.7500 [0.5000, 1.0000] / 6 | 0.7034 [0.4495, 0.9573] | -0.0466 | 6/6/0 | 0/0/0 | 0/N/A | 0 | 46.9237 | DEFAULT/replay 均值为 0.7500/0.7034，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_3_distraction_tools | 0.5417 [0.5000, 1.0000] / 1 | 0.4918 [0.4495, 0.9573] | -0.0499 | 1/11/0 | 0/0/0 | 0/N/A | 0 | 1.7520 | DEFAULT/replay 均值为 0.5417/0.4918，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.6667 [0.5000, 1.0000] / 4 | 0.6007 [0.4495, 0.9573] | -0.0660 | 4/8/0 | 1/1/0 | 1/76.92% | 0 | 26.8264 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.6250 [0.5000, 1.0000] / 3 | 0.5764 [0.4495, 0.9573] | -0.0486 | 3/9/0 | 3/3/0 | 3/60.65% | 0 | -93.3481 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.6250 [0.5000, 1.0000] / 3 | 0.5752 [0.4495, 0.9573] | -0.0498 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 15.2906 | DEFAULT/replay 均值为 0.6250/0.5752，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.6250 [0.5000, 1.0000] / 3 | 0.5584 [0.4495, 0.9573] | -0.0666 | 3/9/0 | 1/1/0 | 1/76.92% | 0 | 16.1585 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt | 0.5000 [0.5000, 0.5000] / 0 | 0.4495 [0.4495, 0.4495] | -0.0505 | 0/12/0 | 0/0/0 | 0/N/A | 0 | -6.8089 | DEFAULT/replay 均值为 0.5000/0.4495，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt_3_distraction_tools | 0.5833 [0.5000, 1.0000] / 2 | 0.5341 [0.4495, 0.9573] | -0.0492 | 2/10/0 | 2/2/0 | 2/72.26% | 0 | -13.8016 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.5000 [0.5000, 0.5000] / 0 | 0.4495 [0.4495, 0.4495] | -0.0505 | 0/12/0 | 2/2/0 | 2/83.48% | 0 | -15.5234 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.5833 [0.5000, 1.0000] / 2 | 0.5329 [0.4495, 0.9573] | -0.0504 | 2/10/0 | 4/4/0 | 4/55.85% | 0 | -131.7178 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.5745 [0.5000, 0.9472] / 0 | 0.4495 [0.4495, 0.4495] | -0.1251 | 0/12/0 | 1/0/0 | 1/100.00% | 0 | -7.5183 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| add_reminder_content_and_weekday_delta_and_time_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.5789 [0.5000, 1.0000] / 1 | 0.4906 [0.4495, 0.9432] | -0.0883 | 1/11/0 | 2/1/0 | 2/75.64% | 0 | -29.2419 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| cellular_off | 0.9236 [0.8748, 0.9496] / 0 | 0.9440 [0.9063, 0.9633] | 0.0203 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 93.0516 | DEFAULT/replay 均值为 0.9236/0.9440，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| cellular_off_3_distraction_tools | 0.9207 [0.8516, 0.9496] / 0 | 0.9333 [0.9063, 0.9633] | 0.0126 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 109.5415 | DEFAULT/replay 均值为 0.9207/0.9333，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| cellular_off_3_distraction_tools_arg_description_scrambled | 0.9241 [0.8569, 0.9496] / 0 | 0.9365 [0.9063, 0.9633] | 0.0124 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 105.6833 | DEFAULT/replay 均值为 0.9241/0.9365，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| cellular_off_3_distraction_tools_arg_type_scrambled | 0.7277 [0.1710, 0.9496] / 0 | 0.7115 [0.0000, 0.9633] | -0.0162 | 9/0/3 | 1/1/0 | 1/78.26% | 0 | 52.9489 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| cellular_off_3_distraction_tools_tool_description_scrambled | 0.9229 [0.8569, 0.9496] / 0 | 0.9333 [0.9063, 0.9633] | 0.0104 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 108.5489 | DEFAULT/replay 均值为 0.9229/0.9333，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| cellular_off_3_distraction_tools_tool_name_scrambled | 0.9099 [0.8569, 0.9496] / 0 | 0.9456 [0.9062, 0.9635] | 0.0357 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 102.5440 | DEFAULT/replay 均值为 0.9099/0.9456，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular | 0.9506 [0.9253, 0.9642] / 0 | 0.9345 [0.9324, 0.9573] | -0.0161 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 88.6265 | DEFAULT/replay 均值为 0.9506/0.9345，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular_3_distraction_tools | 0.9440 [0.8816, 0.9496] / 0 | 0.9365 [0.9324, 0.9573] | -0.0074 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 90.5733 | DEFAULT/replay 均值为 0.9440/0.9365，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular_3_distraction_tools_arg_description_scrambled | 0.9496 [0.9496, 0.9496] / 0 | 0.9324 [0.9324, 0.9324] | -0.0173 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 92.1571 | DEFAULT/replay 均值为 0.9496/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular_3_distraction_tools_arg_type_scrambled | 0.9429 [0.8684, 0.9496] / 0 | 0.9345 [0.9324, 0.9573] | -0.0084 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 97.3013 | DEFAULT/replay 均值为 0.9429/0.9345，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular_3_distraction_tools_tool_description_scrambled | 0.9301 [0.8516, 0.9496] / 0 | 0.9407 [0.9324, 0.9573] | 0.0106 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 94.5978 | DEFAULT/replay 均值为 0.9301/0.9407，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_cellular_3_distraction_tools_tool_name_scrambled | 0.9429 [0.8684, 0.9496] / 0 | 0.9345 [0.9324, 0.9573] | -0.0084 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 84.4764 | DEFAULT/replay 均值为 0.9429/0.9345，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi | 0.8897 [0.8467, 0.9543] / 0 | 0.9511 [0.9324, 0.9573] | 0.0614 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 108.3190 | DEFAULT/replay 均值为 0.8897/0.9511，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi_3_distraction_tools | 0.8788 [0.7877, 0.9368] / 0 | 0.9511 [0.9324, 0.9573] | 0.0723 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.9037 | DEFAULT/replay 均值为 0.8788/0.9511，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi_3_distraction_tools_arg_description_scrambled | 0.8798 [0.8684, 0.9368] / 0 | 0.9532 [0.9324, 0.9573] | 0.0734 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 111.0481 | DEFAULT/replay 均值为 0.8798/0.9532，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi_3_distraction_tools_arg_type_scrambled | 0.8780 [0.8467, 0.9368] / 0 | 0.9532 [0.9324, 0.9573] | 0.0752 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 108.0585 | DEFAULT/replay 均值为 0.8780/0.9532，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi_3_distraction_tools_tool_description_scrambled | 0.8780 [0.8467, 0.9368] / 0 | 0.9532 [0.9324, 0.9573] | 0.0752 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 107.4617 | DEFAULT/replay 均值为 0.8780/0.9532，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| get_wifi_3_distraction_tools_tool_name_scrambled | 0.8855 [0.8684, 0.9368] / 0 | 0.9511 [0.9324, 0.9573] | 0.0656 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 101.4619 | DEFAULT/replay 均值为 0.8855/0.9511，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id | 0.9280 [0.8654, 0.9670] / 0 | 0.9459 [0.9119, 0.9663] | 0.0179 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.7481 | DEFAULT/replay 均值为 0.9280/0.9459，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id_3_distraction_tools | 0.9334 [0.8969, 0.9729] / 0 | 0.9484 [0.9425, 0.9663] | 0.0150 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.9836 | DEFAULT/replay 均值为 0.9334/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id_3_distraction_tools_arg_description_scrambled | 0.9346 [0.8889, 0.9670] / 0 | 0.9413 [0.9119, 0.9663] | 0.0068 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 109.0404 | DEFAULT/replay 均值为 0.9346/0.9413，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id_3_distraction_tools_arg_type_scrambled | 0.9377 [0.8969, 0.9729] / 0 | 0.9419 [0.9119, 0.9663] | 0.0042 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 100.1179 | DEFAULT/replay 均值为 0.9377/0.9419，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id_3_distraction_tools_tool_description_scrambled | 0.9400 [0.8969, 0.9729] / 0 | 0.9445 [0.9425, 0.9663] | 0.0044 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.5202 | DEFAULT/replay 均值为 0.9400/0.9445，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| remove_contact_with_id_3_distraction_tools_tool_name_scrambled | 0.9366 [0.8781, 0.9729] / 0 | 0.9419 [0.9119, 0.9663] | 0.0054 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.6817 | DEFAULT/replay 均值为 0.9366/0.9419，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest | 0.8657 [0.0000, 1.0000] / 4 | 0.8116 [0.0000, 0.9573] | -0.0541 | 10/1/1 | 0/0/0 | 0/N/A | 0 | 117.8948 | DEFAULT/replay 均值为 0.8657/0.8116，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_3_distraction_tools | 0.9266 [0.5000, 1.0000] / 6 | 0.8769 [0.4081, 0.9573] | -0.0496 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 118.0008 | DEFAULT/replay 均值为 0.9266/0.8769，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_3_distraction_tools_arg_description_scrambled | 0.8693 [0.0000, 1.0000] / 4 | 0.8447 [0.0000, 0.9573] | -0.0246 | 11/0/1 | 1/1/0 | 1/78.26% | 0 | 112.3887 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_latest_3_distraction_tools_arg_type_scrambled | 0.9321 [0.8621, 1.0000] / 3 | 0.9187 [0.8280, 0.9573] | -0.0134 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 135.4170 | DEFAULT/replay 均值为 0.9321/0.9187，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_3_distraction_tools_tool_description_scrambled | 0.7931 [0.0000, 1.0000] / 3 | 0.7730 [0.0000, 0.9573] | -0.0202 | 10/0/2 | 0/0/0 | 0/N/A | 0 | 108.2277 | DEFAULT/replay 均值为 0.7931/0.7730，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_3_distraction_tools_tool_name_scrambled | 0.9706 [0.8945, 1.0000] / 6 | 0.9280 [0.8929, 0.9573] | -0.0426 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 136.4452 | DEFAULT/replay 均值为 0.9706/0.9280，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_alt | 0.8499 [0.0000, 0.9718] / 0 | 0.8358 [0.0000, 0.9573] | -0.0141 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 100.3346 | DEFAULT/replay 均值为 0.8499/0.8358，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_alt_3_distraction_tools | 0.9208 [0.8684, 0.9718] / 0 | 0.8923 [0.7294, 0.9543] | -0.0286 | 12/0/0 | 1/1/0 | 1/66.67% | 0 | 122.7755 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| search_message_with_recency_latest_alt_3_distraction_tools_arg_description_scrambled | 0.9125 [0.8424, 0.9718] / 0 | 0.8004 [0.4081, 0.9543] | -0.1121 | 9/3/0 | 1/1/0 | 1/85.71% | 0 | 108.5633 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_latest_alt_3_distraction_tools_arg_type_scrambled | 0.8339 [0.0000, 0.9718] / 0 | 0.7774 [0.0000, 0.9511] | -0.0565 | 10/1/1 | 1/0/0 | 1/100.00% | 0 | 105.4630 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_latest_alt_3_distraction_tools_tool_description_scrambled | 0.8323 [0.0000, 0.9718] / 0 | 0.8260 [0.0000, 0.9511] | -0.0063 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 132.7410 | DEFAULT/replay 均值为 0.8323/0.8260，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_alt_3_distraction_tools_tool_name_scrambled | 0.8564 [0.0000, 0.9718] / 0 | 0.8402 [0.0000, 0.9406] | -0.0162 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 126.4232 | DEFAULT/replay 均值为 0.8564/0.8402，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn | 0.9011 [0.8688, 0.9313] / 0 | 0.9345 [0.8782, 0.9521] | 0.0335 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 170.2886 | DEFAULT/replay 均值为 0.9011/0.9345，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_3_distraction_tools | 0.8904 [0.8318, 0.9313] / 0 | 0.9287 [0.9055, 0.9521] | 0.0383 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 119.1640 | DEFAULT/replay 均值为 0.8904/0.9287，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.8955 [0.8371, 0.9313] / 0 | 0.9288 [0.8810, 0.9521] | 0.0333 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 109.8964 | DEFAULT/replay 均值为 0.8955/0.9288，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.6689 [0.0000, 0.9180] / 0 | 0.6394 [0.0000, 0.9521] | -0.0294 | 8/1/3 | 3/3/0 | 3/69.00% | 0 | 24.6587 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_latest_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.9005 [0.8609, 0.9510] / 0 | 0.9327 [0.8810, 0.9521] | 0.0322 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 111.2794 | DEFAULT/replay 均值为 0.9005/0.9327，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.8903 [0.8717, 0.9180] / 0 | 0.9417 [0.9194, 0.9521] | 0.0514 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 133.8233 | DEFAULT/replay 均值为 0.8903/0.9417，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_alt | 0.8275 [0.0000, 0.9510] / 0 | 0.8467 [0.0000, 0.9521] | 0.0192 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 110.3351 | DEFAULT/replay 均值为 0.8275/0.8467，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_alt_3_distraction_tools | 0.9052 [0.8755, 0.9313] / 0 | 0.8273 [0.4495, 0.9521] | -0.0779 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 104.4869 | DEFAULT/replay 均值为 0.9052/0.8273，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.8958 [0.8594, 0.9510] / 0 | 0.9239 [0.8810, 0.9521] | 0.0280 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 116.6129 | DEFAULT/replay 均值为 0.8958/0.9239，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.7106 [0.0000, 0.9086] / 0 | 0.7290 [0.0000, 0.9521] | 0.0185 | 9/1/2 | 2/1/0 | 2/69.15% | 0 | 38.8871 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_latest_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.8656 [0.5000, 0.9510] / 0 | 0.8411 [0.4329, 0.9521] | -0.0245 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 115.7108 | DEFAULT/replay 均值为 0.8656/0.8411，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_latest_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.9009 [0.8615, 0.9313] / 0 | 0.9268 [0.8751, 0.9521] | 0.0259 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 124.6517 | DEFAULT/replay 均值为 0.9009/0.9268，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_name_with_relationship | 0.9797 [0.9581, 1.0000] / 5 | 0.9401 [0.9003, 0.9573] | -0.0396 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 81.4734 | DEFAULT/replay 均值为 0.9797/0.9401，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_name_with_relationship_3_distraction_tools | 0.9455 [0.8057, 1.0000] / 6 | 0.9484 [0.9003, 0.9573] | 0.0030 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 89.6410 | DEFAULT/replay 均值为 0.9455/0.9484，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_name_with_relationship_3_distraction_tools_arg_description_scrambled | 0.9058 [0.3293, 1.0000] / 6 | 0.8713 [0.0000, 0.9573] | -0.0345 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 88.9034 | DEFAULT/replay 均值为 0.9058/0.8713，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_name_with_relationship_3_distraction_tools_arg_type_scrambled | 0.8596 [0.3051, 1.0000] / 5 | 0.7868 [0.0000, 0.9573] | -0.0728 | 10/0/2 | 1/1/0 | 1/72.00% | 0 | 60.0923 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_name_with_relationship_3_distraction_tools_tool_description_scrambled | 0.9429 [0.8204, 1.0000] / 5 | 0.9463 [0.9003, 0.9573] | 0.0034 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 90.3412 | DEFAULT/replay 均值为 0.9429/0.9463，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_name_with_relationship_3_distraction_tools_tool_name_scrambled | 0.9143 [0.3233, 1.0000] / 3 | 0.8529 [0.0000, 0.9573] | -0.0614 | 11/0/1 | 0/0/0 | 0/N/A | 0 | 80.7413 | DEFAULT/replay 均值为 0.9143/0.8529，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name | 0.9342 [0.5000, 0.9817] / 0 | 0.9345 [0.9324, 0.9573] | 0.0003 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 113.0186 | DEFAULT/replay 均值为 0.9342/0.9345，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name_3_distraction_tools | 0.9728 [0.9424, 0.9817] / 0 | 0.9324 [0.9324, 0.9324] | -0.0404 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 121.4304 | DEFAULT/replay 均值为 0.9728/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name_3_distraction_tools_arg_description_scrambled | 0.9791 [0.9503, 0.9817] / 0 | 0.9324 [0.9324, 0.9324] | -0.0467 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 98.9548 | DEFAULT/replay 均值为 0.9791/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name_3_distraction_tools_arg_type_scrambled | 0.9708 [0.9424, 0.9817] / 0 | 0.9324 [0.9324, 0.9324] | -0.0384 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 109.7372 | DEFAULT/replay 均值为 0.9708/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name_3_distraction_tools_tool_description_scrambled | 0.9740 [0.9454, 0.9817] / 0 | 0.9324 [0.9324, 0.9324] | -0.0417 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 119.1499 | DEFAULT/replay 均值为 0.9740/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_phone_number_with_name_3_distraction_tools_tool_name_scrambled | 0.9700 [0.9424, 0.9817] / 0 | 0.9324 [0.9324, 0.9324] | -0.0376 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 104.6015 | DEFAULT/replay 均值为 0.9700/0.9324，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number | 0.8617 [0.7974, 0.9217] / 0 | 0.9532 [0.9324, 0.9573] | 0.0915 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 111.2917 | DEFAULT/replay 均值为 0.8617/0.9532，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number_3_distraction_tools | 0.8662 [0.8107, 0.8969] / 0 | 0.9573 [0.9573, 0.9573] | 0.0911 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 103.5278 | DEFAULT/replay 均值为 0.8662/0.9573，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number_3_distraction_tools_arg_description_scrambled | 0.8704 [0.7924, 0.9217] / 0 | 0.9469 [0.9324, 0.9573] | 0.0766 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 111.8374 | DEFAULT/replay 均值为 0.8704/0.9469，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number_3_distraction_tools_arg_type_scrambled | 0.8618 [0.8195, 0.8969] / 0 | 0.9573 [0.9573, 0.9573] | 0.0955 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 110.4464 | DEFAULT/replay 均值为 0.8618/0.9573，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number_3_distraction_tools_tool_description_scrambled | 0.8926 [0.8293, 0.9217] / 0 | 0.9469 [0.9324, 0.9573] | 0.0543 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.4754 | DEFAULT/replay 均值为 0.8926/0.9469，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_relationship_with_phone_number_3_distraction_tools_tool_name_scrambled | 0.8675 [0.7924, 0.8969] / 0 | 0.9573 [0.9573, 0.9573] | 0.0899 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 108.3835 | DEFAULT/replay 均值为 0.8675/0.9573，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_sender_phone_number_with_content | 0.9029 [0.7944, 0.9368] / 0 | 0.9401 [0.9003, 0.9573] | 0.0372 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 106.0519 | DEFAULT/replay 均值为 0.9029/0.9401，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_phone_number_and_content | 0.8866 [0.7628, 0.9509] / 0 | 0.7747 [0.4518, 0.9663] | -0.1119 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 85.9333 | DEFAULT/replay 均值为 0.8866/0.7747，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_relationship_with_relationship_alt | 1.0000 [1.0000, 1.0000] / 12 | 0.9455 [0.9416, 0.9458] | -0.0545 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.8347 | DEFAULT/replay 均值为 1.0000/0.9455，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number | 0.8824 [0.8616, 0.9127] / 0 | 0.9194 [0.4518, 0.9663] | 0.0371 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 109.1136 | DEFAULT/replay 均值为 0.8824/0.9194，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number_3_distraction_tools | 0.8984 [0.8650, 0.9217] / 0 | 0.9518 [0.9119, 0.9663] | 0.0535 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 123.2060 | DEFAULT/replay 均值为 0.8984/0.9518，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number_3_distraction_tools_arg_description_scrambled | 0.8953 [0.8524, 0.9217] / 0 | 0.9544 [0.9425, 0.9663] | 0.0591 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.7312 | DEFAULT/replay 均值为 0.8953/0.9544，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number_3_distraction_tools_arg_type_scrambled | 0.8904 [0.8606, 0.9171] / 0 | 0.9558 [0.9119, 0.9663] | 0.0654 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 114.9511 | DEFAULT/replay 均值为 0.8904/0.9558，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number_3_distraction_tools_tool_description_scrambled | 0.8857 [0.8569, 0.9171] / 0 | 0.9558 [0.9119, 0.9663] | 0.0701 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 121.8125 | DEFAULT/replay 均值为 0.8857/0.9558，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_with_id_and_phone_number_3_distraction_tools_tool_name_scrambled | 0.9033 [0.8832, 0.9171] / 0 | 0.9377 [0.9119, 0.9663] | 0.0344 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 113.0754 | DEFAULT/replay 均值为 0.9033/0.9377，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| wifi_off | 0.8648 [0.7679, 0.9368] / 0 | 0.9395 [0.9063, 0.9633] | 0.0748 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 100.1580 | DEFAULT/replay 均值为 0.8648/0.9395，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| wifi_off_3_distraction_tools | 0.8887 [0.8029, 0.9368] / 0 | 0.9348 [0.9063, 0.9633] | 0.0461 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.0901 | DEFAULT/replay 均值为 0.8887/0.9348，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| wifi_off_3_distraction_tools_arg_description_scrambled | 0.8631 [0.7877, 0.9368] / 0 | 0.9443 [0.9063, 0.9633] | 0.0812 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.1035 | DEFAULT/replay 均值为 0.8631/0.9443，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| wifi_off_3_distraction_tools_arg_type_scrambled | 0.6837 [0.1710, 0.9368] / 0 | 0.7109 [0.0000, 0.9633] | 0.0271 | 9/0/3 | 1/1/0 | 1/78.26% | 0 | 68.0161 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| wifi_off_3_distraction_tools_tool_description_scrambled | 0.8535 [0.7877, 0.9368] / 0 | 0.9544 [0.9063, 0.9633] | 0.1009 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 103.9128 | DEFAULT/replay 均值为 0.8535/0.9544，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| wifi_off_3_distraction_tools_tool_name_scrambled | 0.8694 [0.7877, 0.9368] / 0 | 0.9471 [0.9062, 0.9635] | 0.0776 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.3419 | DEFAULT/replay 均值为 0.8694/0.9471，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_reminder_with_recency_latest | 0.9722 [0.6667, 1.0000] / 11 | 0.7950 [0.5970, 0.9301] | -0.1772 | 8/4/0 | 4/3/0 | 4/79.33% | 0 | 139.1665 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_3_distraction_tools | 0.8889 [0.6667, 1.0000] / 8 | 0.7765 [0.6217, 0.9119] | -0.1124 | 7/5/0 | 1/1/0 | 1/70.97% | 0 | 170.4779 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_3_distraction_tools_arg_description_scrambled | 0.8889 [0.6667, 1.0000] / 8 | 0.7682 [0.6217, 0.9104] | -0.1207 | 7/5/0 | 2/1/0 | 2/84.29% | 0 | 177.4798 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_3_distraction_tools_arg_type_scrambled | 0.8056 [0.3333, 1.0000] / 7 | 0.6206 [0.2990, 0.8782] | -0.1849 | 5/7/0 | 5/3/0 | 5/68.02% | 0 | 25.3826 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_3_distraction_tools_tool_description_scrambled | 0.8611 [0.6667, 1.0000] / 7 | 0.7572 [0.5686, 0.9074] | -0.1039 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 211.1968 | DEFAULT/replay 均值为 0.8611/0.7572，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_reminder_with_recency_latest_3_distraction_tools_tool_name_scrambled | 0.8333 [0.6667, 1.0000] / 6 | 0.7571 [0.6217, 0.9074] | -0.0762 | 6/6/0 | 1/1/0 | 1/51.16% | 0 | 143.0757 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt | 0.8056 [0.6667, 1.0000] / 5 | 0.7103 [0.5723, 0.9399] | -0.0953 | 4/8/0 | 3/3/0 | 3/87.22% | 0 | 66.9962 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt_3_distraction_tools | 0.8333 [0.6667, 1.0000] / 6 | 0.7050 [0.5196, 0.9025] | -0.1283 | 4/8/0 | 6/6/0 | 6/66.61% | 0 | -40.1040 | 6/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt_3_distraction_tools_arg_description_scrambled | 0.8056 [0.6667, 1.0000] / 5 | 0.7306 [0.5723, 0.9025] | -0.0749 | 5/7/0 | 5/5/0 | 5/60.51% | 0 | -4.2978 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt_3_distraction_tools_arg_type_scrambled | 0.8333 [0.3333, 1.0000] / 7 | 0.7327 [0.2990, 0.9483] | -0.1007 | 7/5/0 | 5/5/0 | 5/54.03% | 0 | -156.3788 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt_3_distraction_tools_tool_description_scrambled | 0.7778 [0.6667, 1.0000] / 4 | 0.6287 [0.5318, 0.8808] | -0.1490 | 1/11/0 | 8/8/0 | 8/78.39% | 0 | -7.9917 | 8/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_reminder_with_recency_latest_alt_3_distraction_tools_tool_name_scrambled | 0.7778 [0.3333, 1.0000] / 5 | 0.7093 [0.2990, 0.9025] | -0.0685 | 5/7/0 | 2/2/0 | 2/49.13% | 0 | -40.4412 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| remove_contact_by_phone | 0.9365 [0.9123, 0.9695] / 0 | 0.3896 [0.3896, 0.3896] | -0.5469 | 0/12/0 | 12/12/0 | 12/59.52% | 0 | 76.8498 | DEFAULT native score 高（0.9365），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_contact_by_phone_alt | 0.9342 [0.9046, 0.9654] / 0 | 0.3896 [0.3896, 0.3896] | -0.5445 | 0/12/0 | 12/12/0 | 12/39.97% | 0 | -29.4586 | DEFAULT native score 高（0.9342），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_contact_by_phone_ambiguous | 0.9404 [0.9165, 0.9654] / 0 | 0.3896 [0.3896, 0.3896] | -0.5507 | 0/12/0 | 12/12/0 | 12/56.88% | 0 | 72.9441 | DEFAULT native score 高（0.9404），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_contact_by_phone_ambiguous_alt | 0.9355 [0.9156, 0.9677] / 0 | 0.3870 [0.3583, 0.3896] | -0.5484 | 0/12/0 | 12/12/0 | 12/58.44% | 0 | 77.6182 | DEFAULT native score 高（0.9355），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_contact_by_phone_multiple_user_turn | 0.9135 [0.8585, 0.9677] / 0 | 0.4070 [0.4064, 0.4072] | -0.5064 | 0/12/0 | 12/12/0 | 12/72.26% | 0 | 71.0583 | DEFAULT native score 高（0.9135），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_contact_by_phone_multiple_user_turn_alt | 0.9706 [0.9340, 0.9933] / 0 | 0.4072 [0.4072, 0.4072] | -0.5634 | 0/12/0 | 12/12/0 | 12/66.67% | 0 | 101.7002 | DEFAULT native score 高（0.9706），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| remove_reminder_with_recency_latest | 0.7778 [0.6667, 1.0000] / 4 | 0.6699 [0.5686, 0.9214] | -0.1079 | 3/9/0 | 3/3/0 | 3/65.85% | 0 | -0.2000 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_3_distraction_tools | 0.6944 [0.6667, 1.0000] / 1 | 0.6221 [0.5723, 0.7212] | -0.0724 | 1/11/0 | 5/5/0 | 5/71.18% | 0 | -23.8478 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_3_distraction_tools_arg_description_scrambled | 0.8056 [0.6667, 1.0000] / 5 | 0.6885 [0.5723, 0.9399] | -0.1170 | 3/9/0 | 5/5/0 | 5/83.71% | 0 | 23.8749 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| remove_reminder_with_recency_latest_3_distraction_tools_arg_type_scrambled | 0.7778 [0.3333, 1.0000] / 6 | 0.6260 [0.2990, 0.9159] | -0.1518 | 4/8/0 | 5/5/0 | 5/59.73% | 0 | -286.3121 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_3_distraction_tools_tool_description_scrambled | 0.7222 [0.6667, 1.0000] / 2 | 0.6472 [0.5686, 0.9166] | -0.0751 | 1/11/0 | 4/4/0 | 4/65.49% | 0 | -37.0479 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| remove_reminder_with_recency_latest_3_distraction_tools_tool_name_scrambled | 0.8056 [0.6667, 1.0000] / 5 | 0.6913 [0.5723, 0.9313] | -0.1143 | 3/9/0 | 5/4/0 | 5/85.19% | 0 | -7.7663 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| remove_reminder_with_recency_latest_alt | 0.8333 [0.6667, 1.0000] / 6 | 0.7062 [0.5723, 0.9399] | -0.1271 | 5/7/0 | 6/6/0 | 6/74.16% | 0 | 27.3270 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_alt_3_distraction_tools | 0.7500 [0.6667, 1.0000] / 3 | 0.6556 [0.5723, 0.9182] | -0.0944 | 3/9/0 | 6/5/0 | 6/80.76% | 0 | 52.2836 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_alt_3_distraction_tools_arg_description_scrambled | 0.8333 [0.6667, 1.0000] / 6 | 0.7053 [0.3934, 0.9182] | -0.1281 | 6/6/0 | 5/5/0 | 5/72.28% | 0 | 81.7432 | 1/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_alt_3_distraction_tools_arg_type_scrambled | 0.7500 [0.3333, 1.0000] / 6 | 0.6256 [0.2990, 0.9166] | -0.1244 | 6/6/0 | 5/4/0 | 5/81.59% | 0 | 45.1142 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_alt_3_distraction_tools_tool_description_scrambled | 0.8056 [0.6667, 1.0000] / 5 | 0.7075 [0.5723, 0.9182] | -0.0980 | 5/7/0 | 5/5/0 | 5/76.32% | 0 | -97.0275 | 2/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| remove_reminder_with_recency_latest_alt_3_distraction_tools_tool_name_scrambled | 0.8056 [0.6667, 1.0000] / 5 | 0.6930 [0.5686, 0.9399] | -0.1126 | 5/7/0 | 4/3/0 | 4/87.79% | 0 | 107.6491 | 3/12 条出现 full 但 stage fail，结构完成与质量证据冲突。 |
| search_message_with_recency_oldest | 0.8772 [0.6031, 1.0000] / 2 | 0.6879 [0.0000, 0.9277] | -0.1893 | 9/0/3 | 0/0/0 | 0/N/A | 0 | 127.8411 | DEFAULT/replay 均值为 0.8772/0.6879，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_3_distraction_tools | 0.8220 [0.5974, 1.0000] / 1 | 0.5505 [0.0000, 0.9367] | -0.2716 | 7/1/4 | 0/0/0 | 0/N/A | 0 | 74.7360 | DEFAULT/replay 均值为 0.8220/0.5505，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_3_distraction_tools_arg_description_scrambled | 0.8653 [0.5873, 1.0000] / 2 | 0.5854 [0.0000, 0.9235] | -0.2799 | 6/3/3 | 0/0/0 | 0/N/A | 0 | 129.9305 | DEFAULT/replay 均值为 0.8653/0.5854，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_3_distraction_tools_arg_type_scrambled | 0.7942 [0.6052, 1.0000] / 2 | 0.4494 [0.0000, 0.9220] | -0.3448 | 6/0/6 | 1/1/0 | 1/85.71% | 0 | 129.9383 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_3_distraction_tools_tool_description_scrambled | 0.8109 [0.5528, 0.9612] / 0 | 0.4966 [0.0000, 0.9139] | -0.3142 | 6/1/5 | 1/1/0 | 1/52.94% | 0 | 20.6836 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_3_distraction_tools_tool_name_scrambled | 0.8800 [0.6449, 1.0000] / 2 | 0.6637 [0.0000, 0.9437] | -0.2163 | 9/0/3 | 0/0/0 | 0/N/A | 0 | 125.7872 | DEFAULT/replay 均值为 0.8800/0.6637，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_alt | 0.8212 [0.5694, 0.9636] / 0 | 0.5621 [0.0000, 0.9380] | -0.2591 | 6/2/4 | 0/0/0 | 0/N/A | 0 | 111.1242 | DEFAULT/replay 均值为 0.8212/0.5621，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_alt_3_distraction_tools | 0.8886 [0.6667, 0.9636] / 0 | 0.8239 [0.5629, 0.9437] | -0.0647 | 9/3/0 | 1/1/0 | 1/74.29% | 0 | 120.2549 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_alt_3_distraction_tools_arg_description_scrambled | 0.8032 [0.5979, 0.9636] / 0 | 0.5773 [0.0000, 0.9359] | -0.2260 | 7/2/3 | 0/0/0 | 0/N/A | 0 | 82.0736 | DEFAULT/replay 均值为 0.8032/0.5773，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_alt_3_distraction_tools_arg_type_scrambled | 0.7470 [0.0000, 0.9745] / 0 | 0.4717 [0.0000, 0.9220] | -0.2753 | 5/2/5 | 1/1/0 | 1/85.71% | 0 | 101.4434 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_alt_3_distraction_tools_tool_description_scrambled | 0.7343 [0.5743, 0.9636] / 0 | 0.4023 [0.0000, 0.9359] | -0.3320 | 4/3/5 | 1/1/0 | 1/78.79% | 0 | 8.7332 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_alt_3_distraction_tools_tool_name_scrambled | 0.8577 [0.5918, 0.9636] / 0 | 0.6892 [0.0000, 0.9359] | -0.1685 | 9/0/3 | 0/0/0 | 0/N/A | 0 | 80.2256 | DEFAULT/replay 均值为 0.8577/0.6892，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_multiple_user_turn | 0.9226 [0.9134, 0.9331] / 0 | 0.8941 [0.5880, 0.9416] | -0.0285 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 137.3793 | DEFAULT/replay 均值为 0.9226/0.8941，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools | 0.8610 [0.5789, 0.9383] / 0 | 0.7362 [0.0000, 0.9416] | -0.1248 | 9/1/2 | 1/1/0 | 1/74.29% | 0 | 73.6918 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.8654 [0.5920, 0.9394] / 0 | 0.7609 [0.0000, 0.9302] | -0.1045 | 10/0/2 | 0/0/0 | 0/N/A | 0 | 83.3686 | DEFAULT/replay 均值为 0.8654/0.7609，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.7719 [0.3333, 0.9351] / 0 | 0.7258 [0.2996, 0.9181] | -0.0461 | 8/4/0 | 1/1/0 | 1/70.97% | 0 | 80.0215 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.8317 [0.5758, 0.9383] / 0 | 0.6035 [0.0000, 0.9416] | -0.2282 | 6/3/3 | 3/3/0 | 3/51.31% | 0 | -244.2602 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.9192 [0.9063, 0.9286] / 0 | 0.9224 [0.8924, 0.9416] | 0.0032 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 135.9277 | DEFAULT/replay 均值为 0.9192/0.9224，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_multiple_user_turn_alt | 0.7469 [0.3333, 0.9394] / 0 | 0.6289 [0.0000, 0.9416] | -0.1180 | 7/3/2 | 0/0/0 | 0/N/A | 0 | 104.1462 | DEFAULT/replay 均值为 0.7469/0.6289，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_message_with_recency_oldest_multiple_user_turn_alt_3_distraction_tools | 0.9308 [0.9139, 0.9597] / 0 | 0.8080 [0.0000, 0.9320] | -0.1228 | 10/1/1 | 2/2/0 | 2/46.84% | 0 | -64.9006 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.8968 [0.5956, 0.9405] / 0 | 0.7088 [0.0000, 0.9320] | -0.1880 | 8/2/2 | 2/2/0 | 2/59.84% | 0 | 46.6002 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.7107 [0.3333, 0.9383] / 0 | 0.5761 [0.0000, 0.9320] | -0.1346 | 6/4/2 | 3/2/0 | 3/79.31% | 0 | -2.4688 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.7681 [0.0000, 0.9383] / 0 | 0.6823 [0.0000, 0.9299] | -0.0858 | 8/2/2 | 2/1/0 | 2/73.19% | 0 | 21.6276 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_message_with_recency_oldest_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.8968 [0.5648, 0.9391] / 0 | 0.8162 [0.0000, 0.9416] | -0.0806 | 10/1/1 | 1/1/0 | 1/28.57% | 0 | 22.0215 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday | 0.9337 [0.9120, 0.9552] / 0 | 0.9338 [0.9230, 0.9437] | 0.0001 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 146.9890 | DEFAULT/replay 均值为 0.9337/0.9338，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_creation_recency_yesterday_3_distraction_tools | 0.9344 [0.9117, 0.9507] / 0 | 0.8774 [0.6273, 0.9430] | -0.0570 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 148.2565 | DEFAULT/replay 均值为 0.9344/0.8774，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_creation_recency_yesterday_3_distraction_tools_arg_description_scrambled | 0.9370 [0.9096, 0.9505] / 0 | 0.9032 [0.6273, 0.9401] | -0.0338 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 168.1289 | DEFAULT/replay 均值为 0.9370/0.9032，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_creation_recency_yesterday_3_distraction_tools_arg_type_scrambled | 0.9300 [0.8969, 0.9528] / 0 | 0.7232 [0.2990, 0.9437] | -0.2068 | 7/5/0 | 4/4/0 | 4/51.00% | 0 | -43.5524 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_3_distraction_tools_tool_description_scrambled | 0.9351 [0.9043, 0.9597] / 0 | 0.9307 [0.9228, 0.9437] | -0.0044 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 142.5993 | DEFAULT/replay 均值为 0.9351/0.9307，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_creation_recency_yesterday_3_distraction_tools_tool_name_scrambled | 0.9349 [0.9085, 0.9550] / 0 | 0.9068 [0.6273, 0.9437] | -0.0281 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 147.6823 | DEFAULT/replay 均值为 0.9349/0.9068，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_creation_recency_yesterday_implicit | 0.8947 [0.6667, 0.9586] / 0 | 0.8032 [0.6178, 0.9361] | -0.0915 | 7/5/0 | 4/4/0 | 4/70.58% | 0 | 127.7974 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_implicit_3_distraction_tools | 0.9153 [0.6667, 0.9714] / 0 | 0.8285 [0.6273, 0.9401] | -0.0867 | 8/4/0 | 4/4/0 | 4/62.97% | 0 | 46.7347 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_implicit_3_distraction_tools_arg_description_scrambled | 0.9352 [0.9139, 0.9714] / 0 | 0.8810 [0.6273, 0.9429] | -0.0541 | 10/2/0 | 1/1/0 | 1/77.42% | 0 | 181.6242 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_implicit_3_distraction_tools_arg_type_scrambled | 0.9379 [0.9190, 0.9654] / 0 | 0.7209 [0.2990, 0.9379] | -0.2170 | 7/5/0 | 5/5/0 | 5/52.20% | 0 | -45.6596 | 5/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_implicit_3_distraction_tools_tool_description_scrambled | 0.8941 [0.6667, 0.9714] / 0 | 0.8027 [0.6273, 0.9375] | -0.0915 | 7/5/0 | 3/3/0 | 3/76.73% | 0 | 153.5685 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_creation_recency_yesterday_implicit_3_distraction_tools_tool_name_scrambled | 0.9123 [0.6667, 0.9714] / 0 | 0.8253 [0.6178, 0.9302] | -0.0870 | 8/4/0 | 3/3/0 | 3/74.69% | 0 | 121.7926 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming | 0.8707 [0.5813, 0.9878] / 0 | 0.7312 [0.2990, 0.9403] | -0.1395 | 8/4/0 | 1/1/0 | 1/78.72% | 0 | 94.4634 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_3_distraction_tools | 0.8460 [0.5810, 0.9592] / 0 | 0.6305 [0.2990, 0.9429] | -0.2156 | 4/8/0 | 3/2/0 | 3/84.67% | 0 | 100.7140 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_3_distraction_tools_arg_description_scrambled | 0.8535 [0.5820, 0.9852] / 0 | 0.7215 [0.2990, 0.9437] | -0.1320 | 8/4/0 | 1/1/0 | 1/61.02% | 0 | 104.4435 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_3_distraction_tools_arg_type_scrambled | 0.7674 [0.5846, 1.0000] / 1 | 0.5486 [0.2990, 0.9437] | -0.2188 | 4/8/0 | 0/0/0 | 0/N/A | 0 | 71.0136 | DEFAULT/replay 均值为 0.7674/0.5486，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_upcoming_3_distraction_tools_tool_description_scrambled | 0.8464 [0.5858, 0.9915] / 0 | 0.6890 [0.2990, 0.9309] | -0.1574 | 6/6/0 | 0/0/0 | 0/N/A | 0 | 158.0666 | DEFAULT/replay 均值为 0.8464/0.6890，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_upcoming_3_distraction_tools_tool_name_scrambled | 0.9404 [0.9103, 0.9844] / 0 | 0.8483 [0.5905, 0.9429] | -0.0921 | 9/3/0 | 1/1/0 | 1/73.58% | 0 | 116.2196 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_implicit | 0.8807 [0.6667, 0.9417] / 0 | 0.7283 [0.6178, 0.9361] | -0.1524 | 4/8/0 | 4/4/0 | 4/80.59% | 0 | 129.3001 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_implicit_3_distraction_tools | 0.8378 [0.5826, 0.9417] / 0 | 0.6146 [0.2990, 0.9305] | -0.2232 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 95.0894 | DEFAULT/replay 均值为 0.8378/0.6146，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_upcoming_implicit_3_distraction_tools_arg_description_scrambled | 0.8985 [0.5938, 0.9540] / 0 | 0.6693 [0.2990, 0.9338] | -0.2292 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 126.9284 | DEFAULT/replay 均值为 0.8985/0.6693，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_upcoming_implicit_3_distraction_tools_arg_type_scrambled | 0.7853 [0.5855, 0.9372] / 0 | 0.4905 [0.2990, 0.6273] | -0.2948 | 0/12/0 | 4/4/0 | 4/63.06% | 0 | -55.4892 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_implicit_3_distraction_tools_tool_description_scrambled | 0.8968 [0.6018, 0.9585] / 0 | 0.6635 [0.2990, 0.9348] | -0.2333 | 3/9/0 | 2/2/0 | 2/88.83% | 0 | 119.1167 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_upcoming_implicit_3_distraction_tools_tool_name_scrambled | 0.8097 [0.5818, 0.9597] / 0 | 0.5343 [0.2990, 0.9350] | -0.2754 | 1/11/0 | 2/1/0 | 2/85.71% | 0 | 59.4704 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday | 0.9418 [0.8812, 0.9808] / 0 | 0.8811 [0.6273, 0.9437] | -0.0607 | 10/2/0 | 1/1/0 | 1/86.49% | 0 | 116.8055 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_3_distraction_tools | 0.9389 [0.8940, 0.9867] / 0 | 0.9032 [0.6178, 0.9401] | -0.0358 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 158.6151 | DEFAULT/replay 均值为 0.9389/0.9032，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_yesterday_3_distraction_tools_arg_description_scrambled | 0.9434 [0.8907, 0.9852] / 0 | 0.8777 [0.6178, 0.9359] | -0.0657 | 10/2/0 | 1/1/0 | 1/32.00% | 0 | 105.9030 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_3_distraction_tools_arg_type_scrambled | 0.8859 [0.3333, 0.9528] / 0 | 0.7717 [0.2990, 0.9401] | -0.1143 | 8/4/0 | 3/3/0 | 3/45.50% | 0 | -9.8978 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_3_distraction_tools_tool_description_scrambled | 0.9317 [0.8972, 0.9528] / 0 | 0.8531 [0.6178, 0.9437] | -0.0786 | 9/3/0 | 1/1/0 | 1/55.81% | 0 | 111.3605 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_3_distraction_tools_tool_name_scrambled | 0.9596 [0.9372, 0.9808] / 0 | 0.9041 [0.6273, 0.9384] | -0.0555 | 11/1/0 | 1/1/0 | 1/91.43% | 0 | 117.4876 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_implicit | 0.8375 [0.6667, 0.9515] / 0 | 0.7548 [0.6273, 0.9437] | -0.0827 | 5/7/0 | 0/0/0 | 0/N/A | 0 | 178.1577 | DEFAULT/replay 均值为 0.8375/0.7548，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_yesterday_implicit_3_distraction_tools | 0.8291 [0.6667, 0.9636] / 0 | 0.8038 [0.6273, 0.9361] | -0.0253 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 131.7506 | DEFAULT/replay 均值为 0.8291/0.8038，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_yesterday_implicit_3_distraction_tools_arg_description_scrambled | 0.8462 [0.6667, 0.9540] / 0 | 0.8312 [0.6273, 0.9401] | -0.0150 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 151.8465 | DEFAULT/replay 均值为 0.8462/0.8312，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_yesterday_implicit_3_distraction_tools_arg_type_scrambled | 0.8118 [0.3333, 0.9540] / 0 | 0.7487 [0.2990, 0.9437] | -0.0632 | 8/4/0 | 3/3/0 | 3/40.94% | 0 | -133.3682 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| search_reminder_with_recency_yesterday_implicit_3_distraction_tools_tool_description_scrambled | 0.8644 [0.6667, 0.9540] / 0 | 0.8309 [0.6273, 0.9401] | -0.0335 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 143.6322 | DEFAULT/replay 均值为 0.8644/0.8309，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| search_reminder_with_recency_yesterday_implicit_3_distraction_tools_tool_name_scrambled | 0.9082 [0.6667, 0.9878] / 0 | 0.8533 [0.6273, 0.9411] | -0.0549 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 133.8314 | DEFAULT/replay 均值为 0.9082/0.8533，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode | 0.8951 [0.8453, 0.9243] / 0 | 0.9360 [0.9360, 0.9360] | 0.0409 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 100.4105 | DEFAULT/replay 均值为 0.8951/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_3_distraction_tools | 0.8968 [0.8370, 0.9433] / 0 | 0.9341 [0.9242, 0.9360] | 0.0372 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 106.5757 | DEFAULT/replay 均值为 0.8968/0.9341，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0.8917 [0.8441, 0.9433] / 0 | 0.9084 [0.6174, 0.9360] | 0.0167 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 105.3071 | DEFAULT/replay 均值为 0.8917/0.9084，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0.7035 [0.1547, 0.9243] / 0 | 0.7020 [0.0000, 0.9360] | -0.0015 | 9/0/3 | 3/3/0 | 3/63.69% | 0 | -10.9727 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_cellular_low_battery_mode_3_distraction_tools_tool_description_scrambled | 0.8975 [0.8379, 0.9243] / 0 | 0.9360 [0.9360, 0.9360] | 0.0384 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 101.7586 | DEFAULT/replay 均值为 0.8975/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_3_distraction_tools_tool_name_scrambled | 0.9044 [0.8476, 0.9433] / 0 | 0.9341 [0.9243, 0.9361] | 0.0297 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 91.4357 | DEFAULT/replay 均值为 0.9044/0.9341，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_implicit | 0.8514 [0.8128, 0.9022] / 0 | 0.9360 [0.9360, 0.9360] | 0.0846 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 105.7373 | DEFAULT/replay 均值为 0.8514/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_implicit_3_distraction_tools | 0.8516 [0.8118, 0.9123] / 0 | 0.9360 [0.9360, 0.9360] | 0.0844 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 98.7493 | DEFAULT/replay 均值为 0.8516/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_implicit_3_distraction_tools_arg_description_scrambled | 0.8569 [0.8034, 0.9123] / 0 | 0.9360 [0.9360, 0.9360] | 0.0790 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 95.8048 | DEFAULT/replay 均值为 0.8569/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_implicit_3_distraction_tools_arg_type_scrambled | 0.6893 [0.1625, 0.9433] / 0 | 0.7010 [0.0000, 0.9360] | 0.0117 | 9/0/3 | 3/2/0 | 3/69.51% | 0 | -27.5789 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_cellular_low_battery_mode_implicit_3_distraction_tools_tool_description_scrambled | 0.8619 [0.8154, 0.9210] / 0 | 0.9360 [0.9360, 0.9360] | 0.0740 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 104.9055 | DEFAULT/replay 均值为 0.8619/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_cellular_low_battery_mode_implicit_3_distraction_tools_tool_name_scrambled | 0.8611 [0.8154, 0.9180] / 0 | 0.9361 [0.9361, 0.9361] | 0.0750 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 94.3048 | DEFAULT/replay 均值为 0.8611/0.9361，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode | 0.8800 [0.8269, 0.9243] / 0 | 0.9360 [0.9360, 0.9360] | 0.0560 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 104.7445 | DEFAULT/replay 均值为 0.8800/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_3_distraction_tools | 0.8984 [0.8585, 0.9433] / 0 | 0.9074 [0.6174, 0.9360] | 0.0091 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 102.6292 | DEFAULT/replay 均值为 0.8984/0.9074，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0.8933 [0.8389, 0.9243] / 0 | 0.9360 [0.9360, 0.9360] | 0.0427 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.4318 | DEFAULT/replay 均值为 0.8933/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0.7219 [0.1933, 0.9433] / 0 | 0.7000 [0.0000, 0.9360] | -0.0219 | 9/0/3 | 3/3/0 | 3/47.38% | 0 | -249.1144 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_location_low_battery_mode_3_distraction_tools_tool_description_scrambled | 0.8813 [0.8255, 0.9243] / 0 | 0.9360 [0.9360, 0.9360] | 0.0546 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 101.2156 | DEFAULT/replay 均值为 0.8813/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_3_distraction_tools_tool_name_scrambled | 0.8928 [0.8311, 0.9243] / 0 | 0.9361 [0.9361, 0.9361] | 0.0432 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 98.6478 | DEFAULT/replay 均值为 0.8928/0.9361，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_implicit | 0.8287 [0.8026, 0.8633] / 0 | 0.9360 [0.9360, 0.9360] | 0.1073 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 103.1550 | DEFAULT/replay 均值为 0.8287/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_implicit_3_distraction_tools | 0.8280 [0.8008, 0.8789] / 0 | 0.9068 [0.5871, 0.9360] | 0.0788 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 107.2224 | DEFAULT/replay 均值为 0.8280/0.9068，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_implicit_3_distraction_tools_arg_description_scrambled | 0.8117 [0.6667, 0.8616] / 0 | 0.9077 [0.6138, 0.9360] | 0.0959 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 91.8038 | DEFAULT/replay 均值为 0.8117/0.9077，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_implicit_3_distraction_tools_arg_type_scrambled | 0.6639 [0.1650, 0.8789] / 0 | 0.7018 [0.0000, 0.9360] | 0.0380 | 9/0/3 | 3/3/0 | 3/59.57% | 0 | -0.6330 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_location_low_battery_mode_implicit_3_distraction_tools_tool_description_scrambled | 0.8437 [0.7993, 0.8862] / 0 | 0.9357 [0.9345, 0.9360] | 0.0920 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 99.4407 | DEFAULT/replay 均值为 0.8437/0.9357，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_location_low_battery_mode_implicit_3_distraction_tools_tool_name_scrambled | 0.8396 [0.7979, 0.8812] / 0 | 0.9067 [0.5849, 0.9361] | 0.0671 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 99.7487 | DEFAULT/replay 均值为 0.8396/0.9067，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode | 0.8607 [0.8042, 0.8947] / 0 | 0.9094 [0.6174, 0.9360] | 0.0487 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 102.4374 | DEFAULT/replay 均值为 0.8607/0.9094，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_3_distraction_tools | 0.8777 [0.8269, 0.9123] / 0 | 0.9360 [0.9360, 0.9360] | 0.0582 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 96.5017 | DEFAULT/replay 均值为 0.8777/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_3_distraction_tools_arg_description_scrambled | 0.8638 [0.7983, 0.8947] / 0 | 0.9094 [0.6174, 0.9360] | 0.0456 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 100.3994 | DEFAULT/replay 均值为 0.8638/0.9094，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_3_distraction_tools_arg_type_scrambled | 0.6839 [0.1309, 0.8947] / 0 | 0.7020 [0.0000, 0.9360] | 0.0180 | 9/0/3 | 3/3/0 | 3/58.52% | 0 | -9.1329 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_wifi_low_battery_mode_3_distraction_tools_tool_description_scrambled | 0.8783 [0.8241, 0.9123] / 0 | 0.9360 [0.9360, 0.9360] | 0.0576 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 103.6144 | DEFAULT/replay 均值为 0.8783/0.9360，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_3_distraction_tools_tool_name_scrambled | 0.8768 [0.8177, 0.9896] / 0 | 0.9359 [0.9339, 0.9361] | 0.0591 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 100.7374 | DEFAULT/replay 均值为 0.8768/0.9359，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_implicit | 0.8346 [0.8011, 0.8828] / 0 | 0.9094 [0.6174, 0.9360] | 0.0748 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 105.3623 | DEFAULT/replay 均值为 0.8346/0.9094，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_implicit_3_distraction_tools | 0.7777 [0.6667, 0.8650] / 0 | 0.8518 [0.5915, 0.9360] | 0.0741 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 102.5542 | DEFAULT/replay 均值为 0.7777/0.8518，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_implicit_3_distraction_tools_arg_description_scrambled | 0.7771 [0.6667, 0.8650] / 0 | 0.9037 [0.6174, 0.9360] | 0.1266 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 108.1101 | DEFAULT/replay 均值为 0.7771/0.9037，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_implicit_3_distraction_tools_arg_type_scrambled | 0.6391 [0.1266, 0.8650] / 0 | 0.6704 [0.0000, 0.9360] | 0.0314 | 8/1/3 | 3/3/0 | 3/60.05% | 0 | 5.1403 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| turn_on_wifi_low_battery_mode_implicit_3_distraction_tools_tool_description_scrambled | 0.7807 [0.6667, 0.8789] / 0 | 0.7956 [0.5915, 0.9360] | 0.0150 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 111.5103 | DEFAULT/replay 均值为 0.7807/0.7956，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| turn_on_wifi_low_battery_mode_implicit_3_distraction_tools_tool_name_scrambled | 0.8127 [0.6667, 0.8947] / 0 | 0.8269 [0.5916, 0.9528] | 0.0142 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 143.8648 | DEFAULT/replay 均值为 0.8127/0.8269，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_relationship_with_relationship | 0.8422 [0.4418, 0.9096] / 0 | 0.8791 [0.2956, 0.9322] | 0.0369 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 86.0797 | DEFAULT/replay 均值为 0.8422/0.8791，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| update_contact_relationship_with_relationship_multiple_user_turn | 0.8698 [0.8355, 0.8978] / 0 | 0.6901 [0.6051, 0.9289] | -0.1798 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 135.9606 | DEFAULT/replay 均值为 0.8698/0.6901，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday | 0.9691 [0.9510, 0.9864] / 0 | 0.9404 [0.9221, 0.9432] | -0.0287 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 190.2707 | DEFAULT/replay 均值为 0.9691/0.9404，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_3_distraction_tools | 0.9689 [0.9506, 0.9937] / 0 | 0.9397 [0.9310, 0.9432] | -0.0291 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 174.2730 | DEFAULT/replay 均值为 0.9689/0.9397，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_3_distraction_tools_arg_description_scrambled | 0.8455 [0.5000, 0.9751] / 0 | 0.6349 [0.2242, 0.9432] | -0.2106 | 7/5/0 | 2/2/0 | 2/45.29% | 0 | -232.6914 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_3_distraction_tools_arg_type_scrambled | 0.8489 [0.5000, 0.9937] / 0 | 0.8214 [0.4742, 0.9554] | -0.0275 | 9/3/0 | 1/1/0 | 1/81.48% | 0 | 128.4770 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_3_distraction_tools_tool_description_scrambled | 0.8402 [0.4705, 0.9777] / 0 | 0.6998 [0.2242, 0.9432] | -0.1404 | 8/4/0 | 1/1/0 | 1/57.14% | 0 | 56.5025 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_3_distraction_tools_tool_name_scrambled | 0.9095 [0.5000, 0.9937] / 0 | 0.8209 [0.2242, 0.9554] | -0.0886 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 148.3580 | DEFAULT/replay 均值为 0.9095/0.8209，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_alt | 0.9434 [0.9250, 0.9612] / 0 | 0.9424 [0.9310, 0.9554] | -0.0009 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 199.2559 | DEFAULT/replay 均值为 0.9434/0.9424，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_alt_3_distraction_tools | 0.9220 [0.6824, 0.9612] / 0 | 0.8815 [0.2242, 0.9554] | -0.0405 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 183.0293 | DEFAULT/replay 均值为 0.9220/0.8815，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_alt_3_distraction_tools_arg_description_scrambled | 0.8225 [0.4484, 0.9568] / 0 | 0.6468 [0.2242, 0.9554] | -0.1757 | 7/5/0 | 3/3/0 | 3/55.03% | 0 | 18.1155 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_alt_3_distraction_tools_arg_type_scrambled | 0.8276 [0.5000, 0.9541] / 0 | 0.8284 [0.4663, 0.9554] | 0.0008 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 162.7338 | DEFAULT/replay 均值为 0.8276/0.8284，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_alt_3_distraction_tools_tool_description_scrambled | 0.7979 [0.4446, 0.9568] / 0 | 0.7280 [0.2242, 0.9554] | -0.0699 | 8/4/0 | 2/2/0 | 2/66.74% | 0 | 67.1705 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_alt_3_distraction_tools_tool_name_scrambled | 0.8620 [0.4554, 0.9568] / 0 | 0.8275 [0.2242, 0.9554] | -0.0346 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 157.8843 | DEFAULT/replay 均值为 0.8620/0.8275，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_multiple_user_turn | 0.9521 [0.9319, 0.9692] / 0 | 0.9377 [0.9253, 0.9537] | -0.0145 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 34.4805 | DEFAULT/replay 均值为 0.9521/0.9377，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_multiple_user_turn_3_distraction_tools | 0.9577 [0.9420, 0.9751] / 0 | 0.9388 [0.9316, 0.9537] | -0.0189 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 166.6768 | DEFAULT/replay 均值为 0.9577/0.9388，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.9543 [0.9292, 0.9705] / 0 | 0.9354 [0.9209, 0.9537] | -0.0189 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 178.4692 | DEFAULT/replay 均值为 0.9543/0.9354，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.7775 [0.2500, 0.9597] / 0 | 0.7617 [0.2247, 0.9537] | -0.0158 | 8/4/0 | 2/2/0 | 2/55.48% | 0 | -114.5271 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.9069 [0.2500, 0.9751] / 0 | 0.8792 [0.2247, 0.9427] | -0.0276 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 156.6110 | DEFAULT/replay 均值为 0.9069/0.8792，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.9542 [0.9292, 0.9705] / 0 | 0.9442 [0.9316, 0.9537] | -0.0101 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 184.2712 | DEFAULT/replay 均值为 0.9542/0.9442，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off | 0.9312 [0.8962, 0.9484] / 0 | 0.4658 [0.4630, 0.4742] | -0.4654 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 66.9445 | DEFAULT native score 高（0.9312），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_10_distraction_tools | 0.9267 [0.8902, 0.9513] / 0 | 0.4630 [0.4630, 0.4630] | -0.4637 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 63.3157 | DEFAULT native score 高（0.9267），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_3_distraction_tools | 0.9258 [0.8916, 0.9543] / 0 | 0.4677 [0.4630, 0.4742] | -0.4582 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 71.0181 | DEFAULT native score 高（0.9258），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_3_distraction_tools_arg_description_scrambled | 0.9324 [0.8895, 0.9543] / 0 | 0.4658 [0.4630, 0.4742] | -0.4666 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 61.8549 | DEFAULT native score 高（0.9324），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_3_distraction_tools_arg_type_scrambled | 0.7918 [0.3624, 0.9457] / 0 | 0.4033 [0.2242, 0.4630] | -0.3886 | 0/12/0 | 1/0/0 | 1/100.00% | 0 | 30.1458 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_3_distraction_tools_tool_description_scrambled | 0.9306 [0.8938, 0.9484] / 0 | 0.4658 [0.4630, 0.4742] | -0.4648 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 65.4585 | DEFAULT native score 高（0.9306），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_3_distraction_tools_tool_name_scrambled | 0.9260 [0.9026, 0.9484] / 0 | 0.4630 [0.4630, 0.4630] | -0.4631 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 67.0527 | DEFAULT native score 高（0.9260），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_alt | 0.9336 [0.9034, 0.9582] / 0 | 0.4630 [0.4630, 0.4630] | -0.4706 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 79.9724 | DEFAULT native score 高（0.9336），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_alt_10_distraction_tools | 0.9222 [0.8941, 0.9493] / 0 | 0.4630 [0.4630, 0.4630] | -0.4593 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 65.3520 | DEFAULT native score 高（0.9222），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_alt_3_distraction_tools | 0.9184 [0.8941, 0.9420] / 0 | 0.4630 [0.4630, 0.4630] | -0.4554 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 68.0337 | DEFAULT native score 高（0.9184），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_alt_3_distraction_tools_arg_description_scrambled | 0.9215 [0.8859, 0.9564] / 0 | 0.4630 [0.4630, 0.4630] | -0.4586 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 71.7855 | DEFAULT native score 高（0.9215），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_alt_3_distraction_tools_arg_type_scrambled | 0.7925 [0.3857, 0.9445] / 0 | 0.4033 [0.2242, 0.4630] | -0.3892 | 0/12/0 | 3/3/0 | 3/40.00% | 0 | -404.8259 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_alt_3_distraction_tools_tool_description_scrambled | 0.8833 [0.3931, 0.9558] / 0 | 0.4431 [0.2242, 0.4630] | -0.4402 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 60.6367 | DEFAULT/replay 均值为 0.8833/0.4431，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_alt_3_distraction_tools_tool_name_scrambled | 0.9329 [0.9102, 0.9564] / 0 | 0.4630 [0.4630, 0.4630] | -0.4699 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 74.4279 | DEFAULT native score 高（0.9329），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn | 0.8797 [0.3613, 0.9555] / 0 | 0.4220 [0.0000, 0.4618] | -0.4577 | 0/11/1 | 1/1/0 | 1/46.15% | 0 | -14.3634 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_10_distraction_tools | 0.9318 [0.9138, 0.9564] / 0 | 0.4608 [0.4579, 0.4618] | -0.4710 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 64.2269 | DEFAULT native score 高（0.9318），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_3_distraction_tools | 0.9342 [0.9202, 0.9564] / 0 | 0.4218 [0.0000, 0.4618] | -0.5124 | 0/11/1 | 1/1/0 | 1/62.07% | 0 | 50.7763 | DEFAULT native score 高（0.9342），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.9336 [0.9202, 0.9564] / 0 | 0.4604 [0.4530, 0.4618] | -0.4731 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 68.8041 | DEFAULT native score 高（0.9336），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.7973 [0.3503, 0.9591] / 0 | 0.3630 [0.0000, 0.4618] | -0.4343 | 0/11/1 | 4/4/0 | 4/60.60% | 0 | -140.2403 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.9353 [0.9202, 0.9705] / 0 | 0.4612 [0.4547, 0.4618] | -0.4741 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 74.8196 | DEFAULT native score 高（0.9353），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.8691 [0.1191, 0.9595] / 0 | 0.4186 [0.0000, 0.4618] | -0.4505 | 0/11/1 | 1/1/0 | 1/62.07% | 0 | 22.4522 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt | 0.9188 [0.6575, 0.9586] / 0 | 0.4618 [0.4618, 0.4618] | -0.4570 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 58.4347 | DEFAULT native score 高（0.9188），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_10_distraction_tools | 0.9193 [0.6602, 0.9564] / 0 | 0.4618 [0.4618, 0.4618] | -0.4575 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 54.9409 | DEFAULT native score 高（0.9193），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_3_distraction_tools | 0.9156 [0.6575, 0.9564] / 0 | 0.4618 [0.4618, 0.4618] | -0.4538 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 56.2299 | DEFAULT native score 高（0.9156），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.8995 [0.6575, 0.9586] / 0 | 0.4618 [0.4618, 0.4618] | -0.4377 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 62.0818 | DEFAULT/replay 均值为 0.8995/0.4618，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.7709 [0.3492, 0.9513] / 0 | 0.4036 [0.2247, 0.4747] | -0.3673 | 0/12/0 | 3/3/0 | 3/61.99% | 0 | -74.3327 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.9423 [0.9233, 0.9637] / 0 | 0.4618 [0.4618, 0.4618] | -0.4805 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 62.8097 | DEFAULT native score 高（0.9423），但 replay full 仅 0/12，存在漏判或结构/质量口径偏严风险。 |
| send_message_with_contact_content_cellular_off_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.8767 [0.6575, 0.9582] / 0 | 0.4618 [0.4618, 0.4618] | -0.4149 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 64.1492 | DEFAULT/replay 均值为 0.8767/0.4618，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off | 0.8178 [0.7489, 0.9782] / 0 | 0.6447 [0.5351, 0.9283] | -0.1730 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 91.1891 | DEFAULT/replay 均值为 0.8178/0.6447，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_10_distraction_tools | 0.7059 [0.5488, 0.7949] / 0 | 0.4261 [0.1632, 0.5547] | -0.2798 | 0/12/0 | 1/1/0 | 1/66.67% | 0 | -56.5313 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_3_distraction_tools | 0.8228 [0.7623, 0.9927] / 0 | 0.6405 [0.5394, 0.9200] | -0.1823 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 82.9056 | DEFAULT/replay 均值为 0.8228/0.6405，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_3_distraction_tools_arg_description_scrambled | 0.7888 [0.6000, 0.9801] / 0 | 0.5821 [0.3704, 0.8974] | -0.2067 | 3/9/0 | 2/2/0 | 2/75.76% | 0 | -88.5511 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_3_distraction_tools_arg_type_scrambled | 0.6644 [0.2000, 0.9949] / 0 | 0.5160 [0.1632, 0.9099] | -0.1484 | 2/10/0 | 0/0/0 | 0/N/A | 0 | 35.6190 | DEFAULT/replay 均值为 0.6644/0.5160，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_3_distraction_tools_tool_description_scrambled | 0.7439 [0.2000, 0.9822] / 0 | 0.5514 [0.1632, 0.9212] | -0.1925 | 1/11/0 | 0/0/0 | 0/N/A | 0 | 72.2546 | DEFAULT/replay 均值为 0.7439/0.5514，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_3_distraction_tools_tool_name_scrambled | 0.7882 [0.7562, 0.9782] / 0 | 0.5828 [0.5394, 0.9205] | -0.2054 | 1/11/0 | 0/0/0 | 0/N/A | 0 | 59.7633 | DEFAULT/replay 均值为 0.7882/0.5828，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_alt | 0.7737 [0.4000, 0.9665] / 0 | 0.6279 [0.3546, 0.9348] | -0.1458 | 3/9/0 | 0/0/0 | 0/N/A | 0 | 74.3705 | DEFAULT/replay 均值为 0.7737/0.6279，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_alt_10_distraction_tools | 0.7016 [0.5304, 0.7731] / 0 | 0.4916 [0.1794, 0.5547] | -0.2100 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 19.5655 | DEFAULT/replay 均值为 0.7016/0.4916，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_alt_3_distraction_tools | 0.7520 [0.7371, 0.7654] / 0 | 0.5534 [0.5394, 0.5547] | -0.1985 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 42.0583 | DEFAULT/replay 均值为 0.7520/0.5534，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_alt_3_distraction_tools_arg_description_scrambled | 0.6474 [0.4000, 0.7564] / 0 | 0.4704 [0.3704, 0.5418] | -0.1770 | 0/12/0 | 3/1/0 | 3/94.62% | 0 | -72.6871 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_alt_3_distraction_tools_arg_type_scrambled | 0.5967 [0.2000, 0.7623] / 0 | 0.4415 [0.1632, 0.5547] | -0.1552 | 0/12/0 | 2/2/0 | 2/67.66% | 0 | -94.6968 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_alt_3_distraction_tools_tool_description_scrambled | 0.7728 [0.2000, 0.9623] / 0 | 0.6435 [0.1632, 0.9279] | -0.1293 | 4/8/0 | 0/0/0 | 0/N/A | 0 | 100.8111 | DEFAULT/replay 均值为 0.7728/0.6435，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_alt_3_distraction_tools_tool_name_scrambled | 0.7542 [0.7426, 0.7731] / 0 | 0.5522 [0.5394, 0.5547] | -0.2020 | 0/12/0 | 0/0/0 | 0/N/A | 0 | 52.4594 | DEFAULT/replay 均值为 0.7542/0.5522，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_multiple_user_turn | 0.8235 [0.2000, 0.9665] / 0 | 0.7517 [0.1754, 0.9368] | -0.0719 | 7/5/0 | 0/0/0 | 0/N/A | 0 | 272.1569 | DEFAULT/replay 均值为 0.8235/0.7517，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_10_distraction_tools | 0.7435 [0.2000, 0.9801] / 0 | 0.6746 [0.1643, 0.9359] | -0.0688 | 6/6/0 | 1/1/0 | 1/64.86% | 0 | 66.4568 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_3_distraction_tools | 0.7691 [0.4000, 0.9654] / 0 | 0.6519 [0.1643, 0.9195] | -0.1172 | 5/7/0 | 0/0/0 | 0/N/A | 0 | 158.9683 | DEFAULT/replay 均值为 0.7691/0.6519，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.7865 [0.2000, 0.9665] / 0 | 0.6829 [0.1754, 0.9264] | -0.1036 | 6/6/0 | 0/0/0 | 0/N/A | 0 | 174.2760 | DEFAULT/replay 均值为 0.7865/0.6829，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.5534 [0.2000, 0.9643] / 0 | 0.4191 [0.0000, 0.9250] | -0.1343 | 2/8/2 | 4/3/0 | 4/64.93% | 0 | -383.0972 | 4/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.6952 [0.0000, 0.9822] / 0 | 0.6076 [0.0000, 0.9368] | -0.0876 | 5/6/1 | 1/0/0 | 1/100.00% | 0 | 120.3542 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| find_days_till_holiday_wifi_off_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.7004 [0.0000, 0.9690] / 0 | 0.5916 [0.0000, 0.9244] | -0.1089 | 5/6/1 | 0/0/0 | 0/N/A | 0 | 144.1151 | DEFAULT/replay 均值为 0.7004/0.5916，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency | 0.8285 [0.5086, 0.9496] / 0 | 0.7212 [0.3688, 0.9446] | -0.1073 | 6/6/0 | 2/2/0 | 2/63.48% | 0 | -1073.9509 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_10_distraction_tools | 0.8031 [0.5127, 0.9546] / 0 | 0.6490 [0.1632, 0.9446] | -0.1540 | 5/7/0 | 0/0/0 | 0/N/A | 0 | -249.7247 | DEFAULT/replay 均值为 0.8031/0.6490，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_3_distraction_tools | 0.8334 [0.5227, 0.9550] / 0 | 0.7122 [0.1750, 0.9446] | -0.1212 | 6/6/0 | 0/0/0 | 0/N/A | 0 | -368.1366 | DEFAULT/replay 均值为 0.8334/0.7122，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_3_distraction_tools_arg_description_scrambled | 0.7227 [0.3162, 0.9350] / 0 | 0.5090 [0.0000, 0.9446] | -0.2137 | 4/7/1 | 2/2/0 | 2/79.59% | 0 | -167.0552 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_3_distraction_tools_arg_type_scrambled | 0.7652 [0.3063, 0.9634] / 0 | 0.6459 [0.1750, 0.9446] | -0.1193 | 6/6/0 | 2/1/0 | 2/88.71% | 0 | -804.5629 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_3_distraction_tools_tool_description_scrambled | 0.8504 [0.7101, 0.9731] / 0 | 0.7421 [0.5391, 0.9446] | -0.1083 | 6/6/0 | 0/0/0 | 0/N/A | 0 | -532.6506 | DEFAULT/replay 均值为 0.8504/0.7421，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_3_distraction_tools_tool_name_scrambled | 0.8549 [0.7474, 0.9526] / 0 | 0.7430 [0.5325, 0.9446] | -0.1120 | 6/6/0 | 1/1/0 | 1/82.76% | 0 | -695.9668 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_alt | 0.8454 [0.7052, 0.9355] / 0 | 0.7758 [0.5492, 0.9446] | -0.0696 | 7/5/0 | 1/1/0 | 1/26.00% | 0 | -2895.1297 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_alt_10_distraction_tools | 0.8061 [0.7122, 0.9396] / 0 | 0.6260 [0.1794, 0.9446] | -0.1801 | 4/8/0 | 2/2/0 | 2/32.17% | 0 | -2354.3516 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_alt_3_distraction_tools | 0.8354 [0.7419, 0.9325] / 0 | 0.7443 [0.5192, 0.9446] | -0.0910 | 6/6/0 | 0/0/0 | 0/N/A | 0 | -423.0066 | DEFAULT/replay 均值为 0.8354/0.7443，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_alt_3_distraction_tools_arg_description_scrambled | 0.7665 [0.5221, 0.9587] / 0 | 0.5386 [0.1427, 0.9446] | -0.2278 | 4/8/0 | 1/1/0 | 1/36.23% | 0 | -513.8602 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_alt_3_distraction_tools_arg_type_scrambled | 0.7923 [0.5159, 0.9317] / 0 | 0.6971 [0.3205, 0.9446] | -0.0952 | 6/6/0 | 2/0/0 | 2/100.00% | 0 | -672.6658 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_alt_3_distraction_tools_tool_description_scrambled | 0.8153 [0.7190, 0.9272] / 0 | 0.6827 [0.1794, 0.9446] | -0.1326 | 5/7/0 | 0/0/0 | 0/N/A | 0 | -266.8890 | DEFAULT/replay 均值为 0.8153/0.6827，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_alt_3_distraction_tools_tool_name_scrambled | 0.8482 [0.7317, 0.9387] / 0 | 0.7744 [0.5325, 0.9446] | -0.0737 | 7/5/0 | 0/0/0 | 0/N/A | 0 | -312.5264 | DEFAULT/replay 均值为 0.8482/0.7744，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn | 0.9249 [0.5462, 0.9968] / 0 | 0.8401 [0.3528, 0.9408] | -0.0849 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 167.6710 | DEFAULT/replay 均值为 0.9249/0.8401，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_10_distraction_tools | 0.9045 [0.7200, 0.9968] / 0 | 0.7737 [0.1798, 0.9418] | -0.1308 | 8/4/0 | 0/0/0 | 0/N/A | 0 | 87.3657 | DEFAULT/replay 均值为 0.9045/0.7737，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_3_distraction_tools | 0.9450 [0.7512, 0.9968] / 0 | 0.8425 [0.5306, 0.9418] | -0.1025 | 10/2/0 | 0/0/0 | 0/N/A | 0 | 148.5198 | DEFAULT/replay 均值为 0.9450/0.8425，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_3_distraction_tools_arg_description_scrambled | 0.9824 [0.9671, 0.9968] / 0 | 0.8846 [0.8225, 0.9395] | -0.0978 | 12/0/0 | 0/0/0 | 0/N/A | 0 | 158.8125 | DEFAULT/replay 均值为 0.9824/0.8846，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_3_distraction_tools_arg_type_scrambled | 0.8827 [0.2954, 0.9968] / 0 | 0.7895 [0.1798, 0.9378] | -0.0932 | 10/2/0 | 1/1/0 | 1/74.07% | 0 | 132.7433 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_3_distraction_tools_tool_description_scrambled | 0.9400 [0.7512, 0.9968] / 0 | 0.8281 [0.5390, 0.9418] | -0.1119 | 9/3/0 | 0/0/0 | 0/N/A | 0 | 144.1272 | DEFAULT/replay 均值为 0.9400/0.8281，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_3_distraction_tools_tool_name_scrambled | 0.9886 [0.9571, 1.0000] / 1 | 0.8984 [0.7481, 0.9418] | -0.0902 | 11/1/0 | 0/0/0 | 0/N/A | 0 | 176.9401 | DEFAULT/replay 均值为 0.9886/0.8984，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |
| modify_contact_with_message_recency_multiple_user_turn_alt | 0.8046 [0.5376, 0.9695] / 0 | 0.6839 [0.3528, 0.9437] | -0.1207 | 5/7/0 | 2/2/0 | 2/60.46% | 0 | -2149.6552 | 2/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_10_distraction_tools | 0.7937 [0.5091, 0.9551] / 0 | 0.6169 [0.1798, 0.9437] | -0.1768 | 6/6/0 | 3/3/0 | 3/50.71% | 0 | -411.0675 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_3_distraction_tools | 0.8367 [0.5345, 0.9747] / 0 | 0.5934 [0.1798, 0.9370] | -0.2433 | 3/9/0 | 3/3/0 | 3/58.28% | 0 | -282.8433 | 3/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_3_distraction_tools_arg_description_scrambled | 0.7319 [0.5122, 0.9245] / 0 | 0.5253 [0.1798, 0.9437] | -0.2066 | 3/9/0 | 1/1/0 | 1/44.90% | 0 | -156.7530 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_3_distraction_tools_arg_type_scrambled | 0.7596 [0.4961, 0.9695] / 0 | 0.6306 [0.1755, 0.9437] | -0.1290 | 6/6/0 | 1/1/0 | 1/82.93% | 0 | -134.3506 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_3_distraction_tools_tool_description_scrambled | 0.7932 [0.4936, 0.9720] / 0 | 0.6484 [0.1798, 0.9437] | -0.1448 | 6/6/0 | 1/1/0 | 1/46.81% | 0 | -134.7840 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| modify_contact_with_message_recency_multiple_user_turn_alt_3_distraction_tools_tool_name_scrambled | 0.7632 [0.5011, 0.9607] / 0 | 0.6191 [0.1733, 0.9424] | -0.1441 | 4/8/0 | 1/0/0 | 1/100.00% | 0 | -26.7317 | 1/12 条因执行不力被 virtual stop；需结合 progress 与后缀工具判断是否停得合理。 |
| update_contact_relationship_with_relationship_twice_multiple_user_turn | 0.8438 [0.7658, 0.8839] / 0 | 0.4118 [0.3570, 0.9255] | -0.4320 | 1/11/0 | 0/0/0 | 0/N/A | 0 | 105.5279 | DEFAULT/replay 均值为 0.8438/0.4118，未见集中 stop 或 minefield；差异主要来自阶段质量加权。 |

## 8. 限制、改进建议与最终判断

### 改进建议

1. 在 replay 截断终态上重新运行同一 ToolSandbox native scorer，输出 `replay_native_score`；阶段 Judge 分数和 structural coverage 单列，禁止互相替代。
2. 重新生成 `metrics.json.repeat_rank_consistency`，按方案输出方法内 tau-b、exact order、top-1、rank displacement 及 invalid pairs。
3. 将 `full + fail stage + policy stop` 明确拆成多标签结果，overall score 不再承担“做完/质量/停止合理性”三种语义。
4. 为 stop policy 增加误停审计：对 high-native-score、早进度 stop 和关键后缀工具存在的样本做强制复核，并单报 false-stop proxy。
5. 记录 adaptation 的实际调用次数、共享缓存与时间/token；Agent 轨迹也应保留 token，才能计算全流程 token 与 overhead share。
6. 对工具异常区分预期环境限制、Agent 参数错误与评估流程失败；当前工具异常是 Agent 轨迹证据，不应自动上升为系统故障。

### 最终判断

- **运行流程**：最终矩阵完整、JSON 可解析、路径与配对可复核；历史日志错误已被后续完整产物覆盖。
- **结果可信度**：作为“这次两套评估器各自输出了什么”的记录是可信的；作为 replay 正确性优于 DEFAULT 的证据不足。
- **Agent 行为与评估合理性**：多数样本可从工具/状态/stage evidence 回溯，但存在 full+fail、早停与高/低 native score 反向不一致，需要人工/黄金标签校准。
- **DEFAULT/replay 不一致原因**：主要是 native continuous similarity、milestone structural coverage、Judge quality penalty、minefield 与 virtual stop 的口径差异；少量还涉及工具异常与后缀恢复。
- **效率**：全量总时长低约 6.64%，但 64.47% 配对更慢且中位数差值为正；节省由少数长轨迹截断贡献，同时 adaptation 成本缺失，因此不能宣称普遍或已实现的在线效率提升。
- **优劣结论**：replay 的模型区分度更高、质量证据更细；但平均分下移、结构/质量矛盾、stop 合理性与成本均存在问题。当前不应宣布 replay 整体优于 DEFAULT。
