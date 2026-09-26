# DynSTEER 源码直连跨 Benchmark 实验与代码修改方案

## 1. 方案定位

本方案替代 [2026-09-15-DynSTEER跨Benchmark主实验与介入实验方案.md](2026-09-15-DynSTEER跨Benchmark主实验与介入实验方案.md) 中所有 AgentCompass 执行与配置设计。实验问题、指标口径、消融臂和介入实验继续沿用原方案；本文件只重写执行框架、数据接入、目录结构、配置格式和脚本边界。

新的执行原则如下：

1. 三个 benchmark 均直接使用本地源码或本地数据快照：
   - ToolSandbox：`../ToolSandbox`
   - SWE-bench Pro：`../SWE-bench_Pro-os`
   - SkillsBench：`../skillsbench`
2. 三个外部目录对 DynSTEER 只读。禁止在外部目录创建 venv、缓存、任务镜像、运行产物或临时文件。
3. 所有 DynSTEER 生成的数据、venv、缓存、run/output 均位于 `DynSTEER` 目录内。
4. 不再安装或导入 AgentCompass；删除 `dynsteer/adapter/agentcompass` 是预期状态，不恢复。
5. 同一份实验 JSON 可以传入 Docker 版和非 Docker 版脚本；脚本按 profile 声明实际能力。实验 JSON、benchmark manifest 和 run config 均不得显式选择 Docker、backend、sandbox 或执行环境；执行方式只由启动脚本注入。
6. 三个 benchmark 的 Agent 决策循环均由 DynSTEER 直接执行：模型调用使用实验 JSON 中的明文 `api_key` 与 `base_url`，不再依赖 ToolSandbox 外的 benchmark 专有 agent CLI。
7. SWE-bench Pro 不使用 Modal；SkillsBench 不使用 Daytona 或 OpenCode runner 作为 Agent 执行方。二者只把本地 Docker 作为任务工具环境和 native verifier 的隔离运行时。
8. Docker 版采用“宿主 controller + 本地任务 Docker”。不再把 DynSTEER controller 放入容器后再挂 Docker socket，避免外部 runner 在 controller 内部路径上创建 bind mount 时由宿主 Docker daemon 解析路径失败。

## 2. 调整后的实验矩阵

### 2.1 固定设置

| 项目 | 设置 |
| --- | --- |
| 模型 | `deepseek-v4-pro`、`deepseek-v4-flash`、`qwen-plus-2025-12-01`、`qwen3-max-2026-01-23` |
| 重复次数 | 主实验、消融实验、介入实验均为 `repeats=3` |
| 随机种子 | `202608`，用于 case 抽样和 bootstrap |
| 配对键 | `(benchmark, model_id, repeat_index, case_id)` |
| Judge | 三类实验使用同一个 `fixed-judge` profile；模型、endpoint 和 key 直接写在配置中 |
| 凭据 | 延续 `toolsandbox_main_X.json` 的做法，显式保存明文 `api_key` 与 `base_url`；本方案为内部实验代码，不做 key 轮换、删除或脱敏 |

主实验方法保持三臂：

| 方法 | 含义 |
| --- | --- |
| `default` | DynSTEER 直接执行 Agent 决策循环，并使用 benchmark 原生 verifier 评分 |
| `dynsteer_replay` | 复用同 identity Default 轨迹，开启完整 DynSTEER replay |
| `dynsteer_replay_static` | stage-only，关闭动态路由与动态权重，保留阶段式评估与 stop |

SWE-bench Pro 与 SkillsBench 均以完整任务为执行单位，不能逐 agent step 在线中断，因此只进入主实验与消融实验。动态介入实验继续仅限 ToolSandbox。

### 2.2 数据规模

| Benchmark | 主实验 | 消融实验 | 介入实验 |
| --- | ---: | ---: | ---: |
| ToolSandbox | 全量 509 | 100，按 task type / safety category 分层 | 200，按 task type / safety category 分层 |
| SWE-bench Pro | 100，按 `repo` 分层 | 50，按 `repo` 分层 | 不执行 |
| SkillsBench | 当前源码全量 87，按 `metadata.category` 分层报告 | 50，按 `metadata.category` 分层 | 不执行 |

SkillsBench 当前本地任务目录只有 87 个任务，不足原方案的 100；主实验使用全量 87，不用重复任务补齐。若后续源码升级导致任务数变化，配置生成脚本必须记录源码版本和任务 digest，不隐式混用不同版本任务。

所有正式实验配置显式写入完整 `case_ids`。抽样在配置生成时一次完成并固化，运行期不得重新抽样。

### 2.3 消融实验

每个 benchmark 一份独立消融配置，包含七个实际运行臂：

```text
default
full = dynsteer_replay
no_dynamic_routing = dynsteer_replay_static_routing
no_dynamic_weighting = dynsteer_replay_static_weighting
no_minefields = dynsteer_replay_no_minefields
no_milestone_graph = dynsteer_replay_no_milestone_graph
no_policy_stop = dynsteer_replay_no_policy_stop
```

`stage_only = dynsteer_replay_static` 不在消融配置中重复执行；聚合报告按同一 `(benchmark, model_id, repeat_index, case_id)` 从主实验引用。

消融统计分析沿用原方案：严格配对、Wilcoxon 阈值 `p < 0.01`、saved progress paired bootstrap `B=10000`、seed `202608`。`no_policy_stop` 不报告 saved progress，只报告分数与排序指标。

### 2.4 ToolSandbox 动态介入

介入实验保持原方案三臂：

| 臂 | 方法 | 行为 |
| --- | --- | --- |
| `default` | `default` | 原生完整执行 |
| `stop` | `dynsteer_evaluate` | 在线评估并按策略停止 |
| `guided` | `dynsteer_evaluate_guided` | 非 fatal stop 转为隐藏 `USER -> AGENT` 引导；fatal minefield 仍硬停止 |

保留每次最多 2 次引导、同一 stage 只引导一次、引导信息白名单和不泄漏 expected state 的约束。`stop` 与 `guided` 配置开启 `collect_online_native_score=true`，三臂开启 `capture_agent_usage=true`。

### 2.5 Pilot

每类正式配置另建 pilot 配置，`repeats=1`：

```text
toolsandbox_pilot: 10 case
swebench_pro_pilot: 3 case，至少覆盖 2 个 repo
skillsbench_pilot: 3 task，至少覆盖 2 个 category
```

Pilot 只验证源码直连、执行环境、轨迹转换、native score 和输出 schema，不进入论文主表。

## 3. 执行模式与配置边界

### 3.1 两个启动脚本

统一实验入口保留两个版本：

```bash
./scripts/start_experiment.sh --exp data/experiments/swebench_pro_main.json
./scripts/start_experiment_no_docker.sh --exp data/experiments/swebench_pro_main.json
```

脚本在进入 Python 前强制注入：

```text
start_experiment.sh           DYNSTEER_BENCHMARK_EXECUTION=docker
start_experiment_no_docker.sh DYNSTEER_BENCHMARK_EXECUTION=host
```

不允许环境变量或实验 JSON 覆盖该值。直接运行 `main.py --exp ...` 时，SWE-bench Pro 与 SkillsBench harness 必须报错，提示需要通过任一启动脚本选择执行模式。

各 benchmark 的解释如下：

| Benchmark | Docker 版 | 非 Docker 版 |
| --- | --- | --- |
| ToolSandbox | 宿主直接运行源码；ToolSandbox 本身无任务容器，因此不强制检查 Docker | 完整支持主实验、消融实验和介入实验 |
| SWE-bench Pro | DynSTEER Agent 通过本地官方镜像执行 bash；native evaluator 加 `--use_local_docker` | 仅支持数据适配、轨迹转换、replay 和离线汇总；新的 default 执行与 native verifier 在启动前被拒绝 |
| SkillsBench | DynSTEER Agent 通过本地任务镜像执行 bash；直接执行 verifier/test.sh 并读取 reward | 仅支持数据适配、轨迹转换、replay 和离线汇总；新的 default 执行与 native verifier 在启动前被拒绝 |

这个边界是刻意保守的。SWE-bench Pro 的官方镜像和 SkillsBench 的 `environment/Dockerfile` 都是任务状态的一部分；在宿主机上伪造这些环境会引入依赖缺失、宿主机污染和分数失真。非 Docker 脚本必须在 matrix 展开前完成该能力检查，输出缺失的 benchmark、方法和已存在的 default 轨迹，不允许实验中途才因依赖失败退出。

正式主实验、消融实验和 pilot 中的 SWE-bench Pro、SkillsBench 新执行一律使用 Docker 版脚本。非 Docker 版用于 ToolSandbox 正式实验，以及 SWE/Skills 的 `--only_adapt`、离线 replay 和结果汇总。

Docker 版只在实验包含 SWE-bench Pro 或 SkillsBench 时运行 `docker info` 预检。ToolSandbox 配置不应因为没有任务 Docker 而被拒绝。

### 3.2 配置禁用字段

新增统一的执行中性配置校验，入口放在 `load_experiment_config()` 与 benchmark manifest/run config 读取路径中。以下字段在 DynSTEER 配置树中禁止出现：

```text
docker
use_docker
execution_mode
environment
sandbox
backend
agentcompass
```

例外说明：

1. `benchmark.json` 的 `tool_backend` 是 agent 工具面描述，允许存在。
2. SWE 的 `dockerhub_username` 是公开镜像命名空间，不是执行模式选择，允许存在。
3. SkillsBench 的 `condition: with-skills` 是 benchmark 条件，不是执行环境选择，允许存在。
4. 外部 `tasks/*/task.md` 中的 `sandbox` 字段属于 SkillsBench 原生任务定义，DynSTEER 校验不读取、不修改。

发现禁用字段时直接抛出配置错误，不做兼容转换。

## 4. 数据与配置文件

### 4.1 目录结构

新增：

```text
data/swebench_pro/
  benchmark.json
  run_configs.json
  source/
    data/test-00000-of-00001.parquet
    dockerfiles/
    eval_samples.jsonl
    .archive_digest

data/skillsbench/
  benchmark.json
  run_configs.json

data/experiments/
  toolsandbox_main.json
  swebench_pro_main.json
  skillsbench_main.json
  toolsandbox_ablation.json
  swebench_pro_ablation.json
  skillsbench_ablation.json
  toolsandbox_intervention.json
  toolsandbox_pilot.json
  swebench_pro_pilot.json
  skillsbench_pilot.json
```

运行产物继续使用现有 `runs/` 与 `results/`。SkillsBench 的 task mirror、容器元数据、verifier 输出和轨迹也必须显式放在 `runs/skillsbench/` 下，禁止落在用户 home 目录。

### 4.2 SWE-bench Pro manifest

```json
{
  "benchmark": "swebench_pro",
  "source_root": "../SWE-bench_Pro-os",
  "dataset_archive": "../../swebench_pro_data.zip",
  "tool_backend": "shell",
  "language": "en",
  "max_workers": 1,
  "dockerhub_username": "jefzda"
}
```

`dataset_archive` 相对 `data/swebench_pro` 解析。`dockerhub_username` 用于拼接官方 `sweap-images` 镜像。Docker 平台由本机 daemon 自动探测；仅当自动平台拉取失败时，允许用脚本参数 `--docker-platform linux/amd64` 显式覆盖，不写入实验配置。

`run_configs.json` 保持执行中性：

```json
[
  {
    "name": "source_direct",
    "scenarios": [],
    "max_tool_calls": 60,
    "command_timeout_seconds": 600,
    "model_temperature": 0.0
  }
]
```

### 4.3 SkillsBench manifest

```json
{
  "benchmark": "skillsbench",
  "source_root": "../skillsbench",
  "tool_backend": "shell",
  "language": "en",
  "max_workers": 1,
  "condition": "with-skills",
  "max_tool_calls": 60,
  "command_timeout_seconds": 300,
  "model_temperature": 0.0
}
```

主实验和消融实验均使用 `with-skills`。`without-skills` 属于 SkillsBench 自身的技能有效性对照，不进入 DynSTEER 跨 benchmark 主实验。

`run_configs.json`：

```json
[
  {
    "name": "source_direct",
    "scenarios": [],
    "condition": "with-skills",
    "max_tool_calls": 60,
    "command_timeout_seconds": 300,
    "model_temperature": 0.0
  }
]
```

### 4.4 实验配置命名与骨架

所有实验文件名必须是 `{benchmark}_{实验类型}.json`，`experiment_id` 与文件主名一致。禁止再使用 `cross_benchmark_main.json` 这类把多个 benchmark 混入同一配置的文件；每份配置只包含一个 benchmark，避免不同 benchmark 的依赖组和执行预检互相污染。

所有模型配置沿用 `toolsandbox_main_X.json` 的明文字段名。固定映射为：

| model_id | api_key | base_url |
| --- | --- | --- |
| `deepseek-v4-pro` | `sk-7c5fdd9f2d2a46289b20875bc6c723e8` | `https://api.deepseek.com` |
| `deepseek-v4-flash` | `sk-7c5fdd9f2d2a46289b20875bc6c723e8` | `https://api.deepseek.com` |
| `qwen-plus-2025-12-01` | `sk-c424db1a9f7d4535b73947d09ee8f42c` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `qwen3-max-2026-01-23` | `sk-c424db1a9f7d4535b73947d09ee8f42c` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

SWE-bench Pro 模型映射示例：

```json
{
  "model_id": "deepseek-v4-pro",
  "harness_metadata": {
    "client": {
      "model": "deepseek-v4-pro",
      "api_key": "sk-7c5fdd9f2d2a46289b20875bc6c723e8",
      "base_url": "https://api.deepseek.com"
    }
  }
}
```

SkillsBench 使用同一个 `harness_metadata.client` 结构；`model` 与 `model_id` 保持一致，不映射到源码 runner 的 target。ToolSandbox 继续使用现有 `agent_client` / `user_client` 结构，并保留四份历史配置中的明文 key。

配置骨架：

```json
{
  "experiment_id": "swebench_pro_main",
  "repeats": 3,
  "models": [],
  "methods": [
    "default",
    "dynsteer_replay",
    "dynsteer_replay_static"
  ],
  "judge_profiles": {
    "fixed-judge": {
      "provider": "openai_compatible",
      "model": "qwen3-max-2026-01-23",
      "api_key": "sk-c424db1a9f7d4535b73947d09ee8f42c",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }
  },
  "threshold_profiles": {
    "default": {}
  },
  "benchmarks": [
    {
      "benchmark": "swebench_pro",
      "data_root": "data/swebench_pro",
      "case_ids": [],
      "metadata": {
        "capture_agent_usage": true
      }
    }
  ]
}
```

上面的 `case_ids: []` 只是骨架占位；正式文件必须写入完整 case 列表。`toolsandbox_main.json` 需要同步补上 `dynsteer_replay_static`，并保留现有 `toolsandbox_main_X.json` 中的明文 key。

### 4.5 凭据配置

1. 实验配置是唯一模型凭据来源，必须显式包含非空 `api_key` 与 `base_url`。
2. 生成 `swebench_pro_*` 与 `skillsbench_*` 配置时，从上表复制凭据，不做环境变量间接层。
3. 现有 `toolsandbox_main_X.json`、`data/toolsandbox/run_configs.json` 和历史配置中的明文 key 全部保留，不轮换、不删除、不改名。
4. 日志与结果只输出模型名、base URL host 和命令摘要；轨迹中如意外出现 key，按长字符串截断规则处理，但这不改变配置文件内容。

## 5. SWE-bench Pro 源码直连设计

### 5.1 数据快照准备

新增 `scripts/prepare_swebench_pro.py`：

1. 读取 DynSTEER 根目录的 `swebench_pro_data.zip` SHA-256。
2. 使用标准库 `zipfile` 解压到 `data/swebench_pro/source/`，路径过滤必须拒绝绝对路径、`..` 和软链接。该 zip 提供 parquet 与 dockerfiles；不包含 `run_scripts`。
3. 写入 `data/swebench_pro/source/.archive_digest`；digest 一致且必需文件存在时跳过重建。
4. 用 `.venv-swebench-pro` 中的 `polars` 读取 `source/data/test-00000-of-00001.parquet`。
5. 生成源码 evaluator 兼容的 `source/eval_samples.jsonl`。

`eval_samples.jsonl` 的关键转换：

1. `instance_id` 作为 case id 与唯一索引。
2. `fail_to_pass`、`pass_to_pass`、`selected_test_files_to_run` 若为 JSON array，转换为 Python literal 字符串，因为 `swe_bench_pro_eval.py` 内部使用 `eval(...)`。
3. 保留 evaluator 必需字段：`instance_id`、`repo`、`base_commit`、`before_repo_set_cmd`、`fail_to_pass`、`pass_to_pass`、`selected_test_files_to_run`、`dockerhub_tag`。
4. 不把 `gold_patch`、测试期望或 hidden verifier 字段写入 TaskCase、generator view、milestone prompt 或引导消息。

数据准备失败、archive digest 不匹配、`../SWE-bench_Pro-os/run_scripts` 缺对应 run script/parser.py，或快照缺 Dockerfile 时显式失败，不做网络回退。准备脚本同时记录外部源码目录的 git commit；源码脚本与数据 digest 不匹配时必须重新生成配置。

### 5.2 Adapter

新增 `dynsteer/adapter/swebench_pro/`：

```text
__init__.py
adapter.py
harness.py
runtime.py
trajectory.py
evaluator.py
```

`adapter.py` 职责：

1. 读取 manifest 与 `eval_samples.jsonl`，首次缺快照时提示先运行准备脚本，不隐式下载。
2. `list_case_ids()` 返回全量 `instance_id`。
3. `adapt_task_case()` 构造公开 TaskCase：
   - `task_description` 来自 problem statement 等公开任务陈述；
   - `tool_schema` 只声明 `bash`；
   - `environment_schema` 只包含 repo、base commit、工作目录和镜像标签；
   - `initial_state` 只包含 agent 启动时可见状态；
   - metadata 记录 repo、分层字段、源码 evaluator 版本 digest。
4. `generator_task_view()` 只输出公开任务描述、repo 上下文、工具契约和轨迹证据，不输出 gold patch、pass/fail 测试列表或 verifier 脚本。

### 5.3 Agent 执行器

官方 `SWE-agent` 与 `mini-swe-agent` 子模块在当前源码中不可直接依赖；同时 Modal 在原设计中同时承担 Agent runtime 与 evaluator runtime。为满足源码直连目标，本方案把二者拆开：

1. Agent 决策循环由新增 `dynsteer/agent/openai_tool_agent.py` 统一实现，SWE 与 Skills 共用。
2. Agent 工具执行由 `runtime.py` 的本地 Docker executor 提供。
3. native evaluator 独立调用源码 `swe_bench_pro_eval.py --use_local_docker`。
4. 全链路不创建 Modal app、不读取 Modal token、不把 Modal 加入依赖组。

共享 Agent 的对外接口只有一个：

```python
run_tool_agent(
    *,
    task_prompt: str,
    tools: list[dict],
    client_config: OpenAIClientConfig,
    executor: ToolExecutor,
    max_tool_calls: int,
    temperature: float,
) -> ToolAgentResult
```

`openai_tool_agent.py` 直接使用 `openai.OpenAI(api_key=..., base_url=...)`，每轮把 assistant tool call、命令 stdout/stderr、exit code 和耗时写回 messages。该模块不感知 benchmark，也不 import 外部 benchmark 包。

`runtime.py` 提供 Docker executor：

| 模式 | 实现 |
| --- | --- |
| Docker | Docker SDK 拉取官方镜像，启动长驻容器，逐次 `exec_run(["/bin/bash", "-lc", command])` |

执行规则：

1. 镜像 URI 使用 `dockerhub_tag` 与 manifest 中的 `dockerhub_username` 拼接；URI 生成逻辑在 DynSTEER 内实现，不 import 外部 `helper_code`。
2. 容器工作目录为官方镜像中的 `/app` 或镜像默认 repo 目录；启动后先记录 `pwd`、`git status --short`、`git rev-parse HEAD`。
3. agent 每轮只能调用一个 `bash` tool；命令输出与 exit code 原样进入轨迹。
4. 达到 `max_tool_calls`、连续空命令、模型显式完成或命令超时后停止。
5. 最终执行 `git add -A` 与 `git diff --cached`，patch 写入本 case raw output。
6. 每个 repeat 使用新容器；agent 完成后立即销毁。
7. host 模式不创建 executor。若方法需要新 default 轨迹，harness 在 matrix 展开前抛出明确的 unsupported-profile 错误。

模型 usage 来自 OpenAI-compatible response 的 `usage` 字段，并归属到对应 assistant tool-call step；缺失时保持 `null`。命令墙钟时间归属到对应 tool result step，不用整个任务时间反推逐步时间。

### 5.4 Native evaluation

`evaluator.py` 以子进程调用外部源码中的 `swe_bench_pro_eval.py`，不把它 import 进 DynSTEER 进程：

```text
cwd = data/swebench_pro/source
<DynSTEER>/.venv-swebench-pro/bin/python <absolute-path-to>/SWE-bench_Pro-os/swe_bench_pro_eval.py
  --raw_sample_path eval_samples.jsonl
  --patch_path <case-specific-patches.json>
  --output_dir <case-native-output>
  --dockerhub_username jefzda
  --scripts_dir <absolute-path-to>/SWE-bench_Pro-os/run_scripts
  --num_workers 1
```

Docker 版额外加 `--use_local_docker`。host 版不调用 native evaluator；若对应 default 轨迹已存在，只允许 replay 与离线汇总。

resolved 语义：

```text
(FAIL_TO_PASS union PASS_TO_PASS) subset PASSED tests
```

分数转换：

1. evaluator 输出 JSON 中该 instance 为 `true`：`native_score=1.0`。
2. 输出为 `false` 且 case output JSON 存在、进程退出正常：`native_score=0.0`。
3. output JSON 缺失、本地容器基础设施失败、子进程崩溃、patch 无法读取：`native_score=null`，并记录 `native_evaluation_available=false` 与失败摘要。
4. 禁止把基础设施失败写成 `0.0`。

## 6. SkillsBench 源码直连设计

### 6.1 Adapter

新增 `dynsteer/adapter/skillsbench/`：

```text
__init__.py
adapter.py
harness.py
runtime.py
trajectory.py
```

`adapter.py` 职责：

1. 从 `../skillsbench/tasks/*/task.md` 读取任务。
2. 解析 YAML front matter 中的 `metadata.category`、`subcategory`、`task_type`、`difficulty`、agent timeout 和 verifier timeout。
3. `list_case_ids()` 使用任务目录名，默认排除源码 runner 同样排除的 `mhc-layer-impl`。
4. `adapt_task_case()` 使用 front matter 后的正文作为公开任务描述；`tool_schema` 标注 DynSTEER bash tool。
5. 不读取、不进入 generator view 的内容包括 oracle、verifier 内部实现、expected output 或 hidden skill 内容。
6. 只在 `.venv-skillsbench` 中解析任务元数据与调用 Docker SDK；不在 DynSTEER 主 venv 中 import BenchFlow。

YAML 解析依赖放在 Skills 可选依赖组中，不要求 ToolSandbox 实验安装。

### 6.2 源码读取边界与任务镜像

SkillsBench 不安装 BenchFlow，也不运行 `skillsbench_agentbeats.agent_under_test.py`。原因如下：

1. 该文件的核心是把 OpenCode、Claude Code 等 CLI 作为子进程拉起，不是一个可直接复用的纯 Python 工具调用循环。
2. 完整 import 还会带入 A2A、uvicorn 等依赖，重新引入此前 AgentCompass 式的环境问题。
3. 任务环境由每个任务的 `environment/Dockerfile` 定义，verifier 由 `verifier/test.sh` 定义；这三者必须与 Agent 决策解耦。

因此 DynSTEER 只读取源码数据面：

```text
tasks/<task-id>/task.md
tasks/<task-id>/environment/
tasks/<task-id>/verifier/test.sh
tasks/<task-id>/verifier/test_outputs.py
```

新增 `runtime.py` 的执行流程：

1. 把 `tasks/<task-id>` 复制到 `runs/skillsbench/<experiment_id>/.../task-mirror/`；源码目录保持只读。
2. 以 task mirror 的 `environment/` 为 build context，构建本地镜像 `dynsteer-skills:<source-digest>-<task-id>`。
3. 启动长驻容器，工作目录为 Dockerfile 声明的 `WORKDIR`。
4. `with-skills` 时把 mirror 中的 `environment/skills/` 复制到容器 `/root/.dynsteer/skills/`，并把目录清单、README 与脚本调用说明放入 Agent system prompt；`without-skills` 不复制、不放入。不读取 oracle。
5. 共享 `openai_tool_agent.py` 每轮最多执行一个 bash tool，命令通过 `docker exec` 在同一容器内执行。
6. Agent 结束后把 mirror 的 `verifier/` 复制到容器 `/verifier`，执行 `/bin/bash /verifier/test.sh`。
7. 读取容器内 `/logs/verifier/reward.txt`；`1` 转为 `native_score=1.0`，`0` 且 verifier 正常写文件转为 `0.0`。
8. reward 缺失、容器启动/构建失败、verifier 进程崩溃、日志无法取回时，`native_score=null` 并记录 infrastructure failure。

这个实现不调用 Daytona，不要求 Daytona 凭据，也不生成 OpenCode 配置。为了尽量贴近 OpenCode 的技能使用面，Agent 可先用 bash 查看 `/root/.dynsteer/skills/` 中的公开文件，但不能访问 oracle 与 verifier。

容器创建必须读取 task front matter 中的 `sandbox` 配置：`network_mode=none` 时传 `--network none`，否则使用默认 bridge；`cpus`、`memory_mb`、`storage_mb` 尽量映射为 Docker 资源限制；无法满足的项记录到 `container_metadata.json`，不静默忽略。

### 6.3 结果与轨迹

每次 case 输出：

```text
raw_trajectory.jsonl
agent_summary.json
verifier.stdout.log
verifier.stderr.log
verifier_result.json
container_metadata.json
```

轨迹由 DynSTEER Agent 直接生成，不再做官方 OpenCode 轨迹的格式猜测。规则如下：

1. assistant tool-call step 保存模型返回的 `usage`；真实缺失写 `null`。
2. bash tool-result step 保存 stdout、stderr、exit code 与命令墙钟时间。
3. verifier 不进入 Agent 轨迹，只进入 native evaluation artifact。
4. model request 失败、Docker exec 失败、镜像构建失败分别记录错误类型；请求失败可按现有模型重试策略重试，工具执行失败原样返回给 Agent。
5. Agent 达到 `max_tool_calls`、显式完成、连续空命令或任务超时后停止。

## 7. 代码修改总览

### 7.1 Adapter 注册与模型

修改：

1. `dynsteer/adapter/registry.py`
   - 重新显式导入新的 `SWEBenchProAdapter/Harness` 与 `SkillsBenchAdapter/Harness`。
   - 删除 AgentCompass import；当前文件因引用已删除模块而处于坏状态，必须修复。
2. `dynsteer/model.py`
   - 新增 `SWEBenchProSession` 与 `SkillsBenchSession`，字段显式声明，不使用泛型 dict 冒充 session。
3. `dynsteer/adapter/base.py`
   - 保持 `send_guidance()` 默认不支持；SWE 与 Skills 不覆盖。
4. 新增 `dynsteer/agent/openai_tool_agent.py`
   - 显式导入 `openai`，不使用懒加载。
   - 提供 SWE 与 Skills 共用的 tool-call loop、step limit、usage 归属和结构化异常。
   - 直接读取配置中的明文 `api_key` / `base_url`，不读取环境变量。

### 7.2 配置读取

修改：

1. `dynsteer/experiment/config.py`
   - 增加执行中性字段递归校验。
   - 保持每份配置允许一个 benchmark；多于一个 benchmark 时报错。
2. `dynsteer/harness/config.py`
   - 读取 SWE 的 `dataset_archive`、`dockerhub_username`。
   - 读取 Skills 的 `condition`。
   - `run_configs.json` 的 `scenarios: []` 表示 case ids 完全由 experiment spec 覆盖，不做默认抽样。
3. `dynsteer/utils.py`
   - OpenAI-compatible 客户端统一解析显式 `api_key` / `base_url`。
   - SWE、Skills、Judge 与 ToolSandbox 均不再要求 `api_key_env` / `base_url_env`。

### 7.3 脚本

修改 `scripts/start_experiment.sh`：

1. 删除 `agentcompass_environment.sh` source、`ensure_agentcompass_environment` 和 `experiment_uses_agentcompass`。
2. 删除 `--source` 参数；source root 只能来自各 benchmark manifest。
3. 删除 controller-in-container 的 `run_in_container()` 路径。
4. 强制 `DYNSTEER_BENCHMARK_EXECUTION=docker`。
5. 解析实验 JSON，确认其中只有一个 benchmark。
6. 按第 7.5 节选择并创建该 benchmark 专属 `.venv-*`。
7. 用专属 venv 的 Python 直接执行 `main.py`，禁止通过 `uv run` 重新解析默认环境。
8. SWE/Skills 存在时执行 Docker daemon、镜像、任务 Dockerfile 和 run script 预检，并在进入 `main.py` 前完成选中 case 的镜像 pull/build；ToolSandbox 不预检 Docker。

修改 `scripts/start_experiment_no_docker.sh`：

1. 同步删除 AgentCompass bootstrap 与 `--source`。
2. 强制 `DYNSTEER_BENCHMARK_EXECUTION=host`。
3. 使用与 Docker 版相同的 benchmark 专属 `.venv-*`，保证 ToolSandbox 完整实验和 SWE/Skills 离线处理使用一致依赖。
4. 不做本地 Docker 预检，也不读取 Modal、Daytona 或 OpenCode 凭据。
5. SWE/Skills 需要新 default 轨迹或 native verifier 时，在 Python matrix 展开前报错；已存在轨迹的 replay 与 `--only_adapt` 允许继续。

修改 `scripts/experiment_bootstrap.sh`：

1. 删除 `.venv-agentcompass` Python candidate。
2. 删除 `experiment_uses_agentcompass()`。
3. `assert_experiment_avoids_docker()` 改为校验执行中性禁用字段，而不是读取 `metadata.agentcompass`。
4. `prepare_benchmark_source()` 按 benchmark 校验：
   - ToolSandbox：包入口与 `tool_sandbox` 模块；
   - SWE：`swe_bench_pro_eval.py`、`helper_code`、源码 `run_scripts`、`dockerfiles`；
   - Skills：`tasks/*/task.md`、`environment/Dockerfile`、`verifier/test.sh`、`verifier/test_outputs.py`。
5. 不再强制所有源码目录都有 `pyproject.toml` 或 `setup.py`。

新增 `scripts/preflight_experiment.py`：

1. 使用实验 JSON 输出唯一 benchmark、方法、case ids 和源码 digest。
2. 校验明文模型配置非空，且 `base_url` 以 `https://` 开头。
3. Docker profile 校验 Docker daemon、SWE 官方镜像 URI、Skills 每个 selected task 的 Dockerfile 与 verifier 文件。
4. Docker 版启动脚本默认调用 `--prepare-runtime-images`，把 SWE 镜像 pull 和 Skills 镜像 build 前移到 matrix 展开前；仅显式传入 `--no-prepare-images` 时允许只检查不准备。
5. host profile 校验 ToolSandbox 完整能力；SWE/Skills 若请求新 native 执行，列出缺失的 default 轨迹并直接退出。
6. 所有错误在 `main.py` 启动前汇总输出，不在单个 case 运行中途才暴露。

重写 case 导出：

1. 新增 `scripts/get_cases.py`，删除根目录旧 `get_cases.py` 的 AgentCompass 实现。
2. SWE 从本地 zip 快照读取，缺快照时调用准备逻辑，不访问 Hugging Face。
3. Skills 直接扫描本地 `tasks/*/task.md`。
4. 输出全量 case id、分层分布、源码 digest，并支持 `--seed --size` 生成固化候选。
5. `scripts/get_cases.sh` 改为调用新脚本，不再准备 AgentCompass 环境。

清理旧入口：

1. `scripts/start.sh` 与 `scripts/start_no_docker.sh` 删除 AgentCompass 分支和 `--source`，并设置与统一入口一致的执行环境变量。
2. milestone reliability 两个脚本删除 SWE/Skills 对 AgentCompass 的依赖判断；跨 benchmark milestone 可靠性需要 SWE/Skills adapter 后再启用。
3. `scripts/exp_main.sh` 若不再代表当前入口，则只保留兼容最小参数并转到 `start_experiment*.sh`，不保留第二套环境安装逻辑。

### 7.4 依赖

修改 `pyproject.toml` 与 `uv.lock`：

```toml
[project]
dependencies = [
  "openai==1.17.0",
  "httpx==0.27.2",
  "anthropic==0.112.0",
  "docker>=7",
  "polars==0.20.31",
  "networkx>=3.2",
  "pyyaml>=6",
  "tqdm>=4.66.0",
  "scipy==1.13.1",
]

[dependency-groups]
swebench_pro = [
  "pandas>=2.2",
]
skillsbench = [
  "docker>=7",
]
```

说明：

1. `docker` 与 `pyyaml` 必须放在基础依赖：`registry.py` 顶层导入 SWE/Skills adapter，而 adapter runtime 与 Skills task parser 也必须顶层导入依赖；若只放可选组，ToolSandbox venv 会在 registry 导入阶段误报缺依赖。
2. SWE evaluator 额外使用 pandas；官方 requirements 中的 `datasets`、`huggingface_hub` 与 `modal` 对本地快照和本地 Docker 不需要。
3. Skills 只需要 YAML 解析与 docker SDK；不安装 BenchFlow、OpenCode、A2A 或 SkillsBench Python 依赖。
4. 不把外部项目加入 DynSTEER dependencies，不做 editable install。

### 7.5 benchmark 专属运行环境

启动顺序固定为：

1. 用 DynSTEER 根项目创建基础 `.venv`，只安装核心依赖，用于解析实验 JSON 与选择 benchmark。
2. 基础 venv 调用共享的选择函数，输出 benchmark、uv group、venv 路径和执行 profile。
3. 创建或同步对应 `.venv-*`。
4. 用 `.venv-*` 执行 import preflight。
5. preflight 全部通过后才执行 `main.py`。

每个实验配置只包含一个 benchmark，启动脚本按 benchmark 选择专属 venv：

| Benchmark | venv | uv group | 启动 Python |
| --- | --- | --- | --- |
| ToolSandbox | `.venv-toolsandbox` | `toolsandbox` | `benchmark_python` 解析 `bin/python` 或 `Scripts/python.exe` |
| SWE-bench Pro | `.venv-swebench-pro` | `swebench_pro` | `benchmark_python` 解析 `bin/python` 或 `Scripts/python.exe` |
| SkillsBench | `.venv-skillsbench` | `skillsbench` | `benchmark_python` 解析 `bin/python` 或 `Scripts/python.exe` |

`benchmark -> venv` 的映射必须集中在 `scripts/experiment_bootstrap.sh` 一个函数中；两个启动脚本和其他旧入口只调用该函数，不各自维护路径表。

创建命令由共享 bootstrap 函数执行，路径全部落在 DynSTEER 内：

```bash
cd "$PROJECT_ROOT"
UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv-$benchmark" \
UV_CACHE_DIR="$PROJECT_ROOT/.uv-cache" \
UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.uv-python" \
uv sync --frozen --no-dev --no-install-project --inexact --group "$group"
```

启动前必须通过同一个 venv 的 import preflight：

```bash
"$venv/bin/python" "$PROJECT_ROOT/scripts/preflight_experiment.py" \
  --exp "$experiment" --profile "$profile"
```

预检内容包括：Python 版本、DynSTEER 核心依赖、对应 group 依赖、外部 source root 存在性、ToolSandbox 可导入性、SWE 本地数据 digest、Skills task 文件、Docker profile 能力、host profile 能力。预检失败时脚本不调用 `main.py`。

运行期固定环境：

```text
UV_NO_SYNC=1
UV_CACHE_DIR=<DynSTEER>/.uv-cache
UV_PYTHON_INSTALL_DIR=<DynSTEER>/.uv-python
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=<DynSTEER>
```

`ensure_source_root()` 继续把 `../ToolSandbox` 或只读 benchmark 数据路径加入 `sys.path`。不在外部源码目录执行 `uv sync`，不创建 `../skillsbench/.venv`，不写入任何外部目录。

单元测试另建 `.venv-tests`：

```bash
cd "$PROJECT_ROOT"
UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv-tests" \
UV_CACHE_DIR="$PROJECT_ROOT/.uv-cache" \
UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.uv-python" \
uv sync --frozen --all-groups
"$PROJECT_ROOT/.venv-tests/bin/python" -m pytest
```

模型 API key 不进入 `.env`。`.env` 只允许保留与模型无关的本地工具配置；两个启动脚本不得因为缺少模型 key 环境变量而退出。

## 8. 逐文件代码修改实施细则

本节是第 7 节的落地规格。除特殊说明外，所有新增 Python 函数必须声明参数与返回值类型，核心函数使用中文 docstring；所有 import 均为显式顶层 import，禁止函数内临时 import 项目自身模块。外部 benchmark 目录只允许读取文件或以子进程执行入口脚本，不在外部目录创建任何文件。

### 8.1 AgentCompass 调用与适配器移除清单

| 位置 | 当前状态 | 目标修改 | 验收 |
| --- | --- | --- | --- |
| `dynsteer/adapter/agentcompass/` | 用户已删除整目录 | 不恢复。新增的 SWE/Skills adapter 不再继承或引用该目录任何类型 | `rg -n "adapter\\.agentcompass" dynsteer scripts data` 无命中 |
| `dynsteer/adapter/swebench_pro/` | 用户已删除旧 AgentCompass 版本 | 新建第 8.4 节的直连版本；不保留旧 `utils/*` 和 AgentCompass result mapper | 新模块可导入，未导入 `agentcompass` |
| `dynsteer/adapter/skillsbench/` | 用户已删除旧 AgentCompass 版本 | 新建第 8.5 节的直连版本；不安装或导入 BenchFlow/OpenCode/Daytona | 新模块可导入，外部源码目录无写入 |
| `dynsteer/adapter/registry.py` | 仍显式导入已删除模块，当前导入链损坏 | 保持 `SWEBenchProAdapter/Harness` 与 `SkillsBenchAdapter/Harness` 注册，但 import 指向新文件；不增加动态加载 | 在 `.venv-toolsandbox` 中 `import dynsteer.adapter.registry` 成功 |
| 根目录 `get_cases.py` | 顶层导入 `dynsteer.adapter.agentcompass.runtime` 并下载 Hugging Face/AgentCompass 数据 | 删除根目录实现，改为第 8.7 节 `scripts/get_cases.py`；只读取本地 zip 与 Skills tasks | 根目录无 `get_cases.py`；`scripts/get_cases.py --check` 不发起网络请求 |
| `scripts/experiment_bootstrap.sh` | 候选 Python 包含 `.venv-agentcompass`，并有 `experiment_uses_agentcompass()` | 删除 `.venv-agentcompass`、`experiment_uses_agentcompass()`、`metadata.agentcompass` 检查；新增第 8.7 节 benchmark 解析与 venv 映射函数 | `rg -n "agentcompass|AgentCompass" scripts/experiment_bootstrap.sh` 无命中 |
| `scripts/start_experiment.sh` | source `agentcompass_environment.sh`、支持 `--source`、存在 controller-in-container 路径 | 删除这些路径；改为宿主 controller、manifest source root、benchmark venv、Docker profile 注入 | 不再接受 `--source`，不存在 `run_in_container()` controller 路径 |
| `scripts/start_experiment_no_docker.sh` | source AgentCompass bootstrap 并支持 `--source` | 删除同名逻辑；使用共享 bootstrap 和 host profile | 不再接受 `--source`，不读取 Modal/Daytona/OpenCode 凭据 |
| `scripts/start.sh`、`scripts/start_no_docker.sh` | 兼容入口仍含 AgentCompass 分支 | 删除 `agentcompass_is_benchmark()`、`ensure_agentcompass_environment()` 和 `--source`；只包装统一入口并传递 `DYNSTEER_BENCHMARK_EXECUTION` | 两个旧入口不再准备独立 AgentCompass venv |
| `scripts/get_cases.sh` | 自动准备 `.venv-agentcompass` | 调用 `scripts/get_cases.py`，使用基础或 `.venv-tests` Python，不创建 AgentCompass 环境 | 无 `DYNSTEER_AGENTCOMPASS_*` 变量 |
| `scripts/start_milestone_reliability_no_docker.sh` | 对 SWE/Skills 标记 `requires_agentcompass` 并拒绝 | 删除该标记与 `.venv-agentcompass` 特判；ToolSandbox 使用 `.venv-toolsandbox`，SWE/Skills reliability 在 adapter 未支持前直接列为 unsupported benchmark capability | 不再出现 `.venv-agentcompass` |
| `README.md` | AgentCompass 安装、commit、`MODEL_API_KEY`、`MODEL_BASE_URL`、`--source` 说明 | 重写 benchmark 运行说明：三个源码目录、两个启动脚本、专属 venv、明文模型配置、Docker/host 能力边界 | 活跃 README 无 AgentCompass 运行指引 |
| `docs/apis/agentcompass.md` | 整份 API 属于旧调用方 | 删除该 API 文档，新增 `docs/apis/source_direct_benchmarks.md` 说明 SWE/Skills 公共接口 | `rg -n "AgentCompass" docs/apis README.md` 无命中 |
| `docs/apis/experiment.md`、`docs/apis/harness.md` | 引用 `cross_benchmark_main.json`、`--source` 和 AgentCompass Default harness | 改为单 benchmark 配置、脚本注入 profile、SWE/Skills 整任务 harness 协议 | API 文档与第 3 节能力矩阵一致 |
| `docs/cases_swe-bench-pro.json`、`docs/cases_skills-bench.json` | source 与 digest 来自 AgentCompass | 由新 `scripts/get_cases.py` 重生成，source 改为本地 archive/source digest | 两个报告不包含 AgentCompass 字段 |

清理边界固定如下：

1. 活跃代码、脚本、`data/`、`README.md`、`docs/apis/` 中不允许残留 AgentCompass 调用、配置键、环境变量和安装说明。
2. `docs/plans/` 与 `docs/plans/analysis/` 是历史方案存档，不做全文替换，避免误删或改写历史决策；历史文档中的 AgentCompass 字样不代表当前运行链路。
3. 现有及新增实验配置中的明文 `api_key`、`base_url` 全部保留，不进行脱敏、轮换或改成环境变量引用。
4. 不保留 AgentCompass 兼容层、fallback 或条件导入；配置校验发现 `agentcompass` 键直接报错。

### 8.2 新增文件结构与调用链

新增目录和文件：

```text
dynsteer/agent/
  __init__.py
  openai_tool_agent.py

dynsteer/runtime/
  __init__.py
  profile.py
  docker.py

dynsteer/adapter/swebench_pro/
  __init__.py
  adapter.py
  harness.py
  runtime.py
  trajectory.py
  evaluator.py

dynsteer/adapter/skillsbench/
  __init__.py
  adapter.py
  harness.py
  runtime.py
  trajectory.py

scripts/
  prepare_swebench_pro.py
  get_cases.py
  preflight_experiment.py

docs/apis/
  source_direct_benchmarks.md
```

最终调用链固定为：

```text
start_experiment*.sh
  -> experiment_bootstrap.sh：解析唯一 benchmark，创建专属 venv
  -> scripts/preflight_experiment.py：配置、源码、依赖、Docker/host 能力与镜像预检
  -> main.py
  -> registry/get_harness()
  -> SWE/Skills Harness.start_case()
  -> runtime：创建任务容器
  -> openai_tool_agent.run_tool_agent()
  -> Harness.advance_case() 一次返回完整轨迹
  -> SWE evaluator 子进程 / Skills verifier
  -> Harness.default_result_from_session()
  -> outputs.write_default_case_outputs()
```

`__init__.py` 只做显式 re-export，不使用 `__getattr__`、`importlib` 或字符串映射。例如 `dynsteer/agent/__init__.py` 顶层导出 `OpenAIClientConfig`、`ToolAgentResult`、`run_tool_agent`。

### 8.3 共享 Agent 与执行 profile

#### `dynsteer/runtime/profile.py`

新增常量与函数：

```python
EXECUTION_PROFILES = frozenset({"docker", "host"})

def execution_profile_from_environment() -> str:
    """读取启动脚本注入的执行 profile。"""

def require_native_execution_profile() -> str:
    """读取执行 profile，并拒绝缺失或非法值。"""
```

实现规则：

1. 只读取 `DYNSTEER_BENCHMARK_EXECUTION`，不允许实验 JSON 覆盖。
2. 取值只允许 `docker`、`host`；其他值或未设置均抛出 `RuntimeError`，错误信息要求通过两个启动脚本进入。
3. 不读取旧的 `DYNSTEER_NO_DOCKER`、`DYNSTEER_AGENTCOMPASS_*`、Modal、Daytona 或 OpenCode 环境变量。
4. `main.py` 读取实验配置后，若唯一 benchmark 是 SWE-bench Pro 或 SkillsBench，则在展开实验矩阵前调用 `require_native_execution_profile()`；ToolSandbox 保持可直接运行源码方式不受影响。`preflight_experiment.py` 对所有 benchmark 按脚本参数校验同一值。

#### `dynsteer/agent/openai_tool_agent.py`

新增数据类型：

```python
@dataclass(frozen=True)
class OpenAIClientConfig:
    model: str
    api_key: str
    base_url: str
    timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0

@dataclass(frozen=True)
class ToolExecutionResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    latency_ms: int
    timed_out: bool = False
    error_type: str | None = None

class ToolExecutor(Protocol):
    def execute(self, command: str, timeout_seconds: int | None = None) -> ToolExecutionResult:
        """执行一条 bash 命令并返回原样结果。"""

@dataclass
class ToolAgentResult:
    steps: list[TrajectoryStep]
    stop_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    error_type: str | None = None
    error_message: str | None = None
```

唯一公开执行函数：

```python
def run_tool_agent(
    *,
    task_prompt: str,
    system_prompt: str,
    client_config: OpenAIClientConfig,
    executor: ToolExecutor,
    max_tool_calls: int,
    temperature: float,
) -> ToolAgentResult:
    """在 DynSTEER 内执行 OpenAI-compatible bash tool agent。"""
```

实现要求：

1. 顶层 `from openai import OpenAI`；`OpenAI(api_key=client_config.api_key, base_url=client_config.base_url, timeout=...)` 直接使用明文配置。
2. 请求使用 `client.chat.completions.create(...)`，`tools` 只包含：

    ```python
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run one shell command in the task container",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
    ```

3. 每轮强制 `tool_choice="auto"`；模型返回多个 tool call 时记录 `agent_protocol_error` 并停止，不猜测优先级。
4. 每次模型调用产生一个 `Actor.AGENT -> Actor.ENVIRONMENT` 的 `TOOL_CALL` step；命令执行产生对应 `Actor.ENVIRONMENT -> Actor.AGENT` 的 `TOOL_RESULT` step。两者 `raw["openai_tool_call_id"]` 相同，满足现有 `AgentStepTracker` 闭包协议。
5. assistant step 的 `cost.tokens` 来自 response `usage.total_tokens`，并在 raw 中保存 prompt/completion/total 三个字段的原始整数或 `null`；tool result step 的 `cost.latency_ms` 来自命令墙钟时间。
6. 模型最终无 tool call 时追加 `AGENT -> USER` 的 `FINAL` step 并停止；停止原因分别为 `model_finished`、`max_tool_calls`、`empty_command`、`agent_protocol_error`、`model_request_failed`。
7. OpenAI SDK 异常按 `client_config.max_retries` 与指数退避重试；重试耗尽不抛出导致整 case 产物缺失，而是返回失败 result，并生成 `EventType.ERROR` step。
8. `task_prompt`、`system_prompt`、命令与输出写入轨迹；`api_key`、`base_url`、Authorization header 不写入轨迹或 raw summary。
9. stdout/stderr 按 UTF-8 `errors="replace"` 解码，单字段截断上限为 64 KiB，截断时在 raw 中记录原始字节数和 `truncated=true`。
10. 该模块不 import Docker、SWE、Skills 或任何 benchmark 类型，测试用 fake executor 和 monkeypatched OpenAI client。

#### `dynsteer/runtime/docker.py`

新增共享 Docker 边界：

```python
@dataclass(frozen=True)
class DockerRunSpec:
    image: str
    working_dir: str
    command_timeout_seconds: int
    network_mode: str = "bridge"
    nano_cpus: int | None = None
    memory_limit: str | None = None
    platform: str | None = None

class LocalDockerExecutor:
    def __init__(self, spec: DockerRunSpec, container_name: str): ...
    def start(self) -> None: ...
    def execute(self, command: str, timeout_seconds: int | None = None) -> ToolExecutionResult: ...
    def put_directory(self, source: Path, target: str) -> None: ...
    def read_text_file(self, target: str) -> str: ...
    def collect_logs(self) -> str: ...
    def stop(self) -> None: ...
```

实现规则：

1. 顶层 `import docker`，因为 `registry.py` 会在所有 benchmark venv 中导入新 adapter；`docker>=7` 必须属于基础依赖。
2. `start()` 使用 `docker.from_env().containers.run(..., detach=True, tty=False, stdin_open=False, auto_remove=False, name=container_name, **...)`；容器名包含 experiment/case/repeat 的安全化字符串。
3. `execute()` 将命令包装为 `timeout --signal=KILL <seconds> /bin/bash -lc <quoted-command>` 后调用 `container.exec_run(..., workdir=..., demux=True)`；退出码 124 记为 `timed_out=true`，137 保留给 OOM/kill 分类。未显式传 timeout 时使用 `DockerRunSpec.command_timeout_seconds`，Skills verifier 可传 task front matter 中的 verifier timeout。Docker client HTTP timeout 设置为命令 timeout 加 30 秒，避免把容器执行超时误判为 SDK 网络超时。
4. `put_directory()` 用标准库 `tarfile` 在内存中构造 POSIX path archive，再调用 `container.put_archive()`；拒绝 symlink、绝对路径和 `..`，避免把 oracle 或外部路径带进容器。
5. `stop()` 优先 `container.remove(force=True, v=True)`；记录但不吞掉最终 remove 失败，保证资源泄漏可见。
6. 所有容器、镜像 tag、日志和 tar 数据都只指向 DynSTEER `runs/` 或 Docker daemon，不 bind mount 外部源码目录。

### 8.4 SWE-bench Pro 逐文件设计

#### `scripts/prepare_swebench_pro.py`

CLI：

```text
python scripts/prepare_swebench_pro.py
  --project-root .
  --data-root data/swebench_pro
  --source-root ../SWE-bench_Pro-os
  --archive ../../swebench_pro_data.zip
  --force
```

函数结构：

```python
def main() -> int: ...
def prepare_snapshot(project_root: Path, data_root: Path, source_root: Path, archive: Path, force: bool) -> None: ...
def _archive_digest(archive: Path) -> str: ...
def _extract_archive(archive: Path, destination: Path) -> None: ...
def _safe_zip_destination(destination: Path, member_name: str) -> Path: ...
def _write_zip_member(archive: Path, member: ZipInfo, destination: Path) -> None: ...
def _write_eval_samples(data_root: Path) -> None: ...
def _eval_sample(row: Mapping[str, object]) -> JsonObject: ...
def _python_literal(value: object) -> str: ...
def _source_commit(source_root: Path) -> str: ...
def _validate_source(source_root: Path, data_root: Path, sample_ids: list[str]) -> None: ...
```

具体规则：

1. `.archive_digest` 写入 `{"sha256": ..., "source_git_commit": ..., "created_at": ...}`；digest 与 commit 均一致且必需文件齐全时跳过，任何一项变化都重建。
2. zip 成员路径先 `PurePosixPath` 归一化，拒绝绝对路径、`..` 和 symlink；Windows 下按 `ZipInfo.external_attr` 恢复可执行位。
3. 用基础依赖中的 `polars` 读取 `source/data/test-00000-of-00001.parquet`。
4. `instance_id` 必须唯一且均以 `instance_` 开头；`repo`、`base_commit`、`dockerhub_tag` 非空。
5. `fail_to_pass`、`pass_to_pass`、`selected_test_files_to_run` 若是 list，用 `repr(list(value))` 写成 Python literal 字符串；官方 evaluator 的 `eval()` 可直接解析。
6. `eval_samples.jsonl` 每行只保留 evaluator 必需字段；`gold_patch` 不落出到该文件之外的任何 DynSTEER 派生产物。
7. 对每个 case 校验 `source_root/run_scripts/<instance_id>/run_script.sh` 与 `parser.py` 存在，且 zip 解出的 base/instance Dockerfile 目录存在。
8. 本脚本不做网络下载；缺 zip、digest 不匹配、路径不安全或文件缺失时返回非零并列出缺失项。

#### `dynsteer/adapter/swebench_pro/adapter.py`

新增：

```python
@dataclass(frozen=True)
class SWEBenchProSample:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    dockerhub_tag: str
    metadata: JsonObject

class SWEBenchProAdapter(BaseBenchmarkAdapter):
    benchmark = "swebench_pro"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]: ...
    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase: ...
    def generator_task_view(self, config: HarnessRunConfig, task_case: TaskCase, case_id: str) -> GeneratorTaskView: ...
```

主要内部函数：

```python
def _samples(config: HarnessRunConfig) -> dict[str, SWEBenchProSample]: ...
def _sample(config: HarnessRunConfig, case_id: str) -> SWEBenchProSample: ...
def _source_root(config: HarnessRunConfig) -> Path: ...
def _public_problem_statement(row: JsonObject) -> str: ...
```

实现规则：

1. `_samples()` 读取 `data_root/source/eval_samples.jsonl`；缺失时错误信息固定提示先执行 `scripts/prepare_swebench_pro.py`。
2. `TaskCase.task_id="swebench_pro::<instance_id>"`，`case_id=instance_id`。
3. `task_description` 只拼接 problem statement、公开 requirements/interface 字段和 repo/base commit 描述；禁止包含 `gold_patch`、fail/pass 测试名、verifier 脚本或 expected patch。
4. `tool_schema={"tools":[{"name":"bash","input":{"command":"string"}}]}`。
5. `environment_schema` 只含 `repo`、`base_commit`、`dockerhub_tag`、`image_namespace`、`working_directory_probe`。
6. `metadata` 含 `repo`、`archive_digest`、`source_git_commit`、`eval_sample_digest`，用于配置固化与结果索引。
7. `generator_task_view()` 只传公开任务描述、环境声明、工具契约和 TaskCase 的公开 metadata；测试集合与 gold patch 不进入 milestone generator。

#### `dynsteer/adapter/swebench_pro/runtime.py`

新增：

```python
class SWEBenchProRuntime:
    def __init__(
        self,
        *,
        sample: SWEBenchProSample,
        image_namespace: str,
        run_dir: Path,
        command_timeout_seconds: int,
        platform: str | None,
    ): ...

    def start(self) -> None: ...
    def probe_environment(self) -> JsonObject: ...
    def execute(self, command: str) -> ToolExecutionResult: ...
    def collect_patch(self) -> str: ...
    def close(self) -> None: ...
```

内部函数：

```python
def swe_image_uri(instance_id: str, namespace: str, repo: str) -> str: ...
def _repository_root(executor: LocalDockerExecutor) -> str: ...
def _patch_summary(patch: str) -> JsonObject: ...
def _task_prompt(sample: SWEBenchProSample) -> str: ...
```

实现规则：

1. `swe_image_uri()` 在 DynSTEER 内复刻官方 `helper_code/image_uri.py` 的逻辑，不 import `helper_code`：`element-hq/element-web` 的 `-vnan` 特例必须逐行覆盖；其他 tag 去掉 `-vnan`；tag 超过 128 字符时截断。
2. `start()` 拉取并启动官方 `jefzda/sweap-images:<tag>` 镜像；`platform` 只来自启动脚本传入的 `DYNSTEER_DOCKER_PLATFORM`，不写入 JSON。
3. 首条探测命令固定为 `printf '%s\n' "$PWD"; git rev-parse --show-toplevel; git rev-parse HEAD; git status --short`。`working_dir` 取 `git rev-parse --show-toplevel`；失败时记录 `image_probe_failed` 并作为 infrastructure failure 返回，不在宿主机伪造工作区。
4. probe 必须同时满足：仓库根目录存在、`HEAD == sample.base_commit`、`git status --short` 为空；任一条件不满足均返回 `image_state_invalid`，不得执行 Agent，也不得把镜像初始脏状态混入 patch。
5. Agent 执行期间不 bind mount DynSTEER 或 SWE 源码目录；每条命令都在同一长驻官方镜像容器执行。
6. `collect_patch()` 依次执行 `git add -A` 与 `git diff --cached --binary`，patch 写入 `raw/agent.patch`；无修改时保存空字符串并继续 native evaluation。
7. 每个 repeat 创建新容器；`close()` 总是执行，Agent 成功、失败或超时后均销毁容器。

`_task_prompt()` 只包含公开 problem statement、repo/base commit、工作目录、bash 工具契约和结束方式；结束方式固定为“完成修改后直接返回简短总结，不再调用 bash”。不得提示测试名单、gold patch 或 verifier。

#### `dynsteer/adapter/swebench_pro/trajectory.py`

新增：

```python
def agent_trajectory_steps(
    *,
    task_id: str,
    agent_result: ToolAgentResult,
) -> list[TrajectoryStep]: ...

def patch_summary(patch: str) -> JsonObject: ...

def usage_metrics(agent_result: ToolAgentResult) -> JsonObject: ...
```

实现规则：

1. `agent_trajectory_steps()` 直接返回共享 Agent 生成的连续递增 step，不重建第二套轨迹格式。
2. `patch_summary()` 输出字节数、行数、非空 diff 布尔值、SHA-256 和文件变更计数，不把完整 patch 复制进 generator view。
3. `usage_metrics()` 输出 `prompt_tokens`、`completion_tokens`、`total_tokens`、`tool_call_count`、`agent_wall_clock_ms`、`stop_reason`；模型缺失 usage 保持 `null`。

#### `dynsteer/adapter/swebench_pro/evaluator.py`

新增：

```python
@dataclass(frozen=True)
class NativeEvaluationResult:
    score: float | None
    resolved: bool | None
    available: bool
    output_path: Path | None
    returncode: int | None
    failure_type: str | None
    summary: JsonObject

def evaluate_patch(
    *,
    source_root: Path,
    data_root: Path,
    instance_id: str,
    patch: str,
    output_dir: Path,
    image_namespace: str,
    execution_profile: str,
    docker_platform: str | None,
) -> NativeEvaluationResult: ...
```

子进程参数固定：

```text
cwd = data_root/source
python = sys.executable
script = source_root/swe_bench_pro_eval.py
--raw_sample_path eval_samples.jsonl
--patch_path <output_dir>/patch.json
--output_dir <output_dir>/native
--dockerhub_username jefzda
--scripts_dir <source_root>/run_scripts
--num_workers 1
--use_local_docker   # 仅 docker profile 追加
```

结果判定：

1. 先检查 `native/<instance_id>/_output.json` 是否存在且是合法 JSON；这是官方 parser 完整产出的必要条件。
2. `eval_results.json` 中该 case 为 `true` 且 output JSON 存在：`score=1.0`。
3. 为 `false`、returncode 为 0、output JSON 与 stdout/stderr 均存在：`score=0.0`。
4. output JSON 缺失、进程非零退出、patch 文件无法写入或本地 Docker 异常：`score=null`、`available=false`，并按 `evaluator_process_failed`、`native_output_missing`、`docker_infrastructure_failed` 分类。
5. 官方 evaluator 会把部分异常写成 false；没有 `_output.json` 时不得把 false 当模型失败。
6. 保存 stdout/stderr、`eval_results.json`、case output JSON 的相对路径和进程命令摘要；命令摘要不含 key。

#### `dynsteer/adapter/swebench_pro/harness.py`

新增：

```python
class SWEBenchProHarness(BaseBenchmarkHarness):
    benchmark = "swebench_pro"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]: ...
    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> SWEBenchProSession: ...
    def advance_case(self, session: object) -> HarnessAdvanceResult: ...
    def metrics_from_session(self, session: object) -> JsonObject: ...
    def initial_state_from_session(self, session: object) -> JsonObject | None: ...
    def final_state_from_session(self, session: object) -> JsonObject | None: ...
    def raw_summary_from_session(self, session: object) -> JsonObject: ...
    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult: ...
    def teardown_case(self, session: object) -> None: ...
```

`dynsteer/model.py` 新增显式 session。为避免 model 与 adapter 循环依赖，`OpenAIClientConfig` 来自 `dynsteer.agent`；sample/runtime/native result 使用 `from __future__ import annotations` 的字符串类型注释，不在 model 顶层导入 adapter：

```python
@dataclass
class SWEBenchProSession:
    case_id: str
    task_id: str
    sample: SWEBenchProSample
    client_config: OpenAIClientConfig
    runtime: SWEBenchProRuntime | None
    agent_result: ToolAgentResult | None
    probe: JsonObject
    patch: str | None
    native_result: NativeEvaluationResult | None
    stop_reason: str | None
    finished: bool = False
```

执行顺序：

1. `start_case()` 调用 `require_native_execution_profile()`；`host` 直接抛出 `UnsupportedBenchmarkProfile`，错误说明 host 只能适配、replay 与离线汇总。
2. 校验 `config.metadata.client`，读取 `max_tool_calls`、`command_timeout_seconds`、`model_temperature`、`dockerhub_username`。
3. system prompt 固定说明：每轮最多一个 bash tool、命令输出会原样返回、不得破坏 git 基线、任务完成后返回简短总结。task prompt 由 `_task_prompt(sample)` 生成。
4. 创建 runtime、probe、session；probe 失败也返回 session，并在 `advance_case()` 中生成 error step，保证 raw summary 与 default 结果可落盘。
5. `advance_case()` 第一次调用执行完整 `run_tool_agent()` 与 `collect_patch()`，返回全部 steps 和 `continue_running=False`；第二次调用返回空结果。
6. `default_result_from_session()` 执行 native evaluator，映射 score；基础设施失败为 `BenchmarkDefaultResult(score=None, ...)`，不把失败伪装成 0。
7. `teardown_case()` 只负责容器清理；native evaluator 输出已保存在 `raw/native/` 下。

### 8.5 SkillsBench 逐文件设计

#### `dynsteer/adapter/skillsbench/adapter.py`

新增：

```python
@dataclass(frozen=True)
class SkillsBenchTask:
    case_id: str
    description: str
    category: str
    subcategory: str | None
    task_types: tuple[str, ...]
    difficulty: str | None
    agent_timeout_seconds: float
    verifier_timeout_seconds: float
    sandbox: JsonObject
    source_digest: str

class SkillsBenchAdapter(BaseBenchmarkAdapter):
    benchmark = "skillsbench"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]: ...
    def adapt_task_case(self, config: HarnessRunConfig, case_id: str) -> TaskCase: ...
    def generator_task_view(self, config: HarnessRunConfig, task_case: TaskCase, case_id: str) -> GeneratorTaskView: ...
```

内部函数：

```python
def _tasks(config: HarnessRunConfig) -> dict[str, SkillsBenchTask]: ...
def _parse_task(task_dir: Path) -> SkillsBenchTask: ...
def _split_front_matter(text: str) -> tuple[JsonObject, str]: ...
def _directory_digest(task_dir: Path) -> str: ...
```

实现规则：

1. 直接读取 `../skillsbench/tasks/*/task.md`，顶层 `import yaml`，不 import `skillsbench_agentbeats.*`。
2. `yaml.safe_load()` 只接受 mapping；front matter 后正文作为公开任务描述。
3. `case_id=task_dir.name`，默认排除 `mhc-layer-impl`；任务目录名必须可安全用作文件名。
4. `_directory_digest()` 对 `task.md`、`environment/`、`verifier/` 的相对路径、mode 和内容做稳定 SHA-256，用于镜像 tag 与配置固化；不修改 oracle。
5. `TaskCase.task_id="skillsbench::<case_id>"`；`tool_schema` 声明的工具名为 `bash`，与共享 Agent 的 OpenAI function name 一致。
6. metadata 保存 category/subcategory/task_type/difficulty/timeouts/sandbox/resource digest；不保存 oracle 正文、expected output 或 verifier 源码。
7. `generator_task_view()` 只包含任务正文、公开分类、工具契约与技能目录清单；oracle 与 verifier 不进入。
8. 不安装 BenchFlow、OpenCode、A2A、Daytona SDK；Remote 模式、Daytona worker 与 SkillsBench agent CLI 都不是本实现依赖。

#### `dynsteer/adapter/skillsbench/runtime.py`

新增：

```python
class SkillsBenchRuntime:
    def __init__(
        self,
        *,
        task: SkillsBenchTask,
        source_task_dir: Path,
        mirror_dir: Path,
        condition: str,
        command_timeout_seconds: int,
        platform: str | None,
    ): ...

    def start(self) -> None: ...
    def task_prompt(self) -> str: ...
    def system_prompt(self) -> str: ...
    def execute(self, command: str) -> ToolExecutionResult: ...
    def run_verifier(self) -> ToolExecutionResult: ...
    def reward(self) -> float | None: ...
    def metadata(self) -> JsonObject: ...
    def close(self) -> None: ...
```

内部函数：

```python
def mirror_task(source: Path, destination: Path) -> None: ...
def build_task_image(mirror: Path, tag: str, timeout_seconds: int, platform: str | None) -> None: ...
def _docker_resources(sandbox: JsonObject) -> DockerRunSpec: ...
def _skill_manifest(mirror: Path) -> JsonObject: ...
def _network_mode(value: object) -> str: ...
```

执行规则：

1. `mirror_task()` 把 `task.md`、`environment/`、`verifier/` 复制到本 case 的 `raw/task-mirror/`；复制前检查 resolved path 必须仍在 mirror 内，拒绝 symlink 与硬链接。源码目录保持只读。
2. 镜像名固定 `dynsteer-skills:<source_digest前16位>-<case_id安全化>`，build context 是 `mirror/environment/`，build 输出写入 `raw/docker-build.log`。
3. `DockerRunSpec.working_dir` 默认 `"/root"`；启动后执行 `pwd`，若失败标记 infrastructure failure，不在宿主机执行任务命令。
4. `condition="with-skills"` 时，把 mirror 中 `environment/skills/` 复制到容器 `/root/.dynsteer/skills/`，并把文件清单、每个 `SKILL.md` 的相对路径与摘要写入 system prompt；`without-skills` 不复制、不提示。`environment/groundtruth/` 永远不复制进容器。
5. sandbox 映射：`network_mode: none/no-network -> none`，其余为 `bridge`；`cpus -> nano_cpus`，`memory_mb -> mem_limit`，`gpus != 0` 或要求非 Linux OS 时返回 infrastructure failure；`storage_mb` 仅写入 metadata 并记录 daemon 是否支持，不静默声称已限制。
6. Agent 结束后复制 `mirror/verifier/` 到 `/verifier`，创建 `/logs/verifier`，执行 `/bin/bash /verifier/test.sh`；stdout/stderr 与退出码写入 raw。
7. `reward()` 读取容器 `/logs/verifier/reward.txt`，仅接受整行 `0` 或 `1`；分别映射 `0.0/1.0`，其他值或缺失为 `None`。
8. verifier 不进入 Agent 轨迹；它是 native evaluation 阶段的独立工具调用。

#### `dynsteer/adapter/skillsbench/trajectory.py`

新增：

```python
def agent_trajectory_steps(
    *,
    task_id: str,
    agent_result: ToolAgentResult,
) -> list[TrajectoryStep]: ...

def verifier_summary(result: ToolExecutionResult, reward: float | None) -> JsonObject: ...

def usage_metrics(agent_result: ToolAgentResult) -> JsonObject: ...
```

与 SWE 相同，轨迹直接使用共享 Agent steps。`verifier_summary()` 只输出 exit code、reward、stdout/stderr SHA-256 与长度，不复制 verifier 源码。

#### `dynsteer/adapter/skillsbench/harness.py`

新增：

```python
class SkillsBenchHarness(BaseBenchmarkHarness):
    benchmark = "skillsbench"

    def list_case_ids(self, config: HarnessRunConfig) -> list[str]: ...
    def start_case(self, config: HarnessRunConfig, case_id: str, raw_output_dir: Path) -> SkillsBenchSession: ...
    def advance_case(self, session: object) -> HarnessAdvanceResult: ...
    def metrics_from_session(self, session: object) -> JsonObject: ...
    def initial_state_from_session(self, session: object) -> JsonObject | None: ...
    def final_state_from_session(self, session: object) -> JsonObject | None: ...
    def raw_summary_from_session(self, session: object) -> JsonObject: ...
    def default_result_from_session(self, session: object) -> BenchmarkDefaultResult: ...
    def teardown_case(self, session: object) -> None: ...
```

`dynsteer/model.py` 新增：

```python
@dataclass
class SkillsBenchSession:
    case_id: str
    task_id: str
    task: SkillsBenchTask
    client_config: OpenAIClientConfig
    runtime: SkillsBenchRuntime | None
    agent_result: ToolAgentResult | None
    verifier_result: ToolExecutionResult | None
    reward: float | None
    stop_reason: str | None
    finished: bool = False
```

执行顺序：

1. `start_case()` 在 host profile 下直接抛出 `UnsupportedBenchmarkProfile`；docker profile 下镜像 build 失败仍返回带错误状态的 session，确保产物可审计。
2. `advance_case()` 第一次调用执行完整共享 Agent；结束后调用 `run_verifier()` 与 `reward()`，返回全部 Agent steps 和 `continue_running=False`。
3. `default_result_from_session()`：reward 为 0/1 时 score 对应 0.0/1.0；reward 缺失、容器构建失败、verifier 无法运行时 score 为 `None`。
4. `final_state_from_session()` 返回任务产物摘要：skill manifest、verifier reward、容器 metadata 和 Agent stop reason，不读取 groundtruth。
5. `teardown_case()` 总是销毁任务容器；镜像保留在本机供重复 repeat 复用。

### 8.6 既有配置与模型代码修改

#### `dynsteer/experiment/config.py`

新增常量：

```python
FORBIDDEN_EXECUTION_FIELDS = frozenset({
    "docker", "use_docker", "execution_mode", "environment",
    "sandbox", "backend", "agentcompass",
})
```

修改点：

1. `load_experiment_config()` 读取 JSON 后先调用 `_assert_execution_neutral(data, "实验配置")`，再写入 `_config_path`。
2. 新增 `_assert_execution_neutral(value: object, path: str) -> None`，递归遍历 dict/list；命中禁用键即抛出 `ValueError`。
3. 新增 `_assert_single_benchmark(config: Mapping[str, Any]) -> None`：`benchmarks` 必须是长度 1 的对象数组，重复或空项直接报错。
4. 新增 `_assert_experiment_file_name(config_path: Path, experiment_id: str) -> None`：文件主名必须等于 experiment_id；命名必须保持 `{benchmark}_{实验类型}`，实际允许类型为 `main/ablation/intervention/pilot`。
5. `_judge_config()` 删除全部 `api_key_env/base_url_env` 逻辑，改为：`provider/model/api_key/base_url` 必须显式非空；出现 env 键直接报错；`base_url` 必须以 `https://` 开头。
6. 对 SWE/Skills model spec，展开前校验 `harness_metadata.client.model == model_id`，且 client 中 `api_key/base_url` 非空。ToolSandbox 继续校验 `agent_client/user_client`。

#### `dynsteer/harness/config.py`

1. `_RUN_CONFIG_CONTROL_FIELDS` 增加 `client`；`_CLIENT_CONFIG_KEYS=("agent_client", "user_client", "client")`，三者都经 `normalize_client_config()`。
2. `load_benchmark_manifest_metadata()` 按 benchmark 增加校验：SWE 必须有 `dataset_archive`、`dockerhub_username`；Skills 必须有 `condition`，值只允许 `with-skills/without-skills`；均写入 metadata 并拒绝 AgentCompass 键。
3. `load_harness_run_configs()` 为 SWE/Skills 校验 `max_tool_calls > 0`、`command_timeout_seconds > 0`、`0 <= model_temperature <= 2`，并写入 metadata；这些值来自 run config，不属于执行环境选择。
4. `_case_ids_from_spec()` 保持 `scenarios: [] -> None`，实验 spec 的完整 `case_ids` 会覆盖它，不做默认抽样。

#### `dynsteer/utils.py` 与 `dynsteer/llm/factory.py`

1. `normalize_client_config()` 删除 `api_key_env/base_url_env` 允许字段；client 对象一旦提供，必须同时含非空 `api_key` 与 `base_url`。
2. `build_llm_from_config()` 删除环境变量 fallback；provider 存在时直接要求显式 `api_key/base_url`，并拒绝 env 键。
3. `build_llm_from_env()` 仅保留给“完全没有 provider 配置”的旧静态 stage-goal 边界；新增实验配置必须配置 `milestone_generation.generator`，不得依赖它。
4. 日志与 `json_safe()` 输出增加统一 key 隐藏策略：匹配 `sk-` 或已知 key 字符串时显示 `sha256:<前12位>`；配置文件本身不改变。

#### `dynsteer/adapter/utils.py`

1. 删除 `DYNSTEER_BENCHMARK_SOURCE_ROOT` 覆盖逻辑；source root 只来自 benchmark manifest。
2. 新增 `resolve_source_root(data_root: Path, project_root: Path, benchmark: str) -> Path`；`ensure_source_root()` 调用它并把结果加入 `sys.path`，SWE/Skills adapter 直接调用它做只读文件访问。
3. 不因 SWE/Skills 目录缺少 `pyproject.toml/setup.py` 而报错；是否可导入只对 ToolSandbox 有意义。

#### `dynsteer/harness/outputs.py`

不需要重构 default 输出协议，只做两点收紧：

1. `write_default_case_outputs()` 在 `harness.default_result_from_session()` 返回 infrastructure failure 时照常写 summary/report/trajectory，score 为 `null`，并写入 `native_evaluation_available=false`。
2. `runtime_metrics["agent_usage"]` 从 harness metrics 复制 prompt/completion/total token 与 coverage；缺失值保持 `null`，不得用 0 冒充。

### 8.7 脚本与 benchmark 专属 venv 实施

#### `scripts/experiment_bootstrap.sh`

删除 `experiment_uses_agentcompass()`、`experiment_uses_toolsandbox()` 与 `.venv-agentcompass` candidate 后，保留并新增以下 shell 函数：

```bash
bootstrap_python_command()          # 依次找 .venv/Scripts/python.exe、.venv/bin/python、python、python3
experiment_runtime_lines()          # 输出 benchmark<TAB>group<TAB>venv<TAB>profile<TAB>source_root
experiment_runtime()                # 校验唯一 benchmark，读取一行 runtime 信息
benchmark_python()                  # 返回 .venv-*/Scripts/python.exe 或 .venv-*/bin/python
prepare_base_venv()                 # 创建 .venv，安装基础依赖
prepare_benchmark_venv()            # 按 group 创建 .venv-*
prepare_test_venv()                 # 创建 .venv-tests 并安装 all-groups
prepare_benchmark_source()          # 只读校验三个外部源码/数据目录
assert_execution_neutral()          # 调用 Python 递归检查禁用字段
```

`experiment_runtime_lines()` 的内嵌 Python 必须输出：

```text
toolsandbox  toolsandbox  .venv-toolsandbox   <profile>  ../ToolSandbox
swebench_pro swebench_pro .venv-swebench-pro <profile>  ../SWE-bench_Pro-os
skillsbench  skillsbench  .venv-skillsbench  <profile>  ../skillsbench
```

source root 一律从 `data/{benchmark}/benchmark.json` 解析，不接受脚本参数覆盖。profile 只由调用方传入 `docker/host`，不从 JSON 读取。

`prepare_benchmark_venv()` 的核心命令：

```bash
cd "$PROJECT_ROOT"
UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/$venv_name" \
UV_CACHE_DIR="$PROJECT_ROOT/.uv-cache" \
UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.uv-python" \
UV_LINK_MODE=copy \
uv sync --frozen --no-dev --no-install-project --inexact --group "$group"
```

`benchmark_python()` 必须同时支持 Windows Git Bash 的 `Scripts/python.exe` 与 Linux/macOS 的 `bin/python`，并验证文件存在且可执行。脚本后续一律直接调用该 Python，禁止 `uv run` 重新解析默认 `.venv`。

`prepare_benchmark_source()` 只做存在性与只读预检：

| Benchmark | 必需项 |
| --- | --- |
| ToolSandbox | `tool_sandbox` 包入口可导入，源码目录存在 |
| SWE-bench Pro | `swe_bench_pro_eval.py`、`helper_code/image_uri.py`、`run_scripts/<case>/run_script.sh`、`run_scripts/<case>/parser.py`、本地 zip digest |
| SkillsBench | 每个 selected task 均有 `task.md`、`environment/Dockerfile`、`verifier/test.sh`、`verifier/test_outputs.py` |

该函数不执行 `uv sync`，不在外部目录创建缓存或 venv。

#### `scripts/start_experiment.sh`

目标主流程：

```bash
main() {
  parse_args "$@"                 # --exp 必填；不再接受 --source
  resolve_project_root
  assert_execution_neutral "$experiment"
  read_experiment_runtime docker  # profile 写死为 docker
  prepare_base_venv
  prepare_benchmark_venv
  prepare_benchmark_source
  export DYNSTEER_BENCHMARK_EXECUTION=docker
  export UV_NO_SYNC=1 UV_CACHE_DIR=... UV_PYTHON_INSTALL_DIR=...
  export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT"
  benchmark_python "$PROJECT_ROOT/scripts/preflight_experiment.py" \
    --exp "$experiment" --profile docker --prepare-runtime-images
  benchmark_python "$PROJECT_ROOT/main.py" --exp "$experiment" "$@"
}
```

删除内容：

1. `source agentcompass_environment.sh`、`ensure_agentcompass_environment`。
2. `--source`、`--source=*`、container source 转换。
3. `run_in_container()` 与 Docker socket/controller-in-container 路径。
4. ToolSandbox 专属安装逻辑重复实现，改为调用 bootstrap。

保留参数：`--exp`、`--workers`、`--only_adapt`、`--force_adapt`、`--force_eval`、`--no_sum`、`--no-prepare-images`。`--docker-platform linux/amd64` 仅转化为 `DYNSTEER_DOCKER_PLATFORM`，不写入实验 JSON。

#### `scripts/start_experiment_no_docker.sh`

与 Docker 版共享同一 bootstrap 和 venv 映射，差异固定为：

1. `DYNSTEER_BENCHMARK_EXECUTION=host`。
2. 不执行 Docker daemon 检查、不 pull/build 镜像。
3. `preflight_experiment.py --profile host`：
   - ToolSandbox：允许 default、live evaluate、guided、replay 全方法；
   - SWE/Skills：方法含 `default` 且缺对应 default 输出时，启动前报错并列出 `(model_id, repeat_index, case_id)`；
   - SWE/Skills：`--only_adapt`、已有 default 轨迹的 replay、离线汇总允许。

这样同一份实验 JSON 可以传入两个脚本，但能力差异在 matrix 展开前确定。

#### `scripts/preflight_experiment.py`

CLI：

```text
python scripts/preflight_experiment.py
  --exp data/experiments/swebench_pro_main.json
  --profile docker|host
  [--prepare-runtime-images]
  [--no-prepare-images]
```

函数结构：

```python
def main() -> int: ...
def load_context(path: Path, profile: str) -> PreflightContext: ...
def validate_python_dependencies(context: PreflightContext) -> None: ...
def validate_model_credentials(config: JsonObject) -> None: ...
def validate_sources(context: PreflightContext) -> None: ...
def validate_cases(context: PreflightContext) -> None: ...
def validate_docker(context: PreflightContext, prepare_images: bool) -> None: ...
def validate_host_capability(context: PreflightContext) -> None: ...
def prepare_swe_images(context: PreflightContext) -> None: ...
def prepare_skills_images(context: PreflightContext) -> None: ...
```

输出一份 JSON 摘要并返回非零：

```json
{
  "benchmark": "swebench_pro",
  "profile": "docker",
  "case_count": 100,
  "models": ["deepseek-v4-pro"],
  "methods": ["default", "dynsteer_replay"],
  "checks": {"python_dependencies": "passed", "docker": "passed"},
  "failures": []
}
```

依赖检查使用当前解释器实际 import：ToolSandbox 检查 `tool_sandbox`；SWE 检查 `docker`、`pandas`、`polars`；Skills 检查 `docker`、`yaml`；三者都检查 `openai` 与 DynSTEER 核心模块。该检查不发起模型请求。

#### `scripts/get_cases.py` 与 `scripts/get_cases.sh`

`get_cases.py` CLI：

```text
python scripts/get_cases.py
  --benchmark toolsandbox|swebench_pro|skillsbench
  [--seed 202608]
  [--size 100]
  [--output docs/cases_swebench_pro.json]
  [--check]
```

函数：

```python
def export_toolsandbox(source_root: Path) -> JsonObject: ...
def export_swebench_pro(project_root: Path, data_root: Path) -> JsonObject: ...
def export_skillsbench(source_root: Path) -> JsonObject: ...
def stratified_sample(report: JsonObject, size: int, seed: int) -> list[str]: ...
```

规则：

1. ToolSandbox 通过源码模块列 scenario；SWE 读取本地 `eval_samples.jsonl`；Skills 扫描本地 tasks。
2. 输出 `case_ids`、逐 case strata、全量数量、去重数量、分布、source root digest、生成时间；SWE 输出 archive/source commit，Skills 输出 tasks digest。
3. `--check` 只验证当前文件是否与源码 digest 一致，不写报告、不联网。
4. `scripts/get_cases.sh` 调用 `.venv-tests` 或基础 `.venv` 的 Python；首次可调用 `prepare_test_venv`。

### 8.8 数据与实验配置落盘内容

#### Manifest 与 run config

`data/swebench_pro/benchmark.json`：

```json
{
  "benchmark": "swebench_pro",
  "source_root": "../SWE-bench_Pro-os",
  "dataset_archive": "../../swebench_pro_data.zip",
  "tool_backend": "shell",
  "language": "en",
  "max_workers": 1,
  "dockerhub_username": "jefzda"
}
```

`data/swebench_pro/run_configs.json`：

```json
[
  {
    "name": "source_direct",
    "scenarios": [],
    "max_tool_calls": 60,
    "command_timeout_seconds": 600,
    "model_temperature": 0.0
  }
]
```

`data/skillsbench/benchmark.json` 与 `run_configs.json` 按第 4.3 节原样落地；`condition` 固定 `with-skills`，不新增执行环境字段。

#### 10 份实验配置矩阵

| 文件 | benchmark | case | repeats | methods |
| --- | --- | ---: | ---: | --- |
| `data/experiments/toolsandbox_main.json` | toolsandbox | 509 | 3 | default/full/static |
| `data/experiments/swebench_pro_main.json` | swebench_pro | 100 | 3 | default/full/static |
| `data/experiments/skillsbench_main.json` | skillsbench | 87 | 3 | default/full/static |
| `data/experiments/toolsandbox_ablation.json` | toolsandbox | 100 | 3 | 七个消融实际运行臂 |
| `data/experiments/swebench_pro_ablation.json` | swebench_pro | 50 | 3 | 七个消融实际运行臂 |
| `data/experiments/skillsbench_ablation.json` | skillsbench | 50 | 3 | 七个消融实际运行臂 |
| `data/experiments/toolsandbox_intervention.json` | toolsandbox | 200 | 3 | default/stop/guided |
| `data/experiments/toolsandbox_pilot.json` | toolsandbox | 10 | 1 | default/full/static |
| `data/experiments/swebench_pro_pilot.json` | swebench_pro | 3 | 1 | default/full/static |
| `data/experiments/skillsbench_pilot.json` | skillsbench | 3 | 1 | default/full/static |

所有 SWE/Skills model object 结构固定：

```json
{
  "model_id": "deepseek-v4-pro",
  "harness_metadata": {
    "client": {
      "model": "deepseek-v4-pro",
      "api_key": "sk-7c5fdd9f2d2a46289b20875bc6c723e8",
      "base_url": "https://api.deepseek.com"
    }
  }
}
```

三份主配置把第 4.4 节四个模型对象全部放入 `models`；pilot 可只放一个 DeepSeek 与一个 Qwen 任务，用于分别验证两个 endpoint。Judge 与 milestone generator 均直接写同一套明文配置：

```json
{
  "judge_profiles": {
    "fixed-judge": {
      "provider": "openai_compatible",
      "model": "qwen3-max-2026-01-23",
      "api_key": "sk-c424db1a9f7d4535b73947d09ee8f42c",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }
  },
  "milestone_generation": {
    "generator": {
      "provider": "openai_compatible",
      "model": "qwen3-max-2026-01-23",
      "api_key": "sk-c424db1a9f7d4535b73947d09ee8f42c",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "temperature": 0.0
    }
  }
}
```

该字段实际挂在每个 benchmark spec 下；所有正式/pilot 配置必须固化该 generator，不能回退环境变量。

生成配置时的其他约束：

1. `experiment_id` 与文件主名完全一致。
2. `benchmarks` 长度为 1，`case_ids` 为完整排序列表，不允许占位空数组。
3. ToolSandbox intervention 的 stop/guided arm 开启 `collect_online_native_score=true`；全部配置开启 `capture_agent_usage=true`。
4. 不出现第 3.2 节禁用字段，不出现 `metadata.agentcompass`。
5. 现有 `toolsandbox_main_1.json` 至 `toolsandbox_main_4.json` 与 `data/toolsandbox/run_configs.json` 原样保留；新的 `toolsandbox_main.json` 是合并后的正式入口，不覆盖历史四个分模型文件。

### 8.9 测试文件与断言

新增测试布局：

```text
tests/runtime/test_profile.py
tests/runtime/test_docker.py
tests/agent/test_openai_tool_agent.py
tests/adapter/swebench_pro/test_prepare_data.py
tests/adapter/swebench_pro/test_adapter.py
tests/adapter/swebench_pro/test_runtime.py
tests/adapter/swebench_pro/test_evaluator.py
tests/adapter/skillsbench/test_adapter.py
tests/adapter/skillsbench/test_runtime.py
tests/experiment/test_execution_neutral_config.py
tests/scripts/test_preflight_experiment.py
```

关键断言：

1. Agent：tool-call step 与 tool-result step 的 correlation id 一致；usage 只落在 assistant step；key 不出现在任何序列化结果。
2. SWE URI：普通 repo 去 `-vnan`；`element-hq/element-web` 两个官方特例分别断言；128 字符截断。
3. SWE evaluator：`true + _output.json -> 1.0`、`false + 完整 output -> 0.0`、缺 output 或子进程失败 -> `None`。
4. Skills：`mhc-layer-impl` 排除；task mirror 均位于 DynSTEER；`environment/groundtruth` 不进入容器；reward 0/1/缺失分别映射 0/1/null。
5. 配置：禁用字段递归报错；多 benchmark 报错；judge/client 缺明文 key 或 URL 报错；env 字段报错。
6. preflight：benchmark->group->venv 映射正确；host 下 SWE/Skills default 缺轨迹在启动前失败；ToolSandbox host 全方法通过。
7. AgentCompass：`rg -n "agentcompass|AgentCompass" dynsteer scripts data` 无命中；`docs/apis` 与 README 同样无命中；历史 `docs/plans` 不作为清理范围。

## 9. 测试与验收

### 9.1 单元测试

新增测试必须只调用公共接口，不为了测试新增接口：

1. 配置校验
   - 禁用字段递归报错。
   - 一份实验配置包含多个 benchmark 报错。
   - `scenarios: []` 不触发默认 case 选择。
2. SWE 数据准备
   - zip 安全过滤。
   - archive digest 命中跳过、未命中重建。
   - list 字段转换为 Python literal 字符串。
   - gold patch 不进入 TaskCase。
3. SWE harness
   - fake executor 下 bash tool 循环、step limit、patch 生成。
   - native output true/false/missing 分别映射 1/0/null。
   - host profile 请求新 default 轨迹时在 matrix 展开前报错。
4. Skills adapter 与 runtime
   - task.md front matter 解析和默认排除项。
   - task mirror、镜像 tag、run root 全部位于 DynSTEER。
   - with-skills 与 without-skills 的 prompt 输入差异正确。
   - reward 0/1/缺失分别映射 0/1/null。
   - oracle 与 verifier 内容不进入 generator view。
5. 共享 Agent
   - OpenAI-compatible tool-call 消息循环。
   - usage 与命令耗时归属到对应 step，真实缺失为 null。
   - 明文 key 只传给 OpenAI client，不进入轨迹。
6. 环境预检
   - benchmark 到 venv 与依赖组的映射正确。
   - 每个专属 venv 的 import preflight 通过后才允许启动 main.py。

核心新增模块测试覆盖率不低于 80%。

### 9.2 集成验收

依次执行：

```bash
./scripts/get_cases.sh
./scripts/experiment_bootstrap.sh prepare_test_venv
.venv-tests/bin/python -m pytest   # Windows Git Bash 使用 .venv-tests/Scripts/python.exe
.venv-tests/bin/python -m compileall dynsteer
./scripts/start_experiment_no_docker.sh --exp data/experiments/toolsandbox_pilot.json --only_adapt
./scripts/start_experiment.sh --exp data/experiments/swebench_pro_pilot.json
./scripts/start_experiment_no_docker.sh --exp data/experiments/swebench_pro_pilot.json --only_adapt
./scripts/start_experiment.sh --exp data/experiments/skillsbench_pilot.json
./scripts/start_experiment_no_docker.sh --exp data/experiments/skillsbench_pilot.json --only_adapt
```

验收项：

1. `rg -n "agentcompass|AgentCompass" dynsteer scripts data` 无命中。
2. 现有 `toolsandbox_main_X.json` 与 `data/toolsandbox/run_configs.json` 中的明文 key 原样保留；新增 SWE/Skills 配置中的 key 与第 4.4 节映射完全一致。
3. 外部三个源码目录运行后 `git status --short` 不变。
4. `data/swebench_pro/source` 存在且 digest 与 zip 匹配。
5. Skills 运行后不存在 `../skillsbench/.venv`；DynSTEER 下存在 `.venv-toolsandbox`、`.venv-swebench-pro`、`.venv-skillsbench`，且三者分别通过对应 import preflight。
6. ToolSandbox 同一实验 JSON 可被两个脚本完整执行；SWE/Skills 同一实验 JSON 在 Docker 版执行 default/native，在非 Docker 版仅适配或 replay，不允许中途降级。
7. 每个 Docker pilot 输出 native score、DynSTEER score、trajectory、runtime metrics 和成本 ledger。
8. 基础设施失败 case 的 native score 与相应 token/time 均为 null，并带可用性统计。
9. 外部源码目录运行前后 `git status --short` 不变。
10. `git diff --check` 无空白错误。

## 10. 实施顺序

1. 修复配置校验、adapter registry、明文模型客户端与执行环境注入。
2. 更新依赖组，落地三个 benchmark 专属 venv 与 preflight。
3. 落地 SWE 数据准备脚本与 data 目录。
4. 落地共享 OpenAI tool-agent、SWE runtime、trajectory 与本地 Docker evaluator。
5. 落地 Skills adapter、task mirror、本地 Docker runtime、verifier 直调与 trajectory。
6. 重写两个统一实验脚本、bootstrap、get_cases 和旧入口清理。
7. 生成 10 份正式/pilot 实验配置，并保留第 4.4 节明文凭据。
8. 先跑单元测试与 `--only_adapt`，再跑三个 benchmark pilot。
9. Pilot 通过后才启动主实验、消融实验和 ToolSandbox 介入实验。

## 附录A. 项目中没有把握实现的模块部分

1. **SWE 官方镜像内的 agent 可用性**：官方镜像面向 evaluator entryscript，长时间驻留并逐次执行 bash 命令虽然符合镜像能力，但不同 repo 镜像的默认 shell、环境变量和 git 状态仍可能有差异。Docker pilot 必须先验证每个抽样 repo 的 `pwd`、`git status` 和 `git diff --cached`。

2. **本地 Docker 镜像与网络稳定性**：Agent 不再依赖 Modal，但 SWE 官方镜像拉取、Skills task Dockerfile build、apt/pip 安装仍依赖本地 Docker 和网络。脚本只能在启动前确认 daemon 与文件存在，不能保证镜像仓库全程可用；运行期失败必须记录为 infrastructure failure，不能当作模型失败。

3. **自定义 Skills Agent 与 OpenCode 的行为差异**：DynSTEER Agent 使用同一任务容器和官方 verifier，但不再使用 OpenCode CLI。其技能发现方式、上下文管理和停止策略必然与 OpenCode 不完全一致。因此结果是 DynSTEER agent 的源码直连成绩，不能声称等价复现 OpenCode runner 成绩。Pilot 需要人工检查 skill prompt 与前若干条 bash 命令，确认 skill 文件确实可见且 oracle 未泄漏。

4. **Skills 任务镜像语义覆盖**：本方案直接执行 `environment/Dockerfile` 与 `verifier/test.sh`，不再经过 BenchFlow。大多数任务结构一致，但个别任务可能依赖 BenchFlow 注入的 mount、service 或日志目录语义。实现时必须对 87 个任务做静态扫描，发现 `main` 以外的 service、特殊 mount 或非标准 reward 输出时列入 pilot 并显式适配，不允许静默改成宿主机命令。

5. **SWE native false 与基础设施失败的区分**：源码 evaluator 最终输出 boolean。方案通过“是否存在 output JSON + 子进程是否正常退出 + 本地 Docker 日志”区分，但如果上游在某些异常下仍产出完整测试 output 且返回 false，DynSTEER 无法进一步区分，只能按 native unresolved 处理并在审计中保留日志。

6. **非 Docker 版的能力边界**：SWE 与 Skills 的任务状态由官方镜像和任务 Dockerfile 定义。把 bash 工具直接放到宿主机会改变依赖集合并污染宿主机，因此本方案不在 host profile 中伪造完整任务环境；非 Docker 版只做适配、replay 和汇总。若后续确需完整 host-only native 执行，必须另立方案逐 repo、逐 task 定义可复现工作区和依赖锁定，不能由 adapter 自动执行 Dockerfile 中的任意 `RUN`。
