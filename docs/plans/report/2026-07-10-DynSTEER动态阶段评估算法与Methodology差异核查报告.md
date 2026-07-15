# DynSTEER 动态阶段评估算法与 Methodology 差异核查报告

生成日期：2026-07-10  
修订依据：用户补充确认“milestone DAG + immediate dominator 阶段划分”为目标设计；参考线路/参考轨迹为可选能力，暂不作为当前修复项。

## 1. 核查目标与修订口径

本报告核查当前 DynSTEER 代码中针对 Agent 执行轨迹的动态阶段式评估算法，与 `docs/plans/2026-06-02-动态评估粗稿.md` 中 `## 3. 方案 / Methodology` 的流程差异，并根据用户补充信息修订上一版报告中的判断口径。

本次修订后，以下两点不再作为“需要修复的偏差”：

1. **阶段划分方式**：当前确定采用基于 milestone DAG 的阶段划分，即 milestone 作为阶段终点，对应 immediate dominator 作为阶段起点，而不是粗稿中的“上个阶段节点到当前节点”。
2. **参考线路/参考轨迹**：参考线路/参考轨迹是可选能力，仅在基底 benchmark 提供这类数据时使用；当前暂不要求为了 ToolSandbox 或无参考轨迹 benchmark 强行补齐。

本次重点重新核查以下三项：

1. 单步评估层是否缺失。
2. 动态评估策略是否被错误实现为“同阶段 judge 升级”，而不是“跨阶段粒度策略更新”。
3. minefield 当前实现与预期方案之间的真实差异。

核查依据包括：

- 方案原文：`docs/plans/2026-06-02-动态评估粗稿.md:120-154`
- 核心实现：`dynsteer/evaluate/evaluator.py`、`dynsteer/evaluate/milestone.py`、`dynsteer/evaluate/scoring.py`、`dynsteer/graph.py`、`dynsteer/stage.py`
- ToolSandbox 适配与 scorer：`dynsteer/adapter/toolsandbox/utils/convert.py`、`dynsteer/adapter/toolsandbox/harness.py`、`dynsteer/adapter/toolsandbox/scorer.py`
- Judge prompt：`dynsteer/prompt/judge.py` 与 `dynsteer/prompt/templates/judge/*.zh.md`

当前仓库未发现 `tests/` 目录，因此本次核查无法基于测试用例确认行为预期，只能基于源码、API 文档和示例配置进行静态流程核查。

## 2. 当前应保留的设计点

### 2.1 milestone DAG + immediate dominator 阶段划分应保留

当前代码在适配阶段对 `MilestoneGraph` 做增强：

- 为图增加 `__start__` 与 `__finish__` 超级节点。
- 计算每个 milestone 的直接依赖前驱 `dependency_predecessor_ids`。
- 计算每个 milestone 的 `stage_anchor_predecessor_id`，该字段来自增强图上的 immediate dominator。

对应代码：

- `dynsteer/graph.py:100-123`
- `dynsteer/evaluate/milestone.py:42-61`
- `dynsteer/evaluate/evaluator.py:617-681`

当前阶段区间语义是：

```text
(stage_anchor_predecessor 的 boundary, 当前 milestone boundary]
```

这与粗稿文字中的“上个阶段节点到当前节点”不同，但根据用户补充，该差异属于已确认的算法优化方向，不应再作为修复项。

### 2.2 参考线路/参考轨迹暂不作为当前修复项

当前代码没有把 reference route / reference trajectory 建成一等字段，也没有在 judge prompt 或 structured scorer 中接入参考轨迹比较。

对应代码：

- `dynsteer/model.py:145-213`
- `dynsteer/adapter/toolsandbox/utils/convert.py:385-430`
- `dynsteer/prompt/judge.py:22-62`

根据用户补充，这一点暂时搁置。当前报告只保留如下判断：

- 若基底 benchmark 不提供参考轨迹，当前依赖 LLM 判断阶段整体质量是可接受路线。
- 若未来接入的 benchmark 提供参考轨迹，则现有代码确实缺少消费参考轨迹并做结构化比较的手段，但这不是当前优先修复项。

## 3. 当前代码实际动态评估流程

当前实现的主流程如下：

1. `DynSTEEREvaluator.evaluate(...)` 启动 benchmark harness，并初始化运行期 `Trajectory`、动态权重、start settlement、matched milestone 状态。
2. 主循环调用 `harness.advance_case(session)`，逐批推进 benchmark。
3. 每个新增 step 追加到轨迹后，调用 `analyze_milestone_step(...)` 对当前 step 构造唯一候选 boundary，并检查 ready milestone 与 blocked milestone。
4. ready milestone 达到 `StageStatus.PASS` 后触发阶段结算。
5. 阶段结算调用 `_evaluate_stage(...)`：
   - 先运行 `CheapJudge`。
   - 当前阶段内根据 `select_evaluation_level(...)` 判断是否升级到 `StandardJudge`。
   - 若 standard 后仍风险较高，再升级到 `ExpensiveJudge`。
   - 阶段结束后调用 `update_weights(...)` 更新下一阶段权重。
6. 如果启用策略终止，当前实现可因 blocked milestone、stage failure 或 fatal minefield 提前停止。
7. 自然结束时，对未完成 required milestone 生成 synthetic pending stage，并追加 finish stage。

关键代码：

- `dynsteer/evaluate/evaluator.py:189`
- `dynsteer/evaluate/evaluator.py:254-341`
- `dynsteer/evaluate/evaluator.py:479-505`
- `dynsteer/evaluate/runtime.py:111-157`

## 4. 修订后的总体结论

当前 DynSTEER 已经具备运行期 milestone checkpoint、阶段结算、LLM judge 分层调用和动态权重更新能力。但根据用户补充后的目标算法口径，仍存在三类需要重点修复或重新设计的问题：

1. **单步评估层缺失**：当前每个 step 只用于 milestone 边界命中检测，不形成独立的 step-level action quality / semantic consistency / executability / first-error 定位结果。
2. **动态评估粒度策略实现错误**：当前实现是“当前阶段 cheap 不足则同阶段升级到 standard/expensive”，而预期应是“当前阶段评估结果决定下一阶段的评估粒度策略”，与动态权重一样跨阶段生效。
3. **minefield 运行期语义不完整**：当前 minefield 评估不按 boundary/snapshot 扫描，ToolSandbox 下还可能因 `final_state` 缺失而无法触发；同时 penalty 语义未真正接入总分和提前终止策略。

上一版报告中关于阶段划分和参考轨迹的两项，应调整为：

- 阶段划分：当前 immediate dominator 方案是目标设计，应保留。
- 参考轨迹：未来可选增强项，当前不作为修复优先级。

## 5. 单步评估层缺失

### 5.1 原方案预期

Methodology 中明确提到单步骤评估，覆盖方向包括：

- 动作正确率。
- 语义一致性。
- 可执行性。
- 工具调用质量。
- 类似 AgentProcessBench 的 step label：`+1 / 0 / -1`。
- 类似 FirstErrAcc 的首个错误定位。
- 必要时才启用更昂贵的 LLM 单步评估。

原文位置：`docs/plans/2026-06-02-动态评估粗稿.md:136-147`。

### 5.2 当前代码实际做法

当前每个新增 step 会进入 `analyze_milestone_step(...)`，但它的职责是 milestone matching：

- 当前 step 被包装成一个 `Boundary`。
- 对 ready milestone 的 constraints 做评分。
- 如果某个 milestone 满足 `StageStatus.PASS`，则选为阶段结算点。
- 如果后继 milestone 已满足但前驱未完成，则记录 blocked diagnostic。

对应代码：

- `dynsteer/evaluate/evaluator.py:264-341`
- `dynsteer/evaluate/milestone.py:78-185`
- `dynsteer/boundary.py`

这不是单步评估层。它没有为每个 step 输出独立的质量标签或分数，也没有将 step-level 结果汇总进阶段报告。

### 5.3 现有 `runtime_quality_diagnostics` 不等于单步评估层

当前 `build_runtime_quality_diagnostics(...)` 会检查：

- 工具参数是否像非 canonical id 的别名。
- 工具返回是否为空。
- 工具是否失败。
- 空工具结果后是否仍给出具体事实回答。
- 首次工具调用前的额外用户轮次等效率现象。

对应代码：`dynsteer/evaluate/quality.py:10-90`

但它当前只是 raw summary 里的诊断信息：

- 不参与 `StageEvaluationResult.stage_score`。
- 不参与 `dimension_scores`。
- 不参与 `select_evaluation_level(...)`。
- 不按阶段区间生成 per-step result。
- 不形成 `first_error_step_index`。

因此它可以作为未来单步评估层的低成本规则基础，但不能视为已经实现单步评估。

### 5.4 影响

当前缺失单步评估层会造成：

1. 阶段失败时只能知道 milestone 没过，无法结构化定位是哪个动作先出错。
2. tool_quality、state_consistency、efficiency 等维度主要依赖 LLM judge 或 milestone constraint 间接判断。
3. 动态策略无法针对“上一阶段具体哪类 step 出问题”选择下一阶段细粒度评估工具。
4. 无法计算 StepAcc / FirstErrAcc / step label 分布等论文实验指标。

### 5.5 后续修复建议

建议新增独立的 step-level 数据结构和评估入口，例如：

```text
StepEvaluationResult
- step_id
- step_index
- evaluator_level
- status / label: positive | neutral | negative 或 +1 | 0 | -1
- dimension_scores
- evidence
- diagnosis
- first_error_candidate
```

建议拆分职责：

- `analyze_milestone_step(...)`：继续只负责 milestone boundary matching。
- 新增 `evaluate_step(...)` 或 `StepEvaluator`：负责单步动作质量评估。
- 阶段结算时，把阶段区间内的 step-level 结果汇总进 `StageEvaluationResult.metadata` 或显式字段。

低成本规则可先覆盖：

- 工具调用是否失败。
- 工具参数是否缺失或疑似别名。
- 工具结果为空后的 grounding 风险。
- 重复工具调用。
- 用户澄清是否必要。
- 状态修改类工具是否违反 guardrail / minefield。

LLM 单步评估只应在动态策略要求 standard/expensive 粒度时启用。

## 6. 动态评估粒度策略实现错误

### 6.1 原方案预期

根据用户补充，预期的动态评估策略不是“当前阶段内部从 cheap 升级到 standard/expensive 后再给出最终结果”，而是：

- 当前阶段评估结果决定**下一阶段**采用什么评估粒度。
- 权重跨阶段更新。
- 评估粒度也跨阶段更新。
- 某维度低分时，下阶段对该维度采用更高粒度评估。
- 某维度高分时，下阶段对该维度采用更低粒度评估。
- 某维度处于合理区间时，下阶段粒度保持不变或采用默认粒度。

用户还提出一种更直接的策略：按当前阶段分数划分 3 个区间，并映射到 cheap / standard / expensive 三种粒度。

这里有一个需要后续开发前确认的细节：  
“低分 -> 升级、高分 -> 降级”意味着低分应映射到更高成本/更细粒度的 `expensive`，高分应映射到 `cheap`；而“分数从低到高分别对应 cheap、standard、expensive”若按字面理解则相反。建议在正式代码修改方案中明确最终映射方向。若沿用“低分加严、高分降级”的风险控制逻辑，则推荐：

```text
低分区间   -> next stage: expensive
合理区间   -> next stage: standard 或保持上一粒度
高分区间   -> next stage: cheap
```

### 6.2 当前代码实际做法

当前 `_evaluate_stage(...)` 对每个阶段都固定从 cheap 开始：

```text
CheapJudge
  -> select_evaluation_level(...)
  -> StandardJudge, if needed
  -> select_evaluation_level(...)
  -> ExpensiveJudge, if needed
  -> update_weights(...)
```

对应代码：

- `dynsteer/evaluate/evaluator.py:479-505`
- `dynsteer/evaluate/evaluator.py:152-187`

当前 `select_evaluation_level(...)` 的职责是当前阶段的同阶段升级：

- cheap 阶段如果证据不足，则升级 standard。
- standard 阶段如果置信度低、不确定性高、字段缺失多或接近 minefield，则升级 expensive。
- expensive 已是最高层，不再降级。

当前阶段结束后，只有 `next_weights` 被写入下一阶段状态：

- `state.weights = next_weights`
- 没有 `next_evaluation_levels`。
- 没有 `dimension -> EvaluationLevel` 的跨阶段状态。
- 没有“下一阶段按上一阶段低分维度启用更细粒度评估”的机制。

对应代码：

- `dynsteer/evaluate/evaluator.py:464`
- `dynsteer/evaluate/scoring.py:399-418`
- `dynsteer/model.py:335-355`

### 6.3 当前策略与预期策略的核心差异

| 维度 | 当前代码 | 预期方向 |
| --- | --- | --- |
| 粒度决策发生时间 | 当前阶段内部 | 当前阶段结束后，作用于下一阶段 |
| 粒度状态 | 不保存跨阶段粒度状态 | 应保存下一阶段全局或逐维粒度状态 |
| 决策输入 | status、stage_score、不确定性、missing、minefield、confidence | 当前阶段各维度分数区间、可能叠加不确定性和风险 |
| 动态权重 | 已跨阶段更新 | 保留 |
| 动态粒度 | 同阶段升级，不降级 | 跨阶段 cheap/standard/expensive 分配，可升级也可降级 |
| 逐维控制 | 无 | 应支持按 dimension 单独调整 |

### 6.4 影响

当前实现会导致：

1. 每个阶段的粒度都由当前阶段 cheap 结果临时决定，无法体现“上一阶段表现影响下一阶段评估预算”的动态策略。
2. 一旦 cheap 证据不足，当前阶段立即升级；这更像质量兜底复核，而不是动态评估策略。
3. 当前实现只会“向上升级”，没有基于上一阶段高分降低下一阶段粒度的逻辑。
4. 权重动态和粒度动态没有绑定：低分维度权重会变大，但不会触发对应维度的更细粒度 evaluator。

### 6.5 后续修复建议

建议新增跨阶段粒度策略状态，例如：

```text
RuntimeEvaluationState
- weights: dict[Dimension, float]
- evaluation_levels: dict[Dimension, EvaluationLevel] 或 next_evaluation_level: EvaluationLevel
```

建议新增策略函数：

```text
update_evaluation_levels(
    current_levels,
    dimension_scores,
    uncertainty,
    thresholds,
) -> next_levels
```

可选策略：

1. **逐维策略**：每个 dimension 独立维护 cheap/standard/expensive。
2. **阶段全局策略**：根据最低维度分数或加权阶段分数决定下一阶段整体粒度。
3. **混合策略**：全局粒度决定基础成本，低分维度额外启用专项 evaluator。

建议先采用最小可落地版本：

- 保留当前 `select_evaluation_level(...)` 作为安全兜底或异常复核机制，但不要把它当作动态策略本体。
- 新增跨阶段 `next_evaluation_levels`。
- 下一个阶段的 `_evaluate_stage(...)` 根据 `next_evaluation_levels` 决定起始 evaluator，而不是永远从 cheap 起步。
- fatal minefield、安全硬约束失败等仍允许当前阶段立即升级或提前终止。

## 7. minefield 专项核查

### 7.1 预期语义

粗稿中 ToolSandbox 的 minefield 语义是：

- Minefields 表示轨迹中不应发生的错误行为。
- 若踩中 minefield，整条轨迹得分会归零。
- 在动态评估场景中，minefield 更适合作为运行期 guardrail：一旦触发 fatal minefield，应立即停止或至少标记为高风险。

原文位置：`docs/plans/2026-06-02-动态评估粗稿.md:187`。

### 7.2 当前代码的 minefield 评估入口

当前 minefield 评估集中在 `DynSTEEREvaluator.evaluate_minefields(...)`：

- 遍历 `graph.minefields`。
- 对每个 minefield 的 constraints 求平均分。
- `severity == "fatal"` 且分数达到 1.0 时标记 fatal。
- 返回 `matches, max_score, fatal`。

对应代码：`dynsteer/evaluate/evaluator.py:110-149`

该函数在三个位置使用：

1. `_enrich_stage_result(...)`：给当前阶段结果补充 `minefield_score` 与 `fatal_minefield_score`。
2. `_runtime_report(...)`：最终报告中输出 minefield matches，并用 max minefield score 影响 overall score。
3. `_should_stop_after_stage(...)`：阶段结算后检查是否因 fatal minefield 提前停止。

对应代码：

- `dynsteer/evaluate/evaluator.py:512-519`
- `dynsteer/evaluate/evaluator.py:523-559`
- `dynsteer/evaluate/evaluator.py:718-741`

### 7.3 当前 minefield 的关键问题

当前 minefield 实现与预期存在四个差异。

#### 7.3.1 不按运行期 boundary/snapshot 扫描

当前 minefield constraint 的 source 只有两类：

```text
constraint.target == METRIC -> trajectory.metrics
其他 target -> trajectory.final_state or {}
```

对应代码：`dynsteer/evaluate/evaluator.py:128-134`

这意味着：

- `ConstraintTarget.STATE_SNAPSHOT` 不会使用当前 step 的 `boundary_snapshot(...)`。
- `ConstraintTarget.TOOL_CALL` / `TOOL_RESULT` 不会扫描具体 step。
- minefield 不像 milestone 那样在每个 boundary 上评分。
- `_enrich_stage_result(...)` 看到的是整条已观测轨迹的全局 minefield score，而不是当前阶段或当前 step 的 minefield 命中。

这与“轨迹中不应发生的错误行为”的语义不完全一致。minefield 应该能在任意 step 或任意 snapshot 上触发。

#### 7.3.2 ToolSandbox 下很可能无法触发 snapshot minefield

ToolSandbox 的 milestone/minefield 都是由 snapshot constraints 转换而来。minefield 节点通过 `_matcher_nodes(..., required=False)` 生成：

- `target = "state_snapshot"`
- `operator = "custom"`
- `severity = "fatal"`
- `penalty = {"mode": "fixed", "value": 1.0}`

对应代码：

- `dynsteer/adapter/toolsandbox/utils/convert.py:298-380`
- `dynsteer/adapter/toolsandbox/utils/convert.py:385-410`

但是 `ToolSandboxHarness` 当前没有覆盖 `final_state_from_session(...)`，因此继承 `BaseBenchmarkHarness.final_state_from_session(...)` 的默认实现，返回 `None`。

对应代码：

- `dynsteer/adapter/base.py:80-84`
- `dynsteer/adapter/toolsandbox/harness.py`

因此 `evaluate_minefields(...)` 会把 source 变成 `{}`。对于 ToolSandbox custom snapshot constraint：

1. `score_constraint(...)` 读取 selector `$`，actual 变成 `{}`。
2. `ToolSandboxConstraintScorer._score_toolsandbox_snapshot_constraint(...)` 调用 `_rows_to_dataframe(actual, ...)`。
3. `_rows_to_dataframe(...)` 要求 actual 是 list 或 `{"rows": list}`。
4. `{}` 不是合法 rows，因此抛出 `ValueError("ToolSandbox snapshot rows 必须是 list")`。
5. 异常被 `score_custom_constraint(...)` 捕获，返回 0 分。

对应代码：

- `dynsteer/adapter/toolsandbox/scorer.py:154-189`
- `dynsteer/adapter/toolsandbox/scorer.py:230-266`

所以在当前 ToolSandbox 运行期，fatal minefield 很可能不会被命中。

#### 7.3.3 penalty 字段没有真正参与评分

`MinefieldPenalty` 在模型和 parser 中存在：

- `dynsteer/model.py:178-191`
- `dynsteer/adapter/loader.py:177-191`

但 `evaluate_minefields(...)` 没有读取 `minefield.penalty.mode` 或 `minefield.penalty.value`。最终总分只是：

```text
overall_score = average_stage_score * (1 - clamp(minefield_score))
```

对应代码：`dynsteer/evaluate/scoring.py:324-328`

这与 ToolSandbox “踩中 minefield 整条轨迹归零”的语义只有在 `minefield_score == 1.0` 时近似一致。若 penalty 设计为 fixed 1.0，目前并未显式使用。

#### 7.3.4 minefield 停止只发生在阶段结算之后

当前 `_should_stop_after_stage(...)` 只在 milestone stage 被结算后调用。若 Agent 踩中 minefield 但之后一直没有命中任何 milestone，当前代码不会立即停机。

对应代码：

- `dynsteer/evaluate/evaluator.py:445-476`
- `dynsteer/evaluate/evaluator.py:718-741`

这与“运行期 guardrail”的直觉不同。更合理的动态评估语义是：每个新增 step 后都应该能检测 minefield。

### 7.4 minefield 与预期方案的差异结论

minefield 当前实现不只是“有小差异”，而是运行期语义明显不足：

1. 当前把 minefield 当作全局 final_state/metrics 检查，而不是轨迹任意点的禁止行为扫描。
2. ToolSandbox 的 snapshot minefield 在当前 harness 下很可能不会触发。
3. penalty 字段未参与扣分或归零。
4. 提前终止只在阶段结算后检查，不能保证踩雷即停。

### 7.5 后续修复建议

建议将 minefield 改为与 milestone matching 平行的运行期检查层：

1. 每个新增 step 后构造 boundary。
2. 对每个 minefield 使用与 milestone 相同的 source 解析逻辑：
   - `STATE_SNAPSHOT` 使用 `boundary_snapshot(...)`。
   - `TOOL_CALL` / `TOOL_RESULT` 使用 `boundary_step(...)`。
   - `METRIC` 使用 trajectory metrics。
3. 命中 fatal minefield 时立即生成 termination decision。
4. 将 minefield match 写入 `state`，避免每个阶段重复扫描同一历史。
5. `MinefieldPenalty` 应参与 final score：
   - `fixed=1.0` 可直接归零。
   - 其他 mode 可扩展为比例扣分。
6. ToolSandbox 不一定需要实现 `final_state_from_session(...)`，只要 minefield 改为 boundary/snapshot 扫描即可利用已有 `snapshots_from_context(...)`。

## 8. 修订后的差异矩阵

| 项目 | 当前状态 | 修订后判断 | 后续动作 |
| --- | --- | --- | --- |
| milestone DAG + immediate dominator 阶段划分 | 已实现 | 已确认设计选择 | 保留，不作为修复项 |
| 参考线路/参考轨迹 | 未建模 | 可选能力，当前暂缓 | 未来接入提供参考轨迹的 benchmark 时再补 |
| 单步评估层 | 缺失，仅有 milestone matching 与质量诊断 | 确认为偏离预期 | 需要新增 step-level evaluator 与结果模型 |
| 动态权重 | 已跨阶段更新 | 基本符合 | 保留，并与粒度策略联动 |
| 动态评估粒度 | 当前阶段内 cheap -> standard -> expensive 升级 | 策略理解错误 | 改为由当前阶段分数决定下一阶段粒度 |
| 逐维粒度控制 | 缺失 | 需要设计 | 建议新增 `dimension -> EvaluationLevel` 状态 |
| minefield source | final_state / metrics | 不符合运行期禁止行为扫描语义 | 改为 boundary/snapshot/step 扫描 |
| ToolSandbox minefield | 可能因 final_state 缺失而不触发 | 明显风险 | 优先修复 minefield 扫描逻辑 |
| penalty | parser 有，评分未用 | 未完整实现 | 接入 overall_score 与 termination |

## 9. 建议的后续修复优先级

### P0. minefield 运行期扫描修复

原因：

- minefield 是安全/禁止行为逻辑。
- 当前 ToolSandbox fatal minefield 很可能不触发。
- 修复后可以直接提升运行期提前终止可靠性。

建议先做最小修复：

- 新增 `evaluate_minefields_at_boundary(...)`。
- 在每个 step append 后、milestone matching 前检查 minefield。
- 命中 fatal minefield 时立即 stop。
- 保留现有 `evaluate_minefields(...)` 作为最终汇总或重构为扫描全历史。

### P1. 动态评估粒度策略重构

原因：

- 当前策略方向与用户预期不一致。
- 它影响后续单步评估何时启用、用什么粒度启用。

建议先确定分数区间到粒度的映射方向。若采用“低分加严、高分降级”：

```text
score < low_threshold      -> expensive
low_threshold <= score < high_threshold -> standard 或保持当前粒度
score >= high_threshold    -> cheap
```

也可以改成“越高分越高粒度”的实验策略，但这与“低分升级、高分降级”的描述相反，需要单独确认。

### P2. 单步评估层补齐

原因：

- 这是 Methodology 中明确期望的粒度。
- 但它最好依赖 P1 的粒度策略决定何时启用昂贵评估。

建议先实现低成本规则版：

- 工具调用失败 / 空结果 / 参数异常。
- 重复调用与停滞。
- 用户澄清负担。
- 状态修改和 guardrail。

随后再接入 LLM step judge：

- cheap：规则标签。
- standard：单轮 LLM step label。
- expensive：阶段内重点 step 深审或 first-error 复核。

## 10. 本次修订结论

结合用户补充后的最终判断是：

1. 当前阶段划分方式不是问题，应保留 milestone DAG + immediate dominator 方案。
2. 参考轨迹不是当前必修项，暂时不修改。
3. 单步评估层确实缺失，需要后续补齐。
4. 当前动态评估策略把“粒度升级”理解成了同阶段 judge 复核，而用户预期是跨阶段评估粒度策略更新，这一点需要重构。
5. minefield 当前实现与运行期禁止行为检测语义存在明显差异，尤其 ToolSandbox 下可能无法触发 fatal minefield，建议作为优先修复项。
