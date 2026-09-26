# 源码直连 Benchmark API

## 数据与源码

`data/{benchmark}/benchmark.json` 中的 `source_root` 是唯一源码来源；SWE-bench Pro 的 `dataset_archive` 指向 DynSTEER 根目录内的 zip 快照。三个外部源码目录只读，DynSTEER 创建的 venv、缓存、mirror、run 与 result 都留在项目内。

| Benchmark | Case 来源 | 必需准备 |
| --- | --- | --- |
| ToolSandbox | `tool_sandbox.scenarios.named_scenarios()` | 本地 `../ToolSandbox` 可导入 |
| SWE-bench Pro | `data/swebench_pro/source/eval_samples.jsonl` | `scripts/prepare_swebench_pro.py` |
| SkillsBench | `../skillsbench/tasks/*` | 87 个本地任务目录 |

`scripts/get_cases.py --benchmark NAME --seed 202608 --size N --output PATH` 导出全量 case、逐 case 分层、分布和 source digest；`--check` 只做源码一致性检查。`scripts/build_experiment_configs.py --force` 依据报告固化 10 份正式与 pilot 配置，并在 `data/experiments/model_shards/` 生成 28 份全模型 shard；编号 1-4 对应同一正式配置的 4 个互斥 case 子集。

## 启动与预检

```bash
./scripts/start_experiment.sh --exp data/experiments/swebench_pro_pilot.json
./scripts/start_experiment_no_docker.sh --exp data/experiments/swebench_pro_pilot.json --only_adapt
```

Docker 版是宿主 controller 加本地任务 Docker；host 版的 SWE/Skills 仅支持适配、已有 default 轨迹 replay 和离线汇总。`scripts/preflight_experiment.py --exp PATH --profile docker|host` 在启动前检查源码、依赖、明文凭据、case 唯一性、Docker 或 host 能力，并输出 JSON 摘要。`--profile source --sources-only` 供 bootstrap 只做只读源码检查。Docker daemon 不可用仍会让预检失败；单个任务镜像拉取/构建失败会进入 `image_failures`，预检继续返回成功，运行期把对应 case 写成 `docker_image_pull_failed` 或 `docker_image_build_failed` 失败结果。

SWE-bench Pro 实验运行期只允许检查本地 Docker 镜像或载入 case 级压缩缓存，禁止调用远端 pull；两者缺失或缓存损坏时，立即跳过该 case 并写入 `docker_image_pull_failed`。preflight 的镜像准备失败本身不持久化，只有主实验在运行期确认本地缓存不可用时才写 case 级失败结果。

不同 tmux 窗口可并行启动不同 shard。四个 shard 保留基础 `experiment_id`，runs/results 统一归入同一实验目录；case 输出继续按 benchmark/model/repeat/method/case 隔离，且四个 shard 的 case 集合互斥。SWE 拉取锁与 Skills 构建锁按镜像引用/tag 划分，不同 case 可并发，同一镜像等待者会在锁释放后复查本地镜像和压缩包。不要在两个窗口同时启动同一个 shard 配置；那会重复执行同一批输出。

## 共享 Agent

`dynsteer.agent.openai_tool_agent.run_tool_agent(...)` 直接使用实验 JSON 中的 OpenAI-compatible `model`、`api_key` 与 `base_url`。每轮只允许一个 `bash` tool call；stdout、stderr、exit code 和命令耗时原样进入轨迹。usage 来自 response `usage` 并按请求累计，缺失时保持 `null`，不会用 0 冒充。密钥只传给 SDK client，不进入轨迹。

## SWE-bench Pro

`scripts/prepare_swebench_pro.py` 校验 zip SHA-256、安全解压快照，并用 polars 生成 evaluator 兼容的 `eval_samples.jsonl`；列表字段转换为 Python literal 字符串。`gold_patch` 与 verifier 期望不会进入 TaskCase、generator view、milestone prompt 或引导消息。

`SWEBenchProRuntime` 拼接官方 `sweap-images` 镜像，显式覆盖镜像 entrypoint 后启动长驻容器，并解析容器工作目录、仓库根、HEAD 与初始 git status。HEAD 等于 sample `base_commit` 即有效；官方数据构造可能 checkout 测试文件导致初始 status 非空，该状态会完整记录。Agent 结束后执行 `git add -A` 和 `git diff --cached --binary`，patch 写入 raw 目录。启动或 probe 失败时，容器 State、退出码、Docker 错误与尾部日志写入 `raw/docker_diagnostics.json`，并同步附加到 `image_probe.docker`。`evaluate_patch()` 以子进程调用外部源码 `swe_bench_pro_eval.py`，Docker profile 追加 `--use_local_docker`。resolved 映射为 `1.0/0.0`；缺输出、子进程失败或输出不完整时为 `null`。实验假设 controller 与任务容器均在 Linux 服务器执行；不在 DynSTEER 内额外转换外部 evaluator 生成的文件。

## SkillsBench

`SkillsBenchAdapter` 排除 `mhc-layer-impl`，读取 `task.md` front matter 与任务 digest。`mirror_task()` 把 `task.md`、`environment`、`verifier` 复制到 `runs/skillsbench/` 下，排除 `environment/groundtruth`，拒绝 symlink/hardlink，并把任务内所有 `.sh/.bash` 的 CRLF/CR 字节规范化为 LF；Python 等其他文件保持原始字节。容器保留任务镜像 entrypoint，只覆盖 command 为长驻 shell。镜像以任务 Dockerfile 最后一个 `WORKDIR` 为初始目录，`with-skills` 会把公开 skill 文件复制到容器。

SkillsBench 镜像按完整 tag 派生压缩备份，保存为 `../skillsbench_docker_images/{safe-tag}-{sha256前12位}.tar.gz`。构建和运行前依次检查 Docker 本地镜像、压缩包备份，最后才构建 Dockerfile；构建成功或本地缓存命中但备份缺失时自动保存压缩包。载入备份后会校验目标 tag，备份写入失败不会静默跳过。

Agent 结束后复制 verifier 到 `/verifier` 并执行 `test.sh`；stdout/stderr/result 摘要写入 raw。reward 只接受整行 `0` 或 `1`，分别映射 `0.0/1.0`；其他值或缺失为 `null`。当前本地 `setup-fuzzing-py` 源码没有通用 `verifier/test_outputs.py`，但其 `test.sh` 不引用该文件，预检按 test.sh 实际引用的 `/verifier/*.py` 校验。

## 执行中性配置

实验配置树、benchmark manifest 与 run config 禁止任何显式选择执行器、沙箱、backend 或旧适配层的字段；允许的例外包括 SWE 公开 `tool_backend`、公开镜像命名空间和 SkillsBench 原生任务定义。ToolSandbox 模型使用 `harness_metadata.agent_client/user_client`；SWE/Skills 使用 `harness_metadata.client`，且 `client.model` 必须等于 `model_id`。

