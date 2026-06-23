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
