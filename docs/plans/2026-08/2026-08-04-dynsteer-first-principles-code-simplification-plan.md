# DynSTEER 第一性原理代码精简执行方案（2026-08-04）

> 方案状态：仅制定执行方案，不修改生产功能代码。  
> 约束依据：`docs/constraints/code.md`。  
> 审计范围：`main.py`、`display/**/*.py`、`dynsteer/**/*.py`；仓库当前没有测试文件。  
> 核心原则：删除事实、状态和流程的重复表达，不通过压缩换行、合并语句或牺牲命名来制造行数下降。

## 0. 审计结论

当前仓库的 83 个非测试、非文档 Python 文件均可从 `main.py` 或 `display/build.py` 的入口模块到达；不存在可以整模块删除的生产模块，但存在四类明确冗余：

1. 完全没有生产调用的函数、枚举、字段和协议方法。
2. 同一事实被复制到 `raw_summary`、`summary`、`report`、settlement metadata、stage metadata 和 experiment index，多份副本随后又需要兼容读取。
3. live evaluate 与 replay 分别实现相同的“minefield 检查 → agent step 闭包 → milestone 评估”流程，并维护两个 termination 状态来源。
4. 展示层仍兼容已经不再生成的目录、报告文件名和 `runtime:fail/runtime:missing` stage id；这些兼容分支既增加代码，又没有覆盖当前含 `r{repeat}` 的真实目录结构。

静态审计确认的明确死代码包括：

| 文件 | 对象 | 证据 | 处理 |
| --- | --- | --- | --- |
| `dynsteer/config.py` | `DIMENSION_ORDER` | 全仓仅定义、无读取 | 删除 |
| `dynsteer/model.py` | `EvaluationFailureBasis` | 全仓仅定义、无读取 | 删除 |
| `dynsteer/model.py` | `ThresholdConfig.safe_minefield_threshold`、`risky_minefield_threshold` | 主策略和 scorer 均不读取 | 删除 |
| `dynsteer/model.py` | `MilestoneGraph.default_thresholds` | loader 写入后无人读取 | 删除字段及 loader 解析 |
| `dynsteer/model.py` | `TaskCase.policy_constraints` | loader 写入后无人读取 | 删除字段及 loader 解析 |
| `dynsteer/model.py` | `StageEvaluationResult.minefield_evidence_is_structural` | 无构造参数、无读取、未序列化 | 删除 |
| `dynsteer/model.py` | `CaseProgressEvent.message` | 所有事件均未写入或读取 | 删除 |
| `dynsteer/evaluate/evaluator.py` | `DynSTEEREvaluator.from_env()` | 主代码无调用；`from_config()` 已覆盖能力 | 删除并更新 API 文档 |
| `dynsteer/prompt/template.py` | `PromptTemplate.supported_languages` | 无生产读取 | 删除 |
| `dynsteer/prompt/template.py` | `load_prompt_text()` | 仅包级 re-export，无生产调用 | 删除并取消导出 |
| `dynsteer/stage/goal.py` | `generate_stage_goals()` | 仅包级 re-export；主链路使用 template + materialize 两阶段接口 | 删除并取消导出 |
| `dynsteer/adapter/base.py`、ToolSandbox harness | `case_finished()` | evaluator/default writer 只使用 `HarnessAdvanceResult.continue_running` | 删除父子类方法 |
| `dynsteer/adapter/base.py` | `dependency_error_message` | 父子类写入后无读取 | 删除父子类字段 |

`get_log_buffer()`、`clear_log_buffer()` 和 `BufferLogHandler` 虽然没有当前业务调用，但 `docs/constraints/code.md` 明确要求日志具备缓冲区能力，因此本方案不将它们列为死代码。抽象基类中被 ToolSandbox 覆写或供未来 benchmark 实现的 session hook 也属于真实协议，不删除。

## 1. 统计口径与当前基线

统计器按物理行处理，并遵循以下规则：

1. 只统计非测试、非文档 `.py` 文件。
2. 排除空白行和仅含 `#` 注释的行。
3. 通过 AST 排除模块、类、函数 docstring 的完整行区间。
4. 排除独立的 `logger.debug/info/warning/error/exception/critical/log` 调用所覆盖的完整行区间。
5. `print()` 是 CLI 的用户输出，不是日志，因此仍计入有效行数。
6. 不按语句数计数，不因把多行改成一行而减少统计值。

当前基线：

| 范围 | 文件数 | 有效行数 |
| --- | ---: | ---: |
| `dynsteer/**/*.py` | 80 | 11,798 |
| `display/**/*.py` | 2 | 825 |
| `main.py` | 1 | 114 |
| 合计 | 83 | 12,737 |

当前仓库没有 `tests/` 或其他测试 Python 文件，`pytest -q -p no:cacheprovider` 的结果为 `no tests ran`。因此实施时必须先增加行为刻画测试，再开始删除或合并；新增测试不计入上述有效行数。

## 2. 目标架构：每类事实只保留一个权威来源

实施后的权威来源应固定为：

| 事实 | 唯一权威来源 | 不再保存的副本 |
| --- | --- | --- |
| case 分数 | case `summary.json` 的 `score` | `default_score`、`dynsteer_score` 双字段 |
| 详细阶段报告 | `report.json.stage_reports` | `settlement.metadata.stage_report` |
| replay 执行摘要 | `report.json.metadata.replay_execution` | raw summary、finish settlement、finish stage 和 report 顶层副本 |
| termination | `termination={should_stop, code, reason, detail}` | 四个平铺字段及错位的 `termination_detail=reason` |
| runtime 指标 | `summary.json.runtime_metrics` | summary 顶层重复的 elapsed/token/step/timing 字段 |
| benchmark 原生结果 | default `report.json.default_result` | replay metadata 的整份 `default_reference` |
| stage id | `anchor_id->milestone_id` | `runtime:fail:*`、`runtime:missing:*` 兼容映射 |
| 输出报告名 | 所有方法统一 `report.json` | `default_report.json` 特例 |
| 运行期终止状态 | `RuntimeEvaluationState.evaluation_termination` | evaluator 局部 `termination/virtual_termination` 镜像 |

这项原则是后续所有精简的判断依据。若一个字段能从同一对象中的其他稳定字段无歧义推导，则不再持久化第二份状态。

## 3. P0：删除死代码、死字段与无效协议

### 3.1 删除未使用函数和枚举

修改：

- `dynsteer/config.py`：删除 `DIMENSION_ORDER`，保留实际被 `evaluate/weights.py` 使用的 `TASK_TYPE_WEIGHTS`。
- `dynsteer/model.py`：删除 `EvaluationFailureBasis`。
- `dynsteer/evaluate/evaluator.py`：删除 `from_env()`；API 示例统一为 `DynSTEEREvaluator.from_config(...)`。
- `dynsteer/prompt/template.py`：删除 `supported_languages` 和 `load_prompt_text()`。
- `dynsteer/stage/goal.py`：删除只负责依次调用 `generate_stage_goal_templates()` 和 `materialize_stage_goals()` 的 `generate_stage_goals()`。
- `dynsteer/prompt/__init__.py`、`dynsteer/stage/__init__.py`：取消上述符号导出。
- `docs/apis/evaluate.md`、`stage_goal.md`、`harness.md`：删除旧接口说明，明确 loader 的真实两阶段流程。

修改后的 stage goal 主流程固定为：

```python
templates = generate_stage_goal_templates(task_case, mode, llm_provider)
task_case.stage_goal_templates = templates
task_case.stage_goals = materialize_stage_goals(task_case, templates)
```

不新增替代中转函数。

### 3.2 删除只写不读的数据字段

修改 `dynsteer/model.py`：

- `ThresholdConfig` 删除 `safe_minefield_threshold`、`risky_minefield_threshold`；保留实际用于策略的 `fatal_minefield_threshold`。
- `MilestoneGraph` 删除 `default_thresholds`。
- `TaskCase` 删除 `policy_constraints`。
- `StageEvaluationResult` 删除 `minefield_evidence_is_structural`。
- `CaseProgressEvent` 删除 `message`。

同步修改：

- `dynsteer/adapter/loader.py` 不再读取 `default_thresholds`、`policy_constraints`。
- `dynsteer/harness/config.py::threshold_config_from_mapping()` 因使用 `dataclasses.fields()` 自动接受新字段集，不增加旧字段兼容。
- 重新生成包含这两个废弃 key 的 adapted case；不手工维护旧 schema。
- API 文档删除这些字段，历史 `docs/plans` 文档按约束保留原样。

### 3.3 删除重复终止协议和空类型层

修改 `dynsteer/adapter/base.py`：

- 删除抽象 `case_finished()`；运行循环唯一依据为 `HarnessAdvanceResult.continue_running`。
- 删除 `dependency_error_message`。
- 删除没有行为的 `BaseBenchmarkConstraintScorer(GeneralScorer)`；`BaseBenchmarkHarness.constraint_scorer()` 直接返回 `GeneralScorer`。

修改 ToolSandbox：

- `ToolSandboxHarness` 删除 `case_finished()` 和 `dependency_error_message`。
- `ToolSandboxConstraintScorer` 直接继承 `GeneralScorer`。

这不会删除 `metrics_from_session()`、`initial_state_from_session()`、`final_state_from_session()`、`raw_summary_from_session()`、`stop_case()` 或 `teardown_case()`；这些方法均在主执行链中使用，或是 benchmark 的必要可覆写协议。

### 3.4 收紧真实类型

- `RuntimeEvaluationState.milestone_frontier` 改为必填 `MilestoneFrontierState`；当前只有 `_initial_runtime_state()` 一个构造点，且总会提供该值。删除 `evaluate/step.py` 的重复 `None` 检查。
- `HarnessRunResult.task_case`、`trajectory` 改为非 Optional；删除 `__post_init__()` 中与类型声明矛盾的空值分支。
- `HarnessStageSettlement.checkpointed` 删除；当前仅 milestone 构造为 `True`，其值完全等价于 `kind == "milestone"`。

## 4. P0：用显式 registry 取代项目内懒加载

当前 `dynsteer/adapter/registry.py` 使用字符串模块路径和 `importlib.import_module()` 加载项目自身 adapter，违反 `docs/constraints/code.md` 的显式依赖约束，也导致静态工具无法确认 ToolSandbox 类的真实引用。

修改后采用显式类映射：

```python
from dynsteer.adapter.base import BaseBenchmarkAdapter, BaseBenchmarkHarness
from dynsteer.adapter.toolsandbox.adapter import ToolSandboxAdapter
from dynsteer.adapter.toolsandbox.harness import ToolSandboxHarness

_ADAPTERS: dict[str, type[BaseBenchmarkAdapter]] = {
    "toolsandbox": ToolSandboxAdapter,
}
_HARNESSES: dict[str, type[BaseBenchmarkHarness]] = {
    "toolsandbox": ToolSandboxHarness,
}
```

`get_adapter()`、`get_harness()` 直接规范化名称、查表并实例化；删除 `_load_registered_type()`、字符串分割、运行期类型检查和 `TypeVar`。第三方 ToolSandbox 的可选依赖仍只允许通过 `adapter/utils.py::import_module()` 边界延迟加载，不改为顶层强依赖。

同时：

- `dynsteer/adapter/toolsandbox/__init__.py` 不再 re-export 两个类。
- `dynsteer/experiment/runner.py` 改为从 `adapter.base` 和 `adapter.registry` 直接导入，`dynsteer/adapter/__init__.py` 不再承担未使用的聚合 API。
- 清理没有主代码包级使用的 `experiment/__init__.py`、`prompt/__init__.py` 导出；仍被大量主代码使用的 `stage`、`judges`、`llm`、`harness` 显式导出保留并缩减到实际使用符号。

## 5. P0：建立唯一的输出路径和报告 schema

### 5.1 统一报告名和 schema 版本

将 `RESULT_SCHEMA_VERSION` 提升为 3，并做一次性破坏性 schema 收口：

- default、evaluate、replay 均写 `report.json`。
- `existing_case_output()` 删除 `report_name` 参数，固定检查四个产物：`raw_summary.json`、`trajectory.json`、`summary.json`、`report.json`。
- schema 版本只保存在每个 JSON 顶层，不再同时写进 `metadata`。
- v2 产物不做字段级兼容；cache version 不匹配时重新执行。replay writer 删除“v2 但缺 timing 字段”的第二次兼容读取。

### 5.2 输出路径只支持当前两种真实结构

保留的结构：

```text
# benchmark-only
<root>/<benchmark>/<method>/<case_id>/

# experiment
<root>/exp/<experiment_id>/<benchmark>/<model_id>/r<repeat_index>/<method>/<case_id>/
```

修改 `dynsteer/harness/paths.py`：

- 将单次使用的 `output_method()`、`output_experiment_id()`、`output_model_id()` 内联到 `case_output_dir()`。
- 新增一个真正共享的 `case_identity_from_output_dir()`，只解析上述两个 schema，返回 benchmark、experiment、model、repeat、method、case identity。
- builder 和 parser 使用同一套目录规则测试，确保往返一致。

不支持：

- `experiments/<id>` 历史目录。
- experiment 目录缺少 `r<repeat>` 的旧布局。
- experiment 输出缺少 model id。
- 从目录深度猜测多套旧 schema。

旧产物不删除；展示生成器只跳过非 v3/非规范目录，用户可在新目录重新生成。

## 6. P0：精简 experiment result 与 default/replay 依赖链

### 6.1 `ExperimentCaseResult` 只保留实际参与指标的字段

修改后的模型：

```python
@dataclass(frozen=True)
class ExperimentCaseResult:
    experiment_id: str
    benchmark: str
    case_id: str
    model_id: str
    repeat_index: int
    method: ExperimentMethod
    score: float | None = None
    milestone_coverage: str | None = None
    minefield_match_count: int | None = None
    termination_code: str | None = None
    runtime_metrics: JsonObject = field(default_factory=dict)
    output_paths: JsonObject = field(default_factory=dict)
```

删除：

- `default_score` / `dynsteer_score` 及其选择属性；method 已经决定 score 语义。
- `score_components`；它属于 case summary，不参与 experiment 聚合。
- `raw`；其中的 summary metadata 和 default reference 均已有原始产物。
- `_base_payload()` 中动态 `asdict + EXCLUDE_FIELDS` 逻辑；`to_dict()` 与 `to_index_dict()` 用一个小型显式 identity 开关生成稳定 payload。

### 6.2 删除 `default_reference` 的整链路复制

`default_reference` 不参与 replay 评分、finish、judge 或 metrics，只用于输出审计；同 identity 的 default case 已经是唯一权威结果。因此：

- `DefaultOutputCache` 改为 `dict[DefaultOutputKey, HarnessEvaluationOutput]`。
- `run_default_case()` 只返回 output，不再回读 `raw_summary.json.default_result`。
- `_run_default_case_entry()`、`_run_replay_case_entry()`、`run_replay_case()` 不再传递 reference dict。
- `write_replay_case_outputs()` 删除 `default_reference` 参数，不再把它复制进 config metadata 和 raw summary。
- `_case_result_from_output()` 不再从 metadata/default raw 做兼容回退，只读取 v3 summary 的 `score`。
- `DynSTEEREvaluator._report_metadata()` 不再写 `default_reference/default_score`。
- `prompt/judge.py::_safe_extra_context()` 随之删除；judge context 不再先注入再过滤对照数据。

审计关联通过 experiment identity 和规范 default 路径完成，不复制原生 mapping 大对象。

## 7. P0：收口 case 输出 writer

### 7.1 evaluate/replay 共用同一套 payload 组装

`write_case_outputs()` 与 `write_replay_case_outputs()` 目前重复：

- 取得 report/runtime metrics。
- 构造 raw/summary/report。
- 计算 score components 和 minefield 数量。
- 写 termination。
- 给 metadata 再写 schema version。
- 调用 `_write_output_payloads()`。

新增一个有实际共享价值的 `_evaluation_payloads(harness_result, config)`，只返回三类 payload；两条公开 writer 各自只负责执行 evaluator、cache 和目录选择。共享函数中：

- `score_components` 只放轻量 `summary.json`；`report.json` 已含 stage reports，raw summary 已含 settlements，不再复制 stage score map。
- `replay_execution` 仅随 report metadata 序列化，不再附加顶层副本。
- schema version 只写顶层。
- `trajectory_output` 删除已存在于 `runtime_metrics` 的 step/timing 副本，只保留 path、snapshot count、final-state presence。

### 7.2 termination 使用单个结构对象

统一结构：

```json
"termination": {
  "should_stop": true,
  "code": "evaluation_policy_stop",
  "reason": "阶段式动态评估触发提前终止",
  "detail": {}
}
```

修改点：

- `EvaluationTerminationState.to_dict()` 输出 `code/reason/detail`，字段名与持久化 schema 一致。
- `_build_runtime_result()`、三条 writer、display 和 experiment metrics 只读写 `termination`。
- 删除 `terminated_by_policy`、`termination_code`、`termination_reason`、`termination_detail` 四个平铺副本。
- 修复当前 writer 把 termination code 写到 `termination_reason`、把人类 reason 写到 `termination_detail` 的错位。
- default 原生终止原因在 writer 边界转换为相同结构。

### 7.3 summary 不再展开 runtime metrics

`TrajectoryEvaluationReport.to_summary_dict()` 保留：task id、coverage、overall score、stage count、first failure、runtime metrics、metadata。删除 `elapsed_seconds`、`step_count`、token、timing 等从 `runtime_metrics` 重复展开的字段。

default summary 同样只保留 `runtime_metrics`，不再另写三个 timing 空字段。

### 7.4 方法级 summary 不复制 case summary

`_build_method_level_summary()` 当前用 `dict(summary_data)` 复制完整 case summary，再追加 identity、`default_score/dynsteer_score` 和路径。修改后 case item 仅包含：

- case identity。
- `score`、coverage、minefield count、termination code。
- summary/report 相对路径。

聚合器直接从 v3 metadata 读取 benchmark/model/experiment，不再使用 `_summary_benchmark()`、`_summary_model_id()`、`_summary_experiment_id()` 和 `_score_from_summary()` 的路径/旧字段回退，四个 helper 全部删除。

## 8. P1：统一 live/replay 单步评估算法

### 8.1 抽出真正相同的单步核心

在 `DynSTEEREvaluator` 中增加一个内部核心 `_evaluate_appended_step()`：

```python
def _evaluate_appended_step(..., step: TrajectoryStep) -> tuple[RuntimeEvaluationDecision | None, bool]:
    decision = evaluate_step_minefields(...)
    if decision is not None:
        return decision, False
    closure = state.agent_step_tracker.ingest(step)
    if closure is None:
        return None, False
    return self._evaluate_closed_agent_step(..., closure), True
```

live 与 replay 均按以下顺序调用：

1. 追加 step 和当步可见 snapshot。
2. 调用 `_evaluate_appended_step()`。
3. live 负责真实 `harness.stop_case()` 副作用；replay 只记录虚拟终止。

删除两条流程中重复的 minefield、closure、closed step 分支。自然结束时未闭合 step 的 finalize 保留一个共享内部函数。

### 8.2 `RuntimeEvaluationState` 成为 termination 唯一来源

- evaluate/replay 删除局部 `termination`、`virtual_termination` 镜像。
- `_evaluate_step()` 在接受真实终止时写 `state.evaluation_termination`。
- `_record_replay_decision()` 改为原地更新 state，不再接收 current termination 并返回另一份对象。
- `_finalize_state_reports()`、`_build_runtime_result()`、timing 和 replay summary 均从 state 读取。

这样 pending diagnostics、最终报告和循环停止条件不会再从不同变量读取同一状态。

### 8.3 删除 replay execution 的多层复制

`build_replay_execution_summary()` 的结果只写：

```python
report.metadata["replay_execution"] = replay_execution
```

删除：

- `result.raw_summary["replay_execution"]`。
- finish settlement metadata 的 `replay_execution`。
- settlement 内嵌 stage report metadata 的 `replay_execution`。
- finish stage metadata 的 `replay_execution`。
- writer 再次向 raw/summary/report 顶层复制。

## 9. P1：精简 finish settlement 和诊断 metadata

### 9.1 删除重复参数和无变化返回值

当前 `finish_settlement()` 同时接收 `settlements` 和包含同一列表的 `state`，并返回未发生变化的 `evaluation_policy`。修改为：

```python
def finish_settlement(
    task_case: TaskCase,
    trajectory: Trajectory,
    scorer: GeneralScorer,
    state: RuntimeEvaluationState,
    standard_judge: StandardJudge | None,
    thresholds: ThresholdConfig,
) -> tuple[HarnessStageSettlement, StageEvaluationResult]:
```

- settlement id 使用 `len(state.settlements)`。
- 删除 `settlements`、`replay_termination` 参数。
- 删除 `EvaluationPolicyState` 返回值；调用方保留 `state.evaluation_policy`。
- `_deterministic_finish_stage_result()`、`_whole_trajectory_finish_stage_result()` 删除仅用于 metadata 的 evaluation policy/replay 参数。

### 9.2 settlement 不再嵌入完整 stage report

`HarnessStageSettlement` 增加直接字段 `stage_id`，删除 `checkpointed`。metadata 只保留 settlement 独有诊断：

```text
stage_anchor_milestone_id
stage_start_boundary_step_index
stage_trace
milestone_matching
finish_stage_evaluation（仅 finish）
```

删除 `metadata.stage_report = stage_result.to_dict()`。完整阶段报告已经在 `TrajectoryEvaluationReport.stage_reports`，display 按 `stage_id` 关联两者。

### 9.3 finish verification 删除互补布尔字段

`build_finish_verification()` 只保留控制流和审计实际使用的字段：

- `coverage_basis`：`milestone_graph` / `minefield_only` / `whole_trajectory`。
- `status`、`score`。
- unmatched ids、terminal checks、fatal minefield。
- evidence、diagnosis。

删除只写不读或可推导字段：

- `empty_milestone_graph`。
- `fixed_milestones_applicable`。
- `default_reference_used`。
- `all_milestones_matched`（由 unmatched ids 判定）。
- `whole_trajectory_evaluation_required`（由 coverage basis 与 fatal 状态判定）。
- `whole_trajectory_evaluation`、`whole_trajectory_evaluator_unavailable` 两个互补布尔，改为一个 `evaluation_mode=deterministic/standard_judge/unavailable`。

`_finish_stage_metadata()` 只组装 `finish_stage_evaluation` 和真正额外的维度诊断；删除永远为空的 `evaluation_termination`、未变化的 `next_evaluation_policy`、replay virtual stop 和 `finish_after_virtual_stop` 副本。

## 10. P1：删除 display 的旧 schema 兼容层

### 10.1 以 v3 canonical path 发现 case

- `display/build.py` 改为模块运行：`python -m display.build`，删除手工修改 `sys.path`。
- 直接导入 `dynsteer.graph.START_NODE_ID/FINISH_NODE_ID`、`stage.resolve.DEFAULT_FINISH_STAGE_GOAL`、`model.JsonObject`，删除本地重复常量/类型。
- `_collect_case_keys()` 搜索 `summary.json` / `trajectory.json` 文件的父目录，不再对每个目录执行四次 `exists()`。
- 调用 `harness.paths.case_identity_from_output_dir()`，CaseKey 纳入 `repeat_index`。
- 删除 `EXPERIMENT_ROOT_DIRS={exp, experiments}`、`_experiment_case_root()` 和 3/4 层路径猜测。

这同时修复当前 display 无法识别实际 `.../<model>/r0/<method>/<case>` 路径的问题。

### 10.2 删除 stage id 迁移逻辑

v3 stage report、settlement 和 adapted case 均使用 `anchor->milestone`：

- 删除 `_legacy_pending_milestone_id()`。
- `_definition_for_stage_id()` 只做 direct lookup，能直接内联时删除该 helper。
- 删除 `_replace_string()` 递归替换。
- `_stage_reports()` 不再返回 `stage_id_map`。
- `_settlement_summaries()` 不再返回 mapping。
- 删除 `_normalize_summary_stage_ids()` 和 `_normalized_stage_id()`。
- 合并 `_stage_definition_for_report()` / `_stage_definition_for_settlement()` 的同构 lookup，或在两个唯一调用点直接按 stage id/milestone id 查 index。

保留的展示行为：清洗 evidence、补 stage goal、提前终止时隐藏未执行 finish、minefield 定义富化。

### 10.3 删除纯中转 helper

- `_number()` 删除，调用点直接使用共享 `as_number()`。
- `_json_copy()` 改用标准库 `copy.deepcopy()`；避免 JSON round-trip 被误当复制原语。
- `_expected_detail()` 只有一次真实调用，直接使用 `_text(expected, 1200)`。

## 11. P1：算法复杂度优化而不是排版压缩

### 11.1 `Trajectory.get_interval()` 从 O(n) 改为 O(log n + k)

当前每次阶段 prompt、trace、telemetry 都扫描完整 `steps`。由于 `Trajectory` 已强制 step index 递增，直接使用 `bisect_right(..., key=lambda step: step.index)` 定位左右边界，然后返回 list slice：

```python
left = bisect_right(self.steps, min_index, key=lambda step: step.index)
right = bisect_right(self.steps, max_index, key=lambda step: step.index)
return self.steps[left:right]
```

无需维护第二份 step index 数组。`append_step()` 调整为先校验 index、后 append，避免异常时先污染列表。

### 11.2 snapshot 增量去重，避免每批 O(n log n) 重建

当前 `extend_snapshots()` 每次都：

1. 把历史 snapshots 全量重建成 dict。
2. 合并本批数据。
3. 对全量数据排序。

长会话多批推进会退化为近似 O(b × n log n)。修改为在 `Trajectory` 内维护私有 `snapshot_id -> position` 索引：

- 新 id 且顺序不下降时 O(1) append。
- 同 id 时原位替换。
- 仅在第三方输入真正乱序时排序一次并重建索引。
- `__post_init__()` 对初始 snapshots 建索引并检查重复。

该项可能净增加少量有效行，但消除大轨迹上的重复全量工作；不得为了行数目标放弃。

### 11.3 experiment metrics 消除重复全表扫描

`rank_tau_by_repeat()` 当前先构造一个没有被用于取值的 grouped 结构，再针对每个 group 两次扫描全部 results。改为一次构造：

```text
(benchmark, model, repeat) -> method -> case_id -> score
```

随后仅在 bucket 内比较 default 和各 replay，复杂度由 O(group × result_count) 降为 O(result_count + bucket 内配对)。

`discriminability_score()` 先生成一次规范化 model score map，pair 输出复用已规范化值，不再对同一值反复 `case_score()`。

## 12. P2：收口小型重复实现

### 12.1 日志 formatter 与 telemetry 清洗

- `StructuredLogFormatter` 增加 terminal 模式参数，删除逻辑完全相同的 `TerminalLogFormatter` 类。
- `evaluate/telemetry.py` 直接复用 `log.sanitize_log_value()`，删除 `_sanitize_extra()` / `_sanitize_value()` 的第二套递归截断逻辑。
- 缓冲 handler 和 get/clear API 按项目约束保留。

### 12.2 LLM provider fallback

`llm/factory.py::_read_api_key()` 和 `_read_base_url()` 合并为：

```python
def _provider_setting(source: Mapping[str, str], provider: str, setting: str) -> str | None:
    prefix = "ANTHROPIC" if provider in _ANTHROPIC_PROVIDERS else "OPENAI"
    return (
        normalize_str_from_source(source, f"DYNSTEER_JUDGE_{setting}")
        or normalize_str_from_source(source, f"{prefix}_{setting}")
    )
```

调用只传 `API_KEY` 或 `BASE_URL`，不保留两个同构函数。

### 12.3 frontier 状态直接使用 ids

`advance_milestone_frontier()` 已保证 matched id 会从 `ready_ids` 移除，因此：

- 删除接收 `matched` 后再次过滤的 `ready_milestone_ids()`。
- step/no-progress 直接读取 `tuple(frontier.ready_ids)`。
- settlement 的 `ready_milestone_ids_before_match` 直接复制 ready ids，不先转为 Milestone 再取回 id。
- `ready_milestones()`、`blocked_candidate_milestones()` 保留，因为 matching 需要实体对象。

### 12.4 不合并 `parse_int_value()` / `parse_float_value()`

两者代码形状相似，但分别承担整数拒绝浮点、浮点接收整数、返回类型和错误语义。为减少约十行引入泛型转换器会增加类型和分支复杂度，本方案明确不做这项“表面去重”。

## 13. 文件级修改矩阵

| 文件/目录 | 主要修改 |
| --- | --- |
| `main.py` | 仅适配统一 report/schema；不压缩 CLI 排版 |
| `dynsteer/model.py` | 删除死 enum/字段；frontier 必填；termination schema；Trajectory 二分区间与 snapshot 索引 |
| `dynsteer/config.py` | 删除 `DIMENSION_ORDER` |
| `dynsteer/adapter/base.py` | 删除 case_finished、空 scorer 子类、死字段 |
| `dynsteer/adapter/registry.py` | 显式类 registry，删除项目内懒加载 |
| `dynsteer/adapter/loader.py` | 删除 default_thresholds/policy_constraints 解析 |
| `dynsteer/adapter/toolsandbox/*` | 删除 case_finished/死字段；scorer 直接继承 GeneralScorer |
| `dynsteer/evaluate/evaluator.py` | 删除 from_env/default reference；共享单步核心；state 持有唯一 termination；replay metadata 单点 |
| `dynsteer/evaluate/settlement.py` | finish 参数/返回值收口；settlement 不嵌套 stage report；metadata 瘦身 |
| `dynsteer/evaluate/final.py` | finish verification 删除互补和只写不读字段 |
| `dynsteer/evaluate/telemetry.py` | 复用日志值清洗 |
| `dynsteer/evaluate/matching/frontier.py` | 删除冗余 ready id API |
| `dynsteer/experiment/model.py` | 单一 score 的精简 case result |
| `dynsteer/experiment/runner.py` | cache 只存 output；删除 default reference 回读/传递；v3 summary 直读 |
| `dynsteer/experiment/metrics.py` | 单遍 bucket 聚合，删除重复全表扫描 |
| `dynsteer/harness/model.py` | settlement 增 stage_id、删 checkpointed；result 类型收紧 |
| `dynsteer/harness/paths.py` | 单一 builder/parser；删除三个单次 wrapper |
| `dynsteer/harness/outputs.py` | v3 schema、统一 report、公共 evaluation payload、termination 对象、精简方法汇总 |
| `dynsteer/log.py` | 单 formatter；保留缓冲功能 |
| `dynsteer/llm/factory.py` | provider setting 单一 helper |
| `dynsteer/prompt/*` | 删除死 template API 和 default reference 过滤 |
| `dynsteer/stage/*` | 删除 generate_stage_goals 中转及导出 |
| `display/build.py` | canonical path + repeat；删除 legacy stage/path/schema 兼容 |
| `README.md`、`docs/apis/*` | 同步模块运行命令、v3 schema、删除的接口和统一报告名 |

## 14. 实施顺序

1. 先建立测试目录和当前 v2 行为刻画，不修改生产代码。
2. 删除 P0 死符号、死字段、case_finished 和空 scorer 类型；运行全部测试。
3. 改显式 registry 和包导出；执行 import smoke test。
4. 实现 canonical path/parser、统一 `report.json`、schema v3；更新 display path 测试。
5. 精简 `ExperimentCaseResult`，删除 default reference 传递链。
6. 收口三个 writer、termination 和 method summary。
7. 让 runtime state 成为 termination 唯一来源，再抽取 live/replay 共享单步核心。
8. 精简 finish settlement、settlement metadata 和 verification 字段。
9. 删除 display legacy stage/schema 兼容。
10. 实现 Trajectory 二分区间、snapshot 增量索引和 metrics 单遍聚合。
11. 收口 formatter、telemetry、provider setting、frontier 等小型重复。
12. 更新当前 API 文档和 README；历史 plans 不删除。
13. 运行全量验证、有效行统计和生成物 schema 检查。

每一步单独保持可运行，不把全部删除和 schema 改造堆到一次不可定位的提交中；本方案不执行 git commit。

## 15. 测试与验收

### 15.1 必须新增的行为测试

```text
tests/
├── adapter/
│   ├── test_registry.py
│   └── test_loader_schema.py
├── evaluate/
│   ├── test_evaluator_step_core.py
│   ├── test_finish_settlement.py
│   ├── test_replay_termination.py
│   └── test_settlement_serialization.py
├── experiment/
│   ├── test_case_result.py
│   ├── test_metrics.py
│   └── test_runner_default_cache.py
├── harness/
│   ├── test_outputs_v3.py
│   └── test_paths.py
├── display/
│   └── test_build.py
└── test_trajectory.py
```

关键断言：

- registry 可直接定位 ToolSandbox 类，不调用项目内 `import_module()`。
- loader 拒绝/忽略废弃 schema 的策略明确，新生成对象没有死字段。
- live/replay 对同一 step 序列产生一致 milestone/minefield 决策；差异只在真实 stop 副作用。
- state 中只有一个 termination，report/raw/display/metrics 读取同一 code。
- finish 三种模式（milestone graph、minefield-only、whole trajectory）行为不变。
- settlement 不包含 `metadata.stage_report`，仍可按 `stage_id` 与 report 关联。
- v3 default/evaluate/replay 都生成 `report.json`，不存在 `default_report.json`。
- v3 summary 不含 default/dynsteer 双 score、平铺 runtime 指标、default reference 和重复 schema version。
- path builder/parser 对 benchmark-only 和含 repeat 的 experiment 结构往返一致。
- display 能发现实际 `r0/r1` 目录并区分 repeat，不读取 `experiments/` 旧布局。
- `Trajectory.get_interval()` 对稀疏 index、边界值和空轨迹与当前语义一致。
- snapshot 同 id 替换、顺序追加、乱序追加均正确。
- metrics 新 bucket 算法与小型固定样例的预期值一致。

### 15.2 命令验收

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
uv run python -m pytest -q -p no:cacheprovider --cov=dynsteer --cov-report=term-missing
uv run python -m display.build --help
uv run python main.py --help
git diff --check
```

覆盖率要求：核心 evaluator、settlement、outputs、path、experiment metrics 不低于 80%。ToolSandbox 第三方运行只做边界 mock，不在单元测试发起网络请求。

### 15.3 schema 验收

对一个 default + replay、repeat_count=2 的最小 experiment 执行后检查：

1. 8 类 case 产物路径全部符合 canonical schema。
2. 每个 JSON 顶层 `result_schema_version == 3`，metadata 内无第二份版本。
3. default/replay 只有 `score`，没有 `default_score/dynsteer_score/default_reference`。
4. 每份 settlement 不嵌套 stage report。
5. replay execution 只出现于 report metadata 一次。
6. termination 四个信息只出现于一个结构对象。
7. display 输出包含两个 repeat，且报告、stage、settlement 关联正确。

## 16. 明确不做的“精简”

- 不把函数签名、dict、comprehension 或日志参数强行压成单行。
- 不删除核心函数中文 docstring 和关键步骤注释。
- 不把 `parse_int_value()`、`parse_float_value()` 合成难以类型检查的万能 parser。
- 不删除日志缓冲能力。
- 不删除抽象 harness 的真实可覆写 session hook。
- 不以“外部依赖尚未安装”为由删除 ToolSandbox adapter/scorer/harness。
- 不删除 `docs/constraints` 或已有 `docs/plans`。
- 不为旧 schema 继续增加兼容分支；通过 version=3 和重新生成完成迁移。

## 附录A. 项目中没有把握实现的模块部分

1. 仓库没有测试，且 ToolSandbox 源码是运行期可选依赖。静态分析能确认 DynSTEER 内部调用，但不能完全证明第三方 ToolSandbox 每一种 DataFrame/snapshot 顺序。snapshot 索引优化必须用 mock 覆盖乱序、重复 id，并用至少一个真实 ToolSandbox case 回归。
2. 无法静态证明仓库外脚本是否直接调用 `DynSTEEREvaluator.from_env()`、`generate_stage_goals()`、`load_prompt_text()` 或读取 `default_report.json/default_reference`。本任务已明确“主功能未使用即删除”，方案按破坏性升级处理；若存在仓库外调用，应在实施前迁移，而不是在仓库内保留兼容壳。
3. default/replay 的原生对照数据可能被仓库外分析脚本从 replay metadata 读取。删除整份 `default_reference` 后，等价信息仍可通过相同 identity 的 default `report.json` 获得，但外部脚本需要改为 join，而不能继续读取内嵌副本。
4. finish metadata 当前字段很多是近期实验审计字段。仓库主代码没有读取拟删除的互补布尔，但外部论文分析脚本是否读取无法确认；实施前应在现有 results 上执行一次字段使用清单核对。
5. 预计行数以方案级净变化计算；snapshot 索引和必要测试可能使具体生产代码缩减在区间内波动。验收以重新运行同一统计器的结果为准，不通过排版调整补足目标。

## 附录B. 有效行数缩减统计

预计净缩减按功能项计算：

| 项目 | 预计净缩减 |
| --- | ---: |
| 明确死函数、枚举、字段、协议和无用导出 | 70 |
| 显式 registry、包导出与单次 path wrapper 收口 | 55 |
| ExperimentCaseResult 与 default reference 传递链删除 | 75 |
| v3 output writer、termination、runtime/score 副本及方法汇总收口 | 125 |
| live/replay 单步流程和唯一 termination state | 75 |
| finish settlement、stage report 副本和 verification metadata | 85 |
| display legacy path/stage/schema 兼容删除 | 70 |
| formatter、telemetry、LLM provider、frontier、metrics 小型重复 | 40 |
| Trajectory 算法优化的净行数变化 | 5 |
| 合计目标 | 600 |

落地后的目标：

| 范围 | 当前 | 预计缩减 | 预计落地后 |
| --- | ---: | ---: | ---: |
| `dynsteer/**/*.py` | 11,798 | 约 530 | 约 11,268 |
| `display/**/*.py` | 825 | 约 70 | 约 755 |
| `main.py` | 114 | 0 | 114 |
| 合计 | 12,737 | 约 600 | 约 12,137 |

考虑 snapshot 索引实现和测试驱动调整，生产代码净缩减的保守验收区间为 540–660 行；最低验收要求为总有效行数不高于 12,197 行，目标值约 12,137 行。测试和文档行数不计入有效行数，也不得用删除注释、docstring 或强行合并物理行来达到该目标。
