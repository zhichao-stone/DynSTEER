# AgentCompass 适配 API

## 支持范围

DynSTEER 支持通过 AgentCompass 运行 `swebench_pro` 和 `skillsbench`，固定 AgentCompass commit 为 `04d138a1c1decd2c9caa8c2659c698d7ffb677b4`。首版仅支持 Default 整任务执行。

`agentcompass`、`rich` 与 `tabulate` 位于 `agentcompass` optional extra，不运行相关 benchmark 时不安装。执行相关 benchmark 前使用 `uv sync --locked --extra agentcompass`；真实运行所需的 dataset、Docker、mini-swe-agent 或 OpenHands 等依赖，仍按该固定提交的 requirements/安装文档配置。

注意：当前 DynSTEER 基础依赖固定 `openai==1.17.0`，而固定 AgentCompass requirements 要求 `openai>=2.41.1`。这两个环境目前不应在同一个 uv 环境中混用，推荐为 AgentCompass 创建 Python 3.12+ 独立虚拟环境，并使用 `uv run --no-sync` 运行本项目入口。

Windows 独立环境示例：

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

对应的脚本入口为：

```powershell
./scripts/start_experiment_no_docker.sh --exp data/experiments/agentcompass_cross_benchmark.json --workers 1
./scripts/start_experiment.sh --exp data/experiments/agentcompass_cross_benchmark.json --workers 1
```

两个脚本现在会根据实验配置自动启用 `agentcompass` extra；但它们不会自动解决 AgentCompass 与 DynSTEER 当前 OpenAI SDK 的版本冲突，因此真实评估仍需在上面的独立环境中执行。

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

模型 ID 只从 `metadata.model_id` 读取。endpoint 与密钥只从 `MODEL_BASE_URL`、`MODEL_API_KEY` 环境变量读取。配置中禁止 `api_key`、`base_url`、`token`、`password`、`secret` 等明文字段，也禁止 AgentCompass `reuse` 配置。

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
