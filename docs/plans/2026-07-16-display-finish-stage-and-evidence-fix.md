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

### 4.2 `__finish__` 被统一判 fail 的原因

1. `data/toolsandbox/adapted_cases/*.json` 中，`__finish__` 只存在于 `milestone_graph.metadata.graph_analysis.augmented_edges`，并不是普通 milestone 节点。
2. `stage_goals` 只覆盖真实 milestone 阶段，例如 `m4->m5`，没有 `m5->__finish__`。这符合虚拟 finish 节点语义。
3. `finish_settlement()` 构造 `StageInterval(status=PASS, evidence=["finish 结算节点"])`，但随后仍调用通用 `append_stage_settlement()` 和 `evaluate_stage()`。
4. `evaluate_stage()` 会继承上一阶段的动态评估策略。若上一阶段把某些维度升级到 `standard` 或 `expensive`，`__finish__` 也会被送入 LLM judge。
5. LLM judge 对 `__finish__` 使用默认目标 `完成收尾检查：确认已达成的阶段目标没有被后续证据推翻。`，看到的步骤只有 `end_conversation` 与 `None`，于是把它误解为“未执行验证工具”，经常把 `tool_quality` 打为 `0`，部分 case 还把 `progress` 打为 `0`。
6. `_aggregate_stage_status()` 会优先采用 higher-cost judge 的状态；一旦 expensive judge 返回 `fail`，cheap 的 `pass` 会被覆盖。

因此根因不是 ToolSandbox 轨迹真的失败，也不是 adapted case 缺少 finish 真实节点；根因是评估流程把虚拟 `__finish__` 当作普通任务阶段交给 standard/expensive LLM judge 复核，导致 LLM 对“收尾检查”产生额外工具验证义务。

### 4.3 重复 step 证据的原因

- 所有 `trajectory.json` 的 step index 都是唯一递增的，没有重复 step。
- 重复只出现在 `report.json` / `raw_summary.json` 的 `stage_reports[].evidence` 中。
- 来源是 LLM judge 输出与合并策略：`merge_text_items()` 和 `_merge_dimension_result()` 只做 exact dedupe，会同时保留 `step 51: user calls end_conversation` 与裸引用 `step 51`。
- prompt 的 required output 允许 evidence 使用 `step index`，因此 LLM 返回裸 `step N` 属于当前 schema 可接受输出；但展示层把它直接显示出来，看起来像“重复且空内容”。
- `finish_settlement()` 当前只给出 `finish 结算节点`，没有确定性写入终止 step 摘要，所以某些 case 只能看到裸 `step 23`、`step 24`。

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

1. 在 `dynsteer/stage/settlement.py` 中对 `interval.milestone_id == FINISH_NODE_ID` 增加专门路径。
2. `__finish__` 阶段仅使用 cheap deterministic baseline，不调用 `standard_judge` / `expensive_judge`。
3. 保留 finish 作为报告中的阶段，但在 metadata 中标记：
   - `finish_stage_evaluation.mode = "cheap_only"`
   - `finish_stage_evaluation.reason = "finish 是虚拟收尾节点，不引入额外 LLM 验证义务"`
4. 不修改 adapted case 的 graph 结构；`__finish__` 继续作为 `graph_analysis.augmented_edges` 的虚拟终点存在。
5. 不新增 `mN->__finish__` stage_goal，避免把虚拟 finish 重新塑造成普通任务目标。

### 5.3 finish 证据与证据清洗

1. 为 finish interval 生成确定性证据：
   - 固定保留 `finish 结算节点`。
   - 从 `(last_milestone_boundary, last_step]` 区间提取末尾终止 step。
   - 输出形如 `step 51: user -> environment calls end_conversation`、`step 52: environment -> user returns None`。
2. 增加通用 evidence 清洗函数：
   - exact dedupe。
   - 当存在 `step N: ...` 具体证据时，删除裸 `step N`。
   - 当存在覆盖区间的具体证据时，删除裸 `step N-M`。
3. 在 LLM judge evidence 合并和 display build 的历史报告读取处复用该清洗函数。
4. 这样新产物不会再产生重复裸 step，旧产物在展示构建阶段也能被清洗。

### 5.4 测试与验收

1. 新增/更新 PyTest：
   - 构造 `evaluation_policy` 中包含 `standard` / `expensive` 的 finish 场景。
   - 使用会抛错的 fake standard/expensive judge，验证 finish 不调用高成本 judge。
   - 断言 `stage_result.status == PASS`，metadata 包含 `finish_stage_evaluation.mode == "cheap_only"`。
   - 断言 finish evidence 包含带 `actor -> recipient` 的终止 step 摘要。
   - 测试 evidence 清洗函数删除冗余裸 step。
2. 运行相关测试：
   - `uv run pytest tests/test_agent_step_closure.py tests/test_algorithm_revision.py`
3. 重新生成展示数据后抽查：
   - 完整覆盖 case 的 `mN->__finish__` 不再 fail。
   - 证据不再出现裸 `step N` 与具体 `step N: ...` 并存。
   - 面板左栏显示通信方向，右栏维度分数三列对齐。

## 6. 风险与注意事项

- 如果未来希望 `__finish__` 承担真实验证义务，应把它建模为显式 milestone，而不是沿用虚拟 finish 节点。
- 对历史 `report.json` 的展示清洗只能改善面板可读性，不会改写已有结果文件。
- `end_conversation` 在 ToolSandbox 中表现为 `user -> environment` message，而非 DynSTEER 标准 `tool_call`；finish 证据生成需要基于内容识别，而不能只看 `event_type == tool_call`。

## 附录A. 项目中没有把握实现的模块部分

- ToolSandbox 原始运行环境内部状态不可由当前仓库完全重放；本报告基于当前 `runs/results/data` 产物和代码链路定位，无法验证外部 benchmark runtime 是否还有其他非确定性因素。
- 历史 LLM judge 的真实推理过程不可重算，只能依据 `raw_summary.json` 中保留的输出、诊断文本和当前 prompt/schema 推断其失败原因。
- 前端窄屏布局需要浏览器截图验证才能完全确认视觉效果；代码计划中只能先约束 grid 结构和列宽策略。
