# evaluate/scoring 循环导入与职责收敛修复方案

生成日期：2026-07-20  
适用范围：`dynsteer/evaluate/scoring.py`、`dynsteer/evaluate/settlement.py`、`dynsteer/evaluate/evaluator.py`、`dynsteer/judges/*`。  
核心目标：修复 `stage_score_from_dimensions` 循环导入报错，并同步收敛 `scoring.py` 中混杂的动态权重和冗余阶段后处理职责。

## 1. 背景与问题判断

当前启动时报错：

```text
ImportError: cannot import name 'stage_score_from_dimensions' from partially initialized module 'dynsteer.evaluate.scoring'
```

直接导入链如下：

```text
main.py
-> dynsteer.adapter.base
-> dynsteer.evaluate.scoring
-> dynsteer.judges.confidence
-> dynsteer.judges.__init__
-> dynsteer.judges.base
-> dynsteer.evaluate.scoring.stage_score_from_dimensions
```

表面原因是 Python 包初始化阶段出现循环导入。更深层原因是 `evaluate/scoring.py` 当前承担了多类职责：

1. `GeneralScorer`：结构化约束、milestone 评分。
2. `stage_score_from_dimensions`、`overall_score`、`minefield_penalty_score`：阶段或轨迹层面的聚合评分。
3. `select_initial_weights`、`normalize_weights`、`update_weights`：动态权重初始化与更新。
4. `enrich_stage_result`：结算后补齐置信度、不确定性和 minefield 字段。

其中聚合评分函数仍属于评分语义，可以继续保留在 `scoring.py`，不需要为了拆分而新增模块。真正需要收敛的是动态权重逻辑和 `enrich_stage_result` 这类阶段结算后处理逻辑。尤其是 `enrich_stage_result` 为了补齐置信度从 `dynsteer.judges.confidence` 引入函数，触发了 `evaluate.scoring -> judges -> evaluate.scoring` 的循环导入。

## 2. 修复目标

1. 启动入口不再因为 `dynsteer.evaluate.scoring` 与 `dynsteer.judges` 互相导入而失败。
2. `scoring.py` 保留结构化约束评分和聚合评分函数，不再包含动态权重和阶段后处理函数。
3. `judges` 层只负责产出维度级评分、证据、诊断、置信度，不再计算最终 `stage_score`。
4. `settlement` 层成为阶段分数的唯一权威结算位置，在合并 cheap、standard、expensive 维度结果后统一计算 `stage_score`。
5. 不使用局部延迟 import 作为修复手段，避免违反项目“禁止懒加载”的依赖约束。
6. 不删除 `docs/constraints` 与 `docs/plans` 下已有文档，不主动执行 git commit。

## 3. 代码架构调整

建议调整后的职责边界如下：

```text
dynsteer/
  evaluate/
    scoring.py        # GeneralScorer、约束评分、milestone 结构化评分、stage/overall/minefield 聚合评分
    weights.py        # 动态权重初始化、归一化、更新
    settlement.py     # checkpoint/finish 结算、最终 stage_score 计算、minefield 字段写入
    evaluator.py      # 评估主编排，调用 scoring/weights/settlement
  judges/
    base.py           # Judge 抽象基类、LLM payload 校验与维度结果构造
    cheap.py          # cheap 维度评分
    standard.py       # standard LLM 维度评分
    expensive.py      # expensive LLM 维度评分
    confidence.py     # 置信度、不确定性、judge payload 聚合
```

### 3.1 `evaluate/scoring.py`

保留：

1. `GeneralScorer`
2. `get_effective_scorer`
3. 与 `Constraint`、`MilestoneScore`、`boundary_snapshot`、`boundary_step` 直接相关的结构化评分逻辑
4. `stage_score_from_dimensions`
5. `overall_score`
6. `minefield_penalty_score`

移出：

1. `normalize_weights`
2. `select_initial_weights`
3. `update_weights`

删除：

1. `enrich_stage_result`

调整后 `scoring.py` 不再 import `dynsteer.judges.confidence`，从源头切断 `evaluate.scoring -> judges -> evaluate.scoring` 的环。`stage_score_from_dimensions`、`overall_score`、`minefield_penalty_score` 是无 `judges` 依赖的纯评分聚合函数，继续放在 `scoring.py` 更简洁。

### 3.2 新增 `evaluate/weights.py`

放置动态权重函数：

1. `normalize_weights(weights)`
2. `select_initial_weights(task_case)`
3. `update_weights(current, scores, dimension_uncertainty, config)`

该模块只允许依赖：

1. `math`
2. `dynsteer.config.TASK_TYPE_WEIGHTS`
3. `dynsteer.model.Dimension`
4. `dynsteer.model.DynamicWeightConfig`
5. `dynsteer.model.TaskCase`
6. `dynsteer.utils.clamp`

这样可以让权重演化逻辑从 `scoring.py` 中独立出来，也方便后续单独测试动态权重策略。

### 3.3 `evaluate/settlement.py`

调整为阶段结算的权威位置：

1. 从 `evaluate.scoring` 引入 `stage_score_from_dimensions`。
2. 从 `evaluate.weights` 引入 `update_weights`。
3. 删除 `enrich_stage_result` 调用，不再额外补齐 `dimension_confidence` 与 `dimension_uncertainty`。
4. 在 `_evaluate_stage` 中保留唯一最终赋值：

```python
stage_result.stage_score = stage_score_from_dimensions(stage_result.dimension_scores, weights)
```

5. 在阶段状态判定后直接写入 minefield 字段：

```python
stage_result.minefield_score = state.max_minefield_score
stage_result.fatal_minefield_score = state.max_minefield_score if state.fatal_minefield else 0.0
```

6. `_judge_result_metadata` 不再把 judge 自身返回的 `stage_score` 当作权威值。若仍需要记录每层 judge 的诊断性分数，应在 settlement 层根据该 judge 的 `dimension_scores` 和当前 `weights` 计算，并明确字段语义为 `dimension_stage_score` 或继续保留 `stage_score` 但仅作为诊断字段。

关于 `dimension_confidence` 与 `dimension_uncertainty` 的处理结论：

1. `CheapJudge.evaluate_stage` 已通过 `cheap_dimension_confidence` 和 `uncertainty_from_confidence` 生成这两个字段。
2. `LLMJudge._result_from_payload` 已通过 `complete_dimension_confidence` 和 `uncertainty_from_confidence` 生成这两个字段。
3. `_merge_dimension_result` 已对 `dimension_scores`、`dimension_confidence`、`dimension_uncertainty`、`dimension_levels` 按维度融合。
4. 因此 `enrich_stage_result` 再次调用 `complete_dimension_confidence` 和 `uncertainty_from_confidence` 属于重复后处理，应删除。

### 3.4 `evaluate/evaluator.py`

更新导入来源：

1. `GeneralScorer` 继续来自 `evaluate.scoring`。
2. `select_initial_weights` 改为来自 `evaluate.weights`。
3. `overall_score`、`minefield_penalty_score` 继续来自 `evaluate.scoring`。

主流程行为不变。

### 3.5 `judges` 目录

核心原则：judge 层只输出维度判断，不负责最终阶段结算。

计划调整：

1. 从 `judges/base.py`、`judges/cheap.py`、`judges/expensive.py` 移除 `from dynsteer.evaluate.scoring import stage_score_from_dimensions`。
2. `BaseJudge.evaluate_stage` 抽象接口移除 `weights` 参数。
3. `CheapJudge.evaluate_stage`、`StandardJudge.evaluate_stage`、`ExpensiveJudge.evaluate_stage` 同步移除 `weights` 参数。
4. `LLMJudge._result_from_payload` 移除 `weights` 参数，不再计算 `stage_score`。
5. `ExpensiveJudge._pass_metadata` 移除 `weights` 参数，不再计算 pass 级 `stage_score`。
6. judge 构造 `StageEvaluationResult` 时，`stage_score` 使用 `0.0` 作为未结算占位值；该值不得进入最终输出，必须由 `settlement._evaluate_stage` 覆盖。
7. `judge_result_output_metadata` 与 `judge_payload_output_metadata` 不再输出 `judge_stage_score`，改为输出维度分摘要、置信度均值、首条证据和首条诊断。

该调整会让 `weights` 参数从 judge 抽象接口中消失，避免出现未使用参数，也让“阶段总分只在 settlement 中计算”的语义更加清楚。

## 4. 实施步骤

### Step 1. 新增动态权重模块

1. 新建 `dynsteer/evaluate/weights.py`。
2. 从 `scoring.py` 迁移 `normalize_weights`、`select_initial_weights`、`update_weights`。
3. `stage_score_from_dimensions`、`overall_score`、`minefield_penalty_score` 继续保留在 `scoring.py`。

### Step 2. 收敛 `scoring.py`

1. 删除已迁移到 `weights.py` 的动态权重函数。
2. 删除 `enrich_stage_result`。
3. 删除 `math`、`TASK_TYPE_WEIGHTS`、`DynamicWeightConfig`、`complete_dimension_confidence`、`uncertainty_from_confidence` 等不再使用的 import。
4. 确认 `scoring.py` 不再直接或间接依赖 `dynsteer.judges`。

### Step 3. 调整 `settlement.py`

1. 更新 import 来源。
2. 删除 `enrich_stage_result` 调用。
3. 在 `_evaluate_stage` 中保持最终阶段分唯一计算点。
4. 调整 `_judge_result_metadata`，避免读取 judge 返回阶段的占位 `stage_score`。
5. 在 `_evaluate_stage` 中直接写入 `minefield_score` 和 `fatal_minefield_score`。
6. 不再重复补齐 `dimension_confidence` 和 `dimension_uncertainty`。
7. 保持 `finish_settlement` 的 finish 分数逻辑不变，因为 finish verification 当前本身会给出终局分数。

### Step 4. 调整 judge 接口

1. 修改 `BaseJudge.evaluate_stage` 签名。
2. 同步修改 `CheapJudge`、`StandardJudge`、`ExpensiveJudge` 签名与调用。
3. 修改 `LLMJudge._result_from_payload`，只构造维度结果、置信度、证据、诊断、metadata。
4. 修改 `ExpensiveJudge._pass_metadata` 与 judge telemetry，删除 pass 级阶段总分计算。
5. 确认 `judges` 下所有文件均不再 import `dynsteer.evaluate.scoring`。

### Step 5. 更新调用点

1. 更新 `settlement._evaluate_stage` 对 `judge.evaluate_stage` 的调用参数。
2. 更新 `evaluator.py` 中 weights 的 import。
3. 全仓搜索旧导入：

```text
from dynsteer.evaluate.scoring import stage_score_from_dimensions
from dynsteer.evaluate.scoring import select_initial_weights
from dynsteer.evaluate.scoring import update_weights
from dynsteer.evaluate.scoring import enrich_stage_result
```

确保 `select_initial_weights`、`update_weights` 替换为新职责位置，`enrich_stage_result` 被删除，`stage_score_from_dimensions` 只保留在 settlement 等需要最终结算的调用点。

## 5. 测试与验收

### 5.1 静态检查

1. 执行 Python 编译检查：

```bash
python -m compileall dynsteer
```

2. 执行循环导入 smoke test：

```bash
python -c "import dynsteer.evaluate.scoring; import dynsteer.judges; import dynsteer.evaluate.settlement; from dynsteer.evaluate.evaluator import DynSTEEREvaluator"
```

3. 搜索残留依赖：

```bash
rg "dynsteer.evaluate.scoring import .*update_weights|dynsteer.evaluate.scoring import .*select_initial_weights|dynsteer.evaluate.scoring import .*enrich_stage_result" dynsteer
rg "dynsteer.evaluate.scoring import stage_score_from_dimensions" dynsteer/judges
```

验收标准：无循环导入、`scoring.py` 不再依赖 `judges`、`judges` 不再依赖 `stage_score_from_dimensions`、无 Python 编译错误。

### 5.2 单元测试

若当前工作区存在可运行测试，执行：

```bash
uv run pytest
```

若测试目录暂不可用，至少补充或保留以下测试方向：

1. `stage_score_from_dimensions`：空维度、零权重、部分维度、正常加权。
2. `select_initial_weights`：无 task type、未知 task type、多 task type 合并。
3. `update_weights`：低分高不确定性维度权重上升、缺失维度权重保持、归一化结果和为 1。
4. 导入顺序测试：分别先 import `dynsteer.evaluate.scoring`、再 import `dynsteer.judges`，以及反向顺序。

### 5.3 行为验收

1. 同一条轨迹在修复前后，最终 `stage_result.stage_score` 的计算公式保持一致。
2. `dimension_scores`、`dimension_confidence`、`dimension_uncertainty`、`next_weights` 输出结构保持一致。
3. `dimension_judge_results` 中不再依赖 judge 层未结算的 `stage_score`。
4. `finish_settlement` 行为保持不变。
5. `_evaluate_stage` 不再通过 `enrich_stage_result` 重新计算 `dimension_confidence` 与 `dimension_uncertainty`。

## 6. 风险与处理

1. `BaseJudge.evaluate_stage` 移除 `weights` 参数会改变内部接口。当前仓库内调用点集中在 `settlement.py`，按项目约束默认不保留旧接口兼容。
2. 若外部脚本直接调用 judge 并读取 `StageEvaluationResult.stage_score`，重构后该值不再代表权威阶段总分。处理方式是在文档或注释中明确：judge 结果是维度级结果，阶段总分必须经 settlement 结算。
3. `judge_stage_score` telemetry 字段可能出现在历史 `display/data.js` 示例数据中。该文件属于历史生成数据，运行时代码不应依赖该字段；如发现展示逻辑读取该字段，再单独改为展示维度分摘要。
4. 新增 `weights.py` 会增加一个模块，但它承载独立的动态权重策略逻辑，不是中转文件，符合简洁性约束。

## 7. 不采用的方案

1. 不采用在 `scoring.py` 或 `judges/base.py` 内部做局部 import 的方式。该方式只能绕过当前报错，不能修复职责混杂，并且不符合项目禁止懒加载的约束。
2. 不采用只修改 `judges/__init__.py` 减少重导出的方式。该方式可以降低触发概率，但 `judges` 依赖 `evaluate.scoring` 的方向仍然存在。
3. 不采用继续让 judge 层计算最终 `stage_score` 的方式。主流程已经在 settlement 合并多层维度后统一计算，judge 层提前计算会产生冗余和语义歧义。
4. 不新增 `evaluate/aggregation.py`。`stage_score_from_dimensions`、`overall_score`、`minefield_penalty_score` 仍属于评分聚合语义，且不会引入 `judges` 依赖，继续保留在 `scoring.py` 更简洁。

## 附录A. 项目中没有把握实现的模块部分

当前任务中最没有把握的部分是“移除 judge 层 `stage_score` 后，对外部直接调用 judge 接口的影响范围”。原因是当前仓库内调用点可以通过 `rg` 确认主要集中在 `settlement.py`，但无法完全确认仓库外部是否有脚本直接调用 `CheapJudge.evaluate_stage`、`StandardJudge.evaluate_stage` 或 `ExpensiveJudge.evaluate_stage` 并依赖其返回的 `stage_score`。按照当前项目约束，未显式要求兼容旧接口时，应优先采用最新接口并保持设计简洁，因此方案默认不保留旧签名兼容层。

第二个需要注意的部分是历史展示数据中的 `judge_stage_score` 字段。当前搜索结果显示主要出现在 `display/data.js` 这类生成数据中，未确认它是否被外部展示脚本或人工分析流程依赖。落地时需要在修改 telemetry 后运行一次展示侧搜索，若发现真实展示逻辑依赖该字段，应同步改为展示维度分摘要或 settlement 层诊断分。
