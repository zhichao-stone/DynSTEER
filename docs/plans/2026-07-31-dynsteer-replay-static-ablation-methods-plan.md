# DynSTEER replay 静态消融方法补充计划

## 1. 目标

补充两种 replay 消融实验方法，用于把动态路由和动态权重拆成单因素对比：

- `dynsteer_replay_static_weighting`: 保留动态路由，关闭动态权重，即 `dynamic_routing=True`、`dynamic_weighting=False`。
- `dynsteer_replay_static_routing`: 关闭动态路由，保留动态权重，即 `dynamic_routing=False`、`dynamic_weighting=True`。

现有 `dynsteer_replay_static` 继续表示两者都关闭。

## 2. 修改范围

- `dynsteer/experiment/model.py`
  - 在 `ExperimentMethod` 中新增 `DYNSTEER_REPLAY_STATIC_WEIGHTING` 和 `DYNSTEER_REPLAY_STATIC_ROUTING`。
- `dynsteer/experiment/config.py`
  - 在 `_strategy_for_method()` 中为两个新方法设置对应策略开关。
  - 保留用户在 `strategy` 中配置的 `policy_stop`、`fixed_judge_level`、`replay_continue_after_virtual_stop` 和 `metadata`。
- `dynsteer/experiment/runner.py`
  - 将两个新方法加入 `_METHODS_NEED_DEFAULT`，复用现有 default trajectory replay 流程。
- `docs/apis/experiment.md`
  - 补充 methods 支持列表和 replay 消融方法说明。
- `tests/test_experiment_strategy_methods.py`
  - 增加矩阵展开测试，覆盖三种静态/半静态 replay 方法的策略开关。

## 3. 验收方式

- 运行 `pytest tests/test_experiment_strategy_methods.py`。
- 检查新增方法都能从 `expand_experiment_matrix()` 正确展开为 `ExperimentRunSpec`。
- 检查两个新方法都会被识别为需要 default 轨迹的 replay 方法。

## 附录A. 项目中没有把握实现的模块部分

本次修改不涉及 benchmark 执行、trajectory 回放算法、Judge 调用和指标汇总逻辑；这些部分沿用现有实现，没有需要重新实现但没有把握的模块。
