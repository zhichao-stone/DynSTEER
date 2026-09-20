# AgentCompass 适配 API

## 支持范围

DynSTEER 支持通过 AgentCompass 运行 `swebench_pro` 和 `skillsbench`，固定 AgentCompass commit 为 `04d138a1c1decd2c9caa8c2659c698d7ffb677b4`。首版仅支持 Default 整任务执行。

`agentcompass` 不是 DynSTEER 的 uv extra。DynSTEER/ToolSandbox 主环境不安装 AgentCompass，避免 `openai` 版本与 ToolSandbox 锁定依赖冲突。启动脚本根据 `--source` 自动准备 `.venv-agentcompass`；dataset、Docker、mini-swe-agent 或 OpenHands 等运行依赖由 AgentCompass 包声明和 `auto_install_dependencies` 管理。

注意：当前 DynSTEER 基础依赖固定 `openai==1.17.0`，而 AgentCompass 基础依赖要求 `openai>=2.41.1`。这两个环境目前不应在同一个 uv 环境中混用，推荐为 AgentCompass 创建 Python 3.12+ 独立虚拟环境，并以该解释器运行项目入口。

独立环境由启动脚本自动加载：

```powershell
./scripts/start_experiment_no_docker.sh --exp data/experiments/cross_benchmark_main.json --workers 1
```

对应的脚本入口为：

```powershell
./scripts/start_experiment_no_docker.sh --exp data/experiments/cross_benchmark_main.json --source ../AgentCompass --workers 1
./scripts/start_experiment.sh --exp data/experiments/cross_benchmark_main.json --source ../AgentCompass --workers 1
```

相对路径按 DynSTEER 项目根解析。脚本会安装 AgentCompass 源码、以 `--no-deps` 安装 DynSTEER 源码，并补充独立环境运行时；后续运行通过 stamp 复用。`DYNSTEER_AGENTCOMPASS_FORCE_INSTALL=1` 强制重装，`DYNSTEER_AGENTCOMPASS_VENV` 和 `DYNSTEER_AGENTCOMPASS_PYTHON` 可覆盖路径与 Python 版本。

## `metadata.agentcompass` 配置

| 字段 | 要求 | 说明 |
|---|---|---|
| `harness` | 必填非空字符串 | AgentCompass harness ID |
| `environment` | 必填非空字符串 | AgentCompass environment ID |
| `model_api_protocol` | 必填非空字符串 | 模型协议 |
| `data_dir` | 必填非空字符串 | AgentCompass 独立数据缓存目录 |
| `benchmark_params` | 可选对象 | benchmark 原生参数；`sample_ids/k/avgk` 由桥接层覆盖 |
| `harness_params` | 可选对象 | harness 原生参数 |
| `environment_params` | 可选对象 | environment 原生参数 |
| `model_params` | 可选对象 | model 原生参数 |
| `enabled_recipes` | 可选字符串数组 | 启用的 AgentCompass recipes |
| `timeout_seconds` | 可选正整数 | 单次 AgentCompass 执行超时，默认 360000 |
| `auto_install_dependencies` | 可选 bool，默认 false | 传给 AgentCompass 运行 API；开启后仅由 AgentCompass 按其可信 extra 声明安装 host 依赖 |

模型 ID 只从 `metadata.model_id` 读取。endpoint 与密钥只从 `MODEL_BASE_URL`、`MODEL_API_KEY` 环境变量读取。配置中禁止 `api_key`、`base_url`、`token`、`password`、`secret` 等明文字段，也禁止 AgentCompass `reuse` 配置。

`MODEL_BASE_URL` 是一次 AgentCompass run 的唯一 endpoint，必须能服务该实验矩阵中的全部 model ID；`MODEL_API_KEY` 必须对这些 model ID 都有效。配置项中的 `agent_client`、`user_client` 只用于非 AgentCompass harness。混合 endpoint 前应使用 `swebench_pro_pilot.json` 或 `skillsbench_pilot.json` 单模型配置分别验证。

该边界只约束 AgentCompass run request。`milestone_generation.generator.api_key` 属于 DynSTEER 静态 milestone generator 配置，私有实验仓库允许直接保存。

## 公共函数

```python
def load_task_records(
    benchmark: str,
    config: HarnessRunConfig,
) -> Mapping[str, AgentCompassTaskRecord]: ...

def run_agentcompass_case(
    benchmark: str,
    case_id: str,
    config: HarnessRunConfig,
    output_dir: Path,
) -> JsonObject: ...
```

`load_task_records()` 返回只读、有界缓存的任务白名单投影；未知 benchmark、非法配置、重复 task ID 或上游加载失败时抛出异常。`run_agentcompass_case()` 强制单 case、单并发、关闭 analysis/reuse，并返回唯一匹配的脱敏 detail；request、run path、detail 或 schema 不确定时抛出异常。

case list 固化前只调用 `load_task_records()`。SWE-bench Pro 的 ID 来自 dataset `instance_id`，SkillsBench 的 ID 来自任务目录名；分层抽样使用 `repo` 和 `category`，并必须把最终 `case_ids` 显式写入实验配置。

AgentCompass 目录加载只导入目标 benchmark、对应 harness、Docker environment 和 recipe，不调用全量 `load_builtin_components()`。这可以避免 Windows 上无关 benchmark 的 Unix-only 依赖（例如 `fcntl`)阻断 catalog 导出。

项目根目录的 `get_cases.py` 会分别调用两个 benchmark 的 `load_task_records()`，生成 `docs/cases_swe-bench-pro.json` 与 `docs/cases_skills-bench.json`。输出包含排序后的 `case_ids`、逐 case 分层映射、全量数量、去重统计、`category` 分布、分层字段分布、字段覆盖计数、case ID 来源和固定 AgentCompass commit。运行 `bash scripts/get_cases.sh`；它会自动准备 `.venv-agentcompass`，默认源码路径为 `../AgentCompass`，默认缓存路径为 `data/agentcompass`；可通过第一个参数覆盖数据目录，通过 `DYNSTEER_AGENTCOMPASS_SOURCE` 覆盖 AgentCompass 源码路径。导出过程固定 `auto_install_dependencies=false`。

## TaskCase 投影

| benchmark | 保留字段 | 明确丢弃 |
|---|---|---|
| SWE-bench Pro | question、category、repo、base_commit、requirements、interface | gold patch、测试与其他 dataset 字段 |
| SkillsBench | question、category | sample/tests 路径、task TOML、verifier 配置 |

## ACTF 映射

| ACTF 内容 | DynSTEER 步骤 |
|---|---|
| 后续 user content | `User -> Agent / MESSAGE` |
| tool call | `Agent -> Environment / TOOL_CALL` |
| 已配对 observation | `Environment -> Agent / TOOL_RESULT` |
| 未配对 observation | `Environment -> Agent / MESSAGE` |
| 无工具调用的 assistant content | `Agent -> User / FINAL` |

并行调用先输出全部 outbound，再输出 results。tool call ID 用于闭包关联；缺失时生成稳定 ID。token、LLM latency 和环境 latency 在每个 ACTF step 中各记一次。

成本归属按 ACTF native step 而不是转换后的 raw step 统计：有 tool call 时，首个 tool call 是 LLM token 和 LLM latency 的唯一代表；无 tool call 时，assistant final message 是代表。同一 native step 派生的其他 raw step 对应字段写 0，表示确认不归属。environment latency 只归属第一个成功配对 observation，其他 observation 写 0。首个 native step 之外转换出的 user message 的 token/latency 固定为 0。

若上游 metric 的 prompt/completion 只出现一项，token 视为缺失并保持 `null`；LLM 或 environment latency 缺失时同样保持 `null`。不会用半个 usage 或伪 0 填充真实缺失。SWE-bench Pro 和 SkillsBench 的原生 verifier token 是确定性真实 0，并由 `native_evaluation_token_source` 标记。

## Default 输出

| benchmark | score | final state | 原生状态来源 |
|---|---|---|---|
| SWE-bench Pro | resolved 对应 1/0 | patch 大小、哈希、路径摘要 | AgentCompass `correct/eval_raw_data` |
| SkillsBench | `[0,1]` partial reward | artifact 大小、哈希、类型清单 | AgentCompass `score/verify_log` |

合法 0 分与 run/eval error 通过 `raw_summary` 中的状态字段区分。provenance 只写入 `raw_summary`。

## 数据与安全边界

AgentCompass 原始运行目录位于 case 的 `raw/agentcompass-results/` 下。DynSTEER 派生的 TaskCase、trajectory、summary、report 和 final state 不保存 gold patch、hidden verifier 输出、patch/artifact 正文或模型密钥。原始 AgentCompass 目录用于审计，应由运行环境限制访问。

## 已知限制

首版为整任务调用，没有 snapshot、在线中止或 replay；`snapshots=[]`。固定版本的进程全局 runtime settings 要求 `max_workers=1`。SkillsBench 只摘要 AgentCompass 已返回的 artifact，不进入容器补采。

未安装 extra 时，普通 registry 导入仍可用；只有真正调用 AgentCompass task catalog 或 case 执行时，才会报告缺少可选依赖。
