# Stage Goal API

`dynsteer.stage_goal.generate_stage_goals(task_case, mode="auto", llm_provider=None)` 是 stage_goals 的唯一生成入口。

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
