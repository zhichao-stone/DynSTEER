# Milestone LLM 响应按 Case 聚合存储方案

## 1. 问题与目标

当前原始响应路径为：

```text
results/milestone/<exp_id>/llm_outputs/<run_id>/<benchmark>/<case_id>/path_<index>_<strategy>.txt
```

截图中的失败路径长度为 285 个字符，超过 Windows 常见的 260 字符路径限制，因此写入时出现 `FileNotFoundError: [Errno 2] No such file or directory`。这不是 LLM 生成失败，而是原始响应审计文件的路径设计过长。

本次目标改为：

```text
results/milestone/<exp_id>/<benchmark>/llm_outputs/<case_id>.json
```

每个 case 只写一个 JSON 对象，键为 `path_<两位 index>_<strategy>`，值为对应 LLM 原始响应。不再在路径中使用 `run_id`，也不再为每条 path 建立独立文件。

## 2. 修改范围

### 2.1 `dynsteer/milestone/compiler.py`

1. 将 `compile_task_case(..., response_output_dir)` 改为 `compile_task_case(..., response_output_file)`。
2. 单个 case 内维护 `dict[str, str]` 响应集合。
3. 每次 LLM 返回后、解析前，将新响应加入字典并覆盖写入同一个 UTF-8 JSON 文件，确保解析失败时也保留已完成路径。
4. JSON key 使用受控的 `path_<index>_<strategy>`，例如 `path_03_artifact-or-result-first`。
5. `path_summaries[].response_record` 同时记录聚合文件路径和该响应的 key，继续保留字符数、SHA-256、fence/object 标记。
6. 使用临时文件加原子替换，避免进程中断时把此前已保存的响应文件写坏。

### 2.2 `milestone_reliability.py`

1. 删除 LLM 输出路径中的 `run_id`。
2. 将原来的 `_response_output_dir()` 改为 `_response_output_file()`。
3. 返回路径固定为 `<run_dir>/<benchmark>/llm_outputs/<safe_case_id>.json`。
4. `run_id` 仍保留在逐 case 结果协议中，仅用于标识批次，不参与文件目录组织。

### 2.3 文档与测试

1. 更新 milestone API 和原始响应记录方案，删除旧的逐 path 文件及 run_id 路径说明。
2. 测试一个 case 的多条响应最终只生成一个 JSON 文件，并验证 key、原文、哈希和诊断一致。
3. 验证 reliability helper 返回用户指定的新目录层级。
4. 执行完整 milestone 测试、Python 语法检查和差异检查。

## 3. 新文件示例

```json
{
  "path_00_direct-shortest": "{...原始响应...}",
  "path_01_prerequisite-first": "```json\n{...}\n```",
  "path_02_state-check-first": "{...原始响应...}"
}
```

## 4. 安全与覆盖语义

- benchmark、case 文件名仍使用现有安全文件名规则，不使用 LLM 内容构造路径。
- 默认防覆盖机制保证已有实验目录不会被隐式修改；`--force` 会先清空整个 `<exp_id>` 目录，因此不需要用 `run_id` 隔离旧响应。
- 原始响应可能包含任务数据，继续仅保存于实验结果目录，不写入终端日志。
- 本次不修改 prompt 和 JSON 解析宽严度，只修复审计产物布局。

## 5. 验收标准

- 截图所示最长 case 的新路径显著短于旧路径；
- 一个 case 无论请求多少条路径，都只产生一个 LLM 输出 JSON 文件；
- JSON 中每个 path key 对应完整原始响应；
- JSON/schema 失败不会丢失已经返回的响应；
- 结果目录中不再出现 LLM 输出 `run_id` 层级和逐 path `.txt` 文件；
- 全部测试与语法检查通过。

## 附录A. 项目中没有把握实现的模块部分

本次实现没有未明确的核心模块。Windows 是否全局启用 long-path 支持取决于宿主系统，但新布局不依赖该配置；截图中的示例路径会从约 285 字符降至约 229 字符，直接避开当前失败点。
