# Evaluate API

## 目标

`dynsteer.evaluate` 是一个包，`DynSTEEREvaluator` 是 DynSTEER 的主评估入口。它承担两类能力：

- `evaluate(harness, config, task_case)`: 主实验入口，消费 adapter/loader 已适配的 `TaskCase`，编排 benchmark 原生执行并进行阶段式动态评估。

历史离线整轨迹入口 `evaluate_trajectory()` 已删除；当前只支持运行期 `evaluate()` 主流程。

## 包结构

```text
dynsteer/evaluate/
- __init__.py    # 导出 DynSTEEREvaluator、JudgeConfigurationError、权重工具和通用工具
- evaluator.py   # DynSTEEREvaluator
- runtime.py     # RuntimeEvaluationState、raw_summary、pending milestone、trajectory 与 telemetry
- policy.py      # 跨阶段评估粒度策略
- minefield.py   # 运行期 boundary 级 minefield 扫描
- scoring.py     # GeneralScorer、ScoringContext、权重和阶段结果评分工具
- diagnostics.py # 运行期 stage trace 与 milestone matching 诊断序列化
- milestone.py   # milestone DAG 校验和运行期 step 命中分析
```

`dynsteer/match.py` 已并入 `dynsteer/evaluate/milestone/`，`from dynsteer.match import ...` 不再可用。

## 构造函数

```python
evaluator = DynSTEEREvaluator(
    cheap_judge=None,
    standard_judge=None,
    expensive_judge=None,
    thresholds=None,
    weight_config=None,
)
```

- `cheap_judge`: 默认使用 `CheapJudge`，只用于 cheap 层评估。
- `standard_judge`: standard 层使用的 judge，通常是 `StandardJudge`。
- `expensive_judge`: expensive 层使用的 judge，通常是 `ExpensiveJudge`。
- `thresholds`: 阶段通过、告警、失败和不确定性阈值。
- `weight_config`: 动态维度权重配置。

`DynSTEEREvaluator` 不再接收单个 `llm_judge` 参数；standard 与 expensive 两档分别注入，避免由一个对象在内部按 `EvaluationLevel` 分发。如果阶段调度需要 standard 或 expensive，但对应 judge 未配置，会抛出 `JudgeConfigurationError`，不会回退到 `CheapJudge`。

## from_env

```python
evaluator = DynSTEEREvaluator.from_env()
```

`from_env()` 负责：

1. 通过 `dynsteer.llm.build_llm_from_env(...)` 从环境变量构建 `BaseLLM | None`。
2. 未配置 LLM 时只构建 `CheapJudge`。
3. 已配置 LLM 时构建共享同一个 `BaseLLM` 的 `StandardJudge` 和 `ExpensiveJudge`。

`DYNSTEER_EXPENSIVE_JUDGE_PASSES` 控制 `ExpensiveJudge` 的多轮聚焦评估次数，默认 3。

## 主实验入口

```python
result = evaluator.evaluate(harness, config, task_case)
```

执行流程：

1. 校验 `task_case.case_id`，并调用 `harness.prepare_config(config)` 准备 benchmark 运行配置。
2. 通过 `task_case.case_id` 构造 raw 输出目录并调用 `harness.start_case(...)` 启动原生 session。
3. 将运行配置 metadata 合入 `task_case.metadata`。
4. 初始化单个运行期 `Trajectory`，并在后续循环中增量维护 `steps`、`snapshots`、`final_state` 与 `metrics`。
5. 循环调用 `harness.advance_case(session)` 获取 `HarnessAdvanceResult`。
6. 将 `advance.snapshots` 按 `snapshot_id` 合并到运行期 `Trajectory`。
7. 对每个新增 step 调用 `Trajectory.append_step(...)`，立即按当前 boundary / snapshot / step 扫描 minefield；fatal minefield 且 `config.stop_on_minefield=True` 时复用策略终止路径。
8. 未触发 minefield 停止时，通过 `analyze_milestone_step(...)` 分析 ready milestone 命中、可 LLM 复判的语义消息 warn 候选或 blocked milestone 诊断。
9. ready milestone 的 PASS 候选直接进入阶段结算；当没有 PASS、但存在 `emit_message + semantic_equivalent` 且无 missing/硬约束失败的 WARN 候选时，该候选会进入现有 `_evaluate_checkpoint(...)` 通道，由运行期评估策略指定的 judge 复判。
10. 没有成功匹配 milestone 的 attempt 会更新 required ready frontier 无进展 watch；同一 frontier 连续达到 `config.ready_frontier_patience` 次评分观察无有效提升时，触发 no-progress 策略终止。
11. 阶段结算时使用 `state.evaluation_policy.effective_level()` 选择本阶段唯一 judge；阶段完成后同时更新 `state.weights` 与 `state.evaluation_policy`，不再在同一阶段内执行 cheap -> standard -> expensive 升级链路。
12. 根据阶段结果和策略终止决策执行 fail-fast，必要时调用 `harness.stop_case(session, reason)`。
13. 当 `advance.continue_running is False` 时结束主循环。
14. `harness.raw_summary_from_session(session)` 与 `harness.teardown_case(session)` 完成收尾。

Evaluator 不再从 session 动态提取 `TaskCase`，也不通过空 steps 或 `case_finished()` 控制循环；这些属于 adapter/loader 和 harness 返回契约。

## 历史整轨迹入口

离线整轨迹评估 API 已删除。当前评估链只保留 `evaluate(harness, config, task_case)`，
由 harness 逐步推进 benchmark，并在运行期按当前 step 触发 milestone 结算。

### `GeneralScorer`

`GeneralScorer` 是 DynSTEER 默认 milestone / minefield 评分器，位于 `dynsteer.evaluate.scoring`。
它提供 `score_operator()`、`score_constraint()`、`score_milestone()` 和 `constraint_sources()` 四个核心方法。

`Operator.CUSTOM` 不属于通用 operator。默认 `GeneralScorer` 会返回带 evidence 的 0 分约束结果；benchmark 需要通过 `BaseBenchmarkHarness.constraint_scorer()` 返回专用 scorer 处理 CUSTOM。

### `ScoringContext`

`ScoringContext` 用于在运行期传递当前任务、已命中 milestone 边界和已命中状态快照。ToolSandbox 等 benchmark scorer 可通过该上下文读取 reference snapshot，避免在通用层内硬编码 benchmark 语义。

ToolSandbox 等 benchmark 应保证 `matched_snapshots` 中保存的是同一时刻的完整多 namespace 快照；依赖 `reference_milestone_node_index` 的 custom scorer 会从该快照中按 namespace 读取参考数据。

### Ready Frontier 无进展终止

Evaluator 会把当前 `attempt_detail["ready_before"]` 中 `required=True` 且尚未 matched 的 milestone 作为一个 ready frontier 整体观察。只要 frontier 中任意成员的结构化候选分数相对历史最好分数提升至少 `config.ready_frontier_min_delta`，就认为 frontier 仍在推进，并清零连续 stale 计数。

该策略不要求并行 ready milestone 在同一窗口内全部提升。若某个 milestone matched，当前 watch 会被清空；下一次 attempt 会基于新的 ready frontier 重建基准。optional milestone 不进入 no-progress 终止判断。

相关 `HarnessRunConfig` 字段：

- `stop_on_ready_frontier_no_progress`: 是否启用 ready frontier 无进展策略，默认 `true`，可在 `run_configs.json` 中按 run 覆盖。
- `ready_frontier_patience`: 同一 ready frontier 连续无有效提升的评分观察次数阈值，默认 `8`，只从环境变量 `DYNSTEER_READY_FRONTIER_PATIENCE` 读取。
- `ready_frontier_min_delta`: 判定有效提升的最小分数增量，默认 `0.02`，可在 `run_configs.json` 中按 run 覆盖。

新增 termination code：

- `ready_frontier_no_progress:{most_promising_milestone_id}`: 多个 required ready milestone 组成的 frontier 长期无有效提升。
- `milestone_no_progress:{milestone_id}`: 单个 required ready milestone 长期无有效提升。

## 成员评估函数

- `evaluate_minefields(graph, trajectory, scorer=None, context=None) -> (matches, max_score, fatal)`: 对轨迹历史 boundary 扫描 minefield，并返回带 `boundary_id` 与 `penalty` 的命中记录。

评估粒度不再通过公开的 `select_evaluation_level(...)` 在同阶段临时升级，而是由 `RuntimeEvaluationState.evaluation_policy` 保存跨阶段策略：

- 当前阶段读取 `active_evaluation_policy.effective_level()` 选择 cheap / standard / expensive。
- 当前阶段完成后根据阶段分数、不确定性、fatal minefield 和逐维分数生成 `next_evaluation_policy`。
- 低分阶段生成 `evaluation_policy_stop` 终止决策，不升级到 expensive。

## Judge 与 LLM

阶段评估器定义见 `docs/apis/judges.md`，LLM provider 定义见 `docs/apis/llm.md`。

## 输出

`DynSTEEREvaluator.evaluate()` 返回 `HarnessRunResult`，其中：

- `trajectory`: 当前完整或被 fail-fast 截断的轨迹。
- `stage_settlements`: 运行期阶段结算。
- `evaluation_report`: 主实验评估报告。
- `terminated_by_policy`: 是否被 DynSTEER 策略提前终止。
- `termination_code` / `termination_reason`: 策略终止摘要。
- `raw_summary["runtime_metrics"]` 与 `evaluation_report.runtime_metrics`: 当前 case 的运行统计，包含 `elapsed_seconds`、`step_count`、`tool_call_count`、轨迹 token/latency 聚合和 LLM judge token usage 聚合。

`StageEvaluationResult.stage_id` 使用 `stage_goal_key(anchor_milestone_id, milestone_id)` 生成，格式为 `{anchor_milestone_id}->{milestone_id}`，例如 `__start__->m0`、`m3->m4`。运行期不再生成 `runtime:stN`、`runtime:fail:<milestone_id>` 或 `runtime:missing:<milestone_id>` 作为阶段身份。

`__finish__` 只表示完整 agent 轨迹中未被此前 milestone 锚定阶段覆盖的正常尾段。只有当前序 required milestone 没有 pending / fail / missing，并且流程未被策略提前终止时，运行自然结束才会生成 finish 阶段报告。该阶段的 `stage_id` 为 `{finish_stage_anchor_predecessor_id}->{finish_node_id}`，例如 `m4->__finish__`；若 adapted case 的 `stage_goals` 未显式提供该 key，评估器使用默认 finish 目标文案。若自然结束后仍存在 pending required milestone，评估报告以对应 pending stage 结束，不再追加 `stage_settlements(kind="finish")` 或 `__finish__` stage report。

`stage_settlements[].metadata` 会包含运行期排查字段：

- `stage_trace`: 当前阶段左开右闭区间 `(start_boundary_step_index, end_step_index]` 内的轨迹步骤详情，包含 step id、index、actor、event_type、content、tool_call、tool_result、cost 和 adapter raw 字段。
- `milestone_matching`: milestone 匹配诊断。`mode="runtime_checkpoint"` 表示本阶段由 milestone checkpoint 触发，包含命中的 milestone、boundary、milestone score、constraint scores、命中前 ready milestone 和已匹配 milestone；`mode="runtime_finish"` 表示自然完成阶段，包含已匹配 milestone 与 pending required/optional milestone 列表。
- `stage_report.metadata.active_evaluation_policy`: 本阶段使用的评估粒度策略。
- `stage_report.metadata.next_evaluation_policy`: 本阶段完成后生成的下一阶段策略。
- `stage_report.metadata.evaluation_policy_update`: 是否触发策略终止及终止原因。
- `stage_report.metadata.uncertainty_inputs`: 最终 `uncertainty` 的审计输入，包含 top1/top2、缺失比例、阶段分数和 judge uncertainty。
- `stage_report.metadata.stage_quality_diagnostics`: cheap judge 使用的阶段区间质量诊断，包含工具失败、空结果、参数别名、grounding warning 和效率统计。

`evaluation_report.minefield_matches[]` 中每条命中包含 `minefield_id`、`boundary_id`、`boundary_step_index`、`score`、`severity`、`evidence` 和 `penalty`。最终 `overall_score` 使用 `minefield_penalty_score(matches)` 聚合 penalty 后扣分；`penalty.mode="fixed"` 时按 `score * penalty.value` 计算扣罚比例。

`raw_summary` 会额外包含以下运行期诊断字段：

- `task_case_snapshot`: 当前 case 的轻量任务快照，`case_id` 来自 `task_case.case_id`，同时包含 `task_id`、`task_description`、`task_types`、`scenario_name`、`categories` 和首条用户消息摘要。
- `milestone_graph_summary`: milestone 图定义摘要。
- `milestone_match_attempts`: 每次 checkpoint 匹配尝试的候选详情。
- `milestone_final_diagnostics`: 运行结束后每个 milestone 的最终匹配状态。
- `runtime_quality_diagnostics`: 不参与评分的轨迹质量诊断，包含 `tool_argument_warnings`、`empty_tool_results`、`failed_tool_results`、`grounding_warnings` 和 `efficiency`。该字段用于解释“工具调用发生但参数/结果没有推进任务”“工具返回空值后 agent 仍给出具体事实答案”“最终状态正确但用户额外负担较高”等情况。

当 ready frontier 无进展策略触发提前终止时，`raw_summary["termination_detail"]` 会包含 `ready_milestone_ids`、`most_promising_milestone_id`、`ready_since_step_index`、`last_frontier_improved_step_index`、`stale_frontier_observation_count`、`frontier_observation_count`、`patience`、`min_delta` 和各 milestone 的 `best_score` / `best_status` / `best_boundary_step_index` / `last_improved_step_index`。

`runtime_quality_diagnostics.empty_tool_results[]` 中每条记录包含：

- `severity`: `info` 或 `warning`。状态修改工具成功返回空载荷时为 `info`；查询类或未知工具成功返回空载荷时为 `warning`。
- `result_category`: `state_mutation_no_payload`、`query_empty_payload` 或 `unknown_empty_payload`。

只有 `severity="warning"` 的空返回会计入 `warning_count`，并可能触发 `grounding_warnings`。`severity="info"` 的空返回仍保留在 `empty_tool_results` 中，方便审计工具调用行为，但不视为风险。

运行自然结束时仍未完成的 required milestone 会生成 synthetic pending stage，并带有 `metadata.synthetic_pending_required=true`。pending report 沿用该 milestone 对应的固定 `stage_id`，只通过 `status` 表达失败类型：

- `status="fail"`：milestone 已 ready 或已有候选尝试，但未通过。
- `status="missing"`：milestone 未 ready、无候选尝试，或因前驱未匹配导致当前 milestone 不可评估。

本方案不新增 `blocked` 状态。若前驱未匹配，阻塞原因记录在 `metadata.blocker="predecessor_not_matched"` 和 `metadata.pending_predecessor_ids` 中。
synthetic pending stage 的 `uncertainty=1.0`，诊断中会标记 required milestone 未完成导致结果高风险。

`stage_count` 表示当前报告中已评估或补充诊断的 `stage_reports` 数量。静态看板中的 `stage_definitions` 表示当前 scenario 实际应展示的阶段定义；提前退出或 pending required 后，后续未进入评估流程的阶段不会强行写入 `stage_reports`。

当 WARN 候选进入语义消息 LLM 复判时，`milestone_match_attempts[]` 会包含 `llm_semantic_review`：

- `status="candidate"`：结构化分数为 warn，但满足 LLM 复判条件。
- `status="accepted"`：standard judge 判 PASS，已结算 milestone。
- `status="rejected"`：standard judge 未判 PASS，milestone 不结算。
- `status="skipped_no_standard_judge"`：未配置 standard judge，保持原 pending 行为。

当 `task_case_snapshot.task_description` 与首条用户消息摘要不一致时，Evaluator 仍输出 `evaluator_task_description_mismatch` warning，不修改 `TaskCase` 原始字段；LLM judge prompt 模板会直接声明 `stage_goal` 优先于 `task.task_description`。

milestone graph 的直接前驱和阶段锚点字段来自 adapter/loader 阶段的预分析：`stage_anchor_predecessor_id` 是在原始 milestone DAG 增加 `__start__` 超级源和 `__finish__` 超级汇后计算得到的直接支配节点。运行期 ready 判定、路径断裂诊断和 stage interval 构造只读取 `Milestone.dependency_predecessor_ids` 与 `Milestone.stage_anchor_predecessor_id`，不在 checkpoint 时重新扫描 `graph.edges`。

`Trajectory` 会维护 `first_step_index` 与 `successor_by_boundary`，运行期阶段起点通过 `stage_start_step_index(successor_by_boundary, boundary_index, end_step_index)` 查询，不再扫描完整 `trajectory.steps`。`StageInterval` 使用左开右闭语义：`start_boundary_step_index < step.index <= end_step_index`。`start_boundary_step_index` 是 anchor 边界 step，不纳入当前阶段；`start_step_index` 是该区间实际纳入评估的首个 step。

`Trajectory.get_interval(min_index, max_index)` 使用左开右闭语义 `min_index < step.index <= max_index`。当 root stage 的 synthetic boundary 小于首个真实 step index 时，函数会把有效下界钳到 `first_step_index - 1`，不会排除首个真实 step。

## 运行期日志

Evaluator 会通过 `dynsteer.evaluate.runtime.telemetry` 构造短结构化日志：

- `evaluator_policy_stop`: 策略提前终止，记录 termination code、matched/pending milestone、stage 结果和首条诊断。
- `evaluator_task_description_mismatch`: `task_description` 与首条用户消息摘要不一致时的观测性 warning。
- `harness_teardown_failed`: benchmark session 资源释放失败时的错误日志。

普通终端日志使用 `TerminalLogFormatter` 展示短文本；进度条运行期间终端日志会被静默，文件日志和内存日志缓冲区仍保留完整结构化 extra。milestone 命中诊断进入 `raw_summary.stage_settlements[].metadata`，不再通过 INFO 日志输出。日志 formatter 会对 dict/list extra 做 JSON 追加，并截断过长字段，避免输出完整 prompt、表格或大型 raw 数据。

`runs/` 与 `results/` 下的 `raw_summary.json`、`report.json`、`summary.json` 由 `dynsteer.harness.runner` 直接序列化评估结果写出，不依赖终端日志过滤。
