# DynSTEER 评估进度缓存与 ToolSandbox 多配置并行核查报告

## 1. 核查结论

1. 当前 DynSTEER 已经有 adapted case 层面的缓存：`load_task_case(..., force_adapt=False)` 会优先读取 `data/<benchmark>/adapted_cases/<case_id>.json`。但评估运行层没有进度暂存机制，`default`、`dynsteer_evaluate`、`dynsteer_replay` 每次进入流程都会重新执行或重新评估已有 case。
2. 最简单可靠的评估进度缓存方案是：以单 case 已有文件为准，命中时不进入 harness/evaluator，只构造 `HarnessEvaluationOutput`，让后续 `ExperimentCaseResult`、`index.json`、`scores.json`、`metrics.json` 继续从既有 JSON 文件读取。
3. ToolSandbox 的全局运行状态是 Python 进程内模块全局变量，不是跨进程共享状态。两个窗口分别启动两个 `scripts/start_experiment_XXX`，通常是两个 Python 解释器或两个 Docker 容器，不会因为 ToolSandbox 的 `_global_execution_context` 产生内存状态混淆。
4. 本报告默认建立在一个前提上：你自己会保证不同实验配置之间的 `case_id` 完全分离、不重叠。基于这个前提，本方案不再为 case 级文件覆盖额外设计隔离机制。
5. `metrics.json`、`index.json`、`scores.json` 是实验级汇总文件；同一个 `experiment_id` 的多个分片配置如果都在末尾写汇总，确实可能发生写覆盖。新增 `--no_sum` 后，分片配置只产出 case 级结果，把最后汇总留给最终整体验证配置。
6. 最新修订：删除所有 `run_id` 参与身份识别、路径构造、索引输出和轨迹序列化的逻辑。统一实验入口的 case 文件路径只由 `experiment_id / benchmark / method / case_id` 决定；单 benchmark 入口没有 `experiment_id`，路径只由 `benchmark / method / case_id` 决定。重复运行同一路径时直接覆盖，不再引入替代隔离键。

## 2. 核查依据

本次只基于当前本地代码与 `../ToolSandbox` 源码核查，没有重新运行实验。

关键依据：

- `main.py:79` 调用 `run_experiment(..., force_adapt=...)`，当前没有 `force_eval` 和 `no_sum`。
- `dynsteer/experiment/runner.py:20` 的 `run_experiment()` 串行展开 spec/case；`dynsteer/experiment/runner.py:43-54` 每个 case 都进入对应运行函数。
- `dynsteer/harness/outputs.py:69`、`101`、`212` 分别写出 `dynsteer_evaluate`、`default`、`dynsteer_replay` 产物，当前写入前没有检查既有产物。
- `dynsteer/harness/paths.py:4` 当前输出路径为 `<base>/<benchmark>/<method>/<run_id>/<case_id>`；本方案需要改为 `<base>/<benchmark>/<method>/<case_id>`。
- `dynsteer/experiment/config.py:207-211` 当前存在 `_next_run_id()`，但它只产生单进程内顺序号，不携带稳定实验语义；本方案删除该函数和所有 `run_id` 字段传递。
- `../ToolSandbox/tool_sandbox/common/execution_context.py:3` 明确标注当前全局 execution context 非线程安全；`777`、`797`、`806` 通过模块全局 `_global_execution_context` 读写当前 context。
- `dynsteer/adapter/toolsandbox/harness.py:49-50` 每个 case 会 `deepcopy` scenario 起始 context 后设置为当前 context；`196-209` 的 named scenarios 缓存也是进程内缓存。
- `data/toolsandbox/benchmark.json:6` 当前配置 `max_workers=1`，`dynsteer/harness/runner.py:56-67` 会把单 benchmark 并发 worker 限制到该上限。

## 3. 问题一：评估进度缓存方案

### 3.1 当前缺口

统一实验流程中，`run_experiment()` 会先准备 TaskCase 模板，然后对每个 spec 和 case 调用：

- `run_default_case()`：完整执行 benchmark default 轨迹。
- `run_replay_case()`：读取 default 轨迹后执行 DynSTEER replay 评估。
- `run_evaluate_case()`：在线执行 DynSTEER 动态评估。

这些函数都会继续调用 `write_default_case_outputs()`、`write_replay_case_outputs()`、`write_case_outputs()`，最终覆盖写入既有 `runs/results` 文件。当前只有 adapted case 缓存，没有评估结果缓存。

### 3.2 缓存命中标准

建议不要新增数据库、索引文件、lock 文件或复杂 manifest，只按当前固定输出文件是否存在判断。

单 case 缓存命中条件：

| 方法 | 必需 runs 文件 | 必需 results 文件 |
| --- | --- | --- |
| `default` | `raw_summary.json`、`trajectory.json` | `summary.json`、`default_report.json` |
| `dynsteer_evaluate` | `raw_summary.json`、`trajectory.json` | `summary.json`、`report.json` |
| `dynsteer_replay` / `dynsteer_replay_static` | `raw_summary.json`、`trajectory.json` | `summary.json`、`report.json` |

只要任一必需文件缺失，就视为缓存未命中，重新执行该 case。命中时不主动解析全部文件；后续 `_case_result_from_output()` 和 replay 的 default 依赖会自然读取需要的 JSON。如果文件损坏，读取阶段会抛出清晰错误，不额外隐藏问题。

### 3.3 代码修改位置与方案

#### 3.3.1 `main.py`

修改位置：`main.py:17-25` 的参数解析。

新增参数：

```python
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
    help="仅写 case 级结果，不写最终汇总文件",
)
```

修改位置：`main.py:79`。

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

修改位置：`main.py:98`。

如果希望单 benchmark `start.sh` 入口也支持同一机制，改为：

```python
outputs: list[HarnessEvaluationOutput] = run_harness_configs(
    configs=configs,
    max_workers=int(args.workers),
    force_adapt=bool(args.force_adapt),
    force_eval=bool(args.force_eval),
)
```

#### 3.3.2 `dynsteer/harness/outputs.py`

修改位置：建议放在 `snapshot_to_json()` 之后、三个 `write_*_case_outputs()` 之前。

新增统一缓存探测函数：

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

修改位置：`dynsteer/harness/outputs.py:69`。

函数签名改为：

```python
def write_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
```

函数开头增加：

```python
    case_id = task_case.case_id
    if not force_eval:
        cached = existing_case_output(config, case_id, "dynsteer_evaluate", "report.json")
        if cached is not None:
            return cached
```

随后保留现有：

```python
    harness_result = evaluator.evaluate(...)
```

注意：原函数中已经有 `case_id = task_case.case_id`，新增后应删除重复赋值。

修改位置：`dynsteer/harness/outputs.py:101`。

函数签名增加 `force_eval: bool = False`：

```python
def write_default_case_outputs(
    config: HarnessRunConfig,
    harness: BaseBenchmarkHarness,
    task_case: TaskCase,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
```

把当前开头的 `case_id` 与 `harness.prepare_config(config)` 调整为：

```python
    case_id = task_case.case_id
    if not force_eval:
        cached = existing_case_output(config, case_id, "default", "default_report.json")
        if cached is not None:
            return cached

    harness.prepare_config(config)
```

后续 `raw_run_dir`、`result_dir` 改为通过 `case_output_dir(config.runs_dir, config, case_id, "default")` 和 `case_output_dir(config.results_dir, config, case_id, "default")` 构造。

修改位置：`dynsteer/harness/outputs.py:212`。

函数签名增加 `force_eval: bool = False`：

```python
def write_replay_case_outputs(
    config: HarnessRunConfig,
    evaluator: DynSTEEREvaluator,
    task_case: TaskCase,
    trajectory: Trajectory,
    harness: BaseBenchmarkHarness,
    default_reference: JsonObject | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
```

在 `replay_config = replace(config, metadata=metadata)` 之后、`evaluator.evaluate_replay(...)` 之前增加：

```python
    if not force_eval:
        cached = existing_case_output(replay_config, task_case.case_id, "dynsteer_replay", "report.json")
        if cached is not None:
            return cached
```

这里不再构造 replay 专用 `run_id`。replay 的目录名由 `config.metadata["method"]` 或 fallback `dynsteer_replay` 决定，case 目录仍然是 `case_id`。

#### 3.3.3 `dynsteer/experiment/runner.py`

修改位置：`dynsteer/experiment/runner.py:20`。

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

函数开头在加载配置后、展开 specs 前增加有效评估重跑开关：

```python
    effective_force_eval = bool(force_eval or force_adapt)
```

修改位置：`dynsteer/experiment/runner.py:43-54`。

所有运行函数调用增加 `force_eval=effective_force_eval`：

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

`no_sum=True` 时，`run_experiment()` 仍然正常执行 case 级流程并返回 `results`，但不写 `index.json`、`scores.json`、`metrics.json` 这类实验级汇总文件。这样分片配置可以只负责产出 case 级结果，最终整体验证配置再统一做一次汇总。

修改位置：`dynsteer/experiment/runner.py:61`。

签名增加 `force_eval`：

```python
def _get_default_case_outputs(
    spec: ExperimentRunSpec,
    task_case: TaskCase,
    results: list[ExperimentCaseResult],
    outputs: dict[tuple[str, str, int, str], tuple[HarnessEvaluationOutput, JsonObject]],
    force_eval: bool = False,
) -> tuple[HarnessEvaluationOutput, JsonObject]:
```

函数内调用改为：

```python
output, reference = run_default_case(run_spec, task_case, force_eval=force_eval)
```

修改位置：`dynsteer/experiment/runner.py:79`。

签名与写入调用改为：

```python
def run_default_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    force_eval: bool = False,
) -> tuple[HarnessEvaluationOutput, JsonObject]:
    """完整执行 benchmark，不进行 DynSTEER 介入。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    output = write_default_case_outputs(
        config=config,
        harness=harness,
        task_case=task_case,
        force_eval=force_eval,
    )
```

后续读取 `output.raw_summary_path` 的逻辑保持不变。命中缓存时也会从已有 `raw_summary.json` 读取 `default_result`。

修改位置：`dynsteer/experiment/runner.py:89`。

签名与写入调用改为：

```python
def run_replay_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    default_output: HarnessEvaluationOutput,
    default_reference: JsonObject,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
```

最后返回处改为：

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

修改位置：`dynsteer/experiment/runner.py:102`。

签名与写入调用改为：

```python
def run_evaluate_case(
    spec: ExperimentRunSpec,
    task_case_template: TaskCase,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
    """调用 DynSTEEREvaluator.evaluate() 主入口执行在线动态评估。"""
    task_case, harness, config = _prepare_for_run_case(spec, task_case_template)
    evaluator = DynSTEEREvaluator.from_config(config, strategy=spec.strategy)
    return write_case_outputs(
        config=config,
        harness=harness,
        evaluator=evaluator,
        task_case=task_case,
        force_eval=force_eval,
    )
```

#### 3.3.4 `dynsteer/harness/runner.py` 与 `dynsteer/harness/scheduler.py`

如果要让 `python main.py --benchmark ...` 和 `scripts/start.sh` 也支持 `--force_eval`，需要把开关传到单 benchmark 调度层。

`dynsteer/harness/runner.py:13`：

```python
def run_harness_configs(
    configs: list[HarnessRunConfig],
    max_workers: int = 1,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
```

`run_harness_configs()` 开头参数校验后增加：

```python
    effective_force_eval = bool(force_eval or force_adapt)
```

`dynsteer/harness/runner.py:24` 的 `_run_config(...)` 调用增加 `force_eval=effective_force_eval`。

`dynsteer/harness/runner.py:37`：

```python
def _run_config(
    config: HarnessRunConfig,
    *,
    max_workers: int,
    logger: logging.Logger,
    force_adapt: bool = False,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
```

`dynsteer/harness/runner.py:52`：

```python
outputs = run_case_tasks(tasks, max_workers=max_workers, logger=logger, force_eval=force_eval)
```

`dynsteer/harness/scheduler.py:22`：

```python
def run_case_tasks(
    tasks: list[HarnessCaseTask],
    *,
    max_workers: int,
    logger: logging.Logger,
    force_eval: bool = False,
) -> list[HarnessEvaluationOutput]:
```

串行与并行分支继续传递 `force_eval`。`_run_case()` 签名改为：

```python
def _run_case(
    task: HarnessCaseTask,
    progress_reporter: CaseProgressReporter | None = None,
    force_eval: bool = False,
) -> HarnessEvaluationOutput:
```

调用 `write_case_outputs(...)` 时增加：

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

并行提交处把：

```python
executor.submit(_run_case, task, QueueProgressReporter(events))
```

改为：

```python
executor.submit(_run_case, task, QueueProgressReporter(events), force_eval)
```

#### 3.3.5 `scripts/start_experiment.sh`

修改 usage，在 `--force_adapt` 后增加：

```bash
  --force_eval               Re-run evaluation even if case runs/results already exist.
  --no_sum                   Skip experiment-level summary files, keep only case-level outputs.
```

修改变量区，在 `local force_adapt="0"` 后增加：

```bash
local force_eval="0"
local no_sum="0"
```

修改参数解析，增加：

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

修改 `exec` 前参数构造：

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

同时修正当前 `scripts/start_experiment.sh:414-417` 的续行问题。当前写法：

```bash
exec python main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}"
    "${force_adapt_args[@]}"
```

应改为：

```bash
exec python main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}" \
    "${force_adapt_args[@]}" \
    "${force_eval_args[@]}" \
    "${no_sum_args[@]}"
```

否则 Docker 入口下 `--force_adapt` 已经不会传给 `main.py`，新增 `--force_eval` 也会同样失效。

#### 3.3.6 `scripts/start_experiment_no_docker.sh`

同样增加 usage、`force_eval` / `no_sum` 变量、参数解析、`force_eval_args` / `no_sum_args` 构造。

末尾从：

```bash
exec "$python_executable" main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}" \
    "${force_adapt_args[@]}"
```

改为：

```bash
exec "$python_executable" main.py \
    --exp "$experiment_config" \
    "${worker_args[@]}" \
    "${force_adapt_args[@]}" \
    "${force_eval_args[@]}" \
    "${no_sum_args[@]}"
```

#### 3.3.7 `scripts/start.sh`

若第 3.3.4 节同步支持单 benchmark 入口，则 `scripts/start.sh` 也按相同方式增加 `--force_eval`，并在末尾 `exec python main.py ...` 中追加：

```bash
"${force_eval_args[@]}" \
```

#### 3.3.8 API 文档

代码落地时需要同步更新：

- `docs/apis/experiment.md`：说明 `--force_eval` 控制已有 `runs/results` 是否复用，`--no_sum` 控制是否写实验级汇总；`--force_adapt` 控制 adapted case 重建，并且会自动强制启用评估重跑。
- `docs/apis/harness.md`：说明 `existing_case_output()` 的四文件命中标准，以及单 benchmark 入口的行为。

### 3.4 `--force_adapt` 与 `--force_eval` 的关系

`--force_adapt` 必须强制启用 `--force_eval` 的语义。原因是 adapted case 是评估输入的基础数据；一旦强制重建 adapted case，已有 `runs/results` 就是基于旧输入生成的产物，不能继续复用。

最终规则：

- `--force_eval`：忽略已有 `runs/results`，重新执行评估并覆盖单 case 产物。
- `--force_adapt`：重新生成 `data/<benchmark>/adapted_cases`，并自动视为 `force_eval=True`。
- 同时传 `--force_adapt --force_eval` 允许，但语义上等价于 `--force_adapt`。

实现上不要只在 shell 脚本里追加 `--force_eval`，而应在 Python 入口统一计算 `effective_force_eval = bool(force_eval or force_adapt)`。这样通过 `main.py`、`scripts/start_experiment*.sh`、`scripts/start.sh` 或直接调用 `run_experiment()` / `run_harness_configs()` 时，行为都一致。

### 3.5 `--no_sum` 的分片汇总语义

`--no_sum` 只跳过 `run_experiment()` 末尾的实验级汇总写出，不影响 case 执行、不影响进度缓存，也不影响 case 级结果写入。

推荐的用法是：

1. 先把多个分片配置用 `--no_sum` 跑完，只落 case 级产物。
2. 再跑一个包含全部 `case_ids` 的最终整体验证配置，不加 `--no_sum`，让它快速汇总已有结果。

删除 `run_id` 后，最终整体验证配置不再依赖 spec 展开顺序来对齐 `run_0`、`run_1`。它只需要使用相同的 `experiment_id`、`benchmark`、`method` 和完整 `case_ids`，即可按固定 case 文件路径命中缓存并完成汇总。`model_id` 与 `repeat_index` 继续进入 `summary.json` metadata、`index.json` 分组和指标统计，但不参与 case 文件路径隔离；如果同一路径下跑多个 model 或 repeat，后一次运行会覆盖前一次运行，这是本方案接受的行为。

### 3.6 删除 `run_id` 后的新文件路径结构

本节取代本文中所有旧的 `<run_id>` 目录结构说明。后续代码落地时，所有 case 级路径都必须收敛到 `dynsteer/harness/paths.py::case_output_dir()`，并按下面的结构生成。

#### 3.6.1 统一实验入口

统一实验入口指 `python main.py --exp <experiment_config>` 以及 `scripts/start_experiment*.sh`。默认根目录仍由实验配置决定：

- `runs_dir` 默认值：`runs/experiments/<experiment_id>`
- `results_dir` 默认值：`results/experiments/<experiment_id>`

case 级中间产物：

```text
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/raw/
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/raw_summary.json
runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/trajectory.json
```

case 级评估结果：

```text
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/summary.json
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/report.json
results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/default_report.json
```

其中 `default` 方法写 `default_report.json`，`dynsteer_evaluate`、`dynsteer_replay`、`dynsteer_replay_static` 写 `report.json`。`method` 目录优先使用 `config.metadata["method"]`，没有 metadata 时使用调用方传入的 fallback。

实验级汇总结果：

```text
results/experiments/<experiment_id>/index.json
results/experiments/<experiment_id>/scores.json
results/experiments/<experiment_id>/metrics.json
```

启用 `--no_sum` 时，只跳过上面三个实验级汇总文件，不跳过任何 case 级 `runs` 或 `results` 文件。

#### 3.6.2 单 benchmark 入口

单 benchmark 入口指 `python main.py --benchmark <benchmark>` 以及 `scripts/start.sh`。该入口没有 `experiment_id` 层，默认根目录保持：

- `runs_dir` 默认值：`runs`
- `results_dir` 默认值：`results`

case 级中间产物：

```text
runs/<benchmark>/<method>/<case_id>/raw/
runs/<benchmark>/<method>/<case_id>/raw_summary.json
runs/<benchmark>/<method>/<case_id>/trajectory.json
```

case 级评估结果：

```text
results/<benchmark>/<method>/<case_id>/summary.json
results/<benchmark>/<method>/<case_id>/report.json
results/<benchmark>/<method>/<case_id>/default_report.json
```

单 benchmark 的方法级汇总结果：

```text
results/<benchmark>/<method>/summary.json
```

该文件由当前的 `write_run_level_summaries()` 语义演化而来。落地时建议将函数重命名为 `write_method_level_summaries()`，汇总字段删除 `run_id`，保留 `benchmark`、`method`、`case_count`、`average_overall_score`、`milestone_coverage_counts`、`total_step_count`、`total_llm_tokens`、`total_trajectory_tokens`、`average_elapsed_seconds` 和 `cases`。

#### 3.6.3 路径构造函数

`dynsteer/harness/paths.py` 修改为：

```python
def case_output_dir(
    base_dir: Path,
    config: HarnessRunConfig,
    case_id: str,
    method_fallback: str,
) -> Path:
    """构造单个 benchmark case 的输出目录。"""
    if base_dir is None or config is None or not case_id.strip():
        raise ValueError("base_dir、config 和 case_id 不能为空")
    method = output_method(config, method_fallback)
    return base_dir / config.benchmark / method / case_id
```

所有调用点同步删除 `run_id` 入参：

- `case_output_dir(config.runs_dir, config, case_id, "default")`
- `case_output_dir(config.results_dir, config, case_id, "default")`
- `case_output_dir(config.runs_dir, config, case_id, "dynsteer_evaluate")`
- `case_output_dir(config.results_dir, config, case_id, "dynsteer_evaluate")`
- `case_output_dir(config.runs_dir, config, case_id, "dynsteer_replay")`
- `case_output_dir(config.results_dir, config, case_id, "dynsteer_replay")`

#### 3.6.4 `run_id` 删除范围

后续代码落地时直接删除 `run_id`，不替换成 `model_id`、`repeat_index` 或其他隔离键。

- `dynsteer/experiment/config.py`：删除 `run_sequences`、`_next_run_id()`、`ExperimentRunSpec(run_id=...)` 赋值；`validate_experiment_matrix()` 不再做基于输出路径唯一性的 `run_id` 校验。
- `dynsteer/experiment/model.py`：从 `ExperimentRunSpec`、`ExperimentCaseResult`、`to_metadata()`、`to_index_dict()` 排除字段中删除 `run_id`。
- `dynsteer/experiment/runner.py`：`_build_experiment_index_payload()` 的 repeat 节点不再写 `run_id`，不再校验同一 repeat 下 `run_id` 一致；`_case_result_from_output()` 构造 `ExperimentCaseResult` 时不再传 `run_id`。
- `dynsteer/adapter/base.py`：删除 `BaseBenchmarkHarness.build_run_id()`。
- `dynsteer/harness/config.py`：删除 run config 中 `run_id` / `name` 到 `metadata["run_id"]` 的生成和重复检查；`_config_label()` 使用 `run_config_name` 或 `run_config_index`。
- `dynsteer/harness/model.py`：从 `HarnessRunResult` 删除 `run_id` 字段及校验。
- `dynsteer/model.py`：从 `Trajectory`、`TrajectoryEvaluationReport`、`ToolSandboxSession` 删除 `run_id` 字段；`TrajectoryEvaluationReport.to_dict()` 和 `to_summary_dict()` 不再输出 `run_id`。
- `dynsteer/adapter/loader.py`：加载 `trajectory.json` 时不再要求 `run_id`。
- `dynsteer/harness/outputs.py`：`trajectory_to_json()`、default summary、default report、replay/evaluate 输出和方法级汇总都不再写 `run_id`。
- `dynsteer/evaluate/evaluator.py`：删除 `_replay_run_id()`；`evaluate()`、`evaluate_replay()`、`_build_runtime_result()`、`_runtime_report()` 和 teardown 日志全部改为只使用 `benchmark`、`method`、`case_id`。
- `dynsteer/adapter/toolsandbox/harness.py` 与 `dynsteer/adapter/toolsandbox/utils/trajectory.py`：不再从 `raw_output_dir.parent.parent.name` 反推 `run_id`，生成 ToolSandbox 轨迹时只传 `task_id`、steps 和 snapshots。
- `README.md`、`docs/apis/experiment.md`、`docs/apis/harness.md`、`docs/apis/display.md`：同步把路径和 JSON schema 中的 `run_id` 删除。

#### 3.6.5 覆盖语义

路径中不再包含 `run_id` 后，以下情况都会写到同一个 case 目录并直接覆盖：

- 同一 `experiment_id / benchmark / method / case_id` 下重复运行。
- 同一 `experiment_id / benchmark / method / case_id` 下切换 `model_id`。
- 同一 `experiment_id / benchmark / method / case_id` 下切换 `repeat_index`。
- 单 benchmark 入口下同一 `benchmark / method / case_id` 重复运行。

这是本方案的既定前提，不再为这些情况增加锁、去重、分流或额外目录层级。

## 4. 问题二：ToolSandbox 多配置并行启动风险

### 4.1 是否会发生 ToolSandbox 状态混淆

结论：两个窗口分别运行两个实验配置，一般不会发生 ToolSandbox 内存状态混淆。

原因：

1. ToolSandbox 的当前 context 存在 `tool_sandbox.common.execution_context` 模块全局变量 `_global_execution_context` 中。模块全局变量只在当前 Python 解释器进程内共享。
2. 两个终端窗口分别启动 `scripts/start_experiment_XXX` 时，是两个独立 Python 进程；如果走 Docker 版 `start_experiment.sh`，还是两个独立容器进程。
3. DynSTEER 的 `ToolSandboxHarness.start_case()` 会对 scenario 的 `starting_context` 做 `copy.deepcopy()`，再写入当前进程的全局 context。该动作不会写入跨进程共享状态。
4. 当前 `data/toolsandbox/benchmark.json` 设置 `max_workers=1`；即使使用单 benchmark runner，也会被 `_effective_max_workers()` 限制为 1，避免同一进程内多线程同时操作 ToolSandbox context。
5. 统一实验层 `run_experiment()` 目前本身也是按 spec/case 串行执行，`workers` 参数只是保留，没有在该层创建并行 case worker。

### 4.2 本报告的前提

本报告默认你的实验组织方式已经保证不同配置之间的 `case_id` 完全分离、不重叠。

在这个前提下，本方案不再为 case 级文件覆盖额外设计隔离、去重或分流逻辑，仍沿用当前固定目录写法。

### 4.3 结论边界

本报告后续讨论只聚焦“评估流程是否复跑”和“ToolSandbox 是否会跨进程混状态”，不额外展开 case 级覆盖防护。

## 5. 验收建议

代码落地后建议做最小验证：

1. 构造一个已有完整四文件的 fake case，调用 `existing_case_output()`，应返回 `HarnessEvaluationOutput`。
2. 删除任一必需文件后再次调用，应返回 `None`。
3. 对已有 default case 运行 `run_experiment(..., force_eval=False)`，通过 monkeypatch 或日志确认不会调用 `harness.start_case()`。
4. 同一 case 加 `force_eval=True` 后，应进入原有执行流程并覆盖文件。
5. 对 replay 方法确认：default 缓存命中时仍能读取 `raw_summary.json` 中的 `default_result` 和 `trajectory.json`，replay 结果缓存命中时不调用 `evaluate_replay()`。
6. `scripts/start_experiment.sh --force_eval`、`scripts/start_experiment_no_docker.sh --force_eval` 都应能在 `main.py` 参数中收到该开关。
7. 路径断言：统一实验入口下 case 文件必须落到 `runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>` 与 `results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>`；单 benchmark 入口下 case 文件必须落到 `runs/<benchmark>/<method>/<case_id>` 与 `results/<benchmark>/<method>/<case_id>`。
8. schema 断言：`trajectory.json`、`summary.json`、`report.json`、`index.json` 不再包含 `run_id` 字段。
9. 强制关系断言：调用 `run_experiment(..., force_adapt=True, force_eval=False)` 或 `run_harness_configs(..., force_adapt=True, force_eval=False)` 时，已有 case 级缓存必须被忽略，流程应重新执行并覆盖 `runs/results`。

## 6. 边界与风险

1. 本方案按文件存在判断，不校验 JSON 内容正确性。这符合“最简单”的进度缓存要求；坏文件会在后续读取时显式报错。
2. 本方案默认 case_id 已完全分离，因此不额外讨论 case 级互相覆盖的防护。
3. 本方案不做跨进程增量合并 `index.json`、`scores.json`、`metrics.json`。这类合并需要文件锁、合并策略和冲突处理，已经超出“基于文件是否存在”的简单缓存范围。
4. 当前配置文件中存在明文 API key。本报告不复述具体值；后续建议把这些连接参数移入 `.env` 或不入 git 的本地配置。

## 附录A. 项目中没有把握实现的模块部分

1. **跨进程写同一个 `results/experiments/<experiment_id>` 的安全合并。** 没有把握原因：当前索引和 metrics 是整文件重写，不是追加式协议；要安全合并需要设计文件锁、冲突检测和同一结果目录下 model/case 的唯一身份策略，这已经不是本次最简单缓存方案。
2. **未来 ToolSandbox 是否会新增跨进程外部状态。** 没有把握原因：本次核查基于当前 `../ToolSandbox` 源码，当前状态主要是进程内 `_global_execution_context` 和对象内 DataFrame；如果未来 benchmark 引入外部数据库、服务端会话或共享临时文件，需要重新核查。
