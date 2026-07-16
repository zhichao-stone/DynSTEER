# 初步实验方案

## 1. Benchmark

本方案采用两个 benchmark 作为实验基底：

| Benchmark     | 数量 | 范围                                                      | milestone 标注                           | minefield 标注                          | 默认评估方法                                      | 当前 DynSTEER 支持状态                                       |
| ------------- | ---: | --------------------------------------------------------- | ---------------------------------------- | --------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------ |
| ToolSandbox   | 1032 | 15 类场景标签；含单/多工具、单/多轮、干扰工具、参数扰动等 | 有；共 2480 个，单任务 0-6 个，均值 2.40 | 有；共 208 个，单任务 0-1 个，均值 0.20 | 原生 scenario evaluation / final state matcher    | 已有 adapter、harness、milestone graph 转换、ToolSandbox 专用 scorer |
| SWE-bench Pro | 1865 | 41 个活跃维护仓库                                         | 无人工 milestone                         | 无 minefield                            | patch resolved rate，由测试验证补丁是否解决 issue | 需新增 adapter / harness / trace converter / pseudo-stage graph |

## 2. 指标

设 benchmark 为 $b$，模型为 $m \in M$，评估方法为 $e$。

$S_{m,e,b}$ 表示按该 benchmark 自身指标计算方法得到的模型最终得分，分数归一化到 $[0,1]$。

benchmark 默认方法记为 `Default`，DynSTEER 离线回放方法记为 `DynSTEER-Replay`，动态介入方法记为 `DynSTEER-Steering`。

### 2.1 主指标：模型区分度

模型区分度只保留两个指标，分别报告，不做加权、不合成为新的综合指标。

核心区分度使用模型得分间距。对任意模型对 $(i,j)$：

$$
D_{i,j,e,b}=|S_{i,e,b}-S_{j,e,b}|
$$

$$
PSEP_{e,b}=\frac{2}{|M|(|M|-1)}\sum_{i<j}D_{i,j,e,b}
$$

`PSEP` 越高，说明不同模型在该 benchmark 的最终得分上更容易被区分。

排序保真只使用 Kendall tau，作为防止 DynSTEER 通过改变模型排序来“制造区分度”的约束：

$$
\text{RankTau}_{e,b}=\text{KendallTau}(\{S_{m,e,b}\}_{m \in M}\ ,\ \{S_{m,\text{Default},b}\}_{m \in M})
$$

该公式来源于 Kendall rank correlation coefficient（Kendall, 1938, *A New Measure of Rank Correlation*, *Biometrika*）。

该公式用于衡量两组模型排序的一致程度：越接近 $1$，说明 DynSTEER 与默认方法给出的模型排序越一致；接近 $0$ 表示排序关系弱或不稳定；越接近 $-1$，说明排序越接近相反。

主结论以 `PSEP` 为核心，`RankTau` 作为约束性检查。

### 2.2 效率与成本

- **效率指标：**

  - 评估耗时：
    $$
    T^{eval}_{e,b}=\mathbb{E}_{m,c,r}[T^{eval}_{m,c,r,e,b}]
    $$

  - Agent执行步骤数

- **成本指标：** Agent执行消耗token + 评估流程Judge消耗token

## 3. 实验方案

### 3.1 主实验

主实验只对照两种方法：

- `Default`：benchmark 原生评估结果。
- `DynSTEER-Replay`：不介入 Agent 执行，只在同一条完整轨迹上做阶段式回放评估。

实验流程如下：

1. 冻结 benchmark 版本、case 集合、模型版本、prompt、temperature、随机种子和执行环境。
2. 对每个模型、case 和重复运行，先按 benchmark 默认流程执行 Agent，得到完整轨迹和默认结果。
3. 在同一条轨迹上运行 `DynSTEER-Replay`，基于完整轨迹执行阶段划分、动态评估、早停策略。
4. 在各benchmark上计算模型最终得分、`PSEP`、`RankTau`、效率指标、成本指标。

### 3.2 分实验

#### 3.2.1 动态介入实验

该实验只比较 `Default` 执行和 `DynSTEER-Steering` 执行。

DynSTEER介入Agent执行过程，在Agent执行时实时匹配milestone、划分评估阶段，开展动态评估，并基于各阶段的评估结果指导接下来Agent的执行过程：(1) 直接将评估结果注入Agent的执行prompt（估计不太有效）；(2) 额外通过一次转换（用LLM归纳？）将评估结果规整为任务指导性质的表述，如提供一段执行路径规划。

核心观察两点：

1. 任务成功率或 resolved rate 是否提升。
2. 额外耗时和成本是否可接受。

#### 3.2.2 核心消融实验

消融只保留一个最关键版本：关闭动态路由和动态权重，保留阶段式评估框架不变。

该消融用于判断 DynSTEER 的收益来自“阶段式轨迹证据”本身，还是来自动态选择 judge 与动态调整权重。消融结果只与完整 DynSTEER 比较 `PSEP`、`RankTau`、评估耗时和 judge token。

#### 3.2.3 小模型 Judge 可行性实验

该实验将 `DynSTEER-Replay` 中的 judge 替换为 4B / 7B 小模型，其余轨迹、阶段划分、评分规则和汇总方式保持与主实验一致。

小模型 judge 不参与 Agent 执行，只用于长执行轨迹的阶段判断、证据抽取和阶段得分生成。实验以主实验默认 judge 结果作为参照，比较 `PSEP`、`RankTau`、评估耗时和 judge token，判断 4B / 7B judge 是否能完成长执行轨迹评估，并在模型区分度、排序保真和成本效率上不逊色于主实验结果。

#### 3.2.4 参数敏感性分析

参数分析只保留阶段判定阈值一项。以默认阈值为中心，设置低、中、高三档，观察 `PSEP` 和 `RankTau` 是否稳定。

如果三档结果趋势一致，则正文只报告默认阈值结果；敏感性分析作为补充说明即可。

#### 3.2.5 Milestone 生成算法可靠性实验

该实验替代原先单独列出的“评估可靠性”部分，目标是验证 DynSTEER 使用的 milestone / pseudo-stage 是否能稳定表达任务过程。

ToolSandbox 使用原生 milestone 作为参照，报告生成 milestone 与原生 milestone 的匹配质量：

$$
MilestoneF1=F1(\hat{y}_{milestone}, y_{milestone})
$$

SWE-bench Pro 没有人工 milestone，因此只抽取小样本做人工审阅，判断 pseudo-stage 是否符合公开 issue、轨迹行为和最终 patch 证据。该实验只回答 milestone 生成是否可靠，不扩展成人工大规模评估。
