# 2026-07-23 ToolSandbox 异常评估结果代码修复方案 v2

## 1. 修订原则

本方案替代 `2026-07-23-toolsandbox-evaluation-anomaly-code-fix-plan.md`。修订后的原则如下：

1. **不在 `settlement.py` 增加冲突保护。** StandardJudge 本来就是为了复核和弥补结构化 scorer 的局限，不能在结算层用 structured scorer 结果反向覆盖 StandardJudge。
2. **不让 `_constraint_checks()` 变臃肿。** 保持当前字段结构基本不变，不新增 `actual_summary`、`expected_summary`、`structured_pass` 这类成组冗余字段。
3. **只修 StandardJudge 判断依据。** 问题核心是 StandardJudge 的证据输入与评估规则不够精准，因此应加强 prompt 上下文构造与评估逻辑，让 StandardJudge 自己判对。
4. **证据要少而准。** 对状态类约束，不扩大 JSON 体积，而是把 `actual_excerpt` / `expected_excerpt` 改成面向目标的短证据，优先展示与 expected 相关的 actual 行。

## 2. 问题本质

`update_contact_relationship_with_relationship` 的 m1 异常不是因为 structured scorer 缺失，而是 StandardJudge 看到了不合适的状态摘要：

- 当前 `_constraint_checks()` 只给 `actual_excerpt`，该字段由 `constraint_actual_excerpt()` 对完整 actual rows 直接做 420 字符截断。
- CONTACT 表中目标行可能位于截断后的后半部分。
- StandardJudge 因“看不到目标行”得出“状态未满足”的结论。
- 实际上 `constraint_scores.actual` 和 `trajectory.final_state.CONTACT` 都包含两名目标联系人且 relationship 已为 `enemy`。

因此修复重点应是：**让 `actual_excerpt` 本身变得更聪明，而不是给 `_constraint_checks` 增加更多字段，也不是在 settlement 层兜底。**

## 3. 修复目标

1. `_constraint_checks()` 仍保持轻量字段：
   - `constraint_id`
   - `constraint_goal`
   - `satisfied`
   - `score`
   - `missing`
   - `hard`
   - `expected_excerpt`
   - `actual_excerpt`
   - `short_evidence`
2. `actual_excerpt` 对状态类约束必须优先展示 expected 相关 actual rows，而不是从 actual rows 开头机械截断。
3. StandardJudge prompt 明确要求：
   - 不能因为 excerpt 省略或截断而推断目标行不存在。
   - 如需推翻结构化证据，必须指出 `actual_excerpt`、`short_evidence` 或 steps 中的具体矛盾，而不是引用“未看到”。
   - 对 state_snapshot 阶段，`constraint_checks` 是状态证据入口；steps 仍用于审计工具调用、交互质量、效率与行为合理性。
4. `modify_contact_with_message_recency_alt_10_distraction_tools` 的 m3 仍应失败，但诊断应能表达“目标行存在但额外状态污染/非目标修改导致失败”。
5. 不改变现有 dynamic weight、policy stop、semantic message review 机制。

## 4. 文件级修复方案

### 4.1 `dynsteer/evaluate/semantic.py`

修改现有 `constraint_actual_excerpt()` 和必要的私有 helper，不新增大体量 summary API。

#### 当前问题

当前实现逻辑近似为：

```python
return compact_text(json_safe(actual), limit)
```

这对多行 CONTACT / REMINDER / MESSAGING 状态不合适，因为它按原始顺序截断，而不是按评估目标选证据。

#### 修改方案

新增内部 helper：

```python
def _focused_state_excerpt(
    constraint: Constraint,
    actual: JsonValue,
    limit: int,
) -> str | None:
    """为 state_snapshot 约束生成目标导向的 actual 摘要。"""
```

仅在以下条件满足时启用 focused excerpt：

- `constraint.stage_goal_semantics.kind` 为 `set_state` 或 `preserve_state`；或
- `constraint.target.value == "state_snapshot"` 且 actual/expected 可解析为 rows。

focused excerpt 内容保持短文本，不新增 JSON 字段：

- `actual_rows=<n>`
- `expected_rows=<m>`
- `matched_expected_rows=<k>/<m>`
- `relevant_actual_rows=[...]`
- `omitted_actual_rows=<n>`，仅在有省略时出现

关键规则：

1. expected rows 从 `constraint.expected` 中提取，支持 dict、`{"rows": [...]}`、list。
2. actual rows 从 `score.actual` 中提取，支持 dict、`{"rows": [...]}`、list。
3. 对每个 expected row，优先用稳定标识符匹配 actual row：
   - CONTACT：`person_id` 优先，其次 `name`、`phone_number`
   - REMINDER：`reminder_id`
   - MESSAGING：`message_id`，其次 phone/content 组合
   - SETTING：`device_id`
   - 通用：expected row 中出现的非空字段
4. `relevant_actual_rows` 优先包含：
   - 与 expected 标识符命中的 actual rows；
   - 若没有命中，则包含与 expected 共享最多字段值的 actual rows；
   - 对 update/preserve 失败场景，可额外包含疑似被改动的同 namespace rows，但总量受限。
5. 如果 expected row 已命中但某些字段值不同，excerpt 必须展示这些字段差异。
6. 如果 expected row 全部命中但 scorer 仍低分，excerpt 应带出 `matched_expected_rows=<m>/<m>`，让 StandardJudge 能进一步判断是否是额外污染或 reference 约束问题。

不建议新增 public dataclass 或扩展 `ConstraintScore` 模型。所有逻辑封装在 `semantic.py` 内部 helper 中，供现有 `constraint_actual_excerpt()` 调用。

### 4.2 `dynsteer/prompt/judge.py`

保持 `_constraint_checks()` 字段数量不变，只调整字段内容来源：

```python
"expected_excerpt": constraint_expected_excerpt(constraint),
"actual_excerpt": constraint_actual_excerpt(constraint, score),
```

`constraint_actual_excerpt()` 修复后，`_constraint_checks()` 自动获得更精准的状态证据。

同时调整 `short_evidence` 的截取策略：

- 当前只取 `score.evidence[:2]`。
- 对 ToolSandbox state constraints，第二条经常是 reference snapshot 诊断，缺少 actual 侧信息。
- 建议保留最多 3 条，但仍叫 `short_evidence`，不新增字段：
  - scorer 分数行；
  - reference 诊断行；
  - 若 `actual_excerpt` 中无法放下完整关键信息，则补一条 `actual rows focused excerpt available in actual_excerpt` 没有必要；优先不加。

如果担心 prompt 变长，可不改 `short_evidence` 条数，只修 excerpt。

### 4.3 `dynsteer/prompt/templates/judge/standard.en.md`

修改 StandardJudge 规则，不增加代码兜底。

建议替换/补充以下规则：

1. `constraint_checks` 不是“替代 steps 的判决”，而是 **state_snapshot 的结构化状态证据入口**。
2. 对 state_snapshot 约束：
   - 使用 `expected_excerpt` 与 `actual_excerpt` 比较状态目标是否达成。
   - `actual_excerpt` 可能是 focused excerpt，不是完整状态表。
   - 不得因为 excerpt 没展示某行就断言该行不存在；只有当 excerpt 明确列出 unmatched/mismatched row 时才能据此扣分。
3. 若 `constraint_checks[i].satisfied=true` 且 `score >= threshold`：
   - 可以继续审计 steps 是否存在行为矛盾、越权、效率问题；
   - 但若要把 progress/state_consistency 判低，必须引用具体矛盾，例如 tool_result 失败、actual_excerpt 明确字段不符、或后续状态被反向修改。
4. 若 `constraint_checks[i].satisfied=false` 但 `actual_excerpt` 显示 expected row 存在：
   - 不要简单诊断“目标不存在”；
   - 应检查是否是 extra rows、额外修改、reference drift、guardrail 失败或 scorer 语义更严格。

中文模板 `standard.zh.md` 做同等修改。

### 4.4 `dynsteer/evaluate/diagnostics.py`

不新增大 JSON 诊断字段，只让现有失败摘要使用改进后的 excerpt。

修改点：

- `_constraint_failure_detail()` 继续保留 `actual_excerpt` 字段。
- 因 `constraint_actual_excerpt()` 已变为 focused excerpt，失败摘要会自然包含：
  - matched expected rows 数量；
  - relevant actual rows；
  - omitted rows；
  - 目标行存在但 scorer 低分的线索。
- `_constraint_failure_line()` 增加轻量文本规则：
  - 如果 `actual_excerpt` 包含 `matched_expected_rows=<m>/<m>` 且 score 低于阈值，输出“目标相关行已出现，但结构化评分仍未通过，需检查额外状态变更或 reference 约束”。

这样不需要额外 `actual_summary` 字段，也能改善 `modify_contact...` 的诊断。

### 4.5 明确不修改 `dynsteer/evaluate/settlement.py`

本轮不在 `_evaluate_stage()` 中加入 structured-state pass guard，也不在 policy stop 前加例外逻辑。

理由：

- StandardJudge 是评估逻辑的一部分，不能在后处理层用 structured scorer 直接覆盖。
- 如果 StandardJudge 判断错误，应修复其输入与评估规则。
- settlement 层只负责合并评估结果、更新权重与终止策略，不应该知道 ToolSandbox CONTACT 表的证据裁剪细节。

## 5. 针对两个异常 case 的预期变化

### 5.1 `update_contact_relationship_with_relationship`

修复后，m1 的 `actual_excerpt` 应类似：

```text
actual_rows=4; expected_rows=2; matched_expected_rows=2/2; relevant_actual_rows=[
  {"person_id":"9e137f06-916a-5310-8174-cf0b7e9f7054","name":"Fredrik Thordendal","relationship":"enemy",...},
  {"person_id":"a22e1984-6c6c-530c-8831-c3ea3b5138e7","name":"John Petrucci","relationship":"enemy",...}
]; omitted_actual_rows=2
```

StandardJudge 此时仍可以批评 agent：

- 只显式调用了一次 `modify_contact`，但状态结果显示两人都变更，轨迹可观测性有缺口；
- 未向用户发出 “All your friends are now your enemies” 的最终消息；
- m2 可能仍失败。

但它不应再把 m1 的 state target 判断为“没有目标行”。

预期结果：

- m1 不再因截断证据被判 `progress=0/state_consistency=0`。
- 不应在 m1 触发 `evaluation_policy_stop`。
- 若没有 m2 用户可见成功消息，最终仍应 partial，first failure 应落到 `m1->m2` 或对应 pending message stage。

### 5.2 `modify_contact_with_message_recency_alt_10_distraction_tools`

修复后，m3 的 `actual_excerpt` 应能展示：

- 目标 `Homer S` 已有 `phone_number=+10293847563`；
- 但 `Tomas Haake` 也被改成同一号码；
- 后续还新增/修改了 `Bart`；
- actual CONTACT row count 从 4 变为 5。

预期结果：

- m3 仍失败或至少不应被修成 full。
- 诊断从“需要让 CONTACT 状态达到目标值”改为“目标相关行存在，但存在额外 CONTACT 污染/非目标联系人修改，导致 update_similarity 未通过”。

## 6. 测试方案

当前工作区 `tests/` 目录不存在，且 git status 显示历史测试文件处于删除状态。落地时建议新增最小测试，不依赖真实 LLM。

### 6.1 `tests/test_constraint_actual_excerpt.py`

覆盖：

1. CONTACT actual rows 中目标行排在后部时，`constraint_actual_excerpt()` 仍优先展示目标行。
2. 多个 expected rows 均命中时，excerpt 包含 `matched_expected_rows=2/2`。
3. expected row 存在但有额外 actual rows 时，excerpt 包含 `omitted_actual_rows` 或 row count 信息。
4. expected row 不存在时，excerpt 包含最接近的 actual rows，而不是空泛截断。

### 6.2 `tests/test_standard_prompt_state_rules.py`

覆盖：

1. `build_judge_prompt("standard", ...)` 生成的 prompt 中保留轻量 `constraint_checks` 字段，不出现 `actual_summary/expected_summary/structured_pass`。
2. prompt 模板包含“不得因 excerpt 未展示某行就断言不存在”的规则。
3. state_snapshot prompt 中 `actual_excerpt` 包含 focused rows。

### 6.3 `tests/test_diagnostics_focused_excerpt.py`

覆盖：

1. pending/fail 诊断使用 focused `actual_excerpt`。
2. 当 excerpt 中出现 `matched_expected_rows=1/1` 但 score 低于阈值时，failure line 指向额外状态变更或 reference 约束，而不是“目标不存在”。

### 6.4 回归测试命令

```powershell
uv run pytest tests/test_constraint_actual_excerpt.py tests/test_standard_prompt_state_rules.py tests/test_diagnostics_focused_excerpt.py
```

若恢复完整测试目录，再运行：

```powershell
uv run pytest
```

## 7. 验收口径

### 7.1 静态验收

- `_constraint_checks()` 不新增 `actual_summary/expected_summary/structured_pass` 等大字段。
- `settlement.py` 没有新增 structured-state pass guard。
- `constraint_actual_excerpt()` 对 state_snapshot 约束生成目标导向 excerpt。
- StandardJudge prompt 明确要求不能基于截断/省略做不存在推断。
- diagnostics 能表达“目标行存在但额外污染导致失败”。

### 7.2 结果验收

重新评估当前 10 个 ToolSandbox case 后：

| case | 修复后预期 |
|---|---|
| `update_contact_relationship_with_relationship` | m1 不再因 CONTACT actual excerpt 截断而被判 fail；不在 m1 早停 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | m3 仍不应 full；诊断说明额外 CONTACT 污染 |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | 保持 full |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | 保持 full |
| `turn_on_cellular_low_battery_mode` | 保持 full |
| `find_days_till_holiday_wifi_off_alt` | 若仍缺 `get_current_timestamp`，保持 partial |

## 8. 实施步骤

1. 修改 `dynsteer/evaluate/semantic.py::constraint_actual_excerpt()`，增加 state_snapshot focused excerpt 私有逻辑。
2. 如有必要，轻微调整 `constraint_expected_excerpt()`，让 expected rows 也更聚焦但仍保持短文本。
3. 保持 `dynsteer/prompt/judge.py::_constraint_checks()` 字段结构不变，只沿用新的 excerpt。
4. 修改 `standard.en.md` 和 `standard.zh.md` 的 state evidence 规则。
5. 修改 `diagnostics.py::_constraint_failure_line()`，识别 focused excerpt 中的 `matched_expected_rows` 线索。
6. 新增 3 组窄域测试。
7. 运行窄域 pytest。
8. 重新评估当前 10 个 case，重点核查两个异常 case 的 report。

## 9. 风险与控制

| 风险 | 控制方式 |
|---|---|
| focused excerpt 仍遗漏关键行 | expected identifier 优先匹配，并用测试覆盖目标行后置场景 |
| excerpt 过长 | 只输出 relevant rows 和 row count，不输出完整状态表 |
| StandardJudge 仍误判 | prompt 中明确“不能基于省略推断不存在”，并要求推翻结构化证据时引用具体矛盾 |
| 真实失败被误修正 | 不加 settlement guard；StandardJudge 仍保留最终判断权 |
| `modify_contact...` 被误判 full | focused excerpt 会展示额外 CONTACT 污染，StandardJudge 应继续扣分 |

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox 原生 `update_similarity` 的完整内部语义。**
   - 没有把握原因：该函数由外部 ToolSandbox 模块提供。v2 方案不修改其评分，只改善 StandardJudge 所见 evidence。
2. **`update_contact_relationship_with_relationship` 中 raw index 20/22 缺口来源。**
   - 没有把握原因：当前轨迹导出省略了部分 raw index，而 final state 显示 CONTACT 在 `sandbox_message_index=22` 更新。该问题属于可观测性增强，不纳入本次最小修复。
3. **LLM judge 重跑后的精确数值。**
   - 没有把握原因：StandardJudge 存在非确定性。验收以 m1 不再因 evidence 截断误判、诊断语义正确为准。

