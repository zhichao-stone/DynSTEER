# display 评估阶段动态详情展示方案

## 1. 问题与目标

当前右列评估阶段主要展示 `milestone`、总分、置信度、范围和 evidence 文本。用户期望右列体现评估阶段的动态变化，包括：

- 评估级别 `evaluator_level`。
- 总分之外的各维度分数 `dimension_scores`。
- 动态权重 `next_weights`。
- milestone 匹配分数、边界、约束得分不再挤在右列，而是点击中列 milestone 节点时在节点内展开显示。

## 2. 根因

`display/build.py` 已经读取了 `report.json` 的 `stage_reports`，但 `_stage_report_summary` 没有导出 `next_weights`。同时 `stage_settlements[].metadata.milestone_matching` 中已有 milestone 匹配诊断，却没有进入 `display/data.js`，导致前端只能在右列 evidence 中展示匹配痕迹。

## 3. 实现方案

采用展示层最小改动：

1. `display/build.py`
   - `_stage_report_summary` 增加 `next_weights`。
   - `_settlement_summary` 增加 `milestone_matching` 摘要，只保留展示需要的 score、boundary、constraint_scores、ready/matched predecessor 信息，避免输出过大 actual 数据。
2. `display/index.html`
   - 右列 `stageCard` 改为“阶段动态”视图：展示评估级别、总分、置信度、不确定性、范围、各维度分数条、下一轮权重条。
   - 中列 milestone 节点选中后扩大节点高度，显示匹配分数、边界 step、约束得分摘要。
   - 保留右列点击阶段联动中列阶段范围高亮和左列范围滚动。
3. `tests/test_display.py`
   - 增加数据构建测试，验证 `next_weights` 和 `milestone_matching` 被导出。
   - 增加静态 HTML 测试，验证前端包含阶段动态条与 milestone 详情渲染入口。

## 4. 验收方式

- `uv run pytest tests/test_display.py` 先红后绿。
- 重新生成 `display/data.js`。
- `uv run pytest` 完整测试通过。

## 附录A. 项目中没有把握实现的模块部分

没有明显无法把握的部分。唯一需要控制的是 `milestone_matching.score.constraint_scores[].actual` 可能很大，因此本方案只导出约束 ID、分数、缺失状态和 evidence 摘要，不导出 actual 明细，避免 `display/data.js` 体积膨胀。
