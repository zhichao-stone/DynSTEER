# 2026-07-23 ToolSandbox 异常评估结果代码修复方案

## 1. 问题背景

基于 `docs/plans/analysis/2026-07-23-toolsandbox-run-results-audit-report.md` 的核查，本轮异常主要集中在两类问题：

1. `update_contact_relationship_with_relationship` 的 m1 阶段出现结构化评分与 standard judge 结论冲突：
   - `raw_summary.milestone_match_attempts` 中 m1 `score=1.0`、`hard_constraints_all_pass=true`。
   - `trajectory.final_state.CONTACT` 中 `Fredrik Thordendal` 与 `John Petrucci` 均已变为 `enemy`。
   - 但 `report.stage_reports[m0->m1]` 的 standard judge 因 `actual_excerpt` 截断后看不到目标行，把 `progress/state_consistency` 判为 0，并触发 `evaluation_policy_stop`。
2. `modify_contact_with_message_recency_alt_10_distraction_tools` 的 m3 失败诊断不够准确：
   - final CONTACT 中存在目标 `Homer S -> +10293847563`。
   - 但 agent 同时错误修改了 `Tomas Haake`，并新增/修改 `Bart`，造成 CONTACT 额外污染。
   - 当前诊断只说“需要让 CONTACT 状态达到目标值”，没有说明“目标行存在但伴随额外状态变更导致 update_similarity 失败”。

本方案目标是修复评估链路和诊断表达，不修改 ToolSandbox 原生 benchmark 语义，也不重新定义 milestone graph。

## 2. 根因判断

### 2.1 standard judge 输入证据被截断

`dynsteer/prompt/judge.py::_constraint_checks()` 当前只给 LLM judge：

- `expected_excerpt`
- `actual_excerpt`
- `short_evidence`
- `score/satisfied/missing/hard`

其中 `actual_excerpt` 来自 `dynsteer.evaluate.semantic.constraint_actual_excerpt()`，默认限制 420 字符。CONTACT 表一旦含多行，目标行可能在 excerpt 后部被截断。standard judge 看到截断内容后，会把“没看到目标行”误判成“状态未满足”。

### 2.2 结构化 scorer 与 LLM judge 缺少冲突保护

对于 state_snapshot 类硬约束，ToolSandbox scorer 是主要结构化证据来源。当前 `_evaluate_stage()` 在合并 standard judge 结果后，没有检查：

- milestone 结构化评分是否已经 `PASS`；
- hard constraints 是否全部通过；
- standard judge 的低分是否仅由证据截断或 evidence gap 引起。

因此 m1 结构化已通过，仍被 standard judge 推翻并触发早停。

### 2.3 失败诊断没有展示“期望行存在但额外污染”

`dynsteer/evaluate/diagnostics.py::_constraint_failure_detail()` 当前主要输出短 excerpt 和前两条 evidence，没有对实际 rows 与 expected rows 做结构化对比。因此无法区分：

- 目标行完全不存在；
- 目标行存在但存在额外行、额外修改或 reference drift；
- scorer 因 guardrail / update_similarity 语义失败。

## 3. 修复目标

1. standard judge prompt 必须拿到结构化状态摘要，不能依赖截断的 `actual_excerpt` 判断状态行是否存在。
2. 当 structured scorer 明确通过 state_snapshot 硬约束时，standard judge 不能仅因截断 evidence 把状态阶段判成失败。
3. 对 `update_contact_relationship_with_relationship`：
   - m0 应继续 pass。
   - m1 不应因 evidence 截断被判 fail。
   - 不应在 m1 触发 `evaluation_policy_stop`。
   - 若轨迹没有 m2 用户可见完成消息，则最终仍可 partial，但 first failure 应转移到 `m1->m2`。
4. 对 `modify_contact_with_message_recency_alt_10_distraction_tools`：
   - m3 仍应保持 fail/partial，因为 CONTACT 被额外污染。
   - 诊断应明确指出目标行存在，但伴随非目标联系人变更、row_count drift 或 extra mutations。
5. 保持 `add_contact`、`remove_contact`、`turn_on_cellular` 的现有改善结果不回退。

## 4. 文件级修改方案

### 4.1 `dynsteer/evaluate/semantic.py`

在现有 excerpt helper 附近新增结构化摘要函数，避免在 prompt 和 diagnostics 中重复实现。

建议新增函数：

```python
def constraint_expected_summary(constraint: Constraint | None, max_rows: int = 8) -> JsonObject | None:
    """提取约束 expected 的结构化摘要。"""

def constraint_actual_summary(
    constraint: Constraint | None,
    score: ConstraintScore | JsonObject | None,
    max_rows: int = 12,
) -> JsonObject | None:
    """提取 constraint score actual 的结构化摘要，并标注期望行匹配情况。"""
```

摘要字段建议：

- `row_count`: actual / expected 行数。
- `columns`: 行字段集合。
- `rows`: 限量保留的结构化 rows；小表直接全量保留，超过上限时优先保留与 expected identifier 匹配的行。
- `truncated`: 是否被截断。
- `expected_rows_present`: expected 中每一行是否在 actual rows 中找到包含匹配。
- `all_expected_rows_present`: 所有 expected rows 是否存在。
- `extra_row_count`: `actual_row_count - expected_row_count`，仅作为提示，不直接决定 pass/fail。
- `identity_keys`: 用于匹配的键，如 `person_id/name/phone_number/reminder_id/message_id/device_id`。

关键实现规则：

1. `{"rows": [...]}` 与裸 `[...]` 都统一转成 rows。
2. expected 为单个 dict 时视作 1 行。
3. row match 采用“expected row 的所有非空字段在 actual row 中相等”的包含式匹配。
4. 对 CONTACT / REMINDER / MESSAGING 小表优先全量输出，避免目标行再次被截断。
5. helper 返回 JSON 安全对象，使用 `json_safe()` 处理 dataclass、enum、Polars 不支持对象等。

### 4.2 `dynsteer/prompt/judge.py`

修改 `_constraint_checks()`，在原有字段基础上新增结构化字段：

```python
"expected_summary": constraint_expected_summary(constraint),
"actual_summary": constraint_actual_summary(constraint, score),
"structured_pass": score.score >= threshold and not score.missing,
"structured_source": "constraint_score",
```

保留 `expected_excerpt` / `actual_excerpt`，但把它们定位为人类可读短摘要，不再作为唯一状态证据。

同时修改 `standard.en.md` 与 `standard.zh.md` 模板：

- 增加规则：对 state_snapshot 类约束，应优先读取 `constraint_checks[].actual_summary` 和 `expected_summary`。
- 增加规则：不要因为 `actual_excerpt` 截断而判断状态行缺失。
- 增加规则：若 `structured_pass=true` 且 `actual_summary.all_expected_rows_present=true`，应将该状态约束视为已由结构化证据支持，除非 steps 或 actual_summary 明确显示相反状态。
- 增加规则：若 `score` 低但 `all_expected_rows_present=true`，应检查是否存在 `extra_row_count`、额外修改或 reference 约束失败，而不是简单写“目标不存在”。

### 4.3 `dynsteer/evaluate/settlement.py`

在 `_evaluate_stage()` 合并 cheap/standard/expensive 结果后、计算最终 `stage_score` 前，加入窄域冲突保护。

建议新增内部函数：

```python
def _apply_structured_state_pass_guard(
    stage_result: StageEvaluationResult,
    interval: StageInterval,
    task_case: TaskCase,
    thresholds: ThresholdConfig,
) -> None:
    """当结构化状态硬约束明确通过时，避免 standard judge 因证据截断触发错误失败。"""
```

触发条件必须严格：

1. `interval.milestone_score is not None`
2. `interval.milestone_score.status == StageStatus.PASS`
3. `interval.milestone_score.hard_constraints_all_pass is True`
4. 当前 milestone 至少包含一个 `stage_goal_semantics.kind in {"set_state", "preserve_state"}` 的 hard constraint
5. 对这些 state constraints，`constraint_actual_summary(...).all_expected_rows_present` 为 `true` 或约束类型为 preserve_state 且 scorer 已 pass
6. standard/merged 结果中 `progress` 或 `state_consistency` 被压到 `thresholds.fail_threshold` 以下

保护动作：

- 将 `progress` 与 `state_consistency` 中被误压低的维度提升到 `thresholds.pass_threshold`，仅限 state evidence 相关维度。
- 追加 evidence：
  - `structured state constraints passed; low state/progress score was guarded against truncated evidence.`
- 追加 metadata：
  - `structured_state_pass_guard.status = "applied"`
  - `guarded_dimensions`
  - `milestone_score`
  - `hard_constraints_all_pass`
  - `constraint_ids`
- 不改 `efficiency`、`safety`、`interaction_quality` 等维度。

不触发条件：

- structured scorer 本身 fail，例如 `modify_contact_with_message_recency_alt_10_distraction_tools` 的 m3。
- 存在 minefield / fatal。
- 消息类 semantic fail；消息类仍由现有 `semantic_message_review` 处理。

这样能修复 `update_contact_relationship_with_relationship` 的 m1 误停，同时不会把 `modify_contact...` 这类确实被 update_similarity 判 fail 的阶段放行。

### 4.4 `dynsteer/evaluate/diagnostics.py`

修改 `_constraint_failure_detail()`：

- 加入 `expected_summary` 与 `actual_summary`。
- 若 `score < threshold` 但 `actual_summary.all_expected_rows_present=true`，加入字段：
  - `expected_rows_present_but_score_failed=true`
  - `possible_failure_reason="expected rows are present but structured scorer still failed, likely due to extra mutations, guardrail/reference mismatch, or row drift"`

修改 `_constraint_failure_line()`：

- 当 `expected_rows_present_but_score_failed=true` 时，输出：
  - `目标行已出现在实际状态中，但结构化 scorer 仍未通过；请检查额外状态变更、reference drift 或 guardrail 破坏`
- 当 `actual_summary.extra_row_count > 0` 时，追加：
  - `实际状态比目标多 N 行`

这会让 `modify_contact...` 的 m3 failure summary 更接近真实原因。

### 4.5 `dynsteer/adapter/toolsandbox/scorer.py`

本轮不建议改 ToolSandbox scorer 的核心评分公式。原因：

- `update_contact_relationship_with_relationship` 的 structured scorer 已给 m1 满分，问题在 LLM judge 使用了截断证据。
- `modify_contact...` 的 m3 给 0 分符合“目标行存在但额外 CONTACT 污染”的惩罚方向。

只建议可选增强：

- 在 `ConstraintScore.evidence` 中追加一条轻量 `actual_summary` 文本，例如 `actual rows=4, expected rows=2, expected_rows_present=2/2`。
- 如果 prompt/diagnostics 已通过 helper 输出结构化摘要，可以不改 scorer。

## 5. 测试方案

当前工作区中 `tests/` 目录处于缺失状态，且 git status 显示已有多份测试文件被删除。代码落地时应恢复/新增最小回归测试，不依赖真实 LLM。

### 5.1 新增 `tests/test_judge_constraint_summary.py`

覆盖：

1. CONTACT actual rows 有 4 行且目标行在后部时，`constraint_actual_summary()` 不会因为 excerpt 截断丢目标。
2. `all_expected_rows_present=true` 能正确识别两名联系人都为 `enemy`。
3. expected 为单 dict、`{"rows": [...]}`、裸 list 三种输入形式都能解析。

### 5.2 新增 `tests/test_structured_state_pass_guard.py`

用 fake judge / 手工 `StageEvaluationResult` 构造：

1. `MilestoneScore(status=PASS, hard_constraints_all_pass=True)`，standard judge 给 `progress=0/state_consistency=0` 时，guard 将两个维度提升到 pass threshold，并写入 metadata。
2. `MilestoneScore(status=FAIL, hard_constraints_all_pass=False)` 时，guard 不触发，确保 `modify_contact...` 不被误放行。
3. 只有 emit_message semantic fail 时，guard 不触发，避免与现有 semantic review 混淆。

### 5.3 新增 `tests/test_toolsandbox_contact_update_diagnostics.py`

覆盖：

1. 当 expected row 存在但 score 为 0 时，failure detail 包含 `expected_rows_present_but_score_failed=true`。
2. failure line 包含“目标行已出现在实际状态中，但结构化 scorer 仍未通过”。
3. extra row / row_count drift 能进入诊断摘要。

### 5.4 回归运行建议

优先运行：

```powershell
uv run pytest tests/test_judge_constraint_summary.py tests/test_structured_state_pass_guard.py tests/test_toolsandbox_contact_update_diagnostics.py
```

再运行相关已有评估链路测试；如果 `tests/` 目录恢复完整，则运行：

```powershell
uv run pytest
```

## 6. 验收口径

### 6.1 静态验收

- `dynsteer/prompt/judge.py::_constraint_checks()` 输出包含 `actual_summary` 与 `expected_summary`。
- standard prompt 明确禁止基于截断 `actual_excerpt` 判定状态行不存在。
- `_evaluate_stage()` 中有结构化状态 pass guard，且触发条件严格限定为 state_snapshot structured pass。
- diagnostics 对“目标行存在但 scorer 失败”有明确诊断。

### 6.2 单测验收

- 新增 3 组测试全部通过。
- 不破坏已有 semantic message review 测试。
- 不引入真实 LLM 调用。

### 6.3 运行结果验收

重新评估本轮 10 个 ToolSandbox case 后，预期变化：

| case | 当前结果 | 修复后预期 |
|---|---|---|
| `update_contact_relationship_with_relationship` | m1 被 standard judge 判 fail，`evaluation_policy_stop` | m1 pass，不在 m1 早停；若仍缺 m2 用户确认，则 first failure 应为 `m1->m2` |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | m3 fail，但诊断泛化 | m3 仍 fail；诊断明确目标行存在但有额外 CONTACT 污染 |
| `add_contact_with_name_and_phone_number_3_distraction_tools` | full | 保持 full |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | full | 保持 full |
| `turn_on_cellular_low_battery_mode` | full | 保持 full |
| `find_days_till_holiday_wifi_off_alt` | partial，缺 m0 | 保持 partial，除非另行调整时间证据策略 |

## 7. 实施步骤

1. 在 `dynsteer/evaluate/semantic.py` 增加 expected/actual structured summary helper。
2. 在 `dynsteer/prompt/judge.py` 接入 summary helper，扩展 `constraint_checks` JSON。
3. 修改 `standard.en.md` 和 `standard.zh.md`，明确 state evidence 的读取优先级和截断禁令。
4. 在 `dynsteer/evaluate/settlement.py` 增加 `_apply_structured_state_pass_guard()`，并在 `_evaluate_stage()` 中调用。
5. 在 `dynsteer/evaluate/diagnostics.py` 接入 structured summary，改进 failure detail/line。
6. 新增或恢复 tests，先跑窄域 pytest。
7. 重新运行当前 10 个 ToolSandbox case，对比 `results/.../summary.json` 与异常 case 的 `report.json`。
8. 更新 `docs/apis/evaluate.md` 或相关 API 文档，补充 report metadata 中新增的 `structured_state_pass_guard` 与 constraint summary 字段。

## 8. 风险与控制

| 风险 | 控制方式 |
|---|---|
| prompt 变长导致 LLM 成本增加 | 小表全量输出，大表只输出 expected 匹配行和前若干行，设置 `max_rows` |
| guard 过度放行真实失败 | 只在 structured scorer 已 PASS 且 hard constraints 全通过时触发；structured fail 不触发 |
| 状态 summary 与 excerpt 不一致 | summary helper 与 excerpt helper 共用 rows 解析逻辑，并用单测覆盖 |
| `modify_contact...` 被误修成 full | guard 依赖 `MilestoneScore.status == PASS`，该 case m3 structured score 为 FAIL，不会触发 |
| 诊断文本过长 | diagnostics 只在 failure detail 中保留结构化摘要，failure line 输出短句 |

## 附录A. 项目中没有把握实现的模块部分

1. **ToolSandbox 原生 `update_similarity` 的内部语义。**
   - 没有完全把握原因：该函数来自外部 `tool_sandbox.common.evaluation`，当前项目只是动态加载调用。方案不修改其内部算法，只围绕其输出做证据传递与冲突保护。
2. **`update_contact_relationship_with_relationship` 中 raw index 20/22 缺口的来源。**
   - 没有完全把握原因：`trajectory.steps` 只落盘 6 个显式 step，但 final state 的 CONTACT 更新时间为 `sandbox_message_index=22`，可能是 ToolSandbox 内部批量状态更新或轨迹转换层省略了中间事件。方案不会依赖补齐该缺口，但建议后续单独增强轨迹可观测性。
3. **重新跑完整 benchmark 后整体均分的精确值。**
   - 没有完全把握原因：LLM judge 有非确定性，且修复后 prompt 内容变化可能导致边界 case 的分数略波动。验收口径应以异常阶段是否修复、coverage 是否合理为主，而非要求整体均分完全固定。

