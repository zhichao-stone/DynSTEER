# 静态评估看板数据接口

## `build_display_data`

汇总 `runs`、`results` 与 adapted case 目录下的 benchmark/method/case 产物，返回可序列化为 `display/data.js` 的看板数据对象。

入参：

- `runs_dir: Path`：原始运行轨迹目录。
- `results_dir: Path`：评估结果目录。
- `data_dir: Path | None`：benchmark 数据目录；为 `None` 时默认使用 `runs_dir` 同级的 `data` 目录。

`runs_dir` 与 `results_dir` 下的 case 产物按 `<benchmark>/<method>/<case_id>` 或实验态 `exp/<experiment_id>/<benchmark>/<model_id>/<method>/<case_id>` 扫描，旧的 `experiments/...` 目录也仍然兼容；看板数据中的 run 节点会保留 `experiment_id`（如有）、`benchmark`、`model_id` 与 `method`，并按这几个维度区分不同 run。

输出：

- `dict[str, Any]`：包含 `generated_at` 与 `runs` 的展示数据。
  - `scenario.milestone_graph` 会合并 adapted case 中的增强图信息，包含 `__start__`、`__finish__` 超级节点、增强边和阶段锚点 metadata。
  - `scenario.stage_definitions[]` 是右列阶段渲染骨架，来自 adapted case 的 `stage_goals` 和 graph metadata 派生出的当前可展示阶段。每项包含 `stage_id`、`anchor_milestone_id`、`milestone_id` 与 `stage_goal`。
  - `scenario.stage_reports[]` 包含聚焦维度的 `dimension_scores`、`dimension_levels`、`dimension_confidence`、`dimension_uncertainty`、`next_weights`、`metadata.focus_dimensions`、`metadata.next_evaluation_policy` 与 `metadata.evaluation_termination`，用于展示阶段级评估动态和跨阶段评估策略。
  - `scenario.stage_settlements[].milestone_matching` 包含 milestone 命中边界、匹配分数和约束得分摘要，用于中列节点展开详情。`stage_settlements[].metadata.stage_trace.state_snapshot_delta_summary` 会在原始结算 metadata 中保留轻量状态变化摘要。
  - `scenario.minefield_matches[]` 包含运行期 boundary 级 minefield 命中、`severity`、`fatal`、`score`、`penalty`、`trigger_summary` 与 adapted case 中的约束定义，用于解释最终总分扣罚。
  - `scenario.termination` 透传 `terminated_by_policy`、`termination_code`、`termination_reason` 和 `termination_detail`。当策略提前终止且没有任何 stage report 时，展示数据会生成 `status="terminated"` 的 finish 未结算报告，并在 `metadata.finish_unsettled_due_to_termination=true` 中保留终止详情；若终止详情包含 minefield 命中，会合并 adapted case 的 minefield 名称、严重级别、触发条件和约束摘要。
  - `scenario.summary.trajectory_cost_available` 与 `trajectory_latency_available` 表示轨迹 step cost 是否真实可用；不可用时 token/latency 汇总中的 `0` 仅表示缺省兜底。

`scenario.stage_definitions[]` 的 `stage_id` 固定使用 `{anchor}->{milestone}`：

- milestone 阶段来自 `adapted_case.stage_goals`，例如 `__start__->m0`、`m3->m4`。
- finish 阶段来自 `milestone_graph.metadata.graph_analysis.finish_stage_anchor_predecessor_id` 与 `finish_node_id`，例如 `m4->__finish__`。
- 若 `stage_goals` 已显式包含 finish key，使用该文案；否则使用默认 finish 目标文案。
- 如果某个非 finish 阶段已经 `fail`、`missing`、`invalid` 或 `fatal`，展示数据会截断掉未触达的普通后续阶段；但当失败报告已经到达 finish anchor milestone 时，会额外保留 `anchor->__finish__` 定义，右栏可用 `not_started` 展示未进入 finish 的事实。

静态面板右列以 `stage_definitions` 为骨架渲染，使用 `stage_reports` 按相同 `stage_id` 回填状态、分数、诊断、证据和策略 metadata。阶段标题固定显示为 `{stage_id}:{status}`，例如 `m3->m4:fail`、`m4->__finish__:pass`。若某个定义阶段尚无报告，面板使用 `status="not_started"` 的空报告展示该固定阶段；失败后的普通后续阶段不会进入该骨架，未报告的 finish definition 仅在 anchor milestone 已出现在报告中时保留。

左列轨迹卡片标题显示通信方向：`step.index`、`actor -> recipient` 与 `event_type`。若步骤是 `tool_call` 或 `tool_result`，标题行会额外显示 `tool: <name>`；工具结果会优先读取 `openai_function_name`，缺失时按 `openai_tool_call_id` 或最近一次工具调用回推工具名。若历史轨迹缺少 `recipient`，前端显示为 `unknown`。

左列标题右侧提供两个筛选控件：发起方与接收方。筛选只在浏览器内过滤 `scenario.trajectory.steps`，不改变 `data.js` 结构。标题副文本显示 `显示 N / 共 M 步`；若当前 milestone 或 stage 选中的 step 被筛选隐藏，轨迹面板会显示“当前筛选隐藏了选中步骤”提示，并提供“清除筛选”按钮。

右列维度分数使用三列布局：维度名称、评估级别、分数。动态权重使用两列布局：维度名称、权重值。面板不再渲染灰色进度条，避免窄屏下挤压文本。

`display/build.py` 读取历史 `report.json` 与 `stage_settlements` 时会调用 `clean_evidence_items(...)` 清洗 evidence；已有产物中裸 `step N` 与 `step N: ...` 并存时，看板只展示具体 step 证据。

中列 milestone 节点点击后，会在当前 SVG 节点位置原地展开为较宽详情节点；再次点击同一 milestone 会收回详情。详情节点按“阶段目标”“milestone 描述”“约束定义”“匹配诊断”分区，并使用可展开/收起的折叠块展示长文本；其中“约束定义”会继续按 constraint 拆成独立可折叠子项。选中节点右下角提供拉伸手柄，拖动时会更新当前节点宽高并重新计算图布局；切换节点、再次点击收起或切换 run/case 会清空自定义尺寸。SVG 初始视图使用紧凑布局并随中列宽度缩放，避免在常见线性 milestone 图中产生横向滚动。

失败阶段的右侧阶段卡片会优先展示 `metadata.failure_summary`、`metadata.failure_reasons` 和 `metadata.failed_constraints`。`failed_constraints[]` 可包含 `expected_excerpt` 与 `actual_excerpt`，用于解释消息语义或结构化约束的 expected-vs-actual 差异。若 pending stage metadata 中包含 `semantic_review`，右侧“匹配失败摘要”会展示专用语义复判的 accepted/rejected 约束 id、置信度和原因。若历史结果缺少这些字段，前端会基于最佳 `match_attempts[].candidate_scores[]` 生成一条轻量匹配失败摘要，原始最佳候选仍保留在“最佳匹配尝试”折叠块中。

warn 阶段会在中列 milestone 图中显示琥珀色状态，`not_started`/`missing` 会显示为灰色 pending 状态，`terminated` 会按失败样式展示。右侧阶段卡片会从 `metadata.low_score_dimensions` 和 `metadata.stage_quality_diagnostics` 提炼“警告原因”，包含低分维度、工具参数警告、失败工具结果、空工具结果和 grounding 风险摘要；warn 阶段默认展开该摘要。finish 未结算报告会展示“finish 未结算”折叠块，说明对应策略终止原因。

顶部 run 与 case 切换都使用可搜索下拉框。run 输入框显示当前 `run.benchmark / run.method`，case 输入框默认显示当前 `scenario.scenario_id`；键入内容后，下拉列表实时过滤为 id 以前缀匹配该输入的选项，例如输入 `fi` 时只显示 `fi...` 开头的 case。下拉列表最多显示 9 行，多余选项通过滚动查看；重新打开下拉时会高亮当前选中项，并尽量将其滚动到列表中间。过滤不改变当前选中项，只有点击选项或按 Enter 确认时才切换。

constraint 定义来自 adapted case 的 `milestone_graph.nodes[].constraints[]`，每项包含：

- `constraint_id`、`target`、`namespace`、`operator`、`threshold`、`hard`。
- `evaluator_hint`：面向评估器的提示摘要。
- `expected_summary`：适合节点或短摘要使用的 expected 概览。
- `semantic_kind`、`semantic_summary`：来自 `stage_goal_semantics` 的约束语义摘要，例如 `AGENT -> USER: ...` 或 `tool search_contacts args=...`。
- `expected_rows_summary`：从 expected rows 提炼的可读行摘要，用于把 ToolSandbox 工具调用、消息或状态期望直接展示出来。
- `expected_detail`：适合详情卡展示的 expected 完整截断内容，最长约 1200 字符。

## `write_display_data_js`

将看板数据写为静态 HTML 可直接载入的全局变量赋值脚本。

入参：

- `data: dict[str, Any]`：看板数据。
- `output: Path`：`data.js` 输出路径。

输出：

- 无返回值。写出格式为 `window.DYNSTEER_DATA = ...;`。

## 命令行生成

```powershell
uv run python -m display.build --runs-dir runs --results-dir results --data-dir data --output display/data.js
```
