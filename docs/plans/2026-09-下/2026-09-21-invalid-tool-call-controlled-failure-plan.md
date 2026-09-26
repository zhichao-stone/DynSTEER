# 2026-09-21 非法 Tool Call JSON 受控失败方案

## 背景

ToolSandbox 实验中，模型偶发返回不符合 JSON 语法的 `tool_call.function.arguments`。第三方 `OpenAIAPIAgent.respond()` 在 `json.loads()` 时抛出 `JSONDecodeError`；DynSTEER 当前重试 3 次后仍会把异常传播到统一实验 runner，导致整轮实验中断。

## 目标

将模型非法 tool call 参数视为该 case 的受控 agent 失败，而不是基础设施异常。保留现有重试机制；重试耗尽后结束当前 ToolSandbox session，写入已有轨迹和终止原因，继续执行后续 case。

## 修改方案

在 `dynsteer/adapter/toolsandbox/harness.py` 的 `_respond_with_retry()` 中单独捕获 `json.JSONDecodeError`：

- 记录结构化中文错误日志。
- 将 `session.finished` 置为 `true`。
- 将 `termination_reason` 设为 `agent_invalid_tool_call`。
- 将 `stop_reason` 记录为非法 JSON 的解析错误摘要。
- 将 `stop_reason` 放入 `termination_detail.stop_reason`，便于从 `summary.json` 筛出受控失败 case。
- 不向 `_run_worker_group()` 抛出异常，使当前 case 按原生评估流程收敛为结果文件。

## 验证

构造一个 `respond()` 连续抛出 `JSONDecodeError` 的假 session 和假 role，验证：

1. `_respond_with_retry()` 执行 3 次后不再抛出异常。
2. session 被标记为完成。
3. 终止原因为 `agent_invalid_tool_call`。

## 附录 A. 项目中没有把握实现的模块部分

- 第三方 `respond()` 可能同时包含多个 tool call；根据当前实现，`response_messages` 会在全部 tool call 解析成功后才写入 context，因此失败时通常不会留下半写入消息。该判断依赖第三方当前实现，若后续版本改为逐条写入，需要重新评估失败后的 context 快照策略。
