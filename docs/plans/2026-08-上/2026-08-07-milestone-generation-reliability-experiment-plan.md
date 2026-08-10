# DynSTEER 自动 Milestone 生成可靠性实验代码方案

## 1. 文件修改总览

| 文件 | 操作 | 具体修改 |
|---|---|---|
| `data/experiments/toolsandbox_milestone_reliability_main.json` | 新增 | 从 `toolsandbox_main.json` 原样复制 509 个 case_ids，删除无关 Agent 模型矩阵，补充 milestone generator 与 GED 配置。 |
| `data/experiments/toolsandbox_milestone_reliability_partial_main.json` | 新增 | 从 `toolsandbox_partial_main.json` 原样复制 25 个 case_ids，删除无关 Agent 模型矩阵，补充 milestone generator 与 GED 配置。 |
| `milestone_reliability.py` | 新增 | 实现 `--exp` 入口、experiment spec 去重、原生/生成 graph 构造、GED、可选 FGW、结果汇总与写出。 |
| `scripts/start_milestone_reliability.sh` | 新增 | Docker 启动，复用 `experiment_bootstrap.sh` 解析并挂载全部 benchmark source。 |
| `scripts/start_milestone_reliability_no_docker.sh` | 新增 | no-Docker 启动，使用 uv 环境并按 experiment config 安装全部 benchmark source。 |
| `pyproject.toml` | 修改 | 主依赖新增 `networkx>=3.2`。 |
| `uv.lock` | 修改 | 通过 `uv lock` 写入 NetworkX 锁定版本及传递依赖。 |
| `docs/apis/experiment.md` | 修改 | 增加可靠性入口参数、GED/FGW 输出字段和结果目录说明。 |
| `tests/test_milestone_reliability.py` | 新增 | 覆盖配置去重、graph 构造、GED、归一化、失败隔离、输出 schema 和入口返回码。 |

明确不修改 `main.py`、`dynsteer/milestone/*`、`dynsteer/milestone/__init__.py`、已有常规启动脚本、`.gitignore` 和现有 adapted case。

## 2. 具体代码修改

### 2.1 新增 milestone reliability 实验配置

#### 2.1.1 `data/experiments/toolsandbox_milestone_reliability_main.json`

从 `data/experiments/toolsandbox_main.json` 只继承 `benchmarks[0].benchmark`、`data_root` 和完整 `case_ids`。不复制原文件中的四组 Agent model、`harness_metadata`、client 配置、`dynsteer_replay` method 和 `repeats=3`。新文件内容按下面结构生成：

```jsonc
{
  "experiment_id": "toolsandbox_milestone_reliability_main",
  "runs_dir": "runs/exp/toolsandbox_milestone_reliability_main",
  "results_dir": "results/exp/toolsandbox_milestone_reliability_main",
  "repeats": 1,
  "models": [
    {"model_id": "milestone_generator"}
  ],
  "methods": ["default"],
  "benchmarks": [
    {
      "benchmark": "toolsandbox",
      "data_root": "data/toolsandbox",
      "milestone_generation": {
        "use_origin_milestone": false,
        "simulated_path_count": 6,
        "generator": {
          "provider": "qwen",
          "model": "qwen-plus-latest",
          "temperature": 0,
          "timeout_seconds": 120,
          "max_tokens": 4096,
          "max_retries": 3
        }
      },
      "metadata": {
        "milestone_reliability": {
          "ged_solver": "exact",
          "ged_timeout_seconds": 60,
          "edit_cost_profile": {
            "profile_id": "strict_v1",
            "node_insert": 1.0,
            "node_delete": 1.0,
            "edge_insert": 1.0,
            "edge_delete": 1.0,
            "node_label_distance": "canonical_json",
            "edge_label_distance": "exact"
          },
          "fgw": false
        }
      },
      "case_ids": [
        // 将 toolsandbox_main.json 的 509 个 case_id 按原顺序完整复制到这里。
      ]
    }
  ]
}
```

实际落地文件必须是合法 JSON，删除上面 `jsonc` 示例中的注释。case 数组验收条件：

1. 数量为 509；
2. 首项为 `find_days_till_holiday_insufficient_information`；
3. 末项为 `update_contact_relationship_with_relationship_twice_multiple_user_turn`；
4. 使用 `sha256(json.dumps(case_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))` 计算的摘要为 `d4e6fda17ba4ec7329f5e0d01ab080992b157e9d97fdc9471c8df63f49a277bc`。

生成文件时使用一次性维护脚本或等价的 Python 代码构造有序字典，避免手工重排 509 项：读取源 JSON → 深拷贝 `benchmarks[0]["case_ids"]` → 按 `benchmark/data_root/milestone_generation/metadata/case_ids` 顺序写入新对象 → `json.dump(..., ensure_ascii=False, indent=2)`。写出后立刻用上述 SHA-256 断言；断言失败不得提交配置文件。该脚本只用于生成配置，不作为运行时依赖，也不新增到 `scripts/`。

#### 2.1.2 `data/experiments/toolsandbox_milestone_reliability_partial_main.json`

顶层、generator 和 GED 配置与 main reliability 配置完全相同，只修改 experiment id、runs/results 路径和 case 集合。文件内容如下：

```json
{
  "experiment_id": "toolsandbox_milestone_reliability_partial_main",
  "runs_dir": "runs/exp/toolsandbox_milestone_reliability_partial_main",
  "results_dir": "results/exp/toolsandbox_milestone_reliability_partial_main",
  "repeats": 1,
  "models": [
    {"model_id": "milestone_generator"}
  ],
  "methods": ["default"],
  "benchmarks": [
    {
      "benchmark": "toolsandbox",
      "data_root": "data/toolsandbox",
      "milestone_generation": {
        "use_origin_milestone": false,
        "simulated_path_count": 6,
        "generator": {
          "provider": "qwen",
          "model": "qwen-plus-latest",
          "temperature": 0,
          "timeout_seconds": 120,
          "max_tokens": 4096,
          "max_retries": 3
        }
      },
      "metadata": {
        "milestone_reliability": {
          "ged_solver": "exact",
          "ged_timeout_seconds": 60,
          "edit_cost_profile": {
            "profile_id": "strict_v1",
            "node_insert": 1.0,
            "node_delete": 1.0,
            "edge_insert": 1.0,
            "edge_delete": 1.0,
            "node_label_distance": "canonical_json",
            "edge_label_distance": "exact"
          },
          "fgw": false
        }
      },
      "case_ids": [
        "find_days_till_holiday_insufficient_information",
        "modify_contact_with_message_recency_insufficient_information",
        "modify_contact_with_message_recency_insufficient_information_10_distraction_tools",
        "modify_contact_with_message_recency_insufficient_information_3_distraction_tools",
        "remove_contact_by_phone_no_remove_contact_insufficient_information",
        "add_reminder_content_and_date_and_time",
        "add_reminder_content_and_date_and_time_10_distraction_tools",
        "add_reminder_content_and_date_and_time_3_distraction_tools",
        "add_reminder_content_and_date_and_time_3_distraction_tools_arg_description_scrambled",
        "add_contact_with_name_and_phone_number_3_distraction_tools",
        "modify_reminder_with_recency_latest",
        "search_message_with_recency_oldest_multiple_user_turn",
        "search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools",
        "search_message_with_recency_oldest_multiple_user_turn_3_distraction_tools_arg_description_scrambled",
        "turn_on_cellular_low_battery_mode",
        "turn_on_location_low_battery_mode",
        "turn_on_location_low_battery_mode_3_distraction_tools",
        "turn_on_location_low_battery_mode_3_distraction_tools_arg_description_scrambled",
        "turn_on_location_low_battery_mode_3_distraction_tools_arg_type_scrambled",
        "update_contact_relationship_with_relationship",
        "find_days_till_holiday",
        "send_message_with_contact_content_cellular_off",
        "find_days_till_holiday_wifi_off_alt",
        "modify_contact_with_message_recency_alt_10_distraction_tools",
        "update_contact_relationship_with_relationship_twice_multiple_user_turn"
      ]
    }
  ]
}
```

partial case 数组验收条件：数量为 25，顺序与 `toolsandbox_partial_main.json` 完全一致，按同一算法计算的 SHA-256 为 `497065eec9c09409305aeb3e5ab72df8b2ef0e4d4bfc05cac5bb48110343c6eb`。

#### 2.1.3 配置加载规则

`milestone_reliability.py` 不增加专用配置 parser。继续使用 `load_experiment_config()`、`expand_experiment_matrix()` 和 `build_harness_config()`；从代表 spec 的 `metadata["milestone_reliability"]` 读取 GED 配置。新配置固定为一组 model、`default` method 和一个 repeat，因此展开后每个 benchmark 只产生一份代表 spec，不依赖额外去重来消除原始 Agent 实验矩阵。

两个新 JSON 中 `benchmarks[0].case_ids` 必须保持为 benchmark 对象的最后一个字段；只改变字段位置，不改变 case 内容、顺序和摘要。

`build_harness_config()` 会把 `default` 写入 `metadata["method"]`，而 ToolSandbox adapter 在 default method 下不构造原生 milestone graph。入口必须在调用 `adapter.adapt_task_case()` 前使用 `dataclasses.replace()` 创建 reliability 专用 config，将该 metadata 值改成 `milestone_reliability`；这只影响内存配置，不修改 experiment JSON，也不进入普通 experiment runner。

`_group_reliability_specs(specs: list[ExperimentRunSpec]) -> list[_ReliabilitySpecGroup]` 的实现顺序必须固定：

1. 以 `(spec.benchmark, str(spec.data_root.resolve()))` 为 key 建立有序分组；
2. 每组第一份 spec 通过 `build_harness_config()` 构造代表 config；
3. 合并所有 spec.case_ids，使用 `dict.fromkeys()` 保留首次出现顺序；
4. 对同组 `milestone_generation` 和 `metadata["milestone_reliability"]` 做 canonical JSON digest；digest 不一致时抛出配置冲突，不允许静默选择第一份；
5. 将 `case_ids` 为空、benchmark 不一致、data_root 不存在、GED profile 字段缺失转换为 `ValueError`；
6. 返回的 group 顺序必须与 experiment JSON 中 benchmark 首次出现顺序一致。

配置实现时不要把 `case_ids` 写在 generator 或 metadata 前面。对象字段顺序固定为 `benchmark`、`data_root`、`milestone_generation`、`metadata`、`case_ids`；最后一个字段之后不得再追加实验选项。这样打开长配置文件时，实验参数和指标配置始终可见，case 列表集中在文件末尾。

配置校验函数应在入口文件内实现为 `_validate_reliability_metadata(metadata)`，逐项检查：`ged_solver` 只能是 `exact`/`approximate`；`ged_timeout_seconds` 为正数；`edit_cost_profile.profile_id` 非空；四个 insert/delete cost 为有限非负数；`node_label_distance` 为 `canonical_json`；`edge_label_distance` 为 `exact`；`fgw` 为 bool。校验失败消息必须包含 JSON 路径（例如 `benchmarks[0].metadata.milestone_reliability.edit_cost_profile.node_insert`），便于直接定位配置错误。


### 2.2 新增 Python 实验入口：`milestone_reliability.py`

新增项目根目录入口文件，函数由主到次排列：

```text
milestone_reliability.py
├── _parse_args(argv)
├── main(argv)
├── _run_reliability_experiment(...)
├── _group_reliability_specs(...)
├── _run_benchmark_cases(...)
├── _run_case(...)
├── _node_set_f1(...)
├── _compute_ged(...)
├── _compute_fgw_optional(...)
├── _write_report(...)
└── graph label、edit-cost 和 JSON 写出 helpers
```

入口文件的 import 和内部模型固定如下，不从 `dynsteer` 新增 reliability 公共 API：

```python
import argparse
import copy
import concurrent.futures
import datetime as dt
import hashlib
import importlib.util
import json
import logging
import math
import os
import random
import statistics
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import networkx as nx

from dynsteer.adapter.loader import safe_case_file_name
from dynsteer.adapter.registry import get_adapter, get_harness
from dynsteer.experiment.config import (
    build_harness_config,
    expand_experiment_matrix,
    load_experiment_config,
)
from dynsteer.experiment.model import ExperimentRunSpec
from dynsteer.harness.model import HarnessRunConfig
from dynsteer.llm import build_llm_from_config, build_llm_from_env
from dynsteer.milestone import compile_task_case
from dynsteer.milestone.model import (
    GenerationReport,
    MilestoneGenerationConfig,
    MilestoneGenerationError,
)
from dynsteer.model import JsonObject, JsonValue, MilestoneGraph, TaskCase


class _InfrastructureError(RuntimeError):
    """可靠性实验依赖、source 或结果目录不可用。"""


@dataclass(frozen=True)
class _ReliabilitySpecGroup:
    benchmark: str
    data_root: Path
    case_ids: tuple[str, ...]
    harness_config: HarnessRunConfig
    milestone_generation: MilestoneGenerationConfig
    reliability_metadata: JsonObject
    results_dir: Path
    source_root: Path | None


@dataclass(frozen=True)
class _CaseResult:
    benchmark: str
    case_id: str
    status: str
    metrics: JsonObject
    output_path: Path
    generation_report: JsonObject
    reference_count: int
    prediction_count: int
    diagnostics: tuple[str, ...] = ()
    elapsed_ms: int = 0


@dataclass
class _MutableGroup:
    first_spec: ExperimentRunSpec
    harness_config: HarnessRunConfig
    milestone_generation: MilestoneGenerationConfig
    reliability_metadata: JsonObject
    generation_digest: str
    reliability_digest: str
    case_ids: list[str]
```

`_MutableGroup` 只在分组函数内部使用，`freeze()` 返回不可变的 `_ReliabilitySpecGroup`；`_ReliabilitySpecGroup` 是 experiment matrix 到可靠性 case 集合的唯一转换结果；`_CaseResult` 只保存汇总所需字段，不把完整 graph 长期保留在内存。

`_parse_args(argv: Optional[list[str]]) -> argparse.Namespace` 必须：

1. 接受 `--exp` 与 `--experiment-config` 两个别名，二者写入 `experiment_config`；
2. 校验 `--workers >= 1`、`--ged-timeout-seconds > 0`、`--ged-solver in {exact, approximate}`；
3. `--fgw` 为 `store_true`；未配置时不尝试导入 POT；
4. 不接受 `--benchmark`、`--source`、`--data-root`、`--case-id` 或 `--output-dir`；
5. 帮助文本示例只引用两个 reliability 配置文件。

`main(argv: Optional[list[str]]) -> int` 必须按以下异常边界返回：参数/配置校验失败返回 1，所有 case 已落盘但存在 case 级失败时返回 0，uv/source/import/GED 基础设施失败返回 2；入口使用 `configure_logger()`，日志只写 case_id、状态、耗时和数量，不写完整 prompt 或 API key。

命令行接口：

| 参数 | 约定 |
|---|---|
| `--exp PATH` / `--experiment-config PATH` | 必填，直接读取 `data/experiments` 下现有统一实验 JSON。 |
| `--workers NUM` | 可选覆盖；未传入时取各 benchmark manifest 的 `max_workers`，最小为 1。 |
| `--ged-solver MODE` | 可选，`exact` 或 `approximate`；默认 `exact`，图较大时可显式使用近似求解。 |
| `--ged-timeout-seconds NUM` | approximate 模式的单 case 求解上限，默认 60；exact 模式不使用 timeout。 |
| `--fgw` | 可选，额外计算论文定义的 FGW 对照指标；默认关闭，避免引入额外依赖和关系投影假设。 |
| `--random-seed NUM` | 可选，设置 Python 随机种子并写入报告，默认 202608。 |

正常实验命令只需要：

```bash
python milestone_reliability.py --exp data/experiments/toolsandbox_milestone_reliability_partial_main.json
```

入口调用现有 `load_experiment_config()` 和 `expand_experiment_matrix()`，不重新实现 experiment JSON 解析。由于可靠性只取决于 benchmark、data root、case、milestone generation 配置和 edit-cost profile，不取决于 agent model、method、threshold profile 或 repeat，展开后的 spec 按 `(benchmark, data_root)` 分组，case ids 按首次出现顺序去重。同一个 case 即使在 model × method × repeat 矩阵中出现多次，也只生成一次 GED。

输出根目录直接取 spec 的 `results_dir`，实际写入 `<results_dir>/milestone_reliability/<run_id>/`。source root 由 `data_root/benchmark.json` 解析；入口不再接收 `--benchmark`、`--source`、`--data-root`、`--case-id` 或 `--output-dir`。

现有 experiment JSON 可以不改就直接提供 benchmark 与 case 集合；当 `milestone_generation.generator` 为空时，生成器使用 `build_llm_from_env()`。为了让可靠性实验配置可复现，推荐在 benchmark spec 中补充已有的 `milestone_generation` 字段，并可在 benchmark `metadata` 中放置本实验私有的 edit-cost profile：

```json
{
  "benchmark": "toolsandbox",
  "data_root": "data/toolsandbox",
  "case_ids": ["..."],
  "milestone_generation": {
    "use_origin_milestone": false,
    "simulated_path_count": 6,
    "generator": {
      "provider": "qwen",
      "model": "<generator-model>",
      "temperature": 0
    }
  },
  "metadata": {
    "milestone_reliability": {
      "ged_solver": "exact",
      "ged_timeout_seconds": 60,
      "edit_cost_profile": {
        "profile_id": "strict_v1",
        "node_insert": 1.0,
        "node_delete": 1.0,
        "edge_insert": 1.0,
        "edge_delete": 1.0,
        "node_label_distance": "canonical_json",
        "edge_label_distance": "exact"
      },
      "fgw": false
    }
  }
}
```

配置中只保存 provider/model 和 edit-cost profile 等非敏感参数；API key 仍只从环境变量读取。未配置 profile 时使用入口常量 `DEFAULT_EDIT_COST_PROFILE`，其值与上面 `strict_v1` 完全一致。CLI 的 `--ged-solver`、`--ged-timeout-seconds`、`--fgw` 和 `--workers` 仅作为临时覆盖，优先级高于上述 metadata；正常批量运行不需要传入。

入口参数错误返回 1，case 级失败汇总后返回 0，基础设施/配置错误返回 2。`--ged-solver`、`--ged-timeout-seconds`、edit-cost profile 和 FGW 参数必须写入 index，保证同一实验可复算。

#### 2.2.1 入口函数的具体控制流

`main()` 只负责边界处理，不在其中实现 graph 算法。实现顺序固定为：

```python
def main(argv: Optional[list[str]] = None) -> int:
    try:
        options = _parse_args(argv)
        random.seed(options.random_seed)
        return _run_reliability_experiment(
            Path(options.experiment_config).resolve(), options
        )
    except (argparse.ArgumentError, ValueError, FileNotFoundError, KeyError) as exc:
        logger.error("可靠性实验配置失败: %s", _safe_error(exc))
        return 1
    except _InfrastructureError as exc:
        logger.error("可靠性实验基础设施失败: %s", _safe_error(exc))
        return 2
    except (ImportError, OSError, RuntimeError) as exc:
        logger.error("可靠性实验基础设施失败: %s", _safe_error(exc))
        return 2
```

`_parse_args()` 若使用 argparse 默认的 `SystemExit`，`main()` 必须将非零 `SystemExit` 映射为 1（`--help` 的 0 保持不变）；直接执行文件时使用 `raise SystemExit(main())`。这样 Python 入口和两个 shell wrapper 的参数错误返回码一致。

`_run_reliability_experiment()` 应把“experiment JSON 不存在/不可解析”保留为配置错误（返回 1），把 `harness.prepare_config()`、source_root、NetworkX/POT 等运行依赖错误包装为 `_InfrastructureError`（由 `main()` 返回 2）。不要仅按 `FileNotFoundError` 类型判断，否则无法区分配置文件缺失和 benchmark source 缺失。

实际代码中不能用裸 `except Exception` 覆盖前两类异常；case 级异常只在 `_run_case()` 内捕获。`_safe_error()` 只返回异常类型和一行短消息，先对 `api_key`、`token`、`password` 等字符串做替换，禁止把 traceback、prompt 或 LLM 原始响应写日志。

`_parse_args()` 的实现细节：

1. `parser.add_argument("--exp", "--experiment-config", dest="experiment_config", required=True, type=Path)`；`Path` 必须存在且是文件，否则 `parser.error()`。
2. `--workers` 默认 `None`，传入后转换为正整数；`None` 表示稍后从每个 benchmark manifest 的 `max_workers` 取值。
3. `--ged-solver` 默认 `None`，表示使用 metadata 中的值；CLI 非空时只能为 `exact` 或 `approximate`。
4. `--ged-timeout-seconds` 默认 `None`，必须是大于 0 的有限浮点数；只对 approximate solver 生效。
5. `--fgw` 使用 `action="store_true"`，默认 `False`；metadata 中 `fgw=true` 时也要开启，因此运行期取两者逻辑或。
6. `--random-seed` 默认 `202608`，必须是整数。
7. 不定义旧实验的 `--benchmark`、`--source`、`--data-root`、`--case-id`、`--output-dir` 参数；argparse 对这些参数返回 2，shell wrapper 保持同一非零行为。

#### 2.2.2 spec 分组与冲突检测伪代码

`_group_reliability_specs()` 必须保留首次出现顺序，且不能用集合直接迭代。实现可按以下伪代码落地：

```python
groups: dict[tuple[str, str], _MutableGroup] = {}
for spec in specs:
    key = (spec.benchmark, str(spec.data_root.resolve()))
    group = groups.get(key)
    candidate = build_harness_config(spec)
    candidate_meta = _reliability_metadata(spec.metadata)
    generation_digest = _canonical_digest(spec.milestone_generation)
    reliability_digest = _canonical_digest(candidate_meta)
    if group is None:
        groups[key] = _MutableGroup(
            first_spec=spec,
            harness_config=candidate,
            milestone_generation=spec.milestone_generation,
            reliability_metadata=candidate_meta,
            generation_digest=generation_digest,
            reliability_digest=reliability_digest,
            case_ids=list(spec.case_ids or ()),
        )
    else:
        if group.generation_digest != generation_digest:
            raise ValueError(f"{key} 的 milestone_generation 配置冲突")
        if group.reliability_digest != reliability_digest:
            raise ValueError(f"{key} 的 milestone_reliability 配置冲突")
        group.case_ids.extend(spec.case_ids or ())
return [group.freeze() for group in groups.values()]
```

`freeze()` 使用 `tuple(dict.fromkeys(case_ids))` 去重；校验所有 case id 非空、`data_root.is_dir()`、`get_adapter()`/`get_harness()` 可解析，并从 `data_root/benchmark.json` 读取 source root 只作为 index 摘要。若 manifest 没有 source root，保留 `None`，由 `harness.prepare_config()` 按现有仓库规则继续校验。

#### 2.2.3 `_run_reliability_experiment()` 的并发与生命周期

该函数先创建唯一 `run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")`，再为每个 group 建立 `cases/<safe_benchmark>/<safe_case_id>.json`。`safe_benchmark` 也使用 `safe_case_file_name(group.benchmark).removesuffix(".json")`，避免 benchmark 名称含路径字符。

执行顺序：

1. 在创建线程池前，对每个 group 调用一次 `get_harness()`、`harness.prepare_config()`；这一步失败是基础设施错误，直接返回 2。
2. `workers == 1` 时按 group、case_ids 原顺序调用 `_run_case()`。
3. `workers > 1` 时使用 `ThreadPoolExecutor(max_workers=workers)`，只提交 `_run_case()`；每个 future 完成后立即调用 `_write_case_json()`，但把结果放回 `(group_index, case_index)` 槽位，最终 summary 按配置顺序排序。不要让多个线程写同一个 JSON 文件。
4. `_run_case()` 内不得复用可变的 `HarnessRunConfig` 或 `TaskCase`；每次调用都用 `replace()` 和 `copy.deepcopy()`，避免 adapter 在一个 case 中修改对象影响另一个 case。
5. 每个 case 写出后释放 graph、LLM response 和 view 引用；内存中只保留 `_CaseResult`，不累计完整 graph。
6. 所有 case 完成后调用 `_write_report()`；summary/index 任一原子替换失败返回 2。存在 `generation_rejected`、`adapt_failed` 或 `ged_failed` 时仍返回 0，因为这些属于已记录的样本级结果。

#### 2.2.4 `_run_case()` 的双 graph 链路

`_run_case()` 必须严格按以下调用序列实现，调用名与当前仓库接口一致：

```python
adapter = get_adapter(group.benchmark)
harness = get_harness(group.benchmark)
metadata = {**group.harness_config.metadata, "method": "milestone_reliability"}
config = replace(group.harness_config, metadata=metadata, case_ids=(case_id,))
harness.prepare_config(config)
origin_case = adapter.adapt_task_case(config, case_id)
if origin_case.milestone_graph is None:
    raise ValueError("adapter 未返回原生 milestone_graph")
reference_graph = copy.deepcopy(origin_case.milestone_graph)
view = adapter.generator_task_view(config, origin_case, case_id)
generation_config = replace(group.milestone_generation, use_origin_milestone=False)
llm = build_llm_from_config(generation_config.generator) or build_llm_from_env()
if llm is None:
    raise ValueError("未配置 milestone generator")
generated_graph, generation_report = compile_task_case(view, generation_config, llm)
```

这里的 `method` 覆盖是必要的：`ToolSandboxAdapter.adapt_task_case()` 在 `metadata["method"] == "default"` 时会把 graph 设为 `None`。覆盖只存在于内存中的 `config`，不得回写 JSON，也不得调用 `load_task_case()`、`save_task_case()`、`adapted_case_path()` 或私有 `_adapt_task_case()`，因此实验不会生成 `data/*/adapted_cases`。

异常分支必须明确映射：`adapt_task_case()` 的错误 → `adapt_failed`；`MilestoneGenerationError` → `generation_rejected` 并保存 `report.to_dict()`；LLM/view 类型错误 → `generation_failed`；GED 无编辑路径、NetworkX 异常或超时 → `ged_failed`。上述 case 级异常必须继续下一个 case；配置解析、source 安装、依赖导入和结果目录不可写则终止整个入口。

#### 2.2.5 节点/边 descriptor 的字段级实现

新增 `_graph_descriptor(graph: MilestoneGraph) -> nx.DiGraph` 和 `_canonicalize_json(value)`。节点 key 使用原 milestone id 仅用于定位；节点属性 `label` 不包含 id，另存 `label_json = json.dumps(label, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`。普通 milestone label 固定包含 `kind`、`name`、`description`、`terminal`、`pass_threshold`、`constraints`、`stage_goal_semantics`；minefield 转成 `kind="minefield"` 节点并保留 `severity`、penalty 和 constraints。每条 constraint 固定序列化 `target`、`selector`、`operator`、`namespace`、`evaluator_hint`、`expected`、`stage_goal_semantics`、`weight`、`threshold`、`hard`；Enum 使用 `.value`，缺失值写 `null`，字典按 key 排序，列表仅按其定义语义排序。

每个 `(source, target)` 生成一条带 `{"relation": "precedes"}` 的有向边；若 metadata 提供关系类型则使用其 canonical 值，重复边标签冲突时抛错而不是覆盖。沿用 compiler 的 hidden-key 集合递归删除 `secret`、`api_key`、`password`、`token`、`gold`、`ground_truth`、`verifier`、`reward`、`matcher`、`target_dataframe` 等字段。随机 milestone id 不能参与节点语义相似度。

`_node_substitution_cost()` 对 strict_v1 的完整 `label_json` 相同返回 0，否则返回 1；`_edge_substitution_cost()` 对 canonical relation label 相同返回 0，否则返回 1。第一版只实现这个可复算的 exact-label profile，不把“语义相近”未经校准地写成数值。

#### 2.2.6 GED 调用、edit path 解析与归一化

`_compute_ged()` 先将两个 graph 转为 `networkx.DiGraph`，再计算 `ged_base = (|V_p|+|V_r|) * node_insert/delete + (|E_p|+|E_r|) * edge_insert/delete`。NetworkX 回调分别读取节点/边 `label`，不修改 graph。`exact` 模式完整消费 `nx.optimize_edit_paths(...)` 生成器并取最小 cost，`optimal=true`；`approximate` 模式传入 timeout，只保留截止前的最优路径，`optimal=false`，超时前无路径则返回 `ged_failed`。

将 path 转为稳定 JSON：每项包含 `kind`、`operation`、左右端点 id、`cost` 和两端 label 摘要，并统计 node/edge insertions、deletions、substitutions。返回字段必须包括 `ged_distance`、`ged_base`、`ged_similarity`、`solver_mode`、`optimal`、`edit_path`、所有编辑计数和完整 profile。`ged_base==0` 时仅两图都空返回 similarity 1，否则返回 0；其余情况按代码注释中的公式限制在 `[0,1]`。

#### 2.2.7 case/index/summary 写出

新增 `_atomic_write_json()`：临时文件放在目标目录，`json.dump(..., ensure_ascii=False, indent=2, sort_keys=True)` 后 flush + `os.fsync()`，最后 `Path.replace()`；失败时删除临时文件并抛出。case 文件固定包含 `schema_version`、experiment/group/case 标识、状态、UTC 时间、耗时、reference/prediction 的 node/edge count 与 descriptor digest、metrics、generation_report、diagnostics。完整 label 不重复写入，避免 509 case 结果膨胀。

`summary.json` 记录状态计数、仅对 completed case 计算的 GED similarity macro（count/mean/median/min/max）、GED 操作总数、node_set_f1 基线、FGW 汇总、`metric_definition` 和有序 case 文件清单；失败 case 不当作 0。`index.json` 记录实验配置绝对路径及 SHA-256、每个 group 的 benchmark/data_root/source_root、去重前/后 case 数、非敏感 generator 摘要、GED/FGW 配置、NetworkX/Python 版本、ignored matrix dimensions、random seed、UTC 起止时间和相对文件路径；不得写 API key、Authorization 或完整 prompt。

#### 2.2.8 测试文件的逐项断言

新增 `tests/test_milestone_reliability.py`，不调用真实 LLM：

1. 读取两个 JSON，断言字段顺序、509/25 case 数、首尾 case、给定 SHA-256、`case_ids` 为最后字段、`use_origin_milestone=false` 和 strict profile 字段。
2. 测试同 benchmark/data_root 去重、generator/profile 冲突抛 `ValueError`、空 case/source 缺失失败。
3. 测试 expected/Enum canonicalization、敏感键删除、minefield 保留和边方向。
4. 测试相同图 `GED=0/similarity=1`、单节点/边插入删除成本、strict label 替换 `GED=1/base=2/similarity=0.5`、方向反转为 edge delete+insert、approximate 的 `optimal=false`。
5. 测试 F1 仅将零成本 vertex substitution 计入 TP。
6. monkeypatch adapter，断言内存 method 为 `milestone_reliability`、compiler config 的 `use_origin_milestone=false`，且不创建 `adapted_cases`。
7. 模拟 LLM 缺失、`MilestoneGenerationError`、adapter 错误、GED 错误和单个写出错误，断言状态、继续顺序和入口返回码。
8. 用 `tmp_path` 验证 case/summary/index schema、失败 case 的 null 规则和原子写出，不污染仓库结果目录。

### 2.3 可靠性逻辑保留在实验入口文件

本实验使用的结果模型、descriptor、GED/FGW 调用、F1 基线和报告函数都只服务该入口，因此不新增 `dynsteer/milestone/reliability.py`，也不修改 `dynsteer/milestone/__init__.py`。在 `milestone_reliability.py` 内将这些类型和函数设为下划线开头的内部实现，例如 `_MilestoneCaseReliability`、`_compute_ged()`、`_compute_fgw_optional()`、`_node_set_f1()` 和 `_write_report()`。

入口文件虽然包含完整实验逻辑，但仍按“主流程在前、细节函数在后”组织；只复用 `dynsteer` 已有 adapter、experiment config、LLM、compiler、model 和日志接口，不把通用生成逻辑复制进入口。

#### 2.3.1 case 处理流程

`_run_reliability_experiment()` 按如下顺序处理：

1. 通过 `load_experiment_config()`、`expand_experiment_matrix()` 读取统一配置并得到 specs。
2. 按 `(benchmark, data_root)` 分组，每组取第一份 spec 通过 `build_harness_config()` 构造代表性 `HarnessRunConfig`，再用 `replace()` 将 `metadata["method"]` 改为 `milestone_reliability`，确保 adapter 返回原生 graph；同时合并该组所有 `case_ids` 并去重。若同组出现不同 `milestone_generation` 或 edit-cost profile 则直接报配置冲突，不能静默选第一份。
3. 校验 config、adapter、case id 和输出目录；调用 `harness.prepare_config()`，让现有 source-root 校验生效。
4. 调用 `adapter.adapt_task_case(config, case_id)` 得到原生 `TaskCase`，深拷贝或只读保存 `reference_graph`。不调用 `load_task_case()`、`save_task_case()` 或 `_adapt_task_case()`。
5. 基于该原生 `TaskCase` 调用 `adapter.generator_task_view(config, task_case, case_id)`，确保生成器只看到已有公开白名单。
6. 将 `use_origin_milestone` 强制设为 `false` 仅用于本次内存配置；不修改 experiment JSON。生成 LLM 优先读取 benchmark spec 的 `milestone_generation.generator`，为空时回退到现有 `build_llm_from_env()`。调用 `compile_task_case()` 得到 `generated_graph` 与 `GenerationReport`。
7. 生成失败时捕获 `MilestoneGenerationError`，保留其 `report`，将 prediction 节点集合视为空并继续其他 case；不得吞掉异常或伪造成功 graph。
8. 将两份 graph 转为受控 descriptor，调用 `_compute_ged()`；计算 case 级 `ged_distance`、`ged_similarity`、编辑操作统计和 `node_set_f1` 基线；若开启 `--fgw`，再调用 `_compute_fgw_optional()`，并立即写出 case JSON。
9. 最后聚合全部 benchmark/case 结果并原子写出 `summary.json`、`index.json`。

`models`、`methods`、`repeats`、judge threshold 等普通实验矩阵字段仅用于复用配置格式，不改变本实验样本集合，也不会造成重复生成。index 中显式记录 `ignored_matrix_dimensions=["models","methods","repeats","threshold_matrix"]`，避免结果读者误以为可靠性按这些维度重复执行。

`_run_case(group: _ReliabilitySpecGroup, case_id: str, options: argparse.Namespace) -> _CaseResult` 必须实现为“不落 adapted cache 的双 graph 流程”：

```python
def _run_case(group, case_id, options):
    # 1. 只改内存 method，确保 adapter 暴露 origin graph。
    metadata = {**group.harness_config.metadata, "method": "milestone_reliability"}
    config = replace(group.harness_config, metadata=metadata)
    adapter = get_adapter(group.benchmark)
    harness = get_harness(group.benchmark)
    harness.prepare_config(config)

    # 2. 直接适配原生 case；禁止调用 load_task_case/save_task_case。
    origin_case = adapter.adapt_task_case(config, case_id)
    reference_graph = origin_case.milestone_graph

    # 3. 使用公开 view 生成 graph，强制 use_origin_milestone=False。
    view = adapter.generator_task_view(config, origin_case, case_id)
    generation_config = replace(
        config.milestone_generation,
        use_origin_milestone=False,
    )
    generator = build_llm_from_config(generation_config.generator)
    if generator is None:
        generator = build_llm_from_env()
    if generator is None:
        raise ValueError("milestone generator 未配置")
    generated_graph, generation_report = compile_task_case(
        view, generation_config, generator
    )

    # 4. 计算 GED、node_set_f1 和可选 FGW，然后立即写 case 文件。
    metrics = _compute_case_metrics(reference_graph, generated_graph, options)
    return _write_case_result(...)
```

异常处理要求：

1. `adapter.adapt_task_case()` 失败写 `status=adapt_failed`，不调用 generator；
2. `MilestoneGenerationError` 写 `status=generation_rejected`，保存 `GenerationReport.to_dict()`；
3. GED 无可行编辑路径或 solver 抛错写 `status=ged_failed`；
4. 单 case 失败不能中断同一 group 的后续 case；只有 experiment config、source 或依赖初始化失败才终止整个入口；
5. `reference_graph is None` 或 `generated_graph is None` 都必须记录明确诊断，不把空 graph 当作成功生成。

`_run_reliability_experiment(experiment_path: Path, options: argparse.Namespace) -> int` 必须：

1. 调用 `load_experiment_config()`、`expand_experiment_matrix()` 和 `_group_reliability_specs()`；
2. 为每个 group 创建 `<results_dir>/milestone_reliability/<run_id>/cases/<benchmark>/`，目录名使用 `safe_case_file_name()`；
3. `workers=1` 时按 case 顺序串行执行；`workers>1` 时只并发 `_run_case()`，case 文件名和 summary 聚合仍按原始 case 顺序排序；
4. 每个 case 返回后立即写 JSON，最后只保留 `_CaseResult` 列表用于 summary；
5. 全部 case 完成后写 `summary.json` 和 `index.json`，任何写文件失败都返回 2。

#### 2.3.2 节点/边标签与编辑代价

`_graph_descriptor()` 不再构造 LLM pairwise matcher，而是把两个 graph 转为 GED/FGW 所需的节点标签、边标签和关系矩阵：

1. 节点标签包含 milestone 角色、约束列表、约束 target/selector/operator/namespace/evaluator_hint、expected 的规范化 JSON 结构、stage-goal 语义和 terminal 信息；
2. 边标签包含 source、target、方向和关系类型；
3. 节点 id 只用于定位，不作为语义相似度特征；
4. 对 `secret`、`api_key`、`password`、`token`、`gold`、`ground_truth`、`verifier`、`reward`、`matcher`、`target_dataframe` 等字段递归脱敏；
5. descriptor 和 edit-cost profile 都写入 case/index 报告，确保 GED 结果可审计。

节点替换代价由固定的 `edit_cost_profile` 提供。相同结构化标签的替换代价必须为 0；不同标签的代价由 profile 中声明的字段权重和 JSON 距离得到。禁止在 GED 计算期间临时调用 LLM 产生不可复现的 cost。若确实需要语义 embedding，embedding 模型、版本和距离函数必须作为实验配置写入 index，并单独报告其来源。

入口文件新增以下常量和 cost helpers：

```python
DEFAULT_EDIT_COST_PROFILE = {
    "profile_id": "strict_v1",
    "node_insert": 1.0,
    "node_delete": 1.0,
    "edge_insert": 1.0,
    "edge_delete": 1.0,
    "node_label_distance": "canonical_json",
    "edge_label_distance": "exact",
}

def _node_substitution_cost(left: dict[str, object], right: dict[str, object]) -> float:
    # strict_v1：完整 canonical milestone/constraint label 相同返回 0，否则返回 1。
    ...

def _edge_substitution_cost(left: dict[str, object], right: dict[str, object]) -> float:
    # 有向关系 label 相同返回 0，否则返回 1。
    ...
```

后续若增加部分属性距离，只允许新增显式命名和版本化的 profile，例如 `weighted_json_v1`；profile 必须提供每个 milestone/constraint 字段权重、权重和为 1 的校验，以及对应单元测试，不能静默改变 `strict_v1`。

`_compute_ged()` 返回 exact/approximate solver mode、编辑路径摘要、各类插入/删除/替换计数、原始 GED、GED base 和归一化 `ged_similarity`。只有 GED 求解器负责确定 graph 对齐；不再通过一个自定义的节点 matcher 先决定 TP。

`_compute_fgw_optional()` 只在 `--fgw` 开启且依赖可用时执行。它使用与 GED 相同的节点特征投影，并把有向 DAG 投影成报告中明确记录的关系矩阵；若关系投影或依赖不可用，写入 `fgw_status=unavailable`，不得伪造 FGW 数值。
#### 2.3.3 F1 只作为节点集合基线

在入口文件中保留 `_node_set_f1()` 作为旧指标基线；公式和来源必须直接写入函数注释，不把 F1 作为 graph 主指标：

```python
def _node_set_f1(predicted_count: int, reference_count: int, true_positive: int) -> dict[str, float]:
    # 节点集合 F1 基线公式：
    # $$
    # TP = |M|,
    # FP = |V_p| - |M|,
    # FN = |V_r| - |M|
    # $$
    # $$
    # P_v = TP / (TP + FP),
    # R_v = TP / (TP + FN),
    # F1_v = 2 * P_v * R_v / (P_v + R_v)
    # $$
    # 来源：Chinchor, "MUC-4 Evaluation Metrics", 1992（precision/recall 的
    # F-measure 定义）；本实验仅把它作为节点集合基线，不是 graph-level 指标，
    # 也不把边结构纳入该分数。
    ...
```

`M` 从 GED `vertex_path` 中提取：左右端点都非空且 node substitute cost 为 0 的 pair 视为命中；insert/delete 和非零替换不计入 TP。该函数只比较严格相同的节点标签数量，不读取 graph 边；输出字段固定为 `node_set_f1`。

#### 2.3.4 主指标：Graph Edit Distance（GED）

入口文件中的 `_compute_ged()` 使用带节点/边标签的 Graph Edit Distance。函数注释必须包含论文公式和来源：

```python
def _compute_ged(
    predicted_graph: object,
    reference_graph: object,
    edit_cost_profile: dict[str, object],
    solver_mode: str,
) -> dict[str, object]:
    # Graph Edit Distance 公式：
    # $$
    # GED(G_p, G_r) = min_{P in Paths(G_p, G_r)} sum_{o in P} c(o)
    # $$
    # 其中 P 由节点/边的 insert、delete、substitute 操作组成。
    # 来源：Bunke & Shearer, "A graph distance metric based on the maximal common subgraph",
    # Pattern Recognition Letters, 1998.
    # DOI: https://doi.org/10.1016/S0167-8655(97)00164-7
    #
    # 跨 case 归一化：
    # $$
    # GED_base = sum_v c_del(v) + sum_v c_ins(v)
    #           + sum_e c_del(e) + sum_e c_ins(e)
    # $$
    # $$
    # GED_sim = max(0, 1 - GED(G_p, G_r) / GED_base)
    # $$
    # GED_base 为 0 时，仅当两个 graph 均为空才返回 GED_sim=1，否则返回 0。
    ...
```

具体代码修改要求：

1. 将两个 `MilestoneGraph` 转为 `networkx.DiGraph`，节点属性保存稳定的 milestone label，边属性保存关系类型和方向。
2. milestone label 至少包含角色、terminal、约束列表及每条约束的 target、selector、operator、namespace、evaluator_hint、expected 规范化结构和 stage-goal 语义；随机 milestone id 不能作为主要语义标签。
3. `edit_cost_profile` 明确定义节点/边 insert、delete、substitute 的成本，以及约束字段的权重；相同 canonical label 的 substitute cost 必须为 0。
4. 不在 GED 计算期间临时调用 LLM 生成 cost。若使用 embedding 计算 label cost，必须把模型、版本、距离函数和 profile 写入 `index.json`。
5. `solver_mode=exact` 遍历 `networkx.optimize_edit_paths()` 至最优路径；`solver_mode=approximate` 使用同一接口的 timeout/upper-bound 能力保留当前最优路径。传入 `_node_substitution_cost`、节点 insert/delete cost、`_edge_substitution_cost` 和边 insert/delete cost。结果必须带 `solver_mode` 与 `optimal`，近似值不得标记为 exact。
6. case 输出 `ged_distance`、`ged_base`、`ged_similarity`、`edit_path`、`node_insertions`、`node_deletions`、`node_substitutions`、`edge_insertions`、`edge_deletions`、`edge_substitutions` 和 `edit_cost_profile`。

GED 是第一版正式主指标，因为它在同一个编辑路径中同时处理节点标签和边结构；不再额外叠加项目自定义复合分数。

#### 2.3.5 可选论文对照：Fused Gromov-Wasserstein（FGW）

只有显式传入 `--fgw` 时，入口才调用 `_compute_fgw_optional()`。公式和来源写在代码注释中：

```python
def _compute_fgw_optional(
    predicted_features: object,
    reference_features: object,
    predicted_relations: object,
    reference_relations: object,
    alpha: float,
) -> dict[str, object]:
    # Fused Gromov-Wasserstein distance：
    # $$
    # FGW_alpha = min_{T in Pi(a, b)}
    #   (1-alpha) * sum_(i,k) d_X(x_i^p, x_k^r) * T_ik
    #   + alpha * sum_(i,j,k,l) |C^p_ij - C^r_kl|^2 * T_ik * T_jl
    # $$
    # 来源：Vayer et al., "Optimal Transport for Structured Data with Application on Graphs",
    # ICML 2019, PMLR 97.
    # URL: https://proceedings.mlr.press/v97/titouan19a.html
    # FGW 是 distance，越小越相近；不要把它命名为 F1。
    ...
```

具体代码修改要求：

1. 节点特征复用 GED 的 milestone/constraint label projection。
2. 关系矩阵必须在报告中记录；标准 FGW 使用对称关系，当前有向 DAG 只能使用明确记录的无向最短路投影，不能直接把有向邻接矩阵伪装成标准 FGW 输入。
3. FGW 依赖不可用或关系投影未配置时，写出 `fgw_status=unavailable`，不伪造数值。
4. 输出 `fgw_distance`、`fgw_alpha`、`fgw_relation_projection` 和 `fgw_status`；FGW 只作为交叉验证，不改变 GED 主结论。

#### 2.3.6 指标输出与选择原则

第一版正式结论固定使用 `ged_similarity`，并同时保留 `node_set_f1` 作为旧指标基线。case 和 summary 不输出没有论文定义的项目自定义复合分数。若未来新增其他指标，必须在入口函数注释中给出论文、公式、参数和校准方法后再修改方案。

### 2.4 scripts 启动脚本



新增以下文件：

```text
scripts/start_milestone_reliability.sh
scripts/start_milestone_reliability_no_docker.sh
```

两个脚本都只要求 `--exp PATH`，并复用现有 `scripts/experiment_bootstrap.sh`。该 bootstrap 已能从 experiment JSON 的每个 benchmark spec 取得 `data_root`，再从相应 `benchmark.json` 取得 `source_root` 和 `max_workers`，因此不再新增第三个 reliability bootstrap，也不要求用户逐个传 `--source`。

`start_milestone_reliability_no_docker.sh` 参考现有 `start_experiment_no_docker.sh` 的真实启动链路：加载 `.env`、执行 `uv sync --frozen --no-dev --no-install-project --inexact`、遍历 `experiment_bootstrap_lines()` 去重安装所有 benchmark source，最后调用：

```bash
<project-venv-python> milestone_reliability.py \
  --exp "$experiment_config" \
  ...
```

`start_milestone_reliability.sh` 参考现有 `start_experiment.sh`：使用 `experiment_bootstrap_lines()` 一次解析全部 benchmark source，将宿主路径分别挂载到稳定的容器路径，再通过 `docker compose run --entrypoint bash` 调用容器内 `start_milestone_reliability_no_docker.sh`。容器内不再次递归调用 Docker。

两个脚本的帮助文本与参数保持一致：`--exp`、`--experiment-config`、`--workers`、`--ged-solver`、`--ged-timeout-seconds`、`--fgw`、`--random-seed`、`--env-file` 和 `--no-env-file`。非法参数返回 64，配置/source 不存在返回 66，缺少 Docker/Compose 返回 127。脚本不得打印密钥或完整配置。

脚本落地时按现有脚本的函数边界逐项实现，不把一大段 shell 拼接到 `main`：

1. 两个脚本都定义 `usage()`、`require_value()`、`absolute_path()`、`parse_common_args()`；解析 `--exp=PATH` 和 `--exp PATH` 两种写法，拒绝未知参数，保留其余参数到数组 `entrypoint_args`，不得用字符串重新拼接以避免空格路径和注入问题。
2. no-Docker 脚本 source `scripts/experiment_bootstrap.sh`，先将 experiment path 解析为绝对路径并验证文件，再调用 `experiment_bootstrap_lines "$project_root" "$experiment_config"`。逐行读取 benchmark、data_root、source_root、container_source_root、max_workers；以 source_root 为 key 去重，逐个调用现有 `install_benchmark_source`。没有 source_root 时不执行 `uv pip install`，交给 adapter.prepare_config() 报错。
3. no-Docker 脚本执行 `source_env_file`、`ensure_uv_environment`、`project_python`，随后运行 `"$python_executable" "$project_root/milestone_reliability.py" "${entrypoint_args[@]}"`；Python 返回码原样透传。禁止设置或覆盖 `HOME`、`CODEX_HOME` 等系统变量。
4. Docker 脚本先 `detect_compose`，再按 experiment_bootstrap_lines 解析所有 source_root，验证每个宿主路径在 project root 或显式配置的 source 目录内，构造唯一 `--volume host:container:rw`；experiment JSON 只挂载为只读或按现有 compose 约定挂载，不能把整个磁盘作为 volume。
5. Docker 脚本使用 `docker compose run --rm --entrypoint bash`（或检测到的 `docker-compose`），在容器内执行 `scripts/start_milestone_reliability_no_docker.sh --no-env-file ...`；不得在容器内再次调用 Docker，也不得丢失参数边界。
6. 帮助示例固定使用 `data/experiments/toolsandbox_milestone_reliability_partial_main.json` 与 `..._main.json`。参数错误退出 64，experiment 文件/benchmark source 不存在退出 66，Docker/Compose/uv/python 不存在退出 127。

### 2.5 API 文档

更新 `docs/apis/experiment.md`：

1. 说明 `milestone_reliability.py --exp PATH` 是独立可靠性实验入口。
2. 列出 `toolsandbox_milestone_reliability_main.json` 与 `toolsandbox_milestone_reliability_partial_main.json` 的用途、case 数和启动示例。
3. 说明入口不写 adapted case、不执行 evaluator，原生 graph 是 reference，generated graph 是 prediction。
4. 记录 experiment config 的复用字段、被忽略的矩阵维度、GED/FGW 论文定义、edit-cost profile、solver mode、关系投影和 `node_set_f1` 基线定义。
5. 不把入口内部函数登记为 `dynsteer` 公共 API，也不修改 `docs/apis/milestone.md` 的现有公共模块契约。

具体落地是在 `docs/apis/experiment.md` 末尾新增“Milestone reliability 实验”小节，逐项写明：入口命令只接受 `--exp`；两个配置文件的 509/25 case 数和 `case_ids` 位于 benchmark 对象末尾；输出路径为 `results_dir/milestone_reliability/<run_id>/`；case status 枚举及失败语义；`ged_distance`/`ged_base`/`ged_similarity`、edit path 和 strict_v1 字段；GED 的 Bunke & Shearer 论文 DOI、FGW 的 Vayer et al. ICML 2019 URL、F1 的 Chinchor 1992 来源；入口不调用 adapted cache、不运行 evaluator。文档公式只使用代码注释中的 `$$...$$` 包裹形式，不使用方括号数学环境。

### 2.6 运行依赖：`pyproject.toml` 与 `uv.lock`

1. 在 `pyproject.toml` 的 `[project].dependencies` 数组中、`polars` 后新增一行精确文本 `"networkx>=3.2",`；不新增 `scipy` 或 POT 作为第一版强制依赖。
2. 在项目根目录执行 `uv lock`，只接受 `uv.lock` 中 networkx 及其传递依赖的解析变化；若 uv 解析出无关包升级，停止并人工复核，不手工编辑 lock 文件中的 hash。
3. FGW 为可选功能：入口通过 `importlib.util.find_spec("ot")` 检查 POT；未安装时写出 `fgw_status=unavailable`，不修改主依赖以强制安装 POT。`--fgw` 开启但 `ot` 不存在属于 case metric unavailable，不使整批实验失败。

## 3. 输出契约

目录结构：

```text
<experiment results_dir>/milestone_reliability/<run_id>/
├── index.json
├── summary.json
└── cases/
    └── <benchmark>/<safe_case_id>.json
```

`index.json` 记录 schema version、experiment id、experiment config 路径与摘要、各 benchmark/data root/source 摘要（不写密钥）、去重后的 case 数、生成配置摘要、GED solver、edit-cost profile、可选 FGW 配置、被忽略的矩阵维度、random seed、开始/结束时间和所有 case 文件相对路径。

`cases/<case_id>.json` 至少包含：

```json
{
  "case_id": "...",
  "status": "completed|generation_rejected|generation_failed|ged_failed|adapt_failed",
  "reference": {"node_ids": [], "count": 0},
  "prediction": {"node_ids": [], "count": 0},
  "edit_path": [],
  "metrics": {
    "node_set_f1": 0.0,
    "ged_distance": 0.0,
    "ged_base": 0.0,
    "ged_similarity": 1.0,
    "ged_solver_mode": "exact",
    "fgw_distance": null,
    "fgw_alpha": null
  },
  "generation_report": {},
  "diagnostics": []
}
```

实际 schema 还必须补齐以下字段，示例中的简写不能直接作为实现目标：

```json
{
  "schema_version": "milestone_reliability.case.v1",
  "experiment_id": "toolsandbox_milestone_reliability_partial_main",
  "run_id": "20260807T120000.000000Z",
  "benchmark": "toolsandbox",
  "case_id": "...",
  "status": "completed",
  "started_at": "2026-08-07T12:00:00Z",
  "finished_at": "2026-08-07T12:00:03Z",
  "elapsed_ms": 3000,
  "reference": {
    "node_ids": ["m1"], "node_count": 1, "edge_count": 0,
    "descriptor_digest": "sha256:..."
  },
  "prediction": {
    "node_ids": ["g1"], "node_count": 1, "edge_count": 0,
    "descriptor_digest": "sha256:..."
  },
  "metrics": {
    "node_set_f1": {"precision": 1.0, "recall": 1.0, "f1": 1.0,
                    "tp": 1, "fp": 0, "fn": 0},
    "ged_distance": 0.0, "ged_base": 2.0, "ged_similarity": 1.0,
    "solver_mode": "exact", "optimal": true,
    "edit_path": [],
    "node_insertions": 0, "node_deletions": 0,
    "node_substitutions": 1, "edge_insertions": 0,
    "edge_deletions": 0, "edge_substitutions": 0,
    "edit_cost_profile": {"profile_id": "strict_v1"},
    "fgw": {"status": "disabled", "distance": null,
            "alpha": null, "relation_projection": null}
  },
  "generation_report": {},
  "diagnostics": []
}
```

`node_set_f1` 是对象而不是单个 float，summary 才能分别汇总 precision/recall/f1；兼容读取方时可额外写 `node_set_f1_value`，但不得覆盖对象。失败 case 的 `reference`/`prediction` 计数按已知信息填写，未知为 `null`；`metrics` 中无法计算的数值为 `null`，不填 0。

`summary.json` 的 `status_counts` 必须覆盖 `completed`、`generation_rejected`、`generation_failed`、`adapt_failed`、`ged_failed`；`failed_case_count` 是除 completed 外的数量。`ged_similarity` 只在 completed 且 `optimal`/近似状态明确记录时汇总，并同时给出 `exact_count` 与 `approximate_count`。`index.json` 的 `case_files` 为相对 run_dir 的 POSIX 路径，按 group/case 原始顺序排列。

`summary.json` 包含 case 状态计数、`ged_similarity` 的 macro 汇总、GED 编辑操作总数、`node_set_f1` 基线、可选 FGW 汇总和完整 `metric_definition`。错误 case 保留错误类型和短消息，不写堆栈中的密钥。

## 4. 实施顺序与验收

1. 先创建两个 reliability JSON，并测试其 `experiment_id`、benchmark、data_root、case 数、case 顺序/摘要、`use_origin_milestone=false`、generator profile 和 strict GED profile。
2. 在入口文件实现节点/边 descriptor、固定 edit-cost profile 和纯函数 `_node_set_f1()`；补齐空图、单边为空、完全相同图和单节点/单边编辑测试。
3. 实现 `_compute_ged()`，覆盖节点/边插入、删除、替换、exact/approximate solver 标识、`GED_base` 与 `GED_sim` 公式；至少断言：相同图 `GED=0/GED_sim=1`，单边空图与单节点图 `GED=1/GED_sim=0`，单节点标签替换在 strict profile 下 `GED=1/GED_base=2/GED_sim=0.5`，边方向反转产生删除与插入成本。
4. 接入 adapter 原生 graph/生成 graph 双链路；测试确认 reliability config 的内存 `method` 覆盖确实得到 origin graph，不创建或修改 `adapted_cases` 文件，`use_origin_milestone` 只在内存配置中被覆盖。
5. 实现输出写入和增量 case 文件；每个 case 写出 `node_set_f1`、GED 原值/base/similarity、solver mode、edit path 摘要和可选 FGW，测试中断/单 case 失败不会丢失已完成 case JSON。
6. 实现 no-Docker 脚本，再实现 Docker 包装脚本；只做 `--help`、非法参数、experiment config/source 解析和 entrypoint 传递的 shell smoke test。
7. 更新 API 文档，执行静态检查，删除测试产生的 `__pycache__`、`.pytest_cache` 等中间目录；不得删除或覆盖 `docs/constraints`、`docs/plans` 中已有文档，也不得修改 `.gitignore`。

建议验收命令：

```bash
uv run pytest tests/test_milestone_reliability.py --cov=milestone_reliability --cov-fail-under=80
uv run python milestone_reliability.py --help
bash scripts/start_milestone_reliability_no_docker.sh --help
```

先用 25-case partial 配置验收：

```bash
./scripts/start_milestone_reliability_no_docker.sh \
  --exp data/experiments/toolsandbox_milestone_reliability_partial_main.json
```

partial 验收通过后，再用 Docker 运行 509-case main 配置：

```bash
./scripts/start_milestone_reliability.sh \
  --exp data/experiments/toolsandbox_milestone_reliability_main.json
```

验收重点不是某个预设分数，而是：原生和 generated graph 都被记录、GED 可由 edit-cost profile 和 edit path 复算、exact/approximate 状态没有混淆、失败 case 不被静默跳过、常规 `main.py` 结果目录和 adapted case 没有变化。

## 5. 不修改文件与实现约束

1. Python 依赖继续由 uv 管理，LLM API key 只从环境变量读取；配置和报告不得硬编码敏感信息。
2. adapter、compiler、graph parser 等已有函数直接复用；不新增只转发已有函数的入口层。
3. descriptor、GED/FGW 调用、F1 基线和写出逻辑只在 `milestone_reliability.py` 实现；不新增只供单实验使用的 `dynsteer` 模块或包级导出。
4. 核心函数具备类型标注、空值检查、结构化异常处理和中文 docstring；关键生成/匹配/写出步骤输出简短结构化中文日志。
5. GED 不进行网络请求；按 case 增量写文件，避免全量结果驻留内存。若 exact solver 超时，只能显式切换并记录 approximate mode。
6. 不修改 `main.py`、现有常规启动脚本、`.gitignore`，也不删除任何已有计划或约束文档。

## 附录A. 项目中没有把握实现的模块部分

1. **GED edit-cost profile 的校准**：GED 本身有明确论文定义，但节点/边替换成本决定了“约束相近”的具体含义。当前仓库没有已校准的 milestone label cost，因此第一版必须把 profile 固定、写入报告，并通过少量人工样本校准；不能把任意一组权重宣称为客观真值。
2. **各 benchmark 的公开 view 完整性**：`BaseBenchmarkAdapter.generator_task_view()` 是现有跨 benchmark 扩展点，但不同 adapter 是否能同时提供可靠的 origin graph 和公开 view，需要在落地时逐个核验。ToolSandbox 链路已有明确实现；若其他 benchmark 缺少任一部分，方案只能记录 `adapt_failed` 或 `generation_rejected`，不能凭空补造 reference。
3. **FGW 的有向关系投影**：FGW 论文公式主要针对带关系距离的结构化对象，当前 milestone graph 是有向 DAG。若实现 FGW，必须明确采用无向最短路关系矩阵或找到有向关系扩展论文；在此之前只实现 GED，不把未经定义的有向矩阵输入 FGW。
4. **统一实验矩阵维度的取舍**：同一 case 会因 model、method、repeat 和 threshold profile 展开为多份 `ExperimentRunSpec`，但 milestone 生成配置目前只定义在 benchmark spec 上。本方案按 `(benchmark, data_root, case_id)` 去重，适合“生成算法可靠性”实验；若后续把 generator 或 edit-cost profile 配置移到 model/method 维度，必须把相应维度加入可靠性输出主键，不能继续静默合并。
