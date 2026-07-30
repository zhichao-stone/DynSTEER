# DynSTEER 评估缓存、no_sum 与 run_id 删除最终详细实施方案

## 1. 最终结论

本方案取代 `2026-07-30-dynsteer-eval-cache-and-toolsandbox-parallel-audit-report.md` 中的概略实现部分，作为后续代码落地的最终详细方案。

固定决策如下：

1. 评估进度缓存只按 case 级文件是否存在判断，不引入数据库、manifest、lock 文件或额外索引。
2. `--force_eval` 表示忽略已有 `runs/results` case 产物，重新执行并覆盖。
3. `--force_adapt` 表示重建 adapted case，并且必须自动强制 `force_eval=True`。基础输入变了，旧评估结果不能复用。
4. `--no_sum` 只用于统一实验入口，表示跳过最终实验级汇总文件 `index.json`、`scores.json`、`metrics.json`，仍正常写 case 级 `runs/results`。
5. 删除所有代码层 `run_id` 使用，不引入 `model_id`、`repeat_index` 或其他替代路径隔离键。
6. 路径只由 `experiment_id / benchmark / method / case_id` 或 `benchmark / method / case_id` 锚定；同一路径重复运行直接覆盖。
7. 本方案默认实验操作者自行保证并行分片配置之间的 `case_id` 完全分离；不为 case 级覆盖风险做额外处理。

## 2. 最终文件路径结构

### 2.1 统一实验入口

入口包括：

- `python main.py --exp <experiment_config>`
- `scripts/start_experiment.sh`
- `scripts/start_experiment_no_docker.sh`

默认根目录：

```text
runs_dir    = runs/experiments/<experiment_id>
results_dir = results/experiments/<experiment_id>
```

case 级中间产物：

```text
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/raw/
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/raw_summary.json
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/trajectory.json
```

case 级评估结果：

```text
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/summary.json
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/default_report.json
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/report.json
```

实验级汇总结果：

```text
results/experiments/<experiment_id>/index.json
results/experiments/<experiment_id>/scores.json
results/experiments/<experiment_id>/metrics.json
```

`default` 方法写 `default_report.json`；`dynsteer_evaluate`、`dynsteer_replay`、`dynsteer_replay_static` 写 `report.json`。

### 2.2 单 benchmark 入口

入口包括：

- `python main.py --benchmark <benchmark>`
- `scripts/start.sh`
- `scripts/start_no_docker.sh`

默认根目录：

```text
runs_dir    = runs
results_dir = results
```

case 级中间产物：

```text
runs/<benchmark>/<method>/<case_id>/raw/
runs/<benchmark>/<method>/<case_id>/raw_summary.json
runs/<benchmark>/<method>/<case_id>/trajectory.json
```

case 级评估结果：

```text
results/<benchmark>/<method>/<case_id>/summary.json
results/<benchmark>/<method>/<case_id>/default_report.json
results/<benchmark>/<method>/<case_id>/report.json
```

方法级汇总结果：

```text
results/<benchmark>/<method>/summary.json
```

该文件由现有 `write_run_level_summaries()` 演化而来，落地后改名为 `write_method_level_summaries()`，字段中不再包含 `run_id`。

## 3. 修改总表

| 文件 | 修改目标 |
| --- | --- |
| `main.py` | 新增 `--force_eval`、`--no_sum`；传入 runner；`--force_adapt` 文案改为会强制重跑评估 |
| `dynsteer/experiment/model.py` | 删除 `ExperimentRunSpec.run_id` 和 `ExperimentCaseResult.run_id` |
| `dynsteer/experiment/config.py` | 删除 `_next_run_id()`、`run_sequences`、基于 `run_id` 的矩阵校验 |
| `dynsteer/experiment/runner.py` | 增加 `force_eval`、`no_sum`；实现 `force_adapt => force_eval`；删除 index 中的 `run_id` |
| `dynsteer/harness/paths.py` | `case_output_dir()` 删除 `run_id` 入参和路径层级 |
| `dynsteer/adapter/base.py` | 删除 `build_run_id()` 和只为它服务的 `re` import |
| `dynsteer/harness/config.py` | 不再生成或写入 `metadata["run_id"]`；不再校验 run_id 重复 |
| `dynsteer/harness/model.py` | 删除 `HarnessRunResult.run_id` |
| `dynsteer/model.py` | 删除 `Trajectory.run_id`、`TrajectoryEvaluationReport.run_id`、`ToolSandboxSession.run_id` |
| `dynsteer/adapter/loader.py` | 加载 `trajectory.json` 时不再要求 `run_id` |
| `dynsteer/adapter/toolsandbox/harness.py` | 不再从路径反推 `run_id`，ToolSandbox 轨迹不再传 run_id |
| `dynsteer/adapter/toolsandbox/utils/trajectory.py` | `trajectory_from_sandbox_rows()` 删除 `run_id` 参数 |
| `dynsteer/evaluate/evaluator.py` | 删除 evaluate/replay 全链路中的 `run_id` 和 `_replay_run_id()` |
| `dynsteer/harness/outputs.py` | 增加 `existing_case_output()`；三类 case 写出函数支持缓存；删除输出 JSON 的 `run_id` |
| `dynsteer/harness/runner.py` | 单 benchmark runner 传递 `force_eval`，并实现 `force_adapt => force_eval` |
| `dynsteer/harness/scheduler.py` | 调度层传递 `force_eval`；异常上下文删除 `run_id` |
| `scripts/start_experiment.sh` | 增加 `--force_eval`、`--no_sum`；修正末尾续行 |
| `scripts/start_experiment_no_docker.sh` | 增加 `--force_eval`、`--no_sum` |
| `scripts/start.sh`、`scripts/start_no_docker.sh` | 增加单 benchmark `--force_eval` |
| `docs/apis/*.md`、`README.md`、`display/build.py` | 同步删除 run_id 路径和 schema |
| `tests/` | 新增最小 pytest 覆盖缓存、路径、force 关系、no_sum 和 schema |

## 4. `main.py`

### 4.1 参数解析

修改位置：`_parse_args()`。

当前只有：

```python
parser.add_argument("--force_adapt", "--force-adapt", action="store_true", help="强制重建已有 adapted case，可与评估流程独立使用")
```

改成：

```python
parser.add_argument(
    "--force_adapt",
    "--force-adapt",
    action="store_true",
    help="强制重建已有 adapted case，并自动重新执行评估流程",
)
parser.add_argument(
    "--force_eval",
    "--force-eval",
    action="store_true",
    help="强制重新执行评估流程并覆盖已有 runs/results case 产物",
)
parser.add_argument(
    "--no_sum",
    "--no-sum",
    action="store_true",
    help="统一实验入口下仅写 case 级结果，不写 index/scores/metrics 汇总文件",
)
```

### 4.2 统一实验入口调用

修改位置：`main()` 中 `args.experiment_config is not None` 分支。

当前：

```python
results = run_experiment(Path(args.experiment_config), workers=int(args.workers), force_adapt=bool(args.force_adapt))
```

改为：

```python
results = run_experiment(
    Path(args.experiment_config),
    workers=int(args.workers),
    force_adapt=bool(args.force_adapt),
    force_eval=bool(args.force_eval),
    no_sum=bool(args.no_sum),
)
```

### 4.3 单 benchmark 入口调用

修改位置：`main()` 中 `run_harness_configs(...)` 调用。

当前：

```python
outputs: list[HarnessEvaluationOutput] = run_harness_configs(
    configs=configs,
    max_workers=int(args.workers),
    force_adapt=bool(args.force_adapt),
)
```

改为：

```python
outputs: list[HarnessEvaluationOutput] = run_harness_configs(
    configs=configs,
    max_workers=int(args.workers),
    force_adapt=bool(args.force_adapt),
    force_eval=bool(args.force_eval),
)
```

`--no_sum` 在单 benchmark 分支不使用。

## 5. `dynsteer/experiment/model.py`

### 5.1 `ExperimentRunSpec`

删除字段：

```python
run_id: str
```

删除校验：

```python
if not self.run_id.strip():
    raise ValueError("run_id 不能为空")
```

`to_metadata()` 中删除：

```python
"run_id": self.run_id,
```

目标结构：

```python
@dataclass(frozen=True)
class ExperimentRunSpec:
    experiment_id: str
    benchmark: str
    data_root: Path
    runs_dir: Path
    results_dir: Path
    case_ids: tuple[str, ...] | None
    model_id: str
    repeat_index: int
    method: ExperimentMethod
    judge_profile: str | None = None
    judge_config: JsonObject = field(default_factory=dict)
    threshold_profile: str | None = None
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    strategy: EvaluationStrategyConfig = field(default_factory=EvaluationStrategyConfig)
    metadata: JsonObject = field(default_factory=dict)
```

`to_metadata()` 目标：

```python
metadata.update({
    "experiment_id": self.experiment_id,
    "method": self.method.value,
    "model_id": self.model_id,
    "repeat_index": self.repeat_index,
    "judge_profile": self.judge_profile,
    "judge": dict(self.judge_config),
    "threshold_profile": self.threshold_profile,
    "thresholds": asdict(self.thresholds),
    "strategy": self.strategy.to_dict(),
})
```

### 5.2 `ExperimentCaseResult`

删除字段：

```python
run_id: str
```

`_base_payload()` 的排除字段改为：

```python
EXCLUDE_FIELDS = [] if include_identity else [
    "experiment_id",
    "benchmark",
    "case_id",
    "model_id",
    "repeat_index",
    "method",
]
```

## 6. `dynsteer/experiment/config.py`

### 6.1 删除 run_id 生成

删除局部变量：

```python
run_sequences: dict[tuple[str, str], int] = {}
```

删除循环内：

```python
run_id = _next_run_id(run_sequences, benchmark, method.value)
```

`ExperimentRunSpec(...)` 构造从：

```python
ExperimentRunSpec(
    experiment_id=experiment_id, run_id=run_id, benchmark=benchmark,
    ...
)
```

改为：

```python
ExperimentRunSpec(
    experiment_id=experiment_id,
    benchmark=benchmark,
    data_root=data_root,
    runs_dir=runs_dir,
    results_dir=results_dir,
    case_ids=case_ids,
    model_id=model_id,
    repeat_index=repeat_index,
    method=method,
    judge_profile=judge_profile,
    judge_config=judge_config,
    threshold_profile=threshold_name,
    thresholds=thresholds,
    strategy=strategy,
    metadata=metadata,
)
```

### 6.2 简化矩阵校验

当前 `validate_experiment_matrix()` 使用 `(benchmark, method, run_id)` 做唯一性检查。删除 `seen` 相关逻辑。

目标：

```python
def validate_experiment_matrix(specs: list[ExperimentRunSpec]) -> None:
    """检查实验矩阵基础字段合法性。"""
    for spec in specs:
        if spec is None:
            raise ValueError("specs 不能包含空规格")
        if spec.method == ExperimentMethod.DYNSTEER_GUIDANCE and (not spec.strategy.guidance_enabled):
            raise ValueError("dynsteer_guidance 方法必须启用 guidance_enabled")
```

### 6.3 删除 `_next_run_id()`

删除文件末尾函数：

```python
def _next_run_id(...):
    ...
```

## 7. `dynsteer/harness/paths.py`

当前：

```python
def case_output_dir(base_dir: Path, config: HarnessRunConfig, run_id: str, case_id: str, method_fallback: str) -> Path:
    ...
    return base_dir / config.benchmark / method / run_id / case_id
```

改为：

```python
def case_output_dir(base_dir: Path, config: HarnessRunConfig, case_id: str, method_fallback: str) -> Path:
    """构造单个 benchmark case 的输出目录。"""
    if base_dir is None or config is None or not case_id.strip():
        raise ValueError("base_dir、config 和 case_id 不能为空")
    method = output_method(config, method_fallback)
    return base_dir / config.benchmark / method / case_id
```

所有调用点同步删除 `run_id` 实参。

## 8. `dynsteer/adapter/base.py`

删除 import：

```python
import re
```

删除方法：

```python
def build_run_id(self, config: HarnessRunConfig, case_id: str) -> str:
    ...
```

删除后 `BaseBenchmarkHarness` 不再负责运行身份构造。

## 9. `dynsteer/harness/config.py`

### 9.1 控制字段

把 `run_id` 和 `name` 纳入控制字段，避免它们原样进入 metadata：

```python
_RUN_CONFIG_CONTROL_FIELDS = {
    "scenarios",
    "ready_frontier_patience",
    "thresholds",
    "strategy",
    "run_id",
    "name",
    *_CLIENT_CONFIG_KEYS,
}
```

### 9.2 删除 run_id 生成与去重

删除：

```python
seen_run_ids: set[str] = set()
```

删除循环内：

```python
run_id = optional_str(raw_spec.get("run_id")) or optional_str(raw_spec.get("name"))
...
metadata["run_id"] = run_id
```

保留 `name` 作为日志标签：

```python
name = optional_str(raw_spec.get("name"))
if name is not None:
    metadata["run_config_name"] = name
```

### 9.3 `_config_label()`

`dynsteer/harness/runner.py::_config_label()` 当前从 `metadata["run_id"]` 兜底。删除该兜底，只保留：

```python
def _config_label(config: HarnessRunConfig) -> str:
    """构造日志中使用的运行配置标签。"""
    if config is None:
        raise ValueError("config 不能为空")
    name = config.metadata.get("run_config_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    index = config.metadata.get("run_config_index")
    return f"第{index}项配置" if index is not None else "未命名配置"
```

## 10. `dynsteer/harness/model.py`

`HarnessRunResult` 删除字段：

```python
run_id: str
```

删除校验：

```python
if not self.run_id or not self.run_id.strip():
    raise ValueError("run_id 不能为空")
```

目标：

```python
@dataclass(frozen=True)
class HarnessRunResult:
    """benchmark harness 运行结果。"""
    benchmark: str
    case_id: str
    task_case: TaskCase | None
    trajectory: Trajectory | None
    raw_output_dir: Path
    raw_summary: JsonObject = field(default_factory=dict)
    stage_settlements: list[HarnessStageSettlement] = field(default_factory=list)
    evaluation_report: TrajectoryEvaluationReport | None = None
    termination: EvaluationTerminationState = field(default_factory=EvaluationTerminationState)
```

## 11. `dynsteer/model.py`

### 11.1 `Trajectory`

删除字段：

```python
run_id: str
```

目标：

```python
@dataclass
class Trajectory:
    task_id: str
    steps: list[TrajectoryStep]
    snapshots: list[StateSnapshot] = field(default_factory=list)
    final_state: Optional[JsonObject] = None
    metrics: JsonObject = field(default_factory=dict)
    raw: JsonObject = field(default_factory=dict)
    first_step_index: int = 0
    successor_by_boundary: dict[int, int] = field(default_factory=dict)
    latest_step_index: int | None = None
```

### 11.2 `TrajectoryEvaluationReport`

删除字段：

```python
run_id: str
```

`to_dict()` 和 `to_summary_dict()` 删除 `"run_id": self.run_id`。

目标 `to_summary_dict()` 开头为：

```python
return {
    "task_id": self.task_id,
    "milestone_coverage": self.milestone_coverage,
    "overall_score": self.overall_score,
    ...
}
```

### 11.3 `ToolSandboxSession`

删除字段：

```python
run_id: str
```

## 12. `dynsteer/adapter/loader.py`

`load_trajectory()` 当前要求 `run_id`：

```python
known = {"run_id", "task_id", "steps", "snapshots", "final_state", "metrics"}
return Trajectory(run_id=required_str(...), task_id=..., ...)
```

改为：

```python
known = {"task_id", "steps", "snapshots", "final_state", "metrics"}
return Trajectory(
    task_id=required_str(trajectory_data, "task_id", "Trajectory"),
    steps=[_load_step(ensure_json_object(item)) for item in step_values],
    snapshots=[_load_snapshot(ensure_json_object(item)) for item in snapshot_values],
    final_state=final_state,
    metrics=metrics,
    raw=unknown_fields(trajectory_data, known),
)
```

## 13. `dynsteer/adapter/toolsandbox/utils/trajectory.py`

当前：

```python
def trajectory_from_sandbox_rows(
    run_id: str, task_id: str,
    steps: list[dict[str, JsonValue]],
    snapshots: list[dict[str, JsonValue]] | None = None,
) -> Trajectory:
    return Trajectory(run_id=run_id, task_id=task_id, ...)
```

改为：

```python
def trajectory_from_sandbox_rows(
    task_id: str,
    steps: list[dict[str, JsonValue]],
    snapshots: list[dict[str, JsonValue]] | None = None,
) -> Trajectory:
    return Trajectory(
        task_id=task_id,
        steps=[_trajectory_step_from_dict(step) for step in steps],
        snapshots=[_snapshot_from_dict(snapshot) for snapshot in snapshots or []],
    )
```

## 14. `dynsteer/adapter/toolsandbox/harness.py`

### 14.1 `start_case()`

删除：

```python
run_id = raw_output_dir.parent.parent.name
```

`ToolSandboxSession(...)` 删除 `run_id=run_id`：

```python
session = ToolSandboxSession(
    scenario=scenario,
    roles=roles,
    context=context,
    initial_state=None,
    case_id=case_id,
    raw_output_dir=raw_output_dir,
    initial_max_sandbox_message_index=initial_max,
    last_sandbox_message_index=initial_max,
    max_messages=max_messages,
)
```

### 14.2 `advance_case()`

当前：

```python
trajectory = trajectory_from_sandbox_rows(
    run_id=session.run_id,
    task_id=f"toolsandbox::{session.case_id}",
    steps=steps,
    snapshots=snapshot_data,
)
```

改为：

```python
trajectory = trajectory_from_sandbox_rows(
    task_id=f"toolsandbox::{session.case_id}",
    steps=steps,
    snapshots=snapshot_data,
)
```

## 15. `dynsteer/evaluate/evaluator.py`

### 15.1 `evaluate()`

删除：

```python
run_id = harness.build_run_id(config, case_id)
```

路径改为：

```python
raw_output_dir = case_output_dir(config.runs_dir, config, case_id, "dynsteer_evaluate") / "raw"
```

`Trajectory(...)` 改为：

```python
trajectory = Trajectory(
    task_id=task_case.task_id,
    steps=[],
    snapshots=[],
    final_state=harness.final_state_from_session(session),
    metrics=harness.metrics_from_session(session),
)
```

`_build_runtime_result(...)` 调用删除 `run_id=run_id`。

`finally` 中 teardown 调用改为：

```python
self._teardown_session_safely(harness, session, config.benchmark, case_id)
```

### 15.2 `evaluate_replay()`

删除：

```python
run_id = self._replay_run_id(config, trajectory, case_id)
```

路径改为：

```python
raw_output_dir = case_output_dir(config.runs_dir, config, case_id, "dynsteer_replay") / "raw"
```

`replay_trajectory` 改为：

```python
replay_trajectory = Trajectory(
    task_id=trajectory.task_id,
    steps=[],
    snapshots=[],
    final_state=trajectory.final_state,
    metrics=dict(trajectory.metrics),
    raw=dict(trajectory.raw),
)
```

`raw_summary` 删除 `"source_run_id": trajectory.run_id`：

```python
raw_summary={
    "case_id": case_id,
    "method": str(config.metadata.get("method") or "dynsteer_replay"),
}
```

### 15.3 `_build_runtime_result()`

签名删除 `run_id: str`：

```python
def _build_runtime_result(
    self,
    benchmark: str,
    case_id: str,
    task_case: TaskCase,
    trajectory: Trajectory,
    raw_output_dir: Path,
    raw_summary: JsonObject,
    state: RuntimeEvaluationState,
    termination: EvaluationTerminationState,
    runtime_metrics: JsonObject,
    metadata: JsonObject,
) -> HarnessRunResult:
```

`HarnessRunResult(...)` 删除 `run_id=run_id`。

### 15.4 `_runtime_report()`

`TrajectoryEvaluationReport(...)` 删除 `run_id=trajectory.run_id`：

```python
return TrajectoryEvaluationReport(
    task_id=trajectory.task_id,
    milestone_coverage=coverage,
    overall_score=overall_score(...),
    ...
)
```

### 15.5 删除 `_replay_run_id()`

整段删除：

```python
def _replay_run_id(...):
    ...
```

### 15.6 `_teardown_session_safely()`

签名改为：

```python
def _teardown_session_safely(
    self,
    harness: BaseBenchmarkHarness,
    session: object | None,
    benchmark: str,
    case_id: str,
) -> None:
```

日志 extra 删除 `"run_id": run_id`，异常文案改为：

```python
raise HarnessTeardownError(
    f"benchmark session 资源释放失败: benchmark={benchmark}, case_id={case_id}, error={exc}"
) from exc
```

## 16. `dynsteer/harness/outputs.py`

### 16.1 `trajectory_to_json()`

删除：

```python
"run_id": trajectory.run_id,
```

### 16.2 新增缓存探测函数

新增位置：`snapshot_to_json()` 后、`write_case_outputs()` 前。

```python
def existing_case_output(
    config: HarnessRunConfig,
    case_id: str,
    method_fallback: str,
    report_name: str,
) -> HarnessEvaluationOutput | None:
    """根据固定输出文件判断 case 是否已有完整评估产物。"""
    if config is None or not case_id.strip():
        raise ValueError("config 和 case_id 不能为空")
    if not method_fallback.strip() or not report_name.strip():
        raise ValueError("method_fallback 和 report_name 不能为空")

    raw_run_dir = case_output_dir(config.runs_dir, config, case_id, method_fallback)
    result_dir = case_output_dir(config.results_dir, config, case_id, method_fallback)
    output = HarnessEvaluationOutput(
        run_dir=result_dir,
        raw_run_dir=raw_run_dir,
        result_dir=result_dir,
        report_path=result_dir / report_name,
        summary_path=result_dir / "summary.json",
        raw_summary_path=raw_run_dir / "raw_summary.json",
        trajectory_path=raw_run_dir / "trajectory.json",
    )
    required_paths = (
        output.report_path,
        output.summary_path,
        output.raw_summary_path,
        output.trajectory_path,
    )
    return output if all(path.exists() for path in required_paths) else None
```

### 16.3 `write_case_outputs()`

签名增加：

```python
force_eval: bool = False
```

函数开头先查缓存：

```python
case_id = task_case.case_id
if not force_eval:
    cached = existing_case_output(config, case_id, "dynsteer_evaluate", "report.json")
    if cached is not None:
        return cached

harness_result = evaluator.evaluate(...)
```

写路径改为：

```python
return _write_output_payloads(
    raw_run_dir=case_output_dir(config.runs_dir, config, case_id, "dynsteer_evaluate"),
    result_dir=case_output_dir(config.results_dir, config, case_id, "dynsteer_evaluate"),
    ...
)
```

### 16.4 `write_default_case_outputs()`

签名增加：

```python
force_eval: bool = False
```

开头改为：

```python
case_id = task_case.case_id
if not force_eval:
    cached = existing_case_output(config, case_id, "default", "default_report.json")
    if cached is not None:
        return cached

harness.prepare_config(config)
raw_run_dir = case_output_dir(config.runs_dir, config, case_id, "default")
raw_output_dir = raw_run_dir / "raw"
result_dir = case_output_dir(config.results_dir, config, case_id, "default")
```

`Trajectory(...)` 改为：

```python
trajectory = Trajectory(task_id=task_case.task_id, steps=[])
```

`summary` 删除 `"run_id": run_id`。

`report["trajectory"]` 删除 `"run_id": trajectory.run_id`。

### 16.5 `write_replay_case_outputs()`

签名增加：

```python
force_eval: bool = False
```

在 `replay_config = replace(config, metadata=metadata)` 后、`evaluate_replay()` 前增加：

```python
if not force_eval:
    cached = existing_case_output(replay_config, task_case.case_id, "dynsteer_replay", "report.json")
    if cached is not None:
        return cached
```

路径改为：

```python
raw_run_dir = case_output_dir(replay_config.runs_dir, replay_config, task_case.case_id, "dynsteer_replay")
result_dir = case_output_dir(replay_config.results_dir, replay_config, task_case.case_id, "dynsteer_replay")
```

### 16.6 方法级汇总

`write_run_level_summaries()` 改名为：

```python
def write_method_level_summaries(outputs: list[HarnessEvaluationOutput]) -> None:
```

分组逻辑仍用 `output.result_dir.parent`，但变量改名为 `method_dir`：

```python
outputs_by_method_dir: dict[Path, list[HarnessEvaluationOutput]] = {}
for output in outputs:
    outputs_by_method_dir.setdefault(output.result_dir.parent, []).append(output)
for method_dir, method_outputs in outputs_by_method_dir.items():
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "summary.json").write_text(
        json.dumps(_build_method_level_summary(method_dir, method_outputs), ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
```

`_build_run_level_summary()` 改名为 `_build_method_level_summary()`，返回值删除 `run_id`：

```python
return {
    "benchmark": method_dir.parent.name,
    "method": method_dir.name,
    "case_count": case_count,
    "average_overall_score": score_sum / case_count if case_count else 0.0,
    "milestone_coverage_counts": coverage_counts,
    "total_step_count": total_step_count,
    "total_llm_tokens": total_llm_tokens,
    "total_trajectory_tokens": total_trajectory_tokens,
    "average_elapsed_seconds": sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0,
    "cases": cases,
}
```

## 17. `dynsteer/harness/runner.py`

### 17.1 import

当前：

```python
from dynsteer.harness.outputs import write_run_level_summaries
```

改为：

```python
from dynsteer.harness.outputs import write_method_level_summaries
```

### 17.2 `run_harness_configs()`

签名改为：

```python
def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
```

参数校验后增加：

```python
effective_force_eval = bool(force_eval or force_adapt)
```

调用 `_run_config()` 改为：

```python
outputs.extend(_run_config(
    config,
    max_workers=_effective_max_workers(max_workers, config),
    logger=logger,
    force_adapt=force_adapt,
    force_eval=effective_force_eval,
))
```

### 17.3 `_run_config()`

签名增加：

```python
force_eval: bool = False
```

调度调用改为：

```python
outputs = run_case_tasks(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)
write_method_level_summaries(outputs)
```

### 17.4 `_config_label()`

按第 9.3 节删除 run_id 兜底。

## 18. `dynsteer/harness/scheduler.py`

### 18.1 异常类

当前构造包含 `run_id`。改为：

```python
class HarnessCaseExecutionError(RuntimeError):
    """单个 benchmark case 执行失败时抛出。"""

    def __init__(self, benchmark: str, case_id: str, cause: Exception) -> None:
        self.benchmark = benchmark
        self.case_id = case_id
        self.cause = cause
        super().__init__(f"benchmark case 执行失败: benchmark={benchmark}, case_id={case_id}, error={cause}")
```

### 18.2 `run_case_tasks()`

签名增加：

```python
force_eval: bool = False
```

串行与并行分支都传入 `force_eval`。

### 18.3 `_run_case()`

签名增加：

```python
force_eval: bool = False
```

调用改为：

```python
return write_case_outputs(
    task.config,
    harness,
    evaluator,
    task.task_case,
    progress_reporter,
    force_eval=force_eval,
)
```

异常包装改为：

```python
raise HarnessCaseExecutionError(task.config.benchmark, task.case_id, exc) from exc
```

删除 `_safe_run_id()` 整个函数。

### 18.4 并行 submit

当前：

```python
futures[executor.submit(_run_case, task, QueueProgressReporter(events))] = task
```

改为：

```python
futures[executor.submit(_run_case, task, QueueProgressReporter(events), force_eval)] = task
```

## 19. `dynsteer/experiment/runner.py`

### 19.1 `run_experiment()`

签名改为：

```python
def run_experiment(
    config_path: Path | str,
    workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
    no_sum: bool = False,
) -> list[ExperimentCaseResult]:
```

读取配置后增加：

```python
effective_force_eval = bool(force_eval or force_adapt)
```

case 运行处传递 `effective_force_eval`：

```python
if spec.method == ExperimentMethod.DEFAULT:
    _get_default_case_outputs(spec, task_case, results, default_outputs, force_eval=effective_force_eval)
elif spec.method in {ExperimentMethod.DYNSTEER_REPLAY}:
    default_output, default_reference = _get_default_case_outputs(
        spec,
        task_case,
        results,
        default_outputs,
        force_eval=effective_force_eval,
    )
    output = run_replay_case(spec, task_case, default_output, default_reference, force_eval=effective_force_eval)
    results.append(_case_result_from_output(spec, task_case, output, default_reference))
elif spec.method == ExperimentMethod.DYNSTEER_EVALUATE:
    output = run_evaluate_case(spec, task_case, force_eval=effective_force_eval)
    results.append(_case_result_from_output(spec, task_case, output))
```

汇总写出改为：

```python
if results and not no_sum:
    output_dir = specs[0].results_dir
    write_experiment_index(results, output_dir)
    write_metric_tables(results, output_dir)
```

### 19.2 `_get_default_case_outputs()`

签名增加：

```python
force_eval: bool = False
```

调用改为：

```python
output, reference = run_default_case(run_spec, task_case, force_eval=force_eval)
```

### 19.3 `run_default_case()`

签名改为：

```python
def run_default_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    force_eval: bool = False,
) -> tuple[HarnessEvaluationOutput, JsonObject]:
```

调用：

```python
output = write_default_case_outputs(
    config=config,
    harness=harness,
    task_case=task_case,
    force_eval=force_eval,
)
```

### 19.4 `run_replay_case()`

签名增加：

```python
force_eval: bool = False
```

调用：

```python
return write_replay_case_outputs(
    config=config,
    evaluator=evaluator,
    task_case=task_case,
    trajectory=trajectory,
    harness=harness,
    default_reference=default_reference,
    force_eval=force_eval,
)
```

### 19.5 `run_evaluate_case()`

签名增加：

```python
force_eval: bool = False
```

调用：

```python
return write_case_outputs(
    config=config,
    harness=harness,
    evaluator=evaluator,
    task_case=task_case,
    force_eval=force_eval,
)
```

### 19.6 `write_experiment_index()` 与 `_build_experiment_index_payload()`

删除 repeat 节点中的 `run_id`。

当前：

```python
repeat_node = repeats.setdefault(repeat_key, {"run_id": result.run_id, "cases": {}})
if repeat_node.get("run_id") != result.run_id:
    raise ValueError("同一 repeat 下的 run_id 必须一致")
```

改为：

```python
repeat_node = repeats.setdefault(repeat_key, {"cases": {}})
```

排序输出中删除：

```python
"run_id": repeat_node.get("run_id") ...
```

目标 repeat 输出：

```python
ordered_repeats[repeat_index] = {
    "cases": dict(cases.items()) if isinstance(cases, dict) else {}
}
```

### 19.7 `_case_result_from_output()`

构造 `ExperimentCaseResult` 时删除 `run_id=spec.run_id`：

```python
return ExperimentCaseResult(
    experiment_id=spec.experiment_id,
    benchmark=spec.benchmark,
    case_id=task_case.case_id,
    model_id=spec.model_id,
    repeat_index=spec.repeat_index,
    method=spec.method,
    ...
)
```

## 20. 脚本修改

### 20.1 `scripts/start_experiment.sh`

usage 增加：

```bash
  --force_eval               Re-run evaluation even if case runs/results already exist.
  --no_sum                   Skip experiment-level index/scores/metrics files.
```

变量区在 `local force_adapt="0"` 后增加：

```bash
local force_eval="0"
local no_sum="0"
```

参数解析增加：

```bash
--force_eval|--force-eval)
    force_eval="1"
    shift
    ;;
--no_sum|--no-sum)
    no_sum="1"
    shift
    ;;
```

参数构造增加：

```bash
local force_eval_args=()
if [[ "$force_eval" == "1" ]]; then
    force_eval_args=(--force_eval)
fi
local no_sum_args=()
if [[ "$no_sum" == "1" ]]; then
    no_sum_args=(--no_sum)
fi
```

末尾当前有续行 bug：

```bash
exec python main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}"
    "${force_adapt_args[@]}"
```

改为：

```bash
exec python main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}" \
    "${force_adapt_args[@]}" \
    "${force_eval_args[@]}" \
    "${no_sum_args[@]}"
```

### 20.2 `scripts/start_experiment_no_docker.sh`

按 20.1 增加 usage、变量、参数解析和参数构造。

末尾改为：

```bash
exec "$python_executable" main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}" \
    "${force_adapt_args[@]}" \
    "${force_eval_args[@]}" \
    "${no_sum_args[@]}"
```

### 20.3 `scripts/start.sh`

usage 增加：

```bash
  --force_eval         Re-run evaluation even if case runs/results already exist.
```

变量区增加：

```bash
local force_eval="0"
```

参数解析增加：

```bash
--force_eval|--force-eval)
    force_eval="1"
    shift
    ;;
```

参数构造增加：

```bash
local force_eval_args=()
if [[ "$force_eval" == "1" ]]; then
    force_eval_args=(--force_eval)
fi
```

末尾追加：

```bash
"${force_eval_args[@]}" \
```

### 20.4 `scripts/start_no_docker.sh`

同 20.3 增加单 benchmark `--force_eval` 并传给 `main.py`。

### 20.5 `scripts/exp_main.sh`

该脚本会把参数转发给 `start.sh`。如果希望批量单 benchmark 入口也支持该开关，在参数解析处增加：

```bash
--force_eval|--force-eval)
    start_args+=(--force_eval)
    shift
    ;;
```

## 21. `display/build.py`

如果展示面板仍需支持新输出结构，必须同步删除 run 层。

### 21.1 类型别名

当前：

```python
CaseKey = tuple[str, str, str, str]
```

改为：

```python
CaseKey = tuple[str, str, str]
```

### 21.2 `_collect_case_keys()`

当前按 `benchmark/method/run_id/scenario` 扫描。改为按 `benchmark/method/scenario`：

```python
def _collect_case_keys(base_dir: Path) -> set[CaseKey]:
    if not base_dir.exists():
        return set()
    keys: set[CaseKey] = set()
    for benchmark_dir in base_dir.iterdir():
        if not benchmark_dir.is_dir():
            continue
        for method_dir in benchmark_dir.iterdir():
            if not method_dir.is_dir():
                continue
            keys.update(
                (benchmark_dir.name, method_dir.name, scenario_dir.name)
                for scenario_dir in method_dir.iterdir()
                if scenario_dir.is_dir()
            )
    return keys
```

### 21.3 `_discover_runs()`

保留前端概念中的 `runs` 列表，但每个 run 只代表一个 `benchmark/method` 组合：

```python
def _discover_runs(runs_dir: Path, results_dir: Path, data_dir: Path) -> list[JsonObject]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for benchmark, method, scenario_id in sorted(_collect_case_keys(runs_dir) | _collect_case_keys(results_dir)):
        grouped.setdefault((benchmark, method), []).append(scenario_id)
    return [
        {
            "benchmark": benchmark,
            "method": method,
            "summary": _run_summary(scenarios),
            "scenarios": scenarios,
        }
        for (benchmark, method), scenario_ids in grouped.items()
        for scenarios in [
            [_scenario(runs_dir, results_dir, data_dir, benchmark, method, item) for item in scenario_ids]
        ]
    ]
```

### 21.4 `_scenario()`

签名删除 `run_id`，路径改为：

```python
def _scenario(
    runs_dir: Path, results_dir: Path, data_dir: Path, benchmark: str, method: str, scenario_id: str
) -> JsonObject:
    run_case_dir = runs_dir / benchmark / method / scenario_id
    result_case_dir = results_dir / benchmark / method / scenario_id
```

### 21.5 `_summary_payload()`

从 keys 中删除：

```python
"run_id",
```

### 21.6 `display/index.html`

当前显示：

```javascript
const parts = [run.run_id || "未命名 run", run.method || ""].filter(Boolean);
```

改为：

```javascript
const parts = [run.benchmark || "benchmark", run.method || ""].filter(Boolean);
```

## 22. 文档修改

### 22.1 `README.md`

把旧路径：

```text
runs/experiments/double_benchmark_initial/<benchmark>/<method>/<run_id>/<case_id>
results/experiments/double_benchmark_initial/<benchmark>/<method>/<run_id>/<case_id>
```

改为：

```text
runs/experiments/double_benchmark_initial/<benchmark>/<method>/<case_id>
results/experiments/double_benchmark_initial/<benchmark>/<method>/<case_id>
```

`--force_adapt` 描述改为会同时强制重跑评估。

### 22.2 `docs/apis/experiment.md`

需要同步：

- `run_experiment(config_path, workers=1, force_adapt=False)` 改为 `run_experiment(config_path, workers=1, force_adapt=False, force_eval=False, no_sum=False)`。
- 删除所有 `run_id` schema 和样例。
- case 路径改为 `results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/`。
- 说明 `--no_sum` 跳过 `index.json`、`scores.json`、`metrics.json`。
- 说明 `force_adapt=True` 自动等价于 `force_eval=True`。

### 22.3 `docs/apis/harness.md`

需要同步：

- 删除 `BaseBenchmarkHarness.build_run_id()`。
- `run_harness_configs(...)` 签名增加 `force_eval=False`。
- 异常上下文只包含 `benchmark` 和 `case_id`。
- 单 benchmark 路径改为 `runs/<benchmark>/<method>/<case_id>/...` 与 `results/<benchmark>/<method>/<case_id>/...`。
- 方法级汇总文件改为 `results/<benchmark>/<method>/summary.json`，字段不含 `run_id`。

### 22.4 `docs/apis/display.md`

需要同步：

- 扫描路径由 `<benchmark>/<method>/<run_id>/<case_id>` 改为 `<benchmark>/<method>/<case_id>`。
- 顶部选择器文案不再显示 `run_id`。

## 23. 测试方案

当前仓库没有 `tests/` 目录，但 `pyproject.toml` 已配置 pytest。落地时新增以下测试。

### 23.1 `tests/harness/test_paths_and_cache.py`

覆盖：

1. `case_output_dir(Path("runs"), config, "case_a", "default")` 返回 `runs/<benchmark>/default/case_a`。
2. `existing_case_output()` 在四个必需文件都存在时返回 `HarnessEvaluationOutput`。
3. 删除任一必需文件后返回 `None`。
4. default 使用 `default_report.json`，evaluate/replay 使用 `report.json`。

### 23.2 `tests/experiment/test_runner_flags.py`

覆盖：

1. `run_experiment(..., force_adapt=True, force_eval=False)` 传给 case 写出函数的有效值必须是 `force_eval=True`。
2. `run_experiment(..., no_sum=True)` 不调用 `write_experiment_index()` 和 `write_metric_tables()`。
3. `no_sum=False` 时仍写出实验汇总。

### 23.3 `tests/experiment/test_index_without_run_id.py`

构造最小 `ExperimentCaseResult` 列表，断言：

1. `_build_experiment_index_payload()` 的 repeat 节点没有 `run_id`。
2. case 叶子没有 `run_id`。

### 23.4 `tests/adapter/test_trajectory_schema_without_run_id.py`

覆盖：

1. `trajectory_to_json(Trajectory(...))` 不输出 `run_id`。
2. `load_trajectory()` 不要求输入含 `run_id`。
3. ToolSandbox `trajectory_from_sandbox_rows()` 不需要 `run_id` 参数。

### 23.5 `tests/harness/test_scheduler_force_eval.py`

覆盖：

1. `run_case_tasks(..., force_eval=True)` 能把 `force_eval=True` 传到 `write_case_outputs()`。
2. `HarnessCaseExecutionError` 文案不含 `run_id`。

## 24. 验收命令

落地后执行：

```bash
uv run pytest
```

执行静态搜索，代码目录中不应再有任何 `run_id`、`build_run_id`、`_replay_run_id`：

```bash
rg -n "run_id|build_run_id|_replay_run_id" dynsteer display main.py scripts README.md docs/apis
```

允许 `docs/plans` 中保留历史方案和核查报告里的 `run_id` 讨论；不允许运行时代码和 API 文档继续依赖它。

## 25. 分片实验推荐流程

并行分片运行：

```bash
./scripts/start_experiment_no_docker.sh --exp exp_config_part_1.json --no_sum
./scripts/start_experiment_no_docker.sh --exp exp_config_part_2.json --no_sum
```

最终汇总：

```bash
./scripts/start_experiment_no_docker.sh --exp exp_config_all_cases.json
```

最终汇总配置需要满足：

1. `experiment_id` 与分片一致。
2. `benchmark` 与分片一致。
3. `method` 与分片一致。
4. `case_ids` 覆盖所有分片 case。
5. 不传 `--force_eval`，不传 `--force_adapt`，否则会重跑而不是复用缓存。

在上述条件下，最终汇总会通过 case 文件存在判断快速命中缓存，读取已有 `summary.json`、`raw_summary.json`、`trajectory.json` 和 report 文件生成 `index.json`、`scores.json`、`metrics.json`。

