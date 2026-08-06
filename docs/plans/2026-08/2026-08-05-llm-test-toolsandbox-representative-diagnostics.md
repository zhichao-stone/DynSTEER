# llm_test.py ToolSandbox 请求诊断改造方案

## 目标

让 `llm_test.py` 的测试请求更接近 ToolSandbox 的 agent/user 多轮调用，用于区分模型服务延迟、请求 payload 规模、连续多轮交互和重试导致的耗时。

## 修改范围

- 扩展 `llm_test.py`：分别加载 agent 与 user 的模型配置；支持连续多轮对话和带工具定义的请求。
- 每轮记录耗时、HTTP/SDK 异常和成功率；默认关闭 SDK 自动重试，避免隐藏真实失败。
- 保留现有简短单轮测试作为基线，新增命令行参数控制模型、轮数和工具请求。

## 未覆盖部分

- 不直接启动 ToolSandbox benchmark，不复现其具体场景状态机；真实 case 仍需通过实验日志确认。
- 不修改实验运行时的重试策略或远端环境配置。

## 验收

- `uv run python llm_test.py --help` 可执行。
- 默认运行能分别测试 deepseek agent 与 qwen user，并输出基线、多轮和工具请求的耗时。
- 代码不写入 API key，优先读取 experiment JSON 或环境变量。
