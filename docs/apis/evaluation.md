# DynSTEER 评估 API

评估入口文档已迁移到 `docs/apis/evaluate.md`。

当前主入口是 `dynsteer.evaluate.DynSTEEREvaluator`：

- `evaluate(harness, case_id, config)`: benchmark 主实验入口。
- `evaluate_trajectory(task_case, trajectory)`: 整轨迹评估成员函数，用于后续对比实验或消融实验。

模块级 `evaluate_trajectory()` 已删除。
