# 静态评估看板数据接口

## `build_display_data`

汇总 `runs`、`results` 与 adapted case 目录下的 run/scenario 产物，返回可序列化为 `display/data.js` 的看板数据对象。

入参：

- `runs_dir: Path`：原始运行轨迹目录。
- `results_dir: Path`：评估结果目录。
- `data_dir: Path | None`：benchmark 数据目录；为 `None` 时默认使用 `runs_dir` 同级的 `data` 目录。

输出：

- `dict[str, Any]`：包含 `generated_at` 与 `runs` 的展示数据。
  - `scenario.milestone_graph` 会合并 adapted case 中的增强图信息，包含 `__start__`、`__finish__` 超级节点、增强边和阶段锚点 metadata。
  - `scenario.stage_definitions[]` 是右列阶段渲染骨架，来自 adapted case 的 `stage_goals` 和 graph metadata 派生出的当前可展示阶段。每项包含 `stage_id`、`anchor_milestone_id`、`milestone_id` 与 `stage_goal`。
  - `scenario.stage_reports[]` 包含 `dimension_scores`、`next_weights`、`metadata.active_evaluation_policy` 与 `metadata.next_evaluation_policy`，用于展示阶段级评估动态和跨阶段评估策略。
  - `scenario.stage_settlements[].milestone_matching` 包含 milestone 命中边界、匹配分数和约束得分摘要，用于中列节点展开详情。
  - `scenario.minefield_matches[]` 包含运行期 boundary 级 minefield 命中、`severity`、`score` 与 `penalty`，用于解释最终总分扣罚。

`scenario.stage_definitions[]` 的 `stage_id` 固定使用 `{anchor}->{milestone}`：

- milestone 阶段来自 `adapted_case.stage_goals`，例如 `__start__->m0`、`m3->m4`。
- finish 阶段来自 `milestone_graph.metadata.graph_analysis.finish_stage_anchor_predecessor_id` 与 `finish_node_id`，例如 `m4->__finish__`。
- 若 `stage_goals` 已显式包含 finish key，使用该文案；否则使用默认 finish 目标文案。
- 如果某个非 finish 阶段已经 `fail`、`missing`、`invalid` 或 `fatal`，展示数据会截断到该失败阶段，不再追加或展示 `__finish__`。

静态面板右列以 `stage_definitions` 为骨架渲染，使用 `stage_reports` 按相同 `stage_id` 回填状态、分数、诊断、证据和策略 metadata。阶段标题固定显示为 `{stage_id}:{status}`，例如 `m3->m4:fail`、`m4->__finish__:pass`。若某个定义阶段尚无报告，面板使用 `status="not_started"` 的空报告展示该固定阶段；但失败后的后续阶段不会进入该骨架。

中列 milestone 节点点击后，会在图下方详情卡展示以该 milestone 结尾的 `stage_goal`、milestone 描述、constraint 定义与匹配诊断。SVG 节点只展示短摘要，避免长文本撑开拓扑图。

constraint 定义来自 adapted case 的 `milestone_graph.nodes[].constraints[]`，每项包含：

- `constraint_id`、`target`、`namespace`、`operator`、`threshold`、`hard`。
- `evaluator_hint`：面向评估器的提示摘要。
- `expected_summary`：适合节点或短摘要使用的 expected 概览。
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
uv run python display/build.py --runs-dir runs --results-dir results --data-dir data --output display/data.js
```
