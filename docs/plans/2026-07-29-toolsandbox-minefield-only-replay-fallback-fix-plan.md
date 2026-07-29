# 2026-07-29 ToolSandbox 空 milestone graph replay 终态评估修复方案

## 1. 修订结论

基于 `docs/plans/analysis/2026-07-29-toolsandbox-experiment-results-and-runs-audit-report.md`，当前 strict 成败不一致的两个 case 仍然是：

- `modify_contact_with_message_recency_insufficient_information_10_distraction_tools`
- `modify_contact_with_message_recency_insufficient_information_3_distraction_tools`

这两个 case 的共同现象：

1. ToolSandbox 原生 default 运行完整结束，原生评估为 `resolved=true`、`score=1.0`。
2. DynSTEER-Replay 读取同一条轨迹后，结果为 `milestone_coverage=none`、`overall_score=0.0`、finish stage `status=invalid`。
3. 轨迹中没有触发危险 `modify_contact` minefield；agent 在用户补充授权/信息后执行 `search_contacts -> add_contact`，语义上更像完成了替代路径。
4. adapted case 的 `milestone_graph.nodes=[]`，`milestone_graph.minefields=[mf0]`，也就是没有固定正向 milestone，但存在安全 minefield。

修订后的判断：**问题不是 agent 轨迹天然失败，也不是应该借用 default 原生结论判成功，而是 DynSTEER 对空 milestone graph 缺少自身的完整轨迹终态评估路径。**

空 milestone graph 不应被解释为“一定 invalid”。更准确的语义是：当前任务 case 没有可提前切分的固定执行节点，因此阶段式流程应退化为：

1. 执行或读取完整轨迹。
2. 在完整轨迹上持续扫描 minefield。
3. 结束时把 `__start__->__finish__` 作为一个 whole-trajectory stage。
4. 由 DynSTEER 自己基于任务描述、完整轨迹、工具结果、快照/最终状态和 minefield 结果判断任务是否完成。

因此，本方案必须废弃旧方案中的 `default_reference` fallback 思路。`default_reference` 只能作为实验对照和审计元数据存在，不能参与 replay 的 `status`、`score`、`milestone_coverage`、minefield 判定、stage judge 或 finish 判定。

## 2. 当前代码成因

### 2.1 `dynsteer/evaluate/final.py`

当前 `build_finish_verification()` 在空 graph 时直接进入 `_empty_graph_finish_verification(state)`：

```python
if not graph.nodes:
    return _empty_graph_finish_verification(state)
```

当前空图分支只有两个结果：

- 触发 fatal minefield：`fail / 0.0`
- 未触发 fatal minefield：`invalid / 0.0`

这个逻辑的问题不是“不相信 default”，而是把“没有固定 milestone”误当成“没有可评估任务目标”。对于空 milestone graph，任务目标仍然存在于 `TaskCase.task_description`、policy/minefield、完整轨迹和最终状态中，只是不能用 milestone DAG 切成多个固定阶段。

### 2.2 `dynsteer/evaluate/settlement.py`

当前 `finish_settlement()` 只调用确定性 final verification：

```python
verification = build_finish_verification(task_case, trajectory, state, scorer)
```

它没有 whole-trajectory judge 路径，因此空图自然结束时无法进入普通 stage judge，也无法基于完整轨迹做语义终态判断。

另外当前 finish stage 的：

```python
hard_constraints_all_pass = status != StageStatus.FAIL
```

会把 `invalid` 也标成 hard constraints pass，不利于后续诊断。应改成只有 `PASS/WARN` 才算硬约束通过。

### 2.3 `dynsteer/evaluate/evaluator.py`

`evaluate_replay()` 的主体流程是正确的：它按 default 轨迹 step 顺序回放，并用 DynSTEER 自己的 minefield、milestone matching、checkpoint 和策略逻辑重新评估。

但 `_runtime_report()` 对空 graph 固定写：

```python
if not graph.nodes:
    coverage = "none"
```

如果后续新增 whole-trajectory finish stage，这里也需要改成根据 whole-trajectory finish 结果计算 coverage，否则 pass 的空图仍会在 summary 里显示 `none`。

### 2.4 `dynsteer/stage/resolve.py` 与 `dynsteer/stage/spec.py`

当前 finish 阶段目标是：

```text
完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。
```

这个目标适合有 milestone 的 case，因为 finish 只需要复核已完成 milestone 是否被最终状态推翻；但它不适合空 graph。空 graph 没有“已达成的阶段目标”，finish 应改为“完整轨迹任务完成度评估”。

当前 `resolve_stage_evaluation_spec()` 对 finish 只返回 `progress` 和 `state_consistency`。空 graph whole-trajectory stage 需要覆盖更多维度，至少包括 `progress`、`state_consistency`、`tool_quality`、`safety`、`interaction_quality`、`efficiency`，并建议包含 `recovery`，因为这类 insufficient information case 的关键行为常常是澄清、恢复和替代完成。

## 3. 修复原则

1. **空 milestone graph 不是 invalid 的充分条件。**  它表示没有固定阶段节点，评估应退化为完整轨迹终态评估。
2. **无 milestone 不等于自动成功。**  未触发 minefield 只能证明没有命中已建模的违规路径，不能单独证明任务完成。
3. **replay 判定必须独立于 default 原生结论。**  default 只提供被回放的轨迹事实和对照元数据；`default_reference.resolved`、`default_reference.score` 不能影响 replay 判定。
4. **fatal minefield 优先级最高。**  只要 DynSTEER 在 replay 中命中 fatal minefield，whole-trajectory stage 必须失败。
5. **whole-trajectory 成败由 DynSTEER 自己判断。**  当前最小实现应使用 `StandardJudge` 对完整轨迹做一次终态评估；未来可扩展 benchmark 专用终态 scorer，但不能调用 default 原生 evaluator 的结果作为裁判。
6. **没有可用 whole-trajectory judge 时保持 invalid。**  如果空 graph 未触发 fatal minefield，但没有配置 `StandardJudge` 或等价终态评估器，结果应是 `invalid / 0.0`，并明确诊断“缺少 DynSTEER whole-trajectory evaluator”。
7. **coverage 需要标注依据。**  空 graph 的 `milestone_coverage` 不再表示 milestone 覆盖率，而是 whole-trajectory completion：finish `pass` 记为 `full`，finish `warn/ambiguous` 记为 `partial`，finish `fail/invalid/missing` 记为 `none`，并在 metadata 写明 `coverage_basis="whole_trajectory"`。
8. **报告必须透明。**  finish metadata 要写入 `empty_milestone_graph=true`、`fixed_milestones_applicable=false`、`whole_trajectory_evaluation=true`、`default_reference_used=false`、`minefield_count`、`fatal_minefield`、`judge_level` 和被评估的维度。

## 4. `default_reference` 的作用说明

`default_reference` 当前来自实验层，而不是 evaluator 判定层。

### 4.1 来源

在 `dynsteer/experiment/runner.py` 中，replay 方法会先确保同一 case 的 default 运行已经完成：

```python
default_output, default_reference = _get_default_case_outputs(spec, task_case, results, default_outputs)
output = run_replay_case(spec, task_case, default_output, default_reference)
```

`default_reference` 本质上是 `ToolSandboxHarness.default_result_from_session()` 返回的 `BenchmarkDefaultResult.to_dict()`，字段主要包括：

- `score`: benchmark 原生分数，归一到 `[0, 1]`。
- `resolved`: benchmark 原生是否认为任务解决。
- `raw`: 原生 evaluator 的 milestone/minefield similarity、mapping、turn_count 等摘要。
- `metrics`: 原生评估统计。

### 4.2 传递路径

`run_replay_case()` 会做两件事：

1. 从 default 输出目录读取 `trajectory.json`，构造 replay 使用的 `Trajectory`。
2. 把 `default_reference` 传给 `write_replay_case_outputs()`。

`write_replay_case_outputs()` 目前会把 `default_reference` 写入 replay config metadata，随后 `_report_metadata()` 把它带进 `report.metadata` 和 `summary.metadata`。实验索引 `_case_result_from_output()` 也会用它填充 replay case 的 `default_score`，便于同一行中同时展示 default 分数和 DynSTEER 分数。

### 4.3 合理用途

`default_reference` 的合理用途只有这些：

1. **实验对照。**  在 replay 报告中保留 default 原生结果，便于比较 `default_score` 与 `dynsteer_score`。
2. **轨迹溯源。**  说明 replay 使用的轨迹来自哪次 default run，并能追溯原生 `raw` 结果。
3. **汇总补字段。**  当 replay summary 本身没有 `default_score` 时，实验索引用 `default_reference.score` 补齐对照列。
4. **审计差异。**  当 default 和 replay 不一致时，人工可以查看 default 原生 mapping 与 DynSTEER stage report 的差异来源。

### 4.4 明确禁止的用途

`default_reference` 不应承担这些职责：

1. 不能作为 replay finish 的 `pass/fail` 判定依据。
2. 不能作为 replay `overall_score` 或 finish `stage_score` 的来源。
3. 不能作为 `milestone_coverage=full` 的依据。
4. 不能改变 minefield 扫描结果。
5. 不能改变 milestone matching、ready frontier、policy stop 或 pending stage。
6. 不能进入 judge prompt，避免 LLM 被 default 原生结论暗示。

简言之：**default 只提供 replay 要回放的事实轨迹；default_reference 只提供报告对照；DynSTEER-Replay 的所有评估结论必须由 DynSTEER 自己从轨迹和 case 语义中产生。**

## 5. `dynsteer_replay` 完整运行算法流程

### 5.1 实验层入口

1. `run_experiment()` 展开 benchmark、model、method、repeat、threshold profile 矩阵。
2. 对 `dynsteer_replay` / `dynsteer_replay_static`，先调用 `_get_default_case_outputs()`。
3. `_get_default_case_outputs()` 若缓存不存在，则以 `default` 方法完整运行 benchmark，并写出：
   - `default_report.json`
   - `summary.json`
   - `raw_summary.json`
   - `trajectory.json`
4. `run_default_case()` 从 `raw_summary.default_result` 读取原生结果，作为 `default_reference` 返回。
5. `run_replay_case()` 读取 default 的 `trajectory.json`，通过 `load_trajectory()` 还原完整轨迹。
6. 如果 `trajectory.raw.runtime_initial_state` 存在，replay 会把它写入当前 `TaskCase.initial_state`，确保状态约束使用同一条轨迹固化的真实初始状态。
7. 构造 `DynSTEEREvaluator.from_config(config, strategy=spec.strategy)`。
8. 调用 `write_replay_case_outputs(...)` 写 replay 产物。

### 5.2 replay 输出层

`write_replay_case_outputs()` 做以下事情：

1. 复制原 config metadata。
2. 把 `default_reference` 放进 metadata，用于输出对照。
3. 调用：

```python
harness_result = evaluator.evaluate_replay(
    task_case=task_case,
    trajectory=trajectory,
    scorer=harness.constraint_scorer(),
    config=replay_config,
)
```

4. 将 `evaluate_replay()` 产生的 report、summary、raw_summary 和 replay 后轨迹写入对应目录。

注意：第 2 步只是输出元数据准备，不应被 `evaluate_replay()` 读取成判定依据。

### 5.3 evaluator replay 主循环

`evaluate_replay()` 不启动 benchmark session，也不会调用 `harness.stop_case()`。它只消费已经存在的完整轨迹。

主循环如下：

1. 创建空的 `replay_trajectory`，复制 source trajectory 的 `final_state`、`metrics`、`raw`。
2. 调用 `_initial_runtime_state(task_case)` 初始化：
   - 动态权重 `weights`
   - start settlement `st0`
   - milestone ready frontier
   - 空的 matched settlements、stage reports、minefield matches
3. 按 `trajectory.steps` 的原始顺序逐步回放。
4. 每个 step 先追加到 `replay_trajectory.steps`。
5. 追加所有 `after_step_index <= 当前 step.index` 的 snapshots。
6. 对当前 step 构造 boundary，并调用 `evaluate_step_minefields()` 扫描 minefield。
7. 若 minefield 命中：
   - 记录到 `state.minefield_matches`
   - 更新 `state.max_minefield_score`
   - 若 fatal 且 `policy_stop=true`，记录 `virtual_termination`
8. 如果已经虚拟早停且 `replay_continue_after_virtual_stop=false`，停止继续扫描剩余轨迹。
9. 将 step 喂给 `AgentStepTracker`。
10. 如果 agent outbound 尚未闭合，不做 milestone matching。
11. 如果闭合出一个完整 agent step，调用 `_evaluate_closed_agent_step()`。
12. `_evaluate_closed_agent_step()` 会：
    - 用当前 ready frontier 分析可匹配 milestone。
    - 对候选 milestone 调用 benchmark scorer 评分。
    - 必要时做 `emit_message` 语义复判。
    - 命中后调用 `evaluate_checkpoint()` 结算阶段。
    - 未命中时更新 ready frontier 无进展诊断，并可能产生虚拟早停。
13. `evaluate_checkpoint()` 会：
    - 构造当前 milestone 的 `StageInterval`。
    - 调用 `_evaluate_stage()` 完成本阶段 cheap / standard / expensive 维度评估。
    - 写入 settlement、stage report。
    - 推进 milestone frontier。
    - 更新动态权重和下一阶段评估策略。
    - 根据 minefield、结构性失败或低分判断是否策略终止。
14. replay 结束后，如果允许继续且仍有可自然闭合的 final agent message，调用 `AgentStepTracker.finalize()` 补一次闭包评估。
15. 调用 `_finalize_state_reports()` 收尾。

### 5.4 replay 收尾

`_finalize_state_reports()` 当前逻辑：

1. 写入 `state.evaluation_termination`。
2. 若存在未完成真实 milestone，生成 synthetic pending stage 并返回。
3. 若在线 `evaluate()` 已策略提前终止且不允许 finish，则不追加 finish。
4. replay 调用时 `finish_on_termination=true`，因此在没有 pending milestone 时可以追加 finish，并把虚拟早停信息写入 finish metadata。
5. 当前 finish 对有 milestone graph 的 case 做确定性 final verification。
6. 修复后，空 milestone graph 应在这里进入 whole-trajectory finish stage。

### 5.5 报告生成

`_build_runtime_result()` 调用 `_runtime_report()` 生成最终 `TrajectoryEvaluationReport`：

1. `stage_reports` 来自 runtime state。
2. `minefield_matches` 来自 replay 期间扫描结果。
3. `overall_score = overall_score(stage_reports, minefield_penalty_score(minefield_matches))`。
4. `first_failure_stage_id` 取第一个 `fail/missing/invalid` stage。
5. `metadata` 保留 method、strategy、judge profile、threshold profile、model、repeat，以及对照用的 `default_reference`。

修复后，空 graph 的 coverage 应根据 whole-trajectory finish stage 状态计算，而不是固定 `none`。

## 6. 详细代码修改方案

### 6.1 修改 `dynsteer/stage/resolve.py`

新增 whole-trajectory finish stage goal 文案：

```python
DEFAULT_WHOLE_TRAJECTORY_STAGE_GOAL = "完整轨迹终态评估：根据任务描述、全部轨迹步骤、工具结果、最终状态与安全约束，判断任务是否完成且未发生违规。"
```

英文版本同步补充。

调整 `resolve_stage_goal()`：

```python
if milestone_id == FINISH_NODE_ID and _empty_milestone_graph(task_case):
    return _whole_trajectory_stage_goal(language)
```

保留普通 finish 语义：有真实 milestone 的 case 仍使用“确认已达成阶段目标没有被后续证据推翻”。

### 6.2 修改 `dynsteer/stage/spec.py`

调整 `resolve_stage_evaluation_spec()` 的 finish 分支：

```python
if interval.milestone_id == FINISH_NODE_ID:
    if not task_case.milestone_graph.nodes:
        return _whole_trajectory_finish_spec()
    return _normal_finish_spec()
```

`_whole_trajectory_finish_spec()` 建议聚焦全部七维：

- `progress`: 判断完整任务是否完成。
- `state_consistency`: 判断最终状态、工具结果与 agent 声明是否一致。
- `tool_quality`: 判断工具选择、参数、结果读取是否合理。
- `safety`: 判断是否触发或接近 minefield / policy violation。
- `interaction_quality`: 判断用户可见沟通是否清楚、诚实、适量。
- `efficiency`: 判断是否存在明显冗余、重复和无效步骤。
- `recovery`: 判断缺失信息、失败或冲突出现时是否合理澄清和恢复。

普通 milestone 与普通 finish 的 spec 不变。

### 6.3 修改 `dynsteer/prompt/judge.py`

为 whole-trajectory finish 增加额外上下文，避免 judge 只能看到 steps 而看不到最终状态：

```python
if _is_whole_trajectory_finish(interval, task_case):
    data["whole_trajectory_context"] = {
        "coverage_basis": "whole_trajectory",
        "initial_state_summary": task_case.metadata.get("runtime_initial_state_summary"),
        "final_state": json_safe(trajectory.final_state),
        "minefields": _minefield_prompt_json(task_case.milestone_graph),
        "default_reference_used": False,
    }
```

约束：

1. 不把 `default_reference` 放进 prompt。
2. 如 final state 过大，优先做 namespace row count、changed namespace、关键字段 sample 的压缩摘要；ToolSandbox 当前规模较小，可先直接使用 `json_safe(trajectory.final_state)`，后续再做 token 优化。
3. prompt evidence 仍要求引用 step index、tool_result、final state 或 minefield 信息。

### 6.4 修改 `dynsteer/evaluate/final.py`

把空图分支改成“precheck”，而不是最终 invalid：

1. 若 `state.fatal_minefield=True`：
   - `status=fail`
   - `score=0.0`
   - `whole_trajectory_evaluation_required=false`
   - evidence 写明 fatal minefield 优先失败。
2. 若未触发 fatal minefield：
   - `status=ambiguous`
   - `score=0.0`
   - `empty_milestone_graph=true`
   - `fixed_milestones_applicable=false`
   - `whole_trajectory_evaluation_required=true`
   - `default_reference_used=false`
   - evidence 写明空图需要进入完整轨迹终态评估。

不要新增任何 `default_reference` 入参，也不要解析 `default_reference.resolved/score`。

### 6.5 修改 `dynsteer/evaluate/settlement.py`

#### 6.5.1 调整 `finish_settlement()` 签名

增加 whole-trajectory judge 所需依赖：

```python
def finish_settlement(
    settlements: list[HarnessStageSettlement],
    task_case: TaskCase,
    trajectory: Trajectory,
    scorer: GeneralScorer,
    state: RuntimeEvaluationState,
    replay_termination: EvaluationTerminationState | None = None,
    standard_judge: StandardJudge | None = None,
    thresholds: ThresholdConfig | None = None,
) -> tuple[HarnessStageSettlement, StageEvaluationResult, EvaluationPolicyState]:
```

这里不增加 `default_reference`。

#### 6.5.2 新增 whole-trajectory finish 路径

`finish_settlement()` 调用 `build_finish_verification()` 后：

1. 若 verification 是 fatal fail，按现有 finish stage 构造失败结果。
2. 若 `whole_trajectory_evaluation_required=true`：
   - 如果 `standard_judge is None`，返回 `invalid / 0.0`，metadata 写明 `whole_trajectory_evaluator_unavailable=true`。
   - 如果 `standard_judge` 可用，调用 `standard_judge.evaluate_stage(interval, task_case, trajectory, focus_dimensions)`。
   - 用 `stage_score_from_dimensions(result.dimension_scores, state.weights)` 计算 `stage_score`。
   - 根据 `thresholds.fail_threshold/pass_threshold` 和 judge status 生成最终 status。
   - metadata 合并 precheck 与 judge 结果，并写入：
     - `empty_milestone_graph=true`
     - `fixed_milestones_applicable=false`
     - `whole_trajectory_evaluation=true`
     - `coverage_basis="whole_trajectory"`
     - `default_reference_used=false`
     - `judge_level="standard"`
     - `focus_dimensions=[...]`
3. 普通有 milestone 的 finish 路径保持现状。

#### 6.5.3 修正 hard flag

将：

```python
hard_constraints_all_pass = status != StageStatus.FAIL
```

改为：

```python
hard_constraints_all_pass = status in {StageStatus.PASS, StageStatus.WARN}
```

这样 `invalid` 不再被误标为硬约束通过。

### 6.6 修改 `dynsteer/evaluate/evaluator.py`

`_finalize_state_reports()` 增加对 `standard_judge` 和 `thresholds` 的透传：

```python
settlement, stage_result, next_policy = finish_settlement(
    state.settlements,
    task_case,
    trajectory,
    scorer,
    state,
    replay_termination=replay_termination,
    standard_judge=self._standard_judge,
    thresholds=self._thresholds,
)
```

不读取 `config.metadata["default_reference"]`，不把 default 结果传给 finish。

调整 `_runtime_report()`：

```python
if not graph.nodes:
    coverage = _empty_graph_whole_trajectory_coverage(stage_reports)
```

新增 helper：

```python
def _empty_graph_whole_trajectory_coverage(stage_reports: list[StageEvaluationResult]) -> str:
    finish_stage = next((stage for stage in reversed(stage_reports) if stage.milestone_id == FINISH_NODE_ID), None)
    if finish_stage is None:
        return "none"
    evaluation = finish_stage.metadata.get("finish_stage_evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("coverage_basis") != "whole_trajectory":
        return "none"
    if finish_stage.status == StageStatus.PASS:
        return "full"
    if finish_stage.status in {StageStatus.WARN, StageStatus.AMBIGUOUS}:
        return "partial"
    return "none"
```

### 6.7 修改 `docs/apis/evaluate.md`

需要同步说明：

1. `default_reference` 是 replay 报告对照元数据，不参与 replay 判定。
2. 空 milestone graph 的 replay 语义是 whole-trajectory stage，不是 invalid，也不是 default fallback。
3. 空 graph coverage 依据为 `coverage_basis="whole_trajectory"`。
4. 没有 whole-trajectory judge 时才返回 invalid。
5. fatal minefield 始终优先失败。

## 7. 不建议修改的位置

### 7.1 不建议在 ToolSandbox adapter 中生成 synthetic milestone

不要在 `dynsteer/adapter/toolsandbox/utils/scenario.py` 为 minefield-only case 自动补一个 milestone。原因：

- synthetic milestone 会把 DynSTEER 补偿语义混进 benchmark 原始 graph，后续审计难以区分原生 milestone 与评估器补充阶段。
- insufficient information case 的正确路径可能是澄清、拒绝、替代执行或安全停止，不能从 minefield 本身稳定反推出一个结构化 milestone。
- 本问题的根因是 evaluator 缺少 whole-trajectory finish stage，不是 adapter 丢失了原生 milestone。

### 7.2 不建议调用 ToolSandbox 原生 evaluator 作为 replay 裁判

ToolSandbox 原生 evaluator 的结果已经体现在 default 结果里。replay 如果再读取它，就会把 baseline 结论泄漏进 DynSTEER 判定，破坏实验中 default 与 DynSTEER-Replay 的独立性。

### 7.3 不建议把“未触发 minefield”当成功

minefield 是负向约束。未命中只能说明没有发现已建模违规，不能说明任务完成。必须由 whole-trajectory judge 判断任务是否完成。

### 7.4 不建议把 `score >= 0.8` 当 default 成功

旧方案里曾讨论过 default 分数阈值，这是错误方向。default 分数不应进入 replay 判定，阈值更不应成为 replay 成败的替代条件。

## 8. 测试方案

### 8.1 `tests/test_finish_empty_graph_precheck.py`

覆盖 `build_finish_verification()`：

1. empty graph + fatal minefield：返回 `fail / 0.0`，`whole_trajectory_evaluation_required=false`。
2. empty graph + no fatal：返回 `ambiguous` precheck，`whole_trajectory_evaluation_required=true`，`default_reference_used=false`。
3. 有 milestone graph：仍走原有 milestone coverage / terminal check 逻辑。

### 8.2 `tests/test_finish_whole_trajectory_stage.py`

用 fake `StandardJudge` 覆盖 `finish_settlement()`：

1. empty graph + no fatal + fake judge pass：finish stage `pass`，`coverage_basis=whole_trajectory`，`default_reference_used=false`。
2. empty graph + no fatal + fake judge fail：finish stage `fail`，score 来自 DynSTEER judge 维度分，不读取 default。
3. empty graph + no fatal + no judge：finish stage `invalid / 0.0`，metadata 写明 evaluator unavailable。
4. empty graph + fatal minefield + fake judge pass：仍然 `fail / 0.0`，验证 fatal 优先级。

### 8.3 `tests/test_replay_default_reference_isolation.py`

构造两个 replay config：

1. `default_reference={"resolved": false, "score": 0.0}`，fake whole-trajectory judge 返回 pass，预期 replay 仍 pass/full。
2. `default_reference={"resolved": true, "score": 1.0}`，fake whole-trajectory judge 返回 fail，预期 replay 仍 fail/none。

该测试用于证明 default 原生结果不影响 replay 判定。

### 8.4 `tests/test_replay_empty_graph_coverage.py`

覆盖 `_runtime_report()`：

1. empty graph + whole-trajectory finish pass：`milestone_coverage=full`。
2. empty graph + whole-trajectory finish warn/ambiguous：`milestone_coverage=partial`。
3. empty graph + finish fail/invalid/missing：`milestone_coverage=none`。
4. empty graph + 没有 finish stage：`milestone_coverage=none`。
5. 普通 graph coverage 不变。

### 8.5 目标 case 回归

落地后复跑：

- `modify_contact_with_message_recency_insufficient_information_10_distraction_tools`
- `modify_contact_with_message_recency_insufficient_information_3_distraction_tools`

预期：

- replay 不再因为空 graph 固定 `invalid / none / 0.0`。
- 若 DynSTEER whole-trajectory judge 判断轨迹完成，则 `milestone_coverage=full`，finish stage `status=pass`。
- `overall_score` 来自 whole-trajectory finish stage 的 DynSTEER 维度分和 minefield penalty，不来自 default score。
- `report.metadata.default_reference` 仍保留，但 `finish_stage_evaluation.default_reference_used=false`。

同时回归 fatal case：

- `modify_contact_with_message_recency_insufficient_information`
- `find_days_till_holiday_insufficient_information`

这些 case 若 replay 命中 fatal minefield，仍应失败。

## 9. 验收命令

建议按以下顺序执行：

```powershell
uv run pytest tests/test_finish_empty_graph_precheck.py
uv run pytest tests/test_finish_whole_trajectory_stage.py
uv run pytest tests/test_replay_default_reference_isolation.py
uv run pytest tests/test_replay_empty_graph_coverage.py
uv run pytest
```

复跑目标实验后重点检查：

- `results/experiments/toolsandbox_partial_main/scores.json`
- 两个目标 case 的 `summary.json`
- 两个目标 case 的 `report.json`
- `report.metadata.default_reference` 是否仍只是对照元数据
- `finish_stage_evaluation.default_reference_used` 是否为 `false`
- `finish_stage_evaluation.coverage_basis` 是否为 `whole_trajectory`
- `logs/<date>.log` 中无新的 `ERROR` / `Traceback`

## 10. 预期影响

### 10.1 对两个不一致 case

修改前：空 graph 且未触发 fatal minefield会被固定判成 `invalid / none / 0.0`。

修改后：这两个 case 会进入 whole-trajectory finish stage。若 judge 确认用户补充信息后的 `search_contacts -> add_contact` 路径完成任务且未违规，则 replay 应为 `pass / full / 高分`。具体分数由 DynSTEER judge 维度评分和当前权重计算，不承诺等于 default 的 `1.0`。

### 10.2 对 fatal minefield case

不应改变。fatal minefield 仍优先失败，即使完整轨迹其他部分看起来合理，也不能被 whole-trajectory judge 抵消。

### 10.3 对普通 milestone case

不应改变。只有 `not graph.nodes` 时进入 whole-trajectory stage；有真实 milestone graph 的 case 仍按 ready frontier、checkpoint、pending、finish terminal recheck 处理。

### 10.4 对实验指标

`strict 成败一致数` 可能从 `23/25` 提高，但是否达到 `25/25` 取决于 DynSTEER whole-trajectory judge 对两个目标 case 的独立判断。方案不再用 default 原生 `resolved=true` 保证一致，因此指标变化必须以复跑结果为准。

## 11. 风险与控制

风险 1：whole-trajectory judge 引入 LLM 不稳定性。

- 控制方式：使用 temperature 0、结构化 JSON 输出、明确 evidence 必须引用 step/final state/minefield 信息；必要时沿用 standard 多 pass 聚合。

风险 2：final state 太大导致 prompt 过长。

- 控制方式：先在 ToolSandbox 当前规模下直接验证；若 token 过大，再新增状态摘要 helper，只传 namespace row count、changed namespace 和关键行 sample。

风险 3：空 graph coverage 语义与 milestone coverage 名称不完全一致。

- 控制方式：metadata 写入 `coverage_basis=whole_trajectory`，报告文档说明空图时 coverage 表示完整轨迹完成度。

风险 4：没有配置 judge 时大量空图 case 变成 invalid。

- 控制方式：这是正确的保守行为；没有 DynSTEER 自己的完整轨迹 evaluator 时不应借用 default 结果。

## 12. 实施步骤

1. 修改 `dynsteer/stage/resolve.py`：增加 whole-trajectory finish goal。
2. 修改 `dynsteer/stage/spec.py`：为空 graph finish 返回 whole-trajectory 七维 spec。
3. 修改 `dynsteer/prompt/judge.py`：为空 graph finish prompt 补充完整轨迹上下文和最终状态。
4. 修改 `dynsteer/evaluate/final.py`：把空 graph no-fatal 分支改成 whole-trajectory precheck，不再 invalid，不接收 default_reference。
5. 修改 `dynsteer/evaluate/settlement.py`：新增 whole-trajectory finish stage，使用 `StandardJudge` 进行完整轨迹评估；修正 `hard_constraints_all_pass`。
6. 修改 `dynsteer/evaluate/evaluator.py`：向 finish 透传 `standard_judge` / `thresholds`，并修正空 graph coverage。
7. 更新 `docs/apis/evaluate.md` 和必要 API 文档。
8. 新增并运行测试。
9. 复跑目标 case，人工核对报告中的 evidence、diagnosis、coverage_basis 和 default_reference isolation。

## 附录A. 项目中没有把握实现的模块部分

1. **whole-trajectory judge 与 ToolSandbox 原生 evaluator 的一致性。**  
   没有人工 gold label 时，DynSTEER judge 可能与 ToolSandbox 原生 `resolved` 不一致。这不是代码错误本身，而是两个评估器口径不同，需要通过报告 evidence 做人工审计。

2. **final_state prompt 压缩策略。**  
   ToolSandbox 当前状态规模通常可控，但未来 benchmark 可能有大状态表。是否需要新增通用状态摘要器，需要在复跑中观察 prompt token 和 judge 质量后决定。

3. **空 graph `milestone_coverage` 的长期命名。**  
   为兼容现有 summary，本方案仍复用 `milestone_coverage`，并通过 `coverage_basis=whole_trajectory` 区分语义。长期看可以新增 `completion_coverage` 或 `evaluation_coverage_basis` 一等字段，但这属于报告 schema 演进，应单独规划。
