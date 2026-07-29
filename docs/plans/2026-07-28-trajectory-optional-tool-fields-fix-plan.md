# 轨迹工具字段可选解析修复方案

## 1. 问题目标

DynSTEER-Replay 读取 default `trajectory.json` 时，`dynsteer.adapter.loader._load_step(...)` 把 `tool_call` 与 `tool_result` 都作为必填 JSON 对象解析。实际轨迹中普通消息 step 的两个字段通常为 `null`，因此会触发 `ValueError: 输入必须是 JSON 对象`。

本次修复目标是让轨迹 loader 正确支持可选工具字段：字段为 `null` 或不存在时解析为 `None`，字段存在但不是 JSON 对象时继续抛出结构化错误。

## 2. 修改范围

- `dynsteer/adapter/loader.py`
  - 新增 `_optional_json_object(...)`，用于区分可选字段缺失与非法类型。
  - `_load_step(...)` 中 `tool_call`、`tool_result` 改为可选解析。
  - `_load_step(...)` 中 `cost.tokens`、`cost.latency_ms` 支持 `null`，与输出端写出的 JSON 保持一致。
  - `_load_step(...)` 中 `actor`、`event_type` 改为直接读取原始字段并交给枚举解析，避免无类型调用 `get_object(...)`。
  - `load_trajectory(...)` 中 `final_state`、`metrics` 改为可选 JSON 对象解析，避免 tuple 类型校验触发工具函数错误。
  - 保持 `ToolCall.name` 必填校验，保持 `ToolResult` 的 `success/content/exception` 字段语义。

## 3. 验证方式

- 使用项目 `.venv` 对 `dynsteer.adapter.loader` 编译检查。
- 读取最近 default `trajectory.json` 并调用 `load_trajectory(...)`，确认普通 message step 的 `tool_call=None`、`tool_result=None` 能正常解析。
- 使用 `python -B -c "import main"` 确认主入口导入不受影响。

## 附录A. 项目中没有把握实现的模块部分

暂无没有把握的实现部分。本次改动仅调整 JSON 反序列化对可选字段的处理，不改变轨迹模型、评估逻辑或输出 JSON 格式。
