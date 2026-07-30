# Experiment API

## 目标

`dynsteer.experiment` 负责统一编排 ToolSandbox、SWE-bench Pro 等 benchmark 的实验矩阵，避免把 `{benchmark, model, method, repeat}` 组合逻辑塞进 harness 或 evaluator。

## 配置入口

```bash
python main.py --exp data/experiments/double_benchmark_initial.json
```

配置字段：

- `experiment_id`: 实验 ID。
- `benchmarks`: benchmark 数组，每项包含 `benchmark`、`data_root`，可选 `case_ids/scenarios` 和 `metadata`。
- `models`: 模型数组，每项包含 `model_id`，可选 `metadata`、`harness_metadata`。
- `methods`: 方法数组，支持 `default`、`dynsteer_evaluate`、`dynsteer_replay`、`dynsteer_replay_static`。
- `judge_profiles`: Judge 的 provider、model、base_url、temperature、max_tokens、max_retries 等配置；API key 只从环境变量读取。
- `threshold_profiles`: `ThresholdConfig` 字段集合。
- `threshold_matrix`: 需要展开的阈值档位。

启动脚本 `scripts/start_experiment.sh` 与 `scripts/start_experiment_no_docker.sh` 会根据 `benchmarks[*].data_root` 读取对应 `benchmark.json`，自动使用 `source_root` 安装或挂载 benchmark 源码，并使用 `max_workers` 作为默认 worker 数；`--source`、`--workers` 仅作为覆盖项。`--force_adapt` 会在评估前强制重建 `data/<benchmark>/adapted_cases`，并自动等价于 `--force_eval`；`--force_eval` 只强制重跑评估 case 产物；`--no_sum` 只跳过实验级汇总文件。
直接调用 `main.py --exp ...` 时，benchmark 源码仍需要已在当前环境中可导入；自动 bootstrap 逻辑只在 wrapper 脚本中执行。

ToolSandbox 的 agent/user 连接参数应放在 `models[*].harness_metadata` 中，与 `agent`、`user` 角色类型同级：

```json
{
    "model_id": "GPT_4_o_2024_05_13",
    "harness_metadata": {
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
}
```

`judge_profiles` 只配置 DynSTEER Judge，不会影响 ToolSandbox 原生 agent/user role；不要把 agent/user 的 client 配置写到 `judge_profiles`。

## 执行流程

`run_experiment(config_path, workers=1, force_adapt=False, force_eval=False, no_sum=False)` 会先展开实验矩阵，再按 benchmark 名称准备 `TaskCase` 模板。每个 benchmark 在同一次实验中只调用一次 `load_task_case(...)`；`model`、`method`、`judge_profile`、`threshold_profile` 和 `repeat` 不会触发 adapted case 重新加载或重建。

`force_adapt=True` 是 adapted case 重建的唯一显式开关，并且只作用于上述统一准备阶段；由于基础输入已变化，它会自动强制 `force_eval=True`。后续 `default`、`dynsteer_replay`、`dynsteer_replay_static`、`dynsteer_evaluate` 会从同一批模板深拷贝得到单 case 输入，运行期对 `TaskCase.initial_state` 或 metadata 的写入不会污染其他 method。`force_eval=False` 时，若 case 级 `runs/results` 产物完整存在，会直接复用缓存；`no_sum=True` 时仍会写 case 级产物，但不写 `index.json`、`scores.json` 和 `metrics.json`。

Default 输出的 `trajectory.json` 会携带本次 session 的 `runtime_initial_state`。Replay 读取 default trajectory 后，会优先把这个 runtime initial state 注入当前 `TaskCase.initial_state`，确保 `preserve_state`、`reference_milestone_node_index=-1` 等状态约束使用 default 真实初始状态，而不是 adapted JSON 中可能过期的静态占位。

## 输出

实验层输出到 `results/experiments/<experiment_id>/` 或配置指定的 `results_dir`；case 产物按 `<benchmark>/<method>/<case_id>/` 分层写入。统一实验默认目录为：

- `runs/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/`
- `results/experiments/<experiment_id>/<benchmark>/<method>/<case_id>/`

- `index.json`: 分层 case 索引，按 `benchmark -> method -> model_id -> repeats -> repeat_index -> cases -> case_id` 组织。
- `scores.json`: `method -> benchmark -> model_id -> average_score`。
- `metrics.json`: PSEP、`rank_tau`、耗时、步骤数、Agent/Judge token 汇总。

`index.json` 的 repeat 节点只保留 `cases`，case 叶子只保留结果本身，不再重复写 `experiment_id`、`benchmark`、`case_id`、`model_id`。

```json
{
    "experiment_id": "double_benchmark_initial",
    "case_count": 4,
    "results": {
        "toolsandbox": {
            "default": {
                "toolsandbox_gpt4o": {
                    "repeats": {
                        "0": {
                            "cases": {
                                "add_contact_with_birthday": {
                                    "score": 1.0,
                                    "default_score": 1.0,
                                    "dynsteer_score": null,
                                    "resolved": true
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
```

case 产物仍复用 harness 目录：

- Default: `default_report.json`、`summary.json`、`raw_summary.json`、`trajectory.json`。
- Replay/Evaluate: `report.json`、`summary.json`、`raw_summary.json`、`trajectory.json`。

`scores.json` 与 `metrics.json` 保持纯 JSON 结构，不内嵌注释字段；字段语义通过本文档说明：

- `scores.json`: 每个叶子值都是同一 `method / benchmark / model_id` 下的 case 平均分。
- `metrics.json`: `efficiency` 汇总耗时与步骤数，`cost` 汇总 token，`psep` 与 `rank_tau` 是跨模型对比指标。

## 指标

- `case_score(value)`: 将 bool、数字或含 `score/resolved/similarity` 的对象归一到 `[0, 1]`。
- `model_scores(results)`: 计算 `S_{m,e,b}`。
- `psep(scores)`: 计算模型对在平均得分上的间距。
- `aggregate_efficiency(results)`: 汇总耗时和 Agent 步骤数。
- `aggregate_cost(results)`: 汇总 Agent trajectory token 和 Judge LLM token，并保留可用性标记。
