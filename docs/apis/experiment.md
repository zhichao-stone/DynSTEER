# Experiment API

> 当前结果 schema 为 v3：case 结果仅保留单一 `score`，default/evaluate/replay 均使用 `report.json`；replay 不再内嵌 `default_reference`，通过 experiment identity 与对应 default case 关联。

## 元评估指标

`metrics.json` 不再输出 PSEP 或二元成功一致率，保留连续分与运行统计，并新增：

- `discriminability_score`：按 method/benchmark 输出 `0.01` 至 `0.05` 五个阈值。单阈值公式为 `(population_stddev / mean_score) * sqrt(significant_pair_count / pair_count)`，显著模型对要求归一化分差严格大于 epsilon。每项同时输出模型数、模型对数、均值、总体标准差、显著模型对数量/比例和最终 score；模型不足两个时 score 为 `null`，全零均值时为 `0.0`。
- `score_delta`：按相同 benchmark/model/case/repeat 配对 DEFAULT 与 replay，输出连续分差的配对数和均值。
- `coverage_counts`、`minefield_counts`、`termination_counts`：分别汇总 replay 覆盖状态、minefield 命中状态和结构化终止原因。
- `score_rank_tau`：按模型连续均分计算 DEFAULT 与 replay 的排名一致性。
- `repeat_rank_consistency`：按 method/benchmark 分别比较 repeat 内模型均分排名，输出 tau-b、完整顺序一致率、top-1 一致率和平均绝对名次位移。它衡量同一方法的重复运行稳定性，不与 `score_rank_tau` 合并为方法总分。

`ExperimentCaseResult` 不包含 `resolved` 或 `successful`。`milestone_coverage` 仅表示 replay 的结构化覆盖状态，不转换为 benchmark success。

实验 repeat 从 `0` 开始计数。只要存在 experiment metadata，case 输出位于 `<benchmark>/<model>/r<repeat_index>/<method>/<case>`，并在 metadata 中记录 `repeat_count`。`metrics.json` 额外包含 `repeat_statistics`、`rank_tau_by_repeat` 与 `repeat_rank_consistency`。前两者分别提供 repeat 分数统计和同 repeat 的 DEFAULT↔replay 排序关联；`repeat_rank_consistency` 只在同一方法内比较不同 repeat，并保留逐 repeat 排名与 pairwise 明细。缺失模型、缺失或非有限 score、有效模型不足两个时，对应 pair 记入 `invalid_pairs`，不足有效 pair 的汇总值为 `null`。

分数均值只在存在有效分数时计算。某个 repeat、方法或模型桶内没有有效 native score 时，对应 repeat score、均值、标准差或 completion rate 为 `null`，不会用 `0.0` 表示；真实全失败仍会得到 `0.0`。

## Agent step 计数

`runtime_metrics.step_count` 表示已闭合的 Agent outbound 数量，不表示 LLM 请求轮数。一次模型响应中的多个并行 tool calls 分别计数：两个调用都返回 result 时计 2，尚未返回 result 的 pending 调用不计数。`average_agent_step_count` 聚合相同口径；`raw_step_count` 与 `average_raw_step_count` 仍统计完整 raw trajectory steps，不受闭包分组影响。

## 动态 target 生命周期

每个 `ExperimentRunSpec` 开始时独立调用一次 `prepare_task_cases(..., refresh_dynamic_targets=True)`。该 spec 的 default 与 replay 都只对这批已刷新 TaskCase 做深拷贝，replay 在读取 default trajectory 后不会再次调用 adapter 或重新生成 scenario。

ToolSandbox adapter 与 harness 按 data root、backend 和 source root 共享同一份 scenario dictionary，因此当前 `Constraint.expected` 与 default starting context 来自同一次 scenario 生成。不同 experiment 的 default output cache key 包含 `experiment_id`，不会跨实验误用 default 结果。

## 随机种子

`main.py` 支持 `--random_seed NUM` 和 `--random-seed NUM`，默认值为 `202608`。参数在加载 experiment 和构造 ToolSandbox scenario 前调用 `random.seed()`，因此可以固定 ToolSandbox `named_scenarios()` 中基于 Python `random.shuffle()` 的工具顺序。

随机种子不冻结 ToolSandbox 的 `datetime.now()` 动态时间戳，也不控制远端 LLM provider 的随机采样。需要跨进程严格比较动态 expected 时，还应让各进程在相同时间基准下构造 scenario，或进一步显式注入实验时钟。

## 配置入口

```bash
./scripts/start_experiment.sh --exp data/experiments/swebench_pro_main.json --workers 1
```

`start_experiment.sh` 固定注入 `DYNSTEER_BENCHMARK_EXECUTION=docker`，`start_experiment_no_docker.sh` 固定注入 `host`。执行方式不接受实验 JSON 或环境变量覆盖；SWE-bench Pro 与 SkillsBench 新的 default 执行只能使用 Docker profile，host profile 仅允许适配、已有 default 轨迹 replay 与离线汇总。

`main.py --exp PATH` 可直接展开 ToolSandbox 配置；SWE/Skills 新的 default 执行缺少 profile 时会直接报错，必须通过任一启动脚本选择能力边界。`--force_adapt` 强制重建 adapted case 并自动等价于 `--force_eval`；`--force_eval` 只强制重跑评估产物；`--no_sum` 跳过实验级汇总。

## 配置字段

统一实验 JSON 顶层字段如下：

```json
{
  "experiment_id": "swebench_pro_main",
  "repeats": 1,
  "models": [{"model_id": "qwen-plus-2025-12-01"}],
  "methods": ["default", "dynsteer_replay"],
  "threshold_profiles": {"default": {}},
  "benchmarks": [
    {
      "benchmark": "swebench_pro",
      "data_root": "data/swebench_pro",
      "milestone_generation": {
        "generator": {
          "provider": "openai_compatible",
          "model": "qwen3-max-2026-01-23"
        }
      },
      "case_ids": ["<固定 case ID>"],
      "metadata": {}
    }
  ]
}
```

每份配置只能包含一个 benchmark；运行时以顶层 `experiment_id` 作为实验身份，不校验配置文件主名。顶层展开顺序是 `benchmarks × models × methods × threshold_matrix × repeats`；`benchmarks[*]` 支持 `benchmark`、`data_root`、`milestone_generation`、`case_ids/scenarios` 和 `metadata`；`models[*]` 支持 `model_id`、`judge_profile`、`harness_metadata` 和 `metadata`；方法项支持字符串或 `{ "method": ..., "judge_profile": ..., "strategy": ..., "harness_metadata": ..., "metadata": ... }`。

`data/experiments/model_shards` 中的 `_1..4` 是同一正式配置的 4 个全模型 shard；每份 shard 保留完整模型矩阵、基础 `experiment_id`，并将排序后的 `case_ids` 按索引取模均分为互斥子集。因此四个 shard 的 runs/results 默认都写入 `runs/exp/<基础ID>` 与 `results/exp/<基础ID>`。分批窗口可用 `--no_sum` 只写 case 产物；全部完成后运行原始配置，完整存在的 case 输出会直接读取，缺失的才补跑，并统一写出实验级汇总。

消融配置可提供顶层 `source_experiments: [{ "config": "<相对当前配置的主实验 JSON>", "methods": [...] }]`。非强制评估启动时，runner 会按 `benchmark/model/repeat/method/case/threshold_profile` 匹配来源 spec，并要求 judge、threshold、strategy、milestone generation、metadata 与 data root 完全一致；来源 case 四个必要产物完整时复制到当前实验目录，来源配置或产物缺失时正常执行当前实验。复制后的 `summary.json`、`report.json` 与 `raw_summary.json` 会改写当前实验身份并写入 `reused_from`，`trajectory.json` 保持不变。`--force_eval` 或 `--force_adapt` 不导入来源产物。

没有 `runs_dir`/`results_dir` 时，默认从 `experiment_id` 派生为 `runs/exp/<experiment_id>` 和 `results/exp/<experiment_id>`。`case_ids` 必须显式固定，保证跨实验可比。

## Milestone generator

`milestone_generation.generator` 属于 benchmark spec，字段集合是 `provider`、`model`、`base_url`、`api_key`、`timeout_seconds`、`temperature`、`max_tokens`、`max_retries`、`retry_base_seconds`、`retry_max_seconds` 和 `seed`。正式与 pilot 配置必须直接写非空 `api_key` 和 `base_url`，不回退环境变量。

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

- `index.json`：按 benchmark、method、model、repeat 和 case 组织，case 叶子保留 `score`、`native_score`、覆盖/雷区/终止信息、结构化 `failure`、interventions、适配与运行指标。
- `scores.json`：`method -> benchmark -> model_id -> average_score`。
- `metrics.json`：效率、成本、区分度、DEFAULT↔replay delta、排名一致性、repeat 一致性、coverage/minefield/termination 计数和 `task_completion`。
- `costs.json`：逐 case 三段成本、终止分类、适配分摊、相对 DEFAULT delta 和证据路径。

`task_completion` 只由 native score 计算；replay 缺少 native score 时不回填 DynSTEER score，也不进入完成率分母。

## 失败标记

case 降级为失败结果时，`raw_summary.json`、`summary.json`、`report.json` 和 experiment `index.json` 都写入同一份 `failure` 对象，字段包含 `failure_type` 与 `error`。LLMJudge 输入长度超限标记为 `llm_judge_input_length_exceeded`，其他 LLMJudge 响应失败标记为 `llm_judge_evaluation_failed`；对应 case 的 score 为 `null` 并跳过评估，experiment 继续执行后续 case。provider 400 这类确定性客户端错误不再重试，带 `failure` 的旧输出也不作为可复用完整结果。

## 凭据边界

`milestone_generation.generator.api_key`、judge profile 与 SWE/Skills `harness_metadata.client` 都是模型凭据来源，必须直接保存在本私有仓库实验配置中；日志与公开报告只输出模型名、host 和摘要。ToolSandbox 继续使用 `harness_metadata.agent_client` 与 `user_client`。
