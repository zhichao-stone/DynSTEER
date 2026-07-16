# DynSTEER 看板 milestone 节点交互修复方案

## 1. 问题范围

本次只修改静态看板 `display/index.html` 中 milestone 图的前端交互与样式，解决以下问题：

- 选中 milestone 后，节点边框被统一改成蓝色，覆盖 pass / warn / fail / pending 的原始状态表达。
- 拉伸已选中的 milestone 节点时，节点内部标题、摘要和详情字体不随节点尺寸同步放大。
- 拉伸节点过程中，中栏图面板因反复重绘替换滚动容器，导致视角回到顶部。

## 2. 实施方案

- 调整 `.milestone-node.selected` 与 `.milestone-node.selected-stage` 样式，让选中态只增加阴影和线宽，不覆盖状态色与状态底色。
- 在绘制 milestone 节点时根据已选节点尺寸计算内容缩放比例，并通过 CSS 变量同步影响 SVG 文本和 `foreignObject` 内部详情字体。
- 将已选节点标题区高度与详情区域位置按同一缩放比例计算，避免字体放大后遮挡详情内容。
- 为 `renderGraph` 增加可选滚动保持逻辑，在拖拽 resize 的连续重绘中恢复原中栏滚动位置。

## 3. 验收方式

- 检查 `display/index.html` 中脚本语法可解析。
- 通过代码检查确认选中态不再覆盖 pass / warn / fail / pending 样式。
- 通过代码检查确认 resize 过程调用保留滚动位置的重绘路径。

## 附录A. 项目中没有把握实现的模块部分

无。本次修改限定在已有静态看板 DOM/SVG/CSS 逻辑内，不涉及后端评估算法、数据生成结构或新的模块边界。
