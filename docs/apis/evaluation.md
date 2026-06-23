# DynSTEER 评估 API

## 数据模型

- `TaskCase`: 任务定义，包含 `task_id`、`task_description`、`task_types`、工具/环境 schema，以及可选 `milestone_graph`。
- `Trajectory`: Agent 执行轨迹，包含 `run_id`、`task_id`、`steps`、`snapshots`、`final_state`、`metrics`。
- `MilestoneGraph`: 阶段图，使用 `nodes: list[Milestone]` 与 `edges: list[tuple[str, str]]` 表达单向 DAG。

## Adapter

- `load_task_case(data: JsonObject) -> TaskCase`: 载入通用 JSON task。
- `load_trajectory(data: JsonObject) -> Trajectory`: 载入通用 JSON trajectory。
- `load_milestone_graph(data: JsonObject) -> MilestoneGraph`: 载入 milestone DAG。
- `load_toolsandbox_experiment(data: JsonObject) -> tuple[TaskCase, Trajectory]`: 载入 ToolSandbox 风格字典，不依赖外部 ToolSandbox 包。

必要字段缺失、字段类型不匹配或枚举值非法时抛出 `ValueError`。

## 评估入口

`evaluate_trajectory(task_case, trajectory, judge=None, thresholds=None, match_config=None, weight_config=None) -> TrajectoryEvaluationReport`

流程：

1. 检查 minefield。
2. 生成候选边界。
3. 对每个 milestone 和边界计算结构化分数。
4. 匹配 milestone DAG。
5. 构造阶段区间。
6. 使用 `Judge` 协议评估阶段，并按规则在 `cheap`、`standard`、`expensive` 间调度。
7. 更新下一阶段维度权重并输出报告。

## 命令行入口

```bash
python main.py --input examples/minimal_experiment.json --output-dir outputs --pretty
```

输出：

- `outputs/{run_name}/report.json`
- `outputs/{run_name}/summary.json`
- `outputs/{run_name}/logs.json`

返回码：

- `0`: 成功。
- `1`: 输入文件、JSON 或字段解析失败。
- `2`: 评估执行失败。

## Harness 模式

除离线 JSON 输入外，DynSTEER 支持通过 benchmark harness 运行场景并采集轨迹。Harness 运行后仍会转换为 `TaskCase` 与 `Trajectory`，再调用 `evaluate_trajectory()`。因此动态评估核心保持统一，差异只存在于 adapter 与 harness 层。
