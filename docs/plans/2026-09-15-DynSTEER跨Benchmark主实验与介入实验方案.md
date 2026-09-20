# DynSTEER 跨 Benchmark 主实验、消融实验与动态介入实验方案

## 1. 方案定位

本方案只描述实验设计、指标口径和实验验收；代码落地方案单独维护在 [2026-09-15-DynSTEER实验代码修改细化方案.md](2026-09-15-DynSTEER实验代码修改细化方案.md)。实验目标是回答三个问题：

1. 在 ToolSandbox、SWE-bench Pro、SkillsBench 的自带 agent 执行框架上，DynSTEER replay 的评估分数是否比 benchmark 原生终态评估具有更强的模型区分度。
2. 在与 Default 相同的 agent 轨迹上进行 replay 时，动态阶段式评估能在多大比例的任务进度处发现应当停止的执行前缀，并消耗多少时间与 token。
3. 对支持在线中断的 ToolSandbox，把 replay 中的停止信号转换为执行过程中的引导消息后，能否提高原生任务完成占比。

`stage-only` 不再单独定义为消融外概念：它进入主实验，作为“关闭 judge 动态路由与动态权重、但保留阶段式 stop”的对照；同时在消融表中重复展示，便于与模块消融放在同一张表读取。

## 2. 统一实验协议

### 2.1 固定设置

| 项目 | 设置 |
| --- | --- |
| Agent 基座 | `deepseek-v4-pro`、`deepseek-v4-flash`、`qwen-plus-2025-12-01`、`qwen3-max-2026-01-23` |
| 重复次数 | 主实验、消融实验和介入实验均为 `repeats=3` |
| 随机种子 | `202608`，用于 case 分层抽样和 paired bootstrap |
| DynSTEER Judge | 三个实验使用同一个固定 judge profile；provider、base URL、API key 只通过环境变量注入，配置内只允许 `api_key_env`、`base_url_env` |
| 配对键 | `(benchmark, model_id, repeat_index, case_id)`；跨实验汇总时再追加 `experiment_group` |
| 结果单位 | 每 case 输出原生分数、DynSTEER 分数、native score、agent step 数、时间、token、终止原因；缺失值写 `null` 与 `available=false`，禁止用 0 冒充真实消耗 |

### 2.2 数据规模

| Benchmark | 执行框架 | 主实验规模 | 分层规则 |
| --- | --- | ---: | --- |
| ToolSandbox | ToolSandbox 自带 agent/user/environment role，逐步可中断 | 全量 509 | 不抽样 |
| SWE-bench Pro | AgentCompass 黑盒整任务执行 | 100 | 按 `repo` 分层，随机抽样 |
| SkillsBench | AgentCompass 黑盒整任务执行 | 100 | 按 `category` 分层，随机抽样 |

SWE-bench Pro 与 SkillsBench 当前由 `BaseAgentCompassHarness.advance_case()` 一次性执行完整任务，返回完整 ACTF 轨迹，因此只进入 replay 主实验与消融实验，不进入动态介入实验。每个实验配置显式写入 `case_ids`，抽样结果固化为 JSON，禁止运行时隐式重新抽样。若某个 benchmark 可用任务不足 100，则记录全量数量和缺失原因，不用重复任务补齐。

### 2.3 主实验方法

| 方法 | 含义 | 评估分数 | 保留模块 |
| --- | --- | --- | --- |
| `default` | benchmark 自带 agent 框架完整执行，再用 benchmark 原生终态验证器评分 | native score | 无 DynSTEER judge |
| `dynsteer_replay` | 复用同 identity 的 Default 完整轨迹，离线逐 raw step 阶段评估 | DynSTEER overall score | milestone graph、minefield、stage stop、frontier stop、dynamic routing、dynamic weighting |
| `dynsteer_replay_static` | `stage-only`：同一完整轨迹上关闭 judge 动态路由与动态权重，保留固定 judge 和固定权重 | DynSTEER overall score | milestone graph、minefield、stage stop、frontier stop |

主实验不把 `dynsteer_evaluate` 作为跨 benchmark 必选臂，因为 SWE-bench Pro 与 SkillsBench 的 harness 不支持逐 agent step 在线中断。ToolSandbox 在线 stop/guided 单独由介入实验承担。

### 2.4 执行流程

1. 对每个 `(benchmark, model_id, repeat_index)` 先执行 `default`，保存完整 trajectory、execution timing、原生 score、原生评估时间和原生评估 token 可用性。
2. 对每个非 Default 方法读取同 identity Default trajectory，调用 `DynSTEEREvaluator.evaluate_replay()`。
3. Replay 在虚拟停止点后不再继续评估，不修改真实环境，也不重放 agent LLM 调用。
4. 所有方法共享同一份静态适配产物。`no_milestone_graph` 单独构成一个静态适配组，避免空图模板污染其他消融臂。
5. 每个实验配置只使用一个 `results_dir`；`index.json`、`scores.json`、`metrics.json`、`costs.json` 由现有 `run_experiment()` 汇总。

## 3. 主实验指标

### 3.1 分数区分度

沿用 `docs/paper/iclr2027/sections/s4-experiments.tex` 中的定义。对同一 benchmark、method 和模型总体：

```text
DS(epsilon) = (sigma_pop / mu) * sqrt(N_sig(epsilon) / C(M, 2))
N_sig(epsilon) = sum(I(|mean_score_i - mean_score_j| > epsilon))
```

主阈值 `epsilon=0.01`，同时输出现有 `DISCRIMINABILITY_THRESHOLDS` 的 `0.01, 0.02, 0.03, 0.04, 0.05`。`sigma_pop` 使用模型均分的总体标准差；分数先用现有 `case_score()` 归一到 `[0, 1]`。

DS 只描述按 margin 分离的模型对数量，不把 Wilcoxon 结果混入 DS 公式。额外输出 statistical audit：

1. 对每个模型对，按 `(repeat_index, case_id)` 严格配对两模型分数。
2. 只有两侧 case 分数均存在时进入配对样本。
3. 使用 paired Wilcoxon signed-rank test，显著性阈值为 `p < 0.01`。
4. 输出 `paired_case_count`、完整配对缺失数、统计量、p 值、`significant_at_0.01` 和方向性正负差计数。

同时保留现有 Kendall tau-b、repeat ranking consistency、score delta 和 saturation 分布，用于解释 DS 之外的排序稳定性。

### 3.2 节省步骤进度

对每个发生 replay stop 的运行：

```text
a_i = stop 时已闭合 agent step 数
b_i = 同 identity Default 完整轨迹已闭合 agent step 数
progress_i = a_i / b_i
saved_progress_i = 1 - a_i / b_i
```

`a_i` 与 `b_i` 读取 `runtime_metrics.step_count`，不使用 raw step 数作主指标；raw step 数只作为诊断。只有 `b_i > 0` 且 `0 <= a_i <= b_i` 时有效。

报告必须区分两个分母：

1. replay stop rate：`stopped_count / all_paired_replay_count`。
2. saved progress：只对发生 stop 的运行计算分布，同时在表头标明 stopped case 数。

stop 分类继续使用现有 `_stop_detail()` 的 `minefield`、`agent_underperformance`、`other`。对未 stop 的 replay，`progress=1.0`、`saved_progress=0.0`，但不能进入 stopped-run saved progress 分布。

### 3.3 时间消耗

| 消耗类型 | 来源 |
| --- | --- |
| Default agent 执行时间 | Default trajectory 的 batch execution timing 汇总，即 `runtime_metrics.execution_total_seconds` |
| Default native 评估时间 | `runtime_metrics.native_evaluation_seconds` |
| Replay agent 前缀时间 | `build_replay_timing_metrics()` 输出的 `default_prefix_execution_seconds` |
| DynSTEER replay 评估时间 | evaluator 墙钟 `elapsed_seconds` |
| Adaptation 时间 | `adaptation_cost.elapsed_seconds`，按 artifact 消费者数量分摊 |
| Effective replay pipeline time | `default_prefix_execution_seconds + replay evaluator elapsed_seconds` |

时间按各系统真实时钟分别报告，不把 ToolSandbox agent 批次时间、AgentCompass 黑盒运行时间、native verifier 时间和 DynSTEER judge 时间合并成单一“净加速”。只有 `timing_available=true` 的配对进入时间分布。

### 3.4 token 消耗

token ledger 分四列：

| 列 | Default | Replay / 在线 DynSTEER |
| --- | --- | --- |
| Agent tokens | 完整 trajectory 中 agent 轨迹 token | Replay 使用 Default 前缀 token；在线 arm 使用实际执行 token |
| Native evaluation tokens | benchmark verifier 返回值 | 无，写 `null` |
| DynSTEER evaluation tokens | 无，写 `null` | RuntimeMetricsRecorder 汇总的 judge `llm_total_tokens` |
| Adaptation tokens | 0 | milestone/stage 适配产物 token，按 artifact 消费者分摊 |

规则：

1. 缺失 token 写 `null`，并输出 `available_count` 与缺失原因；不得用 0 参与均值。
2. Default 的原生评估 token 只有 benchmark 明确返回 prompt/completion/total 时才有效。
3. ToolSandbox 的用户模拟器 token 不计入 agent tokens；如果能捕获，写入独立 `user_simulator_tokens`。
4. AgentCompass ACTF 的一个 native step 只允许一次 LLM token 归属；转换出的非代表 raw step 写 0，而不是 `null`。
5. DynSTEER evaluation tokens 只统计 replay/guided judge 调用；静态适配成本单独进入 adaptation ledger。

### 3.5 原生任务完成占比

主实验仍以分数区分度为主，但同时输出 task completion 作为审计指标：

| Benchmark | native completion |
| --- | --- |
| ToolSandbox | `native_score >= 1.0` |
| SWE-bench Pro | `native_score == 1.0`，即 resolved |
| SkillsBench | 直接使用原生 partial reward；另补充 `native_score >= 1.0` 的满分占比 |

`task_completion` 必须使用 native score，不使用 DynSTEER overall score，避免评估器惩罚影响完成率解释。

## 4. 消融实验设计

### 4.1 数据规模

| Benchmark | 规模 | 分层 |
| --- | ---: | --- |
| ToolSandbox | 100 | 按 task type / safety category 分层 |
| SWE-bench Pro | 50 | 按 repo 分层 |
| SkillsBench | 50 | 按 category 分层 |

消融使用独立 experiment id，避免与主实验同名结果混写。每个 benchmark 内包含一个 `default` 臂用于同 identity 配对；非 Default 臂共享该 Default 轨迹。

### 4.2 消融臂

| 组名 | 实验方法 | 相对 full 的变化 | 目的 |
| --- | --- | --- | --- |
| `default` | `default` | 原生评估基线 | 提供 paired native score 与完整轨迹 |
| `full` | `dynsteer_replay` | 无 | 完整模块 |
| `stage_only` | `dynsteer_replay_static` | `dynamic_routing=false, dynamic_weighting=false` | 评估只保留阶段式 stop 的效果 |
| `no_dynamic_routing` | `dynsteer_replay_static_routing` | `dynamic_routing=false`，保留动态权重 | 分离 judge 升级路由贡献 |
| `no_dynamic_weighting` | `dynsteer_replay_static_weighting` | `dynamic_weighting=false`，保留动态路由 | 分离动态权重贡献 |
| `no_minefields` | `dynsteer_replay_no_minefields` | `use_minefields=false` | 分离安全 minefield 即时检查与惩罚贡献 |
| `no_milestone_graph` | `dynsteer_replay_no_milestone_graph` | `use_milestone_graph=false` | 分离 milestone 结构与 whole-trajectory fallback 贡献 |
| `no_policy_stop` | `dynsteer_replay_no_policy_stop` | `policy_stop=false` | 全局关闭停止，只看过程评分对区分度的贡献 |

`stage_only` 在主实验已经运行；消融表中引用同 case、同 repeat 的主实验结果，不在同一 experiment id 内重复执行。其余消融臂独立运行。

`no_policy_stop` 的 saved progress 定义为不可用，因为停止机制本身被移除；该组只比较 DS、rank tau、DynSTEER score 与 Default 的相关性。其余组同时输出 DS 和 stopped-run saved progress。

### 4.3 消融分析

1. 每个消融臂与 `full` 在 `(benchmark, model_id, repeat_index, case_id)` 上严格配对。
2. 分数差异使用 paired Wilcoxon signed-rank test，`p < 0.01`。
3. saved progress 差异使用 paired bootstrap，`B=10000`，seed `202608`，报告均值差和 95% percentile CI。
4. 每个 benchmark 分别分析；只有在三个 benchmark 方向一致时才声明模块贡献稳定。
5. `no_milestone_graph` 单独报告 empty-graph fallback 的覆盖率与异常率，不把编译失败误报为模块无效。

### 4.4 消融口径澄清

`no_milestone_graph` 不预期再做中间阶段划分。适配时直接使用空 milestone graph：正向 milestone nodes 和 edges 置空，图内表达的 minefields 也置空；阶段机制退化为一个终局 `START->__finish__` whole-trajectory stage。该 stage 在轨迹结束后由 StandardJudge 根据任务描述、完整轨迹、工具结果、最终状态和可用约束判断完成度。空 frontier 下 ready-frontier 无进展 stop 自然不会触发；该臂的 saved progress 主要来自 whole-trajectory judge 不产生在线 stop，除非后续出现实现层错误，而这些异常单独记录为 implementation failure。

该设计不把 minefields 与 milestone graph 强行拆成同一个消融点：`no_minefields` 在保留 milestone graph 的前提下关闭 minefield 即时检查和惩罚，用于单独评估安全模块；`no_milestone_graph` 则评估整个图结构模块被替换为 whole-trajectory fallback 的影响。ready-frontier stop 保持默认启用，不再单独消融。

## 5. 动态介入实验

### 5.1 范围与分组

动态介入只在 ToolSandbox 上做，因为当前只有 `ToolSandboxHarness.advance_case()` 能逐批次中断执行。从 509 个场景中按 task type 与 safety category 分层抽取 200 个 case，`repeats=3`。

| 臂 | 方法 | 行为 |
| --- | --- | --- |
| `default` | `default` | ToolSandbox 自带框架完整执行，不评估、不介入 |
| `stop` | `dynsteer_evaluate` | 在线阶段评估；非 fatal stop 直接终止，fatal minefield 终止 |
| `guided` | `dynsteer_evaluate_guided` | 第一个非 fatal stop 不终止，而是向 ToolSandbox agent 注入引导；fatal minefield 仍终止 |

Guided 规则：

1. 每个 case 最多 2 次引导，由 `strategy.max_interventions=2` 控制。
2. 同一 stage 只引导一次；第二次命中同一 stage 时按 stop 处理。
3. fatal minefield 是硬停止，不转成引导。
4. 引导后继续运行到自然结束、达到 ToolSandbox 上限或后续 fatal stop。
5. 每次引导记录触发 step、stage id、termination code、公开诊断摘要、消息 digest、后续是否 stage PASS 和最终 native completion。

### 5.2 引导信息边界

引导内容只能使用 agent 可见信息：

1. 公开任务描述与已物化的 stage goal 文本。
2. 当前 stage 的 PASS/FAIL/MISSING/INVALID 状态和分数。
3. ready frontier 的 milestone id / 名称。
4. 已记录 match attempt 的结构化诊断摘要。
5. 轨迹中已经出现的公开工具名、参数摘要和失败证据。

禁止把 expected state、gold patch、隐藏 verifier 结果、完整原生 milestone mapping 或未来步骤直接写入消息。消息固定使用前缀 `[DynSTEER intervention]`，并说明这是过程诊断提示，不是新的任务目标。

`build_intervention_message()` 使用字段白名单构造消息，不把 `task_case.milestone_graph` 中仅用于评估的 expected 约束原文无差别拼接进 prompt。

### 5.3 指标与检验

主指标：

```text
completion_rate = count(native ToolSandbox similarity >= 1.0) / valid_case_count
```

比较：

1. `guided - default`：判断引导是否能提升原生完成率。
2. `guided - stop`：判断把停止转换为引导是否优于直接停止。
3. `stop - default`：确认纯在线 stop 的代价或收益。

显著性：

1. 完成占比差异使用 exact McNemar test（discordant pairs 的 exact binomial test），显著性阈值 `p < 0.05`。
2. step、time、token 差异使用 paired Wilcoxon signed-rank test，`p < 0.05`。
3. 主结论同时给出 paired bootstrap 95% CI，`B=10000`，seed `202608`。
4. 分层敏感性分析按 task type 和 safety category 重复方向性检查。

辅助指标包括：

1. guidance fired rate、per-case guidance 数、同 stage 重复触发率。
2. guidance 后当前 stage 转 PASS 的比例。
3. guidance 后到 natural end / stop 的额外 agent step 数。
4. guided arm 相对 default 的额外 wall time 与 agent token。
5. fatal minefield 拦截率和信息泄漏审计通过率。

## 6. 实验配置与安全

新增三份配置：

```text
configs/experiments/2026-09-15-main.json
configs/experiments/2026-09-15-ablation.json
configs/experiments/2026-09-15-intervention.json
```

配置骨架保持现有统一实验格式：

```json
{
  "experiment_id": "dynsteer-2026-09-15-main",
  "benchmarks": [
    {"benchmark": "toolsandbox", "data_root": "data/toolsandbox", "case_ids": []},
    {"benchmark": "swebench_pro", "data_root": "data/swebench_pro", "case_ids": []},
    {"benchmark": "skillsbench", "data_root": "data/skillsbench", "case_ids": []}
  ],
  "models": [
    {"model_id": "deepseek-v4-pro"},
    {"model_id": "deepseek-v4-flash"},
    {"model_id": "qwen-plus-2025-12-01"},
    {"model_id": "qwen3-max-2026-01-23"}
  ],
  "methods": [
    {"method": "default"},
    {"method": "dynsteer_replay", "judge_profile": "fixed-judge"},
    {"method": "dynsteer_replay_static", "judge_profile": "fixed-judge"}
  ],
  "repeats": 3
}
```

消融配置的 `methods` 展开为 7 个运行臂：`default`、`full`、`no_dynamic_routing`、`no_dynamic_weighting`、`no_minefields`、`no_milestone_graph` 和 `no_policy_stop`。`stage_only` 不在消融配置中重复执行；聚合报告按配对键从主实验引用。介入配置只包含 `toolsandbox`、三个在线/default 臂、`repeats=3`。三个执行臂都设置 `capture_agent_usage=true` 以统计 agent 执行 token；`stop` 和 `guided` 另外设置 `collect_online_native_score=true`。guided 方法保持：

```json
{
  "method": "dynsteer_evaluate_guided",
  "strategy": {
    "policy_stop": true,
    "max_interventions": 2
  }
}
```

运行入口继续使用：

```bash
./scripts/start_experiment_no_docker.sh --exp configs/experiments/2026-09-15-main.json
```

安全要求：

1. 先轮换当前仓库历史数据文件中已经暴露的 API key。
2. 清理 `data/toolsandbox/run_configs.json` 与 `docs/plans/toolsandbox_rapid_api_key_scenarios.json` 中的明文 key，只保留 `api_key_env` 和 `base_url_env`。
3. 不在实验配置、结果 JSON 和日志中输出 key、完整原始请求或隐藏 expected。
4. AgentCompass 配置继续只允许 `MODEL_API_KEY`、`MODEL_BASE_URL` 等环境变量。

## 7. 代码修改方案与实验验收衔接

代码修改、接口细化、测试方案和不确定模块见独立文档：[2026-09-15-DynSTEER实验代码修改细化方案.md](2026-09-15-DynSTEER实验代码修改细化方案.md)。

实验侧验收只检查以下五项：

1. 主实验三份结果分别包含 `default`、`dynsteer_replay`、`dynsteer_replay_static`，且配对键完整。
2. 消融配置包含 `default`、`full`、`no_dynamic_routing`、`no_dynamic_weighting`、`no_minefields`、`no_milestone_graph`、`no_policy_stop` 七个运行臂；`stage_only` 从主实验按配对键引用。
3. 介入实验包含 `default`、`stop`、`guided` 三臂，guided case 可追溯每次 intervention 和最终 native completion。
4. `metrics.json` 输出 DS、statistical audit、saved progress、task completion、paired group audit 和介入检验所需数据。
5. 缺失的时间、token 或 native score 保持 null 并附带可用性计数，不以 0 参与均值。

## 附录A. 项目中没有把握实现的模块部分

1. ToolSandbox 在线消息注入兼容性：`ToolSandboxHarness` 目前只读取 SANDBOX rows 和调用 role `respond()`，本地没有安装 ToolSandbox source，无法直接确认上游 execution context 的公开 SANDBOX row 写入 API、字段约束和 `SYSTEM -> AGENT` 消息对 agent prompt 的实际影响。方案因此把注入封装在 `send_guidance()` / `_append_guidance_message()`，但具体字段名和写入函数需要在实现时以固定 ToolSandbox commit 为准验证。

2. ToolSandbox agent token 捕获覆盖率：ToolSandbox agent/user role 使用各自 provider client，当前轨迹没有 step cost。通过 HTTP response usage hook 可以捕获多数非流式 Chat Completions/Anthropic usage，但上游 role 若使用 streaming、缓存响应或自定义 transport，可能仍然拿不到完整 usage。方案要求这类 case 输出 `available=false`，不能用 0 填充，因此 agent token 分析可能在 ToolSandbox 上只能报告覆盖率而非全量精确值。

3. AgentCompass ACTF 成本归一：ACTF 不同 benchmark/harness 的 metric 字段覆盖情况可能不一致。方案明确“native step 只归属一次、派生 step 为 0、真实缺失为 null”，但仍需要用真实 SWE-bench Pro 与 SkillsBench detail 抽样验证 prompt/completion、LLM latency 和 environment latency 的覆盖率。若上游日志缺字段，只能报告成本缺失，不能反推。

4. 空 milestone graph 的跨 benchmark 稳定性：`no_milestone_graph` 依赖现有 empty graph finish verification 和 whole-trajectory fallback。ToolSandbox 已有相关路径，但 SWE-bench Pro 与 SkillsBench 的 stage goal 物化和 finish 评分在空图下的边界仍需 pilot 验证。如果空图导致大量 score 缺失，应把该臂标记为 implementation failure，而不是解释为 milestone graph 无贡献。

5. 动态介入的因果解释边界：guided 消息会改变后续 agent 轨迹，任务提升不能全部归因于“更早发现错误”，也可能来自额外提示长度、额外探索机会或 ToolSandbox user simulator 的反应变化。方案用 default/stop/guided 三臂和配对检验控制主要差异，但仍需在论文中把结论限定为“在该公开诊断提示策略下”，不声明通用 prompt engineering 的因果普适性。
