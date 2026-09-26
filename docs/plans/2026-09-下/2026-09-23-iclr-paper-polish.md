# ICLR 2027 论文修改计划

## 目标
- 压缩第 3.1 节，去除含混的 public task view 术语，改为 evaluator-visible task inputs。
- 补充 milestone 候选图筛选标准说明，并在附录新增 Contract Validation 细节。
- 统一 DynSTEER-Replay 为 DynSTEER，简化同轨迹对照实验描述。
- 重构 Table 1 分数表达，采用 0--100 量纲和 `Mean ± Std` 展示。

## 修改内容
1. `sections/s3-methodology.tex`
   - 将 3.1 压缩为任务形式化、可见输入定义、双时钟说明三段紧凑内容。
   - 将 `V_public` 替换为 `V_task`，避免无必要解释的 public 概念。
   - 在 3.2 的公式后补充 SchemaCheck、Acyclic 与 BindingConsistency 的语义简介，并引用附录。
2. `appendixs/app_algorithms.tex`
   - 新增 Contract Validation 小节，说明 schema、API/类型、绑定和结构校验标准。
   - 同步算法输入中的任务输入术语。
3. `sections/s4-experiments.tex`
   - 明确 Default 与 DynSTEER 在同一 source trajectory 上评估。
   - 将 replay stop rate 改为 stop rate，并淡化不必要的 replay 后缀。
   - 将 mean/std 从 0--1 的四位小数表达改为 0--100 的两位小数表达。
   - 将 DS 判别阈值从旧量纲的 0.01 更新为等价的 1.00 分。
4. `tables/tab_main_results.tex`
   - 将 Mean Score 与 Std Dev 合并为 `Mean ± Std`。
   - 将分数乘以 100，保留两位小数。
   - 将 `Replay Stop Rate` 改为 `Stop Rate`，caption 同步说明量纲与 DS 性质。
5. 全局术语
   - 将论文叙述、图 caption、算法和 prompt 中的 public task view 统一替换为 task inputs / available task inputs。

## 附录 A. 项目中没有把握实现的模块部分
- 本次任务不涉及新增工程模块；分数类指标可由原始结果严格等比缩放，早停统计已依据实验索引与运行记录重算。

## 2026-09-23 增补：消融实验章节

- 将实验研究问题扩展为 RQ1--RQ4，并按“主实验、消融、milestone graph、早停效率”排序。
- 新增 `tables/tab_ablation_results.tex`，只呈现显著模型对数、DS、Stop Rate 和 Saved Steps。
- 新增 RQ2 消融分析，覆盖 Full、Stage-only、No dynamic judge tier、No dynamic weighting 和 No policy stop。
- 统一 judge 层级术语为 tier，方法章节公式变量由 Level 改为 Tier。
- 新增附录 `Rationale for Uncertainty-Adaptive Dimension Weighting`，整合指数再加权、加权方差和短板暴露的推导逻辑。
- 实验正文只保留消融集的 100 个分层抽样场景；主实验规模细节留在附录数据集统计中。
- 将 Saved Steps 统一定义为节省步骤总数占全部匹配 Default 完整轨迹步骤总数的百分比，并以百分比展示。
- 基于 `results/exp/toolsandbox_main` 与 `runs/exp/toolsandbox_main` 重算早停表，并移除未在论文中强调的 persistent structural-invariant 补充口径。
