# 实验结果分析约束与标准流程

## 1. 目的与适用范围

本文规定 DynSTEER 实验结果的统一分析流程，适用于：

- `results/exp/<experiment_id>/` 下的汇总指标、索引和逐 case 评估结果；
- `runs/exp/<experiment_id>/` 下的 Agent/User/ToolSandbox 运行轨迹；
- `data/experiments/<experiment_id>.json` 下的实验配置；
- `logs/` 下与该实验对应的结构化运行日志；
- DEFAULT、`DYNSTEER_REPLAY` 及其他同一实验矩阵中的评估方法。

本文既约束分析步骤，也约束结论表达。它不改变 benchmark、Agent、User Simulator、scorer、Judge 或 stop policy 的行为，不允许为了得到更好看的统计结果而修改原始实验产物。

## 2. 总体原则

### 2.1 先核查证据，再解释分数

分析顺序必须是：

1. 确认实验配置和矩阵；
2. 确认结果、运行轨迹和日志完整；
3. 确认逐 case 评估口径；
4. 对照 Agent 执行证据和评估证据；
5. 最后才汇总模型、方法和重复实验指标。

不能从 `scores.json` 的单个均值直接推断 Agent 是否完成任务，也不能从排名变化直接推断评估方法优劣。

### 2.2 原始证据不可静默修改

- 分析只能读取和派生统计，不得覆盖 `results/`、`runs/` 或原始配置中的记录。
- 缺失、解析失败、字段类型错误和索引断链必须显式报告，不能补 0、补成功、删除异常样本后继续计算。
- 若需要生成清洗数据，必须写入独立临时或分析目录，并保留原始记录与清洗规则。
- 报告必须列出实验 ID、配置路径、结果路径、运行路径、日志范围和分析时间。

### 2.3 评估口径必须分层

每条结果至少区分以下概念：

- **native task success**：benchmark 原生二元结果，例如 ToolSandbox 的 `resolved`；
- **native score**：原生 milestone/minefield 等结构化评分；
- **structural completion**：例如 replay 的 `milestone_coverage=full`；
- **quality score**：阶段 Judge 对 progress、tool quality、efficiency、state consistency 等维度的评分；
- **policy stop**：DYNSTEER 因质量低、无进展、前置 milestone 缺失或 minefield 触发而停止；
- **运行完成**：trajectory/report/summary 是否正常写出。

这些字段不可在没有明确映射规则的情况下合并成同一个“成功率”。尤其不能默认把 DEFAULT 的 `resolved=true` 与 replay 的 `milestone_coverage=full` 当作同一标签。

### 2.4 方法内稳定性优先于跨方法排名相似度

- 不同评估方法使用不同评分语义，DEFAULT 与 replay 的模型排名不同是可预期现象。
- `rank_tau` 或其他 DEFAULT↔REPLAY 排名相关性只能作为参考，不得作为方法优劣的主要判据。
- 衡量重复实验可靠性时，应读取 `metrics.json` 中方案 `2026-08-06-multi-repeat-model-rank-consistency-plan.md` 定义的 `repeat_rank_consistency.<method>.<benchmark>` 字段，分别分析每个方法自身跨 repeat 的模型排名一致性；分析阶段不得重新发明或混用另一套排名判据。
- 重点读取 `mean_pairwise_kendall_tau`、`exact_order_agreement_rate`、`top1_agreement_rate`、`mean_pairwise_absolute_rank_displacement`、`repeat_pair_count` 和 `valid_pair_count`，并同时查看 `repeat_rankings` 与 `pairwise` 明细定位排名翻转。

## 3. 标准分析流程

### Step 0：建立实验分析清单

从实验配置读取并记录：

- `experiment_id`；
- benchmark 名称和数据根目录；
- model 列表及稳定的 `model_id`；
- method 列表；
- `repeats`、case 列表和 threshold profile；
- 是否使用 replay、Judge、动态路由、virtual stop 或缓存。

计算预期矩阵大小：

```text
expected_case_results = benchmark_count
                         × model_count
                         × method_count
                         × repeat_count
```

若配置包含多个 benchmark，必须按 benchmark 分开核查，不能直接混合均值掩盖某个 benchmark 的缺失。

### Step 1：核查结果和运行产物完整性

至少执行以下检查：

1. `index.json` 的 `experiment_id` 与配置一致；
2. 索引 case 数与预期矩阵一致；
3. 每个 `(benchmark, model_id, method, repeat_index, case_id)` 唯一；
4. 索引引用的 `raw_run_dir`、`result_dir`、`report_path`、`summary_path`、`trajectory_path` 全部存在；
5. `results/` 与 `runs/` 中所有 JSON 可解析，编码正确；
6. DEFAULT 与 replay 的配对键完整，不能用不同 case 或不同 repeat 进行配对；
7. summary、report、index 的 score 和关键元数据一致；
8. `started_at`、`finished_at`、`elapsed_seconds` 的差值合理；
9. 记录缺失文件、重复键、解析错误和时间异常的数量及样例。

验收要求：任何完整性检查失败都必须在报告的“数据质量”章节单独列出；不能只在脚本输出中出现。

### Step 2：统一分析数据模型

逐 case 的主键必须为：

```text
(benchmark, model_id, method, repeat_index, case_id)
```

建议将以下字段规范化到分析表：

| 类别 | 字段示例 |
|---|---|
| 身份 | benchmark、experiment_id、model_id、method、repeat_index、case_id |
| 分数 | score、default_score、dynsteer_score、native_score、overall_score |
| 结果 | resolved、milestone_coverage、first_failure_stage_id |
| replay 控制 | virtual_stop_triggered、virtual_stop_code、trajectory_changed、recovered_after_virtual_stop、final_completion |
| 运行 | step_count、raw_step_count、snapshot_count、tool_call_count、elapsed_seconds、effective_elapsed_seconds |
| Judge | llm_call_count、llm_failed_call_count、llm_total_tokens、cache_hit_count |
| 证据路径 | report_path、summary_path、trajectory_path |

所有派生指标必须注明输入字段和判据。例如 `replay_full = (milestone_coverage == "full")`，不能只写“replay 成功”。

### Step 3：核查运行记录和 Agent 执行情况

#### 3.1 轨迹结构

逐 method 检查：

- steps、snapshots、execution timing 是否存在；
- tool_call 与 tool_result 是否按 correlation id/函数名配对；
- replay 截断造成的未完成后缀是否被标记，而不是误报为环境丢结果；
- final state 是否存在且可用于 scorer；
- trajectory 是否与 DEFAULT source trajectory 相同或发生了预期截断。

#### 3.2 工具和环境异常

将异常分为两类：

- **任务级 Agent 异常**：错误参数、缺搜索条件、网络关闭、权限限制、工具不存在等。这些是 Agent 执行证据，应与 case 目标一起解释。
- **系统/评估流程异常**：JSON 产物损坏、索引断链、Judge 调用失败、未捕获异常导致 case 无报告、时间戳倒置等。这些属于流程故障，必须单独统计。

不能把所有 `tool_result.exception` 都归为评估流程失败，也不能把系统异常当成模型能力差。

#### 3.3 replay 特有字段

至少汇总：

- full/partial/none coverage；
- virtual stop 次数和 stop code 分布；
- trajectory_changed、step_ratio、snapshot_ratio；
- virtual stop 后是否 recovered；
- Judge 调用、失败调用、tokens、缓存命中；
- 是否出现“full 但 stage fail”或“结构 scorer 与 Judge 证据冲突”。

### Step 4：逐 scenario/case 分析

对每个 case 至少输出一行对照表，按 12 个配对样本（4 model × 3 repeat）或实际矩阵汇总：

- DEFAULT native score 均值、范围、resolved 计数；
- replay overall score 均值、范围、structural full 计数；
- score delta（replay−DEFAULT）；
- replay coverage full/partial/none；
- virtual stop / trajectory changed / recovery 计数；
- 主要异常工具、first failure stage 和 minefield 情况；
- 一句有证据支持的 case-level 判断。

case-level 判断必须回答：

1. Agent 是否实际调用了要求的工具、参数是否正确；
2. 最终状态是否满足 milestone/guardrail/minefield；
3. DEFAULT 和 replay 是否观察了同一个 prefix 或同一最终轨迹；
4. 不一致是 strict native threshold、质量惩罚、提前停止、minefield，还是证据/实现问题；
5. replay 的结论是否有独立证据支持，还是仅由 Judge 分数推断。

### Step 5：方法级评估指标

#### 5.0 指标优先级

结果分析必须按以下优先级组织结论，而不是按 JSON 字段出现顺序组织：

1. **模型区分度**（核心指标）；
2. **效率和成本**；
3. **任务完成和质量**；
4. **同方法跨 repeat 的模型排名一致性** 与 **重复实验分数/成功率稳定性**（同等次要优先级）；
5. **跨方法 `rank_tau`**（仅参考指标）。

核心判断应首先回答模型区分度是否有效、是否由普遍 case 差异产生、是否伴随分数压缩或 Judge 偏差；之后再结合成本效率和任务/质量有效性。排名一致性或跨方法排序相似度都不能替代区分度和正确性分析。

#### 5.1 任务完成和质量

分别报告：

- native success rate；
- native score mean/median/std/P90/P95；
- structural completion rate；
- quality score mean/median/std；
- policy stop rate、minefield rate、partial/none rate；
- DEFAULT↔replay 的 success consistency，但必须在表头注明两侧的实际判据。

不得只报告一个混合 success rate。

#### 5.2 同方法跨 repeat 的排名一致性

该指标由方案 `2026-08-06-multi-repeat-model-rank-consistency-plan.md` 生成，分析阶段必须从 `metrics.json` 的：

```text
repeat_rank_consistency.<method>.<benchmark>
```

读取 `repeat_rankings`、`pairwise` 及汇总字段，不应使用 `scores.json` 重新推导出另一套 repeat 排名。重点分析：

- tie-aware Kendall tau-b；
- 完整排序一致率；
- top-1 一致率；
- 平均绝对名次位移；
- 每个模型的平均名次和名次标准差。

对应字段为 `mean_pairwise_kendall_tau`、`exact_order_agreement_rate`、`top1_agreement_rate`、`mean_pairwise_absolute_rank_displacement`、`repeat_pair_count` 和 `valid_pair_count`。必须同时核对 `repeat_count`、`repeat_rankings`、`pairwise` 和 `invalid_pairs`；repeat 少于 3 或有效 pair 不足时必须明确说明统计证据有限。

#### 5.3 跨方法 rank_tau

`DEFAULT` 与 replay 的 rank_tau 仅作为参考信息，用于说明两种评分体系的排序关联，不得用于直接判定哪种方法更好，也不得把 tau 低解释为 replay 错误。

#### 5.4 模型区分度（核心指标）

报告 Discriminability Score 时必须包括：

- epsilon 网格（至少 0.01、0.02、0.03、0.04、0.05）；
- mean score、population stddev；
- 显著模型对数量/比例；
- 每个模型对的 absolute difference 和 significant 标记；
- 按 case 的分散度（均值、P90、零分散 case 数），避免少数 case 拉高总 DS；
- unique score 数或分数频数，识别 Judge 模板化和分数压缩。

DS 提升只能说明模型平均分被拉开，不等于准确性、校准度或任务完成率提升。

#### 5.5 效率和成本

DEFAULT 与 replay 必须分方法报告：

- wall-clock elapsed；
- replay default-prefix execution time；
- replay Judge/evaluator time；
- replay effective time = prefix execution + evaluator time；
- agent steps、raw steps、tool calls；
- Judge calls、失败 calls、prompt/completion/total tokens；
- 缓存命中、重复 prompt 避免和实际费用（若可用）。

不能用 replay evaluator 的短 wall-clock 直接宣称 replay 更高效，因为它通常复用了 DEFAULT trajectory。

#### 5.6 重复稳定性

对每个 model/method 报告：

- repeat score 均值和总体标准差；
- repeat success/coverage rate 及其标准差；
- 每个 repeat 的分数/coverage 摘要；
- 方案输出的同方法跨 repeat 排名指标（详见 `repeat_rank_consistency`，不得在此处重复计算）；
- 缺失 repeat 和有效 pair 数。

不能把多个 repeat 的 case 简单拼接后伪装成独立重复，也不能把缺失 repeat 补成失败。

### Step 6：异常归因与合理性判断

将发现分为以下等级：

- **P0 流程阻断**：实验中断、主键重复、关键报告缺失、结果不可解析、评估器异常退出；
- **P1 评估可信度问题**：成功判据不一致、结构 scorer 与 Judge 证据矛盾、minefield 未传播、virtual stop 与 final completion 冲突；
- **P2 结果解释问题**：分数模板化、模型排序不稳定、成本口径混合、日志无法区分 Agent 异常与系统异常；
- **观察项**：单个 Agent 工具误用、场景预期的网络/权限错误、正常 retry。

只有 P0/P1 会直接降低结果可用性；P2 和观察项应在报告中解释，但不能未经证据扩大为系统故障。

### Step 7：报告和结论验收

报告至少包含：

1. 分析范围、配置和数据路径；
2. 实验矩阵与完整性检查；
3. scenario/case 对照表；
4. Agent 执行、异常和日志核查；
5. DEFAULT/replay 判据差异；
6. 任务完成、质量、区分度、重复稳定性、效率和成本指标；
7. 关键异常样例及证据路径；
8. replay 是否更合理的分场景判断；
9. 限制、改进建议和最终结论。

最终结论必须分别回答：

- 运行流程是否正常；
- 结果文件是否可信可复核；
- 各 case 的 Agent 行为和评估是否合理；
- DEFAULT/replay 不一致的原因；
- replay 在哪些指标上更好、哪些指标上更差；
- 是否有足够证据宣布 replay 优于 DEFAULT。

## 4. 强制约束清单

1. **不得混淆成功标签**：任何 success consistency 表格都必须列出两种方法各自的成功判据。
2. **不得以跨方法 rank_tau 作为主判据**：方法内 repeat 一致性必须从 `metrics.json.repeat_rank_consistency.<method>.<benchmark>` 读取并单独分析，不得用跨方法字段替代。
3. **不得只看均值**：至少同时提供方差/分位数、逐 repeat 数据和 case-level 明细。
4. **不得只看最终分数**：必须回到 trajectory、snapshot、tool result、stage report 和 minefield/guardrail 证据。
5. **不得静默移除异常样本**：异常应分类、计数、给出样例和路径。
6. **不得把 Agent 工具异常自动当作系统故障**：先检查是否符合 case 的错误恢复、权限或网络设定。
7. **不得把 replay 的复用时间当作完整实验时间**：同时报告 prefix、Judge 和 effective 三种口径。
8. **不得把 Judge 分数当作 native ground truth**：Judge 需要通过 native scorer 或人工黄金集校准。
9. **不得在重复统计中补齐缺失 repeat**：缺失必须保留为缺失并降低有效 pair 数。
10. **不得暴露敏感配置**：报告只能记录模型 ID、endpoint 类型等必要元数据，不能复制 API key、token 或完整 secret 配置。
11. **不得覆盖原始结果和约束文档**：分析文档和派生数据写入指定 docs/analysis 或独立目录；仅在用户明确要求时修改已有文档。
12. **不得仅凭排名稳定性宣称正确**：稳定地得到同一排名，可能只是评分模板、Judge 偏差或 case 分布造成的。

## 5. 推荐的数据质量检查伪代码

```text
load config
load index / metrics / scores
assert experiment_id matches
assert unique(primary_key)
assert actual_count == expected_count
assert all indexed paths exist
parse all summary/report/trajectory JSON
validate summary/report/index fields
validate timestamps and runtime counters

for each paired case:
    collect native score and native success
    collect replay score, coverage, stop and recovery
    compare only with explicitly declared predicates
    inspect trajectory and report when predicates disagree

for each method and benchmark:
    aggregate score/quality/completion/cost metrics
    read repeat_rank_consistency.<method>.<benchmark>
    analyze its pairwise tau/exact/top1/displacement fields
    report missing and invalid pairs

write report with evidence paths and limitations
re-read report and verify key totals against raw data
```

## 6. 最低验收标准

分析流程只有在以下条件全部满足时，才可标记为完成：

- 实验矩阵、结果文件和运行轨迹完整性已核查；
- 所有 success、coverage、quality、stop 字段的判据已写清；
- 逐 case 对照表覆盖全部 scenario/case；
- DEFAULT 与 replay 的方法内 repeat 排名一致性已单独报告；
- DS、分数方差、分数压缩、效率和成本至少有一项可审计明细；
- 关键异常有分类、数量、样例路径和影响判断；
- 报告结论区分“数据/流程正常”“评估口径一致”“方法优劣”三个层次；
- 报告不包含敏感凭据，且不会覆盖原始实验产物。
