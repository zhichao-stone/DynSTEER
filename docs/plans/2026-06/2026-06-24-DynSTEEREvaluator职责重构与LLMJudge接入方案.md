# DynSTEEREvaluator 执行编排重构与真实 LLMJudge 接入修改方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本方案是用户确认前的修改方案，不直接落地代码。

**Goal:** 将 DynSTEER 的主实验入口收敛为 `DynSTEEREvaluator.evaluate(harness, case_id, config)`，由 evaluator 编排 benchmark 执行、轨迹采集、阶段式动态评估和 fail-fast，并在当前阶段接入真实 LLMJudge 作为 standard / expensive 评估器。

**Architecture:** `BaseBenchmarkHarness` 只暴露 benchmark 执行接口，例如 `start_case()`、`advance_case()`、`snapshots_from_session()`、`stop_case()`；这些公开接口自己完成返回值校验与默认值归一，`advance_case()` 通过结构化 `HarnessAdvanceResult` 返回新增步骤和循环控制信号。`DynSTEEREvaluator` 调用这些接口执行具体 case，并基于增量轨迹完成 milestone checkpoint、阶段评估、动态调度和主实验报告。`evaluate.py` 删除原模块级函数式 `evaluate_trajectory()`，改为 `DynSTEEREvaluator.evaluate_trajectory()` 成员函数，函数名保持原样。

**Tech Stack:** Python 3.10+、uv、pytest、OpenAI-compatible SDK、DynSTEER dataclass model、结构化中文日志。

---

## 1. 背景与问题

当前实现与目标架构存在以下偏差：

1. `BaseBenchmarkHarness.run_case()` 当前承担了 benchmark 执行编排、milestone checkpoint、阶段评估、动态权重更新、minefield fail-fast 等多类职责。
2. `BaseBenchmarkHarness` 中 `_start_case()`、`_advance_case()`、`_snapshots_from_session()`、`_stop_case()` 等方法是内部抽象方法，但新的 evaluator 中心架构要求这些能力成为对外开放的 benchmark 执行接口。
3. `DynSTEEREvaluator` 目前尚不存在，导致阶段式动态评估逻辑分散在 `evaluate.py` 函数和 `adapter/base.py` harness 基类中。
4. `standard` 与 `expensive` 在原方案中定义为 LLM-as-a-Judge，但当前只有 `LocalJudge`，没有真实 LLM 调用。
5. `evaluate.py` 当前模块级 `evaluate_trajectory()` 是函数式入口；新架构要求删除该模块级函数，将同名 `evaluate_trajectory()` 作为 `DynSTEEREvaluator` 的成员函数保留。
6. `main.py` 和 `harness/runner.py` 当前直接调用函数式 `evaluate_trajectory()`；新主实验流程应调用 `DynSTEEREvaluator.evaluate()`。

## 2. 修改目标

本次修改必须同时完成三件事：

1. 将主实验入口改为：

```python
DynSTEEREvaluator.evaluate(
    harness: BaseBenchmarkHarness,
    case_id: str,
    config: HarnessRunConfig,
) -> HarnessRunResult
```

2. 将 `BaseBenchmarkHarness` 改为执行接口提供者：
   - 不再由 `BaseBenchmarkHarness.run_case()` 编排整套执行与评估。
   - 将 `_start_case()`、`_task_case_from_session()`、`_advance_case()`、`_snapshots_from_session()`、`_case_finished()`、`_stop_case()`、`_teardown_case()` 等内部方法改为公开执行接口。
   - 公开执行接口自己处理返回值校验和默认值归一；`advance_case()` 返回 `HarnessAdvanceResult`，由其中的 `continue_running` 控制 evaluator 主循环。
   - `ToolSandboxHarness`、`GenericHarness` 等具体子类实现这些公开接口。

3. 将评估职责收敛到 `DynSTEEREvaluator`：
   - 通过 harness 公开接口启动 case、推进 step、采集 snapshot、停止 session、清理资源。
   - 基于增量轨迹做 milestone checkpoint。
   - 对命中的阶段执行 cheap -> standard -> expensive 动态评估。
   - standard / expensive 必须调用真实 `LLMJudge`。
   - 根据阶段结果执行 fail-fast。
   - 输出主实验 `HarnessRunResult` 与 `TrajectoryEvaluationReport`。

同时满足：

1. 删除 `evaluate.py` 中模块级函数式 `evaluate_trajectory()`。
2. 保留 `evaluate_trajectory` 这个名字，但作为 `DynSTEEREvaluator.evaluate_trajectory()` 成员函数。
3. 本阶段不实现对比实验或消融实验输出。
4. 不修改 ToolSandbox 源码。

## 3. 目标架构

```text
main.py
  build DynSTEEREvaluator
        |
        v
harness.runner
  select cases
  evaluator.evaluate(harness, case_id, config)
        |
        v
DynSTEEREvaluator
  harness.prepare_config(config)
  session = harness.start_case(...)
  task_case = harness.task_case_from_session(session)
  while True:
      advance = harness.advance_case(session)
      steps = advance.steps
      snapshots = harness.snapshots_from_session(session)
      evaluate incremental checkpoint
      stop via harness.stop_case(...) when fail-fast
      break when advance.continue_running is False
  harness.teardown_case(session)
        |
        v
Judge
  LocalJudge: cheap
  LLMJudge: standard / expensive
```

模块边界：

1. `main.py`
   - 构造 `DynSTEEREvaluator`。
   - benchmark 模式调用 `run_harness_cases(config, harness, evaluator)`。
   - 不直接调用 `evaluate_trajectory()`。

2. `dynsteer/harness/runner.py`
   - 只负责选择 case、调用 `evaluator.evaluate()`、写出报告文件。
   - 不执行阶段评估逻辑。
   - 不调用 `evaluate_trajectory()`。

3. `dynsteer/adapter/base.py`
   - 定义 `BaseBenchmarkAdapter`。
   - 定义 `BaseBenchmarkHarness` 的公开执行接口。
   - 不保留主实验编排逻辑。
   - 不做 milestone scoring、stage evaluation、minefield fail-fast 或 weight update。

4. `dynsteer/evaluate.py`
   - 定义 `DynSTEEREvaluator`。
   - 定义运行期评估状态与决策模型。
   - 保留共享 helper，例如 `select_initial_weights()`、`update_weights()`、`compute_uncertainty()`、`select_evaluation_level()`、`evaluate_minefields()`。
   - 删除模块级 `evaluate_trajectory()`。
   - 新增成员函数 `DynSTEEREvaluator.evaluate_trajectory()`，用于后续整轨迹评估实验复用，但不作为 `main.py` 默认主流程。

5. `dynsteer/judges/llm.py`
   - 新增真实 `LLMJudge`。
   - standard / expensive 必须调用 OpenAI-compatible API。

## 4. BaseBenchmarkHarness 公开接口
