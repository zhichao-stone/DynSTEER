# DynSTEER 代码精简执行方案
生成日期：2026-07-29  
方案状态：仅方案，不直接修改主代码  
约束来源：`docs/constraints/code.md`

## 0. 当前结论
我对当前 `dynsteer` 代码做了一轮静态核查后，结论很明确：**没有发现可以在不改变公开行为的前提下直接整包删除的主模块**，但存在一批明显的“重复边界处理、重复序列化、重复摘要拼装、重复 client config 解析”。

因此，本次精简不走“看着像冗余就删”的路，而是按第一性原理把重复逻辑先收口，再做一次死代码清扫。  
当前最值得下手的地方是：

1. `harness/config.py` 与 `adapter/toolsandbox/utils/roles.py` 的 client config 解析重复。
2. `evaluate/*` 里评估、结算、诊断、语义摘要的 payload 构造重复。
3. `display/build.py` 与 `harness/outputs.py` 的 JSON 摘要拼装重复。
4. `adapter/toolsandbox/*` 的 trace / state / scenario / trajectory 转换职责还可以再收紧。
5. `experiment/config.py`、`experiment/runner.py`、`llm/factory.py` 仍有局部重复的标量读取和 index 拼装。

## 1. 精简原则
1. 只保留一套跨文件复用的原语。  
   只要同一段校验、转换、压缩、序列化逻辑出现在 3 个以上文件，就应当抽到共享层。
2. 只保留一个真正负责某类边界的文件。  
   例如 JSON 读写、client config 归一、ToolSandbox trace 解析、stage 摘要格式化，都应该各有一个权威实现。
3. 不新增纯转发函数。  
   如果一个函数只是把参数原样传给另一个函数，优先内联到调用点，或者并入更高层的真实业务函数。
4. 不为了“看起来干净”拆散公有 schema。  
   `dynsteer/model.py`、`harness/model.py`、`llm/base.py`、`adapter/base.py` 这类边界层保持稳定，不做激进拆分。
5. 死代码只在收口后删除。  
   先把调用链压短，再删没有主代码引用的 helper、wrapper、占位实现和对应测试。

## 2. 代码精简优先级
| 优先级 | 区域 | 原因 |
| --- | --- | --- |
| P0 | `harness/config.py` / `adapter/toolsandbox/utils/roles.py` | 当前已经出现两套 client config 解析，且同一字段有两份校验逻辑。 |
| P0 | `evaluate/evaluator.py` / `evaluate/settlement.py` / `evaluate/runtime.py` / `evaluate/step.py` | 评估主链路重复拼装终止态、报告态、checkpoint 态。 |
| P0 | `display/build.py` | 单文件过大，stage 定位、summary 归一、finish 兼容都在重复做。 |
| P1 | `evaluate/semantic.py` / `evaluate/diagnostics.py` / `evaluate/matching/milestone.py` / `evaluate/final.py` | 语义摘要、诊断摘要、候选摘要存在同构逻辑。 |
| P1 | `adapter/toolsandbox/scorer.py` / `adapter/toolsandbox/harness.py` / `adapter/toolsandbox/utils/*` | ToolSandbox 转换链路可继续收口成“一种输入一条路径”。 |
| P2 | `experiment/config.py` / `experiment/runner.py` / `harness/outputs.py` / `llm/factory.py` | 局部标量读取、metadata 合并、index payload 还有薄封装。 |
| P2 | `model.py` / `experiment/model.py` | 仅在有明确重复时再动，保留 schema 边界稳定性。 |

## 3. 分阶段执行

### 阶段 1：配置与输入标准化
目标：把 JSON / mapping / client config / 标量读取统一到少数几个共享原语。

涉及文件：
- `dynsteer/utils.py`
- `dynsteer/harness/config.py`
- `dynsteer/experiment/config.py`
- `dynsteer/adapter/loader.py`
- `dynsteer/adapter/toolsandbox/utils/roles.py`
- `dynsteer/llm/factory.py`

具体修改：
- `dynsteer/utils.py` 只保留真正跨文件复用的原语，不新增纯包装器。
  - 继续保留 `read_json_file`、`required_str`、`optional_str`、`as_number`、`clamped_number`、`json_safe`、`compact_text`、`clean_evidence_items` 这类已经有跨文件价值的函数。
  - 如需新增共享函数，只新增能被 3 个及以上文件复用的读数 / 归一函数，例如 `read_positive_int`、`read_non_negative_float`、`normalize_mapping`、`merge_json_objects` 这类。
- `dynsteer/harness/config.py`
  - 删除 `_client_configs_from_spec`、`_client_config_from_mapping`、`_client_config_value`。
  - `load_harness_run_configs()` 只负责业务校验和装配，不再负责 client 配置字段级归一。
  - `agent_client` / `user_client` 继续作为 metadata 的显式字段保留，但解析来源改为共享 helper。
- `dynsteer/adapter/toolsandbox/utils/roles.py`
  - 删除 `_client_config`、`_config_text`、`_config_env_value`、`_config_timeout_seconds` 这一组局部解析函数。
  - `get_agent_factory()` / `get_user_factory()` 保留为唯一公有入口，但内部只接收已归一的 client config。
  - `_environment_role_type()` 只处理 role 类型和 client 注入，不再处理字段级校验。
- `dynsteer/llm/factory.py`
  - `_read_positive_int()`、`_read_non_negative_float()`、`_config_positive_int()`、`_config_non_negative_float()` 合并为一套共享标量读取逻辑。
  - `build_llm_from_env()` 与 `build_llm_from_config()` 继续保留，但不重复做同一套数值边界判断。
- `dynsteer/experiment/config.py`
  - 如果 `_metadata()`、`_merge_metadata()`、`_case_ids()` 的行为仍与其他文件的相同片段同构，就把公共部分下沉到共享 helper。
  - `_spec_mapping()`、`_method_mapping()`、`_profile_mapping()` 只保留表达实验矩阵语义所必需的部分，不再做通用 JSON 读入职责。
- `dynsteer/adapter/loader.py`
  - `_optional_object()`、`_optional_json_object()`、`_optional_int()` 这类小函数如果仍只承担一次性字段校验，应并入更靠近调用点的解析函数，避免多层跳转。
  - `parse_constraint()`、`parse_milestone()`、`parse_minefield()`、`parse_task_case()` 继续作为清晰入口，但内部不要重复做 JSON 对象 guard。

验收标准：
- 同一份 `client_config` 校验逻辑只保留一处。
- 同一份字符串 / 数值 / JSON object 归一逻辑只保留一处。
- `harness/config.py`、`roles.py`、`llm/factory.py` 不再各自维护同义 helper。

### 阶段 2：评估主链路收口
目标：让 `evaluate()`、`evaluate_replay()`、`evaluate_checkpoint()`、`finish_settlement()` 分工清晰，避免重复拼装终止态和报告态。

涉及文件：
- `dynsteer/evaluate/evaluator.py`
- `dynsteer/evaluate/runtime.py`
- `dynsteer/evaluate/settlement.py`
- `dynsteer/evaluate/step.py`
- `dynsteer/evaluate/policy.py`

具体修改：
- `dynsteer/evaluate/evaluator.py`
  - 保留 `evaluate()` 和 `evaluate_replay()` 两个 public 入口。
  - 将两者共同依赖的“初始 runtime state + state finalization + runtime result 构造”收成最少的私有 helper。
  - `_evaluate_closed_agent_step()`、`_evaluate_checkpoint_for_strategy()` 只保留依赖注入，不再承载额外装配逻辑。
  - `_finalize_state_reports()` 只负责收口 `pending` / `finish` 的结果，不再重复构建 payload。
- `dynsteer/evaluate/runtime.py`
  - `ready_frontier_no_progress_termination_reason()`、`blocked_milestone_termination_reason()`、`pending_milestone_stage_results()` 继续留在 runtime 层，但终止详情文本只在一个地方拼装。
  - `selected_candidate_from_attempt()` 与 `update_ready_frontier_progress_watch()` 的结果结构统一，不在上层重复解读同一个 detail。
- `dynsteer/evaluate/settlement.py`
  - `evaluate_checkpoint()` 和 `finish_settlement()` 仍然是核心入口。
  - `finish_settlement()` 里与终局、whole-trajectory、metadata、evidence 相关的拼接逻辑合并，避免多处 fallback 读同一字段。
  - `_deterministic_finish_stage_result()`、`_whole_trajectory_finish_stage_result()`、`_finish_stage_metadata()`、`_dimension_rationale_json()` 只保留真正不同的分支。
- `dynsteer/evaluate/step.py`
  - `_semantic_message_review_score()` 与 `_ready_frontier_no_progress_decision()` 的职责边界收紧，只保留一步判断，不再做多余包装。
  - 终止判断相关的 detail 构造尽量交回 `runtime.py` / `settlement.py`。
- `dynsteer/evaluate/policy.py`
  - 如果 `_next_dimension_levels()` 在收口后仍然只被一个上层 helper 使用，可考虑并入 `update_evaluation_policy()`。

验收标准：
- `evaluate()` 和 `evaluate_replay()` 的差异只剩“是否驱动真实 session”。
- 终止 reason / detail / stage_result / report 的拼装路径不再在多个文件重复。
- 单测里只需关心结果，不再关心中间层有几层包装。

### 阶段 3：语义、诊断与 matching 统一
目标：把“同一份语义判断”只写一次，把“同一份诊断摘要”只拼一次。

涉及文件：
- `dynsteer/evaluate/semantic.py`
- `dynsteer/evaluate/diagnostics.py`
- `dynsteer/evaluate/matching/milestone.py`
- `dynsteer/evaluate/final.py`

具体修改：
- `dynsteer/evaluate/semantic.py`
  - `constraint_expected_excerpt()`、`constraint_actual_excerpt()`、`_focused_state_excerpt()`、`_state_row_excerpt_payload()`、`_state_display_keys()` 合并成一条“从 constraint/trajectory 生成 excerpt”的主路径。
  - `_review_target()`、`_supporting_context()`、`_recent_supporting_steps()`、`_recent_state_summaries()`、`_relationship_summary()` 统一成支持语义复核的上下文构建器。
  - `_row_label()`、`_expected_content()`、`_matching_message()`、`_route_matches()` 只保留真正的领域判断，不做重复文本格式化。
- `dynsteer/evaluate/diagnostics.py`
  - `build_milestone_candidate_detail()`、`build_milestone_matching_detail()`、`build_final_milestone_diagnostics()`、`build_finish_matching_detail()` 统一成同一套基础 payload builder。
  - `_constraint_failure_detail()`、`_failed_constraint_details()`、`_pending_failure_diagnostics()`、`_semantic_review_failure()` 的文本分支收口，避免在多个位置重写“同一种失败”的描述。
  - `_compact_json()`、`_format_number()` 之类局部格式化函数如果仅服务单一调用点，应换成共享格式化 helper 或直接用 `dynsteer.utils` 原语。
- `dynsteer/evaluate/matching/milestone.py`
  - `_analyze_ready_candidates()` 与 `_analyze_blocked_candidates()` 合并成一个共享候选分析器，只保留“候选来源不同”的分支。
  - `_is_llm_semantic_review_candidate()`、`_hard_failures_are_semantic_emit_messages()`、`_has_reviewable_semantic_message()` 合并成一组更紧凑的 semantic review gate。
  - `_milestone_route_groups()` 只负责读取 metadata，不再附带额外过滤或格式化。
- `dynsteer/evaluate/final.py`
  - `build_finish_verification()` 保留为终局入口，但内部应直接复用共享的 terminal check / evidence / anchor 判断，不要再单独实现一套相同的摘要构造。

验收标准：
- 语义复核、终局验证、候选匹配三条链路共享同一套摘要原语。
- 诊断文本不会因为走了不同文件而出现两套不同说法。
- 减少“同一字段在两个地方分别拼一次”的现象。

### 阶段 4：ToolSandbox 适配层瘦身
目标：让 ToolSandbox 只保留一条转换链路和一条角色工厂链路。

涉及文件：
- `dynsteer/adapter/toolsandbox/harness.py`
- `dynsteer/adapter/toolsandbox/scorer.py`
- `dynsteer/adapter/toolsandbox/utils/trajectory.py`
- `dynsteer/adapter/toolsandbox/utils/trace.py`
- `dynsteer/adapter/toolsandbox/utils/state.py`
- `dynsteer/adapter/toolsandbox/utils/scenario.py`
- `dynsteer/adapter/toolsandbox/utils/roles.py`

具体修改：
- `dynsteer/adapter/toolsandbox/harness.py`
  - 只保留 session 生命周期、角色选择、context 读取、stop/teardown 这类真正的协调逻辑。
  - 通过 `get_agent_factory()` / `get_user_factory()` 注入 client config，但不在这里再做字段级解析。
- `dynsteer/adapter/toolsandbox/utils/trace.py`
  - 保持 `tool_trace_items()` 作为唯一 trace 解析入口。
  - `tool_trace_from_row()`、`tool_arguments_from_agent_content()`、`tool_call_from_agent_row()` 继续基于同一套 trace 解析，不允许别的文件再写一份解析分支。
- `dynsteer/adapter/toolsandbox/utils/trajectory.py`
  - 只负责 `Trajectory` / `TrajectoryStep` / `StateSnapshot` 的 sandbox row 转换。
  - 不把 scenario、state、trace 的逻辑再带进来。
- `dynsteer/adapter/toolsandbox/utils/state.py`
  - 只负责 context -> state / snapshot，不再兼任 trace 或 scenario 转换。
- `dynsteer/adapter/toolsandbox/utils/scenario.py`
  - 只负责 scenario -> milestone graph / stage_goal_semantics，不再重复 trace 或 state 解析。
- `dynsteer/adapter/toolsandbox/scorer.py`
  - `_reference_dataframe()`、`_reference_summary()`、`_reference_evidence()` 合并为一个 reference lookup 路径。
  - `_namespace_schema()` 与 `_restore_namespace_schema()`、`_column_similarities()` 与 `_restore_column_similarity()`、`_snapshot_constraint_kwargs()` 的职责边界收紧，避免一层一层包同一个字段。
  - `_resolved_target_dataframe()` 保留为唯一 target 选择入口。
- `dynsteer/adapter/toolsandbox/utils/roles.py`
  - 保留 `RoleFactorySpec`、`get_agent_factory()`、`get_user_factory()`、`_environment_role_type()`。
  - 删除 client config 解析的局部重复实现，统一使用阶段 1 的共享 helper。

验收标准：
- 每个 ToolSandbox 职责只剩一个文件负责。
- 任何 trace / state / scenario / trajectory 的转换都没有第二份实现。
- `roles.py` 不再维护一套自己的 client config 解析。

### 阶段 5：展示、输出与实验索引收口
目标：把“怎么写 JSON、怎么展示 JSON、怎么排实验结果”统一成少数几个权威入口。

涉及文件：
- `display/build.py`
- `dynsteer/harness/outputs.py`
- `dynsteer/experiment/runner.py`
- `dynsteer/experiment/config.py`
- `dynsteer/experiment/model.py`
- `dynsteer/model.py`

具体修改：
- `display/build.py`
  - `StageDefinitionIndex` 保留，但 stage definition lookup 只保留一条主路径。
  - `_stage_definition_for_report()`、`_stage_definition_for_settlement()`、`_definition_for_stage_id()`、`_finish_stage_definition()` 合并成更小的索引查询面。
  - `_number()`、`_text()`、`_compact_json()` 若仍只是本文件的薄包装，优先换成共享 helper 或内联。
  - `_summary_payload()`、`_graph_summary()`、`_termination_summary()`、`_minefield_definitions()` 的 payload 形状统一，避免同一份 JSON 在多处重写。
- `dynsteer/harness/outputs.py`
  - 保留 `write_case_outputs()`、`write_default_case_outputs()`、`write_replay_case_outputs()` 三个 public writer。
  - 把 `trajectory.json` / `summary.json` / `raw_summary.json` 的构造收口到一个 payload builder。
  - `_trajectory_output_summary()`、`_merge_snapshots()` 继续存在，但不再让三个 writer 各自拼一份等价结构。
- `dynsteer/experiment/runner.py`
  - `_build_experiment_index_payload()`、`_sort_experiment_index_results()`、`_experiment_index_sort_key()` 合并成一个最小的“构造 + 排序 + 落盘”路径。
  - 实验 index 的字段应尽量复用 `ExperimentCaseResult.to_index_dict()`，避免 runner 再手工组装一遍。
- `dynsteer/experiment/config.py`
  - 若 `_merge_metadata()`、`_metadata()`、`_case_ids()` 在调用侧仍然承担重复的收口职责，可把可复用的部分下沉到共享 helper。
  - `expand_experiment_matrix()` 只保留矩阵展开语义，不再兼做多种 JSON 归一任务。
- `dynsteer/experiment/model.py`
  - 保留 `ExperimentCaseResult._base_payload()` / `to_index_dict()` / `to_dict()` 这种 schema 级装配。
  - 如果上层已经不再手工拼同义字段，就不要再给 model 增加新的派生字段。
- `dynsteer/model.py`
  - 继续把 dataclass 当作 schema 边界，不拆散到多个文件。
  - 仅在序列化重复明显时再补共享序列化 helper，不要把 model 变成业务逻辑容器。

验收标准：
- 实验结果 index、run summary、display data 三处不再分别维护同一份字段拼装。
- `display/build.py` 不再是“全库最大的一份摘要工厂”。
- `ExperimentCaseResult` 成为 index payload 的单一事实来源。

## 4. 明确删除项
以下内容只有在对应共享 helper 落地后才删除：

1. `harness/config.py` 里与 `agent_client` / `user_client` 相关的重复解析函数。
2. `adapter/toolsandbox/utils/roles.py` 里与 client config 相关的重复解析函数。
3. `llm/factory.py` 里与正整数 / 浮点数 / env 读取相关的局部重复 helper。
4. `display/build.py` 里与 stage lookup、number/text compaction 重复相关的局部 helper。
5. `evaluate/semantic.py`、`evaluate/diagnostics.py`、`evaluate/matching/milestone.py` 里已经被共享路径覆盖的单用 helper。
6. `experiment/runner.py` 里已经被 `ExperimentCaseResult` 或共享 builder 吸收的 index/sort helper。

不删除的内容：

1. `dynsteer/adapter/swebench/*` 的 not-ready 适配器和 harness。它们是明确的注册边界，不是死代码。
2. `Base*` 抽象类上的抽象方法和边界参数。
3. `dynsteer/model.py`、`harness/model.py` 这类 schema 边界定义。
4. `tests` 中仍然覆盖主流程的测试文件。

## 5. 验证顺序
每个阶段都应单独验证，不要攒到最后一起看。

阶段级验证建议：

1. 阶段 1 完成后：
   - `uv run pytest tests/test_toolsandbox_role_client_config.py tests/test_toolsandbox_harness_client_config.py tests/test_toolsandbox_client_config_config.py -q`
2. 阶段 2 完成后：
   - `uv run pytest tests/test_finish_whole_trajectory_stage.py tests/test_finish_empty_graph_precheck.py tests/test_replay_default_reference_isolation.py tests/test_replay_empty_graph_coverage.py -q`
3. 阶段 3 完成后：
   - `uv run pytest tests/test_judge_prompt_whole_trajectory_context.py -q`
   - 以及覆盖 semantic / diagnostics 相关的现有测试集
4. 阶段 4 完成后：
   - ToolSandbox 相关测试全跑一遍
5. 阶段 5 完成后：
   - 展示与实验索引测试全跑一遍

最终必跑命令：

```powershell
uv run python -m compileall dynsteer display main.py
uv run pytest tests -q --cov=dynsteer --cov-report=term-missing
git diff --check
```

## 6. 文档同步
只要 public JSON 形状、config 字段、display 输出结构变化，就同步更新：

- `docs/apis/harness.md`
- `docs/apis/experiment.md`
- `docs/apis/display.md`
- `docs/apis/swebench.md`
- `README.md`

同步原则：

1. 只改说明，不扩散新的实现细节。
2. 文档字段名必须与代码输出完全一致。
3. 若某个 helper 删除后不再对外暴露，就同步把 docs 里的伪 API 去掉。

## 附录A. 当前未确认的死代码
本次静态审计没有得到“可以无风险直接整文件删除”的结论。当前看起来像冗余、但实际仍在主流程中的内容如下：

1. `dynsteer/adapter/swebench/adapter.py` / `dynsteer/adapter/swebench/harness.py`：虽然是 not-ready，但已被 registry 和文档引用。
2. `dynsteer/model.py`、`dynsteer/harness/model.py`、`dynsteer/llm/base.py`、`dynsteer/adapter/base.py`：属于边界层，不是死代码候选。
3. `dynsteer/evaluate/*`、`dynsteer/adapter/toolsandbox/*` 中的大量 private helper：很多看起来只调用一次，但它们通常承载唯一的领域分支，必须先收口再决定删不删。

因此，本方案的删除策略是：**先合并，再验证，再删除**。  
只有当某个 helper 在主代码中变成零引用，且没有对外契约价值时，才允许进入删除清单。

## 附录B. 建议落地顺序
推荐按下面顺序分批提交：

1. 配置与 client config 收口。
2. 评估主链路收口。
3. 语义 / 诊断 / matching 收口。
4. ToolSandbox 适配层收口。
5. display / outputs / experiment index 收口。
6. 最终死代码扫尾与文档同步。

