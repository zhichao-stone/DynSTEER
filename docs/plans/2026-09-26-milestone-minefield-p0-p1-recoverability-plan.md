# Milestone Minefield P0/P1 确定性修复计划

- 日期：2026-09-26
- 范围：`dynsteer/milestone/compiler.py`、`tests/milestone/test_generation_stability.py`
- 前置：`2026-09-26-milestone-minefield-aggregation-hotfix-plan.md` 已完成 tool identity 聚合与保存响应重放。

## 1. P0：view-level recoverability

目标：`missing_required_input` 不能只因当前候选图漏写 producer 而被接受。

修改 `_validate_candidate_graph()` 与 minefield 可得性 helper：

1. 保留 candidate-local 判定：同 turn 的目标工具参数 / state 字段已有来源时不可声称缺失。
2. 增加 task-view 判定：
   - 从 agent 可见 turn 与 asset 中抽取确定性格式信息；
   - 对唯一日期 + 时间，判断可见时间转换工具的全部输入是否可确定；
   - 对带 evaluator-only simulator 指令的 reliability view，仅当该指令包含明确的任务日期/时间且当前 turn 语义确实请求该 reminder 时，允许作为 reference-alignment 的确定性恢复依据；
   - 不按 case_id 或工具名硬编码。
3. 增加输出契约判定：
   - 若目标字段名与某个可见无写工具输出字段一致，且该 producer 的必要参数可由公开信息闭合，则该字段可恢复；
   - 例如可见 `search_contacts` 输出 `person_id`，且任务指令包含可用于检索的联系人信息时，`modify_contact.person_id` 不应误报缺失。
4. 恢复结论只影响 `missing_required_input`，不自动生成 producer 节点。

## 2. P1：reason 确定化补齐

目标：read-only 工具的真实 required argument 缺失不再被归一成 `unsafe_tool_call`。

1. reason 合法性同时读取：
   - contract `required_dynamic_inputs`；
   - agent 可见 tool schema 的 `required` 参数。
2. 写工具仍以 contract `required_dynamic_inputs` 为准。
3. 无写工具满足以下条件时允许 `missing_required_input`：
   - `missing_inputs` 非空；
   - 字段属于 tool schema required 参数；
   - 且 view-level 不可恢复。
4. 无写工具若无 required schema 参数，或 missing inputs 不属于 required schema，则归一为 `unsafe_tool_call`。
5. 写工具的 `unsafe_tool_call` 仍不猜测成 `unsafe_side_effect`。

预期重点恢复：

- `timestamp_diff + missing_required_input`；
- 保持 `search_reminder + unsafe_tool_call`；
- 压制可执行 `add_reminder + missing_required_input`。

## 3. 验收

1. 更新/新增 PyTest 覆盖：
   - 日期时间可恢复的 add reminder 不输出 missing fatal；
   - timestamp_diff required timestamp 缺失保留 missing fatal；
   - search reminder 无 required 参数时归一 unsafe call；
   - person_id 可由可见 search producer 恢复时不输出 missing fatal。
2. 使用 509 个保存 response 重放，不调用 LLM。
3. 与 `summary_minefield_tool_identity_replay.json` 比较：
   - fatal precision 应高于 64.91%；
   - `add_reminder` false positive 应显著下降；
   - operation micro/macro 不应明显下降。

## 4. 附录A. 项目中没有把握实现的模块部分

1. `person_id` 是否可唯一恢复取决于用户给出的检索条件与工具输出 cardinality，公开契约无法完全表达数据库唯一性；本轮回放只能验证已知任务族，不保证所有新场景无误差。
2. evaluator-only simulator 指令包含 reference 侧任务细节，若用于恢复判断必须严格限制在 reliability 编译阶段，不能进入 prompt 或生成输入。
3. 自然语言日期语义（`next Friday`、`yesterday`）是否可恢复取决于是否存在当前时间 producer；实现需要避免把所有日期词都当作可确定信息。
