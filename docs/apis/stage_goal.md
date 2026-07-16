# Stage Goal API

`dynsteer.stage.generate_stage_goals(task_case, mode="auto", llm_provider=None)` 是 stage_goals 的唯一生成入口。

所有 milestone 都必须生成 stage_goal。DAG 并行只表示依赖关系，不表示 optional milestone；loader 读取到 milestone `required` 字段会报错并要求重新生成 adapted case。

支持模式：

- `auto`: 优先使用通用 `Constraint.stage_goal_semantics` deterministic 生成；无法完整覆盖时回退 LLM。
- `semantic`: 只允许通用语义 IR 生成，任何约束缺失 IR 时抛出异常。
- `llm`: 直接使用 LLM 生成。
- `stored`: 校验并返回 `TaskCase.stage_goals`。

`stage_goal_semantics` 是 `Constraint` 的公共语义 IR，adapter 可以把 benchmark 私有 metadata 映射到该字段。公共 stage_goal 模块只识别通用 kind，不读取 benchmark 私有 metadata。

当前 kind 由 `dynsteer.model.StageGoalSemanticKind` 统一定义，落盘 JSON 使用其 `.value`：

- `StageGoalSemanticKind.SET_STATE.value == "set_state"`
- `StageGoalSemanticKind.PRESERVE_STATE.value == "preserve_state"`
- `StageGoalSemanticKind.EMIT_MESSAGE.value == "emit_message"`
- `StageGoalSemanticKind.TOOL_CALL.value == "tool_call"`

私有 scorer metadata 应保留在各 benchmark 自己的 key 下，例如 `metadata["toolsandbox"]`。公共 stage_goal 生成不得读取这些私有 key。

ToolSandbox SANDBOX snapshot constraint 如果 target row 包含 `tool_trace`，adapter 应将其 `stage_goal_semantics.kind` 设置为 `tool_call`，并写入 `tool_name` 与 `arguments`。`stage_goal` 文本应表达为 `Call tool ...`，而不是 `Emit a message from EXECUTION_ENVIRONMENT to AGENT...`。普通 AGENT -> USER 消息仍使用 `emit_message`。

## Stage Evaluation Spec API

`dynsteer.stage.generate_stage_evaluation_specs(task_case)` 是阶段聚焦评估维度的公共生成入口。输出 key 与真实 milestone 的 `stage_goals` key 保持一致，例如 `__start__->m0`、`m3->m4`；`__finish__` 不作为普通 milestone spec 生成。

每个 `StageEvaluationSpec` 包含：

- `focus_dimensions: list[Dimension]`：本阶段实际参与 judge、综合分、权重更新和策略升级的维度。
- `dimension_rationale: dict[Dimension, str]`：每个维度被纳入评估的原因。

生成规则：

- `progress` 与 `efficiency` 必选。
- `tool_call` 语义、`TOOL_CALL`/`TOOL_RESULT` 约束或公共工具 metadata 会加入 `tool_quality`。
- `set_state`、`preserve_state`、`STATE_SNAPSHOT`/`STATE_DELTA` 或跨 milestone reference 会加入 `state_consistency`。
- `emit_message` 或 `user_visible_required=true` 会加入 `interaction_quality`。
- guardrail、权限、敏感状态或安全相关 metadata 会加入 `safety`。
- 阶段目标文本包含修复、恢复、重试、失败或异常处理语义时会加入 `recovery`。

adapter 的职责是把 benchmark 私有字段归一化到公共 `Constraint.stage_goal_semantics`、`ConstraintTarget` 或 benchmark-neutral metadata；不应在 adapter 内维护另一套聚焦维度推导规则。
