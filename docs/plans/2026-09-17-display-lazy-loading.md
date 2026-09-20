# DynSTEER 看板场景数据懒加载方案

## 背景

`display/data.js` 目前约 1.03 GB，浏览器在 `file://` 下一次性解析该脚本，导致 `STATUS_BREAKPOINT` 崩溃。

## 修改方案

- `data.js` 只保留 run、run 汇总、scenario 索引和摘要。
- 每个 scenario 完整详情写入 `display/scenarios/0000-0000.js`。
- 看板选中 scenario 后动态加载对应脚本。
- 动态加载使用令牌避免快速切换导致旧数据覆盖新选择。
- 场景脚本执行后从全局注册表移除，避免多 case 浏览时内存累积。

## 验证

- 检查 `display/build.py` 语法。
- 重新生成 `display/data.js` 与场景脚本。
- 检查入口数据体积、场景文件数量，并确认入口脚本不再包含完整轨迹。

## 附录A. 项目中没有把握实现的模块部分

- 单个 scenario 轨迹如果本身极大，浏览器仍可能加载较慢；本次先按场景隔离负载，后续如仍存在极端 case，可再引入轨迹分页或虚拟滚动。