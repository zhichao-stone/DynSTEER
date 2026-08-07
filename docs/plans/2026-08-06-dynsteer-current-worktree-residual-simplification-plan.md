# DynSTEER 当前工作树代码精简最终单轮执行方案

> 状态：最终版实施方案；本轮仅整理方案，不修改生产代码。  
> 审计对象：2026-08-06 最新 `HEAD=ba21f04`（包含 `5f16265 simplify code` 与 `ba21f04 add statistic for time and token in each stage`）。审计期间这两个提交先后进入当前分支，本文基线已按最终 HEAD 重新锁定。  
> 约束依据：`docs/constraints/code.md`。  
> 与已有方案的关系：`2026-08-06-dynsteer-code-simplification-audit-plan.md` 中的 Boundary 删除、Trajectory 索引、AgentStepTracker、MilestoneTopology、route、minefield fingerprint、session 收集等事项已在当前工作树部分或大部分落地；本文只规划当前代码仍然存在的冗余和落地残留，不重复计算已经完成的删减。

## 0. 最终裁决与执行边界

本节是后续实施的“单一解释”。若正文旧段落与本节冲突，以本节和第 5 节的文件级清单为准。第 5 节按主题分组，仅用于防遗漏，不代表多个实施轮次；所有生产代码必须在同一轮中完成。

### 0.1 本轮确定执行的变更

以下项目均视为已批准的代码精简，不再在实施时重新讨论：

1. 先修复 topology 未定义、minefield fingerprint 所在对象错误、缓存 TaskCase 未重建派生索引、snapshot 替换破坏排序等基线缺陷。
2. 删除 Judge 响应缓存及其 telemetry，恢复每个 `passes` 配置对应的真实独立 LLM 调用；这是有意恢复算法语义的变更，不要求分数与错误缓存行为一致。
3. 删除无消费者的类型、字段、导出、事件分支、序列化分支和纯转发函数；保留落盘 JSON 中仍有外部契约意义的键。
4. 将通用 milestone 评分算法收敛为一份基类流程，ToolSandbox 只保留聚合差异 hook。
5. 将外部输入校验、Judge payload 解析、早停判断、topology/context 派生和成本/timing 索引分别收敛到唯一边界或唯一入口。
6. 保留必要的日志、中文 docstring、关键流程注释、可选依赖边界、AgentStepTracker、DAG frontier、minefield source fingerprint 和真实多轮置信度算法。

### 0.2 明确不在本轮暗改的内容

blocked milestone 诊断、pending stage 的计分分母、fatal minefield 是否叠加最终 penalty、live/replay 终止后是否执行 finish、Judge status 多数票还是最严重值，均属于业务/算法决策。本轮只锁定现有行为，不因“看起来重复”而删除或改分；这些内容不计入本轮精简范围，也不得被误写成需要二次精简的任务。

### 0.3 文件级修改总表（执行索引）

| 编号 | 必改文件/符号 | 现状 | 最终形态 | 依赖与验收 |
| --- | --- | --- | --- | --- |
| P0-01 | `evaluate/diagnostics.py::build_milestone_matching_detail`、`_pending_failure_diagnostics`、`evaluate/step.py::evaluate_agent_step` | topology 参数断裂；有未使用局部变量 | 前者显式接收 `MilestoneTopology` 并只使用该索引；后者删除无用参数；删除 `step = closure.end_step` | pyflakes 清零；matching detail fixture 通过 |
| P0-02 | `adapter/loader.py::parse_milestone_graph`、`graph.py::enrich_milestone_graph`、`route.py`、`stage/*`、`evaluate/final.py`、`evaluate/scoring.py` | 缓存反序列化 graph 的 topology/routes/spec 为空，内部各处回退扫描 | 反序列化后固定执行 graph → topology → route → stage goal/spec enrich；内部直接索引，缺失即抛错；不持久化派生字段 | cached TaskCase 可直接 evaluate；无 topology fallback |
| P0-03 | `model.py::Trajectory.extend_snapshots` | 同 ID 替换且 `after_step_index` 改变时可能不排序 | 比较替换项与相邻 key，仅跨位时标记；批次末最多排序一次并重建位置索引 | 乱序/同 ID 替换/二分查询测试 |
| P0-04 | `model.py::RuntimeEvaluationState`、`TrajectoryEvaluationReport`、`evaluate/step.py` | fingerprint 字段错放 report，runtime 读取不存在字段 | fingerprint set 只放 runtime state；0 分 source 也先登记；report 不序列化 | 同 source 只评分一次 |
| J-01 | 删除 `evaluate/judge_cache.py`；改 `judges/base.py`, `standard.py`, `expensive.py`, `evaluate/evaluator.py` | 多 pass 共用 cache key，后续 pass 是深拷贝 | `LLMJudge` 只接收 `llm, passes`；每 pass 直接 `_call_json()`；语义复判每次单调调用 | fake LLM 调用数严格等于 passes |
| J-02 | `judges/{base,standard,expensive,confidence,telemetry}.py`, `model.py` | Judge JSON 在聚合链重复校验/容错 | 入口一次校验为 `ValidatedJudgePayload`；后续只消费 typed payload | 非法 payload 入口失败；每响应 validator 一次 |
| J-03 | `metrics.py`, `evaluate/telemetry.py`, `harness/config.py`, `llm/factory.py`, `model.py::LLMConfig` | cache telemetry 和 Judge env reader 重复 | 删除四个 cache telemetry 键和 `load_judge_config_from_env`；统一 `DEFAULT_JUDGE_TEMPERATURE=0.2`；环境只由 factory 读取一次 | 仅保留 TaskCase adaptation cache 指标 |
| D-01 | `harness/model.py::BenchmarkCase`、`adapter/base.py`、两个 AgentCompass harness、ToolSandbox harness、`harness/{selection,runner}.py`、`main.py` | case ID 被包装成无消费者对象 | `list_case_ids(config) -> list[str]`；删除 `BenchmarkCase`、`validate_loaded_task_cases` 和 `run_all` | case 顺序/ID 与 loader 一致 |
| D-02 | `harness/model.py`, `model.py`, adapter sessions | 只写不读字段 | 删除 `HarnessAdvanceResult.reason`、`MilestoneTopology.root_ids`、report fingerprint、ToolSandbox `raw_output_dir`、`HarnessCaseTask.case_id`、AgentCompass `finished`；`AgentCompassRunData.steps` 移除，`advance_case()` 用局部 `steps` 返回结果，`run_data is not None` 表示完成 | raw summary/trajectory 输出 golden 不变 |
| D-03 | `model.py::HarnessEvaluationOutput`, `harness/outputs.py`, `experiment/runner.py` | 同时保存可互相推导的 7 个路径 | 只保留 `raw_run_dir`、`result_dir`；使用点按固定文件名派生，index serializer 一次展开旧路径键 | 落盘路径键不变 |
| D-04 | 各包 `__init__.py` 与 `harness/runner.py` | 无 wildcard 消费者的 `__all__` | 删除无消费者 `__all__`；公共调用改显式顶层 import | 显式 import/IDE 跳转正常 |
| D-05 | `model.py::CaseProgressEvent/CaseProgressState`、`progress.py`、`harness/scheduler.py` | 死参数、死 event kind、write-only finished | event 仅 `{case_id, step_count}`；queue 只传 advanced；started/finished 由主线程直接管理；删除 `line_writer`, `max_visible_bars`, `_progress_visible_bars`, `DEFAULT_VISIBLE_PROGRESS_BARS`, `_apply_progress_event`, `finished` | 串行/并行 step count 与关闭行为一致 |
| D-06 | `experiment/model.py::ExperimentCaseResult` | `to_dict()` 无调用者，identity 分支无效 | 仅保留 `to_index_dict()` 的非 identity payload，删除 `include_identity` 分支 | experiment index golden 不变 |
| S-01 | `evaluate/scoring.py`, `adapter/toolsandbox/scorer.py`, matching/settlement/final | 两套完整评分算法；snapshots/context 重复传递 | `score_milestone(..., trajectory, context)`；基类统一 source/constraint/status/evidence，子类只覆写 `_aggregate_constraints`；删除 context 缺失回退扫描 | score/status/missing/evidence 顺序 golden 一致 |
| S-02 | `harness/config.py`, experiment/milestone dataclasses, loader, stage resolve/spec, AgentCompass result mappers, `HarnessRunResult` | 内部重复校验与即时回退 | mapping/JSON/第三方边界各校验一次；已构造 dataclass 直接信任；spec/topology 缺失直接报错；report 改为必需字段 | 负例只在入口失败 |
| S-03 | `experiment/runner.py`, `settlement.py`, `judges/base.py`, adapter base/stage helpers | 纯转发 wrapper | 内联三个 experiment 单 case wrapper；唯一 `record_settlement`；删除列出的纯转发函数；保留多调用点序列化边界 | `rg` 无指定 wrapper |
| S-04 | `experiment/runner.py`, `experiment/config.py`, `metrics.py`, `experiment/metrics.py`, `main.py` | 静态适配 O(spec²)；成本 delta 中间态重复；数值校验重复 | 单次 bucket；adaptation 后一次 finalize；`_numeric_delta`/typed row；main/runner 共用分组函数 | 1/10/100 spec 调用次数与唯一 key 相等；成本 golden |
| T-01 | `metrics.py` timing helpers | replay 对同一 trajectory 多次排序/扫描；allocation 恒真断言 | 单次 `_execution_timing_index` 提供 prefix/full/available；直接按批次分摊 latency | timing golden 与复杂度检查 |
| W-01 | `evaluate/weights.py` | `valid_count` 除法后再次 normalize，数学恒等 | task type 直接累加并 normalize 一次；空类型仅入口选默认；保留指数更新和正数下限 | 单/多/空 task type 权重和为 1 |
| E-01 | `evaluate/policy.py`, `settlement.py`, `step.py`, `evaluator.py`, config/model | policy 更新和 stage stop 双判定，三个 stop bool 重复 | policy 只算 next level；`termination_after_stage` 唯一阶段终止入口；raw fatal 保留独立即时入口；统一 `EvaluationStrategyConfig.policy_stop` | 每种失败仅一个稳定 termination code/detail |

表中编号是完整性追踪 ID，不是提交顺序或实施批次。一次实现中应先完成依赖性较强的基线修复，再完成其余删除/收敛，最后统一运行第 6 节的验收；整个过程只形成一个代码修改轮次。

## 1. 复核结论

当前生产代码没有可整模块删除的**业务**模块，但存在一个应整模块删除的技术基础设施模块 `dynsteer/evaluate/judge_cache.py`，并仍有以下高置信问题：

1. 当前 HEAD 自身尚未达到可安全精简的基线：`diagnostics.py` 使用未定义的 `topology`，`step.py` 留有未使用局部变量，缓存 TaskCase 反序列化后没有重建 `MilestoneTopology`，替换已有 snapshot 时可能破坏二分查询依赖的排序不变量。
2. 存在明确的死字段、死类型和死输出：`BenchmarkCase` 的生产消费者只读取 `case_id`；`HarnessAdvanceResult.reason` 从未读取；`MilestoneTopology.root_ids`、`ToolSandboxSession.raw_output_dir` 等只写不读；多个 `__all__` 没有生产消费者。`evaluated_minefield_sources` 则不是普通死字段，而是放错对象：字段定义在 report，运行代码却从 runtime state 读取，必须移动后再删除 report 字段。
3. 通用评分器与 ToolSandbox 评分器仍复制了 source 解析、constraint 遍历、状态判定、证据拼装等整套算法；`trajectory.snapshots` 又作为独立参数沿所有调用点重复传递，允许同一个事实出现两个来源。
4. 已由构造边界保证的对象仍在内部链路重复校验或回退：graph topology、stage spec、AgentCompass status、evaluation report、harness config、strategy/milestone generation dataclass 均有二次校验；实验 runner、finish settlement 和若干基类方法仍保留只调用下一层的中转函数。
5. 最新成本统计实现又引入两类可消除开销：静态适配分组对每个 key 重新全量扫描 specs，形成 O(spec²)；成本行先计算一次未含 adaptation 的 delta，分配 adaptation 后再部分覆盖，保存了没有最终意义的中间结果。
6. `JudgeCache` 与多轮 Judge 的原始目标直接冲突：同一阶段、维度和 prompt 的所有 pass 使用同一个 key，第一轮后其余 pass 只深拷贝第一次结果；`agreement_confidence()` 因而在重复答案上计算一致性，产生虚高置信度。用户已明确决定删除整个 Judge 缓存链，保留真实多轮调用。
7. `force_standard_dimensions` 没有任何生产调用者传入，围绕它构造的 forced dimensions、`semantic_review` metadata、结构失败解除和 `_semantic_only_hard_failure()` 均为不可达死算法；实际消息语义复判已在 milestone 匹配阶段通过 `semantic_message_review` 独立完成。
8. Judge 外部 payload 在 Standard/Expensive 聚合链中被重复校验二至三次；早停又分别由 `update_evaluation_policy()` 与 `should_stop_after_stage()` 对相同事实判定，并通过三组配置字段表达同一个开关。这些都应改成单一可信对象和单一决策入口。
9. replay timing 对同一 trajectory 重复排序和扫描；动态权重初始化先除以 `valid_count` 再 normalize，该公共标量会被归一化完全抵消，属于数学上无效果的步骤。
10. 进度系统保留了从未实现的可见条数/行输出设计：`line_writer`、`max_visible_bars` 从未读取，`_progress_visible_bars()` 的计算结果没有影响任何进度条；跨线程 event 也只产生 `case_advanced`，但仍保留永远不会进入的 started/finished 分支和 write-only `CaseProgressState.finished`。
11. Judge 环境配置被读取两次：`harness.config.load_judge_config_from_env()` 先把环境变量复制进 metadata，`llm.factory.build_llm_from_config()` 随后再次解析；直接 evaluator 又走 `build_llm_from_env()`。前一套 reader 及其 `api_key_configured` 死字段可以整段删除。
12. `_METHODS_NEED_DEFAULT` 枚举了 `ExperimentMethod` 当前全部成员，使 `if spec.method in _METHODS_NEED_DEFAULT` 恒真；`ExperimentCaseResult.to_dict()` 也没有生产消费者，却令 `_payload(include_identity)` 保留一条永远不使用的 identity 分支。

静态检查证据：

- `python -m pyflakes main.py dynsteer display` 当前报告：
  - `dynsteer/evaluate/diagnostics.py:369: undefined name 'topology'`；
  - `dynsteer/evaluate/step.py:69: local variable 'step' is assigned to but never used`。
- 顶层定义引用扫描只发现 `dynsteer.log.get_log_buffer()`、`clear_log_buffer()` 没有仓库内调用；二者是 `docs/constraints/code.md` 明确要求的日志缓冲查询能力，因此不按普通死代码删除。
- 精确函数体克隆扫描只发现两个 benchmark 的 `adapt_task_case()` 三行实现完全相同。该重复很小，直接增加配置驱动类或 mixin 的代码量会大于删除量；本方案不为消除三行重复引入新抽象，见第 8 节。
- 仓库当前只有 `tests/test_cost_statistics.py` 的 2 个成本统计测试；`python -m pytest -q` 为 **2 passed**，但评估、匹配、Judge、harness、适配缓存和输出契约均无覆盖。本轮必须先在同一工作树中补齐基线测试，再继续完成全部精简，禁止将测试补齐和代码精简拆成多个实施轮次。

## 2. 有效行数基线与口径

### 2.1 当前基线

| 范围 | Python 文件数 | 有效行数 |
| --- | ---: | ---: |
| `dynsteer/**/*.py` | 104 | 13,768 |
| `display/**/*.py` | 2 | 678 |
| `main.py` | 1 | 137 |
| 合计 | **107** | **14,583** |

说明：本文的 **14,583** 是在 `ba21f04` 完成后重新统计的基线。它与已有方案中的 14,424 不可直接相减：已有方案标注的是更早的工作阶段，此后既落实了精简，也新增了 `agentcompass/result.py`、`harness/session.py` 和成本统计功能。

### 2.2 统计规则

只统计 `main.py`、`dynsteer/**/*.py`、`display/**/*.py`，排除 `tests/`、`docs/`、示例和运维脚本。逐个物理行排除：

- 空白行；
- 去除左侧空白后以 `#` 开头的纯注释行；
- 模块、类、函数 docstring 覆盖的完整物理行；
- `logger.debug/info/warning/error/exception/critical/log(...)` 整个 AST 调用覆盖的物理行。

`print()` 是 CLI 用户输出，不按日志删除。不得通过合并语句、压缩多行参数、删除必要 docstring 或关键中文注释制造行数下降。

## 3. 精简后的核心不变量

所有修改围绕以下不变量展开；同一事实不得再出现第二个可写来源：

1. `MilestoneGraph.edges` 是持久化拓扑事实；`MilestoneGraph.topology` 是反序列化/适配边界一次构建的运行期派生索引。进入 stage/evaluate 代码后不得再容忍未 enrich graph。
2. `Trajectory` 自己拥有 `steps` 和 `snapshots`；评分接口不得再额外接收同一个 `trajectory.snapshots`。
3. `RuntimeEvaluationState` 是运行期唯一状态对象；final、prompt、settlement 统一读取其 topology/index/context，不重新扫描 graph 或重建等价映射。
4. `HarnessEvaluationOutput` 只保存无法由其他字段推导的目录；固定文件名路径在使用位置由目录生成。
5. benchmark case 枚举的真实输出是 case ID 列表，不构造生产代码从不消费的 category/metadata 包装对象。
6. 外部 JSON、环境变量、第三方框架响应只在入口边界校验一次；内部 dataclass 和已验证对象不重复做 None/type/schema 检查。
7. 通用 milestone 评分流程只有一份；benchmark 差异通过最小聚合 hook 表达，不复制整条算法。
8. `standard_passes`、`expensive_passes` 表示真实、相互独立的 LLM 请求次数；不得再通过缓存、请求合并或复制响应伪造多轮样本。
9. 每个外部 Judge JSON 只在进入系统时转换一次为 `ValidatedJudgePayload`；聚合、置信度、metadata 和最终结果只消费已验证对象。
10. 下一阶段 Judge routing 与“本阶段是否停止”是两个不同问题：前者只计算下一策略，后者由一个终止决策函数统一处理。
11. minefield fingerprint 只属于 `RuntimeEvaluationState`，因为命中与未命中的 source 都必须在运行期去重；report 只保存最终 matches，不保存执行期索引。
12. 跨线程进度队列只传递真实存在的“新增 step 数”事件；case started/finished 由调度主线程直接管理，不在 event schema 中伪装不存在的生产者。
13. 非实验运行的 Judge 环境配置只由 `llm.factory` 读取一次；实验 judge profile 才通过结构化 mapping 进入同一构造边界。

## 4. 当前算法流程（本科生可理解版）

### 4.1 先认识六个核心对象

可以把 DynSTEER 理解成“给 AI agent 的操作过程分阶段阅卷”，而不是只看最后答案。

| 代码对象 | 通俗含义 | 作用 |
| --- | --- | --- |
| `TaskCase` | 一道考试题 | 保存任务说明、工具、初始状态和评分图 |
| `Milestone` | 必须经过的检查点 | 例如“已查到联系人”“已发送消息” |
| `Constraint` | 检查点中的判分小项 | 指定检查什么、期望值、权重、是否一票否决 |
| `Minefield` | 禁止触发的危险条件 | 例如越权调用、修改不该修改的数据 |
| `Trajectory` | agent 的完整答题过程 | 按顺序保存消息、工具调用、工具结果、状态快照和成本 |
| `Settlement/StageResult` | 某个阶段的成绩单 | 保存该阶段从哪里开始、在哪里结束、各维分数和诊断 |

`MilestoneGraph` 是一张有向无环图。例如 `A -> B -> C` 表示必须先完成 A，才允许 B 成为候选；A/B 都完成后才能进入 C。`frontier.ready_ids` 就是“现在已经解锁、可以判定的检查点”。

### 4.2 总体流程

```text
CLI / experiment.json
  -> 展开 benchmark × model × method × repeat
  -> 适配原始 benchmark case 为 TaskCase
  -> 获取或生成 milestone DAG / minefield
  -> 预计算 topology、route、stage goal、stage spec
  -> 选择 DEFAULT / LIVE EVALUATE / REPLAY
  -> 逐批取得 trajectory steps 与 snapshots
  -> 每条 raw step 检查 minefield
  -> AgentStepTracker 把 raw steps 合成一个完整 agent 行为闭包
  -> 对 ready milestones 做结构化匹配与评分
  -> 命中后进行阶段多维评价、权重/策略更新、可选早停
  -> 运行结束后补 pending/finish 结算
  -> 生成 report/summary/trajectory/metrics/costs
```

用一个三阶段例子理解这条主线：

```text
A：查到联系人
B：向联系人发送消息
C：向用户确认已发送

依赖关系：A -> B -> C
初始 ready={A}
A 命中后 ready={B}
B 命中后 ready={C}
C 命中后 milestone 全覆盖，再进入 finish 核查
```

如果 agent 在 A 未完成时已经产生了“发送消息”的证据，B 仍不能被正式结算；当前 blocked scan 会把它记录成“后继已完成但前驱缺失”的越序诊断，并可能提前停止。如果 B 只产生了 0.7 分的候选，它不会推进 frontier，而是继续等待后续闭包；连续多次没有达到有效提升时，ready-frontier watch 可以终止运行。

### 4.3 适配阶段：先把不同 benchmark 变成同一种题目

1. `main.py` 解析 CLI。`--exp` 走统一实验矩阵；`--benchmark` 走单 benchmark；`--only-adapt` 只生成 TaskCase。
2. `expand_experiment_matrix()` 把 benchmark、model、method、repeat、threshold profile 做笛卡尔积，得到多个 `ExperimentRunSpec`。
3. adapter 把 ToolSandbox、SkillsBench、SWE-bench Pro 的原始任务转成统一 `TaskCase`。
4. 如果使用原生 milestone，就读取原图；否则 `compile_task_case()` 让 LLM 模拟多条成功路径，从路径中提取共识节点，再做 DAG 检查和传递约简。
5. `enrich_milestone_graph()` 从持久化 `edges` 计算：前驱、后继、拓扑顺序、stage anchor、terminal milestone、finish anchor。
6. `enrich_milestone_routes()` 从 constraint 的 sender/recipient 语义中为 milestone 选择匹配 route。
7. `generate_stage_goal_templates()` 生成不包含本次具体 expected 的阶段目标模板；`materialize_stage_goals()` 再填入 expected；`generate_stage_evaluation_specs()` 决定每个阶段要评价哪些维度。

最终，不同 benchmark 都变成同一套“题目 + 检查点图 + 禁区 + 评分说明”。这是必要的抽象边界，不应删除。

### 4.4 三种运行模式

#### DEFAULT

让 benchmark 原生执行器完整运行，不进行 DynSTEER 早停。保存原始 trajectory 和 benchmark 自带分数，作为成本/效果比较基线。

#### DYNSTEER_EVALUATE（live）

agent 正在运行时逐步评估。如果命中 fatal minefield、结构失败、低分或 frontier 长期无进展，可以调用 `harness.stop_case()` 真正提前结束外部执行。

#### DYNSTEER_REPLAY

读取 DEFAULT 的完整 trajectory，在本地重新播放评估。它不能改变已经发生的执行，只记录“如果当时在线评估，会在哪一步虚拟停止”。静态 routing/weighting 变体只是关闭部分动态策略，用于消融实验。

当前 live 与 replay 在 termination 后还有一个不一致：live 以 `finish_on_termination=False` 调用最终化，replay 则传 `True`。有 pending milestone 时两者都会生成 pending 并跳过 finish；但若终止时所有 milestone 恰好已经匹配，replay 可能追加 finish，live 不追加，导致同一前缀的 stage 分母不同。它不是可直接证明无效果的死分支，必须先决定“终止前缀是否仍应做 finish 核查”，见第 5.11 节。

### 4.5 每个执行批次如何进入评估

`collect_session_trajectory()` 循环调用 `harness.timed_advance_case()`：

1. 记录这次外部执行批次耗时；
2. 合并新 snapshots、final state 和 benchmark metrics；
3. 按 index 把每条 step 追加到 trajectory；
4. live 模式立即评估该 step；DEFAULT 只统计完整 agent 行为数量；
5. session 自然结束时由 `AgentStepTracker.finalize()` 尝试闭合最后一条 agent 消息。

`AgentStepTracker` 的必要性在于：一次 agent 行为可能不是一行。例如“agent 发起 tool call -> environment 返回 tool result”是一个闭包；并行 tool call 还要用 correlation ID 区分。不能简单地把每个 raw step 都当作一个独立决策。

### 4.6 每条 raw step：先检查 minefield

`evaluate_step_minefields()` 在 milestone 判定前执行：

1. 根据 constraint target 选择当前 message/tool/state source；
2. 用 `(minefield_id, source_identity)` 形成 fingerprint；同一证据已评过则跳过；
3. 对 minefield 内 constraints 做加权平均；
4. score 大于 0 就记录命中；severity=fatal 且 score=1 时标记 fatal；
5. 如果允许 policy stop，立即生成 termination。

这条链保证危险行为不会因为尚未闭合 agent step 而漏检。source fingerprint 去重是必要的；每条 step 重新遍历所有 minefield 的外层循环仍有优化空间，但不是完全无效果的死流程。

### 4.7 一个 agent 闭包：匹配 milestone

闭包完成后，`analyze_milestone_step()` 执行：

1. 从 frontier 取当前 ready milestones；
2. 为闭包建立 `(actor, recipient) -> 最后一步` 的 route index；
3. 每个 ready milestone 选择最合适的 scoring step；
4. `GeneralScorer.score_milestone()` 对所有 constraints 评分；
5. PASS 候选中选择分数最高者；
6. 如果用户可见消息只因文本结构/fuzzy match 失败，可交给 StandardJudge 做窄域语义等价复判；
7. 如果没有 ready hit，还会评分“部分前驱已完成”的 blocked milestones，用于发现 agent 越过前驱直接完成后续节点；
8. 记录 candidate scores、reject reason、selected candidate 和 predecessor diagnostics。

通用 constraint 的 source 选择规则：

- `STATE_SNAPSHOT`：取当前 step 之前最近的 snapshot；
- `METRIC`：取 trajectory metrics；
- `TOOL_CALL/TOOL_RESULT`：在当前阶段区间内倒序找最近对应 step；
- 其他 message/state delta：使用当前 scoring step；
- 有 reference milestone 时，从 `ScoringContext.matched_snapshots` 读取参考快照。

通用 milestone 分数为：

```text
weighted_score = Σ(constraint_score × max(weight, 0)) / Σ(max(weight, 0))
```

只要 hard constraint 低于自身 threshold，最终 milestone score 直接变为 0。否则达到 milestone pass threshold 为 PASS，达到 0.6 为 WARN，其余 FAIL。

ToolSandbox 使用不同聚合：普通 constraint 分数做几何乘积/几何平均，guardrail 命中可直接让 hard pass 失败。因此 ToolSandbox 的聚合差异需要保留，但“解析 source、遍历 constraints、拼结果、判 status”的公共骨架不需要复制。

### 4.8 milestone 命中后：阶段评价、动态权重和路由

命中 milestone 后，`evaluate_checkpoint()` 先确定阶段区间 `(上一个 anchor boundary, 当前 scoring step]`，再调用 `_evaluate_stage()`：

1. 根据 stage spec 选择本阶段 focus dimensions；
2. CheapJudge 用结构化 milestone score 和本地质量诊断给出各维基础分；
3. 当前 policy 指定为 STANDARD 的维度由 StandardJudge 覆盖；
4. 指定为 EXPENSIVE 的维度由 ExpensiveJudge 逐维专项评价后覆盖；
5. 按当前动态权重计算阶段总分：

```text
stage_score = Σ(dimension_score × dimension_weight) / Σ(本阶段出现的 dimension_weight)
```

6. hard constraint 失败时，无论质量维分多高，stage status 仍为 FAIL；
7. 动态权重把低分或高不确定维度的权重提高：

```text
w_next = normalize(w × exp(alpha × (1-score) + beta × uncertainty))
```

8. 动态 routing 根据本阶段分数/不确定性决定下一阶段用 cheap、standard 或 expensive；
9. 接受结算后推进 frontier，并保存 matched snapshot、stage report、weights 和 policy。

直观理解：做得差或系统没把握的维度，下一个阶段会被看得更重、也可能交给更昂贵的 judge。

多轮 Judge 本来的含义是“让同一位阅卷者独立阅卷多次”：Standard 对整个阶段请求 `standard_passes` 次，Expensive 对每个目标维度请求 `expensive_passes` 次，再对真实样本的分数求均值，并根据样本分歧估计置信度。理论上的真实调用数为：

```text
Standard 调用数 = standard_passes
Expensive 调用数 = expensive 目标维度数 × expensive_passes
另加实际触发的单条消息语义复判调用
```

当前实现却在每轮使用完全相同的缓存 key。因此配置 3 passes 时，实际通常只有第一次调用 LLM，后两次是第一次 JSON 的深拷贝；均值等于第一次分数，分歧恒为零或接近零，所谓“置信度”并没有来自三次独立判断。这不是普通性能优化，而是改变了算法定义，属于必须删除的错误冗余。

删除缓存后，每个 pass 必须直接调用 `_call_json()`。Judge 默认 `temperature` 同时从 `0.0` 统一调整为保守非零值 `0.2`，使独立请求有机会产生采样差异；显式配置仍可覆盖该值，以便做确定性消融实验。不得通过给 prompt 添加无业务意义的随机文本制造差异，也不得把“响应不同”当作正确性的充分条件；`agreement_confidence` 只表示多 pass 一致程度，不表示真实准确率。

当前 Judge 聚合的精确规则是：同一维度的多 pass 分数取算术平均；status 不投多数票，而是选择所有响应中最严重的一个；evidence/diagnosis 去重后截断；置信度先把分数映射到 `0/0.25/0.5/0.75/1.0` 五档，再用档位分布的归一化熵计算一致程度。也就是说“两次 PASS、一次 FAIL”仍可能得到 FAIL status，而分数取三次平均。这是保守评估语义，不属于代码重复；是否改为多数票需要独立的算法决策。

### 4.9 结束时如何计算结果

如果仍有未完成 milestone，`pending_milestone_stage_results()` 为每个未完成节点生成一个 0 分的 FAIL/MISSING synthetic stage，并立即返回，**不会再生成 finish settlement**；否则才生成 finish settlement：

- 有 milestone graph：检查覆盖率、terminal state 是否被后续步骤破坏、fatal minefield；
- 空 milestone graph：让 StandardJudge 对完整 trajectory 做一次终态评价；
- terminal message 不在结束后再次调用 LLM，只引用原 milestone 匹配结果。

最终：

```text
raw_stage_average = average(stage_result.stage_score)
overall_score = clamp(raw_stage_average × (1 - max_minefield_penalty))
```

因此当前“未完成”不是 pending 与 finish 双重记零，而是“每个未完成 milestone 各记一个 0 分”。真正需要讨论的是：多个未完成节点是否应该按节点数多次进入平均分。fatal minefield 则可能同时让 finish 变成 0 分并再次应用最终 penalty；这一项确实存在潜在双重惩罚，见第 5.11 节。

输出层写出 `trajectory.json`、`raw_summary.json`、`summary.json`、`report.json`；实验层再生成 index、scores、metrics 和 DEFAULT/DynSTEER 配对成本分析。

### 4.10 当前算法复杂度的主要来源

| 环节 | 当前主要复杂度 | 说明 |
| --- | --- | --- |
| 实验静态适配分组 | O(spec²) | 每个新 key 又扫描一次全部 specs，可降为 O(spec) |
| ready milestone 匹配 | O(closure steps + ready constraints) | route index 已避免每个 milestone 重扫 closure |
| blocked diagnostics | O(blocked constraints) / closure | 会显著增加诊断成本，是否保留取决于是否需要越序早停 |
| minefield | O(changed minefield constraints) / raw step | fingerprint 避免相同 source 重评，但外层仍遍历 minefields |
| Tool/State source | interval 倒序 + O(1) context lookup | snapshot reference 旧回退仍可能线性扫描，可删除 |
| Judge | 当前表面为 passes×dimensions | 实际同 key cache 使多 pass 变成重复拷贝；删除缓存后恢复真实 passes×dimensions 调用，见第 5.1.6 节 |
| replay timing | 同一 trajectory 多次全量扫描 | prefix/full/available 重复解析 records，可合并为一次 |

## 5. 详细执行方案

### 5.1 主题 A：修复残留缺陷并恢复正确行为

#### 5.1.1 修复 `build_milestone_matching_detail()` 的 topology 参数断裂

涉及：

- `dynsteer/evaluate/diagnostics.py`
- `dynsteer/evaluate/settlement.py`

当前 `settlement.evaluate_checkpoint()` 已传入 `topology=state.milestone_frontier.topology`，但 `build_milestone_matching_detail()` 签名没有该参数，函数体却读取 `topology.predecessors_by_id`。将签名显式增加 `topology: MilestoneTopology`，导入对应类型，并保留唯一调用点的现有实参。禁止在函数内重新从 graph 或 milestone metadata 推导 topology。

同文件 `_pending_failure_diagnostics()` 的 `topology` 参数恰好相反：函数体完全不使用它，唯一调用点也没有传入，当前会形成运行时缺参错误。直接删除该形参，不为满足错误签名而传入无用对象。

同时删除 `evaluate_agent_step()` 中未使用的 `step = closure.end_step`。

验收：`python -m pyflakes main.py dynsteer display` 不再有任何输出。

#### 5.1.2 在 TaskCase 反序列化边界重建 graph 派生索引

涉及：

- `dynsteer/adapter/loader.py`
- `dynsteer/graph.py`
- `dynsteer/adapter/route.py`
- `dynsteer/stage/goal.py`
- `dynsteer/stage/spec.py`
- `dynsteer/stage/resolve.py`
- `dynsteer/evaluate/final.py`
- `dynsteer/evaluate/scoring.py`

当前 `MilestoneGraph.topology` 被标记为不序列化字段；`parse_milestone_graph()` 只重建 nodes/edges/minefields，导致命中 adapted case 缓存时 topology 为 `None`。目标改法：

```python
graph = MilestoneGraph(...)
enrich_milestone_graph(graph)
return enrich_milestone_routes(graph)
```

新生成 graph 仍在 `_adapt_task_case()` 中走同一 enrich 顺序。完成后，以 `initialize_milestone_frontier()` 作为 evaluate 边界的最终不变量检查；进入 stage/evaluate 内部后直接使用 `graph.topology`，删除各文件中的“若 topology 为 None 则回退空映射/START/即时扫描”的兼容分支。

不得把 topology 写入 JSON；缓存文件继续只保存 `edges`，避免派生状态持久化。

#### 5.1.3 修复 snapshot 替换时的排序不变量

涉及：`dynsteer/model.py::Trajectory.extend_snapshots()`。

当前新增乱序 snapshot 会触发排序，但使用相同 `snapshot_id` 替换旧对象时，即使 `after_step_index` 改变也不会标记乱序，随后 `snapshot_at_or_before()` 的二分结果可能错误。修改为：

- 新 ID 延续 O(1) append/index 路径；
- 替换 ID 时比较新 key 与相邻 snapshot key，仅在跨越相邻位置时标记 `needs_sort`；
- 批次结束最多排序一次并重建 `_snapshot_positions`；
- 不改为每次无条件全量排序。

#### 5.1.4 把 minefield 去重索引移到真实运行期状态

涉及：

- `dynsteer/model.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/evaluator.py`

当前 `TrajectoryEvaluationReport` 定义了 `evaluated_minefield_sources`，但 report 从不读取或输出它；`evaluate_step_minefields()` 实际访问 `state.evaluated_minefield_sources`，而 `RuntimeEvaluationState` 没有该字段，首次 minefield 扫描会触发 `AttributeError`。修改为：

```python
class RuntimeEvaluationState:
    evaluated_minefield_sources: set[str] = field(default_factory=set)
```

并从 `TrajectoryEvaluationReport` 删除同名字段。不能把该集合完全删除：minefield 得分为 0 的 source 不会进入 `minefield_matches`，但仍必须记录为“已经评过”，否则每条后续 raw step 都会对未变化的 state/message 重复评分。该集合是运行期索引，不进入 report JSON。

#### 5.1.5 新增最小行为基线测试

新增测试不计入有效行数，至少包含：

- `tests/adapter/test_cached_graph_enrichment.py`：保存后重新解析 TaskCase，topology、route、stage goals 和 evaluation specs 可直接使用；
- `tests/model/test_trajectory_snapshots.py`：新增乱序、同 ID 同位置替换、同 ID 跨位置替换和二分查询；
- `tests/evaluate/test_milestone_matching_detail.py`：predecessor diagnostics 可生成且无未定义变量；
- `tests/evaluate/test_minefield_source_dedup.py`：同一 source 只评分一次，0 分 source 也进入 runtime fingerprint set，report 不输出该集合；
- `tests/golden/test_output_schema.py`：固定 default/live/replay 的四类 JSON 文件和关键字段。

这些修复与后续删除/合并属于同一轮修改中的依赖顺序：先在工作树内完成修复，再继续编辑同一轮中的其余文件；不得拆成前后两轮，也不得以中间状态作为交付版本。

#### 5.1.6 删除 `JudgeCache`，恢复真实多轮 Judge

这是用户已经确认的算法语义，不再作为可选项。涉及：

- 删除整个 `dynsteer/evaluate/judge_cache.py`；
- `dynsteer/judges/base.py`；
- `dynsteer/judges/standard.py`；
- `dynsteer/judges/expensive.py`；
- `dynsteer/evaluate/evaluator.py`；
- `dynsteer/metrics.py`；
- `dynsteer/model.py`、`dynsteer/llm/factory.py`、`dynsteer/harness/config.py`；
- 新增的 Judge 单元测试和 metrics golden fixture。

按以下最终形态修改，不保留兼容层：

1. `LLMJudge.__init__()` 只接收 `llm` 与 `passes`；删除 `cache` 参数、`self._cache`、`_cached_call_json()`、`_model_id()`、`judge_context_digest()` 及其 `copy/hashlib/json_safe` 依赖。
2. `StandardJudge.evaluate_stage()` 用明确的 pass 循环直接调用 `_call_json(prompt, language)`；不再构造只服务 cache key 的 `cache_context`。`ExpensiveJudge` 对每个维度执行同样的真实 pass 循环。
3. 每轮 metadata 增加从 1 开始的 `judge_pass_index`，Expensive 同时记录 `judge_dimension`。这些字段用于审计“第几次真实调用”，不成为缓存键，也不改变业务 prompt。
4. `review_message_equivalence()` 直接调用一次 `_call_json()`；删除 `semantic_review=True`、boundary、model ID、trajectory prefix 等只用于缓存的上下文拼装。消息复判仍保持“每个实际触发目标调用一次”，不擅自扩为多 pass。
5. `DynSTEEREvaluator` 删除 `judge_cache` 构造参数、字段、judge 间 cache 注入、case/replay 边界 `clear()` 和 runtime metrics telemetry 注入。`from_config()` 直接构造两个 Judge。
6. `build_runtime_metrics()` 删除 `judge_cache_metrics` 参数和 `metrics.update(...)`；落盘 metrics 删除 `llm_unique_prompt_count`、`llm_cache_hit_count`、`llm_duplicate_prompt_avoided_count`、`semantic_review_cache_hit_count`。`llm_call_count` 与逐调用 `llm_calls` 已经是更真实、足够的观测来源。
7. 不删除 adapter 的 `adaptation_usage.cache_hit` 和实验成本中的 `cache_hit_count`：它们描述 TaskCase 静态适配产物缓存，与 Judge 响应缓存不是同一功能。
8. 整体删除 `harness.config.load_judge_config_from_env()`、`judge_config` 局部变量和向 run metadata 复制 Judge 环境配置的分支。非实验 evaluator 在 metadata 没有结构化 judge profile 时已经调用 `build_llm_from_env()`，passes 也直接从相同环境变量读取；前置复制没有新增能力。随函数删除从未被消费的 `api_key_configured`。
9. 在 `dynsteer/model.py` 定义唯一的 Judge 默认温度 `DEFAULT_JUDGE_TEMPERATURE = 0.2`，`LLMConfig`、`build_llm_from_env()` 和 `build_llm_from_config()` 共用该值，删除各写一个 `0.0` 的重复默认值。实验 judge profile 继续走 mapping，普通运行只由 `llm.factory` 读取环境一次；显式 `temperature` 仍优先，不增加多层重复校验。

真实多轮测试必须使用计数型 fake LLM，并断言：

- Standard 的 chat 次数严格等于 `standard_passes`，返回的三个不同分数均进入聚合；
- Expensive 的 chat 次数严格等于 `len(target_dimensions) × expensive_passes`；
- 相同 prompt 的连续 pass 不会复用对象或响应；
- 三个相同样本得到高 agreement，三个分歧样本得到更低 agreement；
- 两个 evaluator case 之间无待 clear 的 Judge 状态；
- runtime metrics 的 `llm_call_count` 与 fake LLM 实际调用数一致，且所有旧 cache telemetry 键均不存在。

#### 5.1.7 删除不可达的 forced-standard 语义解除链

涉及：`dynsteer/evaluate/settlement.py` 及对应新增测试。全仓生产调用点没有向 `evaluate_checkpoint(..., force_standard_dimensions=...)` 或 `_evaluate_stage(..., force_standard_dimensions=...)` 传入实参；默认 `None` 使下列代码永久不可达：

- `requested_force_dimensions`、`forced_dimensions` 计算和 STANDARD level 强制覆盖；
- `semantic_review_metadata` 的构造、回填和 `metadata["semantic_review"]`；
- `semantic_review_passed` 与 `semantic_review_clears_structural_failure`；
- 强行把 hard failure、missing ratio、诊断和 stage score 改写为通过；
- `_semantic_only_hard_failure()` 及其对 graph 的线性扫描。

删除两个函数签名/中文 docstring 中的该参数和上述整条分支。不得把这条死链与当前实际使用的消息语义复判混淆：真实链路是 `matching.milestone -> step._semantic_message_review_score() -> StandardJudge.review_message_equivalence() -> metadata["semantic_message_review"]`，它在结构化文本比较不足时重新计算 milestone score，应保留。`evaluate_checkpoint()` 当前读取 `metadata["semantic_review"]` 的后处理也随死 metadata 一并删除。

测试分别固定：语义等价消息可通过真实 `semantic_message_review` 链解除对应 constraint 失败；非消息 hard failure 不能被解除；代码扫描不再命中 `force_standard_dimensions`、`_semantic_only_hard_failure` 和 stage-result 级 `semantic_review`。

### 5.2 主题 B：删除明确的死类型、死字段和死导出

#### 5.2.1 用 `list[str]` 取代从未被消费的 `BenchmarkCase`

涉及：

- `dynsteer/harness/model.py`
- `dynsteer/harness/__init__.py`
- `dynsteer/adapter/base.py`
- `dynsteer/adapter/agentcompass/harness.py`
- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/harness/selection.py`
- `dynsteer/harness/runner.py`
- `main.py`

生产代码对 `BenchmarkCase` 的 category、metadata、`to_dict()` 均无读取，只把对象转换回 `case.case_id`。修改接口为：

```python
class BaseBenchmarkHarness(ABC):
    @abstractmethod
    def list_case_ids(self, config: HarnessRunConfig) -> list[str]: ...
```

- AgentCompass 直接返回 `sorted(load_task_records(...))`；
- ToolSandbox 直接返回排序后的 scenario key 字符串，不再提取 category、调用 `enum_name()` 或构造对象；
- `select_case_ids()` 去掉生产中恒为 `True` 的 `run_all` 参数，返回配置 case IDs 或 `harness.list_case_ids(config)`；
- `main._adapt_only_configs()` 复用 `select_case_ids()`，删除手写的 case 对象集合；
- 删除 `validate_loaded_task_cases()` 及 runner/main 的整列顺序复核：`load_task_case()` 已按 `case_ids` 原顺序循环，并在新适配与缓存读取两条分支逐项校验 `task_case.case_id == case_id`，返回后再构造 ID 列表没有新增不变量；
- 删除 `BenchmarkCase` dataclass、序列化函数、相关 import/re-export。

这不是压缩展示代码，而是删除一整层无消费者的数据模型与对象分配。

#### 5.2.2 删除只写不读的状态

逐项修改：

| 位置 | 删除内容 | 修改后的事实来源 |
| --- | --- | --- |
| `HarnessAdvanceResult` | `reason` 字段、校验和三个构造实参 | `continue_running` 决定是否继续；具体终止原因仍在 session/raw summary 中 |
| `MilestoneTopology` | `root_ids` | 需要时由 `predecessors_by_id` 推导；当前没有生产读取者 |
| `TrajectoryEvaluationReport` | `evaluated_minefield_sources` | 该集合只属于 `RuntimeEvaluationState`，report 从未读写它 |
| `ToolSandboxSession` | `raw_output_dir` | ToolSandbox 不向该目录写原生文件；统一接口参数仍保留但不存入 session |
| `HarnessCaseTask` | `case_id` | 使用 `task.task_case.case_id` |
| `AgentCompassRunData` | `steps` | `advance_case()` 保留局部 `steps`，先传给 `_build_run_data(detail)` 之外的返回值，再直接写入本次 `HarnessAdvanceResult`；run-data 只保存 default/raw/final 三类结果 |
| `AgentCompassSession` | `finished` | `run_data is not None` 唯一表示整任务已执行；第二次 `advance_case()` 直接返回空结果 |

`AgentCompassHarness._build_run_data()` 不再接收只为回填 `AgentCompassRunData.steps` 的 `steps` 参数；两个 benchmark 子类同步收紧为 `_build_run_data(detail)`。`advance_case()` 的具体顺序固定为“读取 detail → 局部转换 `steps` → `current.run_data = self._build_run_data(detail)` → 标记由 `run_data` 表示的完成状态 → 返回 `HarnessAdvanceResult(steps=steps, ...)`”，不得把 steps 再存回 session 或 run-data。

#### 5.2.3 收敛 `HarnessEvaluationOutput` 的可推导路径

涉及：

- `dynsteer/model.py`
- `dynsteer/harness/outputs.py`
- `dynsteer/experiment/runner.py`

当前对象同时保存 `run_dir`、`raw_run_dir`、`result_dir` 和四个固定文件名路径；`run_dir` 等于 `result_dir`，`raw_summary_path` 没有生产读取者，其余文件路径均可由两个目录推导。修改为只保存：

```python
@dataclass(frozen=True)
class HarnessEvaluationOutput:
    raw_run_dir: Path
    result_dir: Path
```

使用点明确写为 `output.result_dir / "summary.json"`、`output.result_dir / "report.json"`、`output.raw_run_dir / "trajectory.json"`。experiment index 仍输出既有五个路径键，但只在 serializer 中一次派生，避免内存对象长期保存七份等价路径。

#### 5.2.4 删除无生产语义的 `__all__`

删除以下包/模块的 `__all__` 赋值，但保留约束要求的显式顶层 import：

- `dynsteer/evaluate/__init__.py`
- `dynsteer/evaluate/matching/__init__.py`
- `dynsteer/harness/__init__.py`
- `dynsteer/judges/__init__.py`
- `dynsteer/llm/__init__.py`
- `dynsteer/milestone/__init__.py`
- `dynsteer/stage/__init__.py`
- `dynsteer/harness/runner.py`

仓库没有 wildcard import；删除 `__all__` 不改变显式 import 和 IDE 跳转。`main.py` 不再通过 `harness.runner` 间接 re-export `HarnessEvaluationOutput`，改从其定义模块显式导入。

#### 5.2.5 删除进度系统中从未生效的状态和事件种类

涉及：

- `dynsteer/model.py`
- `dynsteer/progress.py`
- `dynsteer/harness/scheduler.py`

静态调用证据表明：

- `TqdmCaseProgressManager.__init__()` 的 `line_writer` 和 `max_visible_bars` 从未在函数体读取；
- `_progress_visible_bars()` 的返回值只传给上述无用参数；
- `DEFAULT_VISIBLE_PROGRESS_BARS` 只服务该死计算；
- worker queue 只构造 `CaseProgressEvent("case_advanced", ...)`，从未产生 `case_started`/`case_finished`；
- `CaseProgressState.finished` 只被写为 True/False，从未读取。

删除两个构造参数、死 helper、死常量和 `finished` 字段。把 `CaseProgressEvent` 收敛为 `case_id + step_count`，删除 `kind` Literal；调度主线程继续直接调用 manager 的 started/finished，queue drain 只应用 advanced。删除只根据 `kind` 转发的 `_apply_progress_event()`，两个调用点直接执行 `manager.case_advanced(event.case_id, event.step_count)`。

这不会限制可见进度条：当前实际可见数量一直由 `max_workers`、`active_order` 和 tqdm `position` 控制，`max_visible_bars` 从未参与控制。

#### 5.2.6 删除 `ExperimentCaseResult` 的死序列化分支

涉及：`dynsteer/experiment/model.py`。

生产代码只调用 `to_index_dict()`；`to_dict()` 没有调用者。删除 `to_dict()` 后，`_payload(include_identity)` 的 `include_identity=True` 分支也失去唯一入口。将 `to_index_dict()` 直接返回当前非 identity payload，删除私有中转函数、bool 参数和 identity update 分支。实验索引已经用目录层级表达 experiment/benchmark/method/model/repeat/case identity，不需要在每个 case payload 内重复一份。

### 5.3 主题 C：让通用 milestone 评分算法只有一份

#### 5.3.1 删除重复 snapshots 参数和兼容扫描

涉及：

- `dynsteer/evaluate/scoring.py`
- `dynsteer/adapter/toolsandbox/scorer.py`
- `dynsteer/evaluate/matching/milestone.py`
- `dynsteer/evaluate/matching/minefield.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/final.py`

把接口从：

```python
score_milestone(milestone, scoring_step, trajectory, reference_snapshots, context=None)
constraint_sources(constraint, scoring_step, trajectory, snapshots, context=None, ...)
```

收敛为：

```python
score_milestone(milestone, scoring_step, trajectory, context)
constraint_sources(constraint, scoring_step, trajectory, context, ...)
```

理由：所有生产调用都传 `trajectory.snapshots`，额外参数既重复又允许传入不一致列表。reference milestone 统一从 `ScoringContext.matched_snapshots` O(1) 读取，删除“context 未命中后线性扫描全部 snapshots，按 snapshot_id 猜 reference milestone”的旧兼容回退。

`ScoringContext` 在运行期已始终存在，因此 scorer 热路径改为必需参数；context 缺失只在 evaluator 初始化边界报错，不在每条 constraint 内重复兜底。

#### 5.3.2 抽取唯一聚合 hook，删除 ToolSandbox 整段复制实现

`GeneralScorer.score_milestone()` 唯一负责：

1. 计算 stage start；
2. 遍历 constraint 并解析 source/reference；
3. 调用 `score_constraint()`；
4. 计算 missing ratio、status、evidence；
5. 构造 `MilestoneScore`。

仅把 benchmark 差异放入一个受保护 hook：

```python
def _aggregate_constraints(
    self,
    milestone: Milestone,
    scores: list[ConstraintScore],
) -> tuple[float, bool]:
    """返回聚合分数和 hard_constraints_all_pass。"""
```

- `GeneralScorer` 实现当前加权算术平均和 hard threshold；
- `ToolSandboxConstraintScorer` 只覆盖该 hook，实现现有 geometric aggregation、guardrail 和 semantic message 规则；
- 删除 ToolSandbox 中复制的 `score_milestone()`、status 分支、missing/evidence 拼装；
- `score_custom_constraint()` 由基类只在 `Operator.CUSTOM` 时调用，因此删除子类中“不为 CUSTOM 则回调 score_constraint”的不可能分支。

必须用 golden tests 证明改造前后以下字段完全一致：score、status、missing_ratio、hard_constraints_all_pass、constraint_scores 顺序和 evidence 顺序。

#### 5.3.3 复用 topology 和 state context，删除重复扫描

- `prompt/judge.py::_constraint_checks()` 用 `graph.topology.milestone_by_id.get(interval.milestone_id)` 取代逐 node 线性查找；
- `settlement.py::_semantic_only_hard_failure()` 使用同一索引；
- `runtime.pending_milestone_stage_results()` 使用 topology 的 `milestone_by_id`，不再重建字典；
- `final._terminal_state_checks()` 直接接收 `RuntimeEvaluationState` 并调用 `state_scoring_context()`，不再重复传 matched/reference maps 和重建 context；
- 将仅供 `state_scoring_context()` 调用的 `scoring_context()` 构造逻辑内联，删除无必要中转函数。

### 5.4 主题 D：把校验放回唯一边界

#### 5.4.1 dataclass 是 strategy/generation 配置的唯一值域校验者

涉及：

- `dynsteer/harness/config.py`
- `dynsteer/experiment/model.py`
- `dynsteer/milestone/model.py`

`evaluation_strategy_from_mapping()` 当前先用 `_bool_from_mapping()` 校验五个 bool，`EvaluationStrategyConfig.__post_init__()` 又校验一次。删除 `_bool_from_mapping()`，mapping 层只处理缺省值和 enum 转换，bool 值域由 dataclass 一次校验。

`milestone_generation_from_mapping()` 保留未知字段拒绝和 `generator` Mapping 到 dict 的边界转换，删除与 `MilestoneGenerationConfig.__post_init__()` 重复的 `use_origin_milestone`、`simulated_path_count` 类型校验。

#### 5.4.2 graph/stage spec 只在创建或反序列化时校验

涉及：

- `dynsteer/adapter/loader.py`
- `dynsteer/stage/goal.py`
- `dynsteer/stage/spec.py`

- `_postprocess_task_case()` 已调用 `generate_stage_evaluation_specs()`，后者内部已验证结果；删除 `_adapt_task_case()` 随后的第二次 `validate_stage_evaluation_specs()`；
- `resolve_stage_evaluation_spec()` 不再在每个 checkpoint 调用 `_validate_spec()`，也不再在缺失时即时重新生成并回写。适配/反序列化边界保证 specs 完整；运行时缺失直接抛出一个明确错误；
- 修复 `_validate_spec()` 中无效的重复维度判断：当前比较 `set(deduplicated)` 与 `set(original)` 永远相等。改为在边界检查 `len(set(focus_dimensions)) == len(focus_dimensions)`，不在“validate”函数中偷偷修改对象；
- topology 在第 4.1.2 节统一建立后，删除 stage 模块内部重复的 `topology is None` 防御。

#### 5.4.3 harness config 每条执行链只准备一次

涉及：

- `dynsteer/adapter/base.py`
- `dynsteer/adapter/agentcompass/harness.py`
- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/harness/runner.py`
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/harness/outputs.py`

保留 `prepare_config()` 作为公开执行边界；`_validate_config()` 只检查跨对象不变量 `config.benchmark == harness.benchmark`，不重复检查 `HarnessRunConfig.__post_init__()` 已保证的非空 benchmark/data_root。

`list_case_ids()`、`start_case()` 不再二次调用 `prepare_config()`/`_validate_config()`。runner/default/evaluator 三个独立公开入口各自只调用一次，然后内部调用链信任同一个 config。

基类 `refresh_task_case_for_experiment()` 的 no-op 实现直接返回 `task_case`；删除对调用者已持有类型对象的重复 None 检查。`default_result_from_session()` 被所有具体 harness 覆盖，改为 abstract method，删除永远不会执行的 NotImplemented fallback 和 session 校验。

#### 5.4.4 AgentCompass detail 只校验一次

涉及：

- `dynsteer/adapter/agentcompass/runtime.py`
- `dynsteer/adapter/agentcompass/result.py`
- `dynsteer/adapter/skillsbench/utils/result.py`
- `dynsteer/adapter/swebench_pro/utils/result.py`
- 两个 AgentCompass harness

`_sanitize_detail()` 的职责只保留安全白名单投影和敏感字段剥离；删除与 `validated_attempt()` 重复的 status 集合、status/correct 类型校验。benchmark result mapper 是唯一业务 schema 校验边界。

两个 harness 在 `native_result_summary()` 已验证 `detail.attempt` 后不再重新 `isinstance(attempt, dict)`；通过明确的受信任返回约定或静态 `cast` 使用同一 attempt。`_load_task_records_cached()` 删除只由内部常量实参提供的 `agentcompass_commit` 参数及恒等校验，缓存键由 benchmark/data_dir/params 足以唯一确定。

#### 5.4.5 evaluation report 改为必需字段

`HarnessRunResult` 只有一个生产构造点，且总是传入 `TrajectoryEvaluationReport`。把 `evaluation_report` 改为非 Optional 必需字段并调整字段顺序，删除 evaluator 和 outputs 中刚构造后立刻执行的两次 `None` 校验。

#### 5.4.6 Judge payload 只解析和校验一次

涉及：

- `dynsteer/judges/base.py`
- `dynsteer/judges/standard.py`
- `dynsteer/judges/expensive.py`
- `dynsteer/judges/confidence.py`
- `dynsteer/judges/telemetry.py`
- `dynsteer/model.py`

当前 Standard 先逐 payload 调 `_validate_payload()`，聚合函数再从原始 dict 容错解析，`_result_from_payload()` 对聚合 dict 又校验一次。Expensive 还会在 `_pass_metadata()` 中再校验同一 payload。改为：

```text
LLM 原始 JSON
  -> _validate_payload() 一次
  -> ValidatedJudgePayload
  -> typed payload 列表完成均值、一致性、状态和文本聚合
  -> 直接构造 StageEvaluationResult
```

`ValidatedJudgePayload` 是内部可信值对象；聚合函数接收它并返回它，`_result_from_payload()` 不再重新校验。`agreement_confidence()` 直接读 `dimension_scores`；`aggregate_status()` 不再把非法状态悄悄降为 INVALID，因为非法外部值早已在唯一边界报错；`judge_payload_output_metadata()` 和 Expensive pass metadata 读取同一个 typed payload。删除 `_dimension_scores()` 中针对缺失 dict/非数字的二次容错，以及 `_pass_metadata()` 的重复 `_validate_payload()`。

`_parse_json_text()` 已保证返回 dict，`_call_json()` 随后的第二次 `isinstance(data, dict)` 同时删除。同步把 StandardJudge 的“单轮”类/方法 docstring 改为“多轮独立调用并聚合”，避免注释继续描述缓存造成的旧行为。

这里删除的是内部重复校验，不删除外部 JSON 的 status、维度值域、evidence/diagnosis/metadata schema 校验。验证测试应为每个外部响应只观察到一次 payload validator 调用，并固定非法 status、缺失维度、越界分数仍在入口失败。

### 5.5 主题 E：删除只转发调用的流程中转层

#### 5.5.1 内联 experiment 的三个单 case 包装函数

涉及：`dynsteer/experiment/runner.py`。

`run_default_case()`、`run_replay_case()`、`run_evaluate_case()` 各自只有一个生产调用者 `_run_*_case_entry()`，主要工作只是 `_prepare_for_run_case()` 后转调 outputs/evaluator。将各自函数体内联到对应 entry，保留 `_prepare_for_run_case()` 这一处真正共享的准备逻辑，删除三个无外部消费者的 public wrapper。

删除只循环检查 `spec is not None` 的 `validate_experiment_matrix()` 及调用；spec 刚由 `ExperimentRunSpec` 构造并完成边界校验，该循环没有新增不变量。

#### 5.5.2 收敛 settlement 写入入口

`record_finish_settlement()` 只调用 `_record_settlement()`。将后者改名为唯一的 `record_settlement()`，checkpoint 和 evaluator finish 都调用它，删除中转函数。该函数继续唯一负责：清空 scoring context cache、追加 settlement、更新 matched index。

#### 5.5.3 删除其他纯转发私有方法

- `BaseJudge._target_dimensions()`：调用点直接使用 `validated_target_dimensions()`；
- `BaseBenchmarkHarness._project_root()`：唯一调用点直接使用 `Path(__file__).resolve().parents[2]`；
- `stage.resolve._empty_milestone_graph()`：在唯一调用点直接读取已保证存在的 graph；
- `_finish_stage_goal()`、`_whole_trajectory_stage_goal()`：唯一调用点直接从语言映射取值；
- replay 中删除创建后从未使用的 `raw_output_dir` 及目录写入。

不删除 `_render_goal_template()`、`_write_output_payloads()`、`trajectory_to_json()` 等具有多调用点或明确序列化边界职责的函数。

#### 5.5.4 删除恒真实验方法分支与默认权重包装器

涉及：

- `dynsteer/experiment/runner.py`
- `dynsteer/config.py`
- `dynsteer/evaluate/evaluator.py`

`_METHODS_NEED_DEFAULT` 当前逐项列出 `ExperimentMethod` 的全部六个成员，因此 `if spec.method in _METHODS_NEED_DEFAULT` 对所有可构造 spec 恒真。删除该集合和 if/else 外壳，直接执行 DEFAULT 准备，再按 `DEFAULT`、`DYNSTEER_EVALUATE`、其余 replay variants 分派；未知方法应在 experiment config 转成 enum 时失败，不在 runner 保留不可达 else。

`default_dynamic_weight_config()` 只有一个调用点，函数体只是 `DynamicWeightConfig(alpha=1.0, beta=1.0)`，而 dataclass 自身默认值已经完全相同。删除函数及 import，evaluator 直接使用 `weight_config or DynamicWeightConfig()`。不删除 `initial_evaluation_policy()`：它是 dataclass `default_factory`，负责为每个 state 创建独立的 dimension-level dict，内联为 lambda 反而更难读。

### 5.6 主题 F：简化 frontier/matching 热路径

涉及：

- `dynsteer/evaluate/matching/frontier.py`
- `dynsteer/evaluate/matching/milestone.py`

修改内容：

- `initialize_milestone_frontier()` 用一份 predecessor count 字典直接生成 ready IDs，不分两次 graph 循环；
- `advance_milestone_frontier()` 信任“只能匹配 ready milestone”的上游不变量，直接 `ready_ids.remove()`；删除 `_remove_id()`、`max(count - 1, 0)`、不存在 successor 的 `.get(..., ())` 和 `_insert_id_by_order()` 中不可能的重复 ID 保护；
- `analyze_milestone_step()` 不把 `closure.steps` 从 tuple 复制成 list，所有 helper 接收 `Sequence[TrajectoryStep]`；
- ready/blocked 集合由 frontier 保证未匹配，删除每个候选上的 `milestone_id in matched_ids` 重复检查；
- predecessor ID 必须存在于 topology，删除 `_build_predecessor_diagnostics()` 的 `missing_node` 容错分支并直接索引；
- `milestone_scoring_step()` 是内部热路径，删除对已验证 milestone 和非空 closure 的重复校验。

这些改动让不变量错误尽早以 KeyError/ValueError 暴露，不再用默认空值把损坏状态伪装成“没有候选”。

### 5.7 主题 G：降低 stage goal materialization 与 compiler 临时分配

#### 5.7.1 materialize 只替换模板实际出现的 placeholder

当前对每个 stage goal 遍历所有 constraint replacement，复杂度为 O(stage × constraint)。复用现有 placeholder 正则，对每个模板只解析实际出现的 placeholder 并从 replacement map O(1) 查询，未知 placeholder 保持原文并由边界 validator 报错。不得把多层循环压成难读的一行推导式。

#### 5.7.2 compiler 的 pair order 使用单次累计

`_consistent_reduced_edges()` 当前对每个 retained pair 再扫描全部 path 并创建 `containing` 临时列表。改为遍历每条 path 时累计 pair 的方向 bitmask；出现双向即标记冲突，只为单向 pair 生成 edge。这样避免 O(pair × path) 次成员查找和大量短生命周期列表。

`_transitive_reduction()` 保留可读的 DAG reduction，不为了少几行引入第三方图依赖。该项以降低 CPU/临时内存为目标，允许净行数接近 0。

### 5.8 主题 H：精简最新成本/时间统计链路

涉及：

- `dynsteer/experiment/runner.py`
- `main.py`
- `dynsteer/experiment/metrics.py`
- `dynsteer/metrics.py`

#### 5.8.1 静态适配分组从 O(spec²) 改为单次分桶

当前 `run_experiment()` 对每个尚未出现的 key 都执行一次：

```python
candidates = [item for item in specs if _static_adaptation_key(item) == key]
```

先用一次循环建立 `dict[key, list[ExperimentRunSpec]]`，随后每个 bucket 只选择一次非 DEFAULT representative 并执行一次静态适配，复杂度由 O(spec²) 降为 O(spec)。

`main._adapt_only_experiment()` 又实现了一套“按静态身份分组、优先选择非 DEFAULT representative、合并 case IDs”的流程。抽取一个不执行 I/O 的共享分组函数到 `experiment/config.py` 或 `experiment/runner.py` 的公开准备边界，main 与 runner 共同调用；函数返回稳定顺序的 bucket/representative，禁止 main 再维护第二套分组规则。

`_static_adaptation_key()` 不再对同一个 `MilestoneGenerationConfig` 先 `asdict()`、再 `json_safe()`、再 JSON 字符串化；直接使用显式不可变字段与规范化 generator JSON 组成 tuple。

#### 5.8.2 成本 pair 只生成一次最终 delta

当前 `_paired_cost_row()` 先调用 `_cost_delta()` 生成不含 adaptation 的 overhead/pipeline delta；`_allocate_adaptation()` 随后写入 adaptation allocation 并由 `_apply_adaptation_to_deltas()` 部分重算。目标流程改为：

```text
构造 default/dynsteer 基础成本
  -> 按 artifact 分配 adaptation
  -> finalize_cost_row() 一次写入 pipeline totals 与两类 delta
  -> serializer/aggregator 只读最终 row
```

adaptation 不可用时也走同一个 finalize 函数，以 `None` 明确传播，不在 `aggregate_cost()` 主循环手工覆盖六个嵌套字段。删除 `_both_numbers()`；新增一个返回已解析差值的 `_numeric_delta(left, right)`，每个输入只调用一次 `_number()`。

#### 5.8.3 内部自产成本 row 不重复类型校验

成本 row、allocation 和 ledger 都由本模块创建。边界只在 `ExperimentCaseResult`/summary JSON 进入时校验一次；内部 helper 使用固定键直接读取，不在每层重复 `isinstance(..., dict)` 和 `.get()` 兜底。

`_aggregate_rows()`、`_component_totals()`、`_sum_ledger()` 统一把 `_number()` 的返回值加入集合，不再“调用 `_number()` 过滤后继续保存和求和未经转换的原值”。这既删除重复调用，也保证 bool/NaN/inf 不会混入合计。

#### 5.8.4 runtime metrics 复用已计算的统计

`build_runtime_metrics()` 当前已经取得 `execution_records`，随后 `_execution_timing_available(trajectory)` 又重新提取并校验一次；`_unattributed_execution_seconds(trajectory)` 也调用两次。改为在函数开头各计算一次局部值并复用。

trajectory token 的 `total/value_count/available/coverage` 在 `build_runtime_metrics()` 与 `prefix_trajectory_cost()` 各实现一遍。抽取一个接收 `Sequence[TrajectoryStep]` 的纯聚合 helper，两处只负责输出字段命名；不得为同一组 step 重建多份 token 列表。

`build_replay_timing_metrics()` 当前对同一 source trajectory 分别请求 prefix、full、batch count，`prefix_execution_timing()` 每次又排序 steps、提取 records、验证 schema、建立 record-step set。改为一个内部 `_execution_timing_index(trajectory)` 在入口完成一次排序和一次 record 校验，返回只读的 step 序列、step-to-batch 映射、前缀累计 latency 与完整 timing；prefix/full/available 都从该索引 O(1) 或 O(log n) 读取。该索引只存在于单次 metrics 构造中，不缓存到 `Trajectory`，避免第二个可写状态源。

`append_execution_timing()` 的 `allocated` 列表只用于验证 `divmod(latency_ms, step_count)` 分配总和，而该公式数学上必然精确等于原 latency。直接在循环中写入 `step.cost.latency_ms`，删除列表和恒真断言。保留落盘 timing record 的审计字段及历史 schema 校验；`first_step_index`、`last_step_index` 等是否从输出契约删除不在本轮擅自决定。

新增测试：

- 1/10/100 个 spec 的静态适配调用次数必须等于唯一 static key 数量；
- adaptation 可用/不可用、cache hit/本次生成、live/virtual/no-stop 的 pair delta；
- bool、NaN、inf 和部分 token 缺失不会进入 totals；
- 改造前后 `metrics.json`、`costs.json` golden payload 完全一致。

### 5.9 主题 I：让早停只有一个状态源和一个阶段决策入口

涉及：

- `dynsteer/evaluate/policy.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/harness/model.py`
- `dynsteer/experiment/model.py`
- `dynsteer/experiment/config.py`
- `dynsteer/harness/config.py`

当前 `_evaluate_stage()` 调 `update_evaluation_policy()` 时已经按 structural failure、fatal minefield、低 stage score 生成 termination；`evaluate_checkpoint()` 随后又调 `should_stop_after_stage()` 判断相同事实。两套代码的 termination code/detail 还不同，使同一结果可能因先命中哪一层而产生不同输出。目标结构为：

```text
stage result
  -> update_evaluation_policy()：只计算下一阶段 judge level，不做停止判断
  -> termination_after_stage()：唯一判断 structural failure / fatal minefield / low score
  -> RuntimeEvaluationDecision
```

具体修改：

1. `update_evaluation_policy()` 返回 `EvaluationPolicyState`，删除 `allow_stop` 参数和 `EvaluationTerminationState` 返回值；删除从未有生产代码设置的 `metadata["missing_required_milestone"]` 分支。
2. 将 `should_stop_after_stage()` 收敛为唯一的 `termination_after_stage()`，只读取 `StageEvaluationResult` 已有的 `structural_failure`、`fatal_minefield_score`、`stage_score` 和 thresholds，不再从 `RuntimeEvaluationState.minefield_matches` 重建第二份事实。
3. raw step 上的 fatal minefield 必须在 milestone 闭包前立即停止，因此保留 `termination_after_minefield()` 这一不同时间点的入口；它和阶段终止共享同一个 failure-detail 构造 helper，不复制 message/code 拼装。
4. 删除 `HarnessRunConfig.stop_on_stage_failure`、`stop_on_minefield` 两个重复 bool，实验与直接运行统一以 `EvaluationStrategyConfig.policy_stop` 表示是否允许策略早停。调用链显式传递 active strategy，不再把一个 bool 复制到三处对象。
5. evaluator 的 live/replay 只消费 `RuntimeEvaluationDecision.termination`；删除外层再次检查并写入 `policy_stop_suppressed` 的重复分支。静态消融在构造 strategy 时把 `policy_stop=False`，而不是生成终止后再“压制”。

测试覆盖 fatal minefield、结构失败、低分、通过、`policy_stop=False` 和 replay virtual stop，并断言每种输入只有一个稳定 termination code/detail。

### 5.10 主题 J：删除动态权重中的数学空操作与无意义容错

涉及：`dynsteer/evaluate/weights.py` 与对应测试。

多个 task type 的初始权重当前先累加，再把每个维度除以 `valid_count`，最后调用 `normalize_weights()`。因为归一化满足：

```text
normalize(x / c) = (x / c) / Σ(x / c) = x / Σx = normalize(x)
```

所以 `valid_count`、递增和最终逐维除法对任何 `c > 0` 都没有效果。`TaskCase.task_types` 在适配/反序列化边界已经是 `TaskType` enum，`TASK_TYPE_WEIGHTS` 覆盖 enum 值；删除循环中的 `.get()` unknown fallback 和 `valid_count == 0` 分支，空 task types 只在入口选默认类型一次。最终流程就是“按 task type 直接索引并累加 -> normalize 一次”。

`update_weights()` 保留指数更新和极小正数下限：前者是动态注意力算法，后者避免权重为零后永远无法恢复，不属于冗余。测试固定单类型、多类型顺序交换、空类型和每轮权重和为 1。

### 5.11 冗余判定边界：确定删除、保留、待业务决策

| 分类 | 逻辑 | 结论与理由 |
| --- | --- | --- |
| 完全冗余 | JudgeCache 及 telemetry | 与真实多轮采样目标冲突，删除 |
| 完全冗余 | forced-standard / stage `semantic_review` 死链 | 无生产实参，实际语义复判走另一条链，删除 |
| 完全冗余 | Judge payload 二至三次校验 | 外部值转 typed payload 后不再产生新不变量，删除 |
| 完全冗余 | 双阶段早停和三个 stop bool | 同一事实、同一开关的重复表达，收敛为一个入口 |
| 完全冗余 | `missing_required_milestone` 早停分支 | 生产代码从未设置该 metadata，删除 |
| 完全冗余 | 初始权重除以 `valid_count` 后再 normalize | 数学上恒等，删除 |
| 完全冗余 | timing 重复排序/扫描、allocation 恒真断言 | 不改变输出，合并或删除 |
| 完全冗余 | 进度 `line_writer`/`max_visible_bars`/finished/event kinds | 无生产读取者或生产者，删除整条死设计 |
| 完全冗余 | `load_judge_config_from_env()` | 同一环境随后由 llm factory 再解析一次，删除前置 metadata 复制 |
| 完全冗余 | `_METHODS_NEED_DEFAULT` 恒真分支 | 集合覆盖当前全部 enum，删除集合与 if/else 外壳 |
| 完全冗余 | `ExperimentCaseResult.to_dict()` identity 分支 | 无生产调用者，索引层级已表达 identity |
| 完全冗余 | `validate_loaded_task_cases()` | loader 已按输入顺序逐项校验 ID，返回后整列复核不增加约束 |
| 必须修复后保留 | runtime `evaluated_minefield_sources` | 当前字段放错到 report；0 分 source 也需要去重，移动而非删除 |
| 必须保留 | minefield source fingerprint | 防止同一 source 在每条 raw step 重复判定 |
| 必须保留 | AgentStepTracker closure | 工具调用与结果可能跨多条 raw step，不能逐行替代 |
| 必须保留 | milestone DAG frontier | 表达先后依赖，不是普通列表的重复设计 |
| 待业务决策 | blocked milestone 扫描 | 唯一作用是发现越过前驱完成后继并提供诊断；若不需要越序诊断/早停，可整链删除 |
| 待业务决策 | 每个 pending milestone 各生成一个 0 分 stage | 当前不会再生成 finish，不是双重记零；需决定是否按未完成节点数进入平均分 |
| 待业务决策 | fatal minefield finish failure + final penalty | 当前可能双重惩罚危险行为；需先确定 penalty 是替代失败还是追加惩罚 |
| 待业务决策 | live/replay 的 `finish_on_termination` 不一致 | 全 milestone 已匹配但发生终止时，两种模式可能使用不同 stage 分母；需统一 finish 语义 |
| 待算法决策 | Judge status 取最严重值而不是多数票 | 是保守聚合规则而非重复代码；改变会影响评分，不能借精简暗改 |

上述待业务/算法决策项不能仅凭代码形态认定为死逻辑。本轮保留它们并用 golden tests 锁定当前行为；它们不是本次一次性精简的遗漏项，而是明确排除在本次范围外的业务决策。

## 6. 单轮实施顺序、完整性检查与一次性验收

本节描述的是**同一轮代码修改内部的依赖顺序**，不是多轮提交或多轮精简。实施者必须在当前工作树一次完成第 5 节全部主题，期间不得提交中间版本、不得先交付部分精简结果，也不得把未完成项目留到下一轮。

### 6.1 同一轮内的操作顺序

1. **建立基线**：复制现有 2 个成本统计测试的 golden 输出；新增 topology、snapshot、minefield、Judge、scorer、progress、experiment index、timing、weights、termination 的 fixture/test 文件。
2. **先修复依赖性缺陷**：完成执行索引 `P0-01`～`P0-04`，使 pyflakes 和新增基线测试在当前工作树中可运行；不单独提交。
3. **一次完成所有生产代码精简**：按照第 `5.1 → 5.2 → 5.3 → 5.4 → 5.5 → 5.6 → 5.7 → 5.8 → 5.9 → 5.10` 的主题顺序编辑全部文件，覆盖基线修复、Judge、死代码、评分器、校验边界、中转层、热路径、编译器、成本/timing、早停和权重。该顺序只用于避免未定义引用和便于人工检查，不产生中间验收点；每个主题的所有子项都必须完成。
4. **一次完成全仓引用清理**：对被删除的类型、字段、函数、参数、导出和 telemetry 做全仓 `rg`；发现任何残留就在本轮立即修正，不允许记录为“后续处理”。
5. **统一验收**：所有生产代码修改完成后，连续执行 6.2 的全部命令；任何失败都在本轮修复并重新执行完整命令集，不能以“下一轮再修”为结论。
6. **统一复算**：用第 2 节同一 AST 口径计算最终有效行数，输出逐文件增减和总净减少；不使用中间状态数字，不提交 git。

### 6.2 一次性验收命令

```powershell
python -m pyflakes main.py dynsteer display
python -m pytest -q
python -m pytest --cov=dynsteer.evaluate --cov=dynsteer.adapter --cov=dynsteer.harness --cov-report=term-missing
```

### 6.3 精简项目零遗漏清单

在验收前逐项打勾，任何一项未完成即视为本轮未完成：

- [ ] P0-01～P0-04：diagnostics topology、未使用局部变量、缓存 graph enrich、snapshot 排序、runtime minefield fingerprint。
- [ ] J-01～J-03：删除 `JudgeCache` 模块及全部引用；每个 Standard/Expensive pass 真实调用；删除 cache key/context/telemetry；统一 Judge env reader 和默认 temperature。
- [ ] 5.1.7：删除 `force_standard_dimensions`、stage `semantic_review` 死链及 `_semantic_only_hard_failure`，保留 milestone 阶段的真实 `semantic_message_review`。
- [ ] D-01：删除 `BenchmarkCase`，改为 `list_case_ids()`，删除 `run_all` 和 `validate_loaded_task_cases`。
- [ ] D-02：删除 `HarnessAdvanceResult.reason`、`root_ids`、report fingerprint、ToolSandbox/AgentCompass 无效状态、`HarnessCaseTask.case_id`、`AgentCompassRunData.steps`，并完成所有构造调用点迁移。
- [ ] D-03～D-06：路径对象收敛、无消费者 `__all__`、progress 死参数/事件/状态、`ExperimentCaseResult.to_dict()` identity 分支。
- [ ] S-01～S-02：唯一 scorer 主循环、Trajectory snapshots/context 参数收敛、topology/context 复用、配置/graph/stage/result/Judge payload 单边界校验。
- [ ] S-03：experiment 三个单 case wrapper、settlement wrapper、BaseJudge/BaseHarness/stage 纯转发函数全部按清单删除或内联。
- [ ] S-04、T-01：静态适配单次分桶、cost finalize、内部数值校验收敛、timing 单索引和 allocation 恒真断言删除。
- [ ] E-01：policy 只计算 next level；阶段终止只有 `termination_after_stage`；删除重复 stop bool 和外层二次 termination 判断。
- [ ] 5.6：frontier predecessor count、ready/blocked 集合、closure `Sequence`、candidate 重复检查和 predecessor diagnostics 容错分支按上文全部收敛。
- [ ] W-01、5.7：动态权重数学空操作、stage placeholder 全量扫描、compiler pair 临时列表全部完成；不得为降低行数牺牲算法复杂度或可读性。

### 6.4 一次性验收条件

验收条件：

- changed core modules 的语句覆盖率不低于 80%；
- default/live/replay golden JSON 除明确删除的 Judge cache telemetry/死字段外保持契约一致；Judge 分数用可重复 fake response 序列验证新的真实多轮聚合结果，不要求与错误缓存行为一致；
- generic scorer 与 ToolSandbox scorer 的 golden score/status/evidence 顺序一致；
- Judge fake LLM 实际调用数严格等于 passes 配置，agreement 只基于真实独立响应；
- minefield fingerprint 位于 runtime state，同一 0 分 source 不重复评分且 report 不泄漏该索引；
- 进度 queue 只传递 advanced event；串行/并行执行的 case 顺序、step count 和进度条关闭行为保持一致；
- 无 pyflakes 报告、无语法错误；
- `rg` 不再命中已删除的 `JudgeCache`、`judge_cache_metrics`、四个 cache telemetry 键、`load_judge_config_from_env`、`api_key_configured`、`force_standard_dimensions`、`_semantic_only_hard_failure`、`missing_required_milestone`、`stop_on_stage_failure`、`stop_on_minefield`、`BenchmarkCase`、`validate_loaded_task_cases`、`HarnessAdvanceResult.reason`、`root_ids`、report 级 `evaluated_minefield_sources`、`DEFAULT_VISIBLE_PROGRESS_BARS`、`max_visible_bars`、`line_writer`、`CaseProgressState.finished`、`_METHODS_NEED_DEFAULT`、`ExperimentCaseResult.to_dict`、`record_finish_settlement`、三个 experiment 单 case wrapper；
- `rg "cache_hit"` 只允许命中 TaskCase 静态适配缓存，不得残留 Judge 响应缓存；
- 仍保留关键中文日志、核心函数中文 docstring 和关键流程注释；
- 不修改 `.gitignore`，不提交 git，不删除任何已有 `docs/constraints` 或 `docs/plans` 文档；本轮只允许形成一份完整工作树修改结果。

## 7. 有效行数缩减预算

以下是按当前 14,583 行基线，对“新增代码减去删除代码”的净估算；测试不计入。

| 精简主题（非实施批次） | 预计净减少有效行 |
| --- | ---: |
| P0 修复后利用 graph 不变量删除 fallback（含必要修复新增代码） | 8～16 |
| `BenchmarkCase` 模型及对象构造层删除 | 32～42 |
| 其余死字段、AgentCompass 重复状态、结果路径收敛 | 28～42 |
| 无消费者 `__all__` 删除 | 18～22 |
| 通用/ToolSandbox scorer 唯一算法与重复参数删除 | 32～48 |
| topology/context 索引复用与线性扫描删除 | 15～25 |
| 配置、stage、AgentCompass、report 重复校验删除 | 38～55 |
| experiment/settlement/base/stage 中转函数删除 | 45～60 |
| frontier/matching 热路径收敛 | 12～20 |
| stage materialization/compiler 临时分配优化 | -2～8 |
| 最新成本统计分组与 delta/数值聚合收敛 | 35～60 |
| 删除 JudgeCache、cache key/context/telemetry、重复环境配置 reader 并改为直接真实调用 | 75～110 |
| 删除 forced-standard 不可达语义解除链 | 35～50 |
| Judge payload 单次 typed 校验 | 12～20 |
| 早停唯一状态源与重复 stop 配置删除 | 18～28 |
| timing 单次索引、恒真断言与动态权重空操作删除（不重复计算 5.8 已估部分） | 8～15 |
| progress 死参数、死 event kinds、write-only finished 状态删除 | 12～20 |
| experiment 恒真方法分支、死 identity serializer、默认权重包装器删除 | 18～28 |
| **合计** | **439～669** |

执行后的预计有效行数：

- 保守值：`14,583 - 439 = 14,144`；
- 中位目标：约减少 **550 行**，降至 **14,033 行**；
- 理想值：`14,583 - 669 = 13,914`。

因此本方案的验收目标设为：除“删除缓存、恢复真实多轮 Judge”这一项按用户决定有意修正算法行为外，在不改变其他业务行为、不删必要注释和日志的前提下，**净减少至少 430 行有效生产代码**；预期合理落点为 **500～610 行**。最终数字必须在实现完成后使用第 2 节同一 AST 口径重算，不能用 `git diff --stat` 代替。

## 8. 明确保留的设计

以下内容经核查不是本轮冗余：

- `get_log_buffer()`、`clear_log_buffer()`：虽然仓库内没有调用，但属于约束明确要求的日志缓冲查询边界；
- AgentCompass 与 ToolSandbox 的可选依赖延迟 import：位于专门第三方依赖边界，符合约束，不迁移为包级硬依赖；
- 两个 AgentCompass adapter 的三行 `adapt_task_case()`：抽象接口要求具体实现，新增 mixin/配置对象的体量和认知成本高于三行重复；共享查找逻辑已经在 `adapt_record_case()` 中；
- default/live/replay 三种落盘 schema 中重复出现的 termination/metadata 字段：这是不同文件的输出契约，不是可删除的内存状态；统一由 serializer 生成即可；
- `RuntimeEvaluationState.settlements` 与 `stage_reports`：二者分别表达执行结算节点和评估报告，直接合并会改变 JSON 契约和 pending stage 语义。本轮先统一写入入口，不进行高风险单列表迁移；
- ToolSandbox 对 `pyo3_runtime.PanicException` 的特殊捕获、密钥过滤、路径清理和外部 JSON 校验：属于第三方边界/安全边界，不作为冗余删除；
- `standard_passes`、`expensive_passes` 与 `agreement_confidence()`：它们在删除缓存后终于分别表达真实样本数和真实样本一致程度，属于核心算法，必须保留；
- evaluator 的 `_initial_runtime_state()`、`_evaluate_closed_agent_step()`、`_evaluate_checkpoint_for_strategy()`、`_build_runtime_metrics()`：虽然部分函数体最终转调一层，但分别被 live/replay 多次复用并绑定 evaluator 持有的 judge/strategy/recorder，不属于无意义单调用包装；
- `RuntimeEvaluationState.final_milestone_diagnostics` 缓存：pending stage 与 raw summary 都消费同一份较昂贵诊断，缓存避免第二次聚合 match attempts；
- Base harness 的 `metrics_from_session()`、`initial_state_from_session()` 等默认空 hook：不同 benchmark 只实现自己具备的能力，强制所有子类重复空实现会增加代码；只有所有具体 harness 均覆盖的 `default_result_from_session()` 改为 abstract；
- Judge status 采用最严重状态、分数采用均值的聚合规则：这是保守算法选择，不是两套重复聚合；
- 为可读性拆分的多行参数、核心中文 docstring、关键步骤注释。

## 附录A. 项目中没有把握实现的模块部分

1. **AgentCompass 真机集成**：当前环境未确认已安装完整 benchmark/harness/environment 依赖，无法保证在本地运行 SWE-bench Pro 与 SkillsBench 的真实任务。方案可通过投影 fixture 固定 detail/result 契约，但最终仍需带依赖环境的单 case smoke test。
2. **ToolSandbox 原生 scorer parity**：其 snapshot constraint、Polars schema 和 pyo3 panic 行为依赖外部 ToolSandbox 版本。通用聚合 hook 可以静态实现，但必须使用真实 scenario 做至少一个 state、message、guardrail、minefield 用例，否则不能宣称完全等价。
3. **外部消费者是否 wildcard import**：仓库内没有 `from dynsteer... import *`，约束又默认不维护旧接口，因此方案删除 `__all__`。若仓库外有未声明消费者，它们需要迁移为显式 import。
4. **输出路径对象的仓库外依赖**：仓库内可以证明 `run_dir`/`raw_summary_path` 等为可推导字段；若外部脚本直接读取这些 dataclass 属性，需要同步迁移。落盘 JSON 路径键会保持不变。
5. **Milestone dominator/finish anchor 的领域正确性**：本轮只让 topology 成为唯一来源，不重写 `_immediate_dominators()` 的数学语义。缺少复杂 DAG 真实样本时，不把拓扑算法重构混入本次精简；这不是遗漏，而是明确的业务风险边界。
6. **成本统计口径的外部分析依赖**：`ba21f04` 新增的 `metrics.json`/`costs.json` 可能已有仓库外分析脚本消费。本方案保持所有落盘字段和值，仅删除内部中间 delta 和重复分组；若外部消费者依赖数组顺序，也必须加入 golden fixture。
7. **Judge cache telemetry 的仓库外消费者**：本方案按用户决定删除 `llm_unique_prompt_count`、`llm_cache_hit_count`、`llm_duplicate_prompt_avoided_count`、`semantic_review_cache_hit_count`。仓库内没有消费者；若仓库外脚本读取这些键，应迁移为 `llm_call_count` 和 `llm_calls`，不能为兼容旧脚本保留已经失真的指标。
8. **termination 后是否执行 finish**：当前 live 与 replay 的 `finish_on_termination` 不一致。缺少产品定义时，本方案不擅自选择其中一方；实现精简前需要明确“finish 是所有 milestone 完成后的必做终态核查”，还是“任何策略终止都跳过 finish”。

上述不确定项不阻塞高置信死代码和重复校验删除，但会阻塞“全 benchmark 行为完全等价”的最终结论；实施报告必须分别列出已运行与未运行的集成用例。

## 附录B. 完成标志

- P0-01～P0-04 所列基线缺陷全部修复；
- 计划列出的死类型、死字段、死导出已删除；
- milestone 评分主循环只存在一份；
- graph/context/config/schema 各自只有一个校验或派生边界；
- minefield source fingerprint 只存在于 runtime state，report 不再持有错位执行索引；
- 每个 Judge pass 都产生一次真实 LLM 调用，每个响应只校验一次，代码中不存在 Judge 响应缓存；
- Judge routing 只计算 next policy，stage termination 只由一个入口决定；
- progress queue 不再含无生产者的事件种类，experiment runner 不再含恒真方法分支；
- 所有测试、pyflakes 和覆盖率门槛通过；
- 有效生产代码净减少至少 430 行，最终统计脚本和逐文件结果随实施报告一并保存；
- 没有通过合并可读行、删除必要日志/注释或牺牲算法复杂度凑行数。
