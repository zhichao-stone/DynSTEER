# DynSTEER 评估 API

评估入口文档已迁移到 `docs/apis/evaluate.md`。

当前主入口是 `dynsteer.evaluate.DynSTEEREvaluator`：

- `evaluate(harness, config, task_case)`: benchmark 主实验入口，`task_case` 来自 adapter/loader，`case_id` 来自 `task_case.case_id`。

历史离线整轨迹入口 `evaluate_trajectory()` 已删除；当前只支持运行期 `evaluate()` 主流程。
