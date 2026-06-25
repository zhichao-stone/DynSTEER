# Harness API

## 目标

Harness API 用于把 benchmark 原生执行过程接入 DynSTEER。当前职责边界是：

- `BaseBenchmarkAdapter`: 负责离线实验数据转换和 harness 工厂。
- `BaseBenchmarkHarness`: 只负责 benchmark 原生 session 生命周期、增量步骤采集、状态快照、原生摘要和资源清理。
- `DynSTEEREvaluator`: 负责执行编排、milestone checkpoint、阶段式动态评估、LLMJudge 调度和 fail-fast。

Harness 不再拥有 `run_case()` 主编排入口，也不负责阶段评分、动态权重更新或 minefield 策略终止。

## 核心数据结构

- `HarnessRunConfig`: 单次 harness 运行配置，包含 benchmark、data root、case_ids、runs_dir、results_dir、fail-fast 策略和 metadata。
- `BenchmarkCase`: benchmark 内单个可运行测试任务。
- `HarnessAdvanceResult`: `advance_case()` 的结构化返回值，包含 `steps`、`continue_running` 和可选 `reason`。
- `HarnessStageSettlement`: evaluator 在运行期生成的 start/milestone/finish 阶段结算节点。
- `HarnessRunResult`: evaluator 返回的 benchmark 运行结果，包含 `TaskCase`、`Trajectory`、阶段结算、策略终止字段和 `evaluation_report`。

## BaseBenchmarkHarness 接口

子类需要实现以下公开接口：

```python
def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]: ...
def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object: ...
def task_case_from_session(self, session: object) -> TaskCase: ...
def advance_case(self, session: object) -> HarnessAdvanceResult: ...
def case_finished(self, session: object) -> bool: ...
```

基类提供以下默认接口：

```python
def prepare_config(self, config: HarnessRunConfig) -> None: ...
def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str: ...
def snapshots_from_session(self, session: object) -> list[StateSnapshot]: ...
def metrics_from_session(self, session: object) -> JsonObject: ...
def final_state_from_session(self, session: object) -> JsonObject | None: ...
def raw_summary_from_session(self, session: object) -> JsonObject: ...
def stop_case(self, session: object, reason: str) -> None: ...
def teardown_case(self, session: object) -> None: ...
```

## 返回契约

- `task_case_from_session()` 必须返回有效 `TaskCase`；无法构造时在 harness 内部抛出异常，不能返回 `None`。
- `advance_case()` 必须返回 `HarnessAdvanceResult`，不能返回 `None`。
- `advance_case()` 负责判断空步骤是否合理。自然完成时返回 `HarnessAdvanceResult(steps=[], continue_running=False, reason="benchmark 已自然完成")`。
- 如果 session 未完成但没有新增步骤，`advance_case()` 应在 harness 内部抛出异常。
- `snapshots_from_session()`、`metrics_from_session()`、`raw_summary_from_session()` 应把可缺省结果归一为空列表或空字典。
- `case_finished()` 只作为查询接口或子类内部辅助能力；`DynSTEEREvaluator.evaluate()` 不用它控制主循环。

## Runner

`dynsteer.harness.runner` 提供：

- `run_harness_case(config, harness, evaluator)`: 运行一个 case。
- `run_harness_cases(config, harness, evaluator)`: 运行一个或多个 case。

Runner 只负责选择 case、调用 `evaluator.evaluate(harness, case_id, config)` 和写出文件。它不调用 `harness.run_case()`，也不调用整轨迹评估作为主实验流程。

Harness 模式输出：

- `runs/<benchmark>/<run_id>/<case_id>/raw/`
- `runs/<benchmark>/<run_id>/<case_id>/raw_summary.json`
- `results/<benchmark>/<run_id>/<case_id>/report.json`
- `results/<benchmark>/<run_id>/<case_id>/summary.json`

## ToolSandbox 适配说明

ToolSandbox adapter 通过懒加载导入 `tool_sandbox`，不会让 DynSTEER 核心包直接依赖 ToolSandbox。运行时需要保证 ToolSandbox 及其依赖已安装，或在 `data/toolsandbox/benchmark.json` 中配置可导入的外部 `source_root`。

ToolSandbox harness 不调用原生 `play_and_evaluate()`。它通过原生 `advance()`、`step()`、`play()` 或 role 的 `respond()` 推进 session，返回增量 `HarnessAdvanceResult`。阶段评估、minefield 判断和 fail-fast 终止由 `DynSTEEREvaluator` 完成。
