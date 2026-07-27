# DynSTEER Python冗余第二轮代码简化实施方案

生成日期：2026-07-17  
适用基础：已完成第一轮保守简化后的当前工作区。  
当前有效行数：9983 行。统计范围为 `dynsteer/**/*.py`，排除空行、`#` 注释、模块/类/函数 docstring。  
第二轮目标：把有效行数压到 8200-8600 行，净减少约 1380-1780 行。  
最终目标衔接：第二轮后再通过第三轮处理 `model.py` 与跨模块 schema 收敛，冲刺 7400-7800 行。

## 1. 背景与问题判断

第一轮简化已经完成了确定性较高的清理：

1. 折叠 `stage/settlement.py` 的 checkpoint 结算中转链。
2. 清理 `evaluate/policy.py`、`evaluate/quality.py`、`evaluate/runtime.py`、`evaluate/scoring.py` 中一批单次 helper 与未用参数。
3. 将 `adapter/toolsandbox/utils/convert.py` 拆分为 `trace.py`、`scenario.py`、`state.py`、`trajectory.py`。
4. 对 `toolsandbox` role factory 和 scorer module loader 做了第一轮收敛。

但第一轮没有完整达到 7400-7800 行目标，主要原因是：

1. 第一轮以行为不变和低风险为优先，没有深挖 `model.py`、`adapter/loader.py`、`progress.py`、`harness/config.py`、`judges`、`llm` 等大块区域。
2. `convert.py` 拆分降低了单文件复杂度，但拆分本身不会等比例降低总行数。
3. `evaluate` 目录仍存在多处诊断 payload 构造、候选详情构造、pending/final 诊断构造的重复模式。
4. JSON 字段读取、枚举转换、数字/字符串/列表校验仍散落在 loader、harness config、toolsandbox scorer、diagnostics 中。
5. 部分 helper 虽然只调用一次，但承载较强领域语义，不能只按静态调用次数机械删除，需要按职责边界合并。

第二轮应把重点从“删明显死代码”转为“收敛重复结构与重复解析模式”。

## 2. 新增专项问题处理决策

### 2.1 合并 `evaluate/diagnostics.py` 与 `evaluate/quality.py`

结论：第二轮应将 `evaluate/quality.py` 合并进 `evaluate/diagnostics.py`，并删除 `quality.py`。

理由：

1. 两者都构造评估诊断信息，当前差异主要是诊断对象不同：`diagnostics.py` 处理 milestone/stage/final matching diagnostics，`quality.py` 处理 runtime/stage trajectory quality diagnostics。
2. `quality.py` 的 public 入口只有 `build_runtime_quality_diagnostics` 与 `build_stage_quality_diagnostics`，当前仓库内调用点分别是 `evaluate/runtime.py` 与 `judges/cheap.py`，迁移成本低。
3. 合并后 `evaluate` 目录的诊断入口更集中，也能复用 `json_safe`、`compact_text`、数字读取、evidence 清洗等工具，减少重复 helper。

落地要求：

1. 将 `build_runtime_quality_diagnostics`、`build_stage_quality_diagnostics`、空工具结果分类、工具参数 warning、efficiency 诊断逻辑迁移到 `evaluate/diagnostics.py`。
2. 更新 `evaluate/runtime.py` 和 `judges/cheap.py` 的 import，直接从 `dynsteer.evaluate.diagnostics` 引入。
3. 删除 `evaluate/quality.py`，不保留只做 re-export 的兼容文件。
4. 合并后若 `diagnostics.py` 超过 520 有效行，需要优先继续压缩重复 payload 构造，而不是拆出新的转发模块。

### 2.2 将 `stage/settlement.py` 迁移到 `evaluate/settlement.py`

结论：第二轮应将 `dynsteer/stage/settlement.py` 移动到 `dynsteer/evaluate/settlement.py`，并删除旧文件。

理由：

1. `settlement.py` 当前主要处理 checkpoint 评估、finish 结算、stage evaluation、termination、weight/policy 更新，本质是运行期评估逻辑，不是 stage goal/spec/trajectory 的静态阶段定义逻辑。
2. 当前仓库内只有 `evaluate/evaluator.py` 从 `dynsteer.stage.settlement` 引入 `evaluate_checkpoint` 与 `finish_settlement`，迁移调用点清晰。
3. 迁移后 `stage` 目录保留 stage goal、stage spec、trajectory interval 相关纯阶段工具，`evaluate` 目录承载运行期评估与结算，职责边界更自然。

落地要求：

1. 新位置为 `dynsteer/evaluate/settlement.py`。
2. 更新 `evaluate/evaluator.py` import 为 `from dynsteer.evaluate.settlement import evaluate_checkpoint, finish_settlement`。
3. 删除 `dynsteer/stage/settlement.py`，不保留 `stage/settlement.py` 兼容转发文件。
4. 检查 `dynsteer/stage/__init__.py`，不得把 settlement 重新导出到 stage 包。
5. 保留 settlement 对 `stage_goal_key`、`stage_start_step_index`、`resolve_stage_evaluation_spec` 等 stage 工具的直接 import，因为这些是合理的跨层依赖。

### 2.3 删除单次使用的 Callable 类型别名

结论：删除 `evaluate/step.py` 中的 `CheckpointEvaluator = Callable[..., RuntimeEvaluationDecision]`，在 `evaluate_agent_step` 参数处直接使用 `Callable[..., RuntimeEvaluationDecision]`。

理由：

1. 该别名当前只被一个参数使用，无法降低复杂度，反而增加一次跳转。
2. `Callable[..., RuntimeEvaluationDecision]` 本身足够短，直接声明更符合当前简洁性约束。

落地要求：

1. 删除 `CheckpointEvaluator` 类型别名。
2. 保留 `Callable` import，因为函数签名仍需要。
3. 检查全仓是否还有类似只使用一次的 `Callable`/`TypeAlias`/简单别名；若无继承契约或可读性收益，直接内联。

### 2.4 重复校验专项核查

结论：第二轮必须把重复校验作为独立任务处理，而不是只在修改过程中顺手清理。

核查方法：

1. 用 AST 和文本扫描定位以下模式：
   - `x is None`
   - `not isinstance(x, ...)`
   - `isinstance(x, bool) or not isinstance(x, int | float)`
   - `graph is None`
   - `state is None`
   - `config is None`
   - `trajectory is None`
2. 按校验位置分为四类：
   - 外部入口校验：保留，例如 parse/load、public API、harness entry、LLM/IO entry。
   - 外部包边界校验：保留，例如 ToolSandbox context、polars dataframe、provider response。
   - 继承/抽象契约校验：谨慎保留，不按未使用或当前实现路径删除。
   - 内部链路重复校验：删除，依赖入口和数据模型保证。
3. 对同一调用链只保留起点校验。例如 checkpoint 链中，`evaluate_checkpoint` 已校验 `state/task_case/trajectory/milestone/boundary/thresholds` 后，内部 `_evaluate_stage`、metadata 构造、low score helper 不再重复校验这些对象。

重点处理区域：

1. `evaluate/final.py`：入口已确认 `task_case.milestone_graph` 后，内部 terminal helper 不重复 `graph is None`。
2. `evaluate/runtime.py`：`state.milestone_frontier`、`attempt_detail.step_index` 在入口处理后，内部 progress helper 不重复防御。
3. `evaluate/diagnostics.py`：JSON payload 读取集中处理，避免每个文本构造 helper 重复判断 dict/list/number。
4. `adapter/loader.py`：每个 parse/load 入口负责边界校验，内部构造不重复 `ensure_json_object`。
5. `adapter/toolsandbox/scorer.py`：保留外部包和 dataframe 边界校验，删除 schema 恢复链路内部重复 guard。

验收要求：

1. 第二轮完成后运行未用入参 AST 扫描，普通实现函数不得存在确定未用入参。
2. 抽象接口、provider hook、harness hook 的未用参数必须记录为保留项。
3. 重复 guard 删除必须由测试覆盖，不允许为了压行数删除外部边界防御。

## 3. 当前规模与第二轮压缩目标

| 区域 | 当前有效行数 | 第二轮目标 | 主要动作 |
| --- | ---: | ---: | --- |
| `dynsteer/evaluate` | 3515 | 2700-2900 | 迁入 settlement、合并 quality diagnostics、压缩 candidate/detail payload、减少重复 guard |
| `dynsteer/adapter` | 2153 | 1650-1800 | 收敛 loader JSON 解析、继续压缩 toolsandbox scorer/harness |
| `dynsteer` 根文件 | 1520 | 1250-1350 | 提取真实复用工具，压缩 progress 与日志辅助逻辑 |
| `dynsteer/stage` | 419 | 330-380 | 迁出 settlement 后，仅压缩 stage goal/spec/trajectory |
| `dynsteer/harness` | 817 | 650-720 | 合并 run config 字段读取、减少输出/调度中一次性 wrapper |
| `dynsteer/judges` | 744 | 620-680 | 收敛 payload 解析、pass metadata、confidence 默认补齐逻辑 |
| `dynsteer/prompt` | 424 | 350-380 | 合并 prompt JSON 摘要函数与模板辅助函数 |
| `dynsteer/llm` | 393 | 320-350 | 收敛 OpenAI/Anthropic response text/usage 解析模式 |
| **合计** | **9983** | **8200-8600** | 净减少约 1380-1780 行 |

说明：第二轮不强行把项目一次压到 7800 行以内。若直接追求 2180 行以上净减少，必须大规模修改 `model.py` 或拆掉较多领域诊断细节，回归风险偏高。第二轮先把重复结构收敛到位，再进行第三轮 schema 层压缩更稳。

## 4. 总体实施原则

1. 只删除确定冗余，不删除抽象接口、继承契约、外部包边界的必要参数。
2. 不新增纯转发模块或只调用已有函数的中转函数。
3. 解析入口、外部包边界、LLM/IO 边界保留防御性校验；内部链路不重复校验同一不变量。
4. 公共工具只收纳跨 3 个以上文件真实复用、语义一致的函数。
5. 所有重构必须以现有测试调用项目已有接口验证，不为测试新增生产接口。
6. 不主动 git commit，不删除 `docs/constraints` 与 `docs/plans` 内既有文档。
7. 单次使用的简单类型别名默认删除，除非它表达稳定公共语义或复杂泛型。
8. 目录归属以职责为准；运行期评估结算归 `evaluate`，阶段目标/规格/区间工具归 `stage`。

## 5. 代码架构调整说明

第二轮预计新增或调整以下工具层，但必须控制范围：

| 文件 | 职责 |
| --- | --- |
| `dynsteer/utils.py` | 仅保留跨文件通用的 JSON 数字、字符串、列表、对象读取工具；禁止变成大杂烩。 |
| `dynsteer/adapter/loader.py` | 保留 TaskCase/Trajectory 反序列化入口，删除仅服务单处的小 loader wrapper。 |
| `dynsteer/evaluate/diagnostics.py` | 保留诊断聚合入口，合并原 `quality.py` 的 runtime/stage quality diagnostics，并压缩 constraint failure/detail 的重复 payload 构造。 |
| `dynsteer/evaluate/settlement.py` | 从 `stage/settlement.py` 迁入，承载 checkpoint/finish settlement 与 stage runtime evaluation。 |
| `dynsteer/evaluate/runtime.py` | 保留运行期状态推进与 termination 文案入口，压缩 pending/no-progress 的字段拆装。 |
| `dynsteer/adapter/toolsandbox/utils/*.py` | 保持 trace/scenario/state/trajectory 职责边界，继续删除拆分后遗留的一次性 helper。 |

本轮不新建大型模块。若确实需要新建文件，只允许围绕 JSON 读取或 ToolSandbox 单一职责创建，且必须直接被多个模块调用。

## 6. 分阶段实施方案

### 阶段1：建立冗余核查基线

目标：先把可验证指标固化，避免主观压缩。

1. 新增或临时使用 AST 脚本统计：
   - `dynsteer/**/*.py` 有效行数。
   - 未用 import 候选。
   - 未用入参候选。
   - 单次调用且函数体小于 25 行的 helper 候选。
2. 人工过滤以下误报：
   - `adapter/base.py` 抽象基类方法参数。
   - judge/LLM/harness 子类覆盖契约。
   - `__init__.py` re-export。
   - 闭包或动态类使用的参数。
3. 输出第二轮修改前基线，作为验收对比。
4. 额外扫描单次使用的类型别名，例如 `CheckpointEvaluator = Callable[..., RuntimeEvaluationDecision]`，若只服务一个签名则直接内联。
5. 输出重复校验候选清单，按外部入口、外部包边界、继承契约、内部重复校验四类标注处理方式。

预期收益：不直接减少行数，但避免漏删和误删。

### 阶段2：调整 evaluate/stage 职责边界

目标：先把目录职责摆正，再压缩重复代码，避免在错误目录继续堆逻辑。

1. 将 `stage/settlement.py` 移动到 `evaluate/settlement.py`。
2. 更新 `evaluate/evaluator.py` 的 settlement import。
3. 删除旧 `stage/settlement.py`，不保留转发兼容文件。
4. 确认 `stage/__init__.py` 只导出 stage goal/spec/trajectory 工具。
5. 删除 `evaluate/step.py` 中的 `CheckpointEvaluator` 类型别名，直接使用 `Callable[..., RuntimeEvaluationDecision]`。
6. 运行 `python -m compileall dynsteer` 与 `pytest`，确保目录迁移未破坏导入。

预期收益：直接行数收益较小，但目录职责显著清晰，为后续压缩提供正确边界。

### 阶段3：压缩 `evaluate` 诊断与运行期模块

目标：减少 `evaluate` 中重复 payload 构造和一次性 helper。

1. `evaluate/diagnostics.py`
   - 合并 `evaluate/quality.py` 的 runtime/stage quality diagnostics，删除 `quality.py`。
   - 更新 `evaluate/runtime.py` 与 `judges/cheap.py` 的 import。
   - 合并 `_constraint_failure_detail`、`_constraint_failure_line`、`_failed_constraint_details` 中重复读取 score/evidence/threshold 的逻辑。
   - 将 `_format_number` 与通用 `as_number`/`clamped_number` 统一使用。
   - 删除只包装 `json_safe(...)` 的薄函数，调用点直接使用 `json_safe`，除非函数名表达了领域边界。
   - 将 pending diagnostics 中 best/last/common 字段构造合并为单个局部块。
2. `evaluate/matching/milestone.py`
   - 合并 `_build_ready_candidate_detail` 与 `build_milestone_candidate_detail` 的一次性包装。
   - 将 `_build_step_attempt_detail` 的字段构造内联到主流程或与 blocked detail 构造共享局部字段。
   - 保留 `_is_llm_semantic_review_candidate`，因为它表达领域判定。
3. `evaluate/runtime.py`
   - 合并 `ready_frontier_no_progress_termination_reason` 与 `_ready_frontier_no_progress_detail` 中重复字段文本构造。
   - 简化 `blocked_milestone_termination_reason` 的多层 fallback 读取。
   - `pending_milestone_stage_results` 中 synthetic report 的维度默认字典可使用统一工厂函数或局部常量，避免重复推导。
4. `evaluate/final.py`
   - 保留 finish verification 主入口。
   - 合并 terminal state/message check 的共同字段。
   - `_terminal_check_evidence`、`_terminal_message_check_evidence` 可保留为局部构造块，若只服务主入口则内联。
5. `evaluate/scoring.py`
   - 合并数值校验与 clamp 逻辑到 `utils.py`。
   - `_step_to_source` 与 `_resolve_source` 的内部 guard 去重。

预期收益：减少 600-850 有效行。

### 阶段4：压缩 `adapter` 与 JSON 解析链

目标：把 loader/harness/config 中重复 JSON 字段读取收敛到少量清晰工具。

1. `adapter/loader.py`
   - 合并 `_load_tool_call`、`_load_tool_result`、`_load_cost` 到 `_load_step` 局部构造，除非多个入口复用。
   - `_required_str`、`_optional_object` 与 `utils.get_object`/新增 `json_object` 统一。
   - `parse_constraint`、`parse_milestone`、`parse_minefield` 中重复 `ensure_json_object` 调用前移到各 parse 入口。
   - `load_trajectory` 保留为 public 入口，不删除。
2. `harness/config.py`
   - `_read_json_object` 与 `_read_json_array` 合并为带 expected type 的 `_read_json_file`。
   - `_optional_str`、`_optional_bool`、`_optional_non_negative_float` 只保留确实多处复用者。
   - run_id 构造逻辑保留在 `load_harness_run_configs` 主流程附近，不再拆过细。
3. `adapter/toolsandbox/scorer.py`
   - 合并 `_namespace_schema`、`_restore_namespace_schema`、`_restore_sandbox_schema` 中 dataframe schema 恢复分支。
   - `_normalize_sandbox_target_rows` 与 `_serialize_target_tool_trace` 合并为局部转换块。
   - `_snapshot_constraint_kwargs` 与 `_column_similarities` 保留外部 `tool_sandbox` 边界校验，但删除单次 wrapper。
4. `adapter/toolsandbox/harness.py`
   - 合并 `_role_for_recipient` 与 `_role_by_name` 的查找逻辑。
   - `_context_max_sandbox_message_index` 与 `_last_column_value` 中 dataframe 行读取复用局部函数。
   - 不改变 session 生命周期和 teardown 行为。

预期收益：减少 450-650 有效行。

### 阶段5：压缩 `stage`、`progress`、`judges`、`llm`

目标：处理剩余中等收益区域。

1. `evaluate/settlement.py`
   - 继续检查 `_low_score_dimensions`、`_aggregate_stage_status` 等 helper 是否还值得保留。
   - finish settlement 与 checkpoint settlement 的 metadata 构造可共享局部块，但不能新增纯转发函数。
   - 保留入口校验，删除内部 helper 对同一对象的重复 None/type guard。
2. `stage/spec.py` 与 `stage/goal.py`
   - 合并 `_extend_by_constraint`、`_extend_by_stage_goal_text` 中重复维度加入逻辑。
   - 只在 stage spec 入口保留 graph/spec 空值校验。
3. `progress.py`
   - 保留对外 `TqdmCaseProgressManager` 与 `QueueProgressReporter`。
   - 合并 `_create_bar`、`_refresh_bar`、`_finish_bar` 中重复 postfix/elapsed/total 操作。
   - 合并 `_title_text` 与 `_title_bar_format` 的标题构造。
4. `judges/base.py`、`judges/standard.py`、`judges/expensive.py`
   - 合并 result payload 解析、dimension score 默认补齐、pass metadata 构造。
   - 保留 `evaluate_stage` 方法签名，不删除继承接口参数。
5. `llm/openai.py`、`llm/anthropic.py`、`llm/base.py`
   - 收敛 response text/usage 读取模式。
   - provider hook 参数不按未用参数删除。

预期收益：减少 350-500 有效行。

### 阶段6：测试、指标与清理

必须执行：

```powershell
python -m compileall dynsteer
pytest
git diff --check
```

必须复核：

1. `dynsteer/**/*.py` 有效行数在 8200-8600 行。
2. 普通实现函数中不存在确定未使用入参。
3. 不存在未用 import 候选。
4. 不存在新增纯转发函数。
5. `adapter/base.py`、judge/LLM/harness 的继承接口参数保留。
6. 不存在只使用一次且不表达公共语义的简单 `Callable` 类型别名。
7. `quality.py` 与 `stage/settlement.py` 已删除，且无旧路径 import。
8. 源码、测试、display 目录下无 `__pycache__`，根目录无 `.pytest_cache`；不清理 `.venv` 内部缓存。

## 7. 重点测试范围

现有测试必须全部通过：

1. `tests/test_algorithm_revision.py`
2. `tests/test_agent_step_closure.py`
3. `tests/test_progress.py`

建议新增或补强：

1. loader 回归：覆盖 `parse_task_case`、`load_trajectory`、legacy required 字段拒绝。
2. diagnostics 回归：覆盖 pending milestone attempted/finally ready/predecessor blocked 三种诊断。
3. toolsandbox fake module loader 回归：覆盖 scenario graph、sandbox rows、snapshot conversion、custom scorer schema fallback。
4. progress 回归：覆盖完成态不回退、可见窗口 trim、close_all 静态行输出。
5. evaluate/stage 目录迁移回归：覆盖 evaluator 能从 `evaluate.settlement` 正常执行 checkpoint 与 finish settlement。

## 8. 不应处理或暂缓处理的内容

1. 不删除 `adapter/base.py` 抽象接口的未用参数。
2. 不删除 judge/LLM provider hook 的未用参数。
3. 不删除 `__init__.py` re-export，除非先明确包外导入没有依赖。
4. 不在第二轮大改 `model.py` 的 dataclass schema；它应放到第三轮独立处理。
5. 不删除用户已有计划文档，不清理 `.venv` 内缓存。
6. 不为 `stage/settlement.py` 或 `evaluate/quality.py` 保留兼容 re-export 文件，除非实施前发现仓库外强依赖且用户明确要求兼容。

## 9. 第二轮完成后的第三轮方向

如果第二轮达成 8200-8600 行，第三轮再处理：

1. `model.py` dataclass schema 合并和 `to_dict` 重复逻辑压缩。
2. 全局 JSON 序列化/反序列化工具统一。
3. judge/LLM 响应 schema 的统一抽象。
4. toolsandbox scorer 与 adapter 的外部依赖边界测试补强。

第三轮目标是从 8200-8600 行压到 7400-7800 行。

## 附录A. 项目中没有把握实现的模块部分

1. `adapter/toolsandbox` 与外部 `tool_sandbox` 包存在动态类、Pyo3 异常、原生 context、polars schema 等外部边界。没有真实 ToolSandbox 集成环境时，只能通过 fake module loader 和本地单测验证主路径，不能完全确认全部原生角色初始化和 dataframe schema 恢复路径。
2. `model.py` 是领域 schema 边界，第三轮若压缩 dataclass 与 `to_dict` 逻辑，可能影响结果 JSON 结构、display 构建、历史结果兼容。第二轮不建议触碰它的核心结构。
3. LLM judge 与 provider response 解析依赖真实 OpenAI/Anthropic 响应对象。无真实 API 配置时只能验证接口和 fake response，不能完全覆盖线上 SDK 响应差异。
4. 部分 public 函数如 `load_trajectory`、`parse_task_case`、日志缓冲访问函数可能被命令行或外部脚本调用。即使仓库内静态扫描显示低频，也应保留入口或在方案执行前再次确认外部调用。
