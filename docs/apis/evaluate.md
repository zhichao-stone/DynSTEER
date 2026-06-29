# Evaluate API

## 目标

`dynsteer.evaluate` 是一个包，`DynSTEEREvaluator` 是 DynSTEER 的主评估入口。它承担两类能力：

- `evaluate(harness, case_id, config)`: 主实验入口，编排 benchmark 原生执行并进行阶段式动态评估。
- `evaluate_trajectory(task_case, trajectory)`: 对完整轨迹执行评估，保留给后续对比实验或消融实验复用。

模块级函数 `evaluate_trajectory()` 已删除，调用方需要先构造 `DynSTEEREvaluator`。

## 包结构

```text
dynsteer/evaluate/
- __init__.py    # 导出 DynSTEEREvaluator、JudgeConfigurationError、权重工具和通用工具
- models.py      # RuntimeEvaluationState、RuntimeEvaluationDecision、JudgeConfigurationError
- evaluator.py   # DynSTEEREvaluator
- score.py       # GeneralScorer、ScoringContext 和通用约束评分逻辑
- diagnostics.py # 运行期 stage trace 与 milestone matching 诊断序列化
- weights.py     # normalize_weights、select_initial_weights、update_weights
- milestone.py   # milestone DAG 校验、贪心匹配、评分矩阵和运行期命中判定
- utils.py       # compute_uncertainty、overall_score、enrich_stage_result 等纯函数
```

`dynsteer/match.py` 已并入 `dynsteer/evaluate/milestone.py`，`from dynsteer.match import ...` 不再可用。

## 构造函数

```python
evaluator = DynSTEEREvaluator(
    cheap_judge=None,
    standard_judge=None,
    expensive_judge=None,
    thresholds=None,
    match_config=None,
    weight_config=None,
)
```

- `cheap_judge`: 默认使用 `CheapJudge`，只用于 cheap 层评估。
- `standard_judge`: standard 层使用的 judge，通常是 `StandardJudge`。
- `expensive_judge`: expensive 层使用的 judge，通常是 `ExpensiveJudge`。
- `thresholds`: 阶段通过、告警、失败和不确定性阈值。
- `match_config`: 整轨迹评估时的 milestone 匹配配置。
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
result = evaluator.evaluate(harness, case_id, config)
```

执行流程：

1. `harness.prepare_config(config)` 准备 benchmark 运行配置。
2. `harness.start_case(...)` 启动原生 session。
3. `harness.task_case_from_session(session)` 获取 `TaskCase`。
4. 循环调用 `harness.advance_case(session)` 获取 `HarnessAdvanceResult`。
5. 对 `advance.steps` 做 milestone checkpoint 和阶段式动态评估。
6. 根据阶段结果执行 fail-fast，必要时调用 `harness.stop_case(session, reason)`。
7. 当 `advance.continue_running is False` 时结束主循环。
8. `harness.raw_summary_from_session(session)` 与 `harness.teardown_case(session)` 完成收尾。

Evaluator 不检查 `advance_case()` 是否返回 `None`，也不通过空 steps 或 `case_finished()` 控制循环；这些属于 harness 返回契约。

## 整轨迹入口

```python
report = evaluator.evaluate_trajectory(task_case, trajectory, scorer=None)
```

该入口会对完整轨迹执行 milestone matching、stage interval 构造、cheap -> standard -> expensive 动态调度和权重更新。本阶段 `main.py --benchmark` 不使用该入口作为主实验流程。

`scorer` 为空时使用 `GeneralScorer()`；需要处理 benchmark 专有 `Operator.CUSTOM` 约束时，可传入继承自 `GeneralScorer` 的专用评分器：

```python
report = DynSTEEREvaluator().evaluate_trajectory(
    task_case,
    trajectory,
    scorer=MyBenchmarkScorer(),
)
```

### `GeneralScorer`

`GeneralScorer` 是 DynSTEER 默认 milestone / minefield 评分器，位于 `dynsteer.evaluate.score`。
它提供 `score_operator()`、`score_constraint()`、`score_milestone()` 三个核心方法。

`Operator.CUSTOM` 不属于通用 operator。默认 `GeneralScorer` 会返回带 evidence 的 0 分约束结果；benchmark 需要通过 `BaseBenchmarkHarness.constraint_scorer()` 返回专用 scorer 处理 CUSTOM。

### `ScoringContext`

`ScoringContext` 用于在运行期传递当前任务、已命中 milestone 边界和已命中状态快照。ToolSandbox 等 benchmark scorer 可通过该上下文读取 reference snapshot，避免在通用层内硬编码 benchmark 语义。

## 成员评估函数

- `evaluate_minefields(graph, trajectory, scorer=None, context=None) -> (matches, max_score, fatal)`: 评估轨迹是否触发 minefield。
- `select_evaluation_level(result, thresholds=None) -> EvaluationDecision`: 根据阶段风险选择评估粒度；`thresholds` 为空时使用评估器默认阈值。

## Judge 与 LLM

阶段评估器定义见 `docs/apis/judges.md`，LLM provider 定义见 `docs/apis/llm.md`。

## 输出

`DynSTEEREvaluator.evaluate()` 返回 `HarnessRunResult`，其中：

- `trajectory`: 当前完整或被 fail-fast 截断的轨迹。
- `stage_settlements`: 运行期阶段结算。
- `evaluation_report`: 主实验评估报告。
- `terminated_by_policy`: 是否被 DynSTEER 策略提前终止。
- `termination_code` / `termination_reason`: 策略终止摘要。

`stage_settlements[].metadata` 会包含运行期排查字段：

- `stage_trace`: 当前阶段闭区间 `[start_step_index, end_step_index]` 内的轨迹步骤详情，包含 step id、index、actor、event_type、content、tool_call、tool_result、cost 和 adapter raw 字段。
- `milestone_matching`: milestone 匹配诊断。`mode="runtime_checkpoint"` 表示本阶段由 milestone checkpoint 触发，包含命中的 milestone、boundary、milestone score、constraint scores、命中前 ready milestone 和已匹配 milestone；`mode="runtime_finish"` 表示自然完成阶段，包含已匹配 milestone 与 pending required/optional milestone 列表。
