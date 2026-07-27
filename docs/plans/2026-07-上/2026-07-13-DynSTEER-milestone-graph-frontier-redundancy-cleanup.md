# DynSTEER milestone graph 与 frontier 冗余清理方案

## 1. 问题

- `initialize_milestone_frontier` 在初始化阶段接收 `matched`，但运行期初始化时 `matched_settlements` 必然为空，该参数让初始化语义变得不清晰。
- 多处运行期代码使用 `task_case.milestone_graph or MilestoneGraph()`，会在缺失图时生成无信息空图，掩盖上游数据错误。
- milestone graph 的存在性应在 TaskCase 加载边界保证，下游评估流程不应反复用空图兜底。

## 2. 修改方案

- 将 `initialize_milestone_frontier(graph)` 改为单参数函数，初始化时只根据 DAG 前驱关系构造 ready frontier，blocked 候选为空。
- 更新 evaluator 与 frontier 单元测试，使已有匹配只通过 `advance_milestone_frontier` 推进状态。
- 在 `load_task_case` / `load_task_case_file` 边界确保 TaskCase 有 milestone graph；从 adapted 文件读取到缺失图时重新适配并保存，仍缺失则抛出异常。
- 去除运行期核心链路中的 `task_case.milestone_graph or MilestoneGraph()`，改为直接使用加载阶段保证存在的图。
- 删除由上述变化产生的无用 import。

## 3. 验收

- 运行 frontier 与 runtime 相关 PyTest。
- 执行全量 PyTest，确认接口变更无遗漏。
- 复查 `MilestoneGraph()` 空图兜底不再出现在评估运行期链路。

## 附录A. 项目中没有把握实现的模块部分

当前任务主要是删除冗余参数和错误兜底，不涉及新模块或复杂外部系统。唯一需要注意的是历史 adapted case 文件如果确实缺少 `milestone_graph`，应由加载边界触发重新适配；若对应 benchmark adapter 本身不能生成图，则会按预期抛出异常。
