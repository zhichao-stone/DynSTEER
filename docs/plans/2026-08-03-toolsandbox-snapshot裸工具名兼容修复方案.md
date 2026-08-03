# ToolSandbox snapshot 裸工具名兼容修复方案

## 1. 问题与目标

提交 `6f85a98` 将 ToolSandbox SANDBOX 工具调用统一为严格解析：仅接受结构化 `tool_trace`、`openai_function_name` 或完整 `name(...)`。但 ToolSandbox 官方 scenario 的 snapshot constraint 会使用 `content="timestamp_diff"` 这种裸工具名表达 minefield，并配合 `column_contains_similarity` 完成原生匹配。该约束不是运行轨迹中的普通消息，不能直接套用运行轨迹的严格格式。

本次目标是在不恢复“从任意文本提取首个英文单词”、不生成 `tool_name="unknown"` 的前提下，兼容 snapshot constraint 中完整匹配工具标识符的裸字符串。

## 2. 修改方案

### 2.1 `dynsteer/adapter/toolsandbox/utils/scenario.py`

在 `_sandbox_tool_call_semantics()` 中保留现有 route-first 判断和 `tool_call_from_agent_row()` 调用。

仅当满足以下全部条件时，将 `content` 作为 constraint 工具名：

1. route 是 `AGENT -> EXECUTION_ENVIRONMENT/ENVIRONMENT`；
2. 结构化 trace、`openai_function_name` 和完整 `name(...)` 均未解析出工具调用；
3. `content.strip()` 完整匹配 ASCII 工具标识符 `[A-Za-z_][A-Za-z0-9_]*`。

匹配成功时生成 `{"name": <裸工具名>, "arguments": {}}`；普通句子、空字符串和混合文本仍返回 `None` 并沿用现有明确异常。

该兼容逻辑放在 scenario constraint 边界，不修改 `trace.py::tool_call_from_agent_row()`，避免真实运行轨迹中的普通文本被误判为工具调用。

### 2.2 `tests/test_toolsandbox_snapshot_constraint.py`

通过公开的 `constraint_from_snapshot_constraint()` 构造 SANDBOX constraint，覆盖：

1. `AGENT -> EXECUTION_ENVIRONMENT + content="timestamp_diff"` 映射为 `tool_call`；
2. 裸工具名两侧空白会被清理；
3. 普通文本仍抛出“缺少合法消息或工具证据”；
4. `tool_call_from_agent_row()` 对裸字符串仍返回 `None`，证明运行轨迹解析没有被放宽。

### 2.3 `docs/apis/stage_goal.md`

更正 ToolSandbox SANDBOX snapshot constraint 的工具证据说明：运行轨迹仍要求结构化 trace、`openai_function_name` 或完整 `name(...)`；`AGENT -> EXECUTION_ENVIRONMENT/ENVIRONMENT` 的目标约束额外允许完整匹配 ASCII 标识符的裸工具名。

## 3. 验收标准

1. `find_days_till_holiday_insufficient_information` 的 `mf0_c0` 能完成输入适配，工具名为 `timestamp_diff`。
2. 完整 `name(...)`、`tool_trace` 和 `openai_function_name` 的现有行为不变。
3. 普通消息内容不会被首词或子字符串误识别为工具调用。
4. 新增测试通过，相关模块可被 Python 编译。

## 4. 冗余与约束检查

- 不新增仅转调其他函数的中转接口。
- 不修改 ToolSandbox 上游数据。
- 不把 constraint 专用兼容逻辑扩散到运行轨迹解析。
- 不删除 `docs/constraints`、`docs/plans` 中已有文档。

## 附录A. 项目中没有把握实现的模块部分

无。本次输入格式可由当前 ToolSandbox 源码中的 scenario 定义直接确认，修改范围限定在 SANDBOX snapshot constraint 适配边界。
