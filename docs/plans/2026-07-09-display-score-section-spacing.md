# display 右列分数折叠区间距修复方案

## 1. 问题与目标

右列阶段详情展开后，“维度分数”和“动态权重”内部的维度名称贴到折叠边框左侧，视觉上像是文字与边框重叠。

目标是让分数行在折叠区内部有稳定左右内边距，并保持条形图对齐，不改变数据与交互逻辑。

## 2. 根因

`details` 通用样式提供了边框，`summary` 有 padding，但 `score-row` 是 `details.score-section` 的直接子元素，没有左右 padding，因此分数行从边框内侧 0px 处开始渲染。

## 3. 实现方案

- 为 `.score-section .score-row` 增加左右内边距。
- 为 `.score-section .score-row:last-child` 增加底部内边距。
- 为 `.score-section .section-title` 做独立 summary 样式，保证标题与内容区的空间一致。
- 补静态测试，避免以后移除这些间距样式。

## 附录A. 项目中没有把握实现的模块部分

没有无法把握的部分。这是局部 CSS 间距修正。
