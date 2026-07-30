# DynSTEER 包级导出整理方案

## 背景

当前 `dynsteer.evaluate` 及其子包里，很多核心函数仍然通过叶子模块逐个导入。  
这会带来两个问题：

1. 上层模块的 import 列表很长，阅读成本高。
2. 外部调用方需要知道太多内部文件名，不利于统一入口。

截图里这种 `dynsteer.evaluate.matching.*` 的导入最适合先收敛到子包 `__init__.py`。

## 目标

- 让“稳定、常用、跨模块复用”的函数从包级导出。
- 保持私有辅助函数留在叶子模块，不扩大公开面。
- 先整理 `evaluate` 相关包，再决定是否向其他子包推广。

## 建议分层

### 1. `dynsteer.evaluate.matching`

建议作为“匹配基础能力”的统一入口，导出这些稳定函数：

- `candidate_boundary_for_current_step`
- `boundary_step`
- `boundary_snapshot`
- `initialize_milestone_frontier`
- `advance_milestone_frontier`
- `ready_milestones`
- `blocked_candidate_milestones`
- `ready_milestone_ids`
- `stage_start_for_ready_milestone`
- `analyze_milestone_step`
- `milestone_scoring_step`

这层的目标是把 `boundary.py`、`frontier.py`、`minefield.py`、`milestone.py` 的常用能力汇成一个入口。

### 2. `dynsteer.evaluate`

建议导出更上层的评估编排函数：

- `DynSTEEREvaluator`
- `evaluate_checkpoint`
- `finish_settlement`
- `evaluate_agent_step`
- `evaluate_step_minefields`
- `scoring_context`
- `pending_milestone_stage_results`
- `blocked_milestone_termination_reason`
- `ready_frontier_no_progress_termination_reason`
- `task_case_snapshot`
- `selected_candidate_from_attempt`

可选一起导出的异常类：

- `JudgeConfigurationError`
- `HarnessTeardownError`

这一层的定位是“评估流程公共 API”，而不是底层实现细节。

## 不建议导出的内容

- 以 `_` 开头的内部辅助函数。
- 仅被单个模块使用的局部解析函数。
- 仍在频繁改动、语义未稳定的中间步骤函数。
- `adapter.toolsandbox.utils` 里偏数据转换的小工具，除非真的变成多模块公共入口。

## 实施顺序

1. 先补 `dynsteer/evaluate/matching/__init__.py` 的包级 re-export。
2. 再补 `dynsteer/evaluate/__init__.py` 的公共 API re-export。
3. 回收上层模块里的长导入，改成包级导入。
4. 加一个最小导入测试，确保包级 API 可直接 import，且不引入循环依赖。
5. 跑 `py_compile` 和 `pytest` 复核。

## 验收标准

- `dynsteer.evaluate.evaluator` 的 import 列表明显缩短。
- 常见调用方可以只依赖 `dynsteer.evaluate` / `dynsteer.evaluate.matching`。
- `import dynsteer.evaluate`、`import dynsteer.evaluate.matching` 不报循环导入错误。
- 公开导出通过 `__all__` 明确限定。

## 备注

我建议这次只整理 `evaluate` 这条线，先不要把 `dynsteer` 顶层包做得过宽。  
顶层包导出太多，后面反而容易把依赖边界搅乱。
