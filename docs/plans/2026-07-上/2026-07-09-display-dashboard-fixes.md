# display 评估看板展示修复方案

## 1. 问题与目标

当前 `display/index.html` 与 `display/assets/evaluation-dashboard-mockup-zh.png` 的主要差异集中在里程碑图和联动交互：

- 图节点只展示近似相同的 `ToolSandbox milestone` 文案，无法直接看到 `m0/m1/...` 标号。
- 图数据只来自运行期 `raw_summary.json`，缺少 adapted case 中 `graph_analysis.augmented_edges` 提供的 `__start__` 与 `__finish__` 超级节点。
- 点击右侧评估阶段时，中列只高亮该阶段终点 milestone，没有高亮从 `stage_anchor` 到该 milestone 的阶段内节点。
- 点击图节点或评估阶段时，左列定位步骤使用 `scrollIntoView`，会带动整个页面滚动。

目标是在不改动评估核心逻辑的前提下，让静态看板更接近 mockup：图结构完整、节点标识清晰、阶段高亮语义正确，且联动只滚动左列内部容器。

## 2. 推荐实现方案

采用“小范围数据补全 + 前端纯渲染修正”的方案。

1. `display/build.py` 增加 `data_dir` 数据来源，默认读取 `data/<benchmark>/adapted_cases/<scenario_id>.json`。
2. 构造 `milestone_graph` 时优先合并 adapted case 的节点锚点信息与 `metadata.graph_analysis.augmented_edges`。
3. 当增强边包含 `__start__` 或 `__finish__` 时，自动补充超级节点，避免前端只看到普通 milestone。
4. `stage_settlements` 输出 `stage_anchor_milestone_id` 等阶段锚点字段，供前端阶段高亮使用。
5. `display/index.html` 将节点主标题改为 `milestone_id`，副标题展示阶段/状态信息；阶段选中时根据图边计算 anchor 到终点间的节点集合并高亮。
6. 左列步骤定位改为计算 `panel.scrollTop`，只滚动 `.trajectory-panel .panel-body`。

## 3. 备选方案

- 只在前端根据现有 `edges` 推断 `__start__`/`__finish__`：改动更少，但不能保证与评估期 `stage_anchor` 完全一致。
- 修改评估诊断输出，让 `raw_summary.json` 本身包含增强图：数据更统一，但会影响评估核心产物，当前需求只涉及展示面板，风险和范围偏大。

## 4. 测试与验收

- 新增或扩展 `tests/test_display.py`，先验证缺少超级节点和 `stage_anchor` 字段的失败场景。
- 运行 `uv run pytest tests/test_display.py`。
- 重新生成 `display/data.js`，确认实际 toolsandbox 数据包含 `__start__`、`__finish__` 和增强边。
- 检查静态 HTML 不引入外部网络资源，仍使用 `textContent` 防止 XSS。

## 附录A. 项目中没有把握实现的模块部分

当前没有明显无法把握的模块。唯一需要在验收时重点观察的是“阶段内节点”的视觉语义：本方案采用图论上的“从 stage_anchor 可达且可到达当前 milestone 的所有节点”，与用户描述的“以 stage_anchor 为起点、以阶段里程碑为结尾”一致。
