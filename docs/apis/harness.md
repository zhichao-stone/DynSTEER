# Harness API

## 目标

Harness API 用于把 benchmark 原生运行环境接入 DynSTEER。benchmark adapter 负责运行原生环境、采集原始轨迹、转换为 DynSTEER 的 `TaskCase`、`Trajectory` 和 `MilestoneGraph`。

## 核心数据结构

- `HarnessRunConfig`: 单次 harness 运行配置，包含 benchmark 名称、data root、scenario、agent、user 和输出目录。
- `BenchmarkCase`: benchmark 内单个可运行测试任务。
- `HarnessRunResult`: benchmark 原生运行结果转换后的 DynSTEER 数据。
- `BenchmarkHarness`: adapter 协议，包含 `list_cases()` 和 `run_case()`。

## CLI

离线 JSON 评估：

```powershell
uv run python main.py --input examples/minimal_experiment.json --output-dir outputs --pretty
```

benchmark harness 评估：

```powershell
uv run python main.py --benchmark toolsandbox --data-root data\toolsandbox --scenario cellular_off --agent GPT_4_o_2024_05_13 --user GPT_4_o_2024_05_13 --output-dir runs --pretty
```

## 输出

Harness 模式输出：

- `runs/<benchmark>/<run_id>/<case_id>/raw/`: benchmark 原生输出。
- `runs/<benchmark>/<run_id>/<case_id>/dynsteer/report.json`: DynSTEER 完整报告。
- `runs/<benchmark>/<run_id>/<case_id>/dynsteer/summary.json`: DynSTEER 摘要报告。
- `runs/<benchmark>/<run_id>/<case_id>/dynsteer/raw_summary.json`: benchmark 原生摘要。

## ToolSandbox 适配说明

ToolSandbox adapter 通过懒加载导入 `tool_sandbox`，不会让 DynSTEER 核心包直接依赖 ToolSandbox。运行时需要保证 ToolSandbox 及其依赖已安装，或在 `data/toolsandbox/benchmark.json` 中配置可导入的外部 `source_root`。`data/toolsandbox` 只保存静态 manifest，不复制 ToolSandbox 源码，也不保存运行产物。
