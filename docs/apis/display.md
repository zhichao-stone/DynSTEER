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
  - `scenario.stage_reports[]` 包含 `dimension_scores`、`next_weights`、`metadata.active_evaluation_policy` 与 `metadata.next_evaluation_policy`，用于展示阶段级评估动态和跨阶段评估策略。
  - `scenario.stage_settlements[].milestone_matching` 包含 milestone 命中边界、匹配分数和约束得分摘要，用于中列节点展开详情。
  - `scenario.minefield_matches[]` 包含运行期 boundary 级 minefield 命中、`severity`、`score` 与 `penalty`，用于解释最终总分扣罚。

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
