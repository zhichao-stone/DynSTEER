# Evaluate API

## 目标

`dynsteer.evaluate` 提供 DynSTEER 的运行期阶段式评估入口。当前只保留 `DynSTEEREvaluator.evaluate(harness, config, task_case)` 主流程，不再提供离线整轨迹评估入口。

## 主流程

```python
result = DynSTEEREvaluator.from_env().evaluate(harness, config, task_case)
```

执行顺序：

1. `harness.start_case(...)` 启动 benchmark session。
2. 初始化 `Trajectory`、动态权重、`EvaluationPolicyState` 和 milestone ready frontier。
3. 每个 raw step 先写入 `trajectory.steps`，并立刻扫描 minefield；fatal minefield 可在下一次 harness 推进前触发策略终止。
4. `AgentStepTracker` 根据规范化 `actor/recipient` 组装完整 agent step 闭包；未闭合 outbound 不触发 milestone matching、checkpoint 或 ready frontier no-progress。
5. 完整 agent step 闭合后才分析当前 ready milestone 是否命中；未命中时更新 ready frontier 无进展 watch。
6. milestone 命中后进入阶段结算，先运行 cheap baseline，再按 `EvaluationPolicyState.dimension_levels` 对指定维度调用 standard 或 expensive judge。
7. 阶段完成后更新逐维权重和下一阶段评估策略。
8. 收尾时，任何未完成 milestone 都会生成 synthetic pending stage；只有全部 milestone 完成且不是策略提前终止时才追加 `__finish__` 阶段。

## Milestone 语义

所有 `MilestoneGraph.nodes` 都是必须完成的 milestone。DAG 并行只表示没有依赖顺序，不表示可选节点。

- `Milestone.required` 已移除。
- adapted case 中出现 `required` 字段会在 loader 解析时直接报错，要求重新生成。
- coverage 规则：全部 milestone matched 为 `full`；至少一个真实 milestone matched 但不是全部为 `partial`；没有 milestone 或没有 matched 为 `none`。
- pending 规则：运行自然结束或策略提前终止后，所有未 matched milestone 都生成 `synthetic_pending_milestone` stage。

## Ready Frontier

`MilestoneFrontierState.ready_ids` 保存当前所有 ready milestone。`ready_milestone_ids(frontier, matched)` 返回尚未 matched 的 ready milestone id，不再过滤 optional/required。

ready frontier 无进展终止会观察当前 ready frontier 内每个 milestone 的最好候选分数。若连续 `ready_frontier_patience` 次观察都没有超过 `ready_frontier_min_delta` 的有效提升，且没有成员达到 pass 阈值，则返回：

- `ready_frontier_no_progress:{most_promising_milestone_id}`
- `milestone_no_progress:{milestone_id}`

ready frontier 的观察单位是完整闭合的 agent step，不是 raw step。agent 发出 tool call 后、tool result 尚未返回前，不会因为该 outbound 立即触发 no-progress。

## Scoring

`GeneralScorer` 支持三类 operator：

- 静态比较：`equals`、`contains`、`one_of`、`json_subsumes`、`fuzzy_match`
- delta 比较：`added`、`removed`、`updated`、`unchanged_since`
- benchmark 专用：`custom`

`ast_match` 已移除。delta operator 的 `reference_milestone_id` 优先从 `ScoringContext.matched_snapshots` 读取对应 milestone 命中时的 snapshot；找不到 reference snapshot 时返回 missing，而不是把 milestone id 当 snapshot id。

`TOOL_CALL` / `TOOL_RESULT` 约束会在当前 milestone 阶段区间中寻找最近的对应 step。这样 milestone 评估延后到 tool result 闭合点后，仍能使用闭包内的 agent tool call 作为证据。

selector/operator 详细规范见 [constraints.md](constraints.md)。

## 阶段结果

`StageEvaluationResult` 只保留逐维置信度/不确定度：

- `dimension_scores: dict[Dimension, float]`
- `dimension_confidence: dict[Dimension, float]`
- `dimension_uncertainty: dict[Dimension, float]`

全局 `uncertainty` 和 `judge_confidence` 已移除。旧的 `top2_score=0.0`、`evidence_conflict=False`、阈值距离不确定性和 missing-ratio composite 公式不再输出。

`stage_score` 由 `stage_score_from_dimensions(dimension_scores, weights)` 计算。partial judge 结果可只包含目标维度；最终阶段报告会合并回完整七维。

## 动态权重

阶段后权重更新公式：

```text
w_next_d = normalize(w_d * exp(alpha * (1 - score_d) + beta * uncertainty_d))
```

默认 `alpha=1.0`，`beta=1.0`。`StageEvaluationResult.metadata["weight_update_diagnostics"]` 会记录每个维度的旧权重、分数、不确定度和新权重。

## 评估策略

`EvaluationPolicyState.dimension_levels` 是逐维调度依据。当前阶段不再通过单个 `effective_level=max(...)` 选择唯一 judge。

- cheap 总是先给完整七维 baseline。
- `STANDARD` 维度只调用 standard judge 并覆盖这些维度。
- `EXPENSIVE` 维度只调用 expensive judge 并覆盖这些维度。
- 某一维 expensive 不会导致其他维度一起 expensive。

下一阶段策略：

- 高分且最大逐维不确定度低于 `low_dimension_uncertainty` 时回到 cheap。
- 低于 fail 阈值的维度升到 expensive。
- 低于 warn 阈值或高于 `high_dimension_uncertainty` 的维度升一档。

## 输出

`HarnessRunResult.evaluation_report.stage_reports[]` 中每个 stage report 包含：

- 阶段身份：`stage_id`、`milestone_id`
- 阶段结果：`status`、`stage_score`、七维 scores/levels/confidence/uncertainty
- evidence/diagnosis
- `metadata.next_evaluation_policy`
- `metadata.evaluation_termination`
- `metadata.dimension_judge_results`
- `metadata.weight_update_diagnostics`
- `metadata.stage_quality_diagnostics`（cheap baseline）

`StageEvaluationResult.metadata` 只用于报告、展示和诊断附加信息，不参与阶段分数、状态聚合、权重更新或策略更新。高阶 judge 的逐维结果应进入 `dimension_scores`、`dimension_levels`、`dimension_confidence` 和 `dimension_uncertainty`；不再把 standard/expensive judge 的整包 metadata 合并进 stage metadata。

自然结束时的 pending stage 使用 `metadata.synthetic_pending_milestone=true`，并通过 `metadata.blocker`、`metadata.pending_predecessor_ids`、`failure_summary`、`failure_reasons` 解释未完成原因。

`overall_score([])` 固定为 `0.0`。空 `stage_reports` 表示没有任何 milestone 证据被结算，不能作为满分兜底。
