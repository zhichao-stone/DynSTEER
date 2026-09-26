# DynSTEER 跨 Benchmark 实验就绪与配置命名修复方案

## 1. 背景与结论

本方案解决当前跨 Benchmark 实验验收中发现的三类问题：

1. `no_milestone_graph` 消融臂的空图被旧局部变量覆盖，ToolSandbox 消融失效，SWE-bench Pro 与 SkillsBench 首次适配时会崩溃。
2. SWE-bench Pro 与 SkillsBench 没有 origin milestone graph，但正式主实验、消融实验和两个 partial 实验都没有配置 milestone generator，导致所有非 default 实验臂无法完成静态适配。
3. 2026-09-15 前缀的正式配置命名不符合 `{数据集}_{实验策略}` 约定，SkillsBench 主实验缺少不足 100 case 的原因说明。

本方案只修改必要代码、实验配置和当前 API 文档；不重写历史方案文档，不删除已有数据，不修改 `../AgentCompass` 和 `../ToolSandbox`。

## 2. 命名结论

跨数据集配置使用 `cross_benchmark` 表示数据集集合。三份正式配置统一为：

| 当前文件 | 目标文件 | 目标 `experiment_id` |
| --- | --- | --- |
| `data/experiments/2026-09-15-main.json` | `data/experiments/cross_benchmark_main.json` | `cross_benchmark_main` |
| `data/experiments/2026-09-15-ablation.json` | `data/experiments/cross_benchmark_ablation.json` | `cross_benchmark_ablation` |
| `data/experiments/2026-09-15-intervention.json` | `data/experiments/cross_benchmark_intervention.json` | `cross_benchmark_intervention` |

同时把 `swe-bench-pro_partial_main.json` 规范为 benchmark ID 一致的下划线形式：

| 当前文件 | 目标文件 | 目标 `experiment_id` |
| --- | --- | --- |
| `data/experiments/swe-bench-pro_partial_main.json` | `data/experiments/swebench_pro_partial_main.json` | `swebench_pro_partial_main` |

以下文件保持不变：

```text
data/experiments/skillsbench_partial_main.json
data/experiments/toolsandbox_main.json
data/experiments/toolsandbox_partial_main.json
```

`results/exp` 和 `runs/exp` 当前没有 `dynsteer-2026-09-15-*` 产物，因此直接重命名文件和 `experiment_id`，不做结果目录迁移或旧 ID 兼容层。

## 3. 代码修改

### 3.0 修改总览

| 文件 | 修改位置 | 修改动作 |
| --- | --- | --- |
| `dynsteer/adapter/loader.py` | `_adapt_task_case()` 第 141-166 行附近的 graph 分支 | 让 `use_milestone_graph=false` 直接替换局部 `graph`，函数末尾只保留一次最终赋值 |
| `dynsteer/harness/config.py` | `_GENERATOR_CONFIG_FIELDS` 第 30-41 行 | 增加 `"api_key"`，允许 milestone generator 直接使用实验私有凭据 |
| `dynsteer/llm/factory.py` | `build_llm_from_config()` 第 49-85 行 | 让显式 `config["api_key"]` 优先于 provider 默认环境变量，并修正函数注释 |

不修改函数签名，不新增 helper，不引入策略对象或兼容层。上述三个文件已经具备需要的导入。

### 3.1 修复空 milestone graph 消融臂

修改 `dynsteer/adapter/loader.py` 的 `_adapt_task_case()`。

当前代码位于 `_adapt_task_case()` 内、default 提前返回之后：

```python
graph = task_case.milestone_graph
has_origin_graph = graph is not None
generation = config.milestone_generation

if not config.use_milestone_graph:
    task_case.milestone_graph = MilestoneGraph(
        nodes=[],
        edges=[],
        minefields=[],
        metadata={
            "source": "disabled",
            "empty_graph_completion_basis": "whole_trajectory",
        },
    )
elif generation.use_origin_milestone and has_origin_graph:
    graph.metadata["source"] = "origin"
else:
    view = adapter.generator_task_view(config, task_case, case_id)
    llm = build_llm_from_config(generation.generator)
    if llm is None:
        raise ValueError(f"TaskCase 需要自动生成 milestone，但未配置 generator: {case_id}")
    graph, report = compile_task_case(view, generation, llm)
    if report.generation_status == "generation_failed":
        raise RuntimeError(f"milestone generation failed: {case_id}")
    graph.metadata["source"] = "generated"
    task_case.metadata["milestone_generation"] = report.to_dict()

task_case.milestone_graph = graph if graph.topology is not None else enrich_milestone_graph(graph)
```

问题在于：进入 `not config.use_milestone_graph` 后，`task_case.milestone_graph` 已经变成空图，但局部变量 `graph` 仍指向原图。若原图存在，函数末尾用 `graph` 回写会覆盖空图；若原图为 `None`，函数末尾访问 `graph.topology` 会触发 `AttributeError`。

将上面的分支替换为：

```python
graph = task_case.milestone_graph
has_origin_graph = graph is not None
generation = config.milestone_generation

if not config.use_milestone_graph:
    graph = MilestoneGraph(
        nodes=[],
        edges=[],
        minefields=[],
        metadata={
            "source": "disabled",
            "empty_graph_completion_basis": "whole_trajectory",
        },
    )
elif generation.use_origin_milestone and has_origin_graph:
    graph.metadata["source"] = "origin"
else:
    view = adapter.generator_task_view(config, task_case, case_id)
    llm = build_llm_from_config(generation.generator)
    if llm is None:
        raise ValueError(
            f"TaskCase 需要自动生成 milestone，但未配置 generator: {case_id}"
        )
    graph, report = compile_task_case(view, generation, llm)
    if report.generation_status == "generation_failed":
        raise RuntimeError(f"milestone generation failed: {case_id}")
    graph.metadata["source"] = "generated"
    task_case.metadata["milestone_generation"] = report.to_dict()

task_case.milestone_graph = (
    graph if graph.topology is not None else enrich_milestone_graph(graph)
)
```

除上述 if/elif/else 与末尾赋值外，函数内其他语句保持不变。特别是紧随其后的 `_postprocess_task_case()`、adaptation cost 统计和 JSON digest 逻辑都继续复用。

空图继续通过现有 `enrich_milestone_graph()` 生成包含 `__start__` 和 `__finish__` 的增强拓扑，再由现有 stage 机制生成 whole-trajectory stage。不新增第二个“无图阶段器”。已有 `data/toolsandbox/adapted_cases/*.no-milestone-graph.json` 内容为空图，可在验证后继续复用；不批量删除或重建。

### 3.2 支持 generator 直接使用实验凭据

本项目当前按私有实验仓库使用，实验配置允许直接保存 `api_key` 与 `base_url`。`base_url` 已是 generator 的合法字段；还需要修改两处代码，使 `api_key` 也能直接配置。

修改 `dynsteer/harness/config.py` 的 `_GENERATOR_CONFIG_FIELDS`，在现有字段集合中加入：

```python
"api_key",
```

修改后：

```python
_GENERATOR_CONFIG_FIELDS = {
    "provider",
    "model",
    "base_url",
    "api_key",
    "timeout_seconds",
    "temperature",
    "max_tokens",
    "max_retries",
    "retry_base_seconds",
    "retry_max_seconds",
    "seed",
}
```

再修改 `dynsteer/llm/factory.py` 的 `build_llm_from_config()`，让显式 `api_key` 的优先级高于环境变量 fallback：

当前函数 docstring 中下面一行：

```python
        env: 环境变量来源，API key 只从这里读取。
```

改为：

```python
        env: 环境变量来源；配置未显式提供 api_key/base_url 时从这里读取。
```

当前 `LLMConfig` 构造参数中的：

```python
        api_key=_config_setting(source, config, "api_key_env", "API_KEY"),
        base_url=optional_str(config.get("base_url")) or _config_setting(source, config, "base_url_env", "BASE_URL"),
```

将 `api_key` 行改为：

```python
api_key=(
    optional_str(config.get("api_key"))
    or _config_setting(source, config, "api_key_env", "API_KEY")
),
```

`base_url` 行保持不变。它已经是“显式 `base_url` 优先、否则读取环境变量”的语义。

这里复用文件顶部已导入的 `optional_str`，不新增解析函数或凭据格式兼容层。显式 `base_url` 已有相同优先级，不需要改。

修改后，generator 的取值优先级是：

1. `generator.api_key` 显式明文凭据；
2. 未显式提供时，按现有 `_config_setting()` 规则读取 provider 默认环境变量；
3. `generator.base_url` 显式 endpoint 优先，未提供时读取 provider 默认环境变量。

不强制要求环境变量，也不在日志中额外展开凭据值。

### 3.3 不新增配置兜底

不为空 generator 添加 `build_llm_from_env()` 隐藏 fallback。SWE-bench Pro 与 SkillsBench 的 generator 必须显式写入实验配置，避免实验结果依赖启动 shell 中未记录的 judge 环境变量。ToolSandbox 有 origin graph，继续保持当前可省略 generator 的行为。

## 4. 实验配置修改

### 4.1 SWE/Skills milestone generator

先按下表定位 benchmark spec。括号内是重命名前的当前文件；重命名后使用目标文件名：

| 目标配置 | 当前 benchmark spec 行 | 插入位置 |
| --- | --- | --- |
| `cross_benchmark_main.json`（当前 `2026-09-15-main.json`） | SWE-bench Pro：第 601-704 行 | 第 602 行 `"data_root"` 之后、第 603 行 `"case_ids"` 之前 |
| `cross_benchmark_main.json`（当前 `2026-09-15-main.json`） | SkillsBench：第 773-876 行 | 第 774 行 `"data_root"` 之后、第 775 行 `"case_ids"` 之前 |
| `cross_benchmark_ablation.json`（当前 `2026-09-15-ablation.json`） | SWE-bench Pro：第 218-271 行 | 第 219 行 `"data_root"` 之后、第 220 行 `"case_ids"` 之前 |
| `cross_benchmark_ablation.json`（当前 `2026-09-15-ablation.json`） | SkillsBench：第 341-394 行 | 第 342 行 `"data_root"` 之后、第 343 行 `"case_ids"` 之前 |
| `swebench_pro_partial_main.json` | SWE-bench Pro：第 85-120 行 | 第 86 行 `"data_root"` 之后、第 87 行 `"case_ids"` 之前 |
| `skillsbench_partial_main.json` | SkillsBench：第 85-120 行 | 第 86 行 `"data_root"` 之后、第 87 行 `"case_ids"` 之前 |

行号均为重命名和插入前的当前行号。JSON 中应按唯一锚点文本定位；同一文件内的多处插入不要依赖插入后的固定行号。

每个目标 benchmark spec 的字段顺序统一为 `benchmark`、`data_root`、`milestone_generation`、`case_ids`、`metadata`。该顺序只影响人工可读性，不影响配置解析。

六处统一执行同一个锚点替换。下面模板中的 `"data_root"` 值保留目标 spec 当前值，其余缩进和字段完全一致；`<使用现有 Qwen client 配置中的明文 key>` 按下文来源替换：

```json
      "data_root": "<当前 spec 的 data_root>",
      "milestone_generation": {
        "use_origin_milestone": true,
        "target_candidate_graph_count": 6,
        "max_candidate_batch_count": 4,
        "generator": {
          "provider": "qwen",
          "model": "qwen-plus-2025-12-01",
          "api_key": "<使用现有 Qwen client 配置中的明文 key>",
          "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
          "temperature": 0,
          "max_tokens": 32768,
          "timeout_seconds": 3600,
          "max_retries": 3
        }
      },
      "case_ids": [
        "<保留当前 spec 的全部 case_ids>"
      ],
```

也就是说，仅在现有 `"data_root"` 行和 `"case_ids": [` 行之间插入 `"milestone_generation"` 对象及其尾部逗号，不移动、不重排现有 `case_ids` 数组。

在以下配置的每个目标 benchmark spec 中添加同一份 `milestone_generation`：

```text
data/experiments/swebench_pro_partial_main.json
data/experiments/skillsbench_partial_main.json
data/experiments/cross_benchmark_main.json
data/experiments/cross_benchmark_ablation.json
```

目标 benchmark 为：

```text
swebench_pro
skillsbench
```

`toolsandbox` 不添加该字段。`cross_benchmark_intervention.json` 只包含 ToolSandbox，也不添加该字段。

六处使用完全相同的 generator 参数；上方的锚点替换块就是最终写入内容，不再维护第二份配置模板。

`generator.api_key` 的值实施时从现有 ToolSandbox 配置的 Qwen client 复制。来源定位为：

```text
data/experiments/toolsandbox_partial_main.json
  -> models[]
  -> model_id 属于 qwen-plus-2025-12-01 或 qwen3-max-2026-01-23
  -> harness_metadata.agent_client.api_key
```

不把真实 key 写入本方案文档。目标配置中的 `<使用现有 Qwen client 配置中的明文 key>` 实施时替换为该字段的实际字符串值。

`use_origin_milestone=true` 的语义是“有 origin graph 时使用 origin graph”；SWE/Skills 没有 origin graph，因此实际进入 generator 分支。generator 首次生成后写入 `data/<benchmark>/adapted_cases/<case>.json`。full graph 在正式实验与 partial 实验之间复用同一磁盘缓存；`no_milestone_graph` 继续使用独立的 `.no-milestone-graph.json` 缓存。

### 4.2 SkillsBench 主实验缺失原因

在 `cross_benchmark_main.json` 的 SkillsBench `metadata` 中保留现有字段，并在第 867 行 `"selected_case_count": 87,` 后新增 `missing_reason`。当前锚点是第 864-868 行：

```json
      "metadata": {
        "available_case_count": 87,
        "requested_case_count": 87,
        "selected_case_count": 87,
        "agentcompass": {
```

替换为：

```json
      "metadata": {
        "available_case_count": 87,
        "requested_case_count": 87,
        "selected_case_count": 87,
        "missing_reason": "SkillsBench v1.1 官方全量共 87 个任务，不足目标 100，使用全量任务且不重复补齐。",
        "agentcompass": {
```

目标对象摘要为：

```json
{
  "benchmark": "skillsbench",
  "metadata": {
    "available_case_count": 87,
    "requested_case_count": 87,
    "selected_case_count": 87,
    "missing_reason": "SkillsBench v1.1 官方全量共 87 个任务，不足目标 100，使用全量任务且不重复补齐。",
    "agentcompass": {
      "harness": "openhands"
    }
  }
}
```

上面的 JSON 只展示需要确认的字段；`agentcompass` 内现有其他字段全部保留，不重排或删除。

最终 metadata 的数量与原因字段呈现为：

```json
{
  "available_case_count": 87,
  "requested_case_count": 87,
  "selected_case_count": 87,
  "missing_reason": "SkillsBench v1.1 官方全量共 87 个任务，不足目标 100，使用全量任务且不重复补齐。"
}
```

`cross_benchmark_ablation.json` 的 SkillsBench 目标为 50，可用任务 87 足够，不添加 `missing_reason`。

### 4.3 重命名与 ID 同步

使用文件系统重命名而不是复制，避免留下冗余配置副本。重命名后同步修改五个 JSON 顶层的 `experiment_id` 为第 2 节表格中的目标值。

执行重命名时使用如下精确映射：

```powershell
Rename-Item -LiteralPath 'data/experiments/2026-09-15-main.json' -NewName 'cross_benchmark_main.json'
Rename-Item -LiteralPath 'data/experiments/2026-09-15-ablation.json' -NewName 'cross_benchmark_ablation.json'
Rename-Item -LiteralPath 'data/experiments/2026-09-15-intervention.json' -NewName 'cross_benchmark_intervention.json'
Rename-Item -LiteralPath 'data/experiments/swe-bench-pro_partial_main.json' -NewName 'swebench_pro_partial_main.json'
```

JSON 内的精确字符串替换如下：

| 文件 | 查找 | 替换 |
| --- | --- | --- |
| `cross_benchmark_main.json` | `"experiment_id": "dynsteer-2026-09-15-main"` | `"experiment_id": "cross_benchmark_main"` |
| `cross_benchmark_ablation.json` | `"experiment_id": "dynsteer-2026-09-15-ablation"` | `"experiment_id": "cross_benchmark_ablation"` |
| `cross_benchmark_intervention.json` | `"experiment_id": "dynsteer-2026-09-15-intervention"` | `"experiment_id": "cross_benchmark_intervention"` |
| `swebench_pro_partial_main.json` | `"experiment_id": "swe-bench-pro_partial_main"` | `"experiment_id": "swebench_pro_partial_main"` |

其中四个 `experiment_id` 都只出现在当前文件第 2 行；`swebench_pro_partial_main.json` 的当前值在第 2 行，其余三份文件也在重命名后于第 2 行替换。

`cross_benchmark_ablation.json` 中有两处 `subset_of`，必须同步替换：

```json
{
  "subset_of": "data/experiments/2026-09-15-main.json"
}
```

替换为：

```json
{
  "subset_of": "data/experiments/cross_benchmark_main.json"
}
```

实施后用只读搜索确认 `data/experiments` 中不再有 `2026-09-15-main.json` 字符串引用；历史 `docs/plans/*.md` 中的引用不修改。

配置内没有显式 `runs_dir` 和 `results_dir` 时，继续由 `experiment_id` 派生：

```text
runs/exp/<experiment_id>
results/exp/<experiment_id>
```

因此不需要额外修改路径字段。

### 4.4 保留现有明文凭据

不修改、不清理、不替换以下本地配置中的 `agent_client` 与 `user_client`：

```text
data/experiments/toolsandbox_main.json
data/experiments/toolsandbox_main_1.json
data/experiments/toolsandbox_main_2.json
data/experiments/toolsandbox_main_3.json
data/experiments/toolsandbox_main_4.json
data/experiments/toolsandbox_partial_main.json
data/experiments/toolsandbox_partial_retest.json
data/experiments/toolsandbox_partial_test.json
```

这些是私有实验配置，保留现有明文 API key 和 base URL。新增 SWE/Skills generator 时，直接复用现有 Qwen client 中的同类型凭据与 endpoint；不做 provider 侧轮换、脱敏或迁移。

## 5. 引用与文档更新

### 5.1 当前入口引用

更新以下当前入口和 API 文档中的配置路径：

```text
README.md
scripts/start_experiment.sh
scripts/start_experiment_no_docker.sh
docs/apis/agentcompass.md
docs/apis/experiment.md
```

对下列位置执行唯一替换：`data/experiments/2026-09-15-main.json` 改为 `data/experiments/cross_benchmark_main.json`。

```text
README.md
  - 当前约 140 行：无 Docker 启动示例
  - 当前约 146 行：Docker 启动示例
scripts/start_experiment.sh
  - 当前 usage 示例约 25 行
scripts/start_experiment_no_docker.sh
  - 当前 usage 示例约 26 行
docs/apis/agentcompass.md
  - 当前约 14、20、21 行：启动示例
```

历史方案 `docs/plans/2026-09-15-*.md` 和 `docs/plans/2026-09-18-*.md` 作为历史记录不改名、不重写。

### 5.2 修复 experiment API 文档

重写 `docs/apis/experiment.md` 中第 35-168 行的乱码旧内容；第 1-34 行的元评估指标、Agent step 计数、动态 target 生命周期和随机种子说明保留。保留部分结束后的目标全文为：

````markdown
## 配置入口

```bash
./scripts/start_experiment_no_docker.sh --exp data/experiments/cross_benchmark_main.json --source ../AgentCompass --workers 1
```

`main.py --exp PATH` 可直接展开同一配置，但没有 AgentCompass 环境时必须先用启动脚本完成 bootstrap。`--force_adapt` 强制重建 adapted case 并自动等价于 `--force_eval`；`--force_eval` 只强制重跑评估产物；`--no_sum` 跳过实验级汇总。

## 配置字段

统一实验 JSON 顶层字段如下：

```json
{
  "experiment_id": "cross_benchmark_main",
  "repeats": 1,
  "models": [{"model_id": "qwen-plus-2025-12-01"}],
  "methods": ["default", "dynsteer_replay"],
  "threshold_profiles": {"default": {}},
  "benchmarks": [
    {
      "benchmark": "swebench_pro",
      "data_root": "data/swe-bench-pro",
      "milestone_generation": {
        "generator": {
          "provider": "qwen",
          "model": "qwen-plus-2025-12-01"
        }
      },
      "case_ids": ["<固定 case ID>"],
      "metadata": {}
    }
  ]
}
```

顶层展开顺序是 `benchmarks × models × methods × threshold_matrix × repeats`。`benchmarks[*]` 支持 `benchmark`、`data_root`、`milestone_generation`、`case_ids/scenarios` 和 `metadata`；`models[*]` 支持 `model_id`、`judge_profile`、`harness_metadata` 和 `metadata`；方法项支持字符串或 `{ "method": ..., "judge_profile": ..., "strategy": ..., "harness_metadata": ..., "metadata": ... }`。

没有 `runs_dir`/`results_dir` 时，默认从 `experiment_id` 派生为 `runs/exp/<experiment_id>` 和 `results/exp/<experiment_id>`。`case_ids` 必须显式固定，保证跨实验可比。

## Milestone generator

`milestone_generation.generator` 属于 benchmark spec，字段集合是 `provider`、`model`、`base_url`、`api_key`、`timeout_seconds`、`temperature`、`max_tokens`、`max_retries`、`retry_base_seconds`、`retry_max_seconds` 和 `seed`。私有实验配置允许直接写 `api_key` 和 `base_url`；未写 `api_key` 时按 provider 默认环境变量读取。

`use_origin_milestone=true` 表示有 origin graph 时使用 origin graph。SWE-bench Pro 和 SkillsBench 没有 origin graph，因此必须提供非空 generator。ToolSandbox 有 origin graph，可以省略 generator。

## 方法

- `default`：运行 benchmark 原生 Agent。
- `dynsteer_evaluate`：在线执行 DynSTEER 评估。
- `dynsteer_evaluate_guided`：在线 guided 臂，非 fatal stop 最多执行 `max_interventions` 次公开过程引导；fatal minefield 硬停止。
- `dynsteer_replay`：完整动态路由、动态权重、雷区和 policy stop。
- `dynsteer_replay_static`：静态路由和静态权重。
- `dynsteer_replay_static_weighting`：动态路由、静态权重。
- `dynsteer_replay_static_routing`：静态路由、动态权重。
- `dynsteer_replay_no_minefields`：保留 milestone graph 和 stop 策略，关闭雷区。
- `dynsteer_replay_no_milestone_graph`：替换为空 graph，仅保留 `__finish__` whole-trajectory stage；使用 `.no-milestone-graph.json` 缓存。
- `dynsteer_replay_no_policy_stop`：保留过程评分，关闭 policy stop。

消融方法使用独立 enum、输出目录和聚合键，不覆盖 full 方法结果。

## 执行与输出

每个 `ExperimentRunSpec` 先准备一次 TaskCase，同一 spec 的 default 和 replay 共享该批 TaskCase 的深拷贝。实验级输出位于 `results/exp/<experiment_id>`，run 产物位于 `runs/exp/<experiment_id>`。存在 experiment metadata 时，case 路径为 `<benchmark>/<model_id>/r<repeat_index>/<method>/<case_id>`。

- `index.json`：按 benchmark、method、model、repeat 和 case 组织，case 叶子保留 `score`、`native_score`、覆盖/雷区/终止信息、interventions、适配与运行指标。
- `scores.json`：`method -> benchmark -> model_id -> average_score`。
- `metrics.json`：效率、成本、区分度、DEFAULT↔replay delta、排名一致性、repeat 一致性、coverage/minefield/termination 计数和 `task_completion`。
- `costs.json`：逐 case 三段成本、终止分类、适配分摊、相对 DEFAULT delta 和证据路径。

`task_completion` 只由 native score 计算；replay 缺少 native score 时不回填 DynSTEER score，也不进入完成率分母。

## 凭据边界

`milestone_generation.generator.api_key` 是 DynSTEER 静态 milestone generator 凭据，允许保存在本私有仓库实验配置中。AgentCompass run request 的模型凭据边界不同：endpoint 和密钥仍只从 `MODEL_BASE_URL`、`MODEL_API_KEY` 读取，`metadata.agentcompass` 继续禁止明文凭据字段。
````

`docs/apis/agentcompass.md` 中“`metadata.agentcompass` 禁止明文 `api_key`/`base_url`”的约束保留，因为该约束针对 AgentCompass run request 的模型凭据边界；`milestone_generation.generator` 是 DynSTEER judge/generator 配置，两者不冲突。文档需要把这两个边界分开描述。

文档统一保存为 UTF-8，中文内容不得乱码。

### 5.3 补充 generator 配置说明

在 `docs/apis/milestone.md` 中将第 16-23 行的 `MilestoneGenerationConfig` 字段说明替换为：

````markdown
`MilestoneGenerationConfig` 字段为：

- `use_origin_milestone`；
- `target_candidate_graph_count`，范围 2～8；
- `max_candidate_batch_count`，范围 1～4；
- `generator`。

`generator.provider` 和 `generator.model` 必填。`generator.api_key` 和 `generator.base_url` 可直接填实验私有凭据；未提供 `api_key` 时按 provider 默认环境变量读取。

没有 origin graph 的 benchmark 必须配置非空 generator；有 origin graph 的 benchmark 可以省略 generator。

目标数不得超过批次数的两倍。旧 path count 与 repair 字段不再接受。
````

`docs/apis/harness.md` 当前也存在历史乱码，但与本实验阻断问题无直接耦合；本方案不扩大重写范围，后续文档清理单独处理。

## 6. 验证与验收

### 6.1 静态检查

在项目根目录执行：

```powershell
.venv\Scripts\python.exe -m compileall -q dynsteer main.py get_cases.py milestone_reliability.py
```

检查无语法错误。若生成 `__pycache__`，验证后删除。

### 6.2 配置展开检查

逐个展开五份目标配置：

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; from dynsteer.experiment.config import load_experiment_config, expand_experiment_matrix; paths=['data/experiments/swebench_pro_partial_main.json','data/experiments/skillsbench_partial_main.json','data/experiments/cross_benchmark_main.json','data/experiments/cross_benchmark_ablation.json','data/experiments/cross_benchmark_intervention.json']; [print(path, len(expand_experiment_matrix(load_experiment_config(Path(path))))) for path in paths]"
```

预期展开数为：

| 配置 | 展开数 |
| --- | ---: |
| `swebench_pro_partial_main.json` | 24 |
| `skillsbench_partial_main.json` | 24 |
| `cross_benchmark_main.json` | 108 |
| `cross_benchmark_ablation.json` | 252 |
| `cross_benchmark_intervention.json` | 36 |

### 6.3 Generator 与命名检查

用一次只读 Python 检查确认：

1. 五个 JSON 顶层 `experiment_id` 等于文件 stem。
2. `swebench_pro` 和 `skillsbench` 的所有 benchmark spec 都有非空 generator。
3. 所有 generator 的 `api_key` 与 `base_url` 均非空。
4. `cross_benchmark_main.json` 的 SkillsBench metadata 包含 `missing_reason`。

检查命令可使用 `json.load()` 遍历配置，不新增运行时代码。只读审计脚本按以下规则实现：

```python
from pathlib import Path
import json

paths = [
    Path("data/experiments/swebench_pro_partial_main.json"),
    Path("data/experiments/skillsbench_partial_main.json"),
    Path("data/experiments/cross_benchmark_main.json"),
    Path("data/experiments/cross_benchmark_ablation.json"),
    Path("data/experiments/cross_benchmark_intervention.json"),
]
for path in paths:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["experiment_id"] == path.stem, path
    for benchmark in data["benchmarks"]:
        if benchmark["benchmark"] not in {"swebench_pro", "skillsbench"}:
            continue
        generator = benchmark["milestone_generation"]["generator"]
        assert generator["provider"], path
        assert generator["model"], path
        assert generator["api_key"], path
        assert generator["base_url"], path

main = json.loads(paths[2].read_text(encoding="utf-8"))
skills = next(item for item in main["benchmarks"] if item["benchmark"] == "skillsbench")
assert skills["metadata"]["missing_reason"]
```

该脚本只作为临时验证，不提交到仓库。

### 6.4 空图消融回归

用 fake `BaseBenchmarkAdapter` 返回一个带 origin graph 的 `TaskCase`，构造 method 为 `dynsteer_replay_no_milestone_graph` 的 `HarnessRunConfig`，直接调用 `_adapt_task_case()`。断言：

1. 不触发 `AttributeError`。
2. 最终 `milestone_graph.nodes`、`edges`、`minefields` 为空。
3. `metadata.source == "disabled"`。
4. 后处理的 stage evaluation specs 中存在 whole-trajectory `__finish__` stage。

该验证可通过临时 pytest 文件或一次性 Python 脚本执行；临时验证文件不提交。

### 6.5 小样本 pilot

修复合并后，先分别用两个 partial 配置各选一个 case、一个模型、`repeats=1` 做临时 pilot 配置。不要直接修改正式 partial 配置来缩减 case。验收顺序：

1. `default` 能产出 native score 和完整 trajectory。
2. `dynsteer_replay` 能复用 default trajectory，产出 DynSTEER score。
3. SWE/Skills 的 adapted case 包含非空 generated milestone graph 与 generation report。
4. `dynsteer_replay_no_milestone_graph` 使用 `.no-milestone-graph.json`，空图不会被 origin graph 覆盖。
5. generator 使用的 Qwen endpoint 与 key 能完成 LLM 调用。

Pilot 通过后才允许启动正式实验。

## 7. 交付物

1. 修改后的三个 Python 文件：
   - `dynsteer/adapter/loader.py`
   - `dynsteer/harness/config.py`
   - `dynsteer/llm/factory.py`
2. 重命名后的五份实验配置，以及其中 SWE/Skills benchmark spec 的 generator 配置。
3. 更新后的当前入口文档：`README.md`、两个启动脚本 usage、`docs/apis/agentcompass.md`、`docs/apis/experiment.md`、`docs/apis/milestone.md`。
4. 临时验证脚本不提交；验证输出记录五份配置展开数、编译结果和空图断言结果。

## 8. 实施顺序

1. 修改 `loader.py` 空图分支。
2. 修改 generator 字段白名单。
3. 修改 `factory.py` 支持显式 generator API key。
4. 执行编译和空图回归。
5. 重命名配置文件，同步 `experiment_id` 和 `subset_of`。
6. 添加 SWE/Skills generator 与 SkillsBench missing reason。
7. 更新当前 README、启动脚本示例和 API 文档。
8. 展开五份配置并执行静态审计。
9. 运行两个 benchmark 的小样本 pilot。
10. Pilot 通过后按用户指定顺序启动正式实验。

## 附录 A. 项目中没有把握实现的模块部分

1. **SWE/Skills milestone 生成质量与耗时**：generator 需要从 AgentCompass 的公开任务视图生成候选图，SWE 任务描述和工具上下文较长，首次 100/50 case 适配可能耗时明显并产生额外 Qwen token 成本。代码上可以保证失败显式终止，但无法在不实际 pilot 的情况下预判每个 case 的成功率。
2. **OpenHands/mini-SWE-agent 环境稳定性**：本方案不修改 AgentCompass 执行环境。SkillsBench 的 OpenHands 安装和 SWE Docker 镜像可用性仍需要小样本 pilot 确认；方案不隐藏安装失败，也不绕过 AgentCompass 直接修复容器。
3. **旧 ToolSandbox 缓存复用范围**：`.no-milestone-graph.json` 内容核查为空图，理论上可直接复用；但历史 adapted case 是否全部满足当前 stage goal schema 需要静态验证。方案不主动批量删除缓存，避免破坏可追溯产物。
