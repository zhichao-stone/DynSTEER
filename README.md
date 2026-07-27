# DynSTEER

DynSTEER 是阶段式动态 Agent 轨迹评估实验入口。第一阶段实现通用 JSON / ToolSandbox 字典适配、Milestone DAG 阶段划分、本地确定性 Judge、动态权重更新和主实验 CLI。

## 运行最小实验

```bash
uv run python main.py --input examples/minimal_experiment.json --results-dir results
```

## 查看静态评估看板

生成展示数据：

```powershell
uv run python display/build.py --runs-dir runs --results-dir results --output display/data.js
```

然后直接打开 `display/index.html` 查看中文评估看板。页面会展示 run/scenario 切换、执行轨迹、milestone graph、阶段评估与点击联动。

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
    "tool_backend": "DEFAULT",
    "max_workers": 1
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
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
```

## 运行统一实验

统一实验入口读取 `data/experiments/*.json`，用于编排 `default`、`dynsteer_replay`、`dynsteer_evaluate` 等方法矩阵。当前示例配置 `data/experiments/double_benchmark_initial.json` 默认运行 ToolSandbox 的 `default` 与 `dynsteer_replay`。

通过 Docker 启动实验：

```bash
./scripts/start_experiment.sh \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --source ../ToolSandbox \
  --workers 1
```

不通过 Docker、使用本地 uv 环境启动实验：

```bash
./scripts/start_experiment_no_docker.sh \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --source ../ToolSandbox \
  --workers 1
```

如果 benchmark 源码已经可以被当前环境导入，也可以省略 `--source`：

```bash
./scripts/start_experiment_no_docker.sh \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --workers 1
```

脚本默认读取 `.env`，可通过 `--env-file PATH` 指定环境变量文件，或通过 `--no-env-file` 禁用。实验输出目录由实验 JSON 中的 `runs_dir` 与 `results_dir` 控制，例如当前示例会写入：

```text
runs/experiments/double_benchmark_initial
results/experiments/double_benchmark_initial
```

也可以绕过脚本直接调用主入口：

```bash
uv run python main.py \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --workers 1
```
Docker 容器内传入 `--source` 时，启动脚本会复制 `data/{benchmark}` 到 `.dynsteer-runtime/data/{benchmark}`，并只在该运行期副本中把 `benchmark.json` 的 `source_root` 改为容器内挂载路径。原始 `data/{benchmark}/benchmark.json` 不会被修改，也不需要在 `/workspace` 下创建额外软链接。

Docker 镜像在 build 阶段会生成 `/opt/bootstrap-venv` 基础环境。通过 `scripts/start.sh` 启动时，脚本会自动检测宿主机当前用户的 UID/GID，并让容器以该用户运行；直接使用 Docker Compose 且未传入 UID/GID 时，默认回退到 `1000:100`。运行期 uv 虚拟环境和 cache 默认写入项目目录下的 `.venv` 与 `.uv-cache`，因此新生成的 `runs`、`results` 产物会归属当前宿主机用户，便于通过 SFTP 清理。

接入新的 benchmark 时，最小前置步骤如下：

1. 在 `dynsteer/adapter/{benchmark}/` 下实现对应的 `adapter.py` 与 `harness.py`。
2. 在 `dynsteer/adapter/registry.py` 的 `_ADAPTERS` 中注册新的 adapter。
3. 在 `data/{benchmark}/benchmark.json` 中填写 `benchmark`、`source_root` 和 benchmark 需要的静态字段；若 benchmark 不支持 case 并行，可填写 `max_workers` 作为并发上限。
4. 在 `data/{benchmark}/run_config.json` 中填写运行配置与待评估场景。
5. Docker 启动时传入 `--source {benchmark源码路径}`；本地非 Docker 运行时仍可手动执行 `uv add --editable {benchmark源码路径}` 和 `uv sync`。

```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
```
