# DynSTEER 评估 API

评估入口文档已迁移到 `docs/apis/evaluate.md`。

当前主入口是 `dynsteer.evaluate.DynSTEEREvaluator`：

- `evaluate(harness, config, task_case)`: benchmark 主实验入口，`task_case` 来自 adapter/loader，`case_id` 来自 `task_case.case_id`。

历史离线整轨迹入口 `evaluate_trajectory()` 已删除；当前只支持运行期 `evaluate()` 主流程。

## Target、stage goal 与 replay evidence

structured scorer 的状态目标只读取当前 `Constraint.expected`。stage goal 从 `TaskCase.stage_goal_templates` 实例化，模板占位符由当前 constraint 的 canonical JSON 替换；`TaskCase.initial_state` 不参与 target 或目标文本生成。

terminal state check 继续调用 benchmark structured scorer，并在 finish metadata 中记录 constraint ID、score、actual/expected excerpt 与 `target_source=Constraint.expected`。结构化状态约束失败属于 structural failure，LLM judge 不能仅凭自然语言确认把它改判为 PASS。

replay 输出在 report、summary、raw summary 和 finish metadata 中包含 `replay_execution`，记录虚拟停点、最终覆盖、源/回放轨迹长度及比例。Judge 的精确缓存仅存在于单个 case、单次 evaluator 生命周期，key 覆盖模型、prompt 类型、阶段边界、聚焦维度、passes 和 prompt context digest。
