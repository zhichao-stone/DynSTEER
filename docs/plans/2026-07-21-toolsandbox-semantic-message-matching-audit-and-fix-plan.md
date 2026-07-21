# ToolSandbox 语义消息匹配核查报告与修复方案

生成日期：2026-07-21

## 1. 核查范围

本次核查围绕 2026-07-21 16:47-16:58 生成的 ToolSandbox run：

- `results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest`
- `runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest`
- `data/toolsandbox/adapted_cases`
- `display/index.html`
- `dynsteer/adapter/toolsandbox/*`
- `dynsteer/evaluate/*`
- `dynsteer/prompt/judge.py`

重点核查用户指出的两个截图 case：

1. `turn_on_cellular_low_battery_mode` 的 `m2`。
2. `update_contact_relationship_with_relationship` 的 `m2`。

同时横向核查本 run 共 10 个 case 的覆盖率、失败阶段、语义复判、minefield 与展示层数据。

## 2. 核心结论

用户指出的问题属实：两个截图中的 Agent 最终消息都应被视为满足 `emit_message + semantic_equivalent` 语义要求，但当前评估没有通过。

直接原因不是闭包 step 选错。当前代码已经把完整 `AgentStepClosure` 传入 milestone 匹配，并通过 route 选择到了正确的 `AGENT -> USER` 消息 step。

真正原因是：ToolSandbox 原生 `snapshot_similarity` 仍按结构化行相似度和文本相似度给消息打分，`semantic_equivalent` 只触发一个“全阶段 StandardJudge 复判候选”，不是一个独立的“消息语义等价验收”。当 StandardJudge 的阶段综合分低于 `pass_threshold=0.8` 时，即便消息语义等价，也不会 checkpoint milestone。

需要特别澄清：当前流程不是 cheap judge 已经完成了语义判断，而是基础结构化相似度发现消息文本未过阈值后，把整个阶段升级给 StandardJudge。StandardJudge 的 prompt 目标是阶段质量评估，不是专门比较 expected message 与 actual message 是否语义等价，因此它不应承担这类二值语义裁决。

本 run 还存在第三个同类误判风险：

- `remove_contact_by_phone_no_remove_contact_insufficient_information` 的 `m0` 多次发出了“没有删除联系人/修改联系人能力”的拒绝说明，人工复核倾向认为满足约束语义，但语义复判全部被拒绝，最终 coverage 为 `none`。

除上述消息语义类 false negative 外，其他非 full case 主要是 Agent 行为自身偏离目标或 fatal minefield 正常触发；没有发现同等级的状态约束误判。

## 3. 截图 1 失败原因

Case：`turn_on_cellular_low_battery_mode`

目标消息：

```text
Cellular service has been turned on.
```

实际 step 30：

```text
Cellular service has been successfully turned on. Let me know if there's anything else you'd like assistance with!
```

运行证据：

- `m2_c0` 使用 ToolSandbox `snapshot_similarity`。
- actual route 正确：`AGENT -> USER`。
- actual step 正确：`step=30`，不是 `step=32` 的环境结束消息。
- 原生结构化分数为 `0.7728038707951971`。
- milestone 阈值为 `0.8`，因此结构化状态为 `warn`。
- 触发了 `llm_semantic_review`，但结果为 `rejected`。
- 复判结算为 `judge_status=warn`，`settlement_stage_score=0.688073699236611`，低于 `0.8`。

代码原因：

- `dynsteer/adapter/toolsandbox/utils/scenario.py:72-88` 把 SANDBOX 消息标注为 `emit_message`、`match_policy=semantic_equivalent`。
- `dynsteer/adapter/toolsandbox/utils/scenario.py:116-119` 仍将该 constraint 写成 `threshold=1.0`、`hard=True` 的原生 custom constraint。
- `dynsteer/adapter/toolsandbox/scorer.py:201-223` 仍调用 ToolSandbox 原生 `snapshot_similarity`。
- `dynsteer/evaluate/matching/milestone.py:350-398` 只把它识别成需要 StandardJudge 复判的候选。
- `dynsteer/evaluate/step.py:172` 强制复判维度只有 `progress` 和 `interaction_quality`。
- `dynsteer/evaluate/settlement.py:111-140` 仍要求复判后的阶段状态为 `pass` 且阶段综合分不低于 `0.8`，否则不接受 checkpoint。

因此，该失败不是“语义不一致”，而是“语义等价验收被错误绑定到全阶段综合评分”。

## 4. 截图 2 失败原因

Case：`update_contact_relationship_with_relationship`

目标消息：

```text
All your friends are now your enemies
```

实际 step 28：

```text
The relationships for both contacts have been successfully updated to "enemy":

- Fredrik Thordendal -> now labeled as "enemy"
- John Petrucci -> now labeled as "enemy"

Let me know if you'd like to make any further changes!
```

运行证据：

- `m2_c0` 使用 ToolSandbox `snapshot_similarity`。
- actual route 正确：`AGENT -> USER`。
- actual step 正确：`step=28`。
- 原生结构化分数为 `0.4566710912781941`。
- 触发了 `llm_semantic_review`，但结果为 `rejected`。
- 复判结算为 `judge_status=fail`，`settlement_stage_score=0.32336067457015943`。

该 case 的语义差异比截图 1 更明显地暴露了问题：`snapshot_similarity` 更偏字面重合，目标句是概括表达，实际句是枚举联系人和新关系；人类看是等价，原生相似度只有 `0.457`。

补充核查：最终 CONTACT snapshot 中 Fredrik Thordendal 和 John Petrucci 的 `relationship` 均为 `enemy`，所以 `m1` 状态更新通过不是误判。但可见执行轨迹中只展示了一次 `modify_contact`，StandardJudge 诊断里也出现“只看到一次 modify_contact”的自相矛盾。这里更像是轨迹可见层或诊断材料不完整，而不是 CONTACT 状态评分错误。

## 5. 横向运行核查

本 run 共 10 个 case：

| case | coverage | 结论 |
|---|---:|---|
| `add_contact_with_name_and_phone_number_3_distraction_tools` | full | 语义复判 accepted，未发现明显误判。 |
| `find_days_till_holiday` | full | 未发现明显误判。 |
| `find_days_till_holiday_insufficient_information` | none | fatal minefield `timestamp_diff` 命中，符合“信息不足时不应直接计算”的预期；但 minefield 语义元数据被标成 `emit_message`，诊断命名不准确。 |
| `find_days_till_holiday_wifi_off_alt` | partial | Agent 未先调用 `get_current_timestamp`，`m0` 失败更像真实行为失败，不是评估误判。 |
| `modify_contact_with_message_recency_alt_10_distraction_tools` | partial | Agent 多次更新错误联系人并新增 Bart，`m3` 状态目标未达成，更像真实行为失败。 |
| `modify_reminder_with_recency_latest` | full | 未发现明显误判。 |
| `remove_contact_by_phone_no_remove_contact_insufficient_information` | none | 与截图同类：拒绝删除能力的消息语义应进入更可靠的等价验收，当前疑似 false negative。 |
| `send_message_with_contact_content_cellular_off` | full | 语义复判 accepted，未发现明显误判。 |
| `turn_on_cellular_low_battery_mode` | partial | 截图 1，同类 false negative。 |
| `update_contact_relationship_with_relationship` | partial | 截图 2，同类 false negative。 |

综合判断：

1. 评估流程整体能运行：case 加载、闭包 route 选 step、状态快照、guardrail、minefield、报告生成均有产物。
2. 不是所有评估都没有误判。当前至少存在 2 个已确认消息语义 false negative，另有 1 个同类高风险 false negative。
3. 展示层缺少 sender/recipient 筛选，影响人工排查效率。
4. rejected semantic review 的详细 StageResult 没有进入最终 report，最终只留下 pending synthetic stage，导致右侧诊断看不到复判为什么失败。
5. SANDBOX 工具调用类 minefield 在缺少 `tool_trace` 时可能被标注成 `emit_message`，诊断文案容易误导。

## 6. 根因拆解

### 6.1 原生 `snapshot_similarity` 不等于语义等价

ToolSandbox 的 SANDBOX 行约束最终仍进入 `_score_toolsandbox_snapshot_constraint(...)`，按 dataframe 行、列、文本相似度计算分数。

这对工具调用、状态快照、精确消息可用，但对自然语言“含义相同、措辞不同”的消息不稳定：

- 加了 `successfully` 和结束寒暄会被扣分。
- 把概括句改成多行 bullet list 会被大幅扣分。
- 目标短句和实际长句之间的方向、对象、完成状态虽然一致，仍可能低于阈值。

### 6.2 `semantic_equivalent` 只是复判候选，不是验收策略

当前 `match_policy=semantic_equivalent` 的实际作用是：

1. 基础结构化相似度或原生 `snapshot_similarity` 未通过时，尝试进入 StandardJudge。
2. StandardJudge 按阶段维度打分。
3. 只有阶段状态和综合分通过，才 checkpoint。

这会把“消息是否等价”混进完整阶段质量评估。对于 m2 这种“最后发一条确认消息”的阶段，判定应是二值或高置信语义验收；不应因为全阶段综合分、诊断噪声或维度权重低于 0.8 而拒绝。

正确语义应是：当基础结构化相似度不足以可靠判断自然语言消息时，立即在 constraint/matching 层调用专用 LLM 语义判别 prompt，仅判断 expected content 与 actual content 在指定 sender/recipient route 下是否等价；不要把这个问题延后到 stage-level StandardJudge。

### 6.3 语义判别和诊断缺少稳定的 expected/actual 对照

`dynsteer/prompt/judge.py:83-107` 的 `constraint_checks` 只包含 `score`、`missing`、`short_evidence`，没有稳定携带 expected content 与 actual content 的对照字段。

虽然 `steps` 中有完整轨迹，但语义判别或人工诊断都需要自行把目标句、actual step 和失败 constraint 对齐。对于长消息和多轮互动，这会增加复判不稳定性，也会让展示层很难解释“到底是哪两段文本被判为不等价”。

这里的 `expected_excerpt` 与 `actual_excerpt` 不是为了继续扩大 stage-level StandardJudge 的判断范围，而是为了把单条 constraint 的目标文本和实际证据文本结构化保存下来：

1. `expected_excerpt`：从 constraint 的 `stage_goal_semantics.content` 或 expected rows 中提取的目标消息摘要。
2. `actual_excerpt`：从 `ConstraintScore.actual` 中提取的实际消息摘要。
3. 两者限制长度，只保留足够比较语义的文本片段。

专用语义判别 prompt 应直接读取这两个字段进行 expected-vs-actual 判断；最终 report 和展示层也可以用它们解释 semantic review 的 accepted/rejected 原因。

### 6.4 rejected semantic review 没有被充分保留

`evaluate_checkpoint(...)` 在 semantic review 拒绝时返回 `stage_result`，但不 checkpoint，也不把该 rejected StageResult 写入最终 report。最终 `_new_pending_stage_reports(...)` 生成的 synthetic pending stage 把所有维度置为 0，右侧展示只能看到“最佳 snapshot_similarity 未达阈值”，看不到复判维度如何裁决。

### 6.5 ToolSandbox scorer 的 hard 语义与诊断不一致

ToolSandbox scorer 对 guardrail 的 hard pass 有特殊逻辑，但非 guardrail 的 hard message constraint 没有按 `constraint.threshold=1.0` 直接置 `hard_constraints_all_pass=False`。后续 pending diagnostics 又按 threshold 报 failed constraints。

这让同一个候选在不同诊断入口里看起来一会儿是 hard pass，一会儿是 hard fail。虽然它不是截图误判的唯一原因，但会放大复判和展示歧义。

## 7. 修复方案

### P0. 消息语义等价应成为 constraint 级验收

新增一个窄域语义判别流程，只处理 `StageGoalSemanticKind.EMIT_MESSAGE` 且 `match_policy=semantic_equivalent` 的约束。该流程不是把阶段评估级别从 cheap 升到 standard，而是在基础结构化相似度不足时，直接对单条消息约束调用专用 LLM 语义 prompt。

建议落点：

- 新增 `dynsteer/evaluate/semantic.py`
- 修改 `dynsteer/evaluate/step.py`
- 修改 `dynsteer/evaluate/settlement.py`
- 轻量修改 `dynsteer/prompt/judge.py` 或新增专用 prompt

设计要点：

1. 从 `ConstraintScore.actual` 中读取实际 `sender`、`recipient`、`content`。
2. 从 constraint 的 `stage_goal_semantics.content` 读取 expected content。
3. route 必须匹配，且 actual content 不能为空。
4. 基础结构化相似度低于阈值但高于召回下限，或 route 匹配且存在 actual content 时，调用专用语义等价 judge。
5. 专用 prompt 只比较 expected content 与 actual content 的任务语义，不评估工具选择、效率、安全、恢复等阶段维度。
6. 专用 judge 输出 `equivalent: bool`、`confidence: float`、`reason: str`，不要输出完整阶段维度分。
7. 当 `equivalent=True` 且 confidence 达到阈值时，将该 constraint 视为通过。
8. 只在所有其他 hard constraints 已通过时解除 milestone 的结构化消息失败。
9. checkpoint 接受逻辑以“semantic constraint override 后的 MilestoneScore”为准，不再要求全阶段综合分替代消息等价结论。

不建议仅降低 `snapshot_similarity` 阈值。截图 2 的 0.457 说明纯阈值会非常难调，低阈值还会带来错误消息被放过的风险。

### P1. 完善语义判别输入摘要与复判留痕

无论是否新增专用 judge，都应补充以下结构化字段。这里的目标不是让 stage-level StandardJudge 继续承担语义等价判别，而是给专用 semantic judge 和展示诊断提供同一份 expected/actual 对照材料：

1. `constraint_checks` 增加 `expected_excerpt` 与 `actual_excerpt`，限制长度，避免 prompt 或 report 过大。
   - `expected_excerpt` 表示该 constraint 期望 Agent 表达的核心文本。
   - `actual_excerpt` 表示当前命中 route 的实际 Agent 消息文本。
   - 二者用于专用 semantic judge 的输入、失败诊断和展示层说明，不作为阶段综合分的额外打分维度。
2. `match_attempts[*].llm_semantic_review` 保留：
   - review target constraint ids
   - expected content
   - actual content
   - equivalent/confidence
   - judge raw status
   - final accept/reject reason
3. rejected semantic review 不应只丢进 `attempt_detail` 的三字段摘要。最终 pending milestone 的 metadata 应引用最后一次 rejected review。
4. 展示层在“匹配失败摘要”中优先展示 semantic review 的 accepted/rejected 原因。

### P2. 统一 ToolSandbox message hard 语义

建议把 ToolSandbox scorer 的 milestone 聚合逻辑拆清楚：

1. guardrail 仍保持“只有 score=0 才 hard fail”的保护逻辑。
2. 对 `emit_message + semantic_equivalent`，原生 `snapshot_similarity` 低于阈值时不直接代表最终 hard fail，而是 `needs_semantic_review`。
3. 对非 semantic 的 hard constraint，明确按 `constraint.threshold` 判断 hard pass。
4. `MilestoneScore.hard_constraints_all_pass` 与 final diagnostics 的 failed constraints 口径必须一致。

### P3. 修复 SANDBOX 工具调用语义标注

`constraint_from_snapshot_constraint(...)` 目前对 namespace 为 `SANDBOX` 且没有 `tool_trace` 的行，默认标成 `emit_message`。这会让 `find_days_till_holiday_insufficient_information` 的 minefield 文案显示为消息语义，实际它是在抓 `AGENT -> EXECUTION_ENVIRONMENT` 的 `timestamp_diff` 工具调用。

修复规则：

1. 如果 `sender=AGENT` 且 `recipient in {EXECUTION_ENVIRONMENT, ENVIRONMENT}`，优先标记为 `tool_call`。
2. 如果存在 `openai_function_name`、`tool_trace.tool_name` 或 content 中可解析函数名，也标记为 `tool_call`。
3. 只有 `AGENT -> USER`、`ENVIRONMENT -> USER` 等用户可见对话行才标记为 `emit_message`。
4. `user_visible_required` 只应对真正用户可见消息为 true。

### P4. 左侧执行轨迹增加 sender/recipient 筛选

当前 `display/index.html:1179-1199` 的 `renderTrajectory()` 直接渲染全部 steps，没有筛选状态。

建议实现：

1. 在全局 `state` 中增加：

```js
trajectoryFilters: {
  actor: "all",
  recipient: "all"
}
```

2. 在轨迹面板标题下方或标题右侧增加两个紧凑 select：
   - 发起方：全部、agent、user、environment、system、evaluator、unknown
   - 接收方：全部、agent、user、environment、system、evaluator、unknown

3. `renderTrajectory()` 中先计算：

```js
const filteredSteps = steps.filter((step) =>
  matchesActorFilter(step, state.trajectoryFilters.actor) &&
  matchesRecipientFilter(step, state.trajectoryFilters.recipient)
);
```

4. 面板副标题显示 `显示 N / 共 M 步`。
5. 若当前 milestone 或 stage 选中的 step 被筛选隐藏，显示一条轻量提示：`当前筛选隐藏了选中步骤`，并提供“清除筛选”按钮。
6. `scrollToMatchedStep(...)` 不应因为目标 step 被隐藏而静默失败；可以先提示，也可以自动清除筛选。推荐先提示，避免用户筛选状态被意外重置。
7. 不引入新依赖，不改变 data.js 结构。

### P5. 修复可见轨迹与状态证据不一致的诊断材料

`update_contact_relationship_with_relationship` 的 final CONTACT state 显示两个朋友都已为 `enemy`，但 StandardJudge 诊断曾指出“只看到一次 modify_contact”。建议后续补充：

1. 在 stage trace 中加入 `state_snapshot_delta_summary`，让 Judge 能看到状态层实际变化。
2. 对状态 mutation tool result 为 `None` 的场景，展示层保留工具名和参数，同时在诊断中明确“工具结果为空不代表无状态变化”。
3. 如果 ToolSandbox 原始轨迹确实存在 hidden tool invocation，adapter 应将其以 raw metadata 方式暴露，避免 Judge 只凭可见 steps 推断行为缺失。

## 8. 测试计划

新增或调整 pytest：

1. `tests/test_semantic_message_equivalence.py`
   - `Cellular service has been successfully turned on...` 应等价于 `Cellular service has been turned on.`
   - bullet list 说明 Fredrik 和 John 均为 enemy 应等价于 `All your friends are now your enemies`。
   - 删除联系人能力不足的拒绝消息应等价于“没有工具可删除/移除”。
   - route 不匹配时不得通过。
   - unrelated polite closing 不得通过。

2. 修改 `tests/test_semantic_review_checkpoint.py`
   - 当前 fake judge 只覆盖高分 accepted；需要增加“阶段综合分低但专用 semantic equivalent 为 true 仍 checkpoint”的测试。
   - 增加 rejected review 被写入 pending diagnostics 的测试。

3. 修改 `tests/test_display_termination.py` 或新增 `tests/test_display_trajectory_filters.py`
   - 构造 trajectory steps，确认 display data 不需要变更。
   - 对 HTML 可用轻量静态测试校验 filter state、option 文案和函数存在。

4. 增加 ToolSandbox 语义标注测试：
   - `AGENT -> EXECUTION_ENVIRONMENT` + `content=timestamp_diff` 应标成 `tool_call`。
   - `AGENT -> USER` + content 应标成 `emit_message`。

5. 场景级回归：
   - `turn_on_cellular_low_battery_mode` 预期从 partial 变 full。
   - `update_contact_relationship_with_relationship` 预期从 partial 变 full。
   - `remove_contact_by_phone_no_remove_contact_insufficient_information` 预期不再因拒绝消息措辞不同而 coverage none。
   - `find_days_till_holiday_insufficient_information` 仍应 fatal minefield。
   - `find_days_till_holiday_wifi_off_alt` 若 Agent 仍不调用 `get_current_timestamp`，仍应非 full。
   - `modify_contact_with_message_recency_alt_10_distraction_tools` 若 Agent 行为不变，仍应非 full。

## 9. 预期效果

修复后，截图两个 case 的目标变化：

1. `turn_on_cellular_low_battery_mode`
   - `m2_c0` 原生 `snapshot_similarity=0.773` 可以作为召回分。
   - 专用 semantic review 接受 actual 与 expected 等价。
   - `m2` checkpoint。
   - coverage 从 `partial` 变为 `full`。

2. `update_contact_relationship_with_relationship`
   - `m2_c0` 原生 `snapshot_similarity=0.457` 可以作为召回分。
   - 专用 semantic review 接受 bullet list 与概括句等价。
   - `m2` checkpoint。
   - coverage 从 `partial` 变为 `full`。

整体指标预期：

- confirmed false negative 减少。
- pending synthetic stage 诊断更可解释。
- fatal minefield 与状态约束不被语义消息修复误放宽。
- 展示面板排查 sender/recipient 路径更快。

## 10. 实施顺序

1. 先实现 P0 和 P1，解决 false negative 与诊断留痕。
2. 再实现 P3，修复 SANDBOX 工具调用语义标注。
3. 再实现 P4，补充展示筛选。
4. 最后实现 P5，增强状态变化与可见轨迹之间的诊断材料。
5. 每步完成后运行对应 pytest；P0 完成后先重跑 3 个消息语义目标 case。

## 附录A. 项目中没有把握实现的模块部分

1. 专用 semantic message judge 的跨模型稳定性需要实测。即使用三票聚合，不同模型对“概括句”和“枚举说明”的等价边界仍可能波动。
2. ToolSandbox 中 `update_contact_relationship_with_relationship` 出现“可见工具调用少于最终状态变化”的现象，当前只能确认 final CONTACT snapshot 满足目标；隐藏状态变化来自原生 ToolSandbox 还是轨迹转换展示不足，需要进一步读取原生 session 级数据才能完全闭环。
3. 本次横向核查基于单个 run 的 10 个 case。修复后是否影响更多 ToolSandbox 场景，需要对 `data/toolsandbox/adapted_cases` 全量回归。
4. 展示筛选本身不复杂，但与 milestone/stage 自动滚动的交互需要浏览器端人工或 Playwright 检查，避免选中目标被筛掉后产生“点击无反应”的错觉。
