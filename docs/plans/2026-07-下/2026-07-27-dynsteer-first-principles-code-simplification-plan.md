# DynSTEER 第一性原理代码简化方案

生成日期：2026-07-27  
方案状态：仅出具方案，不落地修改生产代码。  
约束来源：严格遵循 `docs/constraints/code.md`，不删除 `docs/constraints` 与 `docs/plans` 既有文档。  

## 1. 简化目标

DynSTEER 的本质任务只有一件事：把 benchmark 产生的 Agent 轨迹和状态，转成阶段边界、阶段结果、终止诊断和实验汇总。

因此，当前代码应只保留四类真实复杂度：

1. 外部边界：benchmark、LLM SDK、文件 JSON、终端进度条。
2. 领域模型：trajectory、milestone graph、stage settlement、judge result。
3. 核心算法：ready frontier、milestone/minefield matching、阶段评估、策略终止。
4. 输出契约：report、summary、trajectory、display data、experiment metrics。

其余代码如果只是重复解析、重复拼 payload、重复写文件、重复读取同一字段、只抛未实现错误的占位函数，均应删除或合并。简化不能通过把多行变量压成一行实现；每行最多 200 个非空字符，保持可读性。

## 2. 当前有效行数基线

统计范围：仓库内 `.py` 与 `.sql`，排除 `tests`、`.venv`、`.uv-cache`；当前仓库未发现 `.sql`。  
用户口径：非空行、非日志行、非仅有注释行；不额外剔除 docstring。  

当前全仓非 tests 有效行数为 **13,313 行**。其中 `dynsteer/` 包为 **12,435 行**。

辅助分析时额外剔除模块/类/函数 docstring 后，全仓为 12,534 行，`dynsteer/` 为 11,661 行；后续缩减预估仍以用户口径 13,313 行为准。

| 区域 | 当前有效行数 | 文件数 | 主要问题 |
| --- | ---: | ---: | --- |
| `dynsteer/evaluate` | 4,575 | 17 | runtime/replay 链路重复、诊断 payload 重复、语义匹配 helper 过碎 |
| `dynsteer/adapter` | 2,391 | 23 | ToolSandbox schema/trace 解析重复，SWE-bench scaffold 存在未接入公共函数 |
| `dynsteer` 根目录 | 1,609 | 9 | `model.py` 手写序列化重复，`progress.py` 存在乱码文案和进度条更新重复 |
| `dynsteer/harness` | 1,087 | 7 | 输出写入链路重复，配置 JSON 读取 helper 重复 |
| `dynsteer/experiment` | 857 | 5 | 实验 index 构造文案乱码，配置解析 helper 与 harness/loader 重复 |
| `display` | 770 | 2 | `display/build.py` 单文件过大，阶段定义、tool trace、数字/文本摘要重复 |
| `dynsteer/judges` | 669 | 7 | standard/expensive 多轮 LLM judge payload 处理有重复 |
| `dynsteer/prompt` | 446 | 5 | 少量一次性 prompt JSON 摘要 helper |
| `dynsteer/llm` | 437 | 5 | provider usage/text 读取重复但属于外部 SDK 边界，需保守 |
| `dynsteer/stage` | 364 | 4 | 可小幅合并 stage goal/spec 内部扩展逻辑 |
| `main.py` | 108 | 1 | CLI 入口基本可保留 |

当前最大热点文件：

| 文件 | 当前有效行数 | 判断 |
| --- | ---: | --- |
| `display/build.py` | 769 | 高收益，可用公共工具和索引结构收敛 |
| `dynsteer/evaluate/semantic.py` | 707 | 高收益，保留语义能力，压缩 row/excerpt/trace 重复 |
| `dynsteer/evaluate/evaluator.py` | 701 | 高收益，online/replay 主流程重复 |
| `dynsteer/model.py` | 678 | 中收益，属于 schema 边界，保守压缩序列化 |
| `dynsteer/evaluate/settlement.py` | 593 | 中高收益，策略/权重/metadata helper 可收敛 |
| `dynsteer/evaluate/diagnostics.py` | 579 | 高收益，失败诊断构造重复 |
| `dynsteer/adapter/toolsandbox/scorer.py` | 424 | 中高收益，schema 恢复和 reference summary 可合并 |
| `dynsteer/harness/outputs.py` | 407 | 中高收益，写文件与 trajectory summary 重复 |

## 3. 总体设计原则

1. 只在调用链起点做参数与类型校验；内部 helper 不重复校验同一不变量。
2. 公共工具只收纳跨 3 个以上文件且语义完全一致的逻辑。
3. 不新增纯转发函数；新增函数必须包含真实决策、转换或资源边界处理。
4. 不为旧接口保留兼容 wrapper；但本方案以“当前功能不变”为约束，已注册 benchmark、CLI、输出 JSON 字段不主动改变。
5. `model.py` 是领域 schema，不做激进拆散；只压缩完全等价的序列化重复。
6. 外部包边界保留防御性异常处理，尤其是 ToolSandbox、OpenAI、Anthropic、Polars。
7. 文档、日志、异常中文不得出现乱码；本次发现的乱码文案应作为简化实施时的顺手修复项。

## 4. 详细修改方案

### 阶段1：建立可复用的最小公共工具

目标：先消灭重复 JSON/string/number 读取，不改变任何业务逻辑。

修改文件：

1. `dynsteer/utils.py`
   - 新增 `read_json_file(path, label, expected_type)`，替代 `harness/config.py` 与 `experiment/runner.py` 中重复的 JSON 文件读取。
   - 新增或整理 `required_str(data, key, label="配置")`、`optional_str(value)`，替代 `adapter/loader.py`、`harness/config.py`、`experiment/config.py` 的局部同名函数。
   - 保留 `as_number()`、`clamped_number()`，让 `display/build.py`、`harness/outputs.py`、`evaluate/telemetry.py` 复用。
   - 不把 `utils.py` 做成杂货铺；只接收本阶段列出的跨文件重复工具。

2. `dynsteer/adapter/loader.py`
   - 删除本地 `_required_str()` 与 `_optional_object()` 中可由公共工具表达的部分。
   - `parse_constraint()`、`parse_milestone()`、`parse_minefield()` 入口只做一次 `ensure_json_object()`，内部列表解析不再重复包一层同样校验。
   - `_load_step()` 中 `tool_call_data`、`tool_result_data`、`cost_data` 需要先确认是 dict，再读取字段；这属于边界校验，保留但合并成局部构造块。

3. `dynsteer/harness/config.py`
   - 删除局部 `_read_json_file()`、`_required_str()`、`_optional_str()`，改用 `utils.py` 统一实现。
   - `load_harness_run_configs()` 保留 manifest/run_configs 的业务校验，不把 benchmark 规则塞进通用工具。

4. `dynsteer/experiment/config.py`
   - 删除局部 `_required_str()`、`_optional_str()` 可替代部分。
   - `_spec_mapping()`、`_method_mapping()` 保留，因为它们表达实验矩阵语义。

5. `dynsteer/experiment/runner.py`
   - `_read_json_object()` 改用 `read_json_file(..., dict)` 和 `ensure_json_object()`，或直接使用返回对象。
   - 修复 `_build_experiment_index_payload()`、`_sort_experiment_index_results()`、`_experiment_index_sort_key()` 中的乱码 docstring 和异常信息。

预期收益：减少 **220-330 行**。

### 阶段2：收敛 evaluator online/replay 重复链路

目标：`evaluate()` 与 `evaluate_replay()` 只保留真正不同的执行源；状态初始化、checkpoint 闭包、pending/finish 收尾、report 构造复用。

修改文件：`dynsteer/evaluate/evaluator.py`

具体修改：

1. 新增私有方法 `_initial_runtime_state(task_case)`：
   - 构造初始 `RuntimeEvaluationState`。
   - 放入 start settlement。
   - 初始化 `milestone_frontier`。
   - 该方法包含真实状态构造，不是中转函数。

2. 新增私有方法 `_checkpoint_evaluator()` 或 `_evaluate_checkpoint_for_strategy(...)`：
   - 封装 cheap/standard/expensive judge、threshold、weight_config、strategy 注入。
   - 当前 online/replay 内部分别定义 `checkpoint_evaluator(**kwargs)`，代码重复且闭包层级过深。

3. 新增私有方法 `_evaluate_closed_agent_step(...)`：
   - 接收 `config/task_case/trajectory/state/closure/scorer`。
   - 调用 `evaluate_agent_step()` 并注入上一步的 checkpoint evaluator。
   - online 模式继续经过 `_evaluate_step()` 处理外部 `harness.stop_case()`；replay 模式直接返回虚拟 decision。

4. 新增私有方法 `_finalize_state_reports(...)`：
   - 合并 pending milestone synthetic stage 写入逻辑。
   - 合并 `finish_settlement()` 调用逻辑。
   - 参数包含 `replay_termination`，online 传 None。

5. 新增私有方法 `_build_result(...)`：
   - 合并 report、runtime_metrics、raw_summary、`HarnessRunResult` 的构造。
   - online 和 replay 只提供 raw summary 来源差异。

6. 保留 `evaluate()` 与 `evaluate_replay()` 两个 public 入口。
   - online 入口仍负责 harness session 生命周期。
   - replay 入口仍负责 snapshot 按 step 注入和虚拟早停。

7. `_append_replay_snapshots()` 当前只服务 replay，可保留；但 `seen` 集合每次调用重建，后续可把 `seen_snapshot_ids` 放进 replay 局部状态，避免 O(n²)。

预期收益：减少 **300-480 行**。

### 阶段3：压缩 settlement、step、runtime 的策略与终止细节

目标：保留阶段结算行为，删除策略/权重/metadata 的碎片化 helper。

修改文件：

1. `dynsteer/evaluate/settlement.py`
   - `_dimension_levels_for_strategy()`、`_next_weights_for_strategy()`、`_next_policy_for_strategy()` 只被 `_evaluate_stage()` 单处调用，可合并到 `_evaluate_stage()` 的策略分支。
   - `_merge_dimension_result()` 与 `_semantic_review_dimensions_pass()` 可合并为一段“semantic review 覆写”局部逻辑。
   - `_milestone_for_interval()` 只服务 `_semantic_only_hard_failure()`，可把 milestone 查找内联。
   - `_average_dimension_confidence()` 保留或内联到 `should_stop_after_stage()`，取决于是否还有第二调用点。
   - `finish_settlement()` 中 terminal check 分数读取使用 `as_number()` 后，不再多次 fallback 同一字段。

2. `dynsteer/evaluate/step.py`
   - `_semantic_message_review_score()`、`_record_attempt_and_check_no_progress()`、`_ready_frontier_no_progress_decision()` 分别只有单一主调用链。
   - 保留能表达领域语义的函数名，但删除只负责把参数原样转交的层。
   - no-progress decision 的 termination detail 构造应与 `runtime.py` 的 reason 构造靠近，避免两个文件分别拼同一批字段。

3. `dynsteer/evaluate/runtime.py`
   - `selected_candidate_from_attempt()` 若只被 termination reason 使用，可把选择逻辑移动进 reason 构造主流程。
   - `ready_frontier_no_progress_termination_reason()` 与 detail 字段拼装目前分离，合并重复文本字段。
   - `pending_milestone_stage_results()` 保留为 public-ish runtime 诊断入口，但内部 synthetic stage 的默认字典不要重复构造。

预期收益：减少 **220-360 行**。

### 阶段4：合并 diagnostics 与 semantic 中重复的摘要逻辑

目标：语义能力不降级，但摘要、row 匹配、失败诊断只保留一套实现。

修改文件：

1. `dynsteer/evaluate/semantic.py`
   - 将 `_expected_actual_matches()`、`_best_identifier_match()`、`_focused_actual_row_indices()` 合并为一个 `_focused_state_rows(...)`，一次遍历返回：
     - matched count；
     - selected actual row indices；
     - expected_by_actual_index。
   - `_state_row_excerpt_payload()`、`_state_display_keys()`、`_shared_value_count()` 保留，但减少对相同 row 的重复扫描。
   - `_parse_tool_trace_value()` 的逻辑与 ToolSandbox trace/display parser 重复，移动到 `dynsteer/adapter/toolsandbox/utils/trace.py` 的公共 `tool_trace_items()`。
   - `_recent_supporting_steps()` 和 `_step_summary()` 可改为复用 `harness/outputs.trajectory_step_to_json()` 的压缩版字段，避免两套 step JSON 摘要。

2. `dynsteer/evaluate/diagnostics.py`
   - `_constraint_failure_detail()` 直接生成 `line` 字段，删除 `_constraint_failure_line()` 的第二次字段读取。
   - `_format_number()` 改为局部格式化表达式或公共 `format_number()`，不要单独保留一行 wrapper。
   - `_semantic_review_from_candidate()` 与 `_semantic_review_failure_line()` 合并到 pending failure 分支。
   - `build_final_milestone_diagnostics()` 中 common/best/last 字段构造单独局部块，避免 matched/pending 两个分支重复改写 `best_score`、`best_status`、`best_boundary_step_index`。
   - `build_quality_diagnostics()` 的 `_next_agent_message_by_index()` 可改为在一次反向扫描里生成，保留函数只在多处复用时才成立。

预期收益：减少 **420-650 行**。

### 阶段5：收敛 ToolSandbox adapter 的 schema、trace、reference 边界

目标：ToolSandbox 外部边界必须防御，但内部不要重复解析 tool trace、namespace schema、reference summary。

修改文件：

1. `dynsteer/adapter/toolsandbox/utils/trace.py`
   - 提供唯一 `tool_trace_items(raw_trace)`。
   - `tool_trace_from_row()`、`scenario.py`、`display/build.py` 均复用它。
   - 保留 `ast.literal_eval()` 解析 agent 参数，避免执行不可信内容。

2. `dynsteer/adapter/toolsandbox/utils/scenario.py`
   - 删除本地 `_tool_trace_items()`、`_parse_tool_trace_value()`。
   - `_sandbox_tool_call_semantics()` 与 `_tool_trace_stage_goal_semantics()` 合并为一个“从 SANDBOX row 推断 stage semantics”的函数。
   - `_is_sandbox_tool_call_row()` 与 `_sandbox_tool_name()` 可内联到该函数。

3. `dynsteer/adapter/toolsandbox/scorer.py`
   - `_reference_dataframe()` 同时返回 dataframe 与 summary，`_reference_summary()` 目前只是 try 包装，应删除或改成在失败路径内联构造 summary。
   - `_reference_summary_from_rows()` 可内联到 `_reference_dataframe()`，因为没有第二调用点。
   - `_namespace_schema()` 和 `_restore_namespace_schema()` 保留，但错误文案不要固定写 `SANDBOX`，避免 namespace 非 SANDBOX 时误导。
   - `_serialize_target_tool_trace()` 复用 `tool_trace_items()`，只保留“转为 ToolSandbox scorer 需要的字符串”这一小段差异。

4. `dynsteer/adapter/toolsandbox/harness.py`
   - `_role_for_recipient()` 和 `_last_column_value()` 保留外部边界，但调用点中的 context None/session finished 重复校验减少。
   - `_context_max_sandbox_message_index()` 与 `_sandbox_database()` 的 dataframe 行读取复用 `rows_from_dataframe()` 后，不再多条路径重复读取。
   - `_toolsandbox_roles()` 内部懒 import `get_agent_factory/get_user_factory` 应改为文件顶部 import，符合项目“禁止懒加载”约束。

5. `dynsteer/adapter/swebench`
   - 建议删除 `stage.py`、`trace.py`、`scorer.py` 中只会抛 `NotImplementedError` 或空类的未接入公共占位。
   - 保留 `SwebenchProAdapter` 与 `SwebenchProHarness` 及 registry 中的清晰 not-ready 错误，确保配置了 `swebench_pro` 时仍能得到明确提示。
   - 同步更新 `docs/apis/swebench.md`，不再把未实现 helper 当成 API。

预期收益：减少 **230-360 行**。

### 阶段6：统一 harness/output/display 的输出构造

目标：输出 JSON 字段不变，但写入路径、trajectory summary、display summary 只保留一套逻辑。

修改文件：

1. `dynsteer/harness/outputs.py`
   - `write_case_outputs()` 改用 `_write_output_payloads()`，删除手写 mkdir/write/return 路径对象代码。
   - 新增 `_trajectory_output_summary(trajectory, runtime_metrics)`，替代三处重复字典：
     - `write_case_outputs()`；
     - `write_default_case_outputs()`；
     - `write_replay_case_outputs()`。
   - `_score_from_summary()` 使用 `as_number()`，删除本地 bool/number 判断重复。
   - `trajectory_step_to_json()` 与 semantic/display 的 step summary 明确边界：完整序列化在这里，压缩摘要在 display/semantic 复用小工具。

2. `display/build.py`
   - 删除本地 `_number()`，使用 `as_number()`。
   - 删除本地 `_text()`，使用 `compact_text(json_safe(value), limit)` 或新增公共 `compact_json_text()`。
   - 删除本地 `_parse_tool_trace()`，复用 ToolSandbox trace 工具。
   - 把 stage definition 查询改为一次构建索引：
     - `definition_by_stage_id`；
     - `definition_by_milestone_id`；
     - `finish_definition`。
   - `_stage_definition_for_report()`、`_stage_definition_for_settlement()`、`_definition_for_stage_id()`、`_definition_for_milestone()`、`_finish_definition()` 合并为对索引的直接查询。
   - `_stage_reports()` 与 `_settlement_summaries()` 目前都维护 `stage_id_map`，应抽出一个“归一 stage_id 并返回映射”的内部块，避免重复替换字符串。
   - `_active_stage_definitions()` 与 `_active_stage_settlements()` 共用“是否已 terminal before finish”的判定结果，不重复扫描 reports。

预期收益：减少 **360-560 行**。

### 阶段7：保守压缩模型和 judge/LLM 层

目标：只删除确定重复，不破坏 schema 和 provider 边界。

修改文件：

1. `dynsteer/model.py`
   - 不拆散 dataclass 所在文件；这是领域 schema 边界。
   - 对 `StageEvaluationResult.to_dict()`、`TrajectoryEvaluationReport.to_dict()`、
     `EvaluationPolicyState.to_dict()`、`EvaluationTerminationState.to_dict()` 的重复 enum-key dict comprehension，
     抽取本文件内部 `_enum_key_dict()`。
   - 不能直接用 `json_safe(dataclass)` 替代所有 `to_dict()`，因为当前部分输出字段是刻意裁剪的，例如某些内部字段未进入 report。
   - `TrajectoryEvaluationReport.to_summary_dict()` 保留，因为它是摘要输出契约，不是普通序列化。

2. `dynsteer/harness/model.py`
   - `BenchmarkCase.to_dict()`、`HarnessStageSettlement.to_dict()` 与 model 内部序列化 helpers 对齐。
   - 不删除 `__post_init__()` 的入口校验；这是对外数据边界。

3. `dynsteer/experiment/model.py`
   - `ExperimentCaseResult.to_index_dict()` 和 `to_dict()` 有字段重复，可用一个 `_base_payload(include_identity: bool)` 私有方法减少重复。
   - `score` property 保留，避免输出字段含义散落。

4. `dynsteer/judges/standard.py` 与 `dynsteer/judges/expensive.py`
   - 多轮 `_call_json()`、`_validate_payload()`、`aggregate_judge_payload()`、`agreement_confidence()` 的框架相似，但 standard 是单 prompt 多 pass，expensive 是逐维多 prompt；只抽取“多 pass 后聚合为 result”的真实共同逻辑，不强行合并两类 judge。
   - `_semantic_payload()` 属于窄域消息复判，保留在 `StandardJudge`。

5. `dynsteer/llm/openai.py` 与 `dynsteer/llm/anthropic.py`
   - 仅统一 usage 字段读取的 `_optional_usage_int()` 使用方式。
   - 不合并 provider 请求构造，因为 OpenAI Chat Completions 与 Anthropic Messages API 的参数形态不同。

预期收益：减少 **180-320 行**。

### 阶段8：删除 scaffold 与乱码，清理观测性噪音

目标：删除明显无效实现，保证中文文档与异常信息不再乱码。

修改文件：

1. `dynsteer/experiment/runner.py`
   - 修复所有乱码 docstring 和异常文本。
   - 不删除 `run_guidance_case()`，因为 `ExperimentMethod.DYNSTEER_GUIDANCE` 当前仍是显式配置入口；删除会改变配置行为。
   - 若用户后续确认 guidance 不作为当前功能，则单独删除 enum、配置方法、runner 分支、README/API 文档。

2. `dynsteer/progress.py`
   - 修复 `case_index 蹇呴...` 乱码文案。
   - `_set_bar_postfix()` 与 `_static_progress_line()` 共用 elapsed/avg_step 格式化小块。
   - `_progress_description()` 与 `_title_text()` 保留，因为它们是不同 UI 文本。

3. `docs/apis/experiment.md`
   - 当前文件存在乱码，应在代码落地时同步修复。
   - 文档修复不计入有效代码行数，但属于项目约束要求。

4. `__pycache__`、`.pytest_cache`
   - 本方案不主动删除现有缓存目录。
   - 实施代码修改并完成测试后，按项目约束清理生成的缓存，但不得删除 tests 源码目录。

预期收益：减少 **80-150 行**，主要来自 progress/runner 小重复和 scaffold 清理；乱码修复不追求行数收益。

## 5. 不建议简化的边界

1. 不删除 `DynSTEEREvaluator.evaluate()` 与 `evaluate_replay()` public 入口；它们对应 online 与离线回放两种真实用法。
2. 不删除 `BaseBenchmarkAdapter`、`BaseBenchmarkHarness`、`BaseJudge`、`BaseLLM` 的抽象方法参数。
3. 不把 `model.py` 的 dataclass 拆到多个目录里，只为减少单文件行数而牺牲 schema 可见性。
4. 不把 OpenAI 与 Anthropic 请求构造强行抽象成一套 provider-agnostic 函数。
5. 不删除 `swebench_pro` registry 的明确 not-ready 行为；只删除未被 registry 使用、且只会抛未实现错误的 helper 占位。
6. 不删除 display 中为兼容历史结果 JSON 的字段读取，除非已有 fixture 能证明旧字段不再出现。

## 6. 测试与验收

必须新增或补强的测试：

1. `tests/test_utils.py`
   - 覆盖 `read_json_file()`、`required_str()`、`optional_str()`、`compact_json_text()`。

2. `tests/evaluate/test_evaluator_runtime_paths.py`
   - 覆盖 online/replay 共用状态初始化、pending 写入、finish settlement 写入。
   - 使用 fake harness，不依赖真实 ToolSandbox。

3. `tests/evaluate/test_semantic_diagnostics.py`
   - 覆盖 focused state excerpt、semantic message review detail、failed constraint diagnostics。
   - 保证合并 helper 后诊断字段不变。

4. `tests/adapter/test_toolsandbox_trace.py`
   - 覆盖 tool trace 字符串、dict、list、非法 JSON、target tool_trace 序列化。

5. `tests/harness/test_outputs.py`
   - 覆盖 default/evaluate/replay 三种输出路径和 `trajectory_output` 字段一致性。

6. `tests/display/test_build.py`
   - 使用最小 runs/results/data fixture，覆盖 stage definition 归一、finish terminal、minefield enrich。

7. `tests/experiment/test_runner.py`
   - 保留现有 index 分层测试。
   - 增加乱码修复后的异常信息断言只做关键含义，不做整句脆弱匹配。

必须执行的命令：

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location) ".uv-cache")
uv run python -m compileall dynsteer display main.py
uv run pytest tests -q
git diff --check
```

建议执行的功能回归：

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location) ".uv-cache")
uv run python main.py --input examples/minimal_experiment.json --results-dir results
uv run python display/build.py --runs-dir runs --results-dir results --output display/data.js
```

如果当前 main CLI 已不再支持 `--input`，则以 README 的最新命令为准，优先回归 `--benchmark` 与 `--experiment-config` 两条路径。

## 7. 有效行数缩减预估

按用户口径，当前全仓非 tests `.py/.sql` 有效行数为 **13,313 行**。执行本方案后，目标为 **9,950-10,750 行**。

预计净减少 **2,560-3,360 行**，降幅约 **19.2%-25.2%**。

| 阶段 | 预期减少 |
| --- | ---: |
| 公共 JSON/string/number 工具 | 220-330 |
| evaluator online/replay 收敛 | 300-480 |
| settlement/step/runtime 收敛 | 220-360 |
| diagnostics/semantic 收敛 | 420-650 |
| ToolSandbox adapter 收敛 | 230-360 |
| harness/output/display 收敛 | 360-560 |
| model/judge/LLM 保守压缩 | 180-320 |
| scaffold/乱码/progress 小清理 | 80-150 |
| 连带 import、重复校验、局部 helper 删除 | 550-750 |
| **合计** | **2,560-3,360** |

如果进一步删除 `dynsteer_guidance` 未实现方法、完全取消 `swebench_pro` scaffold registry、或移除历史 display JSON 兼容字段，行数还能再降约 300-600 行；但这些会改变当前可见接口或错误行为，不纳入“功能不发生变化”的本方案。

## 8. 分批落地建议

建议按以下 PR 或提交批次落地，每批都能独立编译和测试：

1. 工具函数与乱码修复：`utils.py`、`experiment/runner.py`、`progress.py`、`docs/apis/experiment.md`。
2. harness/output/display 收敛：`harness/outputs.py`、`display/build.py`。
3. evaluator/runtime 收敛：`evaluate/evaluator.py`、`evaluate/settlement.py`、`evaluate/step.py`、`evaluate/runtime.py`。
4. semantic/diagnostics 收敛：`evaluate/semantic.py`、`evaluate/diagnostics.py`、ToolSandbox trace 工具。
5. adapter/model/judge 保守压缩：ToolSandbox scorer/harness、model 序列化、judge 聚合。
6. scaffold 清理和最终指标验收：SWE-bench 未接入 helper、API 文档、有效行数统计。

每批落地后都运行对应测试。最后一批再执行全量 pytest 与有效行数统计。

## 附录A. 项目中没有把握实现的模块部分

1. `dynsteer/adapter/toolsandbox` 的真实运行边界没有完全把握。原因是 ToolSandbox 依赖外部包、Polars schema、原生 context、Pyo3 panic 兼容和 role teardown 行为；本地 fake 测试只能覆盖主要路径，不能完全覆盖外部包所有版本差异。

2. LLM provider 相关行为没有完全把握。原因是 OpenAI 与 Anthropic SDK 响应对象会随 SDK 版本变化；没有真实 API key 与线上响应时，只能用 fake response 验证当前字段读取。

3. `display/build.py` 对历史 runs/results JSON 的兼容范围没有完全把握。原因是当前仓库内只有代码和部分文档，没有覆盖全部历史输出结构的 fixture。简化 display 前应先固化最小旧格式 fixture。

4. `swebench_pro` scaffold 的外部调用情况没有完全把握。仓库内 `stage.py`、`trace.py`、`scorer.py` 基本未接入，但如果仓库外脚本 import 这些占位 API，删除会改变 import 行为。本方案建议删除这些未实现 helper，但保留 adapter/harness 的明确 not-ready 行为。

5. `model.py` 的序列化输出契约没有完全把握。原因是 report、summary、display 和历史结果可能依赖某些字段是否存在。实施时必须用 golden JSON 测试比较修改前后的关键输出，不能直接用泛用 dataclass serializer 替代全部 `to_dict()`。
