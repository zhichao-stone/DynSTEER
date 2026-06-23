# DynSTEER

DynSTEER 是阶段式动态 Agent 轨迹评估实验入口。第一阶段实现通用 JSON / ToolSandbox 字典适配、Milestone DAG 阶段划分、本地确定性 Judge、动态权重更新和主实验 CLI。

## 运行最小实验

```bash
uv run python main.py --input examples/minimal_experiment.json --output-dir outputs --pretty
```

## 运行测试

```bash
uv run pytest tests -q --cov=dynsteer --cov-report=term-missing
```

## 运行 Benchmark Harness

ToolSandbox 等 benchmark 需要原生环境和工具集。DynSTEER 的 harness 模式会先调用 benchmark adapter 运行场景，再把原始结果转换为 DynSTEER 轨迹并评估。

```powershell
uv run python main.py --benchmark toolsandbox --data-root data\toolsandbox --scenario cellular_off --agent GPT_4_o_2024_05_13 --user GPT_4_o_2024_05_13 --output-dir runs --pretty
```
