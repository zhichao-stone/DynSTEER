# ToolSandbox 运行结果评分可信度代码修复方案

生成日期：2026-07-20  
依据报告：`docs/plans/analysis/2026-07-20-toolsandbox-run-results-audit-report.md`  
适用范围：ToolSandbox 适配、运行期评分、语义复判、finish 展示与运行观测字段。  
核心目标：优先修复可能导致 false negative 或误导性展示的代码问题，使本轮报告中暴露的评分风险可被最小回归固定。

## 1. 背景与问题判断

本轮 10 个 ToolSandbox case 的运行产物完整，日志与汇总指标正常。但报告指出评分可信度存在局部风险，主要集中在五类问题：

| 优先级 | 问题 | 代表 case | 初步代码判断 |
|---|---|---|---|
| P0 | initial reference guardrail 疑似 false negative | `add_contact_with_name_and_phone_number_3_distraction_tools` | 运行期评分用的是离线 adapted case 中的 `initial_state`，而 ToolSandbox 场景含动态 timestamp；同时离线 initial rows 缺少 `sandbox_message_index`，与运行期 snapshot schema 不一致 |
| P1 | `emit_message` 语义等价候选未真正强制 LLM 复判 | `turn_on_cellular_low_battery_mode` | `analyze_milestone_step()` 已标记 `llm_semantic_review` 候选，但 `_evaluate_stage()` 仍按当前 policy 选择维度，初始 policy 下不会强制调用 standard judge |
| P1 | 无 milestone graph 自然结束时可能默认 finish pass | `find_days_till_holiday_insufficient_information` 的未来同类扩展 | 当前 fatal minefield case 正常提前终止；但若未来空 milestone 且无 minefield 命中，`build_finish_verification()` 可能给出空图 pass |
| P2 | 策略提前终止时展示层 finish 卡片显示 `not_started` 易误解 | fatal minefield case | `display/build.py` 未把 raw_summary 中的 `termination_code` 透传到 scenario，`display/index.html` 只能构造空 finish report |
| P2 | `trajectory.final_state=null` 与 `trajectory_total_tokens=0` 缺少明确可用性口径 | 全部本轮 case | ToolSandbox harness 未实现 `final_state_from_session()`；trajectory step cost 当前未采集，汇总只能显示 0 |

本方案只修代码路径，不改报告结论，不删除 `docs/constraints` 与 `docs/plans` 下已有文档，不主动执行 git commit。

## 2. 修复目标

1. ToolSandbox 运行期评分中的 `reference_milestone_node_index=-1` 必须引用当前 session 的真实初始状态，而不是过期的离线 adapted JSON。
2. ToolSandbox initial snapshot、runtime snapshots、final_state 使用一致的 namespace schema，特别是 `sandbox_message_index`、动态 timestamp、空值列类型。
3. 对 `semantic_equivalent` 的 `emit_message` WARN 候选，若 standard judge 可用，必须强制触发最小维度复判，而不是继续只用 cheap 分数结算。
4. 空 milestone graph 在没有 minefield 或 terminal 约束时不能自然得到 finish pass；要么明确 invalid，要么进入后续 whole-trajectory fallback。
5. 展示层在策略提前终止且 finish 未结算时，应显示终止原因和 minefield 摘要，不再只显示 `__start__->__finish__:not_started`。
6. 运行输出需要明确 final_state 和 trajectory cost 字段是否可用，避免审计时把 `null` 或 `0` 误读为真实值。

## 3. 代码架构调整

### 3.1 运行期初始状态统一

调整后状态来源如下：

```text
ToolSandboxHarness.start_case()
  -> 生成当前 session context
  -> 提取 runtime initial_state
  -> DynSTEEREvaluator.evaluate() 覆盖 task_case.initial_state
  -> scoring_context() 将 runtime initial_state 注入 matched_snapshots["initial"]
  -> ToolSandboxConstraintScorer._reference_dataframe() 读取同一 runtime initial snapshot
```

计划涉及文件：

- `dynsteer/adapter/base.py`
- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/adapter/toolsandbox/utils/state.py`
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/evaluate/runtime.py`
- `dynsteer/adapter/toolsandbox/scorer.py`
- `dynsteer/model.py`

核心设计：

1. 在 `BaseBenchmarkHarness` 增加默认方法 `initial_state_from_session(session)`，默认返回 `None`。
2. 在 `ToolSandboxHarness` 中实现 `initial_state_from_session()` 与 `final_state_from_session()`。
3. 在 `ToolSandboxSession` 中增加 `initial_state: JsonObject | None = None` 字段，避免运行中 context 变化后初始状态被重新读取成当前状态。
4. `dynsteer/adapter/toolsandbox/utils/state.py` 新增共享函数 `state_from_context(...)`，由 `initial_state_from_context()`、`snapshots_from_context()`、`final_state_from_session()` 复用，确保 namespace、`drop_sandbox_message_index=False` 与 JSON 归一化一致。
5. `DynSTEEREvaluator.evaluate()` 在 `start_case()` 后立即读取 runtime initial state，若存在则覆盖当前内存中的 `task_case.initial_state`，并在 `task_case.metadata` 记录来源。

### 3.2 ToolSandbox guardrail 评分诊断增强

`ToolSandboxConstraintScorer` 当前只输出 `ToolSandbox custom constraint ... 得分 ...`，不足以区分“真实状态破坏”与“reference schema 错误”。计划补充诊断 metadata，但不改变评分公式：

1. `_reference_dataframe()` 返回 reference dataframe 时，同时在 evidence 或 metadata 中记录 reference snapshot id、namespace、row_count、columns。
2. `_custom_constraint_failure_score()` 和正常 `ConstraintScore.evidence` 中加入 compact reference 摘要。
3. 不用阈值绕过 guardrail fail；P0 必须通过修正 reference source 解决。

### 3.3 `emit_message` 语义复判强制触发

现有路径已经能识别 WARN 语义候选：

```text
analyze_milestone_step()
  -> _is_llm_semantic_review_candidate()
  -> attempt_detail["llm_semantic_review"] = {"status": "candidate"}
```

需要补齐的是结算阶段的强制复判。计划调整：

1. `MilestoneStepAnalysis` 增加 `requires_semantic_review: bool = False`。
2. `_analyze_ready_candidates()` 选中 WARN semantic candidate 时返回该标记。
3. `evaluate_agent_step()` 在 `requires_semantic_review=True` 且 `standard_judge` 可用时，调用 `evaluate_checkpoint(..., force_standard_dimensions=...)`。
4. `evaluate_checkpoint()` 与 `_evaluate_stage()` 新增内部参数 `force_standard_dimensions: list[Dimension] | None = None`。
5. `_evaluate_stage()` 将强制维度覆盖为 `EvaluationLevel.STANDARD`，最小集合为 `progress` 与 `interaction_quality`，且只保留本阶段 `focus_dimensions` 中实际存在的维度。
6. `stage_result.metadata["semantic_review"]` 记录 candidate cheap score、强制维度、standard judge 状态、最终 settlement 是否接受。

该方案复用现有 `StandardJudge` 与 prompt，不新增只有转发意义的中转函数，也避免为了一个文本约束把整个阶段升级到 expensive。

### 3.4 空 milestone graph 的 finish 语义

短期修复目标是阻止空图自然 pass：

1. `build_finish_verification()` 在 `not graph.nodes` 时单独分支。
2. 若 `state.fatal_minefield=True`，保持 fatal fail。
3. 若无 milestone、无 minefield、无 terminal checks，则返回 `StageStatus.INVALID`、`score=0.0`，evidence 说明“空 milestone graph 未配置 whole-trajectory fallback，不能默认通过”。
4. `overall_score()` 不需要为这个分支单独改公式，因为 invalid finish report 会把总分压到 0。

长期扩展预留：

1. 新增 whole-trajectory fallback stage 之前，不把空图视作“全轨迹语义评估已完成”。
2. 若后续确实需要支持空 milestone 的 safety/refusal 评估，应在 `TaskCase` 层提供明确 `policy_constraints` 或 synthetic milestone，而不是复用空 finish stage。

### 3.5 展示层终止状态

计划调整 `display/build.py` 与 `display/index.html`：

1. `_scenario()` 增加 `termination` 字段，透传 `raw_summary.terminated_by_policy`、`termination_code`、`termination_reason`、`termination_detail`。
2. 当 `stage_reports=[]` 且存在策略终止时，为 finish definition 构造 `terminated` 空报告，evidence 中显示 `termination_code` 和 minefield 命中摘要。
3. `normalizeStageStatus()` 支持 `terminated`，fatal minefield 终止按 fail 样式展示。
4. `stageCard()` 对 `metadata.finish_unsettled_due_to_termination=true` 的卡片展示“finish 未结算”，避免误读为尚未评估。

### 3.6 final_state 与运行成本观测

ToolSandbox 运行期状态：

1. `ToolSandboxHarness.final_state_from_session()` 返回当前 context 的 namespace 状态。
2. `trajectory_to_json()` 无需改字段结构，`final_state_present` 将自然变为 true。

成本字段：

1. `build_runtime_metrics()` 保留 `trajectory_total_tokens` 与 `trajectory_total_latency_ms` 的现有数值口径。
2. 新增 `trajectory_cost_available` 与 `trajectory_latency_available`，分别表示是否有任一 step 提供 tokens 或 latency。
3. `display/build.py` 汇总中透传该可用性字段。
4. 如果 ToolSandbox 原生 role 后续暴露 token/latency，再在 `sandbox_rows_to_step_dicts()` 或 `_trajectory_step_from_dict()` 中填充 `StepCost`；本轮不臆造 token。

## 4. 实施步骤

### Step 1. 统一 ToolSandbox 状态提取

1. 在 `dynsteer/adapter/toolsandbox/utils/state.py` 新增 `state_from_context()`。
2. `initial_state_from_context()` 改为调用 `state_from_context()`，并确保非 SANDBOX namespace 带 `sandbox_message_index`。
3. `snapshots_from_context()` 复用相同 namespace 读取逻辑，减少 schema 漂移。
4. 增加中文注释说明为什么 runtime initial state 不能依赖离线 adapted JSON。

### Step 2. 注入 runtime initial state

1. `BaseBenchmarkHarness` 增加 `initial_state_from_session()`。
2. `ToolSandboxHarness.start_case()` 在 system environment 初始化后保存 `session.initial_state`。
3. `ToolSandboxHarness.initial_state_from_session()` 返回 `session.initial_state`。
4. `DynSTEEREvaluator.evaluate()` 在 `session = harness.start_case(...)` 之后覆盖 `task_case.initial_state`。
5. `raw_summary["task_case_snapshot"]` 增加 `runtime_initial_state_source` 或同等字段，便于审计。

### Step 3. 修复 semantic review 结算路径

1. `MilestoneStepAnalysis` 增加 `requires_semantic_review` 字段。
2. `analyze_milestone_step()` 在 WARN semantic candidate 分支设置该字段。
3. `evaluate_agent_step()` 根据该字段向 checkpoint 传递 `force_standard_dimensions`。
4. `evaluate_checkpoint()`、`_evaluate_stage()` 支持强制 standard 维度。
5. `_evaluate_stage()` metadata 记录 semantic review 过程，便于 raw_summary 与 report 诊断。

### Step 4. 修复空 milestone finish

1. `build_finish_verification()` 增加空 graph 显式分支。
2. evidence 与 diagnosis 使用中文结构化说明。
3. 保持 fatal minefield 分支优先级高于空图 invalid 说明。

### Step 5. 改进展示数据与状态标签

1. `display/build.py` 透传 termination 信息。
2. `display/index.html` 支持 `terminated` 状态和 finish 未结算提示。
3. 检查移动端与桌面布局，确保新增终止提示不会撑破阶段卡片。

### Step 6. 补充 final_state 与 cost 可用性

1. `ToolSandboxHarness.final_state_from_session()` 返回当前状态。
2. `build_runtime_metrics()` 新增 cost 可用性字段。
3. `display/build.py` 透传新增字段。
4. 不把不可用 token 填成估算值。

## 5. 测试与验收

### 5.1 单元测试

新增或更新以下测试：

1. `tests/test_toolsandbox_runtime_state.py`
   - fake context 下 `state_from_context()` 输出包含 `sandbox_message_index`。
   - runtime initial state 与 snapshot namespace schema 一致。
   - `scoring_context()` 使用覆盖后的 `task_case.initial_state` 构造 `matched_snapshots["initial"]`。
2. `tests/test_toolsandbox_guardrail_reference.py`
   - 构造动态 timestamp 不同的离线 initial 与 runtime initial，断言 scorer 读取 runtime initial。
   - 对 `add_contact...` 的最小状态片段验证 preserve MESSAGING/SETTING/REMINDER guardrail 不因 schema 缺列全 0。
3. `tests/test_semantic_review_checkpoint.py`
   - WARN semantic message candidate 在 standard judge 可用时会强制调用 standard judge。
   - standard judge 给出高分时 checkpoint 被接受，并在 metadata 中记录 `semantic_review.status=accepted`。
   - standard judge 缺失时仍标记 `skipped_no_standard_judge`，行为与当前逻辑兼容。
4. `tests/test_finish_empty_graph.py`
   - 空 milestone、无 minefield、自然结束时 finish verification 为 invalid/0 分。
   - 空 milestone、fatal minefield 时仍为 fatal fail。
5. `tests/test_display_termination.py`
   - `build_display_data()` 能把 raw_summary termination 透传到 scenario。
   - stage report 为空且存在 termination 时，展示数据可生成 finish 未结算提示。

### 5.2 回归 case

建议最小回归先跑这三个 case：

1. `add_contact_with_name_and_phone_number_3_distraction_tools`
   - 预期：m0 的新增联系人和 preserve-state guardrail 不再因 runtime initial reference 错误全 0。
2. `turn_on_cellular_low_battery_mode`
   - 预期：`llm_semantic_review` 候选在 standard judge 可用时触发复判，最终消息语义等价时不再被 0.8 cheap 阈值卡死。
3. `find_days_till_holiday_insufficient_information`
   - 预期：fatal minefield 仍提前终止，展示层明确显示 termination，而不是让 finish 只呈现 `not_started`。

### 5.3 验收命令

```bash
python -m compileall dynsteer display
uv run pytest
python display/build.py --runs-dir runs --results-dir results --data-dir data --output display/data.js
```

若未配置真实 LLM provider，semantic review 的“强制 standard judge 接受”测试使用 fake judge，不依赖网络或环境变量。

## 6. 风险与处理

1. 覆盖 `task_case.initial_state` 会改变运行期评分引用，但这是对当前 session 的真实状态对齐，不改变离线 adapted case 文件。
2. `initial_state_from_context()` 若新增 `sandbox_message_index`，后续 force adapt 生成的 JSON 会比旧文件多列。该变化是为了与 runtime snapshot schema 一致，应通过测试固定。
3. 强制 standard semantic review 会增加 LLM 调用次数。触发范围限制为 `semantic_equivalent`、cheap WARN、无 missing、非 message hard 约束均通过的候选，避免扩大成本。
4. 空图 invalid 可能让少数历史“无 milestone 且无 minefield”的 case 分数从 1 变为 0。该变化符合报告结论，因为空 finish pass 不是可靠的全轨迹语义评估。
5. `final_state_from_session()` 会增加 trajectory JSON 体积。ToolSandbox namespace 表规模较小，当前风险可控；若后续有大表 benchmark，应增加摘要模式。

## 7. 不采用的方案

1. 不通过降低 guardrail 阈值或忽略 `sandbox_message_index` 来修 P0。根因是 reference source 与 schema 不一致，应先修状态来源。
2. 不为 semantic message 单独新增一套完全独立的 LLM 客户端。当前 `StandardJudge` 已有 prompt、指标记录和异常处理，复用更简洁。
3. 不让空 milestone graph 默认通过 finish。没有 milestone、minefield 或 terminal 约束时，代码没有足够语义证据判断任务完成。
4. 不伪造 trajectory token。没有真实 cost 数据时只增加可用性标记。

## 8. 代码改动清单

| 文件 | 类型 | 改动 |
|---|---|---|
| `dynsteer/model.py` | 修改 | `ToolSandboxSession` 增加 `initial_state`；`MilestoneStepAnalysis` 增加 semantic review 标记 |
| `dynsteer/adapter/base.py` | 修改 | 增加 `initial_state_from_session()` 默认接口 |
| `dynsteer/adapter/toolsandbox/utils/state.py` | 修改 | 新增共享状态提取函数，统一 initial/snapshot/final schema |
| `dynsteer/adapter/toolsandbox/harness.py` | 修改 | 保存 runtime initial state，实现 initial/final state 提取 |
| `dynsteer/evaluate/evaluator.py` | 修改 | start 后注入 runtime initial state，传递 semantic review 强制维度 |
| `dynsteer/evaluate/runtime.py` | 修改 | task snapshot 增加 runtime initial state 来源摘要 |
| `dynsteer/evaluate/matching/milestone.py` | 修改 | 显式返回 `requires_semantic_review` |
| `dynsteer/evaluate/step.py` | 修改 | semantic candidate 传递 `force_standard_dimensions` |
| `dynsteer/evaluate/settlement.py` | 修改 | `_evaluate_stage()` 支持强制 standard 维度并记录 metadata |
| `dynsteer/evaluate/final.py` | 修改 | 空 milestone graph finish invalid 分支 |
| `dynsteer/adapter/toolsandbox/scorer.py` | 修改 | guardrail reference 诊断增强 |
| `dynsteer/metrics.py` | 修改 | 增加 trajectory cost 可用性字段 |
| `display/build.py` | 修改 | 透传 termination 与 cost 可用性字段 |
| `display/index.html` | 修改 | 展示 `terminated` 与 finish 未结算说明 |
| `tests/*` | 新增/修改 | 补充 P0/P1/P2 单元回归 |

## 附录A. 项目中没有把握实现的模块部分

当前最没有把握的是 ToolSandbox 原生 `guardrail_similarity` 对 `sandbox_message_index` 的完整语义。代码证据显示离线 initial state 缺少该列且动态 timestamp 会漂移，足以解释本轮 false negative；但原生 scorer 内部是否还对 dataframe schema、列顺序、`Null` dtype 或动态 timestamp 有其他隐式要求，需要用最小 dataframe 回归进一步确认。

第二个不确定点是 semantic review 强制 standard 后的分数门控。现有 `StandardJudge` 输出的是维度级分数，而 milestone pass 仍由 settlement 的动态权重聚合决定。若 LLM 明确认定消息语义等价，但其他 cheap 维度被过度拉低，仍可能不能达到 pass 阈值。落地时需要用 `turn_on_cellular_low_battery_mode` 回归检查是否只强制 `progress + interaction_quality` 足够；若不足，再考虑把 semantic review 接受结果映射回对应 `emit_message` constraint，而不是继续放大 judge 维度。

第三个不确定点是展示层的 `terminated` 状态视觉归类。fatal minefield 应按 fail 展示，但 ready-frontier no-progress 或普通策略终止不一定是同等严重。首版可以通过 `termination_code` 前缀区分样式，后续再根据更多样例细化。
