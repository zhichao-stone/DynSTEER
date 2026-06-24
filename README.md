# DynSTEER

DynSTEER 是阶段式动态 Agent 轨迹评估实验入口。第一阶段实现通用 JSON / ToolSandbox 字典适配、Milestone DAG 阶段划分、本地确定性 Judge、动态权重更新和主实验 CLI。

## 运行最小实验

```bash
uv run python main.py --input examples/minimal_experiment.json --results-dir results
```

## 运行测试

```bash
uv run pytest tests -q --cov=dynsteer --cov-report=term-missing
```

## 运行 Benchmark Harness

ToolSandbox 等 benchmark 需要原生环境和工具集。DynSTEER 的 harness 模式会先调用 benchmark adapter 运行场景，再把原始结果转换为 DynSTEER 轨迹并评估。

`data-root` 下需要提供 benchmark 静态 manifest 与运行配置：

`data/toolsandbox/benchmark.json`：

```json
{
    "benchmark": "toolsandbox",
    "source_root": "../ToolSandbox",
    "tool_backend": "DEFAULT"
}
```

`data/toolsandbox/run_config.json`：

```json
[
    {
        "scenarios": ["wifi_off"],
        "agent": "GPT_4_o_2024_05_13",
        "user": "GPT_4_o_2024_05_13"
    }
]
```

`run_config.json` 是运行配置列表。每项的 `scenarios` 是待评估场景 ID 列表；空列表表示运行全部场景。

`source_root` 相对于 DynSTEER 项目根目录解析，也就是 `main.py` 所在目录。通过 Docker 启动时，可以用 `--source` 指定原生 benchmark 源码目录，启动脚本会把该目录挂载进容器，并在容器内执行 editable 安装。例如 ToolSandbox：
```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox
```
Docker 镜像在 build 阶段会生成基础环境，运行时的 `/opt/venv` 和 uv cache 使用 Docker named volume 持久化；因此首次构建后，后续修改代码、`.env` 或 `data/` 配置不需要重建镜像，新增或更新 benchmark 依赖时重新带上 `--source` 启动即可。

接入新的 benchmark 时，最小前置步骤如下：

1. 在 `dynsteer/adapter/{benchmark}/` 下实现对应的 `adapter.py` 与 `harness.py`。
2. 在 `dynsteer/adapter/registry.py` 的 `_ADAPTERS` 中注册新的 adapter。
3. 在 `data/{benchmark}/benchmark.json` 中填写 `benchmark`、`source_root` 和 benchmark 需要的静态字段。
4. 在 `data/{benchmark}/run_config.json` 中填写运行配置与待评估场景。
5. Docker 启动时传入 `--source {benchmark源码路径}`；本地非 Docker 运行时仍可手动执行 `uv add --editable {benchmark源码路径}` 和 `uv sync`。

```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox
```
