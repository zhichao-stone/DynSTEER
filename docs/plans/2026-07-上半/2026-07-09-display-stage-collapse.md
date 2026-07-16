# display 评估阶段折叠展示方案

## 1. 问题与目标

当前右列评估阶段卡片会直接展开显示维度分数和动态权重，信息密度偏高。用户希望：

- 阶段详情默认收起，点击阶段后再显示。
- 阶段详情内部的“维度分数”和“动态权重”也能分别收起、点开。

## 2. 实现方案

仅调整 `display/index.html` 的右列渲染：

1. `stageCard` 改成使用 `<details>` 作为阶段卡片容器，`<summary>` 显示阶段标题、状态和摘要 chip。
2. 阶段详情区域放在 summary 后，保留现有维度分数和动态权重渲染。
3. `scoreSection` 改为 `<details>`，让“维度分数”和“动态权重”独立折叠。
4. 保持右列点击阶段时的联动逻辑：点击阶段 summary 仍会高亮中列阶段范围并滚动左列。

## 3. 测试与验收

- 扩展 `tests/test_display.py` 的静态 HTML 测试，验证存在 `stage-detail-card`、`stage-detail-body`、`score-section`、`stageScoreSection` 等折叠渲染入口。
- 运行 `uv run pytest tests/test_display.py`。
- 运行 `uv run pytest`。

## 附录A. 项目中没有把握实现的模块部分

没有明显无法把握的模块。需要注意的是 native `<details>` 与阶段联动点击同时存在，本方案把联动绑定在 `<summary>` 上，避免详情内容区域的点击反复触发阶段重渲染。
