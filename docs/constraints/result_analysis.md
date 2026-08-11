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
| replay 控制 | virtual_stop_triggered、virtual_stop_code、trajectory_changed、recovered_after_virtual_stop、final_completion、agent_underperformance_stop |
| 运行 | step_count、raw_step_count、snapshot_count、tool_call_count、stop_step、default_total_step、progress、elapsed_seconds、effective_elapsed_seconds |
| 成本 | agent_time、adaptation_time、evaluation_time、agent_tokens、adaptation_tokens、evaluation_tokens、pipeline_time、pipeline_tokens |
| Judge | llm_call_count、llm_failed_call_count、llm_prompt_tokens、llm_completion_tokens、llm_total_tokens、cache_hit_count |
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

#### 3.4 因 Agent 执行不力而提前终止

对每个 DynSTEER 系列方法，必须识别哪些配对样本因 Agent 执行质量不足、持续无进展、关键前置 milestone 未完成等 stop policy 判据而实际提前终止，并与 minefield 触发、正常完成、系统/评估异常和仅进行 virtual stop 模拟的样本分开。`agent_underperformance_stop` 必须由 stop code、stage report、trajectory 截断位置等证据共同判定，不能仅凭低分或轨迹较短反推。

提前终止进度必须与同一 `(benchmark, model_id, repeat_index, case_id)` 的 DEFAULT 完整轨迹配对，并按下式计算：

```text
stop_step = DynSTEER 实际执行到提前终止点的步骤数
default_total_step = 配对 DEFAULT 状态下 Agent 完整轨迹的步骤总数
progress = stop_step / default_total_step
```

- `stop_step` 和 `default_total_step` 必须使用相同的 step 过滤、编号和计数口径；`stop_step` 是已执行步骤数量，不得直接混用从 0 开始的数组索引；
- 必须逐配对样本报告 `stop_step`、`default_total_step`、`progress`、stop code 和证据路径，并按 case、model、method、benchmark 汇总提前终止数量、比例以及 progress 的 mean/median/P10/P90；
- 若 DEFAULT 轨迹缺失、未完整执行、配对键不一致或 `default_total_step = 0`，则 `progress` 必须记为缺失并说明原因，不得补 0、改用其他 repeat/case 或使用全局平均步数；
- 若 `stop_step > default_total_step`，必须作为计数口径或配对异常核查，不能静默截断为 1；
- 分析必须说明提前终止是否确实阻止了低质量后续执行，以及停止过早、停止过晚或误停的代表性 case，不能把“发生提前终止”等同于“提前终止合理”。

### Step 4：逐 scenario/case 分析

对每个 case 至少输出一行对照表，按 12 个配对样本（4 model × 3 repeat）或实际矩阵汇总：

- DEFAULT native score 均值、范围、resolved 计数；
- replay overall score 均值、范围、structural full 计数；
- score delta（replay−DEFAULT）；
- replay coverage full/partial/none；
- virtual stop / trajectory changed / recovery 计数；
- 因 Agent 执行不力而提前终止的计数，以及每个提前终止配对样本的 `stop_step/default_total_step=progress`；
- DEFAULT 与各 DynSTEER 方法的 Agent 执行、case 适配、评估及全流程时间/token，以及逐配对样本差值；
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
- 跨方法比较表必须以 epsilon 为主分组、method 为次分组，或采用“一行一个 epsilon、不同方法并列”的宽表；不得先连续列完某个方法的全部 epsilon，再列另一个方法，导致同 epsilon 难以直接比较；
- mean score、population stddev；
- 显著模型对数量/比例；
- 每个模型对的 absolute difference 和 significant 标记；
- 按 case 的分散度（均值、P90、零分散 case 数），避免少数 case 拉高总 DS；
- unique score 数或分数频数，识别 Judge 模板化和分数压缩。

DS 提升只能说明模型平均分被拉开，不等于准确性、校准度或任务完成率提升。

#### 5.5 效率和成本

成本必须先拆分来源，再比较 DEFAULT 与各 DynSTEER 系列方法。至少区分：

```text
agent_cost      = Agent 执行轨迹产生的时间/token
adaptation_cost = 为 case 生成 milestone、stage_goal、minefield 等适配产物的时间/token
evaluation_cost = DynSTEER 在线评估、Judge、stop policy 等产生的时间/token
pipeline_cost   = agent_cost + adaptation_cost + evaluation_cost
```

DEFAULT 若存在原生 scorer/evaluator 成本，应计入 DEFAULT 的 `evaluation_cost`；不存在的 DynSTEER 专属适配成本记为 0。所有时间和 token 均应在来源可区分时采用同一边界计算，不能将并行任务的 wall-clock 与各组件耗时之和混用。

逐 `(benchmark, model_id, repeat_index, case_id)` 配对样本至少报告：

- Agent 执行时间/token、case 适配时间/token、评估时间/token和全流程时间/token；
- DynSTEER 相对 DEFAULT 的专属额外开销：`adaptation_cost + evaluation_cost - default_evaluation_cost`；
- DynSTEER 相对 DEFAULT 的全流程差值：`dynsteer_pipeline_cost - default_pipeline_cost`，时间与 token 分别计算；差值允许为负，提前终止节省的 Agent 执行成本不能被写成额外开销；
- agent steps、raw steps、tool calls，Judge 调用/失败调用及 prompt/completion/total tokens，缓存命中、重复 prompt 避免和实际费用（若可用）；
- 成本字段的原始来源、缺失状态，以及复用 DEFAULT 轨迹时 prefix execution 成本是实测、由时间戳重建还是估算。

聚合时必须分别覆盖以下样本范围：

1. **实验全量数据**：报告 DEFAULT 与 DynSTEER 的各组件总量、全流程总量、总量差值，同时给出逐样本差值的 mean/median/P90/P95；
2. **因 Agent 执行不力而提前终止的配对子集**：使用 3.4 的同一判据和配对集合，仅与这些样本各自配对的 DEFAULT 行比较，报告相同的时间/token 指标，并结合 `progress` 解释节省的执行成本与新增评估成本；
3. **逐 case 汇总**：按 case_id 报告配对样本数、提前终止数、各成本总量/均值及相对 DEFAULT 的差值，不能只给方法级总均值。

##### 提前终止节省量分析（强制步骤）

对“阶段得分不足或持续无进展而中途终止”的效率分析，必须执行以下步骤：

1. **先固定停止集合**：严格低分停止仅指阶段得分低于失败阈值触发的 stop code（例如 `evaluation_policy_stop`）；广义执行不力停止可进一步包含 `milestone_no_progress:*`、`ready_frontier_no_progress:*` 等持续无进展 stop code。两种集合必须分别计数，不得与 minefield、正常完成、系统/评估异常或未造成轨迹截断的 virtual stop 混合。
2. **区分两种 case 数**：同时报告完整主键 `(benchmark, model_id, method, repeat_index, case_id)` 的配对样本数，以及去重后的 `case_id` 数。不得把 model×repeat 配对样本数简写成互不重复的场景数。
3. **只使用同键 DEFAULT 配对**：每条停止样本必须与相同 `(benchmark, model_id, repeat_index, case_id)` 的 DEFAULT 完整轨迹配对；DEFAULT 缺失、未完整运行或计数口径不一致时，该条节省量记为缺失并说明原因。
4. **计算进度与未执行比例**：

```text
progress = stop_step / default_total_step
saved_progress = 1 - progress
```

`saved_progress` 表示因停止而未继续执行的轨迹比例。必须报告 `progress` 和 `saved_progress` 的 mean/median/P10/P90；不得把平均 `progress` 的补数冒充逐样本比例之外的其他口径。

5. **分别计算毛节省与净节省**：

```text
gross_execution_time_saved = default_full_execution_time
                             - dynsteer_prefix_execution_time

net_pipeline_time_saved = default_pipeline_time
                          - dynsteer_pipeline_time
```

`gross_execution_time_saved` 只衡量未执行后缀带来的轨迹执行时间减少；`net_pipeline_time_saved` 必须计入 prefix execution、case adaptation、DynSTEER evaluation/Judge/stop policy 和 DEFAULT 原生 evaluator 的同边界成本。节省值为正表示 DynSTEER 更快，为负表示 DynSTEER 更慢；若同时报告 `dynsteer_pipeline_time - default_pipeline_time`，必须明确其符号方向与节省量相反。
6. **报告总量、均值和分布**：严格低分停止集合与广义执行不力停止集合均须报告时间节省的 total、mean、median、P90/P95，并给出净节省为正、为零和为负的样本数/比例。若均值节省但中位数不节省，必须明确说明收益由少数长轨迹贡献，不能用均值代表典型 case。
7. **说明 replay 与真实在线停止的差异**：若停止来自 `DYNSTEER_REPLAY` 对 DEFAULT source trajectory 的反事实截断，必须称为 virtual/counterfactual saving，不得写成已实现的线上 Agent 成本节省。adaptation、Agent token 或其他成本缺失时，净节省必须标注为当前可比范围，缺失项不得补 0。

case 适配若在多个 model、method 或 repeat 间共享/缓存，实验总量必须按实际调用次数计费，不能给每个配对样本重复记账。逐样本或逐 case 表中应单列共享成本及其分摊规则；同时优先报告“实际实验总成本”和“按明确规则分摊后的配对成本”，不得将分摊值伪装成实际调用成本。

当 Agent 执行 token 可统计且三类 token 的边界可比时，对实验全量数据和提前终止子集分别报告以下占 DynSTEER 全流程 token 的比例：

```text
adaptation_token_share = adaptation_tokens / (agent_tokens + adaptation_tokens + evaluation_tokens)
evaluation_token_share = evaluation_tokens / (agent_tokens + adaptation_tokens + evaluation_tokens)
combined_overhead_token_share = (adaptation_tokens + evaluation_tokens)
                                / (agent_tokens + adaptation_tokens + evaluation_tokens)
```

这三项分别对应仅 case 适配生成、仅评估、适配与评估合计的 token 范围。比例必须使用“先求分子/分母总量再相除”的 micro 口径；可另附逐样本比例的 macro 均值，但不能以 macro 均值替代总量占比。若 Agent token、适配 token 或评估 token 无法拆分，必须明确标为不可计算并报告可用样本覆盖率，不能把缺失项当作 0。

不能用 replay evaluator 的短 wall-clock 直接宣称 replay 更高效，因为它通常复用了 DEFAULT trajectory；效率结论必须同时考虑完整 Agent 执行/截断 prefix、case 适配和评估成本。

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
6. 任务完成、质量、区分度、重复稳定性、提前终止进度、效率和成本指标；
7. 关键异常样例及证据路径；
8. replay 是否更合理的分场景判断；
9. 限制、改进建议和最终结论。

最终结论必须分别回答：

- 运行流程是否正常；
- 结果文件是否可信可复核；
- 各 case 的 Agent 行为和评估是否合理；
- DEFAULT/replay 不一致的原因；
- DynSTEER 在哪些 case 因 Agent 执行不力而提前终止、停止时的执行进度及停止是否合理；
- 全量数据和提前终止子集中，DynSTEER 相对 DEFAULT 的时间/token 额外开销与全流程差值；
- replay 在哪些指标上更好、哪些指标上更差；
- 是否有足够证据宣布 replay 优于 DEFAULT。

## 4. 强制约束清单

1. **不得混淆成功标签**：任何 success consistency 表格都必须列出两种方法各自的成功判据。
2. **不得以跨方法 rank_tau 作为主判据**：方法内 repeat 一致性必须从 `metrics.json.repeat_rank_consistency.<method>.<benchmark>` 读取并单独分析，不得用跨方法字段替代。
3. **不得只看均值**：至少同时提供方差/分位数、逐 repeat 数据和 case-level 明细。
4. **不得只看最终分数**：必须回到 trajectory、snapshot、tool result、stage report 和 minefield/guardrail 证据。
5. **不得静默移除异常样本**：异常应分类、计数、给出样例和路径。
6. **不得把 Agent 工具异常自动当作系统故障**：先检查是否符合 case 的错误恢复、权限或网络设定。
7. **不得把 replay 的复用时间当作完整实验时间**：必须拆分 Agent 执行、case 适配和评估成本，并报告包含三者的全流程成本。
8. **不得把 Judge 分数当作 native ground truth**：Judge 需要通过 native scorer 或人工黄金集校准。
9. **不得在重复统计中补齐缺失 repeat**：缺失必须保留为缺失并降低有效 pair 数。
10. **不得暴露敏感配置**：报告只能记录模型 ID、endpoint 类型等必要元数据，不能复制 API key、token 或完整 secret 配置。
11. **不得覆盖原始结果和约束文档**：分析文档和派生数据写入指定 docs/analysis 或独立目录；仅在用户明确要求时修改已有文档。
12. **不得仅凭排名稳定性宣称正确**：稳定地得到同一排名，可能只是评分模板、Judge 偏差或 case 分布造成的。
13. **不得脱离配对 DEFAULT 轨迹计算提前终止进度**：`progress` 必须使用相同 case/model/repeat 的 DEFAULT 完整步骤数作为分母，并保留不可配对和计数异常。
14. **不得混合或重复计算成本**：Agent 执行、case 适配、评估成本必须拆分；共享/缓存适配成本按实际调用统计，分摊值必须另行标注。
15. **不得只报告评估器自身成本**：全量数据和提前终止子集都必须报告包含 Agent 执行与评估的全流程时间/token、相对 DEFAULT 的差值及适配/评估 token 占比（数据可用时）。

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
    classify agent_underperformance_stop from stop/report/trajectory evidence
    if agent_underperformance_stop:
        pair with the complete DEFAULT trajectory
        compute progress = stop_step / default_total_step
    split agent/adaptation/evaluation time and tokens
    compute evaluation overhead and pipeline delta against DEFAULT
    compare only with explicitly declared predicates
    inspect trajectory and report when predicates disagree

for each method and benchmark:
    aggregate score/quality/completion/cost metrics
    aggregate full-data and early-stop-subset cost totals separately
    compute adaptation/evaluation/combined token shares when agent tokens are available
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
- 因 Agent 执行不力而提前终止的 case 已识别，并以配对 DEFAULT 完整轨迹计算和报告 `stop_step/default_total_step=progress`；
- DEFAULT 与 replay 的方法内 repeat 排名一致性已单独报告；
- 全量数据与提前终止子集均已给出逐 case 和总量级的全流程时间/token、相对 DEFAULT 差值；不可计算项已说明原因和覆盖率；Agent token 可统计时，三种适配/评估 token 占比均已报告；
- DS、分数方差、分数压缩、效率和成本至少有一项可审计明细；
- 关键异常有分类、数量、样例路径和影响判断；
- 报告结论区分“数据/流程正常”“评估口径一致”“方法优劣”三个层次；
- 报告不包含敏感凭据，且不会覆盖原始实验产物。
