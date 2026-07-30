# 2026-07-27 experiment reliability 与冗余清理代码修改方案

## 1. 当前核查结论

本方案针对 `dynsteer/experiment` 新增实验层中已经出现的未接入代码和冗余函数进行清理。核心结论如下：

1. `dynsteer/experiment/reliability.py` 当前没有业务调用点。全仓代码中没有 `from dynsteer.experiment.reliability ...` 或 `experiment.reliability` import，函数只在 2026-07-23 的历史方案文档中出现。
2. `reliability.py` 的存在原因是上一版“双 Benchmark 初步实验方案”预设了 `reliability.json`、ToolSandbox milestone F1 和 SWE-bench Pro 人工审阅汇总，但当前 `run_experiment()` 实际只写出 `index.json`、`scores.json`、`metrics.json`，没有接入 reliability 汇总链路。
3. `toolsandbox_reference_milestones(case)` 只是 `case.milestone_graph.nodes` 的一行列表推导，不具备公共函数抽象价值。继续保留还需要额外测试和文档，反而增加维护成本。
4. `experiment/metrics.py` 的 `rank_tau()` 只是 `kendall_tau(default_scores, method_scores)` 的中转函数。保留 JSON 输出字段 `rank_tau` 可以，但不需要保留公开中转函数。
5. `experiment/runner.py` 的 `_number()` 与 `dynsteer/utils.py` 中的 `as_number()` 职责重复。真正需要修复的是 `utils.as_number()` 当前注释与实现不一致：注释写明 bool 返回默认值，但实现会把 `True/False` 当成 `1.0/0.0`。
6. `_number()` 中 `isinstance(value, bool)` 不是多余判断。Python 中 `bool` 是 `int` 的子类，`isinstance(True, (int, float))` 为 `True`。对 `default_score`、`overall_score` 这类数值字段，bool 应该被排除；对 `case_score()` 这类明确支持 benchmark 原生 `resolved: bool` 的入口，bool 才应该转为 0/1。

## 2. 修改目标

1. 删除未接入、职责混杂、带有 benchmark 专属命名的公共 `reliability.py`。
2. 保留实验输出语义，不改变 `index.json`、`scores.json`、`metrics.json` 的结构。
3. 删除只做函数中转的 `rank_tau()`，让 `_rank_tau_table()` 直接调用 `kendall_tau()`。
4. 修正并复用 `utils.as_number()`，删除 `runner._number()`。
5. 只为真实公共逻辑补测试，不为了已删除的一行函数额外添加测试。
6. 更新 API 文档中不再存在的公开函数说明，避免文档继续诱导新增冗余接口。

## 3. 详细代码修改方案

### 3.1 删除 `dynsteer/experiment/reliability.py`

修改方式：

1. 删除整个 `dynsteer/experiment/reliability.py` 文件。
2. 不在 `dynsteer/experiment/__init__.py` 中新增任何 reliability 导出。当前 `__init__.py` 本来也没有导出该模块，因此删除不会影响项目内部调用。
3. 不新增替代的 `experiment/reliability.py` 空文件，也不新增同名占位函数。没有真实调用链时保留占位模块，会继续制造“公共 API 已存在”的错觉。
4. 历史计划文档不删除、不回改。只在本次新方案中说明该模块属于半落地预设代码。

后续如确实需要 reliability 指标，应按使用场景重新落位：

1. ToolSandbox milestone F1：只有当实验汇总真的要输出 `reliability.json` 时，再放入 `dynsteer/adapter/toolsandbox/` 的 adapter 专属指标模块，或在 `experiment/metrics.py` 中设计 benchmark 无关入口，并由 adapter 提供提取器。
2. SWE-bench Pro 人工审阅样本：等 SWE-bench Pro adapter 具备真实数据加载、patch 验证、pseudo-stage 结果后，再放入 `dynsteer/adapter/swebench/` 或独立脚本，而不是放入公共 `experiment` 模块。
3. `toolsandbox_reference_milestones(case)` 这种一行逻辑，未来如果只在单个调用点使用，直接写列表推导：

```python
reference = [milestone.milestone_id for milestone in case.milestone_graph.nodes]
```

### 3.2 清理 `ExperimentAggregate` 的半落地 reliability 字段

当前 `dynsteer/experiment/model.py` 中的 `ExperimentAggregate` 只被 `dynsteer/experiment/__init__.py` 导出，没有业务调用点。它还包含 `reliability` 字段，会暗示实验层存在可靠性聚合产物，但当前 runner 没有写该聚合对象。

建议修改：

1. 删除 `ExperimentAggregate` dataclass。
2. 从 `dynsteer/experiment/__init__.py` 的 import 与 `__all__` 中删除 `ExperimentAggregate`。
3. 保留 `ExperimentCaseResult` 作为当前真实使用的 case 级结构。
4. 后续如果需要统一聚合对象，应在 `write_metric_tables()` 或新的真实汇总入口落地后再引入，不提前设计空壳。

风险说明：

1. 项目内部没有调用点，删除风险低。
2. 若外部脚本已经手动 import `ExperimentAggregate`，会产生兼容性影响。但当前项目约束明确默认不考虑旧接口兼容，除非任务显式强调。

### 3.3 删除 `metrics.rank_tau()` 中转函数

当前代码：

```python
def rank_tau(method_scores: Mapping[str, float], default_scores: Mapping[str, float]) -> float:
    """以 Default 模型排序为参照计算 RankTau。"""
    return kendall_tau(default_scores, method_scores)
```

建议修改：

1. 删除 `rank_tau()` 函数。
2. `_rank_tau_table()` 中直接调用 `kendall_tau(default_scores, benchmark_scores)`。
3. 保留 `metrics.json` 中的 `"rank_tau"` 字段名，因为这是实验指标名，不等于必须存在同名 Python 函数。
4. 更新 `docs/apis/experiment.md`，将公开函数列表中的 `rank_tau(method_scores, default_scores)` 删除，改为说明 `metrics.json.rank_tau` 是“相对 Default 排序的 Kendall tau-b 输出字段”。

目标代码形态：

```python
result[method][benchmark] = kendall_tau(
    {str(key): float(value) for key, value in default_scores.items()},
    {str(key): float(value) for key, value in benchmark_scores.items()},
)
```

说明：

1. 这里不新增 `_rank_tau()` 或 `_scores_to_float_mapping()` 之类的新 helper，避免把一个中转函数换成另一个中转函数。
2. `kendall_tau()` 仍保留，因为它包含真实排序相关计算逻辑，并且值得单独测试。

### 3.4 修正 `utils.as_number()` 并替换 `runner._number()`

当前 `dynsteer/utils.py`：

```python
def as_number(value: object, default: float | None = None) -> float | None:
    """读取 JSON 数字值，bool 或非数字返回默认值。"""
    if not isinstance(value, int | float):
        return float(default)
    return float(value)
```

问题：

1. `bool` 会通过 `int | float` 判断，导致 `as_number(True)` 返回 `1.0`，与注释不符。
2. `default=None` 且输入非数字时会执行 `float(None)`，抛出 `TypeError`。

建议修改为：

```python
def as_number(value: object, default: float | None = None) -> float | None:
    """读取 JSON 数字值，bool 或非数字返回默认值。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)
```

同步修改：

1. `dynsteer/experiment/runner.py` 从 `dynsteer.utils` import `as_number`。
2. `_case_result_from_output()` 中：

```python
default_score = as_number(summary.get("default_score"))
if default_score is None and default_reference is not None:
    default_score = as_number(default_reference.get("score"))
dynsteer_score = None if spec.method == ExperimentMethod.DEFAULT else as_number(summary.get("overall_score"))
```

3. 删除 `runner._number()`。
4. 不修改 `experiment.metrics.case_score()` 对 bool 的处理。`case_score()` 是 benchmark 原生结果归一入口，支持 `resolved: bool` 是合理语义；`as_number()` 是 JSON 数字读取工具，不应该把 bool 当数字。

### 3.5 更新文档

需要更新：

1. `docs/apis/experiment.md`
   - 删除公开函数 `rank_tau(method_scores, default_scores)` 的说明。
   - 保留输出字段 `metrics.json.rank_tau` 的说明。
   - 不新增 `reliability.json` 文档，避免文档描述不存在的产物。

不需要更新：

1. 历史 `docs/plans/*.md`。这些是历史设计记录，不作为当前 API 承诺。
2. `README.md`，当前 README 只说明 `index.json`、`scores.json`、`metrics.json`，与本次清理后的输出一致。

## 4. 测试方案

### 4.1 单元测试

新增或调整 `tests/test_utils.py`：

1. `as_number(1) == 1.0`
2. `as_number(1.5) == 1.5`
3. `as_number(True) is None`
4. `as_number(False, 0.25) == 0.25`
5. `as_number("1", 0.0) == 0.0`
6. `as_number(None) is None`
7. `clamped_number(True, default=0.4) == 0.4`
8. `clamped_number(2.0) == 1.0`

新增或调整 `tests/experiment/test_metrics.py`：

1. `kendall_tau()` 覆盖完全一致排序、完全反向排序、并列分数、有效模型不足两个。
2. `write_metric_tables()` 覆盖单模型情况下 `psep == 0.0`、`rank_tau == 0.0`，确保删除 `rank_tau()` 函数后输出字段不变。
3. `case_score()` 覆盖 bool、数字、`{"score": ...}`、`{"resolved": ...}`，确保 bool 语义只在 benchmark 结果归一入口保留。

新增或调整 `tests/experiment/test_runner.py`：

1. 通过最小 summary/default_reference 构造 `_case_result_from_output()` 的输入，确认 bool 类型 `default_score`、`overall_score` 不会被误写为 `1.0/0.0`。
2. 若不希望测试内部函数，则用 `run_default_case`/`run_replay_case` 的文件产物夹具覆盖同等行为。考虑当前实验层仍是新模块，优先直接覆盖 `_case_result_from_output()` 可以降低测试成本。

### 4.2 静态核查命令

```bash
rg -n "from dynsteer\\.experiment\\.reliability|experiment\\.reliability|milestone_f1|toolsandbox_reference_milestones|toolsandbox_predicted_milestones|write_swebench_manual_review_sample|summarize_manual_review_labels" dynsteer tests docs/apis README.md scripts
```

预期：无输出。

```bash
rg -n "def rank_tau|rank_tau\\(" dynsteer tests docs/apis README.md scripts
```

预期：只有 `metrics.json` 输出字段说明或测试断言字段名，不再出现 `def rank_tau` 和对 `rank_tau()` 的函数调用。

```bash
rg -n "def _number|_number\\(" dynsteer/experiment
```

预期：无输出。

### 4.3 回归命令

```bash
uv run pytest tests/test_utils.py tests/experiment/test_metrics.py tests/experiment/test_runner.py
```

如需要完整实验链路回归，并且 ToolSandbox 源码已经可导入：

```bash
uv run python main.py --experiment-config data/experiments/double_benchmark_initial.json --workers 1
```

或使用脚本：

```bash
./scripts/start_experiment_no_docker.sh \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --source ../ToolSandbox \
  --workers 1
```

## 5. 预期实验输出样例

以下样例用于判断输出结构和字段变化，不代表真实实验分数。真实数值取决于 ToolSandbox 版本、模型配置、环境变量、case 集合和 Judge 设置。

### 5.1 终端输出样例

运行：

```bash
./scripts/start_experiment_no_docker.sh \
  --experiment-config data/experiments/double_benchmark_initial.json \
  --source ../ToolSandbox \
  --workers 1
```

预期终端核心输出形态：

```text
Installing benchmark source for experiment: ../ToolSandbox
toolsandbox/default/toolsandbox_gpt4o/add_contact_with_birthday
toolsandbox/dynsteer_replay/toolsandbox_gpt4o/add_contact_with_birthday
toolsandbox/default/toolsandbox_gpt4o/find_days_till_holiday
toolsandbox/dynsteer_replay/toolsandbox_gpt4o/find_days_till_holiday
```

若当前配置运行全部 `data/toolsandbox/adapted_cases`，输出会继续列出其他 case。每个 case 在 `default` 方法下会有一条结果，在 `dynsteer_replay` 方法下会有一条结果。

### 5.2 `results/experiments/double_benchmark_initial/index.json` 样例

```json
{
    "case_count": 4,
    "results": [
        {
            "experiment_id": "double_benchmark_initial",
            "run_id": "double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0",
            "benchmark": "toolsandbox",
            "case_id": "add_contact_with_birthday",
            "model_id": "toolsandbox_gpt4o",
            "repeat_index": 0,
            "method": "default",
            "default_score": 1.0,
            "dynsteer_score": null,
            "score": 1.0,
            "resolved": true,
            "runtime_metrics": {
                "elapsed_seconds": 42.18,
                "step_count": 12,
                "raw_step_count": 30,
                "trajectory_total_tokens": 18420,
                "trajectory_cost_available": true,
                "llm_total_tokens": 0
            },
            "output_paths": {
                "result_dir": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday",
                "raw_run_dir": "runs/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday",
                "summary_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/summary.json",
                "report_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/default_report.json",
                "trajectory_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/trajectory.json"
            },
            "raw": {
                "summary_metadata": {
                    "method": "default",
                    "model_id": "toolsandbox_gpt4o"
                },
                "default_reference": {
                    "score": 1.0,
                    "resolved": true
                }
            }
        },
        {
            "experiment_id": "double_benchmark_initial",
            "run_id": "double_benchmark_initial_toolsandbox_dynsteer_replay_toolsandbox_gpt4o_default_r0",
            "benchmark": "toolsandbox",
            "case_id": "add_contact_with_birthday",
            "model_id": "toolsandbox_gpt4o",
            "repeat_index": 0,
            "method": "dynsteer_replay",
            "default_score": 1.0,
            "dynsteer_score": 0.86,
            "score": 0.86,
            "resolved": true,
            "runtime_metrics": {
                "elapsed_seconds": 18.72,
                "step_count": 8,
                "raw_step_count": 30,
                "trajectory_total_tokens": 18420,
                "trajectory_cost_available": true,
                "llm_total_tokens": 6120
            },
            "output_paths": {
                "result_dir": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_dynsteer_replay_toolsandbox_gpt4o_default_r0/add_contact_with_birthday",
                "raw_run_dir": "runs/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday",
                "summary_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_dynsteer_replay_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/summary.json",
                "report_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_dynsteer_replay_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/report.json",
                "trajectory_path": "results/experiments/double_benchmark_initial/toolsandbox/double_benchmark_initial_toolsandbox_default_toolsandbox_gpt4o_default_r0/add_contact_with_birthday/trajectory.json"
            },
            "raw": {
                "summary_metadata": {
                    "method": "dynsteer_replay",
                    "model_id": "toolsandbox_gpt4o"
                },
                "default_reference": {
                    "score": 1.0,
                    "resolved": true
                }
            }
        }
    ]
}
```

### 5.3 `scores.json` 样例

```json
{
    "default": {
        "toolsandbox": {
            "toolsandbox_gpt4o": 0.72
        }
    },
    "dynsteer_replay": {
        "toolsandbox": {
            "toolsandbox_gpt4o": 0.81
        }
    }
}
```

### 5.4 `metrics.json` 样例

```json
{
    "efficiency": {
        "case_count": 20,
        "average_elapsed_seconds": 30.45,
        "average_agent_step_count": 9.8,
        "average_raw_step_count": 28.6
    },
    "cost": {
        "trajectory_total_tokens": 184200,
        "trajectory_cost_available": true,
        "llm_total_tokens": 61200,
        "llm_cost_available": true
    },
    "psep": {
        "default": {
            "toolsandbox": 0.0
        },
        "dynsteer_replay": {
            "toolsandbox": 0.0
        }
    },
    "rank_tau": {
        "dynsteer_replay": {
            "toolsandbox": 0.0
        }
    }
}
```

说明：

1. 当前示例配置只有一个 `model_id`，因此 `psep` 必然是 `0.0`。
2. 当前示例配置只有一个共同模型，`rank_tau` 也会是 `0.0`，这不是异常，而是 Kendall tau 在有效模型不足两个时无法提供排序信息。
3. 如果希望用 `psep` 和 `rank_tau` 判断模型区分度，实验配置中至少需要两个模型。
4. 清理后不会生成 `reliability.json`。这是预期行为，因为当前代码没有真实 reliability 入口。

## 6. 验收标准

1. `dynsteer/experiment/reliability.py` 不再存在。
2. `dynsteer/experiment/model.py` 不再包含未使用的 `ExperimentAggregate`。
3. `dynsteer/experiment/metrics.py` 不再包含 `rank_tau()` 函数，但 `metrics.json` 仍包含 `"rank_tau"` 输出字段。
4. `dynsteer/experiment/runner.py` 不再包含 `_number()`，统一使用 `utils.as_number()`。
5. `utils.as_number(True)` 返回默认值，不返回 `1.0`。
6. 单元测试覆盖公共数字读取、Kendall tau、实验指标表写出。
7. `uv run pytest tests/test_utils.py tests/experiment/test_metrics.py tests/experiment/test_runner.py` 通过。
8. 在 ToolSandbox 依赖可用时，统一实验脚本可以继续写出 `index.json`、`scores.json`、`metrics.json`。

## 附录A. 项目中没有把握实现的模块部分

1. SWE-bench Pro reliability 和人工审阅链路暂不建议实现。原因是当前 `dynsteer/adapter/swebench/` 仍是 scaffold 状态，真实仓库 checkout、Agent 运行、patch 验证、pseudo-stage 转换都没有落地。此时继续实现 `write_swebench_manual_review_sample()` 只会产生新的未使用公共函数。
2. ToolSandbox milestone F1 暂不建议迁移实现。原因是当前统一实验 runner 没有定义 `reliability.json` 的写出契约，也没有明确 milestone F1 应按 case、model、method、repeat 还是 benchmark 聚合。缺少输出契约时提前封装函数，会重复当前 `reliability.py` 的问题。
3. 完整实验样例中的真实分数没有把握预估。原因是分数依赖外部 ToolSandbox 源码、LLM 环境变量、模型返回、Judge 配置和当前 adapted case 集合。本方案只保证输出结构预期，不承诺数值。
