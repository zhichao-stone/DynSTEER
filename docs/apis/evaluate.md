# Evaluate API

## 目标

`dynsteer.evaluate` 是轻量包标记，运行期阶段式评估入口从具体子模块显式导入。主流程仍是 `DynSTEEREvaluator.evaluate(harness, config, task_case)`；实验层另提供 `DynSTEEREvaluator.evaluate_replay(task_case, trajectory, scorer, config)`，用于对同一条完整轨迹执行离线阶段式回放。两个入口的当前 case 身份都来自 `task_case.case_id`，不要求调用方把 `HarnessRunConfig.case_ids` 改写成单元素元组。

## 主流程

```python
from dynsteer.evaluate.evaluator import DynSTEEREvaluator

result = DynSTEEREvaluator.from_env().evaluate(harness, config, task_case)
```

执行顺序：

1. `harness.start_case(...)` 启动 benchmark session。
2. 若 `harness.initial_state_from_session(...)` 返回真实运行期初始状态，则覆盖内存中的 `TaskCase.initial_state`，并在 `task_case_snapshot` 记录来源摘要。
3. 初始化 `Trajectory`、动态权重、`EvaluationPolicyState` 和 milestone ready frontier。
4. 每个 raw step 先写入 `trajectory.steps`，并立刻扫描 minefield；fatal minefield 可在下一次 harness 推进前触发策略终止。
5. `AgentStepTracker` 根据规范化 `actor/recipient` 组装完整 agent step 闭包；未闭合 outbound 不触发 milestone matching、checkpoint 或 ready frontier no-progress。
6. 完整 agent step 闭合后才分析当前 ready milestone 是否命中；未命中时更新 ready frontier 无进展 watch。
7. milestone 命中后进入阶段结算，先读取 `TaskCase.stage_evaluation_specs[stage_id].focus_dimensions`，cheap/standard/expensive judge 都只评估本阶段聚焦维度。
8. 阶段完成后仅根据本阶段实际评估维度更新权重和下一阶段评估策略；未评估维度不会被当成 0 分。
9. 收尾时，任何未完成 milestone 都会生成 synthetic pending stage；只有全部 milestone 完成且不是策略提前终止时才追加 `__finish__` final verification 阶段。

## Replay 流程

```python
result = evaluator.evaluate_replay(task_case, trajectory, scorer, config)
```

Replay 不启动 benchmark session，也不会调用 `harness.stop_case()`。它把完整 `Trajectory` 按 step 顺序重新喂入 minefield、agent step closure、milestone matching 和 checkpoint 逻辑。若策略触发提前终止，结果写入 `termination` 与 stage metadata 的 `replay_virtual_stop`；默认在虚拟早停后停止扫描，并在继续配置为 `true` 时才完整扫描轨迹，finish metadata 中则标记 `finish_after_virtual_stop=true`。

实验 runner 在调用 replay 前会读取 default `trajectory.json` 顶层 raw 字段中的 `runtime_initial_state`，并覆盖当前 replay `TaskCase.initial_state`。因此 replay 的 `ScoringContext["initial"]` 与 default session 使用同一份真实初始状态，避免 `preserve_state` 被 adapted JSON 中的空 expected 占位或过期动态值误导。

`EvaluationStrategyConfig` 控制 replay 与在线评估共享的策略开关：

- `dynamic_routing`: 关闭后固定使用 `fixed_judge_level`。
- `dynamic_weighting`: 关闭后下一阶段权重保持不变。
- `policy_stop`: 关闭后不产生在线 stop 或 replay 虚拟 stop。
- `replay_continue_after_virtual_stop`: replay 虚拟早停后是否继续扫描完整轨迹，默认关闭。

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

## 闭包 Route 匹配

`AgentStepTracker` 在 agent outbound 闭合时会返回完整闭包步骤组。milestone 匹配仍只评分一次：若 milestone 的 `metadata.milestone_matching.route_groups` 中存在唯一 route，则从当前闭包内选择最后一个 sender/recipient 匹配的 step 构造评分 boundary；无 route 或闭包内找不到匹配 step 时沿用闭包终点。当前实现显式拒绝同一 milestone 存在多个 route group 的 adapted case。

## Scoring

`GeneralScorer` 支持三类 operator：

- 静态比较：`equals`、`contains`、`one_of`、`json_subsumes`、`fuzzy_match`
- delta 比较：`added`、`removed`、`updated`、`unchanged_since`
- benchmark 专用：`custom`

`ast_match` 已移除。delta operator 的 `reference_milestone_id` 优先从 `ScoringContext.matched_snapshots` 读取对应 milestone 命中时的 snapshot；找不到 reference snapshot 时返回 missing，而不是把 milestone id 当 snapshot id。

`TOOL_CALL` / `TOOL_RESULT` 约束会在当前 milestone 阶段区间中寻找最近的对应 step。这样 milestone 评估延后到 tool result 闭合点后，仍能使用闭包内的 agent tool call 作为证据。

selector/operator 详细规范见 [constraints.md](constraints.md)。

ToolSandbox 专用 scorer 对 `preserve_state` 约束会把 `target_dataframe` 解析为 runtime reference snapshot；`set_state`、`emit_message`、`tool_call` 等其他语义仍使用 `constraint.expected`。诊断 evidence 会显示 `target_source=reference_snapshot` 或 `target_source=constraint.expected`，便于区分真实目标与 adapted case 的序列化占位。

## 阶段结果

`StageEvaluationResult` 只保留逐维置信度/不确定度：

- `dimension_scores: dict[Dimension, float]`
- `dimension_confidence: dict[Dimension, float]`
- `dimension_uncertainty: dict[Dimension, float]`

全局 `uncertainty` 和 `judge_confidence` 已移除。旧的 `top2_score=0.0`、`evidence_conflict=False`、阈值距离不确定性和 missing-ratio composite 公式不再输出。

`stage_score` 由 `stage_score_from_dimensions(dimension_scores, weights)` 计算。阶段报告只包含本阶段实际聚焦维度；未聚焦维度不参与当前阶段综合分、权重更新或策略升级。

`TaskCase.stage_evaluation_specs` 使用与真实 `stage_goals` 相同的 stage key。每个 `StageEvaluationSpec` 至少包含 `progress` 与 `efficiency`，并基于公共 `Constraint.stage_goal_semantics`、`ConstraintTarget` 与 benchmark-neutral metadata 增补 `tool_quality`、`state_consistency`、`interaction_quality`、`safety`、`recovery` 等维度。

## 动态权重

阶段后权重更新公式：

```text
w_next_d = normalize(w_d * exp(alpha * (1 - score_d) + beta * uncertainty_d))
```

默认 `alpha=1.0`，`beta=1.0`。`StageEvaluationResult.metadata["weight_update_diagnostics"]` 会记录每个维度的旧权重、分数、不确定度和新权重。

## 评估策略

`EvaluationPolicyState.dimension_levels` 是逐维调度依据。当前阶段不再通过单个 `effective_level=max(...)` 选择唯一 judge。

- cheap 总是先给本阶段聚焦维度 baseline。
- `STANDARD` 维度只调用 standard judge 并覆盖这些维度。
- `EXPENSIVE` 维度只调用 expensive judge 并覆盖这些维度。
- 某一维 expensive 不会导致其他维度一起 expensive。
- 对 `emit_message` 且 `match_policy=semantic_equivalent` 的候选，运行期会在 checkpoint 前执行 constraint 级专用消息语义复判。该复判固定单次调用 LLM，只比较 expected content 与 actual content 在指定 sender/recipient route 下是否任务语义等价，不评价工具选择、效率、安全或完整阶段质量；复判 accepted 且置信度达标时，会把对应消息约束覆写为通过并重新聚合 `MilestoneScore`，随后按正常 cheap/standard/expensive 阶段结算。复判详情写入 `match_attempts[].llm_semantic_review`；accepted checkpoint 的阶段报告额外写入 `metadata.semantic_message_review`。若复判 rejected，pending stage 的 `metadata.semantic_review`、`failure_summary` 和 `failure_reasons` 会保留拒绝原因。

下一阶段策略：

- 高分且最大逐维不确定度低于 `low_dimension_uncertainty` 时回到 cheap。
- 低于 fail 阈值的已评估维度升到 expensive。
- 低于 warn 阈值或高于 `high_dimension_uncertainty` 的已评估维度升一档。
- 未评估维度保持上一阶段粒度，不会因缺失分数被升级。

策略提前终止只由结构性失败、`stage_score < fail_threshold`、fatal minefield 或显式 `missing_required_milestone` 触发。单个 higher-cost judge 返回某个质量维度 `fail`，但阶段综合分达标且没有结构性失败时，不会直接阻断 case 继续执行。

## 输出

`HarnessRunResult.evaluation_report.stage_reports[]` 中每个 stage report 包含：

- 阶段身份：`stage_id`、`milestone_id`
- 阶段结果：`status`、`stage_score`、聚焦维度的 scores/levels/confidence/uncertainty
- evidence/diagnosis
- `metadata.next_evaluation_policy`
- `metadata.evaluation_termination`
- `metadata.dimension_judge_results`
- `metadata.focus_dimensions`
- `metadata.dimension_rationale`
- `metadata.evaluation_strategy`
- `metadata.low_score_dimensions`
- `metadata.weight_update_diagnostics`
- `metadata.stage_quality_diagnostics`（cheap baseline）
- `metadata.semantic_message_review`（仅消息语义复判 accepted 后出现）

`StageEvaluationResult.metadata` 主要用于报告、展示和诊断附加信息；其中 `structural_failure`、`missing_required_milestone` 等结构化标记会参与早停判断，但不参与阶段分数或权重更新。高阶 judge 的逐维结果应进入 `dimension_scores`、`dimension_levels`、`dimension_confidence` 和 `dimension_uncertainty`；不再把 standard/expensive judge 的整包 metadata 合并进 stage metadata。

自然结束时的 pending stage 使用 `metadata.synthetic_pending_milestone=true`，并通过 `metadata.blocker`、`metadata.pending_predecessor_ids`、`failure_summary`、`failure_reasons` 解释未完成原因。若最后一次专用消息语义复判 rejected，`metadata.semantic_review` 会保留复判目标、expected/actual 内容、置信度和原因。

`stage_settlements[].metadata.stage_trace.state_snapshot_delta_summary` 提供轻量状态变化摘要，只包含命名空间行数、变化标记与 changed namespace 列表，不嵌入完整状态数据。

`__finish__` 阶段不再继承上一阶段动态 judge 策略，也不要求 agent 在最后区间额外调用验证工具。它由 `dynsteer.evaluate.final.build_finish_verification(...)` 基于真实 milestone 覆盖、terminal 状态约束重检、terminal 消息约束确认和 fatal minefield 生成确定性 final verification payload，并写入 `metadata.finish_stage_evaluation`。空 milestone graph 且没有 whole-trajectory fallback 时不会默认通过：若已触发 fatal minefield，finish 为 `fail/0`；否则为 `invalid/0`。

finish payload 中的 `terminal_state_checks` 只包含 `set_state`、`preserve_state`、`STATE_DELTA` 和真实持久状态快照约束的最终边界重检。`emit_message`、`user_visible_required=true` 以及 ToolSandbox `SANDBOX` 用户可见消息约束不会在 `end_conversation` 后用最后的 `None` 重评；这些约束会进入 `terminal_message_checks`，表示它们已由原 terminal milestone 匹配结果确认。

evidence 会通过 `dynsteer.utils.clean_evidence_items(...)` 清洗：当存在 `step N: ...` 具体证据时，裸 `step N` 或覆盖该 step 的裸区间引用会被移除。

`overall_score([])` 固定为 `0.0`。空 `stage_reports` 表示没有任何 milestone 证据被结算，不能作为满分兜底。
