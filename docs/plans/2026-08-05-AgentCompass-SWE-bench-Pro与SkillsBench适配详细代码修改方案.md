# AgentCompass SWE-bench Pro 与 SkillsBench 适配详细代码修改方案

> 制定日期：2026-08-05  
> 上位方案：`docs/plans/2026-08-05-AgentCompass-SWE-bench-Pro与SkillsBench适配代码方案.md`  
> 实现范围：仅修改 DynSTEER 工作区；AgentCompass、SWE-bench_Pro-os 和 SkillsBench 源码均只读

## 1. 交付目标与非目标

本方案在 DynSTEER 内新增 `swebench_pro` 和 `skillsbench` 两个 benchmark adapter/harness，通过 AgentCompass 固定版本的 Python API 完成任务加载、Agent 执行、原生评分和 ACTF 轨迹产出。DynSTEER 只做以下四件事：

1. 将 AgentCompass `TaskSpec` 的可见字段投影为 `TaskCase`；
2. 按单 case 调用 AgentCompass，并精确选取该 case 的 detail；
3. 将 ACTF v1.0 线性转换为 DynSTEER `TrajectoryStep`；
4. 将 AgentCompass 原生结果映射为 `BenchmarkDefaultResult`。

本期明确不实现：

- 不复制、不执行 SWE-bench Pro 官方评分脚本；
- 不复制、不执行 SkillsBench verifier；
- 不读取或依赖本地 `SWE-bench_Pro-os`、SkillsBench 源仓库；
- 不修改 AgentCompass，不做 monkey patch、observer 注入或运行时方法替换；
- 不设计或接入任何后续动态评估结构的生成算法；
- 不支持 replay 或在线逐步中断；首版只支持 `default`；
- 不新建 benchmark 私有 output writer、第二套配置入口或第二次轨迹协议遍历。

AgentCompass commit 固定为 `04d138a1c1decd2c9caa8c2659c698d7ffb677b4`。该版本中：

- SWE-bench Pro 的 `evaluate()` 已运行官方 patch evaluator，并返回 `correct/status/error/extra.eval_raw_data`；
- SkillsBench 的 `evaluate()` 已上传 `tests/`、运行 `/tests/test.sh`、读取 `reward.txt`，并返回 `score/correct/status/error/extra.verify_log`；
- SkillsBench task loader 使用 `instruction.md`、可选 `task.toml` 和 `tests/`，不假设未在固定代码中存在的其他 schema。

## 2. 设计收敛结论

### 2.1 仅保留一条生产数据流

```text
AgentCompass TaskSpec
  -> AgentCompassTaskRecord（白名单、有界缓存）
  -> TaskCase

AgentCompass RunRequest
  -> 单 case details JSON
  -> 脱敏 detail
  -> ACTF -> list[TrajectoryStep]（一次）
  -> AgentCompassRunData
  -> 现有 write_default_case_outputs()
```

禁止在 adapter、harness 和 scorer 内各自重读 dataset、detail 或 ACTF。每个 case 只允许一次 AgentCompass 执行、一次 detail JSON 解析和一次 ACTF 转换。

### 2.2 对上位方案的落地收敛

1. `scorer.py` 是用户明确要求的预留扩展代码，不删除。但首版没有可供它消费的 benchmark 结构化约束，因此两个 scorer 只建立类型和 harness 返回入口，不预写无生产调用的评分算法。
2. `utils/` 目录保留，按 ToolSandbox 的组织方式收纳 task/result/patch/artifact 等散乱函数。每个工具函数都必须有 adapter 或 harness 调用方；不为测试单独新建生产 API。
3. 不为 AgentCompass provenance 扩展 `HarnessAdvanceResult`、`Trajectory` 或 output writer 接口。provenance 统一进入现有 `raw_summary_from_session()` 产物；step 级来源进入 `TrajectoryStep.raw`。这避免新增只有 AgentCompass 使用的轨迹 metadata 中转接口。
4. 不新增 `agentcompass/model.py`、`agentcompass/result.py`、`agentcompass/snapshot.py`、无状态 client 类或 benchmark 专用 session 类。
5. 不在通用 config loader 增加 AgentCompass 分支。AgentCompass 配置的唯一解析点是 `dynsteer/adapter/agentcompass/runtime.py`。

## 3. 最终文件结构与变更类型

```text
dynsteer/adapter/
  agentcompass/
    __init__.py
    runtime.py
    harness.py
    trajectory.py
  swebench_pro/
    __init__.py
    adapter.py
    harness.py
    scorer.py
    utils/
      __init__.py
      task.py
      result.py
      patch.py
  skillsbench/
    __init__.py
    adapter.py
    harness.py
    scorer.py
    utils/
      __init__.py
      task.py
      result.py
      artifact.py
tests/
  adapter/
    agentcompass/
      test_runtime.py
      test_harness.py
      test_trajectory.py
    swebench_pro/
      test_adapter.py
      test_harness.py
      test_utils.py
      test_scorer.py
    skillsbench/
      test_adapter.py
      test_harness.py
      test_utils.py
      test_scorer.py
    test_loader_default.py
  experiment/
    test_agentcompass_default.py
data/
  swebench_pro/benchmark.json
  skillsbench/benchmark.json
  experiments/agentcompass_cross_benchmark.json
docs/apis/agentcompass.md
```

| 文件 | 动作 | 不可越界的修改内容 |
|---|---|---|
| `pyproject.toml` | 修改 | 增加 AgentCompass 本地 editable/path dependency；不重复声明它的 benchmark 依赖 |
| `uv.lock` | 机械更新 | 仅反映 dependency 变化 |
| `dynsteer/adapter/registry.py` | 修改 | 显式注册两个 adapter/harness |
| `dynsteer/adapter/loader.py` | 修改 | 使 Default 可加载只有基础字段的 `TaskCase`；非 Default 逻辑不变 |
| `dynsteer/experiment/runner.py` | 修改 | Default 准备时使用现有 `refresh_dynamic_targets=False` 参数 |
| `dynsteer/adapter/agentcompass/*` | 新增 | 唯一依赖边界、共用 harness、ACTF 转换 |
| `dynsteer/adapter/swebench_pro/*` | 新增 | SWE 任务投影、结果映射、patch 摘要、预留 scorer |
| `dynsteer/adapter/skillsbench/*` | 新增 | Skills 任务投影、结果映射、artifact 摘要、预留 scorer |
| `data/...` | 新增 | 两个 manifest 和一个 Default smoke experiment；不新增 `run_configs.json` |
| `tests/...` | 新增 | 脱敏 fixture 单元测试，不包含 gold patch/hidden verifier |
| `docs/apis/agentcompass.md` | 新增 | 配置、输入输出、错误和安全边界 |
| `docs/apis/harness.md` | 修改 | 补充两个 Default harness |
| `README.md` | 修改 | 补充安装、配置和运行示例 |

本方案不修改 `dynsteer/harness/outputs.py`、`dynsteer/harness/model.py`、`dynsteer/model.py` 和通用 config schema。

所有新增公开函数、核心内部函数和 lifecycle 覆盖方法都按 `docs/constraints/code.md` 补充中文 docstring，明确输入、输出和异常。仅在“配置边界”、“AgentCompass 执行”、“detail 脱敏”、“ACTF 转换”、“原生结果映射”五个关键段落前使用中文 `#` 注释，不对每行显而易见代码重复解释。所有公开入参使用类型标注，在最外边界检查空值/形状一次，内部流程不重复规范化和多次校验。

## 4. 依赖与导入边界

### 4.1 `pyproject.toml` 和 `uv.lock`

在 `[project].dependencies` 增加 `agentcompass`，在 `[tool.uv.sources]` 中使用同级目录：

```toml
[project]
dependencies = [
    # 保留现有项
    "agentcompass",
]

[tool.uv.sources]
agentcompass = { path = "../AgentCompass", editable = true }
```

落地前用只读命令确认 `../AgentCompass` 的 HEAD 等于固定 commit，然后执行 `uv lock`、`uv sync --locked`。不在 DynSTEER 重复写 `datasets`、Docker SDK 或 SkillsBench verifier 依赖版本；缺少 benchmark-specific requirement 时，按 AgentCompass 自身安装文档处理。

### 4.2 唯一第三方 import 位置

`dynsteer/adapter/agentcompass/runtime.py` 顶层只显式导入：

```python
from agentcompass import build_run_request, run_evaluation_request
from agentcompass.runtime import BENCHMARKS, load_builtin_components
from agentcompass.runtime.config import bootstrap_runtime
```

其他新文件只导入 DynSTEER 内部类型与 `agentcompass.runtime` 本地边界函数，不直接 import AgentCompass。不使用 `importlib`、`__getattr__`、字符串模块名或函数内部延迟 import。

## 5. `dynsteer/adapter/agentcompass/runtime.py`

### 5.1 公开类型和函数

文件顺序为：常量 -> 公开 dataclass -> 公开函数 -> 内部配置/缓存/detail helper。仅定义一个公开数据类：

```python
AGENTCOMPASS_COMMIT = "04d138a1c1decd2c9caa8c2659c698d7ffb677b4"

@dataclass(frozen=True)
class AgentCompassTaskRecord:
    task_id: str
    question: str
    category: str
    metadata: Mapping[str, JsonValue]

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

`metadata` 用 `MappingProxyType` 包装，返回的 task-id mapping 也用 `MappingProxyType` 包装，避免 adapter/harness 改写有界缓存。`AgentCompassTaskRecord` 不保存 `TaskSpec`、`ground_truth` 或原始 metadata。

### 5.2 配置读取与校验

两个公开函数共用一个内部函数：

```python
def _agentcompass_config(config: HarnessRunConfig) -> JsonObject: ...
```

该函数只读取 `config.metadata["agentcompass"]`，并执行一次边界校验：

- 必填非空字符串：`harness`、`environment`、`model_api_protocol`、`data_dir`；
- `model` 不从该 dict 重复配置，统一使用 `config.metadata["model_id"]`；如 dict 内存在 `model`则拒绝；
- dict 中拒绝 `api_key`、`model_api_key`、`base_url`、`model_base_url`、`token`、`password`、`secret` 键，且递归检查嵌套 mapping；
- `benchmark_params`、`harness_params`、`environment_params`、`model_params` 必须为 JSON object；
- `enabled_recipes` 必须为字符串数组；
- `timeout_seconds` 必须为正整数；不接收 `reuse/reuse_run_id`，结果复用只由 DynSTEER 现有 output cache 负责；
- `data_dir` 解析为绝对路径，相对路径以 DynSTEER 当前工作目录为基准；
- 不读取 `scripts_dir`、`dockerfiles_dir`、`upstream_source_root` 或任何官方评分脚本路径。

endpoint 和密钥仅在 `run_agentcompass_case()` 调用时从 `MODEL_BASE_URL` 和 `MODEL_API_KEY` 读取，只传给 `build_run_request()`，不放入返回值、日志或 provenance。

### 5.3 task catalog 的唯一加载与缓存

`load_task_records()` 执行以下顺序：

1. benchmark 标准化为 `swebench_pro` 或 `skillsbench`，其他值直接拒绝；
2. 调用 `_agentcompass_config()`；
3. 从 `benchmark_params` 中去掉运行级 `sample_ids`、`k`、`avgk`，其余参数用 `json.dumps(sort_keys=True, separators=(",", ":"))` 生成稳定缓存键；
4. 将 `benchmark`、绝对 `data_dir`、canonical params 和 `AGENTCOMPASS_COMMIT` 传入 `_load_task_records_cached()`；
5. 返回只读 task-id mapping，不复制 dataset。

私有缓存函数签名：

```python
@lru_cache(maxsize=4)
def _load_task_records_cached(
    benchmark: str,
    data_dir: str,
    benchmark_params_json: str,
    agentcompass_commit: str,
) -> Mapping[str, AgentCompassTaskRecord]: ...
```

函数内部只执行一次：

```python
bootstrap_runtime(data_dir=data_dir, force=True)
load_builtin_components()
request = build_run_request(
    benchmark=benchmark,
    harness="none",
    model="task-catalog",
    environment="host_process",
    benchmark_params=json.loads(benchmark_params_json),
    enable_analysis=False,
)
native_benchmark = BENCHMARKS.create(benchmark)
tasks = native_benchmark.load_tasks(request)
```

`load_tasks()` 若在固定版本变为 async，应立即报明确 schema/version 错误，不增加当前无需的 sync/async 双分支。

投影白名单固定为：

| benchmark | record 字段 | 原生来源 | 丢弃内容 |
|---|---|---|---|
| SWE-bench Pro | `task_id` | `TaskSpec.task_id` | 无 |
| SWE-bench Pro | `question` | `TaskSpec.question` | 无 |
| SWE-bench Pro | `category` | `TaskSpec.category` | 无 |
| SWE-bench Pro | `metadata.repo/base_commit/requirements/interface` | 同名 metadata | `patch`、tests、F2P/P2P 和其他字段 |
| SkillsBench | `task_id/question/category` | `TaskSpec` 同名字段 | 无 |
| SkillsBench | `metadata` | 空只读 mapping | `sample_dir`、`tests_dir`、整个 `task.toml`/verifier 配置 |

加载时校验 task ID 非空且全局唯一；重复 task ID 报错，不使用“后者覆盖前者”。单 case adapter/harness 查找为 O(1)。

### 5.4 AgentCompass 单 case 执行

`run_agentcompass_case()` 执行如下代码级流程：

与该流程直接对应的两个内部函数签名为：

```python
def _run_identity(
    config: HarnessRunConfig,
    benchmark: str,
    case_id: str,
) -> tuple[str, str]:
    """返回（稳定 run_key，唯一 AgentCompass run_id）。"""

def _sanitize_detail(
    benchmark: str,
    raw_detail: Mapping[str, object],
    run_dir: Path,
    detail_path: Path,
    run_key: str,
    run_id: str,
) -> JsonObject: ...
```

不为这两个返回值再建 identity/detail DTO。

1. 调用 `_agentcompass_config()` 并校验 `case_id/output_dir`；
2. 复制 `benchmark_params`，然后强制覆盖 `sample_ids=[case_id]`、`k=1`、`avgk=True`；
3. 使用 `_run_identity()` 从 `experiment_id/benchmark/model_id/method/repeat_index/case_id` 生成路径安全的稳定 `run_key`；AgentCompass `run_id` 在该 key 后追加 UTC 微秒时间和 8 位随机后缀，保证 `force_eval` 重跑时不与 AgentCompass 已存在目录冲突；超长 key 保留可读前缀并追加 SHA-256 前 16 位；
4. 构建 request，强制 `task_concurrency=1`、`enable_analysis=False`，传入环境变量中的 endpoint/key；
5. 调用同步 `run_evaluation_request()`，`results_dir=output_dir / "agentcompass-results"`，`data_dir` 使用配置的独立缓存根，`progress="none"`；
6. 从返回对象 `paths["run_info"]` 的父目录定位 run dir；如固定版本没有 `run_info` 键，才使用 `paths["params"]` 的父目录；两者都缺失则报 schema 错误；
7. 仅扫描该 run dir 的 `details/*.json`，解析后按顶层 `task_id == case_id` 匹配；要求恰好一个匹配；
8. 要求 `attempts` 为 mapping 且恰好包含一个 attempt，不假设 attempt key 必须是 `"1"`；
9. 立即调用 `_sanitize_detail()` 构造新 dict，释放原始 detail；
10. 返回脱敏 detail，不返回 AgentCompass summary 大对象。

`build_run_request()` 的参数映射必须是直接的，不再建一层 request DTO：

```python
request = build_run_request(
    benchmark=benchmark,
    harness=settings["harness"],
    model=config.metadata["model_id"],
    environment=settings["environment"],
    benchmark_params=benchmark_params,
    harness_params=settings["harness_params"],
    environment_params=settings["environment_params"],
    model_base_url=os.environ.get("MODEL_BASE_URL", ""),
    model_api_key=os.environ.get("MODEL_API_KEY", ""),
    model_api_protocol=settings["model_api_protocol"],
    model_params=settings["model_params"],
    task_concurrency=1,
    enabled_recipes=settings["enabled_recipes"],
    enable_analysis=False,
    run_name="dynsteer",
    run_id=run_id,
    reuse=False,
)
```

DynSTEER 的 `existing_case_output()` 是唯一结果复用层：普通重跑命中 DynSTEER 缓存时不会调用 AgentCompass；`force_eval=True` 时创建新 AgentCompass run。不再启用 AgentCompass 的第二套 reuse 流程，避免最新 run 跨 case/跨配置误复用，也避免显式稳定 run ID 与已存在目录冲突。provenance 同时记录稳定 `run_key` 和实际 `run_id`。

### 5.5 detail 脱敏后的精确 schema

`_sanitize_detail(benchmark, raw_detail, run_dir, detail_path, run_key, run_id)` 的顶层只返回 `task_id/attempt/provenance`。其中 SWE attempt 的精确形状为：

```json
{
  "task_id": "...",
  "attempt": {
    "status": "completed|run_error|eval_error|run_error_or_eval_error|skipped",
    "correct": false,
    "error_present": false,
    "final_answer": "agent generated patch",
    "trajectory": {},
    "evaluation": {
      "completed": true,
      "resolved": false,
      "timed_out": false,
      "returncode": 0,
      "error_present": false
    }
  },
  "provenance": {
    "agentcompass_commit": "04d...",
    "run_key": "...",
    "run_id": "...",
    "run_dir": "...",
    "detail_path": "...",
    "detail_sha256": "..."
  }
}
```

Skills attempt 的精确形状为：

```json
{
  "status": "completed|run_error|eval_error|run_error_or_eval_error|skipped",
  "correct": false,
  "score": 0.0,
  "error_present": false,
  "trajectory": {},
  "files": {},
  "evaluation": {
    "reward": 0.0,
    "test_return_code": 0,
    "test_error_present": false,
    "reward_error_present": false
  }
}
```

详细白名单：

- 顶层只保留 `task_id/attempt/provenance`，不重复保存 attempt 中的 score/correct/category；
- SWE attempt：仅 `status/correct/error_present/final_answer/trajectory/evaluation`；`evaluation` 仅包含 `extra.eval_raw_data` 的 `completed/resolved/timed_out/returncode`，`error` 只投影为 presence bool；
- Skills attempt：仅 `status/correct/score/error_present/trajectory/files/evaluation`；`files` 来自 `artifacts.file`，`evaluation` 仅包含 `extra.verify_log` 的 `reward/test_return_code`，`test_error/reward_error` 各投影为 presence bool；
- 丢弃 `ground_truth`、`meta`、`analysis_result`、SWE `tests/raw_output/stdout/stderr/fail_to_pass/pass_to_pass`、OpenHands history/LLM config 和所有未知键。

Skills `files` 只保留 `artifacts.file` 中的 path -> value mapping，供 artifact util 立即生成摘要；SWE 不消费 artifacts，因此 SWE 脱敏 detail 不保留该字段。`AgentCompassSession` 不保存 detail，原始文件内容不会进入 DynSTEER 结果对象。

## 6. `dynsteer/adapter/agentcompass/trajectory.py`

### 6.1 唯一公开函数

```python
def convert_actf_steps(
    trajectory_data: object,
    *,
    benchmark: str,
    case_id: str,
) -> list[TrajectoryStep]: ...
```

不定义 converter 类、intermediate step dataclass 或 snapshot parser。内部 helper 只在文件末尾定义，包括 `_text()`、`_tool_calls()`、`_observations()`、`_arguments()`、`_timestamp()`和 `_cost()`；如落地时其中某个 helper 只有一行转发，直接合并回主函数。

### 6.2 ACTF `StepInfo` 映射规则

对 `trajectory_data["steps"]` 单次线性扫描；复杂度 O(S+C+O)，S 为 ACTF step 数，C 为 tool call 数，O 为 observation 数。

1. 输入为 `None` 时返回空数组；输入非 object、`steps` 非 array 或 step 非 object 时报带 benchmark/case 的 schema 错误。
2. 第一个 ACTF step 的 `system_prompt` 和 `user_content` 是任务初始输入，不重复生成轨迹 message；后续 step 只有在 `user_content` 非空时才生成 `User -> Agent / MESSAGE`。
3. 每个 tool call 生成一个 `Agent -> Environment / TOOL_CALL`，`ToolCall.name` 取 `function.name`，`arguments` 接受 object 或 JSON object 字符串；其他形状报错。
4. call ID 优先取 `id`；缺失时生成 `actf:{native_step_id}:call:{zero_based_index}`，并在 outbound `raw["generated_call_id"] = true`。无论原生还是生成 ID，都写入 `raw["openai_tool_call_id"]`，供现有 `AgentStepTracker` 使用。
5. 先按原顺序写完同一 ACTF step 的所有 outbound，再写 tool result，不把并行调用改成依赖串行。同一 ACTF step 中的非空 call ID 必须唯一，重复时立即报 schema 错误。
6. observation 若包含 `tool_call_id/call_id/id`，则按 ID 配对；否则只在 call 数和 observation 数相等时按位置配对。
7. 已配对 observation 生成 `Environment -> Agent / TOOL_RESULT`。`success` 优先使用明确 bool；存在非空 `error/exception` 时为 false；否则为 true。不从 stdout 文本猜测成功与否。
8. 无法配对的 observation 生成 `Environment -> Agent / MESSAGE`，`raw["unpaired_observation"] = true`；无 observation 的 tool call 保持未配对，不伪造成功结果。
9. 存在 tool call 时，assistant `content/reasoning_content` 放入第一个 outbound 的 raw，不额外生成一个会破坏 outbound closure 的 Agent message。
10. 无 tool call 且 assistant content 非空时，生成 `Agent -> User / FINAL`；reasoning 只放 raw，不作为可验证事实。
11. 没有 assistant outbound 但存在 observation 的 fallback/error step 仍保留 observation，不创造空 Agent final message。
12. 输出 `index` 从 0 开始全局连续，`step_id` 使用 `{benchmark}::{case_id}::step::{index}`；不直接把可能重复的 ACTF step ID 当 DynSTEER ID。

每个输出 step 的 raw 仅保留必要来源：`actf_step_id`、`actf_call_index`、`openai_tool_call_id`、`reasoning_content`、`stop_reason`、`generated_call_id`、`unpaired_observation`、`aggregate_env_latency`。空值不写入。

### 6.3 token 和 latency 只记一次

- `prompt_tokens_len + completion_tokens_len` 之和写入该 ACTF step 第一个 Agent outbound 的 `StepCost.tokens`；单项缺失时按 0 参与，两项都缺失时保持 `None`；
- `llm_infer_ms` 只写入该 outbound 的 `StepCost.latency_ms`；
- `env_action_ms` 只写入第一个已配对 tool result；一个 ACTF step 有多个 call 时标记 `aggregate_env_latency=true`；
- 不将同一 metric 复制到所有并行 call，不在 `Trajectory.raw`、`metrics` 中再存一份 step metric。

`convert_actf_steps()` 不内置 `AgentStepTracker` 第二次遍历。现有 `write_default_case_outputs()` 在 append step 时已是唯一生产协议跟踪点。

## 7. `dynsteer/adapter/agentcompass/harness.py`

### 7.1 数据类

只定义两个共用类：

```python
@dataclass(frozen=True)
class AgentCompassRunData:
    steps: list[TrajectoryStep]
    default_result: BenchmarkDefaultResult
    raw_summary: JsonObject
    final_state: JsonObject | None

@dataclass
class AgentCompassSession:
    case_id: str
    config: HarnessRunConfig
    raw_output_dir: Path
    finished: bool = False
    run_data: AgentCompassRunData | None = None
```

不保存脱敏 detail，不保存 AgentCompass request/runtime/client 对象，不保存 snapshot 占位符。

### 7.2 `BaseAgentCompassHarness`

类签名：

```python
class BaseAgentCompassHarness(BaseBenchmarkHarness, ABC):
    @abstractmethod
    def _build_run_data(
        self,
        detail: JsonObject,
        steps: list[TrajectoryStep],
    ) -> AgentCompassRunData: ...
```

实现的公开方法及精确行为：

| 方法 | 行为 | 不做的事 |
|---|---|---|
| `list_cases(config)` | 读取共享 task mapping，按 task ID 排序返回 `BenchmarkCase`，category 放 `categories` | 不再加载 dataset |
| `start_case(config, case_id, raw_output_dir)` | 通过 O(1) mapping 确认 case 存在，返回 `AgentCompassSession` | 不创建 AgentCompass 长生命周期 client |
| `advance_case(session)` | 首次执行单 case，转换 ACTF，调用 `_build_run_data()`，设置 `finished=True`，返回全部 steps、`snapshots=[]`、`continue_running=False` | 不循环、不重试完整 case、不解析第二次 |
| `advance_case()` 已完成分支 | 返回空 steps/snapshots 和 `continue_running=False` | 不再调用 AgentCompass |
| `final_state_from_session()` | 返回已构造 final state | 不补采 workspace |
| `raw_summary_from_session()` | 返回脱敏 provenance/status | 不重读 detail |
| `default_result_from_session()` | 返回已构造原生结果 | 不再评分 |

`metrics_from_session()` 没有 AgentCompass 额外指标需要写入，直接继承基类的空 mapping；token/latency 已在 `StepCost` 与现有 runtime metrics 中计算，不再复制一份。`advance_case()` 中的主体应直接表达数据流：

```python
detail = run_agentcompass_case(
    self.benchmark,
    session.case_id,
    session.config,
    session.raw_output_dir,
)
steps = convert_actf_steps(
    detail["attempt"].get("trajectory"),
    benchmark=self.benchmark,
    case_id=session.case_id,
)
session.run_data = self._build_run_data(detail, steps)
session.finished = True
return HarnessAdvanceResult(
    steps=session.run_data.steps,
    snapshots=[],
    continue_running=False,
    reason="agentcompass_completed",
)
```

对 session/run-data 的类型与完成性校验集中在 `_require_session()` 和 `_require_run_data()`，每个具体 harness 不重复实现。`stop_case()`、`teardown_case()`、`initial_state_from_session()` 直接继承现有默认方法，不空覆盖。

## 8. SWE-bench Pro 逐文件修改

### 8.1 `dynsteer/adapter/swebench_pro/utils/task.py`

只新增：

```python
def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase: ...
```

构造规则：

- `task_id=f"swebench_pro::{record.task_id}"`；
- `case_id=record.task_id`；
- `task_description` 为 `question`、`Requirements:\n{requirements}`、`New interfaces introduced:\n{interface}` 三段，后两段只在字段非空时追加；
- `metadata` 仅包含 `benchmark="swebench_pro"`、`category`、`repo`、`base_commit`、`agentcompass_commit`；
- 其他 `TaskCase` 字段使用 dataclass 默认值；
- 本函数不读原始 dataset，不接受 raw `TaskSpec`。

### 8.2 `dynsteer/adapter/swebench_pro/adapter.py`

只定义 `SWEBenchProAdapter`：

```python
class SWEBenchProAdapter(BaseBenchmarkAdapter):
    benchmark = "swebench_pro"

    def adapt_task_case(
        self,
        config: HarnessRunConfig,
        case_id: str,
    ) -> TaskCase:
        records = load_task_records(self.benchmark, config)
        try:
            record = records[case_id]
        except KeyError as exc:
            raise KeyError(f"SWE-bench Pro case 不存在: {case_id}") from exc
        return task_case_from_record(record)
```

方法开头仍按项目约束校验 `config/case_id`，但不再校验 `record` 内已由 runtime 边界校验过的字段。不覆盖 `refresh_task_case_for_experiment()`，因为 Default 不调用它。

### 8.3 `dynsteer/adapter/swebench_pro/utils/result.py`

只新增：

```python
def native_result_summary(detail: Mapping[str, object]) -> JsonObject: ...
```

该函数读取已脱敏 `attempt`，返回：

```json
{
  "status": "completed",
  "resolved": true,
  "run_error": false,
  "eval_error": false,
  "error_present": false,
  "evaluation_completed": true,
  "evaluation_timed_out": false,
  "returncode": 0
}
```

规则：attempt `correct` 和 `evaluation.resolved` 必须都是 bool 且值一致；`status` 必须为 AgentCompass 固定枚举值；`run_error/eval_error` 从 status 确定；合法未解决与 evaluator 错误必须可区分。不返回 error 原文、tests、stdout/stderr 或 hidden test 名。

### 8.4 `dynsteer/adapter/swebench_pro/utils/patch.py`

只新增：

```python
def patch_summary(value: object) -> JsonObject: ...
```

返回形状：

```json
{
  "present": true,
  "unified_diff": true,
  "size_bytes": 1234,
  "sha256": "...",
  "changed_paths": ["src/example.py"]
}
```

实现只做纯文本解析：规范化 CRLF，用 UTF-8 字节计算 size/hash，从 `+++ b/...` 和 rename 头部提取路径，排除 `/dev/null`，按首次出现去重。`unified_diff` 只表示结构头存在，不表示 patch 可应用或能通过测试。不调用 `git apply`、compile、lint 或 evaluator。

### 8.5 `dynsteer/adapter/swebench_pro/harness.py`

只定义 `SWEBenchProHarness(BaseAgentCompassHarness)`，实现两个方法：

```python
class SWEBenchProHarness(BaseAgentCompassHarness):
    benchmark = "swebench_pro"

    def constraint_scorer(self) -> SWEBenchProConstraintScorer: ...

    def _build_run_data(
        self,
        detail: JsonObject,
        steps: list[TrajectoryStep],
    ) -> AgentCompassRunData: ...
```

`_build_run_data()` 只调用一次 `native_result_summary(detail)` 和 `patch_summary(detail["attempt"]["final_answer"])`，然后直接构造：

- `BenchmarkDefaultResult.score = 1.0 if resolved else 0.0`；
- `default_result.raw` 仅包含 `score_source="agentcompass_swebench_pro"`、`status`、`resolved`、`evaluation_completed`、`evaluation_timed_out`；
- `default_result.metrics` 仅包含可选 `returncode`；
- `final_state={"patch": patch_summary}`，不保存 patch 正文；
- `raw_summary` 仅包含 `score_source`、`run_error`、`eval_error`、`error_present` 与 `detail["provenance"]`。

合法的 `resolved=False` 仍正常返回 0 分；`run_error/eval_error` 也使用 0 分，但必须由 raw 字段区分，不冒充普通未解决。

### 8.6 `dynsteer/adapter/swebench_pro/scorer.py`

首版只定义稳定扩展类：

```python
class SWEBenchProConstraintScorer(GeneralScorer):
    """SWE-bench Pro 后续结构化约束评分扩展点。"""
```

类体仅用 docstring 作为合法 suite，不写 `pass`、空 `__init__`、伪评分方法或官方 evaluator 替代。它由 `SWEBenchProHarness.constraint_scorer()` 显式返回，作为用户要求的预留代码，不按死代码删除。

## 9. SkillsBench 逐文件修改

### 9.1 `dynsteer/adapter/skillsbench/utils/task.py`

只新增：

```python
def task_case_from_record(record: AgentCompassTaskRecord) -> TaskCase: ...
```

构造：

- `task_id=f"skillsbench::{record.task_id}"`；
- `case_id=record.task_id`；
- `task_description=record.question`；
- `metadata` 仅包含 `benchmark="skillsbench"`、`category`、`agentcompass_commit`；
- 不放入 `sample_dir`、`tests_dir`、task TOML、verifier timeout、expected reward 或未证实的 public assets；
- 其他 `TaskCase` 字段使用默认值。

### 9.2 `dynsteer/adapter/skillsbench/adapter.py`

与 SWE adapter 同样，只做 O(1) 查找和投影：

```python
class SkillsBenchAdapter(BaseBenchmarkAdapter):
    benchmark = "skillsbench"

    def adapt_task_case(...):
        records = load_task_records(self.benchmark, config)
        # 缺失时招出带 case_id 的 KeyError
        return task_case_from_record(records[case_id])
```

不覆盖本期无调用需求的其他 adapter 方法。

### 9.3 `dynsteer/adapter/skillsbench/utils/result.py`

只新增：

```python
def native_result_summary(detail: Mapping[str, object]) -> JsonObject: ...
```

返回：

```json
{
  "status": "completed",
  "score": 0.75,
  "correct": false,
  "reward_available": true,
  "run_error": false,
  "eval_error": false,
  "error_present": false,
  "test_return_code": 0,
  "test_error_present": false,
  "reward_error_present": false
}
```

规则：

- attempt `score` 为 int/float 且不是 bool 时，必须位于 `[0,1]`；
- score 缺失仅允许出现在非 completed status，此时 `reward_available=false`且对外 Default score 使用 0.0；
- 合法 `score=0.0` 必须保持 `reward_available=true`，不用 truthiness 判断；
- `evaluation.reward` 存在时必须与 attempt `score` 一致，不另建第二个 reward 来源；
- `correct` 必须为 bool，且 completed 状态下应与 `score == 1.0` 一致，不一致则报 schema 错误；
- 不保存 verifier stdout/stderr/reward.txt 原文。

### 9.4 `dynsteer/adapter/skillsbench/utils/artifact.py`

只新增：

```python
def artifact_manifest(files: object) -> list[JsonObject]: ...
```

`files` 是 runtime 已白名单化的 path -> value mapping。输出按 path 排序，每项只包含：

```json
{
  "path": "relative/output/path",
  "kind": "text|bytes|json|unknown",
  "size_bytes": 123,
  "sha256": "..."
}
```

文本按 UTF-8 计算，bytes 直接计算，JSON 用 `sort_keys=True/separators` 的 canonical bytes 计算。非 mapping 报 schema 错误；空 mapping 返回空列表。不读容器、不打开 path、不解析 Office/PDF/表格内容，不将 artifact 正文写入 final state。

### 9.5 `dynsteer/adapter/skillsbench/harness.py`

只定义 `SkillsBenchHarness(BaseAgentCompassHarness)` 并覆盖：

```python
def constraint_scorer(self) -> SkillsBenchConstraintScorer: ...

def _build_run_data(
    self,
    detail: JsonObject,
    steps: list[TrajectoryStep],
) -> AgentCompassRunData: ...
```

`_build_run_data()` 只调用一次 `native_result_summary(detail)` 和一次 `artifact_manifest(detail["attempt"]["files"])`，构造：

- `BenchmarkDefaultResult.score = summary["score"] if reward_available else 0.0`；
- `default_result.raw` 仅包含 `score_source="agentcompass_skillsbench"`、`status`、`correct`、`reward_available`、`test_error_present`、`reward_error_present`；
- `default_result.metrics` 仅包含可选 `test_return_code`；
- `final_state={"artifacts": manifest}`，不虚构固定版本没有提供的 required-output schema/presence；
- `raw_summary` 仅包含 `score_source`、`run_error`、`eval_error`、`error_present` 与 provenance。

### 9.6 `dynsteer/adapter/skillsbench/scorer.py`

与 SWE scorer 一致，只建立预留类型：

```python
class SkillsBenchConstraintScorer(GeneralScorer):
    """SkillsBench 后续结构化约束评分扩展点。"""
```

不运行 verifier，不按 artifact 类型预写大量无调用评分器。该类由 `SkillsBenchHarness.constraint_scorer()` 返回并接受独立测试。

## 10. 包初始化和 registry

### 10.1 `__init__.py`

`agentcompass/__init__.py`、`swebench_pro/__init__.py`、`skillsbench/__init__.py` 只保留包 docstring。两个 `utils/__init__.py` 也只保留“私有工具函数” docstring，与当前 ToolSandbox 目录风格一致。

生产文件直接从具体子模块 import 所需符号，不做无消费者 re-export，不做动态加载。

### 10.2 `dynsteer/adapter/registry.py`

在现有 ToolSandbox import 后增加：

```python
from dynsteer.adapter.skillsbench.adapter import SkillsBenchAdapter
from dynsteer.adapter.skillsbench.harness import SkillsBenchHarness
from dynsteer.adapter.swebench_pro.adapter import SWEBenchProAdapter
from dynsteer.adapter.swebench_pro.harness import SWEBenchProHarness
```

字典修改为：

```python
_ADAPTERS = {
    "toolsandbox": ToolSandboxAdapter,
    "swebench_pro": SWEBenchProAdapter,
    "skillsbench": SkillsBenchAdapter,
}
_HARNESSES = {
    "toolsandbox": ToolSandboxHarness,
    "swebench_pro": SWEBenchProHarness,
    "skillsbench": SkillsBenchHarness,
}
```

保留现有 `lower().replace("-", "_")`；不增加 alias dict、自动扫描、decorator registry 或 plugin loader。

## 11. Default 加载链最小修改

### 11.1 `dynsteer/adapter/loader.py`

只修改 `load_task_case()` 和 `_adapt_task_case()`，不新增第二个 loader。

`load_task_case()` 进入 case 循环前计算一次：

```python
is_default = str(config.metadata.get("method") or "").strip().lower() == "default"
```

缓存命中分支修改为：

```python
data = read_json_file(path, f"TaskCase 文件: {path}", dict)
task_case = parse_task_case(data)
if task_case.case_id != case_id:
    raise ValueError(...)
if not is_default:
    if task_case.milestone_graph is None:
        task_case = _adapt_task_case(config, adapter, case_id)
        save_task_case(path, task_case)
    else:
        before = json_safe(task_case)
        task_case = _postprocess_task_case(...)
        if json_safe(task_case) != before:
            save_task_case(path, task_case)
```

即 Default 缓存只经过现有 JSON parser 和 case ID 校验，不因后续动态评估字段为空而重复 adapter。

`_adapt_task_case()` 在完成返回类型和 case ID 校验后增加早返回：

```python
if str(config.metadata.get("method") or "").strip().lower() == "default":
    return task_case
```

该返回之后的现有非 Default 校验、enrich、postprocess 和 validate 原样保留。不提取只有两处使用的 `_is_default()` 包装函数，避免纯转发 helper。

这个修改仅是解除 Default 与后续动态评估字段的加载耦合，不在本方案中生成、补齐或消费这些字段。

### 11.2 `dynsteer/experiment/runner.py`

将 `run_experiment()` 中唯一的调用：

```python
prepared_task_cases, harness_config = prepare_task_cases(
    harness_config,
    force_adapt,
    refresh_dynamic_targets=True,
)
```

修改为：

```python
prepared_task_cases, harness_config = prepare_task_cases(
    harness_config,
    force_adapt,
    refresh_dynamic_targets=spec.method != ExperimentMethod.DEFAULT,
)
```

不修改 `prepare_task_cases()` 签名，不新建 `prepare_default_task_cases()`。非 Default 方法行为保持不变。

## 12. 配置文件

### 12.1 benchmark manifests

`data/swebench_pro/benchmark.json`：

```json
{
  "benchmark": "swebench_pro",
  "source_root": "../AgentCompass",
  "tool_backend": "agentcompass",
  "language": "en",
  "max_workers": 1
}
```

`data/skillsbench/benchmark.json`：

```json
{
  "benchmark": "skillsbench",
  "source_root": "../AgentCompass",
  "tool_backend": "agentcompass",
  "language": "en",
  "max_workers": 1
}
```

`source_root` 只用于 DynSTEER 现有 manifest/source 依赖模型与 editable dependency 对齐，不表示修改 AgentCompass。目录中不添加 `run_configs.json`、评分脚本、Dockerfile、dataset 或 verifier。`adapted_cases/` 只在运行时由现有 loader 生成。

`max_workers=1` 是首版必须保留的正确性限制：固定 AgentCompass 版本的 `bootstrap_runtime(..., force=True)` 修改进程全局 runtime settings，同一 DynSTEER 进程内并行使用不同 data/results dir 存在竞态。首版不为绕过该上游设计进程池或全局锁；若未来放宽并发，必须先用 AgentCompass 公开接口验证 runtime settings 已隔离。

### 12.2 唯一 experiment 配置

`data/experiments/agentcompass_cross_benchmark.json`：

```json
{
  "experiment_id": "agentcompass_cross_benchmark",
  "repeats": 1,
  "models": [
    {"model_id": "<model-id>"}
  ],
  "methods": ["default"],
  "benchmarks": [
    {
      "benchmark": "swebench_pro",
      "data_root": "data/swebench_pro",
      "case_ids": ["<swe-bench-pro-instance-id>"],
      "metadata": {
        "agentcompass": {
          "harness": "mini_swe_agent",
          "environment": "docker",
          "model_api_protocol": "openai-chat",
          "data_dir": "data/agentcompass",
          "benchmark_params": {
            "eval_timeout": 3600
          },
          "harness_params": {
            "step_limit": 250,
            "cost_limit": 3.0,
            "command_timeout": 2400,
            "timeout": 12000
          },
          "environment_params": {},
          "model_params": {},
          "enabled_recipes": [],
          "timeout_seconds": 14400
        }
      }
    },
    {
      "benchmark": "skillsbench",
      "data_root": "data/skillsbench",
      "case_ids": ["<skillsbench-task-id>"],
      "metadata": {
        "agentcompass": {
          "harness": "openhands",
          "environment": "docker",
          "model_api_protocol": "openai-chat",
          "data_dir": "data/agentcompass",
          "benchmark_params": {
            "timeout_multiplier": 1.0
          },
          "harness_params": {},
          "environment_params": {},
          "model_params": {},
          "enabled_recipes": [],
          "timeout_seconds": 14400
        }
      }
    }
  ]
}
```

`k/avgk/sample_ids/task_concurrency/enable_analysis/run_id` 由 runtime 强制，不在 JSON 重复填写。`model_id` 只在 `models[]` 中配置一次。SkillsBench 数据缓存为空时，在其已有 `benchmark_params` 内增加 AgentCompass 原生 `dataset_source_dir` 或 `dataset_zip_url`；DynSTEER 不增加同义字段。

## 13. 日志、错误和安全处理

### 13.1 错误分层

`runtime.py` 在以下边界使用 `raise ... from exc` 保留 cause，不吞异常：

1. AgentCompass import/version 或 component registry 错误；
2. dataset 缺失/下载/解析错误；
3. task ID 缺失、重复或不存在；
4. request 构建、environment open、harness run 错误；
5. run path/detail 缺失、重复或 schema 错误；
6. ACTF step/tool/observation 形状错误；
7. benchmark 原生 result 缺字段或 score 越界。

AgentCompass 已持久化的 `run_error/eval_error` 不作为 bridge 异常抛出，而是正常写出 0 分和明确 status；只有无法确定结果语义的 schema 破损才中止。

### 13.2 结构化中文日志

关键日志只记录：

- 事件名、benchmark、case ID、run ID、harness、environment、model ID；
- catalog task 数、ACTF step/call/observation 数；
- AgentCompass status、official score、detail path/hash；
- 异常类型与经截断的非敏感错误摘要。

禁止记录 prompt 全文、patch 全文、tool observation 全文、verifier stdout/stderr、API key、gold patch、hidden test 名。日志使用现有 DynSTEER logger，不再创建 AgentCompass bridge 专用 handler/buffer/file sink。

### 13.3 输出边界

- AgentCompass 原始 run dir 位于 DynSTEER case `raw/agentcompass-results/` 下，用于审计；
- DynSTEER `raw_summary.json`、`summary.json`、`report.json`、`trajectory.json` 只使用脱敏后对象；
- `TaskCase` 和 adapted case 不保存 gold/hidden 字段；
- final state 只保存 patch/artifact 摘要，不保存正文；
- provenance 仅在 `raw_summary` 保存一份，不在 summary、metrics、final state 中重复。

## 14. 测试修改明细

所有单元测试使用 PyTest。fixture 使用手工构造的最小脱敏 JSON，不复制真实 gold patch、hidden test 或 verifier 输出。

### 14.1 `tests/adapter/agentcompass/test_runtime.py`

必须覆盖：

- task record 白名单：SWE gold/test 字段不进入 record，Skills paths/task TOML 不进入 record；
- 重复 task ID 报错；
- 同 benchmark/data/params 连续调用只执行一次 native `load_tasks()`；参数改变时不误复用；
- 不将不可哈的 `HarnessRunConfig.metadata` 直接交给 `lru_cache`；
- request 强制单 case、`k=1`、concurrency 1、analysis false 和 `reuse=false`；相同身份生成相同 run key，两次真实执行生成不同 run ID；
- 明文 secret/base URL 配置被拒绝，环境变量值不进入日志/返回值；
- detail 精确匹配，缺失/重复/任务 ID 不一致报错；
- attempts 为空/多个时报错；
- `ground_truth/meta/analysis_result/tests/stdout/stderr/history/llm_config` 被丢弃；
- detail hash/path/run key/run ID/commit provenance 正确。

AgentCompass API、registry、filesystem detail 用 monkeypatch/tmp_path 隔离，不启动 Docker 和模型。测试结束显式调用 `_load_task_records_cached.cache_clear()`；该调用只存在测试，不再为测试新增 public clear API。

### 14.2 `tests/adapter/agentcompass/test_trajectory.py`

必须覆盖：

- assistant final content；
- 单 tool call + observation；
- 多个并行 tool call，先全部 outbound 再全部 result；
- 原生 ID、生成 ID、重复 ID 和非法 arguments；
- observation 按 ID、按位置、无法配对三种分支；
- 后续 user message，以及初始 prompt 不重复；
- token/LLM latency/env latency 不重复累计；
- null trajectory 与非法 schema；
- 输出 index/step ID 全局稳定。

测试在 converter 外使用现有 `AgentStepTracker` 摄入已配对输出，验证并行 closure；不向 converter 加测试专用 tracker 开关。

### 14.3 `tests/adapter/agentcompass/test_harness.py`

- `list_cases()` 使用缓存 mapping；
- `start_case()` 拒绝未知 case；
- 首次 `advance_case()` 对 run/convert/build 各调用一次，返回空 snapshots 和 false continue；
- 第二次 advance 不重复执行；
- final/raw/default 只读 run data，run data 未就绪时报错；`metrics_from_session()` 继承基类并返回空 mapping；
- 类不覆盖 `stop_case/teardown_case/initial_state_from_session`。

### 14.4 benchmark 专用测试

SWE：

- adapter 任务文本及 metadata 白名单；
- resolved true/false、run error、eval error 映射；
- patch absent/normal/delete/rename/CRLF、changed-path 去重和 hash；
- final state/report 不包含 patch 正文或 hidden test 字段；
- `constraint_scorer()` 返回 `SWEBenchProConstraintScorer`。

Skills：

- adapter 不泄漏 tests path/task TOML/verifier config；
- reward `0/partial/1`、缺失、越界、bool 非法值；
- correct/reward 一致性；
- artifact 文本/bytes/JSON/空 mapping 摘要及稳定排序；
- final state 不包含 artifact 正文、verifier stdout/stderr；
- `constraint_scorer()` 返回 `SkillsBenchConstraintScorer`。

### 14.5 loader/experiment 回归

`test_loader_default.py` 覆盖：

- Default 新适配的基础 TaskCase 可保存；
- Default 缓存命中不再调 adapter；
- 非 Default 的原有校验/postprocess 回归不变；
- case ID 错配仍报错。

`test_agentcompass_default.py` mock `prepare_task_cases()`，断言 Default 传 `refresh_dynamic_targets=False`，其他 method 传 true；不运行真实 experiment。

### 14.6 覆盖率与命令

核心模块行覆盖率不低于 80%：

```powershell
uv run pytest tests/adapter tests/experiment/test_agentcompass_default.py `
  --cov=dynsteer.adapter.agentcompass `
  --cov=dynsteer.adapter.swebench_pro `
  --cov=dynsteer.adapter.skillsbench `
  --cov-report=term-missing `
  --cov-fail-under=80
```

`loader.py` 和 `experiment/runner.py` 是大型存量模块，不用本次少量分支变更去要求其整个文件 80% 覆盖；它们的本次新增/修改分支必须在上述回归测试中 100% 触发。新增 AgentCompass 适配包作为本次核心功能，合并行覆盖率不低于 80%。

测试不得为达到覆盖率而添加无生产调用函数。测试后清理 `__pycache__`、`.pytest_cache`、`.coverage`、`htmlcov/` 等中间产物。

## 15. API 文档与 README

### 15.1 `docs/apis/agentcompass.md`

文档按以下固定结构编写：

1. 支持的 benchmark 与固定 AgentCompass commit；
2. `metadata.agentcompass` 字段表（必填、可选、默认、安全限制）；
3. `load_task_records()` 和 `run_agentcompass_case()` 签名、输入、输出、异常；
4. TaskCase 字段投影表；
5. ACTF -> TrajectoryStep 映射表；
6. Default score/status/final-state/provenance 输出表；
7. 数据目录、原始 AgentCompass run dir 与脱敏边界；
8. 已知限制：整任务调用、无 snapshot、无中途取消、Default only。

### 15.2 `docs/apis/harness.md`

仅在 benchmark 支持表增加两行，说明两者在单次 `advance_case()` 中完成 AgentCompass 整任务执行，`snapshots=[]`，首版只可用 Default。不复制 `agentcompass.md` 的配置表。

### 15.3 `README.md`

只补充：

- `../AgentCompass` 固定 commit 与 `uv sync --locked`；
- `MODEL_BASE_URL`/`MODEL_API_KEY` 环境变量；
- 如何替换 experiment 中三个占位 ID；
- `uv run python main.py --exp data/experiments/agentcompass_cross_benchmark.json --workers 1`；
- 不需要本地 SWE-bench_Pro-os 或 DynSTEER 官方评分脚本。

## 16. 实施顺序

1. **依赖与 fixture 边界**：确认 AgentCompass commit，更新 `pyproject.toml/uv.lock`，用固定代码的 `TaskSpec/RunResult/StepInfo` 构造最小脱敏 fixture。
2. **common runtime**：实现配置校验、catalog 缓存、request 执行、detail 精确选取和脱敏；先跑 `test_runtime.py`。
3. **ACTF converter**：实现单次线性转换和协议 fixture；先跑 `test_trajectory.py`。
4. **common harness**：实现整任务单 advance 生命周期；先用 fake subclass 验证一次性。
5. **SWE-bench Pro**：按 task -> result -> patch -> adapter -> harness -> reserved scorer 顺序落地。
6. **SkillsBench**：按 task -> result -> artifact -> adapter -> harness -> reserved scorer 顺序落地。
7. **registry 与 Default loader**：注册两个 benchmark，落地 Default 早返回和 experiment refresh 参数。
8. **data/docs**：添加 manifests、唯一 experiment、API 文档和 README。
9. **单元验收**：运行覆盖率命令，修复 lint/type/import 问题，清理中间文件。
10. **真实 smoke**：每个 benchmark 先各跑 1 个 case，确认结果 schema 后再跑 SWE 3 个跨语言 case 和 Skills 8 domain 样例。不在单元测试中启动这些昂贵流程。

## 17. 真实 smoke 验收项

1. registry 能发现 `swebench_pro` 和 `skillsbench`；
2. 两个 adapter 生成的 adapted case 均只包含任务可见字段；
3. 两个 case 各只产生一个 AgentCompass run 和一个精确匹配 detail；
4. SWE score 与 AgentCompass `correct` 一致；Skills score 与 AgentCompass partial reward 一致；
5. run/eval error 与合法 0 分可区分；
6. DynSTEER trajectory 中 tool call/result 路由、call ID、token 和 latency 不重复；
7. `snapshots` 为空，且没有从 shell 命令猜测 workspace 状态；
8. adapted case、DynSTEER summary/report/trajectory 全量扫描无 gold patch、hidden test、verifier stdout/stderr、API key；
9. DynSTEER 配置不包含 `SWE-bench_Pro-os`、`scripts_dir`、`dockerfiles_dir`、`upstream_source_root`；
10. 不修改 AgentCompass、SWE-bench_Pro-os 或 SkillsBench 源码工作区；
11. 两个 harness 的 `constraint_scorer()` 返回对应预留 scorer；
12. 除两个显式预留 scorer 外，所有新增生产函数都在 Default smoke 数据流中有至少一个调用方。

## 18. 冗余与死代码审计清单

落地后逐项检查：

| 审计项 | 必须满足的结果 |
|---|---|
| AgentCompass import | 只在 common `runtime.py` 出现 |
| dataset 加载 | 只在 `_load_task_records_cached()` 出现，每配置一次 |
| case lookup | adapter/harness 都是 mapping O(1) 查找 |
| AgentCompass 执行 | 只有 `run_agentcompass_case()` 一个入口 |
| detail parse | 每 case 只在 runtime 一次 |
| ACTF convert | 每 case 只在 common harness 一次 |
| closure tracking | 只使用现有 output writer 生产跟踪 |
| output writing | 只使用 `write_default_case_outputs()` |
| official score | 只映射 AgentCompass 已计算结果，不重新运行 |
| artifact/patch | 只计算摘要，不读 workspace、不重新执行 |
| utils 函数 | 每个都有 adapter/harness 生产调用；单文件 helper 不对外暴露 |
| scorer | 仅两个用户指定的预留类，由 harness 返回 |
| package init | 无动态 import、无未使用 re-export |
| config | 只有 experiment `metadata.agentcompass` 一个运行参数来源 |
| Default loader | 复用现有 loader/参数，无第二套函数 |
| 空覆盖 | 具体 harness 不覆盖无额外行为的 lifecycle 方法 |
| 兼容分支 | 只面向固定 AgentCompass schema，不预写未来版本 fallback |
| 未使用 import | Ruff/静态检查为 0 |

最终使用 `rg` 检查 AgentCompass/SWE-bench 相关字段和新增符号，对每个定义确认生产调用方。不以测试调用代替生产调用证明。

## 19. 首版完成标准

- 所有修改仅位于 DynSTEER；
- 两个 benchmark 可通过统一 experiment + Default 运行；
- AgentCompass 继续唯一负责数据、环境、agent harness 和官方评分；
- DynSTEER 不依赖 SWE-bench_Pro-os/SkillsBench 源仓库，不配置官方脚本路径；
- task catalog 每配置加载一次，单 case 查找 O(1)；
- 每 case 只执行一次 AgentCompass、解析一次 detail、转换一次 ACTF；
- SWE official resolved 和 Skills partial reward 映射正确；
- 合法 0 分与 run/eval error 可区分；
- 无 snapshot 伪造、命令重放或 workspace 猜测；
- 两个 scorer 作为明确预留类保留，其他新符号均有生产调用方；
- 核心单元测试行覆盖率不低于 80%；
- 无未使用 import、纯转发 helper、空 lifecycle override、重复 DTO、第二套配置或 benchmark-specific writer；
- 不执行 git commit，由用户自行审核和提交。

## 附录A. 项目中没有把握实现的模块部分

最没有把握的部分是 **AgentCompass 固定版本在不同 harness 下的 ACTF/detail 边界一致性，以及 SkillsBench artifact 的可见性**，原因如下：

1. Mini-SWE-agent 和 OpenHands 虽都输出 ACTF v1.0，但 tool call ID、并行 action、unfinished command 和 observation 配对可能存在边界差异。该问题只能通过真实 smoke detail 验证，不能通过预写多版本兼容分支消除。
2. 固定 AgentCompass SkillsBench `PreparedTask.output` 为空，OpenHands 不一定会将用户要求的所有复杂文件放入 `RunResult.artifacts.file`。因此首版只能对已存在 artifact 生成 manifest；缺失时返回空 manifest，不进入容器补采，也不虚构 required-output presence。
3. AgentCompass `BaseHarness.run_task()` 是整任务调用，DynSTEER 只能在任务结束后取得 ACTF；首版无法真正中途停止 AgentCompass 执行。
4. AgentCompass raw detail 可能持久化 ground truth 和 verifier 输出。本方案可以保证 DynSTEER 派生产物脱敏，但 `raw/agentcompass-results/` 是为可审计性保留的 AgentCompass 原始输出，运行环境必须对该目录使用受限访问权限。

上述限制都不能通过修改 AgentCompass、monkey patch、命令重放或预写未来 schema 解决。本方案只承诺固定版本、Default 整任务执行、现有字段转换和缺失能力的明确标记。
