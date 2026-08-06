# AgentCompass 可选依赖修复方案

## 目标

让 DynSTEER 在未使用 `swebench_pro`/`skillsbench` 时不要求安装 AgentCompass；使用这两个 benchmark 时，通过显式 extra 安装，并在真正执行 benchmark 的依赖边界报告缺失依赖。

## 修改范围

1. 将 `agentcompass`、`rich`、`tabulate` 从基础 dependencies 移到 `agentcompass` optional extra。
2. 将 AgentCompass 第三方导入集中到 `dynsteer.adapter.agentcompass.runtime` 的专用依赖边界函数，普通 registry 导入不触发第三方导入。
3. 保留 registry 的显式 adapter/harness 注册；缺少可选依赖时，只有实际调用 AgentCompass task catalog 或 case 执行才报安装指引。
4. 更新 README/API 文档，说明 extra、上游 benchmark requirements、Python 版本和当前 OpenAI SDK 兼容限制。
5. 增加无 AgentCompass 导入测试和依赖边界测试。

## 验收

- 基础环境不安装 AgentCompass 时，`import dynsteer.adapter.registry` 成功。
- 安装 AgentCompass extra 后，两个 adapter/harness 可被 registry 发现。
- 30 项既有适配测试继续通过，新增分支覆盖率不低于 80%。
- 不修改 AgentCompass、ToolSandbox 或现有用户文档。

## 附录A. 项目中没有把握实现的模块部分

固定 AgentCompass 提交的 `pyproject.toml` 未声明完整运行依赖，且其 builtin registry 会导入所有 benchmark；同时 DynSTEER 当前基础环境固定 `openai==1.17.0`，而 AgentCompass app requirements 要求 `openai>=2.41.1`。因此本修复只负责可选导入边界，不伪装为已经解决上游依赖冲突；真实运行仍需独立、按 AgentCompass requirements 准备的环境。
