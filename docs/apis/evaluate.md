# Evaluate API

## 目标

`dynsteer.evaluate.DynSTEEREvaluator` 是 DynSTEER 的主评估入口。它承担两类能力：

- `evaluate(harness, case_id, config)`: 主实验入口，编排 benchmark 原生执行并进行阶段式动态评估。
- `evaluate_trajectory(task_case, trajectory)`: 对完整轨迹执行评估，保留给后续对比实验或消融实验复用。

模块级函数 `evaluate_trajectory()` 已删除，调用方需要先构造 `DynSTEEREvaluator`。

## 构造函数

```python
evaluator = DynSTEEREvaluator(
    cheap_judge=None,
    llm_judge=None,
    thresholds=None,
    match_config=None,
    weight_config=None,
)
```

- `cheap_judge`: 默认使用 `LocalJudge`，只用于 cheap 层评估。
- `llm_judge`: standard 和 expensive 层必须使用真实 LLMJudge。
- `thresholds`: 阶段通过、告警、失败和不确定性阈值。
- `match_config`: 整轨迹评估时的 milestone 匹配配置。
- `weight_config`: 动态维度权重配置。

如果阶段调度需要 standard 或 expensive，但未配置 `llm_judge`，会抛出 `JudgeConfigurationError`，不会回退到 `LocalJudge`。

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
report = evaluator.evaluate_trajectory(task_case, trajectory)
```

该入口会对完整轨迹执行 milestone matching、stage interval 构造、cheap -> standard -> expensive 动态调度和权重更新。本阶段 `main.py --benchmark` 不使用该入口作为主实验流程。

## LLMJudge

`dynsteer.judges.llm.LLMJudge` 使用 OpenAI-compatible API。可通过环境变量创建：

```text
DYNSTEER_JUDGE_PROVIDER=openai_compatible
DYNSTEER_JUDGE_MODEL=qwen-plus-latest
DYNSTEER_JUDGE_BASE_URL=...
DYNSTEER_JUDGE_API_KEY=...
DYNSTEER_JUDGE_TIMEOUT_SECONDS=60
DYNSTEER_JUDGE_TEMPERATURE=0
DYNSTEER_EXPENSIVE_JUDGE_PASSES=3
```

API key 不写入日志、prompt 或报告。LLM 返回必须是 JSON 对象，解析失败会抛出 `LLMJudgeResponseError`。

## 输出

`DynSTEEREvaluator.evaluate()` 返回 `HarnessRunResult`，其中：

- `trajectory`: 当前完整或被 fail-fast 截断的轨迹。
- `stage_settlements`: 运行期阶段结算。
- `evaluation_report`: 主实验评估报告。
- `terminated_by_policy`: 是否被 DynSTEER 策略提前终止。
- `termination_code` / `termination_reason`: 策略终止摘要。
