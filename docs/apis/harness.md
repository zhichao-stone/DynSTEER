# Harness API

## 目标

Harness API 用于把 benchmark 原生执行过程接入 DynSTEER。当前职责边界是：

- `BaseBenchmarkAdapter`: 负责按 case 适配 `TaskCase`、写入/复用 adapted JSON 缓存。
- `BaseBenchmarkHarness`: 只负责 benchmark 原生 session 生命周期、增量观测批次采集、原生摘要和资源清理，不再构造 `TaskCase`。
- `DynSTEEREvaluator`: 负责执行编排、milestone checkpoint、阶段式动态评估、LLMJudge 调度和 fail-fast。

Harness 不再拥有 `run_case()` 主编排入口，也不负责阶段评分、动态权重更新或 minefield 策略终止。

## 核心数据结构

- `HarnessRunConfig`: 单次 harness 运行配置，包含 benchmark、data root、case_ids、runs_dir、results_dir、fail-fast 策略和 metadata。
- `BenchmarkCase`: benchmark 内单个可运行测试任务。
- `HarnessAdvanceResult`: `advance_case()` 的结构化返回值，包含 `steps`、`snapshots`、`continue_running` 和可选 `reason`。`snapshots` 是必填字段，表示本批推进后可见的状态快照，必须与 `steps` 使用同一时间坐标。
- `HarnessStageSettlement`: evaluator 在运行期生成的 start/milestone/finish 阶段结算节点。
- `HarnessRunResult`: evaluator 返回的 benchmark 运行结果，包含 `TaskCase`、`Trajectory`、阶段结算、策略终止字段和 `evaluation_report`。

## BaseBenchmarkAdapter 接口

```python
def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase: ...
```

adapter 负责把原生 benchmark case 转换为 DynSTEER `TaskCase`。runner 通过 `dynsteer.adapter.loader.load_task_case(config, adapter)` 按 `data/{benchmark}/adapted_cases/<case_id>.json` 读取缓存；缺失时只触发当前 case 的 `adapt_task_case()` 并保存单 case JSON。

运行期 harness 由 `dynsteer.adapter.registry.get_harness(benchmark)` 直接创建，不再通过 adapter 间接创建。这样单 case 运行期执行等只需要 harness 的路径不会实例化 adapter，adapter 也不再承担 harness 工厂职责。

## Adapter 与 Stage Goal 语义边界

Adapter 可以理解 benchmark 私有格式，并把私有约束解释为 DynSTEER 通用 `Constraint.stage_goal_semantics`。例如某 benchmark 的“保持参考状态不变”约束应在 Python 代码中映射为 `{"kind": StageGoalSemanticKind.PRESERVE_STATE.value}`，落盘后表现为 `{"kind":"preserve_state"}`。

Adapter 不应直接生成 `TaskCase.stage_goals`，也不应提供 benchmark 专用 stage_goal hook。`TaskCase.stage_goals` 由 `dynsteer.stage.generate_stage_goals(...)` 统一生成。

私有评分字段仍保留在 benchmark 自己的 metadata key 下，供专用 scorer 使用；公共 stage_goal 和 judge prompt 不读取这些私有字段。

## BaseBenchmarkHarness 接口

子类需要实现以下公开接口：

```python
def list_cases(self, config: HarnessRunConfig) -> list[BenchmarkCase]: ...
def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> object: ...
def advance_case(self, session: object) -> HarnessAdvanceResult: ...
def case_finished(self, session: object) -> bool: ...
```

基类提供以下默认接口：

```python
def prepare_config(self, config: HarnessRunConfig) -> None: ...
def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str: ...
def metrics_from_session(self, session: object) -> JsonObject: ...
def final_state_from_session(self, session: object) -> JsonObject | None: ...
def raw_summary_from_session(self, session: object) -> JsonObject: ...
def stop_case(self, session: object, reason: str) -> None: ...
def teardown_case(self, session: object) -> None: ...
def constraint_scorer(self) -> BaseBenchmarkConstraintScorer: ...
```

### `BaseBenchmarkHarness.constraint_scorer()`

返回当前 benchmark 的约束评分器。默认返回 `BaseBenchmarkConstraintScorer()`，其行为等同 `GeneralScorer`。
需要解释 `Operator.CUSTOM` 或 benchmark 原生约束的 harness 应覆写该方法。

```python
class MyHarness(BaseBenchmarkHarness):
    def constraint_scorer(self) -> BaseBenchmarkConstraintScorer:
        return MyBenchmarkConstraintScorer()
```

## 返回契约

- `TaskCase` 必须在 adapter/loader 阶段完成适配，harness 运行期不提供 `task_case_from_session()`。
- `advance_case()` 必须返回 `HarnessAdvanceResult`，不能返回 `None`。
- `advance_case()` 负责判断空步骤是否合理。自然完成时返回 `HarnessAdvanceResult(steps=[], snapshots=[], continue_running=False, reason="benchmark 已自然完成")`。
- 如果 session 未完成但没有新增步骤，`advance_case()` 应在 harness 内部抛出异常。
- 所有 harness 必须通过 `HarnessAdvanceResult.snapshots` 返回本批推进后可见快照；没有状态快照的 benchmark 必须显式返回空数组。
- `snapshots_from_session()` 不再属于 `BaseBenchmarkHarness` 公开运行期接口。
- 对带原生全局消息索引的 benchmark，`steps[].index` 与 `snapshots[].after_step_index` 必须使用同一坐标系。
- `metrics_from_session()`、`raw_summary_from_session()` 应把可缺省结果归一为空字典。
- `case_finished()` 只作为查询接口或子类内部辅助能力；`DynSTEEREvaluator.evaluate()` 不用它控制主循环。

## 资源释放与异常处理契约

- `DynSTEEREvaluator.evaluate()` 在成功、策略终止和异常路径中都会调用 `harness.teardown_case(session)`。
- 若主执行过程已经抛出异常，teardown 失败只记录结构化错误日志，不遮蔽主异常。
- 若主执行过程成功但 teardown 失败，evaluator 抛出 `HarnessTeardownError`，避免资源释放失败被静默吞掉。
- harness 子类的 `teardown_case()` 应尽力释放全部外部资源，并断开 session 中对大型上下文、轨迹步骤、快照、SDK client 或原生 role 的引用。
- runner 对单个 case 的执行失败统一包装为 `HarnessCaseExecutionError`，错误信息包含 benchmark、run_id 和 case_id。

## Runner

`dynsteer.harness.runner` 提供：

- `run_harness_configs(configs, max_workers=1)`: 唯一公开运行入口，按 `run_configs.json` 中的配置顺序逐组加载 `TaskCase` 列表；同一配置内按 case 顺序串行或并行执行，返回值按配置和 case 的原始顺序排列。若 `benchmark.json` 配置了 `max_workers`，实际 worker 数取命令行 workers 与该字段的较小值，且最小为 1。

Runner 只负责选择 case、加载 adapted `TaskCase`、调用 `evaluator.evaluate(harness, config, task_case)` 和写出文件。它不调用 `harness.run_case()`，也不调用整轨迹评估作为主实验流程。

Runner 对每个 config 会先调用一次 `load_task_case(run_config, adapter)` 加载本组 `TaskCase` 列表，然后输出日志：`基于配置XXX，开始基于 {benchmark} 展开评估，Cases数量: N`。单 case 执行只负责 evaluator 调用和结果文件写入，避免多场景运行时反复初始化日志或刷屏。

`run_harness_configs(...)` 会把单个 case 的异常包装为 `HarnessCaseExecutionError`，错误信息包含 benchmark、run_id 和 case_id，便于串行或并行运行时定位失败样本。并行模式下日志缓冲和 logger 初始化使用锁保护；provider client 不在 worker 之间共享，由每次 `BaseLLM.chat(...)` 调用创建一次，并在该次调用的重试循环中复用。

Runner 使用 `dynsteer.progress.TqdmCaseProgressManager` 显示估算总步数进度条。实际同时运行的 case 数量仍不超过 `max_workers`；可见窗口大小按 case 计算为 `max(5, 实际 worker 数)`，每个 case 占用两行终端输出：第一行显示 `# Test Case {idx}: {case_id}`，其中 `idx` 是当前配置内从 1 开始的原始 case 顺序，第二行显示 desc 为 `Case {idx}执行进度（最多N步）` 的 tqdm 进度条，因此默认可见窗口为 5 个 case、10 个终端行。进度条后缀由 DynSTEER 自行维护 `elapsed`、`steps` 和 `avg_step`，避免 tqdm 重建或关闭时丢失真实耗时。case 完成前会把进度条 total 收敛到实际 step 数，完成后满进度条会继续保留在可见窗口中，直到被后续 case 挤出；窗口变化时会重建可见进度条位置，让剩余 case 从第一组两行开始连续显示。运行结束时 Runner 会先清理动态进度条，再按可见 case 顺序输出稳定的最终静态快照，避免多行 tqdm `leave=True` 留存导致终端内容上移或挤压。进度条运行期间终端日志与第三方 stdout/stderr 会被静默，文件日志和内存日志仍保留 INFO 结构化内容。

Harness 模式输出：

- `runs/<benchmark>/<run_id>/<case_id>/raw/`
- `runs/<benchmark>/<run_id>/<case_id>/raw_summary.json`
- `runs/<benchmark>/<run_id>/<case_id>/trajectory.json`
- `results/<benchmark>/<run_id>/summary.json`
- `results/<benchmark>/<run_id>/<case_id>/report.json`
- `results/<benchmark>/<run_id>/<case_id>/summary.json`

`results/<benchmark>/<run_id>/summary.json` 是 run 级汇总摘要，聚合同一 `run_id` 下所有 case 的单场景 `summary.json`。汇总字段包含 `benchmark`、`run_id`、`case_count`、`average_overall_score`、`milestone_coverage_counts`、`total_step_count`、`total_llm_tokens`、`total_trajectory_tokens`、`average_elapsed_seconds` 和 `cases`。`cases[]` 保留每个场景的 `case_id`、相对 `summary_path`、相对 `report_path` 以及单场景摘要字段，便于从总览追溯到具体场景结果。

`trajectory.json` 包含完整 `Trajectory` 序列化结果。`raw_summary.json.trajectory_output.path` 固定指向 `trajectory.json`，并记录 `step_count`、`snapshot_count` 和 `final_state_present`；完整 steps 不嵌入 `raw_summary.json`，避免单个摘要文件过大。

`raw_summary.json` 会在 benchmark 原生摘要基础上追加 DynSTEER 运行期字段：`runtime_metrics`、`trajectory_output`、`terminated_by_policy`、`termination_code`、`termination_reason` 和 `stage_settlements`。`runtime_metrics` 记录 case 评估耗时、轨迹 step 数、tool call 数、轨迹 step cost 聚合和 LLM judge token usage 聚合。`stage_settlements[].metadata` 中的 `stage_trace` 与 `milestone_matching` 由 `DynSTEEREvaluator` 生成，Runner 只负责序列化落盘。`stage_trace` 用于查看本阶段轨迹步骤，`milestone_matching` 用于查看 milestone 命中边界、约束评分和 finish 阶段未命中 milestone。

`raw_summary.json` 还包含实时 milestone 匹配诊断字段：

- `milestone_graph_summary`: 当前 case 转换后的 milestone DAG 摘要，包含节点、边、required/optional milestone ID 和约束摘要。
- `milestone_match_attempts`: 运行期每个存在 ready milestone 的候选 step 匹配尝试，包含命中前 matched/ready 集合、候选边界、候选 milestone 评分、是否被选中和拒绝原因。
- `milestone_final_diagnostics`: 每个 milestone 的最终状态摘要，包含 `matched`/`pending`、是否曾经 ready、尝试次数、最佳分数、最佳边界、阻塞原因和未满足前驱。

## ToolSandbox 适配说明

ToolSandbox adapter 通过懒加载导入 `tool_sandbox`，不会让 DynSTEER 核心包直接依赖 ToolSandbox。运行时需要保证 ToolSandbox 及其依赖已安装，或在 `data/toolsandbox/benchmark.json` 中配置可导入的外部 `source_root`。

`data/{benchmark}/benchmark.json` 支持 `language` 字段，默认值为 `en`。`load_harness_run_configs(...)` 会校验该字段为非空字符串，并写入 `HarnessRunConfig.metadata["language"]`，供 prompt 模板选择语言版本。`benchmark.json` 还支持可选 `max_workers` 整数字段，用于为不支持并行的 benchmark 设置 case 并发上限。

ToolSandbox adapter 负责读取 scenario、初始 SANDBOX 行、初始数据库状态和 evaluation matcher，生成带 `case_id` 与已 enrich milestone graph 的 `TaskCase`。适配阶段不得调用 `scenario.play()`，也不得调用 agent/user `respond()`。

ToolSandbox harness 不调用原生 `play_and_evaluate()` 或整场 `Scenario.play()`。`start_case()` 只深拷贝一次 `Scenario.starting_context`，准备 system -> execution environment 初始化消息；每次 `_advance_native_session()` 恢复 `session.context`，读取当前 SANDBOX recipient，并只调用该 role 的一次 `respond()`。每次 respond 后都会写回 `session.context = get_current_context()`，避免后续推进重置回 starting context。

ToolSandbox 的原生工具、role 和 execution environment 通过模块级 `_global_execution_context` 读写当前消息上下文，线程并行执行不同 case 会互相覆盖 context。因此 `data/toolsandbox/benchmark.json` 配置 `max_workers: 1`，确保 ToolSandbox case 串行执行。
