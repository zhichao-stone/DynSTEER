# DynSTEER 评估 API

评估入口文档已迁移到 `docs/apis/evaluate.md`。

当前主入口是 `dynsteer.evaluate.DynSTEEREvaluator`：

- `evaluate(harness, config, task_case)`: benchmark 主实验入口，`task_case` 来自 adapter/loader，`case_id` 来自 `task_case.case_id`。

历史离线整轨迹入口 `evaluate_trajectory()` 已删除；当前只支持运行期 `evaluate()` 主流程。

## Target、stage goal 与 replay evidence

structured scorer 的状态目标只读取当前 `Constraint.expected`。stage goal 从 `TaskCase.stage_goal_templates` 实例化，模板占位符由当前 constraint 的 canonical JSON 替换；`TaskCase.initial_state` 不参与 target 或目标文本生成。

terminal state check 继续调用 benchmark structured scorer，并在 finish metadata 中记录 constraint ID、score、actual/expected excerpt 与 `target_source=Constraint.expected`。结构化状态约束失败属于 structural failure，LLM judge 不能仅凭自然语言确认把它改判为 PASS。

replay 输出在 report、summary、raw summary 和 finish metadata 中包含 `replay_execution`，记录虚拟停点、最终覆盖、源/回放轨迹长度及比例。Judge 的精确缓存仅存在于单个 case、单次 evaluator 生命周期，key 覆盖模型、prompt 类型、阶段边界、聚焦维度、passes 和 prompt context digest。

## 消融与执行期引导

`use_minefields=false` 在 `evaluate_step_minefields()` 入口短路：不刷新 reference anchor，不调用 minefield scorer，不写 `minefield_matches`，也不会设置 fatal minefield。`use_milestone_graph=false` 在静态适配阶段直接使用空图；图内表达的 milestone 和 minefield 均为空，阶段机制通过现有 finish stage 退化为 whole-trajectory 评分。

`dynsteer_evaluate_guided` 只把非 fatal stop 转为引导。fatal minefield、超过 `max_interventions`、同一 stage 已引导过、消息构造失败或 `send_guidance()` 失败时仍执行 stop。引导消息固定以 `[DynSTEER intervention]` 开头，只包含任务描述、已物化 stage goal、当前 stage 状态/分数、ready frontier 和结构化匹配诊断白名单。

每次干预写入 `RuntimeEvaluationState.interventions`，并随 raw summary 诊断输出。metadata 至少包含 `intervention_index`、`trigger_step_index`、`trigger_stage_id`、`termination_code`、`message_sha256`、`message_preview` 和 `outcome`。完整消息只保留在 raw summary/trajectory 诊断上下文，不进入聚合指标。

在线 `dynsteer_evaluate` 只有在 harness metadata 中显式设置 `collect_online_native_score=true` 时，才在轨迹收集后调用 native verifier，并把 `native_default_result`、`native_score` 与 native 评估时间/token 写入产物；旧在线配置不触发该调用。
