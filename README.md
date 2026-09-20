# DynSTEER

DynSTEER 是阶段式动态 Agent 轨迹评估实验入口。第一阶段实现通用 JSON / ToolSandbox 字典适配、Milestone DAG 阶段划分、本地确定性 Judge、动态权重更新和主实验 CLI。

## 运行最小实验

```bash
./scripts/start_experiment_no_docker.sh --exp data/experiments/toolsandbox_partial_retest.json
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

统一实验入口读取 `data/experiments/*.json`，用于编排 `default`、`dynsteer_replay`、`dynsteer_evaluate` 等方法矩阵。当前示例配置 `data/experiments/toolsandbox_partial_main.json` 默认运行 ToolSandbox 的 `default` 与 `dynsteer_replay`。

通过 Docker 启动实验：

```bash
./scripts/start_experiment.sh --exp data/experiments/toolsandbox_partial_main.json
```

不通过 Docker、使用本地 uv 环境启动实验：

```bash
./scripts/start_experiment_no_docker.sh --exp data/experiments/toolsandbox_partial_main.json
```

如需强制重建 `data/<benchmark>/adapted_cases`，在命令后追加 `--force_adapt`；它会自动连带强制重跑评估 case。只想覆盖已有 case 产物时用 `--force_eval`，只想跳过 `index.json`、`scores.json` 和 `metrics.json` 时用 `--no_sum`。

实验脚本会按实验配置中的 benchmark `data_root` 读取对应 `benchmark.json`，自动使用 `source_root` 安装或挂载 benchmark 源码，并使用 `max_workers` 作为默认 worker 数。`--source` 与 `--workers` 仍可作为临时覆盖项。

`metrics.json.score_rank_tau` 与 `rank_tau_by_repeat` 用于审计 DEFAULT 和 replay 之间的模型排序关联；`repeat_rank_consistency` 则按评估方法分别衡量多次 repeat 的模型排名稳定性。后者包含 pairwise tau-b、完整顺序一致率、top-1 一致率和平均绝对名次位移，不应与模型平均分、成功/结构覆盖、质量分或成本合并为单一“方法总分”。

脚本默认读取 `.env`，可通过 `--env-file PATH` 指定环境变量文件，或通过 `--no-env-file` 禁用。实验输出目录由实验 JSON 中的 `runs_dir` 与 `results_dir` 控制，例如当前示例会写入：

```text
runs/exp/toolsandbox_partial_main/<benchmark>/<model_id>/<method>/<case_id>
results/exp/toolsandbox_partial_main/<benchmark>/<model_id>/<method>/<case_id>
```

也可以绕过脚本直接调用主入口：

```bash
uv run python main.py --exp data/experiments/toolsandbox_partial_main.json
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

选择 `swebench_pro` 或 `skillsbench` 时，不要把 AgentCompass 安装进 DynSTEER/ToolSandbox 的主环境。启动脚本会根据 `--source` 指向的源码自动准备 `.venv-agentcompass`。DynSTEER/ToolSandbox 主环境保持：

```powershell
uv sync --locked
```

脚本按固定 AgentCompass commit `04d138a1c1decd2c9caa8c2659c698d7ffb677b4` 的包声明安装其依赖。当前 AgentCompass 要求 `openai>=2.41.1`，而 DynSTEER/ToolSandbox 基础依赖固定 `openai==1.17.0`；因此两个 benchmark 环境不能混用同一个 uv 锁文件，AgentCompass 必须使用 Python 3.12+ 独立虚拟环境，DynSTEER 源码以 `--no-deps` 方式装入该环境。模型 endpoint 与密钥分别通过 `MODEL_BASE_URL`、`MODEL_API_KEY` 配置。

使用固化后的跨 Benchmark 主实验配置运行：

```powershell
./scripts/start_experiment_no_docker.sh --exp data/experiments/cross_benchmark_main.json --workers 1
```

Docker 路径使用同样的参数：

```powershell
./scripts/start_experiment.sh --exp data/experiments/cross_benchmark_main.json --source ../AgentCompass --workers 1
```

这些脚本会创建 `.venv-agentcompass`，安装 AgentCompass 源码、以 `--no-deps` 安装 DynSTEER 源码，并补充 DynSTEER 运行所需的独立依赖；后续运行会通过环境内 stamp 复用该环境。`DYNSTEER_AGENTCOMPASS_FORCE_INSTALL=1` 可强制重装；`DYNSTEER_AGENTCOMPASS_VENV` 和 `DYNSTEER_AGENTCOMPASS_PYTHON` 可覆盖环境路径和 Python 版本。

配置中的 SWE-bench Pro 和 SkillsBench case ID 已按固定 seed 固化，模型凭据仍通过环境变量提供。DynSTEER 通过 AgentCompass 加载数据、执行 agent 并采用原生评分，不需要本地 SWE-bench_Pro-os、SkillsBench 源仓库或 DynSTEER 自有官方评分脚本。

AgentCompass 只接受一次 run 一个统一模型 endpoint 和密钥：`MODEL_BASE_URL` 必须能服务实验配置矩阵中的全部 model ID，`MODEL_API_KEY` 必须对这些 ID 都有效。混合多个 endpoint 的主实验前，先用 `swebench_pro_pilot.json` 或 `skillsbench_pilot.json` 这类单模型配置分别验证。

上面的命令会准备 AgentCompass 基础运行时。SWE-bench Pro 的 `mini_swe_agent` 与 SkillsBench 的 `openhands` 可由 `metadata.agentcompass.auto_install_dependencies=true` 交回 AgentCompass 按需安装。运行前仍需准备 AgentCompass 数据缓存、Docker 以及 `MODEL_BASE_URL`/`MODEL_API_KEY`。
