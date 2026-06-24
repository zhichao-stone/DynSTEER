# Harness API

## 目标

Harness API 用于把 benchmark 原生运行环境接入 DynSTEER。新结构将离线数据转换与运行期执行分离：

- `dynsteer.adapter.base.BaseBenchmarkAdapter`: 负责 benchmark 名称、离线实验数据转换和 harness 工厂。
- `dynsteer.adapter.base.BaseBenchmarkHarness`: 负责真实执行过程中的轨迹记录、milestone checkpoint、阶段式评估、动态权重更新和 fail-fast 终止。
- `dynsteer.adapter.registry.get_adapter()`: 按 benchmark 名称获取 adapter。
- `dynsteer.adapter.registry.get_harness()`: 通过 adapter 工厂创建 harness。

## 文件结构

adapter 按 benchmark 分包：

```text
dynsteer/adapter/
  base.py
  registry.py
  generic/
    __init__.py
    adapter.py
    harness.py
  toolsandbox/
    __init__.py
    adapter.py
    harness.py
```

`adapter/<benchmark>/adapter.py` 放离线转换逻辑，`adapter/<benchmark>/harness.py` 放运行期 session 管理和单步推进逻辑。旧的 `from dynsteer.adapter.generic import load_task_case` 与 `from dynsteer.adapter.toolsandbox import load_toolsandbox_experiment` 仍由对应 package 的 `__init__.py` 兼容导出。

## 核心数据结构

- `HarnessRunConfig`: 单次 harness 运行配置，包含 benchmark、data root、可选 `case_ids`、`runs_dir`、`results_dir`、fail-fast 策略和 benchmark 私有 `metadata`。
- `BenchmarkCase`: benchmark 内单个可运行测试任务。
- `HarnessStageSettlement`: 运行期阶段结算节点，类型包括 `start`、`milestone`、`finish`。
- `HarnessRunResult`: benchmark 运行结果，包含截断轨迹、阶段结算、策略终止字段和原生摘要。

`HarnessRunConfig` 新增字段：

- `case_ids=None`: 待运行 case ID 列表；为空时由 runner 运行当前 benchmark 的全部 case。
- `runs_dir=Path("runs")`: benchmark 原生输出、运行期中间产物和 `raw_summary.json` 输出目录。
- `results_dir=Path("results")`: 最终 DynSTEER `report.json` 与 `summary.json` 输出目录。
- `stop_on_stage_failure=True`: 阶段评估为 `fail`、`missing`、`invalid`，或阶段分数低于 `ThresholdConfig.fail_threshold` 时提前终止执行。
- `stop_on_minefield=True`: 当前截断轨迹触发 fatal minefield，或 minefield 分数达到 `ThresholdConfig.fatal_minefield_threshold` 时提前终止执行。
- `metadata`: benchmark 私有运行信息。ToolSandbox 使用其中的 `agent`、`user`、`tool_backend` 和 `run_id`。

## BaseBenchmarkHarness 运行语义

`BaseBenchmarkHarness.run_case()` 由基类实现。子类只需要实现 benchmark 私有的 session 生命周期：

- `_start_case(config, case_id, raw_output_dir)`: 初始化原生 session。
- `_task_case_from_session(session)`: 提取 DynSTEER `TaskCase`。
- `_advance_case(session)`: 推进原生 agent 执行一个可中断批次，并返回新增 `TrajectoryStep`。
- `_case_finished(session)`: 判断原生 session 是否自然完成。
- `_snapshots_from_session(session)`: 提取状态快照，默认空列表。
- `_metrics_from_session(session)`: 提取 metrics，默认空字典。
- `_final_state_from_session(session)`: 提取当前状态，默认空。
- `_stop_case(session, reason)`: 策略终止时停止原生执行。
- `_teardown_case(session)`: 释放原生资源。

执行流程是：

1. 子类启动 benchmark session。
2. agent 每执行一个步骤后，子类返回新的 DynSTEER `TrajectoryStep`。
3. 基类追加步骤并基于当前截断轨迹判断 ready milestone 是否命中。
4. 若命中 milestone，基类暂停 rollout，执行阶段式评估并写入 `HarnessStageSettlement(checkpointed=True)`。
5. 若阶段评估合格，基类恢复同一个 session 的执行，直到下一次 checkpoint 或自然完成。
6. 若阶段评估失败、分数过低或触发 minefield fail-fast，基类调用 `_stop_case()`，返回当前截断轨迹和终止摘要，不追加 `finish` 结算。
7. 若任务自然完成，基类对最后一个已匹配 milestone 到自然终点的尾段执行结算，并追加带 `stage_report` 的 `finish` 结算。

milestone checkpoint 不是“遇到 milestone 就退出”的配置。它只表示暂停当前 rollout、结算上一阶段、更新下一阶段评估策略，然后在合格时继续执行。

## 阶段区间

阶段式评估的区间由 `MilestoneGraph.edges` 决定：

- 当前阶段终点是当前命中的 milestone 对应步骤。
- 当前阶段起点是当前 milestone 的直接前驱 milestone 所匹配的步骤。
- 若存在多个直接前驱，取这些前驱已匹配步骤中的最大 `step_index`。
- 若没有直接前驱，起点取 `start` 结算节点。
- 自然完成时追加 `finish` 结算节点，并在其 `metadata.stage_report` 中记录尾段评估摘要；fail-fast 终止时不追加 `finish`。

## 终止字段

`HarnessRunResult` 包含：

- `stage_settlements`: start/milestone/finish 结算列表。
- `terminated_by_policy`: 是否因 DynSTEER 运行期策略提前终止。
- `termination_code`: 终止短码，例如 `stage_failure:m1`、`stage_score:m1`、`minefield:mf0`、`minefield_score:1.000`。
- `termination_reason`: 面向报告的中文原因。

runner 会把这些字段合并写入 `raw_summary.json`，同时仍基于当前轨迹写出 `report.json` 和 `summary.json`。

`dynsteer.harness.runner` 提供两个入口：

- `run_harness_case(config, harness)`: 运行一个 case；若 `case_ids` 为空则选择当前 benchmark 的第一个 case。
- `run_harness_cases(config, harness)`: 运行一个或多个 case；若未指定 `case_ids`，运行当前 benchmark 的全部 case。`main.py --benchmark ...` 使用该入口。

`dynsteer.harness.config.load_harness_run_configs()` 会从 data-root 读取 `benchmark.json` 和 `run_config.json`，并按运行配置列表生成多组 `HarnessRunConfig`。

## CLI

离线 JSON 评估：

```powershell
uv run python main.py --input examples/minimal_experiment.json --results-dir results
```

benchmark harness 评估：

```powershell
uv run python main.py --benchmark toolsandbox --data-root data\toolsandbox --runs-dir runs --results-dir results
```

benchmark 模式的 CLI 只接收通用参数。`scenario`、`agent`、`user` 等 benchmark 私有运行参数必须写入 data-root 下的配置文件。`report.json`、`summary.json`、`raw_summary.json` 和 `logs.json` 固定使用 `indent=4` 的 UTF-8 JSON 输出，不提供 `--pretty` 配置。

ToolSandbox 示例：

`data/toolsandbox/benchmark.json`

```json
{
    "benchmark": "toolsandbox",
    "source_root": "../../../ToolSandbox",
    "tool_backend": "DEFAULT"
}
```

`data/toolsandbox/run_config.json`

```json
[
    {
        "scenarios": ["wifi_off"],
        "agent": "GPT_4_o_2024_05_13",
        "user": "GPT_4_o_2024_05_13"
    },
    {
        "scenarios": [],
        "agent": "Claude_3_Haiku",
        "user": "GPT_4_o_2024_05_13"
    }
]
```

`run_config.json` 是运行配置列表。每项的 `scenarios` 是 case ID 列表；空列表表示全场景评估。每项会生成独立的 `run_id`，也可以通过可选 `name` 或 `run_id` 字段指定。

## 输出

离线模式输出：

- `results/<run_name>/report.json`: DynSTEER 完整报告。
- `results/<run_name>/summary.json`: DynSTEER 摘要报告。
- `results/<run_name>/logs.json`: 当前离线评估日志缓冲。

Harness 模式输出：

- `runs/<benchmark>/<run_id>/<case_id>/raw/`: benchmark 原生输出。
- `runs/<benchmark>/<run_id>/<case_id>/raw_summary.json`: benchmark 原生摘要、阶段结算和策略终止摘要。
- `results/<benchmark>/<run_id>/<case_id>/report.json`: DynSTEER 完整报告。
- `results/<benchmark>/<run_id>/<case_id>/summary.json`: DynSTEER 摘要报告。

`raw_summary.json` 示例字段：

```json
{
  "terminated_by_policy": true,
  "termination_code": "stage_failure:m1",
  "termination_reason": "阶段评估状态为 fail，提前终止执行：m1",
  "stage_settlements": []
}
```

## ToolSandbox 适配说明

ToolSandbox adapter 通过懒加载导入 `tool_sandbox`，不会让 DynSTEER 核心包直接依赖 ToolSandbox。运行时需要保证 ToolSandbox 及其依赖已安装，或在 `data/toolsandbox/benchmark.json` 中配置可导入的外部 `source_root`。`tool_backend` 是 ToolSandbox 的工具后端枚举，目前 ToolSandbox 只提供 `DEFAULT`；它用于同名工具存在多后端实现时的优先选择，不是通用 benchmark 字段。

ToolSandbox harness 默认不调用 `Scenario.play_and_evaluate()`，因为该函数同时包含原生评估逻辑，而 DynSTEER 运行期只需要真实执行过程和轨迹。新的执行路径会优先尝试使用 `advance()`、`step()` 或 `play()` 推进原生 session；若 scenario 没有这些接口，则按 role 的 `respond()` 接口轮询推进一次原生对话，并记录 `native_evaluation_skipped=True`。阶段评估、minefield 判断和 fail-fast 终止由 DynSTEER 自己完成。
