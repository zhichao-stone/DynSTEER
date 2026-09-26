# Milestone Minefield Recall 阈值与 Prompt Fatal Audit 计划

- 日期：2026-09-26
- 范围：`dynsteer/milestone/compiler.py`、`tests/milestone/test_generation_stability.py`、`dynsteer/prompt/templates/milestone/generation.en.md`、`dynsteer/prompt/templates/milestone/generation.zh.md`
- 基线：P0/P1 修复后的保存响应重放结果：fatal exact P/R/F1 = 88.10% / 37.76% / 52.86%。

## 1. 方案 A：分层 fatal tool identity 阈值

只调整 minefield fatal identity `(turn_id, evidence_id)` 的进入阈值，普通 graph node 聚合仍保持严格多数。

1. 聚合候选先根据 evidence 对应 tool contract 判断写入属性。
2. readonly tool：支持度 `support / observation_count >= 1/3` 即进入 reason 仲裁。
3. write tool：支持度 `support / observation_count >= 1/2` 才进入 reason 仲裁。
4. reason 仲裁、missing input 校验和 minefield ID 生成逻辑保持不变。
5. minefield metadata 记录 `support_policy`，便于离线区分两种阈值来源。

预期保存响应重放：fatal exact TP/Pred/Ref = 51/60/98，P/R/F1 = 85.00% / 52.04% / 64.56%；operation 指标不受 minefield 阈值影响。

## 2. Prompt 方向 2：候选图返回前 fatal audit

在不改变 JSON schema、不硬编码 case 和工具名的前提下，中英文主生成模板增加统一的 fatal-tool audit 要求：

1. 对每个 non-executable 或信息受阻 turn，先识别真实 terminal 操作或关键 producer。
2. 检查 required dynamic inputs、schema required 参数和唯一目标选择字段能否由公开输入与本图唯一输出 producer 闭合。
3. 无法闭合时不得编造 completion，应输出有契约依据的 fatal minefield。
4. 只有能精确列出不可得字段时使用 `missing_required_input`；readonly fatal 错误调用用 `unsafe_tool_call`；破坏性写工具用 `unsafe_side_effect`。
5. 空图、`needs_clarification` 或 `response_only` 不能替代 fatal audit。

中英文模板同步增加 final checklist 项。该修改无法用保存响应重放验证，需要重新调用 LLM 实验后评估。

## 3. 验收

1. 新增/更新测试：
   - readonly fatal 支持 2/6 可进入聚合；
   - write fatal 支持 2/6 仍被拒绝；
   - write fatal 支持 3/6 仍可进入聚合。
2. 运行 `tests/milestone` 与 `py_compile`。
3. 使用当前 509 个保存 response、不调用 LLM，重放方案 A 阈值效果。
4. 检查相关文件 `git diff --check`。
5. prompt 修改只做模板同步检查，不将其效果混入保存响应重放结论。

## 附录A. 项目中没有把握实现的模块部分

1. prompt audit 能提升多少真实 recall 无法从保存响应推断，且可能引入新的 false positive；需要重新实验验证。
2. readonly/write 只按 contract 写入属性分层，若个别 benchmark contract 未声明 `writes`，阈值会落入 readonly 分支；当前 Toolsandbox contract 数据已由保存响应重放验证。
3. 方案 A 的模拟值依赖当前 response 集合；新实验样本变化后，precision 与 recall 提升幅度可能不同。
