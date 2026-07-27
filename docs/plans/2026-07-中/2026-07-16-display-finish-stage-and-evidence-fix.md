# DynSTEER 面板展示与 finish 阶段异常核查报告及代码计划

## 1. 本次处理边界

- 本次只做核查报告与代码计划，不落地功能修复。
- 已将本次已落地的源码、测试与 API 文档内容回退到 `HEAD`：`display/build.py`、`display/index.html`、`docs/apis/evaluate.md`、`dynsteer/judges/confidence.py`、`dynsteer/stage/settlement.py`、`dynsteer/utils.py`、`tests/test_agent_step_closure.py`、`tests/test_algorithm_revision.py`。
- 当前沙箱不能写 `.git/index.lock`，所以无法通过 `git restore` 或 `git update-index` 刷新索引；但上述文件的 `git diff` 已无实际内容差异。
- 本文档作为计划文档保留在 `docs/plans` 下，供后续确认后再进入代码落地。

## 2. 核查对象

- 运行轨迹：`runs/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/trajectory.json` 与 `raw_summary.json`。
- 评估结果：`results/toolsandbox/run_0_qwen-plus-latest_user_qwen-plus-latest/*/report.json` 与 `summary.json`。
- 适配后 case：`data/toolsandbox/adapted_cases/*.json`。
- 相关代码：`display/index.html`、`display/build.py`、`dynsteer/stage/settlement.py`、`dynsteer/stage/__init__.py`、`dynsteer/judges/*`、`dynsteer/prompt/judge.py`、`dynsteer/evaluate/policy.py`。

## 3. 面板展示问题核查

### 3.1 左栏执行轨迹只显示发起方

- 轨迹数据中已经存在 `recipient` 字段，例如最后终止区间为 `user -> environment`、`environment -> user`。
- 展示层当前只读取 `step.actor`，没有把 `step.recipient` 渲染到卡片标题。定位点：`display/index.html` 中 `actor.textContent = step.actor || "unknown"` 和 `meta.append(index, actor, type)`。
- 因此这是前端展示遗漏，不需要改动 trajectory 适配数据结构。

### 3.2 右栏维度分数布局拥挤且灰条无意义

- 当前 `dimensionRows(report)` 调用 `scoreRows(report.dimension_scores || {}, "", report.dimension_levels || {})`。
- `scoreLabel()` 把维度名称和评估级别拼成同一个 label，例如 `效率 · standard`，导致名称和级别粘在一起。
- `scoreRow()` 生成 `score-value`，内部包含分数文本、`score-bar` 和 `score-fill`；权重行复用同一逻辑并传入 `weight-fill`。
- 从截图看，灰条对调试价值不明显，还压缩了文本空间；应改成稳定列布局，而不是在同一行混合 label、level、value、bar。

## 4. 评估结果问题核查

### 4.1 case 级现象

| case | coverage | first_failure | finish/末尾阶段 | 结论 |
| --- | --- | --- | --- | --- |
| `cellular_off` | full | `m1->__finish__` | `m1->__finish__ fail`，`progress=0`、`tool_quality=0` | 真实 milestone 已完成，但 finish 被判 fail |
| `find_current_city_low_battery_mode` | full | `m5->__finish__` | `m5->__finish__ fail`，`tool_quality=0` | 真实 milestone 已完成，但 finish 被判 fail |
| `find_current_city_low_battery_mode_all_tools` | full | `m5->__finish__` | `m5->__finish__ fail`，`tool_quality=0` | 真实 milestone 已完成，但 finish 被判 fail |
| `find_days_till_holiday_alt_3_distraction_tools` | full | `m3->__finish__` | `m3->__finish__ fail`，`tool_quality=0` | 真实 milestone 已完成，但 finish 被判 fail |
| `find_temperature_low_battery_mode` | partial | `m0->m1` | 末尾是 synthetic missing，不是 `__finish__` | 未进入真正 finish |
| `modify_contact_with_message_recency` | partial | `__start__->m0` | 末尾是 synthetic missing，不是 `__finish__` | 未进入真正 finish |
| `remove_reminder_with_recency_latest_alt` | partial | `__start__->m2` | 末尾是 fail/missing 阶段，不是完成后 finish | 未进入真正 finish |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools` | partial | `m1->m2` | 末尾是 synthetic missing，不是 `__finish__` | 未进入真正 finish |

结论：用户观察成立，所有能够完整走到最后真实 milestone 的 scenario，最终 `最后milestone->__finish__` 都被判为 fail；未完整覆盖的 scenario 没有进入真正的 `__finish__`，属于另一类失败。

### 4.2 `__finish__` 被统一判 fail 的原因与修正语义

1. `data/toolsandbox/adapted_cases/*.json` 中，`__finish__` 只存在于 `milestone_graph.metadata.graph_analysis.augmented_edges`，并不是普通 milestone 节点。
2. `stage_goals` 只覆盖真实 milestone 阶段，例如 `m4->m5`，没有 `m5->__finish__`。这符合虚拟 finish 节点语义。
3. `finish_settlement()` 构造 `StageInterval(status=PASS, evidence=["finish 结算节点"])`，但随后仍调用通用 `append_stage_settlement()` 和 `evaluate_stage()`。
4. `evaluate_stage()` 会继承上一阶段的动态评估策略。若上一阶段把某些维度升级到 `standard` 或 `expensive`，`__finish__` 也会被送入 LLM judge。
5. LLM judge 对 `__finish__` 使用默认目标 `完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。`，看到的步骤只有 `end_conversation` 与 `None`，于是把它误解为“未执行验证工具”，经常把 `tool_quality` 打为 `0`，部分 case 还把 `progress` 打为 `0`。
6. `_aggregate_stage_status()` 会优先采用 higher-cost judge 的状态；一旦 expensive judge 返回 `fail`，cheap 的 `pass` 会被覆盖。

因此根因不是 ToolSandbox 轨迹真的失败，也不是 adapted case 缺少 finish 真实节点；根因是评估流程把虚拟 `__finish__` 当作普通任务阶段交给 standard/expensive LLM judge 复核，导致 LLM 对“收尾检查”产生额外工具验证义务。

修正后的语义应为：`__finish__` 是任务级最终核查阶段，需要核查任务目标是否完成，但核查对象不应局限于最后两条 `end_conversation` step，也不应要求 agent 在 finish 区间再次调用验证工具。它应基于完整 trajectory、已完成 milestone、最终状态和 terminal milestone 约束进行收尾判断。

### 4.3 重复 step 证据的原因

- 所有 `trajectory.json` 的 step index 都是唯一递增的，没有重复 step。
- 重复只出现在 `report.json` / `raw_summary.json` 的 `stage_reports[].evidence` 中。
- 来源是 LLM judge 输出与合并策略：`merge_text_items()` 和 `_merge_dimension_result()` 只做 exact dedupe，会同时保留 `step 51: user calls end_conversation` 与裸引用 `step 51`。
- prompt 的 required output 允许 evidence 使用 `step index`，因此 LLM 返回裸 `step N` 属于当前 schema 可接受输出；但展示层把它直接显示出来，看起来像“重复且空内容”。
- `finish_settlement()` 当前只给出 `finish 结算节点`，没有确定性写入终止 step 摘要，所以某些 case 只能看到裸 `step 23`、`step 24`。

### 4.4 其他 fail 阶段核查

| case | 首个失败阶段 | 核查结论 |
| --- | --- | --- |
| `find_temperature_low_battery_mode` | `m0->m1` | 评估流程问题。该阶段结构化 milestone 已通过，`m1_c0~m1_c3` 均为 1.0，`stage_score=0.899`，但 expensive 的 `interaction_quality` 单维返回 `fail`，使整个 stage status 变为 `fail` 并触发 `evaluation_policy_stop`。运行被提前终止在 step 45，agent 刚打开 WiFi，还没机会继续查询天气和回复用户。 |
| `modify_contact_with_message_recency` | `__start__->m0` | 主要是 agent 真实未完成。任务要求按“最近发送消息对象”更新电话，m0 要求调用 `get_current_timestamp`，轨迹只调用 `search_messages(sender_person_id='self')` 得到空结果，之后询问用户 Bart 并失败，没有完成目标。 |
| `remove_reminder_with_recency_latest_alt` | `__start__->m2` | 主要是 agent 真实选错对象。任务要求删除 next reminder，期望删除 `reminder_id=54f20...`，轨迹实际删除 `da2b856e...`，`removal_similarity=0`。 |
| `update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools` | `m1->m2` | 需要进一步确认 benchmark 语义。agent 实际执行了“friend -> enemy -> friend”的状态变更，但没有在中间态单独发出“Fredrik Thordendal and John Petrucci are now your enemies”的消息；adapted milestone 将该中间态通知作为 hard `emit_message` 约束，导致 m2 未完成、后续 m3/m4 被阻塞。如果原 benchmark 只关心最终状态和最终说明，则当前 adapted case 过严；如果原 benchmark 明确要求中间态通知，则该 fail 是合理的。 |

另有一个诊断层问题：`find_temperature_low_battery_mode` 在 m1 后策略终止时，m1 与 m2 均已 matched，按依赖关系 m3 应为“已经 ready 但没有后续候选 step”，但 `build_final_milestone_diagnostics()` 仅根据 `match_attempts.ready_before` 判断 `ready_ever`，因为终止后没有下一次 attempt，报告写成了“m3 从未 ready”。这会误导 debug，需要修正为基于 `matched` 和 graph 依赖重新计算最终 ready 状态。

## 5. 代码计划方案

### 5.1 面板展示修复

1. 修改 `display/index.html` 的轨迹卡片标题渲染：
   - 使用 `step.actor` 与 `step.recipient` 组成 `发起方 -> 接收方`。
   - `recipient` 缺失时回退到 `unknown`。
   - 复用现有 actor chip 样式，新增轻量 CSS 类控制间距与箭头。
2. 修改维度分数展示：
   - `dimensionRows(report)` 输出三列：维度名称、评估级别、分数。
   - CSS 使用稳定 grid：名称靠左，级别居中列左对齐，分数靠右。
   - 去掉 `score-bar`、`score-fill`。
3. 修改动态权重展示：
   - 权重行改为两列：维度名称、权重值。
   - 去掉 `weight-fill` 与灰条。
4. 检查窄屏下列宽，避免维度名称、级别、分数互相挤压。

### 5.2 finish 阶段评估修复

1. 将 `__finish__` 从普通阶段评估改为最终任务目标核查：
   - 核查所有真实 milestone 是否已经 matched。
   - 核查 terminal real milestone 在最终边界仍满足其核心约束，避免“中途完成、最后被破坏”。
   - 核查 fatal minefield 与最终状态是否存在明显冲突。
2. `finish_settlement()` 需要拿到 `scorer` 或一个最终核查结果对象，不能只依赖 `StageInterval(status=PASS)`。
3. finish 的 LLM 使用固定的 final verification 语义，而不是继承上一阶段 `EvaluationPolicyState.dimension_levels`：
   - 输入应覆盖完整任务摘要、真实 milestone 完成情况、terminal milestone 最终重评分、最后若干 step。
   - prompt 明确说明：不要要求 agent 在 finish 区间额外调用工具；判断依据是已有轨迹和最终状态。
   - 对结构化 final check 已确定通过的 case，LLM 只作为可选解释/复核，不应因“未再次调用验证工具”直接 fail。
4. finish 状态建议按以下规则聚合：
   - `fail`：真实 milestone 未全覆盖、terminal final check 失败、fatal minefield、最终状态与任务目标冲突。
   - `warn`：任务目标完成但存在非致命质量问题，例如冗余步骤、解释不足。
   - `pass`：目标完成且无 fatal/terminal 失败。
5. 不新增 `mN->__finish__` 为普通 stage_goal；`__finish__` 继续作为 graph 的虚拟终点，但报告中保留一个 final verification stage。

### 5.3 阶段 fail 与提前终止修复

1. 拆分“维度低分影响什么”和“任务是否继续由什么决定”：
   - 某维度分数较低，只影响下一阶段该维度的评估级别与动态权重。
   - 当前阶段是否继续执行，由当前阶段综合分数 `stage_score` 和 fatal/missing/invalid 等结构性失败决定。
2. 调整 `_aggregate_stage_status()` 或其调用侧：
   - 不再因为某个单独维度的 higher-cost judge 返回 `fail` 就直接把整个 stage 标记为 `fail`。
   - 先根据本阶段实际评估的维度计算综合分数；若 `stage_score >= pass_threshold`，阶段整体应通过。
   - 若 `fail_threshold <= stage_score < pass_threshold`，阶段整体可以是 `warn`，但一般不应阻断任务继续。
   - 只有 `stage_score < fail_threshold`、hard constraint 未通过、fatal minefield、missing/invalid 等情况才阻断执行。
3. 调整 `update_evaluation_policy._should_stop()`：
   - 不再使用 `result.status in {FAIL, MISSING, INVALID}` 作为粗粒度停止条件。
   - 继续执行判断优先基于 `stage_score`、结构化失败标记和 fatal minefield。
4. `find_temperature_low_battery_mode` 应作为回归用例：m1 的 interaction_quality 不应被评估，因为该阶段目标是修复 WiFi 状态，没有用户交互目标；即使某质量维度低分，也只能影响后续该维度评估级别，不能在 WiFi 刚修复后提前终止整个 case。

### 5.3.1 阶段聚焦评估维度

每个阶段不应默认评估所有维度。除 `progress` 与 `efficiency` 必须评估外，其他维度应根据 `stage_goal` 和约束语义选择。

建议规则：

- `progress`：必选，衡量阶段目标是否达成。
- `efficiency`：必选，衡量步骤成本、冗余与拖延。
- `tool_quality`：当阶段要求调用工具、解释工具失败、修改环境状态、读取外部/环境信息时选择。
- `state_consistency`：当阶段涉及 `set_state`、`preserve_state`、状态快照或跨 milestone reference 时选择。
- `safety`：当阶段涉及敏感状态、不可逆操作、权限、minefield 或安全策略时选择。
- `interaction_quality`：仅当阶段目标包含 `emit_message`、`user_visible_required=true`、需要向用户解释/确认/汇报时选择；若阶段只是 agent 调工具并修改环境设置，且没有用户交互目标，则不应评估该维度。
- `recovery`：当阶段包含失败工具结果、异常处理、重试、问题排查或“resolve issue”类目标时选择。

因此，在首次 adapt case、生成 `adapted_cases/*.json` 时，应同步生成每个 stage 的聚焦维度配置。`stage_goal` 描述“要完成什么”，`stage_evaluation_specs` 描述“应该评哪些维度以及为什么评”。

聚焦维度推导必须是 DynSTEER 框架层的公共能力，而不是某个 benchmark adapter 的私有逻辑。各 benchmark adapter 只负责把原生约束归一化为公共 `Constraint.stage_goal_semantics`、`ConstraintTarget` 或 benchmark-neutral metadata；随后由统一函数根据这些公共语义生成 `stage_evaluation_specs`。

### 5.4 pending 诊断修复

1. `build_final_milestone_diagnostics()` 的 `ready_ever` 不能只依赖 `match_attempts.ready_before`。
2. 对未 matched milestone，应基于 `dependency_predecessor_ids` 和最终 `matched` 重新计算：
   - 所有前驱已 matched 且无 candidate：`ready_without_candidate`。
   - 存在未完成前驱：`predecessor_not_matched`。
   - 有 candidate 但未过：`attempted_but_not_pass`。
3. 对策略提前终止场景，在 pending metadata 中写入 `blocked_by_policy_stop=true` 和 termination detail，便于区分“agent 自然结束未完成”和“评估器提前停止导致未完成”。

### 5.5 finish 证据与证据清洗

1. 为 finish interval 生成确定性证据：
   - 固定保留 `finish 结算节点`。
   - 输出真实 milestone 覆盖情况、terminal final check 结果、fatal minefield 状态。
   - 从 `(last_milestone_boundary, last_step]` 区间提取末尾终止 step 作为终止证据，而不是作为任务完成的唯一证据。
   - 输出形如 `step 51: user -> environment calls end_conversation`、`step 52: environment -> user returns None`。
2. 增加通用 evidence 清洗函数：
   - exact dedupe。
   - 当存在 `step N: ...` 具体证据时，删除裸 `step N`。
   - 当存在覆盖区间的具体证据时，删除裸 `step N-M`。
3. 在 LLM judge evidence 合并和 display build 的历史报告读取处复用该清洗函数。
4. 这样新产物不会再产生重复裸 step，旧产物在展示构建阶段也能被清洗。

### 5.6 测试与验收

1. 新增/更新 PyTest：
   - 构造完整 milestone 覆盖且 terminal final check 通过的 finish 场景，断言 finish 通过。
   - 构造 terminal milestone 中途 matched、最终状态被破坏的场景，断言 finish fail。
   - 构造 `find_temperature_low_battery_mode` 类似场景：阶段聚焦维度不包含 `interaction_quality`，结构化 milestone 通过后断言不触发 policy stop。
   - 构造“无用户交互、只调用工具修改环境设置”的 stage，断言 `focus_dimensions` 不包含 `interaction_quality`。
   - 构造“向用户回复结果”的 stage，断言 `focus_dimensions` 包含 `interaction_quality`。
   - 构造未聚焦维度缺失分数的 stage，断言该维度不参与 `stage_score` 且不会被当作 0 分升级。
   - 构造策略提前终止后的 pending 诊断，断言 ready milestone 被标为 `ready_without_candidate` 或 `blocked_by_policy_stop`，而不是“从未 ready”。
   - 断言 finish evidence 包含带 `actor -> recipient` 的终止 step 摘要。
   - 测试 evidence 清洗函数删除冗余裸 step。
2. 运行相关测试：
   - `uv run pytest tests/test_agent_step_closure.py tests/test_algorithm_revision.py`
3. 重新生成展示数据后抽查：
   - 完整覆盖 case 的 `mN->__finish__` 不再 fail。
   - 证据不再出现裸 `step N` 与具体 `step N: ...` 并存。
   - 面板左栏显示通信方向，右栏维度分数三列对齐。

## 6. 具体修复实施方案

### 6.1 代码架构说明

本次修复尽量在既有模块边界内完成，通过新增/迁移几个职责清晰的小模块来收拢逻辑：`__finish__` 最终核查模块、阶段目标模块、阶段评估规格模块和阶段轨迹辅助模块。这样既避免把 finish 逻辑塞进通用阶段结算函数，也避免把 stage 相关实现继续堆在 `__init__.py` 或散落到单个 benchmark adapter 中。

| 文件 | 变更类型 | 职责 |
| --- | --- | --- |
| `dynsteer/evaluate/final.py` | 新增 | 实现 `__finish__` 的任务级最终核查，产出结构化 final verification payload。 |
| `dynsteer/model.py` | 修改 | 增加 `StageEvaluationSpec` 与 `TaskCase.stage_evaluation_specs`，保存阶段聚焦维度。 |
| `dynsteer/stage/goal.py` | 新增/迁移 | 承接当前 `dynsteer/stage/__init__.py` 中的 stage_goal key、生成、解析、校验与 resolve 逻辑。 |
| `dynsteer/stage/spec.py` | 新增 | 实现 benchmark-agnostic 的 `generate_stage_evaluation_specs()`，基于公共约束语义推导每个 stage 的聚焦维度。 |
| `dynsteer/stage/trajectory.py` | 新增/迁移 | 承接当前 `dynsteer/stage/__init__.py` 中的阶段轨迹切片辅助函数。 |
| `dynsteer/stage/__init__.py` | 修改 | 仅保留对 `stage.goal`、`stage.spec`、`stage.trajectory` 和 `stage.settlement` 公共接口的 re-export，不放置具体业务实现。 |
| `dynsteer/adapter/loader.py` | 修改 | 读写 adapted case 时解析、保存、校验阶段聚焦维度配置。 |
| `dynsteer/adapter/toolsandbox/utils/convert.py` | 修改 | 只负责把 ToolSandbox 原生约束归一化为公共 `stage_goal_semantics`、`ConstraintTarget` 或 benchmark-neutral metadata；不承载聚焦维度推导。 |
| `dynsteer/stage/settlement.py` | 修改 | `finish_settlement()` 消费 final verification payload，生成 finish stage report；阶段聚合时区分结构性失败与质量失败。 |
| `dynsteer/evaluate/evaluator.py` | 修改 | 调用 `finish_settlement()` 时传入 scorer；策略提前终止后 pending 诊断写入 termination detail。 |
| `dynsteer/evaluate/policy.py` | 修改 | 调整提前终止条件，避免单维质量 fail 阻断任务继续执行。 |
| `dynsteer/evaluate/diagnostics.py` | 修改 | 基于最终 matched 状态重算 pending milestone 的 ready/blocker，而不是只依赖历史 attempts。 |
| `dynsteer/judges/confidence.py` 与 `dynsteer/utils.py` | 修改 | 增加并复用 evidence 清洗逻辑。 |
| `display/index.html` 与 `display/build.py` | 修改 | 修复轨迹方向、分数布局和历史 evidence 展示清洗。 |
| `docs/apis/evaluate.md`、`docs/apis/display.md` | 修改 | 更新 finish 语义、提前终止语义和展示字段说明。 |

### 6.1.1 stage 包结构修复

当前 `dynsteer/stage/__init__.py` 同时承担阶段轨迹切片、stage goal 生成、LLM 返回解析、公共语义转文本、常量定义和对外导出，职责过重。修复时应先拆分 stage 包结构，再接入新的 `stage_evaluation_specs`，避免继续把实现堆进 `__init__.py`。

建议拆分：

1. `dynsteer/stage/trajectory.py`
   - `stage_start_step_index()`
   - `stage_trajectory_steps()`
2. `dynsteer/stage/goal.py`
   - `DEFAULT_FINISH_STAGE_GOAL`
   - `stage_goal_key()`
   - `required_stage_goal_keys()`
   - `generate_stage_goals()`
   - `generate_stage_goals_with_llm()`
   - `validate_stage_goals()`
   - `resolve_stage_goal()`
   - `_generate_semantic_stage_goals()`、`_parse_stage_goal_from_resp()`、`_constraint_goal_text_from_semantics()` 等私有细节函数。
3. `dynsteer/stage/spec.py`
   - `generate_stage_evaluation_specs()`
   - `validate_stage_evaluation_specs()`
   - 维度选择和 rationale 生成的私有 helper。
4. `dynsteer/stage/__init__.py`
   - 只做 import re-export 与 `__all__`。
   - 不再直接 import `json`、`BaseLLM`、`Constraint` 等实现依赖。
   - 保持 `from dynsteer.stage import generate_stage_goals` 等既有公共导入路径可用。

同包内部模块若调用具体实现，优先从 `dynsteer.stage.goal`、`dynsteer.stage.spec`、`dynsteer.stage.trajectory` 导入，避免通过 `dynsteer.stage` 再绕回 `__init__.py` 造成循环依赖。

低复用 `Callable` 类型别名一并清理：`StageGoalLLMProvider = Callable[[], BaseLLM | None]` 这类只服务一处函数签名的别名不再单独定义，直接在签名中写 `Callable[[], BaseLLM | None] | None`。只有当某个 callable 类型在多个模块稳定复用，或名称本身表达明确业务概念时，才保留独立类型别名。

### 6.1.2 adapted case 数据结构扩展

新增阶段评估配置，和 `stage_goals` 使用同一套 stage key：

```json
{
  "stage_goals": {
    "m0->m1": "Complete milestone m1: ..."
  },
  "stage_evaluation_specs": {
    "m0->m1": {
      "focus_dimensions": [
        "progress",
        "efficiency",
        "tool_quality",
        "state_consistency"
      ],
      "dimension_rationale": {
        "progress": "阶段目标完成度必须评估",
        "efficiency": "所有阶段评估步骤效率",
        "tool_quality": "该阶段需要调用工具并处理工具结果",
        "state_consistency": "该阶段需要修改并保持环境状态"
      }
    }
  }
}
```

模型层建议新增：

```python
@dataclass
class StageEvaluationSpec:
    focus_dimensions: list[Dimension]
    dimension_rationale: dict[Dimension, str] = field(default_factory=dict)
```

`TaskCase` 增加：

```python
stage_evaluation_specs: dict[str, StageEvaluationSpec] = field(default_factory=dict)
```

校验规则：

1. `stage_evaluation_specs` 的 key 必须和 `stage_goals` 一致。
2. 每个 spec 必须包含 `progress` 与 `efficiency`。
3. 其他维度必须来自 `Dimension` 枚举。
4. 若 adapted case 缺失该字段，应要求重新生成，不做静默 fallback，避免旧数据继续触发全维度评估。

### 6.2 `__finish__` 最终核查实现

新增 `dynsteer/evaluate/final.py`，提供一个主入口：

```python
def build_finish_verification(
    task_case: TaskCase,
    trajectory: Trajectory,
    matched: dict[str, HarnessStageSettlement],
    state: RuntimeEvaluationState,
    scorer: GeneralScorer,
) -> JsonObject:
    ...
```

该函数只返回 JSON payload，不直接构造 `StageEvaluationResult`，让 `settlement.py` 继续负责阶段报告组装。payload 建议包含：

- `all_milestones_matched: bool`
- `unmatched_milestone_ids: list[str]`
- `terminal_milestone_ids: list[str]`
- `terminal_state_checks: list[JsonObject]`
- `fatal_minefield: bool`
- `status: "pass"|"warn"|"fail"`
- `score: float`
- `evidence: list[str]`
- `diagnosis: list[str]`

关键规则：

1. `all_milestones_matched=false` 时，finish 必须 `fail`，证据列出未完成 milestone。
2. terminal milestone 只对**最终状态敏感约束**重评分：
   - `ConstraintTarget.STATE_SNAPSHOT`。
   - `stage_goal_semantics.kind in {"set_state", "preserve_state"}`。
   - ToolSandbox guardrail / update / removal / snapshot state 约束。
3. 不在最终边界重评分 `emit_message`、`tool_call` 这类历史行为约束，因为它们已经由原 milestone match 证明；在最后 `end_conversation` 边界重跑会把正确历史行为误判为缺失。
4. 对 terminal 状态约束，使用最后一个真实 step 作为 boundary，结合 `scoring_context()` 中的 matched reference snapshots 调用现有 scorer。
5. 若 terminal 状态重评分失败，finish `fail`；若通过但存在质量警告，finish `warn`；否则 `pass`。

### 6.3 finish report 组装

修改 `dynsteer/stage/settlement.py`：

1. `finish_settlement()` 增加 `scorer: GeneralScorer` 入参。
2. 在函数内先调用 `build_finish_verification()`。
3. 构造 finish `StageInterval` 时，evidence 使用 final verification 的证据，而不是只有 `finish 结算节点`。
4. `__finish__` 不再继承上一阶段动态 `dimension_levels`。建议固定：
   - `progress` 来自 final verification 是否完成任务。
   - `state_consistency` 来自 terminal state checks。
   - `tool_quality` 不因最后没有额外工具调用而扣为 0。
   - `interaction_quality` 只作为质量维度，不覆盖任务完成状态。
5. metadata 写入：
   - `finish_stage_evaluation.mode = "final_verification"`
   - `finish_stage_evaluation.all_milestones_matched`
   - `finish_stage_evaluation.terminal_state_checks`
   - `finish_stage_evaluation.unmatched_milestone_ids`

### 6.4 阶段状态聚合修复

当前 `_aggregate_stage_status()` 的问题是 higher-cost judge 的单维 `fail` 会直接覆盖整体阶段状态。修复建议：

1. `evaluate_stage()` 先从 `task_case.stage_evaluation_specs[interval.stage_id]` 读取 `focus_dimensions`。
2. cheap/standard/expensive judge 都只接收 `focus_dimensions`，不再默认评估 `list(Dimension)`。
3. `stage_score_from_dimensions()` 只基于本阶段实际评估的维度和对应权重计算综合分数；未聚焦维度不参与当前阶段综合分数。
4. 低分维度的作用：
   - 更新该维度的动态权重。
   - 在 `update_evaluation_policy()` 中提升该维度下一阶段的评估级别。
   - 不直接决定当前阶段是否停止。
5. 当前阶段是否继续：
   - `stage_score >= pass_threshold`：继续。
   - `warn_threshold <= stage_score < pass_threshold`：记录 warn，通常继续。
   - `stage_score < fail_threshold`：停止。
   - hard constraint 未通过、missing/invalid、fatal minefield：停止。
6. 当某一聚焦维度低分但综合分仍达标时，写入 `metadata.low_score_dimensions` 和 `metadata.next_policy_reason`，不把 stage status 直接改成 fail。

这一条用于修复 `find_temperature_low_battery_mode`：m1 的目标是修复 WiFi 状态，focus dimensions 应为 `progress/efficiency/tool_quality/state_consistency/recovery`，不包含 `interaction_quality`；综合分达标后应继续执行，让 agent 后续查询天气并回复用户。

### 6.5 策略提前终止修复

修改 `dynsteer/evaluate/policy.py`：

1. `_should_stop()` 不再使用 `result.status in {FAIL, MISSING, INVALID}` 这一条粗规则。
2. 新停止条件建议为：
   - `result.metadata.structural_failure is True`。
   - `result.stage_score < thresholds.fail_threshold`。
   - `result.fatal_minefield_score >= thresholds.fatal_minefield_threshold`。
   - `result.metadata.missing_required_milestone is True`。
3. 对低分维度：
   - 只提高下一阶段对应维度的评估粒度。
   - 按动态权重公式调整该维度权重。
   - 不调用 `harness.stop_case()`。

`update_evaluation_policy._next_dimension_levels()` 也需要只遍历本阶段实际评估的维度，未评估维度保持上一阶段级别或回到 base level，避免“未评估维度被当作 0 分而升级”。

### 6.5.1 阶段聚焦维度生成实现

新增 `dynsteer/stage/spec.py`，并在 adapted case 生成流程中调用；`dynsteer/stage/__init__.py` 只负责导出该公共函数：

```python
def generate_stage_evaluation_specs(task_case: TaskCase) -> dict[str, StageEvaluationSpec]:
    ...
```

该函数是 DynSTEER 公共函数，不属于任何单个 benchmark 或私有 adapter。函数输入应只依赖标准 `TaskCase`、`MilestoneGraph`、`Constraint.stage_goal_semantics`、`ConstraintTarget`、阶段目标文本和 benchmark-neutral metadata；如果某个 benchmark 有特殊原生字段，应先在 adapter 中归一化为上述公共语义，再交给该函数处理。

生成规则建议集中在该函数中，不在多个 adapter 文件中重复实现：

1. 初始集合：`{progress, efficiency}`。
2. 扫描该 stage 对应 milestone 的 constraints 和公共语义：
   - `StageGoalSemanticKind.TOOL_CALL`、工具 trace 要求或公共 metadata 中的 `requires_tool=true`：加入 `tool_quality`。
   - `StageGoalSemanticKind.SET_STATE` / `PRESERVE_STATE`、`ConstraintTarget.STATE_SNAPSHOT`、状态快照或跨 milestone reference：加入 `state_consistency`。
   - `StageGoalSemanticKind.EMIT_MESSAGE`、`user_visible_required=true`、需要解释/确认/汇报给用户：加入 `interaction_quality`。
   - constraint、minefield 或公共 metadata 涉及安全、权限、敏感状态、不可逆操作：加入 `safety`。
3. 扫描 stage_goal 文本或 metadata：
   - 包含 resolve、recover、retry、fix issue、failure、exception 等问题处理语义：加入 `recovery`。
4. benchmark adapter 的职责边界：
   - adapter 可以补充或归一化公共语义，例如把 ToolSandbox 的状态修改约束标记为 `SET_STATE`，把用户可见消息约束标记为 `EMIT_MESSAGE`。
   - adapter 不应自行维护一套聚焦维度规则，也不应在 `dynsteer/adapter/<benchmark>/...` 下实现 `generate_stage_evaluation_specs()` 的变体。
   - 只有环境状态调整、没有用户消息要求的阶段，经公共语义推导后不应加入 `interaction_quality`。
5. 每个维度附带 rationale，便于面板和报告解释为什么评/为什么没评。

### 6.6 pending 诊断修复

修改 `dynsteer/evaluate/diagnostics.py`：

1. `build_final_milestone_diagnostics()` 中为每个未 matched milestone 重新计算：
   - `pending_predecessors = dependency_predecessor_ids - matched_ids`
   - `finally_ready = len(pending_predecessors) == 0`
2. blocker 优先级改为：
   - 有 scored candidate 且未过：`attempted_but_not_pass`
   - 有未完成前驱：`predecessor_not_matched`
   - `finally_ready=true` 且无 candidate：`ready_without_candidate`
   - 否则：`not_ready`
3. 如果 runtime 因 evaluation policy stop 结束，pending stage metadata 增加：
   - `blocked_by_policy_stop=true`
   - `policy_stop_detail`

这样 `find_temperature_low_battery_mode` 的 m3 不会再显示“从未 ready”，而应显示“由于评估器在 m1 后提前终止，m3 ready 后没有后续候选 step”。

### 6.7 ToolSandbox adapter 过严问题核查与修复

`update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools` 暂不直接修改评分逻辑，先增加一项核查任务：

1. 回查原 ToolSandbox milestone 或 expected trace，确认是否要求中间态用户通知。
2. 若原 benchmark 只要求最终回答“谁是朋友”或最终恢复状态：
   - 调整 `dynsteer/adapter/toolsandbox/utils/convert.py` 中对中间 `SANDBOX` message constraint 的 hard 约束生成逻辑。
   - 将非最终用户通知从 mandatory hard milestone 降为 optional quality evidence，或只在最终 terminal message 中评估。
3. 若原 benchmark 明确要求中间态通知：
   - 保持当前 fail 结论。
   - 只优化诊断文字，说明 agent 做了状态变更但缺少中间用户通知。

### 6.8 展示与 evidence 修复

1. `display/index.html`：
   - 轨迹卡片标题改为 `actor -> recipient`。
   - 分数行改为三列 `dimension / level / score`。
   - 权重行改为两列 `dimension / weight`。
   - 移除灰条。
2. `dynsteer/utils.py` 增加 `clean_evidence_items(values: list[str], limit: int | None = None)`。
3. `dynsteer/judges/confidence.py` 的 `merge_text_items()` 对 `evidence` 调用清洗函数。
4. `display/build.py` 读取旧报告时也调用清洗函数，避免历史产物继续显示裸 `step N`。

### 6.9 建议落地顺序

1. 先修阶段状态聚合与策略提前终止，优先解决“比以前更早 fail”的核心问题。
2. 再修 finish final verification，确保完整覆盖 case 的 finish 判断基于任务目标。
3. 再修 pending 诊断，避免 debug 面板给出错误 blocker。
4. 再修 evidence 清洗和前端显示。
5. 最后核查 ToolSandbox adapter 的中间消息约束是否过严。

### 6.10 验收命令

建议最小验收：

```powershell
uv run pytest tests/test_agent_step_closure.py tests/test_algorithm_revision.py
```

建议补充验收：

```powershell
uv run pytest
python display/build.py
```

重新跑 `toolsandbox` 这 8 个 scenario 后重点检查：

- `find_temperature_low_battery_mode` 不应在 m1 后提前停止，应继续到天气查询/用户回复阶段。
- `find_temperature_low_battery_mode` 的 WiFi 修复阶段不应评估 `interaction_quality`。
- 完整覆盖的 4 个 case，`__finish__` 应基于 final verification，而不是因最后没有额外工具验证而 fail。
- 每个 stage 的面板应展示实际聚焦维度；未聚焦维度不应显示为 0 分或 unknown fail。
- 真实未完成的 `modify_contact...`、`remove_reminder...` 仍应保留 fail。
- `update_contact_relationship...` 的结论应取决于原 benchmark 是否要求中间态通知。
- 面板中不再出现重复裸 `step N` 证据。

## 7. 风险与注意事项

- `__finish__` 需要承担任务级最终验证义务，但它不是普通 milestone；实现时必须避免把“最终核查”变成“要求 agent 在最后两步重新执行验证动作”。
- 对历史 `report.json` 的展示清洗只能改善面板可读性，不会改写已有结果文件。
- `end_conversation` 在 ToolSandbox 中表现为 `user -> environment` message，而非 DynSTEER 标准 `tool_call`；finish 证据生成需要基于内容识别，而不能只看 `event_type == tool_call`。
- 对 `update_contact_relationship_with_relationship_twice_multiple_user_turn_all_tools` 的结论依赖原始 ToolSandbox milestone 语义，需要回查原 benchmark 标注后决定是否调整 adapter。

## 附录A. 项目中没有把握实现的模块部分

- ToolSandbox 原始运行环境内部状态不可由当前仓库完全重放；本报告基于当前 `runs/results/data` 产物和代码链路定位，无法验证外部 benchmark runtime 是否还有其他非确定性因素。
- 历史 LLM judge 的真实推理过程不可重算，只能依据 `raw_summary.json` 中保留的输出、诊断文本和当前 prompt/schema 推断其失败原因。
- 前端窄屏布局需要浏览器截图验证才能完全确认视觉效果；代码计划中只能先约束 grid 结构和列宽策略。
