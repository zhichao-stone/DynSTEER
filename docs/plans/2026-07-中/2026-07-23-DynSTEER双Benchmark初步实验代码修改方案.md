# DynSTEER 双 Benchmark 初步实验代码修改方案

## 1. 目标

本方案对齐 `docs/plans/experiments/2026-07-14-DynSTEER双Benchmark初步实验方案.md`，目标是在当前代码基础上支持：

1. ToolSandbox 与 SWE-bench Pro 两个 benchmark 的统一实验编排。
2. `Default`、`DynSTEER-Replay`、主 `evaluate()` 动态监测式评估，以及后续 guidance 分实验的可复现实验记录；论文实验标签可继续对应 `DynSTEER-Steering`。
3. `PSEP`、`RankTau`、评估耗时、Agent 步骤数、Agent token 与 Judge token 的跨模型汇总。
4. 核心消融、小模型 Judge、阈值敏感性和 milestone / pseudo-stage 可靠性实验的最小闭环。

当前优先级建议是先完成主实验与 `DynSTEER-Replay`，同时为 SWE-bench Pro 预留继承基础 adapter / harness 的封装类框架；等 SWE-bench Pro 仓库下载到项目同级目录后，再补齐具体运行、轨迹转换和 pseudo-stage 实现。

## 2. 当前代码现状

| 方向 | 当前状态 | 主要缺口 |
|---|---|---|
| ToolSandbox 接入 | 已有 `dynsteer/adapter/toolsandbox`、harness、scorer、milestone graph 转换 | `raw_summary_from_session()` 当前返回 `native_evaluation_skipped=True`，没有落盘 benchmark 原生 `Default` 分数 |
| SWE-bench Pro 接入 | 未注册、无 adapter、无 harness、无 trace converter | 先新增继承基础类的封装类框架，具体实现等待外部仓库到位 |
| 评估入口 | `DynSTEEREvaluator.evaluate()` 负责边执行边评估，并可 `stop_case()` | 这是 DynSTEER 框架期望的主评估形态，应保留为主函数；另补离线 replay 入口 |
| 离线回放 | `docs/apis/evaluation.md` 明确历史 `evaluate_trajectory()` 已删除 | 需要重建整轨迹回放入口，并避免影响原生执行轨迹 |
| 实验矩阵 | `main.py` 只按单 benchmark 读取 `data/{benchmark}/run_configs.json` | 没有 `{benchmark, model, method, case, repeat}` 统一实验记录 |
| 指标汇总 | run 级 summary 只有平均 `overall_score`、tokens、耗时 | 没有模型级 `S_{m,e,b}`、`PSEP`、`RankTau`、方法对照表 |
| Judge 配置 | `load_harness_run_configs()` 会写入 `metadata["judge"]`，但 `DynSTEEREvaluator.from_env()` 只读环境变量 | 小模型 Judge、不同方法不同 Judge profile 还不能稳定复现 |
| 消融配置 | 动态路由和动态权重在 `_evaluate_stage()` 中固定启用 | 需要可关闭 dynamic routing / dynamic weights 的策略配置 |
| 成本观测 | Judge token 已统计，trajectory token 可聚合 | ToolSandbox 当前 `trajectory_cost_available=false`，Agent token 成本不可审计 |

## 3. 总体代码架构调整

新增一个轻量实验层，不把实验矩阵逻辑塞进 harness 或 evaluator：

```text
dynsteer/
  experiment/
    __init__.py
    model.py
    config.py
    runner.py
    metrics.py
    reliability.py
```

现有 harness 继续负责“如何运行 benchmark case”；evaluator 负责“如何对轨迹做 DynSTEER 阶段评估”；新增 experiment 层负责“同一批模型、case、方法如何比较与汇总”。

## 4. 必须修改的现有函数与类

### 4.1 `dynsteer/evaluate/evaluator.py`

| 函数/类 | 修改方案 |
|---|---|
| `DynSTEEREvaluator.__init__()` | 新增 `strategy: EvaluationStrategyConfig | None` 入参，控制是否启用动态路由、动态权重、策略提前终止。 |
| `DynSTEEREvaluator.from_env()` | 改为委托新的配置构造函数，避免只支持环境变量。 |
| 新增 `DynSTEEREvaluator.from_config(config, judge_config=None, strategy=None)` | 从 run / experiment 配置构建 evaluator，支持主 Judge、小模型 Judge、消融策略。 |
| 当前 `evaluate()` | 保留为主评估入口，语义就是在线执行 + 动态监测式阶段评估 + 必要时策略终止。 |
| 新增 `evaluate_replay(task_case, trajectory, scorer, config)` | 对完整轨迹做离线阶段式回放，不调用 `harness.stop_case()`，记录“虚拟早停”边界和 DynSTEER-Replay 分数。 |
| `_evaluate_stage()` | 接收 strategy；当关闭动态路由时固定 judge level，当关闭动态权重时 `next_weights=current_weights`。 |
| `_runtime_report()` | 增加 method / strategy / default reference score 的 metadata 输出，避免结果文件只能表示 DynSTEER 分数。 |

### 4.2 `dynsteer/evaluate/settlement.py`

| 函数 | 修改方案 |
|---|---|
| `_evaluate_stage()` | 抽出策略选择逻辑，避免 dynamic routing 与 dynamic weight 写死在函数体内。 |
| `should_stop_after_stage()` | 保留主 `evaluate()` 在线评估使用；replay 模式只返回 termination metadata，不触发外部 session stop。 |
| `finish_settlement()` | 支持 replay 在“虚拟早停后仍有完整轨迹”的情况下写明 finish 是否参与最终分数。 |

### 4.3 `dynsteer/harness/config.py`

| 函数 | 修改方案 |
|---|---|
| `load_harness_run_configs()` | 保留 benchmark harness 配置职责，不继续扩展成实验矩阵解析器。只补齐阈值、策略、judge profile 字段的结构化读取。 |
| 新增 `threshold_config_from_mapping(data)` | 从 JSON 显式生成 `ThresholdConfig`，用于阈值敏感性低/中/高三档。 |
| 新增 `evaluation_strategy_from_mapping(data)` | 生成 `EvaluationStrategyConfig`，用于主评估、消融、replay 和 guidance 分实验。 |
| `load_judge_config_from_env()` | 补充 `max_tokens`、`max_retries` 等字段，与 `LLMConfig` 对齐。 |

### 4.4 `dynsteer/harness/scheduler.py`

| 函数 | 修改方案 |
|---|---|
| `_run_case()` | 当前固定 `DynSTEEREvaluator.from_env()`，需改为根据 `HarnessRunConfig.metadata` 构造 evaluator。 |
| `run_case_tasks()` | 保持当前 harness CLI 的在线动态评估能力；主实验的 Default 与 Replay 不建议继续通过该函数硬塞方法逻辑。 |

### 4.5 `dynsteer/harness/outputs.py`

| 函数 | 修改方案 |
|---|---|
| `write_case_outputs()` | 保留名称，继续服务 `evaluate()` 主评估入口；新增 Default / Replay 输出函数时不要把当前主路径改名为 `_steering`。 |
| 新增 `write_default_case_outputs(...)` | 保存原生完整轨迹、原生 summary、原生 score。 |
| 新增 `write_replay_case_outputs(...)` | 保存同轨迹 replay 报告，目录中显式包含 method，避免覆盖 Default 产物。 |
| `_build_run_level_summary()` | 增加 `method`、`model_id`、`repeat_index`、`default_score`、`dynsteer_score`、`agent_cost_available` 字段。 |

### 4.6 `dynsteer/adapter/base.py`

| 类/函数 | 修改方案 |
|---|---|
| 新增 `BenchmarkDefaultResult` 数据类 | 字段包含 `score: float`、`resolved: bool | None`、`raw: JsonObject`、`metrics: JsonObject`。 |
| `BaseBenchmarkHarness` 新增 `default_result_from_session(session)` | benchmark 完整执行后提取原生 `Default` 结果；默认抛出 `NotImplementedError`，要求正式实验 benchmark 显式实现。 |
| `BaseBenchmarkHarness.metrics_from_session()` | 保留，但要求子类尽量区分 agent 执行耗时 / token 与评估耗时 / token。 |

### 4.7 `dynsteer/adapter/registry.py`

| 函数/字段 | 修改方案 |
|---|---|
| `_ADAPTERS` / `_HARNESSES` | 第一阶段先不强制注册未实现的 `swebench_pro`；若创建占位注册，必须让错误信息明确提示“等待 SWE-bench Pro 仓库到位后补实现”。 |
| `_normalize_benchmark()` | 保留小写归一；约定配置中统一使用 `swebench_pro`，避免文件名中使用连字符。 |

### 4.8 `dynsteer/llm/factory.py`

| 函数 | 修改方案 |
|---|---|
| 新增 `build_llm_from_config(config, env=None)` | 支持每个实验方法指定 Judge provider/model/base_url/temperature/max_tokens，同时 API key 仍从环境变量读取。 |
| `build_llm_from_env()` | 继续作为本地默认入口，但不再是实验代码的唯一配置来源。 |

### 4.9 `main.py` 与 `scripts/exp_main.sh`

| 文件 | 修改方案 |
|---|---|
| `main.py` | 保留单 benchmark harness 调试入口；新增 `--experiment-config` 时转入 `dynsteer.experiment.runner.run_experiment()`。 |
| `scripts/exp_main.sh` | 当前只是循环 benchmark；可保留为调试脚本。双 Benchmark 主实验建议新增 `scripts/exp_double_benchmark.sh` 或直接用 `main.py --experiment-config`。 |

## 5. 必须新增的功能函数

### 5.1 实验模型：`dynsteer/experiment/model.py`

| 新增对象 | 职责 |
|---|---|
| `ExperimentMethod` | 枚举 `default`、`dynsteer_evaluate`、`dynsteer_replay`、`dynsteer_replay_static`、`dynsteer_guidance`。论文实验标签仍可映射到 `DynSTEER-Steering`，但代码命名不把主 `evaluate()` 降格为 `_steering`。 |
| `ExperimentRunSpec` | 单个实验运行规格：benchmark、case_ids、model_id、repeat_index、method、judge_profile、threshold_profile、strategy。 |
| `ExperimentCaseResult` | 单 case 结果：default score、dynsteer score、resolved、耗时、step、tokens、输出路径。 |
| `ExperimentAggregate` | 模型级和 benchmark 级汇总结果。 |
| `EvaluationStrategyConfig` | 是否启用动态路由、动态权重、policy stop、guidance 注入。 |

### 5.2 实验配置：`dynsteer/experiment/config.py`

| 新增函数 | 职责 |
|---|---|
| `load_experiment_config(path)` | 读取统一实验 JSON。 |
| `expand_experiment_matrix(config)` | 展开 benchmark × model × method × repeat × threshold profile。 |
| `build_harness_config(spec)` | 把实验 run spec 转换为当前 harness 可用的 `HarnessRunConfig`。 |
| `validate_experiment_matrix(specs)` | 检查 run_id 唯一、case 范围、方法组合合法。 |

建议新增配置文件示例：

```text
data/experiments/double_benchmark_initial.json
```

### 5.3 实验运行：`dynsteer/experiment/runner.py`

| 新增函数 | 职责 |
|---|---|
| `run_experiment(config_path, workers=1)` | 主入口，执行完整实验矩阵并写出汇总。 |
| `run_default_case(spec, task_case)` | 完整执行 benchmark，不进行 DynSTEER 介入，保存原生分数与轨迹。 |
| `run_replay_case(spec, default_output)` | 读取同一条 Default 轨迹，执行 `DynSTEER-Replay`。 |
| `run_evaluate_case(spec, task_case)` | 调用 `DynSTEEREvaluator.evaluate()` 主入口，允许动态监测、阶段评估与提前终止。 |
| `run_guidance_case(spec, task_case)` | 用于后续分实验，在主 `evaluate()` 流程基础上额外启用 guidance 注入。 |
| `write_experiment_index(results)` | 写出所有 case 的结构化索引，供后续汇总和审计。 |

主实验只需要 `run_default_case()` 与 `run_replay_case()`；`run_evaluate_case()` 保持当前主评估流程，`run_guidance_case()` 可在分实验阶段实现。

### 5.4 指标汇总：`dynsteer/experiment/metrics.py`

| 新增函数 | 职责 |
|---|---|
| `case_score(value)` | 将不同 benchmark 原生结果归一到 `[0, 1]`。 |
| `model_scores(results)` | 计算 `S_{m,e,b}`，默认对 case 与 repeat 取平均。 |
| `psep(scores)` | 计算模型对平均得分间距。 |
| `kendall_tau(left, right)` | 不引入新依赖，手写 Kendall tau-a / tau-b；优先处理并列分数。 |
| `rank_tau(method_scores, default_scores)` | 以 Default 模型排序为参照计算 `RankTau`。 |
| `aggregate_efficiency(results)` | 汇总评估耗时、Agent 步骤数。 |
| `aggregate_cost(results)` | 汇总 Agent token、Judge token，并保留可用性标记。 |
| `write_metric_tables(aggregates, output_dir)` | 写出 `scores.json`、`metrics.json`、可选 CSV。 |

### 5.5 Milestone 可靠性：`dynsteer/experiment/reliability.py`

| 新增函数 | 职责 |
|---|---|
| `milestone_f1(predicted, reference)` | 计算 ToolSandbox milestone 匹配 F1。 |
| `toolsandbox_reference_milestones(case)` | 从 ToolSandbox 原生 matcher 或 adapter 产物构造参考标签。 |
| `toolsandbox_predicted_milestones(report)` | 从 DynSTEER replay / evaluate 报告抽取预测命中。 |
| `write_swebench_manual_review_sample(results, output_path)` | 抽样导出 SWE-bench Pro pseudo-stage 人工审阅材料。 |
| `summarize_manual_review_labels(label_path)` | 汇总人工审阅结果。 |

## 6. SWE-bench Pro 新增模块

本阶段只新增继承基础 Benchmark adapter / harness 的封装类框架，不实现真实数据加载、仓库 checkout、Agent 运行、patch 验证和 pseudo-stage 转换。具体实现等待用户将 SWE-bench Pro 仓库下载到项目同级目录后再继续。

新增目录：

```text
dynsteer/adapter/swebench/
  __init__.py
  adapter.py
  harness.py
  trace.py
  stage.py
  scorer.py
```

| 文件 | 核心函数/类 | 职责 |
|---|---|---|
| `adapter.py` | `SwebenchProAdapter(BaseBenchmarkAdapter)` | 先提供继承框架、`benchmark="swebench_pro"` 和清晰的 `NotImplementedError`；真实 `TaskCase` 转换后续补齐。 |
| `stage.py` | `pseudo_stage_graph_from_instance()` | 先保留函数签名与文档注释，暂不实现 pseudo-stage 生成。 |
| `trace.py` | `trajectory_from_swebench_run()` | 先保留函数签名与文档注释，暂不实现轨迹转换。 |
| `harness.py` | `SwebenchProHarness(BaseBenchmarkHarness)` | 先实现继承框架和基础接口占位，所有需要真实仓库的接口显式抛出未实现错误。 |
| `scorer.py` | `SwebenchProConstraintScorer(BaseBenchmarkConstraintScorer)` | 先提供类框架，真实 patch/test/artifact 评分后续补齐。 |

真实实现阶段再新增或启用：

```text
data/swebench_pro/benchmark.json
data/swebench_pro/run_configs.json
```

`benchmark.json` 至少包含 source_root、dataset_path、repo_cache_dir、environment_backend、max_workers。所有路径只解析到本地目录，不在代码中写死外部账户、token 或私有仓库信息。第一阶段若创建示例配置，也应放在 docs 或 examples 中，不作为默认可运行配置。

## 7. 实验输出目录建议

为避免覆盖当前 `results/<benchmark>/<run_id>`，建议实验层输出到：

```text
results/experiments/<experiment_id>/
  index.json
  scores.json
  metrics.json
  reliability.json
  cases/
    <benchmark>/<method>/<model_id>/<repeat>/<case_id>/
      default_summary.json
      replay_report.json
      trajectory.json
```

其中 `index.json` 是唯一汇总入口，记录每个产物路径、benchmark version、case set、model version、prompt、temperature、seed、Judge profile 和 threshold profile。

## 8. 实施顺序

### 阶段一：主实验最小闭环

1. 新增 `dynsteer/experiment/model.py`、`config.py`、`metrics.py`。
2. 在 `BaseBenchmarkHarness` 中新增默认结果接口。
3. 为 ToolSandbox 实现原生 Default 分数提取。
4. 保留 `DynSTEEREvaluator.evaluate()` 主入口，并新增 `evaluate_replay()`。
5. 新增 Default + Replay 输出函数。
6. 写出 `PSEP`、`RankTau`、耗时、Judge token 汇总。
7. 补充 `docs/apis/experiment.md` 与单元测试。

### 阶段二：SWE-bench Pro 接入框架

1. 新增 `dynsteer/adapter/swebench`。
2. 编写 `SwebenchProAdapter`、`SwebenchProHarness`、`SwebenchProConstraintScorer` 的继承框架。
3. 为 `stage.py`、`trace.py` 保留函数签名、中文注释和未实现错误。
4. 暂不实现 dataset/case loader、repo checkout、Agent trace converter、resolved rate 提取和 pseudo-stage graph。
5. 暂不把 `data/swebench_pro` 作为默认可运行配置；等外部仓库到位后再启用注册和数据配置。

### 阶段三：分实验

1. 核心消融：添加 `EvaluationStrategyConfig(dynamic_routing=False, dynamic_weighting=False)`。
2. 小模型 Judge：通过 experiment judge profile 指定 4B / 7B Judge。
3. 阈值敏感性：新增低/中/高 threshold profile。
4. Milestone 可靠性：ToolSandbox 计算 F1，SWE-bench Pro 导出人工审阅样本。
5. 动态 guidance 分实验：新增 guidance 生成与 harness 注入 hook。

## 9. 测试计划

| 测试文件 | 覆盖重点 |
|---|---|
| `tests/experiment/test_metrics.py` | `psep()`、`kendall_tau()`、并列分数、空模型集合异常。 |
| `tests/experiment/test_config.py` | 实验矩阵展开、run_id 唯一性、threshold profile、judge profile。 |
| `tests/evaluate/test_replay.py` | 同一条固定轨迹 replay 得到稳定 stage reports，不调用 `stop_case()`。 |
| `tests/evaluate/test_strategy.py` | 关闭动态路由与动态权重后，judge level 与 weights 不再变化。 |
| `tests/harness/test_default_result.py` | fake harness 的 default result 被正确写入 summary。 |
| `tests/adapter/test_swebench_scaffold.py` | SWE-bench Pro adapter / harness 占位类继承正确，未实现接口抛出清晰错误。 |
| `tests/experiment/test_reliability.py` | milestone F1 的 TP/FP/FN 计算。 |

单元测试不应为了测试新增只转发接口；测试应调用正式模块公开接口。

## 10. API 文档补充

需要新增或更新：

1. `docs/apis/experiment.md`：实验配置、输出目录、指标定义。
2. `docs/apis/harness.md`：补充 `default_result_from_session()` 契约。
3. `docs/apis/evaluate.md`：说明 `evaluate()` 是主动态监测式评估入口，并补充 `evaluate_replay()`。
4. `docs/apis/adapters.md` 或 `docs/apis/swebench.md`：说明 SWE-bench Pro adapter/harness 契约。

## 11. 优先级判断

| 优先级 | 内容 | 原因 |
|---|---|---|
| P0 | ToolSandbox Default 原生分数、Replay 入口、实验指标汇总 | 主实验无法绕开。 |
| P0 | Judge / threshold / strategy 从配置构造 | 小模型 Judge、消融、敏感性分析都依赖。 |
| P1 | SWE-bench Pro adapter/harness 封装类框架 | 先把继承关系和接口边界落好，具体实现等待外部仓库。 |
| P1 | Agent token 成本采集 | 成本指标要求；ToolSandbox 当前缺口明显。 |
| P2 | 动态 guidance 注入 | 属于分实验，且不同 benchmark hook 差异较大。 |
| P2 | SWE-bench Pro 人工审阅导出 | 可靠性补充实验，不阻塞主实验分数。 |

## 附录A. 项目中没有把握实现的模块部分

1. **SWE-bench Pro 原生运行接口**
   - 当前仓库没有 SWE-bench Pro 源码、dataset schema、runner CLI 或 trace 格式样例。
   - 没有把握的点是 repo checkout、测试执行、patch 验证和 resolved rate 的官方接口细节。
   - 处理方式：先设计 `SwebenchProHarness` 的最小契约，用本地 fixture 测试；拿到实际 benchmark 源码后再补适配细节。

2. **ToolSandbox 原生 `Default` 分数提取**
   - 当前 `ToolSandboxHarness.raw_summary_from_session()` 明确跳过原生 evaluation。
   - 没有把握的点是如何在不重新运行 scenario 的前提下，基于当前 session context 调用原生 final state matcher。
   - 处理方式：优先查 ToolSandbox 原生 evaluation API；若无法直接复用，则在 harness 中用已适配的 final-state scorer 构造与原生 matcher 等价的默认分数，并在 summary 标注 score source。

3. **Agent token 成本采集**
   - 当前 ToolSandbox 轨迹 step cost 全为空，`trajectory_cost_available=false`。
   - 没有把握的点是不同 ToolSandbox role / 外部 agent 是否暴露 provider usage。
   - 处理方式：只采集真实 usage，不估算；不可用时在聚合中标注 unavailable。

4. **动态介入 guidance 注入**
   - 当前 evaluator 只能提前终止，不能修改后续 Agent prompt。
   - 没有把握的点是 ToolSandbox 和 SWE-bench Pro 的 Agent 执行循环是否都允许中途注入指导。
   - 处理方式：先新增 `inject_guidance()` 可选 hook，默认不支持；每个 benchmark 单独实现，不把 guidance 写成核心 evaluator 的硬依赖。

5. **SWE-bench Pro pseudo-stage 人工可靠性评价**
   - 该实验需要人工审阅标准和抽样协议，代码只能完成样本导出与标签汇总。
   - 没有把握的点是人工标签的一致性与规模是否足以支撑论文结论。
   - 处理方式：代码只提供审阅材料、label schema 和汇总函数，不在程序中伪造人工结论。
