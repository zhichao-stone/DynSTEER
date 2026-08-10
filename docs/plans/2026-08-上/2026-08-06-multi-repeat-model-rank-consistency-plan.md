# 多 repeat 同方法模型排名一致性指标方案

## 1. 背景与目标

当前 `metrics.json` 的 `rank_tau` / `rank_tau_by_repeat` 主要用于比较 DEFAULT 与 `DYNSTEER_REPLAY` 两种方法的模型排序关系。不同评估方法使用不同评分语义，排名不同本身是自然现象，因此该指标不适合作为方法优劣的主要判断依据。

本方案新增“同一方法跨 repeat 的模型排名一致性”指标，用于回答：

> 在相同 benchmark、相同评估方法下，重复运行是否稳定地区分出相同的模型相对顺序？

方案只增加实验指标和报告输出，不改变 Agent、User Simulator、ToolSandbox scorer、DYNSTEER stop policy 或既有 DEFAULT/replay 评分。

## 2. 指标定义

### 2.1 排名样本单位

对每个 `(method, benchmark, repeat_index)`：

1. 按 `model_id` 汇总该 repeat 的全部 case score 均值；
2. 按均值从高到低排序，得到该 repeat 的模型排名；
3. 只在同一 `method` 内比较不同 repeat，不跨方法比较。

当前 ToolSandbox 实验的输入是 4 个模型、25 个 case、3 个 repeat；每个 repeat 的排名基于 25 个 case 的平均分，而不是把 75 个 case 当成 75 次重复。

### 2.2 主指标

对所有 repeat pair `(r_i, r_j)` 计算：

- `kendall_tau_b`：带并列处理的 Kendall tau-b；没有有效模型对时返回 `null`，不伪造 0；
- `exact_order_agreement`：两个 repeat 的完整模型顺序是否完全相同；
- `mean_absolute_rank_displacement`：四个模型名次绝对差的平均值。

在 benchmark/method 层汇总：

- `mean_pairwise_kendall_tau`：所有有效 repeat pair 的 tau 平均值；
- `exact_order_agreement_rate`：完整顺序一致的 pair 数 / 有效 pair 数；
- `mean_pairwise_absolute_rank_displacement`：pair 级平均名次位移；
- `top1_agreement_rate`：第一名相同的 pair 数 / 有效 pair 数，作为辅助指标；
- `repeat_count`、`repeat_pair_count`、`valid_pair_count`：明确样本覆盖，不足 repeat 时不补零。

主结论应优先使用 `mean_pairwise_kendall_tau`、`exact_order_agreement_rate` 和排名位移；`top1_agreement_rate` 只能作为补充，因为少量模型时 top-1 容易掩盖中后位交换。

### 2.3 并列、缺失与异常处理

- 排名使用平均名次（mid-rank），Kendall 使用 tau-b；不得通过模型名或输入顺序打破并列。
- 某 repeat 缺少模型、模型缺少 score 或 score 非有限值时，该 repeat pair 标为无效，并记录 `invalid_reason`。
- 至少两个模型且至少两个有效 repeat 才输出一致性数值；否则输出 `null` 和计数信息。
- 不把 `resolved`、`milestone_coverage` 或 success threshold 混入排名分数；排名分数只读取现有 `ExperimentCaseResult.score`。
- DEFAULT 和 replay 分别计算，输出中不复用现有 DEFAULT↔REPLAY 的 `rank_tau` 字段，避免语义混淆。

## 3. 输出结构

在 `results/exp/<experiment_id>/metrics.json` 增加：

```json
{
  "repeat_rank_consistency": {
    "default": {
      "toolsandbox": {
        "repeat_count": 3,
        "repeat_pair_count": 3,
        "valid_pair_count": 3,
        "repeat_rankings": {
          "0": {
            "model_scores": {
              "deepseek-v4-flash": 0.8568,
              "deepseek-v4-pro": 0.8554
            },
            "order": [
              "deepseek-v4-flash",
              "deepseek-v4-pro"
            ],
            "ranks": {
              "deepseek-v4-flash": 1.0,
              "deepseek-v4-pro": 2.0
            }
          }
        },
        "pairwise": [
          {
            "left_repeat": 0,
            "right_repeat": 1,
            "kendall_tau_b": 0.3333,
            "exact_order_agreement": false,
            "top1_agreement": true,
            "mean_absolute_rank_displacement": 1.0
          }
        ],
        "mean_pairwise_kendall_tau": -0.3333,
        "exact_order_agreement_rate": 0.0,
        "top1_agreement_rate": 0.3333,
        "mean_pairwise_absolute_rank_displacement": 1.5,
        "invalid_pairs": []
      }
    }
  }
}
```

字段约定：

- `repeat_rankings` 保存可审计的 repeat 内模型均值、顺序和名次；
- `pairwise` 保存每个 pair 的原子结果，便于定位某次 repeat 排名翻转；
- 汇总字段只对 `valid_pair_count` 聚合；
- score 使用 JSON-safe 浮点值，无法计算时显式为 `null`。

## 4. 代码落地方案

### 4.1 修改 `dynsteer/experiment/metrics.py`

新增单一职责函数，建议顺序为：

1. `repeat_model_scores(results)`：返回 `(method, benchmark, repeat) -> model -> mean_score`；复用现有 `case_score()`，不复制 score 归一化逻辑。
2. `rank_models(model_scores)`：处理并列、生成 mid-rank 和稳定展示顺序。
3. `repeat_rank_consistency(results)`：生成上述 JSON 结构并处理缺失/无效 pair。
4. 在 `write_metric_tables()` 中把 `repeat_rank_consistency(results)` 合并到 `metrics`，不改动现有 `rank_tau`、`rank_tau_by_repeat` 字段，保证旧消费者兼容。

函数输入参数必须有类型声明，空输入、非有限分数和异常 repeat 应结构化处理；中文 docstring 说明功能、输入和输出。避免新增只为转发已有函数的中转层。

### 4.2 测试文件

新增 `tests/experiment/test_metrics.py`，覆盖：

- 3 repeat、4 模型、无并列时的完整顺序、tau、exact rate 和位移；
- 完全相同顺序时 tau=1、exact rate=1、位移=0；
- 完全反转顺序时 tau=-1；
- 并列分数的 mid-rank 和 tau-b；
- 缺失模型、缺失 repeat、`None`/非有限 score 的 invalid pair 计数；
- 只有一个 repeat 或一个有效模型时输出 `null` 而不是 0；
- DEFAULT 与 replay 同时存在时分别输出，不能互相污染；
- `write_metric_tables()` 输出 JSON 可序列化且不破坏既有字段。

测试应调用项目已有 metrics 接口或构造最小 `ExperimentCaseResult`，不新建与生产代码无关的测试接口；目标覆盖率不低于项目约束要求的 80%。

### 4.3 文档与展示

- 更新 README 或实验指标文档，说明 `rank_tau`（跨方法参考）与 `repeat_rank_consistency`（同方法稳定性）的语义区别。
- 在 experiment 结果摘要中分别展示：方法内 repeat 排名稳定性、模型平均分、成功/结构覆盖、质量分和成本；不把 repeat rank consistency 合并成一个“方法总分”。
- 当前已有的 `rank_tau_by_repeat` 保留，用于审计 DEFAULT↔REPLAY 的排序关联；新指标使用独立名称，避免下游误读。

## 5. 验收标准

1. 对当前 `toolsandbox_partial_main` 重算后，DEFAULT 应得到：pairwise tau 为 `0.3333/-0.3333/-1.0000`，平均 tau `-0.3333`，完整顺序一致率 `0/3`，平均绝对名次位移 `1.5000`。
2. REPLAY 应得到：pairwise tau 为 `1.0000/0.3333/0.3333`，平均 tau `0.5556`，完整顺序一致率 `1/3`，平均绝对名次位移 `0.6667`。
3. 两种方法 top-1 一致率均为 `1/3`；报告中不得据此声称 replay 已稳定选出同一个最佳模型。
4. 现有 `scores.json`、`rank_tau`、`rank_tau_by_repeat` 和既有结果字段值保持不变。
5. 所有新增 JSON 字段可被 `json.dumps(..., ensure_ascii=False)` 序列化，缺失数据不会导致整张 metrics 表失败。
6. 单元测试通过，新增逻辑不触发网络请求、不修改实验产物、不改变 stop policy。

## 6. 实施顺序与风险控制

### Step 1：先实现纯计算与单元测试

在不读取新数据、不改变实验运行流程的前提下实现排名聚合、tie 处理和 pairwise 指标；先用固定小样本验证 tau 和 invalid pair。

### Step 2：接入 metrics 输出并重算离线结果

仅调用已有 `write_metric_tables()` 生成 `metrics.json`，对当前实验做离线重算；核对旧字段 diff 应为空，新字段只增加 `repeat_rank_consistency`。

### Step 3：更新报告和文档

把方法内 repeat 排名稳定性与跨方法 rank_tau 分开叙述，明确当前 3 repeat 只是小样本证据；补充模型逐 repeat 顺序和 pairwise 明细。

### Step 4：验收与回归

运行新增 metrics 测试及现有测试；检查缺失 repeat、并列和旧结果兼容性。若未来 repeat 数增加，pair 数应按 `n*(n-1)/2` 自动扩展，不写死为 3。

## 7. 项目中暂未把握实现的部分

- 本方案可以确定“如何计算同方法跨 repeat 的排名一致性”，但尚未规定跨 benchmark 汇总时各 benchmark 的权重；默认先按 benchmark 分开输出，不做跨 benchmark 总分。
- 当前仅有 3 个 repeat，无法据此给出可靠的置信区间或显著性检验；是否采用 bootstrap/置换检验应在 repeat≥5 后另行评审。
- 当模型分数大量并列时，Kendall tau-b 与 exact-order rate 的展示解释需要结合 mid-rank；本实验无并列，但测试必须覆盖该情形。
- 本方案不解决 replay Judge 与 native scorer 的语义校准问题；排名稳定性高不等于评估正确性高。

## 附录 A：当前方案不负责的实现部分

1. 不修改 ToolSandbox 原始 scenario、Agent、User Simulator 或 milestone 定义。
2. 不改变 DEFAULT/replay 的评分公式、阈值、动态权重或 virtual-stop 策略。
3. 不把同方法 repeat 排名一致性转化为新的模型分数或方法总分。
4. 不执行新的 online DYNSTEER 实验，不引入新的网络、数据库或外部服务依赖。
5. 不主动执行 git commit 或远端提交。
