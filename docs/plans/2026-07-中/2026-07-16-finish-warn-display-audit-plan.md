# DynSTEER finish、warn 与里程碑展示问题核查报告及修改方案

## 1. 核查范围

本次只核查当前 `data`、`runs`、`results` 与相关展示/评估代码，并形成代码修改方案，不直接落地功能修复。

核查对象：

- `data/toolsandbox/adapted_cases/*.json`
- `runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/trajectory.json`
- `runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/raw_summary.json`
- `results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/summary.json`
- `results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/report.json`
- `display/build.py`
- `display/index.html`
- `dynsteer/evaluate/final.py`
- `dynsteer/stage/settlement.py`
- `dynsteer/judges/cheap.py`
- `dynsteer/model.py`

当前工作树已经存在多处未提交改动与文档移动/删除状态；本方案不回滚、不删除现有文档，后续落地前应先确认这些工作树状态是否符合预期。

## 2. 当前结果概览

`results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/summary.json` 显示：

- scenario 数量：8
- 平均分：0.5911862755122768
- 覆盖：`full=4`、`partial=3`、`none=1`
- 总步数：67
- 总 LLM tokens：175306

逐 scenario 核查如下：

| scenario | coverage | first_failure_stage_id | finish 阶段 | 核查结论 |
| --- | --- | --- | --- | --- |
| `cellular_off` | full | `m1->__finish__` | `m1->__finish__ fail` | 真实 milestone 已全覆盖，但 finish final verification 在终止边界重评 `emit_message`，把 `m1_c0` 打成 0 |
| `find_days_till_holiday_alt_3_distraction_tools` | full | `m3->__finish__` | `m3->__finish__ fail` | 真实 milestone 已全覆盖，但 finish 重评 `m3_c0/m3_c1` 输出消息约束时命中了最后的 `None` |
| `find_current_city_low_battery_mode` | full | `m5->__finish__` | `m5->__finish__ fail` | 真实 milestone 已全覆盖，但 finish 重评 `m5_c0/m5_c1` 输出消息约束时命中了最后的 `None` |
| `find_current_city_low_battery_mode_all_tools` | full | `m5->__finish__` | `m5->__finish__ fail` | 与上一个 case 同因 |
| `find_temperature_low_battery_mode` | partial | `m3->m4` | absent | `m4` 未通过，报告中没有生成 `m4->__finish__`；但 adapted graph 中存在 `m4->__finish__` 增强边 |
| `modify_contact_with_message_recency` | partial | `m2->m3` | absent | `m3` fail、`m4` missing；报告中没有生成 `m4->__finish__` |
| `remove_reminder_with_recency_latest_alt` | partial | `__start__->m2` | absent | 真实删除目标错误，未进入 finish |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools` | none | `__start__->m0` | absent | 首个 milestone 未通过，后续真实 milestone 均为 missing，未进入 finish |

## 3. 问题一：finish 阶段基本都 fail

### 3.1 数据事实

所有 adapted case 都把 `__finish__` 放在 `milestone_graph.metadata.graph_analysis.augmented_edges` 中，而不是普通真实 milestone 节点。所有 8 个 case 的 `stage_goals` 都没有 `mN->__finish__`，这是合理的：finish 是虚拟终点，不应当作为普通 milestone 目标由 LLM 生成阶段目标。

4 个 `full` coverage case 都生成了 finish stage report，但 `finish_stage_evaluation.all_milestones_matched=true`，失败点集中在 `terminal_state_checks`：

- `cellular_off`：`m1_c0` 得分 0，actual 是 `end_conversation` 后的 `None`
- `find_days_till_holiday_alt_3_distraction_tools`：`m3_c0/m3_c1` 得分 0，actual 是最后 `None`
- `find_current_city_low_battery_mode`：`m5_c0/m5_c1` 得分 0，actual 是最后 `None`
- `find_current_city_low_battery_mode_all_tools`：同上

这些低分约束的语义都是 `stage_goal_semantics.kind=emit_message`，本质是“agent 是否已经向用户输出目标消息”。它们在 terminal milestone 匹配时应被检查，但不适合在 `end_conversation` 之后用最终边界再次按 `STATE_SNAPSHOT` 约束重评。

### 3.2 代码根因

当前 `dynsteer/evaluate/final.py` 的 `_is_terminal_state_constraint()` 逻辑过宽：

- 只要 `constraint.target` 是 `STATE_SNAPSHOT` 或 `STATE_DELTA` 就纳入 finish 的 terminal state recheck。
- ToolSandbox 的用户可见输出约束虽然语义是 `emit_message`，但 target 也被编码为 `state_snapshot`。
- 因此 finish 在最终 `Boundary(finish:b{latest_step_index})` 上重新打分输出消息约束。
- 终止区间最后通常是 `end_conversation` 和环境返回 `None`，于是输出消息约束被误判为 0。

这说明当前 finish fail 不是单纯“agent 没完成任务”，而是 final verification 的约束筛选和边界选择有偏差。

### 3.3 修改方案

修改 `dynsteer/evaluate/final.py`：

1. 拆分 terminal 约束类型：
   - `emit_message`、`user_visible_required=true`：只引用原 terminal milestone 匹配结果，不在 finish 最终边界重评。
   - `set_state`、`preserve_state`、`STATE_DELTA`、真实持久状态快照约束：允许在最终边界重评，确认后续步骤没有破坏状态。
2. `_is_terminal_state_constraint()` 先读取 `stage_goal_semantics.kind`：
   - 若为 `StageGoalSemanticKind.EMIT_MESSAGE.value`，直接排除。
   - 若为 `SET_STATE` 或 `PRESERVE_STATE`，纳入。
   - 对没有语义标注但带 ToolSandbox guardrail/snapshot metadata 的约束，只有在不是用户输出类约束时纳入。
3. finish payload 中补充结构化说明：
   - `terminal_state_checks`：只包含最终边界持久状态重检。
   - `terminal_message_checks`：列出哪些输出约束已由原 terminal milestone 匹配确认，不重复重评。
4. finish status 聚合：
   - 未覆盖真实 milestone、持久状态重检 fail、fatal minefield：`fail`
   - 持久状态通过但原 terminal milestone 存在非致命质量问题：`warn`
   - milestone 全覆盖且持久状态通过：`pass`
5. 更新 `docs/apis/evaluate.md`，说明 finish 不会要求 agent 在终止区间再次执行验证工具，也不会用 `end_conversation` 覆盖已发送的用户消息。

建议测试：

- 构造 terminal milestone 含 `emit_message + preserve_state` 的场景，最终追加 `end_conversation`，断言 finish 不因 `emit_message` 在最终边界为 0 而 fail。
- 构造 terminal milestone 之后状态被破坏的场景，断言 finish fail。
- 构造真实 milestone 未全覆盖场景，断言 finish 不生成或显示为 not_started/missing，而不是 pass。

## 4. 问题二：右栏缺失 `m4->__finish__`

### 4.1 数据事实

以 `find_temperature_low_battery_mode` 为例：

- `data/.../find_temperature_low_battery_mode.json` 的增强边包含 `m4->__finish__`
- 当前 `report.json` 的 stage reports 只有：
  - `__start__->m0`
  - `m0->m2`
  - `m0->m1`
  - `m0->m3`
  - `m3->m4 fail`
- 当前 `display.build.build_display_data()` 生成的 `stage_definitions` 也只保留到 `m3->m4`
- 因此右栏没有机会渲染 `m4->__finish__:not_started`

### 4.2 代码根因

`display/build.py` 的 `_active_stage_definitions()` 在发现任意非 finish 阶段是 `fail/missing/invalid/fatal` 后，只保留已有 report 的 stage definition：

```python
reported_ids = {str(report.get("stage_id") or "") for report in reports}
return [definition for definition in definitions if str(definition.get("stage_id") or "") in reported_ids]
```

finish definition 原本由 `_finish_stage_definition()` 从 graph analysis 合成，但因为没有 report，就被过滤掉了。

### 4.3 修改方案

修改 `display/build.py`：

1. `_active_stage_definitions()` 保留现有“失败后过滤未触达阶段”的原则，但额外保留 finish definition。
2. 更精确地说：
   - 若 graph 中存在 `finish_stage_anchor_predecessor_id`，且该 anchor 对应的真实 terminal stage 已 report，或 anchor milestone 本身已作为 fail/missing 出现在报告里，则保留 `anchor->__finish__` definition。
   - 右栏由现有 `emptyStageReport(definition)` 渲染为 `not_started`。
3. 若后端未来已经产出 finish report，则以真实 report 为准，不重复合成。
4. 增加 display build 单元测试：
   - partial case `find_temperature_low_battery_mode` 构建后应包含 `m4->__finish__` stage definition。
   - 该 stage 没有 report 时，前端应显示 `not_started` 或后端可选择显示 `missing`。

## 5. 问题三：warn 原因不清楚，图中没有 warn 标识

### 5.1 数据事实

截图 2 对应的 `modify_contact_with_message_recency` 中，`__start__->m0` 是 `warn`：

- `stage_score=0.7941250000000001`
- 默认阈值：`pass_threshold=0.8`、`warn_threshold=0.6`、`fail_threshold=0.4`
- `dimension_scores.tool_quality=0.59`
- `metadata.low_score_dimensions=[{"dimension":"tool_quality","score":0.59,"severity":"warn"}]`
- `metadata.stage_quality_diagnostics.warning_count=2`
- 具体 warning：
  - `literal_alias_for_id_argument`：`search_messages.sender_person_id="self"`
  - `failed_tool_results`：`creation_timestamp_upperbound` 超出 ToolSandbox 有效范围

右栏之所以“不清楚为什么 warn”，是因为 `diagnosis=[]`，而 UI 没有把 `low_score_dimensions` 和 `stage_quality_diagnostics` 提炼成可读摘要。

中栏之所以没有 warn 颜色，是因为 `display/index.html` 的 `milestoneStatus()` 只返回 `pass` 或 `fail`：

```javascript
if (report.status === "pass") return "pass";
if (report.status === "fail" || report.status === "fatal") return "fail";
return "";
```

CSS 和图例也没有 `.milestone-node.warn`。

### 5.2 修改方案

修改 `display/index.html`：

1. 状态映射：
   - `warn` 映射为 `.warn`
   - `missing/not_started` 映射为 `.pending` 或 `.not-started`
   - `invalid/fatal/fail` 映射为 `.fail`
2. 增加 CSS：
   - `.milestone-node.warn rect` 使用琥珀色边框/浅底
   - `.milestone-node.pending rect` 使用灰色虚线或弱化边框
   - 图例增加 warn 与 not_started/missing
3. 增加 `warningSummarySection(report)`：
   - 优先读取 `metadata.low_score_dimensions`
   - 再读取 `metadata.stage_quality_diagnostics.warning_count`
   - 对 `tool_argument_warnings`、`failed_tool_results`、`empty_tool_results`、`grounding_warnings` 生成简短中文摘要
4. `stageCard()` 中：
   - 当 `report.status === "warn"` 时默认展开“警告原因”块。
   - 当 `diagnosis` 为空但有低分维度时，仍显示可读警告摘要。
5. 可选后端增强：
   - 在 `dynsteer/stage/settlement.py` 或 `dynsteer/judges/cheap.py` 里把低分维度与质量诊断补入 `diagnosis`，这样非前端消费方也能看到原因。

建议测试：

- 构造包含 `warn` report 的 display data，断言 `milestoneStatus()` 返回 `warn`。
- 构造 `low_score_dimensions` 与 `stage_quality_diagnostics`，断言右栏有警告原因文本。

## 6. 问题四：milestone 节点详情太小，缺少可拉伸查看

### 6.1 现状

当前 `display/index.html` 已经支持点击节点后展开详情：

- 未选中节点尺寸：`148 x 48`
- 选中节点尺寸：`320 x 218`
- 详情通过 `foreignObject` 放入节点框内

但当前尺寸是固定值，节点多时整体布局被压缩，展开后的内容仍然偏小；且没有用户可控的节点框拉伸手柄。

### 6.2 修改方案

修改 `display/index.html`：

1. 在前端 state 中新增选中节点尺寸状态：
   - `selectedMilestoneSize: { milestoneId, width, height } | null`
   - 默认展开尺寸仍为 `320 x 218`
   - 最小尺寸建议 `240 x 160`
   - 最大尺寸受当前 SVG viewBox 和 panel 宽高约束
2. `selectedMilestoneBox(milestoneId)`：
   - 若当前 milestone 被选中且存在自定义尺寸，使用自定义尺寸。
   - 若未选中，固定恢复为 `148 x 48`。
3. 在选中节点右下角绘制 resize handle：
   - 使用 SVG `g` 或 `foreignObject` 中的绝对定位小角标。
   - `pointerdown` 后监听 `pointermove`，实时更新 `selectedMilestoneSize` 并重绘 graph。
   - `pointerup/pointercancel` 清理监听。
4. 点击已选中节点或切换 scenario/run 时：
   - 清空 `selectedMilestoneSize`
   - 节点立即恢复默认未选中尺寸
5. 重新计算 graph layout：
   - 当前 `graphLayout()` 已基于节点 box 尺寸绘制，拉伸后需要重新调用 `renderGraph()`。
   - 防止放大节点与相邻节点重叠，必要时根据最大节点宽高增加层间距和同层间距。

建议验收：

- 点击节点后可展开。
- 拖动右下角可放大/缩小。
- 再次点击或点击其他节点后，原节点立即恢复 `148 x 48`。
- 节点多的 scenario 中详情文本可通过放大查看，且边、节点、右栏选中联动不丢失。

## 7. 建议实施顺序

1. 先修 `dynsteer/evaluate/final.py` 的 finish 约束筛选，避免后续重新跑结果时继续产生系统性 finish fail。
2. 修 `display/build.py` 的 finish definition 保留逻辑，让 partial/failed case 右栏能显示 `mN->__finish__` 的未开始状态。
3. 修 `display/index.html` 的 warn 状态映射、图例和警告原因摘要。
4. 最后做 milestone 节点可拉伸交互，因为它主要是体验优化，风险集中在 SVG 布局与 pointer event。
5. 更新 `docs/apis/evaluate.md` 与 `docs/apis/display.md`。
6. 增加 PyTest 覆盖：
   - finish final verification
   - display build stage definition
   - warn/pending 状态数据映射
7. 重新生成 display data 并抽查以下 case：
   - `cellular_off`
   - `find_current_city_low_battery_mode`
   - `find_temperature_low_battery_mode`
   - `modify_contact_with_message_recency`

## 8. 验收标准

- 完整覆盖的 4 个 case 不再因为 `end_conversation` 后的 `None` 导致 finish fail。
- 若 terminal milestone 的持久状态确实被后续步骤破坏，finish 仍能 fail。
- `find_temperature_low_battery_mode` 右栏显示 `m4->__finish__:not_started` 或等价的 missing/not started 状态。
- `modify_contact_with_message_recency` 的 warn 节点在中栏有琥珀色标识，右栏能展示 `tool_quality=0.59` 与具体 warning 原因。
- 点击 milestone 后可展开详情，右下角可拉伸，点击收回后恢复原始节点大小。
- 所有新增/修改核心逻辑有测试，文档不出现中文乱码。

## 附录A. 项目中没有把握实现的模块部分

1. ToolSandbox 输出消息约束的最终语义需要最终确认：当前判断认为 `emit_message` 不应在 finish 最终边界重复重评，但如果希望 finish 检查“最后一条用户可见消息必须仍是任务答案”，则需要额外设计消息选择策略，而不是直接使用 `end_conversation` 后的 `None`。
2. milestone 节点可拉伸交互需要在真实浏览器里验证 SVG `foreignObject`、pointer capture 与滚动容器之间的兼容性；纯单元测试无法完全覆盖拖拽体验。
3. 当前工作树已有多处未提交改动和 docs/plans 删除状态，后续落地前需要确认哪些属于用户已有修改，避免把本次修复和历史改动混在一起提交。
