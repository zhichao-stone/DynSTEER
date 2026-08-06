# DynSTEER

DynSTEER 是阶段式动态 Agent 轨迹评估实验入口。第一阶段实现通用 JSON / ToolSandbox 字典适配、Milestone DAG 阶段划分、本地确定性 Judge、动态权重更新和主实验 CLI。

## 运行最小实验

```bash
uv run python main.py --input examples/minimal_experiment.json --results-dir results
```

## 查看静态评估看板

生成展示数据：

```powershell
uv run python -m display.build --runs-dir runs --results-dir results --output display/data.js
```

然后直接打开 `display/index.html` 查看中文评估看板。页面会展示 benchmark/method/case 切换、执行轨迹、milestone graph、阶段评估与点击联动。

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

`data/toolsandbox/run_configs.json`：

```json
[
    {
        "scenarios": ["wifi_off"],
        "agent": "GPT_4_o_2024_05_13",
        "user": "GPT_4_o_2024_05_13",
        "agent_client": {
            "api_key_env": "DYNSTEER_AGENT_API_KEY",
            "base_url_env": "DYNSTEER_AGENT_BASE_URL"
        },
        "user_client": {
            "api_key_env": "DYNSTEER_USER_API_KEY",
            "base_url_env": "DYNSTEER_USER_BASE_URL"
        }
    }
]
```

`run_configs.json` 是运行配置列表。每项的 `scenarios` 是待评估场景 ID 列表；空列表表示运行全部场景。`agent` 与 `user` 只表示 ToolSandbox 角色类型，连接参数通过同级的 `agent_client` 与 `user_client` 配置；未填写时继续使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 等全局环境变量。

`source_root` 相对于 DynSTEER 项目根目录解析，也就是 `main.py` 所在目录。通过 Docker 启动时，可以用 `--source` 指定原生 benchmark 源码目录，启动脚本会把该目录挂载进容器，并在容器内执行 editable 安装。例如 ToolSandbox：
```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
```

## 运行统一实验

统一实验入口读取 `data/experiments/*.json`，用于编排 `default`、`dynsteer_replay`、`dynsteer_evaluate` 等方法矩阵。当前示例配置 `data/experiments/double_benchmark_initial.json` 默认运行 ToolSandbox 的 `default` 与 `dynsteer_replay`。

通过 Docker 启动实验：

```bash
./scripts/start_experiment.sh --exp data/experiments/double_benchmark_initial.json
```

不通过 Docker、使用本地 uv 环境启动实验：

```bash
./scripts/start_experiment_no_docker.sh --exp data/experiments/double_benchmark_initial.json
```

如需强制重建 `data/<benchmark>/adapted_cases`，在命令后追加 `--force_adapt`；它会自动连带强制重跑评估 case。只想覆盖已有 case 产物时用 `--force_eval`，只想跳过 `index.json`、`scores.json` 和 `metrics.json` 时用 `--no_sum`。

实验脚本会按实验配置中的 benchmark `data_root` 读取对应 `benchmark.json`，自动使用 `source_root` 安装或挂载 benchmark 源码，并使用 `max_workers` 作为默认 worker 数。`--source` 与 `--workers` 仍可作为临时覆盖项。

脚本默认读取 `.env`，可通过 `--env-file PATH` 指定环境变量文件，或通过 `--no-env-file` 禁用。实验输出目录由实验 JSON 中的 `runs_dir` 与 `results_dir` 控制，例如当前示例会写入：

```text
runs/exp/double_benchmark_initial/<benchmark>/<model_id>/<method>/<case_id>
results/exp/double_benchmark_initial/<benchmark>/<model_id>/<method>/<case_id>
```

也可以绕过脚本直接调用主入口：

```bash
uv run python main.py --exp data/experiments/double_benchmark_initial.json
```
直接调用主入口时，benchmark 源码仍需要已能被当前环境导入；自动读取 `source_root` 和 `max_workers` 的是实验 wrapper 脚本。
Docker 启动时，脚本会把 `benchmark.json.source_root` 指向的源码目录自动挂载到容器内与 `benchmark.json` 相同的相对路径。原始 `data/{benchmark}/benchmark.json` 不会被修改，也不需要在 `/workspace` 下创建额外软链接。

Docker 镜像在 build 阶段会生成 `/opt/bootstrap-venv` 基础环境。通过 `scripts/start.sh` 启动时，脚本会自动检测宿主机当前用户的 UID/GID，并让容器以该用户运行；直接使用 Docker Compose 且未传入 UID/GID 时，默认回退到 `1000:100`。运行期 uv 虚拟环境和 cache 默认写入项目目录下的 `.venv` 与 `.uv-cache`，因此新生成的 `runs`、`results` 产物会归属当前宿主机用户，便于通过 SFTP 清理。

接入新的 benchmark 时，最小前置步骤如下：

1. 在 `dynsteer/adapter/{benchmark}/` 下实现对应的 `adapter.py` 与 `harness.py`。
2. 在 `dynsteer/adapter/registry.py` 的 `_ADAPTERS` 中注册新的 adapter。
3. 在 `data/{benchmark}/benchmark.json` 中填写 `benchmark`、`source_root` 和 benchmark 需要的静态字段；若 benchmark 不支持 case 并行，可填写 `max_workers` 作为并发上限。
4. 在 `data/{benchmark}/run_configs.json` 中填写运行配置与待评估场景。
5. Docker 启动时传入 `--source {benchmark源码路径}`；本地非 Docker 运行时仍可手动执行 `uv add --editable {benchmark源码路径}` 和 `uv sync`。

```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
```
# AgentCompass benchmark

AgentCompass 是可选 benchmark 依赖。只运行 DynSTEER/ToolSandbox 时使用：

```powershell
uv sync --locked
```

选择 `swebench_pro` 或 `skillsbench` 时，先安装可选 extra：

```powershell
uv sync --locked --extra agentcompass
```

然后还必须按固定 AgentCompass commit `04d138a1c1decd2c9caa8c2659c698d7ffb677b4` 的 requirements 安装所选 benchmark、harness 和 environment 依赖。当前 AgentCompass 固定提交要求 `openai>=2.41.1`，而 DynSTEER/ToolSandbox 基础依赖仍固定 `openai==1.17.0`；因此不能把两个 benchmark 环境当作同一个已验证的 uv 环境，建议为 AgentCompass 单独创建 Python 3.12+ 虚拟环境，并以 `--no-deps` 安装 DynSTEER 源码后运行。模型 endpoint 与密钥分别通过 `MODEL_BASE_URL`、`MODEL_API_KEY` 配置。

将 `data/experiments/agentcompass_cross_benchmark.json` 中的模型 ID、SWE-bench Pro instance ID 和 SkillsBench task ID 三个占位值替换后运行：

```powershell
./scripts/start_experiment_no_docker.sh --exp data/experiments/agentcompass_cross_benchmark.json --workers 1
```

Docker 路径使用同样的参数：

```powershell
./scripts/start_experiment.sh --exp data/experiments/agentcompass_cross_benchmark.json --workers 1
```

这些脚本会根据 benchmark 自动执行 `uv sync --extra agentcompass`；它们只负责启用 bridge extra，不会替您安装 AgentCompass 的完整上游 requirements。实际运行仍应使用下方的 Python 3.12 独立环境方案。

该示例中的三个 ID 仍是占位符，必须替换为真实值。DynSTEER 通过 AgentCompass 加载数据、执行 agent 并采用原生评分，不需要本地 SWE-bench_Pro-os、SkillsBench 源仓库或 DynSTEER 自有官方评分脚本。

在 Windows 上，针对当前 OpenAI SDK 版本冲突，推荐使用独立环境运行真实 AgentCompass 评估：

```powershell
uv venv .venv-agentcompass --python 3.12
$agentPython = ".venv-agentcompass\Scripts\python.exe"
uv pip install --python $agentPython -r ..\AgentCompass\requirements\app.txt -r ..\AgentCompass\requirements\swe.txt -r ..\AgentCompass\requirements\mini-swe-agent.txt -r ..\AgentCompass\requirements\openhands.txt
uv pip install --python $agentPython --no-deps -e ..\AgentCompass -e .
uv pip install --python $agentPython "polars==0.20.31"
$env:UV_PROJECT_ENVIRONMENT = ".venv-agentcompass"
$env:DYNSTEER_SKIP_UV_SYNC = "1"
./scripts/start_experiment_no_docker.sh --exp data/experiments/agentcompass_cross_benchmark.json --workers 1
```

这会同时准备 SWE-bench Pro 的 `mini_swe_agent` 与 SkillsBench 的 `openhands`。如果只跑其中一个 benchmark，可以去掉另一套 requirements 和实验配置项。运行前仍需准备 AgentCompass 数据缓存、Docker 以及 `MODEL_BASE_URL`/`MODEL_API_KEY`。
