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

实验脚本会按实验配置中的 benchmark `data_root` 读取对应 `benchmark.json`，只读使用 `source_root` 指向的本地源码，并使用 `max_workers` 作为默认 worker 数。`--workers` 仍可作为临时覆盖项；源码路径只来自 manifest，不接受脚本参数覆盖。

`metrics.json.score_rank_tau` 与 `rank_tau_by_repeat` 用于审计 DEFAULT 和 replay 之间的模型排序关联；`repeat_rank_consistency` 则按评估方法分别衡量多次 repeat 的模型排名稳定性。后者包含 pairwise tau-b、完整顺序一致率、top-1 一致率和平均绝对名次位移，不应与模型平均分、成功/结构覆盖、质量分或成本合并为单一“方法总分”。

统一实验脚本默认读取项目根目录的 `.env`，可通过 `--env-file PATH` 指定环境变量文件，或通过 `--no-env-file` 禁用。`.env` 中的同名变量会覆盖启动前已有的 shell 环境变量；脚本后续注入的执行 profile 和运行目录仍保持最终值。实验输出目录由实验 JSON 中的 `runs_dir` 与 `results_dir` 控制，例如当前示例会写入：

```text
runs/exp/toolsandbox_partial_main/<benchmark>/<model_id>/<method>/<case_id>
results/exp/toolsandbox_partial_main/<benchmark>/<model_id>/<method>/<case_id>
```

也可以绕过脚本直接调用主入口：

```bash
uv run python main.py --exp data/experiments/toolsandbox_partial_main.json
```
直接调用 `main.py` 时，ToolSandbox 源码仍需要已能被当前环境导入；SWE-bench Pro 与 SkillsBench 会因为缺少启动脚本注入的执行 profile 而拒绝新的 default 执行。

接入新的 benchmark 时，最小前置步骤如下：

1. 在 `dynsteer/adapter/{benchmark}/` 下实现对应的 `adapter.py` 与 `harness.py`。
2. 在 `dynsteer/adapter/registry.py` 的 `_ADAPTERS` 中注册新的 adapter。
3. 在 `data/{benchmark}/benchmark.json` 中填写 `benchmark`、`source_root` 和 benchmark 需要的静态字段；若 benchmark 不支持 case 并行，可填写 `max_workers` 作为并发上限。
4. 在 `data/{benchmark}/run_configs.json` 中填写执行中性运行配置与待评估场景。
5. 通过统一实验脚本启动，由脚本按 benchmark 创建专属 uv 环境并注入 Docker/host profile。

```powershell
./scripts/start.sh --benchmark toolsandbox --source ../ToolSandbox --workers 3
```
# 源码直连 benchmark

三个 benchmark 均直接读取本地源码或本地数据快照：

```text
../ToolSandbox
../SWE-bench_Pro-os
../skillsbench
swebench_pro_data.zip
```

这些目录和 zip 对 DynSTEER 只读。启动脚本会在项目内创建 `.venv-toolsandbox`、`.venv-swebench-pro`、`.venv-skillsbench` 与 `.uv-cache`，不会向外部源码目录写入 venv、缓存、镜像或运行产物。SkillsBench 的任务 mirror 固定位于 `runs/skillsbench/` 下。

首次运行 SWE-bench Pro 前先准备本地快照：

```powershell
uv run python scripts/prepare_swebench_pro.py
```

使用固化后的单 benchmark 配置运行。Docker 版为宿主 controller 加本地任务 Docker；非 Docker 版对 SWE/Skills 只允许适配、已有轨迹 replay 和离线汇总：

```powershell
./scripts/start_experiment.sh --exp data/experiments/swebench_pro_pilot.json --workers 1
./scripts/start_experiment_no_docker.sh --exp data/experiments/swebench_pro_pilot.json --only_adapt
```

并行跑实验时使用 `data/experiments/model_shards/{benchmark}_{实验}_{1..4}.json`。每份 shard 保留全部 model，并把 case_ids 确定性均分为互斥子集，因此不同 tmux 窗口不会构建同一个任务镜像；不要同时在两个窗口启动同一个 shard 文件。

```bash
./scripts/start_experiment.sh --exp data/experiments/model_shards/swebench_pro_main_2.json --workers 1
```

ToolSandbox 本身没有任务容器，两个脚本都支持完整执行；SWE/Skills 的 default 执行和 native verifier 只能通过 Docker 版脚本运行。启动前预检会输出 JSON 摘要，并在 matrix 展开前报告依赖、凭据、源码、case、Docker 或 host 能力问题。

实验 JSON、benchmark manifest 与 run config 都是执行中性配置；模型、judge 和 milestone generator 的明文 `api_key`、`base_url` 直接写在配置中。运行期不再读取模型环境变量。详细接口见 `docs/apis/source_direct_benchmarks.md`。
